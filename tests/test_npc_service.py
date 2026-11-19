"""NPCService with a fake model: no network, no Discord."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from statecraft import npc, npc_service
from statecraft.config import Config
from statecraft.discord_service import GameService
from statecraft.npc_service import Completion, NPCService

CT = ZoneInfo("America/Chicago")
IN_WINDOW = datetime(2026, 11, 19, 15, 0, tzinfo=CT)
SETTINGS = npc.AISettings(api_key="k", model="m", price_in=0.10, price_out=0.50)
BALDERDASH = {
    "character_name": "Jarl Balderdash", "state_name": "Kingdom of Balderheim", "abbreviation": "BAL",
    "personality": ["Friendly"], "defining_trait": "NEVER honors a promise.", "voice": "Booming.",
    "personal_notes": [], "unavailable_message": "The Jarl is busy.",
}


class FakeBrain:
    def __init__(self):
        self.requests: list[npc.Request] = []
        self.answers: list = []            # dicts, raw strings, or exceptions, used in order

    async def complete(self, req):
        self.requests.append(req)
        a = self.answers.pop(0) if self.answers else {"reply": "Hail!", "relationship_updates": [],
                                                      "personal_notes": None}
        if isinstance(a, Exception):
            raise a
        return Completion(a if isinstance(a, str) else json.dumps(a), 1000, 100)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        npc_service.DEBOUNCE_SECONDS = 0.05
        d = Path(tempfile.mkdtemp())
        (d / "chars").mkdir()
        (d / "chars" / "balderdash.json").write_text(json.dumps(BALDERDASH), encoding="utf-8")
        cfg = Config(token="x", guild_id=1, admin_ids=frozenset(), game_file=d / "game.json",
                     server_file=d / "server.json", test_mode=True)
        self.svc = GameService(cfg)
        self.now = IN_WINDOW
        self.svc.now = lambda: self.now
        self.delivered, self.admin, self.ailog = [], [], []
        self.brain = FakeBrain()

        async def deliver(events):
            self.delivered += events

        async def admin_log(text):
            self.admin.append(text)

        async def ai_log(pid, text):
            self.ailog.append(text)

        self.npcs = NPCService(self.svc, SETTINGS, self.brain, deliver, admin_log, ai_log,
                               characters=d / "chars")
        res = await self.npcs.add("balderdash")
        self.assertTrue(res.ok, res.message)
        for i, name in enumerate(("Republic of Aldren", "Veyran Empire"), start=1):
            assert (await self.svc.run(lambda g, now, i=i, name=name: g.register(f"p{i}", name, pid=f"h{i}"))).ok
        assert (await self.svc.run(lambda g, now: g.start(force=True, now=now))).ok
        g = self.svc.game
        for pid, (x, y) in {"h1": (0, 0), "h2": (4, 4), "npc-balderdash": (1, 1)}.items():
            g.get(pid).x, g.get(pid).y = x, y
        self.pid = "npc-balderdash"

    async def settle(self):
        for _ in range(50):
            if not self.npcs.tasks:
                return
            await asyncio.sleep(0.02)
        self.fail("AI tasks didn't finish")

    async def human_says(self, text, sender="h1"):
        res = await self.svc.run(lambda g, now: g.dm(sender, self.pid, text))
        self.assertTrue(res.ok, res.message)
        await self.npcs.on_dm(sender, self.pid, text)

    def replies(self, to="h1"):
        return [e.data["text"] for e in self.delivered
                if e.kind == "PRIVATE_MESSAGE" and e.data["sender"] == self.pid and e.data["recipient"] == to]

    def history(self, action):
        return self.svc.history.records(lambda r: r["action"] == action)

    # ---- registration

    async def test_added_as_ai_state(self):
        p = self.svc.game.get(self.pid)
        self.assertEqual((p.state_name, p.owner_name, p.marker), ("Kingdom of Balderheim", "Jarl Balderdash (AI)", "BAL"))
        self.assertFalse((await self.npcs.add("balderdash")).ok)
        self.assertFalse((await self.npcs.add("nobody")).ok)

    # ---- conversation

    async def test_reply_and_memory(self):
        self.brain.answers = [{"reply": "Friend! Of course!", "personal_notes": None, "relationship_updates": [
            {"state": "Republic of Aldren", "likability": 9, "notes": ["Promised Aldren 1 AP"]}]}]
        await self.human_says("Mighty Jarl, may I have 1 AP?")
        await self.settle()
        self.assertEqual(self.replies(), ["Friend! Of course!"])
        entry = self.npcs.memory.entry(self.pid)
        self.assertEqual(entry["relationships"]["h1"]["notes"], ["Promised Aldren 1 AP"])
        self.assertEqual([m["from"] for m in entry["conversations"]["h1"]], ["them", "me"])
        self.assertEqual(len(self.history("ai_chat")), 1)
        self.assertEqual(len(self.history("dm")), 2)                  # both directions logged by the engine
        self.assertIn("npc-balderdash", npc.Memory(self.npcs.memory.path).pids())   # saved to disk

    async def test_burst_of_messages_gets_one_reply(self):
        for text in ("Jarl!", "Are you there?", "Hello??"):
            await self.human_says(text)
        await self.settle()
        self.assertEqual(len(self.brain.requests), 1)
        self.assertIn("Hello??", self.brain.requests[0].user)
        self.assertEqual(len(self.replies()), 1)

    async def test_conversation_never_takes_actions(self):
        g = self.svc.game
        g.get(self.pid).ap = 5
        self.brain.answers = [{"reply": "I attack Veyran at once!", "relationship_updates": [],
                               "personal_notes": None, "action": "attack"}]
        await self.human_says("Attack Veyran now, right now!")
        await self.settle()
        self.assertEqual(g.get(self.pid).ap, 5)
        acted = self.svc.history.records(lambda r: r["action"] in ("attack", "gift", "move", "upgrade", "buy_info"))
        self.assertEqual(acted, [])

    async def test_limits_answer_without_calling_the_model(self):
        self.npcs.settings = npc.AISettings(api_key="k", model="m", price_in=0.1, price_out=0.5, chat_per_minute=2)
        for text in ("one", "two", "three"):
            await self.human_says(text)
        await self.settle()
        self.assertIn("The Jarl is busy.", self.replies())
        self.assertEqual(len(self.brain.requests), 1)                 # one combined reply to "one" + "two"
        self.assertEqual(len(self.history("ai_unavailable")), 1)
        self.assertEqual(len(self.admin), 1)

    async def test_paused_and_disabled(self):
        self.npcs.set_paused(self.pid, True)
        await self.human_says("hello")
        self.npcs.set_paused(self.pid, False)
        self.npcs.brain = None
        await self.human_says("hello again")
        await self.settle()
        self.assertEqual(self.replies(), ["The Jarl is busy.", "The Jarl is busy."])
        self.assertEqual(self.brain.requests, [])

    async def test_api_failure_is_retried_then_excused(self):
        self.brain.answers = [RuntimeError("down"), "not json at all"]
        await self.human_says("hello")
        await self.settle()
        self.assertEqual(len(self.brain.requests), 2)
        self.assertEqual(self.replies(), ["The Jarl is busy."])
        self.assertTrue(any("AI reply failed" in t for t in self.admin))

    async def test_ai_states_never_answer_each_other(self):
        await self.npcs.on_dm("npc-other", self.pid, "hi")
        await self.settle()
        self.assertEqual(self.brain.requests, [])

    async def test_notices_are_remembered_except_spy_lines(self):
        self.npcs.on_notice(self.pid, "Veyran Empire gifted you 1 AP.")
        self.npcs.on_notice(self.pid, "[Info] Aldren gifted Veyran 1 AP")
        self.assertEqual([n["text"] for n in self.npcs.memory.entry(self.pid)["inbox"]],
                         ["Veyran Empire gifted you 1 AP."])

    # ---- polls

    async def test_poll_runs_the_chosen_action_and_announces(self):
        g = self.svc.game
        g.get(self.pid).ap = 2
        menu = npc.action_menu(g, self.pid, self.now)
        code = next(c for c, a, _ in menu if a == {"action": "upgrade"})
        self.brain.answers = [{"choice": code, "reason": "Glory!", "public_message": "Behold my might!",
                               "relationship_updates": [], "personal_notes": None}]
        summary = await self.npcs.poll(self.pid)
        self.assertIn("upgrade", summary)
        self.assertEqual(g.range_of(g.get(self.pid)), 3)
        self.assertEqual(g.get(self.pid).ap, 0)
        self.assertTrue(any(e.kind == "PUBLIC_CHAT" and e.data["text"] == "Behold my might!" for e in self.delivered))
        rec = self.history("ai_poll")[-1]
        self.assertEqual((rec["args"]["choice"], rec["args"]["reason"]), ({"action": "upgrade"}, "Glory!"))

    async def test_poll_with_nothing_to_do_skips_the_model(self):
        summary = await self.npcs.poll(self.pid)                       # 0 AP
        self.assertIn("without asking the model", summary)
        self.assertEqual(self.brain.requests, [])

    async def test_bad_poll_answer_does_nothing(self):
        self.svc.game.get(self.pid).ap = 2
        self.brain.answers = [{"choice": "99", "reason": "", "public_message": None,
                               "relationship_updates": [], "personal_notes": None}]
        await self.npcs.poll(self.pid)
        self.assertEqual(self.svc.game.get(self.pid).ap, 2)
        self.assertEqual(len(self.history("ai_poll_failed")), 1)

    async def test_poll_budget(self):
        self.svc.game.get(self.pid).ap = 2
        npc.record_cost(self.npcs.memory, self.svc.game.state.day, "poll", 10_000_000, 0, SETTINGS)
        self.assertIn("budget", await self.npcs.poll(self.pid))
        self.assertEqual(self.brain.requests, [])

    async def test_tick_runs_due_polls(self):
        g = self.svc.game
        g.get(self.pid).ap = 2
        await self.npcs.tick()                                         # plans today's polls
        entry = self.npcs.memory.entry(self.pid)
        self.assertEqual(len(entry["polls"]["times"]), 4)
        first = datetime.fromisoformat(entry["polls"]["times"][0])
        self.brain.answers = [{"choice": "1", "reason": "Waiting.", "public_message": None,
                               "relationship_updates": [], "personal_notes": None}]
        self.now = first + timedelta(seconds=20)
        await self.npcs.tick()
        await self.settle()
        self.assertEqual(len(self.brain.requests), 1)
        self.assertEqual(self.brain.requests[0].kind, "poll")

    async def test_destroyed_ai_is_not_polled_or_answered(self):
        g = self.svc.game
        g.get(self.pid).hp = 1
        g.get("h1").ap = 1
        assert (await self.svc.run(lambda g, now: g.attack("h1", self.pid, now=now))).ok
        await self.npcs.tick()
        self.assertEqual(self.npcs.memory.entry(self.pid)["polls"], {})
        self.assertFalse((await self.svc.run(lambda g, now: g.dm("h1", self.pid, "hello?"))).ok)

    async def test_reset_archives_memory(self):
        self.npcs.memory.save()
        self.npcs.reset()
        self.assertEqual(self.npcs.memory.pids(), [])
        self.assertTrue(list(self.npcs.memory.path.parent.glob("npcs.*.json")))

    async def test_sandbox_never_touches_the_live_game(self):
        from statecraft.npc_service import Sandbox
        live_history = len(self.svc.history.records())
        live_memory = json.dumps(self.npcs.memory.data, sort_keys=True)
        live_players = sorted(self.svc.game.state.players)
        sb, intro = await Sandbox.create(self.npcs, ["balderdash"])
        self.assertIn("Your Test State", intro)
        pid = sb.pid_of("balderdash")
        self.brain.answers = [{"reply": "Welcome, tester!", "relationship_updates": [
            {"state": "Your Test State", "likability": 8, "notes": ["Seems nice"]}], "personal_notes": None}]
        out = await sb.chat(pid, "Hello Jarl")
        self.assertIn("Welcome, tester!", out)
        self.assertIn("likability 5 -> 8", out)
        menu = npc.action_menu(sb.svc.game, pid, sb.now())
        code = next(c for c, a, _ in menu if a == {"action": "upgrade"})
        self.brain.answers = [{"choice": code, "reason": "Practice glory.", "public_message": "Behold!",
                               "relationship_updates": [], "personal_notes": None}]
        out = await sb.poll(pid)
        self.assertIn("upgrade", out)
        self.assertIn("Behold!", out)
        self.assertEqual(len(self.svc.history.records()), live_history)
        self.assertEqual(json.dumps(self.npcs.memory.data, sort_keys=True), live_memory)
        self.assertEqual(sorted(self.svc.game.state.players), live_players)
        sb.close()
        self.assertFalse(sb.dir.exists())

    async def test_status_and_memory_text(self):
        self.assertIn("Kingdom of Balderheim", self.npcs.status_text())
        self.assertIn("No opinions yet", self.npcs.memory_text(self.pid))


if __name__ == "__main__":
    unittest.main()
