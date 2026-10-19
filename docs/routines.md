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

A timed automation a person wrote for a manager bot, which only they have changed since, starts runs that may use the
report grant they gave it ([A manager bot that manages its reports](permissions.md#a-manager-bot-that-manages-its-reports));
once anyone else changes it, the bot itself included, its runs lend nothing.

A prompt can read the trigger's values with `{{...}}`, for example `{{webhook.data}}` for a webhook call's body. Each
one is passed as quoted data after the prompt, never as instructions.

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
