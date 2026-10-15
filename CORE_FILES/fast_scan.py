"""Shared fast MIDI scanning used by the Large and Mega loaders.

The default loader in :mod:`midi_engine` walks every event through a helper
function per variable-length quantity. For tens of millions of notes that
function-call overhead adds up, so these loaders share a single-pass scanner
with the VLQ reads inlined and all status bytes handled in one tight loop.

The scanner is deliberately byte-for-byte compatible with
``midi_engine._scan_track`` (same note-on rule, same structural errors), just
faster. ``build_info`` turns raw file bytes (a ``bytes`` object or an
``mmap``) into the same :class:`~midi_engine.MidiInfo` the rest of the app
consumes.
"""

from __future__ import annotations

from .midi_engine import (
    CHUNK_HEADER,
    CHUNK_TRACK,
    MidiError,
    MidiInfo,
    _decode_name,
)


def scan_track_fast(track_data, progress_cb=None) -> tuple[int, bool, str]:
    """Count note-ons and detect End-of-Track in one inlined pass.

    ``track_data`` may be ``bytes`` or an ``mmap`` slice (anything supporting
    integer indexing and slicing). Returns
    ``(note_count, has_end_of_track, track_name)`` and raises
    :class:`MidiError` for structurally broken tracks.

    ``progress_cb(note_count_so_far)`` is called as the count grows (throttled
    to about once per million notes, plus a final call) so callers can show
    live progress without slowing the scan down.
    """
    n = len(track_data)
    i = 0
    running = 0  # 0 means "no running status yet"
    notes = 0
    eot = False
    track_name = ""
    report_step = 1_000_000
    next_report = report_step

    while i < n:
        # Delta time (validated, value ignored) — VLQ inlined.
        while True:
            byte = track_data[i]
            i += 1
            if not byte & 0x80:
                break
            if i >= n:
                raise MidiError("Truncated variable-length value in MIDI track.")
        if i >= n:
            raise MidiError("MIDI track ends in the middle of an event.")

        first = track_data[i]

        if first == 0xFF:  # meta event
            if i + 1 >= n:
                raise MidiError("Truncated MIDI meta event.")
            meta_type = track_data[i + 1]
            i += 2
            length = 0
            while True:
                if i >= n:
                    raise MidiError("Truncated MIDI meta event.")
                byte = track_data[i]
                i += 1
                length = (length << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    break
            if i + length > n:
                raise MidiError("Truncated MIDI meta event data.")
            if meta_type == 0x2F and length == 0:
                eot = True
            elif meta_type == 0x03 and not track_name:
                track_name = _decode_name(bytes(track_data[i:i + length]))
            i += length

        elif first in (0xF0, 0xF7):  # SysEx event
            i += 1
            length = 0
            while True:
                if i >= n:
                    raise MidiError("Truncated MIDI SysEx event.")
                byte = track_data[i]
                i += 1
                length = (length << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    break
            if i + length > n:
                raise MidiError("Truncated MIDI SysEx event.")
            i += length

        else:
            # Channel (voice) message, possibly using running status.
            if first & 0x80:
                status = first
                running = status
                i += 1
            else:
                if running == 0:
                    raise MidiError("Running status used before any status byte.")
                status = running

            high = status & 0xF0
            if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):  # two data bytes
                if i + 2 > n:
                    raise MidiError("Truncated MIDI channel message.")
                data2 = track_data[i + 1]
                i += 2
                if high == 0x90 and (data2 & 0x7F) > 0:
                    notes += 1
                    if progress_cb is not None and notes >= next_report:
                        progress_cb(notes)
                        next_report += report_step
            elif high in (0xC0, 0xD0):  # one data byte
                if i + 1 > n:
                    raise MidiError("Truncated MIDI channel message.")
                i += 1
            else:
                raise MidiError(f"Invalid MIDI status byte 0x{status:02X}.")

    if progress_cb is not None:
        progress_cb(notes)
    return notes, eot, track_name


def build_info(data, path: str, loader_name: str, progress_cb=None) -> MidiInfo:
    """Parse a MIDI file held in ``data`` (bytes or mmap) into a MidiInfo.

    Mirrors ``midi_engine.parse_midi`` but expects the whole file already in
    memory (or memory-mapped) and records which loader produced the result.

    ``progress_cb(notes_done, tracks_done, tracks_total)`` is invoked while
    scanning so callers can display track/note progress live.
    """
    if len(data) < 14:
        raise MidiError("File too small to be a standard MIDI file.")
    if data[0:4] != CHUNK_HEADER:
        raise MidiError("Not a standard MIDI file (missing 'MThd' header).")

    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise MidiError("Invalid MIDI header (length < 6).")
    header_end = 8 + header_len
    if header_end > len(data):
        raise MidiError("Truncated MIDI header.")

    header = data[8:header_end]
    fmt = int.from_bytes(header[0:2], "big")
    ntrks = int.from_bytes(header[2:4], "big")
    division = bytes(header[4:6])

    track_chunks: list[bytes] = []
    track_note_counts: list[int] = []
    track_names: list[str] = []
    note_count = 0
    eot = False
    pos = header_end
    n = len(data)

    while pos + 8 <= n:
        chunk_id = data[pos:pos + 4]
        if chunk_id != CHUNK_TRACK:
            raise MidiError(
                f"Unexpected chunk {chunk_id!r} in MIDI file "
                f"(expected {CHUNK_TRACK!r})."
            )
        track_len = int.from_bytes(data[pos + 4:pos + 8], "big")
        pos += 8
        if pos + track_len > n:
            raise MidiError("Truncated MIDI track data.")
        track_data = bytes(data[pos:pos + track_len])
        pos += track_len

        base_notes = note_count
        done_tracks = len(track_chunks)

        def track_progress(notes_in_track, _base=base_notes, _done=done_tracks):
            if progress_cb is not None:
                progress_cb(_base + notes_in_track, _done, ntrks)

        notes, t_eot, name = scan_track_fast(
            track_data, progress_cb=track_progress)
        note_count += notes
        eot = eot or t_eot
        track_chunks.append(
            CHUNK_TRACK + int.to_bytes(track_len, 4, "big") + track_data
        )
        track_note_counts.append(notes)
        track_names.append(name)
        if progress_cb is not None:
            progress_cb(note_count, len(track_chunks), ntrks)

    if pos < n:
        raise MidiError("Trailing bytes after the last MIDI track chunk.")

    return MidiInfo(
        path=path,
        format=fmt,
        ntrks=ntrks,
        division=division,
        track_chunks=track_chunks,
        note_count=note_count,
        end_of_track=eot,
        input_size=len(data),
        track_note_counts=track_note_counts,
        track_names=track_names,
        loader=loader_name,
    )
