"""Mega MIDI loader — memory-mapped loading for tracks above ~100 million notes.

For 100M-note files the track alone can be hundreds of megabytes, so reading
the whole file into a Python ``bytes`` object first wastes a full extra copy.
This loader memory-maps the file instead: the operating system pages the data
in on demand and the only heap copy is the single track chunk the app needs
for duplication. The scan itself is the same inlined parser as the Large
loader.
"""

from __future__ import annotations

import mmap

from .fast_scan import build_info
from .midi_engine import MEGA_NOTES

# This loader is selected for files above this many note events.
NOTES_CAPACITY = MEGA_NOTES

LOADER_NAME = "Mega MIDI Loader"


def load(path: str, progress_cb=None):
    """Memory-map and parse ``path``, returning a ``MidiInfo``.

    ``progress_cb(notes_done, tracks_done, tracks_total)`` is called during
    scanning for live progress. Raises :class:`~midi_engine.MidiError` for
    missing/malformed files.
    """
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return build_info(data, path, LOADER_NAME, progress_cb=progress_cb)
