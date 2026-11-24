# Antigravity harness

Choose `runtime: antigravity` for a bot (its harness is `antigravity` too) and select its model, Gemini 4 Neon or
Gemini 4 Hydrogen, and effort in Settings. Google serves both Antigravity and the Gemini CLI (`runtime: gemini`), so a
Gemini CLI bot keeps its own runtime and models. See [Harnesses](harnesses.md) for general provider setup.

The runner installs `agy` itself on a computer whose bots use Antigravity, or where the owner turns it on: Google
publishes it only through its own updater, so `runner-rs/harnesses/antigravity-install-v1.sh` (fetched from this
repository at the checksum `antigravity.toml` pins) reads the updater's manifest for the platform (Linux x64 and arm64,
musl included, and Mac arm64 and x64), checks the download's address and sha512, and installs its one executable as
`agy`. An `agy` already on PATH is used as it is. Tico does not change its global settings, and runs it with
`AGY_CLI_DISABLE_AUTO_UPDATE=1`. A Gemini API key alone does not select Antigravity's API-key provider; follow
Google's CLI settings documentation if switching that authentication mode. Readiness finds `agy` and checks sign-in
with `agy models` before the runner claims work.

A turn signs in with the computer's own Google account (on a Mac, from its keychain), or with an Antigravity plan:
Settings > Providers > Add > Antigravity plan. Connect runs `agy acp --login` on one of your computers, in a home of
its own and told it runs over SSH, so it prints Google's link, reads the code the page shows (or the page's whole
address) and writes its sign-in to a file there, never the keychain; it counts once `agy models` says it is signed in
there. Only a computer on a Tico release that reports `antigravity_plan_v1` signs a plan in, installs `agy` for one,
or runs its turns, and only with an `agy` at least as new as `antigravity.toml`'s minimum (an older one of the
person's own is named, with the release to update to). With no online computer that has `agy`, the owner's first
Connect turns Antigravity on for their computer (or the company's shared cloud computer) and the page waits while it
installs. A plan is never pasted, and the API takes its sign-in from Connect only. Each turn on a plan gets a
`GEMINI_HOME` of its own holding only that file (mode 600) and links to the bot's own `~/.gemini` rules, skills and
conversations, so a move between plans resumes the same conversation; its conversation summaries (a SQLite file, which
a link cannot share) do not carry across plans. That turn's `agy` alone is told it runs over SSH, and no Gemini or
Google key, nor the computer's Application Default Credentials (`AGY_ADC_AUTH`), reaches it; a token it refreshes
during the turn is masked before anything it says is kept; without the file in place it is not started. The home is
removed once `agy` has exited, and any left by a stopped runner when it starts again. The file holds the plan's
refresh token, so, as for a ChatGPT plan, a bot paid by the plan can read it while its turn runs (SECURITY.md).

Each turn starts its own `agy` process, which ends with the turn; no process is kept between turns. The runner saves
each thread's conversation ID locally (`state/agy/<bot>/`, readable by the runner only) and resumes it with
`--conversation`. Failed or interrupted runs are never replayed automatically. Changing the effort keeps the
conversation; starting a new chat starts a new one. A resumed turn's prompt waits until `agy` names the conversation
it resumed (it does so before it reads its input). A turn that must continue the saved conversation is never sent into
another one: it stops instead. On any other turn, when `agy` names a new conversation instead, the runner closes it
without sending the prompt and starts the turn once more in a new conversation, given the whole prompt and the
conversation's recent history; the saved conversation stays until the new one has worked. This happens only while
the prompt is still held: once it has gone, the turn is never started again. If `agy` moves to a new conversation only after the model has worked, the runner keeps
that one, marks it in the saved record, and tells the next turn the conversation's history again; nothing runs twice.
When `agy` exits, whatever of its process group is left (a tool it started) is stopped too.

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
