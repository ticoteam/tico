# connectors/mail

The company's one mail tool. Employees use it through `scripts/mail.sh`; the employee-facing
guide is `docs/mail.md`. The plan and the reasoning are `docs/mail-service.md`.

Stages 1 and 2 are built. Stage 1 reads and files mail: auth, `doctor`, reading, labels, the
rules engine, the database, the audit log. Stage 2 writes it: the lint, `draft`, `send`, `reply`,
`sent-log`, `slots`, `schedule` and `doctor --e2e`. Every send goes to Tico's send queue, which decides it. Everything except `doctor --e2e` and the Gmail calls themselves is exercised
offline by the tests.

## Layout

```text
connectors/mail/
  __init__.py       paths, Failure/Refused, YAML loading, time parsing. Pure, no Google.
  __main__.py       the argparse CLI. `python -m connectors.mail <command>`
  auth.py           service account + domain-wide delegation, subject = the mailbox
  gmail.py          thin Gmail client (retries, pagination, history, drafts, send) + the normalizer
  calendar.py       thin Calendar client + the pure free/busy and slot maths
  db.py             SQLite at <projects>/runtime/mail/mail.db, WAL
  access.py         employee -> mailboxes -> verbs, from employee.yaml. Refusals are exit 2.
  policy.py         the one send door (POST /api/v2/email/sends) and what a run reads from Tico around it
  lint.py           the deterministic rules, L010..L071. Pure. Tico runs the same lint on every send.
  compose.py        MIME building and the two idempotency keys. Pure.
  rules.py          the deterministic inbox rules engine
  labels.py         the hub/* label set
  audit.py          one line per action to audit.jsonl and the audit table
  requirements.txt  the venv scripts/mail.sh builds
  tests/            unittest, stdlib only, fake Gmail and Calendar, JSON fixtures
```

Everything except `auth.py`, the client half of `gmail.py` and the client half of `calendar.py`
is pure: the normalizer, the rules engine, access resolution, the lint, the slot maths, the
message builder and the renderers take dicts and return dicts. That is why the tests need no
network, no key and no Google libraries. `policy.py` talks to Tico through the `HUB_GET`, `HUB_POST`,
`SERVER_GET` and `RUNNER_GET` seams the tests replace.

Google's client libraries are imported lazily inside `auth.py`, so `import connectors.mail`
works on a bare Python. `doctor` reports them missing rather than crashing.

## Running it

```bash
scripts/mail.sh doctor                      # builds the venv on first use, then checks setup
scripts/mail.sh inbox --as influencer --new --format brief
scripts/mail.sh inbox --as influencer --untriaged --format brief
scripts/mail.sh rules backtest --as inbox --since 14d --rules /tmp/candidate.yaml
cd <hub> && python3 -m connectors.mail whoami --as ana --mailbox ana@acme.example
```

`scripts/mail.sh` creates `<projects>/runtime/mail/venv` from `requirements.txt` the first time
and rebuilds it when `requirements.txt` changes. Delete the directory to force a rebuild.

Exit codes match `connectors/slack.py`: 0 ok, 1 failure, 2 policy refusal. `--json` turns
errors into `{"ok": false, "kind": "refused"|"failed", "error": ..., "hint": ...}`.

### Reading without the bodies

A bot lists with `--format brief` and verifies with `--untriaged`. `md` is the full record,
every body included, which is how one inbox read once cost 948K input tokens and truncated the
model's answer before the messages it had already marked seen were triaged.

`inbox`, `thread` and `search` take `--format brief`: a header line, then four lines per
message and nothing else - id, date and sender; subject; thread, labels, whether it has an
unsubscribe link, attachment count; Gmail's snippet on one line. Sender is cut at 40
characters, subject at 90, snippet at 160. The bot opens the few threads that need reading
with `thread <id>`. `md` and `json` are unchanged.

```text
# Inbox for ana@acme.example (all): 2 messages
- 19a2c1  2026-09-14 08:12  Acme Growth
  Grow your team with our guide
  thread:19a2c1  labels:INBOX,UNREAD  unsub:y  att:-
  > Read the guide. Ten ways to fill your calendar this fall...
- 19a2b9  2026-09-14 07:40  Real Person
  Pricing question
  thread:19a2b9  labels:INBOX,UNREAD  unsub:n  att:1
  > How does the 3.9% work when...
```

`inbox --untriaged` is the read that proves a pass is finished: `in:inbox` minus every message
already carrying any `hub/*` label in that mailbox (the query is built from the labels that
exist, `-label:"hub/needs-owner" -label:"hub/triaged/<slug>" ...`, and the fetched labels are
checked again in case Gmail's index lags a relabel). It is label-based and idempotent - no
`seen` row, no watermark - so it returns the same answer until something is labelled, and it
refuses to combine with `--new` or `--since`. `--limit` defaults to 200 there and 50 otherwise.
It is audited as `inbox` with `"untriaged": true`.

`inbox --decisions` adds one line per message from the decision model (below, "Decisions"), judged from these
same listing fields before any thread is opened:

```text
  decision: reply  (reply 0.81; ask 0.90, money 0.05, legal 0.02, urgency 1.4)
```

## State on disk

```text
<projects>/runtime/mail/mail.db        seen, watermarks, rule_hits, audit,
                                       drafts, sends, queued_drafts, messages, sync_state
<projects>/runtime/mail/audit.jsonl    the same audit lines, append-only
<projects>/runtime/mail/venv           the Python environment
<projects>/secrets/google-sa.json      the service-account key, mode 600, never in git
```

`seen` is per (mailbox, employee, message): `inbox --new` returns a message once, and the
watermark's `updated` becomes the `after:` floor on the next run so Gmail is not asked for the
whole mailbox again. `triaged` stamps `triaged_at` and moves the watermark. Neither `inbox`
without `--new` nor `--since` nor `--untriaged` consults any of it, which is the escape hatch
when a run needs to look again; `--untriaged` also writes none of it.

`drafts` is keyed by sha256(employee, issue, to, subject, body): a retried `draft` finds its own
row and updates that Gmail draft instead of leaving a second one. `sends` is keyed by
sha256("send", employee, issue, draft id) and the row is written **before** the API call, so a
crash between the call and the answer cannot become a second email; a call that never happened
deletes its own claim, so a retry is still allowed. `sends` is also where `sent-log --reconcile`
puts the owner's own sends, as employee `human`. `queued_drafts` maps a Gmail draft to the Tico send
it became, so the connector deletes the draft once the send goes out.
Attachments go into the frozen message Tico queues, so Tico's decision and any approval cover the exact
files. `mail draft --attach --json` includes an approval summary payload (name, size and sha256 per
file, and the file id on the task when the upload worked). Files must come from the bot's own folder,
exclude secrets and total at most 25 MB.
`mail discard` deletes a Gmail draft only when its `drafts` row names the same employee and
mailbox and no `sends` row has sent it, then drops the row.

`messages` is the local copy of every normalized message a `mail sync` (or an opportunistic
`inbox` / `thread` / `search` fetch) has stored: id, thread, date, addresses, subject, snippet,
labels, body (same 32 KB cap as a live read), attachments listed but not downloaded, list-id,
internal/unsubscribe flags, and `fetched_at` / `updated_at` / `deleted_at` / `pushed_at`.
`sync_state` is the per-mailbox Gmail history cursor and last-run stamp. Nothing is pushed to
the hub from this CLI; `mail sync export` prints the unpushed batch as JSON and `mail sync ack`
marks those ids pushed, so a later runner can hold the token. Incremental sync uses
`users.history.list`; an expired history id falls back to `after:<last_run − 2d>`. First run
uses `--backfill 90d` (or another duration). `--all-roster` walks every address on
`registry/people.yaml`.

## Tests

```bash
cd <hub> && python3 -m unittest discover -s connectors/mail/tests
```

Offline, stdlib only, a couple of seconds. `tests/fake.py` is a fake Gmail service with the same
resource shape the real client uses (`users().messages().list/get/modify`, `labels()`,
`threads()`, `history().list`, `drafts().create/update/get/delete/send`), including a tiny query matcher for
`in:inbox`, `in:sent`, `label:`, `-label:`, `from:`, `subject:` and `after:`, plus a
`FakeCalendarService` for `calendarList`, `freebusy` and `events`. `tests/harness.py` builds a
whole temp world - manifests, a creator table, a fake Tico send queue - for the stage-2 tests, and
sets `MAIL_DECISIONS=none` so nothing calls a model.

`tests/fixtures/*.json` are raw Gmail messages with the rule outcome each one
should get; they are used by `test_rules.py` and by the CLI:

```bash
python3 -m connectors.mail rules test connectors/mail/tests/fixtures
```

A mailbox's own rules can live with its inbox bot instead: when the bot that handles the mailbox (the
`inbox_bot` on the person's roster entry, or their nearest manager's) has `rules/mail-rules.yaml` in its
repository, that file (same shape) is used for the mailbox in place of the registry's list for it. The
registry's `common:` list always runs first, so the company-wide protections still win.

Adding a rule to `registry/mail-rules.yaml` should come with a fixture, and with a backtest
against real mail:

```bash
scripts/mail.sh rules backtest --as inbox [--since 14d] [--rules candidate.yaml] \
                               [--limit 2000] [--format md|json]
```

`rules backtest` replays the rules over every message in the window - `after:<epoch>` with no
`in:inbox`, so archived and sent mail count too - and reports what they would do. It is a
read: `Ctx(args, "read")`, no label changes, no unsubscribe, no `seen` row, no watermark, and
one audit row (`backtest`) carrying counts only. The report is the window and message count;
per rule, how often it fired and how many of those hits archived, protected
(`never_archive`), unsubscribed or were blocked, with up to five sample subjects; with
`--rules`, the candidate is run beside the registry and every message is bucketed as
`newly archived`, `no longer archived`, `newly protected` or `no longer protected` (subjects
listed for the first and last of those); **conflicts**, meaning messages the rules would
archive that carry `hub/needs-owner`, `hub/drafted` or `hub/handled/owner`, whose thread is in
`drafts` or `scheduling_actions` in `mail.db`, or whose sender is a person at acme.example; and
finally "would reach the model", the count not archived by the rules, which is what a bot
would still read. Only id, thread, sender, subject, labels and rule outcomes are kept per
message, so a 2000-message window stays small. `--format json` carries the same data
structured, including every bucketed message.

## Sending

The CLI decides no send. `send`, `reply` and `schedule` freeze the draft as raw MIME and post it to
`POST /api/v2/email/sends` (`policy.queue_send`, backend/email_sending.py); Tico answers with the send's
`state` and its decision's reasons:

| state | what it means | what the CLI reports |
| ----- | ------------- | -------------------- |
| `pending` | accepted; the undo window runs, then the connector dispatches it | `queued: true`, `send_after` |
| `waiting_approval` | Ask first, or a rule that asks: a person approves it in Tico | `queued: true`, `approval_id` |
| `blocked` | Never, a blocked or suppressed recipient, owner-handles-personally, the lint, the reviewer, the org rule check | `downgraded: "draft"`, `reason` = Tico's `detail` |

Tico decides there, and only there: the company, inbox and bot sending policy (**On their own**, **Ask
first**, **Never**; set in Tico, Settings > Bots > the bot > Email sending, or the company or inbox
sending policy), blocked recipients, owner-handles-personally, outside Cc and many-outside-recipient
asks, the lint and the second reviewer on a bot's own writing, the org rule check, bounce suppression and
approvals. `--approval <id>` on `send`, `reply` and `schedule` passes the id of the person's Tico approval
or message the send relies on; Tico checks it. A blocked send is a downgrade, exit 0, and the Gmail draft
stays. The one check the CLI keeps is access: a bot whose `bot.yaml` grants no `send` verb on the mailbox
gets a draft and `gate: "verb"`.

`policy show --as <slug>` prints the effective sending policy as Tico answers it
(`GET /api/v2/bots/<slug>/email-sending-policy`). `doctor --e2e` runs only against the mailbox named in
`TICO_MAIL_SANDBOX`.

## The lint

`lint.py`'s docstring is the rule table: L010-L011 URLs, L020
placeholders, L030 secret shapes, L040 and L041 internal leakage, L050-L055 structure,
L060-L067 scheduling, L070 and L071 replies. Ids are stable, because playbooks and Issues quote
them. Everything is an error except L041 (something that looks like a hub Issue number, which is
often a real invoice number in a customer's own sentence).

One rule is narrower than it looks, on purpose. L060 (at least two times) fires only without
`--slot`, because a confirmation names exactly one time and that is the point. `mail draft` runs the
lint so the bot sees its findings at once; Tico runs it again on the frozen message.

## Decisions

`connectors/mail/judge.py`: decisions, through the one primitive every
bot has (`clients/judge.py`, `skills/decisions/SKILL.md`). It classifies and scores; it writes
nothing. Two uses, both reading a shared question set from the hub's `questions/`:

| where | set | what happens |
| ----- | --- | ------------ |
| `inbox --decisions` | `mail-triage` | every listed message is judged from the brief fields (sender, subject, snippet, headers; never the body), eight calls in flight; each gets `judgment` in the JSON and a `decision:` line in the brief listing with a one-word suggestion (`archive`, `needs-owner`, `route`, `reply`, `read`) computed from the set's thresholds by `suggest()`: money and legal protect first at low thresholds, filings need high confidence, and under threshold it is `read`. Nothing is labelled. Successful answers are cached for seven days by exact listing state and question set; `cached` in the decision model summary counts calls avoided. A message whose call failed carries `{"error": ...}` and the pass goes on. |
| `draft` | `mail-draft-gate` | six nouls over the draft and the thread (money, a date, a confirmed time, an unsupported claim, a legal position, tone), run after the lint. Advisory: `gate` on the payload and a `gate:` line, one `gate` audit line, the draft still written and Tico still deciding the send. Unreachable is `gate: unavailable`, not a refusal. |
| `scheduling` | `scheduling-review` | before an offer or a booking: is the thread one the standing scheduling permission covers, and does the attendee accept the exact time. Fails closed: no model or a doubtful answer schedules nothing. |
| `rules run`, `explain`, `backtest`, `test` | any set a `decision` condition names | before the rules run, every message is judged with each set the loaded rules name (`judge_sets`) and carries `judgments[set]`; the `decision` condition (`rules.py`; `judge` is the old spelling) then reads it: `{set, question, is?, min?, max?}`. No judge available: `run` and `explain` skip those rules and say so (`judge-skipped` in the audit); `backtest` refuses, because "never fires" would be a lie. Fixtures carry `judgments` so `rules test` stays offline. |

Which engine: inside a bot turn, the hub (`HUB_API_URL` and `HUB_TOKEN`, which the runner sets;
the key stays on the server and the call is audited there); otherwise a `TYPESAFE_API_KEY` of
this process's own; otherwise none, and `--decisions` fails with the hint while `draft` skips the
gate. `MAIL_DECISIONS=none` switches it off (`MAIL_JUDGE` is the deprecated old name and still works; the test harness sets it; `tests/test_judge.py` injects
a fake through `judge.ENGINE`).

## Ideas for stage 3

- **A per-thread cap.** The influencer proposal asked for `max_per_thread: 1` (one cold message,
  no follow-ups). Tico's sending policy has no field for it yet, so it lives in the playbook instead.
- **Bounces and replies.** A bounce should mark the row in `sends` and take the address out of
  the eligible table; a reply should close the loop that `mail reply` opened.
- **Rule proposals from the bots.** `rules test` already runs fixtures, so an inbox bot could
  open a PR with a rule and the fixture that proves it.

## Google setup (the owner, once, about twenty minutes)

From `docs/mail-service.md`, "What the owner does":

1. **Google Cloud.** Create a project - `acme-tico-hub` is the suggested name. Do not reuse
   `anarowboat`, which holds the personal OAuth client the `gws` CLI uses. Enable the **Gmail
   API** and the **Google Calendar API** in it.
2. **Service account.** IAM & Admin > Service Accounts > Create, name it `company-hub-mail`.
   Open it, Keys > Add key > Create new key > JSON. Save the file as
   `~/tico-work/secrets/google-sa.json` and `chmod 600` it. Note the **client ID** (a long
   number on the service account's Details tab); `scripts/mail.sh doctor` prints it too.
3. **Domain-wide delegation.** Google Workspace Admin console > Security > Access and data
   control > API controls > Domain-wide delegation > Add new. Paste the client ID and these
   two scopes, comma separated:

       https://www.googleapis.com/auth/gmail.modify
       https://www.googleapis.com/auth/calendar

   Google has no scope that allows drafts but forbids sending. That is why Tico's send queue is
   the send gate and not Google's.
4. **Mailboxes.** `legal@acme.example` must be a real user mailbox, not a group or an alias:
   delegation can only impersonate a user. Create `hub-test@acme.example` and name it in
   `TICO_MAIL_SANDBOX`: it is the only mailbox `doctor --e2e` will send from.
5. Run `scripts/mail.sh doctor`. It checks the key, the delegation, impersonation for every
   mailbox any employee declares, the hub labels, and calendar access, and prints the exact fix
   for anything that fails.

The key is never loaded into a bot's environment. The CLI reads it by path, and only `doctor`
ever prints anything from it: the client email, the client ID and the project. Never the
private key.
