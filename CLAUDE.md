# Tico

Tico is a source-available operating system for a team of humans and bots (PolyForm Perimeter 1.0.1): a FastAPI
server in `backend/`, the one-page web UI in `ui/` (`index.html`, `app/`, `styles/`; see ui/README.md), the runner in `runner/`, the `hub`
CLI and MCP tool table in `clients/`, and a Tauri desktop shell in `app/`. `README.md` says how to
run it and `docs/how-it-works.md` describes the system.

## Work queue
Engineering work on Tico is GitHub issues on ticoteam/tico, worked from a Claude Code session until
none are left: read `skills/tico-tickets/SKILL.md` before filing, picking or merging anything. A
bot's own work is tasks, never issues.

## Tests
`python3 -m pytest -q` (parallel by default) runs the default Python suite, which leaves out tests marked
`@pytest.mark.slow`; `npm run test:ui` runs the core browser scripts (`CORE` in `scripts/ui-tests.cjs`). On a fresh computer, a bot's runner
included, run `npm ci && npx playwright install chromium` and install `backend/requirements-dev.txt` in a venv
first: runner images ship neither. Without Google Chrome, set `TICO_BROWSER_CHANNEL=` (empty) so the scripts use
Playwright's Chromium. On a small computer (two CPUs, software WebGL) run the scripts you changed one at a time;
the parallel suite is the release check's job on a full-size machine. PR tests run
locally. The manually dispatched Release validation workflow runs the full release gate on isolated
GitHub-hosted Linux VMs, in parallel parts, for an explicit immutable candidate SHA; no push or PR triggers run tests. The default Python suite stays under about 60 seconds and the whole
default run (pytest plus core UI) under 2 minutes; `python scripts/release_checks.py` runs both and records time and
load. A PR runs only the tests for what it changed; the full suite runs once, right before a release. `--release` runs every test (slow included, `ui-tests.cjs --all`) and the Docker
whole-product checks before a tag (docs/releasing.md). The suite is deliberately small: keep only tests that guard a
privacy or permission boundary, migrations and data safety, or a core task/run flow, one or two per rule; no tests
of wording or docs, no exhaustive parametrizations. Write tests freely while building, then keep only the highest-value ones. Real git, Docker, servers or long timers go behind
`@pytest.mark.slow`. Adding a test that slows the default run means cutting another. `evals/botops/run.py` scores a real BotOps
against a dev install with a real model, on demand only; `backend/tests/test_botops_evals.py` is its scripted layer.
`evals/replay/run.py` replays signed real tasks on a candidate at release preparation (docs/releasing.md); its cases
hold customer text and never enter this repository.

Tests stay at zero failures. An intermittent test is fixed quickly or deleted; never rerun it until it passes, and
never ship around a known failure. If a test may fail, it shouldn't exist.

## Hit every surface
The commonest defect in Tico is a change that works where it was tested and is missing everywhere else. Before
calling a change done, walk this list and say in the PR which entries applied:
- **Clients**: the web app at desktop and phone widths, the Tauri app, Slack, the `hub` CLI and the MCP tool table
  (`clients/hubtools.py`), API v2 and `docs/openapi`.
- **Runners**: the Rust runner, on a Mac and in the Docker image. The Python runner is deprecated; leave it alone.
- **Harnesses**: every harness, through the capabilities in its `runner/harnesses/*.toml`, never by comparing names.
  A capability is on only when the harness really does it; unknown is reported as unknown.
- **Who acts**: a human, a bot, BotOps acting for a human, and an external agent over MCP.
- **Reverse states**: a way in needs a way out and a way to see it. Snooze needs unsnooze, close needs reopen,
  grant needs revoke.
- **Docs**: change the page the change makes wrong; do not add a page per feature.

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
- Keep real team names, human names, domains, buckets and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- The UI has no build step and loads nothing from the network: vendored libraries live in
  `ui/vendor/` with their licenses.
- Comments say why in the present tense; they do not name who asked or when.
