# Run a lens

A task or a message from the owner that asks for a lens ("run Scenarios on our strategy"), or a moment where one would
help: a bet tripped, a weekly review with no lens in 90 days. A lens is a short set of questions about the published
strategy, answered once, and the server writes the result (`hub lens list` shows them; `docs/lenses.md` explains them).
Budget 15 minutes.

Inputs: the owner's ask or the trigger, `hub lens list`, and the published strategy (`hub strategy show`).

**Never publish and never write the draft.** A lens run you start files one `strategy_draft` proposal at most, and the
server files it, not you. The owner confirms or rejects it. **Never start a run unasked**: a weekly review only
suggests one.

---

## 1. Pick the lens

`hub lens list` for the ids and what each is for. Scenarios or Pre-mortem fit a tripped bet; Blue Ocean fits "are we
different enough"; Seven Powers fits "what stops a bigger company copying us". If the owner named one, use it. A
market lens (Where to Play, Five Forces) is the Librarian's, not yours.

## 2. Answer from the record

`hub strategy show` is the published strategy and the only thing the lens reads. Fill each step from it, from the
market pages (`hub doc search --market`) and from what the owner said in the task. Write the answers to a file as
`{"step key": answer}`: a list step is a list of short strings, a text step a few sentences. Skip a step you cannot
answer from the record; do not invent. The result says what it assumed for a skipped step, so a skip is honest.

## 3. Run it

    hub lens run <lens id> --answers-file answers.json

It waits up to a minute and prints the run. If it is still running, `hub lens show <run id>` later. If the status is
`no_model` the server has no model set up: tell the owner in one line and stop.

## 4. Finish

If the run filed a proposal, its id is on the run (`proposal_id`) and the owner's Decide card says it came from the
lens. If it did not, the lens found nothing to change, and that is a finding. Finish with
`hub task update <id> --status done --note "<lens, run id, proposal id or none>"` and one line in `state.md`.

**Done when:** the run's result is read, its proposal (if any) is named in the note, and the task is done.
