# Your CEO Bot

Every Tico install has a **CEO Bot**, at the top of the [team chart](org-chart.md). It is the built-in bot that was called
the Goal Manager; its slug is still `goal-manager`, so its KPI readings, goal history and old messages stay its own.
This page says how much it does, who changes that, and what stays yours. There is no On/Off switch: you choose how
much it does, from **Passive** up.

## The three levels

**Settings > CEO Bot** shows the regular checks, then three levels. Each level has one sentence on what the CEO Bot will
and won't do. Every company starts at **Passive**.

| Level | Name | What the CEO Bot does |
|---|---|---|
| 1 | **Passive** (default) | Keeps the regular checks running (KPI readings and the automatic goal colours) and otherwise acts only when you talk to it. It suggests KPIs, goals and Why lines when you ask, and changes nothing until you accept. |
| 2 | **Suggests actions** | Also asks you for the vision, proposes Why lines, sends a Monday note, asks a goal's owner what is happening, and suggests how your bots could recover a goal that falls behind, as items in **Needs you** that you approve one by one. |
| 3 | **Acts on its own** | Also decides on its own: makes, reorders, reassigns and closes bot tasks itself to recover a goal, each decision with an Undo. |

At level 3 the CEO Bot also decides on its own. One decision has a reason and up to 10 changes to bot tasks: a new task
for a bot, a bot's task moved to the top of its queue, a bot's task given to another bot, or a bot's task closed. Each
decision is an entry in the [CEO Bot log](#the-ceo-bot-log) with an Undo. It never acts alone on a private task, or
gives a task to a bot that keeps its tasks private. It touches only tasks it may read and gives work only to bots it may
write to, as its own API calls would: a task hidden from it by a bot's access is refused as not found. A change it can't make alone, such as a person's task or a bot that
is offline, becomes an item in your **Needs you** instead. Everything that
[stays yours](#what-stays-yours) stays yours at level 3 too.

An install that was on the old level 1, **Goals and tracking**, is **Passive** after the upgrade.

## Regular checks

**Regular checks** is a toggle at the top of **Settings > CEO Bot**. It is on by default. It covers the CEO Bot's KPI
pass (it computes each KPI and posts the reading) and the automatic goal colours.

Turn it off and neither runs until you turn it back on. You can still talk to the CEO Bot. Turn it on again and every
goal's automatic colour is worked out at once. A colour a person set by hand stays as it is either way.

## Change the level

1. Open **Settings > CEO Bot**. Only the owner sees this tab.
2. Click a level. Its sentence shows again with **Confirm** and **Cancel**.
3. Click **Confirm**. A "Saved" notice appears and the level shows **Now**.

**Changes** at the bottom lists each change with who made it, when, and the level before and after.

Only the owner changes the level, the regular checks, the CEO Bot's name and the
[Company section](goals-and-kpis.md#the-company-section), and only by their own click. An admin cannot. A bot cannot, the
CEO Bot included, and neither can your Assistant acting for the owner. Choosing the level you are already on changes
nothing and logs nothing.

To rename the CEO Bot, the owner changes its **Name** in **Settings > Bots**. Without a name of your own it is called
**CEO Bot**.

Over the API, `GET /api/v2/ceo-bot` shows `name`, `regular_checks`, `level` and `level_history`;
`POST /api/v2/ceo-bot/level` with `{"level": 2}` changes the level and `POST /api/v2/ceo-bot/checks` with
`{"on": false}` the regular checks. `hub goal ceo-bot` reads them and each person's count for today.

## What the CEO Bot does at Passive

- **Runs the regular checks.** It posts KPI readings and keeps each goal's automatic colour. Nothing else happens on its
  own.
- **Answers when you talk to it.** Ask it for a KPI, a goal or a Why line and it suggests one. It changes nothing until
  you accept ([A goal's Why](goals-and-kpis.md#a-goals-why)).
- **Asks for nothing.** There is no vision ask, no Why bundle in **Needs you**, no ask to a goal's owner and no Monday
  note. Those start at **Suggests actions**. An ask filed earlier stays until it is settled.
- **Changes no tasks.** It creates, reorders, reassigns and closes nothing of anyone's. Going down to Passive withdraws
  the recoveries still waiting, and pauses a Monday note the server started when you went up.

## What the CEO Bot does at Suggests actions

Everything at Passive, plus these. Going up from Passive starts the Monday note.
- **Asks you for the vision.** While the company's Vision is empty, the first item in the owner's **Needs you** is "Tell
  your CEO Bot what this company is for". Writing the vision in the
  [Company section](goals-and-kpis.md#the-company-section) at the top of Goals closes it.
- **Proposes a Why for each goal that has none**, as one **Needs you** item for each person who decides them, never one
  per goal. You accept, edit or replace each on its goal.
- **Sends a Monday note** in the owner's chat: a short weekly goals review that opens with a line on the vision (or that
  it is still empty) and how many goals still have no Why. A person who pauses it stays paused.
- **Asks a goal's owner what is happening** when a goal is red or stalled, as a **Needs you** item for that owner. The
  ask closes by itself when the cause clears.
- **Suggests a recovery** when a goal is red or stalled, as one **Needs you** item for the goal's owner in place of
  "check why it is off track". It shows the evidence, up to 4 options and the CEO Bot's pick. Each option is a few
  changes to bots' tasks: a new task for a bot, a bot's task moved to the top of its queue, or a clash between two
  bots' tasks settled. A person's tasks are never in it.
- **Changes nothing without your yes.** **Approve** on an option applies exactly that option, in the CEO Bot's name.
  **Reject** drops them all. Where part of an option can't be done (a bot is paused, a task finished meanwhile, or you
  may not reorder bots' queues), it says why in plain words and comes back to you as a new recovery. Nothing is
  dropped. Going down to Passive withdraws the recoveries waiting.
- **Makes the same question one item.** When several bots ask you the same thing, you see one item instead of each.
  Your one answer goes to every bot that asked. If the item ends without an answer, their asks come back as they were.
- **Offers level 3 once you trust it.** After 10 recoveries in a row approved with its pick unchanged, the Monday note
  offers level 3, at most once a month. It never raises its own level; only you do, in Settings.

More on recoveries and merged asks: [Goals and KPIs](goals-and-kpis.md#recoveries-and-merged-asks).

### At most 5 items a day

At every level, the CEO Bot puts at most 5 items a day in one person's **Needs you**. The day runs on UTC, and the
count includes the asks Tico files in its name, recoveries and merged items. The most important come first, with the vision before everything else;
the rest wait for the next day. The server refuses a sixth.

## Your chat with it

The owner talks to the CEO Bot in place of an Assistant. The menu tab reads **CEO Bot** (or the name you gave it) and
opens your chat with it (`#/assistant`, `GET /api/v2/assistant` answers `"ceo": true`). It does everything an
[Assistant](assistant.md) does, as you and with the same "via" rules (your cards, your cited requests, your daily update
draft, labelled the CEO Bot's), plus company-wide work within its level. Your chat is one conversation and one session
of its own, whatever its session setting; its goals work (regular checks, goals, bots' messages to it) is another,
and neither shares the other's context. Everything it does in your chat is recorded as you, via the CEO Bot.

Nobody else talks to it: everyone else, administrators included, keeps the Assistant, and the chat routes refuse them
(`403`). Bots still message it, in its goals session, never in your chat. Your chats with your Assistant from before
stay readable under **Earlier chats** on the same page, also while the CEO Bot is paused.

If you hand ownership to someone else (Settings > People), the CEO Bot tab and its chat history go with the owner seat
to the new owner. You get your Assistant back, with your own earlier Assistant chats; the new owner does not see those.
Cards the CEO Bot had waiting on you are withdrawn.

## On the Updates page

Your chat with it is also beside Updates, labelled with its name. It works as the [Assistant's](assistant.md#on-the-updates-page)
does for everyone else: same conversation, show or hide, resize, full screen on a phone. It is handed only the updates
you can read, leaving out any that link a task, chat or bot it can't open. When it picks the top update it follows what
you taught it about you and about the company. A reply is a chat message, never a Needs you item, so it doesn't count
toward the [5 a day](#at-most-5-items-a-day).

## What it has learned

It keeps two layers, both on **What it has learned** at the top of your chat (`#/assistant/learned`):

- **About you**: how you like things ranked and summarised, the same private layer your [Assistant](assistant.md#what-it-has-learned)
  used. It stays yours if you hand ownership on.
- **About the company**: the vision and the company goals (both changed on Goals), and what you taught it about the
  company ("launches matter more than hiring news for us"). Only you, the owner, read and change it, and only the CEO
  Bot uses it, in your chat: Assistants never see it, administrators cannot add to it, and its goals session is not
  handed it. It stays with the owner seat: a new owner gets it, and you no longer see it.

Teach it in your chat; it saves one short line in the right layer and says which. Edit or delete lines on the page
(only your click does). Every turn in your chat reads both layers fresh, so a deleted line is not used from the next
reply on.

## What stays yours

At every level, these stay yours, and the server refuses the CEO Bot each one:
- anything that raises cost: a new routine or a routine that runs more often, a bot's model, fallback or wake settings;
- publishing to a public roadmap without your approval;
- sending anything outside the company, such as a calendar invitation to someone outside it;
- adding, removing or bringing back bots;
- changing a goal, or a goal's KPI target;
- changing the vision or the description;
- changing a bot's instructions or settings;
- giving a task to a person.

When it tries one, nothing changes. It becomes an item in your **Needs you** saying what it wanted, why, and that the
change is yours to make. It also shows in the [CEO Bot log](#the-ceo-bot-log). It counts toward the 5 a day. When the 5
are used, it waits in the log and says so. It is not counted as one of the bot's mistakes.

The CEO Bot can propose a goal's Why, wording or target. It cannot accept one. Nobody's `reports_to` changes and it
gets no access it did not have before it was the CEO Bot.

## The CEO Bot log

The CEO Bot's page shows its log on the **Info** tab, newest first. The log is visible to the owner only: it shows
what happened to bots' tasks, which other people may not be allowed to see. Nobody else sees the section, and the API
refuses them. A task the owner can't open, such as a private task they are not part of, shows as "a task you can't
open", without its words. It lists:
- each decision it made on its own at **Acts on its own**, with its reason and the bot tasks it created, moved up,
  gave to another bot or closed;
- each thing it proposed to you instead, because it stays yours or because a bot was offline.

One decision is one entry. Every change in it carries the same action id in each task's history.

Only the owner sees **Undo**, and it asks once before it acts. Undo puts each task back as it was before the
decision: its owner, its status and step, its place in the queue and its note. A task the decision created is closed.
Each bot whose task changed is told, including a bot that loses a task it was given. If someone changed a task after
the decision, their change stays, and a task the decision created that someone has changed stays open. If Tico can't
put one task back, the others are still put back. A task that has a guest now keeps its owner, so the guest keeps it.
The entry then says, in plain words, what could not be restored. An entry is undone once.

Over the API, the owner reads the log with `GET /api/v2/ceo-bot/log` (`hub goal ceo-bot-log`) and undoes an entry
with `POST /api/v2/ceo-bot/log/{id}/undo`. The CEO Bot decides with `POST /api/v2/ceo-bot/decisions`
(`hub goal ceo-bot-decide`), at level 3 only.

Related: [Goals and KPIs](goals-and-kpis.md), [Team chart](org-chart.md).
