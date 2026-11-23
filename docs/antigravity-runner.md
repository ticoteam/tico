# Antigravity harness

Choose `runtime: antigravity` for a bot (its harness is `antigravity` too) and select its model, Gemini 4 Neon or
Gemini 4 Hydrogen, and effort in Settings. Google serves both Antigravity and the Gemini CLI (`runtime: gemini`), so a
Gemini CLI bot keeps its own runtime and models. See [Harnesses](harnesses.md) for general provider setup.

The runner never installs or updates `agy`: Google ships it only through its own updater. Install it on the assigned
computer by hand from Google's [CLI page](https://www.antigravity.google/docs/cli/), and sign it in there with the
computer's Google account. Until `agy` is on the runner's PATH, Settings says Antigravity is not installed on that
computer. Tico does not change its global settings, and runs it with `AGY_CLI_DISABLE_AUTO_UPDATE=1`. A Gemini API key
alone does not select Antigravity's API-key provider; follow Google's CLI settings documentation if switching that
authentication mode. Readiness finds `agy` and checks sign-in with `agy models` before the runner claims work.

Each turn starts its own `agy` process, which ends with the turn; no process is kept between turns. The runner saves
each thread's conversation ID locally (`state/agy/<bot>/`, readable by the runner only) and resumes it with
`--conversation`. Failed or interrupted runs are never replayed automatically. Changing the effort keeps the
conversation; starting a new chat starts a new one. When `agy` runs a turn in a new conversation instead of the saved one, the runner keeps the new one
and tells the next turn the conversation's history again; nothing is run twice. A turn that must continue the old
conversation stops instead. When `agy` exits, whatever of its process group is left (a tool it started) is stopped too.

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
External service credentials granted to a bot reach only that turn's process.

Runner readiness probes run separately from the 250 ms claim loop. Tico flushes streaming output every 150 ms.

Run the backend and Chromium/WebKit streaming checks before deploying
merged main changes. A runner upgrade must wait for active work to finish before
restarting its launchd service; the server's deployment drain is separate.

Google documentation: [headless CLI](https://www.antigravity.google/docs/cli/headless/),
[authentication settings](https://www.antigravity.google/docs/cli/settings/).
