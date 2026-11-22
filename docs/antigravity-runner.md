# Antigravity harness

Choose `runtime: gemini` and `harness: antigravity` for a bot on its assigned computer. Select its model and effort in Settings;
other Gemini bots keep their configured harness. See [Harnesses](harnesses.md) for general provider setup.

Install and authenticate Google's `agy` CLI on the assigned computer.
The current computer uses Antigravity's existing Google account sign-in. Its
global settings are not changed by Tico. A Gemini API key alone does not select
Antigravity's API-key provider; follow Google's CLI settings documentation if
switching that authentication mode. Readiness finds `agy` and checks sign-in
with `agy models` before the runner claims work.

The runner keeps a separate NDJSON process per bot, human, conversation, session
epoch, model, and effort. Idle processes expire after 15 minutes; the cache is
bounded to eight idle sessions. Provider conversation IDs are saved locally so
an evicted session can resume. Failed or interrupted runs are never replayed
automatically. Changing the model or starting a new chat creates a separate session.

Only the initial run gets the recent conversation bootstrap (up to 30 messages).
Later runs send messages after the last successfully delivered human request,
including relevant intervening bot messages, without repeating the current
request. Bot instructions and current execution restrictions still accompany
each run.

The child process never receives the computer registration credential. Its
`HUB_TOKEN` is a `tico-file:` reference to a mode-600 local lease file. The Tico
Python client reads that file on every request; it is rotated to the current
run credential and cleared when the run ends. Bots must use the Tico client
or `clients/hubcli.py`, rather than treating `$HUB_TOKEN` as a literal bearer
token. The server continues to enforce the current run's permissions.
Changed environment credentials discard the warm process. External service
credentials granted to a bot remain in that process until it expires or stops.

Runner readiness probes run separately from the 250 ms claim loop. Tico flushes streaming output every 150 ms.

Run the backend and Chromium/WebKit streaming checks before deploying
merged main changes. A runner upgrade must wait for active work to finish before
restarting its launchd service; the server's deployment drain is separate.

Google documentation: [headless CLI](https://www.antigravity.google/docs/cli/headless/),
[authentication settings](https://www.antigravity.google/docs/cli/settings/).
