# When Tico refuses a command

A refusal is an answer. Work out which kind it is before you retry anything.

Inputs: the refusal's text, with its `fix`, `link` and `retryable` fields where they are given.

Done when: the refusal has had its one right answer: a retry through `hub_api` for a permissions refusal, or one Needs you item for a `retryable: false` refusal, and the run has ended.

## Permissions
If a friendly tool is refused for permissions, retry the same action with `hub_api` before handing work back. Both use the
requester's rights. If the API also refuses, its error has `fix` (what the person clicks) and `link` (the exact setting). Ask
in one plain sentence plus that link: "I can't change Head of Engineering's model with Release Manager's rights: say go ahead
here, or change it in its settings (link)." Never ask for "the supported permission path".

## `retryable: false`
Stop. Never retry it in a loop, never `sleep` and call again, and never guess another file shape or route (the one `hub_api`
try above aside). If a person must act, ask once: one Needs you item (`playbooks/human-tasks.md`) naming the exact fix from the
refusal's `fix` and `link`, then end the run. Their answer, or the server, wakes you again. When woken again, if the item you
filed for that same fix is still open, do not file another; add to it or end the run.

## A Tico bug
When a `hub` command refuses something that should work, or Tico contradicts its manual, file it as the requester:
`hub support file "<the command you ran, its operation_id if it had one, the error code and detail, what you expected>"`.
Tell the person "Blocked on a Tico bug, reported as <ticket>" and stop. The report leaves the company: never paste credential
values, tokens, customer data or message bodies. File each bug once; keep the ticket in your task note and name it on later
runs instead of filing again. Never work around a Tico bug by hand-editing a computer (AGENT.md Boundaries).

## Waiting on a computer's update
A computer's update (any computer's, this one's too) is read once: `hub computer list` or `hub health check`. If it is still
waiting or updating, record that on your task (`--status waiting` with the reason) and end the run; read it again on your next
run. Never sleep and look again: a running turn holds your own computer's update and the work queued behind it. Never stop,
restart or rebuild another bot's turn, a build or an updater to hurry an update; stopping a turn is the person's choice.
