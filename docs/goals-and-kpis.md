# Goals and KPIs

A goal says what a human, a bot or the team is going for. A KPI is a number that says how it is going. Tico keeps the
two apart: a KPI is a record of its own, a goal links to the KPIs that measure it, and the goal's colour is worked out from
them. A built-in bot, the **Strategy Bot**, keeps the KPIs and sets the colours. A human can always override a colour.

This page is the model, the colours, the override rule, the Strategy Bot, the automatic bot KPIs and the API.

## The model

**Goal.** A title, an owner (Team, a human or a bot; payload values are `company`, `human:x` or `bot:x`), an optional goal it supports, an
optional strategy bet it serves (`bet_id`, shown as **Bet** in the goal editor next to the parent picker;
[Strategy](strategy.md)), a colour and a note. Team goals are visible to everyone; a bot's own goals are visible to whoever may Read the bot
([permissions](permissions.md)). A goal with no bet is shown as "not tied to a bet", a filter on the Strategy page, never a nag; a
retired bet keeps its goals. A bet's health comes only from its goals that have a KPI: a goal without one leaves the bet gray.

When the **Bet** picker shows **Retired or unpublished**, the goal's linked bet is not visible to you.
Saving another field keeps that link. Choose **None** to remove it, or choose an active bet to replace it.

**KPI.** A standalone record. A goal links to zero or more KPIs, one KPI can serve several goals, and not every goal has one.

| Field | Meaning |
|---|---|
| `name`, `definition` | what is counted, per what; the definition is one short paragraph |
| `unit` | `%`, `$`, `demos` and so on |
| `direction` | `up`, `down` or `range`: which way is good |
| `cadence` | `daily`, `weekly` or `monthly`: how often a reading is due |
| `owner` | the one accountable: Team (`company` in the API), a human or a bot |
| `source_note` | where the number comes from |
| `definition_version` | starts at 1 and goes up whenever the definition, unit, direction, cadence or source note changes; a rename or a new owner does not change it |
| `parent_kpi_id` | the KPI this one moves: the inputs under a North Star. Shown as the KPI tree on the Growth tab |
| `growth_role` | `north_star` (one per company; setting a new one clears the old), `input`, `guardrail` or blank |
| `stage` | `reach`, `acquire`, `activate`, `retain` or `refer`: the Journey view |
| `loop_name`, `loop_step` | a loop such as `referrals` and its step, for the Loops view. Labels on the KPI; there is no loop table |

**Reading.** A fact, and immutable.

| Field | Meaning |
|---|---|
| `value` | the number |
| `period_start`, `period_end` | the time the value describes (the week ending Sunday), never when it was written |
| `collected_at` | when it was collected |
| `evidence` | a link or a note saying where it came from |
| `quality` | `measured`, `estimate` or `partial` (half a period is not a value to judge) |
| `definition_version` | the definition it was computed under |
| `supersedes` | set on a correction: the reading it replaces |

A reading is never edited or deleted. A correction is a new reading that names the one it supersedes and says why; the old
one stays in the history, marked `superseded_by`, and the new one is what counts.

**Target.** A target belongs to the link between one goal and one KPI, so the same KPI can be held to different targets by
different goals. There are two kinds:

- **Improvement**: a `baseline`, a `target` and a `deadline`. The pace is a straight line from the baseline to the target
  by the deadline. The baseline defaults to the latest reading when the target is set.
- **Maintenance**: a `min`, a `max` or both. The KPI is meant to stay inside the range.

A link with no target is context only: it shows the number and does not colour the goal.

**Freshness.** Given the cadence, a KPI is *fresh* (a reading covers the latest period, allowing a quarter period for the
collector to run), *stale* (one period missed) or *missing* (never read, or two or more periods missed). Missing is never
zero: a KPI with no fresh data has no value and is gray. Freshness comes from the complete reading used to judge
its value; a newer partial reading cannot make an older complete value fresh. The assessment's `reading_id` names
that reading.

## The colours

### A KPI

A KPI's colour comes from its target on the goal it is shown under (or its range):

| Colour | Improvement | Maintenance |
|---|---|---|
| green | on pace or ahead, or the target is reached | inside the range |
| yellow | behind the pace by no more than 10% of the pace needed so far (never tighter than 2% of the whole climb) | within a tenth of the range of an edge |
| red | further behind | outside the range |
| gray | stale or missing data, or only partial data | stale or missing data |

The pace is where the straight line from the baseline to the target is at the time of the reading. For an `up` improvement
from a baseline of 40% on September 1 to a target of 70% on October 1, a fresh, measured reading of 52% on September 19
is **red**: 18 of 30 days puts the expected value at 58%; the shortfall is 6 percentage points and the yellow tolerance is
`max(10% × 18, 2% × 30) = 1.8` points. This uses the reading period end, not its collection time, and assumes the current
definition version and no human override. A word unit is said once and a count is a whole
number (`Paying studios 148 vs 135 studios needed on pace`); percentages, money and time (`months`, `days`, `min`) keep one
decimal. A KPI with no target and fresh data has a number and no colour.

### A goal

A goal's colour is set automatically:

1. **From its KPIs** that carry a target: the worst of them decides, and the note is the one line that says why. A gray KPI
   never turns a goal red.
2. **From its owner's check-in and its tasks** when it has no KPI to judge it by: the owner's latest check-in that carries a
   signal (`on_track`, `at_risk`, `off_track`) within three weeks; else the tasks that name the goal (all done or one done in
   the last two weeks is green; nothing done in two weeks on a goal at least that old is yellow, and nothing moved in thirty
   days is red).
3. **Gray, "no data"**, when there is not enough to go on. If the goal's only measure was archived, the note reads
   "Its only measure was archived on <date>" instead. A goal nothing has scored yet has no colour at all.

The automatic colour is worked out again whenever a reading, a target, a link, an archive or restore, or a check-in changes,
whenever the Strategy Bot runs its status pass, and once an hour, because data goes stale as time passes.

A sub-goal, a goal that supports another goal, is coloured in exactly the same way as a goal with no parent: from its own
KPIs, check-ins and tasks. Its parent does not decide or hold back its colour. A sub-goal with data gets its colour on the
next status pass, including one that had no colour before; a sub-goal with no data stays uncoloured. A colour a person set
stays, and the Strategy Bot only suggests a different one. Turning the regular checks off changes nothing, as for any goal.

### A human can override

A colour a human sets (`hub goal status`, or the colour control on the Goals page) is theirs. It carries their name and
their one-sentence note ("set by Ana"), and it **stays until a human hands it back**: **Let Strategy Bot set it**
(`hub goal status <id> auto`, `POST /api/v2/goals/{id}/status/auto`) ends the override and the colour is worked out at once.

While a goal is overridden the Strategy Bot may *suggest* a different colour. The suggestion is shown on the goal and never
applied. Done and dropped are always set by a human.

Every colour is stored with who set it: `status_by`, and `status_source` (`auto` or `person`), in the goal and in every
`goal_events` row.

## The Strategy Bot

The Strategy Bot (`goal-manager`) is a built-in bot, created for every team like the Librarian, and on every install. Its slug is `goal-manager`, which keeps its KPI readings, goal history and old messages its own.
It appears in **Settings** as **Built-in** and in its panel beside the Strategy tabs, Goals among them; like the other
built-ins it is not on the [team chart](org-chart.md); nobody archives it, and only the team owner edits it. A team from before it existed gets it on update once a
computer and a model exist. Its template is `templates/catalog/goal-manager/`; its playbooks are the product.

There is no On/Off switch. How much it does is the [level](strategy-bot.md) the owner picks, and a new install starts at
**Suggests actions**. Its first job is the team's [strategy](strategy.md): it proposes the Strategy page to the owner and, when a
bet's wrong-if trips, puts one Decide card in front of them; the owner writes and publishes, never the bot.
On goals and KPIs it does five things:

1. **Goals make sense.** It flags vague or duplicate goals and may suggest a measure once or clearer wording, as proposals on
   the goal. At Passive it does this when you ask it. It prefers goals that serve a strategy bet and have a KPI, and
   suggests one or two per active bet that no KPI-backed goal serves.
2. **KPIs.** It is the steward of every KPI. Each has a folder in its repository, `kpis/<slug>/`, with `definition.md`,
   `sources.md`, the query or script (`hub sql`, PostHog or another API through a tool the team declared), a
   known-values check it runs before trusting a number, and a changelog. It computes each KPI on its cadence in one routine
   pass with a time budget per KPI, posts readings with evidence, marks stale or missing data, and skips a failing KPI and
   reports it.
3. **Status.** After the readings it runs the status pass, which sets the automatic colours and never overwrites a human's.
4. **Check-ins.** When a KPI slips it asks the goal's owner what is happening and records the answer as a check-in on the goal.
   The readings are facts and the check-in is the owner's interpretation; they are kept apart. This starts at
   **Suggests actions**.
5. **Guardrails.** A change to a definition or a target is a *proposal* that the goal's or KPI's owner confirms. It cannot
   change a target it is judged against: the API answers `403`. At **Suggests actions** and above, a short weekly goals
   review (the Monday note) goes to the team owner (and the Chief of Staff, if there is one).

Its routines start paused, like a starter bot's first routine. Because it is built in, Tico starts its daily KPI pass once
the first KPI exists, once: a human who pauses it afterwards is not overruled.

### The Strategy Bot on the chart

On the chart and in the Goals tree it sits under the owner, with the Heads (in their departments' groups) and the bots
that report to no one under it ([Team chart](org-chart.md)). Only the picture moves. Nobody's `reports_to` changes and the
Strategy Bot gets no new access: it does the five things above and proposes each goal's [Why](#a-goals-why), nothing more.

Its name is **Strategy Bot** unless the owner gives it another in **Settings > Bots**. `GET /api/v2/strategy-bot` shows the name,
the level and whether the regular checks are on. Each goal gets a [Why line](#a-goals-why).

### How much your Strategy Bot does

**Settings > Strategy Bot** has the **Regular checks** toggle and three levels: **Passive**, **Suggests actions** (where
every company starts) and **Acts on its own**. At **Acts on its own** it also decides on bots' tasks itself, each
decision with an Undo ([Your Strategy Bot](strategy-bot.md#the-strategy-bot-log)).

- **Passive** keeps the regular checks (KPI readings and automatic goal colours) and otherwise acts only when you talk to
  it. It suggests KPIs, goals and Why lines when asked and changes nothing until accepted. There is no vision ask, no Why
  bundle, no ask to goal owners and no Monday note.
- **Regular checks** off stops the KPI pass and the automatic colours until the owner turns them back on.
- Only the owner changes the level, the regular checks, the Strategy Bot's name and the strategy's vision. An admin cannot.
- The Strategy Bot puts at most 5 items a day in one person's **Needs you**.

The levels, who changes them and what stays yours are on [Your Strategy Bot](strategy-bot.md).

#### Recoveries and merged asks

At **Suggests actions**, a red or stalled goal gets a **recovery** in its owner's Needs you, in place of "check why it is off
track": the evidence, up to 4 options and the Strategy Bot's pick. Each option is a few changes to bots' tasks only: a new
task for a bot, a bot's task moved to the top of its queue, or a clash between two bots' tasks settled. **Approve** on
an option applies exactly that option, in the Strategy Bot's name; **Reject** drops them all. Where part of an option
can't be done (a bot is paused, a task finished meanwhile, or you may not reorder bots' queues), it says why in plain
words, and what wasn't done comes back as a new recovery. Nothing is dropped. Going down to **Passive** withdraws
the recoveries waiting. At **Acts on its own**, the Strategy Bot makes the changes itself as a decision, in place of a
recovery, and posts a comment on the goal saying what it did and why; you undo a decision in the Strategy Bot log. A change
you undid is never made alone again: tried again, it becomes an item in your Needs you.

When several bots ask you the same thing, the Strategy Bot can make them **one item** (`POST /api/v2/strategy-bot/merge`).
Their tasks leave your Needs you while it is open, and your one answer goes to every bot that asked. If the item ends
without an answer, they come back as they were. A merged item counts toward the 5 a day.

After 10 recoveries in a row approved with its pick unchanged, the Monday note offers **Acts on its own** once, and at most once
a month (`level3_offer` in `GET /api/v2/strategy-bot`). The Strategy Bot never raises its own level.

### The Company section

The top of Goals used to be a **Company** section with two fields, a **Vision** (400 characters) and a **Description**
(2000 characters). Both now live on the [Strategy](strategy.md) page: the vision is the strategy's `vision`, and the
description became the *What we sell* section of the first draft. The top of Goals is one line, "Strategy v3 ·
<summary…>", linking to the Strategy page; while nothing is published it says so and links the owner to the draft.
The vision is still what a goal's [Why](#a-goals-why) ties to.

Only the owner writes the vision and publishes a version, on their own click. An admin cannot. No bot writes the draft
or publishes, the Strategy Bot included: it proposes, at every level ([Strategy](strategy.md#who-may-do-what)).
`GET /api/v2/company` answers the strategy's vision, and `POST /api/v2/company` with a vision writes it into the
strategy draft (the owner only), so there is one source. `/company/history` still answers for older clients.

A [hub SQL](hub-sql.md) read of `company_profile.vision` returns the same team-visible vision.
Before the first publication, that is the draft's vision. After publication, it is the latest published vision;
an unpublished edit stays private. Publishing and server startup bring the SQL value into line on upgraded installs too.

### A goal's Why

Each goal in its owner's panel has a **Why** line: one sentence, up to 200 characters, on
how the goal serves the vision. Hover a goal in the tree to see its Why in the tooltip.

The Strategy Bot proposes a Why when you ask it, and at **Suggests actions** or above without being asked. It shows under the goal as "Strategy Bot proposes" with the line and its reason. A person
who may change the goal (see [Permissions](#permissions)) then chooses:

- **Accept**: the line becomes the goal's Why.
- **Edit**: change the words, then **Save** to accept your version.
- **Reject**: the proposal closes and the goal keeps the Why it had.

They can also write one themselves with **Write a Why**, or change an accepted one with **Edit**. Saving an empty
line clears it. Each change is in the goal's history.

A goal has one waiting Why at a time. When the Strategy Bot proposes a new line, it replaces the one waiting. If you
accept a line that was replaced while you read it, Tico refuses and shows the new one. Setting a Why closes any
other Why still waiting on that goal.

A goal with no accepted Why shows "Not tied to the vision". That is a note, not an error. A goal tied to a
[bet](strategy.md) needs no Why: the bet is its why, so it shows no such note and the Strategy Bot proposes no Why for it.

Bots, the Strategy Bot included, only propose a Why. They can never accept one or write one directly. A waiting Why
shows on its goal, and all the Why lines waiting on one person are one item in their **Needs you**, "These goals need
a Why line", never one per goal. It closes by itself once every one is settled.

### Proposals

A proposal is what a caller makes when they may not make the change themselves. The owner confirms it (and only a human, on
their own click) or rejects it; confirming makes the change as that human.

| Kind | Payload | Confirmed by |
|---|---|---|
| `goal_wording` | `title`, `body`, `bet_id` | the goal's owner |
| `goal_kpi` | an existing `kpi_id`, or a new `kpi` (`name`, `definition`, `unit`, `direction`, `cadence`, `source_note`), and a `target` | the goal's owner or the KPI's |
| `kpi_definition` | any of `name`, `definition`, `unit`, `direction`, `cadence`, `source_note` | the KPI's owner |
| `kpi_target` | the target fields, with `goal_id` | the goal's owner or the KPI's |
| `flag` | `issue`: `vague` or `duplicate`, and a `note` | the goal's owner |
| `goal_why` | `why`: one line, up to 200 characters ([A goal's Why](#a-goals-why)); never for a goal tied to a bet | whoever may change the goal |
| `strategy_draft` | partial `summary`, `vision`, `sections`, `bets` ([Strategy](strategy.md#who-may-do-what)); confirming writes the draft, never publishes | the team owner |
| `recovery` | `evidence`, 1 to 4 `options` (`label`, 1 to 6 `actions`: `create`, `reorder` or `settle` on bots' tasks), `pick`; only the Strategy Bot at **Suggests actions** or **Acts on its own** ([Recoveries](#recoveries-and-merged-asks)) | the goal's owner; `{"decision": "confirm", "option": n}` approves another option than the pick |

Pending proposals appear under **Needs you** on the Goals page for the human who decides them, except a Why, which
waits on its goal. To confirm a Why with your own words, send `why` with the decision:
`{"decision": "confirm", "why": "..."}`.

After **No, leave it**, the card offers one optional line, "Tell the Strategy Bot why?": the Strategy Bot reads it before it suggests
again, and it is shown only to people who may read the goal's bot (and the measure's).

### What needs a person is a task

Each thing under **Needs you** is a task the Strategy Bot asks of the person who must act, so it also shows in
Tasks and Decide (backend/goal_asks.py):

| Kind | Asked of | Closes by itself when |
|---|---|---|
| `kpi_red`: a KPI off track on a goal | the goal's owner | the KPI is back on track, or no longer on that goal |
| `kpi_stale`: a KPI's reading is out of date or missing | the KPI's owner | a fresh reading is logged |
| `proposal`: a change to confirm or reject | the goal's owner (the KPI's for a definition) | it is decided |

A company goal or KPI is asked of the owner of the environment; a proposal about a bot's goal or KPI goes to the
nearest person above the bot on the org chart. A bot's own red or stale KPI files no task: it is the bot's work.
There is one task per KPI or proposal, kind and person, never two. Tico brings them up to date after every goals
write and once an hour, when time passing makes data stale. When what it was about becomes someone else's, the
first task closes and the new owner gets one. A task the person closes or declines while its cause stands is
not asked again until the cause clears and comes back; one they mark done while it stands is accepted and asked
again. A proposal still shows on the Goals page to everyone who may decide it; the task is only the asker's. At **Passive** nothing new is filed (a task filed before stays until it is settled), and nothing is filed while the Strategy Bot is not set up;
the Goals page still lists what waits.

## Automatic bot KPIs

Every bot has five KPIs that Tico computes from its own data, so no steward is needed. They are virtual: nothing is stored,
and the value is worked out when it is read. Each has an id, `auto:<bot>:<metric>`, and links to goals like any KPI.

| Metric | Definition |
|---|---|
| `tasks_done_7d` | tasks the bot owns that were marked done in the last 7 days |
| `first_response_min` | median minutes from a message to the bot to its first reply in that conversation, over 7 days |
| `approval_rate_30d` | the share of its approval requests a human approved, of those decided in 30 days |
| `failed_runs_7d` | runs that ended failed in the last 7 days |
| `cost_7d` | what its runs cost in 7 days at list price, from their tokens ([usage](usage.md)); subscription runs are left out |

A metric with nothing to measure has no reading and is gray: no message to the bot means no response time, not zero. Their
history is the same measure at the end of each of the last fourteen days. The bot's page does not show them: link one to a
goal from its owner's panel on the Goals page (**Add KPI**), and it shows there like any KPI. Reading them needs Read on the bot, and they cannot be
edited or logged to.

## The Goals page

The goals tree is the main column; the Strategy Bot has the right rail (below the tree on narrow windows): one line on what it does (keeps KPIs current and each goal green, yellow or
red; humans set the goals), its routines as the server has them (when each runs and when it next does, or paused), and its
last run with the first line of its result. Nobody chats with the Strategy Bot directly, the owner included: ask your
[Assistant](assistant.md#helpers-through-the-assistant) to change a goal, and it hands the Strategy Bot the work. A goal
change stays the owner's to confirm. The tree is drawn again when a change is saved. **Open bot** goes to its page. If it is paused or not set up, the owner gets **Turn on** (`POST
/api/v2/goal-manager/turn-on`, the same steps as on update); with no computer or model yet, the panel says so.

One tree, built like the team chart: the team on top, then every human and every bot that is not archived, indented
under whoever they report to, whether or not they have a goal. Each is one line: the avatar and name, then the goal's
colour dot and its title (cut short; the whole title is in the tooltip) and its KPIs as small chips, a dot and the latest
value each (a count when there is no room). More goals follow on lines of their own under the first. The built-in bots (the
Assistant, BotOps and the Librarian) stay out of this tree and its goal owner choices; the Strategy Bot is in the tree, under the owner.
New teams get no default goals for built-in bots. Existing built-in goals are hidden here and kept in storage.
Message bots such as Inbox Manager stay off the tree, as on the Org tab; one with goals shows at the end.

Tapping a goal opens it on its own (a sheet at the bottom on a phone, a side panel on a computer): its title, owner,
what it supports, its bet, its metric with a target and the date it is due, its colour, and **Remove**. Handing it to
another owner is for the owner of the goal it supports or someone above its owner. **+ Goal** at the top adds one; under a
pointer each line has a **+** for that owner (a group's line: its lead), and on a phone a long press on a line does the
same. A member sets goals for themselves and anyone below them; the owner sets them for anyone, the company included.

Tapping a name opens that owner's panel (a sheet at the bottom on a phone). It lists their goals: tap one to edit its
words, what it supports and its colour (or **Let Strategy Bot set it**). Under each goal are its KPIs, its latest
check-in, **Post update** and **Add KPI**. **Post update** (a check-in) is there for whoever may colour the goal: say in a
sentence what is going on and, if you like, how it is going (on track, at risk or off track). On a goal no KPI judges, that
answer sets its colour for three weeks; a colour a person set stays, as does the colour of a goal whose KPIs carry a target
and a colour, and the update is kept as your words. Tapping a KPI shows its history (a chart and the readings with their
evidence), its definition and version, its owner, its targets and the Strategy Bot's latest check-in, with a way back.
**Add goal** and **Add KPI** (a KPI no goal uses yet) are at the bottom, owned by whoever was tapped, and the KPIs no goal uses are listed there too.
Someone with no goal opens on a new one; the team line opens the team goal. Each goal in a panel shows its
[Why](#a-goals-why), and the goal editor's **Bet** picker links it to a strategy bet. **Needs you** is a short strip at the
top, shown only when something waits: red KPIs on goals you own, stale data on KPIs you own, and definitions and targets
waiting for your confirmation. Each line links to its task. Initial page load makes two requests, the tree and Needs you; the Strategy Bot panel and
the historical KPI view load only when opened.
People who may edit an owner's KPIs can open **Archived KPIs** in that owner's panel. The historical list is separate
from active KPIs; a KPI's detail panel offers **Restore KPI** to return it to active lists and freshness checks.

## Permissions

- Everyone signed in reads the strategy and its versions. Only the owner publishes one, on their own click
  ([Strategy](strategy.md#who-may-do-what)).
- Team goals and team KPIs are visible to everyone signed in. A bot's goals and KPIs, and its automatic KPIs, are
  visible to whoever may Read the bot. A goal shows its KPIs, and their numbers in its colour's reason, to everyone
  who reads it, so a bot's KPI goes only on a goal that no one reads who may not read that bot (its own goals, say):
  linking one elsewhere is refused, by hand or by saying yes to a suggestion, and so is handing such a goal to
  someone else. A goal linked before this rule shows without that KPI to whoever may not read it.
- A goal's owner, the owner of the goal it supports and anyone above the owner on the team chart set its colour, edit it and
  choose its KPIs and targets. The team owner may do all of it.
- A KPI's owner (or anyone above them; the team owner for a team KPI) edits the KPI. Its readings are logged by that
  human, by the Strategy Bot, or by the owner of a goal that uses it.
- The Strategy Bot reads every goal and KPI, writes readings and the automatic colours, records check-ins, and proposes. It
  cannot set a colour by hand, edit a goal, create or link a KPI, change a definition or a target, or archive a KPI.

## `hub` and the API

```
hub goal list [--owner X] [--all]              hub kpi list [--goal ID] [--owner X] [--unlinked] [--bot SLUG] [--archived]
hub goal show <id>                         hub kpi show <id>
                                             hub kpi archive <id> | restore <id>
hub goal create / update [--bet ID]        hub kpi create "<name>" [--goal ID] [--definition ..] [--unit ..]
hub goal status <id> <colour> "<why>"          [--direction ..] [--cadence ..] [--owner ..] [target flags]
hub goal status <id> auto                         hub kpi update <id> [fields]
hub goal refresh                           hub kpi link <goal> <kpi> [target flags]  |  hub kpi unlink <goal> <kpi>
hub kpi update <id> --parent P --role R --stage S --loop NAME:STEP   growth fields; see Growth
hub goal checkin <id> "<words>"            hub kpi log <kpi> <value> ["<note>"] [--period-end D] [--evidence ..]
hub goal checkin-list <id>                         [--quality ..] [--supersedes READING]
hub goal needs-you                         hub kpi show <kpi> [--effective]
hub proposal create | list | decide
```

Target flags: `--baseline N --target N --deadline YYYY-MM-DD` (an improvement) or `--min N --max N` (a range). Every command
has an MCP tool of the same meaning (`hub_goal_*`, `hub_kpi_*`, `hub_proposal_*`).

Archived KPIs stay available in `hub kpi show <id>`, `hub kpi list --archived`, and the owner's **Archived KPIs** view. The active list,
Strategy Bot freshness checks and Needs you omit them. A goal panel keeps each archived KPI that the goal links to as one muted
line, **Archived <date>, not counting**, with no colour. It shows at most three such lines, then **+N archived**. The goal
tree's chips leave them out. Archive asks first and names each goal you can see that the KPI stops counting toward, done
and dropped goals included; goals you cannot see are not named. A KPI on no goal asks as before. Archive is never blocked
by goals, and it removes no goal link. A goal's colour counts only its active KPIs; a done or dropped goal keeps its colour
when its KPI is archived or restored, because the automatic colour never changes those goals. Restore
puts the KPI back on every goal it was linked to, with its target, and recomputes each goal's colour. Archive and restore
require the KPI's owner or someone above them; each transition is audited, and neither changes its definition history,
goal links, readings, privacy or owner. The Strategy Bot cannot archive a KPI.

The stable v2 routes are in [openapi/v2.json](openapi/v2.json):

| Route | What it does |
|---|---|
| `GET /api/v2/goals`, `/goals/tree`, `/goals/{id}` | list (mine, or `all=1`), the whole tree with each goal's KPIs, one goal with its KPIs, check-ins, tasks and history |
| `POST /api/v2/goals`, `/goals/{id}` | create, edit; `bet_id` links the goal to a strategy bet ([Strategy](strategy.md)) and is returned in the list and the tree |
| `POST /api/v2/goals/{id}/status` | set the colour by hand: an override with a note |
| `POST /api/v2/goals/{id}/status/auto` | hand the colour back to the Strategy Bot |
| `POST /api/v2/goals/refresh` | the status pass (the Strategy Bot or the owner) |
| `GET/POST /api/v2/goals/{id}/checkins` | the owner's words on how it is going |
| `GET /api/v2/goals/needs-you` | red KPIs, stale KPIs and proposals that need the caller |
| `POST /api/v2/goals/{id}/kpis` | link a KPI (`kpi_id`) or make one and link it (`name`), with the target |
| `POST /api/v2/goals/{id}/kpis/{kpi}` and `/unlink` | change the target on a link; remove the link |
| `GET/POST /api/v2/kpis`, `GET/POST /api/v2/kpis/{id}` | list, create, read (with its links, readings, definition history and check-ins) and edit |
| `POST /api/v2/kpis/{id}/archive`, `/restore` | hide or restore a KPI; only its owner or someone above them; history stays |
| `GET/POST /api/v2/kpis/{id}/readings` | the readings (`?effective=true` leaves out corrected ones); post one |
| `GET /api/v2/bots/{bot}/kpis` | a bot's automatic KPIs |
| `GET/POST /api/v2/proposals`, `POST .../{id}/decide` | list, propose, confirm or reject |
| `POST /api/v2/proposals/{id}/note` | after a reject, its decider adds one line (up to 280 characters) on why, once |
| `GET/POST /api/v2/company` | read the Vision and Description (with whether you may edit); save them (owner). Kept for older clients: the vision now lives on the [Strategy](strategy.md) page |
| `GET /api/v2/company/history` | every change to the Vision and Description, newest first, with who and when |
| `POST /api/v2/goals/{id}/why` | set a goal's Why (`{"why": "..."}`; empty clears it); a person who may change the goal |

A goal's KPI on any of these answers has `latest`, `freshness`, `spark`, and, under a goal, `link`, `target_label`, `status`
(`green`, `yellow`, `red`, `gray` or `none`) and `reason`.

## What changed for existing data

Nothing is deleted. Each old KPI keeps its readings and becomes a standalone KPI, owned by its goal's owner, monthly, with a
link to its old goal; an old `target` becomes an improvement target on that link (with no deadline until someone sets one).
An old reading's `ts` is now its `period_end`, and its `source` of `estimate` is its `quality`. A colour someone set is now a
human's override: it stays as it is until it is handed back.
