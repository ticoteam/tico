# CLI end-to-end

`backend/tests/test_e2e_cli.py` finds every `hub <command>` that `templates/` and `skills/` tell a bot to run, matches it
to the CLI's own parser, and runs it once as the owner (`hubcli.main`) against a live server with seeded objects: a task,
a bot chat message, a trigger, an automation, and a file. Arguments come from each command's parser: required options,
the first choice of each required exclusive group, and `--dry-run` where the command can preview. A command that needs
more than the parser says is given it in `EXTRA` in the test.

A command must exit 0, or be refused the way `expected-refusals.json` records. A usage error, a silent exit, or an
exception fails the test. A `hub` word that is not a command must be listed in `stale.json` with its reason.

## Files

- `expected-refusals.json`: command path to the refusal it must give (the error code of the CLI's JSON, or the first line
  it printed). Commands that exit 0 are not listed.
- `stale.json`: `hub` words that name no CLI command: prose in running text, or a stale reference to fix. Remove an entry
  when its reference changes; the test fails if a listed word is no longer used.
- `skipped.json`: commands the sweep does not run, with the reason (a command that fetches a public page).

## Run

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_cli.py

About 95 seconds locally; the parser is built once per run.

## After a deliberate change

    TICO_E2E_RECORD_REFUSALS=/path/in/scratchpad/cli.json python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_cli.py

Review the file against `expected-refusals.json`, then copy it over. Commands the seeds do not reach (for example agent
pairing, approvals, goals, KPIs, markets) are refused as `not_found`; seeding them is the way to make those run.
