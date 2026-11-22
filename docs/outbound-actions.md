# Outbound actions

The outbound gate is one queue for the sends and outside writes Tico makes for a company: a Jira transition or
comment, and later texts and webhook calls. Code in `backend/outbound_actions.py`; table `outbound_actions`.

No channel has a sender yet: the first adapter is Jira, after the Jira connection lands. Email and Slack keep their own
paths (`email_sends`, `slack_posts`) until the planned consolidation step.

## Queue an action

```python
from backend import outbound_actions

row, created = outbound_actions.enqueue(
    c, settings,
    actor="bot:release-owner", channel="jira", operation="transition", destination="HM-12",
    occurrence_key="release:v1.2:HM-12:done",
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
2. **The default** for the channel and audience: the company's setting, else On its own (`on_their_own`) inside the
   company and Ask first (`ask_first`) outside it. Relationship-level overrides come later.

An audience the adapter cannot tell is external. Ask first waits on one OK (backend/task_asks.py, subject
`outbound:<id>`) bound to the frozen action, asked of the bot's manager when that is a person who is here and the bot
may ask, else the company's default person. With nobody to ask, the action is `blocked` and says so.

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
  otherwise it is logged and waits until a person confirms it went. It is never canceled or marked not sent. A new action (a new
  occurrence key) is safe only once the original attempt provably can no longer act: finding no record of it at the
  provider is not proof, since a paused worker may still reach it. Its OK's task stays open until it ends.

`payload` is wiped when an action is done, failed or canceled; `metadata` and `receipt` stay. Rows are never pruned.

## The worker

One thread per server (`start`, restarted by the scheduler's tick), rung by each commit that queues or approves an
action, and otherwise asleep until the next row is due (at most a minute, when it also recovers stale claims and
reconciles unknown rows). Sleep when idle stays up for a row in flight or due within 15 minutes and wakes for a later
one. A rehearsal copy never runs the worker, and a dev copy empties the table.

## Adding a channel

Register an `Adapter(channel, prepare, dispatch, reconcile=None)`:

- `prepare(c, settings, actor, destination, payload)` checks the action, at enqueue and before each attempt, and
  answers `(audience, destination)`; a Problem blocks it.
- `dispatch(store, row)` makes the one provider call and answers a receipt dict or `SafeRejection(retry_after,
  error)`; it raises `Refused` for a certain refusal, anything else for unknown.
- `reconcile(store, row)`, where the provider can be asked, answers `Settled(state, receipt, error)` with state done or
  failed, or None when its record cannot tell.
