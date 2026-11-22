---
service: close-crm
title: Close CRM
kind: api
summary: Close call transcripts import into Meetings, and linked opportunities can be read from open tasks.
access: "The runner uses CLOSE_API_KEY for transcript imports. Opportunity links use an administrator-marked record integration and its shared company key (HTTP Basic, key as username, empty password)."
credentials:
  - CLOSE_API_KEY in secrets/close-calls.env on one runner computer, read only by the close-calls job
  - CLOSE_API_KEY stored in Tools → Credentials and granted to the bot, for a bot that reads the Close account
  - Shared company API key on an Everyone Close CRM tool marked as holding records, for opportunity links
declared_as: |
  - service: close-crm
    identity: "the team's Close account"
    can: [read]
    env: CLOSE_API_KEY
    note: "GET only; ids and labels, no contact details in tasks or reports"
writes: never
owner: owner
aliases: [close]
---

## What it is

Close is a sales CRM. Tico reads it in these ways.

- **Meetings.** The runner's `close-calls` job (`runner_support/close_calls.py`) polls completed Close
  calls and Notetaker meetings every five minutes and imports their speaker turns and Close's
  summary into **Meetings**. It starts when `secrets/close-calls.env` holds `CLOSE_API_KEY` on a
  runner computer. It reads only and never downloads Close audio. Setup:
  [Meetings](../docs/meetings.md#close).
- **A bot's own reads.** A bot that declares `close-crm` in its `tools:` calls the API itself
  with the key in its environment. The bot's playbook and these rules keep it read-only.
- **Linked opportunities.** Tico reads the status, value and lead of opportunities linked to open tasks
  through a record integration. File a task with `--primary-link close:opportunity:oppo_X@<tool id>`.
  A won or lost primary opportunity asks the task owner whether to settle it.
  [Outside items and intake](../docs/dev/pipelines-v3.md#outside-items)

## What data it has

Leads with custom fields, statuses, owners and notes; contacts on a lead; opportunities and
their stage changes; smart views (saved searches); custom field definitions; call and meeting
activities, with transcripts where Close's Call Assistant or Notetaker made one.

## How a bot uses it

```bash
curl -s -u "$CLOSE_API_KEY:" "https://api.close.com/api/v1/lead/?_limit=5&query=status:Potential"
curl -s -u "$CLOSE_API_KEY:" "https://api.close.com/api/v1/opportunity/?_limit=100&_skip=0"
curl -s -u "$CLOSE_API_KEY:" "https://api.close.com/api/v1/status/lead/"
curl -s -u "$CLOSE_API_KEY:" "https://api.close.com/api/v1/custom_field/lead/"
```

Paging is `_limit` and `_skip`; `query=` takes Close's own search syntax. For what a prospect
said on a call, read Meetings (`tico meeting search "<company>"`), not the raw activity.

## Rules

- `GET` only. Never create, update or delete a lead, contact, opportunity or status; never
  enrol anyone in a sequence, send an email or place a call. A write needs a new `tools:`
  entry from the owner and a Tico approval for the change.
- Tasks and reports carry ids and labels, not contact details.
- Never print, log or write the key.

## Recipes

- Pipeline movement for a weekly review: `GET /activity/status_change/opportunity/` with
  `date_created__gte` and `date_created__lt` in UTC, then group by `new_status_label`.
- A saved list: `GET /saved_search/`, then `GET /lead/?query=in:"<smart view name>"&_limit=100`.

## Gotchas

- Close filters in UTC. Convert a local-time window before you send it and say which bounds you used.
- Rate limits answer 429; back off.
- Every bot with the key shares it; Close's audit trail shows the key, not the bot, so say which
  bot did what in the task note.
- A call with no transcript in Close has none in Meetings either; the importer does not transcribe.

## Learnings

What bots and people learn about this integration is added with `tico tool learn close-crm "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
