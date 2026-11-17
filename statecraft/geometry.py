"""Board coordinates, movement directions, and range checks.

Coordinates are 0-indexed (x = column, y = row, y=0 is the top row).
They display chess-style: columns A, B, C... and rows 1, 2, 3... from the top.
"""

from __future__ import annotations

DIRECTIONS: dict[str, tuple[int, int]] = {
    "n": (0, -1),
    "ne": (1, -1),
    "e": (1, 0),
    "se": (1, 1),
    "s": (0, 1),
    "sw": (-1, 1),
    "w": (-1, 0),
    "nw": (-1, -1),
}


def distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Range distance: a straight step costs 1 and a diagonal step costs 2.

    That is plain taxicab distance, so diagonal reach is range // 2.
    """
    return abs(x1 - x2) + abs(y1 - y2)


def in_range(x1: int, y1: int, x2: int, y2: int, rng: int) -> bool:
    d = distance(x1, y1, x2, y2)
    return 0 < d <= rng


def on_board(x: int, y: int, size: int) -> bool:
    return 0 <= x < size and 0 <= y < size


def coord_label(x: int, y: int) -> str:
    return f"{column_label(x)}{y + 1}"


def parse_label(label: str) -> tuple[int, int] | None:
    """'C5' -> (2, 4); None if it isn't a square label."""
    label = label.strip().upper()
    if len(label) < 2 or not label[0].isalpha() or not label[1:].isdigit():
        return None
    return ord(label[0]) - ord("A"), int(label[1:]) - 1


def column_label(x: int) -> str:
    return chr(ord("A") + x)
