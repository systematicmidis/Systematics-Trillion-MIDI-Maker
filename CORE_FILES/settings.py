"""Persistent user preferences for Systematics TRILLION MIDI maker.

Settings are stored as a small JSON file in the user's home directory so the
application can be moved around freely without carrying state with it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SETTINGS_FILE = Path.home() / ".systematics_trillion_midi_maker.json"

DEFAULTS: dict = {
    "multiplier": 65535,
    "compression_preset": 6,
    "fast_mode": False,
    "archive_format": "xz",     # output container/stream: xz|gz|bz2|zst|zip|7z
    "compress_passes": 1,       # 1 = .xz, 2 = .xz.xz, 3 = .xz.xz.xz
    "note_multiply": False,     # multiply notes & merge into one track instead of repeating tracks
    "merge_speed": "balanced",  # note-merge speed tier: "balanced" | "fast" | "turbo"
    "ram_mode": "normal",      # "normal" | "double" | "triple" memory preload
    "max_ram_mb": 0,            # cap on RAM used by a loaded file (0 = unlimited)
    "remember_input": True,
    "auto_output": True,
    "theme": "dark",          # "dark" or "light"
    "language": "English",     # name of the active .SMLF language file
    "check_updates": True,     # poll the built-in update feed on startup
    "backup_on_update": True,  # keep a _backup_<timestamp>/ folder when updating
    "window_size": "1080p",     # app window preset: "720p" | "1080p" | "1440p"
    "track_selection": {},     # {input_path: [track indices to multiply]}
    "last_input": "",
    "last_output": "",
    "merge_midi": False,        # append a second MIDI (unduplicated) to the output
    "last_merge_input": "",     # path of the MIDI to merge, if any
}


def load(path: Path | str | None = None) -> dict:
    """Load settings, merging them over :data:`DEFAULTS`."""
    target = Path(path) if path is not None else Path(SETTINGS_FILE)
    settings = dict(DEFAULTS)
    try:
        if target.is_file():
            with open(target, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # Migrate the legacy boolean double-compression flag to the
                # new integer pass count.
                if "compress_passes" not in loaded and loaded.get("double_compress"):
                    loaded["compress_passes"] = 2
                settings.update(loaded)
    except (OSError, ValueError):
        # A corrupt/unreadable settings file should never block the app.
        pass
    return _coerce(settings)


def save(settings: dict, path: Path | str | None = None) -> None:
    """Persist ``settings`` to disk (best effort)."""
    target = Path(path) if path is not None else Path(SETTINGS_FILE)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(_coerce(settings), f, indent=2)
    except OSError:
        pass


def reset(path: Path | str | None = None) -> dict:
    """Return defaults and (optionally) delete the stored file."""
    if path is not None:
        try:
            os.remove(path)
        except OSError:
            pass
    return dict(DEFAULTS)


def _coerce(settings: dict) -> dict:
    """Clamp values into sane ranges so a hand-edited file can't break the UI."""
    out = dict(DEFAULTS)
    # Only keys this build still knows about are kept, so state written by an
    # older version (resource packs, news, poll votes, ...) is dropped here.
    for key in DEFAULTS:
        if key in settings:
            out[key] = settings[key]
    out["multiplier"] = max(1, min(65535, int(out.get("multiplier", DEFAULTS["multiplier"]))))
    out["compression_preset"] = max(0, min(9, int(out.get("compression_preset", DEFAULTS["compression_preset"]))))
    out.pop("double_compress", None)  # legacy key, replaced by compress_passes
    for key in ("fast_mode", "note_multiply", "merge_midi",
                "remember_input", "auto_output", "check_updates",
                "backup_on_update"):
        out[key] = bool(out.get(key, DEFAULTS[key]))
    try:
        out["compress_passes"] = max(1, min(3, int(out.get("compress_passes", 1))))
    except (TypeError, ValueError):
        out["compress_passes"] = 1
    out["archive_format"] = str(out.get("archive_format", DEFAULTS["archive_format"])).strip()
    if out["archive_format"] not in ("xz", "gz", "bz2", "zst", "zip", "7z"):
        out["archive_format"] = "xz"
    out["window_size"] = out.get("window_size", DEFAULTS["window_size"])
    if out["window_size"] not in ("720p", "1080p", "1440p"):
        out["window_size"] = "1080p"
    out["ram_mode"] = out.get("ram_mode", DEFAULTS["ram_mode"])
    if out["ram_mode"] not in ("normal", "double", "triple"):
        out["ram_mode"] = "normal"
    out["merge_speed"] = out.get("merge_speed", DEFAULTS["merge_speed"])
    if out["merge_speed"] not in ("balanced", "fast", "turbo"):
        out["merge_speed"] = "balanced"
    out["max_ram_mb"] = max(0, int(out.get("max_ram_mb", DEFAULTS["max_ram_mb"])))
    out["theme"] = out.get("theme", DEFAULTS["theme"])
    if out["theme"] not in ("dark", "light"):
        out["theme"] = "dark"
    out["language"] = str(out.get("language", DEFAULTS["language"])).strip() or "English"
    for key in ("last_input", "last_output", "last_merge_input"):
        out[key] = str(out.get(key, DEFAULTS[key]))
    # Sanitise the per-file track selection map.
    selection = out.get("track_selection", {})
    if not isinstance(selection, dict):
        selection = {}
    clean = {}
    for path, indices in selection.items():
        if isinstance(indices, list):
            clean[str(path)] = [int(i) for i in indices
                                if isinstance(i, int) and i >= 0]
    out["track_selection"] = clean
    return out
