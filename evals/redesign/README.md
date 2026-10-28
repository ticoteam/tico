# Redesign scenarios

PLAN §7 scenarios for the redesign, written before the code. Each check is a real API call against the factory's
in-process hub on a fake clock (`evals/factory/world.py`). Nothing is stubbed: a check the product cannot pass yet
fails as `not built yet: <wave>`, and a check the CONTRACT cannot back fails as untestable (CONTRACT section 6 lists
the gaps).

The org is Acme: Dana (owner), Omar and Priya (people; Omar is a peer of the Engineering Lead, Priya reports to Dana),
the bots engineering-lead, builder-claude, builder-openai and release-manager. Emails are @acme.example. No real names
belong in this directory.

Run from the repo root:

    python -m evals.redesign.run                  # all named scenarios (about 40 s)
    python -m evals.redesign.run 1 6 perm-audit   # by number or name
    python -m evals.redesign.run 13 --explain     # also print each API call the scenario made
    python -m evals.redesign.run matrix --seed 1  # all-pairs matrix; prints the seed and combination count
    python -m evals.redesign.run --workflows      # workflows 1-10: a fake day each (not in the default run)
    python -m evals.redesign.record               # LIVE recorder: sends each input once to the real judge (see below)

Files
- `harness.py`: the Acme org, the API calls, `Unmet`, `BUILT`, and the helpers the cases share.
- `cases/`: caps (1-10), spend (11-15), inboxes (16-21), engine (22-31), permissions (32-35, 46), strategy (36-38),
  migration (39-44), recorder (90, the L4 recorder and replay judge).
- `cases/migration_fixture.py`: the install as the release before left it, built partly through routes that remain
  (webhook trigger, task rule, system jobs, inboxes with Inbox Manager, listening destinations, a goal) and partly by
  SQL (cron and event routines, custom wake rules, an hourly limit, turn budgets). `cases/migration.py` runs the
  automatic cutover on it (`redesign_migration.cutover`) and checks the result through the real APIs and the report:
  cutover (39), wake rules (40), runs and the old hook address (41), times (42), limits and budgets (43), restart and a
  second cutover (44). Workflow 10 runs a working day on the cut-over install.
- `matrix.py`: the seeded all-pairs plan and its skip rules (no server). `cases/matrix.py` builds and reads each row.
- `workflows.py`: workflows 1-10 (TESTING L2c), run only with `--workflows`.
- `fakes.py`: the judge replay, Slack and Gmail fakes, the incoming-webhook helpers, the outgoing receiver.
- `record.py` and `recordings/`: the recorder and its inputs (`recordings/inputs/`, anonymized, one JSON per input).
- `run.py`: prints PASS or FAIL per check; exits 1 on any FAIL.

Judge and recordings
- `ReplayJudge` answers a passed script first (a named scenario needs its exact confidence), then the recording at
  `recordings/<key>.json`, then raises `MissingRecording`. The key is a hash of (state, questions, level).
- A missing recording fails loudly with the command to re-record it: `python -m evals.redesign.record`.
- The recorder sends each input in `recordings/inputs/` once through the configured judge, using `judge.from_env()`:
  `HUB_API_URL` and `HUB_TOKEN` together send calls through the hub, whose key stays on the server; `TYPESAFE_API_KEY`
  sends them direct. With neither set it refuses to run. `python -m evals.redesign.record --only <input id>` redoes one.
- The recorder's own checks (scenario 90) run with a stub engine; the live recorder is not part of the default run.
- Every input is level `low`: the live engine takes no level, so medium and high are untested (CONTRACT section 6).
- `REDESIGN_LIVE=1` (the live sample: 5% of recorded calls, at most 10 a run) is not built; `go_live` raises.

Status
- `BUILT` (harness.py): a wave is added there when its PR merges. Its failures then print the real reason.
- Holds today: a check that passes on main now. It must keep passing. The matrix's plan checks and the trigger
  checks that existing routes already answer are of this kind.
- CONTRACT gap: a check with no route or read shape named yet. Fails as untestable until the CONTRACT names one.
- Matrix inputs for Slack are untestable: the CONTRACT names no Slack input route yet (section 6).

Adding a scenario: a `Scenario` in a case module, steps that call `org.step`, and checks that return `(ok, detail)`.
A scenario that needs extra bots passes them as `bots=`. A check must not pass when its setup did not run: raise
`Unmet` instead.
