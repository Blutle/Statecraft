"""JSON persistence with atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import GameState


def load(path: str | Path) -> GameState | None:
    """Load game state, or return None if the file does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return GameState.from_dict(json.load(f))


def save(state: GameState, path: str | Path) -> None:
    """Write to a temp file, then atomically replace the real file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
