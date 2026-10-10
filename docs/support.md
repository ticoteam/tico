# Contact support

A human using any Tico can write to the Tico team from the app. The message reaches Tico Team (the project's
own Tico), where the Support Agent works it, and the replies come back to the human's app. This page covers what a human
sees, what is sent and kept, and how the project team works the tickets. [PRIVACY.md](../PRIVACY.md) is the statement of what
is sent and kept.

## For a human

Help (the `?` at the bottom of the sidebar) has the app overview on the left and **Support** in the right rail, using the
same sizing and resize controls as the rest of the app. On a narrow screen, Support follows the overview; the top Support
link jumps there. **Your requests** selects an existing conversation; **New request** starts another.

| Field | |
|---|---|
| Message | Required, up to 4000 characters. Plain text. |
| Reply email and install details | Expand to change the optional reply email and inclusion of version/install ID. |
| @diagnostics | On by default for a new request. Uncheck to send none. Follow-up replies start with it off. |
| Review and edit | Inspect the exact JSON, remove sections, or edit fields. **Check edits** validates and redacts again; **Use diagnostics** attaches the reviewed result. |

The composer lists what Send will send and the destination. No ticket or attachment leaves for HQ until Send. If a capture
expires, the message stays intact and sending fails visibly; use Review and edit → Refresh capture to explicitly replace it.
If HQ refuses an attachment, remove it yourself to send just the message; it is never silently omitted. A reply can include
its own fresh, edited diagnostics without replacing the first report.

Diagnostics contain redacted versions, health, computer readiness and last heartbeat, capped server/runner/updater failures,
and capture coverage. The server groups adjacent repeated failures; failed HTTP requests record only their registered route
pattern and status in the bounded diagnostics buffer. Unexpected exceptions include their type and up to three application
module/line locations, never values, locals or a raw traceback. Browser errors contribute up to 20 groups containing only
kind, time, known script filename, line/column and count. No console text, request bodies, prompts or model output is captured.
These browser groups remain in memory until explicitly captured for an attachment.

The buffers are small and reset on process/page restart. A bundle says when server capture started, how many repeats were
grouped, and how many entries were evicted or omitted. Computer heartbeat time distinguishes old evidence from current health.
This is redacted technical evidence, not an anonymous ticket: the message and optional email/install ID may identify you.
The allowlist and examples are in [PRIVACY.md](../PRIVACY.md#support-diagnostics).

Replies appear in the selected thread and raise the existing notice and sidebar dot. Selecting a request or pressing
**Mark read** clears its unread state. Background refresh preserves your draft, focus and scroll position. **Delete** removes
the request and all its attachments at HQ and its local ticket record. Closed requests remain readable.

- Any signed-in human may file a ticket. A personal API token, the Assistant acting for a human, and a bot may not.
- Demo mode has no Contact support: nothing leaves a demo. Neither does a rehearsal (`TICO_REHEARSAL=1`), which reports "rehearsal" as the reason it is off.
- `TICO_SUPPORT=off` in the server's `.env` removes it from an install that must not phone out. `TICO_HQ_URL` points it at your
  own HQ (see [telemetry.md](telemetry.md)).

## What is sent and kept

The request is `POST <TICO_HQ_URL>/v1/support` with `message`, and, only if present, `email`, `version`, `install_id` and
`diagnostics` (up to 256 KB). The answer is a ticket ID and a **secret for that ticket alone**. Your Tico keeps the ticket, the ID and the secret; the secret
never reaches a browser. HQ keeps the message, the email, the version and install ID and diagnostics if sent, a hash of the secret, the status,
the times, and the thread. It keeps no IP address and no log of a request. Tickets are **kept until someone deletes them**: you,
with **Delete**, or the team when you ask (put "delete this request" in the ticket, or open an issue). The 13 month rule is for
the anonymous install rows only. The reference for HQ's routes is in [telemetry.md](telemetry.md#support-tickets).

Your Tico asks HQ about your tickets only when the app asks: when you open Help, and every 30 seconds while the support rail is open, or about every 5 minutes elsewhere while the app is open
and you have a ticket that is not closed (at most once every 30 seconds for any one ticket). It has no timer of its own.

## How the project team works tickets

Everything below applies to the Tico project's own Support Agent, and to anyone who runs their own HQ.

1. **HQ** stores the ticket. The team's key, `HQ_STAFF_KEY` in HQ's `.env` (24+ characters), opens the staff routes; without it
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

On HQ, `HQ_JUDGE_KEY` in `hq/.env` is that provider's key (`HQ_JUDGE_URL` only to point elsewhere). Without it every verdict is
`unchecked` and nothing is sent to anyone. HQ logs the verdict and reason and never the text. Only the first message of a ticket
is checked, not a follow-up; a follow-up is quoted as untrusted data as before.

### Set it up

On the HQ host: add `HQ_STAFF_KEY=$(openssl rand -hex 24)` to `hq/.env` and `docker compose -f hq/compose.yaml --env-file hq/.env
up -d`. In **Tools → Credentials**, store the same `HQ_STAFF_KEY` and the HQ address as
`HQ_URL` (not `TICO_HQ_URL`: the runner keeps `TICO_` names out of bots' environments), with those exact Bot variable names, and grant both to the Support Agent. For
GitHub, store and grant `GITHUB_TOKEN` (read access; required for Discussions) and optionally
`GH_SUPPORT_REPOS` (`ticoteam/tico`). A Credential card is the other way to store and grant them.
Legacy files can be imported for migration; a new watcher does not load `secrets/support.env`.

Without `HQ_STAFF_KEY` the watcher does nothing. To watch GitHub, list the repositories in `config/github.yaml` or
`GH_SUPPORT_REPOS`. Check Settings > Health for a "Watchers" line: it appears only when a watcher fails or has stopped.

### By hand

`software/hq-tickets list`, `show TK-XXXXXXXX`; or with curl and the key in a header (`Authorization: Bearer $HQ_STAFF_KEY`):
`GET /v1/staff/tickets?status=open`, `POST /v1/staff/tickets/{id}/reply` with `{"body": "..."}`, `POST .../status` with
`{"status": "closed"}`, `DELETE /v1/staff/tickets/{id}` to delete on request, `GET /v1/staff/tickets?status=held` for what the
spam check held, `POST /v1/staff/tickets/{id}/verdict` to correct it.

## Compatibility

Deploy the HQ update before the client update to enable follow-up attachments. Older HQs reject that optional field; the app
shows the failure and keeps the draft, allowing an explicit message-only retry. The attachment limit remains 256 KiB. There is
no new continuous export, persistent log journal, or verbose logging mode.
