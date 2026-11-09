# MCP end-to-end

`backend/tests/test_e2e_mcp.py` calls every remote tool the owner can see, once, through `/api/v2/mcp`, the same door
`backend/tests/test_mcp.py` uses. Each call is built from the tool's own input schema: required fields get seeded, valid
values (a task, a chat, a trigger and an automation are made first), an enum takes its first choice, and `dry_run` is set
where the tool has it. Outside network names do not resolve during the test.

- `test_every_remote_tool_answers_a_generated_call_as_its_recorded_refusal_or_success` (slow): fails on a tool that raises,
  on a refusal that is not the one recorded, on a success that is recorded as a refusal, and on recorded tools that no
  longer exist.
- `test_a_scripted_task_runs_over_mcp_from_create_to_done` (fast): create, comment, reassign, finish, read back.

## Files

- `expected-refusals.json`: tool name to the outcome it must give, either `"<http status> <code>"` or the bare protocol
  code (a missing argument has no status). Only tools that do not succeed are listed.
- `OVERRIDES` in the test: arguments for a tool that needs state no seed makes. Empty today.

## Run

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_mcp.py

## After a deliberate change

Regenerate the recorded outcomes, review the diff, then commit it:

    TICO_E2E_RECORD_REFUSALS=/path/in/scratchpad/mcp.json python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_mcp.py

Copy the file over `expected-refusals.json` only after checking each changed line is the intended change.
