# Routines (now Automations)

A routine is now an automation: a trigger and steps. A bot's routine is an automation whose trigger is a
**Schedule** (a five-field cron in a time zone), an **Event** from Tico's catalog, or an incoming **Webhook**, and whose
step is a **Bot turn**: a task for the bot with the prompt, which the bot runs in its own session. For the jobs Tico
runs on its own, see [System routines](system-routines.md).

An install that had routines moved them once, on the upgrade to Automations; see
[Moving to Automations](updates.md#moving-to-automations-one-time).

## Making one

- **The site.** **Automations** in the sidebar → **New automation**. Pick the trigger, add a Bot turn step, and save.
  An automation's page shows its **Runs** and **Versions**.
- **The `hub` CLI, from a person or a bot.** `hub automation guide` prints the flow format and every trigger, step and
  action. Write the flow as JSON and pass it with `--file` (`-` reads stdin):

  ```
  hub automation create --file - <<'EOF'
  {"name": "Daily audit", "owner": "bot:acme-ops", "enabled": true,
   "trigger": {"type": "schedule", "cron": "0 7 * * 1-5", "tz": "America/Los_Angeles"},
   "steps": [{"id": "audit", "type": "bot_turn", "enabled": true, "bot": "acme-ops",
              "prompt": "Run the daily audit. Follow playbooks/audit.md."}]}
  EOF
  ```

  For an event, the trigger is `{"type": "event", "name": "meeting.ready"}`; for a webhook,
  `{"type": "webhook", "id": "<trigger id>"}` (`hub trigger list` shows the ids). A new automation is saved off
  unless the flow sends `"enabled": true`.

## Seeing them all

**Automations** opens on a list of every automation you may see, Tico's own jobs included (trigger **System job**).
Filter by trigger, status, team and owner on the left; pick a range (5m, 1h, 24h, 7d) at the top for each row's runs,
failures, cost and trend. The **Runs** tab shows every run in that range, newest first, with what started it and how it
ended. A built-in automation can't be edited or deleted; only the top of the org can turn one off.

```
hub automation recent-runs [--range 1h] [--status failed] [--trigger system] [--owner O]
hub automation stats [--range 24h]
```

These are `hub_automation_recent_runs` and `hub_automation_stats` over MCP, and `GET /api/v2/automations/runs` and
`GET /api/v2/automations/stats` in the API. A system job counts once per recorded run; quiet repeats with the same result
fold into one row (`count`).

  A Bot turn step can also name the model its run's task runs on, with `"model"`, and optionally `"effort"` and
  `"harness"` (`"model": "<model id>", "effort": "high"`). The task the run opens is stamped with that choice
  ([Choosing the model a task runs on](tasks.md#choosing-the-model-a-task-runs-on)). Leave them out to use the bot's
  task model. Naming a model needs the same right as changing the bot's model: someone without it sends the change for
  a manager to approve as a pending version, and a bot's new automation starts off. Until a manager approves that
  version, someone without the right cannot turn the routine on or run it by hand; a manager who turns it on approves
  it.

  ```
  hub automation list [--owner O]
  hub automation show <id>
  hub automation update <id> [--file F] [--name N] [--enable|--disable] [--summary S]
  hub automation run <id> [--input JSON]
  hub automation runs <id>
  hub automation delete <id>
  ```

  The MCP tools have the same names (`hub_automation_list`, `hub_automation_create`, `hub_automation_update`,
  `hub_automation_run`, `hub_automation_guide`, ...). The API is under `/api/v2/automations`.

A prompt can read the trigger's values with `{{...}}`, for example `{{webhook.data}}` for a webhook call's body. Each
one is passed as quoted data after the prompt, never as instructions.

## On hold

An automation that keeps starting runs for 20 minutes (120 or more, spread over at least 15 of those minutes), or
starts 500 in 10 minutes, is put **on hold**. That is most often a loop, but a slow catch-up after an outage looks the
same, so nothing is lost: its queued runs, any run whose wait ends meanwhile, and up to 500 new ones wait as **Held**
instead of running (past that a trigger is counted as dropped, and the hold shows how many). A run mid-step finishes
that step first. **Run now** is refused while it is on hold, and manual runs never count toward the guard. Its owner gets one Needs-you task, and the Health Monitor
one notice. The automation's page shows **On hold · N held**: fix what starts it, then **Release** (the held runs
queue, oldest first) or **Drop** (they are skipped). Turning it off or deleting it skips them too.

```
hub automation held <id> release|drop
```

Over MCP this is `hub_automation_held`; in the API, `POST /api/v2/automations/{aid}/held` with `{"action": "release"}`
or `{"action": "drop"}`. Whoever may change the automation may do either.

## Events

`hub event-types` (`hub_event_types`, `GET /api/v2/event-types`) lists every event name with its description and an
example payload; `--group` narrows it. Some that bots use: `meeting.ready` (a team meeting with a transcript is imported;
see [Meetings](meetings.md)), `release.published`, `market.insight.urgent`, `strategy.published` and `market.briefing`
(see [Market briefing](market-briefing.md)). An event fires an automation only once something emits it.

## Webhooks

An incoming webhook (Settings → Webhooks → Incoming) gives an address anything can POST to. An automation with a
Webhook trigger runs on each call; add `filters` to the trigger (for example `webhook.type` is `invoice.paid`) to act on
some calls only. Signatures, presets, the hourly limit, kept calls and **Run again** are on [Webhooks](webhooks.md).
Recipes: [GitHub](webhooks-github.md), [Stripe](webhooks-stripe.md), [Zapier, Make and n8n](webhooks-zapier-make-n8n.md).

## Templates

A template's `bot.yaml` may still declare `routines:` (`id`, `title`, `cron` or `on`, `timezone`, `template:` a
playbook path, and optionally `enabled: false`). `hub bot create` seeds them once, and each becomes an automation for the
new bot. After that the automations are what run; editing the file changes nothing. `clients/preflight.py` still checks
the block.
