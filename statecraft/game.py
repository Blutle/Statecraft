"""Statecraft rules engine.

Pure game logic: no Discord, no console I/O, no clock. Every action returns
a Result with feedback for the actor and a list of Events for the interface
(CLI now, Discord later) to deliver. Rejected actions never change state.

Every call to an @recorded method, accepted or rejected, produces one history
record, sent to `Game.recorder`. That record includes messages and anonymous
posts with their true author. The history feeds the post-game summary.
"""

from __future__ import annotations

import functools
import inspect
import random
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Callable

from . import colours, geometry, schedule
from .models import Event, GameState, Player, Result

STOPWORDS = {"the", "of", "and", "a", "an", "de", "la", "le", "du", "von", "van"}
MAX_NAME_LEN = 24          # state names: sized so the board legend never cuts one off
MAX_PACT_NAME_LEN = 40
ABBREV_LEN = 3
MAX_MESSAGE_LEN = 1500


def data_event(kind: str, **data) -> list[Event]:
    return [Event(kind, data=data)]


def recorded(action: str):
    """Decorator: log every call (args, outcome, events, and the after-state of
    the players involved) to the game's history recorder."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self: "Game", *args, **kwargs):
            day_before = self.state.day
            result = fn(self, *args, **kwargs)
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()
            params = {k: v for k, v in bound.arguments.items() if k != "self"}
            self._record(action, params, result, day_before)
            return result
        return wrapper
    return deco


class Game:
    def __init__(self, state: GameState | None = None, rng: random.Random | None = None,
                 recorder: Callable[[dict], None] | None = None):
        self.state = state or GameState()
        self.rng = rng or random.Random()
        self.recorder = recorder

    # ------------------------------------------------------------------ lookup

    @property
    def s(self) -> dict:
        return self.state.settings

    def get(self, pid: str) -> Player | None:
        return self.state.players.get(pid)

    def alive_players(self) -> list[Player]:
        return [p for p in self.state.players.values() if p.alive]

    def find(self, query: str) -> tuple[Player | None, str]:
        """Resolve a player by id, owner name, marker, state name, or unique
        state-name prefix (all case-insensitive). Returns (player, error)."""
        q = query.strip().lower()
        if not q:
            return None, "No player given."
        players = list(self.state.players.values())
        for getter in (
            lambda p: p.id,
            lambda p: p.owner_name,
            lambda p: p.marker,
            lambda p: p.state_name,
        ):
            matches = [p for p in players if getter(p).lower() == q]
            if len(matches) == 1:
                return matches[0], ""
        # Prefix match on state name, ignoring a leading "the"
        def bare(name: str) -> str:
            n = name.lower()
            return n[4:] if n.startswith("the ") else n

        matches = [p for p in players if p.state_name.lower().startswith(q) or bare(p.state_name).startswith(q)]
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            names = ", ".join(p.state_name for p in matches)
            return None, f"'{query}' is ambiguous: {names}"
        return None, f"No state or player matches '{query}'."

    def range_of(self, p: Player) -> int:
        return self.s["base_range"] + p.range_upgrades

    def occupant(self, x: int, y: int) -> Player | None:
        for p in self.state.players.values():
            if p.x == x and p.y == y:
                return p
        return None

    def in_range(self, a: Player, b: Player) -> bool:
        return geometry.in_range(a.x, a.y, b.x, b.y, self.range_of(a))

    def pos(self, p: Player) -> str:
        return geometry.coord_label(p.x, p.y) if p.x is not None else "-"

    # ------------------------------------------------------------- lifecycle

    @recorded("register")
    def register(self, owner_name: str, state_name: str, pid: str | None = None,
                 abbreviation: str | None = None) -> Result:
        """Register a player. `pid` is the Discord user id (the CLI omits it
        and gets p1, p2, ...). Registering again with the same pid before the
        game starts renames the state. `abbreviation` is the player's chosen
        3-letter board marker; if omitted, one is made from the state name."""
        if self.state.phase != "registration":
            return Result.fail("Registration is closed; the game has already started.")
        owner_name = owner_name.strip()
        state_name = " ".join(state_name.split())
        if not owner_name or not state_name:
            return Result.fail("Both a player name and a state name are required.")
        if len(state_name) > MAX_NAME_LEN:
            return Result.fail(f"State names are limited to {MAX_NAME_LEN} characters.")
        if any(c in state_name for c in "@#<>`*_~|\\"):
            return Result.fail("State names can't contain @ # < > ` * _ ~ | or backslashes.")
        existing = self.state.players.get(pid) if pid else None
        if abbreviation is not None and abbreviation.strip():
            abbreviation = abbreviation.strip().upper()
            if not (len(abbreviation) == ABBREV_LEN and abbreviation.isascii() and abbreviation.isalpha()):
                return Result.fail(f"Abbreviations must be exactly {ABBREV_LEN} letters (A-Z).")
            taken_by = next((p for p in self.state.players.values()
                             if p.marker == abbreviation and p is not existing), None)
            if taken_by:
                return Result.fail(f"The abbreviation {abbreviation} is already taken.")
        else:
            abbreviation = None
        for p in self.state.players.values():
            if p is existing:
                continue
            if p.state_name.lower() == state_name.lower():
                return Result.fail(f"The name '{state_name}' is already taken.")
            if pid is None and p.owner_name.lower() == owner_name.lower():
                return Result.fail(f"Player '{owner_name}' already controls {p.state_name}.")
        if existing:
            old = existing.state_name
            renamed = old != state_name
            existing.state_name = state_name
            existing.owner_name = owner_name
            if abbreviation:
                existing.marker = abbreviation
            elif renamed or not existing.marker:
                existing.marker = ""
                existing.marker = self._make_marker(state_name)
            return Result(True, f"Your state is now **{state_name}**, shown on the board as **{existing.marker}**.",
                          data_event("RENAME", pid=pid))

        auto_id = pid is None
        if auto_id:
            pid = f"p{self.state.next_player_num}"
        self.state.next_player_num += 1
        player = Player(
            id=pid,
            owner_name=owner_name,
            state_name=state_name,
            marker=abbreviation or self._make_marker(state_name),
            hp=self.s["starting_hp"],
        )
        self.state.players[pid] = player
        if auto_id:  # CLI: show the id used with `as`
            return Result(True, f"{owner_name} registered {state_name} [{player.marker}] as {pid}.")
        return Result(True, f"Your state is **{state_name}**, shown on the board as **{player.marker}**. "
                            f"You can change either with `/name` (or your panel) until the game starts.")

    @recorded("unregister")
    def unregister(self, pid: str) -> Result:
        if self.state.phase != "registration":
            return Result.fail("Players can only be removed during registration.")
        p = self.state.players.pop(pid, None)
        if not p:
            return Result.fail("No such player.")
        return Result(True, f"Removed {p.state_name}.")

    def _make_marker(self, state_name: str) -> str:
        """A unique 3-letter abbreviation from the state name, used when the
        player doesn't choose one (e.g. 'The Iron Dominion' -> IDO)."""
        taken = {p.marker for p in self.state.players.values()}
        words = [w for w in state_name.split() if w.lower() not in STOPWORDS] or state_name.split()
        words = ["".join(c for c in w if c.isascii() and c.isalpha()) for w in words]
        words = [w for w in words if w] or ["X"]
        letters = "".join(words)
        candidates = []
        if len(words) >= 3:
            candidates.append(words[0][0] + words[1][0] + words[2][0])
        if len(words) >= 2:
            candidates += [words[0][0] + words[1][:2], words[0][:2] + words[1][0]]
        candidates.append(letters[:3])
        first = letters[0]
        candidates += [first + a + b for a in letters[1:] for b in letters[2:]]
        candidates += [first + a + b for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        for c in candidates:
            c = c.upper()
            if len(c) == ABBREV_LEN and c not in taken:
                return c
        raise RuntimeError("Ran out of abbreviations")

    def auto_board_size(self, n: int) -> int:
        """board_scale_pct of the player count, rounded down (75% -> 16 players
        on 12x12), but never fewer than 2 squares per state (matters for tiny games)."""
        size = n * self.s["board_scale_pct"] // 100
        while size * size < n * 2:
            size += 1
        return size

    def _spawn_squares(self, size: int, n: int) -> list[tuple[int, int]]:
        """Random starting squares. With spawn_no_cardinal_touch on, no two
        states start directly N/S/E/W of each other (diagonal contact is fine).

        Always solvable: a board with at least 2 squares per state has a
        checkerboard color with room for everyone, used as a fallback."""
        squares = [(x, y) for x in range(size) for y in range(size)]
        if not self.s["spawn_no_cardinal_touch"]:
            return self.rng.sample(squares, n)
        for _ in range(200):
            available = set(squares)
            chosen = []
            while len(chosen) < n and available:
                x, y = self.rng.choice(sorted(available))
                chosen.append((x, y))
                available -= {(x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)}
            if len(chosen) == n:
                return chosen
        colors = [[(x, y) for x, y in squares if (x + y) % 2 == c] for c in (0, 1)]
        return self.rng.sample(max(colors, key=len), n)

    def _assign_colors(self) -> None:
        """Anyone who didn't pick a color gets the first free one."""
        taken = {p.color for p in self.state.players.values() if p.color}
        free = [c for c in colours.PALETTE if c not in taken]
        for p in self.state.players.values():
            if not p.color and free:
                p.color = free.pop(0)

    def free_colors(self, for_pid: str | None = None) -> list[str]:
        """Palette colors nobody else holds (in palette order)."""
        taken = {p.color for p in self.state.players.values() if p.color and p.id != for_pid}
        return [c for c in colours.PALETTE if c not in taken]

    @recorded("color")
    def set_color(self, pid: str, color: str) -> Result:
        """Pick a State color before the game starts: first come, first served."""
        p = self.get(pid)
        if not p:
            return Result.fail("Name your State first.")
        if self.state.phase != "registration":
            return Result.fail("Colors are locked once the game has started.")
        name = colours.canonical(color)
        if not name:
            return Result.fail("That isn't one of the colors. Choose from: " + ", ".join(colours.PALETTE))
        holder = next((q for q in self.state.players.values() if q.color == name and q is not p), None)
        if holder:
            return Result.fail(f"{name} is already taken. Pick another color.")
        if p.color == name:
            return Result.fail(f"Your color is already {name}.")
        p.color = name
        return Result(True, f"Your State's color is now **{name}**.", data_event("COLOR", pid=pid))

    @recorded("start")
    def start(self, force: bool = False, *, now: datetime | None = None) -> Result:
        """Begin the game. Needs min_players (12); force allows down to 2 for testing."""
        if self.state.phase != "registration":
            return Result.fail("The game has already started.")
        players = list(self.state.players.values())
        minimum = 2 if force else max(2, self.s["min_players"])
        if len(players) < minimum:
            return Result.fail(f"At least {minimum} players are needed to start ({len(players)} registered)."
                               + ("" if force else " Use 'start force' to test with fewer."))
        n = len(players)
        size = self.s.get("board_size") or self.auto_board_size(n)
        if size * size < n * 2:
            return Result.fail(f"A {size}x{size} board is too small for {n} states.")
        self.state.board_size = size
        for p, (x, y) in zip(players, self._spawn_squares(size, n)):
            p.x, p.y = x, y
            p.hp = self.s["starting_hp"]
            p.ap = 0
        self._assign_colors()
        self.state.phase = "active"
        self.state.day = 0
        if now is not None:
            self.state.clock_marker = now.isoformat()
        return self._done(Result(True, "Game started.", [Event(
            "GAME_START",
            public=f"Statecraft begins: {n} states on a {size}x{size} board. Everyone starts with 0 AP; "
                   f"the first AP arrives at the start of Day 1.",
            ping="mandatory",
            data={"states": [p.id for p in players], "size": size},
        )]))

    # ---------------------------------------------------------------- actions

    def _actor(self, pid: str, cost: int, now: datetime | None = None) -> tuple[Player | None, str]:
        """Shared checks for gameplay actions. With `now` (the Discord bot),
        also enforces the action window and the per-player cooldown; without
        it (CLI, tests) there is no clock."""
        if self.state.phase != "active":
            return None, "The game is not in progress."
        p = self.get(pid)
        if not p:
            return None, "You are not a player in this game."
        if not p.alive:
            return None, "Your state has been eliminated. You can only vote with the jury."
        if now is not None:
            if not schedule.window_open(now, self.s):
                opens = schedule.next_at(now, self.s, "window_open")
                return None, (f"Actions are only allowed {self.s['window_open']}-{self.s['window_close']} "
                              f"({self.s['timezone']}). The next window opens {schedule.fmt(opens, self.s)}.")
            left = self.cooldown_remaining(p, now)
            if left > timedelta(0):
                return None, f"You're on cooldown for another {schedule.fmt_duration(left)}."
        if p.ap < cost:
            return None, f"Not enough AP: this costs {cost}, you have {p.ap}."
        return p, ""

    def _stamp(self, p: Player, now: datetime | None) -> None:
        """Start the action cooldown (successful actions only)."""
        if now is not None:
            p.last_action = now.isoformat()

    def cooldown_remaining(self, p: Player, now: datetime) -> timedelta:
        if not p.last_action:
            return timedelta(0)
        ready = datetime.fromisoformat(p.last_action) + timedelta(minutes=self.s["cooldown_minutes"])
        return max(timedelta(0), ready - now)

    def _target(self, actor: Player, tid: str, verb: str) -> tuple[Player | None, str]:
        t = self.get(tid)
        if not t:
            return None, "No such state."
        if t.id == actor.id:
            return None, f"You can't {verb} yourself."
        if not t.alive:
            return None, f"{t.state_name} has been eliminated."
        if not self.in_range(actor, t):
            return None, (f"{t.state_name} at {self.pos(t)} is outside your Influence "
                          f"(distance {geometry.distance(actor.x, actor.y, t.x, t.y)}, your Influence {self.range_of(actor)}).")
        return t, ""

    @recorded("move")
    def move(self, pid: str, direction: str, *, now: datetime | None = None) -> Result:
        cost = self.s["move_cost"]
        p, err = self._actor(pid, cost, now)
        if err:
            return Result.fail(err)
        d = direction.lower()
        if d not in geometry.DIRECTIONS:
            return Result.fail(f"Unknown direction '{direction}'. Use one of: {', '.join(geometry.DIRECTIONS)}.")
        dx, dy = geometry.DIRECTIONS[d]
        nx, ny = p.x + dx, p.y + dy
        if not geometry.on_board(nx, ny, self.state.board_size):
            return Result.fail("You can't move off the board.")
        blocker = self.occupant(nx, ny)
        if blocker:
            return Result.fail(f"{geometry.coord_label(nx, ny)} is occupied by {blocker.state_name}.")
        old = self.pos(p)
        p.x, p.y = nx, ny
        p.ap -= cost
        self._stamp(p, now)
        return self._done(Result(True, f"Moved to {self.pos(p)}. AP left: {p.ap}.", [Event(
            "MOVE", data={"pid": p.id, "from": old, "to": self.pos(p)}, public=f"{p.state_name} moved from {old} to {self.pos(p)}.", ping="opt_in")]))

    @recorded("attack")
    def attack(self, pid: str, tid: str, *, now: datetime | None = None) -> Result:
        cost = self.s["attack_cost"]
        p, err = self._actor(pid, cost, now)
        if err:
            return Result.fail(err)
        t, err = self._target(p, tid, "attack")
        if err:
            return Result.fail(err)
        p.ap -= cost
        self._stamp(p, now)
        t.hp -= 1
        events: list[Event] = []
        if t.hp > 0:
            events.append(Event(
                "ATTACK",
                public=f"{p.state_name} attacked {t.state_name}! {t.state_name} has {t.hp} HP left.",
                ping="mandatory",
                data={"attacker": p.id, "target": t.id, "hp": t.hp},
            ))
        else:
            t.hp = 0
            t.alive = False
            t.eliminated_on_day = self.state.day
            if t.id in self.state.info_access:      # death ends purchased info access
                self.state.info_access.remove(t.id)
            events.append(Event(
                "ELIMINATION",
                data={"victim": t.id, "attacker": p.id},
                public=(f"{p.state_name} attacked {t.state_name}, destroying it! "
                        f"{t.state_name} was controlled by {t.owner_name}, who now joins the Jury."),
                private={t.id: "Your state has been destroyed. You are now on the Jury and can vote each night."},
                ping="mandatory",
            ))
            events += self._check_victory()
        return self._done(Result(True, f"Hit {t.state_name}. AP left: {p.ap}.", events))

    def _check_victory(self) -> list[Event]:
        alive = self.alive_players()
        if len(alive) != 1:
            return []
        w = alive[0]
        self._archive_votes(tallied=False)          # ballots cast but never tallied
        self.state.phase = "ended"
        self.state.winners = [w.id]
        return [Event(
            "GAME_OVER",
            public=f"{w.state_name} is the last state standing and wins Statecraft! "
                   f"It was controlled by {w.owner_name}.",
            ping="mandatory",
            data={"winners": [w.id], "declared": False},
        )]

    def winners(self) -> list[str]:
        """Ids of the winning states: the last survivor, or whoever the admin declared."""
        return list(self.state.winners)

    @recorded("upgrade")
    def upgrade(self, pid: str, *, now: datetime | None = None) -> Result:
        cost = self.s["upgrade_cost"]
        p, err = self._actor(pid, cost, now)
        if err:
            return Result.fail(err)
        p.ap -= cost
        self._stamp(p, now)
        p.range_upgrades += 1
        r = self.range_of(p)
        return self._done(Result(True, f"Your Influence is now {r}. AP left: {p.ap}.", [Event(
            "UPGRADE", data={"pid": p.id, "range": r}, public=f"{p.state_name} increased its Influence to {r}.", ping="opt_in")]))

    @recorded("gift")
    def gift(self, pid: str, tid: str, *, now: datetime | None = None) -> Result:
        """Give exactly gift_amount (1) AP to a State within your Influence.
        A normal action: the giver pays the AP, and the cooldown applies."""
        amount = self.s["gift_amount"]
        p, err = self._actor(pid, amount, now)
        if err:
            return Result.fail(err)
        t, err = self._target(p, tid, "gift to")
        if err:
            return Result.fail(err)
        p.ap -= amount
        self._stamp(p, now)
        t.ap += amount
        self.state.gifts_today.append({"from": p.id, "to": t.id, "amount": amount})
        line = f"{p.state_name} gifted {t.state_name} {amount} AP"
        private = {t.id: f"{p.state_name} gifted you {amount} AP. You now have {t.ap} AP."}
        for buyer in self.state.info_access:
            if buyer not in (p.id, t.id):
                private[buyer] = f"[Info] {line}"
        return self._done(Result(True, f"Gifted {amount} AP to {t.state_name}. AP left: {p.ap}.",
                                 [Event("GIFT", private=private)]))

    @recorded("buy_info")
    def buy_info(self, pid: str, *, now: datetime | None = None) -> Result:
        cost = self.s["info_cost"]
        p, err = self._actor(pid, cost, now)
        if err:
            return Result.fail(err)
        if pid in self.state.info_access:
            return Result.fail("You're already spying today.")
        p.ap -= cost
        self._stamp(p, now)
        self.state.info_access.append(pid)
        return self._done(Result(
            True,
            f"You're spying until {self._info_until(now)}. AP left: {p.ap}.\n"
            + self.gift_report(),
            [Event("INFO_PURCHASE", public=f"{p.state_name} is spying.", ping="opt_in",
                   data={"pid": p.id})],
        ))

    def _info_until(self, now: datetime | None) -> str:
        if now is None:
            return f"the end of Day {self.state.day}"
        return schedule.fmt(schedule.info_expiry(now, self.s), self.s)

    def gift_report(self) -> str:
        if not self.state.gifts_today:
            return f"Spy log: no Gifts so far on Day {self.state.day}."
        lines = [f"Spy log: Gifts on Day {self.state.day}"]
        for gf in self.state.gifts_today:
            lines.append(f"  {self.get(gf['from']).state_name} gifted {self.get(gf['to']).state_name} {gf['amount']} AP")
        return "\n".join(lines)

    @recorded("vote")
    def vote(self, pid: str, tid: str, *, now: datetime | None = None) -> Result:
        if self.state.phase != "active":
            return Result.fail("The game is not in progress.")
        p = self.get(pid)
        if not p:
            return Result.fail("You are not a player in this game.")
        if p.alive:
            return Result.fail("Only eliminated players (the Jury) can vote.")
        if now is not None and not schedule.voting_open(now, self.s):
            return Result.fail(f"Jury voting is open {self.s['window_close']} until {self.s['vote_lock']} "
                               f"the next day ({self.s['timezone']}). Votes are locked right now.")
        t = self.get(tid)
        if not t or not t.alive:
            return Result.fail("You can only vote for a surviving state.")
        changed = pid in self.state.jury_votes
        self.state.jury_votes[pid] = tid
        verb = "changed to" if changed else "cast for"
        return self._done(Result(True, f"Your vote is {verb} {t.state_name}. You can change it until the day ends."))

    # ------------------------------------------------------------- day cycle

    @recorded("next_day")
    def next_day(self, *, now: datetime | None = None) -> Result:
        """Close the current day and open the next one.

        In the Discord version this is split between 8 PM (info access expires)
        and 2 PM (jury tally, daily AP). The CLI runs both at once.
        """
        if self.state.phase != "active":
            return Result.fail("The game is not in progress.")
        events: list[Event] = []

        # Jury tally: votes for states that died since voting are discarded.
        if self.state.jury_votes:
            counts: dict[str, int] = {}
            for tid in self.state.jury_votes.values():
                t = self.get(tid)
                if t and t.alive:
                    counts[tid] = counts.get(tid, 0) + 1
            parts, awards = [], []
            for tid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                t = self.get(tid)
                bonus = n // self.s["votes_per_ap"]
                t.ap += bonus
                parts.append(f"{t.state_name}: {n} vote{'s' if n != 1 else ''} (+{bonus} AP)")
                awards.append({"pid": tid, "votes": n, "bonus": bonus})
            events.append(Event("JURY_RESULT", public="Jury results: " + ("; ".join(parts) or "no valid votes") + ".",
                                data={"awards": awards}))
            self._archive_votes(tallied=True)

        self.state.info_access = []
        self.state.gifts_today = []
        self.state.day += 1
        for p in self.alive_players():
            p.ap += self.s["daily_ap"]
        events.append(Event(
            "DAILY_AP",
            public=f"Day {self.state.day} begins. Each surviving state receives {self.s['daily_ap']} AP.",
            data={"day": self.state.day, "alive": len(self.alive_players()), "amount": self.s["daily_ap"]},
        ))
        return self._done(Result(True, f"Advanced to Day {self.state.day}.", events))

    def _archive_votes(self, tallied: bool) -> None:
        """Move current ballots into the private (admin-only) vote history."""
        for juror, target in self.state.jury_votes.items():
            self.state.jury_history.append(
                {"day": self.state.day, "juror": juror, "target": target, "tallied": tallied})
        self.state.jury_votes = {}

    def jury_scores(self) -> list[dict]:
        """Admin-only juror ranking for prizes: how often each juror backed the
        eventual winner. A juror eliminated on day d can vote on nights
        d .. final_day-1 (each tallied the next afternoon); only tallied
        ballots count."""
        if self.state.phase != "ended":
            return []
        final_day = self.state.day
        rows = []
        for p in self.state.players.values():
            if p.alive:
                continue
            ballots = [b for b in self.state.jury_history if b["juror"] == p.id and b["tallied"]]
            winners = set(self.winners())
            for_winner = sum(1 for b in ballots if b["target"] in winners)
            nights = final_day - p.eliminated_on_day
            rows.append({
                "player": p, "for_winner": for_winner, "ballots": len(ballots), "nights": nights,
                "rate": for_winner / nights if nights else 0.0,
            })
        rows.sort(key=lambda r: (-r["for_winner"], -r["rate"]))
        return rows

    # ------------------------------------------------------------------ admin

    # ------------------------------------------------------------- scheduler

    def run_due_events(self, now: datetime) -> Result:
        """Run every scheduled event due since the last run, oldest first.

        Called periodically by the bot. Each event runs exactly once, so
        restarts never repeat a tick, and missed ticks (the bot was offline)
        catch up in order."""
        if self.state.phase != "active":
            return Result(True)
        if not self.state.clock_marker:
            self.state.clock_marker = now.isoformat()
            return Result(True)
        events: list[Event] = []
        ran = []
        for when, kind in schedule.due_events(datetime.fromisoformat(self.state.clock_marker), now, self.s):
            if self.state.phase != "active":
                break
            res = self.next_day(now=when) if kind == "daily_tick" else self.expire_info(now=when)
            events += res.events
            ran.append(f"{kind} @ {schedule.fmt(when, self.s)}")
            self.state.clock_marker = when.isoformat()
        if self.state.phase == "active":
            self.state.clock_marker = now.isoformat()
        return Result(True, ", ".join(ran), events)

    @recorded("info_expire")
    def expire_info(self, *, now: datetime | None = None) -> Result:
        """8:15 PM: purchased information access fades."""
        had = list(self.state.info_access)
        self.state.info_access = []
        private = {pid: "Your Spy access has ended for today." for pid in had if self.get(pid).alive}
        return self._done(Result(True, f"Spy access ended for {len(had)} State(s).",
                                 [Event("INFO_EXPIRED", private=private)] if private else []))

    @recorded("admin_declare_winners")
    def declare_winners(self, pids: list[str]) -> Result:
        """Admin: end the game now with one or more surviving states as winners
        (e.g. a draw the survivors agreed to). All winners are revealed."""
        if self.state.phase != "active":
            return Result.fail("The game is not in progress.")
        if not pids or len(set(pids)) != len(pids):
            return Result.fail("Name one or more different states.")
        chosen = [self.get(i) for i in pids]
        if any(p is None or not p.alive for p in chosen):
            return Result.fail("Only surviving states can be declared winners.")
        self._archive_votes(tallied=False)
        self.state.phase = "ended"
        self.state.winners = list(pids)
        names = "; ".join(f"{p.state_name} (controlled by {p.owner_name})" for p in chosen)
        verb = "is declared the winner" if len(chosen) == 1 else "are declared joint winners"
        return self._done(Result(True, "Game ended.", [Event(
            "GAME_OVER", public=f"The game is over. {names} {verb} of Statecraft!", ping="mandatory",
            data={"winners": list(pids), "declared": True})]))

    # Referee edits: every stored attribute of a state.
    ADMIN_FIELDS = {
        "name": "name", "abbreviation": "abbreviation", "ap": "AP", "hp": "HP", "range": "Influence",
        "position": "position", "cooldown": "cooldown", "owner": "owner name", "color": "color",
    }
    PUBLIC_FIELDS = {"name", "abbreviation", "hp", "range", "position", "color"}

    def admin_value(self, p: Player, field: str) -> str:
        """Current value of an editable field, as text."""
        return {
            "name": p.state_name, "abbreviation": p.marker, "ap": str(p.ap), "hp": str(p.hp),
            "range": str(self.range_of(p)), "position": self.pos(p), "owner": p.owner_name,
            "cooldown": "active" if p.last_action else "ready", "color": p.color or "none",
        }[field]

    def _name_problem(self, name: str, exclude: Player) -> str:
        if not name:
            return "The name can't be empty."
        if len(name) > MAX_NAME_LEN:
            return f"State names are limited to {MAX_NAME_LEN} characters."
        if any(c in name for c in "@#<>`*_~|\\"):
            return "State names can't contain @ # < > ` * _ ~ | or backslashes."
        if any(q is not exclude and q.state_name.lower() == name.lower() for q in self.state.players.values()):
            return f"The name '{name}' is already taken."
        return ""

    @recorded("admin_edit")
    def admin_edit(self, pid: str, field: str, value: str, announce: bool = True) -> Result:
        """Referee: set any stored attribute of a state (validated like normal play)."""
        p = self.get(pid)
        if not p:
            return Result.fail("No such state.")
        field = field.lower().strip()
        if field not in self.ADMIN_FIELDS:
            return Result.fail("Field must be one of: " + ", ".join(self.ADMIN_FIELDS))
        value = " ".join(str(value).split())
        label = self.ADMIN_FIELDS[field]
        old = self.admin_value(p, field)
        old_name = p.state_name
        extra: list[Event] = []

        def number(lo: int, hi: int = 999) -> tuple[int | None, str]:
            try:
                n = int(value)
            except ValueError:
                return None, f"{label} must be a whole number."
            if not lo <= n <= hi:
                return None, f"{label} must be between {lo} and {hi}."
            return n, ""

        if field == "name":
            if err := self._name_problem(value, p):
                return Result.fail(err)
            p.state_name = value
        elif field == "abbreviation":
            abbr = value.upper()
            if not (len(abbr) == ABBREV_LEN and abbr.isascii() and abbr.isalpha()):
                return Result.fail(f"Abbreviations must be exactly {ABBREV_LEN} letters (A-Z).")
            if any(q is not p and q.marker == abbr for q in self.state.players.values()):
                return Result.fail(f"The abbreviation {abbr} is already taken.")
            p.marker = abbr
        elif field == "ap":
            n, err = number(0)
            if err:
                return Result.fail(err)
            p.ap = n
        elif field == "range":
            n, err = number(1, 99)
            if err:
                return Result.fail(err)
            p.range_upgrades = n - self.s["base_range"]
        elif field == "position":
            if self.state.board_size == 0:
                return Result.fail("Positions exist once the game has started.")
            sq = geometry.parse_label(value)
            if not sq or not geometry.on_board(*sq, self.state.board_size):
                return Result.fail(f"'{value}' isn't a square on the board (e.g. C5).")
            occ = self.occupant(*sq)
            if occ and occ is not p:
                return Result.fail(f"{geometry.coord_label(*sq)} is occupied by {occ.state_name}.")
            p.x, p.y = sq
        elif field == "cooldown":
            if value.lower() not in ("reset", "ready", "0", "clear"):
                return Result.fail("Cooldown can only be reset: use the value 'reset'.")
            p.last_action = None
        elif field == "color":
            name = colours.canonical(value)
            if not name:
                return Result.fail("Color must be one of: " + ", ".join(colours.PALETTE))
            if any(q is not p and q.color == name for q in self.state.players.values()):
                return Result.fail(f"{name} is already taken.")
            p.color = name
        elif field == "owner":
            if not value:
                return Result.fail("The owner name can't be empty.")
            p.owner_name = value
        elif field == "hp":
            n, err = number(0, 99)
            if err:
                return Result.fail(err)
            if self.state.phase != "active" and (n == 0 or not p.alive):
                return Result.fail("Destroying or reviving a state only works during a game.")
            was_alive = p.alive
            p.hp = n
            if n == 0 and was_alive:                    # destroyed by the referee
                p.alive = False
                p.eliminated_on_day = self.state.day
                if p.id in self.state.info_access:
                    self.state.info_access.remove(p.id)
                extra.append(Event(
                    "ELIMINATION",
                    public=(f"{p.state_name} has been destroyed by the referee. It was controlled by "
                            f"{p.owner_name}, who now joins the Jury."),
                    private={p.id: "Your state has been destroyed. You are now on the Jury and can vote each night."},
                    ping="mandatory", data={"victim": p.id, "attacker": None}))
                extra += self._check_victory()
            elif n > 0 and not was_alive:               # revived by the referee
                p.alive = True
                p.eliminated_on_day = None
                self.state.jury_votes.pop(p.id, None)
                extra.append(Event("REVIVED", public=f"{p.state_name} has been restored by the referee.",
                                   ping="mandatory", data={"pid": p.id}))

        new = self.admin_value(p, field)
        if new == old and not extra:
            return Result.fail(f"{old_name}'s {label} is already {old}.")
        data = {"pid": p.id, "field": field, "old": old, "new": new}
        public = None
        if announce and field in self.PUBLIC_FIELDS and not extra:
            public = f"Referee adjustment: {old_name}'s {label} changed from {old} to {new}."
        events = [Event("ADMIN_EDIT", public=public,
                        private={p.id: f"The referee changed your {label} from {old} to {new}."}, data=data)]
        events += extra
        return self._done(Result(True, f"[Admin] {p.state_name}: {label} {old} → {new}.", events))

    @recorded("admin_give_ap")
    def give_ap(self, pid: str, amount: int) -> Result:
        p = self.get(pid)
        if not p:
            return Result.fail("No such player.")
        if p.ap + amount < 0:
            return Result.fail(f"{p.state_name} only has {p.ap} AP.")
        p.ap += amount
        return self._done(Result(True, f"[Admin] {p.state_name} now has {p.ap} AP."))

    # -------------------------------------------------------------- messaging
    # Relays only: no AP, no cooldown, no game-state change. Their true author
    # is always written to the history, even for anonymous posts.

    def _message_text(self, text: str, limit: int = MAX_MESSAGE_LEN) -> tuple[str, str]:
        text = text.strip()
        if not text:
            return "", "Message is empty."
        if len(text) > limit:
            return "", f"Messages are limited to {limit} characters."
        return text, ""

    @recorded("say")
    def say(self, pid: str, text: str) -> Result:
        """Post to #public-chat under the state's name. Eliminated players
        keep full social access (they only gain the jury chat)."""
        p = self.get(pid)
        if not p:
            return Result.fail("You are not a player in this game.")
        if self.state.phase != "active":
            return Result.fail("Chat through the bot is only open while the game is in progress.")
        text, err = self._message_text(text)
        if err:
            return Result.fail(err)
        return Result(True, "Posted to #public-chat.", [Event(
            "PUBLIC_CHAT", public=f"{p.state_name}: {text}", data={"sender": p.id, "state": p.state_name, "text": text})])

    @recorded("dm")
    def dm(self, pid: str, tid: str, text: str) -> Result:
        """Private state-to-state message, relayed to the recipient's console."""
        p, t = self.get(pid), self.get(tid)
        if not p:
            return Result.fail("You are not a player in this game.")
        if self.state.phase != "active":
            return Result.fail("Chat through the bot is only open while the game is in progress.")
        if not t:
            return Result.fail("No such state.")
        if t.id == p.id:
            return Result.fail("You can't message yourself.")
        text, err = self._message_text(text)
        if err:
            return Result.fail(err)
        return Result(True, f"Message delivered to {t.state_name}.", [Event(
            "PRIVATE_MESSAGE", private={t.id: f"Message from {p.state_name}: {text}"},
            data={"sender": p.id, "recipient": t.id, "text": text})])

    @recorded("anon")
    def anon(self, pid: str, text: str) -> Result:
        """Anonymous post. Publicly unattributed; the author is still logged."""
        if not self.get(pid):
            return Result.fail("You are not a player in this game.")
        text, err = self._message_text(text)
        if err:
            return Result.fail(err)
        return Result(True, "Posted anonymously to #anonymous-chat.",
                      [Event("ANONYMOUS", public=f"Anonymous: {text}", data={"text": text})])

    # ------------------------------------------------------------------ pacts
    # Anonymous group chats. The engine owns membership and logs every
    # message; the interface gives each member a private copy of the pact
    # channel and relays messages between copies under state names.
    #
    # pact = {"id", "name", "founder", "original_founder", "members": [ids in
    #         join order], "invites": [ids], "created_day", "active": bool}

    def pacts_of(self, pid: str) -> list[dict]:
        return [pc for pc in self.state.pacts.values() if pc["active"] and pid in pc["members"]]

    def pact(self, pact_id: str) -> dict | None:
        pc = self.state.pacts.get(pact_id)
        return pc if pc and pc["active"] else None

    def _pact_player(self, pid: str) -> tuple[Player | None, str]:
        p = self.get(pid)
        if not p:
            return None, "You are not a player in this game."
        if self.state.phase == "registration":
            return None, "Pacts open when the game starts."
        if self.state.phase == "ended":
            return None, "The game is over."
        return p, ""

    @recorded("pact_create")
    def create_pact(self, pid: str, name: str) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        name = " ".join(name.split())
        if not name or len(name) > MAX_PACT_NAME_LEN:
            return Result.fail(f"Pact names must be 1-{MAX_PACT_NAME_LEN} characters.")
        if any(c in name for c in "@#<>`*_~|\\"):
            return Result.fail("Pact names can't contain @ # < > ` * _ ~ | or backslashes.")
        founded = sum(1 for pc in self.state.pacts.values() if pc["original_founder"] == pid)
        if founded >= self.s["pact_found_limit"]:
            return Result.fail(f"You've already founded {founded} pacts (the limit is {self.s['pact_found_limit']}).")
        if len(self.pacts_of(pid)) >= self.s["pact_member_limit"]:
            return Result.fail(f"You're already in {self.s['pact_member_limit']} pacts, the most allowed.")
        pact_id = f"k{self.state.next_pact_num}"
        self.state.next_pact_num += 1
        self.state.pacts[pact_id] = {"id": pact_id, "name": name, "founder": pid, "original_founder": pid,
                                     "members": [pid], "invites": [], "created_day": self.state.day,
                                     "active": True}
        return self._done(Result(True, f"Founded the pact **{name}**. Invite states to it from your console.",
                                 [Event("PACT_JOINED", data={"pact": pact_id, "member": pid, "founded": True})]))

    @recorded("pact_invite")
    def invite_to_pact(self, pid: str, pact_id: str, tid: str) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        pc = self.pact(pact_id)
        if not pc or pid not in pc["members"]:
            return Result.fail("You're not in that pact.")
        if pc["founder"] != pid:
            return Result.fail("Only the pact's founder can invite.")
        t = self.get(tid)
        if not t or t.id == pid:
            return Result.fail("No such state.")
        if tid in pc["members"]:
            return Result.fail(f"{t.state_name} is already in {pc['name']}.")
        if tid in pc["invites"]:
            return Result.fail(f"{t.state_name} already has a pending invitation.")
        pc["invites"].append(tid)
        return self._done(Result(True, f"Invited {t.state_name} to {pc['name']}.", [Event(
            "PACT_INVITE", private={tid: f"**{p.state_name}** invites you to join the pact **{pc['name']}**."},
            data={"pact": pact_id, "invitee": tid, "inviter": pid})]))

    @recorded("pact_respond")
    def respond_to_pact(self, pid: str, pact_id: str, accept: bool) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        pc = self.pact(pact_id)
        if not pc or pid not in pc["invites"]:
            return Result.fail("That invitation is no longer open.")
        if accept and len(self.pacts_of(pid)) >= self.s["pact_member_limit"]:
            return Result.fail(f"You're already in {self.s['pact_member_limit']} pacts, the most allowed. "
                               "Leave one first.")
        pc["invites"].remove(pid)
        founder = self.get(pc["founder"])
        if not accept:
            return self._done(Result(True, f"Declined the invitation to {pc['name']}.", [Event(
                "PACT_DECLINED", private={pc["founder"]: f"{p.state_name} declined your invitation to {pc['name']}."},
                data={"pact": pact_id, "member": pid})]))
        pc["members"].append(pid)
        return self._done(Result(True, f"You joined **{pc['name']}** (founded by {founder.state_name}).",
                                 [Event("PACT_JOINED", data={"pact": pact_id, "member": pid, "founded": False})]))

    def _remove_from_pact(self, pc: dict, pid: str, kind: str) -> Event:
        pc["members"].remove(pid)
        data = {"pact": pc["id"], "member": pid, "dissolved": False, "new_founder": None}
        if not pc["members"]:
            pc["active"] = False
            pc["invites"] = []
            data["dissolved"] = True
        elif pc["founder"] == pid:
            pc["founder"] = pc["members"][0]          # longest-standing member
            data["new_founder"] = pc["founder"]
        return Event(kind, data=data)

    @recorded("pact_leave")
    def leave_pact(self, pid: str, pact_id: str) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        pc = self.pact(pact_id)
        if not pc or pid not in pc["members"]:
            return Result.fail("You're not in that pact.")
        ev = self._remove_from_pact(pc, pid, "PACT_LEFT")
        return self._done(Result(True, f"You left {pc['name']}.", [ev]))

    @recorded("pact_kick")
    def kick_from_pact(self, pid: str, pact_id: str, tid: str) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        pc = self.pact(pact_id)
        if not pc or pid not in pc["members"]:
            return Result.fail("You're not in that pact.")
        if pc["founder"] != pid:
            return Result.fail("Only the pact's founder can remove members.")
        if tid == pid or tid not in pc["members"]:
            return Result.fail("That state isn't a member you can remove.")
        t = self.get(tid)
        ev = self._remove_from_pact(pc, tid, "PACT_KICKED")
        return self._done(Result(True, f"Removed {t.state_name} from {pc['name']}.", [ev]))

    @recorded("pact_message")
    def pact_message(self, pid: str, pact_id: str, text: str) -> Result:
        p, err = self._pact_player(pid)
        if err:
            return Result.fail(err)
        pc = self.pact(pact_id)
        if not pc or pid not in pc["members"]:
            return Result.fail("You're not in that pact.")
        text, err = self._message_text(text, limit=2000)
        if err:
            return Result.fail(err)
        others = [m for m in pc["members"] if m != pid]
        return Result(True, "", [Event("PACT_MESSAGE", data={
            "pact": pact_id, "sender": pid, "state": p.state_name, "text": text, "recipients": others})])

    # ---------------------------------------------------------------- history

    def who(self, pid: str | None) -> dict | None:
        """A player's identity plus their current stats, for history records."""
        p = self.get(pid) if pid else None
        if not p:
            return None
        return {
            "id": p.id, "state": p.state_name, "owner": p.owner_name, "marker": p.marker, "color": p.color,
            "pos": self.pos(p), "hp": p.hp, "ap": p.ap, "range": self.range_of(p), "alive": p.alive,
        }

    def snapshot(self) -> list[dict]:
        return [self.who(pid) for pid in self.state.players]

    def _record(self, action: str, params: dict, result: Result, day_before: int) -> None:
        if not self.recorder:
            return
        now = params.pop("now", None)
        rec = {
            "game_time": now.isoformat() if now else None,
            "day": day_before,
            "phase": self.state.phase,
            "action": action,
            "ok": result.ok,
            "actor": self.who(params.pop("pid", None)),
            "target": self.who(params.pop("tid", None)),
            "args": params,
            "result": result.message,
            "events": [asdict(e) for e in result.events],
        }
        if action in ("start", "next_day") or self.state.phase == "ended":
            rec["snapshot"] = self.snapshot()
        if action == "next_day" and result.ok:
            rec["new_day"] = self.state.day
        self.recorder(rec)

    # ---------------------------------------------------------------- helpers

    def _done(self, result: Result) -> Result:
        """Record public events in the persistent log."""
        for ev in result.events:
            if ev.public:
                self.state.log.append({"day": self.state.day, "kind": ev.kind, "text": ev.public})
        return result
