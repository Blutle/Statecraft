# Statecraft: AI-Controlled States (Plan)

AI states are game-filling NPCs: eccentric rulers with exaggerated personalities, played through the
OpenAI API. They take part in diplomacy and play with the same mechanics as everyone else, but they're
built to entertain, not to win. This plan follows the feature brief, with the decisions made on
2026-11-19.

## 0. Decisions (2026-11-19)

- **Not on the Jury.** A destroyed AI state is just gone. It doesn't vote, can't be messaged, isn't
  listed among jurors, and is never in `juryscore`.
- **AI wins aren't special-cased.** If an AI is last standing, the normal `GAME_OVER` happens and the
  referee handles the prize ("a wash").
- **Openly disclosed.** Players know which states are AI. Each AI's ruler name is public from the start
  and isn't a masked identity. There's no artificial reply delay.
- **No pacts.** AIs can't be invited to pacts or found them. They form direct relationships with
  players only.
- **AIs never start conversations.** They only reply to humans who message them, so two AIs can never
  talk to each other. They may post one public statement per poll in #public-chat. They never reply to
  public chat.
- **Limits as in §7.** Change them in `.env` if spending looks wrong.

## 1. Reuse

- **Registration.** AIs register with `Game.register(owner_name="Jarl Balderdash (AI)", state_name,
  pid="npc-<id>")`. They count toward `min_players` and the board size automatically.
- **Actions.** AI actions are ordinary engine calls inside `GameService.run()`. The engine still checks
  the phase, the window, the cooldown, AP and range under the single lock. Everything goes into
  `history.jsonl` through `@recorded`.
- **Replies.** AI replies are `Game.dm(npc, human, text)`, relayed by the existing `deliver_dm`
  webhook path under the state's name.
- **Public statements.** These go through `Game.say(npc, text)`.
- **Polls.** Poll timing hangs off the existing 30-second `scheduler` loop and uses `svc.now()`, so
  `/test clock` drives AIs too.
- **Accountless players.** `is_fake(pid)` becomes a general "no Discord account" check covering
  `fake*` and `npc-*`. The AI-specific branches are an incoming DM in `deliver_dm` and private notices
  in `to_player` (which go to the AI's inbox).

## 2. Files

- **`statecraft/npc.py`**: pure logic with no network calls and no Discord. It covers character
  loading, memory, context building, legal-action menus, response checking, usage limits and poll-time
  generation.
- **`statecraft/npc_service.py`**: async glue. It holds the `AsyncOpenAI` client with strict
  structured outputs, per-AI locks, typing debounce, poll running, and the `#ai-log` mirror.
- **`characters/*.json`**: hand-written character definitions, kept in git.
- **`npcs.json`**: runtime memory and counters, next to `game.json`. It's gitignored and archived with
  the history log on reset.
- **Engine (`game.py`) changes:**
  - `is_npc(pid)` and `is_juror(p)`, meaning destroyed and not an AI. `is_juror` replaces the bare
    `not p.alive` juror checks in `dm()`, DM target lists, `juryscore` and the elimination wording
    ("…has fallen" rather than "joins the Jury").
  - Pact invites to AI states are rejected.
  - `legal_actions(pid, now)`, a read-only helper.
  - No change to the `game.json` schema.
- **Other changes:**
  - `config.py`: the AI settings.
  - `requirements.txt`: add `openai`.
  - `COMMANDS.md`, the FAQ and guides (which states are AI, and that messages to them are sent to
    OpenAI), and `deploy/DEPLOY.md` (`.env` keys).

## 3. Memory

The character file holds `character_name`, `state_name`, `abbreviation`, `personality[]`,
`defining_trait`, `voice`, `unavailable_message` and `personal_notes[]`. The file name is the ID:
`balderdash.json` plays as `npc-balderdash`, registered with owner name "Jarl Balderdash (AI)". See
`characters/README.md`.

`npcs.json` has one entry per AI player ID:

| Field | Contents |
|---|---|
| `relationships` | Keyed by **player ID**, so renames are safe. Each entry is `{likability: 1-10, notes: [≤8 short lines]}`. Promises, grudges and suspicions are just notes. |
| `personal_notes` | Up to about 6 lines. |
| `conversations` | Keyed by player ID: the last `AI_HISTORY_MESSAGES` (20) messages each way. |
| `inbox` | Recent private notices: attacks on it, gifts received, spy-log gift lines. |
| `polls` | `{day, times[], done}`, saved so a restart doesn't pick new times. |
| `usage` | `{day, chat: {pid: n}, polls: n, chat_cost, poll_cost}`. |

There is no separate summarizing step. Each conversation reply rewrites the notes for that
correspondent, so the notes carry older conversations forward as the raw history is trimmed. Updates
are clamped and length-capped, and updates naming unknown states are ignored.

## 4. The two systems

### A. Conversation

A human messages an AI. The flow is:

1. `deliver_dm` sees an AI recipient and passes the message to `npc_service`.
2. The service waits about 6 seconds after the last message, so a burst gets one combined reply.
3. Only one request per AI–human pair runs at a time. If messages arrive mid-request, another round
   follows.
4. If a limit is hit, the AI sends its static `unavailable_message` and no API call is made.
5. Otherwise the service builds the context, calls the API with **no game lock held**, and gets back
   `{reply, relationship_updates, personal_notes}`.
6. The reply goes through `svc.run(g.dm(npc, human, reply))`, then memory is updated under the AI's
   own lock.

This path has no access to gameplay functions.

### B. Gameplay polls

**Scheduling.** On the first scheduler pass of each game day, every living AI gets 4 random times
between 14:00 and 19:45, at least 30 minutes apart.

**Running polls.** Polls that are due run as background tasks. A poll more than 30 minutes late (the
bot was offline) is skipped. If the AI has 0 AP and nothing to do, the poll passes without an API
call.

**Choosing an action.** `legal_actions` builds a lettered menu: pass, upgrade, spy, attack X, gift X,
move dir. The JSON schema only allows those letters. The chosen action runs through `svc.run(...)`,
which checks everything again. If it's rejected, the poll counts as a pass and the reason is logged.
Each poll is one action.

Destroyed AIs aren't polled.

## 5. JSON formats

Conversation reply:
```json
{ "reply": "...",
  "relationship_updates": [ {"state": "Republic of Aldren", "likability": 9, "notes": ["..."]} ],
  "personal_notes": null }
```

Poll response:
```json
{ "choice": "B", "reason": "...", "public_message": "... or null",
  "relationship_updates": [], "personal_notes": null }
```

`reason` is logged to the history log and `#ai-log`, never shown to players.

## 6. What an AI sees

The context is built from an explicit list of allowed data, never from `Game.who()`, which includes
AP.

**Included:**
- Every state's position, HP and Influence.
- Its own AP and cooldown.
- The last ~30 entries of `state.log`.
- Recent `say` records.
- Recent `anon` records, **text only**.
- Its own conversations and inbox.
- Today's gifts, only while it's spying.

**Never included:** other states' AP, owner names of human states that are still alive, jury votes or
`jury_history`, other states' DMs, or pact chat.

A test seeds secrets and checks that no prompt contains them.

## 7. Limits (`.env`)

| Key | Default |
|---|---|
| `OPENAI_API_KEY`, `AI_MODEL` | cheapest suitable model, checked at build time |
| `AI_CHAT_PER_PLAYER_DAY` | 1000 |
| `AI_CHAT_PER_MINUTE` | 6 per human |
| `AI_MAX_REPLY_TOKENS` | 300 (replies are also cut to the engine's 1500 characters) |
| `AI_HISTORY_MESSAGES` | 20 |
| `AI_DAILY_BUDGET_USD` | 2.00, chat |
| `AI_POLL_BUDGET_USD` | separate, polls |
| `AI_PRICE_IN`, `AI_PRICE_OUT` | USD per million tokens, used to estimate cost from the token counts each response reports |

- Counters are saved and reset on each new game day.
- When a limit is hit, the AI sends its unavailable message, makes no API call, and writes one
  admin-log line per day.
- If the API fails, the bot retries once. After that, a conversation gets the unavailable message and
  a poll passes. Both are logged.
- The fixed part of each prompt comes first, so OpenAI's automatic prompt caching applies.

## 8. Admin

`/admin ai` commands:
- `add <character>`, during registration.
- `list`, showing usage and next polls.
- `pause` / `resume`.
- `memory <state>`.
- `poll <state>`, to force a poll now.

`#ai-log` is admin-only, with a thread per AI. It mirrors incoming messages, replies, decisions with
reasons, and memory changes.

Poll decisions, passes, memory changes, limit hits and API failures are also written to
`history.jsonl` as `ai_*` records, for the post-game summary.

## 9. Tests

- A fake client, so the tests make no network calls.
- Context leak test.
- Response parsing and clamping.
- Legal-action menus.
- Poll time generation and skipping late polls.
- Limits and the unavailable message.
- Destroyed AI isn't a juror and can't be messaged.
- Pact invites to AIs are rejected.
- Conversations never reach gameplay functions.

## 10. Effort

Roughly 1,000–1,300 lines including tests, about 2–3 sessions. The suggested order:

1. Engine changes and tests.
2. `npc.py` with a fake client.
3. Conversations.
4. Polls.
5. Admin commands and `#ai-log`.
6. Docs and deploy.
