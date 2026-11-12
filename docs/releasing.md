# Releasing

A release is a version tag. Everything it ships is built once, by the Release validation run for that commit and
version, and that same build is what the gate tests. Pushing `vX.Y.Z` runs `.github/workflows/release.yml`, which
builds nothing: it takes that run's runner binaries and image digests, builds the installer bundle and publishes the
GitHub release. Running installations look for that release and offer it as
"Update to vX" in the sidebar. A release is not held for its notes: the release command tags the validated commit, and the notes are on the
release's approval issue (label `release-approval`), built from the pull requests it ships. No separate product
announcement is required.

**One release path.** On a team that runs Tico with a Release Owner bot, only the Release Owner publishes
releases. A Claude Code session or any other outside session that finishes work hands it to the Release Owner on a
task ("Release vX.Y.Z: <what>") instead of pushing the tag itself; the Release Owner tags, rolls out and records it.
Tico itself never moves a task on a tag, release or deploy; it only marks the merged PR links the running release
contains as Shipped (backend/github.py). Routines can run `on: release_published` for one task per release.

Tico's own repository is the exception: it releases itself through [the release train](#the-release-train) in
GitHub Actions. No bot tags or rolls out Tico releases. Reviewed work reaches main through the
[merge queue](#merge-queue).

The Engineering pipeline, whose Merge and Release stages queue the PRs and wait for a published release, comes in a
later 0.6 phase ([Engineering](pipelines-v3.md#engineering)).

## The release train

Tico's own releases (ticoteam/tico) go out on their own, at any hour, run by `.github/workflows/train.yml` and
`scripts/release_train.py`. There are no release windows. No one tags by hand, except a new line's first release ([A new line](#a-new-line)).

- **When.** A candidate is cut whenever main has merged work past the newest release and no candidate is in flight
  (no validation running, and the newest tag is published). No new commit, no release. Nothing is stored: every run works
  this out from GitHub, so a missed event costs time, never a wrong release.
- **Each step** first promotes the canary's release to stable once it is approved and nothing holds it (see
  [Approval](#approval)). Then it ships a green candidate, or dispatches Release validation (`ci.yml`) for main's head
  as the next patch.
- **A green candidate ships.** The train tags that commit, publishes it (`release.yml`, dispatched on the tag) and
  updates the canary. The release is published as not latest; only once the canary runs it healthy does the train make
  it the latest GitHub release and move the images' `latest` tags, so new installs, update checks and desktop apps
  never get a release the canary has not run. Latest never moves backwards, and a marking that failed is retried by the
  backstop once the canary runs that release healthy.
- **Notes and approval.** Each candidate the canary takes gets an issue with its release notes (see
  [Approval](#approval)). When a release publishes, the monitoring Release Owner files the Technical Writer's "Release
  notes for vX.Y.Z" task.
- **A red candidate** goes on the one open `release-train` issue, once. Each push to main validates the new head, one
  validation at a time. A red or cancelled head waits for the next push, or a manual run (a flaky check). The next
  release that ships closes the issue.
- **Main is red.** A red Release validation run on main reviews, once per break, each merged pull request that changed
  a file a failing test uses (a review, so Tico routes it to the pull request's owner). With more than three such
  pull requests and only pytest tests failing, the bisect job on RunsOn runs the failing tests at the commits between
  the last green run and the red one, twice each, and reviews only the merge where they first fail. A mix of results is
  flaky: nothing is reviewed, and the test is a candidate for `ci/quarantine/` (see Quarantined tests). One open `main-red` issue lists
  the suspects, the verdict and the likely flaky tests. The next green run on main closes it. See
  `scripts/main_red.py`.
- **Canary and stable.** The canary is Tico Team's install, stable is TIDY's. Both are updated through their update
  keys ([Service keys](service-keys.md#update-keys)) with `GET`/`POST /api/v2/system/update`, never a browser or SSH.
- **Holding.** Set the repository variable `RELEASE_HOLD` to a reason (or open a `release-blocker` issue) to keep
  stable where it is, for every release. To keep one release off stable, add the `hold` label to its approval issue.
  Set `RELEASE_TRAINS` to anything but `on` to stop the train altogether.
- **By hand.** `gh workflow run train.yml --ref main` runs one step now (promote, then ship or validate, validating a
  red or cancelled head again), even with `RELEASE_TRAINS` off. `python3 scripts/release_train.py status` (with
  `GITHUB_TOKEN`, and the install variables to see them) shows the next step, the approvals and what each install runs.

### A new line

The train cuts patches of one line, `LINE` in `scripts/release_train.py` (now 0.6). While the newest release is on an
older line, it cuts nothing and says so on the `release-train` issue: the line's first release (v0.6.0, and its rcs
before it) is tagged by hand. After that tag it cuts v0.6.1 and on as usual.

0.6 starts fresh (see [Updates](updates.md#from-05-to-06-every-company-starts-over)). The train never updates an install
that runs 0.5 to 0.6 or later: promotion skips it, and a hotfix or ship to it fails. Move such an install by hand, on a
new database.

Triggers: every push to main, every finished Release validation run, a manual run, and a backstop every 20 minutes (at
:05, :25 and :45) for a missed event, a release waiting for stable and a release waiting for latest. A small read-only
job decides first, so a push with nothing to do ends in seconds. Every job that acts (the train job and the hotfix)
shares one concurrency group, so two never start a build or a release at once. One race remains: GitHub keeps one
waiting job per group, so a hotfix queued behind a running train job can still be replaced by a train job whose
read-only check ran just before the hotfix was queued; a hotfix that shows as cancelled that way is simply run again.

Settings, in the GitHub environment `release` (deployments from `main` and `v*` tags only): variables `CANARY_URL`
and `STABLE_URL`, secrets `CANARY_UPDATE_KEY` and `STABLE_UPDATE_KEY`. Repository variables: `RELEASE_TRAINS`
(`on` to go live), `RELEASE_HOLD` and `RELEASE_NEEDS_PERSON` (see [Approval](#approval)). The train tags with the
Actions token, which starts no workflow, so it dispatches `release.yml` on the tag (with `latest=false`).

### Incremental validation

The train validates a head in one of two ways. A **full** validation is the whole gate (`ci.yml`, as before). An
**incremental** one runs the same gate on less, and says so in its run title (`... from vX.Y.Z (incremental on <anchor>)`).

- **One full validation a day.** At most one full validation is dispatched automatically in any 24 hours, whatever its
  outcome (a cancelled one, and a red one with unnamed failures, count too). The train dispatches a full validation
  only when no full validation finished in the last 24 hours (by finish time), even when an incremental one anchors the chain.
- **Anchor.** The anchor is the newest validation, full or incremental, of the last 24 hours that ran the suite to an
  answer: a green one, or a red one that names its failed tests, of a commit main still contains. It is the anchor
  whichever release it validated, so each incremental validation covers only its own diff from the previous green one
  and its carried failures. The train dispatches only the anchor, the carried failed tests and the anchor's release;
  Release validation makes the plan from its own checkout. If the plan cannot be made (a commit missing from the
  checkout), the train waits and says why.
- **A cancelled or unnamed full run holds the day.** A cancelled full validation, and a red one whose failures no log
  names (a build, a VM, a startup failure), is no anchor, and no second full validation follows it. The train waits
  and says so on the release-train issue: `a full validation already ran in the last 24h (<run>, <conclusion>): a person
  runs Release train by hand to validate again`. A person runs Release train by hand (a manual run), which validates
  main's head in full as it does today. Once that run is a day old, the train may dispatch a full validation again on
  its own. Until then a release can wait up to a day, so a person who needs it sooner runs it by hand.
- **Incremental candidate.** A later head runs only: what `scripts/pr_checks.py` selection picks for anchor..head (the
  merge queue's rules before its light plan's two-level cut, never the full suite; so it runs the tests a queue group
  left out), the browser smoke set, the
  product part (the upgrade journey and the Docker smoke checks) when the candidate's previous release is not the
  anchor's, and the tests that failed in the open red runs (below). A failed file that marks slow tests also runs in the
  contracts lane (`-m slow`). The plan is `scripts/release_increment.py`.
- **Images.** Every candidate, full or incremental, builds its images: the Dockerfile copies the whole tree
  (`COPY . /opt/tico`), so every candidate changes their inputs, and `release.yml` ships the candidate's own images. This
  is the rule that images build only if their inputs changed, not an exception to it.
- **Red runs.** A red run that names its failed tests is the anchor when it is the newest such within the day (see
  Anchor). A red run stays open, across releases, until a green validation (full or incremental, of a commit main still
  contains) runs after it. The tests carried into an incremental plan are the failed ones of the open reds: the anchor's,
  when it is red and still open, together with the newest red run since it. While a red run is open, the train runs a
  later head only when a commit since that run selects one of its failed tests. Otherwise it waits, and the train's log
  says `the train waits` and why. A red run that names no failure, or whose diff cannot be read, is run again on the
  next commit, incrementally from the anchor (a full validation waits for a person, see above). A red run at main's
  head waits for a push.
- **Limits.** Only full validations run every test: a changed file's slow tests run in an incremental one only through the
  contracts lane (which also runs the content sweeps a change reaches, see the selection rules below), and `alone` tests
  never do. A UI script runs when its own files or helpers change, its name matches the changed UI file, or a `UI_MAP`
  entry selects it:
  `ui/tests/task-code.cts` has no entry for the UI files it covers, so a change to them does not select it. The measured
  UI test map (#2897, `ci/ui-test-map`) closes that gap; until it lands, the next full validation is the check.
- **Not changed.** Hotfixes, approval, promotion, the `release-train` issue and the concurrency group.
  `release.yml` accepts an incremental run's title for the tag, because the title starts the same way.

### Approval

Stable (TIDY) takes a canary release only once it is approved. The repository variable `RELEASE_NEEDS_PERSON`
decides who approves:

- **Off (the default, and unset).** A release is approved automatically when every gate is green and its notes list no
  High risk item. Stable's update closes its issue with "approved automatically (no high-risk items;
  RELEASE_NEEDS_PERSON is off)". A release whose notes list a High risk item, or were cut short, still waits for a
  person.
- **On.** Every release waits for a person, as below.

A person's approval always counts. The `hold` label, `RELEASE_HOLD` and `release-blocker` issues hold either way. High
risk is what the notes call it (`scripts/release_notes.py`): a pull request whose `## Risk` section starts with
`high`, or with a `risk:high` or `high-risk` label.

- **The issue.** Each candidate the canary takes gets an open issue, `Release vX.Y.Z: awaiting approval`, labelled
  `release-approval`. Its body is the release notes, built by `scripts/release_notes.py`: every pull request merged
  since the release stable runs. High-risk pull requests come first, then user-facing ones (`ui/`, `clients/`, `app/`),
  then backend ones (`backend/`, `runner-rs/`, `recorder/`, `templates/`), then the internal ones, one line each. The
  first line counts the blocks by group. A block gives the pull request's Spec, its demo steps (written for the Acme
  Lights demo company) and its risk when it has them, and says so in one line when it has none of the three. It also
  gives its reviews and receipts, and the areas it touches. Its checks show only when one failed or none ran.
- **Approving.** A person with write access approves it by adding the `approved` label or commenting `/approve`. The
  repository's permission for that login is checked, and the account must be a person: a bot or a GitHub App does not
  count, even with write access, and neither does anyone without write access. The `hold` label keeps one release off
  stable.
- **Promotion.** At each train step (the backstop runs one every 20 minutes), stable takes the canary's release once it
  is approved, not held, nothing holds stable (`RELEASE_HOLD`, an open `release-blocker` issue) and the canary runs it
  healthy. Stable's update closes the issue, and the issue says who approved it, or that it was approved automatically.
- **Superseded.** A newer release on the canary closes an older issue with a link to the newer one. A candidate whose
  canary update failed is closed too, as not on the canary: there is nothing to approve.
- **Reading the notes.** `python3 scripts/release_train.py notes v0.5.200 v0.5.201` prints the notes for any range,
  read-only. It needs `GITHUB_TOKEN`.
- **Pictures.** A candidate the canary runs also gets a "What it looks like" section above its notes: before and after
  pictures of the pages it changes (see [Before and after screenshots](#before-you-tag)). They are made after the train job
  and the canary update, so neither waits for them, and approval does not wait for them either. The issue says the
  pictures are on their way until they arrive, and says so with the run's link when they could not be made. Hotfixes get
  no pictures.

### A release that did not publish

A tag with no published release keeps the train from moving on (the next version would skip it). When the Release
workflow on the tag failed, the train runs it once more, 30 minutes after the first run started, and then updates the
canary and marks it latest as usual; the release-train issue records it. If the second run fails too, the issue says
so and a person finishes it by hand: read the failed run, fix the cause (a registry or GitHub outage usually clears),
then

```sh
gh workflow run release.yml --ref vX.Y.Z -f desktop=false -f latest=false   # publish, not latest
gh workflow run train.yml --ref main                                        # canary, then latest
```

The train step sees the release published, updates the canary and marks the release latest. To mark it latest
without the train: `gh release edit vX.Y.Z --latest`, and for each of `tico`, `tico-runner` and `tico-updater`,
`docker buildx imagetools create -t ghcr.io/ticoteam/<image>:latest ghcr.io/ticoteam/<image>:vX.Y.Z`. A release whose
canary update failed stays published but not latest; the next release ships the next patch over it.

### Hotfixes

A hotfix is for a major production issue only: data loss, a security hole, an outage, an install that cannot update.
Everything else goes out with the train. Land the fix on main through the merge queue (below), then:

```sh
gh workflow run hotfix.yml --ref main -f reason="Tasks vanish from the board after an update" [-f sha=<commit>] [-f promote_stable=false] [-f override_hold=true]
```

Anyone with write access may run it, bots included. It validates the commit as the next patch, publishes it, updates
the canary, marks it latest once the canary is healthy and, unless `promote_stable` is off, updates stable right
after, without waiting for an approval. Holds still apply: with `RELEASE_HOLD` set or a `release-blocker` issue open, stable is left
where it is unless `override_hold` is on, and the override is written, with who ran it, in the run summary and on the
issue. The reason is required, and the reason and its release notes go on a `release-train` issue: closed when the hotfix
succeeds, left open when it fails. With `promote_stable` off, the
hotfix's approval issue decides stable, as for any canary release.

## The fast path

1. **While building a PR**, run the focused tests for its changes; CI selects the tests a change can reach (below).
2. **Freeze main.** The Release Owner runs the release command's freeze guard on current main.
3. **Build and validate that exact commit.** The release command dispatches the full hosted Release validation for the
   frozen commit as the new version: it builds the runner binaries and the three images once, then tests those. The
   command waits for that run and checks its title and candidate commit. The 8-minute target is report-only.
4. **Tag that commit.** The command checks main again and pushes the version tag at the validated commit.
   The GitHub release is published about a minute later: the version tags are pointed at the tested images.
5. **Notes task.** Once the release is published, the command files one Technical Writer task. The notes are already
   on the release's approval issue.
6. **Server rollout** starts at once: the canary install first, then the rest ("Update now", about 2 minutes each).
7. **Desktop follows**: built only when the shell changed, and attached to the published release when it is done.

These steps are the release command's, for a team whose Release Owner releases. Tico's own repository releases
through [the release train](#the-release-train) instead. Release validation never runs on a schedule or on a push to main
itself: each run is a full gate for one candidate, dispatched for an exact commit and version.

## Supported versions

What a server accepts is what the code enforces. This is the matrix the release notes refer to.

| Part | Accepted | Enforced by |
|---|---|---|
| Database | schema version 71 (`MIN_BRIDGE_USER_VERSION`) and newer up to this release's; cloud steps up to 75 | `backend/migrations` `preflight`, before the first write |
| Runner | release `MIN_RUNNER_RELEASE` (0.4.0) and newer | `backend/runner_versions.py`: claims from an older runner are paused, with the reason |
| Runner wire | the current and previous minor release, both directions | `scripts/check_wire_contract.py`, [runner-wire-contract.md](runner-wire-contract.md) |
| Desktop app | the release's own version, or the previous release's files when the shell did not change | the release workflow; see [Desktop apps follow the release](#desktop-apps-follow-the-release) |

A database below schema version 71 is refused: "This database is at schema version N, below 71, the oldest this server
upgrades. Start it once with Tico v0.5.x (the release that writes version 71), then upgrade to this release." Open it once
with a v0.5.x release, then upgrade. The legacy import (`manage.migrate_legacy`) is the only path that
opens an older file directly. A newer database, one whose recorded migration this code does not know, and one whose
migration file changed after it ran are all refused before anything is written.

## Migrations

Every persistent schema change is a file in `backend/migrations`: `YYYYMMDDHHMMSS_name.sql` (its statements) or `.py`
(an `up(conn)` that does not commit), using a UTC timestamp. Create it with `python3 scripts/new_migration.py name`
(add `--py` for Python). Existing `NNNN_` files keep their names and contents. Versions sort as full strings; legacy
files precede timestamp files. Every unrecorded file runs, even if it sorts before one already applied. Each migration
stands alone and must not depend on another unmerged migration's schema, so parallel PRs can merge in either order.
CI rejects PR-added legacy names or duplicate timestamps against the current base tip and PR, with exact `git mv`
suggestions. A file runs once, in its own transaction with its `schema_migrations` row, so a failure leaves neither.
Never edit a file that has run: its checksum is
recorded, and a start refuses the database if it changed. Make the change in a new file. Temporary views and tables
(`CREATE TEMP`, `backend/sql.py`) are per connection and stay in code. hubdb's `MIGRATIONS` list (user_version 71; a file from a
release that ran growth segments as step 72 opens too, `NEWEST_USER_VERSION`) and the cloud steps in `Store.initialize`
(75) no longer grow.

The files run right after hubdb's own steps, before the module schema code (`SCHEMA` scripts, `ensure_schema`,
`add_column`), so a rename or move lands before code that uses the new names. On a new database a module's table does
not exist yet at that point: a migration that touches one skips it when absent (`DROP ... IF EXISTS`, or a check of
`sqlite_master`/`PRAGMA table_info`), and the module's `CREATE` makes the new shape. A `.py` file that rebuilds a table
sets `FOREIGN_KEYS_OFF = True`: the runner turns foreign keys off before the transaction, checks `PRAGMA
foreign_key_check` for references the migration broke before it commits, and restores the setting.

`backend/tests/test_ddl_guard.py` counts the `CREATE`, `ALTER TABLE` and `add_column` statements still outside
migrations, per file, in `backend/migrations/ddl_allowlist.txt`. A new one fails the test; when a statement moves into a
migration, lower its file's count in the same change.

## Release 1 cutover

Release 1 is the contract release: the column renames and retired spellings (`cancelled` became `canceled`,
`needs_onboarding` became `needs_setup`) land in one tag, and the server, runner, Mac app, templates and bot repos
come from that tag together. Each install upgrades on its own, one at a time: Acme Team first, as the canary, then
Acme Ops. An install starts the next one only when the first is healthy and its computers have followed.

Stored work carries over. Queued jobs, webhook sends and automation definitions are rows the new release reads. Rule conditions and
task-received automations that name `owner` or `requester` keep matching, because those names are read through the
rename map. A webhook send queued before the cutover goes out with its body named as the new API names it.
`backend/tests/test_inflight_cutover.py` pins the webhook, rule and automation cases on rows as they were saved.

Per install, in this order:

1. **Stop the writers.** Stop the server; nothing else writes to the database. Runners and the Mac app reach an install
   only through its API (each keeps its own state file, not the hub database), so the server stop is the stop.
   Work in progress is picked up afterwards: a job whose lease runs out is recovered, and a webhook send caught
   mid-delivery returns to ready after two minutes. Docker: `docker compose stop server`. Mac: `scripts/tico-computer -e acme server stop`.
2. **Take the consistent backup while stopped.** The checkpoint copies every page of the database into a new private
   file and checks that the copy is complete, without an integrity scan, so it is quick enough to run with the server down:

   ```sh
   docker compose run --rm --no-deps --entrypoint sh server -c \
     'mkdir -p /data/snapshots && python -m backend.manage checkpoint /data/hub.sqlite /data/snapshots/pre-r1-acme-team.sqlite'
   ```

   Copy the file out of the `/data` volume to a place the next update cannot reach. The updater keeps only its own
   last three snapshots, so this file is the one to roll back to. `python -m backend.manage inspect` on the copy runs
   the full integrity check; run it once the server is back, since it reads every page.
3. **Tag the release commit.** `scripts/release publish` tags the validated commit as `vX.Y.Z`. That one tag names the
   server image, the runner binaries and the templates in the same tree; the desktop app is part of it when its shell
   changed (see [Desktop apps follow the release](#desktop-apps-follow-the-release)). Bot repositories are not tagged by
   the release: record each one's commit next to the freeze record (`git rev-parse --git-path
   tico-release.json`), so a rollback knows what each bot was on.
4. **Start the new release.** Docker: set `TICO_TAG=vX.Y.Z` in `.env`, then `docker compose pull && docker compose up -d`.
   Mac: check out `vX.Y.Z` in the install's checkout (`git -C <checkout> checkout vX.Y.Z`) and restart it with
   `scripts/tico-computer -e acme server restart`. On start, preflight accepts schema 71 and newer, then the rename files run in
   order, each in its own transaction with its `schema_migrations` row. A database that has both an old and a new name
   of one column is refused, not guessed at (`RenameConflict`). A failed migration leaves the file at the schema it had,
   and the server does not start on it.
5. **Let the computers follow.** Each runner sees the new server release, stops taking work and hands its turns back
   for up to `drain_timeout_seconds` (five minutes by default in `runner.json`), then updates. Its queued work resumes
   afterwards. A computer that has not yet updated keeps working while it waits, as long as its release is at least the
   minimum in [Supported versions](#supported-versions), and the runner wire covers the current and previous minor release.

Acme Team's record for the cutover is the checkpoint file, the tag `vX.Y.Z`, the old tag it left and the bot repository
commits. Acme Ops repeats steps 1 to 5 with its own checkpoint (`pre-r1-acme-ops.sqlite`); its database is its own.

**Rollback boundary.** The updater rolls back by itself only when the new release fails its health check. Before the
install has done real work on the new release, roll back by putting the checkpoint back and starting the old tag:
follow [Rolling back](updates.md#rolling-back) for the server, with the checkpoint in place of the pre-update
snapshot, and pin the runners and the Mac app to the old tag too. After the install has taken real writes on the
new schema, a rollback to the checkpoint drops every write since the backup, and the old release cannot open the
newer database at all. Fix forward with a new patch release instead, unless the owner decides to lose those writes.

## The release command

The Release Owner uses the Rust `tico-release` command through `scripts/release` for each approved release.
Build it once from the reviewed controls with `scripts/heavy.sh cargo build --locked --manifest-path tools/release-command/Cargo.toml`.
Start from a clean checkout at current main:

```sh
scripts/release freeze --version vX.Y.Z --previous vA.B.C --receipts receipts.json
scripts/release publish
```

`freeze` runs `freeze_guard.py`, checks main again, and records the frozen SHA, version and previous release in this
checkout's Git metadata. `publish` dispatches Release validation with the frozen SHA, waits, verifies the workflow,
event, main branch, exact run title, candidate SHA and successful conclusion, checks main still equals the frozen
commit and tags it. Main moving at any checkpoint refuses the release. It then waits for the GitHub release to be
published and files one task, "Release notes for vX.Y.Z", for the Technical Writer (`--writer <bot>`, default
`technical-writer`) with the release's commit range. The command needs `gh` and a `tico` login that can create tasks.
The command does not replace the release's publishing approval, evidence checklist or rollout.

Run `publish` in the background while validation runs. A validation run that ends canceled is rerun by `publish` (same
run, up to twice); a failed one still refuses. Its freeze record retains the candidate, run ID, tag and notes task, so
the same invocation resumes after interruption without dispatching another gate, retagging or filing a second task. If
interrupted during dispatch before the run ID was saved, inspect Actions and pass `--run <id>` to recover it; mismatched
runs refuse. If interrupted while filing the notes task, `publish` refuses rather than file another: check the Technical
Writer's tasks, and file it by hand if it is missing. Retain the record as the release receipt. To start a new freeze
after a fix, archive/remove that record explicitly (`git rev-parse --git-path tico-release.json` shows it), then freeze
current main again. `--state <file>` selects an explicit record for either phase. No command resets a freeze or falls
back to a different validation automatically.

### After freeze

Any change to main after the freeze, however small, requires a fix pull request, a new freeze and a new **full**
gate on the new commit. This includes test fixes, main re-merges and docs edits: a green run for the old commit cannot
be reused. An admin
merge is allowed only on that fix-PR/new-full-gate path; it does not waive review or exact-commit validation. Do not move the tag to a
new commit to borrow an old run. Roll back this tooling change by reverting it; the manual steps below still work.

## Tests on a PR

Open the pull request as a draft and run tests on your computer while you work: a draft runs no checks, so pushes are
free. When the change is done, `python3 scripts/pr_checks.py local --ready` runs the tests the plan selects for the
branch (the same selection a pull request's run makes, through `scripts/heavy.sh`), prints a receipt line naming the full
head sha and the result for the task's review, and marks the draft ready after a pass; without `--ready` it only
runs and prints. It names what only CI runs (Rust conformance). A change that reaches most of
the suite (a catalog template, a shared module) selects most of it: with `TICO_HEAVY_REMOTE=1` each part runs on a RunsOn
VM against the pushed head instead. Mark a pull request ready once:
each later push runs its checks again. A push to a ready pull request runs the cheap checks on free GitHub-hosted runners:
lint, the generated UI types, the runner wire contract, the migration names, shell scripts and workflows, and whether
the recorder changed. A ready pull request from this repository also runs the tests its plan selects, on GitHub-hosted runners; the required check `checks` aggregates them all. A fork's pull request runs the cheap checks
only. The merge queue runs the tests again: once per queue group,
it selects them from the group's merged change (base to group head) with the measured static dependency map: what each
tracked file imports (transitively), runs by path (scripts, `-m` modules, shell and JavaScript) and reads by path (data
files, folders it lists or globs). The Caddy image job runs only when a change reaches the image inputs or
`backend/caddy_assets.py`; otherwise the plan compiles the changed files and runs the Dockerfile's import check itself.
A `docs/` page or a GitHub file runs the tests that read it (by path, folder or listing) and the tests of
the code that reads it, not everything that imports that code. A change to how the checks run (the PR checks workflow,
`scripts/ci_install.sh`, the browser system packages) runs the tests that read it and one short core browser script, and
a workflow change also builds the Caddy image. Conftest, `pytest.ini`, dependency pins and deleted or unparsable files
run the tests that name or read them and the smoke set (`scripts/queue-smoke.json`). A conftest runs the tests below it
while there are at most 25; a test that reaches a changed file only through a conftest fixture adds the smoke set. Runner/harness changes also run slow conformance on
the Rust runner and all its fake harnesses; UI changes select their browser scripts, including imported-history for chat
changes, or the core browser scripts plus the smoke set when the UI mapping is uncertain. Browser scripts the release
check runs are picked by path too (rule 6, `browser_direct`), on a ready pull request and in the queue: a changed
`ui/tests/*.cts` script, every script that uses a changed `ui/tests/support/` helper (through other helpers too), and,
for UI source with no `UI_MAP` entry, the scripts named after the file (`ui/app/tools.ts` runs `tools`, `bot-tools`,
`redesign-e2e-tools` and the rest). Any change under `ui/app/` or `ui/styles/`, or to `ui/tests/support/phone-rules.cts`, also picks
the five `phone-dead-ends-*` scripts. More than 40 picked runs every script the release check runs, in shards. A change to
what a slow content sweep reads runs that sweep in the contracts job (rule 7, `CONTENT_SWEEPS`): `templates/`, `skills/`,
`clients/commands/`, `clients/cli_spec.py`, the CLI code or `e2e/cli/` run `test_e2e_cli.py` with every command
(`TICO_E2E_CLI_ALL=1`, as the release gate runs it), and `templates/` or `clients/catalog.py` run `test_e2e_templates.py`.
The Rust and desktop checks run on a ready pull request and in the queue when a
crate or `app/` changed, and the macOS recorder tests when `recorder/` changed. A draft pull request runs no checks
(every job is skipped); marking it ready for review runs the lint checks and the selected tests. A skipped run is not a pass, so the
ready-for-review run's `checks` must show passing, not skipped. Main has no check run of its own: the queue tests each
change on the merged result, and Release validation runs the whole gate on each candidate. A daily cache run
(`pr-cache.yml`) keeps the main-branch GitHub caches that PR and queue runs restore from fresh. The repository's caches share 10 GB, and an entry unused for seven days is evicted. A pull request's superseded run is canceled; a queue group is never canceled.

**Runner switch.** The repository variable `TICO_CI_HOSTED=true` moves every RunsOn job in `ci.yml` (release validation) and `remote-run.yml` to a GitHub-hosted runner (`ubuntu-24.04`, or `ubuntu-24.04-arm` for arm64). Flip it only when RunsOn cannot launch machines. Unset or any other value keeps RunsOn, which stays the default there. `pr-checks.yml` and `pr-cache.yml` always run on GitHub-hosted runners and never read the variable. Hosted runners have about 14 GB free, so a hosted job first removes the preinstalled .NET, Android and GHC toolchains. Its cache is GitHub's own (10 GB per repository, entries unused for 7 days are evicted), and the timeouts stay as they are, so a slow hosted run can time out where RunsOn would not. `remote-run.yml` takes only `cpus=4` while the variable is set.

Python jobs install no npm packages, browsers or apt packages. The browser job caches both browser binaries and
Ubuntu package indexes/debs under exact runner-image and dependency-lock keys. A warm cache uses apt with
`--no-download`; a cold cache uses the canonical Ubuntu mirror. Each Install attempt has a five-minute limit with
one retry. Pip and npm download caches remain enabled. The queue's tests run on 4-vCPU GitHub-hosted runners, as PR pushes do. `scripts/pr_checks.py` sizes the Python and browser groups so each VM
finishes in about `TICO_CI_JOB_SECONDS` (330 s, under seven minutes) including setup, using JUnit per-file and browser per-script timings
from the latest successful Release validation run (its `test-timings` artifact: the same pytest flags, slow tests
included), with collected test counts until timings exist. New files use the observed
seconds per test; untimed browser scripts count 20 seconds. The **PR job timings** check on a queue run links each job and its elapsed seconds in its summary; it gates nothing. The `checks` gate fails when a job it needs failed or was canceled. The target is under ten minutes from the group's start to its completed checks; the queue run's receipt, including cache state, establishes whether that target is met.

Nothing is mapped by hand or by filename: a test that uses a file in a way the map cannot see (a path built at run
time from a parameter, a subprocess's own reads) must name that file or folder literally, or the change will not
select it. A file that makes a computed import or lists the repository with `git ls-files` runs on every change.
The Release validation workflow retains its full candidate gate and never cancels a running validation.

Write unit tests while you build and run them as you go; keep every one that guards a real failure, as many as the change needs, and drop the trivial ones. Before you mark the PR ready, run `python3 scripts/pr_checks.py local`, not the full suite. For each release, run the full suite once on its frozen candidate.

Use `pytest -ra` to see prerequisite skip reasons. Systemd lifecycle scenarios run on Linux;
unit rendering still runs on every host. Installer scenarios need Docker and stage their mounts
in the system temporary directory (`/private/tmp` on macOS), so an external checkout need not
be shared with Docker Desktop. Runner isolation requires `TICO_RUNNER_TEST_IMAGE` to name a
freshly built image; an unset value skips it, while an explicitly selected missing image fails.
The release check builds and selects its candidate image before running isolation.

```
python scripts/release_checks.py
```

That is the default suite: pytest in parallel without the tests marked `@pytest.mark.slow` (about a minute), then the
core browser scripts (`CORE` in `scripts/ui-tests.cts`) with the load-aware concurrency described below. The check records wall time and load; it
takes about two minutes, and a run over 300 seconds still passes and says it is slow. There is no budget on the
number of tests: keep every test that guards a real failure, and none of wording or copy. Release validation builds the three images and the Release workflow publishes them with the
GitHub release. The merge queue never runs the full default suite: an uncertain selection runs the smoke set, and known mappings run the affected tests above. Release validation runs the full gate for an explicitly dispatched immutable candidate. `--release` runs this suite too, along with the
slow tests and every browser script, and release candidate validation covers that full suite.

## Merge queue

Main takes changes only through its merge queue. Mark the pull request ready for review, wait for its `checks` to pass,
then enqueue it with `gh pr merge <N> --merge`. The queue adds the pull request to a group, merges the group onto the current main, runs the selected
checks on that merge commit, and moves main only when they pass.

- **Each push to a pull request** (GitHub-hosted, free): plan, static checks and the `checks` gate. A draft runs nothing.
- **A ready pull request from this repository** (GitHub-hosted): the selected Python and browser tests, and every other
  job a queue group runs under the same conditions (the Caddy image, Rust lint, the desktop app and
  the macOS recorder tests when the change reaches them), on each push, so a failure shows before the change is enqueued.
  The plan decides which of those the change reaches, so a job it does not reach starts no VM. Only the cosmetic timing
  receipt is queue-only. A failed leg cancels the run (`gh run cancel`), so `checks` fails within minutes. A fork's pull
  request runs none of them.
- **A queue group** (GitHub-hosted, 2 and 4 vCPU): the same jobs again on the merge commit. Its plan job also runs every
  static check on that commit (`scripts/ci/merge_commit_checks.sh`), in place of a static checks VM. A job the change does
  not reach starts no VM. The group's base is the head of the group ahead of it, so it selects from its own pull
  request's change. Its tests pack toward 19 test VMs at most (`NARROW`, and `CEILING` in
  `scripts/pr_checks.py`), each planned at the 330-second job target including setup; the organisation allows 500 concurrent hosted jobs, so a larger selection takes more VMs, up to `TICO_CI_MAX_JOBS`.
- **Quarantined tests**: a test that failed and passed on the same commit (a rerun attempt, or two runs of one commit)
  is flaky. Its entry in `ci/quarantine/` (one JSON file per test: test id, first seen, run urls, ticket) makes PR checks
  and queue groups run it as a non-strict xfail (`scripts/pr_checks.py run --quarantine`), so it cannot fail them.
  Release validation never passes the flag and still fails on it. `.github/workflows/flaky-tests.yml` reads PR-check runs
  daily and proposes entries (`python3 scripts/flake_quarantine.py detect --write ci/quarantine` writes them), and its
  weekly report fails when an entry is older than 14 days. The fix deletes the entry file.
- **Required check**: `checks` on main. It fails when a job it needs failed or was cancelled; a skipped job passes.

Agents open pull requests as drafts and run `python3 scripts/pr_checks.py local --ready` once the change is done; the
queue runs the selected set once per group.

**Enqueue once.** A pull request needs no rebase to be current: the queue merges it onto the current main. Rebase only when
GitHub reports a conflict. `--admin` and the REST merge endpoint skip the queue; do not use them.

**A red queue run** removes the failing pull request from the queue, and the queue retests the entries behind it. Fix the failure on the branch, push, mark the PR ready again if it went back to draft, and enqueue it again. A failing test shows on
the pull request's own run first when the change reaches it directly: a ready pull request runs a light set (the tests
that import, name or read a changed file, the smoke set, at most ten VMs), and its queue group goes one level further (at
most nineteen VMs). Each plan's job summary lists what it left out; release validation's incremental runs select the full
closure over every change since the last full validation, so a test a queue group left out still runs before a release. After a queue failure, fix it and get green PR
checks before enqueuing again.

**Hotfixes do not bypass the queue.** Land the fix through the queue like any change, then dispatch `hotfix.yml` once it
has landed (see Hotfixes above). The queue itself is a repository ruleset (`merge_queue`, and `checks` as the required
check), set by a maintainer.

## Hosted release validation

A validation run is full or [incremental](#incremental-validation); its jobs and their checks are the same gate either way.

Prefer the manual **Release validation** workflow for the full gate so builds and tests do not compete with bots or
interactive work. Its gate jobs run in parallel on ephemeral RunsOn Linux VMs in Tico's own AWS account (4-vCPU for every
part), with read-only repository permissions and no production,
AWS or provider credentials. It cannot tag, publish or deploy.

```sh
gh workflow run ci.yml --ref main -f candidate_sha=<full-40-character-commit-SHA> -f previous_release=v0.3.48 -f version=v0.3.49
```

The run is titled `Validate <sha> as <version> from <previous>`, which is how the Release workflow finds it. It builds
once: the Linux (static, musl) runner and relay binaries, then the server, runner and updater images for
amd64 and arm64 from those Linux binaries (`--build-arg NATIVE=prebuilt`), pushed to ghcr.io by digest with no tag.
The gate VMs test exactly those: the native checks use the built Linux binaries, and the product part pulls the images
by digest (`TICO_RELEASE_IMAGES`, `TICO_RELEASE_VERSION`) instead of building its own. A digest nobody tags is never
installed. To check publication without publishing, run the Release workflow from a branch with that run's ID:
`gh workflow run release.yml --ref <branch> -f validation_run=<run-id>`. It downloads and checks everything, prints
the manifests and files it would publish, and pushes nothing.

The reviewed workflow controls and selected candidate have separate checkouts: a workflow merged after a candidate was
frozen can check that candidate without changing it. The candidate's `scripts/release_checks.py --release` is unchanged.
It can also run one slice of the gate (`--part product|browser|python|desktop`, with `--shard K/N` splitting the Python test
files, or the browser scripts, into N groups balanced by the times in `scripts/test-timings.json`; refresh the browser
times with `--record-browser-timings` from a validation's browser logs). The hosted workflow selects product, four browser
shards, 36 Python shards, desktop. Every part starts with the candidate and runs its setup while the Linux
binaries build. Product, desktop and Python shards 1–2 wait for those binaries; the other shards start their tests
immediately. Only product needs the images and waits for the image builds;
without `--part`, the command runs the whole gate, with desktop only where the Linux app builds. The desktop
part runs the Tauri app's tests on Linux, among them a company app updating from a feed this candidate publishes
(`scripts/app_release.py`) and serves (`backend/downloads.py`) behind a hub session: it must find, download, verify and
install the update. It does not cover the macOS or Windows installers.
Python tests marked `alone` run without workers after their shard's parallel tests pass, so wall-clock budgets do not
compete with other pytest workers. Both passes share one check and contribute to its JUnit report and shard timings.
Python shard planning reads candidate files using pytest.ini's discovery settings (pytest defaults when absent),
conftest collection ignores and selection ignores, without importing pytest or collecting the suite on each VM.
Recorded seconds determine weights; an unrecorded file uses its source test-function count (at least one) times
recorded seconds per estimated test. Sorted paths break ties, so every VM computes the same plan and every candidate
file belongs to exactly one shard. Native test files occupy shards 1–2. Markers still select tests at execution:
release includes slow tests and excludes Docker isolation, while default/PR shards use `not slow`.
The coverage guard (`scripts/release_checks.py --check-shard-coverage --release`) collects the release selection once
on `python-1`, immediately after Python setup while the native Linux binaries are building. This uses its roughly
two-minute binary wait rather than adding collection to the critical Python shards. PR planning reuses its existing
default collection in `scripts/pr_checks.py`, before affected-file filtering, so the guard adds no collection or job.
Both guards fail if a collected file is missing from the filesystem plan; extra empty or deselected candidates are
allowed. The existing no-test-passed check still rejects a release shard that proves nothing.
Native runner/relay prerequisites are the run's own builds, and their fingerprints are checked before tests. Python and
Node Playwright browsers (Chromium and WebKit) are installed explicitly. The run records both SHAs, source-tree identity,
architecture, capacity, worker settings, phase exits and bounded log tails as a three-day artifact; the candidate must
stay clean through completion. It checkpoints the command and actual process start so an interrupted run stays honest.
Only one manual release validation runs at a time. A missing prerequisite, insufficient space or failed phase blocks it.

On each disposable hosted VM only, the controls may remove fixed unused Android, .NET and Haskell SDK directories to
retain at least30GiBfree after prerequisites and before the gate. They refuse local/self-hosted execution. No paid runner,
new machine, production mount or broad Docker prune is used. A capacity refusal requires fixing that environment rather
than skipping tests. Keep affected Linux/root two-user contracts explicit; ordinary Cargo tests ignore those fixtures.
Targeted macOS compile/native checks can use a standard hosted Mac; desktop permissions, keychain, launchd and physical
computer acceptance still need the corresponding Mac environment. The full Docker gate belongs on Linux.

## Before you tag

Build the release task's evidence checklist from the candidate's merged PRs and their linked tasks:

```sh
python scripts/release_checklist.py --head <candidate> --previous vX.Y.Z > checklist.md
python scripts/release_checklist.py --head <candidate> --previous vX.Y.Z --json > checklist.json
```

Run in a checkout with fetched release tags, a `gh` login for the repository and a scoped `tico` login for
the shared team API. No local team database is opened. Without `--previous`, it selects the earlier release
tag using the freeze guard's rule. `--github owner/repository` overrides the checkout's origin.
Attach the Markdown checklist to the release task; the JSON companion retains structured evidence and source
message IDs. The checklist includes every PR found in the first-parent candidate range, each visible linked
task's stage, Review 1/2 verdicts and their full heads, test receipts, `qa` tag, QA verdicts and
Technical Writer tasks and links. Security is outside the release pipeline; no Security result is required.

Evidence comes from the task owner's messages and the owners of sub-tasks titled `Review 1:`,
`Review 2:`, `QA:`, `Docs:`, `Documentation:` or `Technical Writer:`. Review 1 always requires a verdict;
Review 2 requires one when its sub-task exists, and `qa` tasks require a QA pass. Docs are not
checked here. The latest verdict must approve the merged PR head; older heads are flagged for the Release Owner
to resolve with the recorded review delta. A passing test receipt names the full PR head, tests and a passing
result on the same line (for example, `Tests: head <full-sha>: 12 passed, 0 failed`). The latest receipt for that
head must pass; a newer pending result remains a gap. Any recorded failure at that head stays a gap for the Release
Manager to resolve, including after a passing rerun. Other test messages remain visible as evidence. Unrecorded reviews, legacy evidence elsewhere and unreadable tasks stay gaps rather
than being inferred as passes.

Exit 0 means no evidence gaps were found, 1 means the report lists gaps, and 2 means collection failed
(including incomplete pagination). This is a visible-evidence snapshot for the Release Owner to read;
it does not run tests, authorize a release, move tasks, dispatch workflows or replace the freeze guard.

At freeze, before asking for Release validation, check what the candidate carries since the previous tag:

```
python scripts/freeze_guard.py --head <candidate> --receipts receipts.json
```

It names every change that undoes a pull request an earlier release shipped (a stale tree committed over newer work,
or a reverted fix put back): all of it, however small, or most of its meaningful product lines; tests
don't count. It compares against all history unless `--releases N` limits it (about 15 seconds for a release of 60
pull requests). It also names every merged pull request whose final head has no approving GitHub review or task
review receipt at that commit (an approval of an earlier head counts only when the change is the same line for line),
and every commit that landed without a pull request. With no `--previous`, it takes the newest release tag that doesn't
already contain the candidate, and refuses to check an empty range. `receipts.json` lists the heads
the tasks' reviewers approved (`[{"pr": 639, "head": "<sha>", "source": "<task>"}]`); the script's docstring has
the format. It exits 1 with findings: settle each one before the candidate goes on, with a review of the final head
or, for an intended revert, a receipt naming the pull request and the change it undoes
(`{"pr": 640, "reverts": "<sha>", "source": "<task>"}`).

```
python scripts/release_checks.py --release                     # this checkout is the candidate (v9.9.9)
python scripts/release_checks.py --release --previous v0.3.21  # upgrade from a given release instead of the newest tag
```

This is the full suite, run once per release. It builds the server, runner and updater images once (BuildKit cache;
a source-only change rebuilds one layer per image), while the journey installs the previous release. After every build
finishes, it runs every Python test except Docker isolation, the opt-in `@pytest.mark.slow` ones included (real git,
Docker, servers and long timers), and every browser script (`node scripts/ui-tests.cts --all`). Python uses half the
CPU count in workers (at least one; six on a 12-core Mac); `TICO_PYTHON_WORKERS` overrides that default.
The browser runner samples CPU counters over one second at startup. The idle fraction is the change
in idle time divided by the change in total CPU time across all cores. Its job count is
`clamp(floor(cores * idleFraction), 1, 4)`, and every child gets the same `TICO_UI_SLOWDOWN`:
`clamp(1 / max(idleFraction * 2, 0.25), 1, 4)`. It prints jobs, slowdown, cores and idle percentage
before running scripts. On macOS, load average can stay high while several cores are idle.
Explicit `TICO_UI_JOBS` (or `-j N` when invoking the browser runner) and `TICO_UI_SLOWDOWN` win;
the release coordinator passes those overrides through without forcing four jobs. On a 12-core Mac
at 30% idle, the defaults are three jobs and slowdown 1.67; at 50% idle, four jobs and slowdown one.
If CPU counters do not advance or the core count changes during sampling, it conservatively uses
one job and slowdown four. The selected settings stay fixed for that run.
Shared browser support applies `30000 * slowdown` milliseconds to context/page action and navigation
timeouts, including popups. `ui/tests/support/load.cts` exports `SLOWDOWN`, `t(ms)` (rounded scaled
milliseconds), and `applyTimeouts(contextOrPage)` for scripts' explicit waits. Direct script runs
default to slowdown one unless the environment sets it. Explicit per-script timeout options still
override the shared defaults; those waits should use `t(...)` and wait on conditions.
Successful builds also start the following checks against the candidate
at the same time, each with its own Docker names; the journey proceeds once those images are ready:

- `docker/smoke.sh`: the server comes up, a runner joins with a one-time code and installs Codex once OpenAI is enabled,
  the server restarts and the runner reconnects, the data survives down and up, and a runner box updates to the
  server's release and rolls back one that does not turn healthy;
- `docker/side-jobs-smoke.sh`: a runner starts and stops the side job the hub assigns to it;
- `runner_support/tests/test_isolation_docker.py`: Docker isolation against the candidate runner, with
  `TICO_RUNNER_TEST_IMAGE=tico-rc-runner:local` and the same Python worker cap;
- `scripts/journey-test.sh --release`: install the previous release from its checksummed bundle and seed it like a
  real team (older releases' rows, a computer on the previous runner, a team `HUB_` variable granted to BotOps, a bot
  turn that opens a session and adds a task worktree), upgrade to the candidate with "Update now" (the updater
  applies the candidate's bundle and replaces itself), take a turn and add a worktree on the previous runner against
  the candidate server, move the computer to the candidate runner and check the variable, the session, the worktree
  and the rows survived and that a turn still answers once the harness's saved session is gone, roll back an update that migrates the database and never turns healthy (the pre-update
  snapshot is restored, with the server's real entrypoint running Litestream), and update once more to a third build
  so the replaced updater has to pull its image, refresh the bundle and replace itself again. When the previous
  release is below 0.6 and the candidate is 0.6 or later (`scripts/journey_plan.py` says `fresh-start`), the seeded
  install is not upgraded: the candidate's own start on its data volume must exit non-zero with the fresh-start
  message and leave every database file byte-for-byte the same, the previous release must start on it again with its
  tasks, and "Update now" to the candidate must be refused or roll back. A new previous install with no tasks is
  then upgraded in place, and the rest of the journey runs on it.

It prints a phase table with each check's start offset, duration and exit code, plus total time and starting/ending
load. It keeps the logs in a temporary directory and prints the end of any failed log. If any build fails, the
Docker-dependent checks are reported as not run and the release check fails. The release target is under 8 minutes
(480 seconds); a run over budget still passes its assertions but reports that it is over budget. Do not run two at once: the journey's candidate tags are fixed.

The full journey stays available on demand (about ten minutes), for a release that changes enrollment, the runner's
restart behavior, Litestream or backups:

```
scripts/journey-test.sh                  # this checkout is the candidate; starts from the newest release tag
scripts/journey-test.sh --tag vX.Y.Z     # a published candidate (its images and bundle must exist)
```

It runs the release stages above (bot turns through a fake `codex`, `scripts/journey-fake-codex.py`), plus a server
restart before the upgrade; after the rollback it wipes the data volume and checks the server restores from its
replica without the migration, restarts the runner (started as `runner.compose.yaml` starts it) after a run and
requires a second run to work, and finishes with `docker/backup-test.sh` (MinIO and a file replica, wipe, restore). The
updater's images go through a throwaway registry on 127.0.0.1 and its bundles through a throwaway release server, since
an updater replaces itself only with an image it can pull. It prints a table of
PASS, FAIL or SKIP per step and exits non-zero on a failure. `scripts/install.sh` itself needs Linux and root, so the
script does what the installer does after its preflight (checksummed bundle, `.env`, `docker compose up -d`). For a
release that adds updater or migration behavior, the upgrade step is done by the *previous* updater, so also read the
rollback step: it runs on the candidate's updater.

**Before and after screenshots.** `.github/workflows/release-shots.yml` runs `scripts/release_shots.py` on the seeded
demo for the base tag and the candidate, and shoots each page that a changed `ui/` file draws (its `ui` globs in
`scripts/screenshots/index.cts`): desktop 1440 px and phone 390 px, light, plus dark when a dark-theme file changed. A
shared file shoots every page, and a `ui/` file that no table names fails the test suite until one names it. The
changed pictures go to the `release-evidence` branch under the version's folder, and the markdown section "What it
looks like" links them. Both sides run this checkout's harness against their own demo, on a frozen clock
(`TICO_DEMO_NOW` sets the demo's "now"), so a ref compared with itself shoots no changed pair. The release train calls it
for each candidate it ships (the `shots` job in `train.yml`), with stable's release as the base and the candidate as the
head. The job uploads the folder as the artifact `release-shots-<version>`, and the `approval-shots` job puts its section
on the approval issue (see [Approval](#approval)).

### Behaviour replay

> **Beta**: behaviour replay and the computer Performance page work today and may still change. See [Beta features](support.md#beta-features).

A release that changes a bot's prompt, a built-in template, the runner's hosts or how a turn is assembled also replays
the signed real cases (`evals/replay/README.md`) on a scratch team running the candidate, before the tag:

```
python3 evals/replay/run.py --url <scratch team> --token <owner token> --cases <private cases> --candidate vX.Y.Z --report replay.md
```

Attach `replay.md` to the release task. A regression is rerun once with `--only <case>`; one that holds is fixed or
named in the release task as accepted, with why. It needs real model turns, so it is not part of the PR tests or the
hosted gate.

## Manual fallback: validation, tag and notes

1. Freeze current main with the guard above, then run the hosted release validation on that exact commit, with the
   version you will tag, dispatched from main: the tag publishes only a validation run of ci.yml from main.
2. Once it is green, check main still equals that commit, then the Release Owner tags it and pushes:
   `git tag vX.Y.Z <validated-commit> && git push origin vX.Y.Z`.
3. Once the release is published, file the Technical Writer's notes task. The notes are already on the release's
   approval issue.

The workflow then:

- generates `openapi-v2.json` from the tag (`python3 -m backend.openapi_v2`, with the runtime requirements the image installs),
  the same document `GET /api/v2/openapi.json` serves, and attaches it;
- runs `scripts/build_install_bundle.py`, which attaches the one-line installers: `install.sh` and the Windows
  `install-wsl.ps1` with the tag baked into them, `tico-bundle-vX.Y.Z.tar.gz` (`compose.yaml`, `.env.example`,
  `docker/runner.compose.yaml` and the `setup/` wizard) and `SHA256SUMS` over all three. `install.sh` checks the bundle against `SHA256SUMS` before it unpacks anything;
- attaches the validated Rust runner for checkouts, `tico-runner-{linux-x86_64,linux-arm64}.gz` (static, musl), with
  the relay's, and `tico-runner-SHA256SUMS` over them. A Mac or Linux checkout on the tag downloads its one
  ([updates](updates.md#the-runner-engine));
- attaches the Mac runner and relay, `tico-runner-{macos-arm64,macos-x86_64}.gz`. No gate part runs on a Mac, so they
  are not built by validation: when `runner-rs` (its harness manifests included) and `relay-rs` did not change since the previous
  release, its Mac binaries are published with this one (a checkout checks a binary's `--build-id`, the source
  fingerprint). When they did change, the `macos` job builds them after the publication and `macos-assets` adds them
  to the published release, then rewrites both checksum files. Until then a Mac checkout's update says `waiting`
  (runner_support/release_update.py `binary_pending`) and tries again, instead of failing and waiting hours;
- checks each validated image says it is `vX.Y.Z`, then points `ghcr.io/ticoteam/{tico,tico-runner,tico-updater}:vX.Y.Z`
  (and `latest` for a stable version) at those digests before publishing, so no installer runs against missing images;
- marks a tag with a suffix such as `v0.2.0-rc.1` a prerelease, which the update check ignores;
- publishes the release with those four files and the runner binaries, with no notes: the notes are on the release's approval issue. That is everything the installer, the server's update
  check and the updater read (`install.sh`, the bundle and `SHA256SUMS`), so the server rollout can start at once.

The images set `TICO_VERSION` in the image, which is how the running app
knows its version (a source checkout reports `dev`). There is no source archive: the server and the runners run from
the images, and a Mac runner is a git checkout that moves to the release's tag. The Dockerfile copies the source tree
last, after system packages, tools and the Python dependencies, so with the GitHub Actions layer cache a release
rebuilds and pushes one small layer per image.

## Desktop apps follow the release

The desktop app never holds up the server release. Only `validated`, `runner-changes` and `release` run before core
publication; the Mac binaries, desktop, recorder and company build branches start afterwards so their fan-out cannot
compete with publication for job slots. A manual rebuild of an already published tag still runs those branches when
`release` is skipped. The Release workflow compares the tag with the previous release
tag: when nothing the shell is built from changed (`app/`, `scripts/app_release.py`, `scripts/company_apps.py` and the
two desktop workflows), it builds nothing and copies the previous release's desktop files and `latest.json` onto the new
release, with the manifest's URLs moved to the new tag. The app keeps its earlier version, so installed apps are not
offered an update, and the download links keep working. When the shell did change, `.github/workflows/app.yml` builds
it (with a Rust cache, so only the app crate recompiles) and the files are added to the already published release, the
updater manifest last. A failed desktop build leaves the server release published and the Release run red; rerun the
workflow from the tag (Run workflow, `desktop` checked) once it is fixed. The app's version is the release's
(v0.3.6 → app 0.3.6, including any prerelease suffix), stamped from the tag at build time.

The comparison excludes desktop tests (`app/tests/`, the vendored GLib's `tests/` and `benches/`),
`app/src/testdata/` and Markdown docs anywhere under `app/`. Recorder follows the same carry-forward rule with
`recorder/`, `scripts/app_release.py` and `.github/workflows/recorder.yml` as its inputs and
`recorder-latest.json` as its manifest. Recorder tests (`recorder/tests/` and the `cfg(test)`-only
`src/network_tests.rs`, `src/stream_tests.rs` and `src/app/tests.rs`), `recorder/bench/`, `recorder/ui/preview/`
and Markdown docs anywhere under `recorder/` do not trigger an update. `recorder/CHANGES.md` still counts because
it supplies updater notes. Changes to shipped Rust code, `ui/app`, configuration, icons, dependencies, packaging
or those build workflows still build new apps. A missing previous updater manifest or the manual `desktop`
override also forces a build.

Because the files arrive a few minutes after the release, a hub that looks for them in that window finds none and asks
again a minute later. A hub accepts an app version older than its own release when the files are that release's assets.

All public CI builds are the generic **Tico** app. They do not consume company or legacy
per-environment repository variables. The three build jobs upload their bundles as `app-<target>` artifacts. After those
jobs pass, the Release workflow downloads them and adds these assets to the published GitHub release:

- macOS universal `.dmg`, `.app.tar.gz` and `.app.tar.gz.sig`;
- Windows NSIS `-setup.exe` and `-setup.exe.sig`;
- Linux `.AppImage`, `.AppImage.sig` and `.deb`;
- `latest.json`, the Tauri updater manifest, with both macOS architectures pointing at the
  universal archive and every platform URL pointing at an asset of that GitHub release.

`TAURI_SIGNING_PRIVATE_KEY` (and its optional password) signs the updater artifacts. Keep the
corresponding public key in `app/tauri.conf.json`. Missing signatures or platform bundles stop
publication. Generic apps check
`https://github.com/ticoteam/tico/releases/latest/download/latest.json`; prereleases are not
selected by GitHub's latest-release endpoint. See [Desktop app](desktop.md) for installation.

Generate the public manifest locally from collected bundles without uploading anything:

```bash
python scripts/app_release.py --github --version X.Y.Z --tag vX.Y.Z --output latest.json bundles/
```

Public CI no longer runs the legacy variable-based S3 publisher: its variables and branded
artifacts could expose private company configuration in a public repository's Actions run.
Use the private company workflow below for CI publishing. `scripts/app.sh --env <slug>` and
manual `scripts/app_release.py` S3 publishing retain their per-environment behavior.
Hubs prefer a valid company bucket manifest even when older than the server;
without a bucket manifest they offer assets from the GitHub release of their running version, with a ten-minute
cache and no credentials sent to GitHub. Concurrent requests share one GitHub fetch;
other callers use the last cached result or wait at most 100 ms on a cold cache. This fallback applies only to human download routes
(`/download/{os}` and `/api/download/{os}`). `/download/latest.json` serves only the bucket
manifest, including older environment builds without an `app_kind` field, and returns 404 when
none exists. Explicitly generic manifests and updater URLs outside the hub's download-file
route are rejected. Company installer URLs are constructed from that same hub route. Download
storage uses the blob store's region, endpoint and bucket prefix; environment artifacts belong
under `<prefix>/releases/app/` when a prefix is configured.

## The Release Owner's rollout

A team that runs several installs can hand the whole rollout to its Release Owner bot. The owner's OK of a named
release on its release task is the only gate: an OK the bot asked of the owner on that task
(`tico task ask <id> --to <owner> --kind ok "Release vX.Y.Z?"`), or the owner's own message on the task naming the
version. From there the bot runs
`playbooks/rollout.md` (`software/rollout.py` in its repository) without waiting on anyone:

1. it creates the tag `vX.Y.Z` at the approved commit through the GitHub API, with its own write grant on the
   repository. A tag made with the GitHub App's installation token starts the Release workflow, which a
   tag made with a workflow's `GITHUB_TOKEN` would not;
2. it waits for the GitHub release, which installs update from, and stops if the Release workflow for the tag
   fails;
3. it updates the canary, the team's own install, through that install's update key, and waits for the updater to
   report `healthy` and `/healthz` to report the new release, then for its computers to follow (reported, never a gate);
4. it updates every other install together the same way;
5. it reports each install's version before and after on the task.

A failed or rolled-back update stops the rollout: the updater has already put that install back, a canary failure
leaves the other installs untouched, and the bot asks the owner what to do next.

Setup, once:

- **Write on the repository.** An owner or admin ticks the repository in Settings → General → Repositories and gives the bot
  write on it: `tico bot repos <slug> --chosen <owner>/<repo>:write` (list the bot's other chosen repositories in the
  same command; `--chosen` replaces them), or the same in the bot's settings ([Repositories](repositories.md)).
- **An update key per install.** The owner of each install, the team's own included, runs
  `tico service-key create --label "<team> Release Owner" --scope update` there ([Service keys](service-keys.md#update-keys)).
  Each key is stored in Tools → Credentials on the Release Owner's install, granted to the bot, with the variable
  named in its config.
- **The list of installs.** `knowledge/rollout.json` in the bot's repository: the repository, and each install's
  `name`, `url`, `key_env` and whether it is the `canary` (see `knowledge/rollout.example.json`).

A bot created before this template change does not get these files on its own: copy `playbooks/rollout.md`,
`software/rollout.py` and the `.claude/settings.json` line that allows it from `templates/catalog/release-notes-writer`.

## What installations do

The server asks Tico HQ (`https://updates.tico.team/v1/latest`, which serves the same release from GitHub and counts the install
anonymously, see [PRIVACY.md](../PRIVACY.md)) at most every six hours, in the background, without credentials. With counting
off, when HQ does not answer, or with `TICO_RELEASES_URL` set, it asks `https://api.github.com/repos/ticoteam/tico/releases/latest`
directly and sends no ID.

| Variable | Meaning |
|---|---|
| `TICO_UPDATE_CHECK` | `off` stops the check and hides the notice |
| `TICO_RELEASES_URL` | Replaces the check with a GitHub-shaped URL, for a mirror or a test; nothing is counted |
| `TICO_HQ_URL` | Replaces `https://updates.tico.team` |
| `TICO_TELEMETRY`, `DO_NOT_TRACK` | `off` / `1` stops the count; the check then goes to GitHub |
| `TICO_VERSION` | The running version, set by the image |
| `TICO_UPDATER_URL`, `TICO_UPDATER_TOKEN` | An updater service the owner's "Update now" calls |
| `TICO_UPDATES` | `tico`: Tico updates this server from outside. The app has no "Update now" and says "Tico updates this server"; Tico support's scheduled update is off. Cell companies no longer use it: they have an updater ([cell mode](cell.md#updates)) |
| `TICO_CELL` | `1`: a Tico Cloud cell company, written by [cell mode](cell.md) |

The updater contract is `POST {TICO_UPDATER_URL}/update` with `{"version": "X.Y.Z"}` and
`GET {TICO_UPDATER_URL}/status`, which answers `{state, from, to, message}` where `state` is one of
`idle`, `pulling`, `snapshotting`, `restarting`, `uncertain`, `healthy`, `rolled_back` or `failed`. Both carry
`Authorization: Bearer <TICO_UPDATER_TOKEN>`. Without an updater, "Update now" shows the command to
run on the server: `docker compose pull && docker compose up -d`.

## Publish the release docs

The tagged repository is the source for that release's manual. The API spec is generated, not committed: release.yml
generates `openapi-v2.json` from the tag and attaches it to the release, and the public docs link to
`https://github.com/ticoteam/tico/releases/latest/download/openapi-v2.json`. Run `python3 scripts/build_api_docs.py` after
changing API descriptions or tags; check the spec and API guide together.

Publish the public site's docs from the same tag, including the docs landing page's three reader paths and its **Use Tico** and
**Glossary** links. The website source and publishing process live outside this repository. Check that its install and demo pages
use the latest-release installer and `latest` demo image by default, with version pinning shown separately, and mention Docker Desktop
on Mac. Keep the local install command visible on the home page and continue through model sign-in and a first bot result.

Validate the published copy against this release: server Decision calls and HQ suggestion disclosures, 25 active member bots by default,
Inbox Manager's single disabled weekday 07:30 Routine and assigned-mailbox access, and the complete KPI colour example in
[Goals and KPIs](goals-and-kpis.md#the-colours). Run `npm run screenshots` when local dependencies and browsers are available and publish
images from the same release as the UI. Preserve inbound links when moving pages; the transcript and old docs-sync pages link to their successors.

## Company apps

A version tag whose shell changed builds, after core publication and beside the generic desktop app, `.github/workflows/company-app.yml` for
each configured company: its macOS universal DMG and signed updater archive, Windows NSIS
installer with signature, and Linux AppImage with signature plus Debian package. The app version
is stamped from the tag. Company builds restore the generic build's Rust cache (compiled dependencies and the Tauri CLI)
and never save to it, so no branded output reaches a cache a public build could read. A tag whose shell did not change
leaves each company's bucket manifest as it is; after adding a company, run the Release workflow from the current tag
with `desktop` checked. Every build uses the same updater signing key as the generic app,
with `team.tico.env.<stable UUID>` as its bundle ID, its own name and PNG icon, its own server
address, and `<runner_url or url>/download/latest.json` as the update endpoint.

The company shell preserves its configured updater endpoint, including a separate public
`runner_url`. If the configured endpoint is the generic default, it uses the selected hub's
`/download/latest.json`. A hub-origin feed may use the signed-in WebView session when one exists;
the cookie is optional for public feeds and is never attached to a different runner origin. Hub
feeds do not follow redirects, and artifact URLs from them must stay on the selected hub origin.
Tauri still verifies each updater signature before installation. An older shell that cannot reach
the protected feed must be replaced from the team's signed-in **Download** page; see
[Desktop app](desktop.md#your-companys-app).

Keep the JSON list in the private repository Actions **secret** `TICO_COMPANY_APPS`. A secret is
used instead of an Actions variable because the runner prints variables in its pre-step environment
banner before masking commands can run. Company names and URLs must never enter repository
files, public GitHub release assets or job summaries. Each list entry has these fields:

```json
{"slug":"acme","id":"12345678-1234-4234-8234-123456789abc","app_name":"Acme Tico",
 "url":"https://tico.example.com","runner_url":"https://runner.example.com",
 "icon_url":"/api/v2/team/icon","deploy_role_arn":"arn:aws:iam::123456789012:role/tico-company-app-publisher",
 "bucket":"acme-app-files","prefix":"team"}
```

`runner_url` and `prefix` are optional. `prefix` must match the server's storage prefix.
`icon_url` is an HTTPS PNG URL or a path on `url`. URLs require HTTPS except loopback HTTP.
The URL must be reachable without authentication from GitHub's build runners and return a PNG
of at most 1 MiB. If it is `/api/v2/team/icon`, the owner must upload a team icon on that server
(Settings > Team, or `POST /api/v2/team/icon`); the endpoint returns 404 until one is set.
Builds retry transient icon download errors up to eight times, waiting 30, 60, 90, then at most 120 seconds between
attempts, so a server restarting for the update can recover. Permanent HTTP errors and TLS certificate verification
failures are not retried, because waiting does not change them. Invalid or oversized PNGs fail the run at once. If the
download still fails after the retries (network, DNS, timeout or HTTP status), or fails on a certificate, the `icon`
check skips that company's build and publish with a `::warning::` and a job summary line, and the run stays green. The
public release and other companies are unaffected. The company's bucket manifest is not replaced, so its installed app
keeps its current version. Retry logs contain no company URL or remote error text.
The UUID must remain stable across renames to preserve installed preferences and login state.

Company apps put nothing in the macOS menu bar; the Dock shows the company logo with the
Needs you count as its badge. A `tray_label` left in an older private entry is ignored.

Prepare each company's IAM publisher role in its own AWS account, using the normal AWS
credential chain. This script defaults to a dry run that reads live OIDC settings with
`gh api repos/ticoteam/tico/actions/oidc/customization/sub`; authenticate `gh` to the repository
first. Inspect its output privately and run it with `--apply` when ready. It creates the GitHub
OIDC provider if absent, trusts only the repository's default version-tag subject
(`repo:ticoteam/tico:ref:refs/tags/v*` for legacy subjects or the configured repository-ID prefix
for immutable subjects) with audience `sts.amazonaws.com`, and grants only GetObject/PutObject
beneath `[prefix/]releases/app/*` and ListBucket for that prefix. Unsupported custom subject
templates and failed settings discovery are rejected.

```bash
scripts/aws/company-app-publisher.sh --slug acme --id 12345678-1234-4234-8234-123456789abc \
  --app-name 'Acme Tico' --url https://tico.example.com --runner-url https://runner.example.com \
  --icon-url /api/v2/team/icon --bucket acme-app-files --prefix team --account-id 123456789012
```

It prints the role ARN, trust and permission policies, and the JSON entry to add to the secret's
list. Its output is private operator configuration; do not commit it or run it in public CI.
The publisher uses short-lived GitHub OIDC credentials, with the AWS action pinned by commit.
It uploads via `scripts/app_release.py --complete --quiet --prefix ...` into the company's
bucket at `[prefix/]releases/app/<version>/` and then atomically replaces
`[prefix/]releases/app/latest.json` only after all signed platform assets have uploaded.

Matrices and artifact names contain only SHA-256 hashes of slugs. Every company value used is
masked before use; compiler/bundler output is suppressed since it can include derived company
filenames. GitHub Actions artifacts in a public repository can be read by other signed-in
users, so company bundles and their filenames are encrypted with streaming AES-GCM before
upload. A separately derived key uses the existing `TAURI_SIGNING_PRIVATE_KEY` secret; no
additional secret is required. That secret must contain the private signing key material,
rather than a predictable filename. Authentication binds each archive to its company hash, version
tag and CI run. The publisher authenticates each archive before extracting files, rejects paths
and duplicates, and bounds extraction to 32 files and 2 GiB across the three platforms.
Each pack uses a fresh random 96-bit nonce; the signing bytes are never used directly as an
AES key. Decryption uses one automatically closed anonymous temporary file at a time, and
removes extracted files on any failure. Downloaded ciphertext is bounded to 2 GiB plus archive
overhead, with at most 4 GiB of temporary/extracted plaintext on ephemeral publisher disk. Branded
plaintext exists only on ephemeral job disks and in the company's bucket. Ciphertext artifacts
expire after one day and are never matched by the public release's `app-*` artifact download.
The publisher does not fetch the logo again, so a logo change or outage after the build cannot
block publication of already built bundles. Apple signing flags describe only the Mac installer;
Windows and Linux updater signatures do not imply installer code signing.
Company jobs use `fail-fast: false`; one build or publish failure fails that company's workflow
and marks the run red, while other companies and the public release continue independently.
The public release depends on no desktop build. Entries are validated within
their own company job; a malformed company does not prevent valid companies from publishing.
Entries without a slug produce a generic warning and cannot be dispatched. Unparseable JSON
fails the matrix with a generic error and does not prevent the public release.

Company build diagnostics name the failed operation without printing private values. Configuration
and icon fetching run before installing build dependencies. An icon download error reports the HTTP
status or DNS/TLS/timeout category and names `TICO_COMPANY_APPS.icon_url`; it does not indicate a
signing or publishing failure. For a 404, verify the icon exists; for 401/403, allow unauthenticated
icon access. Correct the entry's `icon_url` (and `url` for a relative path) in the repository's
Settings > Secrets and variables > Actions > `TICO_COMPANY_APPS` if the address is wrong. A TLS error means the
icon host's certificate fails verification from GitHub's runners (expired, incomplete chain or wrong name); fix the
certificate on that host. The skip warning names the same category. After the fix, run the Release workflow from the
current tag with `desktop` checked to build and publish that company again.
Missing configuration fields and required environment variables are named explicitly. Icon generation,
bundling, Apple certificate import, artifact encryption/decryption and publication have separate
failure labels; private tool output and raw exception text remain suppressed. An old catch-all
failure log cannot establish which HTTP status or network error occurred; collect the new diagnostic
before changing credentials or assuming the icon is missing.

Validate a private list locally without printing it or fetching icons:
`python scripts/company_apps.py matrix --dry-run` with `TICO_COMPANY_APPS` supplied through
your environment. Publishing still requires the owner's release instruction.
