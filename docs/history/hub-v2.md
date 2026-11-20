> Historical. Describes the hub.db keeper generation. The write-layer rules in §4 still hold in the cloud backend; the keeper, the local hub server and the schema are retired. For the current system read [how-it-works](../how-it-works.md).

# Hub v2: messages, tasks, status, and always-on agents

## Cutover behavior

The remaining GitHub issues were discarded; none were imported. GitHub Issues are disabled
on Tico. All employee configurations resolve to `host: keeper`, `tasks: hub`; the old
dispatcher tick is inert. The CLI and app create SQLite tasks only. Existing playbooks that
mention GitHub task commands are superseded by the keeper’s cutover instruction on every turn.
Do not rebuild discarded tasks from history. Repositories remain for code and durable files.

The board reads SQLite tasks and future recurring schedules only. Scheduled means the next
future run of an active routine, with its start time. Immediate bot tasks appear in Doing
(with “starting” until picked up); Waiting is separate and shows a dependency in task details.
Human-owned tasks appear in Needs you. Done remains a separate view. No GitHub tracking links.

The older phased-rollout notes below describe history, not permission to use GitHub tracking.


The build contract for the system the owner decided (with its scenario tests). This file is what the code
must match. Keep it simple: the main complaint about the Issue system was that it was
"unnecessarily complex to understand".

## 0. Principles
- **hub.db is the record.** SQLite, WAL, at `<projects>/runtime/hub.db`. Backed up nightly to
  S3 (`s3://$HUB_BUCKET/hub-db/<date>.sqlite`). No GitHub mirror. Repos stay the bots' memory.
- **Always-on agents.** One persistent runtime thread per hosted bot. A message or a task is a
  turn on that thread; nothing cold-starts. Crashed sessions are resumed, forked, or restarted.
- **Three kinds of things.** *Messages* (conversation: bot↔bot, bot↔human). *Tasks* (a to-do:
  title, body, who asked, who owns, status; the requester closes). *Approvals* (only for send,
  spend, publish, merge: the exact thing attached, one tap, spent once).
- **Controls live in the write layer, never in prompts.** Identity, reach, caps, kinds,
  refusal counting, quarantine.
- **Coexistence.** The dispatcher can keep running legacy GitHub-Issue bots from its YAML input.
  Cloud bots live in the backend `bots`/`bot_config` directory and are hosted by a registered
  runner selected in `assignments`. Codex, Claude, Gemini, and Grok Build are supported runtimes.

## 1. Files
| Path | What |
| --- | --- |
| `dispatcher/hubdb.py` | schema, migrations, the write layer (every rule), read helpers. Pure Python + sqlite3. |
| `dispatcher/hubcli.py`, `scripts/hub` | the `hub` command bots call inside turns (thin: parse args, call hubdb, print JSON). `scripts/hub` is executable and symlinked to `~/.local/bin/hub`. |
| `dispatcher/keeper.py`, `scripts/keeper.sh`, `scripts/hub.sh` (install/status/restart) | the always-on host: runtime daemons, threads, mailbox loop, schedules, status, recovery, limits, backup. launchd `team.tico.keeper`. |
| `dispatcher/hosts/codex.py`, `dispatcher/hosts/grok.py`, `dispatcher/hosts/base.py` | runtime hosts behind one interface. |
| `dispatcher/hubserver.py` | new `/api/v2/...` routes over hub.db; SSE stream for deltas. |
| `ui/index.html` (the old web app, since removed) | chat over messages; status on hover; Needs you from tasks; tasks tab. |
| `docs/history/hub-v2.md` (this), `docs/history/architecture.md` (one section), `templates/employee-repo/AGENT.md` (the `hub` CLI paragraph) |
| tests: `dispatcher/test_hubdb.py`, `test_hubcli.py`, `test_keeper.py`, `test_hosts.py`, `test_hubserver_v2.py` |

## 2. Schema (hubdb.SCHEMA, applied with `PRAGMA user_version` migrations)
```sql
bots(slug PK, display_name, runtime, model, effort, cwd, host, thread_id, token_hash,
     state, created, last_turn_at)                       -- host: dispatcher|keeper; state: active|paused|planned|quarantined
humans(id PK, name, email, slack_id, teams_json)
conversations(id PK, kind, subject, task_id, participants_json, created, last_message_at, closed_at)
     -- kind: chat|ask|task|notice
messages(id PK, conversation_id, from_actor, to_actor, kind, body, refs_json, in_reply_to,
         created, delivered_at, read_at, expires_at, wait_s)
     -- actor: "bot:<slug>" | "human:<id>" | "keeper"; kind: say|ask|answer|notice|steer
tasks(id PK, title, body, requester, owner, status, due, parent_id, conversation_id,
      created, updated, done_at, closed_at, closed_by, note)
     -- status: open|doing|waiting|done|closed|declined; waiting_on text in `note`
task_events(id PK, task_id, ts, actor, field, old, new, note)
approvals(id PK, kind, task_id, message_id, payload_json, payload_hash, requested_by,
          decided_by, decision, decided_at, consumed_at, created)
     -- kind: send|spend|publish|merge; decision: approved|declined
bot_status(bot PK, state, focus, task_id, since, last_turn_at, last_result, next_due,
           open_tasks, needs_human, updated_at)
     -- state: idle|running|waiting_human|waiting_bot|blocked|limited|crashed|paused|quarantined
bot_status_history(id PK, bot, state, focus, task_id, since, until, reason, by)
schedules(id PK, bot, cron, title, playbook, last_fired, next_due)
     -- the cloud adds source: 'repo' for rows the runner synced from a bot repository
turns(id PK, bot, thread_id, started, finished, trigger, message_id, task_id, exit,
      tokens_in, tokens_out, cost, summary)
deltas(id PK, turn_id, seq, kind, text, ts)              -- streamed text/thought; pruned after 1h
rate_limits(runtime PK, used_percent, window_minutes, resets_at, updated)
refusals(id PK, ts, actor, rule, detail_json, severity)  -- severity: normal|sensitive|escape
events(id PK, ts, actor, action, target, detail_json)
```
Ids are uuid4 strings except `bots.slug`, `humans.id`, `bot_status.bot`, `rate_limits.runtime`.
Timestamps ISO-8601 UTC.

## 3. Actors and authentication
- `human:<id>` writes come from the hub server, which verified the person (Cloudflare or
  local). `keeper` writes come from the keeper process. `bot:<slug>` writes come from the `hub`
  CLI, which reads `HUB_EMPLOYEE` and `HUB_TOKEN` from the turn's environment; the keeper sets
  both when it starts or resumes the bot's thread, stores `sha256(token)` in `bots.token_hash`,
  and rotates it on every resume. hubdb verifies before any bot write. No `--as`.

## 4. The write layer (hubdb) and its rules
Every function takes `actor` first, validates, writes, appends an `events` row, and returns the
row or raises `Refused(rule, detail, severity)`. Refusals are also inserted into `refusals`.

```python
open_conversation(actor, participants, kind="chat", subject="", task_id=None) -> conversation
say(actor, to_actor, body, conversation_id=None, kind="say", refs=None, in_reply_to=None, wait_s=None) -> message
answer(actor, message_id, body, unknown=False) -> message           # kind=answer, in_reply_to set
notice(actor, to_actor, body, refs=None, expires_days=14) -> message
task_create(actor, title, body, owner, due=None, parent_id=None) -> task   # status open
task_update(actor, task_id, status=None, note=None, owner=None, due=None, body=None) -> task
task_close(actor, task_id, note="") -> task                          # requester (or a human) only
approval_request(actor, kind, payload, task_id=None) -> approval     # payload exact; hash; message to the human owner
approval_decide(actor, approval_id, decision, note="") -> approval   # humans only
approval_consume(actor, approval_id) -> approval                     # once; the connector calls it
status_set(actor, bot, state=None, focus=None, task_id=None, reason="") -> status   # moves the old row to history
schedule_sync(actor, entries) ; rate_limit_set(actor, runtime, ...) ; turn_start/turn_finish(...) ; delta_append(...)
# reads (no actor needed): conversations_for(actor), messages(conversation_id, since), inbox(actor), tasks(owner=, requester=, status=), needs_you(human), status_all(), status_history(bot), turns(bot, since)
```
Rules (each has a test):
1. **Identity.** A bot may only act as itself; `bot_status` rows only for itself.
2. **Reach.** A bot may message any bot in `bots` with state `active` and any human in
   `humans`. Paused/planned/quarantined targets → `Refused("reach")`. A human may message any
   bot they are assigned to (hub server enforces via people.yaml `may_chat`).
3. **Conversation cap.** More than 20 messages in an hour in one conversation between bots →
   `Refused("cap")`. Ask depth: a message carries `refs.depth`; an ask made while answering an
   ask at depth 3 → `Refused("depth")`.
4. **Unsolicited.** A bot's `say`/`notice` to a human in a conversation the human did not start:
   max 3 per human per bot per day → `Refused("unsolicited")`. Answers to the human's own
   messages and task/approval items do not count.
5. **Tasks.** Anyone may create for any active owner (a human owner is fine). The owner may
   set `doing|waiting|done|declined` and `note`; it may not `close`. Only the requester (or any
   human) closes. A declined task returns to the requester as a message with the reason. The
   owner may ask one question: the second `ask` on the same task's conversation from the owner
   before any answer → `Refused("one-question")`. A bot requester's `done` task auto-closes
   after 3 days (keeper). Duplicate: same requester+owner+title open → `Refused("duplicate")`.
6. **Approvals.** Only `kind in (send, spend, publish, merge)`. `payload` is the exact thing
   (send: to, cc, subject, body_sha256, mailbox; spend: amount, account, what; publish: url,
   content_sha256; merge: repo, pr). Only humans decide. `consume` succeeds once; a second call
   → `Refused("consumed")`. A duplicate open request (same hash) → `Refused("duplicate")`.
7. **Human-item lint** (messages/tasks addressed to a human, and approvals): title starts with
   a verb, first line is the ask, under 120 words outside quoted drafts, no `owner:`/`status:`
   codes. Failing → `Refused("lint", "<what to fix>")`, not stored.
8. **Refusal counting.** Per bot per UTC day: at 3 → `task_create(keeper, "Review <bot>'s refused
   writes", ..., owner="coo")` once per day. Any `severity=sensitive` (money, send, access, a
   human's inbox) → also a review task for `human:ana`. At 10, or any `severity=escape`
   (a body or payload that references `secrets/`, another bot's repo path, or an external URL
   in a hub-change/access request) → `bots.state=quarantined`, `status_set(quarantined)`, turns
   pause; only a human `status_set(active)` clears it.
9. **Nothing is edited or deleted.** Messages and events are append-only; `expires_at` hides
   notices from the UI.

## 5. The `hub` CLI (bots call it inside a turn; JSON to stdout; exit 2 on Refused)
```
hub whoami
hub say <bot|human> "<text>" [--conversation ID] [--ref task:ID ...]
hub ask <bot> [<bot>...] "<question>" --wait 60          # parallel; prints {bot: answer|timeout|unknown}
hub answer <message-id> "<text>" [--unknown "needs X"]
hub notice <human> "<text>"                              # fyi, not a task
hub task create --owner <bot|human> --title "..." [--body-file f|--body "..."] [--due D] [--parent ID] [--dry-run]
hub task ask <id> "<question>"                           # owner asks requester in the task conversation
hub task update <id> --status doing|waiting|done|declined [--note "..."]
hub task close <id> [--note "..."]                       # requester only
hub task list [--owner me|X] [--requester me] [--status open|doing|waiting|done]
hub task show <id>
hub approval request --kind send|spend|publish|merge --payload-file f.json [--task ID]
hub approval show <id>
hub status set "<focus>" [--state waiting_human|blocked|...] [--task ID]
hub status list [--team marketing]                       # every bot's active row
hub status history <bot> [--since 7d]
hub turns <bot> [--since 24h]
hub inbox                                                 # my undelivered/unread messages and tasks
hub board                                                 # tasks + needs-you across the company (read)
```
`hub ask --wait` blocks by polling hubdb for answers to the message ids it created (250 ms),
returns at the first full set or the timeout. The keeper delivers the asks.

`hub task create ... --dry-run` runs the checks a create would be refused on and writes
nothing: reach (rule 2), the human-item lint (rule 7) when the owner is a person, the
non-empty title for a bot owner, rule 8's escape check, and (local only) the duplicate check.
It prints lines, not JSON: `- <problem>` per problem and exit 1, or
`ok: would create "<title>" for <owner> (<n> words outside quoted drafts)` and exit 0, where
`<n>` is the count the lint itself measures (`hubdb._outside_quotes`). It runs the same hubdb
functions client-side on both paths: against hub.db locally, and with `HUB_API_URL` set (the
runner) without opening a database or sending a request, reading the owner's kind from
`registry/employees.yaml` and `registry/people.yaml`, the files both rosters are seeded from.
Added after a bot spent three turns on a batched human task refused
at 173, 146 and 134 words.

## 6. The keeper
One process. Loops (each a method, each tested with a fake host):
1. **Daemons.** For each runtime in use by a `host: keeper` bot: spawn and supervise. Codex:
   `codex app-server --listen unix://<runtime>/codex.sock`, JSON-RPC 2.0 over the socket
   (`initialize`, then `thread/start|resume`, `turn/start`, `turn/steer`, `turn/interrupt`;
   notifications `turn/started|completed`, `thread/status/changed`, `thread/tokenUsage/updated`,
   `account/rateLimits/updated`, `error`, item notifications for agent messages and command
   output). The JSON schema is at `codex app-server generate-json-schema --out DIR`. Gemini:
   one `gemini -p` stream-json process per turn, with a dedicated per-bot CLI home, API-key auth,
   exact-model verification, and `--resume` for later turns. Grok Build:
   `grok agent stdio` per bot (ACP over stdio: `initialize`, `session/new|load`,
   `session/prompt`, `session/cancel`; updates as `session/update` notifications), second phase.
2. **Threads.** On start: for every `host: keeper` active bot, `resume(thread_id)`; on failure
   `fork`; on failure `start` fresh (record `reset` in status history). Store thread id and a
   new token. Thread settings: cwd = the bot's repo, model/effort from backend bot configuration, approval
   policy never, sandbox danger-full-access (same as today's runs), env = today's `run_env`
   plus `HUB_EMPLOYEE`, `HUB_TOKEN`, `HUB_DIR`.
3. **Mailbox.** Every 250 ms: messages with `delivered_at IS NULL` and `to_actor` a hosted bot.
   If the bot's thread is idle → `turn/start` with the rendered prompt; if running and the
   message is an `ask` with `wait_s` → `turn/steer`; else queue (leave undelivered until idle).
   Set `delivered_at` when the runtime accepted the input. Stream deltas to `deltas`. On
   `turn/completed`: write `turns`, and **the turn's final agent message becomes the reply**:
   for a `chat`/`ask` trigger, a `messages` row (kind `answer` for asks, `say` for chat) to the
   sender in the same conversation, unless the bot already wrote one with `hub answer`/`say`
   in that turn (dedupe by turn id in `refs`). For a task trigger, it becomes the task `note`.
   **A refused reply is not silent**: the keeper writes one line to the person in
   the same conversation, as `keeper`, saying the reply was held and by which rule. **Threads
   rotate**: past `ROTATE_TOKENS` (1M) input on a thread, the next turn starts a fresh one
   (`thread.rotate` event, status reason `rotate`); `turns.tokens_in` is the turn's own input,
   not the thread total. Rule 8's external-URL escape applies to items (tasks, payloads), not
   to messages: a link in an answer is a link.
4. **Prompt rendering.** One template: who is speaking (actor, name, team), the conversation
   subject and last 10 messages, the task (if any) as title/body/status, then the new message
   text verbatim, then one line: "Reply in this conversation; use `hub` for tasks, status and
   asks; `hub inbox` shows anything else waiting." Playbook runs: "Run playbook X" with the
   playbook body, as the dispatcher's schedule Issues do today.
5. **Schedules.** From each hosted bot's `employee.yaml`, synced at start; fire by creating a
   task (owner the bot, requester `keeper`, title the schedule title, body the playbook) and
   delivering it as a message. The dispatcher must skip `host: keeper` bots' schedules.
6. **Tasks.** Wake the owner on `open` (message), wake the requester on `done`/`declined`,
   wake the parent's owner on child close, auto-close bot-requested `done` tasks after 3 days,
   wake on `due`.
7. **Status.** `running` at turn start (focus: the trigger's title), `idle` at finish with
   `last_result` (the first 140 chars of the reply/note), `waiting_human` when the bot opened a
   human item and has nothing else queued, `limited`/`crashed`/`quarantined` as they occur.
   Counts (`open_tasks`, `needs_human`) recomputed on every status write.
8. **Limits.** `account/rateLimits/updated` → `rate_limits`; at `used_percent >= 97` or a
   limit error on a turn: pause turns on that runtime until `resets_at`, mark hosted bots
   `limited`; resume after. Keep the dispatcher's `limits.json` in sync so Issue bots pause too.
9. **Backup.** Daily at 03:30: `PRAGMA wal_checkpoint(TRUNCATE)`, copy to
   `s3://$HUB_BUCKET/hub-db/<YYYY-MM-DD>.sqlite` with the `company-hub` profile; keep 30.
10. **Health.** `runtime/keeper.json` heartbeat each loop (like `status.json`); `scripts/hub.sh
    status` prints it.

## 7. Hub server API (all JSON; identity as today)
```
GET  /api/v2/status                          bot_status rows (+ history?bot=X&since=)
GET  /api/v2/bots                            bot config, people, model, assignment and status
GET  /api/v2/models                          supported model catalog
POST /api/v2/bots/<bot>/owners               {owners[], expected_revision} (owner only)
POST /api/v2/bots/<bot>/model                {model, expected_revision} (owner only; fresh session)
POST /api/v2/bots/<bot>/placement            {runner_id, expected_generation, expected_revision} (owner only)
GET  /api/v2/conversations                   mine (human) ; ?bot=X for a bot's (owner only)
GET  /api/v2/conversations/<id>/messages?since=ISO
POST /api/v2/conversations/<id>/messages     {text} -> message (human say) ; 201
POST /api/v2/chat/<bot>                      {text} -> personal room, or canonical shared room for a shared bot
POST /api/v2/chat/<bot>/new                  archive my active personal room; shared rooms cannot be reset by one member
GET  /api/v2/tico/fleet                      live fleet/tasks/needs snapshot, fenced to person or private Tico attempt
POST /api/v2/voice/live/session              {bot: "coo", resume_handle?, session_id?} -> locked Gemini Live token (docs/live-conversation.md)
POST /api/v2/voice/live/transcript           {bot, session_id, turn_id, conversation_id, user_text?, bot_text?, interrupted} -> room rows, no job
POST /api/v2/voice/live/note                 {bot, session_id, turn_id?, conversation_id, kind: decision|tool, text, name?, label?, outcome?, duration_ms?} -> room row, no job
GET  /api/v2/stream?conversation=<id>        SSE: deltas for the turn answering the newest message, then the message
GET  /api/v2/tasks?owner=&requester=&status=  ; GET /api/v2/tasks/<id> (+events)
POST /api/v2/tasks                           {title, body, owner, due?} as human
POST /api/v2/tasks/<id>                      {status?, note?, close?: true}
GET  /api/v2/needs-you                       tasks owned by me (open/doing/waiting) + approvals pending + declined-to-me
POST /api/v2/approvals/<id>                  {decision, note}
GET  /api/v2/inbox                           notices to me (unexpired), unread first
```
Viewer visibility: a human sees conversations they are in. The company owner can inspect ordinary
and bot-to-bot conversations, but another person's `personal` room deliberately has no normal owner
bypass. A `shared` room is authorized from its current bot-owner membership, not only from a stale
participant list. CPO is shared; Tico is always personal. All other
bot-to-bot conversations are visible to all viewers unless a `private_owners` bot is a
participant. Tasks and status follow the existing `visible_slug` rules.

Bot administration is optimistic and transactional. An explicit nonempty owner list replaces the
bot's broad roster/team fallback. A placement can target only a registered runner identity and
increments both the assignment generation and affected session epochs; a live leased/running
attempt blocks transfer. A model must
come from the server catalog, and a live attempt blocks the change. Successful model changes leave
all tasks and messages in SQLite but increment every affected conversation's `session_epochs` row,
which prevents the local runner from resuming the old provider session.

The send path (`POST /api/send`, `POST /api/recordings/<id>/send`) writes here too: **Auto** is a
hub task on `bot:coo` requested as the sender (`human:<id>`), with the transcript and the notes
in `coo/inbox/<task-id>/` and their `s3://` URIs in the body — the COO routes it and files the
child tasks. A specific bot in the picker gets a hub task when the keeper hosts it and a GitHub
Issue otherwise; the reply carries `task` or `issue` accordingly (`docs/meetings.md`).

## 8. UI (ui/index.html, the old web app, since removed)
- **Chat** with a keeper-hosted bot uses `/api/v2/chat/<bot>` and the SSE stream; the thread
  view lists `messages` (sender chip, time), streams the reply, and shows `hub` actions the bot
  took (task created, approval requested) as small cards. Bots on the dispatcher keep the old
  chat path; the bot page decides by `host`.
- **Status on hover**: the org tree and bot page header show a tooltip from `/api/v2/status`:
  state pill, focus, since, last result, next run, open tasks, needs human. A "History" link
  on the bot page lists `bot_status_history`.
- **Needs you** on Overview: `/api/v2/needs-you` rows (kind, title, first line, age, the tap:
  approve/decline for approvals, done/reopen for tasks, reply for questions) merged with the
  legacy Issue rows until the dispatcher bots migrate; five shown, fold the rest (as today).
- **Tasks tab** on a bot page: hub tasks for keeper bots (open/doing/waiting/done), Issues for
  the rest.
- **Board**: tasks across the company by team, same fold behaviour.

## 9. Bot directory
`registry/employees.yaml` bootstraps an empty database and remains the legacy dispatcher's input.
After initialization the backend `bots` and `bot_config` rows are authoritative for cloud bots;
releases do not reconcile them from YAML. A registered runner receives only the bots assigned to it.
Runner preflight checks the repository, runtime login, model, and backend configuration before work.

**Routines are stored in the database.** Repository files submit validated updates through
runner heartbeats or the server's Git sync worker. Definitions, instruction snapshots,
versions, operational settings and deletion/run history remain in SQLite. The cloud is the
clock and the record; the Mac executes queued tasks from the saved version. Bot pages and
scheduler queries use the same database records, including an empty active set. See
[routine format, synchronization and rollout](../routines.md) for details.

## 10. Bot-side conventions (template AGENT.md, and the three bots' AGENT.md)
"You are always on. Messages arrive as turns. Read the database before asking a bot (`hub
status list`, `hub task list`, `hub board`). Ask with `hub ask`. Report to a person with `hub
task create --owner ana` (a decision or a review), `hub approval request` (a send, spend,
publish, merge) or `hub notice` (fyi). Keep `hub status set` to one factual line as you work.
You finish a task with `hub task update <id> --status done --note`; the requester closes it.
Never close a task you did not request. Never use `gh issue` for work."

## 11. Acceptance: the five scenarios (test plan, `dispatcher/test_scenarios.py` with fake hosts)
S1 human→COO chat answered from the database (< 1 turn) and with a parallel ask to two bots.
S2 bot ask → answer → late answer after timeout wakes the asker.
S3 hub-change as a task to `human:ana` with the proposed YAML in the body; approve = human
   closes with note; (applier is a later step, not in v1).
S4 a bot's `say` to an outside address, an ask at depth 4, a fourth unsolicited notice, and an
   approval with a `secrets/` path in the payload: refused, counted, review tasks created,
   quarantine at `escape`.
S5 a decision task to the owner passes lint, appears in needs-you, a duplicate is refused, a tap
   closes it and wakes the requester; an approval is decided and consumed once.
Plus: keeper recovery (fake host dies mid-turn: message redelivered once, no duplicate reply);
schedules fire as tasks; limits pause turns; status history grows on every change.

## 12. Runtime facts (the hosts must match these)

### Codex app-server (`codex app-server`, stdio; version 0.153.3)
- Transport: **stdio**, newline-delimited JSON-RPC 2.0. `--listen unix://...` opened a socket
  but closed every plain client connection immediately (it is the control socket for `codex
  app-server proxy`); do not use it. One app-server process per keeper, many threads inside.
- Handshake: `{"id":1,"method":"initialize","params":{"clientInfo":{"name":"tico-hub",
  "title":"Tico keeper","version":"0.1"}}}` → result with `userAgent`, `codexHome`; then send
  the `initialized` notification.
- `thread/start` params: `cwd`, `model`, `approvalPolicy: "never"`, `sandbox:
  "danger-full-access"`, optional `config` (object; use it to disable the user's global MCP
  servers for bot threads, they all started in the probe: mobbin, pencil, node_repl, cua_repl,
  posthog-Initech-workflows, codex_apps), optional `developerInstructions`, `baseInstructions`.
  Result: `{"thread": {"id": "<uuid>", "sessionId": ..., ...}}`. Notifications: `thread/started`,
  `mcpServer/startupStatus/updated` (ignore), `thread/status/changed` (`{"type":"active"}` /
  `{"type":"idle"}`).
- `thread/resume` params: `threadId` plus the same settings. `thread/fork` exists.
- `turn/start` params: `threadId`, `input: [{"type":"text","text":"..."}]`, optional `model`,
  `effort`, `cwd`, `sandboxPolicy`, `approvalPolicy`. Result: `{"turn": {"id": "<uuid>",
  "status": "inProgress"}}`. `turn/steer` params: `threadId`, `expectedTurnId`, `input`.
  `turn/interrupt` exists.
- Turn notifications, in order: `turn/started`; `hook/started|completed` (ignore);
  `item/started` (`item.type` `userMessage` | `agentMessage` | command items);
  `item/agentMessage/delta` (`params.delta` text; carries `threadId`, `turnId`, `itemId`);
  `item/completed` with `item.type == "agentMessage"`, `item.text` = the full reply,
  `item.phase == "final_answer"` for the answer; `thread/tokenUsage/updated`
  (`params.tokenUsage.total.{totalTokens,inputTokens,outputTokens}` and `last`);
  `account/rateLimits/updated` (`params.rateLimits.primary.{usedPercent,windowDurationMins,
  resetsAt}` (resetsAt is epoch seconds), `plan_type`); `thread/status/changed` idle;
  `turn/completed` (`params.turn.items`, `status`, `error`).
- A turn answering one short question took ~4 s; 18k input tokens went to system context.
- Errors: `{"id":N,"error":{...}}` on the request, or `{"method":"error", ...}` notifications;
  usage-limit text arrives on the turn error (see the dispatcher's `LIMIT_RE`).

### Grok Build (`grok agent stdio`; version 1.0.25)
- Transport: stdio, newline-delimited JSON-RPC 2.0, the **Agent Client Protocol (ACP)**.
- `initialize` params: `{"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile":
  false, "writeTextFile": false}}}` → result with `agentCapabilities` (`loadSession: true`,
  `sessionCapabilities: {list, resume, close}`).
- `session/new` params: `{"cwd": "<dir>", "mcpServers": []}` → `{"sessionId": "<uuid>",
  "models": {...}}`; `session/load` with `sessionId` resumes; `session/prompt` params:
  `{"sessionId", "prompt": [{"type":"text","text":"..."}]}`; updates arrive as
  `session/update` notifications (`update.sessionUpdate`: `agent_message_chunk` with
  `content.text`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, ...); the prompt
  response returns `{"stopReason": "end_turn"}`. `session/cancel` interrupts. Model and effort:
  `session/set_model` / `session/set_config_option` per ACP, or `-m`/`--reasoning-effort` on the
  `grok agent stdio` command line (verify which the keeper needs). One `grok agent stdio`
  process per bot (the process owns one session at a time); sessions persist under
  `~/.grok/sessions/<urlencoded cwd>/<id>/`.
- `session/request_permission` is a client request, not a turn failure. Because the runner starts
  Grok with `--always-approve`, its ACP client selects `allow_always` (or `allow_once` when that is
  the only allow option); unknown option shapes are cancelled. Other unknown client requests fail
  closed.
- `_x.ai/*` notifications (announcements, models, settings, hooks) are informational.

## Deviations
What the built code does differently from the sections above, and why. Anything not listed here
matches the contract exactly. The same list is at the top of `dispatcher/hubdb.py`.

1. **`conn` first.** §4 writes `say(actor, ...)`. There is no ambient connection, so every
   function is `say(conn, actor, ...)`; `actor` is still the first argument the caller thinks
   about, and §1's `issue_token(conn, slug)` already puts `conn` first. Reads are the same:
   `tasks(conn, owner=...)`, `inbox(conn, actor)`, `status_all(conn)`.
2. **`verify_bot` raises.** It returns the bot row and raises `Refused("identity")` on a bad or
   missing token, so no caller can forget to check a boolean.
3. **Rule 7 lints unsolicited human items.** A bot's `say`/`notice` to a human is linted only
   when rule 4 counts it as unsolicited; a reply to the person's own message, a reply inside
   a conversation the person started, and `answer`, are exempt. Every task with a human owner
   and every approval is linted. Without the carve-out a chat reply longer than 120 words
   would be refused and S1 could not pass;
   the handoffs rule the lint comes from is about *asking* a person for something.
4. **An approval's lint is its payload's fields.** An approval has no title or body, so rule 7
   on an approval means §4 rule 6's "the payload is the exact thing": `send` needs to, cc,
   subject, body_sha256, mailbox; `spend` amount, account, what; `publish` url, content_sha256;
   `merge` repo, pr. A missing one is `Refused("lint", "send needs cc")`.
5. **`status_set(..., state="active")`** is accepted although `active` is not a `bot_status`
   state: it is how rule 8 says a human clears a quarantine. It sets `bots.state=active` and
   leaves the live status `idle`.
6. **Rule names.** §4 names `reach`, `cap`, `depth`, `unsolicited`, `one-question`,
   `duplicate`, `consumed`, `lint`. The rest are `identity` (acting as someone else, a bot
   deciding an approval), `kind` (a bad approval kind, status or state), `close` (closing or
   over-setting a task you did not request), `escape` (rule 8's escape class), `quarantined`
   (a quarantined bot's writes) and `not-found`.
7. **Severity is classified in hubdb, not passed in.** `hubdb.classify()` reads the body or the
   payload: `escape` for a `secrets/` path, another bot's repo path, or an external URL inside a
   hub-change or access request; `sensitive` for money, an outbound send, an access change or a
   human's inbox. Rule 8's review task for `human:ana` is opened for `sensitive` *and* for
   `escape`.
8. **Ask depth is derived, not declared.** A new ask is one deeper than the deepest unanswered
   ask addressed to the asker, which is exactly §4's "an ask made while answering an ask at
   depth 3". A declared `refs.depth` is used when it is deeper, so a bot cannot reset the
   count; repeat asks to the same bot stay at depth 1, because depth counts the chain.
9. **`sync_registry` never lowers a state.** A bot the registry calls `active` that hubdb has
   `quarantined` stays quarantined; only a human clears it. Tokens, thread ids and
   `last_turn_at` are never overwritten by a sync. Nothing is ever deleted.
10. **Extra read helpers** beyond §4's list: `answers_to(conn, ids)` (what `hub ask --wait`
    polls), `undelivered(conn, to_actor)` (what the keeper's mailbox loop reads), `notices`,
    `bot`, `bots`, `human`, `humans`, `task`, `task_history`, `approval`, `approvals`,
    `message`, `conversation`, `schedules`, `rate_limits`, `deltas`, `refusals_for`, `events`.
    Extra writes: `mark_delivered`, `mark_read`, `task_ask` (rule 5's one question),
    `status_result`, `schedule_fired`, `prune_deltas`, `quarantine`, `auto_close_done(conn, at)`
    (rule 5's three-day auto-close, which the keeper calls on a timer).
11. **The CLI seeds an empty database** from `registry/employees.yaml` and
    `registry/people.yaml` on first use, so a fresh `$HUB_DB` is addressable by slug before the
    keeper has run. That import is bootstrap-only: subsequent bot definition reads and writes use
    the `bots` and `bot_config` tables, and ordinary releases never reconcile those rows from YAML.
    Owners create bots with `POST /api/v2/bots` and update their core definition with
    `POST /api/v2/bots/{bot}/definition`; neither operation needs an application deploy. A separate
    `assignments` row selects the registered runner, and model/effort or machine changes use the
    checkpointed transition endpoint. `hub status set` takes `--bot <slug>` so a person can write a bot's status;
    a bot still only writes its own. `hub ask` exits 2 only when every target was refused, and
    otherwise reports the refusal beside the answers. `hub approval request` also accepts
    `--payload '<json>'` inline.

## 13. Deviations (the hub server and the UI)

What the built code does that §7 or §8 does not literally say. Everything else matches.

- **hubdb takes the connection first.** §4 writes `say(actor, ...)`; the module is
  `say(conn, actor, ...)`, so the server opens one `hubdb.connect()` per request (and holds it for
  the length of a stream) and closes it after. No shared handle, no state in the server.
- **Every response is an object, never a bare list.** `{"bots": []}`, `{"conversations": []}`,
  `{"conversation": ..., "messages": []}`, `{"tasks": []}`, `{"task": ..., "events": []}`,
  `{"items": []}`, `{"notices": []}`, and `{"message"|"task"|"approval": row}` from a write. A
  create answers 201. `GET /api/v2/status?bot=X&since=` answers `{bot, status, history}`; `since`
  takes `7d`, `24h`, `90m` or an ISO stamp.
- **`POST /api/v2/inbox/read`** was added: `{ids: [...]}` marks those notices read
  (`hubdb.mark_read`, only for messages addressed to the caller). §7 listed the read but not the
  write, and "mark read on open" needs one.
- **Needs-you kinds are read from the ask.** The schema has no `tasks.kind` (the plan's §11 asked
  for one; §2 does not have it), so the chip comes from the title's opening verb — decide/choose →
  decision, review/read/check → review, approve → approval, anything else → task — plus
  `declined` for a task that came back to me. If `tasks.kind` is ever added, this reads it instead.
- **Approvals in needs-you are filtered to the person.** `hubdb.needs_you` returns every pending
  approval (only a human decides one); the server shows a person the ones whose request message
  was addressed to them, so a viewer never sees the owner's.
- **The stream answers immediately when the reply is already written.** Opening
  `/api/v2/stream?conversation=<id>` when the newest message is not yours sends it as the
  `message` event and closes, so a reload after a turn landed shows the answer instead of waiting
  ten minutes. Otherwise it is §7: deltas every 250 ms, keep-alive comments every 15 s, the final
  `message`, then close. It finds the turn with one `SELECT ... FROM turns WHERE message_id=?`,
  the one read hubdb has no helper for (it reads turns by bot).
- **`/api/employees` rows carry `host`** (`dispatcher` unless the registry says otherwise). The UI
  branches on it for chat, tasks and status; without it the page cannot tell the two worlds apart.
- **`?bot=` on `/api/v2/conversations` is owner-only**, as §7 says, so the chat page finds its own
  conversation with a bot from the plain list (kind `chat`, exactly the two participants) rather
  than asking for the bot's.
- **A person the roster does not know may read but not write.** Cloudflare can allow an address
  `registry/people.yaml` has never heard of; it gets a viewer's reads and a 403 on any v2 write,
  which is the same rule bot chat already had.
- **The bot page's + New tab writes a hub task** for a keeper bot (`POST /api/v2/tasks`) instead of
  a GitHub Issue, with no file attachments: hub.db has no attachment story yet.

## 14. Deviations (the keeper and the runtime hosts)

What `dispatcher/keeper.py` and `dispatcher/hosts/*` do that §6 or §12 does not literally say.
Everything else matches, and the two runtime facts below were probed on the real CLIs.

- **Per-thread environment works, so one app-server serves every Codex bot.** `thread/start`'s
  `config` is deep-merged over `~/.codex/config.toml`, and
  `shell_environment_policy = {inherit: "core", set: {…}}` puts the employee's `run_env` (plus
  `HUB_EMPLOYEE`, `HUB_TOKEN`, `HUB_DIR`, `HUB_DB`) into that thread's shell and no other's —
  verified by a turn that echoed them back. A token can therefore be rotated by resuming the
  thread, without restarting anything. `CodexHost(env_mode="process")` (or `CODEX_ENV_MODE=
  process`) is the fallback: one app-server per bot, whose own environment is the bot's.
- **The user's global MCP servers cannot be cleared, only broken.** `config: {"mcp_servers": {}}`
  and `-c mcp_servers={}` both leave the config.toml servers running (a merge is a merge; a
  `null` is a type error). So `hosts/codex.py` overrides each configured server for bot threads:
  a stdio server's `command` becomes `/usr/bin/true`, a remote server's `startup_timeout_ms`
  becomes 1, and each fails at startup with no tools. Codex's own `cua_repl` and `codex_apps`
  are not in `mcp_servers` and stay; their startup errors land in `runtime/keeper-codex.log`.
- **Grok's model and effort are `grok agent` options, not `stdio` options**:
  `grok agent -m grok-4.6 --reasoning-effort high --always-approve --no-leader stdio` (verified
  with a real prompt: a reply in 2.4 s and a session directory under `~/.grok/sessions/`).
  ACP has no mid-turn input, so `GrokHost.steer` raises and a waited ask to a busy Grok bot
  queues behind the turn instead of steering it; ACP has no turn id either, so the keeper's own
  uuid4 is the turn id, and `fork_thread` is a new session in the same cwd.
- **Only a `say` or an `ask` earns an automatic reply.** §6.3 says a chat or ask trigger's final
  agent message becomes a message row and a task trigger's becomes the task note. A turn woken
  by an `answer`, a `notice` or a task wake writes nothing automatically — otherwise every
  answer answers an answer and two bots talk until rule 3's cap stops them. The bot can still
  say something with `hub say`. `turns.trigger` is `chat`, `ask`, `task` or `message`.
- **Redelivery is `delivered_at = NULL`, once.** A runtime that dies mid-turn leaves the turn
  `exit: crashed`; the keeper clears the message's `delivered_at` so the mailbox hands it over
  again, and remembers the id so a second crash does not deliver it a third time.
- **The backend bot row is authoritative for cloud hosting.** A `bots.host = 'keeper'` row and its
  `bot_config` record define a cloud bot; no registry commit is required. `assignments` chooses its
  current registered runner. The legacy local keeper writes `bots.thread_id` itself (hubdb has no
  setter; only that keeper ever writes it).
- **hubdb already wakes tasks.** `task_create`, `task_update` and `task_close` write the wake
  notices §6.6 asks for, so the keeper's task loop is only the due dates and
  `auto_close_done`, and it runs every 30 s rather than every tick.
- **The reply dedupe is "did the bot speak in this conversation during this turn".**
  `refs.turn_id` is honoured when the `hub` CLI sets it, but any message from the bot in the
  same conversation after the turn started counts, so a `hub say` is never doubled.
- **`bot_status_history` reasons.** §6.2's `resume`, `fork` and `reset` are the recovery
  reasons; a bot's first-ever thread is recorded as `start`, and a resume that returns the same
  thread id writes no history row at all (nothing changed).

## Context chat

Tasks, individual task details and documents have a chat button. The server
builds the page snapshot from records visible to the authenticated person and sends it to COO
with their name, email, page/filter, selected item and comment. The conversation is persisted
and replies appear inline. A selected task grants COO task-update/close authority for 24 hours,
recorded against the human's contextual message; it does not grant access to unrelated tasks.
COO interprets the comment as the request, not the untrusted task body. Reassignments wake the
new owner. The owner's `primary_for: ["*"]` covers every current and future bot alongside other owners.
