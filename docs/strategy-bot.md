# Your Strategy Bot

Every Tico install has a **Strategy Bot**, beside the Strategy tabs; like the other built-ins it is not on the
[team chart](org-chart.md). Its slug is `goal-manager`, which keeps its KPI readings, goal history and old messages its own.
It owns three things, in this order: the team's [strategy](strategy.md), the org changes that follow from it (suggested in its chat, made when you say so),
and the [goals and KPIs](goals-and-kpis.md) that say whether it is working. This page says how much it does, who
changes that, and what stays yours. There is no On/Off switch: you choose how much it does, from **Passive** up.

## The three levels

**Settings > Strategy Bot** shows the regular checks, then three levels. Each level has one sentence on what the Strategy Bot will
and won't do. Every company starts at **Suggests actions** until its owner chooses another level. Until then the
owner can also confirm Suggests actions as their own choice, so a later change to where companies start never moves it.

| Level | Name | What the Strategy Bot does |
|---|---|---|
| 1 | **Passive** | Keeps the regular checks running (KPI readings and the automatic goal colours) and otherwise acts only when asked through your Assistant. It proposes a strategy draft when you ask, writes the card when a bet's wrong-if trips, suggests KPIs, goals and Why lines when you ask, and changes nothing until you accept. |
| 2 | **Suggests actions** (default) | Also takes "Draft the strategy" tasks, asks you for the vision, proposes Why lines, sends a Monday note, asks a goal's owner what is happening, and suggests how your bots could recover a goal that falls behind, as items in **Needs you** that you approve one by one. |
| 3 | **Acts on its own** | Also decides on its own: makes, reorders, reassigns and closes bot tasks itself to recover a goal, each decision with an Undo. |

At level 3 the Strategy Bot also decides on its own. One decision has a reason and up to 10 changes to bot tasks: a new task
for a bot, a bot's task moved to the top of its queue, a bot's task given to another bot, or a bot's task closed. Each
decision is an entry in the [Strategy Bot log](#the-strategy-bot-log) with an Undo. It never acts alone on a private task, or
gives a task to a bot that keeps its tasks private. It touches only tasks it may read and gives work only to bots it may
write to, as its own API calls would: a task hidden from it by a bot's access is refused as not found. A change it can't make alone, such as a person's task or a bot that
is offline, becomes an item in your **Needs you** instead. Everything that
[stays yours](#what-stays-yours) stays yours at level 3 too.

An install whose owner chose the old level 1, **Goals and tracking**, is **Passive** after the upgrade.

## Regular checks

**Regular checks** is a toggle at the top of **Settings > Strategy Bot**. It is on by default. It covers the Strategy Bot's KPI
pass (it computes each KPI and posts the reading) and the automatic goal colours.

Turn it off and neither runs until you turn it back on. You can still talk to the Strategy Bot. Turn it on again and every
goal's automatic colour is worked out at once. A colour a person set by hand stays as it is either way.

## Change the level

1. Open **Settings > Strategy Bot**. Only the owner sees this tab.
2. Click a level. Its sentence shows again with **Confirm** and **Cancel**.
3. Click **Confirm**. A "Saved" notice appears and the level shows **Now**.

**Changes** at the bottom lists each change with who made it, when, and the level before and after.

Only the owner changes the level, the regular checks, the Strategy Bot's name and publishes a
[strategy version](strategy.md#who-may-do-what), and only by their own click. An admin cannot. A bot cannot, the
Strategy Bot included, and neither can your Assistant acting for the owner. Choosing the level you are already on changes
nothing and logs nothing.

To rename the Strategy Bot, the owner changes its **Name** in **Settings > Bots**. Without a name of your own it is called
**Strategy Bot**.

Over the API, `GET /api/v2/strategy-bot` shows `name`, `regular_checks`, `level` and `level_history`;
`POST /api/v2/strategy-bot/level` with `{"level": 2}` changes the level and `POST /api/v2/strategy-bot/checks` with
`{"on": false}` the regular checks. `hub goal strategy-bot` reads them and each person's count for today.

## Strategy

The [Strategy](strategy.md) page is the Strategy Bot's first job. At every level it **proposes**; you write the draft and
the bets, and you publish:

- **Drafting.** A task "Draft the strategy", or you asking in your chat, runs its `draft-the-strategy` playbook. It reads
  the market pages, the setup answers, the goals and KPIs, and puts **claims** to you to keep, edit or strike ("Your
  site implies you compete on price; that rules out white-glove service"), at most eight. It proposes three to five
  bets, each with why, what it rules out and what would prove it wrong, and the four sections, and never invents a
  number. The result is one `strategy_draft` proposal in your **Needs you**; confirming writes it into your draft.
  This is the same at every level.
- **Publishing is yours.** The Strategy Bot ends a drafting run by asking you to confirm and publish from the Strategy page.
  It cannot publish, and it cannot write the draft: the server refuses every bot.
- **When a bet may be wrong.** When a bet's wrong-if trips (a KPI crosses its threshold, a cited market entity
  changes, a check-by date passes), the server files a task and the Strategy Bot writes one Decide card: retire the bet,
  rewrite it, or keep it with a new wrong-if, with its pick. This happens at every level, Passive included.
- **Goals from the strategy.** When you ask for goals, or after you publish, it suggests one or two per active bet,
  each tied to its bet with a KPI, and the Monday note names each active bet with no KPI-backed goal or with gray
  health. A goal tied to a bet needs no Why line.

## What the Strategy Bot does at Passive

- **Runs the regular checks.** It posts KPI readings and keeps each goal's automatic colour. Nothing else happens on its
  own.
- **Answers when asked.** Ask your Assistant for the strategy, a KPI, a goal or a Why line and it suggests one. It changes nothing until
  you accept ([A goal's Why](goals-and-kpis.md#a-goals-why), [Strategy](#strategy)).
- **Asks for nothing.** There is no vision ask, no Why bundle in **Needs you**, no ask to a goal's owner and no Monday
  note. Those start at **Suggests actions**. An ask filed earlier stays until it is settled. The one exception is a
  bet whose wrong-if trips: the server detects it, so its Decide card still reaches you.
- **Changes no tasks.** It creates, reorders, reassigns and closes nothing of anyone's. Going down to Passive withdraws
  the recoveries still waiting, and pauses a Monday note the server started (when the level went above Passive, or on
  an install that never chose a level).

## What the Strategy Bot does at Suggests actions

Everything at Passive, plus these. Going up from Passive starts the Monday note, and so does an install that never
chose a level, since it starts here, unless a person already started or paused the note themselves.
- **Takes "Draft the strategy" tasks.** A drafting run ends in a `strategy_draft` proposal you confirm
  ([Strategy](#strategy)), as at every level; it writes nothing to the page itself.
- **Asks you for the vision.** While the team's vision is empty, the first item in the owner's **Needs you** is "Tell
  your Strategy Bot what this company is for". Writing the vision on the [Strategy](strategy.md) page closes it.
- **Proposes a Why for each goal that has none and is not tied to a bet**, as one **Needs you** item for each person who decides them, never one
  per goal. You accept, edit or replace each on its goal.
- **Sends a Monday note** in the owner's chat: a short weekly goals review with a line on the strategy (or that none is
  published), how many goals still have no bet and no Why, and each active bet with no KPI-backed goal or gray health.
  A person who pauses it stays paused.
- **Asks a goal's owner what is happening** when a goal is red or stalled, as a **Needs you** item for that owner. The
  ask closes by itself when the cause clears.
- **Suggests a recovery** when a goal is red or stalled, as one **Needs you** item for the goal's owner in place of
  "check why it is off track". It shows the evidence, up to 4 options and the Strategy Bot's pick. Each option is a few
  changes to bots' tasks: a new task for a bot, a bot's task moved to the top of its queue, or a clash between two
  bots' tasks settled. A person's tasks are never in it.
- **Changes nothing without your yes.** **Approve** on an option applies exactly that option, in the Strategy Bot's name.
  **Reject** drops them all. Where part of an option can't be done (a bot is paused, a task finished meanwhile, or you
  may not reorder bots' queues), it says why in plain words and comes back to you as a new recovery. Nothing is
  dropped. Going down to Passive withdraws the recoveries waiting.
- **Makes the same question one item.** When several bots ask you the same thing, you see one item instead of each.
  Your one answer goes to every bot that asked. If the item ends without an answer, their asks come back as they were.
- **Offers level 3 once you trust it.** After 10 recoveries in a row approved with its pick unchanged, the Monday note
  offers level 3, at most once a month. It never raises its own level; only you do, in Settings.

More on recoveries and merged asks: [Goals and KPIs](goals-and-kpis.md#recoveries-and-merged-asks).

### At most 5 items a day

At every level, the Strategy Bot puts at most 5 items a day in one person's **Needs you**. The day runs on UTC, and the
count includes the asks Tico files in its name, recoveries and merged items. The most important come first, with the vision before everything else;
the rest wait for the next day. The server refuses a sixth.

## Asking it through your Assistant

Nobody chats with the Strategy Bot directly, the owner included: everyone's one chat is their
[Assistant](assistant.md#helpers-through-the-assistant). Ask it for strategy advice or a goal change and it files the
Strategy Bot a task citing your words. The Strategy Bot works that task in its own turn with its own rights, so a change
that stays yours (below) comes back to you as a proposal or a Confirm card. Bots still message it, in its goals
session.

### Goals agreed in your chat

In the owner's Assistant chat, the Assistant handles goals, measures and targets itself. It saves them only on your plain yes:

1. It checks the suggestions you said no to, and your reasons, before it suggests anything. It does not make the same one again.
2. It asks up to three short questions, one at a time: what should be true, whose goal it is, and what it serves.
3. If the goal has no number or date, it suggests one once. A "no" keeps the goal as written, and it does not ask again in that chat.
4. It posts a read-back in your chat, listing each change and what will be saved. Nothing is saved yet.
5. It saves when your next message is a plain yes ("yes", "ok", "go ahead", "save it"), or a yes to some changes by number ("yes to 1 and 3"). Any other reply, such as "yes, but make it 50" or "wait", saves nothing, and the Assistant writes a new read-back.

A "no" to a suggestion the Assistant made, a number, a date, a measure or new wording, is recorded at once, with your words as its reason. It does not come back.

A colour, done or dropped, archiving, a KPI reading, the company vision, strategy drafts and bets, and recoveries are not agreed in chat. The read-back says why. Change those on the [Goals](goals-and-kpis.md#the-goals-page) page.

Before this change the owner chatted with the Strategy Bot itself. That chat stays yours as read-only history under
**Earlier chats** on your Assistant page, and Confirm cards still waiting there moved to your Assistant chat once. If you
hand ownership to someone else (Settings > People), that earlier chat goes with the owner seat to the new owner, and the
cards waiting on you are withdrawn.

## What it has learned

For the owner, your Assistant keeps two layers, both on **What it has learned** at the top of your chat (`#/assistant/learned`):

- **About you**: how you like things ranked and summarised: your [Assistant's](assistant.md#what-it-has-learned)
  private layer. It stays yours if you hand ownership on.
- **About the company**: the vision (changed on Strategy) and the company goals (changed on Goals), and what you taught it about the
  company ("launches matter more than hiring news for us"). Only you, the owner, read and change it, and only your
  own Assistant uses it, in your chat: other people's Assistants never see it, administrators cannot add to it, and the
  Strategy Bot's goals session is not handed it. It stays with the owner seat: a new owner gets it, and you no longer see it.

Teach it in your Assistant chat; it saves one short line in the right layer and says which. Edit or delete lines on the page
(only your click does). Every turn in your chat reads both layers fresh, so a deleted line is not used from the next
reply on.

## What stays yours

At every level, these stay yours, and the server refuses the Strategy Bot each one:
- anything that raises cost: a new routine or a routine that runs more often, a bot's model, fallback or wake settings;
- publishing to a public roadmap without your approval, and publishing a strategy version at all;
- sending anything outside the company, such as a calendar invitation to someone outside it;
- adding, removing or bringing back bots;
- changing a goal, or a goal's KPI target;
- writing the strategy draft or a bet (it proposes instead);
- changing a bot's instructions or settings;
- giving a task to a person.

When it tries one, nothing changes. It becomes an item in your **Needs you** saying what it wanted, why, and that the
change is yours to make. It also shows in the [Strategy Bot log](#the-strategy-bot-log). It counts toward the 5 a day. When the 5
are used, it waits in the log and says so. It is not counted as one of the bot's mistakes.

The Strategy Bot can propose a goal's Why, wording or target. It cannot accept one. Nobody's `reports_to` changes and it
gets no access it did not have before it was the Strategy Bot.

## The Strategy Bot log

The Strategy Bot's page shows its log on the **Info** tab, newest first. The log is visible to the owner only: it shows
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
The entry then says, in plain words, what could not be restored. An entry is undone once. Your Undo stands: the
Strategy Bot never makes a change you undid alone again (the same new task for the same bot, or the same reorder, reassign or
close of a task); tried again, it becomes an item in your **Needs you**.

Over the API, the owner reads the log with `GET /api/v2/strategy-bot/log` (`hub goal strategy-bot-log`) and undoes an entry
with `POST /api/v2/strategy-bot/log/{id}/undo`. The Strategy Bot decides with `POST /api/v2/strategy-bot/decisions`
(`hub goal strategy-bot-decide`), at level 3 only.

Related: [Strategy](strategy.md), [Goals and KPIs](goals-and-kpis.md), [Team chart](org-chart.md).
