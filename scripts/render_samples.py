"""Render a folder of board images from simulated games, to eyeball the design.

Games are played by the simulator's alliance bots, and snapshots are taken on
random days, so positions, deaths, damage, and upgrades come from play.

Usage:  py scripts/render_samples.py [--count 50] [--out board_samples] [--seed 1]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from statecraft import render_image  # noqa: E402
import simulate  # noqa: E402

PREFIX = ["The Republic of", "The Kingdom of", "The Empire of", "The Free Cities of", "The Duchy of",
          "The Iron", "The Union of", "The League of", "The March of", "The Holy", "The Isles of", ""]
CORE = ["Aldren", "Veyra", "Westhaven", "Kesh", "Orlan", "Dusk", "Varn", "Solmere", "Tessar", "Ember",
        "Greywater", "Norland", "Quill", "Ashen", "Brin", "Corvo", "Lyre", "Halcyon", "Morrow", "Thorn",
        "Calder", "Ostia", "Pellar", "Rook", "Sable", "Umber", "Vantor", "Wrenfield", "Yarrow", "Zephyr",
        "Dominion", "Concord", "Meridian", "Hollow", "Saltmarch", "Crown", "Frost", "Highmoor"]


def state_names(rng: random.Random, n: int) -> list[str]:
    names = set()
    while len(names) < n:
        pre, core = rng.choice(PREFIX), rng.choice(CORE)
        name = f"{pre} {core}".strip() if rng.random() < 0.7 else core
        names.add(name[:40])
    return sorted(names)


def build_game(rng: random.Random, n: int) -> simulate.Sim:
    """A simulator game with realistic names; about a third pick their own abbreviation."""
    sim = simulate.Sim.__new__(simulate.Sim)
    sim.no, sim.rng, sim.verbose, sim.style, sim.jury = 0, rng, False, "alliance", "bloc"
    sim.bloc_fav, sim.records, sim.calls = None, [], 0
    from collections import Counter
    from statecraft.game import Game
    sim.stats, sim.expected_total_ap = Counter(), 0
    sim.g = Game(rng=random.Random(rng.random()), recorder=sim.records.append)
    for i, name in enumerate(state_names(rng, n)):
        abbr = None
        if rng.random() < 0.35:
            letters = [c for c in name.upper() if c.isalpha()]
            abbr = "".join(rng.sample(letters, 3)) if len(letters) >= 3 else None
        res = sim.g.register(f"Player{i + 1:02d}", name, pid=str(1000 + i), abbreviation=abbr)
        if not res.ok:
            sim.g.register(f"Player{i + 1:02d}", name, pid=str(1000 + i))
        sim.calls += 1
    sim.g.start(force=n < 12)
    sim.calls += 1
    return sim


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--out", default=str(ROOT / "board_samples"))
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    rng = random.Random(args.seed)

    made = 0
    game_no = 0
    while made < args.count:
        game_no += 1
        n = rng.choice([12, 13, 14, 16, 16, 18, 20])
        sim = build_game(random.Random(rng.random()), n)
        g = sim.g
        shots = sorted(rng.sample(range(1, 14), k=3))
        declare = rng.random() < 0.15
        day = 0
        while g.state.phase == "active" and made < args.count:
            sim.next_day()
            sim.dead_actions()
            if g.state.day == 1:
                sim.form_alliances()
            sim.alliance_day()
            day += 1
            alive = g.alive_players()
            if declare and len(alive) <= 3 and g.state.phase == "active":
                g.declare_winners([p.id for p in alive[:2]])
            if day in shots or g.state.phase == "ended":
                made += render_shots(g, rng, out, made, n, game_no)
            if day > 30:
                break
    print(f"Wrote {made} images to {out}")


def render_shots(g, rng: random.Random, out: Path, made: int, n: int, game_no: int) -> int:
    """1-2 images of the current position from different points of view."""
    alive = g.alive_players()
    dead = [p for p in g.state.players.values() if not p.alive]
    views = []
    if g.state.phase == "ended":
        views.append(("final", None, None))
    else:
        views.append(("public", None, None))
        me = rng.choice(alive)
        other = rng.choice(alive)
        views.append(rng.choice([
            (f"player-{me.marker}", me.id, me.id),
            (f"player-{me.marker}_range-{other.marker}", other.id, me.id),
            *([(f"juror-{dead[0].marker}_range-{other.marker}", other.id, dead[0].id)] if dead else []),
        ]))
    count = 0
    for label, focus, viewer in views[: 2]:
        idx = made + count + 1
        name = f"{idx:02d}_game{game_no}_{n}p_day{g.state.day}_{len(alive)}alive_{label}.png"
        (out / name).write_bytes(render_image.render(g, focus=focus, viewer=viewer))
        count += 1
    return count


if __name__ == "__main__":
    main()
