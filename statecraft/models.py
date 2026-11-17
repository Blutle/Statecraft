"""Data structures for Statecraft game state, events, and action results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1

DEFAULT_SETTINGS = {
    "starting_hp": 3,
    "base_range": 2,
    "move_cost": 1,
    "attack_cost": 1,
    "upgrade_cost": 2,
    "info_cost": 1,
    "daily_ap": 1,
    "votes_per_ap": 3,
    "board_size": 0,             # 0 = automatic (N x N for N players)
}


@dataclass
class Player:
    id: str                      # internal key (Discord user ID later)
    owner_name: str              # hidden until elimination
    state_name: str
    marker: str = ""             # short board label, e.g. "RA"
    x: int | None = None
    y: int | None = None
    hp: int = 3
    ap: int = 0
    range_upgrades: int = 0
    alive: bool = True
    eliminated_on_day: int | None = None


@dataclass
class GameState:
    schema_version: int = SCHEMA_VERSION
    phase: str = "registration"  # registration | active | ended
    day: int = 0
    board_size: int = 0
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    players: dict[str, Player] = field(default_factory=dict)
    jury_votes: dict[str, str] = field(default_factory=dict)  # juror id -> target id
    jury_history: list[dict] = field(default_factory=list)    # past ballots (admin-only)
    info_access: list[str] = field(default_factory=list)      # ids with access today
    transfers_today: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)              # public event history
    next_player_num: int = 1
    winner: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")
        data = dict(data)
        data["players"] = {pid: Player(**p) for pid, p in data.get("players", {}).items()}
        data["settings"] = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        return cls(**data)


@dataclass
class Event:
    kind: str                                         # MOVE, ATTACK, ELIMINATION, ...
    public: str | None = None                         # text for public channels
    private: dict[str, str] = field(default_factory=dict)  # player id -> text
    ping: str = "none"                                # mandatory | opt_in | none


@dataclass
class Result:
    ok: bool
    message: str = ""                                 # feedback to the acting user
    events: list[Event] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str) -> "Result":
        return cls(ok=False, message=message)
