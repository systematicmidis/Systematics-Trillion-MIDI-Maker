"""Canvas-drawn icons for Systematics TRILLION MIDI maker.

The app is styled after a flat "blue-on-dark" UI, so every glyph is drawn with
Tk canvas primitives instead of shipping image files or depending on icon fonts.
"""

from __future__ import annotations

import math

BLUE = "#00AEEF"
WHITE = "#FFFFFF"


def round_rect(canvas, x0, y0, x1, y1, r=12, **kwargs):
    """Draw a rounded rectangle as a smoothed polygon."""
    points = [
        x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0,
        x1, y0, x1, y0 + r, x1, y0 + r, x1, y1 - r, x1, y1 - r,
        x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y1 - r, x0, y0 + r, x0, y0 + r,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# --------------------------------------------------------------------------
# Sidebar (blue outline) glyphs
# --------------------------------------------------------------------------

def draw_hamburger(canvas, x0, y0, x1, y1, color=BLUE, width=2):
    for i in range(3):
        y = y0 + (y1 - y0) * (0.25 + 0.25 * i)
        canvas.create_line(x0, y, x1, y, fill=color, width=width, capstyle="round")


def draw_reload(canvas, cx, cy, r, color=BLUE):
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=45, extent=300,
                      style="arc", outline=color, width=2)
    a = math.radians(45)
    ex = cx + r * math.cos(a)
    ey = cy + r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)   # tangent (toward smaller angles)
    n = r * 0.5
    bx, by = ex - tx * n, ey - ty * n
    px, py = -ty, tx                     # perpendicular
    canvas.create_polygon(
        ex, ey,
        bx + px * n * 0.6, by + py * n * 0.6,
        bx - px * n * 0.6, by - py * n * 0.6,
        fill=color, outline=color)


def draw_question(canvas, cx, cy, r, color=BLUE):
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2)
    canvas.create_text(cx, cy, text="?", fill=color,
                       font=("Segoe UI", int(r * 1.5), "bold"))


def draw_info(canvas, cx, cy, r, color=BLUE):
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2)
    canvas.create_text(cx, cy, text="i", fill=color,
                       font=("Segoe UI", int(r * 1.3), "bold"))


def draw_gear_outline(canvas, cx, cy, r, color=BLUE):
    canvas.create_oval(cx - r * 0.55, cy - r * 0.55, cx + r * 0.55,
                       cy + r * 0.55, outline=color, width=2)
    for i in range(8):
        a = 2 * math.pi * i / 8
        canvas.create_line(cx + math.cos(a) * r * 0.55, cy + math.sin(a) * r * 0.55,
                           cx + math.cos(a) * r * 0.98, cy + math.sin(a) * r * 0.98,
                           fill=color, width=2)
    canvas.create_oval(cx - r * 0.22, cy - r * 0.22, cx + r * 0.22,
                       cy + r * 0.22, outline=color, width=2)


# --------------------------------------------------------------------------
# Tile button glyphs (solid blue circle + white symbol)
# --------------------------------------------------------------------------

def draw_play(canvas, cx, cy, r):
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=BLUE, outline=BLUE)
    canvas.create_polygon(cx - r * 0.32, cy - r * 0.55,
                          cx - r * 0.32, cy + r * 0.55,
                          cx + r * 0.62, cy, fill=WHITE, outline=WHITE)


def draw_stop(canvas, cx, cy, r):
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=BLUE, outline=BLUE)
    s = r * 0.5
    canvas.create_rectangle(cx - s, cy - s, cx + s, cy + s, fill=WHITE, outline=WHITE)


def draw_gear_button(canvas, cx, cy, r):
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=BLUE, outline=BLUE)
    for i in range(8):
        a = 2 * math.pi * i / 8
        canvas.create_line(cx + math.cos(a) * r * 0.5, cy + math.sin(a) * r * 0.5,
                           cx + math.cos(a) * r * 0.95, cy + math.sin(a) * r * 0.95,
                           fill=WHITE, width=max(2, int(r * 0.32)), capstyle="round")
    canvas.create_oval(cx - r * 0.5, cy - r * 0.5, cx + r * 0.5,
                       cy + r * 0.5, fill=WHITE, outline=WHITE)
    canvas.create_oval(cx - r * 0.2, cy - r * 0.2, cx + r * 0.2,
                       cy + r * 0.2, fill=BLUE, outline=BLUE)


def draw_document(canvas, x0, y0, x1, y1, color=BLUE, width=2):
    fold = (y1 - y0) * 0.22
    canvas.create_polygon(x0, y0, x1 - fold, y0, x1, y0 + fold, x1, y1, x0, y1,
                          fill="", outline=color, width=width)
    canvas.create_line(x1 - fold, y0, x1 - fold, y0 + fold, fill=color, width=width)
    canvas.create_line(x1 - fold, y0 + fold, x1, y0 + fold, fill=color, width=width)
    lx0 = x0 + (x1 - x0) * 0.2
    lx1 = x1 - (x1 - x0) * 0.3
    for f in (0.42, 0.58, 0.74):
        y = y0 + (y1 - y0) * f
        canvas.create_line(lx0, y, lx1, y, fill=color, width=width)


def draw_document_tile(canvas, cx, cy, r):
    draw_document(canvas, cx - r * 0.68, cy - r * 0.92,
                  cx + r * 0.68, cy + r * 0.92)
