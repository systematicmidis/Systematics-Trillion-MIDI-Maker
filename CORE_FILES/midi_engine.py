"""MIDI parsing, validation and compressed duplication for Systematics TRILLION MIDI maker.

The idea (taken from the original ``trillion.py`` example, then hardened):

* Accept a MIDI file and pick which track(s) get multiplied.
* Rewrite the header so the track count reflects the new layout.
* Repeat each selected track chunk ``multiplier`` times (unselected tracks
  are kept once, e.g. a melody track next to a duplicated "spam" track).
* Store the result inside a single compressed archive so the (potentially
  huge) uncompressed MIDI takes almost no disk space.

The output archive format is selectable (``archive_format``):

* **xz** (default) — a single XZ stream.
* **gz** — gzip; repeated blocks become concatenated gzip members.
* **bz2** — bzip2; repeated blocks become concatenated bzip2 streams.
* **zst** — zstandard (stdlib ``compression.zstd`` on Python 3.14+);
  repeated blocks become concatenated zstd frames.
* **zip** — a real .zip container with one entry per emitted track block.
* **7z** — a 7-Zip solid archive via ``py7zr`` (one continuous LZMA2 pass).

Two write strategies are provided:

* **streamed** — one continuous compressor stream; the encoder sees the
  repeated track and produces the smallest possible file. Kept for small
  inputs where it is fast anyway (and it is the only option for 7z, which
  has no "repeat the same compressed block" trick).
* **concatenated (fast)** — the track is compressed once (in parallel
  segments) and those compressed blocks are written repeatedly. Concatenated
  XZ/gzip/bzip2/zstd streams (and repeated zip entries) are all valid, so
  extraction reproduces the full track count. Near instant even for multi-GB
  tracks; used automatically whenever the streamed strategy would have to
  chew through more than ~1 GiB of input.
"""

from __future__ import annotations

import bz2
import concurrent.futures
import gzip
import heapq
import io
import lzma
import os
import shutil
import struct
import tempfile
import time
import zlib
from dataclasses import dataclass

# Python 3.14+ ships zstandard as ``compression.zstd`` (stdlib). Older
# interpreters fall back to ``None`` and the format is disabled in that case.
try:
    from compression import zstd as _zstd
except ImportError:
    _zstd = None

# py7zr is the only pure-Python 7-Zip writer. It is optional: the 7z format
# simply isn't offered when the package is missing.
try:
    import py7zr as _py7zr
except ImportError:
    _py7zr = None

MIN_TRACKS = 1
MAX_TRACKS = 65535  # the MIDI track-count field is an unsigned 16-bit value
MAX_TRACK_BYTES = 0xFFFFFFFF  # the MTrk length field is an unsigned 32-bit value (~4 GB)

# Note-count tiers the loaders are sized for. The dispatcher can't know the
# exact note count without scanning the file, so it estimates it from the
# file size using ESTIMATED_BYTES_PER_NOTE below.
LARGE_NOTES = 10_000_000    # more than this many notes -> Large MIDI Loader
MEGA_NOTES = 100_000_000    # more than this many notes -> Mega MIDI Loader

# Typical black-MIDI files use ~6-10 bytes per note (the reference 164 MB /
# 21.5M-note track is ~7.6). Use a conservative lower bound so files "above N
# notes" reliably reach the right tier.
ESTIMATED_BYTES_PER_NOTE = 5

LARGE_THRESHOLD = LARGE_NOTES * ESTIMATED_BYTES_PER_NOTE   # ~50 MB -> Large
MEGA_THRESHOLD = MEGA_NOTES * ESTIMATED_BYTES_PER_NOTE     # ~500 MB -> Mega

# RAM preload modes: how many copies of the input file are held in memory
# after loading (normal = the parsed track chunks only, roughly 1x the file;
# double = chunks + the raw file bytes; triple = + a joined copy of the
# chunks). The user picks a mode and an optional cap in MB.
RAM_MODES = {"normal": 1, "double": 2, "triple": 3}
RAM_MODE_LABELS = {
    "normal": "Normal (1×)",
    "double": "Double (2×)",
    "triple": "Triple (3×)",
}

# Note-merge speed tiers. The dominant cost of ``build_note_multiplied_xz``
# used to be calling ``LZMACompressor.compress`` once per ~5-byte event; we
# now buffer events into a bytearray and compress in bulk. ``preset_max``
# also clamps the LZMA preset downward (lower preset = much faster but larger
# output), and ``buffer`` is how many bytes of merged track are accumulated
# before one compress() call.
# Output archive formats. ``ext`` is the file suffix; ``stream`` is True
# for formats whose spec allows concatenating independent compressed
# streams/members (the fast duplication trick), False for containers that
# repeat per-entry compressed blocks instead (zip) or that must compress
# everything in one pass (7z). Order here is the order shown in the UI.
ARCHIVE_FORMATS: dict = {
    "xz":  {"ext": ".xz",  "stream": True},
    "gz":  {"ext": ".gz",  "stream": True},
    "bz2": {"ext": ".bz2", "stream": True},
    "zst": {"ext": ".zst", "stream": True},
    "zip": {"ext": ".zip", "stream": False},
    "7z":  {"ext": ".7z",  "stream": False},
}

MERGE_SPEEDS = {
    "balanced": {"preset_max": 9, "buffer": 1 << 20},   # 1 MiB
    "fast":     {"preset_max": 1, "buffer": 1 << 22},   # 4 MiB
    "turbo":    {"preset_max": 0, "buffer": 1 << 24},   # 16 MiB
}

# Track-duplication write strategy. The old default streamed every copy of
# every chunk through one continuous LZMA stream, so duplicating a 4 GB
# track meant 4 GB * multiplier of encoder work ("forever"). Instead the
# engine now compresses each unique chunk **once** (in parallel segments)
# and repeats the compressed blocks — valid concatenated XZ streams — which
# turns Nx encoder work into 1x plus disk writes. The continuous stream is
# kept for small inputs, where LZMA's dictionary can cheaply exploit the
# repetition and the output is genuinely smaller.
SEGMENT_SIZE = 256 << 20        # 256 MiB per parallel compression task
AUTO_CONCAT_INPUT = 1 << 30     # use concatenated streams above 1 GiB of encoder input
AUTO_CONCAT_RATIO = 4           # ...or whenever repetition inflates input > 4x

# Disk-safety guard. Every write is checked against the free space on the
# output drive, and the build aborts (leaving this much free) instead of
# filling the disk to zero and crashing the OS. This is the amount *kept*
# free, not the amount the output may use.
DISK_RESERVE = 1 << 30          # keep 1 GiB free at all times

MIDI_FORMATS = (0, 1)
CHUNK_HEADER = b"MThd"
CHUNK_TRACK = b"MTrk"


class MidiError(Exception):
    """Raised when the input MIDI is missing, malformed or not single-track."""


class Cancelled(Exception):
    """Raised inside a build when the user cancels the operation."""


class DiskFullError(MidiError):
    """Raised when the target drive would run out of space mid-build."""


@dataclass
class MidiInfo:
    """Everything the engine needs to know about a validated input MIDI."""

    path: str
    format: int
    ntrks: int
    division: bytes          # the 2-byte "division" field from the header
    track_chunks: list       # raw "MTrk" chunks (id + length + data)
    note_count: int          # note-on events (velocity > 0), all tracks
    end_of_track: bool       # whether an End-of-Track meta event was seen
    input_size: int          # size of the input file in bytes
    track_note_counts: list  # note-on count for each track chunk
    track_names: list  # track-name meta (0x03) per chunk, or ""
    loader: str = "standard"  # which loader produced this info
    ram_mode: int = 1          # effective preload multiplier (1, 2 or 3)
    ram_capped: bool = False   # True when max_ram_mb forced a lower mode
    max_ram_mb: int = 0        # the cap applied at load time (0 = none)
    raw_data: bytes | None = None    # retained 2nd copy (double/triple)
    linked_data: bytes | None = None  # retained 3rd copy (triple only)


@dataclass
class BuildResult:
    """Summary of a finished duplication run."""

    output_path: str
    multiplier: int
    track_count: int          # total track chunks written
    total_notes: int          # note-on events across every written track
    input_size: int
    uncompressed_size: int
    compressed_size: int
    elapsed: float
    fast_mode: bool
    compress_passes: int = 1   # 1 = .xz, 2 = .xz.xz, 3 = .xz.xz.xz
    selected_tracks: tuple = ()   # indices that were multiplied
    per_track_notes: tuple = ()   # note count of each source track
    effective_multiplier: int = 0  # multiplier after capping to 65,535 tracks
    note_mode: bool = False        # True when notes (not tracks) were multiplied
    archive_format: str = "xz"    # output container/stream format
    append_tracks: int = 0         # tracks added from the merged MIDI (written once)
    append_notes: int = 0          # note-on events from the merged MIDI
    append_name: str = ""          # file name of the merged MIDI, if any


# --------------------------------------------------------------------------
# Archive-format codec layer
# --------------------------------------------------------------------------

def _norm_format(archive_format: str) -> str:
    """Validate a format key; unknown/disabled formats fall back to ``xz``."""
    if archive_format in ARCHIVE_FORMATS:
        return archive_format
    return "xz"


def _format_available(archive_format: str) -> bool:
    """True when the codec for this format is importable in this runtime."""
    fmt = _norm_format(archive_format)
    if fmt == "zst":
        return _zstd is not None
    if fmt == "7z":
        return _py7zr is not None
    return True


def _zstd_level(preset: int) -> int:
    """Map the 0-9 LZMA-style preset onto a sensible zstd level (1-19)."""
    return max(1, min(19, int(preset)))


def _compress_block(archive_format: str, data: bytes,
                    preset: int) -> bytes:
    """One-shot compression of ``data`` into an independent stream/member.

    Used for the fast path: every repeated block is this exact byte string,
    and concatenating the blocks is valid for xz/gz/bz2/zst. For zip the
    caller uses :class:`_ZipConcatWriter` instead.
    """
    fmt = _norm_format(archive_format)
    if fmt == "xz":
        return lzma.compress(data, format=lzma.FORMAT_XZ,
                             check=lzma.CHECK_CRC64, preset=int(preset))
    if fmt == "gz":
        return gzip.compress(data, compresslevel=int(preset))
    if fmt == "bz2":
        return bz2.compress(data, max(1, int(preset)))
    if fmt == "zst":
        if _zstd is None:
            raise MidiError("Zstandard is not available on this Python build.")
        return _zstd.compress(data, level=_zstd_level(preset))
    raise MidiError(f"Unsupported archive format: {archive_format}")


def _make_stream_compressor(archive_format: str, preset: int):
    """Return a streaming compressor with ``.compress(data)`` / ``.flush()``.

    The continuous (non-concat) write path uses one of these. Every returned
    object offers the same two methods the LZMA compressor does, so the
    callers don't need to know which format they are writing.
    """
    fmt = _norm_format(archive_format)
    preset = int(preset)
    if fmt == "xz":
        return lzma.LZMACompressor(
            format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64,
            filters=[{"id": lzma.FILTER_LZMA2, "preset": preset}])
    if fmt == "gz":
        # wbits=31 => gzip wrapper; flush() closes the member.
        return zlib.compressobj(level=preset, method=zlib.DEFLATED, wbits=31)
    if fmt == "bz2":
        return bz2.BZ2Compressor(max(1, preset))
    if fmt == "zst":
        if _zstd is None:
            raise MidiError("Zstandard is not available on this Python build.")
        return _zstd.ZstdCompressor(level=_zstd_level(preset))
    raise MidiError(f"Unsupported archive format: {archive_format}")


def archive_extension(archive_format: str, passes: int) -> str:
    """File suffix for a format + extra compression passes.

    ``archive_extension("xz", 1)`` -> ``".xz"``; ``("gz", 2)`` -> ``".gz.xz"``
    (the second pass recompresses the first file with XZ, matching the
    "double compression" storage-saving feature).
    """
    fmt = _norm_format(archive_format)
    ext = ARCHIVE_FORMATS[fmt]["ext"]
    return ext + ".xz" * max(0, int(passes) - 1)


def output_extension(archive_format: str, passes: int) -> str:
    """Full suggested file suffix, e.g. ``".mid.gz"`` / ``".mid.zip.xz"``."""
    return ".mid" + archive_extension(archive_format, passes)


# -- minimal ZIP writer ----------------------------------------------------
#
# zipfile's high-level API always re-compresses data handed to ``writestr``,
# so the fast duplication path (compress each unique chunk once, then repeat
# the compressed bytes) cannot use it. This writer emits the same byte
# layout zipfile produces — local headers + raw-deflate payloads + a central
# directory — with ZIP64 support (needed for 65,535-track outputs, which is
# one entry past the classic 16-bit entry count, and for ~4 GB tracks). The
# struct formats are copied from CPython's zipfile module so extraction tools
# read the result exactly as if zipfile had written it.

_STRING_LOCAL = b"PK\x03\x04"
_STRING_CENTRAL = b"PK\x01\x02"
_STRING_EOCD = b"PK\x05\x06"
_STRING_EOCD64 = b"PK\x06\x06"
_STRING_EOCD64_LOC = b"PK\x06\x07"
_STRUCT_LOCAL = struct.Struct("<4s2B4HL2L2H")      # 30 bytes
_STRUCT_CENTRAL = struct.Struct("<4s4B4HL2L5H2L")   # 46 bytes
_STRUCT_EOCD = struct.Struct("<4s4H2LH")            # 22 bytes
_STRUCT_EOCD64 = struct.Struct("<4sQ2H2L4Q")        # 56 bytes
_STRUCT_EOCD64_LOC = struct.Struct("<4sLQL")        # 20 bytes
_ZIP64_LIMIT = (1 << 31) - 1      # zipfile's own threshold for 4-byte fields
_ZIP_COUNT_LIMIT = (1 << 16) - 1  # classic EOCD entry count
_ZIP64_VERSION = 45
_ZIP_DEFLATED = 8


def _dos_date_time() -> tuple[int, int]:
    """Current local time packed into DOS date/time (like zipfile does)."""
    import time as _time
    dt = _time.localtime()
    dosdate = (dt.tm_year - 1980) << 9 | dt.tm_mon << 5 | dt.tm_mday
    dostime = dt.tm_hour << 11 | dt.tm_min << 5 | (dt.tm_sec // 2)
    return dostime, dosdate


class _ZipConcatWriter:
    """Write a .zip whose per-entry compressed data is supplied pre-made.

    ``write_entry(name, raw)`` compresses ``raw`` once (raw deflate) and
    records the entry; ``write_entry_precompressed(name, raw_len, cdata)``
    writes a block whose compressed bytes were already produced (the fast
    path compresses each unique chunk once and reuses the result).
    ``finish()`` writes the central directory and end records.
    """

    def __init__(self, out, compresslevel: int = 6,
                 ensure_space=None, cancel_event=None):
        self._out = out
        self._level = max(0, min(9, int(compresslevel)))
        self._ensure_space = ensure_space
        self._cancel_event = cancel_event
        self._entries = []  # (name, crc, csize, usize, header_offset, zip64, flag)
        self._offset = out.tell() if hasattr(out, "tell") else 0

    def _check(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise Cancelled()

    def _space(self, amount: int) -> None:
        if self._ensure_space is not None:
            self._ensure_space(amount)

    def _write_entry(self, name: str, raw_len: int, cdata: bytes,
                     crc: int, precompressed: bool) -> None:
        self._check()
        name_b = name.encode("utf-8")
        header_offset = self._offset
        csize = len(cdata)
        zip64 = raw_len > _ZIP64_LIMIT or csize > _ZIP64_LIMIT
        self._space(len(name_b) + len(cdata) + 64)
        if zip64:
            extra = struct.pack("<HHQQ", 1, 16, raw_len, csize)
            f_size = f_csize = 0xFFFFFFFF
            version = _ZIP64_VERSION
        else:
            extra = b""
            f_size, f_csize = raw_len, csize
            version = 20
        dostime, dosdate = _dos_date_time()
        local = _STRUCT_LOCAL.pack(
            _STRING_LOCAL, version, 0, 0, _ZIP_DEFLATED, dostime, dosdate,
            crc, f_csize, f_size, len(name_b), len(extra))
        self._out.write(local)
        self._out.write(name_b)
        self._out.write(extra)
        self._out.write(cdata)
        self._offset += len(local) + len(name_b) + len(extra) + csize
        self._entries.append((name_b, crc, csize, raw_len, header_offset,
                              zip64, 0))

    def write_stream_entry(self, name: str, total_size: int, pieces) -> None:
        """Write one entry whose payload arrives incrementally via ``pieces``.

        Used by the note-merge path, where the merged track is generated on
        the fly (it can be gigabytes). The local header carries the data-
        descriptor flag (bit 3); CRC and sizes are written in a descriptor
        after the deflate stream, and the central directory records the real
        values. ``total_size`` is the known uncompressed size (used to pick
        ZIP64 up front, since the descriptor must match the header's choice).
        """
        self._check()
        name_b = name.encode("utf-8")
        header_offset = self._offset
        zip64 = total_size > _ZIP64_LIMIT
        version = _ZIP64_VERSION if zip64 else 20
        flag = 0x0008  # data descriptor follows the payload
        dostime, dosdate = _dos_date_time()
        local = _STRUCT_LOCAL.pack(
            _STRING_LOCAL, version, 0, flag, _ZIP_DEFLATED, dostime, dosdate,
            0, 0, 0, len(name_b), 0)
        self._out.write(local)
        self._out.write(name_b)
        self._offset += len(local) + len(name_b)
        comp = zlib.compressobj(level=self._level, method=zlib.DEFLATED,
                                wbits=-15)
        crc = 0
        csize = 0
        usize = 0
        for piece in pieces:
            self._check()
            out = comp.compress(piece)
            if out:
                self._space(len(out))
                self._out.write(out)
                self._offset += len(out)
                csize += len(out)
            crc = zlib.crc32(piece, crc)
            usize += len(piece)
        tail = comp.flush()
        if tail:
            self._space(len(tail))
            self._out.write(tail)
            self._offset += len(tail)
            csize += len(tail)
        crc &= 0xFFFFFFFF
        if zip64:
            desc = struct.pack("<4sLQQ", b"PK\x07\x08", crc, csize, usize)
        else:
            desc = struct.pack("<4sLLL", b"PK\x07\x08", crc, csize, usize)
        self._out.write(desc)
        self._offset += len(desc)
        self._entries.append((name_b, crc, csize, usize, header_offset,
                              zip64, flag))

    def write_entry(self, name: str, raw: bytes) -> None:
        """Compress ``raw`` once and write it as a zip entry."""
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        comp = zlib.compressobj(level=self._level, method=zlib.DEFLATED,
                                wbits=-15)  # raw deflate, no zlib wrapper
        cdata = comp.compress(raw) + comp.flush()
        self._write_entry(name, len(raw), cdata, crc, False)

    def write_entry_precompressed(self, name: str, raw_len: int,
                                  cdata: bytes, crc: int) -> None:
        """Write an entry reusing already-compressed bytes (fast path)."""
        self._write_entry(name, raw_len, cdata, crc, True)

    def finish(self) -> None:
        """Write the central directory and (ZIP64) end-of-archive records."""
        self._check()
        cd_offset = self._offset
        for name_b, crc, csize, usize, header_offset, zip64, flag in \
                self._entries:
            self._check()
            extra_parts = []
            f_size, f_csize = usize, csize
            if zip64:
                extra_parts.extend((usize, csize))
                f_size = f_csize = 0xFFFFFFFF
            if header_offset > _ZIP64_LIMIT:
                extra_parts.append(header_offset)
                header_offset = 0xFFFFFFFF
            if extra_parts:
                extra = struct.pack("<HH" + "Q" * len(extra_parts),
                                    1, 8 * len(extra_parts), *extra_parts)
                version = _ZIP64_VERSION
            else:
                extra = b""
                version = 20
            dostime, dosdate = _dos_date_time()
            central = _STRUCT_CENTRAL.pack(
                _STRING_CENTRAL, version, 0, version, 0, flag, _ZIP_DEFLATED,
                dostime, dosdate, crc, f_csize, f_size, len(name_b),
                len(extra), 0, 0, 0, 0o600 << 16, header_offset)
            self._out.write(central)
            self._out.write(name_b)
            self._out.write(extra)
        self._offset = self._out.tell()
        cd_size = self._offset - cd_offset
        count = len(self._entries)
        if count > _ZIP_COUNT_LIMIT or cd_offset > _ZIP64_LIMIT or \
                cd_size > _ZIP64_LIMIT:
            eocd64_pos = self._offset
            self._out.write(_STRUCT_EOCD64.pack(
                _STRING_EOCD64, 44, _ZIP64_VERSION, _ZIP64_VERSION, 0, 0,
                count, count, cd_size, cd_offset))
            self._out.write(_STRUCT_EOCD64_LOC.pack(
                _STRING_EOCD64_LOC, 0, eocd64_pos, 1))
            count = min(count, 0xFFFF)
            cd_size = min(cd_size, 0xFFFFFFFF)
            cd_offset = min(cd_offset, 0xFFFFFFFF)
        self._out.write(_STRUCT_EOCD.pack(
            _STRING_EOCD, 0, 0, count, count, cd_size, cd_offset, 0))


# -- 7-Zip streaming -------------------------------------------------------

class _SizedStream(io.BufferedIOBase):
    """BufferedIOBase whose ``seek``/``tell`` run on a virtual position.

    py7zr's ``writef`` probes the total size with ``seek(0, SEEK_END)`` then
    reads the stream sequentially; this base class provides that bookkeeping
    without materializing the underlying data (which is generated on the fly
    by subclasses). ``read`` must be implemented by the subclass and must
    advance ``self._pos``.
    """

    def __init__(self, total: int):
        super().__init__()
        self._total = total
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._total + offset
        return self._pos


class _MidStreamReader(_SizedStream):
    """Serve ``header`` followed by ``(chunk, reps)`` copies as a byte stream.

    Lets the 7z path stream the full (potentially enormous) uncompressed MIDI
    without ever materializing it in memory. ``on_chunk(chunk, note_count)``
    fires each time a complete chunk copy has been served (so the UI can
    advance the track progress); ``on_bytes`` reports cumulative bytes.
    """

    def __init__(self, total: int, header: bytes, order, on_chunk=None,
                 on_bytes=None, cancel_event=None, ensure_space=None):
        super().__init__(total)
        self._buf = memoryview(header)
        self._order = list(order)  # (chunk, note_count, reps)
        self._idx = 0             # position inside current chunk copy
        self._chunk = b""
        self._nc = 0
        self._reps_left = 0
        self._on_chunk = on_chunk
        self._on_bytes = on_bytes
        self._cancel_event = cancel_event
        self._ensure_space = ensure_space

    def _next(self) -> bool:
        """Advance to the next chunk copy; False when the stream is done."""
        if self._reps_left > 0:
            return True
        if not self._order:
            return False
        chunk, nc, reps = self._order.pop(0)
        self._chunk = chunk
        self._nc = nc
        self._reps_left = reps
        self._idx = 0
        return True

    def read(self, n: int = -1) -> bytes:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise Cancelled()
        if n is None or n < 0:
            n = 1 << 20
        if self._pos >= self._total:
            return b""
        parts = []
        want = n
        # Serve whatever is left of the header first.
        if len(self._buf):
            take = min(want, len(self._buf))
            parts.append(bytes(self._buf[:take]))
            self._buf = self._buf[take:]
            want -= take
            self._pos += take
            if self._on_bytes is not None:
                self._on_bytes(take)
        while want > 0 and self._next():
            take = min(want, len(self._chunk) - self._idx)
            parts.append(self._chunk[self._idx:self._idx + take])
            self._idx += take
            self._pos += take
            if self._on_bytes is not None:
                self._on_bytes(take)
            if self._idx >= len(self._chunk):
                self._reps_left -= 1
                self._idx = 0
                if self._on_chunk is not None:
                    self._on_chunk(self._chunk, self._nc)
            want -= take
        if not parts:
            return b""
        return b"".join(parts)


class _NoteMergeReader(_SizedStream):
    """Serve the note-merge output (head block + merged track) as a stream.

    ``pieces`` is an iterator of merged-track byte strings; this reader hands
    them out in order so the 7z path can stream the merged track into a
    solid archive without materializing it in memory.
    """

    def __init__(self, total: int, head: bytes, pieces, cancel_event=None,
                 ensure_space=None):
        super().__init__(total)
        self._head = memoryview(head)
        self._pieces = pieces
        self._buf = b""
        self._cancel_event = cancel_event
        self._ensure_space = ensure_space

    def read(self, n: int = -1) -> bytes:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise Cancelled()
        if n is None or n < 0:
            n = 1 << 20
        if self._pos >= self._total:
            return b""
        parts = []
        want = n
        if len(self._head):
            take = min(want, len(self._head))
            parts.append(bytes(self._head[:take]))
            self._head = self._head[take:]
            want -= take
            self._pos += take
            if self._ensure_space is not None:
                self._ensure_space(take)
        while want > 0:
            if not self._buf:
                try:
                    self._buf = next(self._pieces)
                except StopIteration:
                    break
            take = min(want, len(self._buf))
            parts.append(self._buf[:take])
            self._buf = self._buf[take:]
            want -= take
            self._pos += take
            if self._ensure_space is not None:
                self._ensure_space(take)
        if not parts:
            return b""
        return b"".join(parts)


def _write_seven_zip(out, preset: int, entry_name: str, reader) -> None:
    """Stream ``reader`` into a single solid 7z entry via py7zr.

    ``reader`` is a file-like object whose ``read()`` yields the uncompressed
    MIDI (header + every track copy). 7z has no repeat-the-same-block trick,
    so this is always one continuous LZMA2 pass — the correct trade-off for
    a container format, and the only sane option for the note-merge path.
    """
    if _py7zr is None:
        raise MidiError("7z output requires the py7zr package.")
    with _py7zr.SevenZipFile(
            out, "w",
            filters=[{"id": _py7zr.FILTER_LZMA2, "preset": int(preset)}]) as zf:
        zf.writef(reader, entry_name)


# --------------------------------------------------------------------------
# Low level MIDI helpers
# --------------------------------------------------------------------------

def _decode_name(raw: bytes) -> str:
    """Turn raw track-name meta bytes into a display string (best effort)."""
    text = raw.decode("utf-8", errors="replace")
    return text.strip("\x00 \t\r\n") or ""


def _read_vlq(data: bytes, i: int) -> tuple[int, int]:
    """Read a MIDI variable-length quantity starting at ``i``.

    Returns ``(value, next_index)``.
    """
    value = 0
    n = len(data)
    for _ in range(4):  # MIDI VLQs are at most 4 bytes
        if i >= n:
            raise MidiError("Truncated variable-length value in MIDI track.")
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i
    raise MidiError("Invalid variable-length value in MIDI track.")


def _scan_track(track_data: bytes) -> tuple[int, bool, str]:
    """Walk a track's events, counting note-ons and checking structure.

    Returns ``(note_count, has_end_of_track, track_name)``. Raises
    :class:`MidiError` for structurally broken tracks.
    """
    i = 0
    n = len(track_data)
    running_status: int | None = None
    note_count = 0
    end_of_track = False
    track_name = ""

    while i < n:
        # Delta time (ignored here, only validated).
        _, i = _read_vlq(track_data, i)
        if i >= n:
            raise MidiError("MIDI track ends in the middle of an event.")

        first = track_data[i]

        if first == 0xFF:  # meta event
            if i + 1 >= n:
                raise MidiError("Truncated MIDI meta event.")
            meta_type = track_data[i + 1]
            i += 2
            length, i = _read_vlq(track_data, i)
            if i + length > n:
                raise MidiError("Truncated MIDI meta event data.")
            if meta_type == 0x2F and length == 0:
                end_of_track = True
            elif meta_type == 0x03 and not track_name:
                track_name = _decode_name(track_data[i:i + length])
            i += length

        elif first in (0xF0, 0xF7):  # SysEx event
            i += 1
            length, i = _read_vlq(track_data, i)
            if i + length > n:
                raise MidiError("Truncated MIDI SysEx event.")
            i += length

        else:
            # Channel (voice) message, possibly using running status.
            if first & 0x80:
                status = first
                running_status = status
                i += 1
            else:
                if running_status is None:
                    raise MidiError("Running status used before any status byte.")
                status = running_status

            high = status & 0xF0
            if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):  # two data bytes
                if i + 2 > n:
                    raise MidiError("Truncated MIDI channel message.")
                data1 = track_data[i]
                data2 = track_data[i + 1]
                i += 2
                if high == 0x90 and (data2 & 0x7F) > 0:
                    note_count += 1
            elif high in (0xC0, 0xD0):  # one data byte
                if i + 1 > n:
                    raise MidiError("Truncated MIDI channel message.")
                i += 1
            else:
                raise MidiError(f"Invalid MIDI status byte 0x{status:02X}.")

    return note_count, end_of_track, track_name


def _encode_vlq(value: int) -> bytes:
    """Encode a non-negative integer as a MIDI variable-length quantity."""
    value = max(0, int(value))
    buffer = bytearray([value & 0x7F])
    value >>= 7
    while value:
        buffer.append(0x80 | (value & 0x7F))
        value >>= 7
    buffer.reverse()
    return bytes(buffer)


# Event kinds returned by ``_decode_track``.
KIND_NOTE_ON = 0    # note-on with velocity > 0 (counts as a "note")
KIND_NOTE_OFF = 1   # note-off, or note-on with velocity 0
KIND_EOT = 2        # End-of-Track meta event
KIND_OTHER = 3      # any other channel message / meta / SysEx event


def _iter_track_events(track_data: bytes):
    """Yield ``(delta_ticks, raw_event, kind)`` triples lazily.

    Streaming equivalent of :func:`_decode_track`: running-status channel
    messages are emitted in explicit form (status byte included) so every
    event is self-contained and can be re-timed when notes are
    merged/multiplied. The raw bytes never include the delta time. Decoding
    lazily (instead of building a list) keeps huge black-MIDI tracks from
    ballooning in memory during note multiplication.
    """
    i = 0
    n = len(track_data)
    running_status: int | None = None

    while i < n:
        delta, i = _read_vlq(track_data, i)
        if i >= n:
            raise MidiError("MIDI track ends in the middle of an event.")

        first = track_data[i]

        if first == 0xFF:  # meta event
            if i + 1 >= n:
                raise MidiError("Truncated MIDI meta event.")
            meta_type = track_data[i + 1]
            length, j = _read_vlq(track_data, i + 2)
            if j + length > n:
                raise MidiError("Truncated MIDI meta event data.")
            raw = track_data[i:j + length]
            kind = KIND_EOT if (meta_type == 0x2F and length == 0) else KIND_OTHER
            yield delta, raw, kind
            i = j + length

        elif first in (0xF0, 0xF7):  # SysEx event
            length, j = _read_vlq(track_data, i + 1)
            if j + length > n:
                raise MidiError("Truncated MIDI SysEx event.")
            yield delta, track_data[i:j + length], KIND_OTHER
            i = j + length

        else:
            # Channel (voice) message, possibly using running status.
            if first & 0x80:
                status = first
                running_status = status
                i += 1
            else:
                if running_status is None:
                    raise MidiError("Running status used before any status byte.")
                status = running_status

            high = status & 0xF0
            if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):  # two data bytes
                if i + 2 > n:
                    raise MidiError("Truncated MIDI channel message.")
                data1 = track_data[i]
                data2 = track_data[i + 1]
                i += 2
                raw = bytes([status, data1, data2])
                if high == 0x90:
                    kind = KIND_NOTE_ON if (data2 & 0x7F) > 0 else KIND_NOTE_OFF
                elif high == 0x80:
                    kind = KIND_NOTE_OFF
                else:
                    kind = KIND_OTHER
                yield delta, raw, kind
            elif high in (0xC0, 0xD0):  # one data byte
                if i + 1 > n:
                    raise MidiError("Truncated MIDI channel message.")
                raw = bytes([status, track_data[i]])
                i += 1
                yield delta, raw, KIND_OTHER
            else:
                raise MidiError(f"Invalid MIDI status byte 0x{status:02X}.")


def _decode_track(track_data: bytes) -> list[tuple[int, bytes, int]]:
    """Decode a track into ``(delta_ticks, raw_event, kind)`` triples.

    Kept as a convenience wrapper around :func:`_iter_track_events`; streaming
    callers should use the iterator directly to avoid materializing a list.
    """
    return list(_iter_track_events(track_data))


# --------------------------------------------------------------------------
# Parsing / validation
# --------------------------------------------------------------------------

def parse_midi(path: str) -> MidiInfo:
    """Read a MIDI file and return its structure without enforcing track count."""
    if not os.path.isfile(path):
        raise MidiError(f"File not found: {path}")

    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != CHUNK_HEADER:
            raise MidiError("Not a standard MIDI file (missing 'MThd' header).")

        header_len = int.from_bytes(f.read(4), "big")
        if header_len < 6:
            raise MidiError("Invalid MIDI header (length < 6).")
        header = f.read(header_len)
        if len(header) != header_len:
            raise MidiError("Truncated MIDI header.")

        fmt = int.from_bytes(header[0:2], "big")
        ntrks = int.from_bytes(header[2:4], "big")
        division = header[4:6]

        track_chunks: list[bytes] = []
        track_note_counts: list[int] = []
        track_names: list[str] = []
        note_count = 0
        end_of_track = False
        while True:
            chunk_id = f.read(4)
            if chunk_id == b"":
                break
            if chunk_id != CHUNK_TRACK:
                raise MidiError(
                    f"Unexpected chunk {chunk_id!r} in MIDI file (expected {CHUNK_TRACK!r})."
                )
            track_len = int.from_bytes(f.read(4), "big")
            track_data = f.read(track_len)
            if len(track_data) != track_len:
                raise MidiError("Truncated MIDI track data.")
            track_chunks.append(
                CHUNK_TRACK + int.to_bytes(track_len, 4, "big") + track_data
            )
            notes, eot, name = _scan_track(track_data)
            note_count += notes
            end_of_track = end_of_track or eot
            track_note_counts.append(notes)
            track_names.append(name)

    return MidiInfo(
        path=path,
        format=fmt,
        ntrks=ntrks,
        division=division,
        track_chunks=track_chunks,
        note_count=note_count,
        end_of_track=end_of_track,
        input_size=os.path.getsize(path),
        track_note_counts=track_note_counts,
        track_names=track_names,
    )


def _validate_info(info: MidiInfo) -> MidiInfo:
    """Apply the multi-track rules: a valid format and at least one note."""
    if info.format not in MIDI_FORMATS:
        raise MidiError(f"Unsupported MIDI format {info.format} (expected 0 or 1).")

    if max(info.ntrks, len(info.track_chunks)) < 1:
        raise MidiError("The file contains no tracks.")

    if info.note_count == 0:
        raise MidiError("The file contains no note events.")

    return info


def validate_single_track(path: str) -> MidiInfo:
    """Parse ``path`` and require a well-formed MIDI with exactly one note track."""
    info = _validate_info(parse_midi(path))
    actual = max(info.ntrks, len(info.track_chunks))
    if actual != 1:
        raise MidiError(
            f"This file has {actual} tracks. "
            "Systematics TRILLION MIDI maker needs exactly 1 track."
        )
    return info


def validate_multi_track(path: str) -> MidiInfo:
    """Parse ``path`` and require a well-formed MIDI with at least one note track."""
    return _validate_info(parse_midi(path))


def resolve_ram_mode(ram_mode: str, max_ram_mb: int, file_size: int) -> tuple[int, bool]:
    """Return ``(effective_multiplier, capped)`` for a file of ``file_size`` bytes.

    ``ram_mode`` is one of :data:`RAM_MODES` keys. ``max_ram_mb`` (0 =
    unlimited) caps the multiplier so the estimated footprint (file size ×
    multiplier) stays under the budget; the result is never below 1, so a
    file that already exceeds the cap still loads at 1× but is flagged as
    capped.
    """
    target = RAM_MODES.get(ram_mode, 1)
    if not max_ram_mb or max_ram_mb <= 0 or file_size <= 0:
        return target, False
    max_bytes = max_ram_mb * 1024 * 1024
    effective = max(1, min(target, max_bytes // file_size))
    return effective, effective < target


def _apply_ram_mode(info: MidiInfo, effective: int, capped: bool,
                    max_ram_mb: int) -> MidiInfo:
    """Retain extra in-memory copies of the file per the effective RAM mode.

    Double keeps the raw file bytes; triple additionally keeps a joined copy
    of every track chunk. These are real allocations, so the app's footprint
    genuinely scales with the chosen mode (the memory is held, not paged).
    """
    info.ram_mode = effective
    info.ram_capped = bool(capped)
    info.max_ram_mb = int(max_ram_mb or 0)
    if effective >= 2 and info.raw_data is None:
        try:
            with open(info.path, "rb") as f:
                info.raw_data = f.read()
        except OSError:
            info.raw_data = None
    if effective >= 3 and info.linked_data is None:
        info.linked_data = b"".join(info.track_chunks)
    return info


def ram_footprint(info: MidiInfo) -> int:
    """Approximate bytes the app holds in RAM for a loaded ``info``."""
    base = sum(len(c) for c in info.track_chunks)
    extra = 0
    if getattr(info, "ram_mode", 1) >= 2 and info.raw_data is not None:
        extra += len(info.raw_data)
    if getattr(info, "ram_mode", 1) >= 3 and info.linked_data is not None:
        extra += len(info.linked_data)
    return base + extra


def load_midi_fast(path: str, progress_cb=None, ram_mode: str = "normal",
                   max_ram_mb: int = 0) -> MidiInfo:
    """Load + validate a MIDI with the fastest appropriate loader.

    Tiers are note-based: files above :data:`LARGE_NOTES` notes use the Large
    MIDI Loader and files above :data:`MEGA_NOTES` notes use the memory-mapped
    Mega MIDI Loader; smaller files use the default parser. Because the note
    count isn't known until the file is scanned, the dispatcher estimates it
    from the file size (``ESTIMATED_BYTES_PER_NOTE`` bytes per note). Always
    returns a validated multi-track :class:`MidiInfo`, raising
    :class:`MidiError` otherwise.

    ``ram_mode`` selects how many copies of the file stay in memory (see
    :data:`RAM_MODES`); ``max_ram_mb`` (0 = unlimited) caps the effective
    mode so the footprint never exceeds the budget. Preloading needs a real
    byte buffer, so the memory-mapped Mega loader is only used when the
    effective mode is 1×.

    ``progress_cb(notes_done, tracks_done, tracks_total)`` is forwarded to the
    fast loaders (the small-file path completes too quickly to report).
    """
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    effective, capped = resolve_ram_mode(ram_mode, max_ram_mb, size)

    if size < LARGE_THRESHOLD and effective <= 1:
        info = validate_multi_track(path)
    else:
        from . import Large_MIDI_Loader, Mega_MIDI_Loader

        # Preloading (double/triple) needs the file read into real memory, so
        # force the full-read Large loader for those modes regardless of size.
        if effective >= 2 or size < MEGA_THRESHOLD:
            loader = Large_MIDI_Loader
        else:
            loader = Mega_MIDI_Loader
        info = _validate_info(loader.load(path, progress_cb=progress_cb))

    return _apply_ram_mode(info, effective, capped, max_ram_mb)


# --------------------------------------------------------------------------
# Duplication / compression
# --------------------------------------------------------------------------

def make_header(multiplier: int, division: bytes) -> bytes:
    """Build a format-1 MIDI header with ``multiplier`` tracks."""
    return (
        b"MThd"
        + b"\x00\x00\x00\x06"
        + b"\x00\x01"
        + int.to_bytes(multiplier, 2, "big")
        + division
    )


def make_single_track_header(division: bytes) -> bytes:
    """Build a format-0 MIDI header with exactly one (merged) track."""
    return (
        b"MThd"
        + b"\x00\x00\x00\x06"
        + b"\x00\x00"
        + b"\x00\x01"
        + division
    )


def human_size(num: int) -> str:
    """Format a byte count for display (e.g. ``12.4 GB``)."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"  # pragma: no cover


def free_disk_space(path: str) -> int:
    """Free bytes on the drive that will hold ``path`` (0 if it can't be read)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return 0


def _drive_name(path: str) -> str:
    """Human label for the drive holding ``path`` (e.g. ``C:\\``)."""
    abspath = os.path.abspath(path)
    drive = os.path.splitdrive(abspath)[0]
    if drive:
        return drive + "\\"
    return os.path.dirname(abspath) or "."


def _preflight_disk_space(path: str) -> None:
    """Fail immediately if the output drive is already below the reserve."""
    free = free_disk_space(path)
    if 0 < free < DISK_RESERVE:
        raise DiskFullError(
            f"Drive {_drive_name(path)} is nearly full: only "
            f"{human_size(free)} free (the app keeps "
            f"{human_size(DISK_RESERVE)} free). Free up space or output to "
            "a different drive.")


def _ensure_disk_space(path: str, amount: int) -> None:
    """Abort the build if writing ``amount`` bytes would overrun the reserve.

    ``path`` is the final output file; its directory is the drive that also
    holds the temp files, so one check covers everything being written.
    Raises :class:`DiskFullError` when there is not enough room, leaving at
    least :data:`DISK_RESERVE` bytes free on the drive.
    """
    free = free_disk_space(path)
    if free <= 0:
        return  # can't stat the drive; let the OS surface a write error instead
    available = free - DISK_RESERVE
    if available < amount:
        raise DiskFullError(
            f"Not enough disk space on {_drive_name(path)}: the output needs "
            f"about {human_size(amount)} more, but only "
            f"{human_size(max(0, available))} is available after keeping "
            f"{human_size(DISK_RESERVE)} free. Free up space, lower the "
            "multiplier, or output to a different drive.")


def _recompress_file(src_path: str, dst_path: str, filters, pass_num: int,
                     cancel_event, progress2_cb) -> None:
    """Stream-recompress ``src_path`` (an .xz stream) into ``dst_path``.

    Reads in 1 MiB chunks so memory stays flat even for multi-GB files, and
    reports byte progress for the current pass via ``progress2_cb(done, total,
    pass_num)``.
    """
    total = os.path.getsize(src_path)
    done = 0
    with open(src_path, "rb") as src, open(dst_path, "wb") as out:
        compressor = lzma.LZMACompressor(
            format=lzma.FORMAT_XZ,
            check=lzma.CHECK_CRC64,
            filters=filters,
        )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            piece = src.read(1 << 20)  # 1 MiB keeps memory flat
            if not piece:
                break
            _ensure_disk_space(dst_path, len(piece))
            out.write(compressor.compress(piece))
            done += len(piece)
            if progress2_cb is not None:
                progress2_cb(done, total, pass_num)
        out.write(compressor.flush())
        if progress2_cb is not None:
            progress2_cb(total, total, pass_num)


def _run_compression_passes(first_pass, output_path: str, out_dir: str,
                            passes: int, filters, cancel_event,
                            progress2_cb) -> None:
    """Write the raw .xz via ``first_pass(writer)``, then recompress it.

    ``passes`` is 1..3. One pass writes straight to ``output_path``; each
    additional pass recompresses the previous .xz into the next (.xz → .xz.xz
    → .xz.xz.xz). Intermediate stages are spooled to temp files **next to the
    output** (same drive, e.g. a USB stick) and deleted afterwards, so peak
    disk is the final file plus one stage and RAM stays flat.
    """
    if passes <= 1:
        with open(output_path, "wb") as out:
            first_pass(out)
        return

    stage_paths: list[str] = []
    fd, stage_path = tempfile.mkstemp(
        prefix=".trillion_stage_", suffix=".xz", dir=out_dir)
    stage_paths.append(stage_path)
    stage = os.fdopen(fd, "wb")
    try:
        first_pass(stage)
        stage.close()
        stage = None

        src_path = stage_path
        for pass_num in range(2, passes + 1):
            if pass_num == passes:
                dst_path = output_path
            else:
                fd2, dst_path = tempfile.mkstemp(
                    prefix=".trillion_stage_", suffix=".xz", dir=out_dir)
                os.close(fd2)
                stage_paths.append(dst_path)
            _recompress_file(src_path, dst_path, filters, pass_num,
                             cancel_event, progress2_cb)
            src_path = dst_path
    finally:
        if stage is not None:
            try:
                stage.close()
            except Exception:
                pass
        for path in stage_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def _resolve_selection(n_tracks: int, multiply_tracks) -> set:
    """Turn a selection (list of indices or ``None`` = all) into a valid set."""
    if multiply_tracks is None:
        return set(range(n_tracks))
    return {int(i) for i in multiply_tracks if 0 <= int(i) < n_tracks}


def _effective_multiplier(n_tracks: int, selected: set, multiplier: int,
                          reserve: int = 0) -> int:
    """Clamp the multiplier so the emitted track count never exceeds 65,535.

    Unselected tracks are kept once and selected tracks repeat ``multiplier``
    times, so the largest legal multiplier keeps the total at or under the
    16-bit MIDI track-count field. ``reserve`` is the number of tracks that
    will be appended from a merged MIDI (also written once), so they are
    reserved out of the track budget here.
    """
    if not selected:
        return 0
    unselected = n_tracks - len(selected) + max(0, int(reserve))
    max_multiplier = max(1, (MAX_TRACKS - unselected) // len(selected))
    return max(1, min(multiplier, max_multiplier))


def check_merge_compatible(info: MidiInfo, append_info: "MidiInfo | None") -> None:
    """Reject a merge MIDI whose timing division differs from the main file.

    A format-1 file has one division for every track, so a merged track with
    a different ticks-per-quarter-note value would play at the wrong speed.
    """
    if append_info is None:
        return
    if append_info.division != info.division:
        raise MidiError(
            "The MIDI you want to merge uses a different timing division "
            f"({int.from_bytes(append_info.division, 'big')} vs "
            f"{int.from_bytes(info.division, 'big')}). Re-export it with the "
            "same division as the main file so both play at the same speed.")


def merge_tracks(append_info: "MidiInfo | None") -> list:
    """Return ``[(chunk, note_count, 1), ...]`` for a merged MIDI's tracks.

    Merged tracks are always written exactly once (never multiplied) and are
    placed after every track of the main file, so the melody sits at the end
    of the output while the duplicated part fills the rest.
    """
    if append_info is None:
        return []
    return [
        (chunk, notes, 1)
        for chunk, notes in zip(append_info.track_chunks,
                                append_info.track_note_counts)
    ]


def _check_track_budget(total_tracks: int) -> None:
    """Refuse a build whose emitted track count would overflow the header."""
    if total_tracks > MAX_TRACKS:
        raise MidiError(
            f"The output would need {total_tracks:,} tracks, but a MIDI file "
            f"supports at most {MAX_TRACKS:,}. Lower the multiplier, multiply "
            "fewer tracks, or merge a MIDI with fewer tracks.")


def plan_build(info: MidiInfo, multiplier: int,
               multiply_tracks=None,
               append_info: "MidiInfo | None" = None) -> tuple[int, int, int, int]:
    """Compute ``(total_tracks, total_notes, uncompressed_size, eff_multiplier)``.

    ``multiply_tracks`` lists the source track indices to repeat (``None`` =
    every track). Unselected tracks are written once. ``append_info`` is an
    optional second MIDI whose tracks are written once each at the very end
    (unduplicated) and count toward the 65,535-track budget. The multiplier
    is clamped so the emitted track count never exceeds 65,535.
    """
    n = len(info.track_chunks)
    selected = _resolve_selection(n, multiply_tracks)
    merged = merge_tracks(append_info)
    eff = _effective_multiplier(n, selected, multiplier, reserve=len(merged))
    total_tracks = 0
    total_notes = 0
    total_bytes = 0
    for i in range(n):
        reps = eff if i in selected else 1
        total_tracks += reps
        total_notes += info.track_note_counts[i] * reps
        total_bytes += len(info.track_chunks[i]) * reps
    for chunk, notes, reps in merged:
        total_tracks += reps
        total_notes += notes * reps
        total_bytes += len(chunk) * reps
    header = make_header(min(MAX_TRACKS, total_tracks), info.division)
    return total_tracks, total_notes, len(header) + total_bytes, eff


def plan_note_build(info: MidiInfo, multiplier: int,
                    multiply_tracks=None,
                    append_info: "MidiInfo | None" = None) -> tuple[int, int, int, int]:
    """Plan a note-multiplication run (no track-count limit applies).

    Returns ``(track_count, total_notes, uncompressed_size_estimate,
    eff_multiplier)``. The output is always a single merged track, so
    ``track_count`` is 1; ``eff_multiplier`` is the unclamped multiplier
    because only the 16-bit *track* field is limited, not the note count.
    ``append_info``'s notes are merged into that same track once
    (unduplicated), so they add to the total without being multiplied.
    """
    n = len(info.track_chunks)
    selected = _resolve_selection(n, multiply_tracks)
    if not selected:
        return 1, 0, 0, 0
    selected_notes = sum(info.track_note_counts[i] for i in selected)
    unselected_notes = sum(
        info.track_note_counts[i] for i in range(n) if i not in selected)
    merged_notes = sum(append_info.track_note_counts) if append_info else 0
    # Selected tracks' notes are multiplied; unselected tracks and the merged
    # MIDI are merged in once (the same "multiply vs. keep" rule as track
    # duplication).
    total_notes = selected_notes * multiplier + unselected_notes + merged_notes
    # Notes dominate the track bytes, so scale the selected chunks by the
    # multiplier for a close-enough estimate (exact size is measured after
    # the merged track is built).
    estimate = (8 + 6) + sum(
        len(info.track_chunks[i]) * (multiplier if i in selected else 1)
        for i in range(n))
    if append_info is not None:
        estimate += sum(len(c) for c in append_info.track_chunks)
    return 1, total_notes, estimate, multiplier


def _track_event_stream(track_data: bytes, multiply: bool):
    """Yield ``(absolute_ticks, raw_event, kind, multiply)`` for one track.

    Deltas are accumulated into absolute times so several tracks can be
    merged chronologically. End-of-Track events are dropped (a fresh one is
    appended by the caller).
    """
    absolute = 0
    for delta, raw, kind in _iter_track_events(track_data):
        if kind == KIND_EOT:
            continue
        absolute += delta
        yield absolute, raw, kind, multiply


def _note_event_stream(info: MidiInfo, multiplier: int, selected: set,
                       append_info: "MidiInfo | None" = None):
    """Yield ``(delta, raw_event, kind, reps)`` for one merged, multiplied track.

    Every track is decoded lazily and merged chronologically (by absolute
    time) with a k-way merge, so memory stays flat instead of materializing
    every event in a list and sorting it (which froze the UI on big files).
    A merged MIDI's tracks (``append_info``) join the same chronological
    merge but are never multiplied — their notes are written once.

    Each unique event is yielded **once**; ``reps`` is how many copies the
    caller should write (``multiplier`` for note-on/note-off events in a
    selected track, else 1). The first copy keeps its delta and the remaining
    ``reps - 1`` copies use delta 0, so the caller can expand them with a
    single C-speed ``bytes * n`` repetition instead of iterating once per
    copy — that per-copy loop is what made large multipliers crawl.
    End-of-Track events are dropped and a single new one is yielded at the
    end.
    """
    streams = [
        _track_event_stream(info.track_chunks[idx][8:], idx in selected)
        for idx in range(len(info.track_chunks))
    ]
    if append_info is not None:
        streams += [
            _track_event_stream(chunk[8:], False)
            for chunk in append_info.track_chunks
        ]

    previous = 0
    for absolute, raw, kind, multiply in heapq.merge(
            *streams, key=lambda entry: entry[0]):
        delta = absolute - previous
        previous = absolute
        if multiply and kind in (KIND_NOTE_ON, KIND_NOTE_OFF):
            reps = multiplier
        else:
            reps = 1
        yield delta, raw, kind, reps
    yield 0, b"\xff\x2f\x00", KIND_EOT, 1  # single End-of-Track at the very end


def _compress_once_blocks(chunks, preset: int, archive_format: str = "xz",
                          cancel_event=None, progress_cb=None):
    """Compress each unique chunk once, split into parallel segments.

    Returns ``{chunk_bytes: [compressed_segment_streams, ...]}`` in segment
    order. Every segment is a standalone stream (XZ stream, gzip member,
    bzip2 stream or zstd frame), so writing the blocks of a chunk ``reps``
    times produces valid concatenated output that decompresses to the chunk
    repeated ``reps`` times — the exact layout track duplication needs.
    Splitting a chunk into :data:`SEGMENT_SIZE` pieces lets the compressor
    run on several cores at once (the C codecs release the GIL), which is
    what makes multi-GB inputs finish in minutes instead of hours.
    ``progress_cb(bytes_done)`` reports compressed bytes as segments complete
    (used to keep the UI status moving during the encode phase).
    """
    tasks = []  # (chunk, segment_start, segment_bytes)
    for chunk in chunks:
        for start in range(0, len(chunk), SEGMENT_SIZE):
            tasks.append((chunk, start, chunk[start:start + SEGMENT_SIZE]))
    if not tasks:
        return {}

    def _compress(seg: bytes) -> bytes:
        return _compress_block(archive_format, seg, preset)

    if len(tasks) == 1:
        chunk, _start, seg = tasks[0]
        return {chunk: [_compress(seg)]}

    blocks: dict = {}
    done_bytes = 0
    workers = min(os.cpu_count() or 1, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_compress, seg): (chunk, start)
            for chunk, start, seg in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            chunk, start = futures[future]
            result = future.result()
            blocks.setdefault(chunk, {})[start] = result
            done_bytes += len(result)
            if progress_cb is not None:
                progress_cb(done_bytes)
    return {
        chunk: [blocks[chunk][start] for start in sorted(blocks[chunk])]
        for chunk in blocks
    }


def build_duplicated_xz(
    input_path: str,
    output_path: str,
    multiplier: int,
    preset: int = 6,
    fast: bool = False,
    compress_passes: int = 1,
    progress_cb=None,
    progress2_cb=None,
    cancel_event=None,
    info: MidiInfo | None = None,
    multiply_tracks=None,
    speed: str = "balanced",
    archive_format: str = "xz",
    append_info: "MidiInfo | None" = None,
) -> BuildResult:
    """Duplicate the selected MIDI tracks ``multiplier`` times into ``output_path``.

    ``multiply_tracks`` lists the source track indices to repeat (``None`` =
    every track); unselected tracks are written once. ``append_info`` is an
    optional second MIDI (already parsed) whose tracks are appended after
    everything else, each written exactly once — the "duplicate this part,
    then lay the melody underneath unduplicated" workflow. Merged tracks
    count toward the 65,535-track limit, so the multiplier is capped to make
    room for them. ``progress_cb(done,
    total, notes_done, bytes_done)`` reports progress in written track chunks
    plus cumulative notes/bytes; ``progress2_cb(done, total, pass_num)``
    reports each optional extra compression pass (.xz.xz / .xz.xz.xz) in
    bytes. ``cancel_event`` is an optional :class:`threading.Event`.

    ``info`` is an optional already-parsed :class:`MidiInfo` (e.g. the one the
    UI keeps in memory after the file is chosen). When provided the file is
    **not** re-read from disk, so repeated runs skip the slow parse step.

    Two write strategies are used. For small inputs (or ``fast=True``) each
    unique chunk is compressed **once** and the compressed blocks are
    repeated — concatenated XZ streams, near-instant even at high
    multipliers. Large inputs are also handled this way automatically
    (:data:`AUTO_CONCAT_INPUT`), because streaming every copy through one
    continuous LZMA stream would mean ``chunk_size * multiplier`` of encoder
    work. The continuous stream is only kept where it is both fast and
    genuinely smaller (small files, little repetition). Big chunks are split
    into :data:`SEGMENT_SIZE` segments compressed in parallel, so multi-GB
    tracks use every core instead of one.

    ``speed`` is one of :data:`MERGE_SPEEDS` keys ("balanced", "fast",
    "turbo") and clamps the LZMA preset downward, trading a larger file for
    throughput — the same tiers the note-merge path uses. Extra compression
    passes (``compress_passes`` > 1) stream from a temp file next to the
    output instead of buffering the whole first pass in memory.
    """
    if not MIN_TRACKS <= multiplier <= MAX_TRACKS:
        raise MidiError(
            f"Multiplier must be between {MIN_TRACKS} and {MAX_TRACKS}, got {multiplier}."
        )
    if not 0 <= preset <= 9:
        raise MidiError(f"Compression preset must be 0-9, got {preset}.")

    compress_passes = max(1, min(3, int(compress_passes)))
    fmt = _norm_format(archive_format)
    if not _format_available(fmt):
        raise MidiError(
            f"The {fmt.upper()} format is not available in this build of the app.")

    # Resolve the output to an absolute path so the final file, the temp
    # staging file, and the free-space check all target the same drive (e.g.
    # a USB/external drive) — never the system temp dir on C:.
    output_path = os.path.abspath(output_path)

    if info is None:
        info = validate_multi_track(input_path)

    n = len(info.track_chunks)
    selected = _resolve_selection(n, multiply_tracks)
    if not selected:
        raise MidiError("Select at least one track to multiply.")
    check_merge_compatible(info, append_info)

    # Plan first so the capped multiplier and totals are shared with the UI.
    total_tracks, total_notes, uncompressed, eff = plan_build(
        info, multiplier, multiply_tracks, append_info=append_info)
    _check_track_budget(total_tracks)

    # (chunk bytes, note count, repetitions) in output order. The merged
    # MIDI's tracks come last, so the melody sits under the duplicated part.
    order = [
        (info.track_chunks[i], info.track_note_counts[i],
         eff if i in selected else 1)
        for i in range(n)
    ] + merge_tracks(append_info)

    header = make_header(min(MAX_TRACKS, total_tracks), info.division)

    speed_cfg = MERGE_SPEEDS.get(speed, MERGE_SPEEDS["balanced"])
    # Lower presets compress far faster (at the cost of a bigger file); the
    # speed tier clamps the user's preset downward instead of overriding it.
    effective_preset = min(preset, speed_cfg["preset_max"])
    started = time.perf_counter()
    step = max(1, total_tracks // 1000)  # ~1000 progress callbacks at most

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    _preflight_disk_space(output_path)

    # Pick the write strategy up front. Streaming every copy through one
    # LZMA stream is only cheap when the total encoder input is small; above
    # that (or when the user ticked "Fast mode") compress each unique chunk
    # once and repeat the compressed blocks instead.
    total_input = sum(len(chunk) * reps for chunk, _nc, reps in order)
    total_chunk = sum(len(chunk) for chunk, _nc, _reps in order)
    use_concat = fast or (
        total_input > AUTO_CONCAT_INPUT or
        total_input > total_chunk * AUTO_CONCAT_RATIO)

    state = {"done": 0, "notes": 0, "bytes": len(header)}
    # Filters are used only for extra recompression passes, which are always
    # XZ on top of whatever the base format was (.xz.xz / .gz.xz / .zip.xz).
    filters = [{"id": lzma.FILTER_LZMA2, "preset": effective_preset}]
    _zip_track_seq = [0]

    def _report() -> None:
        if progress_cb is not None and (
                state["done"] % step == 0 or state["done"] == total_tracks):
            progress_cb(state["done"], total_tracks, state["notes"],
                        state["bytes"])

    def _after_chunk(chunk: bytes, nc: int) -> None:
        state["done"] += 1
        state["notes"] += nc
        state["bytes"] += len(chunk)
        _report()

    def _check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()

    def _first_pass(out) -> None:
        if fmt == "zip":
            # One zip entry per emitted block. Each unique chunk is deflated
            # once and the compressed bytes are reused for every repetition,
            # so 65,535-track outputs stay near-instant.
            zw = _ZipConcatWriter(
                out, compresslevel=effective_preset,
                ensure_space=lambda amt: _ensure_disk_space(output_path, amt),
                cancel_event=cancel_event)
            zw.write_entry("header.mid", header)
            compressed = {}  # chunk -> (crc, cdata)
            for chunk, nc, reps in order:
                if chunk not in compressed:
                    _check_cancel()
                    crc = zlib.crc32(chunk) & 0xFFFFFFFF
                    comp = zlib.compressobj(
                        level=effective_preset, method=zlib.DEFLATED, wbits=-15)
                    cdata = comp.compress(chunk) + comp.flush()
                    compressed[chunk] = (crc, cdata)
                crc, cdata = compressed[chunk]
                for _ in range(reps):
                    _check_cancel()
                    zw.write_entry_precompressed(
                        f"track_{_zip_track_seq[0]:05d}.mid",
                        len(chunk), cdata, crc)
                    _zip_track_seq[0] += 1
                    _after_chunk(chunk, nc)
            zw.finish()
        elif fmt == "7z":
            # A solid archive: one continuous LZMA2 pass over the whole
            # uncompressed MIDI, streamed so memory stays flat. 7z has no
            # "repeat the same compressed block" trick, so this is the only
            # sane write strategy for a container format.
            reader = _MidStreamReader(
                uncompressed, header, order,
                on_chunk=lambda chunk, nc: _after_chunk(chunk, nc),
                on_bytes=lambda b: _ensure_disk_space(output_path, b),
                cancel_event=cancel_event)
            _write_seven_zip(out, effective_preset, "output.mid", reader)
        elif use_concat:
            # Compress each unique part once (in parallel segments for big
            # chunks), then repeat the compressed blocks. Concatenated
            # XZ/gzip/bzip2/zstd streams are valid: the output decompresses
            # to header + chunk * reps.
            out.write(_compress_block(fmt, header, effective_preset))
            block_map = _compress_once_blocks(
                [chunk for chunk, _nc, _reps in order],
                effective_preset,
                archive_format=fmt,
                cancel_event=cancel_event,
                progress_cb=(lambda done: progress_cb(
                    0, total_tracks, 0, done)) if progress_cb else None)
            for chunk, nc, reps in order:
                blocks = block_map[chunk]
                for _ in range(reps):
                    _check_cancel()
                    for block in blocks:
                        _ensure_disk_space(output_path, len(block))
                        out.write(block)
                    _after_chunk(chunk, nc)
        else:
            # One continuous stream: the encoder exploits the repetition and
            # the result is dramatically smaller than the fast path.
            compressor = _make_stream_compressor(fmt, effective_preset)
            out.write(compressor.compress(header))
            for chunk, nc, reps in order:
                for _ in range(reps):
                    _check_cancel()
                    _ensure_disk_space(output_path, len(chunk) + 1024)
                    out.write(compressor.compress(chunk))
                    _after_chunk(chunk, nc)
            out.write(compressor.flush())

    _run_compression_passes(_first_pass, output_path, out_dir,
                            compress_passes, filters, cancel_event,
                            progress2_cb)

    elapsed = time.perf_counter() - started
    return BuildResult(
        output_path=output_path,
        multiplier=multiplier,
        track_count=total_tracks,
        total_notes=total_notes,
        input_size=info.input_size,
        uncompressed_size=uncompressed,
        compressed_size=os.path.getsize(output_path),
        elapsed=elapsed,
        fast_mode=fast,
        compress_passes=compress_passes,
        selected_tracks=tuple(sorted(selected)),
        per_track_notes=tuple(info.track_note_counts),
        effective_multiplier=eff,
        archive_format=fmt,
        append_tracks=len(merge_tracks(append_info)),
        append_notes=(sum(append_info.track_note_counts)
                      if append_info is not None else 0),
        append_name=(os.path.basename(append_info.path)
                     if append_info is not None else ""),
    )


def build_note_multiplied_xz(
    input_path: str,
    output_path: str,
    multiplier: int,
    preset: int = 6,
    fast: bool = False,
    compress_passes: int = 1,
    progress_cb=None,
    progress2_cb=None,
    cancel_event=None,
    info: MidiInfo | None = None,
    multiply_tracks=None,
    speed: str = "balanced",
    archive_format: str = "xz",
    append_info: "MidiInfo | None" = None,
) -> BuildResult:
    """Duplicate every note in the selected tracks and merge them into one track.

    Unlike :func:`build_duplicated_xz` (which repeats whole tracks, hitting
    the 65,535-track limit), this multiplies each note-on/note-off ``multiplier``
    times and writes the result as a single format-0 track, so the note count
    is limited only by memory and disk, not the MIDI track field.

    Progress is reported in notes: ``progress_cb(done, total, notes_done,
    bytes_done)`` where ``done`` and ``total`` are note-on counts.
    ``progress2_cb(done, total, pass_num)`` reports each optional extra
    compression pass (.xz.xz / .xz.xz.xz) in bytes. ``fast`` is accepted for
    API symmetry but is ignored — the merged track is not a simple
    repetition, so it is always streamed.

    ``speed`` is one of ``"balanced"``, ``"fast"`` or ``"turbo"`` (see
    :data:`MERGE_SPEEDS`). Faster tiers lower the LZMA preset and buffer more
    merged-track bytes before each compress() call, trading output size for
    throughput.
    """
    if not MIN_TRACKS <= multiplier <= MAX_TRACKS:
        raise MidiError(
            f"Multiplier must be between {MIN_TRACKS} and {MAX_TRACKS}, got {multiplier}.")
    if not 0 <= preset <= 9:
        raise MidiError(f"Compression preset must be 0-9, got {preset}.")

    compress_passes = max(1, min(3, int(compress_passes)))
    fmt = _norm_format(archive_format)
    if not _format_available(fmt):
        raise MidiError(
            f"The {fmt.upper()} format is not available in this build of the app.")

    # Resolve the output to an absolute path so the final file, the temp
    # staging file, and the free-space check all target the same drive (e.g.
    # a USB/external drive) — never the system temp dir on C:.
    output_path = os.path.abspath(output_path)

    if info is None:
        info = validate_multi_track(input_path)

    n = len(info.track_chunks)
    selected = _resolve_selection(n, multiply_tracks)
    if not selected:
        raise MidiError("Select at least one track to multiply.")
    check_merge_compatible(info, append_info)

    track_count, total_notes, estimate, eff = plan_note_build(
        info, multiplier, multiply_tracks, append_info=append_info)

    # Fail fast when the merged track is already obviously over the MIDI
    # format's 4 GB per-track limit, instead of building for minutes/hours and
    # then dying on ``int.to_bytes(track_len, 4, ...)`` with an opaque
    # "int too big to convert" OverflowError.
    if estimate > MAX_TRACK_BYTES:
        raise MidiError(
            "The merged track would be about "
            f"{human_size(estimate)}, but a MIDI track is limited to "
            f"{human_size(MAX_TRACK_BYTES)} (4 GB). Lower the multiplier or "
            "use track duplication instead.")

    header = make_single_track_header(info.division)
    speed_cfg = MERGE_SPEEDS.get(speed, MERGE_SPEEDS["balanced"])
    # Lower presets compress far faster (at the cost of a bigger file); the
    # speed tier clamps the user's preset downward instead of overriding it
    # so the normal 0-9 preset still works in "balanced" mode.
    effective_preset = min(preset, speed_cfg["preset_max"])
    buffer_size = speed_cfg["buffer"]
    # Filters are used only for extra recompression passes, which are always
    # XZ on top of whatever the base format was.
    filters = [{"id": lzma.FILTER_LZMA2, "preset": effective_preset}]
    started = time.perf_counter()
    step = max(1, total_notes // 1000)  # ~1000 progress callbacks at most

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    _preflight_disk_space(output_path)

    state = {"notes": 0, "bytes": len(header) + 8}

    def _check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()

    def _report() -> None:
        if progress_cb is not None and (
                state["notes"] % step == 0 or state["notes"] == total_notes):
            progress_cb(state["notes"], total_notes, state["notes"],
                        state["bytes"])

    # Two streaming passes and NO temp file on disk. The old design wrote the
    # compressed track to a temp file and then copied it into the output, so
    # the drive briefly held ~2x the final size (temp + output) — a big merge
    # filled a 230 GB drive and aborted halfway through. Instead we first
    # count the exact uncompressed track length (cheap: no compression, no
    # disk writes), then write the header block — whose track-length field is
    # now known — straight into the final file and stream the compressed
    # track after it. Peak disk usage is just the final file itself.
    track_len = 0
    for delta, raw, kind, reps in _note_event_stream(
            info, multiplier, selected, append_info):
        _check_cancel()
        enc_len = 1 if delta == 0 else len(_encode_vlq(delta))
        # Each duplicate is written as ``b"\x00" + raw``, i.e. one delta byte
        # plus the raw event, so the piece is enc_len + len(raw) + (1 +
        # len(raw)) * (reps - 1) == enc_len + len(raw) * reps + (reps - 1).
        piece_len = enc_len + len(raw) * reps + (reps - 1)
        track_len += piece_len
        if track_len > MAX_TRACK_BYTES:
            raise MidiError(
                "The merged track exceeds the MIDI format's 4 GB "
                "per-track limit. Lower the multiplier or use track "
                "duplication instead.")
        state["bytes"] += piece_len
    uncompressed = len(header) + 8 + track_len

    # The header (with the now-known track length) is a tiny standalone
    # compressed stream concatenated in front of the compressed track.
    # Concatenated XZ/gzip/bzip2/zstd streams are valid and already used by
    # the "fast" duplication path. (7z skips this: the reader streams the
    # raw header bytes into the solid archive instead.)
    head_raw = header + CHUNK_TRACK + int.to_bytes(track_len, 4, "big")
    if fmt in ("7z", "zip"):
        # 7z and zip stream the raw head bytes into the archive instead.
        head_block = b""
    else:
        head_block = _compress_block(fmt, head_raw, effective_preset)

    def _merged_pieces():
        """Yield the merged-track bytes piece by piece."""
        for delta, raw, kind, reps in _note_event_stream(
                info, multiplier, selected, append_info):
            _check_cancel()
            enc_delta = b"\x00" if delta == 0 else _encode_vlq(delta)
            if reps > 1:
                piece = enc_delta + raw + (b"\x00" + raw) * (reps - 1)
            else:
                piece = enc_delta + raw
            state["bytes"] += len(piece)
            if kind == KIND_NOTE_ON:
                state["notes"] += reps
                _report()
            yield piece

    def _write_track(out) -> None:
        """Compress the merged track straight into ``out`` after ``head_block``."""
        compressor = _make_stream_compressor(fmt, effective_preset)
        # Buffer the merged events and compress in bulk. Two things used to
        # make this path crawl at a few MB/s: calling compress() once per
        # ~5-byte event, and looping once per *duplicate* of every note. The
        # first copy keeps its delta and the remaining copies use delta 0, so
        # they are expanded with one C-speed ``bytes * n`` repetition instead
        # of a Python loop per copy. Batching removes the per-event compress()
        # overhead; the repetition removes the per-copy loop.
        buf = bytearray()
        for piece in _merged_pieces():
            buf += piece
            if len(buf) >= buffer_size:
                _ensure_disk_space(output_path, len(buf))
                out.write(compressor.compress(bytes(buf)))
                buf.clear()
        if buf:
            _ensure_disk_space(output_path, len(buf))
            out.write(compressor.compress(bytes(buf)))
        out.write(compressor.flush())

    def _first_pass(out) -> None:
        if fmt == "7z":
            # Solid archive: stream the whole merged MIDI into one 7z entry.
            reader = _NoteMergeReader(
                uncompressed, head_raw, _merged_pieces(),
                cancel_event=cancel_event,
                ensure_space=lambda b: _ensure_disk_space(output_path, b))
            _write_seven_zip(out, effective_preset, "output.mid", reader)
        elif fmt == "zip":
            # One zip entry holding the whole merged MIDI. The track is
            # generated on the fly, so it is deflated incrementally and the
            # sizes land in a data descriptor after the stream.
            def _pieces_with_head():
                yield head_raw
                yield from _merged_pieces()
            zw = _ZipConcatWriter(
                out, compresslevel=effective_preset,
                ensure_space=lambda amt: _ensure_disk_space(output_path, amt),
                cancel_event=cancel_event)
            zw.write_stream_entry("output.mid", uncompressed,
                                  _pieces_with_head())
            zw.finish()
        else:
            out.write(head_block)
            _write_track(out)

    _run_compression_passes(_first_pass, output_path, out_dir,
                            compress_passes, filters, cancel_event,
                            progress2_cb)

    elapsed = time.perf_counter() - started
    return BuildResult(
        output_path=output_path,
        multiplier=multiplier,
        track_count=track_count,
        total_notes=total_notes,
        input_size=info.input_size,
        uncompressed_size=uncompressed,
        compressed_size=os.path.getsize(output_path),
        elapsed=elapsed,
        fast_mode=fast,
        compress_passes=compress_passes,
        selected_tracks=tuple(sorted(selected)),
        per_track_notes=tuple(info.track_note_counts),
        effective_multiplier=eff,
        note_mode=True,
        archive_format=fmt,
        append_tracks=len(merge_tracks(append_info)),
        append_notes=(sum(append_info.track_note_counts)
                      if append_info is not None else 0),
        append_name=(os.path.basename(append_info.path)
                     if append_info is not None else ""),
    )
