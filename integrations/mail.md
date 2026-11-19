---
service: mail
title: Mail (Gmail and Google Calendar)
kind: mail
summary: Read, file, draft and (with approval) send email as a company mailbox, and read or book calendars, through the one shared mail service.
access: "`tico email` and `tico calendar` (the server's mail and calendar routes) — never the Gmail or Calendar API, an SDK or curl"
credentials:
  - GOOGLE_SA_KEY — the Google service account with domain-wide delegation (Tools > Settings > Email > Whole Workspace), or a person's own connected Google account (Tools > Settings > Email > Connect Google; docs/mail.md, Connect your Google account); both stay sealed on the Tico server, never on a computer or in a bot's environment
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

Two thin commands, `tico email` and `tico calendar`, over the server's routes; the Google code runs on the server
(`backend/gmail_live.py`, `backend/gcal_live.py`, `backend/mail_lib/`). Each runs as its caller (a bot's turn token, a
person's own Tico login) at that caller's own mailbox level, and names the mailbox. Who holds which level is Tico's
answer (`policies/access.md`), never a prompt's. Company calendar access is the standing fleet-wide exception: every bot may
read and create events on every roster address with `tico calendar` or the Hub tools, even when its Gmail access is
read-only or absent. Every read of a body and every label, archive, star, draft, send and calendar change is
recorded with who did it. A direct API call skips the checks and is a policy violation, not a shortcut (and would not
work: the key is not in the environment). Employee guide: [docs/mail.md](../docs/mail.md); the reasoning behind every
gate: [docs/mail-service.md](../docs/mail-service.md).

## What data it has

The mailboxes each caller may use (`tico email mailboxes`; for example `owner@example.com`, where an inbox bot can
read, draft and unsubscribe, and other bots read only). Messages, threads, labels, attachments (listed by name and
size, downloadable with `read`), Gmail search, drafts, the send audit, and every calendar a mailbox can see (all of
them treated as busy). Each person's inbox has a Message automation on the server ("<Name>'s inbox", trigger
`message`) that runs on every new message as it arrives: its rules file the obvious mail before a model reads it
(legal-risk and money-owed words are left for the person with `hub/needs-owner`, receipts and settled senders are
archived, marketing is archived or unsubscribed), its decide step sorts the rest, and its actions archive, label, leave
for the person, draft, send, forward or unsubscribe. A rule can also archive copies of another system's
notifications, so bots do not see them twice.

## How a bot uses it

Every command names the mailbox (a message bot's is the `Mailbox:` line in its instructions); there is no `--as`.

For appointments, prefer `tico calendar schedule` (the Hub tool is `tico_calendar_schedule`). It returns a receipt;
only `succeeded` confirms the event (`tico calendar status` reads it again). `unknown` means the provider result could
not be reconciled and must be inspected before any retry. This grant does not allow the bot to draft or send unrelated
email.

```bash
tico email mailboxes                                             # your mailboxes, level, verbs, connection and last check
tico email inbox owner@example.com [--q 'label:hub/needs-owner'] [--limit 20]   # newest first, headers and snippets
tico email search owner@example.com 'from:stripe.com newer_than:7d'
tico email thread owner@example.com 18f2c9a3b4d5e6f7             # a conversation with bodies; audited
tico email message owner@example.com 18f2c9a3b4d5e6f7
tico email attachments owner@example.com 18f2...                 # references a1, a2, ...
tico email attachment owner@example.com 18f2... a1 --out /private/dir/file.pdf
tico email label owner@example.com hub/needs-owner 18f2...       # unlabel, archive, unarchive, mark-read, mark-unread
tico email star owner@example.com 18f2...                        # unstar, important, not-important, spam, not-spam
tico email trash owner@example.com --thread 18f2...              # a whole thread; untrash brings it back
tico email search owner@example.com 'in:trash'                   # in:sent and in:spam too
tico email labels owner@example.com                              # every label with its counts
tico email label-create owner@example.com Clients/Acme           # label-rename, label-delete (your own labels only)
tico email original owner@example.com 18f2... [--out /tmp/message.eml]   # every header as received
tico email draft owner@example.com --to person@example.org --subject "..." --body-file out/draft.txt
tico email draft owner@example.com --reply-to 18f2... --body-file out/reply.txt
tico email drafts owner@example.com [--q 'to:person@example.org'] [--thread 18f2...]   # draft ids, whoever made them
tico email draft-show owner@example.com r-882...                 # one draft: headers, body, files (or its message id)
tico email draft-update owner@example.com r-882... --body-file out/draft.txt   # in place; what you leave out stays
tico email discard owner@example.com r-882...                    # a draft id or its message id; only an unsent draft
tico email send owner@example.com --draft r-882...               # queues it with Tico (prints send id, state, task)
tico email reply owner@example.com --thread 18f2... --body-file out/reply.txt   # draft, then queue it with Tico
tico email reply owner@example.com --thread 18f2... --all --body-file out/reply.txt   # to everyone on it
tico email forward owner@example.com 18f2... --to person@example.org --body "For you."   # with its files; queued
tico email lint owner@example.com --body-file out/draft.txt --subject "..." [--to addr]
tico email unsubscribe owner@example.com 18f2...                 # one RFC 8058 one-click request
tico email policy --bot <slug>                                   # your sending policy, as Tico answers it
tico email sends --mailbox owner@example.com [--pending]         # what was queued and what went out
tico email cancel <send-id>
tico calendar free --who human:owner --minutes 30 --within 5d
tico calendar list --who human:owner,human:teammate --from 2026-09-22 --to 2026-09-24
tico calendar add --title "Customer follow-up" --start 2026-09-22T09:00:00-07:00 --end 2026-09-22T09:30:00-07:00 --who teammate@example.com
tico calendar schedule --title "Intro" --start 2026-09-08T13:00:00-07:00 --end 2026-09-08T13:30:00-07:00 --attendee a@b.c
tico automation list --owner person:<id>                         # the inbox's Message automation
tico automation recent-runs --trigger message --range 24h        # what it did to new mail
```

Each filing command prints an undo line. Add `--json` for the API response. Every filing command takes message ids,
`--thread <id>` (repeatable: every message in the thread) or `--query`. Nothing deletes a message for good: trash and
spam are kept by Gmail for 30 days, and `untrash` and `not-spam` bring them back.

## Rules

- Read lets you list, search, open and download attachments. Read and write also lets you label, archive, mark read,
  star, draft, unsubscribe and queue sends, as far as the company's What Tico may do allows. A message bot's `can:`
  verbs only set the level it starts with: Read and write when they include `draft` or `send`, else Read.
- Calendar read/create is available to every bot for every address in `registry/people.yaml`
  without an `bot.yaml` mail grant. Scheduling is idempotent and audited. Invitation delivery is part of that calendar
  action, not a grant to the generic mail send command.
- A bot with Read on a mailbox (a review-only bot on `owner@example.com`, or a `read_only: true` entry) is refused
  filing, labels, mark-read, star, drafts and sends.
- Sending is set in Tico, never in `bot.yaml`: **Settings > Bots > <bot> > Email sending**, or the company or inbox
  sending policy. The modes are **On their own**, **Ask first** and **Never**.
- Every send goes through Tico's send queue, which decides it: the sending policy, blocked recipients, the people the
  owner handles personally, outside Cc and many outside recipients, the lint and a second reviewer, the org rules,
  bounces and approvals. An allowed send waits out an undo window, then goes; under Ask first a person approves it in
  Tico.
- A send Tico blocks leaves the draft in Gmail with Tico's reason. Read the reason; do not retry the same text or look
  for another route.
- Touch only the labels the hub owns (`hub/triaged/<slug>`, `hub/handled/<slug>`,
  `hub/drafted`, `hub/needs-owner`, `hub/marketing`, `hub/notification`, `hub/noise`) and
  Gmail's INBOX, UNREAD, STARRED. Everything else is the owner's filing.
- A task filed from an email sent to the mailbox may carry the sender, the subject, the message's
  own text, the message id and a thread link. Leave out other recipients (to and cc), quoted
  earlier history and attachment contents unless a human asks. Nothing from a mailbox goes
  into Slack.
- A new inbox rule is a change to the person's Message automation: `tico automation update <id> --file <json>
  --summary "..."`. A bot's change is a pending version the person accepts.
- A refusal is an answer: file a task for `the owner` naming the mailbox and why.

## Recipes

- Triage in order: the inbox's Message automation has already filed and sorted new mail
  (`tico automation recent-runs --trigger message --range 24h`); then `tico email inbox <address>` for what is left,
  then `tico email thread` on the one you are about to act on. What the rules settle costs no tokens.
- The completion note for a mail run is counts, not a transcript: how many new, how many the
  automation filed, how many you sorted, which ids carry `hub/needs-owner`.
- Offer times with `tico calendar free` and paste them into the draft as options; never confirm a time before the
  invite exists (the lint refuses confirmation language).
- A bot that reads the owner's mailbox and also has its own names `owner@example.com` in the commands for the owner's
  mail and its own address for its own.

## Gotchas

- Lists carry headers and snippets only; `thread` and `message` carry bodies, so read one thread at a time.
- `tico calendar schedule` books the exact future interval the calling bot supplies; pick it with
  `tico calendar free`, which counts every calendar the people named can see as busy.
- A calendar action in `pending` or `running` is not a created appointment. Report success only
  after `tico calendar status` returns `succeeded`; inspect `unknown` before retrying.
- A refused `send` is not an error: the answer is `blocked` with Tico's reason, and the draft stays in Drafts. Check
  the state (`pending`, `waiting_approval` or `blocked`), not the exit code.
- Running the same `draft` twice finds the same Gmail draft; changing a word makes a
  new one. To change a draft, use `draft-update`: it keeps the draft id. Sending the same draft twice sends nothing
  the second time.
- A draft's message in `thread` or `search` carries its `draft_id`; `drafts` lists them all. Canceling a queued send
  keeps its draft in Gmail; discard it yourself if it is not wanted.
- If `tico email mailboxes` shows the mailbox not connected or its last check failed, that is the owner's twenty
  minutes: file a task for `the owner` quoting the line and stop.
- An unsubscribe `accepted` means the sender said 2xx, not that they will never write again;
  it cancels a list, never a paid service.

## Learnings

What bots and people learn about this integration is added with `tico tool learn mail "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
