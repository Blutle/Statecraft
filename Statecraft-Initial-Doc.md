# Statecraft
**Game Design & Technical Specification**

**Version:** 0.5  
**Status:** Pre-development  
**Platform:** Discord  
**Target Players:** 16  
**Target Game Duration:** Approximately 7 days  
**Gameplay Reference:** Luke Muscat's *The Prototype that was Banned from Halfbrick* (GDC 2013)

---

## 1. Project Overview

Statecraft is an asynchronous multiplayer strategy game focused on diplomacy, alliances, resource management, information asymmetry, and betrayal.

Each player creates and names their own sovereign state. These states compete for survival on a shared grid, spending Action Points (AP) to move, attack, increase their range, and transfer resources to other states.

Although only one state can ultimately survive, players are free to establish alliances, negotiate agreements, exchange resources, and betray one another.

Players operate behind masked identities. Each player's state is publicly known, but the Discord account controlling it remains secret until elimination. Public and private diplomatic communication is facilitated by the bot.

Statecraft is mechanically based on Halfbrick's Tank Turn Tactics, reimagined with the political and diplomatic atmosphere of grand strategy and social deduction games.

The Discord bot acts as the referee, managing attributes, validating actions, displaying the board, relaying communication, and distributing information.

### Design Principles

- Simple mechanics with complex player-driven diplomacy.
- Alliances emerge through player interaction rather than formal game systems.
- Information is a strategic resource.
- Players communicate through fictional state identities.
- No public communication is required to participate.
- All routine gameplay rules are enforced automatically.
- Exceptional circumstances can be handled by a human administrator.
- The technical implementation remains minimal.

---

## 2. State Creation and Identity

Each participating player creates their own sovereign state.

During registration, players choose a name for their state.

Example names:
- The Republic of Aldren
- Veyran Empire
- The Free Cities
- Westhaven
- The Iron Dominion

States are not restricted to any government type, naming convention, or fictional setting.

### 2.1 State Attributes

| Attribute | Starting Value | Visibility |
|---|---|---|
| Name | Player-selected | Public |
| Owner | Discord player | Hidden until elimination |
| Position | Randomly assigned | Public |
| HP | 3 | Public |
| Cardinal attack range | 2 | Public |
| Diagonal attack range | 1 | Public |
| AP | 0 | Private |
| Status | Active | Public |
| Last action | None | Tracked by bot |

Each player controls exactly one state.

State names are cosmetic and do not confer gameplay advantages.

### 2.2 Masked Identities

The connection between a player's Discord account and their state is secret while that state remains alive.

- Players know which Discord users are participating.
- Players do not automatically know which state belongs to which user.
- Public game actions identify states, not Discord users.
- Public diplomatic messages are published under state names.
- Private diplomatic messages identify the sending state, not its owner.
- The bot maintains the state-to-player mapping internally.

The server administrator has access to the underlying identities.

The system provides identity masking within the game, not guaranteed anonymity against Discord itself, server administrators, or information voluntarily shared outside the game.

### 2.3 Identity Reveal

A player's identity is revealed when their state is eliminated.

Upon elimination:

1. The bot publicly announces the destroyed state.
2. The announcement identifies the Discord account that controlled it.
3. The player receives the Jury role.
4. The player gains access to the private Jury channel.
5. The player's state remains on the board as an obstacle.
6. The player loses access to normal gameplay actions.

Other surviving states remain masked.

The final surviving player's identity is revealed when the game concludes.

---

## 3. Board Configuration

### 3.1 Board Dimensions

Board dimensions scale directly with player count.

**Board size = N × N**, where N is the number of players.

| Players | Board | Total Squares |
|---:|---|---:|
| 8 | 8×8 | 64 |
| 10 | 10×10 | 100 |
| 12 | 12×12 | 144 |
| 14 | 14×14 | 196 |
| **16** | **16×16** | **256** |
| 18 | 18×18 | 324 |
| 20 | 20×20 | 400 |

The initial target is 16 players on a 16×16 board.

Board dimensions are established when the game begins and remain constant throughout that game.

### 3.2 Starting Placement

- Each state is randomly assigned an unoccupied square.
- No two states may occupy the same square.
- Starting positions are publicly visible.
- Additional spawn-spacing restrictions are not currently defined.

### 3.3 Movement

- Movement costs 1 AP.
- A state moves exactly one square per action.
- Movement is permitted horizontally, vertically, or diagonally.
- Movement outside the board is prohibited.
- States cannot move into occupied squares.
- Eliminated states remain on the board and permanently occupy their final squares.

---

## 4. Game Rules

### 4.1 Action Points

- Every state begins with 0 AP.
- Each surviving state receives 1 AP daily at 2:00 PM Central.
- AP accumulates without expiration.
- Jury awards can provide additional AP.
- Players cannot spend more AP than they possess.
- Current AP balances are private.

### 4.2 Player Actions

| Action | AP Cost | Effect |
|---|---:|---|
| Move | 1 | Move exactly one square |
| Attack | 1 | Deal 1 damage to another state within range |
| Upgrade range | 2 | Increase cardinal and diagonal range by 1 |
| Transfer AP | Amount sent | Give AP to another state within range |
| Purchase information | 1 | Access private information records for the current active day |

### 4.3 Attack Range

Attacks follow the eight directions available to a chess queen, with different cardinal and diagonal distances.

Starting range:

- Cardinal: 2 squares
- Diagonal: 1 square

Each range upgrade costs 2 AP and increases both ranges by 1.

| Upgrades | Cardinal Range | Diagonal Range |
|---:|---:|---:|
| 0 | 2 | 1 |
| 1 | 3 | 2 |
| 2 | 4 | 3 |
| 3 | 5 | 4 |
| 4 | 6 | 5 |

Attacks within range automatically deal 1 damage. No accuracy rolls or defensive mechanics exist.

### 4.4 Elimination

- States begin with 3 HP.
- At 0 HP, a state is eliminated.
- Eliminated states cannot perform ordinary gameplay actions.
- Their markers remain on the board as permanent obstacles to movement.
- Their controlling player's identity is publicly revealed.
- Eliminated players join the Jury.
- The attacker does not inherit the eliminated state's remaining AP.
- The last surviving state wins.

---

## 5. Daily Schedule

All scheduling uses `America/Chicago`, automatically accounting for daylight saving time.

| Time (Central) | Event |
|---|---|
| 2:00 PM | Jury voting closes |
| 2:00 PM | Jury votes are tallied and bonus AP awarded |
| 2:00 PM | Surviving states receive 1 daily AP |
| 2:00 PM | Active gameplay opens |
| 8:00 PM | Active gameplay closes |
| 8:00 PM | Purchased information access expires |
| 8:00 PM | Jury voting opens |
| 8:00 PM–2:00 PM | Jury voting period |

The active gameplay window lasts six hours daily, from 2:00 PM to 8:00 PM Central.

Gameplay actions cannot be performed outside the active window.

Communication is unrestricted at all hours.

### 5.1 Action Cooldown

Every gameplay action incurs a 15-minute cooldown for the player performing it.

- Cooldowns are tracked independently for each player.
- All gameplay actions share the same cooldown.
- Purchasing information access triggers the cooldown.
- Invalid or rejected actions do not trigger the cooldown.
- Cooldown timestamps persist through bot restarts.
- Ordinary communication and anonymous posting do not trigger cooldowns.
- Jury voting is governed by its separate off-hours voting schedule.

---

## 6. Jury System

When a state is eliminated, its owner becomes a Jury member.

Jury members influence surviving states through the distribution of bonus AP.

### 6.1 Voting Rules

- Each eliminated state receives exactly one vote per daily voting period.
- Jury voting is available exclusively during off-hours, from 8:00 PM until 2:00 PM the following day.
- Votes must target surviving states.
- Individual votes are secret.
- Voting closes at 2:00 PM.
- Votes are tallied before normal daily AP distribution.
- Every complete group of 3 votes awards the recipient 1 AP.
- Fractional awards are discarded.
- Votes reset after each daily tally.

**Formula:** `Bonus AP = votes_received // 3`

| Votes Received | AP Awarded |
|---:|---:|
| 0–2 | 0 |
| 3–5 | 1 |
| 6–8 | 2 |
| 9–11 | 3 |
| 12–14 | 4 |

### 6.2 Jury Privacy

- Votes are submitted privately to the bot.
- Individual ballots are never published.
- Fellow jurors cannot inspect one another's votes.
- The Jury channel is for discussion, not ballot submission.
- Aggregate vote totals and resulting AP awards are publicly announced at the daily tally.
- No public announcement identifies which juror voted for which state.

### 6.3 Jury Channel

Eliminated players automatically receive access to a private Discord Jury channel.

Their identities are public after elimination, so they may communicate normally in that channel.

Jury communication is unrestricted by time or cooldown.

---

## 7. Information and Visibility

### 7.1 Public Information

Public game information includes:

- State names
- Positions
- HP
- Attack ranges
- Active or eliminated status
- Movement
- Attacks and damage
- Range upgrades
- Information access purchases
- Aggregate Jury voting results
- Identities of eliminated players

Living states' Discord account identities remain hidden.

### 7.2 Private Information

- Each state's current AP balance is visible only to its owner.
- AP transfers are recorded in a restricted information channel.
- Individual Jury ballots are confidential.
- Jury discussion is restricted to eliminated players.
- Private diplomatic messages are visible only to the intended participants, the bot, and administrators with relevant access.
- The mapping between living states and Discord users is not publicly disclosed.

### 7.3 Information Market

Players may spend 1 AP to gain temporary access to private AP transfer records.

Rules:

- Access costs 1 AP.
- The purchase is publicly announced under the purchaser's state name.
- The purchase triggers the normal 15-minute action cooldown.
- Access lasts until 8:00 PM Central on the purchase day.
- AP transfers are recorded with the sending state, receiving state, and amount.
- Purchasing access does not automatically reveal previous days' private information.

The implementation may use separate daily information channels or permission-isolated records.

---

## 8. Discord Server Architecture

### 8.1 Private Player Consoles

Every participating player receives an individual private text channel within the Statecraft Discord server.

These channels serve as the primary interface between players and the game bot.

Each console is accessible only to:

- Its controlling player
- The Statecraft bot
- Authorized server administrators

Other players cannot view the channel or its history.

The console supports:

- Performing gameplay actions
- Viewing private AP balances
- Checking cooldowns and personal statistics
- Sending public diplomatic messages under the player's state identity
- Sending private diplomatic messages to other states
- Receiving private diplomatic messages
- Submitting completely anonymous messages
- Purchasing information access
- Accessing other bot functions

Private consoles eliminate the need to rely on Discord direct messages or a shared write-only command channel.

The bot creates the consoles during game registration or setup.

Console names and permissions must not publicly disclose which player controls which state.

### 8.2 Private Console Communication Flow

The bot receives messages and commands from each player's private console.

Depending on the requested action, it either updates the game state or relays a message to the appropriate destination.

**Public diplomacy:**

`Player Console → Bot → Public Diplomacy Channel`

**Private diplomacy:**

`Sender Console → Bot → Recipient Console`

**Anonymous communication:**

`Player Console → Bot → Anonymous Chat`

The bot controls the identity displayed with each published or relayed message.

Normal public channels can restrict direct player posting to preserve identity masking.

### 8.3 Server Channels

| Channel | Access | Purpose |
|---|---|---|
| `#your-console` | Individual player | Private commands and correspondence |
| `#game-board` | Public | Current map and state attributes |
| `#action-log` | Public | Gameplay actions and elimination announcements |
| `#diplomacy` | Public, bot-mediated posting | Messages from named states |
| `#anonymous-chat` | Public, bot-mediated posting | Completely anonymous messages |
| `#info` | Purchased access | Private AP transfer records |
| `#jury` | Eliminated players | Jury discussion |
| Other channels | As configured | Ordinary community conversation |

Every player sees their own console, the appropriate public channels, and any restricted channels they have permission to access.

### 8.4 Console Lifecycle

- Private consoles are created when players register.
- Consoles remain private throughout the game.
- Eliminated players retain access to their console for permitted bot interactions.
- Normal state actions become unavailable after elimination.
- Historical private diplomatic messages remain restricted.
- Consoles can be archived or deleted when the game concludes, at the administrator's discretion.

---

## 9. Diplomatic Communication

### 9.1 Public Diplomacy

Living states communicate publicly through their fictional state identities.

Players submit messages through their private consoles.

The bot republishes those messages in `#diplomacy`, displaying the sending state's name rather than its owner's Discord account.

Discord webhooks may be used to display state-specific names and avatars.

Messages remain visible to everyone with access to the channel.

Public diplomatic messages do not trigger gameplay cooldowns.

### 9.2 Private Diplomacy

Players can send private messages to other states without learning the recipient's Discord identity.

The bot relays messages between their private consoles.

Example:

1. The Republic of Aldren sends a message addressed to Veyran Empire.
2. The bot identifies Veyran Empire's controlling account.
3. The bot delivers the message to that player's private console.
4. The recipient sees the sender as The Republic of Aldren.
5. The recipient can respond through the same system.

Private diplomacy:

- Is available at all hours.
- Has no cooldown.
- Has no AP cost.
- Does not expose either player's Discord account identity.
- Does not appear in public game logs.
- Is separate from gameplay AP transfers.

The bot may provide reply buttons or commands to simplify ongoing conversations.

### 9.3 Anonymous Chat

Players can anonymously publish messages to a single public Discord channel.

Process:

1. A player submits an anonymous message through their private console.
2. The bot publishes it in `#anonymous-chat`.
3. The bot appears as the author.
4. Neither the player's Discord account nor their state name is displayed.

Anonymous chat rules:

- No forum threads.
- Messages appear chronologically.
- No posting cooldown.
- No message limit imposed by game rules.
- No communication time restrictions.
- No direct gameplay effects.

Anonymous communication remains distinct from state-attributed public diplomacy.

---

## 10. Notifications

Major game events trigger notifications to all participants.

| Event | Notification |
|---|---|
| Attack | Mandatory ping to all participants |
| Elimination | Mandatory ping to all participants |
| Movement | Opt-in ping |
| Range upgrade | Opt-in ping |
| Information purchase | Public announcement; optional ping |
| AP transfer | Restricted information log |
| Daily AP distribution | Public announcement |
| Jury results | Public announcement |

An attack that eliminates a state produces one combined notification.

### Discord Roles

- `@Statecraft-Players` — All participants
- `@Statecraft-Updates` — Players opting into minor notifications
- `@Statecraft-Jury` — Eliminated players

The Jury role is granted upon elimination, coinciding with the public identity reveal.

Roles may expose membership but must not disclose which surviving state belongs to which player.

---

## 11. Technical Stack

Statecraft will be implemented as a lightweight Python application.

| Component | Technology | Purpose |
|---|---|---|
| Language | Python | Application logic |
| Storage | JSON | Persistent game state |
| Image rendering | Pillow | Generate graphical board |
| Discord integration | discord.py | Commands, channels, webhooks, permissions |
| Scheduling | Python | Daily events and cooldowns |
| Timezone handling | zoneinfo | Central Time |
| Hosting | Local process or Docker | Continuous operation |

No dedicated database server, web server, frontend framework, Redis instance, or REST API is required.

### 11.1 Project Structure

```text
statecraft/
│
├── main.py             # Application entry point
├── game.py             # Rules and actions
├── state.py            # JSON persistence
├── board.py            # Board rendering
├── scheduler.py        # Daily scheduling
├── cli.py              # Local test interface
├── discord_bot.py      # Discord integration
├── messaging.py        # Diplomatic and anonymous relay
│
├── game.json           # Persistent state
└── board.png           # Generated board
```

Modules may be consolidated if the initial application is sufficiently small.

### 11.2 Game State

All persistent game data is stored in JSON.

Example:

```json
{
  "day": 1,
  "board_size": 16,
  "last_distribution": null,
  "players": {
    "123456789": {
      "state_name": "The Republic of Aldren",
      "console_channel_id": "111111111",
      "x": 3,
      "y": 5,
      "hp": 3,
      "ap": 0,
      "range_upgrades": 0,
      "alive": true,
      "last_action": null
    },
    "987654321": {
      "state_name": "Veyran Empire",
      "console_channel_id": "222222222",
      "x": 8,
      "y": 2,
      "hp": 3,
      "ap": 0,
      "range_upgrades": 0,
      "alive": true,
      "last_action": null
    }
  },
  "jury_votes": {},
  "info_access": []
}
```

Discord user IDs identify players internally. State names are stored separately.

Private console channel IDs allow the bot to deliver messages without publicly disclosing ownership.

The schema is illustrative rather than finalized.

### 11.3 State Updates

Each gameplay operation follows the same process:

1. Receive an action request.
2. Load or access the current game state.
3. Validate the action.
4. Modify the relevant attributes.
5. Save the JSON file.
6. Return the result to the interface.

Gameplay logic remains independent of Discord.

State-changing operations should be serialized, with JSON saved through atomic replacement to avoid corruption.

Daily distribution records prevent duplicate AP awards following bot restarts.

Communication relay functions operate separately from gameplay state changes and do not trigger action cooldowns.

---

## 12. Game Board Rendering

### 12.1 Representation

The board is a square grid whose dimensions are determined by the initial player count.

For the intended 16-player game:

- Width: 16 squares
- Height: 16 squares
- Total: 256 squares
- Initial state count: 16
- Each state occupies one square

The board displays:

- Grid coordinates
- State markers
- State names or identifying abbreviations
- Positions
- HP
- Attack ranges
- Eliminated-state indicators

Private AP balances and the identities of living states' owners are excluded.

### 12.2 Command-Line Prototype

The initial version uses a text-based grid rendered in the command window.

A legend maps each marker to its state name.

This allows movement, positioning, range, and combat to be tested before implementing Discord.

### 12.3 Discord Rendering

Pillow generates a PNG from the current game state.

**Pipeline:**

`game.json → Pillow → board.png → Discord`

The bot maintains a persistent board message and updates its image following relevant changes.

No external image hosting service or webpage is necessary.

---

## 13. Development Plan

### Phase 1 — Command-Line Prototype

Implement:

- Player registration and state naming
- Board sizing based on player count
- Random starting placement
- JSON loading and saving
- Movement and occupied-square restrictions
- Attacks and elimination
- Range upgrades
- AP transfers
- Cooldown enforcement
- Daily AP awards
- Secret Jury voting and rewards
- Information purchases
- Text board rendering

Support simulated timestamps so schedules and cooldowns can be tested without waiting for actual time to pass.

### Phase 2 — Graphical Board

Implement:

- Pillow grid rendering
- State markers
- State names and public statistics
- Eliminated-state indicators
- Automatic PNG regeneration

### Phase 3 — Discord Integration

Implement:

- State registration and naming commands
- Individual private player consoles
- Console permissions and channel creation
- Action commands
- Private AP status
- Masked public gameplay announcements
- Public diplomatic message relay
- Private state-to-state diplomatic relay
- Anonymous chat posting
- Mandatory and opt-in notifications
- Identity reveals upon elimination
- Jury role and channel access
- Private Jury ballot submission
- Daily Jury tally
- Information purchases and restricted channel access
- Daily scheduling
- Graphical board message updates

### Phase 4 — Testing

Validate:

- Board dimensions and random placement
- Movement restrictions
- Cardinal and diagonal attack ranges
- Insufficient AP
- Action cooldowns
- Active and inactive hours
- Jury voting privacy and reward calculations
- Daily AP distribution
- Information access expiration
- Private console permissions
- Public identity masking
- Private diplomatic message delivery
- Anonymous message attribution
- Elimination identity reveals
- Discord role transitions
- Victory conditions
- Restarts and state persistence
- Concurrent player actions

---

## 14. Administration

The game will have a human administrator who can intervene when necessary.

The bot should support basic administrative functions:

- Initialize a game
- Start and end a game
- Reset game state

Complex administrative automation is not a project requirement. Manual intervention
will be handled via direct editing of the game's JSON memory.

The administrator may have visibility into player identities and private channels, unlike ordinary participants.

---


## 17. Project Scope

Statecraft is a lightweight asynchronous Discord strategy game built around player diplomacy, masked identities, and simple resource management.

The bot acts as a referee, recordkeeper, and communication relay rather than a conventional game engine.

Each participant creates a sovereign state, manages its resources, negotiates with other players through public or private channels, and competes to become the final surviving power.

Identities remain hidden while states survive and are revealed upon elimination.

**Primary success criterion:** A group of approximately 16 friends can play a complete game of Statecraft through Discord, targeting roughly seven days of play, without requiring manual administration of routine gameplay, AP, cooldowns, voting, communication relays, or daily scheduling.