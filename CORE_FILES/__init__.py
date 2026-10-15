"""CORE_FILES — the important Python files for Systematics TRILLION MIDI maker.

This package holds the application's real logic so the top-level launcher
(``trillion.py``) stays tiny:

* ``midi_engine`` — MIDI parsing, validation and XZ duplication.
* ``settings``    — persistent user preferences.
* ``app``         — the tkinter desktop UI (menus, dialogs, progress).
* ``icons``       — canvas-drawn icon glyphs used by the UI.
* ``lang_loader`` — reads the ``.SMLF`` language files in ``lang/``.
* ``updater``     — checks the hosted JSON feed for newer versions — this is
                    the only part of the app that uses the network.
* ``icon.png``    — the application icon used by the window.
"""

__version__ = "1.6.0.0"
APP_NAME = "Systematics TRILLION MIDI maker"
