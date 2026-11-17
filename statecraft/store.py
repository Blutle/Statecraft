"""JSON persistence with atomic writes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import GameState


def load(path: str | Path) -> GameState | None:
    """Load game state, or return None if the file does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return GameState.from_dict(json.load(f))


def history_path(game_path: str | Path) -> Path:
    """game.json -> game.history.jsonl"""
    p = Path(game_path)
    return p.with_name(p.stem + ".history.jsonl")


class History:
    """Append-only JSON Lines log of everything that happens in a game.

    One line per record, each stamped with a sequence number and wall-clock
    time. Flushed and fsynced per line so nothing is lost on a crash.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.seq = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                self.seq = sum(1 for _ in f)

    def append(self, record: dict) -> None:
        self.seq += 1
        line = {"seq": self.seq, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def tail(self, n: int) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(line) for line in lines]

    def archive(self) -> Path | None:
        """Rename the current log aside (used when a game is reset)."""
        if not self.path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.path.with_name(f"{self.path.stem}.{stamp}.jsonl")
        os.replace(self.path, dest)
        self.seq = 0
        return dest


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
