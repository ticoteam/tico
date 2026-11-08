# Contributing to Tico

Thanks for helping. Tico is released under the [PolyForm Perimeter License 1.0.1](LICENSE).
By submitting a contribution, you license that contribution under both PolyForm Perimeter 1.0.1
and [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). This lets the maintainers
include your contribution in the current Tico or in a future Apache 2.0 release. It does not
license Tico as a whole under Apache 2.0.

There is no separate contributor license agreement (CLA): outside contributors sign off each
commit instead (see below). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Open an issue on [ticoteam/tico](https://github.com/ticoteam/tico/issues) that states the
  current behavior, the wanted outcome and how you will check it. Small fixes can go straight
  to a pull request.
- Report security problems privately, as described in `SECURITY.md`.

## Running the tests

```bash
pip install -r backend/requirements-dev.txt
python -m pytest -q                            # the default Python suite, in parallel (about a minute)

npm ci
npx playwright install chromium
npm run test:ui                                # the core browser scripts (CORE in scripts/ui-tests.cjs)
```

Run both locally before you open a pull request. GitHub Actions lints the pull request again and, once it is ready, runs its selected tests (the PR checks workflow);
the merge queue runs them again on the merged result before a change lands. The default run leaves out
tests marked `@pytest.mark.slow` (real git, Docker, servers, long timers) and the browser scripts outside the core
list; `python -m pytest -q -m slow` and `node scripts/ui-tests.cjs --all` run them, and the release check does. Run
them too when you change what they cover. To run less while you work: `python -m pytest -q backend/tests/test_x.py`
for one file, `node scripts/ui-tests.cjs <name>` for one browser script. If you touch `app/`, also run `cargo check`
there. `python3 scripts/remote.py -- <command>` runs a command on a fresh VM instead: it exits 75 when it never dispatched
the run and 76 when the run ended without a result; any other code is the command's own (`--help` lists them).

## Before you push

`scripts/check.sh` is the fast local gate. It runs each static check that applies to what you changed (the Python
lint baseline, shellcheck, actionlint, cargo fmt, the migration order, the UI API types and basedpyright on the
backend against its baseline), one line per check with its time, and exits non-zero if one fails. It compares against the merge-base
with origin/main, so committed, uncommitted and new files are all checked. `scripts/check.sh --all` runs every check
on every tracked file; CI runs the same checks. Turn on the pre-push hook once per clone:

```bash
git config core.hooksPath .githooks
git config merge.tico-json.driver 'python3 scripts/json_merge.py %O %A %B'
git config merge.tico-baseline-counts.driver 'python3 scripts/baseline_merge.py counts %O %A %B'
git config merge.tico-baseline-toml.driver 'python3 scripts/baseline_merge.py toml %O %A %B'
git config merge.tico-baseline-pyright.driver 'python3 scripts/baseline_merge.py pyright %O %A %B'
```

After that, each `git push` runs `scripts/check.sh` first. To push without it once, use `git push --no-verify`.
The second line makes a rebase merge two edits to `docs/openapi/v2.json` by key (`scripts/json_merge.py`), so they stop
conflicting; without it git merges that file as text. The last three lines do the same for the lint baseline
(`ruff-baseline-counts.json`, `ruff-baseline.toml`) and the type baseline (`.basedpyright/baseline.json`): each side's
change is applied to the base, so two branches that fix different findings merge. GitHub's "Update branch" button and
the merge queue merge as text and do not run these drivers; update a branch locally when it needs the drivers. The
generated `ui/types/api.d.ts` is not committed: `scripts/check_ui_types.sh` writes it.
shellcheck and actionlint are pinned versions downloaded on first use and checked against their SHA-256 (see
`scripts/pinned_tool.sh`); they are cached under `~/.cache/tico/tools`.

The UI's scripts have a strict type check (`python3 scripts/check_ui_strict.py`, run by `scripts/check.sh`), a loose
type check of the `.js` files left (`bash scripts/check_ui_types.sh`) and a Biome lint (`bash scripts/check_ui_lint.sh`),
all seconds long. Each counts findings per file against its own baseline file: `ui/baselines/strict/<path>.json` for a
`.ts` file with strict findings (a new `.ts` file must be at zero), `ui/baselines/tsc/<path>.json` for a `.js` file, or
`ui/baselines/biome/<path>.json` (a file with no findings has none). Flipping a `.js` file to `.ts` is
`python3 scripts/ui_flip.py`: it moves the file and its baselines, and changes its references and its script tag in
`ui/index.html`, which keeps a blank line between script tags so that flips of different files do not conflict in a
merge. The counts only shrink: after you fix findings, lower the baseline with
`python3 scripts/check_ui_strict.py --write` (or `python3 scripts/ui_baseline.py tsc --write`, or `biome`).
`ui/index.html` must keep the blank lines: `python3 scripts/ui_baseline.py migrate` adds them.
The UI's API calls are checked against the backend's routes by `bash scripts/check_ui_routes.sh`: it imports the app and
walks its routes (no server), and counts the calls that match no route per file in `ui/routes-baseline.json`, which
only shrinks the same way. Fix a call by pointing it at the route it should reach; the baseline is written with
`python3 scripts/check_ui_routes.py --write`.

## Profiling API test setup

Each API test creates a fresh app, store, authentication state, registry and database. For targeted
runs with repeated API setup, `--api-database-snapshots` restores a worker-local schema snapshot with
SQLite's backup API before normal lifespan initialization. It is opt-in: template construction can
cost more than it saves for small selections at high worker counts. `--fresh-api-databases` forces
empty database paths, and direct Store/migration fixtures always initialize from empty paths.

Compare the same tests and dependencies with alternating fresh/snapshot runs:

```bash
python scripts/benchmark_test_setup.py --output /tmp/tico-setup-benchmark --workers 2 4 6 auto --rounds 3
```

Use a new output directory each time. The JSON and logs preserve every result, worker count,
dependency hash, command and system load. A targeted worker-count result does not establish the
best count for the whole suite; keep explicit `-n N` for a measured selection.

## Updating dependency locks

Resolve runtime packages first, then development packages against those pins. This keeps tests on the
same shared dependencies as installations. Use the supported Python version for both commands:

```bash
uv pip compile backend/requirements.in --python-version 3.15 -o backend/requirements.txt
uv pip compile backend/requirements-dev.in --python-version 3.15 -o backend/requirements-dev.txt
```

Upgrade a small compatible group with `--upgrade-package NAME` on the first command, then regenerate
the development lock. Keep major migrations separate. After changing the browser SDK pins in
`package.json`, run `npm run build:observability` and the real SDK privacy and retry tests in
`node scripts/ui-tests.cjs telemetry`. The desktop GLib backport and its provenance checks are
documented in `app/vendor/README.md`; retain them when updating Cargo locks.

## Developer Certificate of Origin

Every commit in an outside contributor's pull request must be signed off, which says you wrote it or have the right to
submit it under the contribution licenses above, as set out in the
[Developer Certificate of Origin](https://developercertificate.org/). Add the sign-off with `-s`:

```bash
git commit -s -m "Fix the thing"
```

That appends `Signed-off-by: Your Name <you@example.com>`, using your git `user.name` and
`user.email`, which must be your real name and an address you use. To add it to commits you
have already made: `git rebase --signoff main` (then `git push --force-with-lease`). A pull request
with unsigned commits will be asked to sign them before it is merged.

This applies to outside contributors. Tico team members and the company's own bots commit work the
company already owns, so their commits need no sign-off, and their pull requests are never held for one.

## Pull requests

- Make the smallest change that fixes the problem, against `main`.
- High-value tests, as many as the change needs. Each one guards a real failure: a security or privacy boundary,
  data safety (migrations, backup, restore), a core contract (the updater and release path, job claim and lease,
  task writes, the API schema), a regression or an edge case that would lose or duplicate work. No tests of
  wording or docs prose, trivial getters or exhaustive parametrizations of one path. Mark a test
  `@pytest.mark.slow` if it needs real git, Docker or a server.
- Added or changed an icon in `ui/`? Run `python3 scripts/build-icon-font.py` to rebuild the icon font subset (a test fails until you do).
- New UI files are TypeScript (`ui/app/name.ts`, strict types, no `any`). After `npm ci`, `npm run build:ui` compiles
  them to `build/ui/` (`npm run watch:ui` while editing); the server will not serve a stale build.
  `bash scripts/check_ui_types.sh` type-checks them.
- Keep the tests green, and do not add a dependency, a network call in the UI or a build step
  without saying why.
- Comments explain why, in the present tense. They do not name who asked or when.
- Keep real team names, human names, domains and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- Describe the behavior change and how you checked it in the pull request.
- Fill in the template's Spec, Demo steps and Risk sections. The release notes that approvers read are built from them:
  the Spec link, the steps to see the change work in the Acme Lights demo company, and the risk level with what could go
  wrong.

## How we work

- Trunk-based: everything lands on `main` through short-lived branches named `<who>/<topic>`, one ticket per PR.
- Draft PRs run no CI. Mark the PR ready for review to start its checks (lint and the selected tests); each later push runs them again.
- Once the PR is reviewed, its `checks` pass, and the tests you touched pass with `scripts/remote.py`, enqueue it with
  `gh pr merge <N> --merge`. The merge queue tests the PR merged onto current main before the change lands.
- After a queue failure, fix it and get green PR checks before you enqueue again.
- Enqueue once. Don't rebase or merge main just to be current; rebase only when GitHub reports a conflict.
- If the queue removes the PR, read why before you push again. Never use `--admin` or the REST merge endpoint:
  both skip the queue.
- If a release candidate goes red because of your merge, fixing it comes before anything else.

## Releases

Release notes are on each release's approval issue (label release-approval).

A release is a version tag, and contributors do not tag. Tico releases from release windows (weekdays 07:30, 11:00,
14:00 and 20:30 Los Angeles time, weekends 07:30 and 15:00):
GitHub Actions validates main's head and ships the first green candidate to Tico Team's install (the canary). Stable
(TIDY) takes a canary release only after a person with write access approves its release notes, on that release's
`release-approval` issue (the `approved` label, or a `/approve` comment). A hotfix is for major production issues only.
The Release Manager bot no longer tags or rolls out Tico releases; it only monitors them. The details are in
[docs/releasing.md](docs/releasing.md#release-windows), and the approval steps are in
[docs/releasing.md](docs/releasing.md#approval).
