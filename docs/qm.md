# QM in Tico

> **Beta**: QM is one of the external agents Tico syncs in. It may still change. See [Beta features](support.md#beta-features).

[QM](https://github.com/yc-software/qm) is Y Combinator's open-source multiplayer agent harness (MIT): one agent per
person and per Slack room, in Slack and on the web. This page connects a person's QM agent to Tico. It then appears on the
team chart under that person, as a **QM** bot, takes tasks, and gets its chat with the person in Tico. It is the same kind
of link as Dots and Muse ([External agents](external-agent-sync.md)), with two extras: QM can take tasks, and Tico can wake
it the moment a task is given to it.

What a QM bot is in Tico:

- One row per person, under them on the team chart, with `harness` `qm`. Move it anywhere afterwards like any bot.
- Its name, description and full instructions are kept in `config_json.qm`, so it can be rebuilt if QM goes away.
- Its transcript with the person is copied into their chat with it, already read.
- Tasks given to it are its work. QM calls Tico's tools as the person, so it works with that person's rights and no more.
- Without a wake address, a task waits for the next daily sync. With one, Tico posts the task to QM at once.

## a. Make a token

Each QM agent calls Tico as its person, with a personal token.

1. In Tico, open **Connect an external agent**, pick **Other**, and press **Create token**. The token lasts 90 days and is
   shown once. Copy it now.
2. Or, for many people, make one token per person the same way and save each one in QM (step b).

The MCP server URL is `https://<your team hostname>/api/v2/mcp`. The dialog shows it.

## b. Register Tico in QM

A QM admin registers Tico's MCP server with `PUT /v1/admin/mcp-servers/:id`. The registration sets where calls go and which
calls need approval. Agents cannot change it.

For each person, with their own token saved in QM (`per-user`, the usual choice):

```json
{
  "name": "Tico",
  "url": "https://tico.example.com/api/v2/mcp",
  "auth": "bearer",
  "bearerToken": "<any one person's Tico token>",
  "credentialScope": "per-user",
  "credentialHost": "tico.example.com",
  "approval": "annotations",
  "enabled": true
}
```

`bearerToken` is used only to list Tico's tools. Each call uses the calling person's own token. `approval: "annotations"`
makes QM pause the calls whose tool annotations mark them destructive, for that person to approve.

Then save each person's token with `POST /v1/connectors/token`, from a trusted integration with QM's source sign-in:

```json
{
  "host": "tico.example.com",
  "principalId": "<that person's QM user id>",
  "accessToken": "<that person's Tico token>",
  "expiresAt": 1798761600000
}
```

`expiresAt` is Unix milliseconds, when the Tico token ends (90 days after it was made). Make a new token and save it again
before then. A call with no saved token fails with a connection-required message.

For one person only, `"credentialScope": "shared"` with that person's token as `bearerToken` works too. Every QM caller then
acts as that person, so use it only for a QM that serves that person alone.

## c. Sync the agent once a day

The sync puts the QM bot on the team chart, copies the transcript and hands over what the person wrote to it in Tico. It is
a QM cron that runs the routine below, as the person:

```json
{
  "createdBy": "<that person's QM user id>",
  "title": "Tico sync",
  "schedule": {"cron": "0 8 * * *", "timezone": "Europe/London"},
  "task": "<the routine below>"
}
```

> **Tico sync.** Use the Tico MCP tools.
> 1. Call `hub_external_sync` with `provider` `qm`, one bot with `name` (your name), `description` and your full
>    `instructions` verbatim, and the messages of our conversation newer than the `synced_through` Tico returned
>    (all of them the first time), oldest first, as `role` (user or bot), `text` verbatim and `at` (ISO time). At
>    most 200 messages per call; repeat until all are sent.
> 2. For each `inbox` message in the reply, act on it as from me, in order. Each one is handed over once.
> 3. Tell me in one line what Tico added and what you did with the inbox.

The first sync may take a few calls. Later ones send only what is new, so a daily run is short.

## d. Instant hand-off (optional)

Without this, a task waits for the next daily sync. With it, Tico posts each task given to the QM bot to QM the moment it
is given, and QM starts a turn for it.

1. In QM, create an inbound webhook in the person's scope, with the `hmac-sha256` scheme and a strong random secret of at
   least 16 characters. Tico sends the task as a JSON body, signed with that secret:

   ```json
   {
     "ownerScopeId": "<the person's scope>",
     "owner": "<that person's QM user id>",
     "createdBy": "<that person's QM user id>",
     "action": "A Tico task was given to you. Read it with hub_task_show (its task_id), start it as in the Tico sync routine, and do the work.",
     "verification": {"scheme": "hmac-sha256", "secret": "<the secret>"}
   }
   ```

   QM answers with the webhook's URL, shown once. Copy it with the secret.

2. In Tico, set the QM bot's wake address with the person's own token (the one from step a). The QM bot's slug is `qm`,
   or the one Tico gave it if the person has more than one (its bot page shows it):

   ```
   PUT /api/v2/bots/qm/qm-wake
   Authorization: Bearer <that person's Tico token>
   Idempotency-Key: <any new key>

   {"url": "https://qm.example.com/v1/webhooks/incoming/<id>", "secret": "<the same secret>"}
   ```

   Only the person who synced the bot, or an admin, may set it. The address must be `https`, public, and not an IP address. A QM on the same computer as Tico (a local trial) can use `http://localhost:<port>/...` when the owner sets it with `"allow_http": true`.
   The secret is at least 16 characters. Tico keeps it sealed and never shows it again. Send `{"url": "", "secret": ""}`
   to turn the wake off.

Tico posts one event, `task.assigned`, to the address, as JSON:

```json
{"event": "task.assigned", "task_id": "t_1", "title": "Draft the launch email",
 "url": "https://tico.example.com/#/task/t_1", "requester": "Ana", "due": "2026-11-02"}
```

Each post carries `x-signature: sha256=<hex>`, an HMAC-SHA256 of the exact body with the secret. QM checks it. Tico also
sends its usual `webhook-id` and `webhook-timestamp` headers, so a repeat is recognized by its id. A failed post is tried
again for about 28 hours, as other outgoing webhooks are ([Outgoing webhooks](webhooks.md#outgoing)). It shows on the
Outgoing tab of Settings → Webhooks as `QM wake: <name>`, with its send log.

The post only starts QM. The task itself stays in Tico, and QM reads it there.

## e. What the QM agent can do

QM calls the same tools as any agent that connects to Tico ([Connect an external agent](connect-an-agent.md)). With a QM
bot, the useful ones are:

| To | Tool |
|---|---|
| See its tasks, in queue order | `hub_task_list` |
| Read one task with its history and conversation | `hub_task_show` |
| Start a task, or change its status, note or owner | `hub_task_update` (`status: doing`) |
| Comment, or ask the people on a task a question | `hub_task_comment` |
| Answer a question another bot asked it | `hub_question_answer` |
| Finish a task with a result | `hub_task_done` (a concise result note) |

The requester closes a task (`hub_task_close`). A QM bot may not create tokens, change its own permissions or approve
things for its person. Those need the person signed in to Tico.

## Check it

- After step c, the QM bot shows on the team chart under the person, labelled **QM**, and its transcript is in the person's
  chat with it.
- Give the QM agent a task in Tico, or ask it "who needs me". It should list the task with `hub_task_list`.
- With step d, give it a task (`hub_task_create` with `owner` set to the QM bot). QM should start a turn within a minute.
  If nothing happens, the send log under Settings → Webhooks → Outgoing shows the attempt and QM's answer.
