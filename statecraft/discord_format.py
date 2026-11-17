"""Text, embeds, and parsing shared by the Discord bot. No network calls."""

from __future__ import annotations

import re
import shlex
from datetime import datetime

import discord

from . import geometry, render_text, schedule
from .game import Game
from .models import Result

NO_PINGS = discord.AllowedMentions.none()

HELP = """**Statecraft**: use the **control panel** pinned at the top of your console. Every slash command below also works as a backup.
**Before the game**: `/name` sets or changes your state's name · `/leave` withdraws
**Actions** (2:00-8:00 PM Central, 15-minute cooldown after each, always confirmed first):
Move 1 AP · Attack 1 AP · Upgrade 2 AP (range +1) · Gift (give AP to a state in range) · Buy info 1 AP (today's gifts until 8:15 PM)
**Info** (free, any time): `/status` (you, or any state) · `/board` · `/info` · `/help` · `/panel` (re-post your panel)
**Talk** (free, any time, works after elimination too): `/say` (#public-chat, under your state name) · `/dm` (another state's console) · `/anon` (#anonymous-chat, no name)
**Jury** (after elimination): `/vote` 8:00 PM-1:45 PM, secret, changeable until the lock
**Pings**: `/notify on|off` for minor events (moves, upgrades, info purchases)

Range = horizontal + vertical steps (a diagonal step counts as 2). Everything you send the bot is logged for the post-game summary."""

ARROWS = {"nw": "↖", "n": "↑", "ne": "↗", "w": "←", "e": "→", "sw": "↙", "s": "↓", "se": "↘"}
DIRECTION_NAMES = {"n": "north", "ne": "north-east", "e": "east", "se": "south-east",
                   "s": "south", "sw": "south-west", "w": "west", "nw": "north-west"}


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
    return discord.Embed(title=f"Statecraft: Day {game.state.day}",
                         description=f"```\n{text[:3900]}\n```", colour=discord.Colour.dark_teal())


def panel_embed(game: Game, pid: str, now: datetime) -> discord.Embed:
    """The status card at the top of a player's control panel."""
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
        lines = [f"**AP {p.ap}** · HP {p.hp} · Range {game.range_of(p)} · Position {game.pos(p)}"]
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
            lines.append(f"Info access: active until {schedule.fmt(schedule.info_expiry(now, s), s)}")
        reach = [q for q in game.alive_players() if q.id != pid and game.in_range(p, q)]
        threats = [q for q in game.alive_players() if q.id != pid and game.in_range(q, p)]
        lines.append("In your range: " + (", ".join(f"{q.marker} ({q.hp} HP)" for q in reach) or "nobody"))
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
        if verb == "pact" and args:
            return _pact_text(game, pid, args[0].lower(), args[1:])
    except TypeError:
        pass
    return Result.fail("Unknown command. Try: move ne | attack VE | upgrade | gift VE 2 | buyinfo | "
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
