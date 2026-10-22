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
| 3 | **Acts on its own** | Also makes, reorders and closes bot tasks itself to recover a goal, each with an Undo. |

Level 3 can be chosen today, but it does not do more yet. Until it arrives in a later release, it does what level 2
does, and its sentence says so.

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

## What stays yours

At every level, spending, publishing, people, and changes to your goals or vision stay yours. The CEO Bot can
propose a goal's Why, wording or target. It cannot accept one. Nobody's `reports_to` changes and it gets no access it
did not have before it was the CEO Bot.

Related: [Goals and KPIs](goals-and-kpis.md), [Team chart](org-chart.md).
