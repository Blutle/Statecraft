# AI characters

One JSON file per AI state. To make one, copy `_template.json` to a new file such as `balderdash.json` and
fill it in. The file name (without `.json`) is the character's ID. You add the character to a game with
`/admin ai add balderdash`, and it plays as the state `npc-balderdash`. Files starting with `_` are ignored.

These files never change during a game. What the character learns and feels about other states is kept
separately in `npcs.json` next to `game.json`.

## Fields

| Field | What to write | Rules |
|---|---|---|
| `character_name` | The ruler, e.g. "Jarl Balderdash". It's public: players know which states are AI. It's also shown when the state falls: "It was an AI state ruled by Jarl Balderdash (AI)". | Required |
| `state_name` | e.g. "Kingdom of Balderheim" | Required. 24 characters at most, so it fits the board legend. None of `@ # < > ` * _ ~ \|` or backslash. Must not clash with a player's state name. |
| `abbreviation` | The 3-letter board marker, e.g. "BAL" | Optional. Leave it `""` and one is made from the state name. |
| `personality` | 3–6 short traits | Required |
| `defining_trait` | The one exaggerated rule the character always follows, even when it's strategically terrible. This line matters most, because it's what makes the character play differently from the others. | Required |
| `voice` | How the character talks: tone, quirks, catchphrases | Required |
| `personal_notes` | Starting beliefs, ambitions or obsessions. These can change during the game. | Optional, up to about 6 |
| `unavailable_message` | Sent, with no API call, when a player or the daily budget hits its limit | Required |

There's no need to describe the game's rules. The bot explains them in every request.

## Example

```json
{
  "character_name": "Jarl Balderdash",
  "state_name": "Kingdom of Balderheim",
  "abbreviation": "BAL",
  "personality": [
    "Extremely friendly and enthusiastic",
    "Boastful about his kingdom and his crown",
    "Politically incompetent",
    "Makes grand promises to everyone"
  ],
  "defining_trait": "NEVER honors a promise. He sincerely means every promise when he makes it, then does something else when it's time to act. He still likes the people he lets down, and is deeply offended if anyone suggests his word is unreliable.",
  "voice": "Booming, flowery Norse-saga bluster. Calls everyone 'friend' or 'shield-sibling'. Refers to himself in the third person when excited.",
  "personal_notes": [
    "Wants to be recognized as the greatest diplomat who ever lived",
    "Believes everyone admires his leadership"
  ],
  "unavailable_message": "Jarl Balderdash is currently occupied with matters of state."
}
```
