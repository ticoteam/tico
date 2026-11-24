# Antigravity harness

Choose `runtime: antigravity` for a bot (its harness is `antigravity` too) and select its model, Gemini 4 Neon or
Gemini 4 Hydrogen, and effort in Settings. Google serves both Antigravity and the Gemini CLI (`runtime: gemini`), so a
Gemini CLI bot keeps its own runtime and models. See [Harnesses](harnesses.md) for general provider setup.

The runner installs `agy` itself on a computer whose bots use Antigravity, or where the owner turns it on: Google
publishes it only through its own updater, so `runner-rs/harnesses/antigravity-install-v1.sh` (fetched from this
repository at the checksum `antigravity.toml` pins) reads the updater's manifest for the platform (Linux x64 and arm64,
musl included, and Mac arm64 and x64), checks the download's address and sha512, and installs its one executable as
`agy`. An `agy` already on PATH is used as it is. Tico does not change its global settings, and runs it with
`AGY_CLI_DISABLE_AUTO_UPDATE=1`. Readiness finds `agy` and checks the
computer's own sign-in with `agy models` before the runner claims work.

Tico runs `agy` the way T3 Code does: `agy acp --gemini-dir <home> --app-data-dir antigravity-cli`, speaking ACP
(JSON-RPC over its input and output). `<home>` is the bot's own `~/.gemini` on the computer's own sign-in, and a plan
turn's own home on a plan; only `agy acp` takes its home from that flag (headless `agy -p` and `agy models` read
`$HOME/.gemini` whatever `GEMINI_HOME` says). It runs with `GEMINI_HOME` set to that home, no `BROWSER`, unbuffered
output, a `TMPDIR` of its own (removed once it has exited) and, on Linux, a CA bundle when none is set. A new process says
`initialize`, `authenticate` (a plan turn with the Google account's method; any other with the computer's own: a
`GEMINI_API_KEY` signs in with the Gemini API key, a `GOOGLE_API_KEY` or Application Default Credentials (`AGY_ADC_AUTH`,
`GOOGLE_APPLICATION_CREDENTIALS`) with Google Cloud, else the Google account), resumes the thread's conversation (`session/resume`)
or begins one (`session/new`, given the bot's MCP servers of a transport `agy` offers, none so far), sets the model
and thinking level as the session's config options when the session offers them (else `agy`'s own stay), and sends `session/prompt`; tool permissions are given as asked.
A sign-in `agy` has not got (`Authentication required`, or "No browser is available here") fails the turn as a
sign-in, and `QUOTA_EXHAUSTED` (or a typed `refusal`) as a usage limit. `agy` streams a failed model call as chat
text and ends the turn as answered, so, as T3 Code does, the turn reads the steps its prompt added to the
conversation's session log (`antigravity-cli/conversations/<id>.db`, read only): an error step fails it. Usage counts
cache reads as input. What `agy` says refusing a request is passed on scrubbed and cut short, never its raw data.

A turn signs in with the computer's own Google account (on a Mac, from its keychain), or with an Antigravity plan:
Settings > Providers > Add > Antigravity plan. Connect runs `agy acp --login` on one of your computers, in a home of
its own and told it runs over SSH, so it prints Google's link, reads the code the page shows (or the page's whole
address) and writes its sign-in to a file there, never the keychain; it counts once a short `agy acp` there
(`initialize`, `authenticate`, `session/new`) starts a session that offers a model. A failed Connect says why: Antigravity
did not start within 90 seconds, or showed no Google link 30 seconds after it did (broken IPv6 is a common cause), the
link expired, Google's page was not approved, the account has no eligible Antigravity subscription, `agy` exited with
its code, or it signed in but could not start a session or load models; the scrubbed last lines it printed go to the
runner's log. Only a computer on a Tico release that reports `antigravity_acp_v1` signs a plan in, installs `agy` for one,
or runs its turns, and only with an `agy` at least as new as `antigravity.toml`'s minimum (an older one of the
person's own is named, with the release to update to). With no online computer that has `agy`, the owner's first
Connect turns Antigravity on for their computer (or the company's shared cloud computer) and the page waits while it
installs. A plan is never pasted, and the API takes its sign-in from Connect only. Each turn on a plan gets a
`GEMINI_HOME` of its own holding only that file (mode 600) and links to the bot's own `~/.gemini` rules, skills and
conversations, so a move between plans resumes the same conversation; its conversation summaries (a SQLite file, which
a link cannot share) do not carry across plans. That turn's `agy` alone is told it runs over SSH, and no Gemini or
Google key, nor the computer's Application Default Credentials (`AGY_ADC_AUTH`), reaches it; a token it refreshes
during the turn, or while it is kept after it, is masked before anything it says is kept; without the file in place
it is not started. The home is removed once `agy` has exited (a kept one, below, when it is stopped), and any left by a
stopped runner when it starts again. The file holds the plan's refresh token, so, as for a ChatGPT plan, a bot paid by
the plan can read it while its turn runs, and while its `agy` is kept after it: at most 2 minutes unused, which is
also how long a removed or revoked plan's sign-in can stay on the computer (SECURITY.md). No new turn reaches it.

The `agy` process stays up after a turn that completed or was stopped, as T3 Code keeps it: one per
thread, holding its conversation open, in the runner's session keeper (`keeper.rs`, modelled on T3 Code's
`ProviderSessionManager`; `agy` is its first harness, Claude and Codex come next; as many as the runner's capacity, at
least 8). The hub still claims, starts and completes each message; only the process is reused, and the turn's timings
say `warm: true`. The thread's next turn goes straight to `session/prompt` there, setting the model and thinking level
on the live session first when they changed. The runner releases a kept process after 2 minutes unused (T3's idle
timeout; a session reporting background work still running is kept up to 4 hours, checked every 2 minutes, though
`agy` reports none), when the keeper is full (oldest first), when it stops, and when a turn would start `agy`
differently (another home, environment, folder or MCP servers, or another plan or a sign-in the plan was handed again:
a turn no longer handed the plan never goes on in its process): that turn starts a new one. The turn's own token and
its turn-limit scope (`TICO_TURN_SCOPE`) are not compared: on a computer with turn limits the process stays in the
scope of the attempt that started it, and a memory kill there fails the attempt running on it then. While kept, its
`HUB_TOKEN` file is blank (a `tico-file:` reference, rewritten with each turn's token), what it asks is refused and what
it says is dropped. A plan turn's `agy` keeps its own home and sign-in while it is kept; the next turn on the plan,
though handed a home of its own, goes on there. Stopping a turn sends
`session/cancel`: when `agy` ends the prompt as cancelled within 15 seconds, its process stays; otherwise it is stopped.
The runner keeps each thread's conversation ID locally (`state/agy/<bot>/`, readable by
the runner only) and a new process resumes it with `session/resume`, so a kept process that exited is replaced by one
that resumes the conversation, and a prompt is never sent twice. Failed or interrupted runs are never replayed
automatically. Changing the effort keeps the
conversation; starting a new chat starts a new one. The prompt goes only once `agy` has resumed the saved
conversation or begun the turn's own. A turn that must continue the saved conversation is never sent into another
one: it stops instead. On any other turn, when `agy` cannot resume it, the same process begins a new conversation,
given the whole prompt and the conversation's recent history when there is one; the saved conversation stays until
the new one has worked, and the next turn is told the history again unless this one was and succeeded. Nothing runs
twice. When `agy` exits, whatever of its process group is left (a tool it started) is stopped too; a tool it left
running when its turn ended keeps running while the process is kept.

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
