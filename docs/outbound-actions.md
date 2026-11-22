# Outbound actions

The outbound gate queues Jira transitions and comments, Twilio texts, Close texts and Help replies. See
`backend/outbound_actions.py` and the `outbound_actions` table.

Email and Slack use `email_sends` and `slack_posts` until their planned consolidation.

## Queue an action

```python
from backend import outbound_actions

row, created = outbound_actions.enqueue(
    c, settings,
    actor="bot:release-owner", channel="jira", operation="transition", destination="HM-12",
    connection_id=jira_tool_id, occurrence_key="release:v1.2:HM-12:done",
    payload={"to": "Done"},
    metadata={"release": "v1.2"},
)
```

Or over the API, CLI or MCP:

| | |
|---|---|
| `POST /api/v2/outbound-actions` | queue one, as a bot (`tico outbound send`, `tico_outbound_send`) |
| `GET /api/v2/outbound-actions` | yours, or all for the owner or an admin (`tico outbound list`) |
| `GET /api/v2/outbound-actions/{id}` | one, with its payload and the `shown_hash` its OK sends back |
| `POST /api/v2/outbound-actions/{id}/approve` | the person asked OKs it, by their own click in Tico |
| `POST /api/v2/outbound-actions/{id}/cancel` | cancel one that has not gone (waiting, queued or blocked) |
| `POST /api/v2/outbound-actions/{id}/confirm-sent` | a person, as themselves, confirms an unknown one went (`tico outbound confirm-sent`) |
| `GET`/`PUT /api/v2/outbound-actions/policy` | the company's policy (`tico outbound policy`, `policy-set`) |

Only a bot (or Tico itself) queues. The same `occurrence_key` always answers the action it already made. A
`task_id` must be a task the bot reads, a `connection_id` a tool it may see, `send_after` an ISO time (stored in UTC). Every listed field is bounded at enqueue (a destination at most 500
characters), so every action fits on a list page. `metadata` is
the caller's own record: kept, shown in the detail, never acted on.

An action is seen by whoever queued it, the owner and admins, and the person asked to OK it.

## The policy

One resolver (`decide`) runs when the action is queued and again before every attempt, with the audience derived
anew by the channel's adapter.

1. **The floor**, which only tightens: the company's Never for the channel and audience, and a Contacts rule
   (backend/entities/rules.py) on the destination that blocks (Never) or escalates (Ask first).
2. **The default** for the channel and audience: the company's setting, else the channel's own default (Jira: On its
   own), else On its own (`on_their_own`) inside the company and Ask first (`ask_first`) outside it. Relationship-level
   overrides come later.

An audience the adapter cannot tell is external. Ask first waits on one OK (backend/task_asks.py, subject
`outbound:<id>`) bound to the frozen action, asked of the bot's manager when that is a person who is here and the bot
may ask, else the company's default person. With nobody to ask, the action is `blocked` and says so. Who may OK it
is checked again when the OK is given and before it is used: an OK from someone no longer the bot's manager (or the
default person) is asked again of someone who is. An OK sent back or taken back, or whose task closed, stops the
action: a queued one is `blocked` at its next attempt, a waiting one `canceled` by the sweep; neither is asked again.

A bot that is onboarding, paused or archived queues nothing, and its queued actions are `blocked` before they go.

## States

`waiting_approval`, `queued`, `attempting`, `done`, `failed`, `unknown`, `blocked`, `canceled`
(docs/status-and-state.md has each one and its moves).

- **Fenced attempt.** The worker claims a due row through backend/outbox.py (a claim token). Before the provider call,
  one durable write under that token, and only while `provider_attempted_at` is empty, sets it and uses the OK. A
  worker whose claim lapsed, or a replay of the same claim, calls nothing.
- **Stale claims.** A row still claimed after two minutes goes back to `queued` only when it was never attempted; one
  that was is `unknown`, in the same transaction.
- **Answers.** A receipt is `done`. A safe rejection (a rate limit with Retry-After) is requeued in one write that
  clears `provider_attempted_at` and sets `next_attempt_at`; the OK it used carries the retry only while whoever gave
  it may still give it. A certain refusal is `failed`. Anything else is `unknown`.
- **Unknown.** Never sent again on its own, never put back in line: the worker that made the attempt may still reach
  the provider. The adapter's reconcile (outside any transaction) may settle it done or failed from the provider's own
  record; the original attempt's own answer arriving after all (matched by the row and its attempt marker) settles it;
  otherwise it is logged and waits until a person confirms it went. It is never canceled or marked not sent. An
  external one still unknown after a lookup (at once when its channel has no lookup) asks its owner once
  (backend/owner_asks.py, a people-only OK) on a small task of its own (the action id, its link and the exact
  `tico outbound confirm-sent` command), filed for the first who can read the action's task among that task's owner
  (a person), the bot's manager and the company's default person; nobody is asked when none can. The row keeps it
  (kept in the row's metadata under `_tico`, which the server owns); that person may read the action and confirm it went while they still read its task. After its first lookup a row is looked up at most hourly (`_tico.looked`), and the ask is taken
  back (its own task closed) when the row settles: a lookup, a late answer, confirm-sent. A new action (a new
  occurrence key) is safe only once the original attempt provably can no longer act: finding no record of it at the
  provider is not proof, since a paused worker may still reach it. Its OK's task stays open until it ends.

`payload` is wiped when an action is done, failed or canceled; `metadata` and `receipt` stay. Rows are never pruned.

## The worker

One thread per server (`start`, restarted by the scheduler's tick), rung by each commit that queues or approves an
action, and otherwise asleep until the next row is due. At most once a minute, after its due row, it recovers stale
claims, cancels actions whose OK was sent back, and makes a few provider lookups for unknown rows. Sleep when idle stays up for a row in flight or due within 15 minutes and wakes for a later
one. A rehearsal copy never runs the worker, and a dev copy empties the table.

## Help tickets

The Support Agent can queue `help-ticket` actions for its own open tasks filed by the matching HQ watcher.
Use the HQ ticket ID as the destination and the Support task as `task_id`. The `reply` operation takes `{"body":
"<reply>"}`. Use `answer` for a docs answer on a ticket triaged as a question, with the same body plus
`last_message_id` (the latest customer message ID from `hq-tickets show`, or zero for the original request).
HQ's confirmed receipt records the reply on the task and settles an `answer` through the task's normal Done step.
An answer with a newer customer message waits for the Support Agent to read it and queue a fresh answer.

Help replies default to Ask first. The Support Agent's effective sending mode can tighten the outbound policy.
For a Support-filed Engineering subtask with a linked Tico GitHub issue, its PR merge or Done queues one follow-up
on the Support task through the same gate. A merge or Done does not establish that a release is available.
See [Support setup](support.md#set-it-up) for the staff credential.

## Jira

`backend/outbound_jira.py`. Destination: the issue key (`HM-12`); `connection_id`: the Jira connection, which must be a
record integration (an Everyone API tool an administrator marked as holding records, on one Jira site) with a shared
read and write key: the same rule, key lookup and deadline-bounded call as the background reads
(backend/item_sources/jira.py). The bot (or person) queuing it must hold Read and write on the exact key a write goes with (the
oldest read and write key; a grant a person passed on to their bot is stored at no more than their own); Tico itself always may. The connection's Jira site is
frozen into the payload (`site`), so an OK shows and binds it with the key, and an attempt on another site is blocked. Anything else is refused at enqueue and blocked at an attempt, so a grant taken back stops a queued action.
While an action to it may still go or is unknown, the connection's base URL and sites cannot move to another site
(tool_services.referenced). The audience is external,
but a record write is bookkeeping, not a message to people, so `jira` goes On its own until the company sets
`jira:external` to Ask first or Never.

| operation | payload | the write |
|---|---|---|
| `transition` | `{"to": "Done"}` | the transition Jira offers from the issue's status whose target status is `to`; none offered is a certain refusal |
| `comment` | `{"body": "text"}` (at most 30,000 characters) | a comment, blank lines making paragraphs, ending with the action's marker `[tico:<10 hex of sha256(occurrence_key)>]` as its own last paragraph |

Before the write it asks whether the effect is already there (the issue already in the status; a comment among the
newest 100, read 20 at a time (up to 2 MiB a page) and in halves when a page is too long, whose last paragraph is one text node that is
exactly the marker; a comment or node that cannot be read, or one comment still too long alone, makes the lookup fail): found, the action is done with no write (`"already": true` in the receipt). Reconcile
uses the same lookup to settle an unknown row done; a row it cannot prove stays unknown for the owner. A 429 is a safe
rejection after its Retry-After (else a minute), as is anything that fails before the write, a lookup answer cut off
or not the shape asked for included. Jira's 400, 401, 403 or
404 to the write is a certain refusal; any other answer, or none, is unknown.

### Release close-out

backend/release_closeout.py queues the Jira side of a released task filed for a Jira issue (its one primary link):
once every merged PR of the task is in an actual release tag (the In a release reading of saved releases, no new
GitHub call), the task's forward or done move out of its Release visit queues a `transition` to `Done` (Jira's default
Done status name) and one `comment` naming each tag with its GitHub release link, as Tico itself, with occurrence keys
`closeout:<task>:<repo>@<newest tag>:jira:<operation>` (the newest release holding its merges; none is queued when one
published at the same time or later was announced for the task already, so a reopen or a removed PR adds nothing).
Only Tico itself may queue a `closeout:` key (422 for anyone else). Both carry `closeout:
true`, the issue's saved Ready and the `done` target in their payload: the sender reads the issue right before the
write, and while it is withdrawn, gone (404, at the read or the write) or closed in another Done status it waits an
hour and tries again (not counted as a retry, so the retry cap never ends it). Each is on its own: one refused is logged (`closeout.skipped`) and the other goes. A record integration
with a shared write key is the administrator's explicit opt-in for Tico to keep linked issues in step, and a close-out
also needs a merged PR in a real release tag. No Slack reply in the task's thread yet: it comes back when Slack sends
move into this queue, where org rules and privacy apply to every send.

## SMS

Use `operation: "send"`, an E.164 phone number as the destination and the provider's Tools connection as
`connection_id`.

| Channel | Payload | Tools connection |
|---|---|---|
| `sms` | `{"from":"+12025550100","body":"Your appointment is ready."}` | Twilio at `https://api.twilio.com`. Use the Twilio template with an Account SID and Auth Token or API key pair, or a Basic password key with the Account SID as its username. Grouped templates can keep the Account SID as a credential. |
| `close_sms` | `{"from":"+12025550100","body":"Your appointment is ready."}`. Add `lead_id` to link the SMS to a Close lead. | Close CRM at `https://api.close.com` with a Basic user API key. The sender must be an internal Close number. |

Both connections must be Everyone API tools marked as record integrations by an administrator. The bot needs
Read and write on each shared key used. Such a connection is reserved for the queue: through the Tools proxy it only
reads (GET and HEAD); every other request, MCP calls included, is refused, so use another connection for other writes
to Twilio or Close. The Twilio
account is frozen in the approval preview. Both channels are external and default to Ask first. A Contacts block on
`channel:sms` applies to both. The destination is matched by canonical number against Contacts phone fields and phone
identifiers. Rules follow merges of contacts and rule targets. The sender checks policy again in the transaction
that authorizes the exact credential it will use. If policy now requires an OK, the action waits for approval.

Twilio creates a Message. Close creates an SMS activity with status `outbox`. The receipt's `provider_id` records
provider acceptance. A 429 requeues using the first Retry-After value (capped at one day) or a one-minute fallback. A vault outage
before dispatch also requeues. These endpoints
have no documented lookup by a client reference. A lost or unreadable answer goes to the owner with the action
still unknown, with no automatic resend. [Twilio Messages API](https://www.twilio.com/docs/messaging/api/message-resource)
[Close SMS API](https://developer.close.com/api/resources/activities/sms/create)

## Adding a channel

Register an `Adapter(channel, prepare, dispatch, reconcile=None)`:

- `prepare(c, settings, actor, destination, payload)` checks the action, at enqueue and before each attempt, and
  answers `(audience, destination)`; a Problem blocks it.
- `dispatch(store, row)` makes the one provider call and answers a receipt dict or `SafeRejection(retry_after,
  error)`; it raises `Refused` for a certain refusal, anything else for unknown.
- `reconcile(store, row)`, where the provider can be asked, answers `Settled(state, receipt, error)` with state done or
  failed, or None when its record cannot tell.
- `defaults`, optional: the channel's own choice per audience when the company set none.
- `freeze(c, connection_id, destination)`, optional: fields frozen into the payload at enqueue (what an OK shows).
- `approval_prompt`, `action_label(destination)` and `provider_name`, optional: text for approval and owner asks.

Import the module at the end of `backend/outbound_actions.py`, so it registers whenever the gate is loaded.
