"""The State color palette: 20 named colors, chosen to stay distinct from
each other and readable on the dark board. Players pick one (first come,
first served); anyone who doesn't gets the first free one at the start."""

from __future__ import annotations

PALETTE: dict[str, tuple[int, int, int]] = {
    "Crimson": (214, 40, 57),
    "Coral": (255, 127, 102),
    "Orange": (242, 140, 40),
    "Amber": (255, 191, 0),
    "Lemon": (240, 230, 90),
    "Lime": (160, 210, 50),
    "Green": (46, 158, 68),
    "Olive": (120, 128, 30),
    "Mint": (130, 230, 180),
    "Teal": (0, 150, 136),
    "Cyan": (40, 200, 230),
    "Sky": (120, 170, 255),
    "Blue": (40, 90, 220),
    "Violet": (165, 80, 230),
    "Magenta": (220, 50, 180),
    "Pink": (255, 150, 200),
    "Maroon": (130, 30, 60),
    "Brown": (140, 90, 45),
    "Tan": (210, 180, 140),
    "Silver": (200, 205, 212),
}


def canonical(name: str) -> str | None:
    """'teal' -> 'Teal'; None if it isn't a palette color."""
    for key in PALETTE:
        if key.lower() == name.strip().lower():
            return key
    return None


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB -> CIE L*a*b* (D65), for perceptual distance checks."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Perceptual color difference (CIE76 delta E)."""
    la, lb = _lab(a), _lab(b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5
