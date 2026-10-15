"""Large MIDI loader — fast single-pass loading for tracks above ~10 million notes.

Used when a MIDI has more than ~10M notes (enough that the default
step-by-step parser gets noticeably slow) but fewer than ~100M. It reads the
whole file once and scans it with the inlined parser from :mod:`fast_scan`,
then returns the same :class:`~midi_engine.MidiInfo` the app already uses.
"""

from __future__ import annotations

from .fast_scan import build_info
from .midi_engine import LARGE_NOTES

# This loader is selected for files above this many note events.
NOTES_CAPACITY = LARGE_NOTES

LOADER_NAME = "Large MIDI Loader"


def load(path: str, progress_cb=None):
    """Read and parse ``path`` quickly, returning a ``MidiInfo``.

    ``progress_cb(notes_done, tracks_done, tracks_total)`` is called during
    scanning for live progress. Raises :class:`~midi_engine.MidiError` for
    missing/malformed files.
    """
    with open(path, "rb") as f:
        data = f.read()
    return build_info(data, path, LOADER_NAME, progress_cb=progress_cb)
