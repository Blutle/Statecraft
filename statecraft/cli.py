"""Command-line hot-seat interface for testing Statecraft game logic.

You pick which player you are with `as <player>`; every command after that
acts as them. No clock, action window, or cooldowns: `nextday` advances time.
"""

from __future__ import annotations

import argparse
import random
import shlex
from pathlib import Path

from . import render_text, store
from .game import Game
from .models import GameState, Result

HELP = """
SETUP
  add <player> <state name>     Register a player and their state (quotes optional)
  remove <player>               Remove a player (registration only)
  start                         Place states randomly and begin the game
  reset                         Wipe the game (asks for confirmation)

WHO AM I
  as <player>                   Act as this player (id, player name, marker, or state name)
  as <player> <command...>      Run one command as that player, e.g.  as greg move ne
  admin                         Stop acting as anyone

LOOKING
  board                         Show the board (with your range marked ':')
  states                        Public state list
  status                        Your private status (AP, etc.)
  info                          Re-show today's AP transfers (needs info access)
  log [n]                       Last n public events (default 15)

ACTIONS (current player)
  move <dir>                    n, ne, e, se, s, sw, w, nw          (1 AP)
  attack <state>                Deal 1 damage to a state in range   (1 AP)
  upgrade                       Range +1                            (2 AP)
  transfer <state> <amount>     Give AP to a state in range         (amount)
  buyinfo                       See today's AP transfers            (1 AP)
  vote <state>                  Jury only: vote for a surviving state

TIME / ADMIN
  nextday                       End the day: jury tally, then +1 AP to survivors
  give <player> <amount>        Admin: add (or remove, if negative) AP
  help                          This text
  quit                          Exit (the game is saved after every change)

A state can be named by its marker (e.g. RA), its name, or the start of its name.
"""


class CLI:
    def __init__(self, path: Path, seed: int | None = None):
        self.path = path
        state = store.load(path)
        self.game = Game(state, rng=random.Random(seed))
        self.current: str | None = None
        if state:
            print(f"Loaded {path} (phase: {state.phase}, day {state.day}, {len(state.players)} players).")
        else:
            print(f"Starting a new game. It will be saved to {path}.")

    # --------------------------------------------------------------- output

    def show(self, result: Result) -> None:
        if result.message:
            print(result.message if result.ok else f"Rejected: {result.message}")
        for ev in result.events:
            if ev.public:
                tag = {"mandatory": "[PUBLIC @Players]", "opt_in": "[PUBLIC @Updates]"}.get(ev.ping, "[PUBLIC]")
                print(f"{tag} {ev.public}")
            for pid, text in ev.private.items():
                p = self.game.get(pid)
                print(f"[PRIVATE -> {p.state_name if p else pid}] {text}")
        if result.ok:
            store.save(self.game.state, self.path)

    def prompt(self) -> str:
        p = self.game.get(self.current) if self.current else None
        who = f"{p.owner_name} / {p.state_name}" if p else "admin"
        return f"\n[{who}] > "

    # ------------------------------------------------------------- dispatch

    def run(self) -> None:
        print('Type "help" for commands.')
        while True:
            try:
                line = input(self.prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not self.handle(line):
                break

    def handle(self, line: str) -> bool:
        try:
            args = shlex.split(line)
        except ValueError:
            args = line.split()  # e.g. an apostrophe in "King's Reach"
        if not args:
            return True
        cmd, rest = args[0].lower(), args[1:]
        if cmd in ("quit", "exit", "q"):
            return False
        handler = getattr(self, f"cmd_{cmd}", None)
        if not handler:
            print(f"Unknown command '{cmd}'. Type 'help'.")
            return True
        handler(rest)
        return True

    def _me(self):
        if not self.current:
            print("You aren't acting as anyone. Use: as <player>")
            return None
        return self.current

    def _find(self, query: str):
        p, err = self.game.find(query)
        if err:
            print(err)
        return p

    # ------------------------------------------------------------- commands

    def cmd_help(self, a):
        print(HELP)

    def cmd_add(self, a):
        if len(a) < 2:
            print("Usage: add <player> <state name>")
            return
        self.show(self.game.register(a[0], " ".join(a[1:])))

    def cmd_remove(self, a):
        p = self._find(" ".join(a))
        if p:
            if self.current == p.id:
                self.current = None
            self.show(self.game.unregister(p.id))

    def cmd_start(self, a):
        res = self.game.start()
        self.show(res)
        if res.ok:
            print()
            print(render_text.board(self.game))

    def cmd_reset(self, a):
        if a != ["yes"]:
            print("This deletes the whole game. Type 'reset yes' to confirm.")
            return
        self.game = Game(GameState(), rng=self.game.rng)
        self.current = None
        store.save(self.game.state, self.path)
        print("Game reset.")

    def cmd_as(self, a):
        if not a:
            print("Usage: as <player> [command...]")
            return
        p = self._find(a[0])
        if not p:
            return
        if len(a) == 1:
            self.current = p.id
            print(f"You are now {p.owner_name}, controlling {p.state_name} [{p.marker}].")
            return
        previous, self.current = self.current, p.id
        try:
            self.handle(shlex.join(a[1:]))
        finally:
            self.current = previous

    def cmd_admin(self, a):
        self.current = None
        print("Acting as admin.")

    def cmd_board(self, a):
        me = self.game.get(self.current) if self.current else None
        print(render_text.board(self.game, me if self.game.state.phase == "active" else None))

    def cmd_states(self, a):
        me = self.game.get(self.current) if self.current else None
        if self.game.state.phase == "registration":
            for p in self.game.state.players.values():
                print(f"  {p.id:<4} {p.marker:<3} {p.state_name}  ({p.owner_name})")
            print(f"{len(self.game.state.players)} registered. Type 'start' when ready.")
        else:
            print(render_text.legend(self.game, me))

    def cmd_status(self, a):
        pid = self._me()
        if pid:
            print(render_text.status(self.game, self.game.get(pid)))

    def cmd_info(self, a):
        pid = self._me()
        if not pid:
            return
        if pid not in self.game.state.info_access:
            print("You don't have information access today. Use 'buyinfo' (1 AP).")
            return
        print(self.game.transfer_report())

    def cmd_log(self, a):
        n = int(a[0]) if a and a[0].isdigit() else 15
        for entry in self.game.state.log[-n:]:
            print(f"  Day {entry['day']:<3} {entry['text']}")

    def cmd_move(self, a):
        pid = self._me()
        if pid:
            if len(a) != 1:
                print("Usage: move <n|ne|e|se|s|sw|w|nw>")
                return
            self.show(self.game.move(pid, a[0]))

    def cmd_attack(self, a):
        pid = self._me()
        if pid:
            t = self._find(" ".join(a))
            if t:
                self.show(self.game.attack(pid, t.id))

    def cmd_upgrade(self, a):
        pid = self._me()
        if pid:
            self.show(self.game.upgrade(pid))

    def cmd_transfer(self, a):
        pid = self._me()
        if not pid:
            return
        if len(a) < 2 or not a[-1].lstrip("-").isdigit():
            print("Usage: transfer <state> <amount>")
            return
        t = self._find(" ".join(a[:-1]))
        if t:
            self.show(self.game.transfer(pid, t.id, int(a[-1])))

    def cmd_buyinfo(self, a):
        pid = self._me()
        if pid:
            self.show(self.game.buy_info(pid))

    def cmd_vote(self, a):
        pid = self._me()
        if pid:
            t = self._find(" ".join(a))
            if t:
                self.show(self.game.vote(pid, t.id))

    def cmd_nextday(self, a):
        self.show(self.game.next_day())

    def cmd_give(self, a):
        if len(a) < 2 or not a[-1].lstrip("-").isdigit():
            print("Usage: give <player> <amount>")
            return
        p = self._find(" ".join(a[:-1]))
        if p:
            self.show(self.game.give_ap(p.id, int(a[-1])))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="statecraft", description="Statecraft command-line prototype")
    parser.add_argument("--file", default="game.json", help="game save file (default: game.json)")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible placement")
    args = parser.parse_args(argv)
    try:
        CLI(Path(args.file), seed=args.seed).run()
    except ValueError as e:
        print(f"Could not load {args.file}: {e}")
