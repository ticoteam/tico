Fixes seven task-rule bugs from the 2026-10-26 Tasks bug hunt (items 1, 2, 3, 9, 14, 15, 16) with a test for each; nothing is left open in this PR.

## What changes
1. **Leaving closed or done clears the old time.** Done after closed drops `closed_at`/`closed_by`; declined drops `done_at`, `closed_at` and `closed_by`. Each cleared field is recorded in the task history. The close rule needs `closed_at IS NULL`, so these tasks were never closed again.
   **One-time cleanup:** `H.clear_finished_close_times` runs on every start (cloud store and local `migrate`). It clears the close time on done or declined tasks that still have one, and records a keeper event for each. Fixed rows no longer match, so later starts find nothing. It runs through the normal deploy, not by hand. On the team install it matches these 7 done tasks, which the close rule can then close as usual 24 hours later:
   | task | done | close time kept |
   |---|---|---|
   | 55ee69c7 | Oct 4 | Oct 4 |
   | f8a7ea48 | Oct 15 | Oct 15 |
   | 29070aec | Oct 15 | Oct 15 |
   | af57ef13 | Oct 15 | Oct 15 |
   | bec68d9e | Oct 17 | Oct 16 |
   | 74e3f963 | Oct 18 | Oct 18 |
   | 4cee51f5 | Oct 20 | Oct 20 |
   Closing a done task still keeps `done_at`, because that is when the work was finished.
2. **Closing needs edit rights, not read rights.** A person may close a task if they are its requester or owner, a party to a task above it, a current delegate, or someone who moves tasks. Moving tasks means a mover team, or the environment owner, which the HTTP route passes as `mover`. These are the same people `task_update` lets change status and note. Anyone else gets `close`. Bots, the keeper and the Task Manager are unchanged. The tool text and `docs/how-it-works.md` now say this.
3. **A private parent takes its subtasks with it.** Making a task private (directly, or through a hand-off to a private-by-default bot) now makes every task below it private, with the same follow-ups the attach rule uses (`H._made_private`, now shared with `set_parent`). Attaching a task under a private parent now also makes that task's own subtasks private.
9. **A bot owning a private parent hears that its subtasks finished.** `_parent_finished` no longer skips private subtasks when the parent's owner can read the subtask. The notice names the subtask as its source, so anyone on the parent who cannot read the subtask does not see it. `stalled_tasks` now includes private tasks. Their words go nowhere new: there is no BotOps diagnostic (`_no_diagnosis` gives the cause, and only the task's own requester is told), Decide does not read them (`stall_triage.inputs`), and the Task Manager report still lists company tasks only.
14. **Renaming to a finished task's title is allowed,** as creating one is: `_retitle` checks `ACTIVE_STATUSES`.
15. **The same owner in another form** (`cmo` vs `bot:cmo`) counts as no change: no owner event, no "Open:" wake, no session demotion.
16. **A bot cannot reassign a task a person closed.** It is the same guard that already blocks status and note changes. A requester bot can still reassign a task that no person closed.

Also: `docs/system-routines.md` no longer says task rules are Unreleased.

## Tests
- `backend/tests/test_task_lifecycle_rules.py`: 14 tests, all pass. On `main` (39005141d), 13 of them fail. The 14th, which checks who may still close, passes on both by design.
- Related suites (batch, hubdb, needs-you cards, questions close, stale review, stall, stall triage, subtask results and sessions, task boundaries, task manager, task privacy and privacy reads, task relations, task rules, tasks board): 255 passed.
- Guards: `test_docs.py`, `test_openapi_v2.py`, `clients/tests`: 200 passed. No API route or model changed, so OpenAPI is untouched.
- Not run here: the full default suite and the browser scripts. Hosted PR checks run them.

## Surfaces
Server rules only, so HTTP, CLI, MCP and the web app all get them. The only client text changed is the MCP tool's. Nothing in the UI changes, so this does not need QA. Runners and harnesses are not affected.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
