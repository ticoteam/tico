# connectors/mail

The company's one mail tool. Employees use it through `scripts/mail.sh`; the employee-facing
guide is `docs/mail.md`. The plan and the reasoning are `docs/mail-service.md`.

Stages 1 and 2 are built. Stage 1 reads and files mail: auth, `doctor`, reading, labels, the
rules engine, the database, the audit log. Stage 2 writes it: the policy file, the lint, the
second-model reviewer, `draft`, `send`, `reply`, `sent-log`, `slots`, `schedule` and
`doctor --e2e`. Everything except `doctor --e2e` and the Gmail calls themselves is exercised
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
  policy.py         registry/mail-policy.yaml: the send chain, the caps, the approval Issue
  lint.py           the deterministic rules, L001..L071. Pure.
  review.py         the second model (Grok Build, xAI, or none). Never raises.
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
network, no key and no Google libraries. `policy.py` shells out to `gh` for one thing only, the
approval Issue, through a `RUN` seam the tests replace; `review.py` shells out to the reviewer
through the same kind of seam.

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
                                       drafts, sends, reviews, messages, sync_state
<projects>/runtime/mail/audit.jsonl    the same audit lines, append-only
<projects>/runtime/mail/review/        scratch directories the Grok Build reviewer runs in
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
puts the owner's own sends, as employee `human`, which is how the owner's sends count towards a bot's
per-recipient cooldown. `reviews` holds every verdict; a send re-uses one only while it is under
24 hours old, and never for a draft with attachments, which is reviewed again as it stands and
always needs a per-message approval to send.
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
whole temp world - manifests, a creator table, a policy file - for the stage-2 tests, and sets
`MAIL_REVIEWER=none` so nothing calls a model.

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

## registry/mail-policy.yaml

The schema is documented at the top of the file itself; this is the shape and where each part
bites. `policy.validate()` rejects an unknown key rather than ignoring it, because a typo in a
cap must not silently become "no cap".

```yaml
global: { send_enabled: true }              # the kill switch: false means everything is a draft
sandbox_mailbox: hub-test@acme.example          # the only mailbox doctor --e2e will touch
internal_domains: [acme.example]                # internal recipients need no allowance; lint allows links on these hosts
forbidden_phrases: []                           # lint L001: house-style phrases no message may contain
signature_names: []                             # lint L055: names a signature may carry (default: the mailbox's own name)
scheduling:                                     # the one mailbox that may use the standing scheduling permission
  mailbox: ana@acme.example
  employee: inbox
  booking_url: ''                               # optional link offered when neither time works
  meeting_title: Meeting                        # the calendar event title
mailboxes:
  ana@acme.example: { paused: false }         # per-mailbox pause
defaults:                                   # the stricter of these and an allowance wins
  max_sends_per_day: 20
  per_recipient_cooldown_days: 14
  max_external_recipients: 1
  allow_cc_external: false
  allow_attachments: false                  # opt-in; true lets `draft --attach` add files from the bot's folder
blocklist: { addresses: [], domains: [] }                  # never written to, in any verb
owner_handles_personally: { addresses: [], domains: [] }   # forces hub/needs-owner
allowances:
  - employee: influencer
    mailbox: ana@acme.example
    purpose: "one paragraph; the reviewer is shown it"
    recipients:
      source: csv                           # csv or list
      path: emp-influencer/data/creators.csv        # relative to <projects>/
      column: email
      require_column: { fit: keep }         # "keep" and "keep: reason" match, "skip" does not
    caps: { per_day: 10, per_recipient_cooldown_days: 365 }
    urls:
      required_pattern: '^https://acme\.example/guide\?utm_source=...$'
      forbidden: [calendly.com, acme.example/demo, go.acme.example]
```

The send chain, in order: `global.send_enabled`, then the mailbox pause, then the employee
declaring the `send` verb on this mailbox, then `outbound_send: true` in its `employee.yaml`,
then the recipient being internal, listed in the bot's `forward_to:`, the sender of the thread being replied to, covered
by an allowance, or named by `--approval-issue N` (the first three need no approval once `outbound_send` is true),
then the caps, then the blocklist, then owner-handles-personally. Every one of those failing is
a **downgrade to a draft with a reason**, exit 0. Only a broken policy file is an error, exit 1. With no
`registry/mail-policy.yaml` (a Docker computer) `policy.builtin()` is the policy: sending on, the team's domains internal,
the usual caps, an empty blocklist.

`check_draft` is the short version - blocklist, owner-handles-personally, attachments - and that
one refuses with exit 2, because a draft nobody may write is a mistake to fix rather than
something to leave lying about.

An approval Issue is checked with `gh issue view N -R ticoteam/tico --json
state,labels,title,body`: it has to be **closed**, carry `owner:ana` and `type:decision`, and
name the recipient address or the thread id in its title or body.

## The lint

`lint.py`'s docstring is the rule table: L001 forbidden phrases, L010-L012 URLs, L020
placeholders, L030 secret shapes, L040 and L041 internal leakage, L050-L056 structure,
L060-L067 scheduling, L070 and L071 replies. Ids are stable, because playbooks and Issues quote
them. Everything is an error except L041 (something that looks like a hub Issue number, which is
often a real invoice number in a customer's own sentence).

Two rules are narrower than they look, on purpose. L012 (the employee's required CTA) fires only
when the body has a link at all, because the "reply to a no" script carries none. L060 (at least
two times) fires only without `--slot`, because a confirmation names exactly one time and that
is the point.

## The reviewer

`MAIL_REVIEWER` picks the backend; the default is `grok:grok-4.6`.

| value            | what it does |
| ---------------- | ------------ |
| `grok:<model>`   | `grok -p '<prompt>' --output-format json -m <model> --max-turns 1 --disable-web-search --permission-mode plan`, run in a scratch directory under `runtime/mail/review/`. Grok Build is a coding agent, so the flags plus a one-line preamble (`GROK_PREAMBLE`: answer from this message alone, no tools, no narration) hold it to one answer and no side effects; without the preamble it spends its turn saying what it is about to do and gets cancelled. The owner's xAI subscription, no key to manage. About ten seconds. |
| `xai:<model>`    | `https://api.x.ai/v1/chat/completions` with `XAI_API_KEY`, stdlib urllib. |
| `none`           | returns ok without a model. What the tests use, and the escape hatch if both backends are down and the owner wants drafts anyway. |

It is shown: the incoming thread (truncated), the draft, the employee's Role paragraph from its
`AGENT.md` **plus its standing allowance's `purpose:`**, the "What we are not" and "Locked pitch"
sections of `docs/company.md`, and the list of rules the lint already checked. The allowance
purpose matters: without it a standing, approved offer reads as a fresh commitment and every
cold message fails. That is why the influencer allowance's `purpose:` spells the offer out.

The verdict must be JSON `{ok, problems, claims_not_in_thread, commitments, tone}`. It is parsed
leniently - the first balanced `{...}` anywhere in the answer - and any commitment or unsupported
claim forces `ok: false` whatever the model said about itself. Timeout 90 seconds. Unreachable is
not the same as bad: `draft` proceeds with `review: "unavailable"` on the record, `send`
downgrades to a draft.

## Decisions

`connectors/mail/judge.py`: decisions, through the one primitive every
bot has (`clients/judge.py`, `skills/decisions/SKILL.md`). It classifies and scores; it writes
nothing. Two uses, both reading a shared question set from the hub's `questions/`:

| where | set | what happens |
| ----- | --- | ------------ |
| `inbox --decisions` | `mail-triage` | every listed message is judged from the brief fields (sender, subject, snippet, headers; never the body), eight calls in flight; each gets `judgment` in the JSON and a `decision:` line in the brief listing with a one-word suggestion (`archive`, `needs-owner`, `route`, `reply`, `read`) computed from the set's thresholds by `suggest()`: money and legal protect first at low thresholds, filings need high confidence, and under threshold it is `read`. Nothing is labelled. Successful answers are cached for seven days by exact listing state and question set; `cached` in the decision model summary counts calls avoided. A message whose call failed carries `{"error": ...}` and the pass goes on. |
| `draft` | `mail-draft-gate` | six nouls over the draft and the thread (money, a date, a confirmed time, an unsupported claim, a legal position, tone), run after the lint and before the reviewer. Advisory: `gate` on the payload and a `gate:` line, one `gate` audit line, the draft still written and the reviewer still deciding. Unreachable is `gate: unavailable`, not a refusal. |
| `rules run`, `explain`, `backtest`, `test` | any set a `decision` condition names | before the rules run, every message is judged with each set the loaded rules name (`judge_sets`) and carries `judgments[set]`; the `decision` condition (`rules.py`; `judge` is the old spelling) then reads it: `{set, question, is?, min?, max?}`. No judge available: `run` and `explain` skip those rules and say so (`judge-skipped` in the audit); `backtest` refuses, because "never fires" would be a lie. Fixtures carry `judgments` so `rules test` stays offline. |

Which engine: inside a bot turn, the hub (`HUB_API_URL` and `HUB_TOKEN`, which the runner sets;
the key stays on the server and the call is audited there); otherwise a `TYPESAFE_API_KEY` of
this process's own; otherwise none, and `--decisions` fails with the hint while `draft` skips the
gate. `MAIL_DECISIONS=none` switches it off (`MAIL_JUDGE` is the deprecated old name and still works; the test harness sets it; `tests/test_judge.py` injects
a fake through `judge.ENGINE`).

## Ideas for stage 3

- **Attachments, properly.** Today the policy just refuses them. The honest version reads the
  file from the S3 bucket by URI, checks type and size, and never touches the local disk.
- **A per-thread cap.** The influencer proposal asked for `max_per_thread: 1` (one cold message,
  no follow-ups). The schema has no field for it yet, so it lives in the playbook instead.
- **Bounces and replies.** A bounce should mark the row in `sends` and take the address out of
  the eligible table; a reply should close the loop that `mail reply` opened.
- **The Mail tab in the hub app.** `sends`, `drafts` and `reviews` are already the right shape:
  what went out today, what is waiting, what the reviewer refused and why.
- **A nightly reconcile.** `sent-log --reconcile` exists; a launchd entry would keep the
  cooldowns honest without anyone remembering to run it.
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

   Google has no scope that allows drafts but forbids sending. That is why the send gate is
   `policy.py` and not Google's.
4. **Mailboxes.** `legal@acme.example` must be a real user mailbox, not a group or an alias:
   delegation can only impersonate a user. Create `hub-test@acme.example`: it is
   `sandbox_mailbox` in `registry/mail-policy.yaml`, and the only mailbox `doctor --e2e` will
   send from.
5. Run `scripts/mail.sh doctor`. It checks the key, the delegation, impersonation for every
   mailbox any employee declares, the hub labels, and calendar access, and prints the exact fix
   for anything that fails.

The key is never loaded into a bot's environment. The CLI reads it by path, and only `doctor`
ever prints anything from it: the client email, the client ID and the project. Never the
private key.
