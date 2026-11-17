"""Statecraft Discord bot.

Run with:  py -m statecraft.discord_bot      (or run_bot.bat)

Layers: slash commands and the control panel (discord_ui) -> GameService
(lock + save) -> Game engine (rules + history). The engine returns Events;
`Router.deliver` sends them to the right channel, console, or webhook.
Nothing reaches players except through an engine call, so everything is in
the history log.

Commands are documented in COMMANDS.md; keep the two in sync.
"""

from __future__ import annotations

import io
import logging
import secrets
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import colours, config, discord_setup, geometry, guides, render_image, schedule
from .discord_format import (HELP, NO_PINGS, action_card, board_message, history_line, own_status, parse_duration, public_status,
                             run_text)
from .discord_service import GameService
from .discord_ui import (UI, BoardRangeSelect, PactInviteButton, PanelButton, PanelSelect, ReplyButton, board_view,
                         pact_summary, send_color_picker)
from .models import Event, Result

log = logging.getLogger("statecraft")

PUBLIC_KINDS_PLAYERS_PING = {"ATTACK", "ELIMINATION", "GAME_OVER", "GAME_START", "REVIVED"}
PUBLIC_KINDS_UPDATES_PING = {"MOVE", "UPGRADE", "INFO_PURCHASE"}
BOARD_CHANGING = {"GAME_START", "MOVE", "ATTACK", "ELIMINATION", "UPGRADE", "DAILY_AP",
                  "JURY_RESULT", "GAME_OVER", "ADMIN_EDIT", "REVIVED"}
PICTURES = {"influence": lambda: render_image.influence_png()}   # illustrations used by the guides
KEEP_ON_FRESH = {"admin_log", "rules", "how_to_play"}   # fresh_channels never wipes these


def _same_card(msg: discord.Message, card: discord.Embed) -> bool:
    """Whether a posted guide message already shows this card (so it needn't be edited)."""
    if len(msg.embeds) != 1:
        return False
    old = msg.embeds[0]
    return (old.title == card.title and old.description == card.description
            and (old.colour and old.colour.value) == (card.colour and card.colour.value)
            and old.footer.text == card.footer.text and bool(old.image.url) == bool(card.image.url))
CHAT_KINDS = {"PUBLIC_CHAT", "ANONYMOUS", "PRIVATE_MESSAGE", "PACT_MESSAGE"}   # no panel refresh needed
DIRECTIONS = [app_commands.Choice(name=n, value=v) for n, v in [
    ("North (up)", "n"), ("North-east", "ne"), ("East (right)", "e"), ("South-east", "se"),
    ("South (down)", "s"), ("South-west", "sw"), ("West (left)", "w"), ("North-west", "nw")]]


# ------------------------------------------------------------------ router

class Router:
    """Delivers engine Events to Discord."""

    def __init__(self, bot: "StatecraftBot"):
        self.bot = bot

    @property
    def svc(self) -> GameService:
        return self.bot.svc

    def channel(self, key: str) -> discord.TextChannel | None:
        cid = self.svc.channel_id(key)
        return self.bot.get_channel(cid) if cid else None

    async def admin_log(self, text: str) -> None:
        ch = self.channel("admin_log")
        if ch:
            await ch.send(text[:2000], allowed_mentions=NO_PINGS)

    async def deliver(self, events: list[Event], echo_dm: bool = True) -> None:
        board_dirty = False
        for ev in events:
            try:
                await self._deliver_one(ev, echo_dm)
            except discord.HTTPException as e:
                log.exception("delivery failed")
                await self.admin_log(f"Delivery failed for {ev.kind}: {e}")
            board_dirty |= ev.kind in BOARD_CHANGING
        if board_dirty:
            await self.refresh_board()
        if any(ev.kind not in CHAT_KINDS for ev in events):
            self.bot.ui.schedule_refresh()

    async def _deliver_one(self, ev: Event, echo_dm: bool = True) -> None:
        if ev.kind == "PRIVATE_MESSAGE":
            await self.deliver_dm(ev.data["sender"], ev.data["recipient"], ev.data["text"], echo_dm)
            return
        if ev.kind == "PUBLIC_CHAT":
            await self.post_as_state(ev.data["state"], ev.data["text"])
        elif ev.kind == "ANONYMOUS":
            ch = self.channel("anonymous_chat")
            if ch:
                await ch.send(ev.data["text"], allowed_mentions=NO_PINGS)
        elif ev.public:
            ch = self.channel("action_log")
            if ch:
                role_key = "players" if ev.kind in PUBLIC_KINDS_PLAYERS_PING else \
                    "updates" if ev.kind in PUBLIC_KINDS_UPDATES_PING else None
                rid = self.svc.role_id(role_key) if role_key else None
                card = action_card(ev, self.svc.game, self.svc.now())
                if rid:   # pings only work in message text, so the mention sits above the card
                    await ch.send(f"<@&{rid}>", embed=card, allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[discord.Object(rid)]))
                else:
                    await ch.send(embed=card, allowed_mentions=NO_PINGS)

        for pid, text in ev.private.items():
            view = None
            if ev.kind == "PACT_INVITE":
                view = discord.ui.View(timeout=None)
                view.add_item(PactInviteButton(ev.data["pact"], "y"))
                view.add_item(PactInviteButton(ev.data["pact"], "n"))
            await self.to_player(pid, text, view=view)

        if ev.kind == "ELIMINATION":
            await self.grant_role(ev.data["victim"], "jury")
        if ev.kind == "REVIVED":
            await self.revoke_role(ev.data["pid"], "jury")
        if ev.kind == "ADMIN_EDIT" and ev.data["field"] == "name":
            await self.rename_dm_threads(ev.data["pid"])
        if ev.kind in ("PACT_JOINED", "PACT_LEFT", "PACT_KICKED", "PACT_MESSAGE"):
            await self._pact(ev)

    # ---- pacts: one private copy of the pact channel per member, relayed

    async def _pact(self, ev: Event) -> None:
        d, game = ev.data, self.svc.game
        pact_id = d["pact"]
        pc = game.state.pacts.get(pact_id)
        if ev.kind == "PACT_MESSAGE":
            for rid in d["recipients"]:
                await self.pact_post(pact_id, rid, d["text"], username=d["state"])
            return
        who = game.get(d["member"]).state_name
        if ev.kind == "PACT_JOINED":
            await self.ensure_pact_copy(pact_id, d["member"])
            if not d.get("founded"):
                await self.pact_notice(pact_id, f"**{who}** joined the pact.", exclude=d["member"])
            return
        # left or kicked
        await self.remove_pact_copy(pact_id, d["member"])
        if d["dissolved"]:
            return
        verb = "left the pact" if ev.kind == "PACT_LEFT" else "was removed from the pact"
        note = f"**{who}** {verb}."
        if d["new_founder"]:
            note += f" **{game.get(d['new_founder']).state_name}** is now the founder."
        await self.pact_notice(pact_id, note)
        if ev.kind == "PACT_KICKED":
            await self.to_player(d["member"], f"You were removed from the pact **{pc['name']}**.")

    async def pact_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        """A pact category with room (Discord allows 50 channels per category)."""
        ids = self.svc.server.setdefault("pact_categories", [])
        for cid in ids:
            cat = guild.get_channel(cid)
            if cat and len(cat.channels) < 50:
                return cat
        cat = await guild.create_category(
            f"Statecraft Pacts {len(ids) + 1}" if ids else "Statecraft Pacts",
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(**discord_setup.BOT_PERMS)},
            reason="Statecraft pacts")
        ids.append(cat.id)
        self.svc.save_server()
        return cat

    async def ensure_pact_copy(self, pact_id: str, pid: str) -> None:
        if self.svc.is_fake(pid) or self.svc.pact_copy(pact_id, pid):
            return
        guild = self.bot.get_guild(self.svc.cfg.guild_id)
        pc = self.svc.game.state.pacts[pact_id]
        member = await guild.fetch_member(int(pid))
        slug = "".join(c if c.isalnum() else "-" for c in pc["name"].lower()).strip("-") or "pact"
        ch = await guild.create_text_channel(
            f"pact-{slug}"[:90], category=await self.pact_category(guild),
            topic="Messages you type here are relayed to the other members under your state name.",
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(**discord_setup.BOT_PERMS),
                        member: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                            read_message_history=True,
                                                            attach_files=True, embed_links=True,
                                                            **discord_setup.PLAYER_PRIVATE)},
            reason="Statecraft pact")
        hook = await ch.create_webhook(name="Statecraft Pact Relay", reason="Statecraft pact")
        self.svc.set_pact_copy(pact_id, pid, {"channel": ch.id, "hook_id": hook.id, "hook_token": hook.token})
        game = self.svc.game
        members = ", ".join(game.get(m).state_name + (" (founder)" if m == pc["founder"] else "")
                            for m in pc["members"])
        await ch.send(f"**Pact: {pc['name']}**\nMembers: {members}\n\nType here to talk. Your messages are relayed "
                      "to the other members under your **state name**, and theirs appear here the same way. "
                      "Nobody sees anyone's Discord account. You won't see messages from before you joined. "
                      "Everything is logged for the post-game summary.", allowed_mentions=NO_PINGS)
        self.bot.ui.schedule_refresh()

    async def remove_pact_copy(self, pact_id: str, pid: str) -> None:
        rec = self.svc.pact_copy(pact_id, pid)
        if not rec:
            return
        self.svc.set_pact_copy(pact_id, pid, None)
        ch = self.bot.get_channel(rec["channel"])
        if ch:
            await ch.delete(reason="Statecraft pact")

    async def pact_post(self, pact_id: str, pid: str, text: str, username: str) -> None:
        if self.svc.is_fake(pid):
            pc = self.svc.game.state.pacts.get(pact_id, {})
            await self.admin_log(f"[pact {pc.get('name')} -> {self.svc.game.get(pid).state_name}] {username}: {text}")
            return
        rec = self.svc.pact_copy(pact_id, pid)
        if rec:
            hook = discord.Webhook.partial(rec["hook_id"], rec["hook_token"], client=self.bot)
            await hook.send(text[:2000], username=username[:80], allowed_mentions=NO_PINGS)

    async def pact_notice(self, pact_id: str, text: str, exclude: str | None = None) -> None:
        pc = self.svc.game.state.pacts.get(pact_id)
        if not pc:
            return
        for m in pc["members"]:
            if m == exclude:
                continue
            if self.svc.is_fake(m):
                continue
            rec = self.svc.pact_copy(pact_id, m)
            ch = self.bot.get_channel(rec["channel"]) if rec else None
            if ch:
                await ch.send(f"*{text}*", allowed_mentions=NO_PINGS)

    async def to_player(self, pid: str, text: str, view: discord.ui.View | None = None) -> None:
        """Tell a player something privately.

        Only messages that need an answer (a `view` with buttons, e.g. a pact
        invitation) are posted in the console, and the panel then moves back
        below them. Everything else becomes a line in the panel's Recent
        activity, so consoles don't fill up with notices."""
        if self.svc.is_fake(pid):
            p = self.svc.game.get(pid)
            await self.admin_log(f"[to {p.state_name if p else pid}] {text}")
            return
        if view is None:
            self.svc.add_recent(pid, text[:300])
            self.bot.ui.schedule_refresh()
            return
        ch = self.bot.get_channel(self.svc.console_of(pid) or 0)
        if ch:
            await ch.send(text[:2000], view=view, allowed_mentions=NO_PINGS)
            self.bot.ui.schedule_repost(pid)

    # ---- DMs: a private thread per correspondent inside each console

    async def open_dm_thread(self, owner: str, other: str) -> discord.Thread | None:
        """The owner's private thread for talking with `other` (created on demand)."""
        if self.svc.is_fake(owner):
            return None
        if tid := self.svc.dm_thread(owner, other):
            th = self.bot.get_channel(tid)
            if th is None:
                try:
                    th = await self.bot.fetch_channel(tid)
                except (discord.NotFound, discord.Forbidden):
                    th = None
            if th is not None:
                return th
            self.svc.set_dm_thread(owner, other, None)
        console = self.bot.get_channel(self.svc.console_of(owner) or 0)
        if not console:
            return None
        o = self.svc.game.get(other)
        th = await console.create_thread(name=f"↔ {o.state_name}"[:100], type=discord.ChannelType.private_thread,
                                         invitable=False, auto_archive_duration=10080,
                                         reason="Statecraft private conversation")
        await th.add_user(discord.Object(int(owner)))
        self.svc.set_dm_thread(owner, other, th.id)
        # Remove the "started a thread" notice from the console, if Discord posted one
        try:
            async for m in console.history(limit=5):
                if m.type == discord.MessageType.thread_created:
                    await m.delete()
        except discord.HTTPException:
            pass
        await th.send(f"Private conversation with **{o.state_name}**. Type here: your messages are relayed to them "
                      "under your state name, and theirs appear here. Nobody sees anyone's Discord account. "
                      "Everything is logged for the post-game summary.", allowed_mentions=NO_PINGS)
        return th

    async def console_webhook(self, pid: str) -> discord.Webhook | None:
        rec = self.svc.console_hook(pid)
        if rec:
            return discord.Webhook.partial(rec["id"], rec["token"], client=self.bot)
        console = self.bot.get_channel(self.svc.console_of(pid) or 0)
        if not console:
            return None
        hook = await console.create_webhook(name="Statecraft Relay", reason="Statecraft DM relay")
        self.svc.set_console_hook(pid, {"id": hook.id, "token": hook.token})
        return hook

    async def post_in_dm_thread(self, owner: str, other: str, text: str, username: str) -> None:
        th = await self.open_dm_thread(owner, other)
        hook = await self.console_webhook(owner)
        if not th or not hook:
            return
        try:
            await hook.send(text[:2000], username=username[:80], thread=th, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            await th.edit(archived=False)                   # archived after a quiet week: reopen and retry
            await hook.send(text[:2000], username=username[:80], thread=th, allowed_mentions=NO_PINGS)

    async def deliver_dm(self, sender: str, recipient: str, text: str, echo: bool) -> None:
        g = self.svc.game
        s_name, r_name = g.get(sender).state_name, g.get(recipient).state_name
        if self.svc.is_fake(recipient):
            await self.admin_log(f"[DM {s_name} -> {r_name}] {text}")
        else:
            await self.post_in_dm_thread(recipient, sender, text, s_name)
        if echo and not self.svc.is_fake(sender):
            await self.post_in_dm_thread(sender, recipient, text, f"{s_name} (you)")

    async def post_as_state(self, state_name: str, text: str) -> None:
        hook = await self.bot.public_chat_webhook()
        if hook:
            await hook.send(text, username=state_name[:80], allowed_mentions=NO_PINGS)

    async def grant_role(self, pid: str, key: str) -> None:
        if self.svc.is_fake(pid):
            return
        guild = self.bot.get_guild(self.svc.cfg.guild_id)
        rid = self.svc.role_id(key)
        if not guild or not rid:
            return
        try:
            member = await guild.fetch_member(int(pid))
            await member.add_roles(discord.Object(rid), reason="Statecraft")
        except (discord.NotFound, ValueError):
            pass

    async def revoke_role(self, pid: str, key: str) -> None:
        if self.svc.is_fake(pid):
            return
        guild = self.bot.get_guild(self.svc.cfg.guild_id)
        rid = self.svc.role_id(key)
        if not guild or not rid:
            return
        try:
            member = await guild.fetch_member(int(pid))
            await member.remove_roles(discord.Object(rid), reason="Statecraft")
        except (discord.NotFound, ValueError):
            pass

    async def rename_dm_threads(self, pid: str) -> None:
        """After a referee rename, retitle other players' conversation threads with this state."""
        name = self.svc.game.get(pid).state_name
        for owner, threads in self.svc.server.get("dm_threads", {}).items():
            tid = threads.get(pid)
            th = self.bot.get_channel(tid) if tid else None
            if th:
                try:
                    await th.edit(name=f"↔ {name}"[:100])
                except discord.HTTPException:
                    pass

    async def post_guides(self) -> list[str]:
        """Post (or update in place) the #rules and #how-to-play guides, and
        the pinned welcome card in #register."""
        report = []
        s = self.svc.game.s

        def mention(key: str) -> str:
            cid = self.svc.channel_id(key)
            return f"<#{cid}>" if cid else f"#{key.replace('_', '-')}"

        welcome = guides.register(s, mention("rules"), mention("how_to_play"))
        for key, sections in (("rules", guides.rules(s)), ("how_to_play", guides.how_to_play(s)),
                              ("register", welcome)):
            ch = self.channel(key)
            if not ch:
                report.append(f"no #{key.replace('_', '-')} channel (run /admin setup)")
                continue
            cards, pictures = [], []
            for title, body, *pic in sections:
                card = discord.Embed(title=title, description=body, colour=0x1ABC9C if key == "rules" else 0x5865F2)
                if pic:
                    card.set_image(url=f"attachment://{pic[0]}.png")
                    if len(pic) > 1:
                        card.set_footer(text=pic[1])           # sits underneath the picture
                    pictures.append((f"{pic[0]}.png", PICTURES[pic[0]]()))
                else:
                    pictures.append(None)
                cards.append(card)

            def files(n):
                return [discord.File(io.BytesIO(pictures[n][1]), filename=pictures[n][0])] if pictures[n] else []

            # One history read per channel, then only touch cards that changed:
            # Discord allows ~5 edits per 5 seconds per channel, so re-editing
            # every card on every run is what made /admin guides crawl.
            stored = self.svc.server.setdefault("guides", {}).get(key, [])
            posted = {m.id: m async for m in ch.history(limit=100) if m.author.id == self.bot.user.id}
            if stored and len(stored) == len(cards) and all(mid in posted for mid in stored):
                changed = 0
                for n, (mid, card) in enumerate(zip(stored, cards)):
                    if not _same_card(posted[mid], card):
                        await posted[mid].edit(embed=card, attachments=files(n))
                        changed += 1
                report.append(f"#{ch.name}: {changed} of {len(cards)} cards updated" if changed
                              else f"#{ch.name}: already up to date")
                ids = stored
            else:
                for mid in stored:                               # layout changed: replace the set
                    if mid in posted:
                        try:
                            await posted[mid].delete()
                        except discord.HTTPException:
                            pass
                ids = [(await ch.send(embed=card, files=files(n), allowed_mentions=NO_PINGS)).id
                       for n, card in enumerate(cards)]
                self.svc.server["guides"][key] = ids
                self.svc.save_server()
                report.append(f"posted #{ch.name} ({len(cards)} cards)")
            if key == "register":
                msg = posted.get(ids[0])
                if not (msg and msg.pinned):
                    report.append(await self._pin(ch, ids[0]))
        report += await self._remove_stray_guide_copies()
        return report

    async def _pin(self, ch: discord.TextChannel, mid: int) -> str:
        """Pin a message and remove Discord's "pinned a message" notice."""
        if not ch.permissions_for(ch.guild.me).pin_messages:
            return (f"⚠️ Couldn't pin the #{ch.name} welcome: the bot's role is missing **Pin Messages**. "
                    "Turn it on (Server Settings → Roles → the bot's role), then run `/admin guides` again.")
        try:
            await ch.get_partial_message(mid).pin(reason="Statecraft welcome")
        except discord.HTTPException as e:
            return f"⚠️ Couldn't pin the #{ch.name} welcome ({e.text or e.status})."
        async for m in ch.history(limit=5):
            if m.type == discord.MessageType.pins_add and m.author.id == self.bot.user.id:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
        return f"pinned the #{ch.name} welcome"

    async def _remove_stray_guide_copies(self) -> list[str]:
        """Delete bot messages in the guide channels that aren't the tracked guide cards."""
        report = []
        for key in ("rules", "how_to_play", "register"):
            ch = self.channel(key)
            keep = set(self.svc.server.get("guides", {}).get(key, []))
            if not ch or not keep:
                continue
            stray = [m async for m in ch.history(limit=200) if m.author.id == self.bot.user.id and m.id not in keep]
            for m in stray:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
            if stray:
                report.append(f"removed {len(stray)} old guide message(s) from #{ch.name}")
        return report

    async def refresh_board(self) -> None:
        ch = self.channel("game_board")
        game = self.svc.game
        if not ch or game.state.board_size == 0:
            return
        emb, file = await board_message(game, public=True)
        view = board_view(game) or discord.ui.View()          # an empty view clears the menu at game end
        mid = self.svc.server.get("board_message_id")
        if mid:
            try:
                await ch.get_partial_message(mid).edit(embed=emb, attachments=[file], view=view)
                return
            except discord.NotFound:
                emb, file = await board_message(game, public=True)
        msg = await ch.send(embed=emb, file=file, view=view, allowed_mentions=NO_PINGS)
        self.svc.server["board_message_id"] = msg.id
        self.svc.save_server()


# --------------------------------------------------------------------- bot

class StatecraftBot(discord.Client):
    def __init__(self, cfg: config.Config):
        intents = discord.Intents.default()
        intents.message_content = True          # pact channels relay typed messages
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.cfg = cfg
        self.svc = GameService(cfg)
        self.router = Router(self)
        self.ui = UI(self)
        self.tree = app_commands.CommandTree(self)
        self._webhook: discord.Webhook | None = None
        register_commands(self)

    async def setup_hook(self):
        self.add_dynamic_items(ReplyButton, PanelButton, PanelSelect, PactInviteButton, BoardRangeSelect)
        guild = discord.Object(self.cfg.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.scheduler.start()

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        await self.router.admin_log(f"Statecraft bot online. Phase: {self.svc.game.state.phase}, "
                                    f"day {self.svc.game.state.day}."
                                    + (" **TEST MODE**" if self.cfg.test_mode else ""))
        self.ui.schedule_refresh()

    async def on_message(self, message: discord.Message):
        """Relay messages typed in a pact channel copy to the other members."""
        if message.author.bot or message.webhook_id:
            return
        dm = self.svc.dm_of_thread(message.channel.id)
        if dm:
            owner, other = dm
            if str(message.author.id) != owner:
                return
            text = message.content.strip()
            if message.attachments:
                text = (text + "\n" + "\n".join(a.url for a in message.attachments)).strip()
            if not text:
                return
            res = await self.svc.run(lambda g, now: g.dm(owner, other, text))
            if not res.ok:
                await message.reply(f"Not delivered: {res.message}", allowed_mentions=NO_PINGS)
                return
            await self.router.deliver(res.events, echo_dm=False)
            return
        found = self.svc.pact_of_channel(message.channel.id)
        if not found:
            return
        pact_id, owner = found
        if str(message.author.id) != owner:          # e.g. the referee looking in
            return
        text = message.content.strip()
        if message.attachments:
            text = (text + "\n" + "\n".join(a.url for a in message.attachments)).strip()
        if not text:
            return
        res = await self.svc.run(lambda g, now: g.pact_message(owner, pact_id, text))
        if not res.ok:
            await message.reply(f"Not relayed: {res.message}", allowed_mentions=NO_PINGS)
            return
        await self.router.deliver(res.events)

    async def public_chat_webhook(self) -> discord.Webhook | None:
        if self._webhook:
            return self._webhook
        ch = self.router.channel("public_chat")
        hid = self.svc.server.get("public_chat_webhook_id")
        if ch and hid:
            self._webhook = next((h for h in await ch.webhooks() if h.id == hid), None)
        return self._webhook

    @tasks.loop(seconds=30)
    async def scheduler(self):
        res = await self.svc.run(lambda g, now: g.run_due_events(now))
        if res.events:
            await self.router.deliver(res.events)
        if res.message:
            await self.router.admin_log(f"Scheduler ran: {res.message}")

    @scheduler.before_loop
    async def _wait_ready(self):
        await self.wait_until_ready()


# ---------------------------------------------------------------- commands

def register_commands(bot: StatecraftBot) -> None:
    tree, svc, ui = bot.tree, bot.svc, bot.ui

    def is_admin(i: discord.Interaction) -> bool:
        return i.user.id in bot.cfg.admin_ids

    async def states_autocomplete(i: discord.Interaction, current: str, alive_only: bool):
        if svc.game.state.phase == "registration" and not is_admin(i):
            return []                      # who has joined stays secret until the start
        cur = current.lower()
        out = []
        for p in svc.game.state.players.values():
            if alive_only and not p.alive:
                continue
            if cur in p.state_name.lower() or cur == p.marker.lower():
                out.append(app_commands.Choice(name=f"{p.state_name} [{p.marker}]"[:100], value=p.id))
        return out[:25]

    async def alive_states(i, current):
        return await states_autocomplete(i, current, True)

    async def any_states(i, current):
        return await states_autocomplete(i, current, False)

    # ---- registration
    # /join takes no name on purpose: if a slash command misfires in #register
    # it posts as plain text, so the state name is only ever entered in the
    # private console.
    @tree.command(description="Join the next game: creates your private console")
    async def join(i: discord.Interaction):
        if i.channel_id != svc.channel_id("register"):
            await i.response.send_message(f"Use `/join` in <#{svc.channel_id('register')}>.", ephemeral=True)
            return
        if svc.game.state.phase != "registration":
            await i.response.send_message("Registration is closed; a game is in progress.", ephemeral=True)
            return
        pid = str(i.user.id)
        existing = svc.console_of(pid)
        if existing and bot.get_channel(existing):
            await i.response.send_message(f"You've already joined. Your console is <#{existing}>.", ephemeral=True)
            return
        await i.response.defer(ephemeral=True, thinking=True)
        console = await create_console(i.guild, i.user)
        svc.set_console(pid, console.id)
        await bot.router.grant_role(pid, "players")
        await console.send("Welcome to Statecraft. This is your **private console**: only you (and the referee) "
                           "can see it.\n\nEverything you need is on the **control panel** below (it always stays at the bottom): start by "
                           "tapping **Name my state**. Slash commands work here too (`/help` lists them).",
                           allowed_mentions=NO_PINGS)
        await ui.post_panel(pid)
        await i.followup.send(f"You're in. Head to your console, {console.mention}, and name your state.",
                              ephemeral=True)

    async def create_console(guild: discord.Guild, member: discord.abc.User) -> discord.TextChannel:
        cat = guild.get_channel(svc.server.get("console_category_id"))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(**discord_setup.BOT_PERMS),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                use_application_commands=True,
                                                send_messages_in_threads=True, **discord_setup.PLAYER_PRIVATE),
        }
        return await guild.create_text_channel(f"console-{secrets.token_hex(2)}", category=cat,
                                               overwrites=overwrites, reason="Statecraft console")

    @tree.command(description="Name (or rename) your state before the game starts")
    @app_commands.describe(name="Your state's name, up to 24 characters",
                           abbreviation="3 letters shown on the board, e.g. IRN (leave out for automatic)")
    async def name(i: discord.Interaction, name: app_commands.Range[str, 1, 24],
                   abbreviation: app_commands.Range[str, 3, 3] | None = None):
        if pid := await ui.console_pid(i, need_state=False):
            await ui.reply(i, await svc.run(lambda g, now: g.register(i.user.display_name, name, pid=pid,
                                                                      abbreviation=abbreviation)))

    @tree.command(description="Pick your State's color before the game starts (first come, first served)")
    @app_commands.choices(color=[app_commands.Choice(name=c, value=c) for c in colours.PALETTE])
    async def color(i: discord.Interaction, color: app_commands.Choice[str] | None = None):
        if not (pid := await ui.console_pid(i)):
            return
        if color is None:
            await send_color_picker(ui, i, pid)
            return
        await ui.reply(i, await svc.run(lambda g, now: g.set_color(pid, color.value)))

    @tree.command(description="Re-post your control panel at the bottom of your console")
    async def panel(i: discord.Interaction):
        if pid := await ui.console_pid(i, need_state=False):
            await i.response.send_message("Here's a fresh panel.", ephemeral=True)
            await ui.post_panel(pid)

    @tree.command(description="Withdraw from the game before it starts")
    async def leave(i: discord.Interaction):
        pid = str(i.user.id)
        cid = svc.console_of(pid)
        if not cid and not svc.game.get(pid):
            await i.response.send_message("You haven't joined.", ephemeral=True)
            return
        if svc.game.state.phase != "registration":
            await i.response.send_message("Rejected: you can only leave before the game starts.", ephemeral=True)
            return

        async def go(ci: discord.Interaction):
            if svc.game.get(pid):
                res = await svc.run(lambda g, now: g.unregister(pid))
                if not res.ok:
                    await ci.response.edit_message(content=f"Rejected: {res.message}", view=None)
                    return
            # Respond before deleting: the command may have been run in the console itself.
            await ci.response.edit_message(content="You've left the game. Your console is being deleted.", view=None)
            svc.set_console(pid, None)
            for key in ("panels", "dm_threads", "console_hooks", "recent"):
                svc.server.get(key, {}).pop(pid, None)
            svc.save_server()
            ch = bot.get_channel(cid) if cid else None
            if ch:
                await ch.delete(reason="Statecraft: player left")
            rid = svc.role_id("players")
            if rid and isinstance(ci.user, discord.Member):
                await ci.user.remove_roles(discord.Object(rid))

        await ui.ask(i, "Leave the game? Your state and your console will be deleted.", go, ephemeral=True)

    # ---- actions (all confirmed first)
    @tree.command(description="Move one square (1 AP)")
    @app_commands.choices(direction=DIRECTIONS)
    async def move(i: discord.Interaction, direction: app_commands.Choice[str]):
        if pid := await ui.console_pid(i):
            p = svc.game.get(pid)
            dx, dy = geometry.DIRECTIONS[direction.value]
            dest = geometry.coord_label(p.x + dx, p.y + dy) if p.x is not None else "?"
            await ui.confirm_action(i, f"Move **{direction.name}** from {svc.game.pos(p)} to **{dest}**? "
                                       + ui.ap_note(pid, svc.game.s["move_cost"]),
                                    lambda g, now: g.move(pid, direction.value, now=now))

    @tree.command(description="Attack a State within your Influence (1 AP)")
    @app_commands.autocomplete(state=alive_states)
    async def attack(i: discord.Interaction, state: str):
        if pid := await ui.console_pid(i):
            tid, err = ui.resolve(state)
            if not tid:
                await ui.reply(i, Result.fail(err))
                return
            t = svc.game.get(tid)
            await ui.confirm_action(i, f"Attack **{t.state_name}** [{t.marker}] ({t.hp} HP)? "
                                       + ui.ap_note(pid, svc.game.s["attack_cost"]),
                                    lambda g, now: g.attack(pid, tid, now=now))

    @tree.command(description="Increase your Influence by 1 (2 AP)")
    async def upgrade(i: discord.Interaction):
        if pid := await ui.console_pid(i):
            r = svc.game.range_of(svc.game.get(pid))
            await ui.confirm_action(i, f"Upgrade your Influence from {r} to **{r + 1}**? "
                                       + ui.ap_note(pid, svc.game.s["upgrade_cost"]),
                                    lambda g, now: g.upgrade(pid, now=now))

    @tree.command(description="Gift 1 AP to a State within your Influence (private)")
    @app_commands.autocomplete(state=alive_states)
    async def gift(i: discord.Interaction, state: str):
        if pid := await ui.console_pid(i):
            tid, err = ui.resolve(state)
            if not tid:
                await ui.reply(i, Result.fail(err))
                return
            t = svc.game.get(tid)
            n = svc.game.s["gift_amount"]
            await ui.confirm_action(i, f"Gift **{n} AP** to **{t.state_name}** [{t.marker}]? Gifts are private. "
                                       + ui.ap_note(pid, n),
                                    lambda g, now: g.gift(pid, tid, now=now))

    @tree.command(description="Spy: see the top-secret log of today's Gifts until 8:15 PM (1 AP)")
    async def spy(i: discord.Interaction):
        if pid := await ui.console_pid(i):
            await ui.confirm_action(i, "**Spy**: see the top-secret log of every Gift made today (until 8:15 PM)? "
                                       "Spying is announced publicly. " + ui.ap_note(pid, svc.game.s["info_cost"]),
                                    lambda g, now: g.buy_info(pid, now=now))

    @tree.command(description="Jury: secretly vote for a surviving state (8:00 PM-1:45 PM)")
    @app_commands.autocomplete(state=alive_states)
    async def vote(i: discord.Interaction, state: str):
        if pid := await ui.console_pid(i):
            tid, err = ui.resolve(state)
            await ui.reply(i, await svc.run(lambda g, now: g.vote(pid, tid, now=now)) if tid else Result.fail(err))

    # ---- info
    @tree.command(description="Your status, or another state's public status")
    @app_commands.autocomplete(state=any_states)
    async def status(i: discord.Interaction, state: str | None = None):
        if not (pid := await ui.console_pid(i)):
            return
        if state is None or ui.resolve(state)[0] == pid:
            await i.response.send_message(own_status(svc.game, pid, svc.now()), ephemeral=True)
            return
        tid, err = ui.resolve(state)
        await i.response.send_message(public_status(svc.game, pid, tid) if tid else f"Rejected: {err}",
                                      allowed_mentions=NO_PINGS, ephemeral=True)

    @tree.command(description="Show the board here, with a State's Influence highlighted")
    @app_commands.autocomplete(state=any_states)
    async def board(i: discord.Interaction, state: str | None = None):
        if not (pid := await ui.console_pid(i)):
            return
        focus = ui.resolve(state)[0] if state else pid
        await ui.send_board(i, focus=focus or pid)

    @tree.command(description="Re-show today's Spy log (needs /spy)")
    async def spylog(i: discord.Interaction):
        if pid := await ui.console_pid(i):
            if pid in svc.game.state.info_access:
                await i.response.send_message(f"```\n{svc.game.gift_report()}\n```", ephemeral=True)
            else:
                await i.response.send_message("You're not spying today. Use `/spy` (1 AP).",
                                              ephemeral=True)

    @tree.command(name="help", description="Commands and rules summary")
    async def help_cmd(i: discord.Interaction):
        await i.response.send_message(HELP, ephemeral=True)

    @tree.command(description="Opt in or out of pings for minor events")
    @app_commands.choices(setting=[app_commands.Choice(name="on", value="on"),
                                   app_commands.Choice(name="off", value="off")])
    async def notify(i: discord.Interaction, setting: app_commands.Choice[str]):
        if not await ui.console_pid(i):
            return
        rid = svc.role_id("updates")
        if rid and isinstance(i.user, discord.Member):
            if setting.value == "on":
                await i.user.add_roles(discord.Object(rid))
            else:
                await i.user.remove_roles(discord.Object(rid))
        await i.response.send_message(f"Minor-event pings are now **{setting.value}**.", ephemeral=True)

    # ---- communication
    @tree.command(description="Post in #public-chat under your state's name")
    async def say(i: discord.Interaction, message: app_commands.Range[str, 1, 1500]):
        if pid := await ui.console_pid(i):
            await ui.reply(i, await svc.run(lambda g, now: g.say(pid, message)))

    @tree.command(description="Send a private message to another state")
    @app_commands.autocomplete(state=any_states)
    async def dm(i: discord.Interaction, state: str, message: app_commands.Range[str, 1, 1500]):
        if pid := await ui.console_pid(i):
            tid, err = ui.resolve(state)
            await ui.reply(i, await svc.run(lambda g, now: g.dm(pid, tid, message)) if tid else Result.fail(err))

    @tree.command(description="Post anonymously in #anonymous-chat")
    async def anon(i: discord.Interaction, message: app_commands.Range[str, 1, 1500]):
        if pid := await ui.console_pid(i):
            await ui.reply(i, await svc.run(lambda g, now: g.anon(pid, message)))

    # ---- pacts
    pact = app_commands.Group(name="pact", description="Private, anonymous group chats with other states")

    async def my_pacts(i: discord.Interaction, current: str):
        pid, cur = str(i.user.id), current.lower()
        return [app_commands.Choice(name=pc["name"][:100], value=pc["id"])
                for pc in svc.game.pacts_of(pid) if cur in pc["name"].lower()][:25]

    def resolve_pact(pid: str, q: str) -> str | None:
        for pc in svc.game.pacts_of(pid):
            if q in (pc["id"], pc["name"]) or pc["name"].lower() == q.lower():
                return pc["id"]
        return None

    @pact.command(name="create", description="Found a pact (you can found up to 3)")
    async def pact_create(i: discord.Interaction, name: app_commands.Range[str, 1, 40]):
        if pid := await ui.console_pid(i):
            await ui.reply(i, await svc.run(lambda g, now: g.create_pact(pid, name)))

    @pact.command(name="invite", description="Founder only: invite a state to your pact")
    @app_commands.autocomplete(pact=my_pacts, state=any_states)
    async def pact_invite(i: discord.Interaction, pact: str, state: str):
        if pid := await ui.console_pid(i):
            pact_id, (tid, err) = resolve_pact(pid, pact), ui.resolve(state)
            if not pact_id or not tid:
                await ui.reply(i, Result.fail("You're not in that pact." if not pact_id else err))
                return
            await ui.reply(i, await svc.run(lambda g, now: g.invite_to_pact(pid, pact_id, tid)))

    @pact.command(name="leave", description="Leave a pact")
    @app_commands.autocomplete(pact=my_pacts)
    async def pact_leave(i: discord.Interaction, pact: str):
        if pid := await ui.console_pid(i):
            pact_id = resolve_pact(pid, pact)
            if not pact_id:
                await ui.reply(i, Result.fail("You're not in that pact."))
                return
            name = svc.game.pact(pact_id)["name"]
            await ui.confirm_action(i, f"Leave **{name}**? Your copy of its channel will be deleted.",
                                    lambda g, now: g.leave_pact(pid, pact_id))

    @pact.command(name="kick", description="Founder only: remove a member")
    @app_commands.autocomplete(pact=my_pacts, state=any_states)
    async def pact_kick(i: discord.Interaction, pact: str, state: str):
        if pid := await ui.console_pid(i):
            pact_id, (tid, err) = resolve_pact(pid, pact), ui.resolve(state)
            if not pact_id or not tid:
                await ui.reply(i, Result.fail("You're not in that pact." if not pact_id else err))
                return
            pc, t = svc.game.pact(pact_id), svc.game.get(tid)
            await ui.confirm_action(i, f"Remove **{t.state_name}** from **{pc['name']}**?",
                                    lambda g, now: g.kick_from_pact(pid, pact_id, tid))

    @pact.command(name="list", description="Your pacts, their members, and pending invitations")
    async def pact_list(i: discord.Interaction):
        if pid := await ui.console_pid(i):
            await i.response.send_message(pact_summary(svc.game, pid), allowed_mentions=NO_PINGS, ephemeral=True)

    tree.add_command(pact)

    async def delete_game_channels(guild: discord.Guild, people: set[str]) -> None:
        """Delete consoles and pact channels, and remove game roles. Keeps game.json.

        Works from the records *and* by location: every channel in the
        console category and in any "Statecraft Pacts" category goes, even
        ones the records lost track of. Discord rate-limits bulk deletes;
        discord.py waits and retries, so this can take a minute."""
        async def drop(ch) -> None:
            try:
                await ch.delete(reason="Statecraft cleanup")
            except discord.NotFound:
                pass

        doomed = {cid for cid in svc.server.get("consoles", {}).values()}
        doomed |= {rec["channel"] for copies in svc.pact_channels().values() for rec in copies.values()}
        console_cat = guild.get_channel(svc.server.get("console_category_id") or 0)
        pact_cats = {guild.get_channel(cid) for cid in svc.server.get("pact_categories", [])}
        pact_cats |= {c for c in guild.categories if c.name.startswith("Statecraft Pacts")}
        pact_cats.discard(None)
        for cat in [console_cat, *pact_cats]:
            if cat:
                doomed |= {ch.id for ch in cat.channels}
        for cid in doomed:
            ch = guild.get_channel(cid)
            if ch:
                await drop(ch)
        for cat in pact_cats:
            await drop(cat)
        for pid in list(svc.server.get("consoles", {})):
            svc.set_console(pid, None)
        svc.server["pact_channels"] = {}
        svc.server["pact_categories"] = []
        for key in ("panels", "dm_threads", "console_hooks", "recent"):
            svc.server[key] = {}
        # Remove game roles from each known player (no member-list intent needed)
        role_ids = [svc.role_id(k) for k in ("players", "updates", "jury") if svc.role_id(k)]
        for pid in people:
            if svc.is_fake(pid):
                continue
            try:
                member = await guild.fetch_member(int(pid))
                await member.remove_roles(*[discord.Object(r) for r in role_ids], reason="Statecraft cleanup")
            except (discord.NotFound, ValueError):
                pass
        svc.save_server()

    # ---- admin
    admin = app_commands.Group(name="admin", description="Referee tools",
                               default_permissions=discord.Permissions(manage_guild=True))

    async def admin_only(i: discord.Interaction) -> bool:
        if not is_admin(i):
            await i.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    @admin.command(description="Create or repair roles, channels, and permissions")
    async def setup(i: discord.Interaction):
        if not await admin_only(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        keep = {k: v for k, v in svc.server.items() if k not in ("roles", "channels", "category_id",
                                                               "console_category_id", "public_chat_webhook_id")}
        report = await discord_setup.ensure_server(i.guild, svc.cfg.server_file)
        svc.server = {**discord_setup.load_server(svc.cfg.server_file), **keep}
        svc.save_server()
        bot._webhook = None
        report += await bot.router.post_guides()
        await i.followup.send("\n".join(report)[:2000], ephemeral=True)

    @admin.command(name="guides", description="Post or update the #rules and #how-to-play guides")
    async def guides_cmd(i: discord.Interaction):
        if not await admin_only(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        await i.followup.send("\n".join(await bot.router.post_guides()), ephemeral=True)


    @admin.command(description="Start the game (needs 12+ players)")
    async def start(i: discord.Interaction):
        if await admin_only(i):
            unnamed = [pid for pid in svc.server["consoles"] if not svc.game.get(pid)]
            if unnamed:
                await i.response.send_message(
                    f"Rejected: {len(unnamed)} player(s) joined but haven't named a state: "
                    + ", ".join(f"<@{p}>" for p in unnamed) + ". They need to name it on their panel "
                    "(or `/leave`).", ephemeral=True, allowed_mentions=NO_PINGS)
                return
            await ui.reply(i, await svc.run(lambda g, now: g.start(now=now)), ephemeral=True)

    @admin.command(name="status", description="Full game view, including owners and AP")
    async def admin_status(i: discord.Interaction):
        if not await admin_only(i):
            return
        g = svc.game
        lines = [f"Phase {g.state.phase}, day {g.state.day}, {len(g.state.players)} players, "
                 f"game time {schedule.fmt(svc.now(), g.s)}"]
        for p in g.state.players.values():
            cd = g.cooldown_remaining(p, svc.now())
            lines.append(f"{p.marker:<3} {p.state_name:<24} {p.owner_name[:16]:<16} AP {p.ap:<3} HP {p.hp} "
                         f"{g.pos(p):<4} Inf{g.range_of(p)} {'alive' if p.alive else 'dead'}"
                         + (f" cd {schedule.fmt_duration(cd)}" if cd else ""))
        if g.state.jury_votes:
            lines.append("Ballots: " + ", ".join(f"{g.get(j).state_name}->{g.get(t).state_name}"
                                                 for j, t in g.state.jury_votes.items()))
        if g.state.info_access:
            lines.append("Spying: " + ", ".join(g.get(p).state_name for p in g.state.info_access))
        await i.response.send_message("```\n" + "\n".join(lines)[:1900] + "\n```", ephemeral=True)

    @admin.command(description="Add (or remove, if negative) AP")
    @app_commands.autocomplete(state=any_states)
    async def give(i: discord.Interaction, state: str, amount: int):
        if not await admin_only(i):
            return
        tid, err = ui.resolve(state, admin=True)
        await ui.reply(i, await svc.run(lambda g, now: g.give_ap(tid, amount)) if tid else Result.fail(err),
                       ephemeral=True)

    @admin.command(description="Set everyone's action cooldown, in seconds (the normal game uses 900 = 15 minutes)")
    @app_commands.describe(seconds="e.g. 30 for a quick test; 900 puts the normal 15 minutes back")
    async def cooldown(i: discord.Interaction, seconds: app_commands.Range[int, 0, 86400]):
        if not await admin_only(i):
            return
        await ui.reply(i, await svc.run(lambda g, now: g.set_cooldown(seconds)), ephemeral=True)

    @admin.command(description="Edit any stored value of a State: name, abbreviation, AP, HP, Influence, position...")
    @app_commands.autocomplete(state=any_states)
    @app_commands.choices(field=[app_commands.Choice(name=n, value=v) for n, v in [
        ("Name", "name"), ("Abbreviation", "abbreviation"), ("AP", "ap"), ("HP (0 destroys, >0 revives)", "hp"),
        ("Influence", "range"), ("Position (square like C5)", "position"), ("Cooldown (value: reset)", "cooldown"),
        ("Owner name (revealed on elimination)", "owner"), ("Color (palette name)", "color")]])
    @app_commands.describe(value="New value: text, a number, a square like C5, or 'reset' for cooldown",
                           announce="Post changes to public stats in #action-log (default: yes)")
    async def edit(i: discord.Interaction, state: str, field: app_commands.Choice[str], value: str,
                   announce: bool = True):
        if not await admin_only(i):
            return
        tid, err = ui.resolve(state, admin=True)
        if not tid:
            await i.response.send_message(f"Rejected: {err}", ephemeral=True)
            return
        g = svc.game
        p = g.get(tid)
        current = g.admin_value(p, field.value)
        visibility = ("announced in #action-log" if announce and field.value in g.PUBLIC_FIELDS
                      else "not announced publicly")
        warn = ""
        if field.value == "hp" and value.strip() == "0" and p.alive:
            warn = "\n**This destroys the state** and reveals its owner."
        elif field.value == "hp" and not p.alive:
            warn = "\n**This revives the state** and removes its Jury role."
        await ui.confirm_action(
            i, f"Set **{p.state_name}**'s {g.ADMIN_FIELDS[field.value]} from **{current}** to **{value}**? "
               f"The player is told privately; this change is {visibility}.{warn}",
            lambda g, now: g.admin_edit(tid, field.value, value, announce))

    @admin.command(description="End the game with these states as (joint) winners")
    @app_commands.describe(states="Markers or names separated by commas, e.g. VE, WE")
    async def declare(i: discord.Interaction, states: str):
        if not await admin_only(i):
            return
        ids, errors = [], []
        for q in [x.strip() for x in states.split(",") if x.strip()]:
            tid, err = ui.resolve(q, admin=True)
            (ids.append(tid) if tid else errors.append(err))
        if errors:
            await i.response.send_message("Rejected: " + " ".join(errors), ephemeral=True)
            return
        names = ", ".join(svc.game.get(t).state_name for t in ids)
        await ui.confirm_action(i, f"**End the game now** with winner(s): **{names}**? This can't be undone.",
                                lambda g, now: g.declare_winners(ids), ephemeral=True)

    @admin.command(description="Rank jurors by votes for the winner(s)")
    async def juryscore(i: discord.Interaction):
        if not await admin_only(i):
            return
        rows = svc.game.jury_scores()
        if not rows:
            await i.response.send_message("Available once the game has ended.", ephemeral=True)
            return
        lines = [f"{r['player'].owner_name[:16]:<16} {r['player'].state_name[:22]:<22} for winner {r['for_winner']:>2}"
                 f"  ballots {r['ballots']:>2}  nights {r['nights']:>2}  rate {r['rate']:.0%}" for r in rows]
        await i.response.send_message("```\n" + "\n".join(lines)[:1900] + "\n```", ephemeral=True)

    @admin.command(description="Every state's owner, console, conversation threads, and pact channels")
    async def consoles(i: discord.Interaction):
        if not await admin_only(i):
            return
        g = svc.game
        pact_copies: dict[str, list[str]] = {}
        for pact_id, copies in svc.pact_channels().items():
            pc = g.state.pacts.get(pact_id)
            for pid, rec in copies.items():
                pact_copies.setdefault(pid, []).append(f"<#{rec['channel']}> ({pc['name'] if pc else pact_id})")
        fields = []
        for pid in list(dict.fromkeys(list(svc.server.get("consoles", {})) + list(g.state.players))):
            p = g.get(pid)
            title = f"{p.state_name} [{p.marker}]" if p else "(joined, not named yet)"
            if p and not p.alive:
                title += " · destroyed"
            if svc.is_fake(pid):
                fields.append((title, f"Test player ({p.owner_name if p else pid}); no console"))
                continue
            cid = svc.console_of(pid)
            lines = [f"Owner: <@{pid}> ({p.owner_name if p else 'unnamed'})",
                     f"Console: {f'<#{cid}>' if cid else 'none'}"]
            dms = svc.server.get("dm_threads", {}).get(pid, {})
            if dms:
                lines.append("DMs: " + ", ".join(f"<#{t}> ({g.get(o).state_name if g.get(o) else o})"
                                                 for o, t in dms.items()))
            if pid in pact_copies:
                lines.append("Pacts: " + ", ".join(pact_copies[pid]))
            value = "\n".join(lines)
            fields.append((title, value if len(value) <= 1024 else value[:1020] + "…"))
        if not fields:
            await i.response.send_message("Nobody has joined yet.", ephemeral=True)
            return
        embeds = []
        for start in range(0, len(fields), 8):                  # stay well under Discord's embed limits
            emb = discord.Embed(title="Consoles" if not embeds else None, colour=discord.Colour.dark_grey())
            for name, value in fields[start:start + 8]:
                emb.add_field(name=name[:256], value=value, inline=False)
            embeds.append(emb)
        await i.response.send_message(embeds=embeds[:1], ephemeral=True, allowed_mentions=NO_PINGS)
        for emb in embeds[1:]:
            await i.followup.send(embed=emb, ephemeral=True, allowed_mentions=NO_PINGS)

    @admin.command(description="Recent history log entries, for one state or everyone")
    @app_commands.autocomplete(state=any_states)
    @app_commands.describe(state="Leave out for everyone", count="How many entries (default 20, max 60)")
    async def log(i: discord.Interaction, state: str | None = None,
                  count: app_commands.Range[int, 1, 60] = 20):
        if not await admin_only(i):
            return
        pid = None
        if state:
            pid, err = ui.resolve(state, admin=True)
            if not pid:
                await i.response.send_message(f"Rejected: {err}", ephemeral=True)
                return

        def keep(rec):
            if pid is None:
                return True
            ids = {(rec.get("actor") or {}).get("id"), (rec.get("target") or {}).get("id")}
            return pid in ids

        lines = [history_line(r, svc.game) for r in svc.history.records(keep, last=count)]
        if not lines:
            await i.response.send_message("No matching history yet.", ephemeral=True)
            return
        title = f"History: {svc.game.get(pid).state_name}" if pid else "History: everyone"
        chunks, cur = [], ""
        for line in lines:
            if len(cur) + len(line) + 1 > 3900:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        chunks.append(cur)
        embeds = [discord.Embed(title=title if n == 0 else None, description=c, colour=discord.Colour.dark_grey())
                  for n, c in enumerate(chunks)]
        await i.response.send_message(embeds=embeds[:1], ephemeral=True, allowed_mentions=NO_PINGS)
        for emb in embeds[1:]:
            await i.followup.send(embed=emb, ephemeral=True, allowed_mentions=NO_PINGS)

    @admin.command(description="Reload game.json after editing it by hand")
    async def reload(i: discord.Interaction):
        if await admin_only(i):
            async with svc.lock:
                svc.reload()
            await i.response.send_message(f"Reloaded. Phase {svc.game.state.phase}, day {svc.game.state.day}.",
                                          ephemeral=True)
            await bot.router.refresh_board()
            ui.schedule_refresh()

    async def recreate_public_channels(guild: discord.Guild) -> None:
        """Delete the public game channels and recreate them empty (same names
        and permissions). Their Discord history goes; the history log stays.
        #rules, #how-to-play, and #statecraft-admin are left alone."""
        kept = {}
        for key, cid in list(svc.server.get("channels", {}).items()):
            if key in KEEP_ON_FRESH:
                kept[key] = cid
                continue
            ch = guild.get_channel(cid)
            if ch:
                await ch.delete(reason="Statecraft: fresh channels")
        svc.server["channels"] = kept
        svc.server.pop("board_message_id", None)
        svc.server.pop("public_chat_webhook_id", None)
        svc.save_server()
        keep = {k: v for k, v in svc.server.items() if k not in ("roles", "channels", "category_id",
                                                               "console_category_id", "public_chat_webhook_id")}
        await discord_setup.ensure_server(guild, svc.cfg.server_file)
        svc.server = {**discord_setup.load_server(svc.cfg.server_file), **keep}
        svc.save_server()
        bot._webhook = None
        await bot.router.post_guides()             # the new #register needs its pinned welcome

    FRESH_NOTE = ("Also wipe the game channels (board, action log, chats, jury, register) by recreating them "
                  "empty. Rules, how-to-play, and the admin channel are kept; #register gets its welcome back.")

    @admin.command(description="Wipe the game (keeps the old history log)")
    @app_commands.describe(fresh_channels=FRESH_NOTE)
    async def reset(i: discord.Interaction, fresh_channels: bool = False):
        if not await admin_only(i):
            return

        async def go(ci: discord.Interaction):
            await ci.response.edit_message(content="Resetting...", view=None)
            people = set(svc.server["consoles"]) | set(svc.game.state.players)
            async with svc.lock:
                archived = svc.reset()
            await delete_game_channels(ci.guild, people)
            svc.server.pop("board_message_id", None)
            svc.save_server()
            if fresh_channels:
                await recreate_public_channels(ci.guild)
            done = ("Game reset." + (" Public channels recreated empty." if fresh_channels else "")
                    + (f" Old history kept as {archived}." if archived else ""))
            try:
                await ci.edit_original_response(content=done)
            except discord.HTTPException:          # the channel it was run in may be gone
                await bot.router.admin_log(done)

        extra = " **The public game channels will also be deleted and recreated empty.**" if fresh_channels else ""
        await ui.ask(i, "**Wipe the current game?** This deletes every state, console, and pact channel, and "
                        f"removes the game roles. The history log is kept under a timestamped name.{extra}", go,
                     ephemeral=True)

    @admin.command(description="After a game: delete consoles and pact channels, remove roles (keeps the record)")
    @app_commands.describe(fresh_channels=FRESH_NOTE)
    async def cleanup(i: discord.Interaction, fresh_channels: bool = False):
        if not await admin_only(i):
            return

        async def go(ci: discord.Interaction):
            await ci.response.edit_message(content="Cleaning up...", view=None)
            await delete_game_channels(ci.guild, set(svc.server["consoles"]) | set(svc.game.state.players))
            if fresh_channels:
                await recreate_public_channels(ci.guild)
            done = ("Cleaned up." + (" Public channels recreated empty." if fresh_channels else "")
                    + " game.json and the history log are untouched.")
            try:
                await ci.edit_original_response(content=done)
            except discord.HTTPException:          # the channel it was run in may be gone
                await bot.router.admin_log(done)

        warn = "" if svc.game.state.phase == "ended" else " **The game is still in progress!**"
        extra = (" **The public game channels will also be deleted and recreated empty.**"
                 if fresh_channels else " Public channels keep their messages.")
        await ui.ask(i, "Delete every console and pact channel and remove the game roles? The game record "
                        f"(game.json and the history log) is kept.{extra}{warn}", go, ephemeral=True)

    tree.add_command(admin)

    # ---- test mode
    if not bot.cfg.test_mode:
        return
    test = app_commands.Group(name="test", description="Test-mode tools (disabled for real games)",
                              default_permissions=discord.Permissions(manage_guild=True))

    @test.command(description="Register fake players (no Discord accounts)")
    async def addplayers(i: discord.Interaction, count: app_commands.Range[int, 1, 20]):
        if not await admin_only(i):
            return
        made = []
        for _ in range(count):
            n = svc.game.state.next_player_num
            name = f"Test State {n}"
            res = await svc.run(lambda g, now, n=n, name=name: g.register(f"Test Bot {n}", name, pid=f"fake{n}"))
            if res.ok:
                made.append(name)
        await i.response.send_message(f"Added {len(made)} fake players. Their console messages go to "
                                      f"#statecraft-admin.", ephemeral=True)

    @test.command(name="as", description="Run a console command as any state")
    @app_commands.autocomplete(state=any_states)
    @app_commands.describe(command="e.g. attack VE · move ne · gift WE 2 · say hello · vote VE")
    async def as_(i: discord.Interaction, state: str, command: str):
        if not await admin_only(i):
            return
        tid, err = ui.resolve(state, admin=True)
        if not tid:
            await i.response.send_message(f"Rejected: {err}", ephemeral=True)
            return
        res = await svc.run(lambda g, now: run_text(g, tid, command, now))
        who = svc.game.get(tid).state_name
        await i.response.send_message(f"[{who}] " + (res.message if res.ok else f"Rejected: {res.message}"),
                                      ephemeral=True, allowed_mentions=NO_PINGS)
        await bot.router.deliver(res.events)

    @test.command(name="start", description="Start with fewer than 12 players")
    async def test_start(i: discord.Interaction):
        if await admin_only(i):
            await ui.reply(i, await svc.run(lambda g, now: g.start(force=True, now=now)), ephemeral=True)

    @test.command(description="Shift the bot's clock: +2h, +30m, +1d, or 0 to reset")
    async def clock(i: discord.Interaction, offset: str):
        if not await admin_only(i):
            return
        secs = parse_duration(offset)
        if secs is None:
            await i.response.send_message("Use a value like +2h, +30m, +1d, or 0.", ephemeral=True)
            return
        total = 0 if secs == 0 else int(svc.clock_offset().total_seconds()) + secs
        svc.set_clock_offset(total)
        await i.response.send_message(f"Clock offset {timedelta(seconds=total)}. Game time is now "
                                      f"**{schedule.fmt(svc.now(), svc.game.s)}**. Scheduled events will run "
                                      f"within 30 seconds.", ephemeral=True)
        ui.schedule_refresh()

    @test.command(description="Run the 2:00 PM daily tick now")
    async def tick(i: discord.Interaction):
        if await admin_only(i):
            await ui.reply(i, await svc.run(lambda g, now: g.next_day(now=now)), ephemeral=True)

    tree.add_command(test)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    StatecraftBot(cfg).run(cfg.token, log_handler=None)


if __name__ == "__main__":
    main()
