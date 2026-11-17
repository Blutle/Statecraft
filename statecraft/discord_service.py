"""Owns the live game for the Discord bot: loading, serialized changes,
saving, the (test-shiftable) clock, and server bookkeeping (consoles etc.).

Every state change goes through `run()`, which holds one asyncio lock, so
two players acting at the same moment are processed one after the other.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import discord_setup, store
from .config import Config
from .game import Game
from .models import GameState, Result


class GameService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = asyncio.Lock()
        self.history = store.History(store.history_path(cfg.game_file))
        self.server = discord_setup.load_server(cfg.server_file)
        self.server.setdefault("consoles", {})
        self.game = self._load_game()

    # ------------------------------------------------------------ game state

    def _load_game(self) -> Game:
        state = store.load(self.cfg.game_file)
        return Game(state, rng=random.Random(), recorder=self.history.append)

    def reload(self) -> None:
        """Pick up hand edits to game.json."""
        self.game = self._load_game()

    def reset(self) -> str | None:
        """Wipe the game; the old history is kept under a timestamped name."""
        archived = self.history.archive()
        self.game = Game(GameState(), rng=random.Random(), recorder=self.history.append)
        store.save(self.game.state, self.cfg.game_file)
        return str(archived) if archived else None

    async def run(self, fn: Callable[[Game, datetime], Result]) -> Result:
        """Apply one engine call under the lock; save if it changed anything."""
        async with self.lock:
            result = fn(self.game, self.now())
            if result.ok:
                store.save(self.game.state, self.cfg.game_file)
            return result

    # ----------------------------------------------------------------- clock

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self.clock_offset()

    def clock_offset(self) -> timedelta:
        if not self.cfg.test_mode:
            return timedelta(0)
        return timedelta(seconds=self.server.get("test_clock_offset", 0))

    def set_clock_offset(self, seconds: int) -> None:
        self.server["test_clock_offset"] = seconds
        self.save_server()

    # ---------------------------------------------------------------- server

    def save_server(self) -> None:
        discord_setup.save_server(self.cfg.server_file, self.server)

    def channel_id(self, key: str) -> int | None:
        return self.server.get("channels", {}).get(key)

    def role_id(self, key: str) -> int | None:
        return self.server.get("roles", {}).get(key)

    def console_of(self, pid: str) -> int | None:
        return self.server["consoles"].get(pid)

    def owner_of_console(self, channel_id: int) -> str | None:
        for pid, cid in self.server["consoles"].items():
            if cid == channel_id:
                return pid
        return None

    def set_console(self, pid: str, channel_id: int | None) -> None:
        if channel_id is None:
            self.server["consoles"].pop(pid, None)
        else:
            self.server["consoles"][pid] = channel_id
        self.save_server()

    # ---- pact channels: each member has a private copy of each pact channel
    #   server["pact_channels"][pact_id][pid] = {"channel": id, "hook_id": id, "hook_token": str}

    def pact_channels(self) -> dict:
        return self.server.setdefault("pact_channels", {})

    def pact_copy(self, pact_id: str, pid: str) -> dict | None:
        return self.pact_channels().get(pact_id, {}).get(pid)

    def set_pact_copy(self, pact_id: str, pid: str, record: dict | None) -> None:
        copies = self.pact_channels().setdefault(pact_id, {})
        if record is None:
            copies.pop(pid, None)
            if not copies:
                self.pact_channels().pop(pact_id, None)
        else:
            copies[pid] = record
        self.save_server()

    def pact_of_channel(self, channel_id: int) -> tuple[str, str] | None:
        """(pact_id, owner pid) for a pact channel copy."""
        for pact_id, copies in self.pact_channels().items():
            for pid, rec in copies.items():
                if rec["channel"] == channel_id:
                    return pact_id, pid
        return None

    # ---- DM threads: server["dm_threads"][owner][other] = thread id (a private
    #      thread in the owner's console for their conversation with `other`)

    def dm_thread(self, owner: str, other: str) -> int | None:
        return self.server.setdefault("dm_threads", {}).get(owner, {}).get(other)

    def set_dm_thread(self, owner: str, other: str, thread_id: int | None) -> None:
        threads = self.server.setdefault("dm_threads", {}).setdefault(owner, {})
        if thread_id is None:
            threads.pop(other, None)
        else:
            threads[other] = thread_id
        self.save_server()

    def dm_of_thread(self, thread_id: int) -> tuple[str, str] | None:
        """(owner, other) for a DM thread."""
        for owner, threads in self.server.get("dm_threads", {}).items():
            for other, tid in threads.items():
                if tid == thread_id:
                    return owner, other
        return None

    def console_hook(self, pid: str) -> dict | None:
        return self.server.setdefault("console_hooks", {}).get(pid)

    def set_console_hook(self, pid: str, record: dict | None) -> None:
        hooks = self.server.setdefault("console_hooks", {})
        if record is None:
            hooks.pop(pid, None)
        else:
            hooks[pid] = record
        self.save_server()

    # ---- recent activity shown on the panel card (instead of console messages)

    RECENT_MAX = 6

    # ---- per-chat mutes: server["mutes"][pid] = ["pact:<pact id>", "dm:<other pid>", ...]
    # Pact messages and incoming DMs @mention the reader unless they muted that chat.

    def muted(self, pid: str, key: str) -> bool:
        return key in self.server.get("mutes", {}).get(pid, [])

    def set_muted(self, pid: str, key: str, muted: bool) -> None:
        keys = self.server.setdefault("mutes", {}).setdefault(pid, [])
        if muted and key not in keys:
            keys.append(key)
        elif not muted and key in keys:
            keys.remove(key)
        self.save_server()

    def add_recent(self, pid: str, text: str) -> None:
        items = self.server.setdefault("recent", {}).setdefault(pid, [])
        items.append({"time": self.now().isoformat(), "text": text})
        del items[:-self.RECENT_MAX]
        self.save_server()

    def recent(self, pid: str) -> list[dict]:
        return self.server.get("recent", {}).get(pid, [])

    @staticmethod
    def is_fake(pid: str) -> bool:
        """Test-mode players have no Discord account."""
        return pid.startswith("fake")
