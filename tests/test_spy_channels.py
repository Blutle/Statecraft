"""The per-spy Discord channels: opened on Spy, fed new Gifts, closed when access ends."""
import asyncio
import discord
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


class FakeChannel:
    def __init__(self, cid):
        self.id, self.sent, self.deleted = cid, [], False
        self.mention = f"<#{cid}>"

    async def send(self, content=None, embed=None, allowed_mentions=None):
        self.sent.append((content, embed))

    async def delete(self, reason=None):
        self.deleted = True


class FakeGuild:
    def __init__(self):
        self.channels, self.me, self.default_role = {}, object(), object()

    def get_channel(self, cid):
        ch = self.channels.get(cid)
        return None if ch is None or getattr(ch, "deleted", False) else ch

    async def fetch_member(self, uid):
        return discord.Object(uid)

    async def create_category(self, name, overwrites=None, reason=None):
        cat = FakeChannel(9000 + len(self.channels))
        self.channels[cat.id] = cat
        return cat

    async def create_text_channel(self, name, category=None, topic=None, overwrites=None, reason=None):
        ch = FakeChannel(1000 + len(self.channels))
        self.channels[ch.id] = ch
        return ch


def setup():
    g = Game(rng=random.Random(2))
    for n, name in enumerate(["Westhaven", "Veyran Empire", "Kesh"]):
        g.register(f"U{n}", name, pid=str(100 + n))
    g.start(force=True, now=datetime(2026, 11, 17, 10, 0, tzinfo=CT))
    g.state.board_size = 6
    for n, p in enumerate(g.state.players.values()):
        p.x, p.y, p.ap = n, 0, 5
    server = {"consoles": {}}
    svc = SimpleNamespace(game=g, server=server, cfg=SimpleNamespace(guild_id=1), now=lambda: NOW,
                          save_server=lambda: None, is_fake=GameService.is_fake,
                          console_of=lambda pid: None,
                          add_recent=lambda pid, text: server.setdefault("recent", {}).setdefault(pid, []).append(text))
    guild = FakeGuild()
    bot = SimpleNamespace(svc=svc, get_guild=lambda gid: guild, get_channel=guild.get_channel)
    return g, svc, guild, Router(bot)


class SpyChannelTests(unittest.TestCase):
    def test_open_feed_and_close(self):
        g, svc, guild, router = setup()
        sync = lambda res: asyncio.run(router.sync_spy_channels(res.events))

        sync(g.gift("100", "101", now=NOW))                       # a Gift before anyone spies
        self.assertEqual(svc.server["spy_channels"], {})

        sync(g.buy_info("102", now=NOW))
        ch = guild.get_channel(svc.server["spy_channels"]["102"])
        content, card = ch.sent[0]
        self.assertEqual(content, "<@102>")
        self.assertIn("**Westhaven** `", card.description)        # today's earlier Gift is listed
        self.assertIn("gifted **Veyran Empire**", card.description)
        self.assertIn(ch.mention, svc.server["recent"]["102"][0])     # console points to it

        sync(g.gift("101", "100", now=NOW.replace(minute=20)))    # a new Gift is forwarded, with a ping
        self.assertEqual(len(ch.sent), 2)
        self.assertTrue(ch.sent[1][0].startswith("🎁 **Veyran Empire**"))
        self.assertTrue(ch.sent[1][0].endswith("-# <@102>"))

        sync(g.expire_info(now=NOW.replace(hour=20, minute=15)))  # 8:15 PM: the channel goes
        self.assertTrue(ch.deleted)
        self.assertEqual(svc.server["spy_channels"], {})

    def test_spies_get_separate_channels(self):
        g, svc, guild, router = setup()
        asyncio.run(router.sync_spy_channels(g.buy_info("101", now=NOW).events))
        asyncio.run(router.sync_spy_channels(g.buy_info("102", now=NOW).events))
        chans = svc.server["spy_channels"]
        self.assertEqual(set(chans), {"101", "102"})
        self.assertNotEqual(chans["101"], chans["102"])


if __name__ == "__main__":
    unittest.main()
