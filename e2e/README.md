# End-to-end safety net

Run every kind and print one verdict per kind:

    python -m e2e.run                      # all kinds; exit 0 only when every kind passes
    python -m e2e.run --kind contract      # one kind (repeatable)
    python -m e2e.run --rename-map PATH    # read goldens and before-states through a rename map (see Renames)
    python -m e2e.run --record             # re-record the contract goldens (review the diff before committing)
    python -m e2e.run --old-server-ref REF # rollback: the old server is the checkout at REF (see Rollback)
    python -m e2e.run -v                   # full pytest output for every kind

Each kind is one pytest run with the `slow` tests included; the default `pytest` run leaves those out. Running a
single file the same way: `python3 -m pytest -q -o addopts= -m "" -p no:cacheprovider e2e/tests/test_upgrade.py`.

## Kinds

| kind     | file                                  | what it proves |
|----------|---------------------------------------|----------------|
| checks   | `tests/test_renames.py`, `tests/test_preserve.py` | The comparison helpers and the rename map, on small databases where the answer is known. Fast. |
| contract | `tests/test_contract.py`              | The main v2 responses to the fixed scenario (tasks, task detail, conversations, messages, needs-you, bots, status, goals, KPIs, files, pages, automations) and the events catch-up equal the goldens in `goldens/`. |
| upgrade  | `tests/test_upgrade.py`               | A populated database at the current schema, copied and upgraded by `Store.initialize()` and then by two server boots: every row and column is kept, integrity and foreign keys are clean, a second initialize and a second boot change no data, and the owner, a member and a bot each read the same scenario tasks and messages as before. |
| rollback | `tests/test_rollback.py`              | A backup taken before an upgrade, served by the old server (the checkout at `--old-server-ref`, in its own process), answers the scenario exactly as before. Without a ref it only checks this checkout's server on a copy (see Rollback). |
| roles    | `tests/test_roles.py`                 | Conversation visibility per role on the scenario: the owner, a member, a shared-room member and a bot each read exactly the conversations the rules allow (personal rooms, shared CPO room, a direct conversation, a task thread), in reads, lists, the owner's bot filter, `chat_with` and outlines. Not covered: a bot manager (a bot above another in the `reports_to` chain, which `auth.py` `bot_manager` reads); the auth model has no guest role. |

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

## Rollback

The real rollback takes one step per process, so the old server runs its own code and never this checkout's:

1. The old server (the checkout at REF, extracted with `git archive`; this working copy is not touched) populates a new
   database with the fixture's company and the scenario, then takes SQLite's backup-API copy: the pre-upgrade backup.
   The scenario's direct rows are written under the old schema's column names (`renames.old_columns`).
2. The old server boots a restored copy of that backup and must answer the scenario exactly as it did before the
   backup (`normalize`d, so ids and timestamps do not count).

`--old-server-ref REF` is passed through as `TICO_E2E_OLD_SERVER_REF`. The ref must be a commit in this checkout: a
missing ref fails the test, it never skips. A skipped rollback is not a rollback, and `python -m e2e.run` counts a skip
as not passing.

Without a ref, the kind runs the same check with this checkout's server. That proves the copy restores and serves,
but it is not a rollback: the code that wrote the backup is the code that reads it. Use a ref for a real check. The
ref is the pre-upgrade commit (for the cleanup, `8d2e55c7e`, the code running before the codemod).

Not covered here: the old runner and the old Mac app, which the plan's rollback kind also names. Those need their
own release builds and are not started by this kind.

## Outside `python -m e2e.run`

The plan's e2e kinds, and where each one lives. The browser and bot checks run from their own scripts or test files,
not from these kinds.

| Plan kind | Where it lives | Not covered |
|---|---|---|
| Browser journeys | `scripts/ui-tests.cjs --group e2e` (`ui/tests/e2e-*.cjs`), on RunsOn | No rollback smoke journey |
| Bots end-to-end | `backend/tests/test_e2e_bots.py`, `test_e2e_bot_rename.py` | A real Rust runner on a demo computer |
| MCP | `backend/tests/test_e2e_mcp.py` (every remote tool) | Claude Code and Codex sessions that run a scripted task (need real harnesses and model access) |
| CLI | `backend/tests/test_e2e_cli.py` (every hub command in templates and skills) | |
| Webhooks and triggers | `backend/tests/test_e2e_webhooks.py` (generic JSON, signed delivery) | No Make-shaped payload: no backend code names Zapier or Make, so it would run the same path |
| Integrations | `backend/tests/test_vendor_contracts.py` (HQ billing and storage, Stripe, QuickBooks, Xero, Slack, GitHub), `test_granola_recorded_shapes.py`, `test_mail_api.py`, `test_google_connect.py` | Mail and Google only against their unit fakes |
| Templates | `backend/tests/test_e2e_templates.py` | |
| `hub sql` | `backend/tests/test_sql_parity.py` (each caller sees what the API shows it; a reassigned bot is refused), `test_sql_scale.py` | Not an e2e kind; the stable query set is not run against an upgraded backup copy |
| Upgrade, interrupted | Unit: `backend/tests/test_migrations.py` (a failed migration rolls back and runs once fixed), `test_upgrade.py` | No process killed partway through an upgrade of a real backup |
| Hosted | not built | Needs staging HQ |
| Mac app | not built | macOS GUI, pairing |

The vendor contract fixtures (`backend/tests/fixtures/vendors/`) are derived from Tico HQ's sender and the vendors'
public API references, not live captures. Each names its source. A renamed field fails the test that reads it.

## Not in this package

Browser UI journeys (`scripts/ui-tests.cjs`), the Docker install journey (`scripts/journey-test.sh`), bot, MCP and CLI
checks, and the factory evals (`evals/factory/`) run from their own scripts and kinds, not from `python -m e2e.run`.

## On RunsOn

Push the branch, then run from the worktree:

    python3 scripts/remote.py --setup python --minutes 20 -- python3 -m e2e.run
