# Webhooks

A webhook is one program posting a small message to another over HTTP. Tico does it both ways.

- **Incoming**: something outside Tico (a payment service, a code host, a form, a script) posts to an address Tico gives you. Tico
  keeps the call, starts your routines and rules, and can wake a bot.
- **Outgoing**: Tico posts to an address you choose when something happens in Tico, such as a task finishing or an approval being asked.
- **Through Tico Gateway**: a second address for an incoming webhook that keeps what is sent while your server is off or asleep.

All three live in Settings → Webhooks, on the tabs **Incoming**, **Outgoing** and **Through Tico Gateway**. Everything on the page
is also in `hub` and in the `hub_*` tools for bots and agents ([the table at the end](#the-commands)).

Step by step:
[GitHub](webhooks-github.md), [Stripe](webhooks-stripe.md), [Zapier, Make and n8n](webhooks-zapier-make-n8n.md).

## Incoming

Add a webhook on the Incoming tab, or:

```
hub trigger create "GitHub issues" --id github
```

Tico shows the address once:

```
https://tico.example.com/api/v2/hooks/github/<secret>
```

Give that address to the sender. It also accepts `POST /api/v2/hooks/github` with the header `Authorization: Bearer <secret>`. Tico
keeps only a hash of the secret, so it cannot show the address again. **New secret** (`hub trigger rotate github`) makes a new
address and stops the old one at once. A wrong id and a wrong secret get the same answer, 404.

The word *trigger* in the CLI, the API and routines means the same thing as an incoming webhook.

### What the sender gets back

| Answer | Meaning |
|---|---|
| 202 | Accepted. The reply says how many tasks it started and how many rules worked (`rules`; see [Rules](#rules-on-webhookreceived)). |
| 200 with `"duplicate": true` | Tico has seen this delivery before and did nothing. |
| 401 | The signature is missing or wrong. |
| 404 | No such webhook, or the secret is wrong. |
| 413 | The body is over 256 KB. |
| 429 | Over the hourly limit. The sender should retry later. |

### Check who is calling

By default anyone with the address can post. Turn on **Signing** and Tico checks every call, and refuses one that
fails with 401 before anything starts. Tico makes a signing secret and shows it once, or you can paste the sender's own (at least
16 characters). `hub trigger sign <id> --preset github` does the same; `--off` stops checking.

Pick the preset that matches the sender:

| Preset | The sender needs to send | Notes |
|---|---|---|
| `none` | Nothing. | The address is the only secret. |
| `hmac` | `X-Hub-Signature-256: sha256=<hex>` or `X-Signature: <hex>` | An HMAC-SHA256 of the exact body. The `sha256=` is optional. |
| `github` | `X-Hub-Signature-256: sha256=<hex>` | GitHub's own scheme. Paste the same secret into GitHub's *Secret* box. |
| `standard` | `webhook-id`, `webhook-timestamp`, `webhook-signature` | [Standard Webhooks](https://www.standardwebhooks.com). The secret starts `whsec_`. A timestamp more than 5 minutes off is refused. |
| `stripe` | `Stripe-Signature: t=...,v1=...` | Paste Stripe's *signing secret* (`whsec_...`) as it is. A timestamp more than 5 minutes off is refused. |
| `slack` | `X-Slack-Signature: v0=<hex>`, `X-Slack-Request-Timestamp` | Paste Slack's *signing secret*. A timestamp more than 5 minutes off is refused. |

Tico keeps the signing secret sealed, because checking a signature needs the secret itself.

**Slack's address check.** When you give Slack the address, it first posts a `url_verification` call with a challenge. With the
`slack` preset Tico checks its signature, answers with the challenge, and does not count it as a delivery or start anything.

### What kind of call it is

Every call has a **type**, read from `type_from`: a header name (`X-GitHub-Event`) or a JSON pointer that starts with `/`
(`/type`), or several joined with `|` that Tico tries in turn. `github` reads `X-GitHub-Event`. `standard` and `stripe` read `/type`.
`slack` reads `/event/type`, then `/type`. Otherwise the type is empty. The type shows
in the log, in the task (a `type:` line) and to rules.

```
hub trigger update github --type-from /action
```

### Limits and keeping

- **Hourly cap**: 60 calls an hour by default, up to 600 (`--hourly`). Past it the sender gets 429. A call that arrives through
  Tico Gateway does not count.
- **Keep days**: Tico keeps each accepted call, body and headers, for 7 days by default, 1 to 30 (`--keep-days`).
- **Duplicates**: if the sender names the delivery (`Webhook-Id`, `X-GitHub-Delivery`, `Idempotency-Key` or `X-Request-Id`),
  the same id again does nothing. Without one, a Stripe webhook uses the event id in the body (`evt_...`), and any other counts the same body in the same minute as the same delivery. Tico remembers ids
  for 30 days, longer than it keeps the calls.

### If nothing listens

A call that reached no routine and no rule is only logged. Set **If nothing listens** to *Tell the maker* (`--unheard note`)
and Tico also leaves a notice for whoever made the webhook, at most once an hour. The default is *Log it* (`--unheard log`).

### The delivery log

Under each webhook, **Recent calls** lists the last 50: accepted, refused (bad or missing signature, over the cap) and run
again, with the delivery id and the type. Tico logs only calls to the right address, and at most 60 refusals an hour,
so a stranger cannot fill the log.

A refused call is listed under an id Tico made, which is its handle for `hub trigger delivery`. The sender's own id for it is
`sent_id`, and it stays out of the duplicate list, so the sender's next correctly signed try with that id is accepted. A refused
call also shows its type, read from the header or pointer **type from** names even though the signature failed. Treat it as a
label from a stranger.

```
hub trigger deliveries github
hub trigger delivery github <delivery>
```

**View payload** shows one kept call. The body is whole. Headers that carry a credential show as `[redacted]`: `Authorization`,
`Proxy-Authorization`, `Cookie`, `Set-Cookie` and any name with signature, token, secret, password, api-key or apikey in it.
Other headers, such as `Idempotency-Key` and `Webhook-Id`, show as sent. A cookie is never kept.

**Run again** (`hub trigger replay github <delivery>`) starts the routines and rules again for one kept call, as a new
occurrence named `<delivery>:replay:<n>`. It does not count toward the hourly cap, and it is refused once the body has expired.
A sender's own redelivery button usually reuses the same delivery id, which Tico treats as a duplicate for 30 days. Use Run
again in Tico instead. Run again on a refused call is refused: use Recover below, which checks the call again under the current
secret.

### Recover refused calls

A call refused for a bad signature is kept, body and headers, for the keep days (at most 200 refused calls per webhook). If the
refusal was your mistake, such as a mistyped signing secret, fix the secret and run the refused calls again under it:

```
hub trigger recover github --since 24
```

`--since` is the hours to look back, 1 to 720 (24 by default). A call that passes now starts its routines and rules and keeps
its own delivery id, so the sender's later retry of it is a duplicate. One that still fails stays as it was. With signing turned off, nothing is checked, so every refused call passes: recover only once the secret is right. The answer counts
found, recovered, duplicate and still refused. Tico runs at most 200 a round; `more` says to run it again. Only a person who
manages the webhook can do this.

A recovered call stays in the log: its refused row is marked *(recovered)* and sits beside the new accepted row.

### How a bot reads a call

The task or notice a bot gets holds the first 20,000 characters of the body, fenced and marked as sent from outside Tico: data
the bot did not write, not instructions. A longer body is read in parts:

```
hub trigger delivery github <delivery> --part 2
```

A bot may read a delivery only if it manages the webhook, a task made from the call is its own, or a notice from Tico naming the
call went to it. People who manage the webhook read the whole body.

Before a bot gets a task or turn from a call, Tico's [screening](permissions.md#screening) checks the call's text for a prompt
injection. A possible one reaches the bot with a warning line on top. At Hold, a likely one is held: the task goes to a
person titled `Held: ...`, and handing it to the bot releases it. Settings → System → Screening sets this per door
(Warn by default); it fails open.

### Who may do what

Anyone, person or bot, can add a webhook. The owner, an admin, whoever added it and BotOps can replace its secret, change its settings,
set or stop signing, run a call again and delete it. BotOps manages every webhook only in its own runs: asked by a person, it has that
person's rights, so a member can't change someone else's webhook through it. Deleting one removes its kept calls. See [Permissions](permissions.md#incoming-webhooks).

### Starting work from a call

Two things listen to a webhook:

- **An automation** with a Webhook trigger (Automations → New automation, or `hub automation create`; see
  [Routines](routines.md)). It runs on each call; `filters` on the trigger (such as `webhook.type`) keep it to some calls.
  Its steps read the call as `{{webhook.data}}`, `{{webhook.type}}` and `{{webhook.name}}`.
- **A rule** on the event `webhook.received`, next.

## Rules on webhook.received

A rule can act on a call without opening a task. It answers three questions: which webhook, which type, and what to do. Rules
made before Automations keep running from the **What wakes** automations, and the rest of this section describes them. To act on
a call now, make an automation with a Webhook trigger, a filter on `webhook.type` and an action:

```
hub automation create --file - <<'EOF'
{"name": "New GitHub issue", "owner": "bot:ops", "enabled": true,
 "trigger": {"type": "webhook", "id": "github",
             "filters": [{"field": "webhook.type", "op": "is", "value": "opened"}]},
 "steps": [{"id": "wake", "type": "action", "enabled": true, "do": "wake_bot",
            "args": {"bot": "ops", "text": "New issue: {{webhook.data.issue.title}} ({{webhook.data.issue.html_url}})"}}]}
EOF
```

**When.** `trigger` is the webhook id. `type` is a word, a list of words, or a word ending in `*`. Leave `type` out to match every
call. A rule can match only on these two things, never on a field inside the body.

**Then.** Four actions are available:

- `wake_bot {bot, text}` leaves a notice in the bot's room and wakes it. The notice is your `text`, then the call's content
  (fenced as data), then, when the body was cut, the line `The rest: hub trigger delivery ... --part 2`.
- `send_webhook {webhook, type, data}` posts an event to one of your [outgoing endpoints](#outgoing). `type` should be a
  `custom.*` name.
- `forward {webhook}` sends the call itself on to one of your outgoing endpoints: the body as it came is the event's `data`
  (anything that is not a JSON object arrives as `{"data": ...}`), and the type is `custom.forward.<webhook id>`, so the
  receiver knows which webhook it came through. It works only on `webhook.received`.
- `create_task {owner, title, body}` files a task for the bot `owner`, in the rule maker's name. `title` and `body` are templates.
  The body ends with the call's content, fenced as data. Nothing is filed twice for one call, and the maker must be allowed to
  give that bot tasks. With [screening](permissions.md#screening) on, a long call's task opens a few seconds after the call is
  answered, once the check is in, and a held one goes to the rule's maker.

Rules also have the ordinary outcomes: *ignore*, *note*, *wake* and *ask*.

**Templates.** In `text`, `type` and `data`, `{{name}}` is replaced from the call. A dotted path walks into the body, and a number
picks from a list: `{{data.items.0.name}}`. These names are available:

| Name | Value |
|---|---|
| `data` or `payload` | The JSON body |
| `type` | The call's type |
| `trigger.id`, `trigger.name` | The webhook |
| `delivery` | The delivery id |
| `via` | `direct` or `gateway` |
| `content` | The body as text |

A name that is not there becomes empty. A whole object becomes compact JSON. A value is never read for more `{{...}}`, so a sender
cannot write its own template. Output is cut at a limit, ending with ` ... (cut)`. `text` can be up to 1,000 characters.

**Order.** Rules run bot first, then group, then team, each in its position. At most one rule acts on each target (a bot to wake,
an endpoint to send to). An *ignore* with no bot named stops the rules after it. A given call runs the rules once, even if the
sender posts it again.

**What `rules` counts.** The answer to the sender (`rules`) and the call's row in the log count the rules whose action worked. A
rule that failed is not counted, but it is kept: `hub trigger delivery <id> <delivery>` lists each rule's result as `rule_results`,
with `ok: false` and the reason (`cpo is planned`). A wake rule, or a `wake_bot` action, reaches a bot that exists and has been
started. A bot that is still *planned* is not reached; a paused or quarantined bot keeps the message until it is back.

**Nothing matches, nothing happens.** There is no catch-all. If no rule matches or one fails, the call is still logged, a routine
on the webhook still runs, and the failure is in History as `rule.event_error`.

**Who may write them.** The owner and admins write team rules; a bot's owners, its operator and BotOps write that bot's rules.
`wake_bot` needs rights over the bot it wakes. `send_webhook` is written by a person, or by a bot with its outbound send switch on.
The Strategy Bot never writes one. Tico checks this when the rule is saved: the endpoint must exist and its maker must be allowed to send
to it (they own it, or are an admin), or the save is refused with the reason. When the rule runs, the same test is made again, and a
bot's rule reaches only an endpoint that listens for the type.

## Through Tico Gateway

A webhook's own address works only while your server is up and reachable. A server on a laptop, behind a firewall or asleep
misses what is sent.

An address through Tico Gateway fixes that. It is a second address at Tico HQ that holds what senders post, and your server
collects it from there. Make one from the **Through Tico Gateway** tab, or:

```
hub trigger relay create github
```

It looks like `https://hooks.tico.dev/hr_<16 characters>/<secret>`, and like the first address it is shown once.
`hub trigger relay rotate github` replaces it and `hub trigger relay delete github` removes it. The webhook's own address keeps
working beside it. It needs the Tico Gateway connected in Settings → Gateway ([Tico AI Gateway](ai-gateway.md)).

- Your server collects the calls every 15 seconds and treats each as if the sender had posted it here. Signature checks,
  duplicates, routines and rules behave the same. The hourly cap does not apply to them.
- Tico HQ checks only the secret in the address. It does not check signatures and does not run anything.
- **While the server is off or asleep**, calls wait at HQ, up to a limit, and arrive in order within seconds of the server being
  back. A sender that waits for an answer gets HQ's, not your server's.
- On a server Tico hosts, a call through the Gateway also wakes a server that is [asleep](#sleep-when-idle), if the address's
  **wake** switch is on. HQ keeps the switch and Tico shows it on the address (`wake`). Change it with
  `hub relay set github --wake off`. The owner, an admin and whoever made the webhook can; BotOps can for them.
- A server you run yourself can ask Tico to knock on an address of yours when a call has waited over a minute, so something you
  run can turn it on: `hub relay wake-url set <https address>`. See [Wake on webhook](install.md#wake-on-webhook).
- `hub relay status` shows the key, the addresses, the last collection and what waits. Health speaks when collecting has failed for
  15 minutes or a call has waited over an hour. A rehearsal copy never collects.

## Outgoing

An **endpoint** is an address Tico posts events to. Add one on the Outgoing tab, or:

```
hub webhook create --name "Acme orders" --url https://hooks.example.com/tico --events 'task.*' 'custom.*'
```

Tico shows the signing secret (`whsec_...`) once. Keep it where the receiver can read it.

### Choosing events

`--events` takes event names, groups ending in `.*`, or `*` for everything. `hub event-types` lists every name with a
description and an example (`--group task` narrows it). A few:

| Group | Events |
|---|---|
| `task` | `task.created`, `assigned`, `step_changed`, `state_changed`, `commented`, `done`, `declined`, `closed`, `blocked`, `reopened` |
| `approval` | `approval.requested`, `decided`, `withdrawn` |
| `needs_you` | `needs_you.created`, `answered` |
| `bot` | `bot.run_failed`, `held`, `released` |
| `thread` | `thread.created`, `message.created` (outgoing webhooks only), `message.held` ([Threads](threads.md)) |
| other | `release.published`, `meeting.ready`, `strategy.published`, `market.insight.urgent`, `market.briefing`, `inbound.received`, `health.finding` |
| `custom` | `custom.<name>`, which only a person or a rule sends (below) |

On a thread shared with another company, what that company wrote stays out: see [Federation](federation.md).

`webhook.received` and `tico.notice` are for rules only: an endpoint cannot subscribe to them (use a rule with `forward`).
An endpoint can hold up to 60 patterns, and an event goes to at most 20 endpoints.

### What arrives

A `POST` with this body:

```json
{"type": "task.done", "version": 1, "timestamp": "2026-10-25T10:00:00Z",
 "data": {"id": "t_1", "title": "Ship it"},
 "links": {"app": "https://tico.example.com/#/task/t_1", "api": "https://tico.example.com/api/v2/tasks/t_1"}}
```

`data` over 64 KB is cut. **Send example** (`hub webhook example <id> --type task.done`) posts a sample of any event to one
endpoint, so you can build the receiver before real events come.

### Check the signature

Every delivery is signed as [Standard Webhooks](https://www.standardwebhooks.com), with three headers:

- `webhook-id`: stable across retries. Use it to ignore a delivery you already handled, because delivery is at least once.
- `webhook-timestamp`: new on every try.
- `webhook-signature`: `v1,<base64>`, an HMAC-SHA256 over `id.timestamp.body`. For 24 hours after a secret is rotated there are
  two `v1,` values, one per secret.

Verify with the sender-neutral library. Always pass the raw body, not parsed JSON.

Python (`pip install standardwebhooks`):

```python
import json
from standardwebhooks import Webhook

wh = Webhook("whsec_your_endpoint_secret")

def handle(raw_body: bytes, headers: dict):
    wh.verify(raw_body, headers)      # raises if the signature or timestamp is wrong
    event = json.loads(raw_body)
    print(event["type"], event["data"])
```

Node (`npm install standardwebhooks`):

```js
import { Webhook } from "standardwebhooks";

const wh = new Webhook("whsec_your_endpoint_secret");

app.post("/tico", express.raw({ type: "application/json" }), (req, res) => {
  try {
    wh.verify(req.body, req.headers);   // throws if the signature or timestamp is wrong
  } catch {
    return res.sendStatus(400);
  }
  const event = JSON.parse(req.body);
  console.log(event.type, event.data);
  res.sendStatus(200);
});
```

### Answering, and retries

- Any `2xx` means delivered.
- `webhook-delivery: abort-message` on the answer cancels the send. Use it when the event can never work.
- Anything else retries, and so does a redirect (Tico never follows one).

A failed send is tried again after 5 seconds, 5 minutes, 30 minutes, 2 hours, 5 hours, 10 hours and 10 hours, each a little
jittered. That is 8 tries over about 28 hours. After the last, the send is **failed**, the endpoint's Health shows it, and Needs
you gets at most one task a day per endpoint. **Retry** (`hub webhook retry <id> <send>`) sends a failed, cancelled or delivered one again now.

Each try waits up to 30 seconds, and the answer is not read past 1 KB. An endpoint sends 600 an hour by default
(`--rate-per-hour`); sends over that wait, and none are dropped. Tico keeps events and finished sends for 30 days.

The send log (`hub webhook sends <id>`, `--state failed` to narrow) shows each send's state: ready, sending, delivered, failed,
cancelled or withheld.

### Only some events

An endpoint can narrow what it hears, beyond its event names, by **department** (of the task's owner), **bot** (the task's owner
or whoever acted) and **task type**:

```
hub webhook update acme --departments sales --bots ops --task-types bug
hub webhook update acme --no-filter
```

Each list left empty means all, and the lists combine: an event must pass every one that is set. An event with no task or bot in it
(a release, a health finding) always passes.

### Custom headers

An endpoint can send up to 10 headers of your own with every delivery, such as an API key the receiver wants:

```
hub webhook update acme --header "X-Env: prod" --secret-header "X-Api-Key: the-value"
```

A **secret** header's value is sealed and never shown again (the endpoint lists it as set); `--secret-header "X-Api-Key"` alone keeps the value it
has. Every use of `--header` replaces the whole list, and `--no-headers` removes it. Tico refuses the names it sets itself
(`webhook-*`, `host`, `content-length`, `content-type`, `transfer-encoding`, `connection`) and any value with a line break.

### When an endpoint keeps failing

An endpoint whose attempts have all failed for 5 days is turned off. The five days count from its first failed attempt, any
`2xx` answer starts the count over, and the latest attempt must be at least 12 hours after that first one, so a short burst of
errors after a quiet spell does not count. Tico keeps what was queued, shows the endpoint in Health, files one task for its owner
and records the turn-off in History.

Fix the address, then turn it on (`hub webhook enable <id>`, or Settings → Webhooks). The answer says how many sends gave up since
the failing began. Two commands send again what was held back; both need the endpoint on and handle at most 500 at a time (`more`
says to run it again):

```
hub webhook recover acme --since 2026-10-20
hub webhook replay acme --since 2026-10-20
```

- **Recover** sends again every send that gave up since the date, with the same ids and bodies, so a receiver that kept the first
  one drops the repeat.
- **Replay** sends the events since the date that the endpoint should have heard and has no send for: it was added late, was paused
  and cleared, or its filter changed. It applies the same subscription, filter and read tests as a live event, and never copies a
  one-off send meant for another endpoint. Tico keeps events for 30 days. A round looks at 500 events; when it says `more`, run it
  again with `--since` set to its `next_since` and `--after` to its `next_after`.

### Allowed addresses

Tico checks the address twice. When you save it, it must be `https`, with no name or password in it, and it may not be an IP
address outside public space (`10.0.0.1`, `192.168.1.10`, `169.254.169.254`, `::1`) or a local name (`localhost`, `*.localhost`,
`*.local`, `*.internal`); Tico looks nothing up then. On every try it checks again, and every address the name resolves to must be
a public one. Tico uses no proxy. For development, the owner can switch on **Allow plain http to this computer** for one
endpoint (`--allow-http`), which lets that endpoint use `http://localhost`, `127.0.0.1` or `::1`, and nothing else.

### Rehearsal copies

A copy of real data run in rehearsal mode ([Environments](environments.md)) sends nothing out. Sends that come due there are
**withheld**, and Retry refuses them.

### Custom events

`hub webhook send <id> --type custom.deploy-done --data '{"app":"web"}'` sends your own event to one endpoint. A rule's
`send_webhook` does the same. The endpoint must list `custom.*` or that exact name when a bot sends. A send to a paused endpoint
is refused (`hub webhook example` too); turn the endpoint back on first.

### Rights

- People make and manage their own endpoints. The owner and admins manage all of them. BotOps asked by a person has that person's
  rights.
- A bot cannot make an endpoint itself. Its request files an approval of kind *send*. A person approves, and the bot repeats the
  call. The approver then owns the endpoint.
- Bots can list endpoints without the full address. They cannot read send logs.
- A bot sends a custom event only while its **outbound send** switch is on (Settings → Bots → Mail sending, or
  `hub bot mail <slug> --send`), and only to an endpoint that listens for it. The Strategy Bot sends nothing.
- An endpoint hears about a task, approval or bot only if its owner could read it. A thread or message event goes only to an
  endpoint whose owner may read that thread. `message.held` goes to owner and admin endpoints, as does a health, inbound or
  webhook event. Thread events carry ids and facts, never a message's words.
- A person can have 50 endpoints.

More in [Permissions](permissions.md#outgoing-webhooks) and the [API](api.md#outgoing-webhooks).

## Changing where an integration sends

An endpoint with secret headers keeps their values for one address. When you save a new **URL** on **Edit endpoint** that
sends those values to another service, Tico asks first. The service is the scheme, host and port. For an endpoint with
secret headers, a different path or query also counts. Changing the address of an endpoint with no saved secret values does
not ask.

The question shows the old and new scheme, host and port, whether the path is the same, and the names of the secret headers.
It never shows a value. Pick one:

- **Keep and send to the new address**: the secret headers keep their values and go to the new address.
- **Clear and re-enter**: the values are removed. Each secret header keeps its name, marked "needs value", and the endpoint
  pauses until you type every value again or remove the header. An endpoint you paused yourself stays paused.
- **Cancel**: nothing is saved. The old address and values stay.

The same choice is on the command line. Changing the address needs the revision you read:

```
hub webhook update acme --url https://hooks.example.org/tico --expected-revision 4 --credentials clear
```

Whoever may edit the endpoint decides: its creator, an admin or the owner. If BotOps makes the change for someone, it asks
in a Confirm card in that person's BotOps chat, and only that person can tap it.

Each move is recorded once in Tico's audit log as `integration.destination_changed`: who made it and when, the old and
new scheme, host and port, whether the path is the same, and which secret headers were kept or cleared. Values never
appear in it. Settings > History does not list it.

## Sleep when idle

On a server Tico hosts, the owner or an admin can have Tico stop the server after 1 to 24 quiet hours. A call through Tico Gateway
wakes it, so a sender that posts while it sleeps is not lost: the call waits at HQ, the server starts, and the call arrives within
seconds of it being up. The routine time and a sign-in on your account page wake it too. The settings, and what counts as quiet,
are in [Account](account.md#sleep-when-idle):

```
hub sleep status
hub sleep set --hours 2
```

A server stopped because the balance ran out never starts on its own. An address that is not through Tico Gateway cannot wake a
sleeping server, because nothing is listening there. Use the Gateway address for any sender that must get through.

## Notices from Tico Team

Tico Team sends notices to a server that has a Gateway key: a security warning, a billing note, news about your version. They
arrive on a built-in webhook named **Tico Team**. It has no address, cannot be deleted, and only its `keep_days` changes.

Rules see each as the event `tico.notice`, with `type` set to `info`, `security` or `billing`. Three built-in team rules act on them:

- **security**: wake BotOps and ask the owner.
- **info**: leave BotOps a note.
- **billing**: ask the owner.

They are built in. `hub notice list` shows the notices. See also
[Tico HQ](tico-hq.md).

## The commands

| What | `hub` | Tool for bots and agents |
|---|---|---|
| List event names | `hub event-types [--group G]` | `hub_event_types` |
| Incoming: list, add, show, change | `hub trigger list`, `create`, `show`, `update` | `hub_trigger_list`, `hub_trigger_create`, `hub_trigger_show`, `hub_trigger_update` |
| Incoming: new secret, signing | `hub trigger rotate`, `sign` | `hub_trigger_rotate`, `hub_trigger_sign` |
| Incoming: delete | `hub trigger delete <id>` | `hub_trigger_delete` |
| Incoming: log, one call, run again | `hub trigger deliveries`, `delivery [--part n]`, `replay` | `hub_trigger_deliveries`, `hub_trigger_delivery`, `hub_trigger_replay` |
| Incoming: run refused calls again | `hub trigger recover [--since hours]` | `hub_trigger_recover` |
| Through Tico Gateway | `hub trigger relay create`, `rotate`, `delete`; `hub relay status`; `hub relay set --wake on\|off` | `hub_trigger_relay_create`, `hub_trigger_relay_rotate`, `hub_trigger_relay_delete`, `hub_relay_status`, `hub_relay_set` |
| Automations on a webhook | `hub automation list`, `create`, `update`, `delete` | `hub_automation_list`, `hub_automation_create`, `hub_automation_update`, `hub_automation_delete` |
| Outgoing: endpoints | `hub webhook list`, `create`, `show`, `update`, `rotate`, `pause`, `resume`, `delete` | `hub_webhook_list`, `hub_webhook_create`, `hub_webhook_show`, `hub_webhook_update`, `hub_webhook_rotate`, `hub_webhook_pause`, `hub_webhook_resume`, `hub_webhook_delete` |
| Outgoing: send, log, retry, example | `hub webhook send`, `sends`, `retry`, `example` | `hub_webhook_send`, `hub_webhook_sends`, `hub_webhook_retry`, `hub_webhook_example` |
| Outgoing: turn on, recover, replay | `hub webhook enable`, `recover --since`, `replay --since` | `hub_webhook_enable`, `hub_webhook_recover`, `hub_webhook_replay` |
| Wake address for a server you run | `hub relay wake-url show`, `set <url>`, `off` | `hub_relay_wake_url_show`, `hub_relay_wake_url_set`, `hub_relay_wake_url_off` |
| Sleep when idle | `hub sleep status`, `hub sleep set` | `hub_sleep_status`, `hub_sleep_set` |
| Notices | `hub notice list` | `hub_notice_list` |

The API routes are under `/api/v2/triggers`, `/api/v2/webhooks`, `/api/v2/event-types`, `/api/v2/relay`, `/api/v2/hooks/wake-url` and `/api/v2/notices`,
and in the OpenAPI file ([API](api.md)).
