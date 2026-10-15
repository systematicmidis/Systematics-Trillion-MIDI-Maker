"""Generate the small test MIDIs used to try the merge feature.

Run it with::

    python test_files/make_test_midis.py

It writes the files next to this script:

    Arts.mid              1 track, 16 notes, 480 ppqn   <- the part to duplicate
    Melody.mid            1 track,  8 notes, 480 ppqn   <- merge target (unduplicated)
    Melody_960ppqn.mid    1 track,  8 notes, 960 ppqn   <- should be refused (division)
    Merge_2tracks.mid     2 tracks, 12 notes, 480 ppqn  <- merge target with 2 tracks

Everything is plain format-1 MIDI (``MThd`` + ``MTrk`` chunks), no dependencies
or external libraries, so the files open in any MIDI player as well.
"""

from __future__ import annotations

import os
import struct

PPQN = 480


def vlq(value: int) -> bytes:
    """Encode a variable-length quantity (the MIDI delta-time encoding)."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def meta(meta_type: int, payload: bytes) -> bytes:
    return b"\xFF" + bytes([meta_type]) + vlq(len(payload)) + payload


def track_name(name: str) -> bytes:
    return meta(0x03, name.encode("ascii", "replace"))


def tempo(bpm: int = 120) -> bytes:
    usec = int(60_000_000 / bpm)
    return meta(0x51, struct.pack(">I", usec)[1:])


def build_track(name: str, notes: list[tuple[int, int, int]]) -> bytes:
    """notes is a list of (pitch, start_tick, length_ticks).

    Every event carries its own delta time (running status is deliberately
    avoided so the byte stream stays easy to read in a hex editor).
    """
    # (absolute tick, tie-break, event bytes); note-offs (2) sort after
    # note-ons (1) at the same tick so repeats don't cut each other short.
    events: list[tuple[int, int, bytes]] = [
        (0, 0, track_name(name)),
        (0, 0, tempo()),
    ]
    for pitch, start, length in notes:
        events.append((start, 1, bytes([0x90, pitch, 100])))
        events.append((start + length, 2, bytes([0x80, pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    body = bytearray()
    clock = 0
    for tick, _order, event in events:
        body += vlq(tick - clock) + event
        clock = tick
    body += vlq(0) + meta(0x2F, b"")   # end of track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def build_midi(division: int, tracks: list[bytes]) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division)
    return header + b"".join(tracks)


def write(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"wrote {os.path.basename(path)}  ({len(data)} bytes)")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))

    # --- The "arts": 16 notes, one 16th apart (120 ticks at 480 ppqn). --------
    arts = [(60 + (i % 8), i * 120, 120) for i in range(16)]
    write(os.path.join(here, "Arts.mid"),
          build_midi(PPQN, [build_track("Arts", arts)]))

    # --- The "melody": 8 quarter notes, exactly the same division. -----------
    melody = [(72 - i, i * PPQN, PPQN - 10) for i in range(8)]
    write(os.path.join(here, "Melody.mid"),
          build_midi(PPQN, [build_track("Melody", melody)]))

    # --- Same melody at a different division: the app must refuse it. --------
    write(os.path.join(here, "Melody_960ppqn.mid"),
          build_midi(960, [build_track("Melody", melody)]))

    # --- A two-track merge target: both tracks should land once each. --------
    chords = [(40 + (i % 4), i * 240, 240) for i in range(8)]
    bass = [(28 + (i % 2) * 7, i * 480, 480) for i in range(4)]
    write(os.path.join(here, "Merge_2tracks.mid"),
          build_midi(PPQN, [build_track("Chords", chords),
                            build_track("Bass", bass)]))


if __name__ == "__main__":
    main()
