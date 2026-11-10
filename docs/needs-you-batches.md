# Needs you batches for external agents

An external agent can walk a human through what bots need from them, record responses, then apply the responses together.
Connect with that human's personal token ([Connect an external agent](connect-an-agent.md)); bot tokens cannot use batches.
The caller has the human's own rights. These internal APIs may change between releases.

[Use Tico](using-tico.md) · [API](api.md) · [Glossary](glossary.md)

## One complete walkthrough

Call the MCP tools with these arguments. Every write supports `operation_id`, used as its idempotency key: choose a fresh id
per action and reuse that id with the same arguments for a retry. Replace `<batch-id>` with the start response's `id`.

1. `tico_needs_you_start({"bot":"next","operation_id":"example-start-1"})` starts with the bot that most needs this human.
   Omit `bot` for that default, name a bot for its items, or use `all:true` for all bots. The response contains `item`, `total`,
   `position`, `alerts`, `lineup` and `resumed`. An existing open batch resumes; `fresh:true` abandons it and starts again.
   The human's own tasks are separate: use `tico_task_list` for them.
2. Read the current item's question or exact proposed action. For a question the human answers, record their own words:

   ```json
   {"batch":"<batch-id>","kind":"decide","decision":"answer","text":"Use the example app's welcome guide.","operation_id":"example-answer-1"}
   ```

   Call `tico_needs_you_respond` with that body. Recording a response does not apply it. For an OK use `decision:ok`, or
   `send_back` with the text of what to change; do not substitute a general yes. An OK bound to what was shown (a send,
   a spend, a publish) is refused here: the person OKs it from its preview in the app. `item` can select another item by
   its 1-based number.
3. `tico_needs_you_next({"batch":"<batch-id>","operation_id":"example-next-1"})` advances one item. Repeat respond/next,
   using a different action id each time. When `end:true`, read the returned `summary` to the human.
4. After the human confirms that summary, call
   `tico_needs_you_commit({"batch":"<batch-id>","operation_id":"example-commit-1"})`. The batch contract requires this final
   confirmation. Decisions apply as that human; questions, instructions and rules go to each item's bot. Read the per-item
   results and report failures accurately; do not claim that recording a response completed an action. `up_next` names the next bot.
5. If the human stops before committing, call
   `tico_needs_you_abandon({"batch":"<batch-id>","operation_id":"example-abandon-1"})`. Responses are discarded; items remain
   available in a future batch. Abandon is an alternative to commit, not a rollback of committed actions.

## HTTP equivalent and response kinds

Send `Authorization: Bearer <personal-token>` and a distinct `Idempotency-Key` for each POST. Retrying the same body with its
original key returns the first receipt; a changed body with that key returns `409 idempotency_conflict`.

| Step | Route | Body |
|---|---|---|
| Start | `POST /api/v2/batch` | `{"bot":"next"}`; omit `bot` for all items at the HTTP level |
| Resume | `GET /api/v2/batch` | none; returns `batch`, or null |
| Next | `POST /api/v2/batch/<id>/next` | `{}` |
| Respond | `POST /api/v2/batch/<id>/respond` | `{"kind":"decide","decision":"answer","text":"Use the welcome guide."}` |
| Commit | `POST /api/v2/batch/<id>/commit` | `{}`; only after summary confirmation |
| Abandon | `POST /api/v2/batch/<id>/abandon` | `{}` |

`kind` is `decide`, `needs_info`, `instruct`, `rule`, `skip` or `later`. `decide` takes `ok` or `send_back` on an OK,
`answer` on a question, and `done` or `close` on a task filed for the person. `needs_info`, `instruct` and `rule` send
the human's text to the originating bot on commit.
`later` takes a timezone-bearing ISO `until`; `skip` leaves the item for another pass. HTTP also accepts `heard` for a voice readback.
Responses can be replaced while the batch is open. A batch belongs to its human: another human gets `404`; an operation on a closed
batch can return `409 state`. See `backend/batch.py`, `backend/models.py` and `clients/hubtools.py` for the current contract.
