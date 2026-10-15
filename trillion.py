#!/usr/bin/env python3
"""Systematics TRILLION MIDI maker — launcher.

Run this file to start the desktop app:

    python trillion.py

All of the real logic lives in the ``CORE_FILES`` package (the MIDI engine,
settings and the tkinter UI).
"""

from CORE_FILES import app

if __name__ == "__main__":
    app.main()
