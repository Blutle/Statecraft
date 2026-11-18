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
    "gift_amount": 1,            # AP moved by one Gift (paid by the giver)
    "daily_ap": 1,
    "votes_per_ap": 2,
    "board_size": 0,             # 0 = automatic, using board_scale_pct
    "board_scale_pct": 75,       # automatic side = players * pct // 100 (16 players -> 12x12)
    "timezone": "America/Chicago",
    "window_open": "14:00",      # daily tick (jury tally, then daily AP) and actions open
    "window_close": "20:00",     # actions close, jury voting opens
    "info_expire": "20:15",      # purchased information access fades
    "vote_lock": "13:45",        # jury votes lock until the 14:00 tally
    "cooldown_minutes": 15,      # per-player cooldown after every gameplay action
    "pact_found_limit": 3,       # pacts a player may found (ever, per game)
    "pact_member_limit": 6,      # pacts a player may belong to at once (including founded)
    "min_players": 12,           # fewest players needed to start (start force bypasses, min 2)
    "spawn_no_cardinal_touch": 1,  # 1 = no two states start directly N/S/E/W of each other
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
    color: str | None = None         # palette name (statecraft/colours.py), chosen or assigned at start
    last_action: str | None = None   # ISO time of last gameplay action (cooldown)


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
    gifts_today: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)              # public event history
    next_player_num: int = 1
    pacts: dict[str, dict] = field(default_factory=dict)      # pact id -> pact (see Game.create_pact)
    next_pact_num: int = 1
    winners: list[str] = field(default_factory=list)
    clock_marker: str | None = None  # scheduler: time up to which events have run          # last survivor, or admin-declared

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")
        data = dict(data)
        data["players"] = {pid: Player(**p) for pid, p in data.get("players", {}).items()}
        if "transfers_today" in data:                       # renamed to gifts
            data["gifts_today"] = data.pop("transfers_today")
        if "winner" in data:                                # single winner -> list
            w = data.pop("winner")
            data["winners"] = [w] if w else []
        data.pop("draw_votes", None)                        # removed draw feature
        data.pop("drawn", None)
        settings = {k: v for k, v in data.get("settings", {}).items() if k in DEFAULT_SETTINGS}
        data["settings"] = {**DEFAULT_SETTINGS, **settings}
        return cls(**data)


@dataclass
class Event:
    kind: str                                         # MOVE, ATTACK, ELIMINATION, ...
    public: str | None = None                         # text for public channels
    private: dict[str, str] = field(default_factory=dict)  # player id -> text
    ping: str = "none"                                # mandatory | opt_in | none
    data: dict = field(default_factory=dict)          # structured details for the interface


@dataclass
class Result:
    ok: bool
    message: str = ""                                 # feedback to the acting user
    events: list[Event] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str) -> "Result":
        return cls(ok=False, message=message)
