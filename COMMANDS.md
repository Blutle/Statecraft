# Statecraft Bot Commands

The Discord command reference. Update this file whenever a command is added, changed, or removed.

**Build status (2026-11-17):**
- **Built:**
  - Every command and panel control in this file, except the items below.
  - Pacts: anonymous relayed channel copies, Accept/Decline invitations, the panel Pacts menu, and the `/pact` commands.
  - `/admin cleanup`.
- **Not built yet:**
  - `/admin open`. It isn't really needed: a new or reset game is already open for `/join`.

Every command is a slash command. Replies are **private** (only the person who ran the command sees them) unless the table says something is posted publicly. Every command, including rejected attempts, is written to the game history log (see `IMPLEMENTATION_PLAN.md` §4b).

**Where commands work:**
- **#register:** `/join` only (`/leave` works anywhere).
- **Your console:** everything a player does. Console commands used anywhere else are rejected, because a slash command in a public channel would show who used it.
- **Anywhere:** admin commands (`ADMIN_IDS` in `.env` only).

**Other apps:**
- **"Use External Apps" is denied** in every game channel, console, and pact channel. An app a player installed on their own Discord account would post its response under their real name, which would break the masking.
- **Slash commands are disabled** in the read-only channels.
- **Other bots' commands** can only be limited per bot, by a server admin under **Server Settings → Integrations → [bot] → Manage**. The simplest option is to not add other bots to the game server.

**Confirmation:** every command that **spends AP** (`/move`, `/attack`, `/upgrade`, `/gift`, `/buyinfo`) or **can't be undone** (`/leave`, `/admin reset`, `/admin declare`) first shows a preview with **Yes** and **Cancel** buttons:
- Only the person who ran it can answer, and unanswered prompts expire after 2 minutes with nothing spent.
- An action that would fail anyway (out of range, on cooldown, not enough AP, window closed) is rejected immediately, with no prompt. The rejected attempt is still logged.
- The action runs at the moment **Yes** is clicked. The window, cooldown, and range are checked again then.

**Targets:** commands that take a state autocomplete from state names as you type. You can type the name, the start of it, or its 3-letter abbreviation (e.g. `VEY`).

---

## Control panel (the main way to play, built for phones)

Every console has a **control panel** that always sits at the **bottom** of the console, so it's right under your thumb on a phone. It's a status card plus a menu and buttons. Each panel choice runs exactly the same game action, confirmation, and logging as the matching slash command below. The slash commands are a backup.

**Status card:**
- **Living states:** AP, HP, range, and position; when your cooldown is ready; whether actions are open; info access; which states you can reach; and which states can reach you.
- **Jurors:** your current vote and whether voting is open.
- **Updates:** the card refreshes after every change to the game, and on demand with **Refresh**.
- **Recent activity:** the last 6 private notices (gifts received, forwarded gifts from bought info, info expiring, being destroyed, pact removals or declines) are listed on the card instead of being posted as separate messages.

**Keeping consoles clean:**
- Every reply to something you do (command results, rejections, confirmations, menus) is **private** and disappears when dismissed.
- The only messages that stay in your console are the panel and things you need to answer, such as **pact invitations** (removed once you accept or decline). When one arrives, the panel moves back below it.
- **Conversations** with other states live in their own private threads (below).

| Panel control | What it does |
|---|---|
| **Name my state / Rename my state** (before the game) | Opens a box for your state name and 3-letter abbreviation. When renaming, it starts filled in with your current values. |
| **Choose an action…** menu (living states) | **Move** opens a 3×3 arrow grid, with blocked and off-board squares greyed out. **Attack** and **Gift AP** list only the states in your range (gift then asks for an amount). **Upgrade range** and **Buy info** go straight to confirmation. Every choice ends in **Yes / Cancel**. |
| **Choose an action…** menu (jurors) | **Vote** lists the surviving states. |
| **Status** / **Board** | Your full status, or the board with your range marked, shown privately. |
| **Public chat** / **Anonymous** | Opens a text box and posts to #public-chat (under your state name) or #anonymous-chat (no name). |
| **Message a state** | Pick any state to open your **private conversation thread** with them (e.g. `↔ Veyran Empire`), created the first time. Type there like a normal chat. |
| **Refresh** / **Help** | Updates the card / shows the command list. |

The menus that open from the panel (direction grid, target list, confirmation) are private to you and expire after 2 minutes. If the panel ever goes missing, `/panel` posts a fresh one.

## Registration (before the game starts)

Your state name is only ever typed in your **private console**, never in #register. If a slash command misfires (say, Enter is pressed before Discord picks up the command), the text posts as an ordinary chat message, so `/join` takes no name.

| Command | Where | What it does |
|---|---|---|
| `/join` | #register | Creates your private console (a random name like `#console-7f3a`, visible only to you, the bot, and the admin), pins your control panel there, and gives you `@Statecraft-Players`. The reply is private. |
| `/name name:<state name> [abbreviation:<ABC>]` | your console | Sets your state's name and its **3-letter abbreviation** (the board marker, letters only, unique; leave it out and one is made from the name), or changes them. You can rename any time before the game starts. Names must be unique, up to 24 characters, and can't contain @, #, <, >, backticks, asterisks, underscores, tildes, pipes, or backslashes. |
| `/leave` | anywhere | (Yes/Cancel) Withdraws before the game starts, removes your role, and deletes your console. |

`/admin start` refuses to start while anyone has joined but not named a state.

## Gameplay actions (console only)

Each action is allowed only **2:00–8:00 PM Central** and starts your **15-minute cooldown**, shared by all actions. Rejected actions cost nothing and don't start a cooldown. Living states only.

| Command | Cost | What it does | Public? |
|---|---|---|---|
| `/move direction:<n, ne, e, se, s, sw, w, nw>` | 1 AP | Moves one square. You can't move off the board or onto an occupied square, and destroyed states still block. | #action-log, pings `@Statecraft-Updates` |
| `/attack state:<state>` | 1 AP | Deals 1 damage to a state within your range. At 0 HP the state is destroyed, its owner is revealed, and they join the Jury. | #action-log, pings `@Statecraft-Players` |
| `/upgrade` | 2 AP | Range +1. Range is the number of horizontal plus vertical steps, with a diagonal step counting as 2. | #action-log, pings `@Statecraft-Updates` |
| `/gift state:<state> amount:<n>` | n AP | Gives AP to a state within your range. The recipient gets a private notice in their console. | **No.** Only the recipient, and anyone with bought info, finds out. |
| `/buyinfo` | 1 AP | Shows every AP gift made today in your console, and forwards new gifts until 8:15 PM. | The purchase is posted in #action-log (pings Updates); the gift details stay private. |

## Info (console only, free, any time, no cooldown)

| Command | What it does |
|---|---|
| `/status` | **Your own** full status: AP, HP, position, range, cooldown remaining, whether you have info access, whether the window is open, and when it next opens. Jurors also see their current vote. |
| `/status state:<state>` | **Another state's** public stats: HP, position, range, alive or destroyed (with the owner if destroyed), plus whether it's in your range and whether you're in its range. **AP and cooldown are hidden.** AP is the one hidden stat. Cooldown stays hidden because gifts are private but still start a cooldown, so showing it would leak when someone gifted. |
| `/board` | Posts a board image **in your console**. By default it highlights your own range and outlines in red every state that can reach you. A **"Show range for…" dropdown** under the image redraws the board with any state's range highlighted (ranges are public). |
| `/board state:<state>` | The same board, starting with that state's range highlighted. |
| `/info` | Re-shows today's gift report, if you've bought info. |
| `/help` | The command list and a short rules summary. |
| `/panel` | Re-posts your control panel (if it's missing or buried). |

## Communication (console only, free, any time, no cooldown)

Available to living **and** eliminated players. Players are told that everything is logged for the post-game summary, including the real author of anonymous posts.

| Command | What it does |
|---|---|
| `/say message:<text>` | Posts in #public-chat under your **state name** (via the webhook). |
| `/dm state:<state> message:<text>` | Sends a private message to another state. It appears in your conversation thread with them, and in theirs with you, under your state name. |

**Conversation threads:**
- Each pair of states talks in a private thread inside each player's console, such as `↔ Veyran Empire`, visible only to that player, the bot, and the admin.
- **Typing:** type in the thread and the bot relays it to the other player's matching thread under your state name. A message sent with `/dm` shows up in your own thread as "*Your State* (you)".
- **Limits:** threads don't count toward Discord's 500-channel limit. Quiet threads archive after a week and reopen when a new message arrives.
- **Logging:** every message is in the history log.
| `/anon message:<text>` | Posts in #anonymous-chat with no name at all. |

## Pacts: private group chats (console commands; you chat in pact channels)

Pacts let players form anonymous group chats, such as alliances, by inviting other states by name.

**How they stay anonymous:** in a shared Discord channel, everyone's posts show their Discord name. So each pact member gets their **own private copy** of the pact channel (e.g. `#pact-iron-accord`), visible only to them, the bot, and the admin. You type normally in your copy. The bot reposts your message into every other member's copy under your **state name**, and you see theirs the same way. Nobody ever sees another member's Discord account.

| Command | What it does |
|---|---|
| `/pact create name:<pact name>` | Creates a pact with you as its founder, plus your copy of its channel. **Each player can found up to 3 pacts.** |
| `/pact invite pact:<pact> state:<state>` | **Founder only.** Invites a state. The invitee's console gets an invitation with **Accept** / **Decline** buttons, showing the pact name and the founder's state name. |
| `/pact leave pact:<pact>` | Leaves the pact. Your copy of the channel is deleted, and the other members are told that your state left. |
| `/pact kick pact:<pact> state:<state>` | Founder only: removes a member. |
| `/pact list` | Your pacts, their members (★ marks the founder), and any pending invitations. |

**On the panel:** the **Pacts** button shows the same summary, with **Found a pact**, **Invite a state**, **Remove a member**, and **Leave a pact** buttons. Each opens a menu, and removing or leaving asks Yes/Cancel. Invitations arrive in your console with **Accept** / **Decline** buttons.

**Rules:**
- Pacts cost nothing, have no cooldown, and are open at all hours, including to eliminated players (jurors keep full social access).
- A destroyed state stays in its pacts unless it leaves or is kicked.
- Every relayed message is in the history log.
- Text only to start. Edits and deletions are not relayed, and the log keeps the original.
- If a founder leaves, founding (and the ability to invite) passes to the longest-standing member. The pact still counts toward the original founder's limit of 3.
- **No history for new members:** a new member's copy of the channel starts empty and only receives messages sent after they join. Earlier messages stay out of reach (they remain in the history log).
- **Limits:**
  - Each player can found up to 3 pacts and **belong to at most 6 in total**, including the ones they founded.
  - There is no cap on members per pact.
- **Cleanup:** the bot deletes your copy when you leave or are kicked, and deletes the pact (and every copy) when its last member leaves. `/admin cleanup` removes every pact channel after the game.

## Jury (console only, eliminated players)

| Command | What it does |
|---|---|
| `/vote state:<state>` | Secretly votes for a surviving state. Voting is open **8:00 PM until 1:45 PM**, and you can change your vote until the lock. At the 2:00 PM tally, each state gets 1 AP per 3 votes. Only the totals are announced, never who voted for whom. |

Jury discussion happens in #jury. It's normal chat, not a command.

## Notifications (console only)

| Command | What it does |
|---|---|
| `/notify on` / `/notify off` | Opts in or out of `@Statecraft-Updates`, the pings for moves, upgrades, and info purchases. Attacks and eliminations always ping everyone. |

---

## Admin (`ADMIN_IDS` only, all replies private)

| Command | What it does |
|---|---|
| `/admin setup` | Creates or repairs roles, channels, permissions, and the webhook, and posts or updates the guides. Safe to run any time. |
| `/admin guides` | Posts or updates the **#rules** (the game) and **#how-to-play** (using the bot) guides. They're written in `statecraft/guides.py`, and their numbers (times, costs, cooldown, limits) come from the game settings. Re-run it after editing either. |
| `/admin open` | Opens registration for a new game (enables `/join`). |
| `/admin start` | Closes registration, places the states (needs 12+ players), posts the board, and announces the state names without owners. The first AP arrives at the next 2:00 PM. |
| `/admin status` | Full view: phase, day, and every state with its owner, AP, HP, position, and cooldown. Also shows tonight's ballots and anyone with info access. |
| `/admin edit state:<state> field:<field> value:<value> [announce:<true/false>]` | (Yes/Cancel) Sets any stored value of a state, with the same validation as normal play. Every edit is logged, and the player is always told privately (Recent activity). <ul><li>**Name / Abbreviation:** unique, with the usual limits. Renaming retitles other players' conversation threads.</li><li>**AP:** never announced.</li><li>**HP:** **0 destroys** the state (normal elimination: owner revealed, Jury role, win check). **Above 0 on a destroyed state revives** it (Jury role removed).</li><li>**Range.**</li><li>**Position:** a square like `C5`. It must be on the board and empty.</li><li>**Cooldown:** value `reset`.</li><li>**Owner name:** what's revealed on elimination.</li></ul>Changes to public stats (name, abbreviation, HP, range, position) are posted in #action-log as a "Referee adjustment" unless `announce` is false. The console version has the same thing as `edit <player> <field> <value>`. |
| `/admin give state:<state> amount:<n>` | Adds or removes AP (negative amount). Logged. |
| `/admin declare states:<states>` | (Yes/Cancel) Ends the game with one or more joint winners, for a draw agreed outside the game. Reveals the winners. |
| `/admin consoles` | Every state with its owner and clickable links to their console, conversation threads, and pact channels. Consoles have random names, so this is how you find them. (As server owner you can open all of them. Anyone else with Administrator or Manage Threads can too, so keep those roles to yourself.) |
| `/admin log [state:<state>] [count:<n>]` | The most recent history-log entries (20 by default, up to 60) for one state or for everyone. Covers actions (rejected ones marked ✗), messages, pact messages, and anonymous posts, **with the real author**. |
| `/admin juryscore` | After the game: ranks jurors by votes for the winner(s), with ballots, nights, and rate. |
| `/admin reload` | Reloads `game.json` after you edit it by hand. |
| `/admin reset [fresh_channels:<true>]` | (Yes/Cancel) Wipes the game for a fresh one. The old history log is kept under a timestamped name. |
| `/admin cleanup [fresh_channels:<true>]` | (Yes/Cancel) After a game: deletes every console (and the conversation threads inside them) and every pact channel (and the extra pact categories), and removes game roles. `game.json` and the history log are kept. By default the public channels keep their messages. With `fresh_channels: True` (also available on `/admin reset`), #register, #game-board, #action-log, #public-chat, #anonymous-chat, and #jury are deleted and recreated empty with the same permissions. **#rules, #how-to-play, and #statecraft-admin are kept.** |

## Test mode (`TEST_MODE=1` in `.env` only, admins only)

These let one person run a full game alone on the test server. They are disabled for real games.

| Command | What it does |
|---|---|
| `/test addplayers count:<n>` | Registers *n* fake players with generated state names. They have no Discord account, so their console messages go to #statecraft-admin. |
| `/test as state:<state> command:<text>` | Runs any console command as that state, e.g. `/test as VE attack WE` or `/test as VE say hello`. Pacts work by quoted name: `pact create "Iron Accord"`, `pact invite "Iron Accord" WE`, `pact accept "Iron Accord"`, `pact say "Iron Accord" hi`, `pact leave "Iron Accord"`, `pact kick "Iron Accord" WE`. A fake player's pact messages go to #statecraft-admin. |
| `/test start` | Starts with fewer than 12 players. |
| `/test clock offset:<duration>` | Shifts the bot's clock forward (e.g. `+2h`, `+1d`) to test windows, cooldowns, the 2 PM tick, the vote lock, and the info fade without waiting. |
| `/test tick` | Runs the 2:00 PM daily tick immediately. |

---

## Automatic (no command)

| When | What the bot does |
|---|---|
| Every ~30 seconds | Runs any due scheduled events, exactly once each, and catches up after downtime. |
| 2:00 PM | Announces jury results, gives each survivor its daily AP, opens actions, and posts "Day N begins" in #action-log. |
| 8:00 PM | Closes actions and opens jury voting. |
| 8:15 PM | Ends info access and notifies the buyers. |
| Every public game event | Posts a short card in **#action-log**: an icon and one or two lines, with each state shown as **Name** `ABC`. Attacks show hearts for the HP left. Eliminations, the day start, and jury results each have their own card, and the game start is a roster card. Role pings sit on the line above the card, because pings don't work inside cards. |
| After **every** visible action | Updates the #game-board message in place: the map plus the public stats table (HP, range, position, status). Each marker shows its state's range number so reach can be judged at a glance. The message has the same **"Show range for…" dropdown**, and picking a state shows the highlighted board **privately** to whoever picked it, so nobody's choice is visible. Gifts aren't visible, so they don't trigger an update. |
| On elimination | Reveals the owner, grants `@Statecraft-Jury` (and access to #jury), and posts in #action-log. |
| At game end | Announces the winner(s) and reveals their owners. |

## The board image

The board is a picture drawn by the bot, sized for phones:
- **Tiles:** each state is a colored tile with its 3-letter abbreviation, HP dots, and a range badge on the corner. Destroyed states are greyed out with an ✕.
- **Legend:** listed under the board, inside the image, so it never wraps.
- **Contents:** public information only, which a test enforces. It never shows AP or the owner of a living state.

**#game-board** shows the public board. It updates after every visible action and has a **Show range for…** menu.

**Your board** (panel **Board** button, or `/board`):
- The range shading defaults to yours, or to the state you name in `/board`.
- Your tile has a **white outline**.
- Every state that can reach you has a **red ring**, also marked "can reach you" in the legend.

## Range display (decided)

A Pillow image is static, and Discord can't do mouse-over on images. So range is shown in four ways:
1. **Range numbers on markers** on every board.
2. **Your console board** highlights your reach and outlines in red the states that can reach you.
3. **The "Show range for…" dropdown** on both the console board and the public board shows any state's range highlighted, privately.
4. **`/status state:`** says whether you and that state can reach each other.

## Discord requirements this design adds

- **Message Content intent:** enabled (2026-11-17). Pact channels relay plain typed messages, so the bot reads them.
- **Channel limits:** Discord allows 500 channels per server and 50 per category. Pact copies are spread across "Statecraft Pacts" categories as needed. With 16 players and the membership cap of 6, there are at most 96 pact channels.

## Open questions

None right now.
