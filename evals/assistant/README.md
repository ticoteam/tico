# Assistant evals

Scripted chats from one person (Dana, the owner) to the Assistant template (`templates/catalog/assistant`, slug `coo`),
scored with a real model. The CI test (`backend/tests/test_assistant_evals.py`) checks the cases without a model; the
model run is on demand only and never in CI. The shape follows `evals/botops/`.

    python3 evals/assistant/run.py --repo <tico checkout> [--model claude-haiku-5-5] [--judge claude-opus-5-5] \
        [--only queue,approvals,jobs] [--case id,id] [--samples 2] [-j 6] [--tag base-haiku] [--surface both]

- `--repo` names the Tico checkout. Its template and its `clients/hubcli.py` parser are used, so the same cases score
  main against a branch.
- `--model` is the model under test. `--judge` (default `claude-opus-5-5`) grades each reply against the case's rubric.
- `--samples N` runs each case N times. Results report a pass rate per case, not one run.
- Output goes to `evals/assistant/out/<tag>/` (gitignored): `<case>.s<n>.json` per sample, and `summary.md`.

The runs use the person's Claude subscription through headless `claude -p`. A usage limit stops the run partway; the
summary then covers only the samples that finished.

## What one sample does

1. Copies the template to a sandbox with the placeholders filled (company Acme, assistant "Tico Assistant"). The card
   and the reports are left out, as the runner leaves them.
2. Puts a fake `hub` first on `PATH` (unless `--surface mcp`), and attaches the fake hub MCP server (unless `--surface cli`). It parses each call with the repo's real parser, so a command the CLI does not have
   fails the sample. It logs every call and answers from the case's `hub:` entries; a call with no entry gets a generic
   reply. `--on-behalf-of` is ignored when answers are matched, so an answer does not depend on whether the template
   cites the turn itself.
   Writes answer as the real hub does, with the changed object (`task close` gives `task.status` closed). The fake hub
   keeps the ids a run closed or updated, and the automations it enabled or disabled, in `state.json` next to the log;
   a later `task list` or `automation show` from canned output shows that state. A canned row that matches still wins.
3. Builds the prompt the way the runner builds an Assistant DM turn (`runner-rs/src/prompt.rs`): the standing opening,
   the personal-chat block ("You are privately assisting human:dana"), the case's `history` if it has one, then
   "Current message from human:dana:" and the person's message. The runner's full prompt is longer; this keeps the parts
   that decide what the Assistant may do.
4. Checks the run. A sample passes when every check holds:
   - `must`: each regex matches some logged hub call, or the reply when it starts with `reply:`.
   - `must_not`: no logged call (or reply, with `reply:`) matches.
   - `invalid`: no hub call is rejected by the parser.
   - the judge passes the reply against `reply:`, reading the person's message, the rubric, the hub calls, and the
     canned record (numbers in a reply must match it).

## Surfaces

Production gives the Assistant the hub two ways: the hub as an MCP server (`clients/hubmcp.py`, the tool listing for its
kind, here `assistant`, about 200 tools that Claude Code defers behind tool search) and the `hub` CLI on PATH. `--surface`
picks which the run has: `both` (the default, production) attaches the MCP server and keeps `hub` on PATH; `mcp` attaches
the server and takes `hub` off PATH; `cli` is the old harness with no MCP server. An MCP `tools/call` is turned into the
same canonical line the CLI logs (the typed command words, then the id, then `--key value` per argument), so one set of
regexes checks either surface. The summary reports the calls and the invalid calls per surface, and an MCP call the
tool's input schema rejects counts as invalid, as a CLI call the parser rejects does.

## Cases

`cases/*.yaml`, a list of cases. Each case:

```yaml
- id: queue-undo-close            # unique across files
  group: queue                     # the file name; --only takes it
  prompt: "undo that"              # the person's message
  history:                         # optional: earlier words of this chat, as the runner shows them
    - {from: "human:dana", body: "..."}
  hub:                             # canned answers, matched against the call without --on-behalf-of
    - match: '^assistant change list'
      out: {changes: [...]}        # a string prints as is; anything else prints as JSON
  must: ['^assistant change list\b']
  must_not: ['^task (create|close)\b']
  reply: |                         # the rubric for the judge
    What a good turn does, in plain words.
```

Write canned outputs shaped like the real routes (`backend/hubdb.py` `needs_you`, `task_origin`), with fictional people
and companies only. Regexes are Python `re` against the call's words (`^task close`, not `task close`).

The commands the assistant tools branch adds (`needs-you show`, `origin show`, `assistant change list|restore`) are in
the cases, and the CI test allows them to be absent on main. Delete an entry from `NEW_COMMANDS` in the test when its
command lands, so it is checked against the real parser.

Calls that did not work are visible: each logged call carries its `exit` and a short `out`. A `must` regex matches
only calls that worked (exit 0), unless the case sets `must_allow_failed: true`; `must_not` still sees every attempt. The
judge sees a failed call marked FAILED. Inherited `HUB_*` and `TICO_*` variables never reach the run. On `--surface mcp`
a `hub` stub on PATH exits 127 and logs the attempt as an invalid call.

Known limit: the call log (`EVAL_LOG`) is a file the model could write to with Bash. Treat a run whose log looks forged as
void; the harness does not guard against it.

## Adding a case

Turn a 👎 on an Assistant reply, or a chat that went wrong, into a case: the person's words as the prompt, the
canned record that the failure saw, `must` for the call the Assistant should have made, `must_not` for the one it
should not, and a rubric that names the fix. Run it on main first; a case that main already passes is still worth
keeping as a guard.

## Layout

- `run.py`: the harness (sandbox, fake hub, prompt, checks, judge, summary).
- `cases/queue.yaml`: the Needs you queue, the sources that file it, undo, and the morning summary.
- `cases/approvals.yaml`: named read-backs, declines, approvals kept back when the decisions setting is off, text that
  asks the Assistant to act, and the one-card sweep.
- `cases/jobs.yaml`: launch status, routing to a bot, a BotOps hand-off, a decision for the CFO, a routine change, what a
  bot did, a doc search, and a task for a person.
- `BASELINE.md`: the numbers for main, by model.
