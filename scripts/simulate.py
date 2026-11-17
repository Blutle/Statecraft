"""High-speed simulated games with invariant checking.

Random bots play complete games through the real Game API. Bots mix sensible
play (attack, chase, upgrade, transfer, buy info, jury votes) with deliberately
illegal actions. After every call the simulator checks invariants: what an
accepted action must have changed, that rejected actions changed nothing, AP
bookkeeping, board integrity, and identity masking in public text.

Usage:  py scripts/simulate.py [--games 10] [--seed 1] [--verbose]
"""

from __future__ import annotations

import argparse
import copy
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from statecraft import geometry  # noqa: E402
from statecraft.game import Game  # noqa: E402
from statecraft.models import Result  # noqa: E402

STATE_WORDS = ["Republic", "Empire", "Dominion", "Cities", "Haven", "Kingdom", "League", "Union",
               "Realm", "March", "Duchy", "Isles", "Reach", "Pact", "Throne", "Coast"]
PLACE_WORDS = ["Aldren", "Veyra", "Iron", "Westmoor", "Kesh", "Orlan", "Dusk", "Varn", "Sol",
               "Tessar", "Ember", "Grey", "Nor", "Quill", "Ashen", "Brin", "Corvo", "Lyre"]
MAX_DAYS = 400
SETTINGS_OVERRIDES: dict[str, int] = {}   # filled from --set key=value


class InvariantError(AssertionError):
    pass


class Sim:
    def __init__(self, game_no: int, n_players: int, rng: random.Random, verbose: bool,
                 style: str = "mixed", jury: str = "random"):
        self.no = game_no
        self.jury = jury
        self.bloc_fav: str | None = None
        self.style = style
        self.rng = rng
        self.verbose = verbose
        self.records: list[dict] = []
        self.calls = 0
        self.g = Game(rng=random.Random(rng.random()), recorder=self.records.append)
        self.g.state.settings.update(SETTINGS_OVERRIDES)
        self.stats = Counter()
        self.expected_total_ap = 0      # AP that should exist across all states
        names = set()
        while len(names) < n_players:
            names.add(f"{rng.choice(['The ', ''])}{rng.choice(STATE_WORDS)} of {rng.choice(PLACE_WORDS)}")
        for i, name in enumerate(sorted(names)):
            self.call("register", self.g.register(f"user{i + 1:02d}", name), expect_ok=True)
        # Illegal setup actions
        self.call("register-dup", self.g.register("someone", sorted(names)[0]), expect_ok=False)
        self.call("start", self.g.start(), expect_ok=True)
        self.call("start-again", self.g.start(), expect_ok=False)
        self.call("register-late", self.g.register("late", "Too Late"), expect_ok=False)
        self.check_board()

    # ------------------------------------------------------------- checks

    def fail(self, msg: str):
        raise InvariantError(f"[game {self.no}, day {self.g.state.day}] {msg}")

    def call(self, label: str, res: Result, expect_ok: bool | None = None, before=None):
        """Record a result; verify rejections changed nothing; scan public text."""
        self.calls += 1
        if len(self.records) != self.calls:
            self.fail(f"{label} was not written to the history log")
        rec = self.records[-1]
        if rec["ok"] != res.ok or rec["result"] != res.message:
            self.fail(f"history record for {label} does not match the result")
        self.stats[f"{label}:{'ok' if res.ok else 'rejected'}"] += 1
        if expect_ok is not None and res.ok != expect_ok:
            self.fail(f"{label} expected ok={expect_ok}, got ok={res.ok}: {res.message}")
        if not res.ok:
            if res.events:
                self.fail(f"rejected {label} emitted events")
            if before is not None and self.g.state.to_dict() != before:
                self.fail(f"rejected {label} changed state: {res.message}")
        self.check_masking(res)
        if self.verbose and res.ok:
            for ev in res.events:
                if ev.public:
                    print(f"    {ev.public}")
        return res

    def check_masking(self, res: Result):
        living_owners = [p.owner_name for p in self.g.state.players.values() if p.alive]
        for ev in res.events:
            if not ev.public or ev.kind == "GAME_OVER":  # winner reveal is intended (spec 2.3)
                continue
            for owner in living_owners:
                if owner in ev.public:
                    self.fail(f"public {ev.kind} text leaks living owner {owner}: {ev.public}")

    def check_board(self):
        st = self.g.state
        n = st.board_size
        seen = set()
        for p in st.players.values():
            if not geometry.on_board(p.x, p.y, n):
                self.fail(f"{p.state_name} off board at {(p.x, p.y)}")
            if (p.x, p.y) in seen:
                self.fail(f"two states share {(p.x, p.y)}")
            seen.add((p.x, p.y))
            if p.ap < 0:
                self.fail(f"{p.state_name} has negative AP {p.ap}")
            if not 0 <= p.hp <= st.settings["starting_hp"]:
                self.fail(f"{p.state_name} HP out of bounds: {p.hp}")
            if p.alive != (p.hp > 0):
                self.fail(f"{p.state_name} alive={p.alive} but hp={p.hp}")
            if p.range_upgrades < 0:
                self.fail("negative range upgrades")
        for pid in st.info_access:
            if not st.players[pid].alive:
                self.fail("a destroyed state still has info access")
        if len({p.marker for p in st.players.values()}) != len(st.players):
            self.fail("duplicate markers")
        total = sum(p.ap for p in st.players.values())
        if total != self.expected_total_ap:
            self.fail(f"AP bookkeeping off: states hold {total}, expected {self.expected_total_ap}")
        alive = self.g.alive_players()
        if st.phase == "active" and len(alive) < 2:
            self.fail("game still active with fewer than 2 survivors")
        if st.phase == "ended" and (len(alive) != 1 or st.winner != alive[0].id):
            self.fail("ended game has wrong winner/survivors")

    # ------------------------------------------------------------- actions

    def snapshot(self):
        return copy.deepcopy(self.g.state.to_dict())

    def do_action(self, p):
        """One action by a living player: mostly sensible, sometimes illegal."""
        g = self.g
        enemies = [e for e in g.alive_players() if e.id != p.id]
        in_range = [e for e in enemies if g.in_range(p, e)]
        out_range = [e for e in enemies if not g.in_range(p, e)]
        before = self.snapshot()
        roll = self.rng.random()

        if self.rng.random() < 0.10:          # free messages (don't use AP)
            kind = self.rng.choice(["say", "dm", "anon"])
            text = f"msg-{self.rng.randrange(10**6)}"
            if kind == "say":
                res = self.call("say", g.say(p.id, text), True)
            elif kind == "dm":
                res = self.call("dm", g.dm(p.id, self.rng.choice(enemies).id, text), True)
            else:
                res = self.call("anon", g.anon(p.id, text), True)
                if p.state_name in res.events[0].public:
                    self.fail("anonymous post shows its author")
            rec = self.records[-1]
            if rec["actor"]["id"] != p.id or rec["args"]["text"] != text:
                self.fail(f"{kind} history record missing true author or text")

        if roll < 0.08:                       # deliberately illegal actions
            choice = self.rng.randrange(7)
            if choice == 0 and out_range:
                return self.call("attack-out-of-range", g.attack(p.id, self.rng.choice(out_range).id), False, before)
            if choice == 1:
                return self.call("attack-self", g.attack(p.id, p.id), False, before)
            if choice == 2:
                return self.call("transfer-too-much", g.transfer(p.id, (in_range or enemies)[0].id, p.ap + 1), False, before)
            if choice == 3:
                return self.call("transfer-zero", g.transfer(p.id, enemies[0].id, 0), False, before)
            if choice == 4:
                return self.call("vote-while-alive", g.vote(p.id, enemies[0].id), False, before)
            if choice == 5:
                return self.call("move-bad-dir", g.move(p.id, "up"), False, before)
            dead = [d for d in g.state.players.values() if not d.alive]
            if dead and g.in_range(p, dead[0]):
                return self.call("attack-dead", g.attack(p.id, dead[0].id), False, before)
            return None

        if self.style == "aggressive":       # kill-bot: attack if possible, else chase
            roll = 0.0 if in_range else 0.99

        if in_range and roll < 0.55:
            t = self.rng.choice(in_range)
            hp_before, ap_before = t.hp, p.ap
            res = self.call("attack", g.attack(p.id, t.id), None, before)
            if res.ok:
                self.expected_total_ap -= g.s["attack_cost"]
                if t.hp != hp_before - 1 or p.ap != ap_before - g.s["attack_cost"]:
                    self.fail("attack did not deal exactly 1 damage / cost 1 AP")
                if t.hp == 0:
                    self.stats["~ap_stranded_on_death"] += t.ap
            return res
        if roll < 0.62:
            ap_before, r_before = p.ap, g.range_of(p)
            res = self.call("upgrade", g.upgrade(p.id), None, before)
            if res.ok:
                self.expected_total_ap -= g.s["upgrade_cost"]
                if g.range_of(p) != r_before + 1 or p.ap != ap_before - 2:
                    self.fail("upgrade wrong")
            return res
        if in_range and roll < 0.68:
            t = self.rng.choice(in_range)
            amt = self.rng.randint(1, max(1, p.ap))
            sender_before, recv_before = p.ap, t.ap
            buyers = set(g.state.info_access) - {p.id, t.id}
            res = self.call("transfer", g.transfer(p.id, t.id, amt), None, before)
            if res.ok:
                if p.ap != sender_before - amt or t.ap != recv_before + amt:
                    self.fail("transfer amounts wrong")
                recipients = set(res.events[0].private)
                if recipients != {t.id} | buyers:
                    self.fail(f"transfer notice went to {recipients}, expected {({t.id} | buyers)}")
                if res.events[0].public:
                    self.fail("transfer was announced publicly")
            return res
        if roll < 0.71:
            res = self.call("buyinfo", g.buy_info(p.id), None, before)
            if res.ok:
                self.expected_total_ap -= g.s["info_cost"]
            return res
        # Otherwise move: toward nearest enemy, or randomly
        target = min(enemies, key=lambda e: geometry.distance(p.x, p.y, e.x, e.y))
        if self.style == "aggressive":
            legal = [k for k, (dx, dy) in geometry.DIRECTIONS.items()
                     if geometry.on_board(p.x + dx, p.y + dy, g.state.board_size)
                     and not g.occupant(p.x + dx, p.y + dy)] or list(geometry.DIRECTIONS)
            d = min(legal, key=lambda k: geometry.distance(
                p.x + geometry.DIRECTIONS[k][0], p.y + geometry.DIRECTIONS[k][1], target.x, target.y))
        elif self.rng.random() < 0.75:
            d = min(geometry.DIRECTIONS, key=lambda k: geometry.distance(
                p.x + geometry.DIRECTIONS[k][0], p.y + geometry.DIRECTIONS[k][1], target.x, target.y))
        else:
            d = self.rng.choice(list(geometry.DIRECTIONS))
        x0, y0, ap_before = p.x, p.y, p.ap
        res = self.call("move", g.move(p.id, d), None, before)
        if res.ok:
            self.expected_total_ap -= g.s["move_cost"]
            dx, dy = geometry.DIRECTIONS[d]
            if (p.x, p.y) != (x0 + dx, y0 + dy) or p.ap != ap_before - 1:
                self.fail("move went to the wrong square or cost wrong")
        return res

    def bloc_target(self, alive):
        """Coordinated jury: everyone backs one favorite until it dies."""
        if not self.bloc_fav or not self.g.get(self.bloc_fav).alive:
            self.bloc_fav = self.rng.choice(alive).id
        return self.g.get(self.bloc_fav)

    def dead_actions(self):
        g = self.g
        alive = g.alive_players()
        for d in [p for p in g.state.players.values() if not p.alive]:
            before = self.snapshot()
            # Dead states must not act
            self.call("dead-move", g.move(d.id, "n"), False, before)
            self.call("dead-buyinfo", g.buy_info(d.id), False, before)
            # Jury vote (sometimes changing it)
            if self.jury == "bloc":
                self.call("vote", g.vote(d.id, self.bloc_target(alive).id), True)
            else:
                for _ in range(self.rng.choice([1, 1, 2])):
                    self.call("vote", g.vote(d.id, self.rng.choice(alive).id), True)
            before = self.snapshot()
            self.call("vote-for-dead", g.vote(d.id, d.id), False, before)

    def next_day(self):
        g = self.g
        votes = Counter(v for v in g.state.jury_votes.values() if g.get(v).alive)
        ap_before = {p.id: p.ap for p in g.state.players.values()}
        res = self.call("nextday", g.next_day(), True)
        for p in g.state.players.values():
            expected = ap_before[p.id]
            if p.alive:
                expected += g.s["daily_ap"] + votes.get(p.id, 0) // g.s["votes_per_ap"]
            if p.ap != expected:
                self.fail(f"{p.state_name} AP after day tick {p.ap}, expected {expected}")
            self.expected_total_ap += p.ap - ap_before[p.id]
        if g.state.jury_votes or g.state.info_access or g.state.transfers_today:
            self.fail("day tick did not reset votes/info/transfers")
        return res

    # ------------------------------------------------------- alliance mode
    # Bots form blocs with nearby states, daisy-chain AP to one attacker,
    # strike the weakest reachable enemy, and occasionally betray.

    def act(self, label: str, res: Result, before, cost: int = 0):
        res = self.call(label, res, None, before)
        if res.ok:
            self.expected_total_ap -= cost
        self.check_board()
        return res

    def form_alliances(self):
        g = self.g
        unassigned = {p.id for p in g.alive_players()}
        order = g.alive_players()
        self.rng.shuffle(order)
        self.alliances: list[list[str]] = []
        for p in order:
            if p.id not in unassigned:
                continue
            size = self.rng.choice([1, 2, 3, 3, 4, 4])
            unassigned.discard(p.id)
            near = sorted(unassigned, key=lambda q: geometry.distance(p.x, p.y, g.get(q).x, g.get(q).y))
            group = [p.id] + near[:size - 1]
            unassigned -= set(group)
            self.alliances.append(group)
        self.stats["~alliances_formed"] += sum(1 for a in self.alliances if len(a) > 1)

    def ally(self, a: str, b: str) -> bool:
        return any(a in grp and b in grp for grp in self.alliances)

    def chain_members(self, hitter, members) -> set[str]:
        """Allies that can relay AP to the hitter (directly or via other allies)."""
        g = self.g
        seen = {hitter.id}
        queue = [hitter]
        while queue:
            u = queue.pop(0)
            for m in members:
                if m.id not in seen and g.in_range(m, u):
                    seen.add(m.id)
                    queue.append(m)
        return seen

    def chain_pool(self, hitter, members) -> int:
        return sum(self.g.get(i).ap for i in self.chain_members(hitter, members))

    def step_toward(self, m, target):
        g = self.g
        legal = [k for k, (dx, dy) in geometry.DIRECTIONS.items()
                 if geometry.on_board(m.x + dx, m.y + dy, g.state.board_size)
                 and not g.occupant(m.x + dx, m.y + dy)]
        if not legal or m.ap < 1:
            return
        d = min(legal, key=lambda k: geometry.distance(
            m.x + geometry.DIRECTIONS[k][0], m.y + geometry.DIRECTIONS[k][1], target.x, target.y))
        self.act("regroup", g.move(m.id, d), self.snapshot(), g.s["move_cost"])

    def pool_to(self, hitter, members, need: int):
        """Daisy-chain AP to the hitter: BFS over allies by transfer range,
        then each donor's AP hops along the chain toward the hitter."""
        g = self.g
        parent = {hitter.id: None}
        queue = [hitter]
        while queue:
            u = queue.pop(0)
            for m in members:
                if m.id not in parent and g.in_range(m, u):
                    parent[m.id] = u.id
                    queue.append(m)
        for donor_id in list(parent)[1:]:
            if hitter.ap >= need:
                return
            donor = g.get(donor_id)
            amount = donor.ap
            if amount <= 0:
                continue
            hops = 0
            cur = donor_id
            while cur != hitter.id:
                nxt = parent[cur]
                self.act("chain-transfer", g.transfer(cur, nxt, amount), self.snapshot())
                cur = nxt
                hops += 1
            self.stats["~chain_hops"] += hops

    def alliance_day(self):
        g = self.g
        self.rng.shuffle(self.alliances)
        for grp in list(self.alliances):
            if g.state.phase != "active":
                return
            members = [g.get(i) for i in grp if g.get(i).alive]
            if not members:
                continue
            enemies = [e for e in g.alive_players() if e.id not in grp]
            if not enemies:                       # only allies left: alliance breaks
                self.alliances.remove(grp)
                self.alliances += [[m.id] for m in members]
                self.stats["~alliances_dissolved"] += 1
                continue

            # Betrayal: a member with AP stabs an ally in range and leaves
            if len(members) > 1 and self.rng.random() < 0.04:
                traitor = self.rng.choice(members)
                victims = [m for m in members if m.id != traitor.id and g.in_range(traitor, m)]
                if traitor.ap > 0 and victims:
                    v = self.rng.choice(victims)
                    self.act("betrayal", g.attack(traitor.id, v.id), self.snapshot(), g.s["attack_cost"])
                    grp.remove(traitor.id)
                    self.alliances.append([traitor.id])
                    self.stats["~betrayals"] += 1
                    continue

            if len(members) > 1 and self.rng.random() < 0.5:      # chatter for the log
                a, b = self.rng.sample(members, 2)
                self.call("ally-dm", g.dm(a.id, b.id, "stick to the plan"), True)

            # Strike when the chain can deliver a kill: weakest reachable enemy.
            # Otherwise mostly save up (impatient blocs sometimes chip anyway).
            pairs = [(e.hp, self.rng.random(), m, e) for m in members for e in enemies if g.in_range(m, e)]
            killable = [t for t in pairs if self.chain_pool(t[2], members) >= t[3].hp]
            if killable or (pairs and self.rng.random() < 0.25):
                _, _, hitter, target = min(killable or pairs, key=lambda t: (t[0], t[1]))
                self.pool_to(hitter, members, need=target.hp)
                while hitter.ap > 0 and target.alive and g.state.phase == "active":
                    self.act("chain-attack", g.attack(hitter.id, target.id), self.snapshot(), g.s["attack_cost"])
                continue
            if pairs:
                # Saving for a kill: pull disconnected allies into chain range
                hitter = min(pairs, key=lambda t: (t[0], t[1]))[2]
                linked = self.chain_members(hitter, members)
                for m in members:
                    if m.id not in linked and m.ap >= 2 and self.rng.random() < 0.5:
                        self.step_toward(m, hitter)
                continue

            # Nobody in reach: the member closest to an enemy advances; others
            # save, or sometimes upgrade with spare AP
            def gap(m):
                return min(geometry.distance(m.x, m.y, e.x, e.y) for e in enemies)
            scout = min(members, key=gap)
            if scout.ap > 0:
                tgt = min(enemies, key=lambda e: geometry.distance(scout.x, scout.y, e.x, e.y))
                legal = [k for k, (dx, dy) in geometry.DIRECTIONS.items()
                         if geometry.on_board(scout.x + dx, scout.y + dy, g.state.board_size)
                         and not g.occupant(scout.x + dx, scout.y + dy)]
                if legal:
                    d = min(legal, key=lambda k: geometry.distance(
                        scout.x + geometry.DIRECTIONS[k][0], scout.y + geometry.DIRECTIONS[k][1], tgt.x, tgt.y))
                    self.act("advance", g.move(scout.id, d), self.snapshot(), g.s["move_cost"])
                elif scout.ap >= g.s["upgrade_cost"]:     # boxed in: reach over the walls
                    self.act("upgrade", g.upgrade(scout.id), self.snapshot(), g.s["upgrade_cost"])
            for m in members:
                if m.id != scout.id and m.ap >= 4 and self.rng.random() < 0.3:
                    self.act("upgrade", g.upgrade(m.id), self.snapshot(), g.s["upgrade_cost"])

    # ----------------------------------------------------------------- run

    def run(self) -> dict:
        g = self.g
        n = len(g.state.players)
        # Day 0: nobody has AP, every action must fail
        for p in g.state.players.values():
            before = self.snapshot()
            self.call("day0-move", g.move(p.id, "n"), False, before)
        while g.state.phase == "active":
            if g.state.day >= MAX_DAYS:
                self.fail(f"no winner after {MAX_DAYS} days")
            self.next_day()
            self.dead_actions()
            if self.style == "alliance":
                if g.state.day == 1:
                    self.form_alliances()
                self.alliance_day()
                continue
            order = g.alive_players()
            self.rng.shuffle(order)
            for p in order:
                # Bots sometimes save AP; otherwise spend until out
                tries = 0
                while p.alive and g.state.phase == "active" and p.ap > 0 and tries < 12:
                    tries += 1
                    if self.style == "mixed" and self.rng.random() < 0.15:
                        break
                    self.do_action(p)
                    self.check_board()
            self.check_board()

        # After the game ends, nothing may happen
        w = g.get(g.state.winner)
        before = self.snapshot()
        self.call("post-move", g.move(w.id, "n"), False, before)
        self.call("post-upgrade", g.upgrade(w.id), False, before)
        self.call("post-nextday", g.next_day(), False, before)
        self.call("post-say", g.say(w.id, "gg"), False, before)
        over = [e for e in g.state.log if e["kind"] == "GAME_OVER"]
        if len(over) != 1 or w.owner_name not in over[0]["text"]:
            self.fail("missing or wrong GAME_OVER announcement")
        elims = [e for e in g.state.log if e["kind"] == "ELIMINATION"]
        if len(elims) != n - 1:
            self.fail(f"{len(elims)} eliminations logged for {n} players")
        jury_ap = sum(int(part.split("(+")[1].split(" ")[0])
                      for e in g.state.log if e["kind"] == "JURY_RESULT"
                      for part in e["text"].split("; ") if "(+" in part)
        self.stats["~jury_ap_awarded"] += jury_ap
        self.stats["~history_records"] += len(self.records)
        deaths = Counter(p.eliminated_on_day for p in g.state.players.values() if not p.alive)
        return {"players": n, "days": g.state.day, "winner": w.state_name,
                "upgrades": w.range_upgrades, "stats": self.stats,
                "first_death": min(deaths), "day1_kills": deaths.get(1, 0),
                "worst_day": max(deaths.values())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--style", choices=["mixed", "aggressive", "alliance"], default="mixed",
                    help="mixed: varied play, sometimes saving AP; aggressive: always attack or chase; "
                         "alliance: blocs daisy-chain AP into one attacker, with occasional betrayal")
    ap.add_argument("--players", type=int, default=None, help="fixed player count")
    ap.add_argument("--jury", choices=["random", "bloc"], default="random",
                    help="random: each juror votes at random; bloc: jurors coordinate on one state")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a game setting, e.g. --set daily_ap=2 --set starting_hp=2")
    args = ap.parse_args()
    for item in args.set:
        key, value = item.split("=", 1)
        SETTINGS_OVERRIDES[key] = int(value)
    rng = random.Random(args.seed)
    totals = Counter()
    lengths: dict[int, list[int]] = {}
    early: list[dict] = []
    failures = 0
    for i in range(1, args.games + 1):
        n = args.players or rng.choice([2, 3, 5, 8, 10, 12, 16, 16, 20])
        try:
            r = Sim(i, n, random.Random(rng.random()), args.verbose, args.style, args.jury).run()
        except InvariantError as e:
            failures += 1
            print(f"Game {i:>2}: FAILED  {e}")
            continue
        totals.update(r["stats"])
        lengths.setdefault(r["players"], []).append(r["days"])
        early.append(r)
        print(f"Game {i:>2}: {r['players']:>2} players, {r['days']:>3} days, winner {r['winner']} "
              f"(range upgrades: {r['upgrades']})")
    print("\nAction counts across all games:")
    for k in sorted(totals):
        print(f"  {k:<28} {totals[k]}")
    print("\nGame length (days) by player count:")
    for n in sorted(lengths):
        ds = sorted(lengths[n])
        print(f"  {n:>2} players: avg {sum(ds) / len(ds):5.1f}, min {ds[0]:>3}, max {ds[-1]:>3}  ({len(ds)} games)")
    if early:
        k = len(early)
        print("\nEarly game:")
        print(f"  first death on day:       avg {sum(r['first_death'] for r in early) / k:.1f}, "
              f"earliest {min(r['first_death'] for r in early)}")
        print(f"  games with a day-1 kill:  {sum(1 for r in early if r['day1_kills']) / k:.0%} "
              f"(most day-1 kills in one game: {max(r['day1_kills'] for r in early)})")
        print(f"  deadliest single day:     avg {sum(r['worst_day'] for r in early) / k:.1f} kills, "
              f"max {max(r['worst_day'] for r in early)}")
    print(f"\n{args.games - failures}/{args.games} games passed all invariant checks.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
