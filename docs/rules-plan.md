# Rules + General Counsel plan

**Status:** final spec Oct 30, 2026 (v4: Chris's post-v3 decisions, then a second Fable 5.1 and Codex gpt-6-astra
pass). Plan only, no code. Read against ticoteam/tico `origin/main` c15ff9b04. Mockups: canvas "Tico Rules spec" (boards 1–11). Built in Python in Tico core; no Rust (decided Oct 30, same as Finance).

## The idea in one line

A rule is one sentence, **Internal** (how teammates, bots and people, treat each other) or **External** (what someone
outside requires of us). It says **Who** follows it; an internal rule can also say **With** whom. From then on the bots
it applies to know it. Counsel reads contracts and suggests external rules. A rule can have a **due date** with a status (Upcoming, Due soon, Overdue, Satisfied); a routine that
opens a task before it is optional.

## Why

| Need | What exists | Gap |
|---|---|---|
| Outside obligations (vendor terms, customer contracts, laws, HOA) | `policies/*.md`, bot instructions, prose | No record, no source, no "applies to", no review |
| Reading a contract for what binds us | Nothing | The core of this plan |
| Legal dates | Tasks have `due`; the scheduler runs daily jobs (`backend/scheduler.py`) | Nothing says "file X by Y every year, start 2 weeks before" |
| Who may message a bot, sending outside, limits | `bot_access.py` write audience (Open by default); `outbound_send` toggle in Settings › People (`ui/app/settings-access.js`); `usage_limits.py`, `turn_budgets.py`, `task_caps.py` | All exist and are editable, but spread across Settings; there's no single view per bot |
| Checks on outbound calls | Optional approvals (`hub approval request --kind send\|spend\|publish\|merge`, payload-bound, consumed once); the mail connector's per-send switch (`connectors/mail/policy.py`) | Nothing ties an outside obligation to the action it governs |

## The model

**Rules** are the one new thing, in two kinds. Internal comes first: it's what Chris will start adding.
- **Internal:** between teammates. "Never tell anyone what color you like" (Who: Everyone). "Ask Chris before
  assigning him a task" (Who: all bots, With: Chris). "Don't ask Bookkeeper to pay anything" (Who: Marketing, With:
  Bookkeeper).
- **External:** someone outside requires it, with a **From** (Twilio, Acme Corp, City of Austin, FTC) of kind
  Vendor, Customer, Government or Other.
- Each is one sentence (up to 1000 characters) plus an optional description.
- **Who** follows it: Everyone (every teammate), all bots, departments, bots or people. Only owners and admins can
  add or edit an Everyone rule.
- **With** (internal, optional): the teammates it's about: departments, bots or people. Empty means any output.
- Status is suggested, active or retired.
- **Settings show as internal rules** on a bot's Rules tab: who can message it, sends outside, spend and turn limits,
  written as sentences ("Only Marketing and Chris can message Marketing Bot"). They aren't stored as rules. Each one
  opens the existing setting, with the same API and permissions, so the tab explains everything that governs the bot.
  Tico's Team rules (company-wide switches in `team_rules.py`) stay in Settings.

  | Sentence | Source | Who edits |
  |---|---|---|
  | "Only Marketing and Chris can message Marketing Bot" (the write audience; see/read stay in Settings) | `bot_access.py` | the bot's managers |
  | "Marketing Bot can't send outside Tico" / "…can send mail" | Settings › People `outbound_send`; `mail_settings.py` | a person who manages the bot (never a bot, BotOps or the Assistant, for mail) |
  | "Spends up to $X a month" or "Follows the company limit" | `usage_limits.py` (may be a company or group pool) | as today |
  | "Up to N turns a day" or "Team default: N" | `turn_budgets.py` | as today |
  | "Up to N tasks a day to Chris" (one per cap row) | `task_caps.py` | as today |
  | "At most 10 unasked messages to a person a day", "Asks go 3 deep at most", "20 bot-to-bot messages an hour in one chat" | `hubdb.py` fixed limits | nobody (no edit link) |

  Each shows in `hub rules list --mine` as a read-only `setting:` line, so a bot learns its limits from one command.

How a rule is used:
1. **Checked** (always, §3): Decide checks a bot's outputs against the rules that apply, and holds or warns. Rules
   aren't injected into prompts. Every teammate can read every rule, and bots are told where to look.
2. **Due date** (optional): a date, a repeat, an owner and a status. A routine that opens a task N days before is
   optional on top (§4).
3. **Enforced on an API call** (optional, later, §7): a vendor wrapper.

**Naming** (UI → code):

| UI | Code |
|---|---|
| Rules (Internal, External) | `org_rules` with `kind internal\|external` |
| Who / With | `who`, `with` |
| From | `rule_sources` |
| Suggested | status `suggested` |
| Due | `rule_due_dates` + `rule_due_status`; routine optional |
| Decide | `judge.py`'s default engine; not "Jev" |

"Rules" alone in code already means wake rules (`backend/rules.py`), so new code never uses the bare word. The CLI is
`hub rules …`, which is free today. There is no "Sources" tab name clash in the UI, because Docs/Meetings "Add source"
lives on other pages; on this page the button is **+ Add document**.

## Screens

1. **Bot › Rules tab** (board 1): **Internal** (written rules where the bot is in Who, then its settings as
   sentences, each opening the setting), then **External**.
2. **Rules › Internal** (board 2): list with Rule | Who | With | Due; filters Who, With, and "Settings too" (off).
   Clicking a rule opens a side panel: rule, description, Who, With, If broken (Block / Escalate / Warn), due date; Retire / Save.
3. **Blocked** (board 3): what a blocked bot gets back and does next (Block, the default), and an Escalate review task
   for the bot's manager (Keep blocked / Send anyway / Edit rule). **Nav:** Rules is a top-level item under Automations and Library, collapsed by default.
4. **Rules › External** (board 4): the page is **Rules** with tabs **Internal · External · Due · Suggested**. External
   is a flat list with filters for From, Who and Due. Buttons: **+ Add document**, **+ Add rule**. The From filter's list replaces a Sources tab. Clicking a
   rule opens a side panel: edit the rule, a description, Who (company, departments or bots; + add), From;
   see its due date and the cited quote; Retire / Save. No "Counsel is reading" banner.
5. **Add document** (board 5): one file or link, a short description ("We just closed our deal with Twilio"), and
   From (or a new one plus a kind). It starts a Counsel review. (Finding public terms on its own is Later #1.)
6. **Suggested** (board 6): grouped by From ("Twilio"). Each row is the sentence, Who, and Skip / Add.
   **Add all** adds the group with the scopes shown. The quote is stored and shows in the rule's side panel, not here.
7. **Add rule** (board 7): Internal or External first; Internal asks Who and With, External asks Who and From.
8. **Due** (board 8), for both kinds: columns Due | Rule | Repeats | Routine | Owner | Status. Repeats is the frequency; Routine is
   optional ("30 days before" or —). Status is Overdue / Due soon / Upcoming / Satisfied. Clicking a row opens a
   side panel: Due, Repeats, Owner, Routine, the status with **Mark satisfied**, and a history of past due dates
   (task closed, or marked by a person). **+ Add due date** picks a rule, then the same fields.
9. **Counsel**: no page of its own (§5). Board 9 is the Ask panel conversation.

## Shape

### 1. Records

The shape follows `strategy_bets` (`backend/strategy.py`): a statement, cites, proposed/active/retired, and its own
events.

```
rule_sources(id, name, kind vendor|customer|government|other, created_by, created_at)

org_rules(id, kind internal|external, text, description?, source_id (external only),
          cite {file_id+version, section, quote} (external only),
          who  {everyone: bool, all_bots: bool, groups: [], bots: [], people: []},
          with {groups: [], bots: [], people: []} (internal only, optional; empty = any output),
          if_broken block|escalate|warn (default block), reviewer? (a person; else person_above),
               status suggested|active|retired, suggested_by, decided_by, decided_at, via ui|cli|mcp|assistant,
               version, created_at, updated_at)
```

```
rule_due_dates(id, rule_id, first_due, repeats once|weekly|monthly|quarterly|yearly, timezone, owner, routine_id?,
               ended_on?, retired_at?, created_by, created_at)
rule_due_status(due_date_id, due_on, satisfied_by, via routine|person, task_id?, at)   unique (due_date_id, due_on)
```

- `all_bots` is a live selector, so a bot made later is covered; it isn't expanded into slugs. `if_broken`, `reviewer`
  and every other field are part of the rule's `version`.
- **Due dates are their own rows**, so one rule can have several (quarterly returns plus an annual report).
- **Status is derived:** Satisfied if a `rule_due_status` row exists for that `due_on`; else Overdue after the end of
  `due_on` in the due date's `timezone` (default the linked routine's, else `updates.ZONE`); Due soon inside the
  routine's window (or 30 days with no routine); else Upcoming. A Satisfied "once" stays Satisfied. No proof file
  (Later #12).
- **Repeats** clamp to month end (monthly from the 31st, yearly from Feb 29).
- **Editing** `first_due` or `repeats` sets `ended_on` and starts a new row, so history stays true. **Remove** sets
  `retired_at`; rows are never deleted.
- **Satisfied** is written when the routine's task for that `due_on` is completed (marked done by its owner, or
  closed by a person), or by **Mark satisfied** (the owner, or
  anyone who can edit the rule; it asks which `due_on` when more than one is open). Every write goes to audit.
- **Cites always point at a Files version** (`backend/files.py` keeps immutable version bytes). An upload is one
  already; a fetched link's exact text is saved as a Files version at the first read. Page monitors
  (`market_watch.py`) only detect changes: they keep one mutable `last_text`, not a history, and a change saves a new
  version for review. No new snapshot store.
- Every change writes `audit_log` (`org_rule:<id>`) with actor and `via`.

### 2. Counsel reads a document and suggests rules

Adding a document opens a task for Counsel (a hidden helper, §5) with its own budget.

1. **Read.**
   - Upload: the Files version.
   - Link: fetched through `clients/doc_fetch.py` and saved as a Files version (the cite target).
   - "Find public terms": search, then fetch the vendor's ToS, AUP and privacy pages.

   Text is fenced as untrusted data in Counsel's prompt, and Counsel has no outbound tools. That's the real
   control. A screening "door" over a 60-page contract would mostly be theater, since the model layer reads 20k
   characters and fails open.
2. **Extract** only obligations that bind *us* and that a bot or person could break. Each one gets:
   - a sentence;
   - the section and quote;
   - a suggested Who: the departments of bots granted that vendor's credential (`backend/credentials.py`),
     else ask;
   - if the text has a date, the date stays in the sentence ("…every year by Nov 15"). Counsel doesn't suggest a
     routine (Later #13); a person sets one on the Due tab.

   No cap on the count: each one that binds us gets a suggestion. A document with none gets a one-line "read, nothing
   else for us" note. A resumed run skips what a rule for the same version already states, whatever its status.
3. **Suggest.** Rows go in as `suggested`. A person taps Add (or Add all on screen).
4. **Re-read** (phase 2). Register each fetched page as a page monitor (`market_watch` path: daily read, hash, diff,
   first read as baseline). A real change files a "changed" suggestion against the rule. A failed or partial read
   never retires anything.

**Eval before calling it good:** about 20 real documents (Twilio, Meta, Gusto, a customer MSA, an HOA CC&R, a city
STR ordinance), each with a hand-made list of expected obligations. Score missed obligations, wrong scope, and
quotes that don't appear in the source text.

### 3. Checked by Decide on outputs (not in the prompt)

Rules work without relying on a bot's instructions. Decide checks what a bot sends, and the bot can read every rule
ahead of time so it doesn't try.

- **Shape: `ask_ingest`'s preflight/apply** (`backend/ask_ingest.py`). One Decide call outside the write's transaction
  (`preflight`), plain code deciding inside it (`apply`), a deadline per level (Low 5 s, Medium 10 s, High 15 s) that
  the engine call itself stops at (`J.within`), one `judge.call` row per call, fail open. It
  borrows screening's verdict cache and budget helpers (`backend/screening.py`). Screening's deferred thread can't stop
  an output before delivery, so it isn't the model. No engine call inside `store.transaction()`.
  - **Bounded:** at most `TICO_RULE_CHECK_CONCURRENCY` checks (default 8) run at once; a write that finds them all
    busy goes through unchecked at once (reason `busy`), never waiting. After 5 timeouts or errors in a row, checks
    stop for 60 s (reason `breaker`); the first one after that is a probe. Both are per process.
  - No active rule at all: one `SELECT ... LIMIT 1` and nothing else, no roster read and no engine.
- **One gate helper** judges every bot-authored string a teammate or the public will read. The HTTP routes, the hubdb
  writers and the automation writers all call it. A write is a bot's when the actor is a bot, or when `task_actor` is
  a bot or `via` is `botops` (BotOps writes as the person; it's still checked as BotOps). Only a person's own typed
  writes, and their Assistant's, are exempt.
- **Where it hooks (phase 1):**
  - a bot's reply at run completion. `POST /api/v2/attempts/{aid}/complete` preflights the body *before* `mutate`
    (as `POST /api/v2/messages` does for ask ingest), and `execution.complete` applies the verdict next to
    `privacy.require_destination`. `complete` runs inside the write lock, so the Decide call must not.
  - **Live text:** while a run whose bot has matching rules is going, `conversation_execution` shows "Working" and
    tool steps, not the reply text, until the verdict says deliver. On Block or Escalate the text lives only on the
    `org_rule_holds` row; `final_text`, `result`, the run log and the bot session show the one-line note. The
    privacy withhold keeps text on the attempt; this doesn't.
  - `hub message send` (`POST /api/v2/messages`, and `POST /api/v2/chat/{bot}` through the same check and write),
    task comments, task create and owner change: the routes `ask_ingest` already gates;
  - task questions (`POST /api/v2/task-questions`): title, body, question and why as one output to the person asked;
    on Block no task, fold, comment or note;
  - updates (`POST /api/v2/updates`), slide text included, for the whole company;
  - task updates (`POST /api/v2/tasks/{tid}`: title, body, note, `public_title`, `public_summary`; on Block they stay
    unchanged; public text, or making a task public, adds the whole company and judges the public text), the owner's
    ask (`POST /api/v2/tasks/{tid}/ask`), and bulk changes' `note`;
  - notes to a bot (`hub_note_create`), which ride its next prompt;
  - automation steps that write as a bot or for one: `_create_task`, `_on_task`, `_note`, `_wake_bot` (their text is
    judged against the automation's owning bot), `_post_update`; Slack `send_reply`;
  - comment edits (`POST /api/v2/tasks/{tid}/comments/{mid}`, `hub_task_comment_edit`): the new text is judged in the
    comment's context, and on Block the old comment stays;
  - bulk owner changes (`POST /api/v2/task-bulk`): one verdict per task, and held tasks come back as skipped with the
    reason;
  - mail: a new `POST /api/v2/bots/{bot}/mail-check` the connector calls with the draft before `check_send` (today the
    connector only reads the on/off switch). It's one try within 20 s (the longest deadline plus a margin). A person's
    mail, their Assistant's included, answers `not_checked`. Another bot's token is refused; a refusal or a server
    error is a `rules.unchecked` event (reason `forbidden` / `error`), and the connector sends on a failed check but
    logs it (`rule-check-failed`) and says so in its answer. Each check carries a fresh UUID as its
    Idempotency-Key, and the route keeps it with any `rules.unchecked` it writes for that check (`org_rule_misses`).
    A mail sent after any failed check (any status, or no answer) is kept in the connector's state by that key alone
    (at most 100 per bot, oldest dropped) and reported once Tico answers again: `POST
    /api/v2/bots/{bot}/mail-check/misses {ids}` records one `rules.unchecked` (reason `connector`) per id not yet
    kept for that bot, so a check the route counted, or a repeat, counts nothing more. Whichever lands first, the
    check or the report, claims the id and counts it; a report it doesn't count isn't kept. The connector makes an id
    reportable only in the commit that records Gmail's confirmed send. It counts only when a rule
    in force names the bot with With empty (an id can't say where the mail went), up to 200 a bot a UTC day. The
    connector forgets the ids after its 2xx; a release without the route (404/405) leaves them queued. BotOps writing
    mail for a person finds its holds and approvals under BotOps, the author they were held as.

  Not phase 1: public roadmap comments (Later #16). Send/publish approvals aren't check points:
  they're optional, so they'd only catch sends a bot chose to ask about.
- **Which rules (matching):**
  - Who: active rules for Everyone, the author bot's slug, or its group via `people.team_of` (nested groups included,
    its group at the time of the write). Built-in bots and helpers are in no group, so "Who: Marketing" never means the
    Assistant, while "Who: all bots" does.
  - With: empty, or *any* recipient of the write is in With: a comment's audience (owner, requester, @mentions), a
    conversation's participants, or a completion reply's resolved target.
  - Mail: To and Cc addresses that match roster people are recipients for With; other addresses match only rules
    with With empty.
  - No matching rule means no Decide call, which is most outputs.
  - A person's own writes are never checked, including what their Assistant writes as them (`refs.via`).
- **What's judged:** the whole output, short or long. Screening's 200-character minimum, 20k cut and `free_text`
  extraction are injection heuristics and aren't reused ("My favorite color is blue" must be checked). One output is
  one call. An output that doesn't fit Decide's state limit (120k) is held, not passed; it isn't on the fail-open
  list. When every rule it meets is Warn, it's delivered with a line saying it was too long to check, and counted
  unchecked (reason `too_large`).
- **The question** (`questions/rule-check.json`): state `{rules, operation, output, from, to, context}`; one `noul` per matched
  rule ("does this output break it"), capped per call; thresholds in the set file, as screening does. Level **Medium**
  (Claude Sonnet 5.5) until the rule-check eval shows Low (Jev) is good enough: Jev is calibrated per question set and
  hasn't seen arbitrary rules. Roughly half a cent per 2k-token check. Past the deadline it fails open.
- **If broken**, set per rule. In every mode the bot that sent it is told it broke a rule, and which one.
  - **Block** (default): not delivered. The write returns 200 with a `held` result, never a refusal, so it counts
    toward no quarantine (`hubdb.refuse` would). Nobody is pinged. The bot gets the rule and "Think it's wrong? Ask
    <manager>: `hub rules appeal <hold-id>`", naming the same person Escalate would. No override toggle.
  - **Escalate** (for serious rules): not delivered, plus one review task (Keep blocked / Send anyway / Edit rule).
  - **Warn** (optional): delivered with a warning line on the recipient's copy.
  - **On a completion reply**, the person waiting sees one line in the thread, without the text: "Marketing Bot's
    reply was held: it broke the rule 'X'." Same as the privacy withhold, so they don't re-ask.
  - **Repeats:** the 3rd block of the same rule by the same bot in a day turns into Escalate for the rest of that day:
    one review task, not one per block. Cache hits count as blocks.
- **Reviewer and the held output:**
  - The reviewer is the rule's reviewer, else `report_managers.person_above` (first human up the chain), else the
    bot's operator, else the default person (screening's `person_for` order), else the team owner, else an admin;
    someone who left or whose sign-in is off is skipped. Never a bot. With no person at all, an Escalate stays a Block.
    The owner or an admin can read, keep or send any hold whose reviewer is missing or gone, and the review task's
    hold list shows it to them.
  - The held output lives in its own `org_rule_holds` row (operation, payload, author, destinations, rules, verdict),
    readable only by the author bot and the reviewer. It is never a message or task row: an undelivered message is
    what inboxes and the mailbox loop read. Send anyway submits the hold's one-shot server nonce: the normal write
    runs once, as the bot, with `bot_access` and ask ingest, but Decide isn't asked again for that exact payload. A
    changed payload or a newer rule version is a new check. A held automation step whose automation now acts as
    someone else (a new owner, a group's new lead) is refused with 409 `changed`. Keep blocked leaves it. The review
    task body is a fixed line plus the hold id; nothing from the output is copied in.
  - **Appeal:** `hub rules appeal <hold-id> "<why>"` sends the hold to the reviewer as a review task. `why` is stored
    on the hold and shown only to the reviewer, never in a task body or a prompt. It isn't checked by rules, never delivers the payload, and isn't an override. That's the
    "ask your manager" route, so a rule can't block it. Nobody hears anything unless the bot appeals.
  - Edit rule opens the side panel under the normal permission check.
  - Org-rule refs are `refs.org_rule`; `refs.rule` already means the wake rule.
  - A held output never shows as a task hold or on the Blocked tab.
- **Rollout:** live with Block by default when it ships; no observe-only week (Chris, Oct 30). A team setting turns
  checks Off. The rule-check eval runs before merge and picks the level.
- **Fails open:** no engine, the team's spend cap, the day's call cap, the deadline, an error or a malformed answer let
  the output through, with a `rules.unchecked` event. Each block, escalate or warn is one `rules.blocked` /
  `rules.escalated` / `rules.warned` event. Never the text.
  - It's visible: the Rules page header reads "Checks on · 212 today · 4 unchecked · $31 left", and a high unchecked share in a day
    is a Health finding.
  - Model text: "Block holds what Decide catches. When Decide is off or over budget, the output goes through and is
    marked unchecked."
- **Cost and cache:** one call per output with matching rules. Rule checks have their own monthly budget
  (`TICO_RULES_MONTHLY_USD`, new, separate from the shared $50 `decisions_monthly_usd`, so rules can't starve
  screening or ask ingest, nor the reverse) and the keeper pays. `hub rules check` has a per-bot daily cap; hitting
  it stops that bot's drafts only. The Rules header shows dollars left, and says so when it's gone.
  Verdicts are cached by a digest of the full check input (operation, author bot, resolved recipients, exact content
  or change, the object's version) plus the matched rule ids and versions, the question-set version and the Decide
  level. An unchanged retry is free; the same words sent as a message and as a task assignment are two checks.
- **With `ask_ingest`:** where both apply (a task filed on a person), the rule check runs first, and a block skips
  ask ingest.
- **Bots read rules; they're not injected.** Every teammate, bots included, can read every rule (`hub rules list`,
  `hub_rules_list`). Bot instructions get one fixed line: "Rules apply to you. Read them: `hub rules list --mine`.
  Check a draft: `hub rules check`." That's a policy text change, not a runner contract bump.
  - `--mine` = Everyone, the bot's group (via `team_of`), its slug, and rules where it's in With, plus its settings as
    read-only `setting:` lines.
  - `hub rules check "<draft>" --to <teammate> [--as message|comment|task|assign]` runs the same check on the same
    input and writes the verdict cache, so sending that exact draft costs no second call.
- Settings shown as sentences are enforced by their own code, not Decide.

### 4. Routines with a due date (general, not legal-only)

Tico has no "deadline" idea today. A routine runs at a time. An automation's `create_task` takes a title, body and
owner, with no due date (`backend/automation_actions.py` `_create_task`). Yet "due every Jan 31, start two weeks
before" fits much more than legal: payroll, renewals, board reports, a customer's yearly SOC 2 letter. So this is a
**general routine feature** ("When: Before a due date", next to On a schedule and When something happens), and
legal rules are one user of it.

- **Fields:**
  - **Due**: a first date plus a repeat (yearly, quarterly, monthly or once). No RRULE.
  - **Open task**: N days before (default 14).
  - **Owner**: the caller, a teammate in the rule's Who, or someone the caller manages; the team owner and admins may
    set anyone. An owner change meets the same check; an existing due date whose owner no longer passes keeps its
    routine.
  - **For rule** (optional): links it to an external rule.
- It shows on the Automations page with other routines, and on the owner's Tasks with its due date.
- Each occurrence is a normal task with `due` set and `refs {routine_id, due_on}`. That pair is the unique key: a
  repeated or concurrent tick does nothing, and a missed day catches up.
- Occurrences are independent. An overdue one stays open, shows red, and never blocks the next.
- Closing is a normal task close; no proof is required.
- **What it adds to Tico:**
  - a `due` trigger on routines (fires at `due - open_days`);
  - `due` on the `create_task` step;
  - the occurrence key;
  - an optional `rule_due_id`: the routine then reads its date and repeat from that row.

  No new scheduler. Created like any routine (Automations page, `hub automation create`) or from the Due tab (the
  Routine field in a due date's side panel).
- **Linked to a rule's due date:**
  - The routine is optional; a due date with no routine still shows status and can be marked satisfied.
  - Completing the routine's task writes `rule_due_status` for that `due_on`. A close by the keeper (ask expiry,
    cleanup) or a cancel never does, and due-date tasks are exempt from the ask-expiry sweep
    (`ask_deadlines.handed_to_people`). Reopening the task removes the status; audit keeps the history.
  - The routine skips an occurrence already marked Satisfied by hand.
  - The routine's task is own work (the automation acts as the due date's owner), and own work is never capped
    (`task_caps.py`), so caps don't apply.
  - Retiring the rule asks whether to remove its due dates and routines.
- The weekly summary (overdue, due in 14 days, missing an owner) is a Counsel task linked from the Due tab.

### 5. General Counsel

- Counsel is a hidden helper next to BotOps, the Strategy Bot and the Librarian (`backend/assistant.py`
  `HELPERS`), because of One Assistant (Oct 29): people have one chat.
- What people see:
  - "Can I text this list?" or "We signed Gusto, contract's in Files" goes to the Ask panel, and the Assistant hands
    it to Counsel.
  - Counsel's reviews show on the Rules › External page.
  - Its questions land in the person's existing Needs-you queue.
- **Jobs:** review documents, answer "can I?" with cited rules, the weekly due summary, and (phase 2) re-read
  changes.
- **Adding rules from chat:** when a person says "add a rule: only Chris runs payroll" in their own Assistant chat,
  the rule is written as them, `via: assistant`. That's how org changes already work (`instructed_org`,
  `on_behalf_of`, Team rule `assistant_direct`). Rules Counsel finds in documents still need a tap.
- **Bots** (Counsel included) can read and suggest; they can't accept rules on their own.
- **Swappable** (optional): Settings picks which bot sits behind the Counsel helper. Nothing else is swappable
  today, so this waits until someone asks.
- Answers carry one short line: "Not legal advice. For real exposure, ask a lawyer."

### 6. CLI + MCP

```
hub rules list [--internal | --external] [--bot X | --mine | --from F | --with W]
hub rules due [--next 90d]                                                     # due dates + status
hub rules due add <rule> --on 2027-01-31 --repeats yearly --owner bookkeeper [--task-days 30]
hub rules due satisfied <rule|due-id> [--on 2027-01-31]
hub rules edit <id> [--text ...] [--description ...] [--who ...] [--with ...] [--if-broken ...] [--reviewer ...]
hub rules add "<sentence>" --who all-bots --with chris [--if-broken escalate --reviewer chris]   # internal
hub rules add "<sentence>" --from twilio --who marketing[,bot:x]               # external
hub rules read <file-id|url> --from twilio [--kind vendor] [--note "..."]     # starts a Counsel review
hub rules suggested [--from F]   hub rules accept <id> [--who ...]   hub rules skip <id>   hub rules retire <id>
hub rules check "<draft>" --to <teammate> [--as message|comment|task|assign]  # same check Decide runs on send
hub rules appeal <hold-id> "<why>"                                             # a blocked bot asks its reviewer
hub rules ask "<question>"                                                     # Counsel answers with cites
```

- MCP: `hub_rules_list`, `hub_rules_add`, `hub_rules_read`, `hub_rules_suggested`, `hub_rules_accept`,
  `hub_rules_edit`, `hub_rules_check`, `hub_rules_appeal`, `hub_rules_due`, `hub_rules_due_set`, `hub_rules_due_satisfied`, `hub_rules_ask`.
- Each is a v2 route with one permission check. A bot's `add` lands as `suggested`.
- **Permissions:** accept, retire or edit is open to the owner, admins, a lead of a group in Who, or an owner of a
  bot in Who. Everyone rules are owner and admins only. A With party can't edit a rule, but sees it. Any member can add a document.
- **Visibility:** every rule's sentence and From are readable by every teammate, bots included (public for now;
  hiding them is Later #14 if bots start gaming them). The quote follows
  its file's own visibility (`backend/files.py`). That check runs in the list, the bot tab, CLI, MCP and
  `hub audit`. The table stays off the SQL page, as `learned` does. A private contract gets a vaguer sentence,
  which Counsel suggests.

### 6.5 Finance coordination (agreed with the Finance dev, Oct 30)

- **Bookkeeper** is Finance's built-in bot (off the org chart, appears when Finance is set up). Never "Finance Bot".
- **Due dates:** sales tax, 1099s and franchise tax are rule due dates owned by Bookkeeper, with an optional routine.
  Whichever side ships first builds the due-date piece. The **monthly close stays Finance's own** (internal, starts on
  `finance.period.ready`, ends when the owner approves `close_period`) and doesn't depend on Rules. Bill and invoice due
  dates stay Finance events.
- **Money is gated by Finance, not Decide.** Decide's check fails open, so obligations that must hold before money
  moves (W-9 before payout, sanctions) are Finance gate rules (`fin_rules`), linked to the external rule by
  `external_rule_id` (an `org_rules` id of kind external). Counsel can suggest these gate rules; Finance has no Rules tab.
  Payouts aren't a Rules check point.
- `rule_sources` → `fin_contacts` link: optional on our side.
- No shared runner release: Rules no longer changes the prompt.
- Retiring an external rule that a gate links to leaves the gate (Finance owns it). The retire panel lists linked
  gates, and Finance gets an `org_rule.retired` event.

### 7. Optional: enforcement on vendor API calls

This was the original wrapper idea. It's kept here and not scheduled.

- **What it is.** A hub-side adapter per vendor with fixed ops on fixed hosts (`hub vendor call buffer.create_post`;
  `hub api` is already taken). Its credential is marked **hub only**, which means it's never delivered to a run.
- **Checks per rule:** bot confirms (an attestation, recorded as a claim), Decide checks, or code. Modes are Warn,
  Block or Ask, and the strictest wins. Ask uses a payload-bound approval, and the bot calls again with its id.
- **Why it waits:**
  - It's a new trust boundary. Today credentials are delivered to runs by design, and bots on one computer share a
    filesystem (`SECURITY.md`).
  - It's close to the per-action tool policy declined on Oct 29.
  - Phase 2's **Ask** on existing send/publish approvals, plus the mail connector's per-send switch, cover the main
    cases (the #ad post, marketing texts) without it.
- Revisit it with its own plan once rules are in use and a specific vendor action keeps going wrong.

### 8. laws.sh (optional, later)

laws.sh would be a cloud lookup: give it a company profile (states, industry, vendors) and it returns suggested
government rules with citations into the same Suggested tab. Tico works fully without it. The domain is registered
(HotMic AWS) but not live.

## Phases

| # | Ships |
|---|---|
| 0 | Routines with a due date (§4), as a general routine feature. Useful on its own and can ship first |
| 1 | **Internal first**, in three PRs: (a) records + audit + visibility; Rules page (Internal, External, Due, Suggested); Add rule (Who, With, From); rule side panel; bot Rules tab (internal incl. settings as sentences, then external); nav item; due dates + status, optional routine; `hub rules` + MCP. (b) Decide checks on outputs, live with Block by default, plus Escalate / Warn, appeal and the rule-check eval |
| 1b | External from documents: Add document → Counsel (hidden helper) suggests with quotes → Add/Skip/Add all; eval set. Can ship right after 1 |

## Later (agreed Oct 30: not now, keep working through)

1. **Find public terms** automatically when adding a vendor.
2. **Re-read monthly** via page monitors; changes come back as suggestions.
3. (dropped: send/publish approvals are optional, so they aren't check points, §3)
4. **"When something happens" deadlines** (tell Acme before adding an AI vendor), via wake-rules `judge_event`.
5. (moved to phase 1: Internal rules, with settings shown as sentences)
6. **Swappable Counsel.**
7. **Vendor API wrappers + hub-only credentials** (§7), with their own plan.
8. **laws.sh** suggestions through the Gateway.
9. **Calendar view and .ics** for the Due tab.
10. **Counsel suggests internal changes** (a contract caps spend → suggest a limit).
11. **Conflicts view** (a vendor says X, a customer says not-X); until then Counsel flags it as one Needs-you.
12. **Proof on close** (a routine's task needs a file before it closes), if it turns out we want it.
13. **Counsel suggests a routine** for a dated rule (pre-filled Due, Repeats, Owner).
14. **Hide rules from bots** (or some of them) if bots use what they read to work around rules.
15. (moved to phase 1: the 3rd same-rule block in a day turns into Escalate, §3)
16. **More check points:** Slack channel posts, public roadmap comments.

## Tests (high-ROI only)

- **Who / With:** a group, a bot, a person, a bot that changes group, Everyone by a non-admin (refused), and a With
  rule checked only on outputs to its With teammates.
- **Settings as sentences:** each one matches the live setting, and editing from the tab goes through the setting's own
  permission check.
- **Matching:** a comment whose requester is in With and owner isn't (checked); a bot whose group comes from its
  manager; the Assistant writing as a person under an all-bots rule (not checked); `--mine` for a derived group.
- **Checks:**
  - No matching rule → no Decide call.
  - A short break ("My favorite color is blue") and a break at the end of a long output are both caught.
  - Safe comment → forbidden edit is held and the old comment stays; a mixed bulk assignment holds only the
    breaking task.
  - Same words as a message and as an assignment are two checks; a rule revision invalidates the cache.
  - A held output can't be read through the recipient's conversation, task comments or inbox, and makes no work.
  - An appeal reaches the reviewer even when a rule would block messaging them.
  - The completion check makes no Decide call under the write lock; a held reply's text isn't in the live stream,
    `final_text`, the run log or the session.
  - A clean task then a forbidden body edit / close note is held and the task is unchanged; a note to a bot and an
    automation `_wake_bot` text are checked; a BotOps write as a person is checked.
  - An output over 120k is held; Send anyway with the nonce delivers once without a second Decide call.
  - Rules' monthly budget running out doesn't change screening's or ask ingest's.
  - An all-bots rule covers a bot created later; a Warn rule with a named reviewer round-trips.
  - Block: not delivered, the bot gets the rule, nobody is pinged, and the bot isn't quarantined (no refusal row).
  - A completion reply that breaks a rule is held, and the thread gets the one-line note.
  - Escalate: not delivered, one review task to `person_above` (or operator) even when `reports_to` is a bot; Send
    anyway delivers once.
  - The 3rd same-rule block in a day opens one review task, not one per block.
  - Warn: delivered with the warning line.
  - Fail open on timeout, budget and error, with `rules.unchecked`.
  - The same output isn't paid for twice (cache); `hub rules check` then send is one Decide call.
  - No engine call inside `store.transaction()` (a lint the reliability plan can hold).
  - Rules never appear in a prompt.
- **Suggestions:**
  - Accept writes audit with actor/via.
  - A bot's add lands as suggested.
  - A failed re-read never retires.
- **Cites:** a fetched link's cite points at a Files version that a later page change doesn't alter.
- **Visibility:** someone without file access sees the sentence but not the quote, in the list, CLI and MCP.
- **Routines with a due date:**
  - One task per (routine, due_on) across repeated or concurrent ticks.
  - Catch-up after a missed day.
  - An overdue occurrence doesn't block the next.
  - Completing the task marks that due date Satisfied; an expired or cancelled task doesn't; reopening undoes it; a
    due date with no routine can be marked by hand.
  - Overdue flips at the end of the day in the due date's zone; an edited repeat keeps its history.
- **Evals:**
  - Counsel: missed-obligation rate and quote-not-in-source rate on the ~20-document set.
  - Rule check: about 30 outputs × 10 rules, hand-labeled, scored on false-block rate. It picks Low or Medium.

## Open questions

1. Is Counsel hidden behind the Assistant (One Assistant), or a visible teammate with its own page (board 7)? v3
   chooses hidden.

## Review log

**Grok 4.7 (xhigh), v4.1, Oct 30** (`2026-10-30-rules-plan-review-grok.md`, verified at c15ff9b04). Taken: the
completion preflight goes before `mutate` (write lock); live reply text withheld while a checked run streams; rules'
own monthly budget and a per-bot draft-check cap; one gate helper over every bot-authored string (task update
fields, owner's ask, bulk note, notes, automation `_note` / `_wake_bot` / `_post_update`, Slack replies); mail With
via roster addresses; too-large is held; Send anyway nonce, `why` stays on the hold, cache hits count toward
repeats; BotOps writes are checked; Everyone wording, Later #3, `with.anyone` dropped. Not taken: a person must
satisfy a bot-owned due date (Chris chose no proof, and it would ping him for every Bookkeeper filing; the Due tab
shows who marked it); quotes on Suggested rows (Chris asked for sentence, Who and Skip/Add only).

**Second pass (v4), Oct 30:** Fable 5.1 high (`2026-10-30-rules-plan-review-fable-2.md`) on the post-v3 changes, and
Codex gpt-6-astra high (`2026-10-30-rules-plan-review-codex-2.md`) on the whole spec. All code claims checked at
c15ff9b04.

Taken from Fable:
- Named hook points, including the completion reply, with a one-line note in the thread.
- `ask_ingest`'s preflight/apply instead of screening's deferred thread.
- (Rehearsal for a week before Block, taken then dropped: Chris wants it live on ship.)
- Decide Medium until a rule-check eval says Low.
- Block is a `held` 200, not a refusal (no quarantine).
- The 3rd same-rule block in a day escalates (Later #15 moved in).
- The keeper pays under its own cap, and unchecked counts show on the page.
- Matching rules for multi-recipient writes and derived groups.
- Due-date time zone, month-end clamp, end-and-restart edits, retire not delete; caps question closed (own work is
  never capped).
- Settings sentences table with `setting:` lines in `--mine`.
- Reviewer = `person_above`, never a bot.
- Gate-link on retire.
- `via` drops `bot`.

Not taken from Fable:
- A count line in the prompt (Chris: rules aren't in the prompt).
- Holding output as an undelivered message row (Codex showed inboxes read those).

Taken from Codex:
- No minimum length or 20k cut; long outputs checked in segments or marked unchecked.
- Cache keyed by the full check input plus rule versions.
- Comment edits and bulk owner changes are check points.
- Only a real completion satisfies a due date, not an expiry or cancel; due-date tasks are exempt from the ask-expiry
  sweep.
- Cites always point at a Files version (page monitors keep no history).
- A separate `org_rule_holds` table.
- `all_bots`, `if_broken` and `reviewer` fields, plus CLI flags.
- `hub rules appeal`, so "ask your manager" can't itself be blocked.

Not taken from Codex: nothing; all eight were verified.

Oct 30 (Chris, after v3): Internal is a real kind; rules aren't injected into prompts but checked by Decide on
outputs; all rules readable by all teammates; due dates have a status. The Standing-block and runner-release notes
below are superseded.

**Codex (gpt-6.1-terra high)**, `2026-10-30-rules-plan-review-codex.md`. Taken into v2:
- Approvals and talk permissions corrected.
- Payload-bound Ask.
- Attestations are claims.
- A failed read never removes a rule.
- Deadlines as unique occurrences.
- Ordered instruction budget.
- Bots never accept.
- Eval set.

Not taken:
- Full per-rule ACLs.
- A new snapshot store.
- An outbox for vendor calls.
- Dropping Add all.
- Putting Counsel last.

**Fable 5.1 (high)**, `2026-10-30-rules-plan-review-fable.md`, checked against code. Taken into v3:
- Counsel is a hidden helper (One Assistant).
- Suggestions-first phase 1.
- Reuse `strategy_bets` shape, Files versions, `market_watch` re-reads, and task refs instead of an occurrence table.
- Scheduler job, not a built-in automation.
- Standing block with stable order and a count budget (protects the prompt delta).
- Contract bump and runner release noted.
- `hub api` is taken.
- "Decide" not "Jev".
- Drop the screening door.
- No RRULE.
- Real role checks.
- A person's own Assistant chat may add a rule as them.
- Visibility on every read path.
- "Sends outside" was already visible (my error).

Not taken (kept as Chris asked for them, but moved):
- Internal was a read-only view in v3; Oct 30 Chris made it a real rule kind (between teammates, Who/With) and
  phase 1, with settings still shown as sentences.
- Wrappers stay in the plan as an optional §7, not deleted.
