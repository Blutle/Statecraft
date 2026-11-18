"""Bot configuration from .env (secrets never live in code or git)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    admin_ids: frozenset[int]
    game_file: Path
    server_file: Path       # Discord channel/role IDs created by setup
    test_mode: bool
    # Where Statecraft sits in the server (see .env.example). The defaults suit a
    # server of its own; the others let it live quietly at the bottom of a shared one.
    placement: str = "top"                  # top | bottom
    announcements: str = "announcements"    # existing channel to post in, or "" for the bot's own
    players_only: bool = False              # game channels visible only to players (and jurors)

    @property
    def layout(self):
        from .discord_setup import Layout
        return Layout(self.placement, self.announcements, self.players_only)


def load() -> Config:
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing from .env")
    guild = os.getenv("GUILD_ID", "").strip()
    if not guild.isdigit():
        raise SystemExit("GUILD_ID in .env must be your server's numeric ID")
    admins = frozenset(int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    game_file = ROOT / os.getenv("GAME_FILE", "game.json").strip()
    placement = os.getenv("CATEGORY_PLACEMENT", "top").strip().lower()
    if placement not in ("top", "bottom"):
        raise SystemExit("CATEGORY_PLACEMENT in .env must be top or bottom")
    return Config(
        token=token,
        guild_id=int(guild),
        admin_ids=admins,
        game_file=game_file,
        server_file=game_file.with_name("server.json"),
        test_mode=os.getenv("TEST_MODE", "0").strip() == "1",
        placement=placement,
        announcements=os.getenv("ANNOUNCEMENTS_CHANNEL", "announcements").strip().lstrip("#"),
        players_only=os.getenv("PLAYERS_ONLY", "0").strip() == "1",
    )
