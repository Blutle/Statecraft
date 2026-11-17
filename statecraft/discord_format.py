"""Text, embeds, and parsing shared by the Discord bot. No network calls."""

from __future__ import annotations

import asyncio
import copy
import io
import re
import shlex
from datetime import datetime

import discord

from . import geometry, render_image, render_text, schedule
from .game import Game
from .models import Result

NO_PINGS = discord.AllowedMentions.none()

HELP = """**Statecraft**: use the **control panel** at the bottom of your console. Conversations with other states open as private threads (✉️ Message a state). Every slash command below also works as a backup.
**Before the game**: `/name` sets or changes your state's name · `/leave` withdraws
**Actions** (2:00-8:00 PM Central, 15-minute cooldown after each, always confirmed first):
Move 1 AP · Attack 1 AP · Upgrade Influence 2 AP (+1) · Gift (give AP to a State within your Influence) · Spy 1 AP (today's Gift log until 8:15 PM)
**Info** (free, any time): `/status` (you, or any State) · `/board` · `/spylog` · `/help` · `/panel` (re-post your panel)
**Talk** (free, any time, works after elimination too): `/say` (#public-chat, under your state name) · `/dm` (another state's console) · `/anon` (#anonymous-chat, no name)
**Jury** (after elimination): `/vote` 8:00 PM-1:45 PM, secret, changeable until the lock
**Pings**: `/notify on|off` for minor events (moves, upgrades, info purchases)

Influence: a straight step counts 1, a diagonal step counts 2. Everything you send the bot is logged for the post-game summary."""

ARROWS = {"nw": "↖", "n": "↑", "ne": "↗", "w": "←", "e": "→", "sw": "↙", "s": "↓", "se": "↘"}
DIRECTION_NAMES = {"n": "north", "ne": "north-east", "e": "east", "se": "south-east",
                   "s": "south", "sw": "south-west", "w": "west", "nw": "north-west"}


def public_status(game: Game, viewer_pid: str | None, target_pid: str) -> str:
    t = game.get(target_pid)
    v = game.get(viewer_pid) if viewer_pid else None
    lines = [f"**{t.state_name}** [{t.marker}]"]
    if t.alive:
        lines += [f"HP {t.hp} · Influence {game.range_of(t)} · Position {game.pos(t)}"]
        if v and v.alive and v.id != t.id:
            d = geometry.distance(v.x, v.y, t.x, t.y)
            lines.append(f"Distance {d}: " + ("**you can reach it**" if game.in_range(v, t) else "outside your Influence")
                         + " · " + ("**it can reach you**" if game.in_range(t, v) else "you're outside its Influence"))
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


async def board_message(game: Game, focus: str | None = None, viewer: str | None = None,
                        public: bool = False) -> tuple[discord.Embed, discord.File]:
    """The board image as an embed + attachment.

    Rendered from a snapshot in a worker thread, so drawing never blocks
    the bot and never sees a half-applied action."""
    snap = Game(copy.deepcopy(game.state))
    png = await asyncio.to_thread(render_image.render, snap, focus, viewer)
    emb = discord.Embed(colour=discord.Colour.dark_teal())
    emb.set_image(url="attachment://board.png")
    if public:
        emb.description = "Pick a State in **Show Influence for…** to see its reach (only you will see the result)."
    return emb, discord.File(io.BytesIO(png), filename="board.png")


def board_embed(game: Game, viewer_pid: str | None = None) -> discord.Embed:
    """Text board (kept for reference; Discord uses board_message)."""
    viewer = game.get(viewer_pid) if viewer_pid else None
    text = render_text.board(game, viewer)
    return discord.Embed(title=f"Statecraft: Day {game.state.day}",
                         description=f"```\n{text[:3900]}\n```", colour=discord.Colour.dark_teal())


def panel_embed(game: Game, pid: str, now: datetime, recent: list[dict] | None = None) -> discord.Embed:
    """The status card on a player's control panel, plus their recent activity."""
    emb = _panel_card(game, pid, now)
    if recent:
        s = game.s
        lines = []
        for item in reversed(recent):                       # newest first
            when = schedule.fmt(datetime.fromisoformat(item["time"]), s)
            lines.append(f"`{when}` {item['text']}")
        text = ""
        for line in lines:                                  # embed fields are capped at 1024 characters
            if len(text) + len(line) + 1 > 1024:
                break
            text += line + "\n"
        emb.add_field(name="Recent activity", value=text.strip() or "-", inline=False)
    return emb


def _panel_card(game: Game, pid: str, now: datetime) -> discord.Embed:
    p = game.get(pid)
    s = game.s
    phase = game.state.phase
    if p is None:
        return discord.Embed(title="Your Statecraft console",
                             description="**Name your state** to enter the game: tap **Name my state** below.",
                             colour=discord.Colour.light_grey())
    emb = discord.Embed(title=f"{p.state_name} [{p.marker}]", colour=discord.Colour.dark_teal())
    if phase == "registration":
        emb.description = ("You're registered. The game hasn't started yet, so you can still rename your state.\n"
                           "Your first AP arrives at the first 2:00 PM after the game starts.")
        return emb
    if phase == "ended":
        won = pid in game.winners()
        emb.description = "**The game is over.** " + ("You won!" if won else "Thanks for playing.")
        emb.colour = discord.Colour.gold() if won else discord.Colour.light_grey()
        return emb
    if p.alive:
        lines = [f"**AP {p.ap}** · HP {p.hp} · Influence {game.range_of(p)} · Position {game.pos(p)}"]
        left = game.cooldown_remaining(p, now)
        if left:
            lines.append(f"Cooldown: ready at **{schedule.fmt(now + left, s)}**")
        else:
            lines.append("Cooldown: **ready**")
        if schedule.window_open(now, s):
            lines.append(f"Actions: **open** until {_clock(s['window_close'])}")
        else:
            lines.append(f"Actions: closed, open {schedule.fmt(schedule.next_at(now, s, 'window_open'), s)}")
        if pid in game.state.info_access:
            lines.append(f"Spying until {schedule.fmt(schedule.info_expiry(now, s), s)}")
        reach = [q for q in game.alive_players() if q.id != pid and game.in_range(p, q)]
        threats = [q for q in game.alive_players() if q.id != pid and game.in_range(q, p)]
        lines.append("Within your Influence: " + (", ".join(f"{q.marker} ({q.hp} HP)" for q in reach) or "nobody"))
        lines.append("Can reach you: " + (", ".join(q.marker for q in threats) or "nobody"))
    else:
        emb.colour = discord.Colour.purple()
        vote = game.state.jury_votes.get(pid)
        lines = [f"**Destroyed** on day {p.eliminated_on_day}. You're on the **Jury**.",
                 f"Your vote tonight: **{game.get(vote).state_name if vote else 'none yet'}**",
                 "Voting: " + (f"**open** until {_clock(s['vote_lock'])}" if schedule.voting_open(now, s)
                               else f"opens at {_clock(s['window_close'])}")]
    emb.description = "\n".join(lines)
    emb.set_footer(text=f"Day {game.state.day} · updated {schedule.fmt(now, s)}")
    return emb


CARD_COLOURS = {
    "ATTACK": 0xED4245, "ELIMINATION": 0x2B2D31, "MOVE": 0x4F545C, "UPGRADE": 0x5865F2,
    "INFO_PURCHASE": 0x9B59B6, "DAILY_AP": 0xFAA61A, "JURY_RESULT": 0x9B59B6, "GAME_START": 0x1ABC9C,
    "GAME_OVER": 0xF1C40F, "ADMIN_EDIT": 0x95A5A6, "REVIVED": 0x57F287,
}


def action_card(ev, game: Game, now: datetime | None = None) -> discord.Embed:
    """A short, readable #action-log card for a public game event."""
    d, s = ev.data, game.s

    def tag(pid: str | None) -> str:
        p = game.get(pid) if pid else None
        return f"**{p.state_name}** `{p.marker}`" if p else "**?**"

    k = ev.kind
    emb = discord.Embed(colour=CARD_COLOURS.get(k, 0x4F545C))
    if k == "ATTACK":
        hearts = "❤️" * d["hp"] + "🖤" * (s["starting_hp"] - d["hp"])
        emb.description = f"⚔️ {tag(d['attacker'])} attacked {tag(d['target'])}\n{hearts} {d['hp']} HP left"
    elif k == "ELIMINATION":
        v = game.get(d["victim"])
        by = f" by {tag(d['attacker'])}" if d.get("attacker") else " by the referee"
        emb.description = (f"💀 {tag(d['victim'])} was **destroyed**{by}\n"
                           f"It was controlled by **{v.owner_name}**, who joins the Jury.")
    elif k == "MOVE":
        emb.description = f"🧭 {tag(d['pid'])} moved {d['from']} → **{d['to']}**"
    elif k == "UPGRADE":
        emb.description = f"📡 {tag(d['pid'])} extended its Influence to **{d['range']}**"
    elif k == "INFO_PURCHASE":
        emb.description = f"🕵️ {tag(d['pid'])} is spying"
    elif k == "DAILY_AP":
        emb.description = (f"🌅 **Day {d['day']} begins** · {d['alive']} states remain · "
                           f"each gains {d['amount']} AP · actions open until {_clock(s['window_close'])}")
    elif k == "JURY_RESULT":
        lines = [f"{tag(a['pid'])}: {a['votes']} vote{'s' if a['votes'] != 1 else ''}"
                 + (f" → **+{a['bonus']} AP**" if a["bonus"] else "") for a in d.get("awards", [])]
        emb.description = "🗳️ **Jury results**\n" + ("\n".join(lines) or "No valid votes.")
    elif k == "GAME_START":
        emb.title = "⚔️ Statecraft begins"
        first = (schedule.fmt(schedule.next_at(now, s, "window_open"), s) if now else "the next 2:00 PM")
        emb.description = (f"**{len(d['states'])} states** on a **{d['size']}×{d['size']}** board. "
                           f"Everyone starts with 0 AP; the first AP arrives **{first}**.\n"
                           f"The map is in the board channel.")
        states = sorted((game.get(pid) for pid in d["states"]), key=lambda p: p.marker)
        half = -(-len(states) // 2)
        for col in (states[:half], states[half:]):
            if col:
                emb.add_field(name="​", value="\n".join(f"`{p.marker}` {p.state_name}" for p in col),
                              inline=True)
    elif k == "GAME_OVER":
        win = [game.get(w) for w in d.get("winners", [])]
        if len(win) == 1 and not d.get("declared"):
            emb.description = (f"🏆 {tag(win[0].id)} is the last state standing and **wins Statecraft!**\n"
                               f"It was controlled by **{win[0].owner_name}**.")
        else:
            names = "\n".join(f"{tag(w.id)}, controlled by **{w.owner_name}**" for w in win)
            emb.description = f"🏆 **The game is over.** Declared {'winner' if len(win) == 1 else 'joint winners'}:\n{names}"
    elif k == "ADMIN_EDIT":
        emb.description = f"🛠️ Referee adjustment: {ev.public.split(': ', 1)[-1]}"
    elif k == "REVIVED":
        emb.description = f"✨ {tag(d['pid'])} has been restored by the referee"
    else:
        emb.description = ev.public
    return emb


def history_line(rec: dict, game: Game) -> str:
    """One history record as a short line for the referee (/admin log)."""
    s = game.s
    when = schedule.fmt(datetime.fromisoformat(rec["time"]), s) if rec.get("time") else "?"
    who = rec["actor"]["state"] if rec.get("actor") else "system"
    target = f" → {rec['target']['state']}" if rec.get("target") else ""
    args = rec.get("args", {})
    detail = ""
    if "text" in args:
        detail = f': "{args["text"][:150]}"'
    elif "direction" in args:
        detail = f" {args['direction']}"
    elif "amount" in args:
        detail = f" {args['amount']} AP"
    elif "state_name" in args:
        detail = f" {args['state_name']}" + (f" [{args['abbreviation']}]" if args.get("abbreviation") else "")
    elif "name" in args:
        detail = f" {args['name']}"
    if "pact_id" in args:
        pc = game.state.pacts.get(args["pact_id"])
        detail = f" in {pc['name'] if pc else args['pact_id']}" + detail
    status = "" if rec.get("ok") else f" ✗ *{rec.get('result', '')[:80]}*"
    return f"`{when}` **{who}** {rec['action']}{target}{detail}{status}"


def _clock(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


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
        if verb in ("spy", "buyinfo"):
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
        if verb == "pact" and args:
            return _pact_text(game, pid, args[0].lower(), args[1:])
    except TypeError:
        pass
    return Result.fail("Unknown command. Try: move ne | attack VE | upgrade | gift VE 2 | spy | "
                       "vote VE | say hi | dm VE hello | anon hi | pact create \"Name\" | "
                       "pact invite \"Name\" VE | pact accept \"Name\" | pact decline \"Name\" | "
                       "pact say \"Name\" hi | pact leave \"Name\" | pact kick \"Name\" VE")


def _pact_text(game: Game, pid: str, sub: str, args: list[str]) -> Result:
    """`pact ...` for /test as. Pacts are named by their (quoted) name."""
    def find_pact(name: str):
        for pc in game.state.pacts.values():
            if pc["active"] and pc["name"].lower() == name.lower():
                return pc["id"]
        return None

    if sub == "create" and args:
        return game.create_pact(pid, " ".join(args))
    if not args:
        return Result.fail("Name the pact, in quotes if it has spaces.")
    pact_id = find_pact(args[0])
    if not pact_id:
        return Result.fail(f"No active pact named '{args[0]}'.")
    if sub in ("accept", "decline"):
        return game.respond_to_pact(pid, pact_id, sub == "accept")
    if sub == "leave":
        return game.leave_pact(pid, pact_id)
    if sub == "say" and len(args) >= 2:
        return game.pact_message(pid, pact_id, " ".join(args[1:]))
    if sub in ("invite", "kick") and len(args) == 2:
        t, err = game.find(args[1])
        if not t:
            return Result.fail(err)
        return (game.invite_to_pact if sub == "invite" else game.kick_from_pact)(pid, pact_id, t.id)
    return Result.fail("Unknown pact command.")


def parse_duration(text: str) -> int | None:
    """'+2h', '30m', '-1d', '0' -> seconds (a bare number means minutes)."""
    m = re.fullmatch(r"\s*([+-]?)(\d+)\s*([smhd]?)\s*", text)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    mult = {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(3)]
    return sign * int(m.group(2)) * mult
