# How Tico works

One Tico server holds the team's shared work, and your computers run the bots. Humans reach the server from the web
app, Slack (DM Tico) or MCP and the CLI; each computer's runner asks it for work, runs the bot on that computer's own
model subscriptions and sends the results back. The same picture is the in-app Help page (**?** in the sidebar).

![How Tico works](images/help-desktop-light.png)

- **Tico server**: tasks, goals and KPIs, docs, updates, messages and Credentials. It runs no bots
  ([Architecture](architecture.md)).
- **Computers**: your Macs or Linux and Docker machines, with the runner. Each bot is a Git repository with its
  Instructions and memory, on one computer.
- **Team**: humans and bots are teammates on one team chart, in groups.
- **Built-in**: four bots every team gets. The [Assistant](assistant.md) is each human's own helper; BotOps sets up and
  fixes bots, computers, Tools and Credentials; the [Librarian](librarian.md) answers from the team's docs and the Tico
  manual; the [Goal Manager](goals-and-kpis.md#the-goal-manager) keeps KPIs and goal status current.
- **Message bots** work a human's email or a Slack channel. **External agents** (Hermes, OpenClaw, Codex, Claude) connect
  over MCP with a token ([Connect an external agent](connect-an-agent.md)).
- **Tools and Credentials**: a bot gets only the Credentials granted to it, and drafts to outsiders until its send switch
  is on.

The rest of this page is the current system in detail. For a teammate's questions read [Using Tico](using-tico.md).
Words are defined in the [Glossary](glossary.md).

## The six nouns

- **Bot** — an AI teammate (teammates are humans or bots) with its own `bot-<slug>` repository, run on one registered computer,
  or run by an external agent such as a Hermes profile that reaches Tico with its own
  credential and is never dispatched to ([Hermes agents](hermes-agents.md)).
  Record states: `active`, `paused`, `planned`, `quarantined`. Live status while it works:
  `idle`, `running`, `waiting_human`, `waiting_bot`, `blocked`, `limited`, `crashed`, `paused`,
  `quarantined`.
- **Task** — a unit of work with a requester, an owner (bot or human), a title, a body and a
  note; a `lane` (`company` or `product`), a `rank` in its owner's queue, labels, links (a pull
  request first), an optional parent and an optional `blocked_by`. States: `open`, `doing`,
  `waiting`, `review`, `ready`, `done`, `closed`, `declined` (`review` and `ready` belong to the
  product lane). The owner marks it `done`; only the requester (or any human) closes it. Every
  task has a comment thread, on the record with the author.
- **Message** — one line in a conversation. Kinds: `say`, `ask`, `answer`, `notice`, `steer`.
  A bot's `ask` to a human is what lands in **Needs you**.
- **Approval** — a human's yes or no to one exact action. Kinds: `send`, `spend`, `publish`,
  `merge`. A bot may request one when uncertain about a specific action; authorized work needs no separate
  approval. Only a human decides; an approval is spent once and does not turn outbound sending on. Rules: `policies/approvals.md`.
- **Routine** — a cron schedule owned by a bot: `id`, `title`, `cron`, `timezone`, `template`
  (a playbook file) or inline `instructions`, `labels`. Each occurrence becomes a task.
- **Goal** — what a human or a bot is for: a title, an owner (`company`, a human or a bot), the goal it
  supports (`parent_id`, optional) and a colour with a one-line note. The colour is set automatically
  from the goal's KPIs (or its owner's check-ins and tasks) by the built-in **Goal Manager**; a human
  may override it, and it stays theirs until they hand it back. `done` or `dropped` when it ends. A task
  may name the goal it serves. Bots read theirs with `hub goal list`; nothing is pushed into a run.
- **KPI** — a measure that stands on its own: a name, definition, unit, direction, cadence and one owner.
  A goal links to zero or more KPIs and the target lives on the link (an improvement by a deadline, or a
  range to stay inside). Readings are facts with a period, evidence and a quality; they are never edited,
  and a correction is a new reading. A KPI with no fresh data is gray, never zero. Every bot also has
  automatic KPIs computed from Tico's own data. See [Goals and KPIs](goals-and-kpis.md).

The rules for all six live in the backend's write layer, not in prompts: a bot acts only as
itself; it may message only active bots and humans; at most 20 bot-to-bot messages per
conversation per hour and 10 unsolicited messages per human per bot per day
(`TICO_UNSOLICITED_PER_DAY`); one open clarifying question per task; the requester closes; an approval
is optional, decided by a human and consumed once. Bot requests to a human are linted:
a nonempty first line and under 120 words outside quoted drafts. Put the ask first; the first-line
check does not judge whether it is an ask. Replies to the human's own message are exempt from this
request-format lint, and so is a task on a custom type, which is a ticket on that type's board
rather than a request ([Tasks](tasks.md)); title and Credential checks apply separately. Repeated refusals
open a review task and, at 10 a day, pause the bot for an hour; a third attempt in a day to reach another bot's files or a `secrets/` path
(`TICO_ESCAPE_QUARANTINE_AT`) quarantines it until a human clears it.

## The three places

**hub.acme.example** — the record, the interface and the clock. A FastAPI backend (`backend/`) on one
EC2 instance with SQLite on an encrypted EBS volume, attachments in a private S3 bucket, backed up
to S3 twice over (Litestream replicates every ten seconds; a verified bundle of the database and
every attachment is taken daily and restore-tested), behind Cloudflare Access. It holds every task, message, approval, routine, run and
bot setting, serves the web interface (`ui/`), and runs the scheduler that turns due routines into
tasks. It never runs a bot. It runs from the Docker image ([install.md](install.md)). Beside it
on the same host, the Slack gateway (`backend/slack_gateway.py`, its own unit) turns a DM to
Tico or an `@Tico` in a channel into a message for the bot the decision model routes it to, posts the
reply back under that bot's name, and stores everything else said in the channels Tico is
in so that each channel's readers see it within the hour (`docs/slack-gateway.md`).
Pages: **Tasks** (where the app opens), **Meetings** (imported transcripts, source sync status, Import, and each meeting's
action items), **Docs**,
**Tools**, **Changelog**, the **Team** tree, and under your email **Runs** and **Settings**
and, for the owner and admins, **SQL**. Tasks has List, Board and Done; Routines are in **Settings → Routines**.
Credentials are in **Tools → Credentials**. See [Navigation](navigation.md). A bot's page has **Chat**,
**Tasks**, **Docs** and **More**. The assistant (Tico, `coo`), BotOps, the Librarian and the Goal Manager are built in and cannot be archived or deleted (`409 system_bot`); the assistant
and the Librarian work in the background and are not listed for humans. Each human has one private **Assistant** chat, its own page from the left rail
(also the first tab on their own page, and "Ask the Assistant…" in search): it looks things up at once, does low-risk things as that human, and
proposes anything with a side effect for their own click ([The Assistant](assistant.md)). There is no shared
Tico chat page and no Tico Live; humans can also act across Tico through their own external agent over Tico's
MCP ([Connect an external agent](connect-an-agent.md)).
The database itself is readable with plain SQL (`hub sql`, the SQL page, `POST /api/v2/sql`):
one `SELECT` at a time, each caller seeing only what the JSON API would show it, credentials never
([Querying Tico with SQL](hub-sql.md)).

**Your Mac's runner** — `runner/`, started as `python -m runner`, registered once with
**Add computer** in **Settings → Computers** (`scripts/setup-runner.sh`, credential in
`~/.config/tico/runner.json`). Four launchd jobs, installed and managed with `scripts/tico`:

| Job | Command | What it does |
|---|---|---|
| `team.tico.tico-bot` | `python -m runner run` | Heartbeats every 15 s with readiness, claims work for the bots assigned to this computer, runs each run in the bot's local repository with the local model CLI, streams output back |
| `team.tico.tico-connectors` | `python -m runner connectors` | Publishes calendar snapshots, executes Tico-queued calendar appointments, and pushes persisted mail from local Google access (Ana's Mac, or a Linux runner that holds the Google key) |
| `team.tico.tico-close-calls` | `python -m runner close-calls` | Every five minutes, imports available Close call and Notetaker meeting transcripts without fetching audio, using the local Close key (Ana's Mac only; `scripts/tico install close-calls`) |
| `team.tico.tico-importers` | `python -m runner importers` | Runs the meeting importers the owner turned on for this computer (Zoom, Google Meet, Granola), using credentials kept in `secrets/` here; see [Meetings](meetings.md#meeting-importers) (`scripts/tico install importers`) |

A Linux runner (the Docker image) has no launchd: with `TICO_SIDE_JOBS=1` (set by the image) `python -m runner run` supervises `importers` (while Tico assigns importers to this computer) and
`close-calls` (while the Close key is in `secrets/`) and `connectors` (while `secrets/google-sa.json` is there, or
Tico names this computer's owner in `TICO_PROCESSING_OPERATORS`) as child processes with restart and backoff
(`runner/sidejobs.py`). A Mac keeps its checkout-sibling layout; a Linux runner names the same places with
`TICO_PROJECTS_DIR` and `TICO_MAIL_VENV` (see [Message bots](mail.md#works-on-linux-runners)).

Logs are `~/.config/tico/logs/tico-{bot,connectors,close-calls,importers}.log`. The runner makes outbound
HTTPS calls only; nothing listens on the Mac. Model subscriptions (Codex, Claude, Gemini, Grok
Build) are signed in on the Mac and never leave it. Bot Credentials are stored in
**Tools → Credentials**, granted to each bot and delivered only for its run. Existing own files,
`_shared.env` and declared profiles migrate into grants on upgrade; new runs do not load those files.
Model API keys stored in Tico are separate from local subscription logins.
Each run gets a scoped credential in `HUB_API_URL`/`HUB_TOKEN`; the bot talks to hub.acme.example
through the `hub` CLI (`scripts/hub`) or Tico's MCP tools (`hub_*`, the same names with
underscores: `hub task create` is `hub_task_create`), and downloads attachments with
`clients/files.py`. The tool schema is `clients/hubtools.py` and is the contract; the two are
one implementation behind two transports. Codex and Claude runs get the MCP server for free
(the runner spawns `clients/hubmcp.py` with the run's credential); anything that speaks MCP
over HTTP can also `POST /api/v2/mcp` with a bearer token. Either way every call goes through
the same routes and `backend/hubdb.py` rules; there is no privileged path.

Calendar appointments are three shared Tico tools available to every bot:
`hub_calendar_list`, `hub_calendar_schedule`, and `hub_calendar_status`. Reads come from the
bounded calendar snapshot, limited to the team owner's or the bot operator's calendar
([calendar paths](mail.md#calendar)). A schedule call writes an idempotent Tico action; the private calendar tool
claims it once, creates the Google event and invitations locally, and reports the result. Bots must
see `succeeded` before saying the appointment exists. This path grants no generic Gmail authority.

One of those tools is a second model, the decision model. `hub_decision_ask` (`hub decision ask`) sends a JSON state and typed
questions to the decision model, and gets a calibrated answer per question back:
a choice with a confidence, a score on ordered levels, or the probability a statement is true (yes/no, choice and score: the question format of OpenRouter's Decisions API).
It writes no prose, so a bot uses it to decide (sort a listing, dedupe, route, gate a draft) and
its own model to write. Tico holds the one key and audits every call by label, never the
state (`backend/judge.py`); the decisions themselves are versioned files in `questions/`
(`questions/README.md`), which the meeting brain and the mail CLI read the same way a bot does.
`skills/decisions/SKILL.md` says when to reach for it.

**The bot's repository** — `acme/bot-<slug>`, checked out at `<projects>/bot-<slug>` on the
Mac that runs it (`~/tico-work` on Ana's Mac). The contract (`templates/employee-repo/`):

| File | What |
|---|---|
| `AGENT.md` (with `AGENTS.md`/`CLAUDE.md` pointing at it) | its instructions, what it owns, how it starts and ends a run; read every run |
| `bot.yaml` | `name`, `runtime`, `model`, `reasoning_effort`, `max_run_minutes`, `tools:`, `outbound_send` (`routines:` only in a template, as the seed) |
| `state.md` | current focus, open threads, next step |
| `memory/learnings.md`, `memory/decisions.md` | how to do the job; dated decisions |
| `knowledge/` | what is true in its domain, one topic per file, dated sources |
| `playbooks/` | repeatable methods; a routine's text is usually one of these |
| `reports/`, `software/`, `skills/` | dated deliverables, its own tools, runtime skills |
| `<slug>.data/` (sibling, never committed) | large or regenerable data |

Bots commit directly to `main`; the runner pushes after each completed run and once at start-up
for every assigned bot, and leaves a diverged checkout for a human (one log line an hour). Files
bots produce for the team are published to Tico's private store and listed on the bot's page
([Files](files.md)).

## What happens when

**You send a message or file a task.** hub.acme.example saves it and queues a job for the bot. The bot's
runner claims it on its next poll, does one run (prompt = `AGENT.md`, the conversation, the task,
your message and attachments), and streams the reply back. The conversation shows *Saved — queued*,
*Starting*, *Working*, then the reply. Closing the browser changes nothing.

**A routine fires.** The server (America/Los_Angeles unless the routine says otherwise) creates one
task for the bot with the saved instructions and version. Missed occurrences coalesce; a task still
open from the last occurrence absorbs the next one. The runner claims it like any task. The wake,
the note, and the bot's answer land in that bot's Chat room (the owner's room for a personal bot,
the shared room otherwise), so the next run can read them.

**The Mac is asleep.** Everything on hub.acme.example stays available and accepts work. After 60 s
without a heartbeat the bot shows *offline* and new messages show *Saved — waiting for <computer>*.
A sleeping Mac is not reliably absent: macOS wakes it for a few seconds at a time to check the
network, and the runner asks for work in those seconds. So a computer that has been silent for
more than 60 s must then report in without a break for two minutes before it is given anything.
Contact means a heartbeat or a claim, whichever arrives: a wake of a few seconds never reaches
the runner's fifteen-second heartbeat timer, so counting only heartbeats let each brief wake
stand on the record of the one before it and take work anyway,
and until it has, its queued work reads *Saved — waiting for <computer> to stay awake*. A brief
wake cannot take a 90 s lease it has no chance of finishing, and a computer that never went away
is unaffected — including every computer already enrolled when this rule arrived.
A run that was mid-flight loses its 90 s lease: not-yet-started work is re-queued; started work
becomes *Interrupted*. Fifteen minutes later (after the runner's own chance to reclaim it) the
scheduler settles what needs no eyes: a delivery whose task is already done or closed is dismissed,
and a run that recorded no tool call and consumed no approval is resumed, each with a note
in the job's recovery record (`job.auto_reconcile` in the event log). A run that used tools stays
*owner review needed*, because Tico cannot see what a tool did on the Mac; the bot's owner
resumes or dismisses it after checking what already happened. Nothing is lost or re-sent.
While such a review is owed the bot's tasks and routines wait, but a human's own chat
messages still run: nothing about them is uncertain.

**Needs you.** Every issue Tico derives says whether a human has to act. Those that do (a
review owed, a computer gone for more than ten minutes, a missing agent credential, a stale backup,
a usage limit hit three times) open in a dialog in front of the human the first time they appear,
wherever they are in the app, with the action inline: *Review now* opens the interrupted work,
*Open bot* goes to the bot. Closing it is remembered per issue and *Snooze* quiets it for an hour;
the alert under the sidebar reopens it any time. Issues Tico resolves itself (a computer that
just dropped off, a usage limit that is retrying) stay out of the way as *resolves on its own* in
**Settings → Health**, one row per computer rather than one per bot. A cloud deploy is different: hub.acme.example is
unreachable for a few minutes, the runner keeps the run going and the server extends every
running lease when it comes back, so the finished reply still lands. The same forgiveness covers
a server that stalls without restarting (the daily backup runs at the lowest CPU and I/O
priority for this reason): a lease lapsing while the server has answered no runner for more
than half a lease period gets one more period instead of expiring, and a run that did
expire is handed back to its runner when the runner reports in within 15 minutes, as long as no
one has reviewed the job meanwhile (`attempt.lease-restored` in the event log). The runner's log
(`scripts/tico logs bot`) writes one timestamped line when the cloud becomes unreachable, a
progress line at most once a minute while it stays so, and one when it is back; `scripts/tico
status` says when the running bot job predates the checkout's current commit. On a bot's Chat
page, one line above the composer says when the bot is paused and why (a usage limit, a
quarantine, or a Mac offline for more than ten minutes) and that messages are saved, or that a
review is owed while chat still answers.

**A subscription usage limit.** A run that hits one fails with `limited` set; it did nothing, so
the job stays queued however often this happens (it is never *Interrupted*), the bot shows
*limited* and the cloud claims it again after 30 minutes. A third limit in a row waits two hours
and says so in **Settings → Health**, with the retry time. If the bot has a fallback
harness in **Settings → Bots**, the runner reruns that run at once on the fallback (fresh
session) and reports `fallback` as that harness, which **Runs** shows in the Session column.
Tico is seeded with Gemini CLI as its fallback from Antigravity, so a usage limit uses
a stored `GEMINI_API_KEY` Credential granted to the bot. **None (fail)** means no hop:
the cloud cooldown applies.

**A bot's session is its own.** By default each bot has one provider thread. BotOps defaults to
one thread per task and per chat conversation so unrelated jobs and requesters stay separate.
Other bots can opt into that isolation with `session: task`; explicit `session: bot` keeps one
thread. Wakes about the same task resume its thread. Sessions use the same local `bot-<slug>` checkout,
and only that computer knows which thread
it is. Tico never ends a thread, never asks the bot for a checkpoint before a model or computer
change, and never rebuilds history into a prompt: when the conversation fills the model's window
the runtime compacts it, and a run carries only what the room said since the bot last answered.
A bot that wants what was said before reads it with `hub conversation show <conversation id>`: the
newest 200 messages, then `--before <next_before>` for each older page. Every run's prompt names the
conversation and that command, because a compacted session, or one on a new model, no longer holds
all of it. The runner fast-forwards the checkout from origin before the run, and
pulls or clones any `reads:` sibling repos beside it.

**A bot needs you.** It asks with `hub task ask` (the task goes `waiting`), requests an approval,
or files a task for you. All of these appear in **Needs you** at the top of **Tasks** with
**Reply**, **Approve** / **Decline**, **Done** / **Close**. Needs you is your queue: approvals and
questions first (each blocks a bot), then your own tasks in rank order. Tickets on a numbered type
stay on their board and come to Needs you only with a question for you ([Tasks](tasks.md)). There
is no priority; a mover (anyone in the leadership, product or engineering group) drags a task up or
down, into another column, or between lanes; everyone else sees the board and comments.

**A bot finishes.** It commits its repository, marks the task `done` with a note, and the reply is
saved in the conversation. The task shows under **Tasks → Done** with the bot's note; a bot that
requested it gets a *Finished: <title>* message (`/api/v2/messages?unread=1`) and may choose Close.
Done stays Done until someone authorized closes it. Every run is listed under **Runs** and on
the bot's **More** tab.

**You change a bot's Instructions.** Open **Bot → More → Instructions → Edit Instructions**
and ask BotOps to make the change. It commits and pushes the update for the next run. For a manual edit, change `AGENT.md` (or a playbook, `memory/`, `knowledge/`) in
`bot-<slug>` and commit. The runner reads the checkout on its Mac at the start of every run, so
the change is live on the next run once that checkout has it: push, and pull on the runner Mac
if you edited elsewhere. Do not edit while the bot is running there.

**You change a routine.** Routines are rows in Tico (`docs/routines.md`): edit one on the
site under Settings → Routines, or a bot changes its own with `hub routine set`. The change is in
the table at once; a sleeping Mac does not block it. **You change a bot's switches.** Tools
read `outbound_send` and `tools:` from the Mac checkout each time a bot sends or posts. Model, effort, computer, humans, name,
status and reporting line are changed in **Settings → Bots**, not in the file.

## Calling the API from a script

A human is normally the browser sign-in (Cloudflare Access). A **personal API token** is the
same human from a script, a cron job or another computer, with no browser: any human (the owner may
limit it to admins, Settings > Humans) opens **Connect an external agent** beside their email
(or, for an admin, **Settings → Computers → API tokens**), gives the
token a label and a life (90 days unless changed, a year at most), and copies it once; it is
not shown again and only its hash is kept. Then:

```
export HUB_API_URL=https://hub.acme.example HUB_TOKEN=tico_pt_...
hub whoami            # human:<you>
hub sql "SELECT slug FROM bots"
curl -H "Authorization: Bearer $HUB_TOKEN" https://hub.acme.example/api/v2/bots
```

No `HUB_BOT`: the token is you, not a bot. It is you for every purpose, with the rights you
have in the browser (a member sees what a member sees, an admin adds bots for their own
account, the owner does what the owner does), and it leaves the same audit trail. The one thing a token cannot do is make
or revoke tokens; that takes a signed-in browser, so a leaked token cannot extend its own life.
Revoke one on the same page, and it stops at once; the owner may revoke anyone's. Every token
made or revoked is an `events` row (`token.create`, `token.revoke`).

## Where things are

| Directory | What |
|---|---|
| `backend/` | the cloud API, authorization, write layer, scheduler, backups (FastAPI, SQLite) |
| `runner/` | the local runner: enrolment, readiness, leases, runs, connectors, Close call import |
| `clients/` | what bots and the runner call: the `hub` CLI, the HTTP client, attachment download, routine validation, preflight, the docs read and write commands |
| `connectors/` | shared Slack, mail/calendar and browser adapters bots use instead of vendor APIs |
| `integrations/` | one page per outside system (what it is, how a bot uses it, rules, recipes) and the query lists; served as **Tools** and `hub tool show <service>`, with the learnings bots add |
| `questions/` | the question sets the decision model answers (`hub_decision_ask`, `hub decision ask`, `mail inbox --decisions`, the meeting brain): one versioned JSON file per decision, with its thresholds |
| `ui/` | the web interface (`index.html`, `app/` scripts, `styles/`, feature scripts, browser tests; see `ui/README.md`) |
| `app/` | the native macOS shell around hub.acme.example |
| `infra/` | the EC2 stack, release packaging, deploy and restore scripts |
| `scripts/` | `tico` (operate the runner), `hub` (the bots' CLI), setup, publishing and maintenance |
| `docs/` | team knowledge |
| `policies/` | rules every bot follows: approvals, access, handoffs, writing, shared rules |
| `registry/` | bootstrap data: humans, sign-in access, mail rules, Slack channels; `employees.yaml` seeds a new database only |
| `templates/` | the bot repository contract and prompt templates |
| `skills/` | shared runtime skills for bots |

To reopen a finished task, open it and choose **Reopen**. After **Done**, the toast offers **Undo**.
Reopening clears its completion dates and closer, so Goals and KPIs count it as active again;
the earlier completion stays in task history. Questions may be asked in sequence, with one open
clarifying question on a task at a time.

An ordinary bot-to-bot `hub message send` starts work but does not return the receiving bot's
final answer. Use `hub question ask` (`hub_question_ask` over MCP) when you need an answer.
A bot receiving an ordinary message that asks for a reply sends that reply explicitly with
`hub message send`. Humans' chat replies and ask-message answers are delivered automatically.

Computer readiness covers only bots assigned to that Computer. Eligible bots are placement
options, and their missing repositories do not count as readiness or Health failures there.

## Task pipelines

Task types add named steps above the existing status contract. General is built in and backfilled
onto existing tasks. Every status write maps to a compatible step, or clears the step when the
type has none, so older runners and GitHub webhook moves keep working. Movers manage definitions
in Settings → Types; the board's type filter uses steps as columns. See [Tasks](tasks.md).

## On computers

Each computer keeps base clones of ticked repositories that its assigned active bots can reach in
`<workspace>/repos/<owner>__<repo>/`, beside the existing bot folders. The runner checks the list
each heartbeat, clones one repository per cycle in the background, and fetches at most every
15 minutes. These are plain clones of the default branch; setup commands and task worktrees
come in a later phase. Fetching uses a temporary GitHub App read token kept out of git config
and logs. Run tokens still follow each bot's own repository access.

Heartbeats and `python -m runner doctor` report each base clone's state, last fetch, disk use and
any error. A base clone failure does not change a bot's readiness. A clone that has been absent
from this computer's repository list for 30 days is removed from `repos/`. Older servers without
the repository endpoint leave this feature idle.
