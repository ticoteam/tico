# Redesign test world

The Acme org that the browser tests and some backend tests run against. Each check is a real API call against an
in-process hub on a fake clock (`evals/redesign/world.py`); nothing is stubbed.

The org is Acme: Dana (owner), Omar and Priya (people; Omar is a peer of the Engineering Lead, Priya reports to Dana),
the bots engineering-manager, builder-claude, builder-openai and release-manager. Emails are @acme.example. No real names
belong in this directory.

Files
- `world.py`: a disposable hub on a fake clock, with its server and runner.
- `harness.py`: the Acme org (`Org`), the API calls a teammate makes, and the helpers the seeds share.
- `seeds.py`: the named seeds the UI's real-hub tests apply (`ui/tests/support/real_hub_server.py`), such as `directory`,
  `tools` and `counsel`.
- `fakes.py`: the judge replay, Slack and Gmail fakes, the incoming-webhook helpers and the outgoing receiver.
- `record.py` and `recordings/`: the live judge recorder and its inputs (`recordings/inputs/`, anonymized, one JSON per
  input). Run it with `python -m evals.redesign.record`; `--only <input id>` redoes one input.

Who uses it
- `ui/tests/support/real_hub_server.py` and the browser tests on a real hub (`ui/tests/support/real-hub.cts`).
- `backend/tests/test_automation_mail_actions.py` (the Acme org and the mail seeds).
- `evals/screening`, which runs on the Acme org and reuses the judge replay and webhook helper.

Judge and recordings
- `ReplayJudge` answers a passed script first, then the recording at `recordings/<key>.json`, then raises
  `MissingRecording`. The key is a hash of (state, questions, level).
- A missing recording fails loudly with the command to re-record it: `python -m evals.redesign.record`.
- The recorder sends each input in `recordings/inputs/` once through the configured judge, using `judge.from_env()`:
  `HUB_API_URL` and `HUB_TOKEN` together send calls through the hub, whose key stays on the server; `TYPESAFE_API_KEY`
  sends them direct. With neither set it refuses to run.
- Every input is level `low`: the live engine takes no level, so medium and high are untested.
