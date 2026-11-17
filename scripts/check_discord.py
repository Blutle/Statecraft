"""Connect to Discord once, report whether the bot can see the game server
and has the permissions setup needs, then exit. Prints an invite link if the
bot isn't in the server yet. Never prints the token.

Usage:  py scripts/check_discord.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402

from statecraft import config  # noqa: E402

from statecraft.discord_setup import REQUIRED  # noqa: E402


async def main() -> int:
    cfg = config.load()
    client = discord.Client(intents=discord.Intents.default())
    result = {"code": 1}

    @client.event
    async def on_ready():
        try:
            app_id = client.application_id
            print(f"Logged in as {client.user} (application {app_id})")
            invite = discord.utils.oauth_url(app_id, permissions=REQUIRED,
                                             scopes=("bot", "applications.commands"),
                                             guild=discord.Object(cfg.guild_id))
            guild = client.get_guild(cfg.guild_id)
            if guild is None:
                print(f"\nThe bot is NOT in server {cfg.guild_id}. Invite it with:\n{invite}")
                return
            me = guild.me
            perms = me.guild_permissions
            print(f"In server: {guild.name}  (bot's top role: {me.top_role.name})")
            missing = [name for name, needed in REQUIRED if needed and not getattr(perms, name)]
            if perms.administrator:
                print("Permissions: Administrator (everything allowed)")
                missing = []
            if missing:
                print("MISSING permissions: " + ", ".join(missing))
                print(f"Re-invite with the right permissions:\n{invite}")
                return
            print("All required permissions present.")
            result["code"] = 0
        finally:
            await client.close()

    try:
        await client.start(cfg.token)
    except discord.LoginFailure:
        print("Login failed: the DISCORD_TOKEN in .env was rejected. Reset it on the Bot page and paste it again.")
    return result["code"]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
