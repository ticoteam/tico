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
`python3 -m pytest -q` (parallel by default) runs the Python suite; `npm run test:ui` runs the browser
scripts in `ui/tests/` a few at a time (Playwright, needs `npm ci`). Tests run locally; GitHub Actions does not run tests. A full local run (pytest plus UI) must
stay under 5 minutes; `python scripts/release_checks.py` runs both and records time and load (the per-PR gate); `--release` runs only the Docker whole-product checks before a tag (docs/releasing.md). The suite is deliberately small: write tests while building if they help, then keep
only the ones that guard a security or privacy boundary, data safety, or a core contract, and delete the
rest. Adding tests that push the run past 5 minutes means cutting something else. `evals/botops/run.py` scores a real BotOps
against a dev install with a real model, on demand only; `backend/tests/test_botops_evals.py` is its scripted layer.

## Conventions
- Keep real team names, human names, domains, buckets and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- The UI has no build step and loads nothing from the network: vendored libraries live in
  `ui/vendor/` with their licenses.
- Comments say why in the present tense; they do not name who asked or when.
