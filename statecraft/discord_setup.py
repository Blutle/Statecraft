"""Create (or repair) the Statecraft roles, channels, and webhook on a server.

Safe to run repeatedly: anything already present is found (by saved ID, then
by name) and its permissions are reset to the intended values; anything
missing is created. The resulting IDs are saved to server.json, which is
git-ignored. It holds no secrets, but it is specific to the server.
"""

from __future__ import annotations

import json
from pathlib import Path

import discord

CATEGORY = "Statecraft"
CONSOLE_CATEGORY = "Statecraft Consoles"

ROLES = {
    # key: (name, colour)
    "players": ("Statecraft-Players", discord.Colour.dark_teal()),
    "updates": ("Statecraft-Updates", discord.Colour.light_grey()),
    "jury": ("Statecraft-Jury", discord.Colour.purple()),
}

# key: (channel name, topic, access)
#   readonly: everyone reads, only the bot posts (identity masking)
#   open:     everyone reads and writes (needed to run /join)
#   jury:     only the Jury role
#   admin:    hidden (server admins see it via Administrator)
CHANNELS = {
    "rules": ("rules", "The rules of Statecraft.", "readonly"),
    "how_to_play": ("how-to-play", "How to use the bot: your console, the panel, chat, DMs, and pacts.", "readonly"),
    "register": ("register", "Run /join here to enter the next game. Your state name stays private.", "open"),
    "game_board": ("game-board", "The current map and every state's public stats.", "readonly"),
    "action_log": ("action-log", "Every move, attack, upgrade, and elimination.", "readonly"),
    "public_chat": ("public-chat", "Messages posted under state names. Post with /say from your console.", "readonly"),
    "anonymous_chat": ("anonymous-chat", "Unattributed messages. Post with /anon from your console.", "readonly"),
    "jury": ("jury", "For eliminated players. Discussion only; vote privately with /vote.", "jury"),
    "admin_log": ("statecraft-admin", "Bot status and errors for the referee.", "admin"),
}

# Discord only lets a bot set channel permissions it holds itself, so every
# permission named in these overwrites must be in the bot's invite (REQUIRED).
# The bot keeps every permission it sets for players: once @everyone is
# denied something in a category, the bot (also in @everyone) loses it there
# too unless its own overwrite allows it, and then Discord won't let it set
# that permission on the channels inside.
BOT_PERMS = dict(view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                 read_message_history=True, mention_everyone=True, manage_messages=True,
                 manage_channels=True, manage_webhooks=True, use_application_commands=True,
                 use_external_apps=True, send_messages_in_threads=True, create_public_threads=True,
                 create_private_threads=True, add_reactions=True)
# Players can't post in read-only channels, so no slash commands there either.
# "Use External Apps" is denied everywhere: an app a player installed on their
# own account would post its response under their real Discord name.
READ_ONLY = dict(view_channel=True, read_message_history=True, send_messages=False,
                 send_messages_in_threads=False, create_public_threads=False,
                 create_private_threads=False, add_reactions=True,
                 use_application_commands=False, use_external_apps=False)
HIDDEN = dict(view_channel=False)
MEMBER = dict(view_channel=True, read_message_history=True, send_messages=True, add_reactions=True,
              use_external_apps=False)
# Per-player overwrites on consoles and pact channel copies
PLAYER_PRIVATE = dict(use_external_apps=False)


def _overwrites(guild: discord.Guild, access: str, roles: dict[str, discord.Role]) -> dict:
    bot = {guild.me: discord.PermissionOverwrite(**BOT_PERMS)}
    everyone = guild.default_role
    if access == "readonly":
        return {everyone: discord.PermissionOverwrite(**READ_ONLY), **bot}
    if access == "open":
        return {everyone: discord.PermissionOverwrite(**MEMBER, use_application_commands=True), **bot}
    if access == "jury":
        return {everyone: discord.PermissionOverwrite(**HIDDEN),
                roles["jury"]: discord.PermissionOverwrite(**MEMBER), **bot}
    if access == "hidden":
        return {everyone: discord.PermissionOverwrite(**HIDDEN), **bot}
    if access == "admin":
        return {everyone: discord.PermissionOverwrite(**HIDDEN), **bot}
    raise ValueError(access)


def load_server(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_server(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


REQUIRED = discord.Permissions(
    view_channel=True, manage_channels=True, manage_roles=True, manage_webhooks=True,
    send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    mention_everyone=True, manage_messages=True, add_reactions=True, use_application_commands=True,
    create_public_threads=True, create_private_threads=True, send_messages_in_threads=True,
    use_external_apps=True,
)


class _Report(list):
    """A list that also prints each line as it's added (so failures show progress)."""

    def __init__(self, echo):
        super().__init__()
        self.echo = echo

    def append(self, line):
        super().append(line)
        if self.echo:
            self.echo(line)


async def ensure_server(guild: discord.Guild, path: Path, echo=None) -> list[str]:
    """Create or repair everything. Returns a human-readable report."""
    saved = load_server(path)
    data = {"guild_id": guild.id, "roles": {}, "channels": {}}
    report = _Report(echo)

    # Roles
    roles: dict[str, discord.Role] = {}
    for key, (name, colour) in ROLES.items():
        role = guild.get_role(saved.get("roles", {}).get(key, 0)) or discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=name, colour=colour, mentionable=False,
                                           reason="Statecraft setup")
            report.append(f"created role @{name}")
        else:
            report.append(f"found role @{name}")
        roles[key] = role
        data["roles"][key] = role.id

    # Categories
    async def ensure_category(key: str, name: str, access: str) -> discord.CategoryChannel:
        cat = guild.get_channel(saved.get(key, 0)) or discord.utils.get(guild.categories, name=name)
        ow = _overwrites(guild, access, roles)
        if cat is None:
            cat = await guild.create_category(name, overwrites=ow, reason="Statecraft setup")
            report.append(f"created category {name}")
        else:
            await cat.edit(overwrites=ow, reason="Statecraft setup (repair)")
            report.append(f"found category {name} (permissions reset)")
        data[key] = cat.id
        return cat

    category = await ensure_category("category_id", CATEGORY, "readonly")
    consoles = await ensure_category("console_category_id", CONSOLE_CATEGORY, "hidden")

    # Channels
    channels: dict[str, discord.TextChannel] = {}
    for key, (name, topic, access) in CHANNELS.items():
        ch = guild.get_channel(saved.get("channels", {}).get(key, 0)) \
            or discord.utils.get(category.text_channels, name=name)
        ow = _overwrites(guild, access, roles)
        if ch is None:
            ch = await category.create_text_channel(name, topic=topic, overwrites=ow, reason="Statecraft setup")
            report.append(f"created #{name}")
            # keep the category in CHANNELS order (new channels are otherwise added at the bottom)
            keys = list(CHANNELS)
            before = [channels[k] for k in keys[:keys.index(key)] if k in channels]
            try:
                if before:
                    await ch.move(after=before[-1], reason="Statecraft setup")
                else:
                    await ch.move(beginning=True, reason="Statecraft setup")
            except discord.HTTPException:
                pass
        else:
            await ch.edit(topic=topic, overwrites=ow, category=category, reason="Statecraft setup (repair)")
            report.append(f"found #{name} (permissions reset)")
        channels[key] = ch
        data["channels"][key] = ch.id

    # #public-chat webhook: posts appear under each state's name
    diplo = channels["public_chat"]
    hooks = await diplo.webhooks()
    hook = next((h for h in hooks if h.id == saved.get("public_chat_webhook_id")), None) \
        or next((h for h in hooks if h.name == "Statecraft Public Chat"), None)
    if hook is None:
        hook = await diplo.create_webhook(name="Statecraft Public Chat", reason="Statecraft setup")
        report.append("created #public-chat webhook")
    else:
        report.append("found #public-chat webhook")
    data["public_chat_webhook_id"] = hook.id

    # Existing consoles and pact copies: make sure players can't use external apps there
    patched = 0
    for cid in [data.get("console_category_id")] + saved.get("pact_categories", []):
        cat = guild.get_channel(cid or 0)
        for ch in (cat.channels if cat else []):
            for target, ow in ch.overwrites.items():
                if isinstance(target, discord.Member) and target != guild.me and ow.use_external_apps is not False:
                    ow.update(**PLAYER_PRIVATE)
                    await ch.set_permissions(target, overwrite=ow, reason="Statecraft setup (repair)")
                    patched += 1
    if patched:
        report.append(f"blocked external apps for {patched} player channel(s)")

    # Keep existing console channel records (created later by /join)
    data["consoles"] = saved.get("consoles", {})
    save_server(path, data)
    report.append(f"saved IDs to {path.name}")
    return report
