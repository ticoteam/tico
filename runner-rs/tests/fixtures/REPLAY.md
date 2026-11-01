# Replay fixtures

`<harness>/replay/<name>/` holds one provider session recorded at the process boundary, as the runner's host saw
it. `cargo test` in `runner-rs` plays each one back (`src/hosts/replay.rs`): a fake CLI writes what the provider
wrote, and the test checks both the host's events and every frame the host wrote back. The older
`<harness>/*.jsonl` fixtures check events only.

Every runner bug fix in a host adds the session that showed it here.

## Files

- `input.json`: what the runner was asked to do.
  - `harness`, `cli` (the CLI's `--version`, which the fake repeats), `about`.
  - `source`: `real` (true for a recording of the installed CLI), `recorded` (date), `how`.
  - `turns`: each `{"prompt", "actions"}`. An action is `{"steer": "<text>"}` or `{"interrupt": true}` with
    `after_events`, the number of events of that turn the host had made when it was fired.
- `transcript.ndjson`: what crossed the process boundary, in order, one entry per line.
  - `{"dir": "spawn"}`: a process started (Claude runs one per turn).
  - `{"dir": "out", "frame": {...}}`: a line the runner wrote to stdin.
  - `{"dir": "in", "frame": {...}}`, or `{"dir": "in", "line": "..."}` for a line that is not JSON: the CLI's stdout.
  - `{"dir": "close"}`: the runner closed stdin.
  - `{"dir": "signal", "signal": "TERM"}`: the runner sent SIGTERM.
  - `{"dir": "exit", "code": N, "stderr": "..."}`: the process ended (a negative code is the signal).
- `output.json`: `{"turns": [[events of turn 1], ...], "after": [events once the host stopped]}`. Each action's
  result sits among its turn's events as `{"act": "steer" | "interrupt", "ok": bool}`. Claude events carry no
  `thread_id` or `turn_id` (the runner's own uuids); a Codex id the runner made up reads `<runner>`.

An `out` frame is compared whole, except what the runner chooses afresh each run: a Claude message's `uuid`, and the
`cwd` and `config` of Codex's `thread/start` and `thread/resume`.

## Recording

    cd runner-rs
    TICO_REPLAY_RECORD=claude/steer-mid-turn,codex/limit-notice cargo test record_replays -- --ignored --nocapture

(`all` records every scenario.) Each scenario in `record::plans()` runs on the installed CLI, signed in as this
computer's operator in a scratch home, through `replay_proxy.py`. The scratch folder and the copy of the login in it
are readable by the operator only; a recording that is killed rather than failing leaves the folder in the temp
directory, so delete it. The recorder masks the login's tokens, home and scratch paths, the host name, and email
addresses. Read the files before committing anything else that could be private: a prompt
in a recorded session is kept as written.

The recorder copies Claude's login from `~/.claude/.credentials.json` (on a Mac it is in the Keychain, so record on
Linux) and Codex's API-key login from `~/.codex/auth.json`; a Codex ChatGPT login is not masked or broken by it. There
is no Codex compaction plan: the host ends a control call's turn from the caller's task, after notifications read in
the same burst, so its order would not replay.

A capture of a run on a real install (the runner's raw-stream option) goes in the same three files.
