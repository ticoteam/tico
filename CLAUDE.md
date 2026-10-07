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
`@pytest.mark.slow`; `npm run test:ui` runs the core browser scripts (`CORE` in `scripts/ui-tests.cjs`). Tests run
locally; GitHub Actions does not run tests. The default Python suite stays under about 60 seconds and the whole
default run (pytest plus core UI) under 2 minutes; `python scripts/release_checks.py` runs both and records time and
load. A PR runs only the tests for what it changed; the full suite runs once, right before a release. `--release` runs every test (slow included, `ui-tests.cjs --all`) and the Docker
whole-product checks before a tag (docs/releasing.md). The suite is deliberately small: keep only tests that guard a
privacy or permission boundary, migrations and data safety, or a core task/run flow, one or two per rule; no tests
of wording or docs, no exhaustive parametrizations. Write tests freely while building, then keep only the highest-value ones. Real git, Docker, servers or long timers go behind
`@pytest.mark.slow`. Adding a test that slows the default run means cutting another. `evals/botops/run.py` scores a real BotOps
against a dev install with a real model, on demand only; `backend/tests/test_botops_evals.py` is its scripted layer.

## Conventions
- Keep real team names, human names, domains, buckets and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- The UI has no build step and loads nothing from the network: vendored libraries live in
  `ui/vendor/` with their licenses.
- Comments say why in the present tense; they do not name who asked or when.
