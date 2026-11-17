"""The per-spy Spy log threads: opened in the console on Spy, fed new Gifts, closed when access ends."""
import asyncio
import random
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from statecraft.discord_bot import Router
from statecraft.discord_service import GameService
from statecraft.game import Game

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 17, 15, 0, tzinfo=CT)


class FakeThread:
    def __init__(self, tid, name):
        self.id, self.name, self.sent, self.members, self.archived = tid, name, [], set(), False
        self.mention = f"<#{tid}>"

    async def send(self, content=None, embed=None, allowed_mentions=None):
        self.sent.append((content, embed))

    async def add_user(self, user):
        self.members.add(user.id)

    async def remove_user(self, user):
        self.members.discard(user.id)

    async def edit(self, archived=None, locked=None):
        self.archived = bool(archived)


class FakeConsole:
    def __init__(self, cid, registry):
        self.id, self.registry = cid, registry

    async def create_thread(self, name, type=None, invitable=None, auto_archive_duration=None, reason=None):
        th = FakeThread(5000 + len(self.registry), name)
        self.registry[th.id] = th
        return th

    def history(self, limit=5):
        async def gen():
            return
            yield
        return gen()


def setup():
    g = Game(rng=random.Random(2))
    for n, name in enumerate(["Westhaven", "Veyran Empire", "Kesh"]):
        g.register(f"U{n}", name, pid=str(100 + n))
    g.start(force=True, now=datetime(2026, 11, 17, 10, 0, tzinfo=CT))
    g.state.board_size = 6
    for n, p in enumerate(g.state.players.values()):
        p.x, p.y, p.ap = n, 0, 5
    registry = {}
    for n in range(3):
        registry[900 + n] = FakeConsole(900 + n, registry)
    server = {"consoles": {str(100 + n): 900 + n for n in range(3)}}
    svc = SimpleNamespace(game=g, server=server, cfg=SimpleNamespace(guild_id=1), now=lambda: NOW,
                          save_server=lambda: None, is_fake=GameService.is_fake,
                          console_of=lambda pid: server["consoles"].get(pid),
                          add_recent=lambda pid, text: server.setdefault("recent", {}).setdefault(pid, []).append(text))
    bot = SimpleNamespace(svc=svc, get_channel=registry.get)
    return g, svc, registry, Router(bot)


class SpyThreadTests(unittest.TestCase):
    def test_open_feed_and_close(self):
        g, svc, registry, router = setup()
        sync = lambda res: asyncio.run(router.sync_spy_channels(res.events))

        sync(g.gift("100", "101", now=NOW))                       # a Gift before anyone spies
        self.assertEqual(svc.server["spy_threads"], {})

        sync(g.buy_info("102", now=NOW))
        th = registry[svc.server["spy_threads"]["102"]]
        self.assertIn("Spy log", th.name)
        self.assertEqual(th.members, {102})                       # only the spy is in it
        content, card = th.sent[0]
        self.assertEqual(content, "<@102>")
        self.assertIn("**Westhaven** `", card.description)        # today's earlier Gift is listed
        self.assertIn("gifted **Veyran Empire**", card.description)
        self.assertIn(th.mention, svc.server["recent"]["102"][0])  # the panel links to it

        sync(g.gift("101", "100", now=NOW.replace(minute=20)))    # a new Gift is forwarded, with a ping
        self.assertEqual(len(th.sent), 2)
        self.assertTrue(th.sent[1][0].startswith("🎁 **Veyran Empire**"))
        self.assertTrue(th.sent[1][0].endswith("-# <@102>"))

        sync(g.expire_info(now=NOW.replace(hour=20, minute=15)))  # 8:15 PM: the spy is removed
        self.assertEqual(th.members, set())
        self.assertTrue(th.archived)
        self.assertEqual(svc.server["spy_threads"], {})

    def test_spies_get_separate_threads_in_their_own_consoles(self):
        g, svc, registry, router = setup()
        asyncio.run(router.sync_spy_channels(g.buy_info("101", now=NOW).events))
        asyncio.run(router.sync_spy_channels(g.buy_info("102", now=NOW).events))
        threads = svc.server["spy_threads"]
        self.assertEqual(set(threads), {"101", "102"})
        self.assertEqual(registry[threads["101"]].members, {101})
        self.assertEqual(registry[threads["102"]].members, {102})


class YourStateCategoryTests(unittest.TestCase):
    """Consoles and pact copies share "Your State"; a full category overflows into another."""

    def test_pacts_sit_with_the_console_and_overflow_when_full(self):
        class Cat:
            def __init__(self, cid, n, position=0):
                self.id, self.channels, self.position = cid, [object()] * n, position

        class Guild:
            me, default_role = object(), object()

            def __init__(self):
                self.chans = {}

            def get_channel(self, cid):
                return self.chans.get(cid)

            async def create_category(self, name, overwrites=None, position=None, reason=None):
                cat = Cat(70 + len(self.chans), 0, position)
                cat.name = name
                self.chans[cat.id] = cat
                return cat

        guild = Guild()
        main = Cat(1, 49)
        guild.chans[1] = main
        console = SimpleNamespace(category=main)
        guild.chans[500] = console
        server = {"console_category_id": 1, "consoles": {"100": 500}}
        svc = SimpleNamespace(server=server, save_server=lambda: None, console_of=lambda pid: server["consoles"].get(pid))
        router = Router(SimpleNamespace(svc=svc))
        run = lambda pid=None: asyncio.run(router.state_category(guild, pid))

        self.assertIs(run("100"), main)                 # room next to the console
        main.channels.append(object())                  # now 50: full
        extra = run("100")
        self.assertIsNot(extra, main)
        self.assertEqual(extra.name, "Your State")
        self.assertEqual(server["state_categories"], [extra.id])
        self.assertIs(run(), extra)                     # reused, not created again


if __name__ == "__main__":
    unittest.main()
