# Your CEO Bot

Where the [CEO Bot switch](goals-and-kpis.md#the-ceo-bot-switch) is on, the Goal Manager works as your **CEO Bot**. This
page says how much it does, who changes that, and what stays yours. With the switch off, none of it applies and
**Settings > CEO Bot** is not there.

## The three levels

**Settings > CEO Bot** shows three levels. Each has one sentence on what the CEO Bot will and won't do. Every company
starts at level 1.

| Level | Name | What the CEO Bot does |
|---|---|---|
| 1 | **Goals and tracking** | Keeps your goals clear and tracked, asks you for the vision, proposes Why lines, sends a Monday note and asks a goal's owner what is happening. It never changes anyone's tasks. |
| 2 | **Suggests actions** | Also suggests how your bots could recover a goal that falls behind, as items in **Needs you** that you approve one by one. |
| 3 | **Acts on its own** | Also makes, reorders and closes bot tasks itself to recover a goal, each with an Undo. |

Levels 2 and 3 can be chosen today, but they do not do more yet. Until they arrive in a later release, both do what
level 1 does, and their sentences say so.

## Change the level

1. Open **Settings > CEO Bot**. Only the owner and admins see this tab.
2. Click a level. Its sentence shows again with **Confirm** and **Cancel**.
3. Click **Confirm**. A "Saved" notice appears and the level shows **Now**.

**Changes** at the bottom lists each change with who made it, when, and the level before and after.

Only the owner or an admin can change the level, and only by their own click. A bot cannot, the CEO Bot included, and
neither can your Assistant acting for someone. Choosing the level you are already on changes nothing and logs nothing.

Over the API, `GET /api/v2/ceo-bot` shows `level` and `level_history`, and `POST /api/v2/ceo-bot/level` with
`{"level": 2}` changes it. `hub goal ceo-bot` reads the switch, the level and each person's count for today.

## What the CEO Bot does at level 1

- **Asks for the vision.** While the company's Vision is empty, the first item in the owner's **Needs you** is "Tell your
  CEO Bot what this company is for". Writing the vision in the [Company section](goals-and-kpis.md#the-company-section)
  at the top of Goals closes it.
- **Proposes a Why for each goal that has none**, as one **Needs you** item for each person who decides them, never one
  per goal. You accept, edit or replace each on its goal ([A goal's Why](goals-and-kpis.md#a-goals-why)).
- **Sends a Monday note** in the owner's chat: a short weekly goals review that opens with a line on the vision (or that
  it is still empty) and how many goals still have no Why. Turning the CEO Bot switch on starts it. Turning the switch
  off pauses it again only if the switch started it.
- **Asks a goal's owner what is happening** when a goal is red or stalled, as a **Needs you** item for that owner. The
  ask closes by itself when the cause clears.
- **Changes no tasks.** At level 1 it creates, reorders, reassigns and closes nothing of anyone's. The only tasks it
  files are questions to a person.

### At most 5 items a day

At every level, the CEO Bot puts at most 5 items a day in one person's **Needs you**. The day runs on UTC, and the
count includes the asks Tico files in its name. The most important come first, with the vision before everything else;
the rest wait for the next day. The server refuses a sixth.

## What stays yours

Whatever the level, spending, publishing, people, and changes to your goals or vision stay yours. The CEO Bot can
propose a goal's Why, wording or target. It cannot accept one. Nobody's `reports_to` changes and it gets no access it
did not have as the Goal Manager.

Related: [Goals and KPIs](goals-and-kpis.md), [Team chart](org-chart.md).
