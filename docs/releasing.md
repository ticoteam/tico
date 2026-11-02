# Releasing

A release is a version tag. Everything it ships is built once, by the Release validation run for that commit and
version, and that same build is what the gate tests. Pushing `vX.Y.Z` runs `.github/workflows/release.yml`, which
builds nothing: it takes that run's runner binaries and image digests, builds the installer bundle and publishes the
GitHub release. Running installations look for that release to show
"New version" in the sidebar. A release is not held for its notes: the release command tags the validated commit,
and once the release is published it files one task for the Technical Writer to write that version's section of
[CHANGELOG.md](../CHANGELOG.md). The installed `CHANGELOG.md` supplies the in-app, API and MCP product changelog,
so a release's notes reach users with the next build that contains them. No separate product announcement is required.
Don't edit CHANGELOG.md in a PR. The notes land as a CHANGELOG-only pull request that needs review at its final head,
and each notes commit needs a `Changelog-Release: vX.Y.Z` trailer for each version section it changes; preserve the
trailers in the merged commit. PR checks reject ordinary changelog edits before selecting tests. A pull request that
corrects a shipped section needs the same trailers. Keep the versioned sections on `main`, so later builds retain the
complete history and do not announce already shipped work again. A released section is held to its tag, or, for a
release tagged before its notes were written, to the section as first written on `main`: a later difference fails the tests.

**One release path.** On a team that runs Tico with a Release Manager bot, only the Release Manager publishes
releases. A Claude Code session or any other outside session that finishes work hands it to the Release Manager on a
task ("Release vX.Y.Z: <what>") instead of pushing the tag itself; the Release Manager tags, rolls out and records it,
so the tasks waiting on the release learn it shipped. Tico itself never moves a task on a tag, release or deploy; it
only marks the merged PR links the running release contains as Shipped (backend/github.py).

**When releases are cut.** The Release Manager hears about Dev tickets at Pending Release once, as one task:
"Release: <n> tickets at Pending Release". It arrives once a day at a set hour (10:00 company time by default;
an owner or admin changes the hour, adds a second one or turns it off in Settings, or with `hub release schedule`),
and not at all when nothing is pending. A ticket arriving at Pending Release wakes no one. For a hotfix or a
midday release, a person who moves Dev tickets (or the Release Manager or Head of Engineering bot) runs `hub release now [--note ...]`;
it sends the same task at once, or wakes the Release task the Release Manager already has open. With nothing at
Pending Release, a person's `--note` still asks (work often reaches main as pull requests that were never Dev tickets):
the task, "Release: requested by <name>", carries the note and no ticket list. A bot's request, or a person's with no
note, sends nothing. The Release
Manager is the bot the schedule setting names, else the one built from the `release-notes` starter; with neither,
nothing is sent.

**After a release is published**, Tico acts on the Dev tickets the release carries (at Pending Release with a
pull request merged in the release repository; prereleases excepted). It wakes each ticket's QA owner (tickets
tagged `qa` only: the post-release tests), leaves each ticket a quiet "Shipped in vX.Y.Z" note, and files the
Technical Writer ONE task for the release, "Docs for vX.Y.Z: N tickets", listing each ticket with its pull request.
It is separate from the release command's "Release notes for vX.Y.Z" task. Docs and release notes are not part of
the pre-release checklist. Only the owner, a person who moves Dev tickets, the Release Manager and the bot that
manages the Dev tickets type run `hub release now`. Routines can run `on: release_published` for one task per release.

## The fast path

1. **While building a PR**, run the focused tests for its changes; CI selects the tests a change can reach (below).
2. **Freeze main.** The Release Manager runs the release command's freeze guard on current main.
3. **Build and validate that exact commit.** The release command dispatches the full hosted Release validation for the
   frozen commit as the new version: it builds the runner binaries and the three images once, then tests those. The
   command waits for that run and checks its title and candidate commit. The 8-minute target is report-only.
4. **Tag that commit.** The command checks main again and pushes the version tag at the validated commit.
   The GitHub release is published about a minute later: the version tags are pointed at the tested images.
5. **Notes follow.** Once the release is published, the command files one Technical Writer task for its notes.
6. **Server rollout** starts at once: the canary install first, then the rest ("Update now", about 2 minutes each).
7. **Desktop follows**: built only when the shell changed, and attached to the published release when it is done.

Releases are started by the Release Manager only. Release validation never runs on a schedule or on a push to main:
each run is a full gate for one candidate.

## Supported versions

What a server accepts is what the code enforces. This is the matrix the release notes refer to.

| Part | Accepted | Enforced by |
|---|---|---|
| Database | schema version 71 (`MIN_BRIDGE_USER_VERSION`) and newer up to this release's; cloud steps up to 75 | `backend/migrations` `preflight`, before the first write |
| Runner | release `MIN_RUNNER_RELEASE` (0.4.0) and newer | `backend/runner_versions.py`: claims from an older runner are paused, with the reason |
| Runner wire | the current and previous minor release, both directions | `scripts/check_wire_contract.py`, [runner-wire-contract.md](runner-wire-contract.md) |
| Desktop app | the release's own version, or the previous release's files when the shell did not change | the release workflow; see [Desktop apps follow the release](#desktop-apps-follow-the-release) |

A database below schema version 71 is refused, with the message "upgrade to v0.5.x first". Open it once with a v0.5.x
release (the one that writes version 71), then upgrade. The legacy import (`manage.migrate_legacy`) is the only path that
opens an older file directly. A newer database, one whose recorded migration this code does not know, and one whose
migration file changed after it ran are all refused before anything is written.

## Migrations

Every persistent schema change is a file in `backend/migrations`: `NNNN_name.sql` (its statements) or `NNNN_name.py`
(an `up(conn)` that does not commit). The number is the next after the highest file. A file runs once, in its own
transaction with its `schema_migrations` row, so a failure leaves neither. Never edit a file that has run: its checksum is
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

Stored work carries over. Approvals keep their payloads and hashes, because the renames touch column names and no
payload key. Queued jobs, webhook sends and automation definitions are rows the new release reads. Rule conditions and
task-received automations that name `owner` or `requester` keep matching, because those names are read through the
rename map. A webhook send queued before the cutover goes out with its body named as the new API names it.
`backend/tests/test_inflight_cutover.py` pins the approval path on a database from before the renames, and the webhook,
rule and automation cases on rows as they were saved.

Per install, in this order:

1. **Stop the writers.** Stop the server; nothing else writes to the database. Runners and the Mac app reach an install
   only through its API (each keeps its own state file, not the hub database), so the server stop is the stop.
   Work in progress is picked up afterwards: a job whose lease runs out is recovered, and a webhook send caught
   mid-delivery returns to ready after two minutes. Docker: `docker compose stop server`. Mac: `scripts/tico -e acme server stop`.
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
   `scripts/tico -e acme server restart`. On start, preflight accepts schema 71 and newer, then the rename files run in
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

The Release Manager uses the Rust `tico-release` command through `scripts/release` for each approved release.
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
`docs-writer`) with the release's commit range. The command needs `gh` and a `hub` login that can create tasks.
The command does not replace the release's publishing approval, evidence checklist or rollout.

Run `publish` in the background while validation runs. A validation run that ends cancelled is rerun by `publish` (same
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
be reused. The notes pull request lands after publication, so it does not affect the release it describes. An admin
merge is allowed only on that fix-PR/new-full-gate path; it does not waive review or exact-commit validation. Do not move the tag to a
new commit to borrow an old run. Roll back this tooling change by reverting it; the manual steps below still work.

## Tests on a PR

Run tests on your computer while you work. On pull requests to main, the PR checks workflow runs the default tests
that a measured static dependency map says use a changed file: what each tracked file imports (transitively), runs
by path (scripts, `-m` modules, shell and JavaScript) and reads by path (data files, folders it lists or globs). The
Caddy image job runs only when a change reaches the image inputs or `backend/caddy_assets.py`; otherwise the plan
compiles the changed files and runs the Dockerfile's import check itself. Conftest, `pytest.ini`, workflows,
dependencies, `docs/` (except `docs/images` and `docs/history`), CHANGELOG.md, the selector, and deleted or
unparsable files fall back to the full default Python suite and core browser scripts. Main pushes always run that full default suite, and a newer main push never
cancels an earlier one; only a pull request's superseded run is cancelled. Runner/harness changes also run
slow conformance on the Rust runner and all its fake harnesses; UI changes select their browser scripts, including
imported-history for chat changes, or every browser script when the UI mapping is uncertain.

Python jobs install no npm packages, browsers or apt packages. The browser job caches both browser binaries and
Ubuntu package indexes/debs under exact runner-image and dependency-lock keys. A warm cache uses apt with
`--no-download`; a cold cache uses the canonical Ubuntu mirror. Each Install attempt has a five-minute limit with
one retry. Pip and npm download caches remain enabled. Checks run on 4-vCPU RunsOn Spot VMs in Tico's own AWS
account (fork PRs on GitHub-hosted runners). `scripts/pr_checks.py` sizes the Python and browser groups so each VM
finishes in about `TICO_CI_JOB_SECONDS` (180) including setup, using JUnit per-file and browser per-script timings
from the last successful full main run, with collected test counts until timings exist. New files use the observed
seconds per test; untimed browser scripts count 20 seconds. The **PR job timings** check links each job and its elapsed seconds in its summary. It also
fails if a required job failed or was cancelled. The target is under ten minutes from start to completed checks;
hosted run receipts, including cache state, establish whether that target is met.

Nothing is mapped by hand or by filename: a test that uses a file in a way the map cannot see (a path built at run
time from a parameter, a subprocess's own reads) must name that file or folder literally, or the change will not
select it. A file that makes a computed import or lists the repository with `git ls-files` runs on every change.
The Release validation workflow retains its full candidate gate and never cancels a running validation.

Write unit
tests while you build and run them as you go; keep every one that guards a real failure, as many as the change needs, and drop the trivial ones. Before merging,
run the test files for what you touched and the checks the change obviously affects, not the full suite. For each release, run the full
suite once on its frozen candidate.

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
core browser scripts (`CORE` in `scripts/ui-tests.cjs`) with the load-aware concurrency described below. The check records wall time and load; it
takes about two minutes, and a run over 300 seconds still passes and says it is slow. There is no budget on the
number of tests: keep every test that guards a real failure, and none of wording or copy. Release validation builds the three images and the Release workflow publishes them with the
GitHub release. The PR checks workflow runs this full default suite on main and on uncertain PR selections; known PR mappings run the affected tests above. Release validation runs the full gate for an explicitly dispatched immutable candidate. `--release` runs this suite too, along with the
slow tests and every browser script, and release candidate validation covers that full suite.

## Hosted release validation

Prefer the manual **Release validation** workflow for the full gate so builds and tests do not compete with bots or
interactive work. Its twelve gate jobs, each about 3 minutes of tests, run in parallel on ephemeral 4-vCPU RunsOn Linux VMs in Tico's own AWS account, with read-only
repository permissions and no production, AWS or provider credentials. It cannot tag, publish or deploy.

```sh
gh workflow run ci.yml --ref main -f candidate_sha=<full-40-character-commit-SHA> -f previous_release=v0.3.48 -f version=v0.3.49
```

The run is titled `Validate <sha> as <version> from <previous>`, which is how the Release workflow finds it. It builds
once: the Linux (static, musl) and macOS runner and relay binaries, then the server, runner and updater images for
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
times with `--record-browser-timings` from a validation's browser logs). The hosted workflow selects product, two browser
shards, eight Python shards and desktop. Only product needs the images: the other parts start as soon as the Linux
binaries are built, and product waits for the image builds; without `--part`, the command runs the whole gate, with desktop only where the Linux app builds. The desktop
part runs the Tauri app's tests on Linux, among them a company app updating from a feed this candidate publishes
(`scripts/app_release.py`) and serves (`backend/downloads.py`) behind a hub session: it must find, download, verify and
install the update. It does not cover the macOS or Windows installers.
Beside the gate, four `factory-sim` jobs run the factory simulation (`evals/factory`) on seeds 1-3, packed into four
groups of about three minutes each by `evals/factory/timings.json`. A failed check fails the release; NOTE lines
(tickets over 24 h, a step over 12 h) go in the job summary for the Release Manager to read.
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

Build the release task's evidence checklist from the candidate's merged PRs and their linked Dev tickets:

```sh
python scripts/release_checklist.py --head <candidate> --previous vX.Y.Z > checklist.md
python scripts/release_checklist.py --head <candidate> --previous vX.Y.Z --json > checklist.json
```

Run in a checkout with fetched release tags, a `gh` login for the repository and a scoped `hub` login for
the shared team API. No local team database is opened. Without `--previous`, it selects the earlier release
tag using the freeze guard's rule. `--github owner/repository` overrides the checkout's origin.
Attach the Markdown checklist to the release task; the JSON companion retains structured evidence and source
message IDs. The checklist includes every PR found in the first-parent candidate range, each visible linked
Dev ticket's current step, Review 1/2 verdicts and their full heads, test receipts, `qa` tag, QA verdicts and
Technical Writer tasks and links. Security is outside the release pipeline; no Security result is required.

Evidence comes from the ticket owner's messages and the owners of step children titled `Review 1:`,
`Review 2:`, `QA:`, `Docs:`, `Documentation:` or `Technical Writer:`. Review 1 always requires a verdict;
Review 2 requires one when its step child exists, and `qa` tickets require a QA approval. Docs are not
checked here: the Technical Writer starts after the release (below). The latest verdict must approve the merged PR head; older heads are flagged for the Release Manager
to resolve with the recorded review delta. A passing test receipt names the full PR head, tests and a passing
result on the same line (for example, `Tests: head <full-sha>: 12 passed, 0 failed`). The latest receipt for that
head must pass; a newer pending result remains a gap. Any recorded failure at that head stays a gap for the Release
Manager to resolve, including after a passing rerun. Other test messages remain visible as evidence. Unrecorded reviews, legacy evidence elsewhere and unreadable tickets stay gaps rather
than being inferred as passes.

Exit 0 means no evidence gaps were found, 1 means the report lists gaps, and 2 means collection failed
(including incomplete pagination). This is a visible-evidence snapshot for the Release Manager to read;
it does not run tests, authorize a release, move tickets, dispatch workflows or replace the freeze guard.

At freeze, before asking for Release validation, check what the candidate carries since the previous tag:

```
python scripts/freeze_guard.py --head <candidate> --receipts receipts.json
```

It names every change that undoes a pull request an earlier release shipped (a stale tree committed over newer work,
or a reverted fix put back): all of it, however small, or most of its meaningful product lines; tests and release notes
don't count. It compares against all history unless `--releases N` limits it (about 15 seconds for a release of 60
pull requests). It also names every merged pull request whose final head has no approving GitHub review or ticket
review receipt at that commit (an approval of an earlier head counts only when the change is the same line for line),
and every commit that landed without a pull request. With no `--previous`, it takes the newest release tag that doesn't
already contain the candidate, and refuses to check an empty range. `receipts.json` lists the heads
the tickets' reviewers approved (`[{"pr": 639, "head": "<sha>", "source": "<ticket>"}]`); the script's docstring has
the format. It exits 1 with findings: settle each one before the candidate goes on, with a review of the final head
or, for an intended revert, a receipt naming the pull request and the change it undoes
(`{"pr": 640, "reverts": "<sha>", "source": "<ticket>"}`).

```
python scripts/release_checks.py --release                     # this checkout is the candidate (v9.9.9)
python scripts/release_checks.py --release --previous v0.3.21  # upgrade from a given release instead of the newest tag
```

This is the full suite, run once per release. It builds the server, runner and updater images once (BuildKit cache;
a source-only change rebuilds one layer per image), while the journey installs the previous release. After every build
finishes, it runs every Python test except Docker isolation, the opt-in `@pytest.mark.slow` ones included (real git,
Docker, servers and long timers), and every browser script (`node scripts/ui-tests.cjs --all`). Python uses half the
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
timeouts, including popups. `ui/tests/support/load.cjs` exports `SLOWDOWN`, `t(ms)` (rounded scaled
milliseconds), and `applyTimeouts(contextOrPage)` for scripts' explicit waits. Direct script runs
default to slowdown one unless the environment sets it. Explicit per-script timeout options still
override the shared defaults; those waits should use `t(...)` and wait on conditions.
Successful builds also start the following checks against the candidate
at the same time, each with its own Docker names; the journey proceeds once those images are ready:

- `docker/smoke.sh`: the server comes up, a runner joins with a one-time code and installs Codex once OpenAI is enabled,
  the server restarts and the runner reconnects, the data survives down and up, and a runner box updates to the
  server's release and rolls back one that does not turn healthy;
- `docker/side-jobs-smoke.sh`: a runner starts and stops the side job the hub assigns to it;
- `runner/tests/test_isolation_docker.py`: Docker isolation against the candidate runner, with
  `TICO_RUNNER_TEST_IMAGE=tico-rc-runner:local` and the same Python worker cap;
- `scripts/journey-test.sh --release`: install the previous release from its checksummed bundle and seed it like a
  real team (older releases' rows, a computer on the previous runner, a team `HUB_` variable granted to BotOps, a bot
  turn that opens a session and adds a task worktree), upgrade to the candidate with "Update now" (the updater
  applies the candidate's bundle and replaces itself), take a turn and add a worktree on the previous runner against
  the candidate server, move the computer to the candidate runner and check the variable, the session, the worktree
  and the rows survived and that a turn still answers once the harness's saved session is gone, roll back an update that migrates the database and never turns healthy (the pre-update
  snapshot is restored, with the server's real entrypoint running Litestream), and update once more to a third build
  so the replaced updater has to pull its image, refresh the bundle and replace itself again.

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
2. Once it is green, check main still equals that commit, then the Release Manager tags it and pushes:
   `git tag vX.Y.Z <validated-commit> && git push origin vX.Y.Z`.
3. Once the release is published, file the Technical Writer's notes task. The notes go in `CHANGELOG.md` under
   `## [X.Y.Z] - YYYY-MM-DD`, below a fresh empty `## [Unreleased]`, with the link references at the bottom updated.

The workflow then:

- runs `scripts/build_install_bundle.py`, which attaches the one-line installers: `install.sh` and the Windows
  `install-wsl.ps1` with the tag baked into them, `tico-bundle-vX.Y.Z.tar.gz` (`compose.yaml`, `.env.example`,
  `docker/runner.compose.yaml` and the `setup/` wizard) and `SHA256SUMS` over all three. `install.sh` checks the bundle against `SHA256SUMS` before it unpacks anything;
- attaches the validated Rust runner for checkouts, `tico-runner-{macos-arm64,macos-x86_64,linux-x86_64,linux-arm64}.gz`
  (Linux static, musl), with the relay's, and `tico-runner-SHA256SUMS` over them. A Mac or Linux checkout on the tag
  downloads its one ([updates](updates.md#the-runner-engine));
- checks each validated image says it is `vX.Y.Z`, then points `ghcr.io/ticoteam/{tico,tico-runner,tico-updater}:vX.Y.Z`
  (and `latest` for a stable version) at those digests before publishing, so no installer runs against missing images;
- marks a tag with a suffix such as `v0.2.0-rc.1` a prerelease, which the update check ignores;
- publishes the release with those four files and the runner binaries, with no notes: the notes are written
  afterwards in `CHANGELOG.md`. That is everything the installer, the server's update
  check and the updater read (`install.sh`, the bundle and `SHA256SUMS`), so the server rollout can start at once.

The images set `TICO_VERSION` in the image, which is how the running app
knows its version (a source checkout reports `dev`). There is no source archive: the server and the runners run from
the images, and a Mac runner is a git checkout that moves to the release's tag. The Dockerfile copies the source tree
last, after system packages, tools and the Python dependencies, so with the GitHub Actions layer cache a release
rebuilds and pushes one small layer per image.

## Desktop apps follow the release

The desktop app never holds up the server release. The Release workflow compares the tag with the previous release
tag: when nothing the shell is built from changed (`app/`, `scripts/app_release.py`, `scripts/company_apps.py` and the
two desktop workflows), it builds nothing and copies the previous release's desktop files and `latest.json` onto the new
release, with the manifest's URLs moved to the new tag. The app keeps its earlier version, so installed apps are not
offered an update, and the download links keep working. When the shell did change, `.github/workflows/app.yml` builds
it (with a Rust cache, so only the app crate recompiles) and the files are added to the already published release, the
updater manifest last. A failed desktop build leaves the server release published and the Release run red; rerun the
workflow from the tag (Run workflow, `desktop` checked) once it is fixed. The app's version is the release's
(v0.3.6 → app 0.3.6, including any prerelease suffix), stamped from the tag at build time.

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

## The Release Manager's rollout

A team that runs several installs can hand the whole rollout to its Release Manager bot. The owner's approval of a
named release on its release task is the only gate: an approval card the bot requested (`hub approval request --kind
publish`) that the owner approved, or the owner's own message on the task naming the version. From there the bot runs
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

- **Write on the repository.** An owner or admin ticks the repository in Settings → Repositories and gives the bot
  write on it: `hub bot repos <slug> --chosen <owner>/<repo>:write` (list the bot's other chosen repositories in the
  same command; `--chosen` replaces them), or the same in the bot's settings ([Repositories](repositories.md)).
- **An update key per install.** The owner of each install, the team's own included, runs
  `hub service-key create --label "<team> Release Manager" --scope update` there ([Service keys](service-keys.md#update-keys)).
  Each key is stored in Tools → Credentials on the Release Manager's install, granted to the bot, with the variable
  named in its config.
- **The list of installs.** `knowledge/rollout.json` in the bot's repository: the repository, and each install's
  `name`, `url`, `key_env` and whether it is the `canary` (see `knowledge/rollout.example.json`).

A bot created before this template change does not get these files on its own: copy `playbooks/rollout.md`,
`software/rollout.py` and the `.claude/settings.json` line that allows it from `templates/catalog/release-notes`.

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
`idle`, `pulling`, `restarting`, `healthy`, `rolled_back` or `failed`. Both carry
`Authorization: Bearer <TICO_UPDATER_TOKEN>`. Without an updater, "Update now" shows the command to
run on the server: `docker compose pull && docker compose up -d`.

## Publish the release docs

The tagged repository is the source for that release's manual. Run `python -m backend.openapi_v2` and
`python scripts/build_api_docs.py` after changing API descriptions; check the spec and API guide together.

Publish the public site's docs from the same tag, including the docs landing page's three reader paths and its **Use Tico** and
**Glossary** links. The website source and publishing process live outside this repository. Check that its install and demo pages
use the latest-release installer and `latest` demo image by default, with version pinning shown separately, and mention Docker Desktop
on Mac. Keep the local install command visible on the home page and continue through model sign-in and a first bot result.

Validate the published copy against this release: server Decision calls and HQ suggestion disclosures, 25 active member bots by default,
Inbox Manager's single disabled weekday 07:30 Routine and assigned-mailbox access, and the complete KPI colour example in
[Goals and KPIs](goals-and-kpis.md#the-colours). Run `npm run screenshots` when local dependencies and browsers are available and publish
images from the same release as the UI. Preserve inbound links when moving pages; the transcript and old docs-sync pages link to their successors.

## Company apps

A version tag whose shell changed builds, beside the generic desktop app, `.github/workflows/company-app.yml` for
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

Validate a private list locally without printing it or fetching icons:
`python scripts/company_apps.py matrix --dry-run` with `TICO_COMPANY_APPS` supplied through
your environment. Publishing still requires the owner's release instruction.
