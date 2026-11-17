# Statecraft — Implementation Plan

**Based on:** `Statecraft-Initial-Doc.md` v0.5
**Created:** 2026-11-17

This plan turns the design spec into a build order. It keeps the spec's approach (plain Python, JSON storage, discord.py, Pillow) and adds the structure needed to keep game rules testable without Discord.

---

## 1. Architectural Decisions

### 1.1 Three layers, with dependencies in one direction

```
discord_bot.py / cli.py     ← interfaces (talk to users)
        │
        ▼
game.py + schedule.py       ← rules engine (pure Python, no Discord, no I/O except via store)
        │
        ▼
store.py                    ← JSON load/save (atomic)
```

- **The rules engine never imports discord.py.** Every action takes the acting player's ID and a `now` timestamp, and returns a `Result`.
- A `Result` holds `ok`, an error message if rejected, and a list of **Events** describing what happened.
- Each Event has a type (`MOVE`, `ATTACK`, `ELIMINATION`, `TRANSFER`, `INFO_PURCHASE`, `DAILY_AP`, `JURY_RESULT`, `GAME_OVER`, …), public text, private text addressed to specific players, and a ping level (`mandatory`, `opt_in`, `none`).
- The CLI prints events. The Discord bot routes them to the right channels and roles. Because the bot only routes what the engine returns, we avoid writing two copies of the game rules.

### 1.2 Time is injected, never read directly

- Every engine function takes `now: datetime`, a timezone-aware value in `America/Chicago`.
- The Discord bot passes the real time. The CLI and tests pass simulated time.
- The schedule times (2 PM and 8 PM), the 15-minute cooldown, and the daily AP amount live in a `settings` block in `game.json`. A playtest can then run a compressed schedule, for example a 10-minute window every hour.

### 1.3 The scheduler catches up after restarts

- A background loop runs about every 30 seconds and asks the engine, "Which scheduled events are due as of `now`?"
- The engine compares `now` against persisted markers (`last_daily_tick`, `last_window_close`). Whatever is due runs **once**, and the marker is saved in the same write.
- If the bot was offline at 2:00 PM, the tick runs as soon as the bot comes back. Duplicate AP awards cannot happen.

### 1.4 Concurrency and persistence

- All state-changing operations go through one `asyncio.Lock` in the bot. With a single-process asyncio bot, that fully serializes writes.
- Game state stays in memory. `store.save()` writes `game.json.tmp`, then calls `os.replace()` to swap it in atomically.
- `store.load()` validates the schema version and fails loudly if something is wrong, because admins will hand-edit the JSON.
- Keep a rolling backup (`game.json.bak`) on each daily tick as cheap insurance against bad manual edits.

### 1.5 Delivering the Information Market without permission juggling

The spec allows "permission-isolated records." The simplest version:

- On purchase, the bot posts **today's** transfer log (sender, receiver, amount) to the purchaser's private console.
- Until 8 PM, the bot also forwards each new transfer to every console with active access.
- There is no shared `#info` channel, so there is no message history to leak, and nothing to revoke at 8 PM: the forwarding simply stops.

---

## 2. Data Model (`game.json`)

This extends the spec's example. Fields marked ★ are new.

```json
{
  "schema_version": 1,                          // ★
  "phase": "registration",                      // ★ registration | active | ended
  "day": 0,
  "board_size": 16,
  "settings": {                                 // ★ configurable for testing
    "timezone": "America/Chicago",
    "window_open": "14:00",
    "window_close": "20:00",
    "vote_lock": "13:55",
    "cooldown_minutes": 15,
    "daily_ap": 1,
    "starting_hp": 3,
    "base_range": 2,
    "upgrade_cost": 2,
    "votes_per_ap": 3
  },
  "last_daily_tick": null,                      // ★ ISO date of last 2 PM processing
  "last_window_close": null,                    // ★ ISO date of last 8 PM processing
  "players": {
    "123456789": {
      "state_name": "The Republic of Aldren",
      "marker": "A",                            // ★ short board label
      "console_channel_id": "111111111",
      "x": 3, "y": 5,
      "hp": 3, "ap": 0, "range_upgrades": 0,
      "alive": true,
      "eliminated_on_day": null,                // ★
      "last_action": null,                      // ISO timestamp → cooldown
      "notify_minor": false                     // ★ opt-in pings
    }
  },
  "jury_votes": { "juror_id": "target_id" },    // reset at each tally
  "info_access": { "player_id": "expires_iso" },// ★ dict instead of list
  "transfers_today": [                          // ★ feeds the info market
    { "time": "...", "from": "id", "to": "id", "amount": 2 }
  ],
  "events": [ ]                                 // ★ append-only audit log
}
```

Coordinates are stored 0-indexed. They display chess-style (columns `A–P`, rows `1–16`) so commands and the board use the same labels.

---

## 3. Module Layout

```text
statecraft/
├── main.py            # entry point: `python main.py bot` or `python main.py cli`
├── config.py          # loads .env (DISCORD_TOKEN, GUILD_ID, paths)
├── models.py          # dataclasses: Player, GameState, Event, Result
├── store.py           # JSON load/save, atomic replace, backup, schema check
├── game.py            # all rules: register, start, move, attack, upgrade,
│                      #   transfer, buy_info, vote, end
├── schedule.py        # window open/closed, due events, daily tick, 8 PM close
├── geometry.py        # coordinates, queen-line range checks, adjacency
├── render_text.py     # ASCII board + legend (CLI)
├── render_image.py    # Pillow board.png
├── cli.py             # local multi-player simulator with a fake clock
├── discord_bot.py     # bot setup, slash commands, event routing
├── discord_setup.py   # channel, role, and webhook creation and permissions
├── messaging.py       # diplomacy / private / anonymous relay
├── tests/
│   ├── test_geometry.py
│   ├── test_actions.py
│   ├── test_schedule.py
│   ├── test_jury.py
│   └── test_store.py
├── requirements.txt   # discord.py, Pillow, tzdata, python-dotenv, pytest
├── .env.example
└── game.json          # created at runtime (gitignored)
```

> **Windows note:** `zoneinfo` has no timezone database on Windows by itself. Add the `tzdata` package to the requirements.

---

## 4. Build Phases

### Phase 0 — Project Setup (small)
- [ ] Initialize git, add `.gitignore` (`.env`, `game.json*`, `board.png`, `__pycache__`, `.venv`)
- [ ] Create a venv and `requirements.txt`
- [ ] Set up pytest and confirm that an empty test passes
- [ ] Create a Discord application and bot token, plus a **private test server**

### Phase 1 — Rules Engine and CLI (the core of the project)

> **Status (2026-11-17):** a command-line prototype is built (`play.bat`, or `py -m statecraft`) and 21 tests pass. It has no clock: the action window, cooldowns, and vote lock are deferred until the Discord migration, and `nextday` advances the day by hand. Messaging (`say`, `dm`, `anon`) is also deferred, because it's relay-only. The items below describe the full target.

**1a. Models and storage**
- [ ] Dataclasses with `to_dict` / `from_dict`
- [ ] Atomic save, load, backup, and schema-version check

**1b. Geometry**
- [ ] `range_of(player) = base_range + range_upgrades`
- [ ] `in_range(attacker, target)`: `abs(dx) + abs(dy) <= range`. A straight step costs 1 and a diagonal step costs 2, so diagonal reach is `range // 2`. Nothing blocks range.

  | Upgrades | Range | Cardinal reach | Diagonal reach |
  |---:|---:|---:|---:|
  | 0 | 2 | 2 | 1 |
  | 1 | 3 | 3 | 1 |
  | 2 | 4 | 4 | 2 |
  | 3 | 5 | 5 | 2 |
  | 4 | 6 | 6 | 3 |

  Range 3, for example (`@` = state, `X` = in range):
  ```
  . . . X . . .
  . . X X X . .
  . X X X X X .
  X X X @ X X X
  . X X X X X .
  . . X X X . .
  . . . X . . .
  ```
- [ ] `can_move(from, to)`: the move is to an adjacent square (including diagonals), on the board, and unoccupied; living and dead states both block

**1c. Game lifecycle**
- [ ] `register(player_id, state_name)`: names must be unique (case-insensitive) and 1–40 characters; registration is only allowed during the registration phase
- [ ] `start(now)`: N = player count, random unique placement, assign markers, phase = active, day = 1. Every state starts with **0 AP**; the first AP arrives at the next 2 PM tick, whenever the game starts.
- [ ] `end()` and `reset()`

**1d. Actions.** Every action follows the same validation chain:
`game active → player alive → window open → cooldown elapsed → AP sufficient → action-specific checks → apply → stamp last_action → save → events`
- [ ] Move (1 AP)
- [ ] Attack (1 AP): the target must be alive and in range. Handle elimination, and check for victory.
- [ ] Upgrade range (2 AP)
- [ ] Transfer AP (amount sent): the target must be alive and in range, and the amount must be ≥ 1. Record it in `transfers_today`.
- [ ] Buy info (1 AP): set `info_access[player]` to expire at today's 8 PM, and emit a public announcement
- [ ] Rejected actions change **nothing**, and that includes the cooldown

**1e. Schedule**
- [ ] `is_window_open(now)` and `next_window_open(now)`
- [ ] `run_due_events(now)`, in this order:
  - **2 PM:** tally jury votes (`votes // 3` AP each), announce the totals, clear the votes, give daily AP to living states, increment the day, clear `transfers_today`
  - **8 PM:** expire info access, open jury voting
- [ ] `vote(juror_id, target_id, now)`: only eliminated players can vote, from 8:00 PM until votes lock at **1:55 PM** (`vote_lock` setting), and only for a living target. Jurors may change their vote until the lock; the latest vote counts. The tally runs at 2:00 PM.

**1f. CLI simulator**
- [ ] REPL commands: `add <id> <name>`, `start`, `as <id> move NE`, `as <id> attack <state>`, `time set …`, `time +15m`, `tick`, `board`, `status <id>`, `dump`
- [ ] ASCII board with grid labels and a legend

**1g. Tests (written alongside 1b–1e)**
- [ ] Range edge cases at every upgrade level, including board edges, knight-offset squares (in range at range 3, not at 2), and `(2,2)` (in range only from range 4)
- [ ] Moves blocked by edges, living states, and dead states
- [ ] Insufficient AP, cooldown boundary (14:59 vs 15:00), window boundaries (1:59 PM, 2:00 PM, 7:59 PM, 8:00 PM)
- [ ] Jury math, and votes from living players rejected
- [ ] Daily tick runs exactly once across simulated restarts (save → reload → tick again)
- [ ] DST transition days (e.g., the first Sunday in November)
- [ ] Elimination → victory when one state remains

**Phase 1 is done when** a full simulated game can be played in the CLI from registration to victory, and all tests pass.

### Phase 2 — Graphical Board
- [ ] Pillow grid with A–P / 1–16 labels
- [ ] Markers colored by HP; dead states shown greyed out with an ✕
- [ ] Legend panel listing marker, state name, HP, and range
- [ ] Optional: a highlighted range overlay for one state
- [ ] `render_image.render(state) -> bytes`, so nothing needs to be written to disk unless wanted
- [ ] Never include AP or owner identity in the image (add a test that checks the inputs)

### Phase 3 — Discord Integration

**3a. Bot skeleton**
- [ ] `discord.py` client with slash commands synced to one guild
- [ ] A single `GameService` that owns the in-memory state, the lock, and the store
- [ ] An event router that maps each engine Event to its channel and ping level

**3b. Server setup (`/admin setup`)**
- [ ] Create the roles `Statecraft-Players`, `Statecraft-Updates`, and `Statecraft-Jury`
- [ ] Create the channels `#game-board`, `#action-log`, `#diplomacy`, `#anonymous-chat`, and `#jury`, with the right permission overwrites (players can read but not post in the bot-mediated channels)
- [ ] Create a `#diplomacy` webhook (one webhook, with the username overridden per message)
- [ ] Save the channel and role IDs to `game.json`

**3c. Registration**
- [ ] `/join name:<state name>`: the bot replies **ephemerally** so the name is never shown publicly. It adds the Players role and creates a private console with a random, non-identifying name (e.g., `console-7f3a`).
- [ ] `/admin start`: place the states, post the board, and announce the state names (without owners)

**3d. Console commands** (rejected outside the player's own console)
- [ ] `/move direction`, `/attack state`, `/upgrade`, `/transfer state amount`, `/buyinfo`
- [ ] `/status`: private AP, cooldown remaining, position, range
- [ ] `/say message` → `#diplomacy` via the webhook, shown as the state name
- [ ] `/dm state message` → the recipient's console, labeled with the sending state
- [ ] `/anon message` → `#anonymous-chat`, posted as the bot
- [ ] `/vote state` (jurors only, off-hours, private confirmation)
- [ ] `/notify on|off` → toggles the Updates role
- [ ] Autocomplete living state names for every command that takes a target

**3e. Event routing**
- [ ] Attack or elimination → `#action-log` with a `@Statecraft-Players` ping (one combined message if the attack eliminates)
- [ ] Move or upgrade → `#action-log` with a `@Statecraft-Updates` ping
- [ ] Info purchase → `#action-log`; the transfer log goes to the buyer's console
- [ ] Transfer → private notices to both consoles, plus forwarding to active info buyers
- [ ] Elimination → reveal the owner, grant the Jury role, remove the Players-only perms for gameplay
- [ ] Board message edited in place after every public change

**3f. Scheduler**
- [ ] A `discord.ext.tasks` loop every 30 seconds that runs `run_due_events(now)` under the lock and routes its events

**Phase 3 is done when** a 3–4 person test game runs on the private server with a compressed schedule.

### Phase 4 — Playtest and Hardening
- [ ] Compressed-schedule playtest with friends or alt accounts
- [ ] Kill the bot mid-day and at 2 PM, then confirm catch-up and no duplicate awards
- [ ] Two players acting at the same moment → both are processed correctly
- [ ] An identity-leak review: check every message the bot posts publicly for owner info
- [ ] Admin hand-edits `game.json` → `/admin reload` picks up the change
- [ ] Run one full real-time game

---

## 5. Identity-Masking Checklist

These are the ways a living state's owner could be exposed. Each one needs to be handled.

| Risk | Mitigation |
|---|---|
| Slash command invoked in a public channel shows "User used /attack" | Gameplay commands are rejected outside consoles; `/join` replies ephemerally |
| Console channel name reveals the player | Random names; only the owner and admins can see the channel |
| Bot replies mention the user | Never use `interaction.user.mention` in non-ephemeral output |
| Diplomacy posted by the user directly | Players lack Send Messages permission in `#diplomacy` and `#anonymous-chat` |
| Role membership | The Players and Updates roles are shared by everyone, so they reveal nothing per state |
| Timing correlation (someone acts right when they come online) | Can't be prevented. Note this to players as part of the meta-game. |

---

## 6. Rules Decisions

**Decided (2026-11-17):**
- States block **movement only**. Nothing blocks attacks or transfers.
- Targets don't need to be on a straight or diagonal line. Any state inside the range area can be hit.
- **Range is one number.** It starts at 2, and each upgrade (2 AP) adds 1. A target is in range if `|dx| + |dy| <= range`, meaning a diagonal step costs 2. Diagonal reach is `range // 2`, which goes 1, 1, 2, 2, 3. **This replaces the range table in spec §4.3,** where diagonal range was 1, 2, 3, 4.
- Jurors can change their vote. Votes lock at 1:55 PM and are tallied at 2:00 PM.
- All states start with 0 AP, no matter when the game starts.

**Still open (proposed defaults):**

| # | Question | Proposed default |
|---|---|---|
| 1 | Can eliminated players post in `#anonymous-chat`? | **Yes.** It's unattributed anyway. |
| 2 | Can eliminated players use `/say` in `#diplomacy`? | **No.** The spec says only living states use it. |
| 3 | Does a player eliminated at 7 PM vote that same night? | **Yes.** |
| 4 | Can a state transfer AP to itself, or attack itself? | **No.** |
| 5 | Does the info market show transfers made **earlier today**, before the purchase? | **Yes.** It shows all of the current active day. |
| 6 | Is there a minimum player count? | 2 for testing, about 8 recommended for real games |
| 7 | Where will the bot be hosted (always-on PC, VPS, Docker)? | A local Windows process for development. Decide before the real game. |

---

## 7. Suggested Order of Work

1. Phase 0 setup
2. `geometry.py` + tests
3. `models.py` + `store.py` + tests
4. `game.py` actions + tests
5. `schedule.py` + tests
6. `cli.py`, then play a full simulated game
7. `render_image.py`
8. Discord skeleton → setup → registration → actions → relays → scheduler
9. Playtest
