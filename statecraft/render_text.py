"""Plain-text board and status rendering for the command line (ASCII only)."""

from __future__ import annotations

from . import geometry
from .game import Game
from .models import Player


def board(game: Game, viewer: Player | None = None) -> str:
    """Draw the board. With a viewer, squares in their range are marked ':'."""
    st = game.state
    n = st.board_size
    if n == 0:
        return "The board does not exist until the game starts."
    rng = game.range_of(viewer) if viewer and viewer.alive else None

    lines = ["    " + "".join(f"{geometry.column_label(x):<4}" for x in range(n))]
    for y in range(n):
        row = f"{y + 1:>2}  "
        for x in range(n):
            occ = game.occupant(x, y)
            if occ:
                cell = occ.marker if occ.alive else occ.marker.lower()
            elif rng is not None and geometry.in_range(viewer.x, viewer.y, x, y, rng):
                cell = ":"
            else:
                cell = "."
            row += f"{cell:<4}"
        lines.append(row.rstrip())
    if rng is not None:
        lines.append(f"\n':' = within range {rng} of {viewer.state_name} [{viewer.marker}]")
    lines.append("")
    lines.append(legend(game, viewer))
    return "\n".join(lines)


def legend(game: Game, viewer: Player | None = None) -> str:
    """Public information only: no AP, and no owners of living states."""
    rows = [f"Day {game.state.day}  |  {len(game.alive_players())} states remaining", ""]
    rows.append(f"  {'Abv':<4} {'State':<{_w(game)}} {'Pos':<4} {'HP':<3} {'Rng':<4} Status")
    for p in game.state.players.values():
        if p.alive:
            status = "active"
            if p.id in game.state.winners:
                status = "WINNER"
            if viewer and viewer.alive and p.id != viewer.id and game.in_range(viewer, p):
                status += "  (in your range)"
        else:
            status = f"destroyed day {p.eliminated_on_day} (was {p.owner_name})"
        mk = p.marker if p.alive else p.marker.lower()
        rows.append(f"  {mk:<4} {p.state_name:<{_w(game)}} {game.pos(p):<4} {p.hp:<3} {game.range_of(p):<4} {status}")
    return "\n".join(rows)


def _w(game: Game) -> int:
    return max([len(p.state_name) for p in game.state.players.values()] + [5])


def status(game: Game, p: Player) -> str:
    """Private view for the state's owner."""
    lines = [
        f"{p.state_name} [{p.marker}]  (player: {p.owner_name}, id {p.id})",
        f"  Status:   {'active' if p.alive else 'destroyed (Jury member)'}",
        f"  Position: {game.pos(p)}",
        f"  HP:       {p.hp}",
        f"  AP:       {p.ap}   (private)",
        f"  Range:    {game.range_of(p)}  (upgrades: {p.range_upgrades})",
    ]
    if p.alive:
        lines.append(f"  Info:     {'purchased for today' if p.id in game.state.info_access else 'not purchased'}")
    else:
        vote = game.state.jury_votes.get(p.id)
        lines.append(f"  Vote:     {game.get(vote).state_name if vote else 'none cast'}")
    return "\n".join(lines)
