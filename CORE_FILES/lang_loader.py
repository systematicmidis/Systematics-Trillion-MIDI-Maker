"""Language (.SMLF) loading for Systematics TRILLION MIDI maker.

A ``.SMLF`` file is a plain-text ``key = value`` document (openable in
Notepad). The app looks in ``CORE_FILES/lang/`` for every ``*.SMLF`` file,
and the active language is chosen from Settings. This module parses those
files and exposes a single ``t(key, **placeholders)`` lookup used by the UI.

Format recap (also documented inside each .SMLF file)::

    [section]
    key = Some text with a {placeholder}

Lines starting with ``#`` are comments, blank lines are ignored, ``[..]``
lines are section headers, and the value is everything after the first ``=``
(whitespace-trimmed). ``{placeholders}`` are replaced by the app at runtime
and must be preserved when translating.
"""

from __future__ import annotations

import os
import re

LANG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")
DEFAULT_LANGUAGE = "English"
_EXT = ".SMLF"


def list_languages() -> list[str]:
    """Return every available language name (from the .SMLF filenames).

    The display name is the filename without the ``.SMLF`` extension. English
    is always listed first (it is the built-in fallback).
    """
    names: list[str] = []
    if os.path.isdir(LANG_DIR):
        for fname in sorted(os.listdir(LANG_DIR)):
            if fname.lower().endswith(_EXT.lower()):
                names.append(fname[: -len(_EXT)])
    if DEFAULT_LANGUAGE not in names:
        names.insert(0, DEFAULT_LANGUAGE)
    return names


# A key line looks like ``key = value``; the key is a simple identifier.
# Any following lines that are not comments, section headers, or another key
# are continuations of the current value, so multi-line values (help text,
# multi-paragraph dialogs, ...) are preserved with their blank lines intact.
_KEY_RE = re.compile(r"^[A-Za-z0-9_]+\s*=")


def _parse(text: str) -> dict[str, str]:
    """Parse .SMLF text into a ``{key: value}`` dict."""
    strings: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current is not None:
            value = "\n".join(buffer).replace("\\n", "\n")
            strings[current] = value.strip()
        current = None
        buffer = []

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            continue
        if _KEY_RE.match(line):
            flush()
            key, _, value = line.partition("=")
            current = key.strip()
            buffer = [value.strip()]
        elif current is not None:
            buffer.append(line)
        # A stray line before any key is ignored.
    flush()
    return strings


def _read(name: str) -> dict[str, str]:
    """Read a language file to a dict, or ``{}`` when it can't be read."""
    path = os.path.join(LANG_DIR, name + _EXT)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return _parse(f.read())
    except OSError:
        return {}


_ENGLISH: dict[str, str] = _read(DEFAULT_LANGUAGE)

# The currently active language. ``apply`` swaps these; ``t`` reads them.
_current: dict[str, str] = dict(_ENGLISH)
_current_name: str = DEFAULT_LANGUAGE


def apply(name: str) -> None:
    """Switch the active language to ``name`` (falls back to English)."""
    global _current, _current_name
    name = (name or DEFAULT_LANGUAGE).strip()
    strings = _read(name) if name != DEFAULT_LANGUAGE else {}
    _current = strings if strings else _read(DEFAULT_LANGUAGE)
    _current_name = name if strings else DEFAULT_LANGUAGE


def current_name() -> str:
    """Return the name of the active language."""
    return _current_name


def t(key: str, **placeholders) -> str:
    """Return the active language's string for ``key``.

    ``{placeholder}`` tokens are substituted from ``placeholders`` (only
    known keys are replaced, so stray braces in translations are left
    alone). Missing keys fall back to English, then to the key itself so the
    UI never renders an empty label.
    """
    text = _current.get(key)
    if text is None:
        text = _ENGLISH.get(key, key)
    for name, value in placeholders.items():
        text = text.replace("{" + name + "}", str(value))
    return text
