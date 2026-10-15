"""Update checking and self-update for Systematics TRILLION MIDI maker.

The app polls a tiny JSON "feed" hosted on GitHub Pages (or anywhere that
serves plain HTTP/HTTPS) and tells the user when a newer version exists. If
the feed includes a ``url`` pointing at a zip of the new build, the app can
download and install it in place.

Feed format::

    {
      "version": "1.1.0.0",
      "name": "Systematics TRILLION MIDI maker",
      "notes": "What changed in this release...",
      "url": "https://example.com/downloads/systematics-1.1.0.0.zip",
      "versions": [                       # optional: older releases for downgrading
        {
          "version": "1.0.5.0",
          "notes": "Older release notes...",
          "url": "https://example.com/downloads/systematics-1.0.5.0.zip"
        }
      ]
    }

The zip must contain ``trillion.py`` and a ``CORE_FILES/`` folder (optionally
wrapped in a single top-level directory). When the app installs an update it
backs up the current files to ``_backup_<timestamp>/`` beside the app and
replaces them, then restarts itself.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from typing import Optional

DEFAULT_TIMEOUT = 10

# --------------------------------------------------------------------------
# Built-in update feed (hosted on GitHub Pages). This is the only network
# request the app makes.
# --------------------------------------------------------------------------
DEFAULT_FEED_URL = "https://systematicmidis.github.io/feed.json"


class UpdateError(Exception):
    """Raised when the update feed can't be fetched, parsed or installed."""


def parse_version(version: str) -> tuple:
    """Turn '1.2.3.4' into a comparable tuple of ints.

    Non-numeric characters are stripped per segment, so 'v1.2' and '1.2'
    both parse as (1, 2). Missing segments become 0 and the tuple is padded
    to four places so '1.2' and '1.2.0.0' compare equal.
    """
    parts: list[int] = []
    for piece in str(version).strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(feed_version: str, current_version: str) -> bool:
    """True when the feed advertises a version strictly newer than ours."""
    return parse_version(feed_version) > parse_version(current_version)


def fetch_feed(feed_url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Download and parse the update feed.

    Returns ``None`` when the URL is empty (feature disabled). Raises
    :class:`UpdateError` when the feed can't be fetched or is malformed.
    """
    url = (feed_url or "").strip()
    if not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        raise UpdateError("Update URL must start with http:// or https://")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as exc:
        raise UpdateError(f"Could not fetch the update feed: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("Update feed is not a JSON object.")
    version = str(data.get("version", "")).strip()
    if not version:
        raise UpdateError("Update feed is missing a 'version' field.")
    versions: list[dict] = []
    for item in data.get("versions", []):
        if not isinstance(item, dict):
            continue
        v = str(item.get("version", "")).strip()
        if v and v != version:  # skip dupes of the current release
            versions.append({
                "version": v,
                "name": str(item.get("name", "")),
                "notes": str(item.get("notes", "")),
                "url": str(item.get("url", "")),
                "exe_url": str(item.get("exe_url", "")),
            })
    return {
        "version": version,
        "name": str(data.get("name", "")),
        "notes": str(data.get("notes", "")),
        "url": str(data.get("url", "")),
        "exe_url": str(data.get("exe_url", "")),
        "versions": versions,
    }


def download(url: str, dest: str, progress_cb=None, timeout: int = 30) -> None:
    """Download ``url`` to ``dest`` with optional ``progress_cb(done, total)``.

    Raises :class:`UpdateError` on any failure (the partial file is removed).
    """
    url = (url or "").strip()
    if not url:
        raise UpdateError("The update feed has no download URL.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise UpdateError("Download URL must start with http:// or https://")
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Systematics-TRILLION-MIDI-maker"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb is not None:
                        progress_cb(done, total)
    except Exception as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise UpdateError(f"Could not download the update: {exc}") from exc
    if total and done != total:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise UpdateError("Download was incomplete.")


def url_exists(url: str, timeout: int = 8) -> bool:
    """Return True when ``url`` actually responds with a success status.

    Uses a HEAD request when the server supports it, falling back to a tiny
    ranged GET. Any error (404, refused, timeout) is treated as "not
    available" so callers can fall back to an alternative download.
    """
    url = (url or "").strip()
    if not url:
        return False
    headers = {"User-Agent": "Systematics-TRILLION-MIDI-maker"}
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            url, method="GET", headers={**headers, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _root_dir(names: list) -> str:
    """Return the single shared top-level folder name, or '' if none."""
    tops = set()
    for name in names:
        parts = name.replace("\\", "/").split("/")
        if parts and parts[0]:
            tops.add(parts[0])
    return tops.pop() if len(tops) == 1 else ""


def validate_update_zip(zip_path: str) -> None:
    """Check that ``zip_path`` looks like a Systematics TRILLION MIDI maker build.

    Raises :class:`UpdateError` if it isn't a valid zip or is missing the
    expected layout (``trillion.py`` + ``CORE_FILES/``).
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n.replace("\\", "/") for n in zf.namelist()]
    except Exception as exc:
        raise UpdateError(f"Not a valid update zip: {exc}") from exc
    top = _root_dir(names)
    base = (top + "/") if top else ""
    if not any(n == base + "trillion.py" for n in names):
        raise UpdateError("Update package is missing trillion.py.")
    if not any(n.startswith(base + "CORE_FILES/") for n in names):
        raise UpdateError("Update package is missing CORE_FILES/.")


def list_missing_files(zip_path: str, app_root: str) -> list:
    """Dry-run: return the app files missing on disk, without extracting.

    Opens ``zip_path`` (the source package from the feed) and returns the
    relative paths of every ``trillion.py`` / ``CORE_FILES/`` entry that does
    not exist under ``app_root`` yet. Nothing is written — use this to show
    the user what a restore would download before doing it.
    """
    if not os.path.isdir(app_root):
        raise UpdateError(f"App folder not found: {app_root}")
    validate_update_zip(zip_path)
    missing: list = []
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        top = _root_dir(names)
        base = (top + "/") if top else ""
        for name in names:
            if name.endswith("/"):
                continue
            rel = name[len(base):] if base else name
            if rel != "trillion.py" and not rel.startswith("CORE_FILES/"):
                continue
            target = os.path.join(app_root, *rel.split("/"))
            if not os.path.exists(target):
                missing.append(rel)
    return missing


def restore_missing_files(zip_path: str, app_root: str) -> list:
    """Restore any app files that are missing on disk from a release zip.

    Opens ``zip_path`` (the source package from the feed), compares every
    ``trillion.py`` / ``CORE_FILES/`` entry with what exists under
    ``app_root``, and extracts only the files that are missing. Files that
    already exist are left untouched. Returns the list of restored relative
    paths (empty when everything is already present).
    """
    missing = list_missing_files(zip_path, app_root)
    restored: list = []
    if not missing:
        return restored
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        top = _root_dir(names)
        base = (top + "/") if top else ""
        member_of: dict = {}
        for name in names:
            if name.endswith("/"):
                continue
            rel = name[len(base):] if base else name
            member_of.setdefault(rel, name)
        for rel in missing:
            name = member_of.get(rel, rel)
            target = os.path.join(app_root, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            restored.append(rel)
    return restored


def apply_update(zip_path: str, app_root: str, backup: bool = True) -> str:
    """Install a validated update zip over ``app_root``.

    The current ``trillion.py`` and ``CORE_FILES/`` are moved to a backup
    folder ``<app_root>/_backup_<timestamp>/`` first, so a failed install can
    be rolled back manually. When ``backup`` is False the old files are moved
    to a temporary folder that is discarded once the swap succeeds (it is
    still used for rollback if the swap fails, but nothing is left behind).
    Returns the backup path (or '' when no persistent backup was kept).
    """
    if not os.path.isdir(app_root):
        raise UpdateError(f"App folder not found: {app_root}")
    validate_update_zip(zip_path)

    staging = tempfile.mkdtemp(prefix="systematics_update_")
    backup_dir = (os.path.join(app_root, "_backup_" +
                               time.strftime("%Y%m%d_%H%M%S"))
                  if backup
                  else tempfile.mkdtemp(prefix="systematics_swap_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
        # Determine the source folder (strip a single top-level wrapper dir).
        src = staging
        entries = [e for e in os.listdir(staging)
                   if os.path.isdir(os.path.join(staging, e))]
        if (len(entries) == 1
                and not os.path.isfile(os.path.join(staging, "trillion.py"))):
            src = os.path.join(staging, entries[0])

        for required in ("trillion.py", "CORE_FILES"):
            if not os.path.exists(os.path.join(src, required)):
                raise UpdateError(f"Update package is missing {required}.")

        os.makedirs(backup_dir, exist_ok=True)

        try:
            for name in ("trillion.py", "CORE_FILES"):
                current = os.path.join(app_root, name)
                if os.path.exists(current):
                    shutil.move(current, os.path.join(backup_dir, name))
            for name in ("trillion.py", "CORE_FILES"):
                shutil.move(os.path.join(src, name), os.path.join(app_root, name))
        except Exception as exc:
            # Try to roll the backup back so the app isn't left broken.
            for name in ("trillion.py", "CORE_FILES"):
                bak = os.path.join(backup_dir, name)
                if os.path.exists(bak) and not os.path.exists(os.path.join(app_root, name)):
                    try:
                        shutil.move(bak, os.path.join(app_root, name))
                    except OSError:
                        pass
            raise UpdateError(f"Could not install the update: {exc}") from exc
        return backup_dir
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not backup:
            # Discard the temporary swap folder (no persistent backup kept).
            shutil.rmtree(backup_dir, ignore_errors=True)
