"""Board image (PNG) with Pillow, designed to read well on a phone.

The image shows public information only: positions, HP, range, alive or
destroyed, and the owners of destroyed states. It never includes AP or the
owners of living states.

render(game, focus=None, viewer=None) -> PNG bytes
  focus:  a state whose range is tinted on the board
  viewer: the player looking (their tile is outlined in white, and every
          state that can reach them gets a red ring)
"""

from __future__ import annotations

import colorsys
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import geometry
from .game import Game

# Discord-dark palette
BG = (30, 31, 34)
PANEL = (43, 45, 49)
CELL_A = (49, 51, 56)
CELL_B = (54, 57, 63)
GRID = (32, 34, 37)
TEXT = (230, 231, 233)
MUTED = (148, 155, 164)
DEAD = (78, 80, 88)
DEAD_TEXT = (150, 152, 158)
THREAT = (237, 66, 69)
YOU = (255, 255, 255)
GOLD = (250, 196, 55)

_FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/TTF"),
              Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
_BOLD = ["segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"]
_REGULAR = ["segoeui.ttf", "DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]
_font_cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False):
    key = (bold, size)
    if key not in _font_cache:
        font = None
        for name in (_BOLD if bold else _REGULAR):
            for d in _FONT_DIRS:
                if (d / name).exists():
                    font = ImageFont.truetype(str(d / name), size)
                    break
            if font:
                break
        _font_cache[key] = font or ImageFont.load_default(size)
    return _font_cache[key]


def state_colour(index: int) -> tuple[int, int, int]:
    """Distinct, saturated colours: golden-ratio hue steps, two lightness bands."""
    h = (index * 0.61803398875) % 1.0
    light = 0.55 if index % 2 == 0 else 0.45
    r, g, b = colorsys.hls_to_rgb(h, light, 0.70)
    return int(r * 255), int(g * 255), int(b * 255)


def _ink(fill: tuple[int, int, int]) -> tuple[int, int, int]:
    """Black or white text, whichever reads better on `fill`."""
    r, g, b = fill
    return (20, 20, 22) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (255, 255, 255)


def _centered(draw: ImageDraw.ImageDraw, xy, text, font, fill):
    x, y = xy
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text((x - (r - l) / 2 - l, y - (b - t) / 2 - t), text, font=font, fill=fill)


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: float, size: int, bold: bool, min_size: int = 10):
    """The largest font (from `size` down to `min_size`) at which `text` fits."""
    for s in range(size, min_size - 1, -1):
        font = _font(s, bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(min_size, bold)


def _pips(draw, cx, y, hp, max_hp, size, fill_on, fill_off):
    gap = size * 0.55
    total = max_hp * size + (max_hp - 1) * gap
    x = cx - total / 2
    for k in range(max_hp):
        box = (x, y, x + size, y + size)
        if k < hp:
            draw.ellipse(box, fill=fill_on)
        else:
            draw.ellipse(box, outline=fill_off, width=max(1, int(size / 5)))
        x += size + gap


def render(game: Game, focus: str | None = None, viewer: str | None = None) -> bytes:
    st = game.state
    n = st.board_size
    players = list(st.players.values())
    colours = {p.id: state_colour(i) for i, p in enumerate(players)}
    focus_p = game.get(focus) if focus else None
    viewer_p = game.get(viewer) if viewer else None
    max_hp = game.s["starting_hp"]

    cell = max(44, min(96, 960 // max(n, 1)))
    label = int(cell * 0.45) + 10
    pad = 24
    board_px = cell * n
    width = max(pad * 2 + label + board_px, 820)
    left = (width - board_px - label) // 2 + label
    header = 70
    top = header + label

    # Legend layout: one or two columns
    def sort_name(p):
        name = p.state_name.lower()
        return name[4:] if name.startswith("the ") else name
    alive_first = sorted(players, key=lambda p: (not p.alive, sort_name(p)))
    winners = set(game.winners()) if st.phase == "ended" else set()
    cols = 2 if len(players) > 8 else 1
    row_h = 46
    rows = -(-len(alive_first) // cols)
    legend_top = top + board_px + pad
    height = legend_top + 40 + rows * row_h + pad

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    # Header
    alive = game.alive_players()
    title = f"Statecraft · Day {st.day}"
    sub = f"{len(alive)} of {len(players)} states remain"
    if st.phase == "ended":
        win = [game.get(w) for w in game.winners()]
        lead = "Game over · winners: " if len(win) > 1 else "Game over · winner: "
        sub = lead + ", ".join(w.state_name for w in win)
        if d.textlength(sub, font=_font(14)) > width - 2 * pad:      # too long to read: use abbreviations
            sub = lead + ", ".join(w.marker for w in win)
    d.text((pad, 14), title, font=_font(28, True), fill=TEXT)
    sub_room = width - 2 * pad - (260 if focus_p else 0)
    d.text((pad, 46), sub, font=_fit(d, sub, sub_room, 17, False, min_size=14), fill=MUTED)
    if focus_p:
        note = f"Showing range {game.range_of(focus_p)} of {focus_p.marker}"
        tw = d.textlength(note, font=_font(17, True))
        d.text((width - pad - tw, 46), note, font=_font(17, True), fill=colours[focus_p.id])

    # Coordinates
    lf = _font(max(12, int(cell * 0.28)), True)
    for x in range(n):
        _centered(d, (left + x * cell + cell / 2, top - label / 2), geometry.column_label(x), lf, MUTED)
    for y in range(n):
        _centered(d, (left - label / 2, top + y * cell + cell / 2), str(y + 1), lf, MUTED)

    # Grid
    for x in range(n):
        for y in range(n):
            x0, y0 = left + x * cell, top + y * cell
            d.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), fill=CELL_A if (x + y) % 2 == 0 else CELL_B)

    # Range tint for the focused state
    if focus_p and focus_p.alive:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        r = game.range_of(focus_p)
        cr = colours[focus_p.id]
        for x in range(n):
            for y in range(n):
                if geometry.in_range(focus_p.x, focus_p.y, x, y, r):
                    x0, y0 = left + x * cell, top + y * cell
                    od.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), fill=(*cr, 105))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
        d = ImageDraw.Draw(img)

    # Grid lines
    for k in range(n + 1):
        d.line((left + k * cell, top, left + k * cell, top + board_px), fill=GRID, width=2)
        d.line((left, top + k * cell, left + board_px, top + k * cell), fill=GRID, width=2)

    # States
    threats = set()
    if viewer_p and viewer_p.alive:
        threats = {p.id for p in alive if p.id != viewer_p.id and game.in_range(p, viewer_p)}
    inset = max(3, cell // 14)
    radius = max(4, cell // 6)
    mf = _font(max(12, int(cell * 0.26)), True)
    bf = _font(max(10, int(cell * 0.20)), True)
    for p in players:
        if p.x is None:
            continue
        x0, y0 = left + p.x * cell + inset, top + p.y * cell + inset
        x1, y1 = x0 + cell - 2 * inset, y0 + cell - 2 * inset
        if not p.alive:
            d.rounded_rectangle((x0, y0, x1, y1), radius, fill=DEAD)
            d.line((x0 + 6, y0 + 6, x1 - 6, y1 - 6), fill=(92, 94, 102), width=max(2, cell // 20))
            d.line((x0 + 6, y1 - 6, x1 - 6, y0 + 6), fill=(92, 94, 102), width=max(2, cell // 20))
            _centered(d, ((x0 + x1) / 2, (y0 + y1) / 2), p.marker, mf, DEAD_TEXT)
            continue
        fill = colours[p.id]
        ink = _ink(fill)
        d.rounded_rectangle((x0, y0, x1, y1), radius, fill=fill)
        if p.id in threats:
            d.rounded_rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), radius + 2, outline=THREAT, width=max(3, cell // 16))
        if viewer_p and p.id == viewer_p.id:
            d.rounded_rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), radius + 2, outline=YOU, width=max(3, cell // 16))
        if p.id in winners:
            d.rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), radius + 3, outline=GOLD, width=max(4, cell // 12))
        _centered(d, ((x0 + x1) / 2, y0 + (y1 - y0) * 0.44), p.marker, mf, ink)
        _pips(d, (x0 + x1) / 2, y0 + (y1 - y0) * 0.70, p.hp, max_hp, max(5, cell // 11), ink,
              (ink[0], ink[1], ink[2]))
        # Range badge, sitting on the tile's top-right corner
        br = max(8, int(cell * 0.15))
        bx, by = x1 - 1, y0 + 1
        d.ellipse((bx - br, by - br, bx + br, by + br), fill=BG, outline=fill, width=2)
        _centered(d, (bx, by), str(game.range_of(p)), bf, TEXT)

    # Legend
    d.text((pad, legend_top), "States", font=_font(20, True), fill=TEXT)
    hint = "number in the corner = range · dots = HP"
    if viewer_p:
        hint += " · white = you · red ring = can reach you"
    d.text((pad + 90, legend_top + 4), hint, font=_font(14), fill=MUTED)
    col_w = (width - 2 * pad) // cols
    nf, sf = _font(17, True), _font(14)
    for k, p in enumerate(alive_first):
        c, r_ = divmod(k, rows)
        x = pad + c * col_w
        y = legend_top + 40 + r_ * row_h
        sw = 36
        if p.alive:
            fill = colours[p.id]
            d.rounded_rectangle((x, y, x + sw * 1.6, y + sw), 7, fill=fill)
            _centered(d, (x + sw * 0.8, y + sw / 2), p.marker, _font(15, True), _ink(fill))
        else:
            d.rounded_rectangle((x, y, x + sw * 1.6, y + sw), 7, fill=DEAD)
            _centered(d, (x + sw * 0.8, y + sw / 2), p.marker, _font(15, True), DEAD_TEXT)
        tx = x + sw * 1.6 + 10
        name = p.state_name
        max_name = col_w - sw * 1.6 - 20
        name_font = _fit(d, name, max_name, 17, True)     # all-caps names get a little smaller
        tags = []
        if viewer_p and p.id == viewer_p.id:
            tags.append("you")
        d.text((tx, y - 1), name, font=name_font, fill=TEXT if p.alive else DEAD_TEXT)
        if p.alive:
            detail = f"HP {p.hp}/{max_hp} · range {game.range_of(p)} · {game.pos(p)}"
            colour = MUTED
            if p.id in threats:
                detail += " · can reach you"
                colour = THREAT
            if p.id in winners:
                detail += " · WINNER"
                colour = GOLD
        else:
            detail = f"destroyed day {p.eliminated_on_day} · was {p.owner_name}"
            colour = DEAD_TEXT
        if tags:
            detail += " · " + ", ".join(tags)
        d.text((tx, y + 20), detail[:60], font=sf, fill=colour)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
