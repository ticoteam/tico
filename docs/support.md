# Contact support

A human using any Tico can write to the Tico team from the app. The message reaches Tico Team (the project's
own Tico), where the Support Agent works it, and the replies come back to the human's app. This page covers what a human
sees, what is sent and kept, and how the project team works the tickets. [PRIVACY.md](../PRIVACY.md) is the statement of what
is sent and kept.

## For a human

Help (the `?` at the bottom of the sidebar) has a short overview on the left (what Tico is, where to start, how to get
help, and links to the docs) and **Support** in the right rail, using the same sizing and resize controls as the rest of
the app. On a narrower screen, such as a phone, Support comes first: your
requests, **New request** and the composer are at the top without scrolling. A **Read the Tico docs** link sits right under
Support, and the short overview follows below it.

- **Your requests (N)** is a list of all N requests, newest activity first. Each row shows the first line of your first message
  as its title and the rest of it as a muted preview, a status badge
  (**Open**: waiting for Tico support; **Answered**: they replied; **Closed**; **Removed**: the team deleted it at HQ), when it
  last changed, and a dot when it has a reply you haven't read. **New request** starts another.
- Opening a request shows the conversation as bubbles, yours on the right and **Tico support**'s on the left, each with its
  time, and the composer below. It opens at the newest message; when replies arrive while you are reading further up, it
  doesn't move you. The back arrow returns to the list. An open request says **Delivered to Tico support** under it: a
  request is only listed once HQ has accepted it.
- In the composer, **Enter** sends and **Shift+Enter** adds a new line; the round arrow button (labelled Send) does the
  same. While a message is sending the composer is locked. If it fails, **Not sent** and the reason appear under the box
  and the message stays in it; nothing has reached Tico support. A sent message appears in the conversation at once.
- The trash icon in the corner of a conversation deletes it, after you confirm.
- With no requests yet, the rail is an **Ask Tico support** prompt with the composer. When support is off on the server
  (demo, rehearsal or `TICO_SUPPORT=off`), Help says so in place of the rail.

| Field | |
|---|---|
| Message | Required, up to 4000 characters. Plain text. |
| Reply email and install details | Expand to change the optional reply email and inclusion of version/install ID. From the desktop app, the app's own version goes with them, labelled apart from the server's. |
| @diagnostics | On by default for a new request. Uncheck to send none. Follow-up replies start with it off. |
| Computer performance | How much of each computer's performance history goes with @diagnostics: None, 1 day (the default), 7 days or 14 days. Computers and bots are relabelled as everywhere else in the bundle. |
| Review and edit | Inspect the exact JSON, remove sections, or edit fields. **Check edits** validates and redacts again; **Use diagnostics** attaches the reviewed result. |

The composer lists what Send will send and the destination. No ticket or attachment leaves for HQ until Send. If a capture
expires, the message stays intact and sending fails visibly; use Review and edit → Refresh capture to explicitly replace it.
If HQ refuses an attachment, remove it yourself to send just the message; it is never silently omitted. A reply can include
its own fresh, edited diagnostics without replacing the first report.

Diagnostics contain redacted versions, health, computer readiness and last heartbeat, capped server/runner/updater failures,
and capture coverage, and the [Beta features](#beta-features) your team has on or in use (`beta`, by id). From the desktop
app they also carry the app's version (`versions.app`) and the last 200 lines of its log
file (`logs.app`, [Desktop app](desktop.md)). The server groups adjacent repeated failures; failed HTTP requests record only their registered route
pattern and status in the bounded diagnostics buffer. Unexpected exceptions include their type and up to three application
module/line locations, never values, locals or a raw traceback. Browser errors contribute up to 20 groups containing only
kind, time, known script filename, line/column and count. No console text, request bodies, prompts or model output is captured.
These browser groups remain in memory until explicitly captured for an attachment.

The buffers are small and reset on process/page restart. A bundle says when server capture started, how many repeats were
grouped, and how many entries were evicted or omitted. Computer heartbeat time distinguishes old evidence from current health.
This is redacted technical evidence, not an anonymous ticket: the message and optional email/install ID may identify you.
The allowlist and examples are in [PRIVACY.md](../PRIVACY.md#support-diagnostics).

Replies appear in their conversation, raise a notice, and put the number of unread replies inside Ask: on **Your requests**
in the Ask header, and on **Tico support** under its **+**. Ask's own button carries no count. Opening a
conversation, or having it open in front of you when a reply lands, marks it read; there is no Mark read or Refresh button.
Replies are checked when you open Help, when the app regains focus or comes back into view, and on the timer below. Background
refresh preserves your draft, focus and scroll position. Deleting removes the request and all its attachments at HQ and its
local ticket record. Closed requests remain readable.

- Any signed-in human may file a ticket from the form. A personal API token, the Assistant acting for a human, and a bot
  may not file one directly; they report through a person's yes (below).
- Demo mode has no Contact support: nothing leaves a demo. Neither does a rehearsal (`TICO_REHEARSAL=1`), which reports "rehearsal" as the reason it is off.
- `TICO_SUPPORT=off` in the server's `.env` removes it from an install that must not phone out. `TICO_HQ_URL` points it at your
  own HQ (see [telemetry.md](telemetry.md)).

### Reach us from anywhere

- **Tico support** opens from any link to `#/support` (a chat reply's "Loop in Tico support", `#/help`): Help with a
  new request, ready to write; a `#/support/manage` link opens one asking for full management. **Loop in Support**,
  the link at the top right of the Ask panel, shows an open request's messages in that chat as blue-gray bubbles and
  writes your replies to it. It sends the chat's last 20
  turns, keys cut out, to that request, or to a new request sent at once when none is open ([The Assistant](assistant.md)).
  A new request sent that way has no diagnostics; **Attach diagnostics** under its line in the chat opens **Review and edit** for it. Empty lists carry no support line of their own.
- **Report this** appears on every error message (toast), on a failed run (a task's Runs section and Settings → History → Runs), on a red
  Health line and on a stuck bot. It opens a new request with what failed named in the message and attached as
  diagnostics in a `report` section: its kind (`toast`, `run`, `health`, `stuck`, `setup_check`), the words on screen,
  the run, check or bot it is about, and a `group` that names the same problem the same way each time (numbers and quoted
  names left out). The composer shows **Attached:** with a **Remove** button, and **Review and edit** shows the exact
  attachment before anything is sent. Every string in it goes through the same redactor as the rest of the diagnostics.
  When support is off, no Report this is offered.
- **Setup check** in Help runs the checks `tico-setup doctor` runs, from the server against its own address (HTTPS
  certificate, `/healthz`, the sign-in redirect for Google or Microsoft sign-in, the runner hostname), plus Health's red
  and yellow lines, and shows each with a plain fix. A Tico that serves only its own computer has no HTTPS to check.
  The server container, tunnel connector and `.env` permissions need a shell on the server, so for those it points to
  doctor. **Send to support** attaches the result to a new request, so support can ask you to "press Setup check and
  send" instead of asking questions. The check itself works with support off (`POST /api/v2/support/setup-check`, any
  signed-in person, once every 30 seconds each).
- When the installer or `tico-setup doctor` fails at a terminal, it asks **Send this to Tico support? [y/N]**. Only a
  `y` sends; with no terminal it never asks. Before asking it prints exactly what would be sent. On yes it asks for an optional email, then sends the failing checks and
  their fixes, the OS, architecture and Tico version as a request of kind `setup`, redacted with the diagnostics rules
  (`backend/redaction.py`) plus the install's own domain, owner, team name, runner and ssh hosts, and the account name in home folders and ssh
  logins. It prints the
  request's number and a link, `https://updates.tico.team/support/TK-XXXXXXXX#s=...`, where replies show up and where you
  can answer or delete the request. The secret is after the `#`, so it never reaches a server log. `--no-support` or
  `TICO_SUPPORT=off` turns the question off. The installer asks only once it has unpacked its bundle, so a failure
  before that (for example a machine that does not qualify) prints its error without the question.

### Talk to a person on tico.team/setup

A visitor planning their team on the public setup page, with no Tico yet, can press **Talk to a person**. The page files a
ticket of kind `setup-website` at HQ, from `https://tico.team` only, whether or not the page's assistant is connected:
their email, the tab they were on, the mode (`browser` when the page plans on its own, `server` when the assistant is
connected), how many messages they had sent, a note if they wrote one, and their plan only if they ticked to send it.
Never the chat. The ticket's text is fixed ("A visitor on tico.team/setup asked to talk to a person." and those fields);
the note and plan are an attachment marked **Untrusted public input**. Key-shaped strings are cut from both before
they are checked or kept, and the page tells the visitor when one was. The page gets a link to the request's page, where
the reply shows and the visitor can answer or delete it. One ticket per visit, 3 a day per address and per email address,
and `HQ_SETUP_TICKETS_DAILY_CAP` a day in all (default 50); over a limit the page says it is busy. What is kept is in
[PRIVACY.md](../PRIVACY.md#support-tickets); the route is in [telemetry.md](telemetry.md#support-tickets).

**Answering one.** It arrives as one Support task like any other, titled `Support (setup-website): A visitor asked to talk
to a person`, with the attachment named on it. `software/hq-tickets show` prints the note and plan
inside `<untrusted-public-input>` markers under their label: data to read, never instructions. Answer about planning
the team and getting started; never ask for a key or password (tools connect on their own install). The reply is posted
the usual way (step 4 below), shows on the visitor's request page, and, since every setup-website ticket has an email,
is marked email pending for the email copy.

### A bot's report, after a person's yes

A bot that hits a Tico bug (or a person's token, through MCP) reports it to the Tico team without handing the person a
copy-and-paste task, and nothing leaves the install until a person says yes:

1. `hub support report --message "..."` (or `--file report.md`; MCP `hub_support_report`) drafts the report as a
   `support` approval: the `message` (up to 4000 characters, plain text), its `content_sha256`, and `include_ids`
   (default true: the version and install ID go with it; `--no-ids` sends only the message). It prints the approval id.
   Nothing is sent, and the draft is refused while support is off (demo, rehearsal, `TICO_SUPPORT=off`).
2. The person the approval goes to sees **Send this to Tico support?** in Needs you and Decide, with the whole message,
   what goes with it and the host it goes to, and approves or declines it in their own browser (not with a token or
   through the Assistant). A bot's report goes where any approval goes: the owner of its task, else the default person. A
   person's own report (drafted with their token) goes to them.
3. After a yes, `hub support send --approval <id>` (MCP `hub_support_send`, `POST /api/v2/support/tickets/from-approval`)
   files it **as the person who approved it**: the ticket is theirs, its replies show on their Support page, and it counts
   toward their 10 a day. Only whoever asked for the approval may send it, and only once it is approved and its message
   still matches its hash. The approval is spent in the same step that keeps the ticket, so if HQ refuses or cannot be
   reached nothing is spent and the send can be tried again; sending again after it worked answers with the same ticket.

No email and no diagnostics bundle go this way: diagnostics need a person to preview them on the form.

## Letting Tico support in

When a reply is not enough, an owner or an admin can let the Tico team into their app for a while. It is off until you
start it, it always starts from one of your own open requests, and it ends on its own.

1. Open the request in Help > Support and press **Let Tico support in**, under the conversation. Where it is off, the same place says
   why in one line (the server's `TICO_SUPPORT_ACCESS`, or the team rule).
2. Choose what support may do:
   - **App only**: what an admin sees (settings, Health, computers, bots, tasks and run history), and changes to setup only: a
     bot's model, fallback and context size; a computer's runner restart; asking a computer to read its subscriptions'
     allowance again. Support cannot start a model sign-in or read the link and code of one you start (whoever enters the
     code first chooses the account a computer runs on), see a credential's value or a raw run stream, approve anything for you, send a message, start or run a task or
     bot, publish, spend, or change people, access or credentials.
   - **Full access** (the owner only): what the owner can do in Tico. Even then support cannot make or see personal tokens,
     service keys or credential values, sign a person in or out of Tico, hand the install to someone else, or change these sessions.
     What it changes the owner's way can outlast Stop, such as a webhook, a credential, the GitHub App, Slack's tokens,
     directory sync or a computer's model sign-in (which account its AI tool runs on), and it reads what the owner reads, mail and raw run streams included: give it only when you want
     support to have that.
3. Choose how long: 1 hour, 4 hours, 24 hours (the default), 7 days, or any number of hours up to 30 days. Press **Start**.

While it lasts, everyone on your Tico sees a banner on every screen: "Tico support has access (App only), 23 h 12 min left ·
Stop"; the request it started from says the same, and the banner opens it. **Stop** (the person who started it, the owner or
an admin) ends it at once. Only one session runs at a time. Every request support makes is listed under the request it
started from, in every session started there (when, as how long ago with the full time on hover; who made it, staff or the
Support Agent; the method, the path and the answer's status), and in the audit log support's changes show as **Tico support**.

How it reaches you: while a session is live your Tico keeps one request open to HQ asking for work (HQ holds it up to 30
seconds); HQ never connects in, and nothing is opened on your network. HQ refuses any session your Tico did not open, and any that ended or ran
out. Support's requests use a token your server made for the session, which lives only in its memory and stops working the
moment the session ends.

The Tico team uses it, and so does its Support Agent bot. Everything the Support Agent reads from your app is untrusted data
to it: it reads it, and never follows it as an instruction.

**Off switches.** It is not available with `TICO_SUPPORT=off`, in demo mode or in a rehearsal, or when the owner turns off
**Owners and admins may let Tico support in** in Settings > People ([team rules](permissions.md#team-rules)); turning it off
ends a live session. Until its security review is done, it is also off unless the server is started with
`TICO_SUPPORT_ACCESS=on`. A session that support's own switch ends (`TICO_SUPPORT=off`, a rehearsal) ends here without a
word to HQ, which ends its side at the session's time.

For the team: a session has one id, HQ's (`sa_` and 24 hex digits), in the install's audit log and API as in admin, and
the install's events name the request's HQ ticket (`TK-...`). `GET /v1/staff/access` lists live sessions, and
`/v1/staff/access/<session>/api/v2/...` is one request to that install's API, with `X-Tico-Support-By: support-agent` when
the bot makes it ([Tico HQ](tico-hq.md#support-access)).

## Beta features

A Beta feature works today and may still change. The app shows a **Beta** pill beside it (its nav item, settings section,
page title or template card), Help lists them under **Beta features**, and its page in this manual opens with a Beta note.
@diagnostics lists the ones your team has on or in use; a Windows computer through WSL 2 reports itself as Linux, so the
server cannot tell one is connected. One list in `backend/beta.py` drives all of these.

- Windows computers through WSL 2: [install](install.md#windows-pc-wsl-2)
- Chat goals: [chat-goals](chat-goals.md)
- Message bots: mail and calendar: [messaging](messaging.md), [mail](mail.md), [mail-service](mail-service.md)
- Slack gateway: [slack-gateway](slack-gateway.md)
- Listening: [listening](listening.md)
- Live Meetings API and Recorder: [live-meetings-api](live-meetings-api.md)
- Computer screen (View and Control): [install](install.md#screen), [permissions](permissions.md#a-computers-screen)
- External agents: Hermes, OpenClaw, Grok Bot, Dots, Muse sync: [hermes-agents](hermes-agents.md), [openclaw-agents](openclaw-agents.md), [external-agent-sync](external-agent-sync.md)
- Behaviour replay and the computer Performance page: [releasing](releasing.md#behaviour-replay), [permissions](permissions.md#a-computers-performance)
- The Mac desktop app: [desktop](desktop.md)

## What is sent and kept

The request is `POST <TICO_HQ_URL>/v1/support` with `message`, and, only if present, `email`, `version`, `install_id`, `app_version` (the
desktop app's, when sent from it) and `diagnostics` (up to 256 KB). The answer is a ticket ID and a **secret for that ticket alone**. Your Tico keeps the ticket, the ID and the secret; the secret
never reaches a browser. HQ keeps the message, the email, the version and install ID and diagnostics if sent, a hash of the secret, the status,
the times, and the thread. It keeps no IP address and no log of a request. Tickets are **kept until someone deletes them**: you,
with **Delete**, or the team when you ask (put "delete this request" in the ticket, or open an issue). The 13 month rule is for
the anonymous install rows only. The reference for HQ's routes is in [telemetry.md](telemetry.md#support-tickets).

Your Tico asks HQ about your tickets only when the app asks: when you open Help, when the app regains focus or comes back
into view, and every 30 seconds while the support rail is open, or about every 5 minutes elsewhere while the app is open
and you have a ticket that is not closed. It has no timer of its own. Each time, it asks about a ticket at most once every
30 seconds when it was written on in the last hour or is the one you have open, every 5 minutes when it is open or
answered but quiet, and every 6 hours when it is closed, the most recent first.

## How the project team works tickets

Everything below is how the Tico project's own Support Agent works tickets at HQ.

1. **HQ** stores the ticket. The team's key, `HQ_STAFF_KEY` (24+ characters, set on HQ), opens the staff routes; without it
   they answer 404. A human's ticket secret opens that one ticket and nothing else.
2. **A watcher notices, with no model.** The Support Agent's runner runs `software/hq-tickets watch` every 5 minutes
   ([watchers.md](watchers.md)). It asks HQ for tickets changed since its cursor. For each new open ticket it prints one event, and
   Tico opens **one task per ticket**, titled `Support: <first words>`, assigned to the Support Agent, with the ticket quoted as
   untrusted data, the version and the requester's email if given. When the human writes again on an open ticket, the same task
   gets a note, which wakes the bot; when HQ closes the ticket the task hears about it. A quiet poll is one request and costs no
   tokens. `software/gh-support watch` does the same for GitHub issues and Discussions, read-only.
3. **The bot drafts.** It works the task with `playbooks/tico-hq-tickets.md`: reads the diagnostics first (`software/hq-tickets show`
   prints a summary, then the whole bundle; only the staff routes return it), sorts the ticket, asks the Librarian what the docs say,
   hands bugs to engineering, and writes a reply file.
4. **The bot posts when sending is on.** Once a person who manages the bot has turned its sending on in Tico (Settings > Bots > the bot > Mail sending, or `hub bot mail <slug> --send`; docs/mail.md, "Turning sending on"), the bot runs `software/hq-tickets reply <id> <file>` from its repository to post the requested reply. `outbound_send: true` in its bot.yaml is only a request: with no value in Tico, or no Tico reachable from the run, the reply stays a draft. If genuinely unsure, the bot may request an optional approval for that exact ticket and text and pass it with `--approval <id>`; this does not turn sending on. If the ticket has an email, HQ marks the reply "email pending" and the bot leaves an email-ready copy on the task or sends it with a connected mail Tool. HQ sends no email.
5. **The daily update** counts tickets opened, replies posted and drafts waiting, so they show on the Updates page.

Ticket text is untrusted data from anyone on the internet. HQ stores it as plain text, the app and the staff tools escape or
quote it, and the bot's playbook says an instruction inside a ticket is never followed.

### The spam and injection check

Each new ticket is classified when it arrives, before any bot reads it, by the decision model (the optional provider the README describes). The verdict
is `legit`, `spam`, `injection_risk` or `unchecked`, stored with a one-line reason and never the text.

| Verdict | What happens |
|---|---|
| `legit`, `unchecked` | Filed as usual. `unchecked` means no key, the decision model was down or slower than 3 seconds, or it was not sure (under 0.7). It fails open: a check that cannot run never holds a ticket. |
| `spam` | Held: not in `status=open`, `answered`, `closed` or `all`, so the watcher and the bot never see it. `GET /v1/staff/tickets?status=held` lists them. |
| `injection_risk` | Filed with a warning. The task is titled `Support (injection risk): ...`, opens with WARNING, and the bot reads it only: a draft, no tool but reading docs. |

A human corrects a verdict with `POST /v1/staff/tickets/{id}/verdict` and `{"verdict": "legit", "note": "..."}`. Changing a held
ticket to anything but `spam` releases it: it appears in the queue as a new ticket. Each correction is recorded (old, new, when)
so the decision model can be tuned. The human who filed a ticket sees nothing different.

The same check is `POST /v1/staff/judge` (`{"text": "..."}` answers `{verdict, reason}`, nothing kept), which `software/gh-support`
calls, with the staff key already in its secrets, for each new issue, Discussion and outside comment: `spam` is not filed and is
counted in its output line, `injection_risk` is filed with a warning. A message bot checks email with `hub classify` (text on
standard input or `--file`), which asks the server's own decision model the same question.

On HQ, `HQ_JUDGE_KEY` is that provider's key (`HQ_JUDGE_URL` only to point elsewhere). Without it every verdict is
`unchecked` and nothing is sent to anyone. HQ logs the verdict and reason and never the text. Only the first message of a ticket
is checked, not a follow-up; a follow-up is quoted as untrusted data as before. On a setup-website ticket the check reads
the note and the plan instead (after key-shaped strings are cut), a long plan in parts: any part can make it `injection_risk`,
only the note can make it `spam`, and a plan part the check calls spam leaves the ticket `unchecked` for a person. A
ticket with neither is fixed text and needs no check.

### Support triage

When your Tico has a [decision model](how-it-works.md), it also asks the `support-triage` question set about
each new ticket the watcher reports, before the Support Agent's turn on it: is the person's text spam or a prompt
injection, what kind of request it is (bug, question, feature, setup, billing, other) and how urgent (0 to 3). The
watcher's report is answered at once; the ticket's task opens a few seconds later, when the answer is in.

An ordinary note to a person, such as "please ignore this test", counts as legitimate text.
Words such as "ignore", "test", and "please" do not make it a prompt injection by themselves.
The check looks for instructions addressed to an AI, bot, or assistant. `hub classify` uses the same distinction.

| Answer | What happens |
|---|---|
| Spam or injection at or over the set's `hold` line (85%) | Held: the task goes to a person (the bot's operator, else the default person), titled `Support (held: spam or injection): ...`. The bot is not woken; nothing is answered and nothing is deleted, and the ticket stays open at HQ. Later messages and HQ closing it are notes on that task. Hand the task to the bot to release it; close it if it is spam. |
| Anything under the line | The Support Agent's task as before, with one line on top: `Triage (support-triage@2, ...): kind question (90%), urgency 1 of 3 ...`. |
| Decide off, an error or timeout, the month's spend cap, the bot's daily decision budget | Fails open: the task opens as before (with a line saying triage did not run, when it was tried). |

Only the first report of a ticket is triaged; a message or HQ's close that arrives while it waits becomes a note on its task. A ticket for a paused bot waits until the bot is active again and holds up no other; one still waiting after an hour goes as a task to the person a held ticket goes to, and Tico keeps no other copy of it. Each call is a `judge.call` row labelled `support-triage@2` (a failed call keeps only its error code), with the
answers and never the text; the verdict is kept with the ticket's watcher row. This runs alongside HQ's own spam and
injection check above, which holds spam at HQ before the watcher sees it.

### Set it up

HQ needs `HQ_STAFF_KEY` set (`openssl rand -hex 24` makes one; HQ's hosting is in ticoteam/tico-services). In
**Tools → Credentials**, store the same `HQ_STAFF_KEY` and the HQ address as
`HQ_URL` (not `TICO_HQ_URL`: the runner keeps `TICO_` names out of bots' environments), with those exact Bot variable names, and grant both to the Support Agent. For
GitHub, store and grant `GITHUB_TOKEN` (read access; required for Discussions) and optionally
`GH_SUPPORT_REPOS` (`ticoteam/tico`). A Credential card is the other way to store and grant them.
Legacy files can be imported for migration; a new watcher does not load `secrets/support.env`.

Without `HQ_STAFF_KEY` the watcher does nothing. To watch GitHub, list the repositories in `config/github.yaml` or
`GH_SUPPORT_REPOS`. Check Settings > Health for a "Watchers" line: it appears only when a watcher fails or has stopped.

### By hand

**Fixed in.** Link a ticket to the ticoteam/tico issue or pull request that will fix it (`software/hq-tickets link TK-XXXXXXXX
'#712'`). When a release ships it, HQ posts "Fixed in vX, update here" on the ticket by itself ([tico-hq.md](tico-hq.md#messages-known-issues-and-fixed-in-notices)),
and the human sees it in Help under Your requests. HQ also keeps the known issues by version that Help shows.

`software/hq-tickets list`, `show TK-XXXXXXXX`; or with curl and the key in a header (`Authorization: Bearer $HQ_STAFF_KEY`):
`GET /v1/staff/tickets?status=open`, `POST /v1/staff/tickets/{id}/reply` with `{"body": "..."}`, `POST .../status` with
`{"status": "closed"}`, `DELETE /v1/staff/tickets/{id}` to delete on request, `GET /v1/staff/tickets?status=held` for what the
spam check held, `POST /v1/staff/tickets/{id}/verdict` to correct it.

## Compatibility

An HQ from before `app_version` refuses it as an unknown field; the server then sends the ticket again without it, so a
request from the app never fails for it. Deploy the HQ update before the client update to enable follow-up attachments. Older HQs reject that optional field; the app
shows the failure and keeps the draft, allowing an explicit message-only retry. The attachment limit remains 256 KiB. There is
no new continuous export, persistent log journal, or verbose logging mode.
