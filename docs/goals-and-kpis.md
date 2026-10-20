# Goals and KPIs

A goal says what a human, a bot or the team is going for. A KPI is a number that says how it is going. Tico keeps the
two apart: a KPI is a record of its own, a goal links to the KPIs that measure it, and the goal's colour is worked out from
them. A built-in bot, the **Goal Manager**, keeps the KPIs and sets the colours. A human can always override a colour.

This page is the model, the colours, the override rule, the Goal Manager, the automatic bot KPIs and the API.

## The model

**Goal.** A title, an owner (Team, a human or a bot; payload values are `company`, `human:x` or `bot:x`), an optional goal it supports, a
colour and a note. Team goals are visible to everyone; a bot's own goals are visible to whoever may Read the bot
([permissions](permissions.md)).

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
3. **Gray, "no data"**, when there is not enough to go on. A goal nothing has scored yet has no colour at all.

The automatic colour is worked out again whenever a reading, a target, a link or a check-in changes, whenever the Goal
Manager runs its status pass, and once an hour, because data goes stale as time passes.

### A human can override

A colour a human sets (`hub goal status`, or the colour control on the Goals page) is theirs. It carries their name and
their one-sentence note ("set by Ana"), and it **stays until a human hands it back**: **Let Goal Manager set it**
(`hub goal status <id> auto`, `POST /api/v2/goals/{id}/status/auto`) ends the override and the colour is worked out at once.

While a goal is overridden the Goal Manager may *suggest* a different colour. The suggestion is shown on the goal and never
applied. Done and dropped are always set by a human.

Every colour is stored with who set it: `status_by`, and `status_source` (`auto` or `person`), in the goal and in every
`goal_events` row.

## The Goal Manager

The Goal Manager (`goal-manager`) is a built-in bot, created for every team like the Librarian. It appears in **Settings**
as **Built-in**, and in its panel at the top of Goals; nobody archives it, and only the team owner edits it. A team from before it
existed gets it on update once a computer and a model exist. Its template is `templates/catalog/goal-manager/`; its
playbooks are the product.

It does five things:

1. **Goals make sense.** It flags vague, duplicate or unmeasured goals and suggests a KPI or clearer wording, as proposals on
   the goal.
2. **KPIs.** It is the steward of every KPI. Each has a folder in its repository, `kpis/<slug>/`, with `definition.md`,
   `sources.md`, the query or script (`hub sql`, PostHog or another API through a tool the team declared), a
   known-values check it runs before trusting a number, and a changelog. It computes each KPI on its cadence in one routine
   pass with a time budget per KPI, posts readings with evidence, marks stale or missing data, and skips a failing KPI and
   reports it.
3. **Status.** After the readings it runs the status pass, which sets the automatic colours and never overwrites a human's.
4. **Check-ins.** When a KPI slips it asks the goal's owner what is happening and records the answer as a check-in on the goal.
   The readings are facts and the check-in is the owner's interpretation; they are kept apart.
5. **Guardrails.** A change to a definition or a target is a *proposal* that the goal's or KPI's owner confirms. It cannot
   change a target it is judged against: the API answers `403`. A short weekly goals review goes to the team owner (and
   the Chief of Staff, if there is one).

Its routines start paused, like a starter bot's first routine. Because it is built in, Tico starts its daily KPI pass once
the first KPI exists, once: a human who pauses it afterwards is not overruled.

### The CEO Bot switch

Each install has a CEO Bot switch, off by default. With it on, the Goal Manager is named **CEO Bot** in Settings > Bots,
on the team chart, on Goals (its panel, **Let CEO Bot set it**) and in its messages. On the chart and in the Goals tree
it sits under the owner, with the Heads (in their departments' groups) and the bots that report to no one under it
([Team chart](org-chart.md)). It keeps its slug, `goal-manager`, so its KPI readings, goal history and old messages stay
its own.

Only the picture moves. Nobody's `reports_to` changes and the CEO Bot gets no new access: it does the five things above
and proposes each goal's [Why](#a-goals-why), nothing more.

An owner or admin turns it on or off; a bot cannot, the Goal Manager included:

```
hub api POST ceo-bot '{"on": true}'
```

or `POST /api/v2/ceo-bot` with `{"on": true}` or `{"on": false}`. `GET /api/v2/ceo-bot` shows whether it is on, and who
changed it last and when. Turning it off gives back the name **Goal Manager** and the chart as they were. A name someone
gave the bot while the switch was on stays.

With the switch on, Goals also opens with the [Company section](#the-company-section) and each goal gets a
[Why line](#a-goals-why).

### How much your CEO Bot does

With the switch on, **Settings > CEO Bot** has three levels: 1 Goals and tracking (where every company starts), 2 Suggests
actions and 3 Acts on its own. Level 3 does what level 2 does until it arrives in a later release. Only the owner
or an admin changes the level, and the CEO Bot puts at most 5 items a day in one person's **Needs you**. The levels, who
changes them and what stays yours are on [Your CEO Bot](ceo-bot.md).

#### Recoveries and merged asks

At level 2 or 3, a red or stalled goal gets a **recovery** in its owner's Needs you, in place of "check why it is off
track": the evidence, up to 4 options and the CEO Bot's pick. Each option is a few changes to bots' tasks only: a new
task for a bot, a bot's task moved to the top of its queue, or a clash between two bots' tasks settled. **Approve** on
an option applies exactly that option, in the CEO Bot's name; **Reject** drops them all. Where part of an option
can't be done (a bot is paused, a task finished meanwhile, or you may not reorder bots' queues), it says why in plain
words, and what wasn't done comes back as a new recovery. Nothing is dropped. Lowering the level below 2 withdraws
the recoveries waiting.

When several bots ask you the same thing, the CEO Bot can make them **one item** (`POST /api/v2/ceo-bot/merge`).
Their tasks leave your Needs you while it is open, and your one answer goes to every bot that asked. If the item ends
without an answer, they come back as they were. A merged item counts toward the 5 a day.

After 10 recoveries in a row approved with its pick unchanged, the Monday note offers level 3 once, and at most once
a month (`level3_offer` in `GET /api/v2/ceo-bot`). The CEO Bot never raises its own level.

### The Company section

Only with the [CEO Bot switch](#the-ceo-bot-switch) on, the top of Goals is a **Company** section with two fields:

- **Vision**: one or two sentences on where the company is going, up to 400 characters, on one line.
- **Description**: a short paragraph on what the company does and for whom, up to 2000 characters.

While both are empty, the section says "Tell your CEO Bot what this company is for." and shows the two fields to
the owner and admins. Once something is saved, they change it with **Edit**. Everyone else signed in reads it and
cannot change it.

**History** lists every change to either field, newest first: which field, who changed it, when, the new words and
what they replaced. Saving the same words again adds nothing.

Only a person who is the owner or an admin writes the Vision and the Description, on their own click. No bot can,
the CEO Bot included, and neither can the Assistant acting for someone.

### A goal's Why

Only with the switch on, each goal in its owner's panel has a **Why** line: one sentence, up to 200 characters, on
how the goal serves the vision. Hover a goal in the tree to see its Why in the tooltip.

The CEO Bot proposes a Why. It shows under the goal as "CEO Bot proposes" with the line and its reason. A person
who may change the goal (see [Permissions](#permissions)) then chooses:

- **Accept**: the line becomes the goal's Why.
- **Edit**: change the words, then **Save** to accept your version.
- **Reject**: the proposal closes and the goal keeps the Why it had.

They can also write one themselves with **Write a Why**, or change an accepted one with **Edit**. Saving an empty
line clears it. Each change is in the goal's history.

A goal has one waiting Why at a time. When the CEO Bot proposes a new line, it replaces the one waiting. If you
accept a line that was replaced while you read it, Tico refuses and shows the new one. Setting a Why closes any
other Why still waiting on that goal.

A goal with no accepted Why shows "Not tied to the vision". That is a note, not an error.

Bots, the CEO Bot included, only propose a Why. They can never accept one or write one directly. A waiting Why
shows on its goal, and all the Why lines waiting on one person are one item in their **Needs you**, "These goals need
a Why line", never one per goal. It closes by itself once every one is settled.

With the switch off, the Company section and the Why lines are hidden, and every write to them answers `409`.
What was saved is kept and shows again when the switch is turned back on.

### Proposals

A proposal is what a caller makes when they may not make the change themselves. The owner confirms it (and only a human, on
their own click) or rejects it; confirming makes the change as that human.

| Kind | Payload | Confirmed by |
|---|---|---|
| `goal_wording` | `title`, `body` | the goal's owner |
| `goal_kpi` | an existing `kpi_id`, or a new `kpi` (`name`, `definition`, `unit`, `direction`, `cadence`, `source_note`), and a `target` | the goal's owner or the KPI's |
| `kpi_definition` | any of `name`, `definition`, `unit`, `direction`, `cadence`, `source_note` | the KPI's owner |
| `kpi_target` | the target fields, with `goal_id` | the goal's owner or the KPI's |
| `flag` | `issue`: `vague`, `duplicate` or `unmeasured`, and a `note` | the goal's owner |
| `goal_why` | `why`: one line, up to 200 characters ([A goal's Why](#a-goals-why)); only with the CEO Bot switch on | whoever may change the goal |
| `recovery` | `evidence`, 1 to 4 `options` (`label`, 1 to 6 `actions`: `create`, `reorder` or `settle` on bots' tasks), `pick`; only the CEO Bot at level 2 or 3 ([Recoveries](#recoveries-and-merged-asks)) | the goal's owner; `{"decision": "confirm", "option": n}` approves another option than the pick |

Pending proposals appear under **Needs you** on the Goals page for the human who decides them, except a Why, which
waits on its goal. To confirm a Why with your own words, send `why` with the decision:
`{"decision": "confirm", "why": "..."}`.

### What needs a person is a task

Each thing under **Needs you** is a task the Goal Manager asks of the person who must act, so it also shows in
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
again. A proposal still shows on the Goals page to everyone who may decide it; the task is only the asker's. Nothing is filed while the Goal Manager is not set up;
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

The goals tree is the main column; the Goal Manager has the right rail (below the tree on narrow windows): one line on what it does (keeps KPIs current and each goal green, yellow or
red; humans set the goals), its routines as the server has them (when each runs and when it next does, or paused), and its
last run with the first line of its result. The chat box at the bottom of the rail is your own chat with the Goal Manager, the one on its bot
page: ask it to change a goal and its latest reply shows there, earlier messages under **History**, and the tree is drawn
again when it answers. **Open bot** goes to its page. If it is off or not set up, the owner gets **Turn on** (`POST
/api/v2/goal-manager/turn-on`, the same steps as on update); with no computer or model yet, the panel says so.

One tree, built like the team chart: the team on top, then every human and every bot that is not archived, indented
under whoever they report to, whether or not they have a goal. Each is one line: the avatar and name, then the goal's
colour dot and its title (cut short; the whole title is in the tooltip) and its KPIs as small chips, a dot and the latest
value each (a count when there is no room). More goals follow on lines of their own under the first. The built-in bots (the
Assistant, BotOps, the Librarian and the Goal Manager) stay out of this tree and its goal owner choices; with the
[CEO Bot switch](#the-ceo-bot-switch) on, the Goal Manager is in the tree as the CEO Bot.
New teams get no default goals for built-in bots. Existing built-in goals are hidden here and kept in storage.
Message bots such as Inbox Manager follow separately under **Message bots**.

Tapping any line opens that owner's panel (a sheet at the bottom on a phone). It lists their goals: tap one to edit its
words, what it supports and its colour (or **Let Goal Manager set it**). Under each goal are its KPIs, its latest
check-in and **Add KPI**; tapping a KPI shows its history (a chart and the readings with their evidence), its definition
and version, its owner, its targets and the Goal Manager's latest check-in, with a way back. **Add goal** and **Add KPI**
(a KPI no goal uses yet) are at the bottom, owned by whoever was tapped, and the KPIs no goal uses are listed there too.
Someone with no goal opens on a new one; the team line opens the team goal. With the CEO Bot switch on, the
[Company section](#the-company-section) sits above everything, and each goal in a panel shows its
[Why](#a-goals-why). **Needs you** is a short strip at the
top, shown only when something waits: red KPIs on goals you own, stale data on KPIs you own, and definitions and targets
waiting for your confirmation. Each line links to its task. Initial page load makes two requests, the tree and Needs you; the Goal Manager panel and
the historical KPI view load only when opened.
People who may edit an owner's KPIs can open **Archived KPIs** in that owner's panel. The historical list is separate
from active KPIs; a KPI's detail panel offers **Restore KPI** to return it to active lists and freshness checks.

## Permissions

- With the CEO Bot switch on, everyone signed in reads the Company section and its history. Only the owner and
  admins edit it, on their own click.
- Team goals and team KPIs are visible to everyone signed in. A bot's goals and KPIs, and its automatic KPIs, are
  visible to whoever may Read the bot.
- A goal's owner, the owner of the goal it supports and anyone above the owner on the team chart set its colour, edit it and
  choose its KPIs and targets. The team owner may do all of it.
- A KPI's owner (or anyone above them; the team owner for a team KPI) edits the KPI. Its readings are logged by that
  human, by the Goal Manager, or by the owner of a goal that uses it.
- The Goal Manager reads every goal and KPI, writes readings and the automatic colours, records check-ins, and proposes. It
  cannot set a colour by hand, edit a goal, create or link a KPI, change a definition or a target, or archive a KPI.

## `hub` and the API

```
hub goal list [--owner X] [--all]              hub kpi list [--goal ID] [--owner X] [--unlinked] [--bot SLUG] [--archived]
hub goal show <id>                         hub kpi show <id>
                                             hub kpi archive <id> | restore <id>
hub goal create / update                   hub kpi create "<name>" [--goal ID] [--definition ..] [--unit ..]
hub goal status <id> <colour> "<why>"          [--direction ..] [--cadence ..] [--owner ..] [target flags]
hub goal status <id> auto                         hub kpi update <id> [fields]
hub goal refresh                           hub kpi link <goal> <kpi> [target flags]  |  hub kpi unlink <goal> <kpi>
hub goal checkin <id> "<words>"            hub kpi log <kpi> <value> ["<note>"] [--period-end D] [--evidence ..]
hub goal checkin-list <id>                         [--quality ..] [--supersedes READING]
hub goal needs-you                         hub kpi show <kpi> [--effective]
hub proposal create | list | decide
```

Target flags: `--baseline N --target N --deadline YYYY-MM-DD` (an improvement) or `--min N --max N` (a range). Every command
has an MCP tool of the same meaning (`hub_goal_*`, `hub_kpi_*`, `hub_proposal_*`).

Archived KPIs stay available in `hub kpi show <id>`, `hub kpi list --archived`, and the owner's **Archived KPIs** view. The active list, goal views,
Goal Manager freshness checks and Needs you omit them. Archive and restore require the KPI's owner or someone above
them; each transition is audited, and neither changes its definition history, goal links, readings, privacy or owner.
The Goal Manager cannot archive a KPI.

The stable v2 routes are in [openapi/v2.json](openapi/v2.json):

| Route | What it does |
|---|---|
| `GET /api/v2/goals`, `/goals/tree`, `/goals/{id}` | list (mine, or `all=1`), the whole tree with each goal's KPIs, one goal with its KPIs, check-ins, tasks and history |
| `POST /api/v2/goals`, `/goals/{id}` | create, edit |
| `POST /api/v2/goals/{id}/status` | set the colour by hand: an override with a note |
| `POST /api/v2/goals/{id}/status/auto` | hand the colour back to the Goal Manager |
| `POST /api/v2/goals/refresh` | the status pass (the Goal Manager or the owner) |
| `GET/POST /api/v2/goals/{id}/checkins` | the owner's words on how it is going |
| `GET /api/v2/goals/needs-you` | red KPIs, stale KPIs and proposals that need the caller |
| `POST /api/v2/goals/{id}/kpis` | link a KPI (`kpi_id`) or make one and link it (`name`), with the target |
| `POST /api/v2/goals/{id}/kpis/{kpi}` and `/unlink` | change the target on a link; remove the link |
| `GET/POST /api/v2/kpis`, `GET/POST /api/v2/kpis/{id}` | list, create, read (with its links, readings, definition history and check-ins) and edit |
| `POST /api/v2/kpis/{id}/archive`, `/restore` | hide or restore a KPI; only its owner or someone above them; history stays |
| `GET/POST /api/v2/kpis/{id}/readings` | the readings (`?effective=true` leaves out corrected ones); post one |
| `GET /api/v2/bots/{bot}/kpis` | a bot's automatic KPIs |
| `GET/POST /api/v2/proposals`, `POST .../{id}/decide` | list, propose, confirm or reject |
| `GET/POST /api/v2/company` | read the Vision and Description (with `on` and whether you may edit); save them (owner or admin) |
| `GET /api/v2/company/history` | every change to the Vision and Description, newest first, with who and when |
| `POST /api/v2/goals/{id}/why` | set a goal's Why (`{"why": "..."}`; empty clears it); a person who may change the goal |

A goal's KPI on any of these answers has `latest`, `freshness`, `spark`, and, under a goal, `link`, `target_label`, `status`
(`green`, `yellow`, `red`, `gray` or `none`) and `reason`.

## What changed for existing data

Nothing is deleted. Each old KPI keeps its readings and becomes a standalone KPI, owned by its goal's owner, monthly, with a
link to its old goal; an old `target` becomes an improvement target on that link (with no deadline until someone sets one).
An old reading's `ts` is now its `period_end`, and its `source` of `estimate` is its `quality`. A colour someone set is now a
human's override: it stays as it is until it is handed back.
