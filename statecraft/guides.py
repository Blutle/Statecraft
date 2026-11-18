"""Content for #rules (the game) and #how-to-play (using the bot).

Numbers come from the game settings, so the guides stay correct if a
setting changes. Each guide is a list of (title, body) sections, or
(title, body, picture, caption): picture names an illustration the bot
draws (PICTURES in discord_bot), and caption is shown underneath it (as the
card's footer, the only text Discord places below a picture). Game terms: State (capitalized), Influence
(a State's reach), Gift, Spy.
"""

from __future__ import annotations

from . import schedule


def _t(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _jury_rule(s: dict) -> str:
    if not s["jury_bands"]:
        text = f"Every full **{s['votes_per_ap']} votes** a State received gives it **+1 AP**"
        if s["jury_max_award"]:
            text += f", up to **+{s['jury_max_award']} AP** per day"
        return text + "."
    r, p = s["jury_reward_votes"], s["jury_penalty_votes"]
    text = f"A State that received **exactly {r} votes** gets **+1 AP**. "
    if p:
        between = f"{r + 1}" if p == r + 2 else f"{r + 1}–{p - 1}"
        if p > r + 1:
            text += f"**{between} votes** gives nothing, and "
        text += f"**{p} or more** costs it its **daily AP** that day. Too much support backfires."
    else:
        text += "More than that gives nothing."
    return text


def rules(s: dict) -> list[tuple[str, str]]:
    open_, close, lock, fade = _t(s["window_open"]), _t(s["window_close"]), _t(s["vote_lock"]), _t(s["info_expire"])
    tz = "Central" if s["timezone"] == "America/Chicago" else s["timezone"]
    return [
        ("💰 Meta Rules: No Outside Money",
         "This rule sits above every other rule in the game.\n"
         "• **No promising cuts of the win to other players.**\n"
         "• **The Prize Pool and any monetary gain must be completely separate from the game.**\n"
         "Every deal, bribe, and promise has to be made with what the game gives you: AP, Gifts, alliances, "
         "and votes."),

        ("📜 Statecraft: the rules",
         "Every player secretly runs one **State** on a shared map. Spend **Action Points (AP)** to move, attack, "
         "and Gift, make (and break) alliances, and be the **last State standing**.\n\n"
         "Everyone can see the States, but **not who controls them**. A player's identity is revealed only when "
         "their State is destroyed, or when the game ends."),

        ("🗺️ The board",
         "Every State starts on a **random** square of a shared map."),

        ("⚡ Action Points (AP)",
         f"• Every surviving State gets **{s['daily_ap']} AP each day at {open_} {tz}**.\n"
         "• AP never expires. Save it up or spend it straight away.\n"
         "• **Your AP is secret.** Only you can see it.\n"
         "• The Jury can award bonus AP (see below).\n"
         "• When a State is destroyed, its unspent AP is lost."),

        ("🎯 Actions",
         f"Actions can only be taken **{open_}–{close} {tz}**. After **any** action you must wait "
         f"**{schedule.fmt_cooldown(s['cooldown_minutes'])}** before your next one. All actions are **public**, with the "
         f"exception of **Gifting**, which is **private**.\n\n"
         f"**Move**: {s['move_cost']} AP. One square in any of the 8 directions. You can't move off the board or "
         "onto another State, living or destroyed.\n"
         f"**Attack**: {s['attack_cost']} AP. Deal 1 damage to a State within your Influence.\n"
         f"**Gift**: {s['gift_amount']} AP. Give {s['gift_amount']} AP to a State within your Influence.\n"
         f"**Spy**: {s['info_cost']} AP. Gain access to a top-secret log that shows all gift actions performed "
         "for the current day.\n"
         f"**Upgrade Influence**: {s['upgrade_cost']} AP. Your Influence grows by 1, permanently."),

        ("📏 Influence",
         "Any action that targets another State requires that State to be in Range of your State's Influence. "
         "Example Influence ranges are pictured below.",
         "influence",
         f"Every State starts with {s['base_range']} Influence Range"),

        ("💀 Destruction",
         f"Every State starts with **{s['starting_hp']} HP**. A State at **0 HP** is destroyed:\n"
         "• Its controller is **revealed** to everyone.\n"
         "• They join the **Jury**.\n"
         "• They're removed from every pact, their private conversations close, and they can no longer post "
         "in public chat. They can still post in anonymous chat and the Jury chat, and privately message "
         "other jurors.\n"
         "• The State stays on the map as a permanent **obstacle**."),

        ("🗳️ The Jury",
         "Destruction is not the end of your time in Statecraft. Your votes can grant opportunities to those who "
         "remain.\n"
         f"• Each juror gets **one secret vote** for a surviving State, from **{close} until {lock}** the next day. "
         f"You can change it until the lock.\n"
         f"• At **{open_}**, the votes are counted. {_jury_rule(s)}\n"
         "• The vote **totals** are announced. Who voted for whom is never revealed."),

        ("🏆 Winning",
         "• The **last State standing wins**.\n"
         "• Survivors who want to end the game in a **draw** can agree to it among themselves and ask the "
         "**referee**, who can declare joint winners.\n"
         "• Prize details are announced by the referee."),

        ("🕑 The daily schedule",
         f"**{open_}**: Jury votes are counted, every State gets its daily AP, and **actions open**\n"
         f"**{close}**: **actions close** and Jury voting opens\n"
         f"**{fade}**: Spy access ends for the day\n"
         f"**{lock}**: Jury votes lock\n"
         f"All times are {tz}. Chatting, private messages, and pacts are open at **all hours**."),

        ("🎭 Secrecy and fair play",
         "• Talk through the bot as your **State**, never as yourself, if you want to stay hidden. How much you "
         "reveal is up to you.\n"
         "• **Everything you send through the bot is recorded**, including anonymous posts and private messages, "
         "for the post-game summary. Nothing sent through the bot stays private forever.\n"
         "• The referee can see everything and settles anything the rules don't cover."),
    ]


def faq(s: dict, register_ch: str = "#register", rules_ch: str = "#rules",
        how_ch: str = "#how-to-play") -> list[tuple[str, str]]:
    """#faq: a quick overview for people who've just been invited."""
    open_, close = _t(s["window_open"]), _t(s["window_close"])
    tz = "Central" if s["timezone"] == "America/Chicago" else s["timezone"]
    ap = s["daily_ap"]
    qa = [
        ("What is this?",
         "A game of diplomacy and betrayal played entirely through Discord, on your phone. Everyone secretly "
         "controls a State on a shared map, and nobody knows who's behind which one. Make alliances, break them, "
         "and be the last State standing.\n"
         "It's based largely on **Tank Turn Tactics**, a game Luke Muscat made at Halfbrick Studios (the studio "
         "behind Fruit Ninja). It caused so much drama among the staff that the studio banned it from the office "
         "after about a week. His GDC talk about it is a fun watch: "
         "[The Prototype that was Banned from Halfbrick](https://gdcvault.com/play/1017744)."),
        ("How much of a time commitment is this?",
         "You can put as little or as much time into politics as you want, but the actual active playtime per day "
         f"is anywhere from thirty seconds to a few minutes. You get {ap} action point{'s' if ap != 1 else ''} a "
         "day, so taking your turn takes seconds. The talking is where the game happens."),
        ("What's with the active hours? That seems like a long time.",
         f"Actions can only happen {open_}–{close} {tz}. The active hours aren't meant to show you how long you "
         "have to play. They're purely in place to stop people from getting destroyed by surprise while they're "
         "asleep. You don't need to be online during them; one visit is enough."),
        ("Will people know it's me?",
         "No. You play as your State, and nobody can see which State is yours unless it's destroyed or the game "
         "ends."),
        ("What if I miss a day?",
         "Nothing bad happens. Your action points save up, so you can take your turn tomorrow. (Unless you made "
         "some enemies, then you might find yourself on the Jury.)"),
        ("What happens if I get knocked out?",
         "You join the Jury, which votes every night to give bonus action points to surviving States. You can "
         "still influence the game, even once you're knocked out."),
        ("How long does a game last?",
         "I'm expecting a week or so, but it could be more or less depending on how people play."),
        ("Is there a prize?",
         "That's up to the players. I've floated the idea of a cheap buy-in to make a prize pool, but we can also "
         "just play for fun."),
        ("How do I join?",
         f"Type `/join` in {register_ch}. The rules are in {rules_ch}, and {how_ch} explains the bot."),
    ]
    return [("❔ Statecraft FAQ", "\n\n".join(f"**{q}**\n{a}" for q, a in qa))]


def register(s: dict, rules_ch: str = "#rules", how_ch: str = "#how-to-play",
             faq_ch: str = "#faq") -> list[tuple[str, str]]:
    """The pinned welcome card in #register. The channel names come in as
    mentions so they're clickable in Discord."""
    return [
        ("🏛️ Welcome to Statecraft",
         "**Type `/join` in this channel to register a State.**\n\n"
         "• You'll get a **private console**, a channel only you (and the referee) can see.\n"
         "• Everything you send through the bot is recorded for the post-game summary.\n\n"
         f"🗒️ What is this? {faq_ch}\n"
         f"📜 Game rules: {rules_ch}\n"
         f"❔ How to use the server: {how_ch}"),
    ]


def how_to_play(s: dict) -> list[tuple[str, str]]:
    close, fade = _t(s["window_close"]), _t(s["info_expire"])
    return [
        ("👋 How to play on Discord",
         "Everything happens in your **private console**, a channel only you (and the referee) can see, near the "
         "top of the channel list in your own category: **Your State** until the game starts, then your State's "
         "name. Your pact channels appear there too. Nobody "
         "else can tell which console, or which State, is yours.\n\n"
         "This guide covers using the bot. For the game itself, read the rules channel."),

        ("1️⃣ Joining",
         "• In **#register**, type `/join`. You'll get a private console channel.\n"
         "• In your console, tap **✏️ Name my state** and pick a name (up to 24 characters) and a 3-letter "
         "**abbreviation** for the map (or leave it blank and one is made for you).\n"
         "• Tap **🎨 Pick a color** to choose your State's color on the map. It's **first come, first served**, "
         "so no two States share a color. If you don't pick one, you'll get a free color when the game starts.\n"
         "• You can change your name, abbreviation, and color until the game starts.\n"
         "• Changed your mind? `/leave` before the game starts.\n"
         "• Who else has joined stays secret until the game starts; pacts open then."),

        ("🎛️ Your control panel",
         "The **control panel** always sits at the **bottom of your console**. On a phone, everything is a tap or "
         "two away.\n"
         "• **The status card**: your AP, HP, Influence, position, when your cooldown is ready, whether actions "
         "are open, which States are within your Influence, and which can reach you.\n"
         "• **Recent activity**: Gifts you received, your Spy log link, and other notices.\n"
         "• **Spying** opens a private **🕵️ Spy log** thread in your console with every Gift made today. New "
         "Gifts appear there as they happen, and the thread closes when your access ends.\n"
         "• **🔄 Refresh** updates it. If it's ever missing, type `/panel`."),

        ("🎯 Taking actions",
         "1. Open **Choose an action…** on your panel.\n"
         "2. Pick **Move** (tap an arrow), **Attack** or **Gift** (only States within your Influence are listed), "
         "**Upgrade Influence**, or **Spy**.\n"
         "3. Check the summary and tap **Yes**. **Nothing happens until you confirm.**\n"
         "Every reply to you is private and disappears when dismissed. If an action isn't possible (not enough AP, "
         "outside your Influence, cooldown, outside hours), it's rejected and costs nothing."),

        ("🗺️ The board",
         "• **#game-board** always shows the current map.\n"
         "• Tap **🗺️ Board** on your panel for **your own view**: your Influence is shaded, your State has a white "
         "outline, and any State that can reach you has a **red ring**.\n"
         "• Use **Show Influence for…** under any board to see any State's reach (only you see the result).\n"
         "• On the map, each tile shows a State's **3-letter abbreviation**, its **HP as dots**, and its "
         "**Influence** in the corner. Destroyed States are grey with an ✕."),

        ("💬 Talking",
         "• **📣 Public chat**: posts in #public-chat under your **State's name**.\n"
         "• **🎭 Anonymous**: posts in #anonymous-chat with **no name at all**.\n"
         "• **✉️ Message a state**: opens a **private conversation thread** with that State inside your console. "
         "Type there like a normal chat. They see your messages under your State's name, and you see theirs.\n"
         "• If your State is destroyed, you keep anonymous chat and gain **#jury**. You can still read "
         "#public-chat but not post there. Your conversation threads and pact channels close, and **✉️ Message "
         "a juror** opens private threads with other jurors."),

        ("🤝 Pacts (group chats)",
         f"Pacts are private group chats for alliances (or plots).\n"
         f"• Tap **🤝 Pacts** to **found** one (you can found up to {s['pact_found_limit']}). Only the **founder** can invite "
         "or remove members.\n"
         "• Invitations arrive in your console with **Accept** / **Decline** buttons.\n"
         "• Each member gets their own copy of the pact channel. Just type there: your messages appear to the "
         "others under your State's name.\n"
         "• You only see messages sent **after you joined**.\n"
         f"• You can be in up to {s['pact_member_limit']} pacts at once. Leave from the same menu."),

        ("🔔 Notifications",
         "• **Attacks, destructions, and big announcements** always ping everyone playing.\n"
         "• Moves, upgrades, and Spying only ping you if you opt in: `/notify on` (or `/notify off`).\n"
         "• Anything new in your console, every pact message, and every message from another State pings you.\n"
         "• To silence one pact or conversation, use `/notify off` and pick it under **chat**. Its messages "
         "still arrive, just without a ping. `/notify on` with the same chat turns it back on.\n"
         "• **#action-log** shows everything that happens, in order."),

        ("⌨️ Slash commands (backup)",
         "Everything on the panel also works as a command in your console:\n"
         "`/move` `/attack` `/gift` `/upgrade` `/spy` · `/status` (you, or any State) `/board` `/spylog` · "
         "`/say` `/anon` `/dm` · `/pact create|invite|leave|kick|list` · `/vote` (jurors) · `/notify` · "
         "`/panel` `/help`\n"
         "Use them **only in your console**. A command typed anywhere else is rejected so it can't give you away.\n"
         f"Tip: actions close at {close}, and Spy access ends at {fade}."),

        ("🔒 Staying hidden",
         "• Don't use other bots or apps in the game server. Their replies could show your real name.\n"
         "• Your console, threads, and pact channels are visible only to you and the referee.\n"
         "• Remember: **everything sent through the bot is recorded** for the post-game summary."),
    ]
