"""tkinter desktop UI for Systematics TRILLION MIDI maker.

Layout: a sidebar (hamburger header whose label shows the active page, four
navigation items and a footer) plus a main area with a small brand bar, tile
buttons (Start / Stop / Customise / File properties) around a central document
card, and a status line. The style echoes the original H63DNC look but is its
own thing: it supports light and dark themes, a hover glow on the tiles, and
an optional update-feed checker.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import tkinter as tk
from tkinter import filedialog, ttk

from . import APP_NAME, __version__
from . import icons
from . import lang_loader
from . import midi_engine
from . import settings as app_settings
from . import updater

# Short alias so UI code reads ``_t(key, **placeholders)`` for every string.
_t = lang_loader.t

# Built-in update feed (hosted on GitHub Pages).
UPDATE_FEED_URL = updater.DEFAULT_FEED_URL


def _console(message: str, end: str = "\n") -> None:
    """Print to the terminal without crashing under ``pythonw`` (no console)."""
    try:
        print(message, end=end, flush=True)
    except Exception:
        pass

# --------------------------------------------------------------------------
# Palettes (dark + light)
# --------------------------------------------------------------------------

PALETTES = {
    "dark": {
        "SIDEBAR": "#2b2b2b",
        "SIDEBAR_HEADER": "#1f1f1f",
        "NAV_ACTIVE": "#333333",
        "MAIN_BG": "#1b1b1b",
        "TILE": "#262626",
        "TILE_HOVER": "#323232",
        "TILE_DISABLED": "#202020",
        "TILE_BORDER": "#3a3a3a",
        "CARD": "#232323",
        "CARD_BORDER": "#343434",
        "ACCENT": "#00AEEF",
        "ACCENT_HOVER": "#2cc2ff",
        "TEXT": "#f2f2f2",
        "TEXT_MUTED": "#8f8f8f",
        "FIELD": "#242424",
        "BUTTON_SECONDARY": "#2b2b2b",
        "BUTTON_SECONDARY_HOVER": "#3a3a3a",
        "ACCENT_ON": "#04121a",
    },
    "light": {
        "SIDEBAR": "#e6e6e6",
        "SIDEBAR_HEADER": "#f0f0f0",
        "NAV_ACTIVE": "#d8d8d8",
        "MAIN_BG": "#f2f2f2",
        "TILE": "#ffffff",
        "TILE_HOVER": "#e9e9e9",
        "TILE_DISABLED": "#e2e2e2",
        "TILE_BORDER": "#d1d1d1",
        "CARD": "#ffffff",
        "CARD_BORDER": "#d6d6d6",
        "ACCENT": "#0078d4",
        "ACCENT_HOVER": "#1f95e0",
        "TEXT": "#1c1c1c",
        "TEXT_MUTED": "#6a6a6a",
        "FIELD": "#ffffff",
        "BUTTON_SECONDARY": "#e4e4e4",
        "BUTTON_SECONDARY_HOVER": "#d5d5d5",
        "ACCENT_ON": "#ffffff",
    },
}

PALETTE = PALETTES["dark"]  # active palette (module-level so Tile can read it)


def set_palette(name: str) -> None:
    """Activate the dark or the light palette."""
    global PALETTE
    PALETTE = dict(PALETTES.get(name, PALETTES["dark"]))


# Active font family + size ratio, applied app-wide. Set before the UI is
# built.
FONT_FAMILY = "Segoe UI"
FONT_SCALE = 1.0

# Widget sizing, applied app-wide. Read at build time and pushed into every
# button by ``App._apply_sizing()``.
SIZING = {
    "button_padx": 16,     # interior horizontal padding of every button
    "button_pady": 8,      # interior vertical padding of every button
    "button_font": 1.0,    # button text size ratio
    "sidebar_width": 220,  # open sidebar width in px
    "tile_size": 104,      # start/stop/customise/properties tile edge in px
}


def _font(size: int, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, max(6, int(round(size * FONT_SCALE))), weight)


def _walk_widgets(widget) -> list:
    """All descendants of ``widget`` (including itself), breadth-first."""
    found = []
    pending = [widget]
    while pending:
        current = pending.pop(0)
        found.append(current)
        try:
            pending.extend(current.winfo_children())
        except tk.TclError:
            pass
    return found


# Window size presets. The window is locked (not resizable); users pick one of
# these resolutions in Settings.
WINDOW_SIZES = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
}


# --------------------------------------------------------------------------
# Theme (ttk styling)
# --------------------------------------------------------------------------

def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    p = PALETTE
    root.configure(bg=p["SIDEBAR"])
    style.configure(".", background=p["MAIN_BG"], foreground=p["TEXT"],
                    fieldbackground=p["FIELD"], bordercolor=p["TILE_BORDER"],
                    lightcolor=p["FIELD"], darkcolor=p["FIELD"],
                    troughcolor=p["FIELD"], selectbackground=p["ACCENT"],
                    focuscolor=p["ACCENT"])
    style.configure("TFrame", background=p["MAIN_BG"])
    style.configure("TLabel", background=p["MAIN_BG"], foreground=p["TEXT"])
    style.configure("TEntry", fieldbackground=p["FIELD"], foreground=p["TEXT"],
                    insertcolor=p["TEXT"], padding=6)
    style.configure("TSpinbox", fieldbackground=p["FIELD"], foreground=p["TEXT"],
                    insertcolor=p["TEXT"], arrowcolor=p["TEXT"], padding=6)
    style.configure("TCombobox", fieldbackground=p["FIELD"], foreground=p["TEXT"],
                    arrowcolor=p["TEXT"], padding=6)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["FIELD"])],
              foreground=[("readonly", p["TEXT"])],
              selectbackground=[("readonly", p["FIELD"])],
              selectforeground=[("readonly", p["TEXT"])])
    style.configure("TCheckbutton", background=p["MAIN_BG"], foreground=p["TEXT"],
                    focuscolor=p["MAIN_BG"])
    style.map("TCheckbutton",
              background=[("active", p["MAIN_BG"])],
              foreground=[("active", p["TEXT"])])
    style.configure("TRadiobutton", background=p["MAIN_BG"], foreground=p["TEXT"],
                    focuscolor=p["MAIN_BG"])
    style.map("TRadiobutton",
              background=[("active", p["MAIN_BG"])],
              foreground=[("active", p["TEXT"])])
    style.configure("TScrollbar", background=p["FIELD"], troughcolor=p["MAIN_BG"],
                    arrowcolor=p["TEXT"], bordercolor=p["MAIN_BG"])
    style.configure("Accent.Horizontal.TProgressbar", troughcolor=p["FIELD"],
                    background=p["ACCENT"], bordercolor=p["FIELD"],
                    lightcolor=p["ACCENT"], darkcolor=p["ACCENT"])


# --------------------------------------------------------------------------
# Tile button (rounded square + icon + caption)
# --------------------------------------------------------------------------

class Tile:
    """A clickable rounded tile with a canvas icon and a caption below it."""

    def __init__(self, parent, text, subtext=None, command=None, size=104,
                 icon_draw=None, image=None):
        p = PALETTE
        self.frame = tk.Frame(parent, bg=p["MAIN_BG"], cursor="hand2")
        self.canvas = tk.Canvas(self.frame, width=size, height=size,
                                bg=p["MAIN_BG"], highlightthickness=0, bd=0)
        self.canvas.pack()
        self.label = tk.Label(self.frame, text=text, bg=p["MAIN_BG"], fg=p["TEXT"],
                              font=_font(10, "bold"))
        self.label.pack(pady=(6, 0))
        self.sub = None
        if subtext is not None:
            self.sub = tk.Label(self.frame, text=subtext, bg=p["MAIN_BG"],
                                fg=p["TEXT_MUTED"], font=_font(8),
                                wraplength=size + 70, justify="center")
            self.sub.pack(pady=(2, 0))

        self._icon_draw = icon_draw
        self._image = image
        self._size = size
        self._hover = False
        self._enabled = True
        self._command = command
        self._redraw()

        widgets = [self.canvas, self.label]
        if self.sub is not None:
            widgets.append(self.sub)
        for widget in widgets:
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _enter(self, _event) -> None:
        if self._enabled:
            self._hover = True
            self._redraw()

    def _leave(self, _event) -> None:
        self._hover = False
        self._redraw()

    def _click(self, _event) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._hover = False
        self.frame.configure(cursor="hand2" if enabled else "arrow")
        self._redraw()

    def set_sub(self, text: str) -> None:
        if self.sub is not None:
            self.sub.configure(text=text)

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        size = self._size
        p = PALETTE
        if self._enabled and self._hover:
            # Accent glow on hover — a small touch that sets this apart.
            icons.round_rect(canvas, 2, 2, size - 2, size - 2, 22,
                             fill=p["TILE_HOVER"], outline=p["ACCENT"], width=2)
        else:
            fill = p["TILE"] if self._enabled else p["TILE_DISABLED"]
            icons.round_rect(canvas, 2, 2, size - 2, size - 2, 22,
                             fill=fill, outline=p["TILE_BORDER"])
        if self._image is not None:
            canvas.create_image(size / 2, size / 2, image=self._image)
        elif self._icon_draw is not None:
            self._icon_draw(canvas, size / 2, size / 2, size * 0.30)


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

NAV_ITEMS = (
    ("converting", icons.draw_reload),
    ("help", icons.draw_question),
    ("about", icons.draw_info),
    ("settings", icons.draw_gear_outline),
)


def _nav_label(key: str) -> str:
    """Localised caption for a sidebar entry."""
    return _t(f"nav_{key}")


def _loader_label(value: str) -> str:
    """Localised name for the MIDI loader stored on a MidiInfo."""
    key = {"large": "loader_large", "mega": "loader_mega"}.get(
        str(value).lower(), "loader_standard")
    return _t(key)

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = app_settings.load()
        self._theme_name = self.settings.get("theme", "dark")
        lang_loader.apply(self.settings.get("language", "English"))

        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._note_run = False
        self._icon: tk.PhotoImage | None = None

        self._update_queue: queue.Queue = queue.Queue()
        self._update_pending = False
        self._update_notes = ""  # 'notes' text from feed.json, shown in the sidebar
        self._dl_queue: queue.Queue = queue.Queue()
        self._dl_pending = False
        self._dl_widgets = None
        self._dl_dest = ""
        self._downgrade_queue: queue.Queue = queue.Queue()
        self._downgrade_pending = False
        self._downgrade_versions: list[dict] = []
        self._restore_queue: queue.Queue = queue.Queue()
        self._restore_pending = False
        self._load_queue: queue.Queue = queue.Queue()
        self._load_pending = False
        self._load_token = 0
        self._load_manual = False

        self.info: midi_engine.MidiInfo | None = None
        self.track_bytes = 0
        self.selected_tracks: list[int] = []
        # The optional "merge another MIDI" file: parsed like the main input
        # but in its own background load, so the UI never blocks on it.
        self.merge_info: midi_engine.MidiInfo | None = None
        self._merge_queue: queue.Queue = queue.Queue()
        self._merge_pending = False
        self._merge_token = 0
        self.current_page = "converting"
        self._scroll_canvas: tk.Canvas | None = None

        root.title(_t("app_name"))
        self._apply_window_size()
        self._apply_palette()
        apply_theme(root)
        self._build_vars()
        self._load_assets()
        self._build_sidebar()
        self._build_pages()
        self._add_traces()
        self._apply_settings()
        self._select_page("converting")
        self._load_icon()
        _enable_dark_titlebar(root, self._theme_name == "dark")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        if self.settings.get("check_updates"):
            # Let the window appear before any network work.
            self.root.after(1500, lambda: self._check_updates(manual=False))

    # -- state -------------------------------------------------------------

    def _apply_palette(self) -> None:
        """Activate the dark or the light palette."""
        global FONT_FAMILY, FONT_SCALE
        set_palette(self._theme_name)
        FONT_FAMILY = "Segoe UI"
        FONT_SCALE = 1.0

    def _apply_sizing(self) -> None:
        """Apply the active sizing to the built widget tree.

        Button interior padding and button font ratio are pushed into every
        ``tk.Button`` in the window (this is what makes buttons visibly bigger
        or smaller); sidebar width and tile size are read at build time via
        :data:`SIZING`."""
        ratio = SIZING.get("button_font", 1.0)
        padx = SIZING.get("button_padx", 16)
        pady = SIZING.get("button_pady", 8)
        for widget in _walk_widgets(self.root):
            if not isinstance(widget, tk.Button):
                continue
            try:
                widget.configure(padx=padx, pady=pady)
                if ratio != 1.0:
                    # cget returns the font as a Tcl list string
                    # (e.g. '{Segoe UI} 9 normal') - split it properly.
                    parts = widget.tk.splitlist(widget.cget("font"))
                    family = parts[0] if parts else FONT_FAMILY
                    size = 9
                    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                        size = int(parts[1])
                    weight = parts[2] if len(parts) > 2 else "normal"
                    widget.configure(font=(
                        family, max(6, int(round(size * ratio))), weight))
            except tk.TclError:
                continue

    def _build_vars(self) -> None:
        self.input_var = tk.StringVar(value="")
        self.output_var = tk.StringVar(value="")
        self.multiplier_var = tk.StringVar(value=str(self.settings["multiplier"]))
        self.preset_var = tk.StringVar(value=str(self.settings["compression_preset"]))
        self.fast_var = tk.BooleanVar(value=bool(self.settings["fast_mode"]))
        self.archive_format_var = tk.StringVar(
            value=self._archive_format_label(
                self.settings.get("archive_format", "xz")))
        self.compress_passes_var = tk.StringVar(
            value=str(self.settings.get("compress_passes", 1)))
        self.note_multiply_var = tk.BooleanVar(
            value=bool(self.settings["note_multiply"]))
        self.merge_speed_var = tk.StringVar(
            value=self._merge_speed_label(
                self.settings.get("merge_speed", "balanced")))
        self.ram_mode_var = tk.StringVar(value=self.settings.get("ram_mode", "normal"))
        self.max_ram_var = tk.StringVar(value=str(self.settings.get("max_ram_mb", 0)))
        self.remember_var = tk.BooleanVar(value=bool(self.settings["remember_input"]))
        self.auto_var = tk.BooleanVar(value=bool(self.settings["auto_output"]))
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "dark"))
        self.language_var = tk.StringVar(
            value=self.settings.get("language", "English"))
        self.window_size_var = tk.StringVar(
            value=self.settings.get("window_size", "1080p"))
        self.check_updates_var = tk.BooleanVar(
            value=bool(self.settings.get("check_updates", True)))
        self.backup_var = tk.BooleanVar(
            value=bool(self.settings.get("backup_on_update", True)))
        self.update_status_var = tk.StringVar(value="")
        self._output_auto = False   # True while the output name is auto-derived
        self._last_out_spec = None  # (archive_format, passes) of the last refresh
        self.merge_midi_var = tk.BooleanVar(
            value=bool(self.settings.get("merge_midi", False)))
        self.merge_path_var = tk.StringVar(
            value=str(self.settings.get("last_merge_input", "") or ""))
        self.merge_status_var = tk.StringVar(value="")

    def _add_traces(self) -> None:
        self.multiplier_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.preset_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.fast_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.archive_format_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.compress_passes_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.note_multiply_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.merge_speed_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.ram_mode_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.max_ram_var.trace_add("write", lambda *_: self._refresh_dynamic())
        self.merge_midi_var.trace_add("write", lambda *_: self._refresh_dynamic())

    def _apply_window_size(self) -> None:
        """Lock the window and size it to the chosen preset (720p-1440p)."""
        size = self.settings.get("window_size", "1080p")
        w, h = WINDOW_SIZES.get(size, WINDOW_SIZES["1080p"])
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # Never make the window bigger than the physical screen.
        w = min(w, sw)
        h = min(h, sh)
        self.root.resizable(False, False)
        self.root.geometry(f"{w}x{h}")
        self.root.update_idletasks()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # -- sidebar -----------------------------------------------------------

    def _load_assets(self) -> None:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        # Light mode loads the gray set from assets/light/ instead of the
        # blue (dark) set. Every icon in the app ships as a PNG in both sets;
        # the canvas-drawn glyphs in icons.py remain only as a fallback.
        folder = os.path.join(base, "light") if self._theme_name == "light" else base
        self.assets = {}
        for name in ("start", "stop", "gears", "gears_small",
                     "document", "document_card",
                     "hamburger", "reload", "question", "info"):
            path = os.path.join(folder, name + ".png")
            if os.path.isfile(path):
                try:
                    self.assets[name] = tk.PhotoImage(file=path)
                except tk.TclError:
                    self.assets[name] = None
            else:
                self.assets[name] = None

    def _build_sidebar(self) -> None:
        p = PALETTE
        sidebar = tk.Frame(self.root, bg=p["SIDEBAR"],
                           width=SIZING.get("sidebar_width", 220))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.sidebar = sidebar

        header = tk.Frame(sidebar, bg=p["SIDEBAR_HEADER"], height=44)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.hamburger = tk.Canvas(header, width=30, height=44,
                                   bg=p["SIDEBAR_HEADER"], highlightthickness=0,
                                   bd=0, cursor="hand2")
        self.hamburger.pack(side="left", padx=(7, 6))
        if self.assets.get("hamburger") is not None:
            self.hamburger.create_image(15, 22, image=self.assets["hamburger"])
        else:
            icons.draw_hamburger(self.hamburger, 6, 15, 24, 29)
        self.hamburger.bind("<Button-1>", self._toggle_sidebar)
        self.header_label = tk.Label(header, text=_t("nav_converting"),
                                     bg=p["SIDEBAR_HEADER"], fg=p["TEXT"],
                                     font=_font(11, "bold"))
        self.header_label.pack(side="left")

        self.sidebar_body = tk.Frame(sidebar, bg=p["SIDEBAR"])
        self.sidebar_body.pack(fill="both", expand=True)

        self.nav = {}
        for key, icon_fn in NAV_ITEMS:
            label = _nav_label(key)
            item = tk.Frame(self.sidebar_body, bg=p["SIDEBAR"], cursor="hand2")
            item.pack(fill="x")
            bar = tk.Frame(item, bg=p["SIDEBAR"], width=3)
            bar.pack(side="left", fill="y")
            cv = tk.Canvas(item, width=20, height=20, bg=p["SIDEBAR"],
                           highlightthickness=0, bd=0)
            cv.pack(side="left", padx=(17, 12), pady=11)
            icon_name = {"converting": "reload", "help": "question",
                         "about": "info", "settings": "gears_small"}[key]
            if self.assets.get(icon_name) is not None:
                cv.create_image(10, 10, image=self.assets[icon_name])
            else:
                icon_fn(cv, 10, 10, 8)
            lbl = tk.Label(item, text=label, bg=p["SIDEBAR"], fg=p["TEXT"],
                           font=_font(10), anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            for widget in (cv, lbl):
                widget.bind("<Button-1>", lambda _e, k=key: self._select_page(k))
            self.nav[key] = {"frame": item, "canvas": cv, "label": lbl, "bar": bar}

        # Update notes panel — the 'notes' text from feed.json, so users see
        # what changed without opening the update dialog.
        updates = tk.Frame(self.sidebar_body, bg=p["SIDEBAR"])
        tk.Label(updates, text=_t("sidebar_updates"), bg=p["SIDEBAR"],
                 fg=p["ACCENT"], font=_font(9, "bold"), anchor="w").pack(
            fill="x", padx=20, pady=(14, 2))
        self.sidebar_update_text = tk.Text(
            updates, bg=p["SIDEBAR"], fg=p["TEXT_MUTED"], relief="flat",
            wrap="word", font=_font(8), height=6, highlightthickness=0,
            bd=0, padx=0, pady=0)
        self.sidebar_update_text.insert(
            "1.0", self._update_notes or _t("sidebar_update_none"))
        self.sidebar_update_text.configure(state="disabled")
        self.sidebar_update_text.pack(fill="both", expand=True, padx=20)
        updates.pack(fill="both", expand=True)

        footer = tk.Frame(self.sidebar_body, bg=p["SIDEBAR"])
        footer.pack(side="bottom", fill="x", pady=(0, 18))
        tk.Label(footer, text=_t("sidebar_footer_1"), bg=p["SIDEBAR"],
                 fg=p["TEXT"], font=_font(9, "bold")).pack(anchor="w", padx=20)
        tk.Label(footer, text=_t("sidebar_footer_2", version=__version__),
                 bg=p["SIDEBAR"], fg=p["TEXT_MUTED"],
                 font=_font(8)).pack(anchor="w", padx=20)

        self._set_sidebar(False)  # start collapsed

    def _set_sidebar_update_text(self) -> None:
        """Refresh the update-notes panel in the sidebar from feed.json."""
        if not hasattr(self, "sidebar_update_text"):
            return
        self.sidebar_update_text.configure(state="normal")
        self.sidebar_update_text.delete("1.0", "end")
        self.sidebar_update_text.insert(
            "1.0", self._update_notes or _t("sidebar_update_none"))
        self.sidebar_update_text.configure(state="disabled")

    def _toggle_sidebar(self, _event=None) -> None:
        self._set_sidebar(not self.sidebar_open)

    def _set_sidebar(self, open_: bool) -> None:
        self.sidebar_open = open_
        if open_:
            self.sidebar.configure(width=SIZING.get("sidebar_width", 220))
            self.header_label.pack(side="left")
            self.sidebar_body.pack(fill="both", expand=True)
        else:
            self.sidebar.configure(width=44)
            self.header_label.pack_forget()
            self.sidebar_body.pack_forget()

    def _build_pages(self) -> None:
        p = PALETTE
        self.content = tk.Frame(self.root, bg=p["MAIN_BG"])
        self.content.pack(side="left", fill="both", expand=True)

        # Small brand bar across the top of the main area.
        topbar = tk.Frame(self.content, bg=p["SIDEBAR_HEADER"], height=44)
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)
        tk.Label(topbar, text=_t("topbar_brand"), bg=p["SIDEBAR_HEADER"],
                 fg=p["ACCENT"], font=_font(12, "bold")).pack(side="left",
                                                              padx=(18, 0))
        tk.Label(topbar, text=_t("topbar_sub_brand"), bg=p["SIDEBAR_HEADER"],
                 fg=p["TEXT"], font=_font(12, "bold")).pack(side="left")
        tk.Label(topbar, text=_t("topbar_version", version=__version__),
                 bg=p["SIDEBAR_HEADER"], fg=p["TEXT_MUTED"],
                 font=_font(9)).pack(side="right", padx=18)

        self.page_host = tk.Frame(self.content, bg=p["MAIN_BG"])
        self.page_host.pack(side="top", fill="both", expand=True)
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)

        self.pages = {}
        for key, _icon in NAV_ITEMS:
            page = tk.Frame(self.page_host, bg=p["MAIN_BG"])
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page

        self._build_converting(self.pages["converting"])
        self._build_text_page(self.pages["help"], _t("help_text"))
        self._build_text_page(
            self.pages["about"], _t("about_text", app_name=APP_NAME,
                                   version=__version__))
        self._build_settings(self.pages["settings"])

    def _select_page(self, key: str) -> None:
        self.current_page = key
        self.pages[key].tkraise()
        label = _nav_label(key)
        self.header_label.configure(text=label)
        for nav_key, item in self.nav.items():
            active = nav_key == key
            bg = PALETTE["NAV_ACTIVE"] if active else PALETTE["SIDEBAR"]
            item["frame"].configure(bg=bg)
            item["canvas"].configure(bg=bg)
            item["label"].configure(
                bg=bg, fg=PALETTE["ACCENT"] if active else PALETTE["TEXT"])
            item["bar"].configure(
                bg=PALETTE["ACCENT"] if active else PALETTE["SIDEBAR"])
        if key == "settings":
            self._refresh_dynamic()

    # -- converting page ---------------------------------------------------

    def _build_converting(self, page: tk.Frame) -> None:
        p = PALETTE
        page.columnconfigure(0, weight=0)
        page.columnconfigure(1, weight=1)
        page.columnconfigure(2, weight=0)
        page.rowconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        pad = 24
        tile_size = SIZING.get("tile_size", 104)
        # Each tile shows its PNG when the asset set ships one and falls back
        # to a canvas-drawn glyph when it is missing (same rule as the
        # document card below).
        self.start_tile = Tile(page, _t("tile_start"), size=tile_size,
                               image=self.assets.get("start"),
                               icon_draw=icons.draw_play,
                               command=self.start_generate)
        self.start_tile.frame.grid(row=0, column=0, sticky="nw",
                                   padx=(pad, 14), pady=(pad, 12))

        self.interrupt_tile = Tile(page, _t("tile_stop"), size=tile_size,
                                   image=self.assets.get("stop"),
                                   icon_draw=icons.draw_stop,
                                   command=self.request_cancel)
        self.interrupt_tile.frame.grid(row=1, column=0, sticky="sw",
                                       padx=(pad, 14), pady=(12, pad))
        self.interrupt_tile.set_enabled(False)

        self.customise_tile = Tile(page, _t("tile_customise"), size=tile_size,
                                   subtext="",
                                   image=self.assets.get("gears"),
                                   icon_draw=icons.draw_gear_button,
                                   command=self._open_settings_page)
        self.customise_tile.frame.grid(row=0, column=2, sticky="ne",
                                       padx=(14, pad), pady=(pad, 12))

        self.props_tile = Tile(page, _t("tile_file_properties"), size=tile_size,
                               image=self.assets.get("document"),
                               icon_draw=icons.draw_document_tile,
                               command=self.show_file_properties)
        self.props_tile.frame.grid(row=1, column=2, sticky="se",
                                   padx=(14, pad), pady=(12, pad))

        # Central document card.
        card_frame = tk.Frame(page, bg=p["MAIN_BG"])
        card_frame.grid(row=0, column=1, rowspan=2, sticky="nsew",
                        pady=(pad, pad))
        self.card = tk.Canvas(card_frame, bg=p["MAIN_BG"], highlightthickness=0,
                              bd=0, cursor="hand2")
        self.card.pack(fill="both", expand=True)
        self.card.bind("<Button-1>", lambda _e: self.choose_input())
        self.card.bind("<Configure>", lambda _e: self._redraw_card())

        # Multiplier row.
        controls = tk.Frame(page, bg=p["MAIN_BG"])
        controls.grid(row=2, column=0, columnspan=3, sticky="ew",
                      padx=pad, pady=(0, 4))
        tk.Label(controls, text=_t("tracks_label"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).pack(side="left")
        ttk.Spinbox(controls, from_=1, to=65535, width=12,
                    textvariable=self.multiplier_var).pack(side="left", padx=8)
        self.output_hint = tk.Label(controls, text="", bg=p["MAIN_BG"],
                                    fg=p["TEXT_MUTED"], font=_font(9))
        self.output_hint.pack(side="left")

        # Loading progress — shown while a MIDI parses in the background.
        self.load_progress = ttk.Progressbar(
            page, style="Accent.Horizontal.TProgressbar", mode="indeterminate")
        self.load_progress.grid(row=3, column=0, columnspan=3, sticky="ew",
                                padx=pad, pady=(4, 0))
        self.load_progress.grid_remove()
        self.load_info_var = tk.StringVar(value="")
        self.load_info = tk.Label(page, textvariable=self.load_info_var,
                                  bg=p["MAIN_BG"], fg=p["TEXT_MUTED"],
                                  font=_font(9), anchor="w")
        self.load_info.grid(row=4, column=0, columnspan=3, sticky="ew",
                            padx=pad, pady=(2, 0))
        self.load_info.grid_remove()

        self.progress = ttk.Progressbar(page, style="Accent.Horizontal.TProgressbar",
                                        mode="determinate", maximum=1, value=0)
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew",
                           padx=pad, pady=(6, 6))

        status = tk.Frame(page, bg=p["MAIN_BG"])
        status.grid(row=6, column=0, columnspan=3, sticky="ew",
                    padx=pad, pady=(0, pad))
        self.track_status_var = tk.StringVar(value=_t("status_ready"))
        tk.Label(status, textvariable=self.track_status_var, bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).pack(anchor="w")
        self.file_status_var = tk.StringVar(value=_t("file_current", name="None"))
        tk.Label(status, textvariable=self.file_status_var, bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"], font=_font(9)).pack(anchor="w", pady=(2, 0))

        # Optional merge: a second MIDI appended after the duplicated part,
        # written once (unduplicated). E.g. duplicate the art track 65,534
        # times and lay the melody underneath a single time.
        merge_row = tk.Frame(page, bg=p["MAIN_BG"])
        merge_row.grid(row=7, column=0, columnspan=3, sticky="ew",
                       padx=pad, pady=(0, pad))
        ttk.Checkbutton(merge_row, text=_t("merge_toggle"),
                        variable=self.merge_midi_var,
                        command=self._on_merge_toggle).pack(side="left")
        self.merge_browse_btn = tk.Button(
            merge_row, text=_t("merge_browse"), command=self.choose_merge_input,
            bg=p["BUTTON_SECONDARY"], fg=p["TEXT"], relief="flat",
            activebackground=p["BUTTON_SECONDARY_HOVER"],
            activeforeground=p["TEXT"], padx=12, pady=1, font=_font(9),
            disabledforeground=p["TEXT_MUTED"])
        self.merge_browse_btn.pack(side="left", padx=(10, 4))
        self.merge_clear_btn = tk.Button(
            merge_row, text=_t("merge_clear"), command=self.clear_merge_input,
            bg=p["BUTTON_SECONDARY"], fg=p["TEXT"], relief="flat",
            activebackground=p["BUTTON_SECONDARY_HOVER"],
            activeforeground=p["TEXT"], padx=10, pady=1, font=_font(9),
            disabledforeground=p["TEXT_MUTED"])
        self.merge_clear_btn.pack(side="left")
        tk.Label(merge_row, textvariable=self.merge_status_var,
                 bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9),
                 anchor="w").pack(side="left", padx=(10, 0))

    def _redraw_card(self) -> None:
        canvas = self.card
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 8 or height < 8:
            return
        p = PALETTE
        canvas.delete("all")
        icons.round_rect(canvas, 4, 4, width - 4, height - 4, 18,
                         fill=p["CARD"], outline=p["CARD_BORDER"])
        cx = width / 2
        img = self.assets.get("document_card")
        if img is not None:
            cy = max(img.height() / 2 + 10, (height - 96) / 2)
            canvas.create_image(cx, cy, image=img)
            label_y = cy + img.height() / 2 + 16
        else:
            cy = height / 2 - 22
            rh = min(height * 0.30, width * 0.24)
            rw = rh * 0.9
            icons.draw_document(canvas, cx - rw, cy - rh, cx + rw, cy + rh)
            label_y = cy + rh + 30
        if self.info is not None:
            name = os.path.basename(self.input_var.get()) or _t("card_untitled")
            canvas.create_text(cx, label_y, text=name, fill=p["TEXT"],
                               font=_font(11, "bold"), width=width - 60)
            ntrks = max(1, len(self.info.track_chunks))
            line = _t("card_line", notes=f"{self.info.note_count:,}",
                      tracks=ntrks, s="" if ntrks == 1 else "s")
            if ntrks > 1:
                line += " · " + _t("card_to_multiply",
                                  count=len(self.selected_tracks))
            if self.note_multiply_var.get():
                line += " · " + _t("card_note_mode")
            footprint = midi_engine.ram_footprint(self.info)
            line += " · " + _t("card_in_ram",
                              size=midi_engine.human_size(footprint),
                              mode=self._ram_mode_label(self.info))
            canvas.create_text(cx, label_y + 24, text=line,
                               fill=p["TEXT_MUTED"], font=_font(9), width=width - 60)
        elif self.input_var.get().strip():
            name = os.path.basename(self.input_var.get())
            canvas.create_text(cx, label_y, text=name, fill=p["TEXT"],
                               font=_font(11, "bold"), width=width - 60)
            canvas.create_text(cx, label_y + 24, text=_t("card_loading"),
                               fill=p["TEXT_MUTED"], font=_font(9),
                               width=width - 60)
        else:
            canvas.create_text(cx, label_y, text=_t("card_click_hint"),
                               fill=p["TEXT_MUTED"], font=_font(10),
                               width=width - 60)

    # -- help / about pages ------------------------------------------------

    def _build_text_page(self, page: tk.Frame, body: str) -> None:
        p = PALETTE
        wrap = tk.Frame(page, bg=p["MAIN_BG"])
        wrap.pack(fill="both", expand=True, padx=24, pady=24)
        text = tk.Text(wrap, bg=p["CARD"], fg=p["TEXT"], insertbackground=p["TEXT"],
                       relief="flat", wrap="word", font=_font(10),
                       padx=14, pady=12, highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", body)
        text.configure(state="disabled")

    # -- settings page -----------------------------------------------------

    def _on_mousewheel(self, event) -> None:
        """Scroll the settings page when the wheel turns over it."""
        if self.current_page == "settings" and self._scroll_canvas is not None:
            self._scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _make_scrollable(self, parent: tk.Frame) -> tuple[tk.Canvas, tk.Frame]:
        """Wrap a page's content in a vertically scrollable canvas."""
        p = PALETTE
        canvas = tk.Canvas(parent, bg=p["MAIN_BG"], highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=p["MAIN_BG"])
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(inner_id, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._scroll_canvas = canvas
        return canvas, inner

    def _build_settings(self, page: tk.Frame) -> None:
        p = PALETTE
        _canvas, inner = self._make_scrollable(page)
        form = tk.Frame(inner, bg=p["MAIN_BG"])
        form.pack(anchor="nw", padx=28, pady=26)

        tk.Label(form, text=_t("settings_title"), bg=p["MAIN_BG"], fg=p["ACCENT"],
                 font=_font(16, "bold")).grid(row=0, column=0, columnspan=3,
                                              sticky="w", pady=(0, 10))

        # Appearance.
        tk.Label(form, text=_t("settings_theme"), bg=p["MAIN_BG"], fg=p["TEXT"],
                 font=_font(10)).grid(row=1, column=0, sticky="w", pady=4)
        theme_row = tk.Frame(form, bg=p["MAIN_BG"])
        theme_row.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=4)
        ttk.Radiobutton(theme_row, text=_t("settings_dark_mode"), value="dark",
                        variable=self.theme_var).pack(side="left")
        ttk.Radiobutton(theme_row, text=_t("settings_light_mode"), value="light",
                        variable=self.theme_var).pack(side="left", padx=(16, 0))

        # Language picker (reads every *.SMLF file in CORE_FILES/lang/).
        lang_row = tk.Frame(form, bg=p["MAIN_BG"])
        lang_row.grid(row=1, column=2, sticky="w", padx=(24, 0), pady=4)
        tk.Label(lang_row, text=_t("settings_language"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).pack(side="left")
        ttk.Combobox(lang_row, width=14, state="readonly",
                     values=lang_loader.list_languages(),
                     textvariable=self.language_var).pack(side="left", padx=(8, 0))

        # Window size (the window is locked, so this is how you resize it).
        size_row = tk.Frame(form, bg=p["MAIN_BG"])
        size_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        tk.Label(size_row, text=_t("settings_window_size"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).pack(side="left")
        ttk.Combobox(size_row, width=8, state="readonly",
                     values=list(WINDOW_SIZES.keys()),
                     textvariable=self.window_size_var).pack(side="left", padx=(8, 0))

        # Duplication.
        tk.Label(form, text=_t("settings_default_multiplier"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(row=3, column=0, sticky="w",
                                                    pady=4)
        ttk.Spinbox(form, from_=1, to=65535, width=12,
                    textvariable=self.multiplier_var).grid(
            row=3, column=1, sticky="w", padx=(12, 0), pady=4)

        tk.Label(form, text=_t("settings_compression_preset"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(row=4, column=0, sticky="w",
                                                    pady=4)
        ttk.Combobox(form, width=6, state="readonly",
                     values=[str(i) for i in range(10)],
                     textvariable=self.preset_var).grid(
            row=4, column=1, sticky="w", padx=(12, 0), pady=4)
        tk.Label(form, text=_t("settings_preset_hint"), bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=4, column=2, sticky="w", padx=(10, 0), pady=4)

        ttk.Checkbutton(form, text=_t("settings_fast_mode"),
                        variable=self.fast_var).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tk.Label(form, text=_t("settings_compression_passes"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(
            row=6, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(form, width=14, state="readonly",
                     values=["1", "2", "3"],
                     textvariable=self.compress_passes_var).grid(
            row=6, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
        tk.Label(form, text=_t("settings_compression_passes_hint"), bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=6, column=2, sticky="w", padx=(10, 0), pady=(4, 0))

        # Output archive format (XZ, GZIP, BZIP2, ZSTD, ZIP, 7-ZIP).
        tk.Label(form, text=_t("settings_archive_format"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(
            row=7, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(form, width=16, state="readonly",
                     values=[self._archive_format_label(k)
                             for k in midi_engine.ARCHIVE_FORMATS],
                     textvariable=self.archive_format_var).grid(
            row=7, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
        tk.Label(form, text=_t("settings_archive_format_hint"), bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=7, column=2, sticky="w", padx=(10, 0), pady=(4, 0))

        ttk.Checkbutton(form, text=_t("settings_note_multiply"),
                        variable=self.note_multiply_var).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        tk.Label(form, text=_t("settings_note_multiply_hint"),
                 bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=9, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(0, 2))
        tk.Label(form, text=_t("settings_merge_speed"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(
            row=10, column=0, sticky="w", pady=4)
        ttk.Combobox(form, width=14, state="readonly",
                     values=[_t("merge_speed_balanced"), _t("merge_speed_fast"),
                             _t("merge_speed_turbo")],
                     textvariable=self.merge_speed_var).grid(
            row=10, column=1, sticky="w", padx=(12, 0), pady=4)
        tk.Label(form, text=_t("settings_merge_speed_hint"), bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=10, column=2, sticky="w", padx=(10, 0), pady=4)
        ttk.Checkbutton(form, text=_t("settings_remember_input"),
                        variable=self.remember_var).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(form, text=_t("settings_auto_output"),
                        variable=self.auto_var).grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Memory / RAM preload.
        tk.Label(form, text=_t("settings_memory"), bg=p["MAIN_BG"],
                 fg=p["ACCENT"], font=_font(10, "bold")).grid(
            row=13, column=0, columnspan=3, sticky="w", pady=(14, 2))
        ram_row = tk.Frame(form, bg=p["MAIN_BG"])
        ram_row.grid(row=14, column=0, columnspan=3, sticky="w",
                     padx=(0, 0), pady=2)
        ttk.Radiobutton(ram_row, text=_t("settings_ram_normal"), value="normal",
                        variable=self.ram_mode_var).pack(side="left")
        ttk.Radiobutton(ram_row, text=_t("settings_ram_double"), value="double",
                        variable=self.ram_mode_var).pack(side="left", padx=(14, 0))
        ttk.Radiobutton(ram_row, text=_t("settings_ram_triple"), value="triple",
                        variable=self.ram_mode_var).pack(side="left", padx=(14, 0))
        tk.Label(form, text=_t("settings_ram_hint"),
                 bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=15, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(form, text=_t("settings_max_ram"), bg=p["MAIN_BG"],
                 fg=p["TEXT"], font=_font(10)).grid(
            row=16, column=0, sticky="w", pady=4)
        ttk.Entry(form, width=12, textvariable=self.max_ram_var).grid(
            row=16, column=1, sticky="w", padx=(12, 0), pady=4)
        tk.Label(form, text=_t("settings_max_ram_hint"),
                 bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9)).grid(
            row=16, column=2, sticky="w", padx=(10, 0), pady=4)

        # Updates.
        ttk.Checkbutton(form, text=_t("settings_check_updates"),
                        variable=self.check_updates_var).grid(
            row=17, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(form, text=_t("settings_backup_on_update"),
                        variable=self.backup_var).grid(
            row=18, column=0, columnspan=3, sticky="w", pady=(4, 0))

        update_row = tk.Frame(form, bg=p["MAIN_BG"])
        update_row.grid(row=19, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tk.Button(update_row, text=_t("settings_button_check_updates"),
                  command=lambda: self._check_updates(manual=True),
                  bg=p["ACCENT"], fg=p["ACCENT_ON"],
                  activebackground=p["ACCENT_HOVER"],
                  activeforeground=p["ACCENT_ON"], relief="flat", padx=14,
                  pady=6, font=_font(9, "bold"), cursor="hand2").pack(
            side="left")
        tk.Button(update_row, text=_t("settings_button_downgrade"),
                  command=self._open_downgrade_dialog,
                  bg=p["BUTTON_SECONDARY"], fg=p["TEXT"],
                  activebackground=p["BUTTON_SECONDARY_HOVER"],
                  activeforeground=p["TEXT"], relief="flat", padx=14,
                  pady=6, font=_font(9), cursor="hand2").pack(
            side="left", padx=(8, 0))
        tk.Button(update_row, text=_t("settings_button_restore_files"),
                  command=self._restore_missing_files,
                  bg=p["BUTTON_SECONDARY"], fg=p["TEXT"],
                  activebackground=p["BUTTON_SECONDARY_HOVER"],
                  activeforeground=p["TEXT"], relief="flat", padx=14,
                  pady=6, font=_font(9), cursor="hand2").pack(
            side="left", padx=(8, 0))
        tk.Label(update_row, textvariable=self.update_status_var,
                 bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9)).pack(
            side="left", padx=(12, 0))

        buttons = tk.Frame(form, bg=p["MAIN_BG"])
        buttons.grid(row=20, column=0, columnspan=3, sticky="w", pady=(16, 0))
        tk.Button(buttons, text=_t("settings_button_save"),
                  command=self._save_settings, bg=p["ACCENT"], fg=p["ACCENT_ON"],
                  activebackground=p["ACCENT_HOVER"],
                  activeforeground=p["ACCENT_ON"], relief="flat", padx=16,
                  pady=8, font=_font(10, "bold"), cursor="hand2").pack(
            side="left", padx=(0, 8))
        tk.Button(buttons, text=_t("settings_button_reset"),
                  command=self._reset_settings, bg=p["BUTTON_SECONDARY"],
                  fg=p["TEXT"],
                  activebackground=p["BUTTON_SECONDARY_HOVER"],
                  activeforeground=p["TEXT"], relief="flat", padx=16, pady=8,
                  font=_font(10), cursor="hand2").pack(side="left")

    def _open_settings_page(self) -> None:
        self._select_page("settings")

    def _save_settings(self) -> None:
        try:
            multiplier = int(self.multiplier_var.get())
        except (ValueError, tk.TclError):
            self._error(_t("settings_error_multiplier_number"))
            return
        if not 1 <= multiplier <= 65535:
            self._error(_t("settings_error_multiplier_range"))
            return
        try:
            max_ram_mb = int(self.max_ram_var.get() or 0)
        except (ValueError, tk.TclError):
            self._error(_t("settings_error_max_ram_number"))
            return
        if max_ram_mb < 0:
            self._error(_t("settings_error_max_ram_negative"))
            return
        theme = self.theme_var.get()
        language = self.language_var.get().strip() or "English"
        theme_changed = theme != self._theme_name
        language_changed = language != self.settings.get("language", "English")
        self.settings.update({
            "multiplier": multiplier,
            "compression_preset": int(self.preset_var.get()),
            "fast_mode": bool(self.fast_var.get()),
            "archive_format": self._archive_format_key(
                self.archive_format_var.get()),
            "compress_passes": self._passes(),
            "note_multiply": bool(self.note_multiply_var.get()),
            "merge_speed": self._merge_speed_key(self.merge_speed_var.get()),
            "ram_mode": self.ram_mode_var.get(),
            "max_ram_mb": max_ram_mb,
            "remember_input": bool(self.remember_var.get()),
            "auto_output": bool(self.auto_var.get()),
            "theme": theme,
            "language": language,
            "check_updates": bool(self.check_updates_var.get()),
            "backup_on_update": bool(self.backup_var.get()),
            "window_size": self.window_size_var.get(),
        })
        self._persist()
        if theme_changed or language_changed:
            self._rebuild()
        else:
            self._refresh_dynamic()
        self._apply_window_size()
        self._info(_t("settings_saved"))

    def _reset_settings(self) -> None:
        if not self._confirm(_t("settings_reset_confirm")):
            return
        old_language = self.language_var.get()
        self.settings = app_settings.reset()
        theme_changed = self.settings["theme"] != self._theme_name
        language_changed = old_language != self.settings.get("language", "English")
        self.multiplier_var.set(str(self.settings["multiplier"]))
        self.preset_var.set(str(self.settings["compression_preset"]))
        self.fast_var.set(bool(self.settings["fast_mode"]))
        self.archive_format_var.set(self._archive_format_label(
            self.settings.get("archive_format", "xz")))
        self.compress_passes_var.set(str(self.settings.get("compress_passes", 1)))
        self.note_multiply_var.set(bool(self.settings["note_multiply"]))
        self.merge_speed_var.set(self._merge_speed_label(
            self.settings.get("merge_speed", "balanced")))
        self.ram_mode_var.set(self.settings.get("ram_mode", "normal"))
        self.max_ram_var.set(str(self.settings.get("max_ram_mb", 0)))
        self.remember_var.set(bool(self.settings["remember_input"]))
        self.auto_var.set(bool(self.settings["auto_output"]))
        self.theme_var.set(self.settings["theme"])
        self.language_var.set(self.settings.get("language", "English"))
        self.check_updates_var.set(bool(self.settings["check_updates"]))
        self.backup_var.set(bool(self.settings["backup_on_update"]))
        self.window_size_var.set(self.settings.get("window_size", "1080p"))
        if self.info is not None:
            self.selected_tracks = list(range(len(self.info.track_chunks)))
        self._persist()
        if theme_changed or language_changed:
            self._rebuild()
        else:
            self._refresh_dynamic()
        self._apply_window_size()

    def _rebuild(self) -> None:
        """Rebuild the widget tree after a theme change (state is kept)."""
        page = self.current_page
        self._theme_name = self.settings.get("theme", "dark")
        lang_loader.apply(self.settings.get("language", "English"))
        self.merge_speed_var.set(self._merge_speed_label(
            self.settings.get("merge_speed", "balanced")))
        self._apply_palette()
        apply_theme(self.root)
        _enable_dark_titlebar(self.root, self._theme_name == "dark")
        self.sidebar.destroy()
        self.content.destroy()
        self._load_assets()  # swap to the dark/light icon set
        self._build_sidebar()
        self._build_pages()
        self._apply_settings(skip_validate=True)
        self._select_page(page)
        self._refresh_dynamic()

    # -- update checking ---------------------------------------------------

    def _check_updates(self, manual: bool = False) -> None:
        if self._update_pending:
            return
        self._update_pending = True
        if manual:
            self.update_status_var.set(_t("update_checking"))
        threading.Thread(target=self._update_worker,
                         args=(UPDATE_FEED_URL, manual), daemon=True).start()
        self.root.after(200, self._poll_updates)

    def _update_worker(self, url: str, manual: bool) -> None:
        try:
            feed = updater.fetch_feed(url)
            if feed is None:
                self._update_queue.put(("none", manual))
            elif updater.is_newer(feed["version"], __version__):
                self._update_queue.put(("available", feed, manual))
            else:
                self._update_queue.put(("current", feed, manual))
        except updater.UpdateError as exc:
            self._update_queue.put(("error", str(exc), manual))

    def _poll_updates(self) -> None:
        drained = False
        try:
            while True:
                msg = self._update_queue.get_nowait()
                drained = True
                kind = msg[0]
                if kind in ("available", "current"):
                    # Keep the feed's release notes for the sidebar panel.
                    self._update_notes = (msg[1].get("notes") or "").strip()
                    self._set_sidebar_update_text()
                    if kind == "available":
                        self._show_update_available(msg[1])
                    elif msg[2]:
                        self.update_status_var.set(
                            _t("update_up_to_date_short", version=__version__))
                        self._show_up_to_date(msg[1])
                elif kind == "none":
                    if msg[2]:
                        self.update_status_var.set(_t("update_no_feed"))
                elif kind == "error":
                    if msg[2]:
                        self.update_status_var.set(_t("update_check_failed"))
                        self._error(msg[1])
        except queue.Empty:
            pass
        if drained:
            self._update_pending = False
            return
        if self._update_pending:
            self.root.after(200, self._poll_updates)

    # -- restore missing files --------------------------------------------

    def _restore_missing_files(self) -> None:
        """Download the source package from the feed and restore missing files.

        Fetches the feed, downloads the release zip (the same ``url`` the .bat
        tools use), then extracts any ``trillion.py`` / ``CORE_FILES/`` entry
        that is missing on disk. Existing files are never overwritten.
        """
        if self._restore_pending:
            return
        if not self._confirm(_t("restore_confirm")):
            return
        self._restore_pending = True
        self.update_status_var.set(_t("restore_downloading"))
        threading.Thread(target=self._restore_worker,
                         args=(UPDATE_FEED_URL,), daemon=True).start()
        self.root.after(200, self._poll_restore)

    def _restore_worker(self, url: str) -> None:
        """Scan phase: download the source zip and list what is missing.

        Runs on a worker thread. When the scan is done the queue gets
        ``("ready", missing)`` where ``missing`` is the list of relative
        paths that do not exist on disk yet. Nothing is extracted here —
        the confirm dialog (main thread) decides that next.
        """
        try:
            feed = updater.fetch_feed(url)
            if feed is None:
                self._restore_queue.put(("error", _t("update_no_feed")))
                return
            src_url = (feed.get("url") or "").strip()
            if not src_url:
                self._restore_queue.put(("error", _t("restore_no_source")))
                return
            dest = self._restore_zip_path()
            updater.download(src_url, dest)
            frozen = bool(getattr(sys, "frozen", False))
            if frozen:
                app_root = os.path.dirname(os.path.abspath(sys.executable))
            else:
                app_root = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))
            missing = updater.list_missing_files(dest, app_root)
            self._restore_queue.put(("ready", missing))
        except updater.UpdateError as exc:
            self._restore_queue.put(("error", str(exc)))

    def _restore_zip_path(self) -> str:
        """Temp path used for the source zip during the restore flow."""
        return os.path.join(tempfile.gettempdir(),
                            "systematics_missing_restore.zip")

    def _restore_extract_worker(self) -> None:
        """Extract phase: restore the confirmed missing files."""
        try:
            dest = self._restore_zip_path()
            frozen = bool(getattr(sys, "frozen", False))
            if frozen:
                app_root = os.path.dirname(os.path.abspath(sys.executable))
            else:
                app_root = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))
            restored = updater.restore_missing_files(dest, app_root)
            try:
                os.remove(dest)
            except OSError:
                pass
            self._restore_queue.put(("done", restored))
        except updater.UpdateError as exc:
            self._restore_queue.put(("error", str(exc)))

    def _poll_restore(self) -> None:
        drained = False
        try:
            while True:
                msg = self._restore_queue.get_nowait()
                drained = True
                kind = msg[0]
                if kind == "ready":
                    missing = msg[1]
                    if not missing:
                        self._restore_pending = False
                        self.update_status_var.set(_t("restore_all_present"))
                        self._info(_t("restore_all_present_msg"))
                        try:
                            os.remove(self._restore_zip_path())
                        except OSError:
                            pass
                    elif self._confirm(
                            _t("restore_confirm_files",
                               count=len(missing),
                               files="\n".join("• " + f for f in missing))):
                        self.update_status_var.set(_t("restore_downloading"))
                        threading.Thread(target=self._restore_extract_worker,
                                         daemon=True).start()
                        self.root.after(200, self._poll_restore)
                        return
                    else:
                        self._restore_pending = False
                        self.update_status_var.set(_t("restore_cancelled"))
                        try:
                            os.remove(self._restore_zip_path())
                        except OSError:
                            pass
                elif kind == "done":
                    restored = msg[1]
                    if restored:
                        self.update_status_var.set(
                            _t("restore_done_count", count=len(restored)))
                        self._info(
                            _t("restore_done", count=len(restored),
                               files="\n".join("• " + f for f in restored)))
                    else:
                        self.update_status_var.set(_t("restore_all_present"))
                        self._info(_t("restore_all_present_msg"))
                else:
                    self.update_status_var.set(_t("restore_failed"))
                    self._error(msg[1])
        except queue.Empty:
            pass
        if drained:
            self._restore_pending = False
            return
        if self._restore_pending:
            self.root.after(200, self._poll_restore)




    def _show_update_available(self, feed: dict) -> None:
        self.update_status_var.set(
            _t("update_available_short", version=feed["version"]))
        self._show_update_dialog(feed)

    def _show_update_dialog(self, feed: dict) -> None:
        p = PALETTE
        win = tk.Toplevel(self.root)
        win.title(_t("update_window_title", app_name=APP_NAME))
        win.configure(bg=p["MAIN_BG"])
        win.resizable(False, False)
        win.transient(self.root)
        self._apply_dialog_titlebar(win)

        frame = tk.Frame(win, bg=p["MAIN_BG"], padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=_t("update_dialog_title"), bg=p["MAIN_BG"],
                 fg=p["ACCENT"], font=_font(14, "bold")).pack(anchor="w")
        tk.Label(frame,
                 text=_t("update_dialog_versions", version=feed["version"],
                         current=__version__),
                 bg=p["MAIN_BG"], fg=p["TEXT"], font=_font(10)).pack(
            anchor="w", pady=(4, 8))
        if feed["notes"]:
            notes = tk.Text(frame, bg=p["CARD"], fg=p["TEXT"],
                            insertbackground=p["TEXT"], relief="flat",
                            wrap="word", font=_font(10), height=6, width=52,
                            padx=10, pady=8, highlightthickness=0)
            notes.insert("1.0", feed["notes"].rstrip())
            notes.configure(state="disabled")
            notes.pack(fill="both", expand=True, pady=(0, 10))

        status_lbl = tk.Label(frame, text="", bg=p["MAIN_BG"],
                              fg=p["TEXT_MUTED"], font=_font(9))
        status_lbl.pack(anchor="w")
        bar = ttk.Progressbar(frame, style="Accent.Horizontal.TProgressbar",
                              mode="determinate", maximum=1, value=0)

        row = tk.Frame(frame, bg=p["MAIN_BG"])
        row.pack(fill="x", pady=(12, 0))
        later_btn = tk.Button(row, text=_t("update_button_later"),
                              command=win.destroy,
                              bg=p["BUTTON_SECONDARY"], fg=p["TEXT"],
                              activebackground=p["BUTTON_SECONDARY_HOVER"],
                              activeforeground=p["TEXT"], relief="flat",
                              padx=16, pady=8, font=_font(10),
                              cursor="hand2")
        install_btn = tk.Button(
            row, text=_t("update_button_download"),
            command=lambda: self._start_download(feed, win, bar, status_lbl,
                                                 install_btn, later_btn),
            bg=p["ACCENT"], fg=p["ACCENT_ON"],
            activebackground=p["ACCENT_HOVER"],
            activeforeground=p["ACCENT_ON"], relief="flat", padx=16, pady=8,
            font=_font(10, "bold"), cursor="hand2")
        install_btn.pack(side="left")
        later_btn.pack(side="left", padx=(8, 0))
        frozen = bool(getattr(sys, "frozen", False))
        has_link = bool((feed.get("url") or "").strip())
        if frozen:
            has_link = has_link or bool((feed.get("exe_url") or "").strip())
        if not has_link:
            install_btn.configure(
                text=_t("update_no_link"), state="disabled",
                command=lambda: self._info(_t("update_no_link_msg")))
        win.grab_set()

    def _start_download(self, feed, win, bar, status_lbl, install_btn,
                        later_btn) -> None:
        # EXE builds must download the new .exe itself; source builds use the
        # source zip. The feed can carry both: "url" (source zip, used by the
        # .bat tools) and "exe_url" (the standalone EXE). If no EXE exists
        # for the version (no exe_url, or the file 404s), fall back to the
        # source package and say so.
        frozen = bool(getattr(sys, "frozen", False))
        src_url = (feed.get("url") or "").strip()
        exe_url = (feed.get("exe_url") or "").strip()
        self._dl_source_fallback = False
        if frozen:
            if exe_url and updater.url_exists(exe_url):
                url = exe_url
            elif src_url:
                url = src_url
                self._dl_source_fallback = True
                status_lbl.configure(text=_t("update_no_exe_fallback"))
            else:
                return
        else:
            url = src_url
            if not url:
                return
        install_btn.configure(state="disabled")
        later_btn.configure(state="disabled")
        status_lbl.configure(text=_t("update_downloading"))
        bar.pack(fill="x", pady=(6, 0))
        dest = os.path.join(tempfile.gettempdir(),
                            f"systematics_update_{feed['version']}.exe"
                            if frozen and not self._dl_source_fallback else
                            f"systematics_update_{feed['version']}.zip")
        self._dl_widgets = (win, bar, status_lbl, install_btn, later_btn)
        self._dl_dest = dest
        self._dl_pending = True
        threading.Thread(target=self._download_worker,
                         args=(url, dest, feed, self._dl_source_fallback),
                         daemon=True).start()
        self.root.after(100, self._poll_download)

    def _download_worker(self, url: str, dest: str, feed: dict,
                         source_fallback: bool) -> None:
        try:
            updater.download(url, dest, progress_cb=self._dl_progress)
            self._dl_queue.put(("dlresult", "ok", feed, dest,
                                source_fallback))
        except updater.UpdateError as exc:
            self._dl_queue.put(("dlresult", "error", str(exc)))

    def _dl_progress(self, done: int, total: int) -> None:
        self._dl_queue.put(("dlprogress", done, total))

    def _poll_download(self) -> None:
        drained = False
        try:
            while True:
                msg = self._dl_queue.get_nowait()
                drained = True
                kind = msg[0]
                if kind == "dlprogress":
                    if self._dl_widgets is None:
                        continue
                    win, bar, status_lbl, install_btn, later_btn = self._dl_widgets
                    if not win.winfo_exists():
                        self._dl_pending = False
                        self._dl_widgets = None
                        return
                    _, done, total = msg
                    bar.configure(maximum=total or 1, value=done)
                    status_lbl.configure(
                        text=_t("update_download_progress",
                                done=midi_engine.human_size(done),
                                total=midi_engine.human_size(total)))
                elif kind == "dlresult":
                    self._dl_pending = False
                    if msg[1] == "ok":
                        self._install_downloaded(msg[2], msg[3], msg[4])
                    else:
                        self._download_failed(msg[2])
        except queue.Empty:
            pass
        if drained and not self._dl_pending:
            self._dl_widgets = None
            return
        if self._dl_pending:
            self.root.after(100, self._poll_download)

    def _install_downloaded(self, feed: dict, dest: str,
                            source_fallback: bool = False) -> None:
        if self._dl_widgets is None:
            return
        win, bar, status_lbl, install_btn, later_btn = self._dl_widgets
        if getattr(sys, "frozen", False) and not source_fallback:
            # EXE build: the download must be a Windows executable. If the
            # feed still points at a source zip, tell the user what's wrong.
            if not self._is_exe_file(dest):
                status_lbl.configure(
                    text=_t("update_invalid_package",
                            error=_t("update_exe_not_exe")))
                later_btn.configure(state="normal")
                return
        else:
            try:
                updater.validate_update_zip(dest)
            except updater.UpdateError as exc:
                status_lbl.configure(text=_t("update_invalid_package", error=exc))
                later_btn.configure(state="normal")
                return
        status_lbl.configure(text=_t("update_download_complete"))
        bar.configure(value=bar.cget("maximum"))
        if source_fallback:
            install_btn.configure(
                text=_t("update_button_install_source"), state="normal",
                command=lambda: self._apply_source_install(feed, dest, win))
        else:
            install_btn.configure(
                text=_t("update_button_install_restart"), state="normal",
                command=lambda: self._apply_and_restart(feed, dest, win))

    @staticmethod
    def _is_exe_file(path: str) -> bool:
        """True when the file starts with the MZ DOS header (a PE executable)."""
        try:
            with open(path, "rb") as f:
                return f.read(2) == b"MZ"
        except OSError:
            return False

    def _apply_and_restart(self, feed: dict, dest: str, win) -> None:
        win.destroy()
        keep_backup = bool(self.settings.get("backup_on_update", True))
        msg = (
            _t("update_install_confirm", version=feed["version"]) + "\n"
            + (_t("update_backup_note") if keep_backup
               else _t("update_no_backup_note")))
        if not self._confirm(msg):
            return
        # PyInstaller builds (sys.frozen) live in a one-file EXE: the app
        # folder is the EXE's own directory and we restart the EXE itself,
        # not `python trillion.py`. In source mode the app folder is the
        # project root (parent of CORE_FILES).
        if getattr(sys, "frozen", False):
            try:
                self._swap_exe(dest, keep_backup=keep_backup)
            except Exception as exc:
                self._error(_t("update_failed", error=exc))
                return
            self._persist()
            self.root.destroy()
            return
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        restart = [sys.executable, os.path.join(app_root, "trillion.py")]
        try:
            updater.apply_update(dest, app_root, backup=keep_backup)
        except updater.UpdateError as exc:
            self._error(_t("update_failed", error=exc))
            return
        self._persist()
        try:
            subprocess.Popen(restart, cwd=app_root)
        except Exception:
            pass
        self.root.destroy()

    def _apply_source_install(self, feed: dict, dest: str, win) -> None:
        """Install a source-code fallback download next to the app.

        Used when a version has no EXE published: we still deliver the source
        package so the user can run it from source. The running build (EXE or
        source) itself is left untouched; we just extract the new files next
        to the app and restart the source build when applicable.
        """
        win.destroy()
        keep_backup = bool(self.settings.get("backup_on_update", True))
        msg = (
            _t("update_source_confirm", version=feed["version"]) + "\n"
            + (_t("update_backup_note") if keep_backup
               else _t("update_no_backup_note")))
        if not self._confirm(msg):
            return
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            app_root = os.path.dirname(os.path.abspath(sys.executable))
        else:
            app_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
        try:
            updater.apply_update(dest, app_root, backup=keep_backup)
        except updater.UpdateError as exc:
            self._error(_t("update_failed", error=exc))
            return
        self._persist()
        if frozen:
            # EXE build: the source lands next to the EXE but can't replace
            # the running binary — tell the user what happened.
            self._info(_t("update_source_installed_exe",
                          version=feed["version"]))
            return
        restart = [sys.executable, os.path.join(app_root, "trillion.py")]
        try:
            subprocess.Popen(restart, cwd=app_root)
        except Exception:
            pass
        self.root.destroy()

    def _swap_exe(self, new_exe: str, keep_backup: bool = True) -> None:
        """Replace the running EXE with ``new_exe`` and relaunch it.

        Windows won't let you overwrite or delete an EXE that is currently
        running, but it DOES let you rename it. So we rename the current EXE
        to ``<name>.old``, move the downloaded EXE into its place, then spawn
        a tiny helper script that waits for this process to fully exit,
        removes the ``.old`` (unless a backup is wanted) and launches the new
        EXE.
        """
        cur = os.path.abspath(sys.executable)
        old = cur + ".old"
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        os.rename(cur, old)                    # allowed on a running EXE
        shutil.move(new_exe, cur)              # new EXE takes the old name
        pid = os.getppid() or os.getpid()      # bootloader PID in one-file EXEs
        helper = os.path.join(os.path.dirname(cur), "_systematics_restart.bat")
        keep = "1" if keep_backup else "0"
        with open(helper, "w", encoding="ascii", errors="replace") as f:
            f.write(
                "@echo off\r\n"
                "rem Waits for the old app to exit, then finishes the swap.\r\n"
                f":wait\r\n"
                f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
                "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto :wait )\r\n"
                f'if "{keep}"=="0" ( del "{old}" >nul 2>nul )\r\n'
                f'start "" "{cur}"\r\n'
                'del "%~f0" >nul 2>nul\r\n'
            )
        try:
            subprocess.Popen([helper], cwd=os.path.dirname(cur),
                             creationflags=getattr(subprocess,
                                                   "CREATE_NO_WINDOW", 0))
        except Exception:
            # Last resort: start the new EXE directly (it may fail while the
            # old process still holds the name, but try anyway).
            subprocess.Popen([cur], cwd=os.path.dirname(cur))

    def _download_failed(self, error: str) -> None:
        if self._dl_widgets is None:
            return
        _win, _bar, status_lbl, _install_btn, later_btn = self._dl_widgets
        status_lbl.configure(text=_t("update_download_failed"))
        later_btn.configure(state="normal")
        self._error(_t("update_download_error", error=error))

    def _show_up_to_date(self, feed: dict) -> None:
        self._info(
            _t("update_up_to_date_title", version=feed["version"],
               current=__version__))

    # -- downgrade ---------------------------------------------------------

    def _open_downgrade_dialog(self) -> None:
        """Let the user pick an older release (from the feed's 'versions' list)."""
        p = PALETTE
        win = tk.Toplevel(self.root)
        win.title(_t("downgrade_window_title", app_name=APP_NAME))
        win.configure(bg=p["MAIN_BG"])
        win.resizable(False, False)
        win.transient(self.root)
        self._apply_dialog_titlebar(win)

        frame = tk.Frame(win, bg=p["MAIN_BG"], padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=_t("downgrade_title"), bg=p["MAIN_BG"],
                 fg=p["ACCENT"], font=_font(14, "bold")).pack(anchor="w")
        tk.Label(frame,
                 text=_t("downgrade_hint", version=__version__),
                 bg=p["MAIN_BG"], fg=p["TEXT"], font=_font(10)).pack(
            anchor="w", pady=(4, 8))

        listbox = tk.Listbox(frame, bg=p["CARD"], fg=p["TEXT"],
                             selectbackground=p["ACCENT"],
                             selectforeground=p["ACCENT_ON"], relief="flat",
                             font=_font(10), height=6, highlightthickness=0,
                             activestyle="none")
        listbox.pack(fill="both", expand=True)

        status_lbl = tk.Label(frame, text=_t("downgrade_loading"),
                              bg=p["MAIN_BG"], fg=p["TEXT_MUTED"], font=_font(9))
        status_lbl.pack(anchor="w", pady=(8, 0))
        bar = ttk.Progressbar(frame, style="Accent.Horizontal.TProgressbar",
                              mode="determinate", maximum=1, value=0)

        row = tk.Frame(frame, bg=p["MAIN_BG"])
        row.pack(fill="x", pady=(12, 0))
        install_btn = tk.Button(
            row, text=_t("update_button_download"), state="disabled",
            command=lambda: self._start_downgrade_download(),
            bg=p["ACCENT"], fg=p["ACCENT_ON"],
            activebackground=p["ACCENT_HOVER"],
            activeforeground=p["ACCENT_ON"], relief="flat", padx=16, pady=8,
            font=_font(10, "bold"), cursor="hand2")
        close_btn = tk.Button(row, text=_t("downgrade_close"),
                              command=win.destroy,
                              bg=p["BUTTON_SECONDARY"], fg=p["TEXT"],
                              activebackground=p["BUTTON_SECONDARY_HOVER"],
                              activeforeground=p["TEXT"], relief="flat",
                              padx=16, pady=8, font=_font(10),
                              cursor="hand2")
        install_btn.pack(side="left")
        close_btn.pack(side="left", padx=(8, 0))
        win.grab_set()

        self._downgrade_win = win
        self._downgrade_listbox = listbox
        self._downgrade_status = status_lbl
        self._downgrade_bar = bar
        self._downgrade_install_btn = install_btn
        self._downgrade_close_btn = close_btn
        self._downgrade_versions = []
        listbox.bind("<<ListboxSelect>>",
                     lambda _e: self._downgrade_selection())
        self._downgrade_pending = True
        threading.Thread(target=self._downgrade_worker,
                         args=(UPDATE_FEED_URL,), daemon=True).start()
        self.root.after(200, self._poll_downgrade)

    def _downgrade_worker(self, url: str) -> None:
        try:
            feed = updater.fetch_feed(url)
            if feed is None:
                self._downgrade_queue.put(("error", _t("update_no_feed")))
            else:
                self._downgrade_queue.put(("versions", feed.get("versions", [])))
        except updater.UpdateError as exc:
            self._downgrade_queue.put(("error", str(exc)))

    def _poll_downgrade(self) -> None:
        if (not hasattr(self, "_downgrade_status")
                or not self._downgrade_status.winfo_exists()):
            self._downgrade_pending = False
            return
        drained = False
        try:
            while True:
                msg = self._downgrade_queue.get_nowait()
                drained = True
                if msg[0] == "versions":
                    self._populate_downgrade_list(msg[1])
                else:
                    self._downgrade_status.configure(text=msg[1])
                    self._downgrade_listbox.insert(
                        "end", _t("downgrade_no_versions"))
        except queue.Empty:
            pass
        if drained:
            self._downgrade_pending = False
            return
        if self._downgrade_pending:
            self.root.after(200, self._poll_downgrade)

    def _populate_downgrade_list(self, versions: list) -> None:
        """Show only versions older than the running one, newest first."""
        current = updater.parse_version(__version__)
        rows = [v for v in versions
                if isinstance(v, dict)
                and updater.parse_version(str(v.get("version", ""))) < current]
        rows.sort(key=lambda v: updater.parse_version(v["version"]),
                  reverse=True)
        self._downgrade_versions = rows
        if not rows:
            self._downgrade_status.configure(
                text=_t("downgrade_none"))
            self._downgrade_listbox.insert("end", _t("downgrade_none_item"))
            return
        for v in rows:
            label = f"v{v['version']}"
            if v.get("notes"):
                label += " — " + v["notes"].splitlines()[0][:60]
            self._downgrade_listbox.insert("end", label)
        self._downgrade_status.configure(
            text=_t("downgrade_found", count=len(rows)))

    def _downgrade_selection(self) -> None:
        self._downgrade_install_btn.configure(
            state="normal" if self._downgrade_listbox.curselection()
            else "disabled")

    def _start_downgrade_download(self) -> None:
        sel = self._downgrade_listbox.curselection()
        if not sel:
            return
        v = self._downgrade_versions[sel[0]]
        if not ((v.get("url") or "").strip()
                or (v.get("exe_url") or "").strip()):
            self._downgrade_status.configure(
                text=_t("downgrade_no_link"))
            return
        feed = {"version": v["version"], "name": v.get("name", APP_NAME),
                "notes": v.get("notes", ""), "url": v["url"],
                "exe_url": v.get("exe_url", "")}
        # Reuse the same download/install/restart flow as updates.
        self._start_download(feed, self._downgrade_win, self._downgrade_bar,
                             self._downgrade_status,
                             self._downgrade_install_btn,
                             self._downgrade_close_btn)

    # -- dynamic labels ----------------------------------------------------

    def _mode_text(self) -> str:
        if self.note_multiply_var.get():
            return _t("mode_notes", multiplier=self.multiplier_var.get())
        fast = _t("mode_fast") if self.fast_var.get() else _t("mode_streamed")
        extra = {2: _t("mode_extra_double"), 3: _t("mode_extra_triple")}.get(
            self._passes(), "")
        return _t("mode_current", mode=fast,
                  multiplier=self.multiplier_var.get(),
                  preset=self.preset_var.get(), extra=extra)

    def _refresh_dynamic(self) -> None:
        if hasattr(self, "customise_tile"):
            self.customise_tile.set_sub(self._mode_text())
        if hasattr(self, "output_hint"):
            ext = midi_engine.output_extension(
                self._archive_format_key(self.archive_format_var.get()),
                self._passes())
            self.output_hint.configure(text=_t("output_hint", extension=ext))
            # Keep an auto-suggested output name in sync with the chosen
            # archive format / pass count: switching to 7z renames
            # song.mid.xz -> song.mid.7z instead of silently writing 7z into
            # a file still named .xz.
            current = (self._archive_format_key(
                self.archive_format_var.get()), self._passes())
            if getattr(self, "_last_out_spec", None) != current:
                self._last_out_spec = current
                if (getattr(self, "_output_auto", False)
                        and self.settings.get("auto_output")
                        and self.input_var.get().strip()
                        and os.path.isfile(self.input_var.get().strip())):
                    self._suggest_output()
        if not hasattr(self, "track_status_var"):
            return
        if self._running:
            return
        if self._load_pending:
            self.track_status_var.set(_t("status_loading_file"))
            return
        multiplier = self._multiplier()
        if self.info is not None:
            if self.note_multiply_var.get():
                _tracks, total_notes, length, _eff = self._plan()
                self.track_status_var.set(
                    _t("status_note_plan", notes=f"{total_notes:,}",
                       bytes=f"{length:,}"))
            else:
                total_tracks, _total_notes, length, eff = self._plan()
                status = _t("status_loading_track", done=0,
                            total=f"{total_tracks:,}", bytes=f"{length:,}")
                if eff and eff < multiplier:
                    status += _t("status_capped", multiplier=f"{multiplier:,}",
                                 effective=f"{eff:,}")
                self.track_status_var.set(status)
        else:
            self.track_status_var.set(
                _t("status_ready_with_multiplier", multiplier=f"{multiplier:,}"))

    # -- settings persistence ----------------------------------------------

    def _apply_settings(self, skip_validate: bool = False) -> None:
        if self.settings.get("remember_input") and self.settings.get("last_input"):
            path = self.settings["last_input"]
            if os.path.isfile(path):
                self.input_var.set(path)
                if not skip_validate:
                    # Load in the background so startup stays instant even for
                    # a very large remembered track.
                    self.info = None
                    self.track_bytes = 0
                    self._start_background_load(path)
        # Restore the optional merge-MIDI choice (path first, then its parse).
        merge_path = str(self.settings.get("last_merge_input", "") or "")
        self.merge_path_var.set(merge_path)
        self.merge_midi_var.set(bool(self.settings.get("merge_midi", False)))
        if skip_validate:
            self._refresh_merge_controls()
        elif (self.merge_midi_var.get() and merge_path
                and os.path.isfile(merge_path)):
            self._start_merge_load(merge_path)
        else:
            self._refresh_merge_controls()
        if self.settings.get("auto_output") and self.settings.get("last_output"):
            self.output_var.set(self.settings["last_output"])
            self._output_auto = True  # remembered path; refreshable on format change
        self.multiplier_var.set(str(self.settings.get("multiplier", 65535)))
        self.archive_format_var.set(self._archive_format_label(
            self.settings.get("archive_format", "xz")))
        self.ram_mode_var.set(self.settings.get("ram_mode", "normal"))
        self.max_ram_var.set(str(self.settings.get("max_ram_mb", 0)))
        self.theme_var.set(self.settings.get("theme", "dark"))
        self.window_size_var.set(self.settings.get("window_size", "1080p"))
        self.check_updates_var.set(bool(self.settings.get("check_updates", True)))
        self._refresh_after_file()
        self._apply_sizing()

    def _show_loading(self, visible: bool) -> None:
        """Show/hide the in-window loading bar (not the generation bar)."""
        if not hasattr(self, "load_progress"):
            return
        if visible:
            self.load_progress.grid()
            self.load_info.grid()
            self.load_progress.start(12)
        else:
            self.load_progress.stop()
            self.load_progress.grid_remove()

    def _start_background_load(self, path: str, manual: bool = False) -> None:
        """Parse a MIDI on a worker thread so the UI never freezes.

        ``manual`` marks a load the user explicitly requested (choosing a
        file), so errors are shown in a dialog rather than just the status
        line.
        """
        self._load_pending = True
        self._load_manual = manual
        self._load_token += 1
        token = self._load_token
        self.track_status_var.set(_t("status_loading_file"))
        self._show_loading(True)
        self.load_info_var.set(_t("status_loading_file"))

        def load() -> None:
            _console("[Systematics] " + _t("terminal_loading", path=path))

            def progress(notes_done: int, tracks_done: int, tracks_total: int) -> None:
                _console(
                    "\r" + _t("terminal_track_loaded", done=tracks_done,
                              total=tracks_total, notes=f"{notes_done:,}"),
                    end="")
                self._load_queue.put(
                    ("loadprogress", token, notes_done, tracks_done, tracks_total))

            try:
                info = midi_engine.load_midi_fast(
                    path, progress_cb=progress,
                    ram_mode=self.settings.get("ram_mode", "normal"),
                    max_ram_mb=int(self.settings.get("max_ram_mb", 0) or 0))
                ntrks = len(info.track_chunks)
                _console(
                    "\r" + _t("terminal_loaded", tracks=ntrks,
                              notes=f"{info.note_count:,}",
                              size=midi_engine.human_size(info.input_size)))
                self._load_queue.put((token, info, ""))
            except midi_engine.MidiError as exc:
                _console("\r" + _t("terminal_failed", error=exc))
                self._load_queue.put((token, None, str(exc)))
            except Exception as exc:  # never let a worker die silently
                _console("\r" + _t("terminal_failed", error=exc))
                self._load_queue.put(
                    (token, None, _t("load_unexpected_error", error=exc)))

        threading.Thread(target=load, daemon=True).start()
        self.root.after(100, self._poll_load)

    def _poll_load(self) -> None:
        try:
            msg = self._load_queue.get_nowait()
        except queue.Empty:
            if self._load_pending:
                self.root.after(100, self._poll_load)
            return

        if msg[0] == "loadprogress":
            _kind, token, notes_done, tracks_done, tracks_total = msg
            if token == self._load_token:
                self.load_info_var.set(
                    _t("load_track_progress", done=tracks_done,
                       total=tracks_total, notes=f"{notes_done:,}"))
            self.root.after(100, self._poll_load)
            return

        token, info, error = msg
        if token != self._load_token:
            # A newer load started; ignore this stale result but keep polling
            # for the newer load if one is still in flight.
            if self._load_pending:
                self.root.after(100, self._poll_load)
            return
        self._load_pending = False
        self._show_loading(False)
        if info is None:
            self.info = None
            self.track_bytes = 0
            self.load_info_var.set("")
            if hasattr(self, "load_info"):
                self.load_info.grid_remove()
            self.track_status_var.set(error or _t("load_failed"))
            if self._load_manual:
                self._load_manual = False
                self._error(error or _t("load_failed"))
        else:
            self.info = info
            self.track_bytes = sum(len(c) for c in info.track_chunks)
            self.selected_tracks = self._selection_for(info)
            self._load_manual = False
            ntrks = len(info.track_chunks)
            loaded_text = _t(
                "load_loaded_summary", notes=f"{info.note_count:,}",
                tracks=ntrks, s="s" if ntrks != 1 else "",
                mode=self._ram_mode_label(info))
            if getattr(info, "ram_capped", False):
                loaded_text += " " + _t("load_capped_note")
            self.load_info_var.set(loaded_text)
        self._refresh_after_file()

    def _persist(self) -> None:
        self.settings["last_input"] = self.input_var.get()
        self.settings["last_output"] = self.output_var.get()
        self.settings["merge_midi"] = bool(self.merge_midi_var.get())
        self.settings["last_merge_input"] = self.merge_path_var.get()
        app_settings.save(self.settings)

    # -- file / path helpers ----------------------------------------------

    def choose_input(self) -> None:
        initial = self.input_var.get().strip() or None
        path = filedialog.askopenfilename(
            parent=self.root, title=_t("file_open_title"),
            initialdir=os.path.dirname(initial) if initial else None,
            filetypes=[(_t("filetype_midi"), "*.mid *.midi"),
                       (_t("filetype_all"), "*.*")])
        if not path:
            return
        self.input_var.set(path)
        self.info = None
        self.track_bytes = 0
        if self.settings.get("auto_output") and not self.output_var.get().strip():
            self._suggest_output()
        # Load in the background so the window stays responsive even for a
        # huge manually-chosen track.
        self._start_background_load(path, manual=True)
        self._refresh_after_file()

    def choose_output(self) -> None:
        initial = self.output_var.get().strip()
        if not initial and self.input_var.get().strip():
            initial = self._derived_output()
        fmt = self._archive_format_key(self.archive_format_var.get())
        ext = midi_engine.output_extension(fmt, self._passes())
        path = filedialog.asksaveasfilename(
            parent=self.root, title=_t("file_save_title"),
            initialdir=os.path.dirname(initial) if initial else None,
            initialfile=os.path.basename(initial) if initial else _t("file_default_name"),
            defaultextension=ext,
            filetypes=[(_t("filetype_midi_archive"), f"*{ext}"),
                       (_t("filetype_all"), "*.*")])
        if path:
            self.output_var.set(path)
            self._output_auto = False  # user chose this path; don't rename it
            self._persist()

    # -- merge another MIDI ------------------------------------------------

    def choose_merge_input(self) -> None:
        """Pick the optional second MIDI that is appended unduplicated."""
        initial = (self.merge_path_var.get().strip()
                   or self.input_var.get().strip() or None)
        path = filedialog.askopenfilename(
            parent=self.root, title=_t("merge_open_title"),
            initialdir=os.path.dirname(initial) if initial else None,
            filetypes=[(_t("filetype_midi"), "*.mid *.midi"),
                       (_t("filetype_all"), "*.*")])
        if not path:
            return
        self.merge_path_var.set(path)
        self.merge_midi_var.set(True)
        self.settings["merge_midi"] = True
        self.settings["last_merge_input"] = path
        self._persist()
        self._start_merge_load(path)
        self._refresh_merge_controls()
        self._refresh_dynamic()

    def clear_merge_input(self) -> None:
        """Forget the merge MIDI (keeps the option ticked but empty)."""
        self._merge_token += 1  # invalidate any load still in flight
        self._merge_pending = False
        self.merge_info = None
        self.merge_path_var.set("")
        self.settings["last_merge_input"] = ""
        app_settings.save(self.settings)
        self._refresh_merge_controls()
        self._refresh_dynamic()

    def _on_merge_toggle(self) -> None:
        """Enable/disable the merge row and re-plan when the option flips."""
        self.settings["merge_midi"] = bool(self.merge_midi_var.get())
        app_settings.save(self.settings)
        path = self.merge_path_var.get().strip()
        if (self.merge_midi_var.get() and path and self.merge_info is None
                and not self._merge_pending and os.path.isfile(path)):
            self._start_merge_load(path)
        self._refresh_merge_controls()
        self._refresh_dynamic()

    def _append_info(self) -> midi_engine.MidiInfo | None:
        """The parsed merge MIDI to append, or None when merging is off."""
        if not self.merge_midi_var.get():
            return None
        return self.merge_info

    def _start_merge_load(self, path: str) -> None:
        """Parse the merge MIDI on a worker thread (same pattern as the input).

        The main file's loader is reused so huge merge files still stream
        instead of freezing the window.
        """
        self._merge_pending = True
        self._merge_token += 1
        token = self._merge_token
        self._refresh_merge_controls()

        def load() -> None:
            try:
                info = midi_engine.load_midi_fast(path, ram_mode="normal",
                                                  max_ram_mb=0)
                self._merge_queue.put((token, info, ""))
            except midi_engine.MidiError as exc:
                self._merge_queue.put((token, None, str(exc)))
            except Exception as exc:  # never let a worker die silently
                self._merge_queue.put(
                    (token, None, _t("load_unexpected_error", error=exc)))

        threading.Thread(target=load, daemon=True).start()
        self.root.after(100, self._poll_merge)

    def _poll_merge(self) -> None:
        try:
            token, info, error = self._merge_queue.get_nowait()
        except queue.Empty:
            if self._merge_pending:
                self.root.after(100, self._poll_merge)
            return
        if token != self._merge_token:
            if self._merge_pending:
                self.root.after(100, self._poll_merge)
            return
        self._merge_pending = False
        if info is None:
            self.merge_info = None
            self._refresh_merge_controls()
            self._error(error or _t("load_failed"))
        else:
            self.merge_info = info
            self._refresh_merge_controls()
        self._refresh_dynamic()

    def _refresh_merge_controls(self) -> None:
        """Enable/disable the merge buttons and refresh the status label."""
        if not hasattr(self, "merge_browse_btn"):
            return
        enabled = bool(self.merge_midi_var.get())
        state = "normal" if enabled else "disabled"
        self.merge_browse_btn.configure(state=state)
        self.merge_clear_btn.configure(
            state="normal" if (enabled and self.merge_path_var.get().strip())
            else "disabled")
        if not enabled:
            self.merge_status_var.set(_t("merge_hint"))
        elif self._merge_pending:
            self.merge_status_var.set(_t("merge_loading"))
        elif self.merge_info is not None:
            n = len(self.merge_info.track_chunks)
            self.merge_status_var.set(_t(
                "merge_loaded",
                name=os.path.basename(self.merge_info.path),
                tracks=n, s="" if n == 1 else "s",
                notes=f"{self.merge_info.note_count:,}"))
        else:
            self.merge_status_var.set(_t("merge_none"))

    def _derived_output(self) -> str:
        source = self.input_var.get().strip()
        if not source:
            return ""
        stem, _ = os.path.splitext(source)
        ext = midi_engine.output_extension(
            self._archive_format_key(self.archive_format_var.get()),
            self._passes())
        return f"{stem}_x{self._multiplier()}{ext}"

    def _suggest_output(self) -> None:
        self.output_var.set(self._derived_output())
        self._output_auto = True

    def _multiplier(self) -> int:
        try:
            return int(self.multiplier_var.get())
        except (ValueError, tk.TclError):
            return int(self.settings.get("multiplier", 65535))

    def _passes(self) -> int:
        """Current compression pass count (1 = .xz, 2 = .xz.xz, 3 = .xz.xz.xz)."""
        try:
            return max(1, min(3, int(self.compress_passes_var.get())))
        except (ValueError, tk.TclError):
            return int(self.settings.get("compress_passes", 1))

    def _ram_mode_label(self, info: midi_engine.MidiInfo | None = None) -> str:
        """Short label for the RAM preload mode of ``info`` (or the setting)."""
        mode = getattr(info, "ram_mode", None) if info is not None else None
        if mode is None:
            mode = midi_engine.RAM_MODES.get(
                self.settings.get("ram_mode", "normal"), 1)
        return {1: _t("ram_normal"), 2: _t("ram_double"),
                3: _t("ram_triple")}.get(
            mode, _t("ram_other", multiplier=mode))

    _MERGE_SPEED_KEYS = ("balanced", "fast", "turbo")

    def _merge_speed_label(self, key: str) -> str:
        """Translated label for a merge-speed key (combobox displays this)."""
        return _t(f"merge_speed_{key}") if key in self._MERGE_SPEED_KEYS else key

    def _archive_format_key(self, label: str) -> str:
        """Reverse-lookup a format key from its translated combobox label."""
        for key in midi_engine.ARCHIVE_FORMATS:
            if self._archive_format_label(key) == label:
                return key
        return str(self.settings.get("archive_format", "xz"))

    def _archive_format_label(self, key: str) -> str:
        """Translated label for an archive-format key (combobox displays this)."""
        key = key if key in midi_engine.ARCHIVE_FORMATS else "xz"
        try:
            return _t(f"archive_format_{key}")
        except Exception:
            return key.upper()

    def _merge_speed_key(self, label: str) -> str:
        """Map a (possibly translated) label back to its merge-speed key."""
        for key in self._MERGE_SPEED_KEYS:
            if label == _t(f"merge_speed_{key}"):
                return key
        return label if label in self._MERGE_SPEED_KEYS else "balanced"

    # -- track selection ---------------------------------------------------

    def _selection_for(self, info: midi_engine.MidiInfo) -> list[int]:
        """Restore the stored track selection for a file (default: all tracks)."""
        n = len(info.track_chunks)
        stored = self.settings.get("track_selection", {}).get(info.path)
        if isinstance(stored, list) and stored:
            valid = [i for i in stored if isinstance(i, int) and 0 <= i < n]
            if valid:
                return valid
        return list(range(n))

    def _selected(self) -> list[int]:
        """Return the currently selected (to-multiply) track indices."""
        if self.info is None:
            return []
        n = len(self.info.track_chunks)
        return [i for i in self.selected_tracks if 0 <= i < n]

    def _plan(self) -> tuple:
        """Plan for the current file/selection (empty when no file loaded)."""
        if self.info is None:
            return (0, 0, 0, 0)
        append = self._append_info()
        if self.note_multiply_var.get():
            return midi_engine.plan_note_build(self.info, self._multiplier(),
                                               self._selected(),
                                               append_info=append)
        return midi_engine.plan_build(self.info, self._multiplier(),
                                      self._selected(), append_info=append)

    def _set_track_selected(self, index: int, selected: bool) -> None:
        """Toggle one track's multiply flag, persist, and refresh the UI."""
        if self.info is None:
            return
        n = len(self.info.track_chunks)
        current = set(self._selected())
        if selected:
            current.add(index)
        else:
            current.discard(index)
        self.selected_tracks = [i for i in range(n) if i in current]
        selection = self.settings.setdefault("track_selection", {})
        selection[self.info.path] = list(self.selected_tracks)
        self._persist()
        self._refresh_after_file()

    def _refresh_after_file(self) -> None:
        self._redraw_card()
        name = os.path.basename(self.input_var.get()) if self.input_var.get() else "None"
        self.file_status_var.set(_t("file_current", name=name))
        self._refresh_dynamic()

    # -- file properties ---------------------------------------------------

    def show_file_properties(self) -> None:
        if self.info is None:
            self._info(_t("fp_no_file"))
            return

        p = PALETTE
        window = tk.Toplevel(self.root)
        window.title(_t("fp_window_title", app_name=APP_NAME))
        window.configure(bg=p["MAIN_BG"])
        window.resizable(False, False)
        window.transient(self.root)
        self._apply_dialog_titlebar(window)

        frame = tk.Frame(window, bg=p["MAIN_BG"], padx=20, pady=18)
        frame.pack(fill="both", expand=True)

        info = self.info
        row = 0
        for label, value in (
            (_t("fp_file"), os.path.basename(self.input_var.get())),
            (_t("fp_path"), self.input_var.get()),
            (_t("fp_size"),
             f"{midi_engine.human_size(info.input_size)} ({info.input_size:,} bytes)"),
            (_t("fp_format"), _t("fp_format_value", format=info.format)),
            (_t("fp_tracks"), str(len(info.track_chunks))),
            (_t("fp_notes"), f"{info.note_count:,}"),
            (_t("fp_loader"),
             _loader_label(getattr(info, "loader", "standard") or "standard")),
            (_t("fp_ram_preload"), self._ram_mode_label(info)),
            (_t("fp_memory"),
             f"{midi_engine.human_size(midi_engine.ram_footprint(info))} "
             f"({midi_engine.ram_footprint(info):,} bytes)"),
            (_t("fp_end_of_track"), _t("yes") if info.end_of_track else _t("no")),
        ):
            tk.Label(frame, text=label + ":", bg=p["MAIN_BG"], fg=p["TEXT_MUTED"],
                     font=_font(9)).grid(row=row, column=0, sticky="nw", pady=2)
            tk.Label(frame, text=value, bg=p["MAIN_BG"], fg=p["TEXT"], font=_font(9),
                     wraplength=360, justify="left").grid(
                row=row, column=1, sticky="nw", padx=(14, 0), pady=2)
            row += 1

        # Track selection (multi-track support).
        n = len(info.track_chunks)
        tk.Label(frame,
                 text=_t("fp_tracks_to_multiply",
                         multiplier=f"{self._multiplier():,}"),
                 bg=p["MAIN_BG"], fg=p["ACCENT"], font=_font(10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 4))
        row += 1
        selected = set(self._selected())
        for i in range(n):
            var = tk.BooleanVar(value=i in selected)
            notes = info.track_note_counts[i] if i < len(info.track_note_counts) else 0
            name = info.track_names[i] if i < len(info.track_names) else ""
            if name:
                label_text = _t("fp_track_row_named", number=i + 1,
                                notes=f"{notes:,}", name=name)
            else:
                label_text = _t("fp_track_row", number=i + 1,
                                notes=f"{notes:,}")
            ttk.Checkbutton(
                frame, text=label_text, variable=var,
                command=lambda idx=i, v=var: self._set_track_selected(idx, v.get())
            ).grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
            row += 1

        sep = tk.Frame(frame, bg=p["CARD_BORDER"], height=1)
        sep.grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        row += 1

        tk.Label(frame, text=_t("fp_output"), bg=p["MAIN_BG"],
                 fg=p["TEXT_MUTED"],
                 font=_font(9)).grid(row=row, column=0, sticky="w", pady=2)
        out_entry = tk.Entry(frame, textvariable=self.output_var, width=44,
                             bg=p["FIELD"], fg=p["TEXT"], insertbackground=p["TEXT"],
                             relief="flat")
        out_entry.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=2)
        tk.Button(frame, text=_t("fp_choose"), command=self.choose_output,
                  bg=p["BUTTON_SECONDARY"], fg=p["TEXT"],
                  activebackground=p["BUTTON_SECONDARY_HOVER"],
                  activeforeground=p["TEXT"], relief="flat", padx=10, pady=2,
                  font=_font(9), cursor="hand2").grid(
            row=row, column=2, sticky="w", padx=(8, 0), pady=2)
        row += 1

        tk.Button(frame, text=_t("fp_close"), command=window.destroy,
                  bg=p["ACCENT"],
                  fg=p["ACCENT_ON"], activebackground=p["ACCENT_HOVER"],
                  activeforeground=p["ACCENT_ON"], relief="flat", padx=18,
                  pady=6, font=_font(9, "bold"), cursor="hand2").grid(
            row=row, column=0, columnspan=3, sticky="e", pady=(16, 0))

        window.grab_set()

    # -- run lifecycle -----------------------------------------------------

    def start_generate(self) -> None:
        if self._running:
            return
        if self._load_pending:
            self._info(_t("run_still_loading"))
            return
        if self.info is None or not os.path.isfile(self.input_var.get()):
            self._error(_t("run_need_file"))
            return

        try:
            multiplier = int(self.multiplier_var.get())
        except (ValueError, tk.TclError):
            self._error(_t("run_error_multiplier_number"))
            return
        if not 1 <= multiplier <= 65535:
            self._error(_t("run_error_multiplier_range"))
            return

        input_path = self.input_var.get()
        output_path = self.output_var.get().strip().strip('"')
        fmt = self._archive_format_key(self.archive_format_var.get())
        expected_ext = midi_engine.output_extension(fmt, self._passes())
        # Keep an auto-suggested output name in sync with the chosen archive
        # format (e.g. switching to 7z renames song.mid.xz -> song.mid.7z).
        if (not output_path or (
                getattr(self, "_output_auto", False)
                and self.settings.get("auto_output")
                and not output_path.lower().endswith(expected_ext))):
            output_path = self._derived_output() or _t("file_default_name")
            self.output_var.set(output_path)

        if os.path.abspath(output_path) == os.path.abspath(input_path):
            self._error(_t("run_error_output_same"))
            return
        if os.path.exists(output_path):
            if not self._confirm(
                    _t("run_overwrite_confirm", path=output_path)):
                return

        try:
            preset = int(self.preset_var.get())
        except (ValueError, tk.TclError):
            preset = int(self.settings.get("compression_preset", 6))
        fast = bool(self.fast_var.get())
        passes = self._passes()
        merge_speed = self._merge_speed_key(self.merge_speed_var.get())

        selected = self._selected()
        if not selected:
            self._error(_t("run_error_no_tracks"))
            return

        # Resolve the optional merge MIDI before planning so its tracks are
        # budgeted against the 65,535-track limit and its notes are counted.
        merge_info = None
        if self.merge_midi_var.get():
            merge_path = self.merge_path_var.get().strip()
            if not merge_path:
                self._error(_t("run_error_merge_missing"))
                return
            if self._merge_pending:
                self._error(_t("run_error_merge_not_loaded"))
                return
            if self.merge_info is None:
                try:
                    self.merge_info = midi_engine.load_midi_fast(
                        merge_path, ram_mode="normal", max_ram_mb=0)
                except midi_engine.MidiError as exc:
                    self._error(str(exc))
                    return
                except Exception as exc:
                    self._error(_t("load_unexpected_error", error=exc))
                    return
            merge_info = self.merge_info
            if merge_info.division != self.info.division:
                self._error(_t(
                    "run_error_merge_division",
                    merge=int.from_bytes(merge_info.division, "big"),
                    main=int.from_bytes(self.info.division, "big")))
                return

        note_multiply = bool(self.note_multiply_var.get())
        if note_multiply:
            total_tracks, total_notes, length, eff = midi_engine.plan_note_build(
                self.info, multiplier, selected, append_info=merge_info)
            if length > midi_engine.MAX_TRACK_BYTES:
                self._error(_t(
                    "run_error_track_too_big",
                    size=midi_engine.human_size(length),
                    max=f"{midi_engine.MAX_TRACK_BYTES:,}"))
                return
        else:
            total_tracks, _total_notes, length, eff = midi_engine.plan_build(
                self.info, multiplier, selected, append_info=merge_info)
            if total_tracks > midi_engine.MAX_TRACKS:
                self._error(_t("run_error_merge_tracks",
                               tracks=f"{total_tracks:,}",
                               max=f"{midi_engine.MAX_TRACKS:,}"))
                return
            if eff < multiplier:
                self._info(
                    _t("run_cap_notice", multiplier=f"{multiplier:,}",
                       effective=f"{eff:,}", tracks=f"{total_tracks:,}"))

        self._persist()
        self._set_running(True)
        self._note_run = note_multiply
        self.progress.configure(maximum=total_notes if note_multiply else total_tracks,
                                value=0)
        self._run_uncompressed = length
        if note_multiply:
            self.track_status_var.set(
                _t("status_multiplying_notes", done=0,
                   total=f"{total_notes:,}", bytes=f"{length:,}"))
        else:
            self.track_status_var.set(
                _t("status_loading_track", done=0, total=f"{total_tracks:,}",
                   bytes=f"{length:,}"))

        self._cancel = threading.Event()
        info = self.info  # already parsed & held in memory; skip re-reading
        self._thread = threading.Thread(
            target=self._worker,
            args=(input_path, output_path, multiplier, preset, fast, passes,
                  info, selected, note_multiply, merge_speed, fmt, merge_info),
            daemon=True)
        self._thread.start()
        self.root.after(60, self._poll)

    def _worker(self, input_path: str, output_path: str, multiplier: int,
                preset: int, fast: bool, passes: int,
                info: midi_engine.MidiInfo | None,
                multiply_tracks: list[int], note_multiply: bool,
                merge_speed: str = "balanced",
                archive_format: str = "xz",
                merge_info: midi_engine.MidiInfo | None = None) -> None:
        def progress(done: int, total: int, notes_done: int,
                     bytes_done: int) -> None:
            self._queue.put(("progress", done, total, notes_done, bytes_done))

        def progress2(done: int, total: int, pass_num: int) -> None:
            self._queue.put(("progress2", done, total, pass_num))

        try:
            if note_multiply:
                result = midi_engine.build_note_multiplied_xz(
                    input_path, output_path, multiplier,
                    preset=preset, fast=fast, compress_passes=passes,
                    progress_cb=progress, progress2_cb=progress2,
                    cancel_event=self._cancel, info=info,
                    multiply_tracks=multiply_tracks, speed=merge_speed,
                    archive_format=archive_format, append_info=merge_info)
            else:
                result = midi_engine.build_duplicated_xz(
                    input_path, output_path, multiplier,
                    preset=preset, fast=fast, compress_passes=passes,
                    progress_cb=progress, progress2_cb=progress2,
                    cancel_event=self._cancel, info=info,
                    multiply_tracks=multiply_tracks, speed=merge_speed,
                    archive_format=archive_format, append_info=merge_info)
            self._queue.put(("done", result))
        except midi_engine.Cancelled:
            self._queue.put(("cancelled", output_path))
        except midi_engine.MidiError as exc:
            self._queue.put(("error", str(exc), output_path))
        except Exception as exc:  # never let a worker thread die silently
            self._queue.put(("error", _t("load_unexpected_error", error=exc),
                             output_path))

    def _poll(self) -> None:
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, done, total, notes_done, bytes_done = message
                    self.progress.configure(value=done)
                    speed = ""
                    elapsed = time.perf_counter() - self._run_started
                    if elapsed > 0.05:
                        speed = " · " + _t(
                            "speed_programme",
                            speed=self._speed_text(notes_done, bytes_done, elapsed))
                    if self._note_run:
                        self.track_status_var.set(
                            _t("status_multiplying_notes", done=f"{done:,}",
                               total=f"{total:,}",
                               bytes=f"{self._run_uncompressed:,}") + speed)
                    else:
                        self.track_status_var.set(
                            _t("status_loading_track", done=f"{done:,}",
                               total=f"{total:,}",
                               bytes=f"{self._run_uncompressed:,}") + speed)
                elif kind == "progress2":
                    _, done, total, pass_num = message
                    self.track_status_var.set(
                        _t("status_compress_pass", done=f"{done:,}",
                           total=f"{total:,}", pass_num=pass_num))
                elif kind == "done":
                    self._finish(message[1])
                elif kind == "cancelled":
                    self._cancelled(message[1])
                elif kind == "error":
                    self._failed(message[1], message[2])
        except queue.Empty:
            pass
        if self._running:
            self.root.after(60, self._poll)

    def _speed_text(self, notes: int, bytes_: int, elapsed: float) -> str:
        """Format a processing-speed summary like the reference app's log."""
        if elapsed <= 0:
            return ""
        nps = notes / elapsed
        mbps = bytes_ / elapsed / (1024 * 1024)
        return f"{nps:,.0f} notes/s ({mbps:.1f} MB/s)"

    def _finish(self, result: midi_engine.BuildResult) -> None:
        self._set_running(False)
        self.progress.configure(
            value=result.total_notes if result.note_mode else result.track_count)
        ext = midi_engine.archive_extension(
            result.archive_format, result.compress_passes)
        self.track_status_var.set(
            _t("status_done", uncompressed=f"{result.uncompressed_size:,}",
               compressed=f"{result.compressed_size:,}", extension=ext))
        self.settings["last_output"] = result.output_path
        self._persist()
        avg_speed = self._speed_text(result.total_notes,
                                     result.uncompressed_size, result.elapsed)
        lines = [
            _t("finish_title"),
            "",
            _t("finish_tracks", count=f"{result.track_count:,}"),
        ]
        if result.note_mode:
            lines.append(_t("finish_note_single_track"))
        if result.effective_multiplier and \
                result.effective_multiplier != result.multiplier:
            lines.append(
                _t("finish_multiplier_capped",
                   effective=f"{result.effective_multiplier:,}",
                   requested=f"{result.multiplier:,}"))
        else:
            lines.append(_t("finish_multiplier",
                            multiplier=f"{result.multiplier:,}"))
        if 0 < len(result.selected_tracks) < len(result.per_track_notes):
            sel = ", ".join(str(i + 1) for i in result.selected_tracks)
            lines.append(_t("finish_multiplied", tracks=sel))
        if getattr(result, "append_tracks", 0):
            lines.append(_t(
                "finish_merge", name=result.append_name,
                tracks=f"{result.append_tracks:,}",
                notes=f"{result.append_notes:,}"))
        lines += [
            _t("finish_total_notes", count=f"{result.total_notes:,}"),
            _t("finish_uncompressed",
               size=midi_engine.human_size(result.uncompressed_size)),
            _t("finish_compressed",
               size=midi_engine.human_size(result.compressed_size)),
            _t("finish_archive_format",
               format=result.archive_format.upper()),
            _t("finish_compress_passes", count=result.compress_passes),
            _t("finish_elapsed", seconds=f"{result.elapsed:.2f}s"),
            _t("finish_avg_speed", speed=avg_speed),
            "",
            _t("finish_saved_to"),
            result.output_path,
        ]
        self._info("\n".join(lines))

    def _cancelled(self, output_path: str) -> None:
        self._set_running(False)
        self.track_status_var.set(_t("status_cancelled"))
        _remove_partial(output_path)

    def _failed(self, message: str, output_path: str) -> None:
        self._set_running(False)
        self.track_status_var.set(_t("status_failed"))
        _remove_partial(output_path)
        self._error(message)

    def request_cancel(self) -> None:
        if self._running:
            self._cancel.set()
            self.track_status_var.set(_t("status_cancelling"))

    def _set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self._run_started = time.perf_counter()
        self.start_tile.set_enabled(not running)
        self.interrupt_tile.set_enabled(running)
        if not running:
            self._refresh_dynamic()

    # -- icon / close ------------------------------------------------------

    def _load_icon(self) -> None:
        icon_dir = os.path.dirname(os.path.abspath(__file__))
        if os.name == "nt":
            # Windows taskbar: needs a real .ico and an explicit AppUserModelID,
            # otherwise the shell shows python.exe's icon instead of ours.
            _set_app_user_model_id("SystematicsTRILLIONMIDImaker.1.0")
            ico_path = os.path.join(icon_dir, "icon.ico")
            if os.path.isfile(ico_path):
                try:
                    self.root.iconbitmap(default=ico_path)
                    return
                except tk.TclError:
                    pass
        icon_path = os.path.join(icon_dir, "icon.png")
        if os.path.isfile(icon_path):
            try:
                self._icon = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, self._icon)
            except tk.TclError:
                self._icon = None

    def _on_close(self) -> None:
        if self._running and not self._confirm(_t("run_quit_confirm")):
            return
        if self._running:
            self._cancel.set()
        self._persist()
        self.root.destroy()

    def _apply_dialog_titlebar(self, window: tk.Toplevel) -> None:
        """Match a popup window's title bar to the active theme."""
        _enable_dark_titlebar(window, self._theme_name == "dark")

    # -- themed message dialogs -------------------------------------------

    def _themed_dialog(self, title: str, message: str, kind: str = "info",
                       buttons=None):
        """Show a modal dialog styled with the active palette (dark/light).

        ``kind`` is ``info``, ``error``, ``question`` or ``warning`` and picks
        the glyph/colour. ``buttons`` is a list of ``(label, value)`` pairs;
        the default is a single OK button. Returns the pressed button's
        ``value`` (or ``None`` when the window is closed). Native ``messagebox``
        dialogs ignore the app theme, so every popup routes through here.
        """
        p = PALETTE
        if buttons is None:
            buttons = [(_t("dialog_ok"), "ok")]

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=p["MAIN_BG"])
        win.resizable(False, False)
        win.transient(self.root)
        self._apply_dialog_titlebar(win)

        frame = tk.Frame(win, bg=p["MAIN_BG"], padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        glyphs = {"info": "i", "error": "\u2715", "question": "?",
                  "warning": "!"}
        glyph = glyphs.get(kind, "i")
        glyph_colour = {"error": "#e5484d", "warning": "#e8a13d"}.get(
            kind, p["ACCENT"])
        tk.Label(frame, text=glyph, bg=p["MAIN_BG"], fg=glyph_colour,
                 font=_font(16, "bold")).pack(anchor="w", pady=(0, 8))

        tk.Label(frame, text=message, bg=p["MAIN_BG"], fg=p["TEXT"],
                 font=_font(10), justify="left", wraplength=430,
                 anchor="w").pack(anchor="w")

        row = tk.Frame(frame, bg=p["MAIN_BG"])
        row.pack(fill="x", pady=(18, 0))

        result = {"value": None}

        def _finish(value) -> None:
            result["value"] = value
            win.destroy()

        for label, value in buttons:
            accent = value not in ("cancel", "later", "no", "close")
            tk.Button(
                row, text=label, command=lambda v=value: _finish(v),
                bg=p["ACCENT"] if accent else p["BUTTON_SECONDARY"],
                fg=p["ACCENT_ON"] if accent else p["TEXT"],
                activebackground=(p["ACCENT_HOVER"] if accent
                                  else p["BUTTON_SECONDARY_HOVER"]),
                activeforeground=p["ACCENT_ON"] if accent else p["TEXT"],
                relief="flat", padx=16, pady=6,
                font=_font(9, "bold" if accent else "normal"),
                cursor="hand2").pack(side="left", padx=(0, 8))

        win.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        x = parent_x + max(0, (parent_w - width) // 2)
        y = parent_y + max(0, (parent_h - height) // 3)
        win.geometry(f"+{x}+{y}")

        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: _finish(None))
        self.root.wait_window(win)
        return result["value"]

    def _info(self, message: str, title: str | None = None) -> None:
        self._themed_dialog(title or APP_NAME, message, kind="info")

    def _error(self, message: str, title: str | None = None) -> None:
        self._themed_dialog(title or APP_NAME, message, kind="error")

    def _confirm(self, message: str, title: str | None = None) -> bool:
        return self._themed_dialog(
            title or APP_NAME, message, kind="question",
            buttons=[(_t("yes"), "yes"), (_t("no"), "no")]) == "yes"


def _remove_partial(path: str) -> None:
    """Delete a partial/corrupt output file after a failed run."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _set_app_user_model_id(app_id: str) -> None:
    """Give the process an explicit Windows AppUserModelID.

    Without this, Windows groups the window under the Python interpreter and
    shows python.exe's icon on the taskbar instead of the app's icon.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _enable_dark_titlebar(root: tk.Tk, dark: bool = True) -> None:
    """Ask Windows to render this window's title bar dark (or light)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32")
        dwmapi = ctypes.WinDLL("dwmapi")
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

        root.update_idletasks()
        hwnd = user32.GetParent(ctypes.c_void_p(root.winfo_id()))
        enabled = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on Win10 20H1+, 19 before that.
        for attribute in (20, 19):
            if dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled),
                    ctypes.sizeof(enabled)) == 0:
                break
    except Exception:
        pass


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
