# Outbound actions

The outbound gate is one queue for every send and outside write Tico makes for a company: a Slack reply, and later a
Jira transition, a text or a webhook call. Code in `backend/outbound_actions.py`; table `outbound_actions`.

Today one channel has a sender: `slack`, an internal post as a bot to a channel on the Slack channel list. Email and
Slack storage (`email_sends`, `slack_posts`) are unchanged; Slack posts made through the gate are handed to the
existing gateway outbox.

## Queue an action

```python
from backend import outbound_actions

row, created = outbound_actions.enqueue(
    c, settings,
    actor="bot:ops", channel="slack", operation="reply", destination="#release",
    occurrence_key="release:v1.2:slack-reply",
    payload={"bot": "ops", "text": "Shipped in v1.2", "thread_ts": "1700000000.000100"},
    metadata={"release": "v1.2"},
)
```

Or over the API, CLI or MCP:

| | |
|---|---|
| `POST /api/v2/outbound-actions` | queue one (`tico outbound send`, `tico_outbound_send`) |
| `GET /api/v2/outbound-actions` | yours, or all for the owner or an admin (`tico outbound list`) |
| `GET /api/v2/outbound-actions/{id}` | one, with its payload and the `shown_hash` its OK sends back |
| `POST /api/v2/outbound-actions/{id}/approve` | the person asked OKs it, by their own click in Tico |
| `POST /api/v2/outbound-actions/{id}/cancel` | cancel one that has not gone, or whose outcome is unknown |
| `GET`/`PUT /api/v2/outbound-actions/policy` | the company's policy (`tico outbound policy`) |

The same `occurrence_key` always answers the action it already made, whatever else is sent.

## The policy

One resolver (`decide`) runs when the action is queued and again before every attempt.

1. **The floor**, which only tightens: the company's Never for the channel and audience, a bounced email address
   (`email_suppressed_recipients`), and a Contacts rule (backend/entities/rules.py) that blocks (Never) or escalates (Ask
   first).
2. **The default** for the channel and audience: the company's setting, else On its own (`on_their_own`) inside the
   company and Ask first (`ask_first`) outside it. Relationship-level overrides come later.

The audience is worked out on the server by the channel's adapter from the destination; one it cannot tell is
external. A Slack channel on the channel list is internal, and the gateway asks Slack again before it posts: a channel
shared outside the workspace since is refused there and the action is `blocked`.

Ask first waits on one OK from a person (backend/task_asks.py, subject `outbound:<id>`), bound to the frozen action:
asked of whoever queued it when that is a person, else the bot's manager, else the company's default person.

## States

`waiting_approval`, `queued`, `attempting`, `done`, `failed`, `unknown`, `blocked`, `canceled`
(docs/status-and-state.md has each one and its moves).

- **Fenced attempt.** The worker claims a due row through backend/outbox.py (a claim token). Before any provider call,
  ONE write under that token sets `provider_attempted_at` and uses the OK. A worker whose claim lapsed calls nothing.
- **Stale claims.** A row still claimed after two minutes goes back to `queued` only when it was never attempted; one
  that was is `unknown`, in the same transaction.
- **Answers.** A receipt is `done`. A safe rejection (a rate limit with Retry-After) is requeued in one write that
  clears `provider_attempted_at` and sets `next_attempt_at`; the OK it used still holds for the retry. A certain
  refusal is `failed`. Anything else is `unknown`.
- **Unknown.** Never sent again on its own. The adapter reconciles where it can look (Slack: the gateway's post); when
  it proves the provider never had it, the row goes back in line. Otherwise an outside action gets one owner ask (a task
  in their Needs you) and an internal one a log line. Cancel says it must not be tried again.
- **Handoff.** Slack posts go to the gateway's outbox (`slack_posts`) in one transaction with the row's handoff mark.
  The message carries the action's id and occurrence key, and the gateway settles the action in the same write that
  records the post: `sent` is done, `uncertain` unknown, `failed` failed, a Slack Connect cancel blocked.

`payload` is wiped when an action is done, failed or canceled; `metadata` (who, what for) and `receipt` stay. Rows
are never pruned.

## The worker

One thread per server (`start`, restarted by the scheduler's tick), rung by each commit that queues or approves an
action. A sweep every minute is the backstop: it recovers stale claims and reconciles handed-off and unknown rows. A
rehearsal copy never runs it, and a dev copy empties the table.

## Adding a channel

Register an `Adapter(channel, prepare, dispatch, reconcile=None)`:

- `prepare(c, settings, actor, destination, payload)` checks the action and answers `(audience, destination)`.
- `dispatch(store, row)` makes the one provider call and answers a receipt dict, `HandedOff(receipt)` or
  `SafeRejection(retry_after, error)`; it raises `Refused` for a certain refusal, anything else for unknown.
- `reconcile(c, row)`, where the provider can be asked, answers `Settled(state, receipt, error)` or None.
