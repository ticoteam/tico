---
service: mail
title: Mail (Gmail and Google Calendar)
kind: mail
summary: Read, file, draft and (with approval) send email as a company mailbox, and read or book calendars, through the one shared mail service.
access: "`$HUB_DIR/scripts/mail.sh` (connectors/mail) — never the Gmail or Calendar API, an SDK or curl"
credentials:
  - GOOGLE_SA_KEY — the Google service account with domain-wide delegation, key file secrets/google-sa.json on the runner computer; the mail service reads it by path and it is never loaded into a bot's environment
declared_as: |
  - service: gmail
    identity: owner@example.com          # the mailbox it acts as
    can: [read, draft]                # read | draft | send | unsubscribe | schedule
    read_only: true                   # optional: read messages and attachments only, no filing
    env: GOOGLE_SA_KEY
  - service: google-calendar
    identity: owner@example.com
    can: [read]                       # add draft/schedule for slots and bookings
    env: GOOGLE_SA_KEY
writes: approval
owner: owner
aliases: [gmail, google-calendar]
---

## What it is

One CLI, `mail`, in the hub at `connectors/mail/`, run as `$HUB_DIR/scripts/mail.sh`. One Google
service account acts as every mailbox. Gmail authority comes from the `tools:` block in
`bot.yaml` (`policies/access.md`), never from a prompt. Company calendar access is the
standing fleet-wide exception: every bot may read and create events on every roster address with
the Hub tools or the audited `mail calendar` commands, even when its Gmail access is read-only or
absent. The Hub queues and audits provider actions; the connector on the runner computer performs them
without exposing the Google credential. Every read of a body and every label, archive, star,
draft, send and calendar change is written to an audit log with the bot's slug. A direct API call
skips the checks and is a policy violation, not a shortcut (and would not work: the key is not in
the environment). Employee guide:
[docs/mail.md](../docs/mail.md); the reasoning behind every gate:
[docs/mail-service.md](../docs/mail-service.md).

## What data it has

The mailboxes declared in each bot's `bot.yaml` (for example `owner@example.com`, where an
inbox bot can read, draft, unsubscribe and schedule, and other bots read only).
Messages, threads, labels, attachments (listed by name and size, downloadable with `read`),
Gmail search, drafts the hub wrote, the sent log, and every calendar a mailbox can see (all of
them treated as busy). The rules in `registry/mail-rules.yaml` file the obvious mail before a
model reads it: legal-risk and money-owed words get `hub/needs-owner`, receipts and settled
senders are archived, unsubscribe-link mail is marketing, machine senders get
`hub/notification`. A rule can also archive
copies of another system's notifications, so bots do not see them twice.

## How a bot uses it

Inside a turn `HUB_BOT` and `HUB_DIR` are set, so the tool knows who you are; `--as <slug>`
is for running it by hand. Add `--mailbox <address>` when you hold more than one.

For appointments, prefer the Hub tools. `hub_calendar_schedule` returns a durable action, not
instant success. Poll `hub_calendar_status`; only `succeeded` confirms the event. `unknown` means
the provider result could not be reconciled and must be inspected before any retry. This grant
does not allow the bot to draft or send unrelated email.

```bash
$HUB_DIR/scripts/mail.sh doctor                                      # is mail set up at all
$HUB_DIR/scripts/mail.sh whoami                                      # your mailboxes and verbs
$HUB_DIR/scripts/mail.sh rules run --dry-run                         # what the rules would do; drop --dry-run to do it
$HUB_DIR/scripts/mail.sh rules explain 18f2c9a3b4d5e6f7
$HUB_DIR/scripts/mail.sh inbox --untriaged --format brief            # inbox mail with no hub/* label: the list you work
$HUB_DIR/scripts/mail.sh inbox --new --since 24h                     # unseen in the last day (marks it seen)
$HUB_DIR/scripts/mail.sh thread 18f2c9a3b4d5e6f7 --format brief      # a conversation; md when drafting a reply
$HUB_DIR/scripts/mail.sh search "from:stripe.com newer_than:7d"      # any Gmail query, max 50
$HUB_DIR/scripts/mail.sh attachments 18f2... --format json           # references a1, a2, ...
$HUB_DIR/scripts/mail.sh attachment 18f2... a1 --out /private/dir/file.pdf
$HUB_DIR/scripts/mail.sh label add 18f2... hub/needs-owner           # label remove, archive, mark-read, star, triaged
$HUB_DIR/scripts/mail.sh draft --to person@example.org --subject "..." --body-file out/draft.txt --issue 128
$HUB_DIR/scripts/mail.sh draft --reply-to 18f2... --body-file out/reply.txt --issue 128 --attach out/letter.pdf   # repeat --attach; 25 MB in all
$HUB_DIR/scripts/mail.sh discard r-882... --issue 128                # only an unsent draft you made
$HUB_DIR/scripts/mail.sh send --draft r-882... --issue 128 [--approval-issue 131]
$HUB_DIR/scripts/mail.sh reply --thread 18f2... --body-file out/reply.txt --issue 128   # draft, then send if allowed
$HUB_DIR/scripts/mail.sh lint --body-file out/draft.txt --subject "..." [--to addr]
$HUB_DIR/scripts/mail.sh review --body-file out/reply.txt --thread 18f2...
$HUB_DIR/scripts/mail.sh slots --for owner@example.com --n 2 --minutes 30
$HUB_DIR/scripts/mail.sh upcoming --for owner@example.com --hours 24
$HUB_DIR/scripts/mail.sh calendar list --for teammate@example.com --hours 48
$HUB_DIR/scripts/mail.sh calendar add --for teammate@example.com --start 2026-09-22T09:00:00-07:00 --minutes 30 --summary "Customer follow-up"
$HUB_DIR/scripts/mail.sh calendar get --for teammate@example.com --event <id>
$HUB_DIR/scripts/mail.sh schedule --thread 18f2... --slot 2026-09-08T13:00:00-07:00 --minutes 30 --attendee a@b.c --body-file out/confirm.txt --issue 128
$HUB_DIR/scripts/mail.sh unsubscribe 18f2... --dry-run --json        # RFC 8058 one-click; needs the unsubscribe verb
$HUB_DIR/scripts/mail.sh scheduling offer|book ...                   # the inbox bot's scoped workflow only
$HUB_DIR/scripts/mail.sh policy show                                 # what you may send, to whom, how often
$HUB_DIR/scripts/mail.sh audit --since 24h                           # everything mail did
$HUB_DIR/scripts/mail.sh sent-log --since 7d                         # what actually went out
```

Every write command takes `--dry-run`. Exit codes: `0` fine, `1` something broke, `2` a hub
policy refused you. Add `--json` for structured output.

## Rules

- `read` also allows `label`, `archive`, `mark-read`, `star` and `triaged` on your own mailbox
  and downloading its attachments. `draft` is needed for `draft`, `reply` and `schedule`;
  `send` on top of that for anything to leave; `unsubscribe` and `schedule` are their own verbs.
- Calendar read/create is available to every bot for every address in `registry/people.yaml`
  without an `bot.yaml` mail grant. Hub scheduling is idempotent, connector-queued and
  audited; the direct `mail calendar` commands are also audited and deterministic. Invitation
  delivery is part of that calendar action, not a grant to the generic mail send command.
- A `read_only: true` entry (a review-only bot on `owner@example.com`) refuses filing, labels, rules,
  mark-read, star, drafts and sends whatever else is listed.
- Every write goes through five gates in order: policy (`registry/mail-policy.yaml` plus your
  `bot.yaml`), lint, a second model (a different vendor), execution, audit. A gate that
  says no leaves a draft in Gmail with the reason. Read the reason; do not retry the same text
  or look for another route.
- A send to anyone outside `@example.com` needs `outbound_send: true` and one of: a standing
  allowance in `registry/mail-policy.yaml` that lists the recipient, or a per-message approval
  (`--approval-issue`: a matching GitHub Issue, a decided Tico send approval, or the owner's
  Tico message telling you to send). With the flag false every send is a draft unless that
  approval is a full yes.
- Caps: sends per employee per day, a 14-day per-recipient cooldown, one external recipient per
  message, no Cc or Bcc outside example.com, no attachments unless the policy allows them, and a
  blocklist of addresses bots never email.
- Touch only the labels the hub owns (`hub/triaged/<slug>`, `hub/handled/<slug>`,
  `hub/drafted`, `hub/needs-owner`, `hub/marketing`, `hub/notification`, `hub/noise`) and
  Gmail's INBOX, UNREAD, STARRED. Everything else is the owner's filing.
- A task filed from an email sent to the mailbox may carry the sender, the subject, the message's
  own text, the message id and a thread link. Leave out other recipients (to and cc), quoted
  earlier history and attachment contents unless a human asks. Nothing from a mailbox goes
  into Slack.
- A new inbox rule is a hub change: prove it with `rules backtest --since 14d --rules <file>`
  and one fixture, then open a PR against `registry/mail-rules.yaml`; a human merges it.
- A refusal is an answer: file a task for `the owner` naming the mailbox and why.

## Recipes

- Triage in order: `rules run`, then `inbox --untriaged --format brief`, then `thread <id>
  --format md` on the one you are about to act on. What the rules settle costs no tokens.
- The completion note for a mail run is counts, not a transcript: how many new, how many the
  rules filed, how many you triaged, which ids carry `hub/needs-owner`, and the `audit` line.
- Offer times with `slots` and paste its lines (`Tue Sep 8, 1:00–1:30pm PT`) into the draft;
  never confirm a time before the invite exists (lint refuses confirmation language without
  `--slot`).
- A bot that reads the owner's mailbox and also has its own uses `--as <slug> --mailbox owner@example.com`; its own mailbox is
  its `default_mailbox`.

## Gotchas

- `--format md` prints whole bodies (cut at 32 KB); use `brief` on any list and `md` on one
  thread.
- Scheduling on the owner's behalf: 30-minute meetings, at least 20 hours' notice, weekdays
  excluding holidays, business hours in the owner's timezone, 30-minute buffers both sides,
  every calendar on the mailbox counts as busy. Those are the scoped inbox workflow's automatic
  slot-selection rules; an explicit `hub_calendar_schedule` call uses the exact future interval
  the calling bot supplies.
- A calendar action in `pending` or `running` is not a created appointment. Report success only
  after `hub_calendar_status` returns `succeeded`; inspect `unknown` before retrying.
- A refused `send` is a downgrade, not an error: `{"ok": true, "sent": false, "downgraded":
  "draft", "gate": ..., "reason": ...}` and exit 0. Check `sent`, not the exit code.
- Running the same `draft` command twice updates the same Gmail draft; changing a word makes a
  new one. Sending the same draft twice sends nothing the second time.
- If `doctor` says the key is missing or delegation is not granted, that is the owner's twenty
  minutes: file a task for `the owner` quoting the line and stop.
- An unsubscribe `accepted` means the sender said 2xx, not that they will never write again;
  it cancels a list, never a paid service.

## Learnings

What bots and people learn about this integration is added with `hub tool learn mail "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
