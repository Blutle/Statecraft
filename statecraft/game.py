"""Statecraft rules engine.

Pure game logic: no Discord, no console I/O, no clock. Every action returns
a Result with feedback for the actor and a list of Events for the interface
(CLI now, Discord later) to deliver. Rejected actions never change state.
"""

from __future__ import annotations

import random

from . import geometry
from .models import Event, GameState, Player, Result

STOPWORDS = {"the", "of", "and", "a", "an", "de", "la", "le", "du", "von", "van"}
MAX_NAME_LEN = 40


class Game:
    def __init__(self, state: GameState | None = None, rng: random.Random | None = None):
        self.state = state or GameState()
        self.rng = rng or random.Random()

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

    def register(self, owner_name: str, state_name: str) -> Result:
        if self.state.phase != "registration":
            return Result.fail("Registration is closed; the game has already started.")
        owner_name = owner_name.strip()
        state_name = " ".join(state_name.split())
        if not owner_name or not state_name:
            return Result.fail("Both a player name and a state name are required.")
        if len(state_name) > MAX_NAME_LEN:
            return Result.fail(f"State names are limited to {MAX_NAME_LEN} characters.")
        for p in self.state.players.values():
            if p.state_name.lower() == state_name.lower():
                return Result.fail(f"The name '{state_name}' is already taken.")
            if p.owner_name.lower() == owner_name.lower():
                return Result.fail(f"Player '{owner_name}' already controls {p.state_name}.")

        pid = f"p{self.state.next_player_num}"
        self.state.next_player_num += 1
        player = Player(
            id=pid,
            owner_name=owner_name,
            state_name=state_name,
            marker=self._make_marker(state_name),
            hp=self.s["starting_hp"],
        )
        self.state.players[pid] = player
        return Result(True, f"{owner_name} registered {state_name} [{player.marker}] as {pid}.")

    def unregister(self, pid: str) -> Result:
        if self.state.phase != "registration":
            return Result.fail("Players can only be removed during registration.")
        p = self.state.players.pop(pid, None)
        if not p:
            return Result.fail("No such player.")
        return Result(True, f"Removed {p.state_name}.")

    def _make_marker(self, state_name: str) -> str:
        taken = {p.marker for p in self.state.players.values()}
        words = [w for w in state_name.split() if w.lower() not in STOPWORDS] or state_name.split()
        letters = [c for c in "".join(words) if c.isalnum()]
        candidates = []
        if len(words) >= 2:
            candidates.append(words[0][0] + words[1][0])
        if len(letters) >= 2:
            candidates.append(letters[0] + letters[1])
        first = letters[0] if letters else "X"
        candidates += [first + c for c in "23456789"]
        candidates += [a + b for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        for c in candidates:
            c = c.upper()
            if c not in taken:
                return c
        raise RuntimeError("Ran out of markers")

    def start(self) -> Result:
        if self.state.phase != "registration":
            return Result.fail("The game has already started.")
        players = list(self.state.players.values())
        if len(players) < 2:
            return Result.fail("At least 2 players are needed to start.")
        n = len(players)
        self.state.board_size = n
        squares = [(x, y) for x in range(n) for y in range(n)]
        for p, (x, y) in zip(players, self.rng.sample(squares, n)):
            p.x, p.y = x, y
            p.hp = self.s["starting_hp"]
            p.ap = 0
        self.state.phase = "active"
        self.state.day = 0
        roster = ", ".join(f"{p.state_name} [{p.marker}] at {self.pos(p)}" for p in players)
        return self._done(Result(True, "Game started.", [Event(
            "GAME_START",
            public=f"Statecraft begins on a {n}x{n} board with {n} states: {roster}. "
                   f"All states start with 0 AP; the first AP arrives at the start of Day 1.",
            ping="mandatory",
        )]))

    # ---------------------------------------------------------------- actions

    def _actor(self, pid: str, cost: int) -> tuple[Player | None, str]:
        if self.state.phase != "active":
            return None, "The game is not in progress."
        p = self.get(pid)
        if not p:
            return None, "You are not a player in this game."
        if not p.alive:
            return None, "Your state has been eliminated. You can only vote with the jury."
        if p.ap < cost:
            return None, f"Not enough AP: this costs {cost}, you have {p.ap}."
        return p, ""

    def _target(self, actor: Player, tid: str, verb: str) -> tuple[Player | None, str]:
        t = self.get(tid)
        if not t:
            return None, "No such state."
        if t.id == actor.id:
            return None, f"You can't {verb} yourself."
        if not t.alive:
            return None, f"{t.state_name} has been eliminated."
        if not self.in_range(actor, t):
            return None, (f"{t.state_name} at {self.pos(t)} is out of range "
                          f"(distance {geometry.distance(actor.x, actor.y, t.x, t.y)}, your range {self.range_of(actor)}).")
        return t, ""

    def move(self, pid: str, direction: str) -> Result:
        cost = self.s["move_cost"]
        p, err = self._actor(pid, cost)
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
        return self._done(Result(True, f"Moved to {self.pos(p)}. AP left: {p.ap}.", [Event(
            "MOVE", public=f"{p.state_name} moved from {old} to {self.pos(p)}.", ping="opt_in")]))

    def attack(self, pid: str, tid: str) -> Result:
        cost = self.s["attack_cost"]
        p, err = self._actor(pid, cost)
        if err:
            return Result.fail(err)
        t, err = self._target(p, tid, "attack")
        if err:
            return Result.fail(err)
        p.ap -= cost
        t.hp -= 1
        events: list[Event] = []
        if t.hp > 0:
            events.append(Event(
                "ATTACK",
                public=f"{p.state_name} attacked {t.state_name}! {t.state_name} has {t.hp} HP left.",
                ping="mandatory",
            ))
        else:
            t.hp = 0
            t.alive = False
            t.eliminated_on_day = self.state.day
            events.append(Event(
                "ELIMINATION",
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
        self.state.phase = "ended"
        self.state.winner = w.id
        return [Event(
            "GAME_OVER",
            public=f"{w.state_name} is the last state standing and wins Statecraft! "
                   f"It was controlled by {w.owner_name}.",
            ping="mandatory",
        )]

    def upgrade(self, pid: str) -> Result:
        cost = self.s["upgrade_cost"]
        p, err = self._actor(pid, cost)
        if err:
            return Result.fail(err)
        p.ap -= cost
        p.range_upgrades += 1
        r = self.range_of(p)
        return self._done(Result(True, f"Range is now {r}. AP left: {p.ap}.", [Event(
            "UPGRADE", public=f"{p.state_name} increased its range to {r}.", ping="opt_in")]))

    def transfer(self, pid: str, tid: str, amount: int) -> Result:
        if amount < 1:
            return Result.fail("You must transfer at least 1 AP.")
        p, err = self._actor(pid, amount)
        if err:
            return Result.fail(err)
        t, err = self._target(p, tid, "transfer AP to")
        if err:
            return Result.fail(err)
        p.ap -= amount
        t.ap += amount
        self.state.transfers_today.append({"from": p.id, "to": t.id, "amount": amount})
        line = f"{p.state_name} -> {t.state_name}: {amount} AP"
        private = {t.id: f"You received {amount} AP from {p.state_name}. You now have {t.ap} AP."}
        for buyer in self.state.info_access:
            if buyer not in (p.id, t.id):
                private[buyer] = f"[Info] {line}"
        return self._done(Result(True, f"Sent {amount} AP to {t.state_name}. AP left: {p.ap}.",
                                 [Event("TRANSFER", private=private)]))

    def buy_info(self, pid: str) -> Result:
        cost = self.s["info_cost"]
        p, err = self._actor(pid, cost)
        if err:
            return Result.fail(err)
        if pid in self.state.info_access:
            return Result.fail("You already have information access for today.")
        p.ap -= cost
        self.state.info_access.append(pid)
        return self._done(Result(
            True,
            f"Information access purchased for the rest of Day {self.state.day}. AP left: {p.ap}.\n"
            + self.transfer_report(),
            [Event("INFO_PURCHASE", public=f"{p.state_name} purchased information access.", ping="opt_in")],
        ))

    def transfer_report(self) -> str:
        if not self.state.transfers_today:
            return f"No AP transfers so far on Day {self.state.day}."
        lines = [f"AP transfers on Day {self.state.day}:"]
        for tr in self.state.transfers_today:
            lines.append(f"  {self.get(tr['from']).state_name} -> {self.get(tr['to']).state_name}: {tr['amount']} AP")
        return "\n".join(lines)

    def vote(self, pid: str, tid: str) -> Result:
        if self.state.phase != "active":
            return Result.fail("The game is not in progress.")
        p = self.get(pid)
        if not p:
            return Result.fail("You are not a player in this game.")
        if p.alive:
            return Result.fail("Only eliminated players (the Jury) can vote.")
        t = self.get(tid)
        if not t or not t.alive:
            return Result.fail("You can only vote for a surviving state.")
        changed = pid in self.state.jury_votes
        self.state.jury_votes[pid] = tid
        verb = "changed to" if changed else "cast for"
        return self._done(Result(True, f"Your vote is {verb} {t.state_name}. You can change it until the day ends."))

    # ------------------------------------------------------------- day cycle

    def next_day(self) -> Result:
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
            parts = []
            for tid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                t = self.get(tid)
                bonus = n // self.s["votes_per_ap"]
                t.ap += bonus
                parts.append(f"{t.state_name}: {n} vote{'s' if n != 1 else ''} (+{bonus} AP)")
            events.append(Event("JURY_RESULT", public="Jury results: " + ("; ".join(parts) or "no valid votes") + "."))
            self.state.jury_votes = {}

        self.state.info_access = []
        self.state.transfers_today = []
        self.state.day += 1
        for p in self.alive_players():
            p.ap += self.s["daily_ap"]
        events.append(Event(
            "DAILY_AP",
            public=f"Day {self.state.day} begins. Each surviving state receives {self.s['daily_ap']} AP.",
        ))
        return self._done(Result(True, f"Advanced to Day {self.state.day}.", events))

    # ------------------------------------------------------------------ admin

    def give_ap(self, pid: str, amount: int) -> Result:
        p = self.get(pid)
        if not p:
            return Result.fail("No such player.")
        if p.ap + amount < 0:
            return Result.fail(f"{p.state_name} only has {p.ap} AP.")
        p.ap += amount
        return self._done(Result(True, f"[Admin] {p.state_name} now has {p.ap} AP."))

    # ---------------------------------------------------------------- helpers

    def _done(self, result: Result) -> Result:
        """Record public events in the persistent log."""
        for ev in result.events:
            if ev.public:
                self.state.log.append({"day": self.state.day, "kind": ev.kind, "text": ev.public})
        return result
