"""One-shot: connect, create or repair the Statecraft server layout, then exit.

Usage:  py scripts/setup_server.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402

from statecraft import config, discord_setup  # noqa: E402


async def main() -> int:
    cfg = config.load()
    client = discord.Client(intents=discord.Intents.default())
    result = {"code": 1}

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(cfg.guild_id)
            if guild is None:
                print("The bot isn't in the server yet. Run scripts/check_discord.py for the invite link.")
                return
            print(f"Setting up Statecraft on {guild.name}...")
            await discord_setup.ensure_server(guild, cfg.server_file, echo=lambda line: print(f"  {line}"))
            result["code"] = 0
        except discord.Forbidden as e:
            print(f"Discord refused a change (missing permission?): {e}")
        finally:
            await client.close()

    await client.start(cfg.token)
    return result["code"]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
