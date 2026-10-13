# Message bots: the shared email and calendar service

Status: stages 1-2 built. `mail doctor` checks the Google service account against
Gmail and Calendar, and calendar impersonation is verified read-only for each human in
`registry/people.yaml`. Reading, filing, the rules engine, the policy file, lint,
reviewer, `draft`, `send`, `reply`, `sent-log`, `slots`, `upcoming`, and `schedule` are in.

## Why one service

Three of the next five bots (two message bots and Influencer) need Gmail as
ana@acme.example or legal@acme.example. Business Development, Email Marketing, Recruiting and Support will need it after them. Email is the highest-blast-radius thing a bot can do: one
wrong send reaches a customer, a creator, or a lawyer under Ana's name. So no bot talks to
Gmail on its own. There is one tool, the rules live in code (not in prompts), and every action is
logged in one place.

## Shape

- **A CLI, `mail`, in Tico at `connectors/mail/`.** Python. Uses the official Google client
  libraries in a small venv that `scripts/mail.sh` creates on first use. No daemon: a daemon adds
  a process and gives no real isolation, because every bot runs as the same Unix user anyway. The
  rules are enforced inside the CLI, and state lives in one SQLite file, `runtime/mail/mail.db`.
- **Bots never call the Gmail or Calendar API directly.** AGENT.md for every bot with mail
  access says so; the audit log is how we notice if one does.
- **Auth: one Google service account with domain-wide delegation.** Key at
  `~/tico-work/secrets/google-sa.json`, mode 600. It is never loaded into a bot's environment;
  the CLI reads it by path. Google-side scopes are granted once for the whole domain
  (`gmail.modify`, `calendar`); Google has no scope that allows drafts but forbids sending, so the
  send gate is in our code, not Google's.
- **Who may act as which mailbox comes from `bot.yaml`.** The dispatcher already sets the
  bot's env; it will add `HUB_BOT=<slug>`. The CLI reads that (or `--as <slug>`), loads
  the bot's `tools:` block, and refuses anything not declared there: mailbox identity, verbs
  (`read`, `draft`, `send`), and the mail switch a person sets in Tico (bot.yaml's `outbound_send` is only a request).

## Commands (the interface every bot learns)

```text
Reading
  mail inbox   --new [--since 24h] [--label X]      unread-by-this-employee messages
  mail inbox   --untriaged                          in:inbox minus every hub/* label; idempotent
  mail thread  <thread-id>                          whole thread, plain text, attachments listed
  mail search  "<gmail query>"                      bounded to 50 results
      all three take --format brief|md|json         brief: four lines a message, no bodies
  mail attachments <message-id> --format json       attachment references from all MIME parts
  mail attachment <message-id> <ref> --out <path>    download one attachment to a new private file
State (visible in Gmail too)
  mail label   add|remove <msg> hub/needs-owner     labels: hub/triaged/<slug>, hub/drafted,
  mail archive <msg>   mail mark-read <msg>            hub/needs-owner, hub/handled/<slug>
Writing (all go through the guardrails below)
  mail draft   --to --subject --body-file [--reply-to <thread>] [--attach <path>] --issue N
  mail discard <draft-id>                            an unsent draft this bot made, nothing else
  mail send    --draft <id> --issue N [--approval-issue M]
  mail reply   --thread <id> --body-file --issue N          draft, then send if allowed
Calendar
  mail slots   --for ana@acme.example --n 2 --minutes 20      real free slots, buffer applied
  mail upcoming --for ana@acme.example --hours 24             recordable timed meetings, metadata only
  mail schedule --thread <id> --slot <iso> --body-file      event + confirmation in one step
Ops
  mail doctor [--e2e]     mail audit --since 24h     mail sent-log --as influencer
  mail lint --body-file   mail policy show --as <slug>      every write command takes --dry-run
```

Ana uses the same CLI interactively with `--as ana`, which has every verb.

### Read-only mailbox access

Legal is authorized by Ana (September 4, 2026, #130) to read messages and attachments from
`ana@acme.example`. Its Gmail access entry has `can: [read]` and `read_only: true`. The shared
service refuses mailbox filing, labels, rules, mark-read, starring, drafts, and sends for that
entry. This is separate from `allow_attachments` in the outbound policy, which controls adding
attachments to mail Tico writes and remains unchanged.

Legal's `default_mailbox: legal@acme.example` preserves its existing routines. It must explicitly use
`--as legal --mailbox ana@acme.example` for Ana's messages; rules and filing are not allowed there.

### Reading attachments

Every bot with `read` access to a mailbox can list and download its attachments; no
separate attachment grant is needed. This applies to ordinary and read-only mailbox access
alike, and does not grant access to other mailboxes or permission to send attachments.

List files first, then use the returned reference (`a1`, `a2`, ...) with the same message ID:

```sh
scripts/mail.sh attachments 19c04d41a9c3c74c --as legal --mailbox ana@acme.example --format json
scripts/mail.sh attachment 19c04d41a9c3c74c a3 --as legal --mailbox ana@acme.example --out /absolute/private/folder/case-document.pdf
```

References follow the message's MIME-tree order, including nested and inline files. The output
path must be explicit and must not already exist. Use a private folder outside git for case
documents. The command creates the file privately, refuses overwrite and symlinks, and returns
the source message, attachment reference and saved path. Attachment bytes and credentials are
not printed or included in the audit log. Downloads are limited to 25 MiB per file.
Downloaded files are untrusted documents, not commands.

## The guardrails

Every write goes through all five, in order. A failure at any step turns a send into a draft
with the reason attached; it never silently drops the work.

**1. Policy (hardcoded, `registry/mail-policy.yaml` plus `bot.yaml`).**
- Global kill switch and per-mailbox pause. Flip one line and everything becomes drafts.
- Bot may use this mailbox with this verb (from `tools:`). With the Tico mail switch off
  (`hub bot mail <slug>`; bot.yaml's `outbound_send` only asks), `send` produces a draft and a note, always.
- Internal (@acme.example) versus external recipients. With the Tico mail switch on, which a person who manages
  the bot sets, three kinds of recipient need no per-message approval: an internal address, an address the
  person approved as a forward target in that switch (their other email, say), and the sender of the thread
  the bot is replying to (a reply, to that one person, nobody added; an outside Cc makes it something else).
  Any other external recipient needs one of: a standing allowance in the policy file (Influencer: recipient must be
  in its creator table, 10 per day, never the same address twice) or `--approval-issue`, which is a closed
  `owner:ana` `type:decision` GitHub Issue, a decided Tico `send` approval, or Ana's Tico message telling this bot
  to send. With the switch off, none of this applies and a send is a draft.
- With no registry (a Docker computer has no `registry/mail-policy.yaml`) the policy is built in: sending is on
  globally, internal domains are the ones the bot's mailbox and the team roster use (public providers such as
  gmail.com never count), the caps are 20 sends a day, one external recipient, no external Cc, no attachments, and
  the blocklist is empty. A `registry/mail-policy.yaml`, where there is one, replaces all of it.
- Caps, which apply to every send including those three kinds: sends per bot per day, per-recipient cooldown (14 days
  unless they wrote last; 0 in the built-in policy, because the forward address is written to again and again and
  the daily cap is the brake),
  one external recipient per message, no CC or BCC outside acme.example, no attachments unless the
  policy sets `defaults.allow_attachments: true` (it is off by default). A per-message approval (below) lifts the recipient
  count, the external Cc rule and the cooldown for that one message; never the daily cap, the
  blocklist, or `allow_attachments`.
- Attachments come only from the bot's own folder, never from a secret-looking file (`.env*`, keys, `credentials*`,
  `*secret*`, `*token*`, `secrets/`, `.ssh/` and the like), and a message carrying one always needs a per-message
  approval, even to the sender of the thread, a forward target or an internal address. That approval names the
  files: a Tico send approval whose payload lists the draft and each file's name, size and sha256 (`mail draft`
  prints the request), or a GitHub Issue naming each file and its sha256. The files are hashed again from the
  draft at send time; an owner's "send it" message does not cover attachments. See docs/mail.md, "Draft".
- Blocklist of addresses and domains bots never email (press, counterparties, anyone Ana
  lists), and a "Ana handles personally" list that forces `hub/needs-owner`. Both beat a forward address and a
  reply to a sender.

**Per-message approval.** Ana sometimes approves one
exact message: these words, to these humans, once. The approval task says so with a line in
its body, one block per message, and the block has to match the message exactly (external
addresses only; a mention elsewhere in the task, or a superset, does not count):

```
Send to: counsel@lawfirm.example
Cc: partner@lawfirm.example; associate@lawfirm.example
```

When `--approval-issue` is a full yes — a closed `owner:ana` `type:decision` GitHub Issue with a
matching block, a decided Tico `send` approval whose payload names every external address,
or a Tico message in which Ana told this bot to send — that message may carry more
than one external recipient and external Cc, the cooldown does not apply to it, lint L056
does not apply to it, the second reviewer runs but advises instead of blocking (its verdict
is still recorded and shown), and the switch being off is lifted for that
message only. The bot still has to declare `send` on the mailbox, the daily cap and
the blocklist still apply, and `mail draft` accepts the same `--approval-issue` so the
draft can be written with the external Cc in the first place. Everything else about the
bot stays draft-only.

**2. Lint (deterministic, each rule has an id, `mail lint` runs standalone).**
- Forbidden phrases from the team writing rules and the playbooks, for example "we guarantee"
  or other promises the team does not make (the list is configuration, not code).
- URLs: only acme.example hosts; no calendly.com, no go.acme.example/get-demo, no presigned S3 links;
  Influencer CTAs must carry the UTM pattern from its playbook.
- Placeholders and leaks: `{{`, `[NAME]`, `TODO`, `lorem`, key-shaped strings (`xoxb-`, `AKIA`,
  `sk-`), S3 URIs, Tico task numbers, anything internal-only.
- Structure: subject present and under 120 characters, body within bounds, reply keeps the
  thread and its recipient, signature present, one language.
- Scheduling: when times are offered there are at least two, in the future, chronological,
  weekdays in business hours, time zone spelled out, and each one is actually free on every
  calendar on ana@acme.example with a 30-minute buffer (checked live). A confirmation cannot be
  sent without the invite; `mail schedule` does both or neither.

**3. Reviewer (a second model, different vendor from the writer).**
The bots write with Codex (OpenAI). The CLI asks Grok 4.6 to review the draft against the
incoming thread, the bot's declared purpose, and the team writing rules. Grok runs through the
Grok Build, xAI's CLI, on this Mac (`grok -p '<prompt>' --output-format json -m grok-4.6
--max-turns 1 --disable-web-search --permission-mode plan`, Ana's xAI subscription, no key to
manage; the flags, plus a one-line preamble telling it to answer from the message alone
with no tools and no narration, hold the coding agent to one answer and no side effects). The backend is one setting,
`MAIL_REVIEWER`, default `grok:grok-4.6`, so it can point at xAI's API directly
(`xai:grok-4` with `XAI_API_KEY`) if Grok Build is flaky. Claude is not used here. Strict JSON back: ok, problems,
claims not supported by the thread, commitments (money, dates, discounts, legal, guarantees),
tone. Any commitment or unsupported claim fails the draft. The verdict is stored with the message.
If the reviewer is unreachable, `send` fails closed; `draft` proceeds with a warning flag.

**4. Execution.**
Idempotency key written to the database before every API call, so a retried run cannot
double-send. Exponential backoff on 429 and 5xx. `mail schedule` creates the event, then sends;
if the send fails it deletes the event. The sent-log row comes from the API response, and a
nightly reconcile against Gmail's Sent folder makes Ana's own sends count toward cooldowns.

**5. Audit.**
Every read of a thread body, label change, draft, send, and reviewer verdict goes to
`runtime/mail/audit.jsonl` and the database: bot, task, mailbox, recipient, rule results,
message id. `mail audit` prints it; Tico gets a Message bots tab later.

## Mailbox rules (deterministic, run before any model sees the mail)

The goal for the message bots is inbox zero with almost no input from Ana. Most of
that is not judgement, it is rules, and rules cost no tokens. `mail rules run --as <slug>` applies
`registry/mail-rules.yaml` to every new message before the bot reads anything:

```yaml
rules:
  - id: legal-risk-words                          # protections first: never_archive blocks every later archive
    when: { subject_or_body_matches: ["\\bsubpoena\\b", "\\bcease and desist\\b", "\\blawsuit\\b"] }
    do:   { label: hub/needs-owner, never_archive: true }
  - id: clio-bills                                # an exact sender Ana has settled: file, mark read, stop
    when: { to_matches: ["ana@acme.example"], from_matches: ["notifications@billing.example"], subject_matches: ["^A bill from "] }
    do:   { label: hub/notification, archive: true, mark_read: true, stop: true }
  - id: receipts
    when: { to_matches: ["ana@acme.example"], subject_matches: ["\\breceipt\\b", "\\bpayment successful\\b"] }
    do:   { label: hub/notification, archive: true, mark_read: true, stop: true }
  - id: payment-risk-words                        # money owed, not money already paid
    when: { subject_or_body_matches: ["\\bpast due\\b", "\\boverdue\\b", "\\bfinal notice\\b"] }
    do:   { label: hub/needs-owner, never_archive: true }
  - id: unsubscribe-is-marketing
    when: { has_unsubscribe_link: true }          # List-Unsubscribe header or an unsubscribe URL in the body
    do:   { label: hub/marketing, archive: true }
  - id: known-notification-senders
    when: { from_matches: ["noreply@", "no-reply@", "notifications@github.com", "*@calendly.com"] }
    do:   { label: hub/notification, archive: true }
  - id: internal-thread-ana-already-replied
    when: { from_domain: acme.example, last_reply_by: ana@acme.example }
    do:   { label: hub/handled/owner }
```

Conditions are a fixed vocabulary implemented in code (headers, sender patterns, domains, thread
state, regexes, attachment types, age). Actions are label, archive, mark-read, star,
`never_archive`, and `stop` (do not show this to the model at all). Rules run in order; the
first `stop` wins. Every rule hit is audited with the rule id so a wrong archive is one query
away and one label away from undone. The bot only sees what the rules did not settle,
which is the token saving Ana asked for. Ana and the message bots add rules over time; a bot
proposes a rule on its task, Ana merges it into the Tico file.

A proposed rule comes with a backtest, not just a fixture. `mail rules backtest --as <slug>
[--since 14d] [--rules candidate.yaml] [--limit 2000] [--format md|json]` replays the registry
(and the candidate file, if given) over every message in the window, all mail and not only the
inbox, and changes nothing: no labels, no unsubscribe, no seen row, no watermark, one audit row
of counts. It reports hits per rule with sample subjects; the candidate's diff against the
registry bucketed as newly archived, no longer archived, newly protected, no longer protected;
conflicts, meaning anything the rules would archive that carries `hub/needs-owner`,
`hub/drafted` or `hub/handled/owner`, sits on a thread with a hub draft or a scheduling action,
or comes from a human at acme.example; and the number that would still reach the model. The
conflicts list is the part to read before merging.

Day one for ana@acme.example: anything with an unsubscribe link is marketing and is archived
immediately. Same for legal@acme.example.

## Reading side

- Watermarks per mailbox and bot, so a run only sees what it has not triaged.
- Normalized JSON: from, to, date in America/Los_Angeles, thread id, plain-text body extracted
  from HTML, attachments listed with names and sizes but not downloaded unless asked, bodies
  truncated at 32 KB with a marker.
- Tico labels make the bot's state visible in Gmail, so Ana can see at a glance what a bot
  touched and can override by relabeling.
- A bot lists with `--format brief` and verifies with `inbox --untriaged`. Brief is four lines
  a message (id, date, sender; subject; thread, labels, unsubscribe, attachment count; the
  snippet) and no bodies; the bot opens the threads that need reading with `mail thread`. The
  full `md` read of a busy inbox once cost 948K input tokens in one run and truncated the
  answer after `--new` had already marked everything seen, so the untriaged half was lost.
- `inbox --untriaged` is `in:inbox` minus every message carrying any `hub/*` label. It is
  label-based and idempotent, touches neither `seen` nor the watermark, and cannot be combined
  with `--new` or `--since`. An empty answer means the pass is complete; a non-empty one lists
  exactly what is still owed, however the previous run ended.

## Persisted mail on Tico

The server never talks to Gmail. The `connectors` job (`python -m runner connectors`) syncs one
roster mailbox every `TICO_MAIL_SYNC_SECONDS` (default 10 minutes): `mail sync --as ana`
locally ( `--backfill 90d` when the server still has no messages), then `mail sync export` →
`POST /api/v2/connectors/mail/messages` → `mail sync ack` until the local outbox is empty.
The CLI never holds `HUB_TOKEN`; the runner posts. Same owner gate as calendar snapshots.
Bodies stay ≤32 KB; Tico keeps about 180 days (`TICO_MAIL_RETENTION_DAYS`) and a porter FTS
index. Humans browse that copy on the Message bots page (`GET /api/v2/mail/...`, [Message bots](mail.md)); SQL
access to `mail_*` is owner-only.

## Tests and rollout

- Unit tests for policy and lint with fixture emails, `python3 -m unittest`, and preflight runs
  them for any bot that declares gmail access.
- `mail doctor --e2e` uses a sandbox mailbox, `hub-test@acme.example`: draft, label, send to itself,
  create and delete a throwaway event. Run by preflight before any message bot goes active.
- Week one: all three bots read and draft only (mail switch off). Ana reviews
  drafts in Gmail and on the tasks. Week two, if the drafts are good: the mail switch on for
  Influencer with the 10-per-day creator allowance. A message bot sends only once its owner turns sending on
  (a person who manages it sets the mail switch in Settings → Bots → Mail sending or with `hub bot mail <slug> --send`; docs/mail.md), and then only to the
  three kinds of recipient above without a per-message approval; Ana can also just send the draft herself.

## Build order (about three working days of bot time)

1. Auth, `doctor`, reading, labels, database, audit.
2. `draft`, lint, reviewer, `--dry-run` everywhere, unit tests.
3. `send`, policy file, caps, approval-issue check, `slots`, `schedule`, reconcile.
4. `--e2e`, preflight hook, `docs/mail.md` for bots, `HUB_BOT` in the dispatcher.

## What Ana does (about 20 minutes)

1. Google Cloud: a project (suggest `acme-tico-hub`; don't reuse a personal OAuth client from another
   project). Enable the Gmail
   API and the Google Calendar API. Create a service account `company-hub-mail`, create a JSON
   key, save it as `~/tico-work/secrets/google-sa.json`, `chmod 600`. Note its client ID.
2. Google Workspace admin: Security > Access and data control > API controls > Domain-wide
   delegation > Add new: the client ID with scopes
   `https://www.googleapis.com/auth/gmail.modify` and `https://www.googleapis.com/auth/calendar`.
3. Make sure `legal@acme.example` is a real user mailbox, not a group or alias (delegation can only
   act as a user), and create `hub-test@acme.example` for the end-to-end check.
4. Answer the open questions below.

## Decisions

1. legal@acme.example is a real user mailbox; the legal message bot acts as it directly.
2. The message bot archives noise from day one, by rules first (unsubscribe link means marketing,
   archive at once), then by the model for what the rules miss.
3. Reviewer is Grok 4.6 through Grok Build (`MAIL_REVIEWER=grok:grok-4.6`), with xAI's API
   as the fallback; the bots write with Codex. Claude is not part of the mail path.
   Anything that runs a model should run on a subscription Ana already pays for (xAI, Codex,
   Claude Code), not a metered key, unless there is no other way.
4. CLI, not a daemon.
5. The purpose of both message bots is inbox zero with very little input from Ana; add linting
   and automatic rules over time rather than more model calls.
