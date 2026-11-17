# Company

Open Company from the desktop sidebar or the mobile More menu (it was called Overview; ⌘K still finds it by that name).
It is where Tico opens. Strategy, Finance (for those who see it) and Growth sit under it in the sidebar; its chevron
folds them, kept per browser. Company is one page (`#/home`), with no tabs: your own cards and pages, the
[Floor](#floor) first. Its ⋯ has **Share** for owners and admins ([Company page](company-page.md)). The addresses of the
views it once had (`#/overview`, `#/overview/floor`, `#/overview/campus`, `#/overview/graph`, `#/overview/factories`)
open it. The team chart is on Strategy › Org (`#/strategy/org`), goals by teammate on Strategy › Goals and rules by
teammate on Rules › Company.

## Your Company page

Company is yours alone: the place `person:<id>` (`person:me` in the API, CLI and tools). Only you read and arrange it;
BotOps and the Assistant arrange it for you when you ask. Nobody else, an admin included, can open it or learn what is
pinned there. A pin there widens nothing: every card and page on it is read with your own rights, so it never shows you
something you could not already read. You pin only cards you made and pages you may read.

Unarranged, it shows the **Floor**, full width, then **Needs attention** (your Needs you list), **Unread threads**, **My
tasks** (open tasks you own, the soonest due first), **Updates** (the newest you may read, with your unread count) and
**Latest pages**. A Company page saved before the Floor was a card got it once, first and full width. The Floor is a card
like the others: **Edit** in ⋯ moves it, resizes it or removes it, and **+ Card** adds it back. It goes on Company only,
once.

## Team pages and cards

A team page (`#/team/<group>`, opened from the team's name in the sidebar tree; its arrow still folds it) is a grid of
pinned cards and pages for one group, arranged the same way as Company. Each is a place (`backend/cards.py`):

| Place | Who sees it | Who arranges it |
|---|---|---|
| `dashboard` | Everyone, through the API only: no page in the app shows it | The owner and admins |
| `growth` | Everyone (the [Growth page](growth.md), `#/growth`) | The owner, admins, and a bot acting as itself, which pins only its own cards there |
| `team:<group>` | Whoever may Read the group: **Seen by** opens the group's Access (permissions.md, People and groups) | The owner, admins, and a person who leads the group or a group above it |
| `bot:<slug>` | Whoever may Read the bot | The bot itself, or a person who manages it |
| `task:<id>` | Whoever may read the task | The owner, requester, a person who can move tasks, or the org owner; a bot only tasks it owns or requested, acting as itself |
| `person:<id>` | That person only (Company) | That person, or BotOps and the Assistant acting for them |

A team page nobody arranged shows Tasks by status, Done per week and Needs you for its team; the Growth page shows its
default growth cards (see [growth.md](growth.md)). The first change saves them as real pins. Arranging: drag a card by its grip, and its
⋯ menu edits it, resizes it (small, medium, large) or removes it; **Add card** and **Add page** add one. On a phone the
grid is one column.

**Cards** are Tico's own templates with settings Tico checks; none runs a query:

| Card | Shows | Settings |
|---|---|---|
| Tasks by status | Open, and done this week | team |
| Done per week | Tasks done each week | team, weeks (4 to 26, 8 by default) |
| Needs you | The reader's own count and top three; with a team, the items its bots are behind | team |
| Spend | This month's spend against the monthly budget | team |
| KPI | One KPI's latest value and trend | kpi |
| Automation runs | Runs in the last 24 hours, failures first | team |
| Latest pages | The newest current [pages](pages.md): not archived or replaced | team, count (1 to 10) |
| Needs attention | The reader's own Needs you: count and top five | none |
| Unread threads | Threads with something new for the reader: unread (a human message or one addressed to them) first, then bot activity | count (1 to 10) |
| My tasks | Open tasks the reader owns, the soonest due first | count (1 to 10) |
| Updates | The newest updates the reader may read, and how many are unread | count (1 to 10) |
| Floor | The [Floor](#floor), drawn with the reader's rights; on Company only, once | none |
| Growth number | One [Growth](growth.md) metric for the month picked on the Growth page, with its change and a sparkline | metric, channel (a slug; empty for all) |
| Growth funnel | The five funnel stages' KPIs, their conversion, and the North Star | none |
| Growth channels | Each channel's new customers, CAC and LTV:CAC for the month, with its open tests | count (1 to 20) |
| Growth retention | Cohort retention at 30, 90 and 180 days | months (3 to 12) |
| Growth board | Open growth ideas by their label: Live, Working, Measuring, In development, On deck | count per lane (1 to 5) |

The growth cards go on the Growth page only; a growth card at any other place is refused.

Every card is drawn for whoever reads it, from what they may already see: tasks they may read, their own Needs you, KPIs
whose owner they may read, automations they may see. Spend shows to the owner and admins, and to a lead for a group they
lead. A card that shows the reader nothing is hidden for them.

**Pins** put a page (`page:<file id>`) or a card (`card:<id>`) at a place, in order and at a size. The pinning rule keeps
a page's data from spreading on its own: a bot pins only its own pages to its sidebar, and its own cards to its sidebar
or a task it holds or asked for. A person pins to a team page, and a page they pin there may be read by that place's
audience; a private task's file is never pinned there. Each pin and unpin of a page is in the file's
activity (docs/files.md).
A page published with `--task` pins itself to the top of that task; a page's full view says where it is pinned.
Any person or bot acting as itself who can read a task may link a page they can read there. Task pins widen nothing.
This uses the task place's `can_link` flag; `can_edit` still controls arranging pins and creating cards.
Unlinking is for whoever linked it, the task's owner or requester, or the org owner.
A bot's sidebar pins are its sidebar widgets: `page:<file id>` and `card:<id>` beside Tasks, Updates and Files, any
number, and the built-ins may be hidden so the sidebar is only pages. The Assistant's sidebar keeps its own widgets.

People change cards from the ⋯ menu; bots and BotOps (with the rights of the person who asked) use `tico card
list|add|update|remove` and `tico pin add|update|remove`, the `tico_card_*` and `tico_pin_*` tools, or the API:
`GET|PUT /api/v2/places/{place}`, `POST /api/v2/places/{place}/order`, `POST /api/v2/pins`,
`PATCH|DELETE /api/v2/pins/{id}`, `GET|POST /api/v2/cards`, `PATCH|DELETE /api/v2/cards/{id}` and
`GET /api/v2/cards/choices`. `GET /api/v2/places/growth?month=YYYY-MM` reads the Growth page for one month and answers
`month` and `months` for its picker; `GET /api/v2/cards/choices?place=growth` adds the growth templates, the metrics and
the live channels, and `?place=person:me` adds the Floor. Tests: `backend/tests/test_cards.py`,
`backend/tests/test_my_dashboard.py`, `backend/tests/test_growth_cards.py`, `ui/tests/dashboard.cts`.

## Floor

The **Floor**, Company's first card, draws the work as it stands. Each factory you can see is a hall, with its name set large and faint and a dim
org chart: its manager (the factory's own, else the one most of its pipelines have) over its reports on the
org chart and the teammates holding its work. Each live pipeline is a line, and each work stage a small stack of boxes standing on it, as tall as the stage is
full: relative, not one box a task, and never taller than eight (1, 3, 10 and 50 tasks stand at 1, 3, 6 and 8 boxes).
A stage with nothing in it keeps its spot as an outline. Nothing is written on the floor but each line's two rates: tasks
in at its start and tasks that reached Done at its end, as `3/day`; either flips every rate between a day and a week,
and the browser keeps the choice. Flow's mark stands over a stack that holds something waiting on you, and opens Flow on
that stage.

Point at a stack for its card: who holds the work, the stage and how many are in it, how many wait on you, and its
oldest tasks with how long each has been there (a row opens the task). A click or Enter pins the card and Escape closes
it; on a phone a tap opens it as a sheet. Pointing at a teammate in a hall's org chart lights their stacks. A hall's
name opens that factory on its own page (`#/overview/floor/<factory id>`, **Company ›** leads back): each stage there
shows one deck per teammate holding tasks in
it, their face on top, the twelve most loaded and then one deck for the rest; the stage's name is under the line with
one Flow mark beside it, and the manager and teammates are listed by name.

A factory or pipeline you may not see is not drawn: a private pipeline shows only to its manager, its owner, the
company's person and whoever reads one of its tasks, and every count is of tasks you may read; a person you may not
see is counted but not named. Pipelines in no factory are drawn last in a hall named Other, once one of them holds an
open task you may read; it has no page and no org chart. Retired pipelines are left out. Data: `GET /api/v2/company/floor` (`backend/company_floor.py`), read again when a task moves or your Needs you
changes; each changed line is redrawn in place. Tests: `backend/tests/test_company_floor.py`,
`ui/tests/company-floor.cts`.
