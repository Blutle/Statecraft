"""AI state logic (statecraft/npc.py): no network, no Discord."""

import json
import random
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from statecraft import npc
from statecraft.game import Game

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 19, 15, 0, tzinfo=CT)
SETTINGS = npc.AISettings(api_key="k", model="m", price_in=0.10, price_out=0.40)

BALDERDASH = {
    "character_name": "Jarl Balderdash", "state_name": "Kingdom of Balderheim", "abbreviation": "BAL",
    "personality": ["Friendly", "Boastful"], "defining_trait": "NEVER honors a promise.",
    "voice": "Booming.", "personal_notes": ["Wants glory"],
    "unavailable_message": "Jarl Balderdash is occupied with matters of state.",
}


def char_dir(**chars) -> Path:
    d = Path(tempfile.mkdtemp())
    for key, data in chars.items():
        (d / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")
    return d


def balderdash() -> npc.Character:
    return npc.load_character("balderdash", char_dir(balderdash=BALDERDASH))


def make_game():
    """Humans h1-h3 with secret owners and AP, Balderheim (npc-balderdash), and
    a real history recorder so prompts are built from genuine records."""
    records = []
    g = Game(rng=random.Random(1), recorder=records.append)
    g.state.settings["board_size"] = 7
    c = balderdash()
    assert g.register("SecretOwnerAlice", "Republic of Aldren", pid="h1").ok
    assert g.register("SecretOwnerBob", "Veyran Empire", pid="h2").ok
    assert g.register("SecretOwnerCara", "Free Cities", pid="h3").ok
    assert g.register(c.owner_name, c.state_name, pid=c.pid, abbreviation=c.abbreviation).ok
    assert g.start(force=True).ok
    for pid, (x, y) in {"h1": (0, 0), "h2": (6, 6), "h3": (2, 0), c.pid: (1, 1)}.items():
        g.get(pid).x, g.get(pid).y = x, y
    return g, c, records


class SettingsTests(unittest.TestCase):
    def test_defaults_and_disabled_without_key(self):
        s = npc.AISettings.from_env({})
        self.assertFalse(s.enabled)
        self.assertEqual(s.chat_per_player_day, 1000)
        self.assertEqual(set(s.missing()), {"OPENAI_API_KEY", "AI_MODEL", "AI_PRICE_IN", "AI_PRICE_OUT"})

    def test_reads_env(self):
        s = npc.AISettings.from_env({"OPENAI_API_KEY": "sk-x", "AI_MODEL": "cheap", "AI_PRICE_IN": "0.1",
                                     "AI_PRICE_OUT": "0.4", "AI_DAILY_BUDGET_USD": "5", "AI_CHAT_PER_MINUTE": "3"})
        self.assertTrue(s.enabled)
        self.assertEqual((s.daily_budget, s.chat_per_minute), (5.0, 3))
        self.assertAlmostEqual(s.cost(1_000_000, 1_000_000), 0.5)

    def test_bad_numbers_are_reported(self):
        with self.assertRaises(ValueError):
            npc.AISettings.from_env({"AI_DAILY_BUDGET_USD": "lots"})
        with self.assertRaises(ValueError):
            npc.AISettings.from_env({"AI_CHAT_PER_MINUTE": "-1"})


class CharacterTests(unittest.TestCase):
    def test_loads_and_names(self):
        c = balderdash()
        self.assertEqual((c.pid, c.owner_name, c.abbreviation), ("npc-balderdash", "Jarl Balderdash (AI)", "BAL"))

    def test_rejects_bad_files(self):
        bad = {
            "empty_trait": {**BALDERDASH, "defining_trait": ""},
            "no_traits": {**BALDERDASH, "personality": []},
            "long_name": {**BALDERDASH, "state_name": "The Most Serene Kingdom of Balderheim"},
            "markup": {**BALDERDASH, "state_name": "Balder*heim"},
            "abbrev": {**BALDERDASH, "abbreviation": "BA"},
            "notes_type": {**BALDERDASH, "personal_notes": "not a list"},
        }
        d = char_dir(**bad)
        for key in bad:
            with self.assertRaises(npc.CharacterError, msg=key):
                npc.load_character(key, d)
        with self.assertRaises(npc.CharacterError):
            npc.load_character("Bad Name", d)
        with self.assertRaises(npc.CharacterError):
            npc.load_character("missing", d)

    def test_templates_are_not_listed(self):
        d = char_dir(balderdash=BALDERDASH, _template={})
        self.assertEqual(npc.character_keys(d), ["balderdash"])

    def test_shipped_template_and_readme_example(self):
        """The blank template must be refused (it's blank); the README example must load."""
        with self.assertRaises(npc.CharacterError):
            npc.load_character("_template")             # underscore names aren't valid keys
        readme = (npc.CHARACTER_DIR / "README.md").read_text(encoding="utf-8")
        example = json.loads(re.search(r"```json\n(.*?)```", readme, re.S).group(1))
        c = npc.load_character("example", char_dir(example=example))
        self.assertEqual(c.state_name, "Kingdom of Balderheim")


class MemoryTests(unittest.TestCase):
    def test_save_reload_archive(self):
        path = Path(tempfile.mkdtemp()) / "npcs.json"
        m = npc.Memory(path)
        e = m.add(balderdash())
        self.assertEqual(e["personal_notes"], ["Wants glory"])
        m.save()
        self.assertIn("npc-balderdash", npc.Memory(path).pids())
        self.assertTrue(m.archive().exists())
        self.assertEqual(m.pids(), [])
        self.assertFalse(path.exists())

    def test_conversation_trim_and_unanswered(self):
        e = npc.Memory(Path(tempfile.mkdtemp()) / "n.json").add(balderdash())
        for i in range(5):
            npc.add_message(e, "h1", "them", f"m{i}", NOW, keep=3)
        self.assertEqual([m["text"] for m in e["conversations"]["h1"]], ["m2", "m3", "m4"])
        self.assertEqual(npc.unanswered(e, "h1"), 3)
        npc.add_message(e, "h1", "me", "reply", NOW, keep=3)
        self.assertEqual(npc.unanswered(e, "h1"), 0)

    def test_inbox_is_capped(self):
        e = npc.Memory(Path(tempfile.mkdtemp()) / "n.json").add(balderdash())
        for i in range(npc.MAX_INBOX + 5):
            npc.add_notice(e, 1, f"notice {i}")
        self.assertEqual(len(e["inbox"]), npc.MAX_INBOX)
        self.assertEqual(e["inbox"][-1]["text"], f"notice {npc.MAX_INBOX + 4}")


class InformationTests(unittest.TestCase):
    """Prompts must never carry what the AI's state couldn't know (plan §6)."""

    def setUp(self):
        self.g, self.c, self.records = make_game()
        g = self.g
        for pid in ("h1", "h2", "h3"):
            g.get(pid).ap = 9137                       # secret AP
        g.get("npc-balderdash").ap = 4
        assert g.say("h1", "Public greetings from Aldren").ok
        assert g.anon("h2", "Someone is plotting").ok
        assert g.dm("h1", "h2", "SECRET-DM-BETWEEN-HUMANS").ok
        g.get("h1").ap, g.get("h3").ap = 9138, 9139
        assert g.gift("h1", "h3").ok                   # secret gift: h1 -> h3
        g.get("h1").ap = g.get("h3").ap = 9137
        self.memory = npc.Memory(Path(tempfile.mkdtemp()) / "npcs.json")
        self.entry = self.memory.add(self.c)
        npc.add_message(self.entry, "h1", "them", "Will you give me 2 AP?", NOW, keep=20)

    def prompts(self) -> str:
        chat = npc.chat_request(self.g, self.c, self.entry, "h1", self.records, NOW, SETTINGS)
        menu = npc.action_menu(self.g, self.c.pid, NOW)
        poll = npc.poll_request(self.g, self.c, self.entry, menu, self.records, NOW, SETTINGS)
        return "\n".join([chat.system, chat.user, poll.system, poll.user])

    def test_no_secrets_leak(self):
        text = self.prompts()
        for secret in ("9137", "SecretOwner", "SECRET-DM-BETWEEN-HUMANS", "gifted"):
            self.assertNotIn(secret, text)
        self.assertIn("AP 4", text)                                    # its own AP
        self.assertIn("Republic of Aldren: Public greetings from Aldren", text)
        self.assertIn("Anonymous: Someone is plotting", text)
        self.assertNotIn("Veyran Empire: Someone is plotting", text)  # anon authors stay anonymous
        self.assertIn("Will you give me 2 AP?", text)                  # its own conversation

    def test_juror_ballots_never_shown(self):
        g = self.g
        g.get("h2").hp = 0
        g.get("h2").alive = False
        g.state.jury_votes["h2"] = "h3"
        g.state.jury_history.append({"day": 0, "juror": "h2", "target": "h3", "tallied": True})
        self.assertNotIn("vote", self.prompts().lower().replace("jury that awards", ""))

    def test_spy_report_only_while_spying(self):
        self.assertNotIn("gifted", self.prompts())
        assert self.g.buy_info(self.c.pid).ok
        self.assertIn("Republic of Aldren gifted Free Cities 1 AP", self.prompts())

    def test_other_ai_states_are_labelled(self):
        g = self.g
        self.assertIn("(AI state)", npc.board_text(g, "h1"))
        self.assertNotIn("(AI state)", npc.board_text(g, self.c.pid))


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.g, self.c, self.records = make_game()
        self.entry = npc.Memory(Path(tempfile.mkdtemp()) / "n.json").add(self.c)

    def test_poll_menu_and_schema(self):
        self.g.get(self.c.pid).ap = 2
        menu = npc.action_menu(self.g, self.c.pid, NOW)
        self.assertEqual(menu[0][:2], ("1", {"action": "pass"}))
        req = npc.poll_request(self.g, self.c, self.entry, menu, self.records, NOW, SETTINGS)
        self.assertEqual(req.schema["properties"]["choice"]["enum"], [code for code, _, _ in menu])
        descs = [d for _, _, d in menu]
        self.assertIn("upgrade your Influence from 2 to 3 (2 AP)", descs)
        self.assertIn(f"attack Republic of Aldren [{self.g.get('h1').marker}] (it has 3 HP) (1 AP)", descs)
        self.assertIn("move south to B3 (1 AP) [you and these would be out of each other's reach: RAL, FCI]", descs)
        self.assertIn("move north to B1 (1 AP)", descs)                 # changes nothing

    def test_game_sense_hints(self):
        g, me = self.g, self.g.get(self.c.pid)
        g.get(self.c.pid).ap = 2
        g.get("h1").hp = 1
        g.get("h2").range_upgrades = 20                                   # Veyran can reach everyone
        board = npc.board_text(g, self.c.pid)
        self.assertIn("Veyran Empire [VEM]: G7, HP 3, Influence 22, distance 10 (IT CAN REACH YOU)", board)
        self.assertIn("(you can reach it, IT CAN REACH YOU)", board)       # Aldren, equal Influence
        descs = [d for _, _, d in npc.action_menu(g, self.c.pid, NOW)]
        self.assertIn("attack Republic of Aldren [RAL] (it has 1 HP: this would DESTROY it) (1 AP)", descs)
        g.get("h2").range_upgrades = 0
        me.range_upgrades = 1                                             # Influence 3 vs their 2
        east = next(d for d in (d for _, _, d in npc.action_menu(g, self.c.pid, NOW)) if d.startswith("move east"))
        self.assertIn("you'd escape RAL", east)                           # still reach it at 3, it can't reach us
        self.assertIn("BASIC SENSE", npc.rules_text(g))

    def test_strict_schemas(self):
        """OpenAI strict mode: every object lists all its properties as required
        and forbids extras."""
        menu = npc.action_menu(self.g, self.c.pid, NOW)
        for req in (npc.chat_request(self.g, self.c, self.entry, "h1", [], NOW, SETTINGS),
                    npc.poll_request(self.g, self.c, self.entry, menu, [], NOW, SETTINGS)):
            stack = [req.schema]
            while stack:
                node = stack.pop()
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                stack += list(node.get("properties", {}).values()) + ([node["items"]] if "items" in node else [])

    def test_check_poll(self):
        self.g.get(self.c.pid).ap = 2
        menu = npc.action_menu(self.g, self.c.pid, NOW)
        action, reason, public, err = npc.check_poll({"choice": "2", "reason": "Glory!", "public_message": None},
                                                     menu)
        self.assertEqual((action, reason, public, err), (menu[1][1], "Glory!", "", ""))
        self.assertTrue(npc.check_poll({"choice": "99"}, menu)[3])
        self.assertTrue(npc.check_poll(npc.parse_json("not json"), menu)[3])

    def test_check_chat(self):
        self.assertEqual(npc.check_chat({"reply": "  Hail!  "}), ("Hail!", ""))
        self.assertEqual(len(npc.check_chat({"reply": "x" * 5000})[0]), 1500)
        self.assertTrue(npc.check_chat({"reply": "  "})[1])
        self.assertTrue(npc.check_chat(None)[1])


class ApplyMemoryTests(unittest.TestCase):
    def setUp(self):
        self.g, self.c, _ = make_game()
        self.entry = npc.Memory(Path(tempfile.mkdtemp()) / "n.json").add(self.c)

    def apply(self, **data):
        return npc.apply_memory(self.g, self.entry, self.c.pid, data)

    def test_updates_are_clamped_and_capped(self):
        changes = self.apply(relationship_updates=[
            {"state": "Republic of Aldren", "likability": 15, "notes": ["n"] * 20},
            {"state": self.g.get("h2").marker.lower(), "likability": -3, "notes": ["Insulted my crown", "  ", "x" * 999]},
        ])
        rel = self.entry["relationships"]
        self.assertEqual(rel["h1"]["likability"], 10)
        self.assertEqual(len(rel["h1"]["notes"]), npc.MAX_NOTES)
        self.assertEqual(rel["h2"]["likability"], 1)
        self.assertEqual(rel["h2"]["notes"][0], "Insulted my crown")
        self.assertEqual(len(rel["h2"]["notes"]), 2)
        self.assertEqual(len(rel["h2"]["notes"][1]), npc.MAX_NOTE_LEN)
        self.assertEqual(changes, ["Republic of Aldren: likability 5 -> 10", "Veyran Empire: likability 5 -> 1"])

    def test_unknown_states_and_self_are_ignored(self):
        self.apply(relationship_updates=[{"state": "Atlantis", "likability": 3, "notes": []},
                                         {"state": "Kingdom of Balderheim", "likability": 1, "notes": []}])
        self.assertEqual(self.entry["relationships"], {})

    def test_bad_values_keep_old_ones(self):
        self.apply(relationship_updates=[{"state": "Free Cities", "likability": 8, "notes": ["Ally"]}])
        self.apply(relationship_updates=[{"state": "Free Cities", "likability": True, "notes": "oops"}])
        self.assertEqual(self.entry["relationships"]["h3"], {"likability": 8, "notes": ["Ally"]})

    def test_personal_notes(self):
        self.assertEqual(self.apply(personal_notes=None), [])
        self.assertEqual(self.entry["personal_notes"], ["Wants glory"])
        self.apply(personal_notes=["a", "b", "c", "d", "e", "f", "g", "h"])
        self.assertEqual(len(self.entry["personal_notes"]), npc.MAX_PERSONAL_NOTES)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.m = npc.Memory(Path(tempfile.mkdtemp()) / "n.json")

    def test_per_player_daily_limit_resets_next_day(self):
        s = npc.AISettings(chat_per_player_day=3, chat_per_minute=100)
        for i in range(3):
            self.assertEqual(npc.chat_block(self.m, 1, "h1", NOW, s), "")
            npc.count_chat_message(self.m, 1, "h1", NOW + timedelta(seconds=i))
        self.assertEqual(npc.chat_block(self.m, 1, "h1", NOW, s), "player_day")
        self.assertEqual(npc.chat_block(self.m, 1, "h2", NOW, s), "")
        self.assertEqual(npc.chat_block(self.m, 2, "h1", NOW, s), "")

    def test_per_minute_limit(self):
        s = npc.AISettings(chat_per_minute=2)
        npc.count_chat_message(self.m, 1, "h1", NOW)
        npc.count_chat_message(self.m, 1, "h1", NOW + timedelta(seconds=10))
        self.assertEqual(npc.chat_block(self.m, 1, "h1", NOW + timedelta(seconds=20), s), "per_minute")
        self.assertEqual(npc.chat_block(self.m, 1, "h1", NOW + timedelta(seconds=75), s), "")

    def test_budgets_are_separate(self):
        s = npc.AISettings(price_in=1.0, price_out=1.0, daily_budget=0.01, poll_budget=0.01)
        cost = npc.record_cost(self.m, 1, "chat", 6000, 4000, s)
        self.assertAlmostEqual(cost, 0.01)
        self.assertEqual(npc.chat_block(self.m, 1, "h1", NOW, s), "budget")
        self.assertFalse(npc.poll_blocked(self.m, 1, s))
        npc.record_cost(self.m, 1, "poll", 10000, 0, s)
        self.assertTrue(npc.poll_blocked(self.m, 1, s))
        self.assertEqual(npc.usage(self.m, 1)["chat_calls"], 1)

    def test_first_notice_once_a_day(self):
        self.assertTrue(npc.first_notice(self.m, 1, "budget"))
        self.assertFalse(npc.first_notice(self.m, 1, "budget"))
        self.assertTrue(npc.first_notice(self.m, 2, "budget"))


class PollScheduleTests(unittest.TestCase):
    S = Game().s

    def test_four_spaced_times_inside_the_window(self):
        day = NOW.date()
        for seed in range(200):
            times = npc.poll_times(day, self.S, random.Random(seed))
            self.assertEqual(len(times), 4)
            self.assertEqual(times, sorted(times))
            self.assertGreaterEqual(times[0], datetime(2026, 11, 19, 14, 0, tzinfo=CT))
            self.assertLessEqual(times[-1], datetime(2026, 11, 19, 19, 45, tzinfo=CT))
            for a, b in zip(times, times[1:]):
                self.assertGreaterEqual(b - a, timedelta(minutes=30) - timedelta(seconds=1))

    def test_starting_mid_window_uses_the_rest(self):
        late = datetime(2026, 11, 19, 18, 30, tzinfo=CT)
        times = npc.poll_times(late.date(), self.S, random.Random(1), not_before=late)
        self.assertEqual(len(times), 4)
        self.assertTrue(all(late < t <= datetime(2026, 11, 19, 19, 45, tzinfo=CT) for t in times))
        after = datetime(2026, 11, 19, 20, 30, tzinfo=CT)
        self.assertEqual(npc.poll_times(after.date(), self.S, random.Random(1), not_before=after), [])

    def test_due_polls(self):
        g, c, _ = make_game()
        e = npc.Memory(Path(tempfile.mkdtemp()) / "n.json").add(c)
        morning = datetime(2026, 11, 19, 9, 0, tzinfo=CT)
        self.assertTrue(npc.ensure_polls(e, g, morning, random.Random(3)))
        self.assertFalse(npc.ensure_polls(e, g, morning + timedelta(hours=1), random.Random(3)))
        times = [datetime.fromisoformat(t) for t in e["polls"]["times"]]
        self.assertEqual(npc.next_poll(e), times[0])
        self.assertEqual(npc.take_due_poll(e, times[0] - timedelta(seconds=1)), "")
        self.assertEqual(npc.take_due_poll(e, times[0] + timedelta(seconds=30)), "run")
        self.assertEqual(npc.take_due_poll(e, times[0] + timedelta(seconds=60)), "")      # once only
        self.assertEqual(npc.take_due_poll(e, times[2] + timedelta(minutes=1)), "run")    # 2 and 3 due: one run
        self.assertEqual(e["polls"]["done"], [True, True, True, False])
        self.assertEqual(npc.take_due_poll(e, times[3] + timedelta(hours=2)), "late")    # bot was offline
        self.assertIsNone(npc.next_poll(e))
        self.assertTrue(npc.ensure_polls(e, g, morning + timedelta(days=1), random.Random(3)))


if __name__ == "__main__":
    unittest.main()
