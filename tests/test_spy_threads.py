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


class JurorDMThreadTests(unittest.TestCase):
    def test_destruction_closes_conversation_threads(self):
        g, svc, registry, router = setup()
        mine, theirs, other = FakeThread(1, "↔ Kesh"), FakeThread(2, "↔ Westhaven"), FakeThread(3, "↔ VE")
        for th, owner in ((mine, 100), (theirs, 102), (other, 101)):
            th.members.add(owner)
            registry[th.id] = th
        svc.server["dm_threads"] = {"100": {"102": 1}, "102": {"100": 2, "101": 3}}
        svc.set_dm_thread = lambda o, t, tid: GameService.set_dm_thread(svc, o, t, tid)
        asyncio.run(router.close_dm_threads("100"))                    # Westhaven is destroyed
        self.assertEqual(mine.members, set())                          # its own thread leaves its console
        self.assertTrue(mine.archived)
        self.assertEqual(svc.server["dm_threads"]["100"], {})
        self.assertIn("destroyed", theirs.sent[-1][0])                 # Kesh keeps its copy, with a note
        self.assertEqual(theirs.members, {102})
        self.assertEqual(svc.server["dm_threads"]["102"], {"100": 2, "101": 3})
        self.assertEqual(other.sent, [])                               # unrelated conversations untouched


class PlayerCategoryTests(unittest.TestCase):
    """Each player gets their own category: "Your State" until the start, then the State's name."""

    def test_own_category_named_at_the_start(self):
        import discord

        class Cat:
            def __init__(self, cid, name, overwrites):
                self.id, self.name, self.overwrites, self.channels, self.moved = cid, name, overwrites, [], None

            async def move(self, after=None, reason=None):
                self.moved = after

            async def edit(self, name=None, reason=None):
                self.name = name

        class Guild:
            me, default_role = object(), object()

            def __init__(self):
                self.chans = {7: SimpleNamespace(id=7)}       # the Info category

            def get_channel(self, cid):
                return self.chans.get(cid)

            def get_member(self, uid):
                return None

            async def create_category(self, name, overwrites=None, reason=None):
                cat = Cat(70 + len(self.chans), name, overwrites)
                self.chans[cat.id] = cat
                return cat

        guild = Guild()
        g = Game(rng=random.Random(2))
        for n, name in enumerate(["Westhaven", "Veyran Empire"]):
            g.register(f"U{n}", name, pid=str(100 + n))
        server = {"info_category_id": 7}
        svc = SimpleNamespace(game=g, server=server, save_server=lambda: None, cfg=SimpleNamespace(guild_id=1))
        router = Router(SimpleNamespace(svc=svc, get_guild=lambda gid: guild))
        mine = asyncio.run(router.player_category(guild, "100"))
        theirs = asyncio.run(router.player_category(guild, "101"))
        self.assertIsNot(mine, theirs)                                    # one each
        self.assertIs(asyncio.run(router.player_category(guild, "100")), mine)   # reused
        self.assertEqual(mine.name, "Your State")                         # during registration
        self.assertIs(mine.moved, guild.chans[7])                         # placed right below Info
        members = [t for t in mine.overwrites if isinstance(t, discord.Object)]
        self.assertEqual([t.id for t in members], [100])                  # only its owner can see it
        self.assertFalse(mine.overwrites[guild.default_role].view_channel)
        g.start(force=True)
        asyncio.run(router.rename_player_categories())
        self.assertEqual((mine.name, theirs.name), ("Westhaven", "Veyran Empire"))


if __name__ == "__main__":
    unittest.main()
