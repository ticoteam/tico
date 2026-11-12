# Tico

Tico is a source-available operating system for a team of humans and bots (PolyForm Perimeter 1.0.1): a FastAPI
server in `backend/`, the one-page web UI in `ui/` (`index.html`, `app/`, `styles/`; see ui/README.md), the runner in `runner-rs/` (Python helpers in `runner_support/`), the `tico`
CLI and MCP tool table in `clients/`, and a Tauri desktop shell in `app/`. `README.md` says how to
run it and `docs/how-it-works.md` describes the system.

## Work queue
Engineering work on Tico is GitHub issues on ticoteam/tico, worked from a Claude Code session until
none are left: read `skills/tico-tickets/SKILL.md` before filing, picking or merging anything. A
Tico bot's work is its task, never issues (next section).

## A Tico bot on a Tico task
A Tico bot working a task on this repository follows these where the rest of this file differs:
- The task is the work queue. Never file, pick or close GitHub issues, and never use the tico-tickets skill.
- Run `scripts/dev_setup.sh` once in each clone or worktree, and again after a pull that changes a lock file.
- Test only what the change can break, one file at a time: `.venv/bin/python -m pytest -q -n 2 <file>`. Never the
  whole suite, `scripts/pr_checks.py local` or `scripts/remote.py` (bots have no access to it); PR checks run the rest.
  Mark the PR ready with `gh pr ready`.
- Never skip the pre-push hook. If it cannot run, say on the task what is missing.
- Never enqueue or merge: Tico queues the PR when the task reaches Merge. Never tag, and never cut or change a release.
- Never edit `.github/workflows/`: the push is refused. Say so on the task; a person lands that part.
- Branches are `tico/<first 8 of the task id>-<slug>`, as Tico's task worktrees name them.
- Tico opens the branch's draft PR at its first push and links it: edit it with `gh pr edit`, never `gh pr create`.

## Tests
`python3 -m pytest -q` (parallel by default) runs the default Python suite, which leaves out tests marked
`@pytest.mark.slow`; `npm run test:ui` runs the core browser scripts (`CORE` in `scripts/ui-tests.cts`). In a fresh clone or worktree, a bot's
runner included, run `scripts/dev_setup.sh` first (`--browser` readies the browser scripts: Google Chrome when it is on PATH,
else Playwright's Chromium): runner images ship no dev tools. The browser scripts run on Google Chrome, so leave
`TICO_BROWSER_CHANNEL` unset (Tico computers have Chrome). A computer without Chrome (rare; a person's laptop) runs
`scripts/dev_setup.sh --browser` for Playwright's Chromium and sets `TICO_BROWSER_CHANNEL=` (empty).
On a small computer (two CPUs, software WebGL) run the scripts you changed one at a time;
the parallel suite is the release check's job on a full-size machine. The PR checks
workflow runs the cheap checks (lint, generated types, the OpenAPI document, the wire contract, workflows, shell scripts) on each push to a
pull request, on free GitHub-hosted runners. A ready pull request from this repository also runs a light set of tests (`plan --light`: the tests its change reaches directly, the smoke set, at most ten VMs; the browser scripts and Caddy image only when it touches their own inputs; `scripts/pr_checks.py` NARROW), and every other job the queue runs (the Caddy image, the Rust and desktop checks, the macOS recorder tests) when its change reaches it, on GitHub-hosted runners, so `checks` (the one required check) passes only when they pass; a draft runs nothing, and a fork's pull request runs the cheap
checks only. PR checks and the queue run a test listed in `ci/quarantine/` (it failed and passed on one commit) without failing on it; release validation still fails on it (`scripts/flake_quarantine.py`, `.github/workflows/flaky-tests.yml`). The fix for a quarantined test deletes its entry file. The merge queue runs the tests again, once per queue group on the merged result, on 4-vCPU GitHub-hosted runners: the tests a measured static
dependency map (imports, plus files run or read by path) says use the change within two levels (not through a hub such as `backend/app.py`), capped at nineteen VMs, with the smoke set when that drops any; release validation's incremental runs and its daily full run cover the rest, the Caddy image job when the change reaches
it, the Rust and desktop checks when they changed, and the macOS recorder tests when `recorder/` changed. A docs page or GitHub file runs the tests that read it. Dependency pins, conftest, pytest.ini, deleted or unparsable files
and UI files outside the UI map run the tests that name or read them and the smoke set (`scripts/queue-smoke.json`, checked by
the static job), never the full default suite or every browser script; release validation runs both. Draft pull requests run no checks until marked ready for
review; a skipped run is not a pass. Main has no check run of its own: the queue tests each change before it lands, and
Release validation tests each candidate. Agents run the tests they touched locally before marking a PR
ready. RunsOn is for release checks only; ordinary test runs happen locally. A newer push cancels a pull request's superseded run. A failed test leg cancels its whole run, so a failing pull request or
queue group stops at once; `checks` still runs and reports it. PR and queue runs restore GitHub's own caches, which `pr-cache.yml` fills from main. Python and browser groups are sized from the latest successful Release validation
run's per-file and per-script timings (collected counts until timings exist) so each VM finishes in about 330 s, under seven minutes
(`TICO_CI_JOB_SECONDS`, 330), and an interrupted Spot job is rerun automatically. Browser and system-package caches avoid apt
downloads on warm queue runs; each Install attempt is limited to five minutes, with one retry. The PR job timings check
on a queue run links the per-job timing receipt. Runner/harness changes also run conformance, including slow tests; unclassified UI changes run all browser scripts. The manually dispatched Release validation workflow runs the full release gate across isolated
RunsOn Linux VMs for an explicit immutable candidate SHA. It never runs on a schedule; the release train dispatches it (docs/releasing.md#the-release-train). The default Python suite runs in parallel in about a minute and the whole
default run (pytest plus core UI) in about 2 minutes; `python scripts/release_checks.py` runs both and records time and
load. On a computer several bots share, start a full suite, the browser scripts or a cargo build through
`scripts/heavy.sh` (`scripts/full-suite.sh` for the Python suite; `release_checks.py`, `ui-tests.cts` and pytest in a
bot's turn do it themselves): at most `TICO_HEAVY_SLOTS` (2) such runs at once per box, at low priority, the rest waiting with
"waiting for heavy-job slot (n holders)", so start them detached. Hosted CI skips it. Release checks run off the
shared computer: `python3 scripts/remote.py -- <command>` (or `TICO_HEAVY_REMOTE=1` for anything that goes through
`heavy.sh`) runs it on a fresh RunsOn VM against the pushed HEAD, then prints its last lines and exits with its code: 75 when it never dispatched the run, 76 when the run ended without a result, and 2 for a bad command line (`--help` lists them). At most 4 remote runs of the repository go at once (`TICO_REMOTE_MAX` changes that, 0 for none); a run past the cap waits up to 20 minutes for a slot.
It needs `gh` signed in with permission to dispatch workflows; a test file or two you changed still runs locally (in a bot's
turn with `TICO_HEAVY_REMOTE=1`, a pytest run naming up to three test files stays local and a whole suite goes remote).
The never-stuck fault rig (`python -m evals.never_stuck.run`, evals/never_stuck/README.md) runs the task engine on a
fake clock and breaks things on purpose; `backend/tests/test_never_stuck_rig.py` runs its scenarios. Locally, a PR runs only the tests for what it changed; for each release, run the full suite once on its frozen candidate. `--release` runs every test (slow included, `ui-tests.cts --all`) and the Docker
whole-product checks before a tag (docs/releasing.md). There is no budget on how many tests the suite has: write as many as a change needs, as long as each one
guards a real failure (a privacy or permission boundary, migrations and data safety, a core task/run flow, a
regression, an edge case that would lose or duplicate work). No tests of wording, copy or docs, trivial getters or
exhaustive parametrizations of one path. Real git, Docker, servers or long timers go behind `@pytest.mark.slow`. `evals/botops/run.py` scores a real BotOps
against a dev install with a real model, on demand only; `backend/tests/test_botops_evals.py` is its scripted layer.
`evals/replay/run.py` replays signed real tasks on a candidate at release preparation (docs/releasing.md); its cases
hold customer text and never enter this repository.

Tests stay at zero failures. An intermittent test is fixed quickly or deleted; never rerun it until it passes, and
never ship around a known failure. If a test may fail, it shouldn't exist.

## Before you push
Turn on the pre-push hook once per clone: `git config core.hooksPath .githooks`. It is expected in every clone, bots' included.
Each push runs `scripts/check.sh` (the checks for what you changed) and `scripts/check_conflicts.sh`, which stops the push
when the branch would not merge into origin/main and names the files. A person may skip it once with `git push --no-verify`
(a bot never does); the PR checks run the same static checks, so a skipped failure still shows there. CONTRIBUTING.md lists where the local gate differs from CI.

## How we work
Trunk-based: branches are `<who>/<topic>` (a Tico bot's: `tico/...`), one ticket per PR. Draft PRs run no CI. The flow: open the PR as a draft
(`gh pr create --draft`) and push freely; when done, a person runs `python3 scripts/pr_checks.py local --ready`, which runs the
tests the PR checks select for your diff on this computer, prints a receipt line with the head sha for the ticket, and
marks the PR ready after a pass. A Tico bot never runs that script: it marks its PR ready with `gh pr ready` after its own tests (see "A Tico bot on a Tico task"). Mark ready once: each push to a ready PR runs CI again, so batch later fixes. Wait for
green `checks` on that run (not skipped), and once it is reviewed, enqueue it with
`gh pr merge <N> --merge`: the merge queue tests the PR merged onto current main before it lands. Enqueue once. No bot
enqueues: Tico queues a bot's PR at the checked head when its task reaches Merge, and a queue removal goes to the
Release Owner (docs/pipelines-v3.md).
After a queue failure, fix it and get green PR checks before enqueuing again.
Don't rebase or merge main to be current; rebase only when GitHub reports a conflict. If the queue removes the PR,
read why before you push again. Never use `--admin` or the REST merge endpoint: both skip the queue.
PR checks on each push run lint, and a ready PR's selected tests. A red release candidate caused by your merge is your top priority. Releases ship on their
own, at any hour, whenever main has merged work past the newest release (docs/releasing.md#the-release-train); never
tag. Stable takes a release automatically when its notes list no High risk item; one with a High risk item, or every
one while the repository variable `RELEASE_NEEDS_PERSON` is `on`, waits for a person's `approved` label or `/approve`
(docs/releasing.md#approval). Hotfixes (`hotfix.yml`) are for
major production issues only. No bot tags or rolls out Tico releases; the Engineering pipeline's Release Owner sees
reviewed work through the merge queue.

## Hit every surface
The commonest defect in Tico is a change that works where it was tested and is missing everywhere else. Before
calling a change done, walk this list and say in the PR which entries applied:
- **Clients**: the web app at desktop and phone widths, the Tauri app, Slack, the `tico` CLI and the MCP tool table
  (`clients/hubtools.py`), API v2 and its spec (`backend/openapi_v2.py`).
- **Runners**: the Rust runner, on a Mac and in the Docker image.
- **Harnesses**: every harness, through the capabilities in its `runner-rs/harnesses/*.toml`, never by comparing names.
  A capability is on only when the harness really does it; unknown is reported as unknown.
- **Who acts**: a human, a bot, BotOps acting for a human, and an external agent over MCP.
- **Reverse states**: a way in needs a way out and a way to see it. Snooze needs unsnooze, close needs reopen,
  grant needs revoke.
- **Docs**: change the page the change makes wrong; do not add a page per feature.
- **Demo mode**: a feature with a page adds or updates its `backend/demo_sections/` module, and `test_demo_coverage.py` maps the page.

## Ways to hurt yourself
1. **Killing by pattern.** Never `pkill -f` or kill a PID found by matching a name or path: bot runs, runners and
   other worktrees' servers share this computer. Kill only a PID you started.
2. **Writing to a live install.** Never start a server against a live install's database or open it read-write,
   and never work in a bot's live checkout the runner uses. For real data, make a copy with `python -m backend.devcopy`;
   it starts in rehearsal mode, which sends nothing and refuses runners.
3. **Unbounded lists.** A list endpoint is paged and byte-capped, and tool output never goes in a list or an event.
   `backend/tests/test_payload_budgets.py` holds the limits.

When the server is slow or stuck, read `python -m backend.flight summary --since 30m` before guessing: it reads the
flight file, not the database.

## Conventions
- Create persistent schema changes with `python3 scripts/new_migration.py name` (`--py` for `up(conn)`):
  `YYYYMMDDHHMMSS_name.sql|py`, UTC. Keep existing `NNNN_` files and applied checksums unchanged. Each migration
  stands alone and must not depend on another unmerged migration's schema; pending files run even below applied ones.
- Format the Python files you change with `uvx ruff@0.15.9 format <files>` before committing; `scripts/lint.sh` fails on unformatted code. Branches older than the format commit: `scripts/format_rebase.sh`.
- The Python lint and type baselines are per source file: `baselines/ruff/<path>.json` and `baselines/pyright/<path>.json`. After fixing findings, run `python3 scripts/lint_baseline.py --write` (types: `scripts/check_types.sh --writebaseline`). Never commit `ruff-baseline.toml` or `.basedpyright/baseline.json`: they are generated and git-ignored. A bare `uvx ruff@0.15.9 check` reads `ruff.toml` alone and reports every finding, baselined or not; `scripts/lint.sh` is the gate that counts them against the baseline (`ruff-baseline.toml` extends `ruff.toml`, never the other way round).
- Keep real team names, human names, domains, buckets and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- The UI loads nothing from the network: vendored libraries live in `ui/vendor/` with their licenses. Write a new
  UI file as TypeScript (`ui/app/name.ts`, listed in index.html by that name; strict types, no `any`). `npm run build:ui`
  compiles it to `build/ui/` (`npm run watch:ui` while editing; tests and `backend.server` build it when they can), and the
  server refuses a missing or stale build (ui/README.md).
- Generated files: `ui/types/api.d.ts` is written by `scripts/check_ui_types.sh`, and the API spec is generated by
  `python3 -m backend.openapi_v2` (served at `GET /api/v2/openapi.json`). Neither is committed, so never edit or hand-merge
  them: their source is `backend/openapi_v2.py`, where a rebase conflict is resolved.
- Every UI script is strict TypeScript, enforced by `scripts/check_ui_strict.py`: a `.js` under `ui/` fails unless it is
  vendor code, a Node test, or in the `KEPT_JS` list (the classic scripts that stay JavaScript, each with its reason).
  Each `.ts` must pass `ui/tsconfig.strict.json`; one with findings has a piece under `ui/baselines/strict/` that only
  shrinks, and a new `.ts` must be at zero (a renamed `.js` may keep its count).
- Comments say why in the present tense; they do not name who asked or when.
