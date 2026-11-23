# Computers

A computer runs your bots. **Settings → Computers** lists each enrolled computer's name, kind, state, runner version
and agent count. To add a computer, click **Add computer** or see
[Install](install.md#add-computers-to-run-your-bots).

Re-enrolling the same physical computer can leave an older registration behind. The hourly cleanup removes that
registration after more than 24 hours offline only when a newer, active registration has the same reported device
identity, owner, name and platform, and was enrolled or contacted Tico after the old registration's last contact.
The old registration must have no assigned bots, cloud machine, shared browser access, saved or reported sign-ins,
keys, subscriptions or profiles. Computers with an unknown device identity stay listed. The runner
reports an app-salted hash of the OS device identity; the raw identity stays on the computer.

**Settings → Providers** lists Claude and ChatGPT plans and API keys by owner. **Tico Gateway** holds the
[Gateway](ai-gateway.md) key and a link to its limits. **Settings → Agents → Defaults** holds the model for new agents
and the meetings model. Open **Usage → Limits** for the [company turn levels](#change-the-company-turn-levels).

Everyone can see the pools and usage badges. **Picked by** counts the agents the viewer can read that select
the provider or its pool. Providers opens **Your pool**
first and folds other people's pools by default. Older plans appear under **No owner** and shared keys under
**Company keys**. Plan owners and admins see the plan's account preview and detailed refusal messages. Only admins
see API key previews in the list. A provider capacity hold shows **Busy** until it ends. **Next** marks an eligible
spare for an agent currently using another plan from the pool. Old links to Models or Subscriptions open Providers.

## Subscriptions

Open **Settings → Providers → Add** to connect a plan or paste a key. Each person can save plans and API keys
in their own pool. Owners and admins can add for a teammate. The provider's owner or an admin can reconnect,
rename, replace or remove it.

**Paid by** on an agent's settings picks a person's pool, one plan or one key for each AI tool. The **Paid by**
column in **Settings → Agents** shows the choice for the agent's current tool. **Last turn** shows the recorded payer
or the current payer wait with its reason, known reset time and who can fix it. New agents
start on their human owner's pool for each tool where the owner has a connected plan. The pool's owner or an admin
can assign it or one of its providers to an agent they manage. Run `tico subscription show <agent>` to read the choice
and `tico subscription set <agent> <tool> <pool|plan|key|unset> [person or provider id]` to change it.

On a pool, the next turn uses the compatible plan with the soonest reset and more than 0.5% left. Plans with
no usage reading rank after measured plans. A plan whose credential cannot be opened is skipped.
When no provider in the choice can pay, the turn waits and Health shows the reason and next known reset.
With **Paid by** unset, the computer's sign-in and Gateway still work where configured. Fallbacks can only
hop to tools with **Paid by** unset. A saved fallback session waits if its tool gains a **Paid by** choice.
A computer that needs a runner update can use an explicitly configured
fallback on an unset tool. It skips automatic fallbacks for agents with a **Paid by** choice.

Changing **Paid by** takes effect at the next turn. Current runners install the turn's own credential and stop if
installation fails. A placement or move by an agent manager clears selections owned by someone else
unless that manager is an admin. The automatic placement sweep keeps existing choices and clears a foreign pool choice
if the destination belongs to someone other than the agent's owner or the pool's owner. Removing a provider
clears exact picks and any pool picks it empties for that tool, and restarts affected turns.
When someone leaves, Tico removes their providers, cancels their pending
Connect attempts and shows an admin the agents whose selections became unset.

Tico model turns on the current Docker runner each use a separate temporary OS user. Other bots on the computer
cannot read the turn's live environment, open file descriptors or private temporary files, or use its Gateway
connection. Workspace files stay shared through the bot group. This does not separate subscription sign-ins or
turns on other providers. See [the Gateway boundary](ai-gateway.md#pick-a-tico-model-for-a-bot) and
[Docker runner setup](install.md#linux-or-cloud-server-docker).

## Turn counts

**Settings → Health** reports four turn counts:

| Count | What it means |
|---|---|
| **Running** | The turn has started. |
| **Starting** | The computer has reserved a place and is preparing the turn. |
| **Waiting** | The turn needs a login, quota, available memory, a free lane or another pre-start requirement. Each reason shows its count and oldest wait. |
| **Queued** | The work is in line to be picked up. |

For example, **3 running · 2 starting · 7 waiting for quota (oldest 2 h) · 5 queued** separates work from its waits.
The counts describe the computer's total load, including work belonging to other people. Wait reasons never name
bots, tasks, people or accounts. The bot list still follows each viewer's access.

On an upgraded runner, a turn holds its place from its claim until it ends. A computer runs up to its turn limit, and
holds at most twice that in claimed work, waiting turns included; the rest stays queued on the server, unleased, until
a claim ends. A turn claimed past the limit holds no place: its start is refused as "computer busy" until one frees. A
login, quota or other pre-start wait is parked: the turn keeps its claim but takes no start place, so
other work can start beside it. Waiting for
a lane or login releases only the start reservation; continuing requires a fresh atomic reservation. Two turns cannot
take the last start reservation together. Codex's shared ChatGPT login does not queue turns
([sharing a sign-in](#bots-sharing-one-chatgpt-sign-in-run-side-by-side)).

### Builds at once

A turn on a task that has a worktree on its computer is a build: its checkout and setup use a lot of disk. A computer
runs at most one build per 15 GB of disk it reports, at least two and never more than its turn limit (51 GB gives 3,
157 GB gives 10; 4 when it has not reported its disk). A build past that waits as **waiting: builds at once**, and
starts by itself when one of the computer's builds ends. Turns with no repo work are not held by this, and nothing is
set in the app. Worktrees are described in [Task worktrees](worktrees.md).

A starting turn reserves a place for up to **3 minutes (180 seconds)**, including active Git and runtime preparation.
Keeping that preparation active does not extend the deadline. If the turn has not started, its place is released and
its work returns to the queue without changing the task. After 10 missed starts for one job within the last 24 hours,
including starts missed before a server restart, Tico reports a pre-start failure instead of retrying forever.
Leases cut by a hub restart or update and planned pauses do not count as missed starts. Check the computer named
in the failure.

CPU or memory strictly above **90% for 5 minutes** holds new starts on that computer with **waiting: computer busy**.
Memory at **85% or more** holds them too, with **waiting: computer busy: memory N%**; they start again once memory is
below **80%**, so the queue does not flap. A computer whose last sample is more than **2 minutes** old holds new starts
with **waiting: computer not reporting**, because a computer that stops reporting may be frozen. A computer that has
never reported is not held. The server also ends a run that has gone silent (no heartbeat or event for 5 minutes)
once it is past its bot's max_run_minutes plus 5 minutes, without waiting for the computer.
This includes turns with a reservation that have not started yet. Running turns continue. New starts resume when
the hold clears. A person's own message to a bot passes CPU overload and stale reports, ahead of the waiting turns, when
the computer has a free turn. That covers a chat, a message in its task, a reply to a question the bot asked, and a
person's message in a thread that wakes the bot, such as an @mention. Tico's routine notices, sweeps and rule wake-ups
wait, and so do messages between bots. On Linux computers (including cloud machines and Docker), memory pressure also
holds a person's messages. On Macs and computers whose platform is unknown, a person's messages pass the server's memory
hold too.

The Linux runner also checks current memory before starting a harness or preparing its thread, including when a
fallback is the first harness for the turn. Its local hold uses the same 85% and 80% thresholds, even with diagnostics
off. While waiting it gives its start place back and keeps its delivery lease. The run, Computers and Health say
**waiting: computer busy: memory N%**. Memory admission ends only when a harness process actually launches or the turn
acquires a live pooled process. Preparing a lazy host or its thread (such as Claude before its first send) does not
cross that boundary: waits for sign-in renewal still check current memory before launching. The runner reports an
allocated process on start-place and started requests, so neither the runner nor the hub parks that turn on its own
allocation’s memory use. The boundary stays crossed through fallback. If a lazy harness acknowledges its start but
fails to launch, its fallback checks memory locally until it allocates a process, keeping the running turn's place.
It does not request pre-start admission again. Runners advertise allocation reporting through the
`harness_allocation_v1` heartbeat capability. Every runner the server accepts advertises it (the minimum release is 0.6.0-rc.1). A runner talking to an older hub drops the new field or capability if that hub refuses it.
The Mac runner never parks a turn on local memory readings;
macOS's Memory Used includes memory it can reclaim through compression and swap. This does not cap the memory of
tools already running inside a turn, or change your account, model or computer size.

During an update, server and runner negotiate the start rules through heartbeat and claim. Older runners retain
their existing delivery leases, including preparation already underway, until they update. Those starts have no new
three-minute deadline. Legacy waiting turns retain their place too, because those runners cannot obtain a fresh
reservation before starting. Update the runner to release those places safely and enable the new capacity rules
for its turns. Upgraded runners opt in after a successful heartbeat and keep the older rules against an older server.

### The turn fixer

A [Heartbeat](heartbeat.md) step, **Turn fixer**, checks every computer on every beat (every 10 seconds by default). It is plain code: no AI and no
cost. It frees only turns that are proven dead: a start a person stopped, or a turn whose lease is lost and whose
computer no longer runs its bot (the computer was removed, or its latest claim does not list the bot). A slow reply or
a healthy wait keeps its place. The task and its history stay, and the task gets a note: **Tico freed a dead start,
will retry.** A dead turn that may have pushed, sent or paid is not retried blindly: one that only read runs again,
and any other is held for Agent Ops with the tools it used.

When the company reaches its warning level, **Settings → General → Health → Details** shows one **Company turns** finding for the
incident: the counts by wait reason, the oldest waits, the last progress of each running turn, each computer's CPU
and memory, and the likely bottleneck in one sentence. Later runs update the same finding, and when the count drops
below the warning level it says when the incident resolved. If the incident is still open after 5 minutes, Agent Ops
gets exactly one task with the finding (the Support bot where there is no Agent Ops). The owner gets one notice, only when
the incident is still open after an hour or its main wait needs a person: a login, quota or money.

## Company turn levels

Company turn levels apply across **all computers**, separately from each computer's capacity and CPU or memory brake.
The company count includes running and starting turns, plus waiting turns already admitted to run. It is not the sum of every displayed **Waiting** count.
Queued work blocked on quota, spending, computer readiness or a paused bot does not count. Neither does work held by the company stop itself.

Each level is multiplied by the number of people who can sign in to Tico, with a minimum of one person.
Guests, hidden or departed people, and people whose sign-in is disabled do not count.
This counts people with sign-in access, not the number currently online or the AI tools' logins.

| Level | Default per person | What happens at or above it |
|---|---|---|
| **Warning** | **40** | New turns can still start. The Health page (**Settings → General → Health → Details**) records the crossing as an incident under **Company turns**. Tico sends no “no more slots” alert to a bot. |
| **Stop** | **100** | New turns wait with “Your company is at its limit; Tico is fixing what's stuck.” Running turns continue. |

For example, four people who can sign in, plus one guest, give a warning at **160** and a stop at **400**.
One person, or no people with sign-in access, gives **40** and **100**.

Just below the stop, while the count is still at or above the warning, work that waited through the stop keeps first claim on each freed place, in order across computers. Below the warning that list is cleared and each computer starts from its own queue again. Work still waiting for a login, quota or computer remains held for that reason.
Below the warning level, Health marks the incident resolved and records the time. Crossing the warning again opens a new incident.

These settings provide the warning record and start limit. See [The turn fixer](#the-turn-fixer) for automatic recovery.
The company-limit message does not confirm that an automatic repair has run.

### Change the company turn levels

You must be the owner or an admin. The server rejects changes from anyone else.

![Usage Limits card with Warn at 40 and Stop at 100 runs per person, 3 people · 18 of 300 in use, and Save](images/settings-models-limits-desktop-light.png)

Example owner/admin view of **Usage → Limits**, using synthetic data.

1. Open **Usage → Limits**.
2. Find **Limits**.
3. Enter **Warn at** (runs per person).
4. Enter **Stop at** (runs per person).
5. Click **Save**.

Both values must be whole numbers from **1** to **10,000**, and warning must not exceed stop.
For example, enter **45** and **110** for four people. After saving, the card shows **Warn at 180 · stop at 440 company runs**.
Before a save, it shows the people count and runs in use against the company stop level.

Saved values apply to the current company count. Lowering stop to or below that count holds new turns; running turns continue.
Raising stop above the count lets eligible waiting work resume. Raising warning above the count resolves an active warning incident.
To restore the defaults, enter **40** and **100** and click **Save** again.

If saving fails, read the error beside the button and correct the values before trying again.
If the card is missing, ask an owner or admin to change the levels.
Check **Settings → Health** for the company count and individual wait reasons.
Changing the levels does not add computer capacity or clear a login, quota or overload wait.

## Connections

An agent with **Paid by** unset uses the computer's login for the tool, or the Gateway where configured.
Each computer has one local login per AI tool. Open an agent's **Computer** tab to sign in or use **Switch account**.
A local login can use a Claude or ChatGPT account or a key configured on the computer. Plans and keys saved in
**Settings → Providers** are kept sealed in Tico's credential vault and are selected per agent under **Paid by**.

Named subscriptions that an earlier release signed in on a computer are no longer used or shown. Bots that ran on one
now run on the computer's login for that tool, which must be signed in for them to run
([Subscriptions from earlier releases](#subscriptions-from-earlier-releases)).

Before upgrading from separate bot subscriptions, check the computer's login for every tool its bots use.
When the runner identifies a primary connection's login as an API key, its connection shows a warning: API billing is separate from a subscription's
allowance, and Tico does not check the API credit balance. Paid API use remains available. If you intend to use
a subscription, choose **Switch to subscription** and sign in to that plan on the computer. **Health** also warns about
API-key computer logins used by bots, and flags a missing, failed or rejected login used by an active bot's
primary or fallback model, even before it has queued work. Computers that are offline keep their last report;
the login check resumes when they report again.
An unknown account type gives no API-key warning; a retained named login may need its next account report first.
When a tool cannot write its data folder, Health names the reported folder problem for the computer operator.

### Which connection a bot runs on

An agent uses its **Paid by** choice for the tool running the turn. An older runner can take the turn when that payer
matches the computer's current provider for the tool. For a pool, the older runner keeps its current plan while
that plan belongs to the pool and can pay. A legacy computer can refresh its last delivered Claude plan while
it hosts an agent still authorized for the plan. A turn with a different payer waits for the computer to update.

### Company subscriptions

A **subscription** is one of the company's model logins or keys, saved once in Tico rather than on a computer: a
Claude setup-token (from `claude setup-token`), a ChatGPT login (the `auth.json` from a computer where `codex login` is
signed in, usually `~/.codex/auth.json`), or an Anthropic or OpenAI API key.

Adding a plan signs it in without pasting. Tico picks an online computer on the latest release with that tool
installed, the person's own first, and runs the sign-in there: `claude auth login --claudeai` (a **full Claude login**,
below) on a Linux computer that runs Tico's latest runner, else `claude setup-token`, or `codex login` in a folder of its
own. That computer's own login stays as it was. The page shows the link and any code, never the result, which the
computer sends straight to Tico and keeps no copy of. If it has to run on someone else's computer, the page says so before
the sign-in, since the result passes through that computer. **Paste instead** ends the sign-in before showing the paste form.
With no such computer online, a company with Tico Cloud computers has its shared one started (made first, if it never was) and kept on until
the sign-in ends; if none is up within five minutes, or the company has no cloud computers, the sheet says **No
computer online** and offers **Add a computer** and **Paste instead**. A sign-in the command reports as failed saves nothing. If the sign-in cannot
accept the code and asks to retry, Tico ends that attempt and offers **Try again**. Open the new link and use the code
from that page; a code from the previous attempt does not carry over.
The computer's runner log records the sign-in ID, stages, elapsed time, command exit code and a safe failure category
(code rejected, OAuth network error, or another OAuth error). It never records the authorization link, code, token or
raw command output. A generic code rejection cannot say whether that code expired, was reused or was incomplete.
**Reconnect** signs a saved plan in again and asks first if it would restart running turns. The plan's
usage is visible to everyone. Its account email is visible to its owner and admins. Provider usage counts
include only the agents the viewer can read.

A capable runner receives the turn's credential at claim. A Claude token or API key is set in the turn's
environment. A ChatGPT login or OpenAI key goes in a Codex home with a turn-specific `auth.json`. The
runner removes the file after the turn and sends refreshed ChatGPT login credentials back to Tico.

One computer is one trust group ([SECURITY.md](../SECURITY.md#bots-on-one-computer-share-a-trust-boundary-on-purpose)):
while a turn runs on a subscription, another bot's turn on the same computer could read it.

#### Bots' models follow the plans

When a person's Claude or ChatGPT plans change, their template agents with **Paid by** unset on the current tool
follow the model and task model for that role and the owner's connected plans. Tico applies the same rule at
startup. An agent with no connected plans keeps its current models.

| Role | | Claude only | ChatGPT only | Both |
| --- | --- | --- | --- | --- |
| Builder, Reviewer 1, every Owner, Feature Owner and manager, every other role | bot | Haiku 5.5, extra high | GPT-6.1 Terra, medium | Haiku 5.5, extra high |
| | tasks | Opus 5.5, medium | GPT-6.2 Sol, high | Opus 5.5, medium |
| The Assistant (it chats; no task model) | bot | Opus 5.5, medium | GPT-6.2 Sol, high | Opus 5.5, medium |
| Reviewer 2 (no task model) | bot | Haiku 5.5, extra high | GPT-6.1 Terra, medium | GPT-6.2 Sol, high |
| Librarian, Rules, Updates, Automations and Finance Owners (no task model) | bot | Haiku 5.5, extra high | GPT-6.1 Terra, medium | GPT-6.1 Terra, medium |

A model the catalog does not offer yet is replaced by the set's first offered model on the same tool. A bot whose model a
person changed (Settings, the model picker, a manager bot or the Assistant) keeps it, and gets no task model from the
plans; undoing that change gives it back to the plans. A task model a person set stays the same way. A task model keeps
its task context length when the plans move it. Each move is a `bot.model_changed` or `bot.task_model_changed` event
with `why: plans`. Decision models (pipeline judges, Decide) stay on
the Gateway. On Tico Cloud, setup's **Done** waits for a plan or key.

### Bots sharing one ChatGPT sign-in run side by side

Codex bots on one Computer using the same ChatGPT sign-in run turns at the same time, just as Claude bots do. This applies to every bot on the Computer's login and to separate tasks of the same bot. They share the plan's usage allowance; that allowance is the limit, and a busy login does not make another turn wait. When the allowance runs out, the usual usage-limit and fallback rules apply ([how it works](how-it-works.md#one-turn-per-chatgpt-login)). Bots on an OpenAI API key also run side by side. Tico uses the original login in its own home and never copies a login file into another bot's home: copies can rotate apart during token refresh.

### What "% left" means

Usage always reads as the share of the plan's weekly allowance that is **left**, never used. It is the provider's own
reading for that account, so **use outside Tico counts too**: someone chatting in ChatGPT or Claude on the same account
lowers it. Tico never estimates it from its own token counts. A reading has a reset time and an age ("as of 2 min ago");
one older than a day is marked as possibly out of date, and after its reset it reads as the previous week, never as a
fresh 100%. An API key has no plan, so it shows "No plan usage (API key)". Details are in
[Signed-in account and usage windows](#signed-in-account-and-usage-windows).

An admin can read a Claude plan's week now: **Check use** in its **⋯** menu on Providers asks one
computer that has the plan. That computer sends a one-line prompt to Haiku with the plan's token, in a temporary home it
deletes after, and sends back the week's percent and reset from that reply. A setup-token can only run prompts, so
Claude Code's `/usage` cannot read it; the prompt uses a few tokens of the plan. The token is used for that one read and
is not kept there. One read runs at a time, and a plan can be read again a minute after the last. ChatGPT plans have no
such button.

#### Full Claude logins

A Claude plan connected with `claude auth login --claudeai` is a full login, as Orca and t3code use. There is one plan
per account in the company. Connecting the same account again reconnects that plan.
A setup-token plan reconnected this way becomes a full login and keeps its owner, computers and history; pasting a
setup-token into a full login's Reconnect turns it back. It is offered only from a Linux computer, since a Mac keeps the
login in its Keychain, and runs only on computers whose runner says `claude_login_v1`.

- **Turns** get only the login's short-lived access token, in a `.credentials.json` of their own (the process's
  `CLAUDE_CONFIG_DIR`, with the rest of the computer's `~/.claude` linked in); never the refresh token.
- **Refresh**: an access token lives about eight hours. When less than an hour is left, Tico leases the refresh to one
  computer at a time. That computer refreshes a copy of the login, rewrites its own turns' file first, and keeps the new
  login on disk until Tico has it; Tico then sends the new token to every other computer with turns on that plan. A
  lease whose computer went quiet is never handed to another computer while the old token still works. A turn that the
  rotation caught between its calls ("OAuth token revoked") resumes its session instead of failing, and the plan is not
  marked for Reconnect.
- **Usage**: every 15 minutes for a plan in use or next in line (and on **↻**), one computer reads the plan's 5-hour
  window, its week and each model's week from Claude's usage endpoint. A refused read (429) waits as long as it is
  told; neither it nor an offline computer ever marks Reconnect. A plan at 95% of a window comes after the others in
  its owner's set until it is back under 90%; only a refusal holds it.
- **Reconnect**: the login itself lasts about a month and refreshes do not extend it. A few days before it ends the row
  shows **Reconnect by** and the date.

## The computers list

Each enrolled computer has one row, including computers with no agents. On a phone the row wraps onto two lines.

| Column | What it shows |
|---|---|
| **Computer** | Its name. |
| **Kind** | **Cloud shared**, **Cloud** or **Your own**. |
| **State** | **Online**, **Offline** or **Restarting**, as reported by Tico. |
| **Version** | The reported runner version. |
| **Agents** | How many assigned agents the viewer can see. |

Select a row to open the computer's panel with its details, agents and controls. Open an agent's **Computer** tab
to manage the computer's own sign-ins. **Hosted servers** and **When idle** appear below the list where available.

## A computer's pop-up

The panel shows the computer's **State**, **Kind**, **Platform** and runner **Version** where available. Cloud computers
also show **Size** and **Place** when reported. Select an agent under **Agents** to open its page.

When the computer supports screen sharing, its operator and admins can set **Screen** to **Off**, **View** or **Control**.
They can also **Restart** or **Remove** the computer. Owners and admins can **Rename** it and change **Fall back to Tico Gateway**.

### Who sees and changes what

Every member sees the enrolled computers, the Gateway setting and the agents they may see on each computer. On an
agent's **Computer** tab, the computer's operator and admins can manage its sign-ins. See [Permissions](permissions.md).

## Share internet with bots

A bot's browser can reach the web from another computer's internet connection: your laptop's home IP instead of a
cloud machine's. Sites that block data-center addresses then see an ordinary home connection. No proxy service is
involved.

1. The computer's operator turns on sharing and picks the bots with `tico computer browser-exit <computer> on --bots <bot-slugs>`.
   Use `--cap-gb <GB>` to set the daily cap (5 GB by default).
2. An owner, admin or bot manager sets the bot's exit with `tico bot browser-exit <bot> <computer>`. The computer must
   share with that bot.

The bot's browser then goes out through that computer for every site. Both runners connect out to Tico's relay, as for
the computer screen; neither needs an open port. The browser's encrypted traffic passes through unread: Tico sees the
site name and how many bytes, never the pages.

What each side guards against: the sharing computer protects its owner from any bot, so its rules below (home
network, router, its own addresses, ports, the cap, the owner's choice of bots) hold whatever the bot does. The bot's
side (its proxy, the browser wrapper, failing closed) keeps a bot from browsing directly by accident. It is no wall
against a bot set on going around it, which could only reach the web from its own computer's address anyway.

What the sharing computer allows:

- Ports 80 and 443 only.
- Public internet addresses only. The sharing computer looks the name up itself and refuses its own addresses
  (including its home's public address, as Tico's relay sees it), its home network (`10.*`, `172.16-31.*`,
  `192.168.*`), loopback, link-local, `100.64.0.0/10`, multicast, private IPv6 and the other special-purpose ranges
  IANA lists, and checks the address it actually connects to. It also refuses every device on the networks its
  interfaces are on (on IPv4 a `/16` or narrower; on IPv6 the `/64` of each of its addresses, where home devices
  have public addresses), the far end of a point-to-point link, and its router.
  It asks the relay for its public address over IPv4 and over IPv6 when it connects and every 5 minutes; while the
  IPv4 answer is missing, it refuses IPv4 sites, so an IPv6-only computer reaches no IPv4 site, NAT64 included.
- Up to the daily cap. Crossing it closes that day's connections through it until midnight UTC. Bytes count on the UTC
  day they pass, so a connection open across midnight counts on both days.

When the computer is offline, stops sharing with the bot, was removed or reached its cap, the bot's browser connection
fails, and the proxy's answer says why ("Ana's MacBook is offline"; also at the `TICO_BROWSER_PROXY` address in a
browser). It never falls back to going out directly. On a computer whose runner is too old for this, the bot's work waits
("Update Office server to use Browse through"). Changing a bot's
**Browse through**, or turning sharing off, closes the connections already open.

The computer's operator and admins can run `tico computer browser-exit <computer> --usage` to see the last seven days of
traffic by day, bot and site. The API keeps 30 days.

In turns of a bot with **Browse through** set, the runner sets `TICO_BROWSER_PROXY` (a proxy on `127.0.0.1` for that
bot), adds `--proxy-server` to `CHROMIUM_FLAGS`, and puts `google-chrome`, `chromium` and their usual names first on
`PATH` so a browser started by name goes through it. A browser started another way takes the proxy itself, for example
Playwright's `chromium.launch({proxy: {server: process.env.TICO_BROWSER_PROXY}})`. A browser started by name gets
the bot's own profile (`~/.tico/browser-exit/<bot>`; any other `--user-data-dir` is refused), and refuses to start
when a browser it didn't start through the proxy is open on that profile, or when a flag would skip the proxy (with
one dash or two, spaces around it, or a bare `--`). `localhost` stays local: the browser reaches the bot's own computer directly, never
through the exit.

The proxy asks for no password, because a browser can't send one for every connection. Under turn isolation (Linux
runners with `TICO_RUNNER_BOT_UID`), it takes connections only from the OS users that bot's turns run as. Without
isolation, as on a Mac or a cloud machine, bots on one computer share one OS user and are one trust group: any of them
can use another's proxy.

API: `GET/POST /api/v2/computers/{id}/browser-exit`, `GET /api/v2/computers/{id}/browser-exit/usage`,
`GET/POST /api/v2/bots/{bot}/browser-exit` ([OpenAPI](api.md#the-contract)). Hub: `tico computer browser-exit` and
`tico bot browser-exit`.

## Browser host

The browser host is the one computer that runs the company's website tools ([Website
logins](credential-vault.md#website-logins)): a Chrome profile per account, kept signed in, which bots drive by verbs
and people watch or take control of from the tool's page. An admin picks it with the **Browser host** switch in that
computer's pop-up; one per company.

- It needs Chrome: Google Chrome on a Mac; on Linux `google-chrome`, else `chromium`, on its PATH. The runner image
  (Docker, amd64 and arm64) ships Debian's Chromium, since Google publishes no Linux arm64 Chrome; elsewhere Tico does
  not install it. On Linux without a display it runs headless. In the runner container Chromium starts without its own
  sandbox, which needs a user namespace that Docker's default seccomp profile refuses; it runs there as the runner's
  user with no capabilities, and bot code, which runs as another user, cannot read its profiles.
- It runs no bots of its own: placing a bot there is refused while it is the browser host.
- It is online when it asked for website commands within the last 40 seconds; a running computer asks continuously.
  Health shows "Browser host offline" or "Chrome missing on <computer>" when it is not ready.
- Each account's browser starts on its first command, closes after 15 minutes idle (the sign-in is kept) and at most 8
  are open at once.

Where a website runs is chosen when it is opened:

1. On the browser host, when one is set, online and has Chrome.
2. Otherwise on the bot's own computer, if it has Chrome, unless the site is **central only**.
3. Otherwise it is refused, naming the browser host when it is the one that is offline.

Every later command stays on that computer. If it goes offline or is no longer allowed, the command is refused and the
bot opens the website again, which chooses afresh. A person's view follows the session to its computer and keeps its
page; a browser that restarted or left the site goes back to the site. With no session it opens on the browser host.

## Let a bot view the app (read only)

A bot can open the app signed in as itself and take screenshots, so a Product Owner shows what it checked in
production on its own page instead of asking a person to look. It is off for every bot until an owner or admin turns
on **Can view the app (read only)** for that bot: `tico bot app-view <bot> on` (`off` turns it off and ends any view
it has open).

The bot then runs `tico bot app-shot /#/task/<id> --width 390 --out shot.png` (390 for a phone, 1280 for a desktop)
on its computer, which needs Chrome. Each shot opens a view for at most 15 minutes, bound to the run that opened it.
The view sees only what that bot already reads, never another person's view, and every write is refused with
"Read-only view": a button, a form, marking something read. Reads that make or show a token, key or credential are
refused too. Each view, and each time the setting changes, is a line in the audit log.

The page a view shows offers nothing that writes, so a screenshot never suggests the bot could act: New task, Follow,
the page + and answer buttons are not shown, the message box is a grey "Read-only view" line, and other controls that
change something show greyed out. Tasks lists the bot's own open tasks by stage (Needs you is a person's list), and
Company is one card of them.

API: `GET/POST /api/v2/bots/{bot}/app-view`, `POST /api/v2/app-view` ([OpenAPI](api.md#the-contract)).

## Add a connection

### Subscription or API key?

Choose **Subscription** when you pay for a plan, such as ChatGPT or Claude:

- You sign in with the plan's account in the browser. There is no key to copy.
- The plan pays for the runs. The chip shows how much of the week's allowance is left ("N% left"), and use of the same
  account outside Tico lowers it too ([What "% left" means](#what--left-means)).
- An agent using a provider pool can pick another eligible plan when one runs out. An agent pinned to a single plan
  waits for that plan to reset.

Choose **API key** when you pay the provider for each use:

- There is no weekly allowance to run out. The chip shows only its state, with no "% left"; its line in the
  pop-up reads "No plan usage (API key)". The provider bills each run.
- Use it for a tool that has no subscription sign-in here (**Subscription** is greyed out), or when a plan's weekly
  limit is too small for your bots.
- Bots on one OpenAI API key run side by side, as do bots on one ChatGPT sign-in
  ([sharing a sign-in](#bots-sharing-one-chatgpt-sign-in-run-side-by-side)).
- Save portable keys for Codex, Claude Code, Cursor Agent and Muse Code in **Settings → Providers** and select them
  under **Paid by**. Use **Credentials** for a tool such as Gemini CLI that takes a per-agent credential grant.

### Add one

In an agent's **Computer** tab, open **This computer’s sign-in**, choose **Add a sign-in**, and pick an installed AI
tool that supports browser sign-in. Follow the provider's link and enter its code when prompted. The sign-in list
updates when the computer reports the login. Add shared API keys in **Settings > Providers** (see
[API keys on a Computer](#api-keys-on-a-computer)).

The computer must be online. Agents with **Paid by** unset for that tool use the new login from their next turn.

| Tool | Sign in from Tico | Sign-in state |
|---|---|---|
| Grok Build | Open the link and enter the device code shown in the pop-up | The computer checks Grok's local login store |
| Cursor Agent | Open the browser link shown in the pop-up and finish signing in | The computer runs Cursor's status command |

Both tools support **Sign out** and switching accounts through the API.
Cursor uses a file login so the computer's default login home stays separate on macOS as well as Linux. If Cursor
was signed in through the macOS Keychain, sign in once from Tico to create this login; the Keychain entry stays untouched.
A computer login with a custom auth-folder override must sign out and sign in to change accounts. See
[harness sign-in details](harnesses.md#supported-harnesses).

On an isolated Docker Computer, Grok's default `~/.grok` folder belongs to the bot user so sign-in,
token refresh and session saves work. This includes its credentials, `config.toml` and session history:
bots using the computer login share that whole folder, including an existing personal Grok login there.
Use a dedicated team login on that Computer. The runner repairs ownership in place at startup, sign-in,
and before starting or resuming a Grok thread; it does not copy login files between bot homes or change
file modes. A very large CLI folder is handed over shallowest first: if one pass (20,000 entries or 2 seconds)
cannot finish it, the runner log says so and the rest continues in the background; if that still cannot finish,
the next handover of the folder retries it. The same applies to the other harness login folders, Muse Code and
Gemini homes, and subscription profile homes. Grok's sign-in status check follows no link a bot could have made
anywhere in the auth file's path. A named profile uses its own runtime home; a shared-operator profile uses the
computer home.
Custom auth-folder overrides never select a folder for this ownership repair.

A new computer whose team signs in with a plan needs its own sign-in: a plan login cannot be copied between computers.
Settings > General > Health > Details shows "*computer name*: sign in to Codex" (or Claude Code) until it is done.

## Change or clear a login

### Signing out and switching accounts

In an agent's **Computer** tab, **This computer’s sign-in** offers **Sign out** on each signed-in tool that supports it.
The computer's operator, owners and admins can sign out. The computer must be online and on a current runner. An older
runner answers `Update <computer> to sign out from here`.

- **Sign out** first names the bots that stop: the active bots assigned to that Computer whose runtime is that login. The button stays off until they are named; if they can't be looked up, the dialog says so and offers
  **Try again**. The Computer then runs the CLI's own logout (`codex logout`, `claude auth logout`, `grok logout`, `cursor-agent logout`) as the user that
  signs in, and checks the login again. If it is still signed in, the sign-out fails and the login is as it was. A
  shared-key sign-in already running there is waited for (up to 40 seconds) so it cannot sign the login back in; if it
  is still running, the sign-out fails with "try again in a minute". Bots and assignments stay: those bots wait, as for
  any signed-out login, until someone signs in again.
- To switch accounts with the API, send `switch: true` to `POST /api/v2/computers/{id}/logins`. The Computer signs the new
  account in to a private folder inside the login's home (`.tico-switch-<id>`, 0700, with copies of the files the CLI keeps
  beside the login). Once the new account is signed in, the Computer asks Tico to go ahead. Once Tico agrees, the switch
  can no longer be canceled and has two more minutes to finish.
  Only then does it move the new login files in, keeping the old ones until it has checked the login again; if moving
  a file or that check fails, the old files go back. Canceling before that point, failing or running out of time leaves
  the old account working, and the side folder is removed (kept, and named in the message, only if the old files could
  not be put back). Runs that start afterwards use the new account. The account and usage readings of the old account
  are cleared.
- A **computer's own login** stays signed out until someone signs in again. An agent with **Paid by** set uses
  its selected provider for new turns on that tool.

None of this restarts the runner. `POST /api/v2/computers/{runner_id}/logouts` with `{"runtime":"codex"}` (and
`"profile"` for a named subscription left from an earlier release) signs out; `GET /api/v2/computers/{runner_id}/logouts/bots?runtime=codex` lists the
bots it stops; `POST /api/v2/computers/{runner_id}/logins` with `"switch": true` switches accounts. The legacy `POST
/api/v2/computers/{runner_id}/shared-key` can lift the sign-out marker. Follow a job with `GET /api/v2/computers/{runner_id}/logins/{id}` (`kind` says which; a sign-out ends `signed_out`). Agent Ops
cannot sign a login out, because it stops bots. A turn already running when the account changes is not stopped and
may finish on the old account. On a Mac, Claude Code keeps its login in the Keychain rather than in files, so there is
no side to sign in on: Switch account isn't offered for it there (the server and the runner refuse it too); sign out,
then sign in. Codex switches on a Mac as anywhere.

After a sign-in, switch or sign-out, the page looks again until the Computer has reported since
the change finished (its heartbeat; `readiness_at` on the computer in `GET /api/v2/operations`) and that report shows
the change, redrawing **Settings → Computers**, for about a minute.

### API keys on a Computer

Codex, Claude Code, Cursor Agent and Muse Code can use a key configured on their computer. To use a portable key,
add or replace it in **Settings → Providers**, then choose it under **Paid by** on the agent. Tico sends the selected
credential for the turn. Removing a saved key clears any selections that named it and restarts affected turns.

Legacy computer key routes answer `410` (`moved`) and direct you to Providers. Portable computer bindings are
read-only for compatibility. `PUT /api/v2/computer-subscriptions` answers `410`.

## Subscriptions from earlier releases

Earlier releases let a computer sign in named subscriptions besides its own login, and let a bot choose one. Each
computer now has one login per AI tool for all its bots. Upgrading deletes the bots' choices (the bot then runs on its
computer's login), and nothing signs a named subscription in or runs on one. A computer that still has some keeps
reporting them, unread and unshown, for one release; their folders stay on the computer. The computer's operator or an
admin can take one off with `POST /api/v2/computers/{runner_id}/removals` (`{"runtime":"codex","profile":"team-chatgpt"}`,
online computers on a runner that knows it, `model_remove_v1`) or sign it out with `POST
/api/v2/computers/{runner_id}/logouts` (`{"runtime":"codex","profile":"team-chatgpt"}`).

The server and runner negotiate this change with `computer_logins_v1` in config, assignments and attempts, and an
acknowledgement in the runner's capabilities and readiness. An updated runner keeps an older server's
existing account selections until that server supports the change; it also keeps the login home pinned across a
turn's primary and fallback work. On an updated server, an older runner reporting a bot on a named login waits with
an **Update** explanation, and changes to that tool's computer login wait too. Older runners with only unnamed
computer logins keep working. Update the named-login runner to resume on its configured `default_profile` (or the
operator's login without one); no credential files or model and harness choices are changed. The acknowledged
readiness report names that physical home as `computer_profile`, so its existing usage holds and readings still
belong to the same login, including after a runner restart.

## Usage readings

### Stale and missing readings

Sign-in status, weekly allowance and refresh status are separate. Failed or unsupported
reads keep the last successful reading, with its original timestamp. An elapsed reset
never means a fresh allowance or a repaired sign-in. Claude and other runtimes without
a supported standalone weekly read use observations from normal runs or a manual
provider reading. Missing percentages remain unknown, not zero.

A reading that names the provider used by an agent's turn stays with that provider when a different
agent reports usage on the same computer. **Try now** retries the selected provider's usage hold.
An agent with **Paid by** unset retries its current sign-in.

Starting a new sign-in, a switch or a sign-out through Tico invalidates earlier readings and in-flight refresh
results for that profile/runtime. Replacing credentials outside Tico under the same
local profile cannot currently be detected: refresh and confirm the provider reading
after such a change. Usage reported by a turn that was already running during sign-in
can also refer to its earlier account; avoid replacing an active profile while turns
are running. This is approximate monitoring, not a spending limit.

## Reference

The rest of this page is for administrators and integrators: where readings come from, the API, and how a computer keeps its logins.

### Signed-in account and usage windows

Weekly reports are scoped to a Computer, profile and runtime. Codex reports its seven-day window when its running host emits one. Claude Code may report the seven-day reset and limit state without a percentage; that percentage stays unknown. No API calls or token-to-percentage estimates are used to invent a provider balance. Usage elsewhere on the same provider account counts toward that allowance.

The last provider report includes its observation time and reset. Reports older than a day are labelled as possibly out of date. After reset they stay labelled as the previous week and never read as a fresh allowance. Reports survive a runner restart on the server. An updated computer can refresh Codex weekly usage directly; other providers need a supported subscription-backed run or a manual reading. An unnamed computer-default login has no named subscription snapshot; it remains unknown rather than being assigned to another profile. When that default login keeps stopping on a usage limit, **Settings → General → Health → Details** says it is out of quota ([Check health](health-monitor.md), `default_login_limited`).

Computers report the account each login is signed in to: email, plan and organization from `claude auth status --json`, and email and plan from Codex app-server's `account/read`, always requested with `refreshToken: false`. A Codex turn's or refresh's `planType` keeps the plan current. This deliberately reverses the earlier rule that Tico kept no account records: it is the first stored account identity, so the page can show which account each Computer and profile uses (`signed in as <email> · <plan>`). Credential files and `codex login status` output are never read for it, only allow-listed fields are kept, and a value that looks like a key or token is dropped on the computer and refused by the server, without affecting the sign-in state. The account is read alongside the existing one-minute sign-in probe; Codex's app-server read runs in the background at most every ten minutes. A signed-out login shows no account. An account stays one login's report: two profiles that show the same email are not merged.

Only owners and admins and the Computer's operator see accounts in `GET /api/v2/operations`, `GET /api/v2/computers` and `GET /api/v2/subscriptions`; other members see sign-in state only, and bots never see accounts. No new grants or permissions are added. A current server tells the runner it takes accounts in the heartbeat reply (`subscription_accounts`); until then, and against an older server, the runner leaves them out so the Computer reads as before.

Next to the weekly window, a profile reports Codex's 5-hour window (`five_hour`, from the same rate-limit events) with its own reset and observation time. A 5-hour reading whose reset is more than 5 hours 10 minutes after it was taken (10 minutes for clock skew) is unknown; the weekly window keeps its 8-day limit. The computer's pop-up shows both windows on each connection line as "% left" bars with the reading's age; each tool's installed version and Enabled switch are under **Versions and updates**. Tools without an account or usage source (Gemini CLI, Cursor Agent, pi, Grok) read "unavailable", never empty or full.

The Computer's own login (the one its bots use when no profile is assigned) reports the same two windows on its runtime row in readiness (`weekly`, `five_hour`), checked against the same limits and shown to the same people as its account. Claude reads them from each turn's `rate_limit_event`, and from the limit a reply it blocked carries (`api_error_params.rate_limit_info`), since Claude Code does not repeat an unchanged event: Claude Code 2.1.30x sends both windows in `unifiedWindows` with a 0–1 `utilization`, which becomes the percentage used (the page shows what is left). That field is undocumented, so a missing or out-of-range value reads "percentage unavailable", never 0% or 100%, and an older CLI reports only the reset as before. Until a Claude turn runs on that login, the line says "Weekly usage unavailable · reported after a Claude Code turn on this computer": Tico never starts a turn just to read usage. Codex's windows come from the same background app-server session that reads its account (at most every ten minutes, `account/rateLimits/read`, no turn and no paid request), for the own login and each named profile; an API-key login has no plan usage and is not asked. A server from before this change refuses the two runtime fields; the runner then leaves them out for ten minutes and sends the rest.

### A bot's connection in the API

Read or update **Paid by** per AI tool with `GET /api/v2/bots/{bot}/subscriptions` and
`PUT /api/v2/bots/{bot}/subscriptions`. The legacy `GET /api/v2/bots/{bot}/subscription` reports the local computer
login and its sign-in state. The legacy `GET /api/v2/subscriptions` returns named profiles that computers still
report from earlier releases.

### Sign-in and profiles on the computer

The Computer operator can manage sign-in on their own Computer; owners and admins can manage it on any Computer. A
sign-in, switch or switch to a subscription is for the Computer's login: `POST /api/v2/computers/{runner_id}/logins`
with `{"runtime":"codex"}` (or `claude`). One with a named `"profile"` is refused (422 `one_login`), and so is
`tico-runner profile login <name>` for any profile but the computer's own. The browser relays the provider's link and
code; the provider writes its credentials locally. A Computer's login can itself be a profile folder: the one named
`default_profile` in its runner.json (`profiles/<name>`), whose HOME (Claude Code) or CODEX_HOME (Codex) every bot's turn
uses. On an isolated Docker Computer the sign-in and turns run as the bot user, so each runtime's home in that profile
belongs to the bot user, while the profile folder and `profile.json` stay the supervisor's (the folder is traversable
only).

A current runner retries a rejected heartbeat without only the unsupported report: subscriptions, worktrees or repository clones. Other reports stay intact, and unrelated errors retain their original message. Invalid local profile names are omitted from reports. Provider sign-in probes are cached for one minute and refreshed after sign-in or a rejection. Turns and watcher
scripts use the cached sign-in state without probing at every start. A probe timeout leaves sign-in unknown and allows a
turn to try. A rejection affects only that runtime on that Computer; on a runtime that signs each bot in with its own key (pi, the Gemini CLI), only the bot whose key was refused stops until its key or grants change or the recheck passes. Completion and usage records include the profile
a turn ran in when it is a named one; empty profile fields are omitted.

Each run pins its selected local profile directory before preparing the provider environment. Changes to the local default or a profile directory affect later runs, not an in-progress primary or fallback attempt. Environment, host setup, session reuse, usage and rejection attribution use the same binding; fallback still checks its runtime eligibility and known sign-in state. A run started on the unnamed operator login does not acquire a newly configured profile midway through execution. Changing a profile directory starts a separate provider session. The first run after upgrading to directory-bound session keys also starts a fresh provider session for named profiles; the Tico conversation remains intact. This pins the login slot, not the provider account inside it: avoid replacing credentials in a slot while it has active runs.

Profile directories separate provider logins. Browser-created profiles live outside the bot workspace, with directories set to 0700 and owned by the same user that runs sign-in, probes and turns. Linux Docker harnesses that keep shared sign-in files (every one but pi) retain the existing shared bot Unix user; profiles do not yet provide a Unix permission boundary between groups. Do not rely on profiles to isolate files from another bot on the same Computer.
