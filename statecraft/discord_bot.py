"""Statecraft Discord bot.

Run with:  py -m statecraft.discord_bot      (or run_bot.bat)

Layers: commands here -> GameService (lock + save) -> Game engine (rules +
history). The engine returns Events; `Router.deliver` sends them to the
right channel, console, or webhook. Nothing reaches players except through
an engine call, so everything is in the history log.

Commands are documented in COMMANDS.md; keep the two in sync.
"""

from __future__ import annotations

import logging
import re
import secrets
import shlex
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import config, discord_setup, geometry, render_text, schedule
from .discord_service import GameService
from .game import Game
from .models import Event, Result

log = logging.getLogger("statecraft")

NO_PINGS = discord.AllowedMentions.none()
PUBLIC_KINDS_PLAYERS_PING = {"ATTACK", "ELIMINATION", "GAME_OVER", "GAME_START"}
PUBLIC_KINDS_UPDATES_PING = {"MOVE", "UPGRADE", "INFO_PURCHASE"}
BOARD_CHANGING = {"GAME_START", "MOVE", "ATTACK", "ELIMINATION", "UPGRADE", "DAILY_AP",
                  "JURY_RESULT", "GAME_OVER"}
DIRECTIONS = [app_commands.Choice(name=n, value=v) for n, v in [
    ("North (up)", "n"), ("North-east", "ne"), ("East (right)", "e"), ("South-east", "se"),
    ("South (down)", "s"), ("South-west", "sw"), ("West (left)", "w"), ("North-west", "nw")]]

HELP = """**Statecraft commands** (use them here in your console)
**Before the game**: `/name` sets or changes your state's name · `/leave` withdraws
**Actions** (2:00-8:00 PM Central, 15-minute cooldown after each):
`/move` 1 AP · `/attack` 1 AP · `/upgrade` 2 AP (range +1) · `/gift` (give AP to a state in range) · `/buyinfo` 1 AP (see today's gifts until 8:15 PM)
**Info** (free, any time): `/status` (you, or any state) · `/board` · `/info` · `/help`
**Talk** (free, any time, works after elimination too): `/say` (#public-chat, under your state name) · `/dm` (another state's console) · `/anon` (#anonymous-chat, no name)
**Jury** (after elimination): `/vote` 8:00 PM-1:45 PM, secret, changeable until the lock
**Pings**: `/notify on|off` for minor events (moves, upgrades, info purchases)

Range = horizontal + vertical steps (a diagonal step counts as 2). Everything you send the bot is logged for the post-game summary."""


# --------------------------------------------------------------- formatting

def public_status(game: Game, viewer_pid: str | None, target_pid: str) -> str:
    t = game.get(target_pid)
    v = game.get(viewer_pid) if viewer_pid else None
    lines = [f"**{t.state_name}** [{t.marker}]"]
    if t.alive:
        lines += [f"HP {t.hp} · Range {game.range_of(t)} · Position {game.pos(t)}"]
        if v and v.alive and v.id != t.id:
            d = geometry.distance(v.x, v.y, t.x, t.y)
            lines.append(f"Distance {d}: " + ("**you can reach it**" if game.in_range(v, t) else "out of your range")
                         + " · " + ("**it can reach you**" if game.in_range(t, v) else "you're out of its range"))
    else:
        lines.append(f"Destroyed on day {t.eliminated_on_day} at {game.pos(t)}. It was controlled by {t.owner_name}.")
    lines.append("*AP and cooldowns are private.*")
    return "\n".join(lines)


def own_status(game: Game, pid: str, now: datetime) -> str:
    p = game.get(pid)
    s = game.s
    text = render_text.status(game, p)
    extra = []
    if p.alive and game.state.phase == "active":
        left = game.cooldown_remaining(p, now)
        extra.append(f"  Cooldown: {'ready' if not left else schedule.fmt_duration(left)}")
        if schedule.window_open(now, s):
            extra.append(f"  Window:   OPEN until {s['window_close']}")
        else:
            extra.append(f"  Window:   closed, opens {schedule.fmt(schedule.next_at(now, s, 'window_open'), s)}")
    elif not p.alive and game.state.phase == "active":
        extra.append("  Voting:   " + ("open" if schedule.voting_open(now, s) else f"locked until {s['window_close']}"))
    return "```\n" + text + ("\n" + "\n".join(extra) if extra else "") + "\n```"


def board_embed(game: Game, viewer_pid: str | None = None) -> discord.Embed:
    viewer = game.get(viewer_pid) if viewer_pid else None
    text = render_text.board(game, viewer)
    emb = discord.Embed(title=f"Statecraft: Day {game.state.day}",
                        description=f"```\n{text[:3900]}\n```", colour=discord.Colour.dark_teal())
    return emb


def run_text(game: Game, pid: str, text: str, now: datetime) -> Result:
    """Console commands as plain text (used by /test as)."""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts:
        return Result.fail("No command given.")
    verb, args = parts[0].lower().lstrip("/"), parts[1:]

    def target(q: str):
        p, err = game.find(q)
        return (p.id, "") if p else (None, err)

    try:
        if verb == "move" and len(args) == 1:
            return game.move(pid, args[0], now=now)
        if verb == "upgrade":
            return game.upgrade(pid, now=now)
        if verb == "buyinfo":
            return game.buy_info(pid, now=now)
        if verb in ("attack", "vote") and args:
            tid, err = target(" ".join(args))
            if not tid:
                return Result.fail(err)
            return (game.attack if verb == "attack" else game.vote)(pid, tid, now=now)
        if verb == "gift" and len(args) >= 2 and args[-1].isdigit():
            tid, err = target(" ".join(args[:-1]))
            return game.gift(pid, tid, int(args[-1]), now=now) if tid else Result.fail(err)
        if verb in ("say", "anon") and args:
            return (game.say if verb == "say" else game.anon)(pid, " ".join(args))
        if verb == "dm" and len(args) >= 2:
            tid, err = target(args[0])
            return game.dm(pid, tid, " ".join(args[1:])) if tid else Result.fail(err)
    except TypeError:
        pass
    return Result.fail("Unknown command. Try: move ne | attack VE | upgrade | gift VE 2 | buyinfo | "
                       "vote VE | say hi | dm VE hello | anon hi")


def parse_duration(text: str) -> int | None:
    """'+2h', '30m', '-1d', '0' -> seconds."""
    m = re.fullmatch(r"\s*([+-]?)(\d+)\s*([smhd]?)\s*", text)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    mult = {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(3)]
    return sign * int(m.group(2)) * mult


# ------------------------------------------------------------ DM reply UI

class ReplyModal(discord.ui.Modal):
    def __init__(self, bot: "StatecraftBot", to_pid: str, to_name: str):
        super().__init__(title=f"Reply to {to_name}"[:45])
        self.bot = bot
        self.to_pid = to_pid
        self.body = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=1500)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        pid = str(interaction.user.id)
        res = await self.bot.svc.run(lambda g, now: g.dm(pid, self.to_pid, str(self.body.value)))
        await interaction.response.send_message(res.message if res.ok else f"Rejected: {res.message}")
        await self.bot.router.deliver(res.events)


class ReplyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"dmreply:(?P<sender>[\w]+)"):
    """Persistent Reply button on received DMs (survives bot restarts)."""

    def __init__(self, sender: str):
        super().__init__(discord.ui.Button(label="Reply", style=discord.ButtonStyle.primary,
                                           custom_id=f"dmreply:{sender}"))
        self.sender = sender

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["sender"])

    async def callback(self, interaction: discord.Interaction):
        bot: StatecraftBot = interaction.client  # type: ignore[assignment]
        sender = bot.svc.game.get(self.sender)
        if not sender:
            await interaction.response.send_message("That state no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(ReplyModal(bot, self.sender, sender.state_name))


# ------------------------------------------------------------------ router

class Router:
    """Delivers engine Events to Discord."""

    def __init__(self, bot: "StatecraftBot"):
        self.bot = bot

    @property
    def svc(self) -> GameService:
        return self.bot.svc

    def channel(self, key: str) -> discord.TextChannel | None:
        cid = self.svc.channel_id(key)
        return self.bot.get_channel(cid) if cid else None

    async def admin_log(self, text: str) -> None:
        ch = self.channel("admin_log")
        if ch:
            await ch.send(text[:2000], allowed_mentions=NO_PINGS)

    async def deliver(self, events: list[Event]) -> None:
        board_dirty = False
        for ev in events:
            try:
                await self._deliver_one(ev)
            except discord.HTTPException as e:
                log.exception("delivery failed")
                await self.admin_log(f"Delivery failed for {ev.kind}: {e}")
            board_dirty |= ev.kind in BOARD_CHANGING
        if board_dirty:
            await self.refresh_board()

    async def _deliver_one(self, ev: Event) -> None:
        game = self.svc.game
        if ev.kind == "PUBLIC_CHAT":
            await self.post_as_state(ev.data["state"], ev.data["text"])
        elif ev.kind == "ANONYMOUS":
            ch = self.channel("anonymous_chat")
            if ch:
                await ch.send(ev.data["text"], allowed_mentions=NO_PINGS)
        elif ev.public:
            ch = self.channel("action_log")
            if ch:
                role_key = "players" if ev.kind in PUBLIC_KINDS_PLAYERS_PING else \
                    "updates" if ev.kind in PUBLIC_KINDS_UPDATES_PING else None
                rid = self.svc.role_id(role_key) if role_key else None
                if rid:
                    await ch.send(f"<@&{rid}> {ev.public}", allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(rid)]))
                else:
                    await ch.send(ev.public, allowed_mentions=NO_PINGS)

        for pid, text in ev.private.items():
            view = None
            if ev.kind == "PRIVATE_MESSAGE":
                view = discord.ui.View(timeout=None)
                view.add_item(ReplyButton(ev.data["sender"]))
            await self.to_player(pid, text, view=view)

        if ev.kind == "ELIMINATION":
            await self.grant_role(ev.data["victim"], "jury")

    async def to_player(self, pid: str, text: str, view: discord.ui.View | None = None) -> None:
        if self.svc.is_fake(pid):
            p = self.svc.game.get(pid)
            await self.admin_log(f"[to {p.state_name if p else pid}] {text}")
            return
        cid = self.svc.console_of(pid)
        ch = self.bot.get_channel(cid) if cid else None
        if ch:
            kwargs = {"allowed_mentions": NO_PINGS}
            if view:
                kwargs["view"] = view
            await ch.send(text[:2000], **kwargs)

    async def post_as_state(self, state_name: str, text: str) -> None:
        hook = await self.bot.public_chat_webhook()
        if hook:
            await hook.send(text, username=state_name[:80], allowed_mentions=NO_PINGS)

    async def grant_role(self, pid: str, key: str) -> None:
        if self.svc.is_fake(pid):
            return
        guild = self.bot.get_guild(self.svc.cfg.guild_id)
        rid = self.svc.role_id(key)
        if not guild or not rid:
            return
        try:
            member = await guild.fetch_member(int(pid))
            await member.add_roles(discord.Object(rid), reason="Statecraft")
        except (discord.NotFound, ValueError):
            pass

    async def refresh_board(self) -> None:
        ch = self.channel("game_board")
        game = self.svc.game
        if not ch or game.state.board_size == 0:
            return
        emb = board_embed(game)
        mid = self.svc.server.get("board_message_id")
        if mid:
            try:
                msg = await ch.fetch_message(mid)
                await msg.edit(embed=emb)
                return
            except discord.NotFound:
                pass
        msg = await ch.send(embed=emb, allowed_mentions=NO_PINGS)
        self.svc.server["board_message_id"] = msg.id
        self.svc.save_server()


# --------------------------------------------------------------------- bot

class StatecraftBot(discord.Client):
    def __init__(self, cfg: config.Config):
        intents = discord.Intents.default()
        intents.message_content = True          # pact channels relay typed messages
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.cfg = cfg
        self.svc = GameService(cfg)
        self.router = Router(self)
        self.tree = app_commands.CommandTree(self)
        self._webhook: discord.Webhook | None = None
        register_commands(self)

    async def setup_hook(self):
        self.add_dynamic_items(ReplyButton)
        guild = discord.Object(self.cfg.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.scheduler.start()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        await self.router.admin_log(f"Statecraft bot online. Phase: {self.svc.game.state.phase}, "
                                    f"day {self.svc.game.state.day}."
                                    + (" **TEST MODE**" if self.cfg.test_mode else ""))

    async def public_chat_webhook(self) -> discord.Webhook | None:
        if self._webhook:
            return self._webhook
        ch = self.router.channel("public_chat")
        hid = self.svc.server.get("public_chat_webhook_id")
        if ch and hid:
            self._webhook = next((h for h in await ch.webhooks() if h.id == hid), None)
        return self._webhook

    @tasks.loop(seconds=30)
    async def scheduler(self):
        res = await self.svc.run(lambda g, now: g.run_due_events(now))
        if res.events:
            await self.router.deliver(res.events)
        if res.message:
            await self.router.admin_log(f"Scheduler ran: {res.message}")

    @scheduler.before_loop
    async def _wait_ready(self):
        await self.wait_until_ready()


# ---------------------------------------------------------------- commands

def register_commands(bot: StatecraftBot) -> None:
    tree, svc = bot.tree, bot.svc

    def is_admin(i: discord.Interaction) -> bool:
        return i.user.id in bot.cfg.admin_ids

    async def console_pid(i: discord.Interaction) -> str | None:
        """The caller's player id if this is their console; otherwise reply and return None."""
        pid = str(i.user.id)
        cid = svc.console_of(pid)
        if not cid:
            await i.response.send_message("You're not in the game. Use `/join` in #register.", ephemeral=True)
            return None
        if not svc.game.get(pid):
            await i.response.send_message(f"Name your state first with `/name` in your console: <#{cid}>",
                                          ephemeral=True)
            return None
        if i.channel_id != cid:
            await i.response.send_message(f"Use game commands in your console: <#{cid}>", ephemeral=True)
            return None
        return pid

    def resolve(q: str) -> tuple[str | None, str]:
        if q in svc.game.state.players:
            return q, ""
        p, err = svc.game.find(q)
        return (p.id if p else None), err

    async def reply(i: discord.Interaction, res: Result, ephemeral: bool = False):
        text = res.message if res.ok else f"Rejected: {res.message}"
        await i.response.send_message(text[:2000], ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        await bot.router.deliver(res.events)

    async def states_autocomplete(i: discord.Interaction, current: str, alive_only: bool):
        cur = current.lower()
        out = []
        for p in svc.game.state.players.values():
            if alive_only and not p.alive:
                continue
            if cur in p.state_name.lower() or cur == p.marker.lower():
                out.append(app_commands.Choice(name=f"{p.state_name} [{p.marker}]"[:100], value=p.id))
        return out[:25]

    async def alive_states(i, current):
        return await states_autocomplete(i, current, True)

    async def any_states(i, current):
        return await states_autocomplete(i, current, False)

    # ---- registration
    # /join takes no name on purpose: if a slash command misfires in #register
    # it posts as plain text, so the state name is only ever typed in the
    # private console (/name).
    @tree.command(description="Join the next game: creates your private console")
    async def join(i: discord.Interaction):
        if i.channel_id != svc.channel_id("register"):
            await i.response.send_message(f"Use `/join` in <#{svc.channel_id('register')}>.", ephemeral=True)
            return
        if svc.game.state.phase != "registration":
            await i.response.send_message("Registration is closed; a game is in progress.", ephemeral=True)
            return
        pid = str(i.user.id)
        existing = svc.console_of(pid)
        if existing and bot.get_channel(existing):
            await i.response.send_message(f"You've already joined. Your console is <#{existing}>.", ephemeral=True)
            return
        await i.response.defer(ephemeral=True, thinking=True)
        console = await create_console(i.guild, i.user)
        svc.set_console(pid, console.id)
        await bot.router.grant_role(pid, "players")
        await console.send("Welcome to Statecraft. This is your **private console**: only you (and the referee) "
                           "can see it. Run every game command here.\n\n"
                           "**First, name your state** with `/name`. You can rename it any time before the game "
                           f"starts.\n\n{HELP}", allowed_mentions=NO_PINGS)
        await i.followup.send(f"You're in. Head to your console, {console.mention}, and name your state with `/name`.",
                              ephemeral=True)

    @tree.command(description="Name (or rename) your state before the game starts")
    @app_commands.describe(name="Your state's name, up to 40 characters")
    async def name(i: discord.Interaction, name: app_commands.Range[str, 1, 40]):
        pid = str(i.user.id)
        cid = svc.console_of(pid)
        if not cid:
            await i.response.send_message("Use `/join` in #register first.", ephemeral=True)
            return
        if i.channel_id != cid:
            await i.response.send_message(f"Name your state in your console: <#{cid}>", ephemeral=True)
            return
        await reply(i, await svc.run(lambda g, now: g.register(i.user.display_name, name, pid=pid)))

    async def create_console(guild: discord.Guild, member: discord.abc.User) -> discord.TextChannel:
        cat = guild.get_channel(svc.server.get("console_category_id"))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(**discord_setup.BOT_PERMS),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                use_application_commands=True, add_reactions=True),
        }
        return await guild.create_text_channel(f"console-{secrets.token_hex(2)}", category=cat,
                                               overwrites=overwrites, reason="Statecraft console")

    @tree.command(description="Withdraw from the game before it starts")
    async def leave(i: discord.Interaction):
        pid = str(i.user.id)
        cid = svc.console_of(pid)
        if svc.game.get(pid):
            res = await svc.run(lambda g, now: g.unregister(pid))
            if not res.ok:
                await i.response.send_message(f"Rejected: {res.message}", ephemeral=True)
                return
        elif not cid:
            await i.response.send_message("You haven't joined.", ephemeral=True)
            return
        # Reply before deleting: the command may have been run in the console itself.
        await i.response.send_message("You've left the game. Your console will be deleted.", ephemeral=True)
        svc.set_console(pid, None)
        ch = bot.get_channel(cid) if cid else None
        if ch:
            await ch.delete(reason="Statecraft: player left")
        rid = svc.role_id("players")
        if rid and isinstance(i.user, discord.Member):
            await i.user.remove_roles(discord.Object(rid))

    # ---- actions
    @tree.command(description="Move one square (1 AP)")
    @app_commands.choices(direction=DIRECTIONS)
    async def move(i: discord.Interaction, direction: app_commands.Choice[str]):
        if pid := await console_pid(i):
            await reply(i, await svc.run(lambda g, now: g.move(pid, direction.value, now=now)))

    @tree.command(description="Attack a state in range (1 AP)")
    @app_commands.autocomplete(state=alive_states)
    async def attack(i: discord.Interaction, state: str):
        if pid := await console_pid(i):
            tid, err = resolve(state)
            await reply(i, await svc.run(lambda g, now: g.attack(pid, tid, now=now)) if tid else Result.fail(err))

    @tree.command(description="Increase your range by 1 (2 AP)")
    async def upgrade(i: discord.Interaction):
        if pid := await console_pid(i):
            await reply(i, await svc.run(lambda g, now: g.upgrade(pid, now=now)))

    @tree.command(description="Gift AP to a state in range (private)")
    @app_commands.autocomplete(state=alive_states)
    async def gift(i: discord.Interaction, state: str, amount: app_commands.Range[int, 1, 999]):
        if pid := await console_pid(i):
            tid, err = resolve(state)
            await reply(i, await svc.run(lambda g, now: g.gift(pid, tid, amount, now=now)) if tid else Result.fail(err))

    @tree.command(description="See today's AP gifts until 8:15 PM (1 AP)")
    async def buyinfo(i: discord.Interaction):
        if pid := await console_pid(i):
            await reply(i, await svc.run(lambda g, now: g.buy_info(pid, now=now)))

    @tree.command(description="Jury: secretly vote for a surviving state (8:00 PM-1:45 PM)")
    @app_commands.autocomplete(state=alive_states)
    async def vote(i: discord.Interaction, state: str):
        if pid := await console_pid(i):
            tid, err = resolve(state)
            await reply(i, await svc.run(lambda g, now: g.vote(pid, tid, now=now)) if tid else Result.fail(err))

    # ---- info
    @tree.command(description="Your status, or another state's public status")
    @app_commands.autocomplete(state=any_states)
    async def status(i: discord.Interaction, state: str | None = None):
        if not (pid := await console_pid(i)):
            return
        if state is None or resolve(state)[0] == pid:
            await i.response.send_message(own_status(svc.game, pid, svc.now()))
            return
        tid, err = resolve(state)
        await i.response.send_message(public_status(svc.game, pid, tid) if tid else f"Rejected: {err}",
                                      allowed_mentions=NO_PINGS)

    @tree.command(description="Show the board here, with a state's range highlighted")
    @app_commands.autocomplete(state=any_states)
    async def board(i: discord.Interaction, state: str | None = None):
        if not (pid := await console_pid(i)):
            return
        if svc.game.state.board_size == 0:
            await i.response.send_message("The board appears when the game starts.")
            return
        focus = resolve(state)[0] if state else pid
        await i.response.send_message(embed=board_embed(svc.game, focus or pid))

    @tree.command(description="Re-show today's AP gifts (needs /buyinfo)")
    async def info(i: discord.Interaction):
        if pid := await console_pid(i):
            if pid in svc.game.state.info_access:
                await i.response.send_message(f"```\n{svc.game.gift_report()}\n```")
            else:
                await i.response.send_message("You don't have information access today. Use `/buyinfo` (1 AP).")

    @tree.command(name="help", description="Commands and rules summary")
    async def help_cmd(i: discord.Interaction):
        await i.response.send_message(HELP, ephemeral=True)

    @tree.command(description="Opt in or out of pings for minor events")
    @app_commands.choices(setting=[app_commands.Choice(name="on", value="on"),
                                   app_commands.Choice(name="off", value="off")])
    async def notify(i: discord.Interaction, setting: app_commands.Choice[str]):
        if not await console_pid(i):
            return
        rid = svc.role_id("updates")
        if rid and isinstance(i.user, discord.Member):
            if setting.value == "on":
                await i.user.add_roles(discord.Object(rid))
            else:
                await i.user.remove_roles(discord.Object(rid))
        await i.response.send_message(f"Minor-event pings are now **{setting.value}**.")

    # ---- communication
    @tree.command(description="Post in #public-chat under your state's name")
    async def say(i: discord.Interaction, message: app_commands.Range[str, 1, 1500]):
        if pid := await console_pid(i):
            await reply(i, await svc.run(lambda g, now: g.say(pid, message)))

    @tree.command(description="Send a private message to another state")
    @app_commands.autocomplete(state=any_states)
    async def dm(i: discord.Interaction, state: str, message: app_commands.Range[str, 1, 1500]):
        if pid := await console_pid(i):
            tid, err = resolve(state)
            await reply(i, await svc.run(lambda g, now: g.dm(pid, tid, message)) if tid else Result.fail(err))

    @tree.command(description="Post anonymously in #anonymous-chat")
    async def anon(i: discord.Interaction, message: app_commands.Range[str, 1, 1500]):
        if pid := await console_pid(i):
            await reply(i, await svc.run(lambda g, now: g.anon(pid, message)))

    # ---- admin
    admin = app_commands.Group(name="admin", description="Referee tools",
                               default_permissions=discord.Permissions(manage_guild=True))

    async def admin_only(i: discord.Interaction) -> bool:
        if not is_admin(i):
            await i.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    @admin.command(description="Create or repair roles, channels, and permissions")
    async def setup(i: discord.Interaction):
        if not await admin_only(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        report = await discord_setup.ensure_server(i.guild, svc.cfg.server_file)
        svc.server = {**discord_setup.load_server(svc.cfg.server_file),
                      **{k: v for k, v in svc.server.items() if k in ("board_message_id", "test_clock_offset")}}
        svc.save_server()
        bot._webhook = None
        await i.followup.send("\n".join(report)[:2000], ephemeral=True)

    @admin.command(description="Start the game (needs 12+ players)")
    async def start(i: discord.Interaction):
        if await admin_only(i):
            unnamed = [pid for pid in svc.server["consoles"] if not svc.game.get(pid)]
            if unnamed:
                await i.response.send_message(
                    f"Rejected: {len(unnamed)} player(s) joined but haven't named a state: "
                    + ", ".join(f"<@{p}>" for p in unnamed) + ". They need to run `/name` in their console "
                    "(or `/leave`).", ephemeral=True, allowed_mentions=NO_PINGS)
                return
            await reply(i, await svc.run(lambda g, now: g.start(now=now)), ephemeral=True)

    @admin.command(name="status", description="Full game view, including owners and AP")
    async def admin_status(i: discord.Interaction):
        if not await admin_only(i):
            return
        g = svc.game
        lines = [f"Phase {g.state.phase}, day {g.state.day}, {len(g.state.players)} players, "
                 f"game time {schedule.fmt(svc.now(), g.s)}"]
        for p in g.state.players.values():
            cd = g.cooldown_remaining(p, svc.now())
            lines.append(f"{p.marker:<3} {p.state_name:<24} {p.owner_name[:16]:<16} AP {p.ap:<3} HP {p.hp} "
                         f"{g.pos(p):<4} R{g.range_of(p)} {'alive' if p.alive else 'dead'}"
                         + (f" cd {schedule.fmt_duration(cd)}" if cd else ""))
        if g.state.jury_votes:
            lines.append("Ballots: " + ", ".join(f"{g.get(j).state_name}->{g.get(t).state_name}"
                                                 for j, t in g.state.jury_votes.items()))
        if g.state.info_access:
            lines.append("Info access: " + ", ".join(g.get(p).state_name for p in g.state.info_access))
        await i.response.send_message("```\n" + "\n".join(lines)[:1900] + "\n```", ephemeral=True)

    @admin.command(description="Add (or remove, if negative) AP")
    @app_commands.autocomplete(state=any_states)
    async def give(i: discord.Interaction, state: str, amount: int):
        if not await admin_only(i):
            return
        tid, err = resolve(state)
        await reply(i, await svc.run(lambda g, now: g.give_ap(tid, amount)) if tid else Result.fail(err), ephemeral=True)

    @admin.command(description="End the game with these states as (joint) winners")
    @app_commands.describe(states="Markers or names separated by commas, e.g. VE, WE",
                           confirm="Must be True to actually end the game")
    async def declare(i: discord.Interaction, states: str, confirm: bool = False):
        if not await admin_only(i):
            return
        ids, errors = [], []
        for q in [x.strip() for x in states.split(",") if x.strip()]:
            tid, err = resolve(q)
            (ids.append(tid) if tid else errors.append(err))
        if errors:
            await i.response.send_message("Rejected: " + " ".join(errors), ephemeral=True)
            return
        if not confirm:
            names = ", ".join(svc.game.get(t).state_name for t in ids)
            await i.response.send_message(f"This ends the game with winner(s): {names}. Run again with "
                                          f"`confirm: True`.", ephemeral=True)
            return
        await reply(i, await svc.run(lambda g, now: g.declare_winners(ids)), ephemeral=True)

    @admin.command(description="Rank jurors by votes for the winner(s)")
    async def juryscore(i: discord.Interaction):
        if not await admin_only(i):
            return
        rows = svc.game.jury_scores()
        if not rows:
            await i.response.send_message("Available once the game has ended.", ephemeral=True)
            return
        lines = [f"{r['player'].owner_name[:16]:<16} {r['player'].state_name[:22]:<22} for winner {r['for_winner']:>2}"
                 f"  ballots {r['ballots']:>2}  nights {r['nights']:>2}  rate {r['rate']:.0%}" for r in rows]
        await i.response.send_message("```\n" + "\n".join(lines)[:1900] + "\n```", ephemeral=True)

    @admin.command(description="Reload game.json after editing it by hand")
    async def reload(i: discord.Interaction):
        if await admin_only(i):
            async with svc.lock:
                svc.reload()
            await i.response.send_message(f"Reloaded. Phase {svc.game.state.phase}, day {svc.game.state.day}.",
                                          ephemeral=True)
            await bot.router.refresh_board()

    @admin.command(description="Wipe the game (keeps the old history log)")
    async def reset(i: discord.Interaction, confirm: bool = False):
        if not await admin_only(i):
            return
        if not confirm:
            await i.response.send_message("This deletes the current game, all consoles, and game roles. "
                                          "Run again with `confirm: True`.", ephemeral=True)
            return
        await i.response.defer(ephemeral=True, thinking=True)
        async with svc.lock:
            archived = svc.reset()
        for pid, cid in list(svc.server["consoles"].items()):
            ch = bot.get_channel(cid)
            if ch:
                await ch.delete(reason="Statecraft reset")
            svc.set_console(pid, None)
        for key in ("players", "updates", "jury"):
            role = i.guild.get_role(svc.role_id(key) or 0)
            if role:
                for m in list(role.members):
                    await m.remove_roles(role, reason="Statecraft reset")
        svc.server.pop("board_message_id", None)
        svc.save_server()
        await i.followup.send("Game reset." + (f" Old history kept as {archived}." if archived else ""), ephemeral=True)

    tree.add_command(admin)

    # ---- test mode
    if not bot.cfg.test_mode:
        return
    test = app_commands.Group(name="test", description="Test-mode tools (disabled for real games)",
                              default_permissions=discord.Permissions(manage_guild=True))

    @test.command(description="Register fake players (no Discord accounts)")
    async def addplayers(i: discord.Interaction, count: app_commands.Range[int, 1, 20]):
        if not await admin_only(i):
            return
        made = []
        for _ in range(count):
            n = svc.game.state.next_player_num
            name = f"Test State {n}"
            res = await svc.run(lambda g, now, n=n, name=name: g.register(f"Test Bot {n}", name, pid=f"fake{n}"))
            if res.ok:
                made.append(name)
        await i.response.send_message(f"Added {len(made)} fake players. Their console messages go to "
                                      f"#statecraft-admin.", ephemeral=True)

    @test.command(name="as", description="Run a console command as any state")
    @app_commands.autocomplete(state=any_states)
    @app_commands.describe(command="e.g. attack VE · move ne · gift WE 2 · say hello · vote VE")
    async def as_(i: discord.Interaction, state: str, command: str):
        if not await admin_only(i):
            return
        tid, err = resolve(state)
        if not tid:
            await i.response.send_message(f"Rejected: {err}", ephemeral=True)
            return
        res = await svc.run(lambda g, now: run_text(g, tid, command, now))
        who = svc.game.get(tid).state_name
        await i.response.send_message(f"[{who}] " + (res.message if res.ok else f"Rejected: {res.message}"),
                                      ephemeral=True, allowed_mentions=NO_PINGS)
        await bot.router.deliver(res.events)

    @test.command(name="start", description="Start with fewer than 12 players")
    async def test_start(i: discord.Interaction):
        if await admin_only(i):
            await reply(i, await svc.run(lambda g, now: g.start(force=True, now=now)), ephemeral=True)

    @test.command(description="Shift the bot's clock: +2h, +30m, +1d, or 0 to reset")
    async def clock(i: discord.Interaction, offset: str):
        if not await admin_only(i):
            return
        secs = parse_duration(offset)
        if secs is None:
            await i.response.send_message("Use a value like +2h, +30m, +1d, or 0.", ephemeral=True)
            return
        total = 0 if secs == 0 else int(svc.clock_offset().total_seconds()) + secs
        svc.set_clock_offset(total)
        await i.response.send_message(f"Clock offset {timedelta(seconds=total)}. Game time is now "
                                      f"**{schedule.fmt(svc.now(), svc.game.s)}**. Scheduled events will run "
                                      f"within 30 seconds.", ephemeral=True)

    @test.command(description="Run the 2:00 PM daily tick now")
    async def tick(i: discord.Interaction):
        if await admin_only(i):
            await reply(i, await svc.run(lambda g, now: g.next_day(now=now)), ephemeral=True)

    tree.add_command(test)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    StatecraftBot(cfg).run(cfg.token, log_handler=None)


if __name__ == "__main__":
    main()
