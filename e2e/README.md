# End-to-end safety net

Run every kind and print one verdict per kind:

    python -m e2e.run                      # all kinds; exit 0 only when every kind passes
    python -m e2e.run --kind contract      # one kind (repeatable)
    python -m e2e.run --rename-map PATH    # read goldens and before-states through a rename map (see Renames)
    python -m e2e.run --record             # re-record the contract goldens (review the diff before committing)
    python -m e2e.run --old-server-ref REF # rollback: boot the copy with the server at REF (TODO, see below)
    python -m e2e.run -v                   # full pytest output for every kind

Each kind is one pytest run with the `slow` tests included; the default `pytest` run leaves those out. Running a
single file the same way: `python3 -m pytest -q -o addopts= -m "" -p no:cacheprovider e2e/tests/test_upgrade.py`.

## Kinds

| kind     | file                                  | what it proves |
|----------|---------------------------------------|----------------|
| checks   | `tests/test_renames.py`, `tests/test_preserve.py` | The comparison helpers and the rename map, on small databases where the answer is known. Fast. |
| contract | `tests/test_contract.py`              | The main v2 responses to the fixed scenario (tasks, task detail, conversations, messages, needs-you, bots, status, goals, KPIs, files, pages, automations) and the events catch-up equal the goldens in `goldens/`. |
| upgrade  | `tests/test_upgrade.py`               | A populated database at the current schema, copied and upgraded by `Store.initialize()` and then by two server boots: every row and column is kept, integrity and foreign keys are clean, a second initialize and a second boot change no data, and the owner, a member and a bot each read the same scenario tasks and messages as before. |
| rollback | `tests/test_rollback.py`              | A copy taken with SQLite's backup API boots and serves the scenario with the same answers. |

The scenario (`scenario.py`) runs on the `api` fixture: a fresh temporary database and a fictional company (Acme).
It drives the v2 API as a person and as a bot (a runner claims the bot's turn): a chat and a reply, a task created,
assigned, moved to done and closed, a bot turn that uploads a file and a page and asks for a spend approval (one
declined, one left open), a goal with a KPI, a reading and a check-in, and an automation. Rows the API does not reach
(rate limits, service health, host performance points, runner load samples, watcher runs) are written directly.

## Goldens

`goldens/*.json` hold `{request, status, body}` for each capture. A normal run never rewrites them. Re-record only on
purpose (`--record`), and review `git diff e2e/goldens` before committing. The normalizer (`normalize.py`) replaces
the scenario's ids by their names, other ids by numbered labels, timestamps, digests and cursors by fixed words,
durations and opaque versions by `<volatile>` and `<opaque>`, and rounds floats. A field that appears, disappears
or changes shape still shows.

## Renames

A rename codemod passes its old-to-new map as YAML (`--rename-map PATH`):

    fields:  {old_key: new_key}          # golden JSON keys, at any depth (the API contract)
    columns: {"table.old_col": new_col}  # a column renamed in place; the table is named by its old name
    tables:  {old_table: new_table}      # a table renamed

`scripts/cleanup/renames.yaml` is such a map, with more sections (`api_fields`, `api_kept`, `golden_fields`; see
`renames.py`). To prove a codemod changed only names, run the goldens of the commit before it through its map:
`python3 scripts/cleanup/check_goldens.py REF` compares them with the goldens in the working tree.

`fields` renames the golden side before comparing, so the contract passes only when the renamed code still returns the
same data under the new key. `columns` and `tables` do the same for the upgrade's before-state. Without a map, the
kinds compare as they are on main. The map is read by `renames.py`; a malformed map is refused before any kind runs.

## Upgrade details

- The fixture seeds rows after its own boot, so the upgrade baseline is a database that one server boot has settled.
- Change counters (`roster_clock`, `provenance_clock`, `row_clock`) count every write, no-op writes included, and the
  caches read them. They are compared as "never went back", not as equal (`preserve.COUNTERS`).
- Each viewer check runs its own bot turn (a bot reads with an attempt token, which only a live turn holds). The
  messages that turn writes are new, so the viewer comparison covers only the tasks and messages the database held
  before the check.

## Rollback (TODO)

`--old-server-ref REF` is passed through as `TICO_E2E_OLD_SERVER_REF`. Today the rollback kind skips when it is set,
so a run with it is not a pass. The real rollback boots the snapshot with the previous release's server, which needs
that release's checkout and its own migration path. Until that is built, the kind checks only that the current code
boots and serves a copy.

## Not in this package

Browser UI journeys (`scripts/ui-tests.cjs`), the Docker install journey (`scripts/journey-test.sh`), bot, MCP and CLI
checks, and the factory evals (`evals/factory/`) run from their own scripts and kinds, not from `python -m e2e.run`.

## On RunsOn

Push the branch, then run from the worktree:

    python3 scripts/remote.py --setup python --minutes 20 -- python3 -m e2e.run
