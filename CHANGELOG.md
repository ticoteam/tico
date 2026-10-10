# Changelog

All notable changes to Tico are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each release is also published on
[GitHub](https://github.com/ticoteam/tico/releases) with its section below as the notes.

## [Unreleased]

### Changed
- **Naming a path no longer pauses a bot.** A note, message, task, approval or reply that names another bot's folder or a `secrets/` path is written as it is, never refused and never counted toward a quarantine; the mention is recorded for review. The runner no longer rewrites such paths in a bot's reply (local `file://` links still become plain words), and `TICO_ESCAPE_QUARANTINE_AT` is gone. An approval's payload naming the bot's own repository is accepted.
- **Writing checks warn instead of refusing.** A task or unsolicited message for a person whose title does not start with a verb, or whose body runs over 120 words, is written, and the response's `warnings` (and the task's history) say what to fix next time. A daily update with a section label, a heading or too many words is posted with a `warning`; an empty update, a week in review without its slides, a missing title or a missing approval field are still refused. `TICO_STYLE_LINT=refuse` brings the refusals back. A merge approval with no pull request says to file a decision task for the person instead.
- A bot can park a task it asked for itself in Waiting with a note saying what it waits on; the response warns that it goes back to open after a day unless the wait is recorded.
- **Done with subtasks still open.** A bot can mark its task Done, or close it, while subtasks are open. They stay open, and the task's history and the Finished or Closed notice end with "Still open:" and their titles.
- **A follow-up to a busy Claude bot reaches its running turn**, as it already did on Codex: the message goes into the turn Claude is working on instead of waiting for the next run. One that arrives just as the turn ends gets a turn of its own in the same run.

### Added
- **BotOps manages every bot in its own runs.** With no requester (the keeper's maintenance, a daily update), BotOps changes any bot's Tools, repositories, contact and Instructions except the built-in bots', and its GitHub token writes every bot's repository, so repairs no longer wait on the bot's owner. When a person asks, the token writes the repositories of the bots that person manages, with no repository step first; a bot-asked run gets only BotOps' own. It still never changes people, sees a Credential value, turns on sending or archives a bot with its own rights, and a run a bot asked for keeps that bot's rights. The new team rule **BotOps manages every bot** (Settings > Humans) turns it off. See docs/permissions.md.
- OAuth sign-in for the MCP server, so agents that cannot send a token (Dots, ChatGPT, the Claude app) connect with just the URL: you press Allow on Tico's page and the agent works as you. It is one line under Settings > Computers > API tokens, and Revoke there stops it. The Connect dialog's Dots and Claude steps no longer need a token.
- **Members store credentials for their own bots.** Anyone on the team fills a credential card, or has BotOps store a pasted value (`hub credential set --for-bot`), for a bot they own or manage; it is granted to that bot at once. They replace only a credential they stored, never read a value, and cannot store for someone else's bot. The owner turns it off with the new team rule **Members store credentials for their bots** (Settings > Humans, `members_store_credentials`). See docs/permissions.md.
- **Share a credential you added with your own bots.** The person who stored a credential can give it to, and take it from, bots they own or manage, in Tools > Credentials or through BotOps, without waiting on a credential administrator. They see its name, variable and grants, never its value unless it is also granted to them. Another person's bot, a person, or every computer stays an administrator's call, refused with who to ask. See docs/credential-vault.md.
- **Links and files show inline:** image links in task bodies, comments and chat show as a thumbnail that opens in the viewer, PDFs and documents (md, txt, csv, docx, xlsx, pptx, zip) as a card with the file's name and host, long links as host and a short path, and bare `https://` and `s3://` addresses become links. With `TICO_S3_VIEW_URLS` (`bucket=https://base`, such as a CloudFront distribution in front of the bucket), an `s3://` URI links to its view URL, and `hub` and the MCP tools rewrite it in a bot's text before sending; an unmapped one shows as a file chip with a Copy button. See docs/files.md, "Links to bucket objects".
- **Mail drafts take attachments, and a bot can discard its own drafts.** `mail draft --attach <path>` (repeat it, 25 MB in all) builds the draft with the files, for new mail and replies. Attachments are opt-in: `allow_attachments` in the mail policy stays false unless a team sets it to true. A file must be inside the bot's own folder (symlinks followed, `..` refused), files that look like keys, passwords or tokens are refused, sizes are checked before anything is read, and sending an attached draft always needs a per-message approval naming each file by name, size and sha256 (`mail draft` prints the request; the files are hashed again at send time), even to the sender of the thread. The reviewer and audit name the files. `mail discard <draft-id>` deletes an unsent draft the same bot made with `mail draft`; anyone else's draft, a sent one or a message is refused. See docs/mail.md, "Draft". ([#329](https://github.com/ticoteam/tico/issues/329))

### Fixed
- **A usage-limited bot no longer waits out its cooldown after the plan is renewed.** A person's new message to a limited bot lets it try once at once, and **Try now** beside the warning on the bot's page and in its chat line (anyone who manages the bot, or BotOps for them: `POST /api/v2/bots/{bot}/limit/retry`) clears the cooldown for it and every bot limited on the same runtime and computer. A run that hits the limit again goes back to the cooldown. ([#340](https://github.com/ticoteam/tico/issues/340))
- A person's request to BotOps lends their rights for as long as its task is open, not 24 hours (a cited message) or a week; a chat message with no open task still lends them for a week. The task's requester commenting on it in their own words counts as them asking, so the run it wakes can act; anyone else's comment, the Assistant's words and Slack channel text still lend nothing.
- A bot set to **Tasks only** still takes notes, comments and messages from its manager and from BotOps, so a manager's required notes arrive.
- BotOps acting for the person who asked files their support report and, for the owner, turns on the Librarian, instead of sending them to click it. With its own rights both are still refused.
- Granola imports retry a throttled note fetch within the sync, after 20 and then 60 seconds, and keep the first note fetch ten seconds clear of the sync's setup calls. A failed batch whose first two single-note fetches fail alike stops without more calls, and the next sync fetches the notes left untried (or notes missing from Granola's reply), giving up after three syncs. An already-imported note is re-read only while the meeting is under a day old, counted from the meeting's listed start, and a stored transcript is never fetched again, so each sync spends far fewer calls. Status shows what Granola answered (`last_error_detail`: step, HTTP status, JSON-RPC code, tool error, `Retry-After`, the throttle phrase matched, batch size) and skipped counts per code (`skip_reasons`), without its text. See docs/meetings.md.

## [0.3.37] - 2026-10-09

### Added
- **Setup gives a Cloudflare tunnel a runner hostname without Access.** With `--auth cloudflare`, `python3 -m setup` adds `runner.<domain>` (or `<name>-runner.<zone>` for a subdomain) to the tunnel it creates, routed for `/api/v2` and `/download` only, with its DNS record and `TICO_RUNNER_URL`, so computers and external agents connect without an Access bypass. `--runner-hostname` picks another host or `none`; `setup doctor` checks the host answers from the server and not a login page. See docs/install.md, "Cloudflare Tunnel". ([#315](https://github.com/ticoteam/tico/pull/315))
- **Check the AWS identity at start:** with `TICO_EXPECTED_AWS_ACCOUNT` (and optionally `TICO_EXPECTED_AWS_ROLE`, a role name or ARN) in `.env`, the Docker server, the Slack gateway and `restore` ask STS who they are and refuse to start, naming the expected and actual account or role, when it differs, no credentials are found or STS cannot be reached. Unset, nothing changes. See docs/install.md, "Check the AWS identity". ([#316](https://github.com/ticoteam/tico/pull/316))

### Fixed
- External sync connects to the address it checked is public when it fetches an image, so a name that resolves differently on a second lookup (DNS rebinding) cannot point the download at Tico's own network. TLS and the `Host` header still use the name. ([#319](https://github.com/ticoteam/tico/pull/319))
- A synced Grok Bot or Dots joins its human's group, so it sits under them on the team chart instead of at the top when the human is in a group. ([#317](https://github.com/ticoteam/tico/pull/317))
- **Runner hostname on a Cloudflare tunnel:** when `TICO_RUNNER_URL` names a host other than `TICO_DOMAIN`, the tunnel config the server writes routes that host too, for `/api/v2/...` and `/download/...` only, so it can be served without Access as docs/connect-an-agent.md says. No hand-written tunnel config or compose override is needed. See docs/install.md, "Cloudflare Tunnel". ([#313](https://github.com/ticoteam/tico/pull/313))
- Runners of several company environments on one checkout each move to the server's release. The first to update moved the shared checkout and restarted only itself; the others reported the new release while still running the old code until restarted by hand. A runner now reports the release it started on and restarts onto a checkout that is already there. ([#314](https://github.com/ticoteam/tico/pull/314))
- What you write to a Grok Bot in Tico is no longer lost by a routine set up before 0.3.36: only a sync that takes the inbox
  (`hub_external_sync`, which sends `inbox: true`) receives those messages and marks them delivered. `hub_grokbot_sync` leaves them
  waiting. `hub_external_sync` against an older server retries once without `inbox`. ([#318](https://github.com/ticoteam/tico/pull/318))
- "N messages wait for the next sync" now clears once a sync has handed them over. ([#318](https://github.com/ticoteam/tico/pull/318))
- External sync checks who is asking, the body and the member's bot limit before it fetches any image, and fetches at most 50 image
  links per sync; the rest stay links. ([#318](https://github.com/ticoteam/tico/pull/318))

## [0.3.36] - 2026-10-09

### Changed
- A human's Grok Bots sit in one framed **Grok Bot** cluster under them on the team chart, with Grok's own sections (Pinned first) nested inside, instead of chips beside their name. The sync now takes every Bot on the account and its section. Dots links the same way as a single bot under its human (`hub_external_sync` with `provider` `dots`). Any human may link their own bots, within a member's bot limit. What you write to a synced bot in Tico comes back to its routine in the next sync's `inbox`, so it can answer. `hub_grokbot_sync` keeps working. See docs/external-agent-sync.md. ([#311](https://github.com/ticoteam/tico/pull/311))
- A bot paused by the safety check says why in plain words (what it wrote and the secrets path or other bot's folder it named), shows the refused text with credential values masked and the task it was on, and has a one-click Resume bot beside the warning and under More, for every bot you manage, including bots on a computer. ([#309](https://github.com/ticoteam/tico/pull/309))
- A bot's Tools list is one line per tool (icon, name, key values, status) that opens to its details, and two or more GitHub repositories share one line ("GitHub · N repositories", access added up) that opens to a short line each. ([#301](https://github.com/ticoteam/tico/pull/301))
- On a phone, Tasks is in the bottom bar (Team, Search, Tasks, Updates, More). Phone task rows are denser: titles wrap to two lines, a task's note shows under its title, and a lone subtask's 0/1 is hidden. A task waiting on you shows Waiting once, not twice (desktop too). ([#300](https://github.com/ticoteam/tico/pull/300))
- A task's header has Done (for its owner) and Close buttons: one tap, no reason asked, and the task window closes back to where you were. Closing a request a bot made no longer asks why. The header's icons match (no stray lock, a drawn ✕), and the Private setting always reads Private, with a line saying who can see the task. ([#299](https://github.com/ticoteam/tico/pull/299))

### Fixed
- **Granola imports run again.** A long wait saved by the old code is capped at 24 hours, Sync now retries instead of being silently blocked, and the status shows its attempts. ([#310](https://github.com/ticoteam/tico/pull/310))
- Computers still on 0.3.34 can add and attach task worktrees against a 0.3.35 or newer server again. ([#306](https://github.com/ticoteam/tico/pull/306))
- Turning on a helper bot no longer places it on a computer that an inbox bot keeps to itself or that belongs to another member. ([#308](https://github.com/ticoteam/tico/pull/308), [#312](https://github.com/ticoteam/tico/pull/312))
- On a phone, the Tasks toolbar fits on one line whatever the font. ([#303](https://github.com/ticoteam/tico/pull/303))
- Phone screenshots of Docs work with the current reader. ([#302](https://github.com/ticoteam/tico/pull/302))


## [0.3.35] - 2026-10-09

### Added
- **Copy a chat message:** a faint copy button sits in the message's bottom-right corner and shows fully on hover or focus. People's message bubbles are wider. ([#283](https://github.com/ticoteam/tico/pull/283))
- **A paused bot says when it resumes.** Its banner gives the resume time and a Resume now button, people's messages wait for it instead of being refused, and only repeated refusals count toward a pause (at most 30 a day). ([#287](https://github.com/ticoteam/tico/pull/287))

### Changed
- A design pass across the app: one type scale and control size, clearer bot page tool stack, Docs with a recent list, reading width, "On this page" and a guard for unsaved edits, lighter Meetings with one Granola entry and an Add source menu, and pages that show content sooner. ([#296](https://github.com/ticoteam/tico/pull/296))
- Settings > Bots is one line per bot: Model and Fallback show as text ("Claude Opus 5.5 · high") and open a small popover with harness, model and effort; computer and fallback read as text until hovered. Settings > Computers shows status as a dot, the bots as one line that opens Bots filtered to that computer, and moves Remove computer and Accepts members' bots into the row's ⋯ menu. ([#292](https://github.com/ticoteam/tico/pull/292))
- Overview is a floating island at dusk: soft glow on lamps, screens and robot faces, clouds, fireflies, wildflowers, stepping-stone paths with a delivery robot, and furnished floors (bookshelves, arcade cabinets, lamps). Opening a floor works like an elevator: it settles onto the ground while floors above lift away and floors below sink, with a sweeping camera; buildings stack up storey by storey when the page opens. Teammates blink, hop, look up and wave when tapped. ([#290](https://github.com/ticoteam/tico/pull/290))
- Overview: select any floor to see it from above, with name tags; ▲ ▼ move between floors. Bots are 80s robots whose screen face is their own icon, glowing; people are rounder and varied. A **Buildings / Tower** switch shows one building per computer or every computer as a floor of one tower. The 3D view loads faster: its scripts download in parallel and three.js is sent gzipped (~790 KB to ~200 KB). ([#289](https://github.com/ticoteam/tico/pull/289))
- Opening a task reads only the messages that could be about it, not its whole chat room: each message keeps which tasks, replies and runs it links to, filled in for older messages at start. A task in a busy bot's room opens in the same time however long the room's history is. ([#293](https://github.com/ticoteam/tico/pull/293))

### Fixed
- Granola imports recover from Granola's rate limits. A throttled sync no longer retries on a fixed short schedule that kept the quota exhausted; it backs off (5 minutes, doubling, up to 6 hours, or longer if Granola asks), waits in the sync only when Granola names a short wait, and fetches notes not yet imported before re-reading ones it already has. ([#295](https://github.com/ticoteam/tico/pull/295))
- Task checkouts are created resumably again, and older or mixed-version worktrees are handled safely. This restores a fix that an accidental revert had removed. ([#284](https://github.com/ticoteam/tico/pull/284))
- Task lists, status and runner assignments read from one consistent database snapshot, and the media poll always uses its small pending index instead of scanning every file. ([#285](https://github.com/ticoteam/tico/pull/285), [#288](https://github.com/ticoteam/tico/pull/288))

## [0.3.34] - 2026-10-08

### Changed
- Pull requests, release tags and deploys no longer move tasks. A PR on a task shows its state (open, merged, shipped); a person moves the task. ([#269](https://github.com/ticoteam/tico/pull/269))
- A task's Private row is a plain checkbox; the "Company" label and help line are gone (hover for who can see it). ([#274](https://github.com/ticoteam/tico/pull/274))
- On a task type with bot `work` access, a bot may close a task that is in a ready step, so a deploy bot can move shipped tickets to Done. Moving a task into ready stays with people. ([#271](https://github.com/ticoteam/tico/pull/271))
- A personal API token no longer makes other credentials: creating or rotating a bot's agent credential, approving a pairing
  or making a SCIM token needs a signed-in browser. A service key made with a token stops when that token is revoked or expires. ([#273](https://github.com/ticoteam/tico/pull/273))
- The owner and Admins see every person's API tokens in Settings > Computers and revoke any of them. The token card and
  **Connect an agent** show for anyone allowed to make a token, not only Admins; someone who still holds a token keeps the card. ([#273](https://github.com/ticoteam/tico/pull/273))

### Fixed
- Task lists load faster: the chat list, a bot's Tasks tab, the Assistant page and a person's page ask the server only for the tasks they show, without bodies, and an unchanged list is answered "not modified" far more often. Single-task reads are faster, and an agent can read many tasks in one request. ([#279](https://github.com/ticoteam/tico/pull/279))
- Fewer GitHub token requests: a bot whose temporary assignment is refused for a repository is remembered instead of retried, and the server reuses its GitHub reads within a request. ([#280](https://github.com/ticoteam/tico/pull/280))
- Fleet status, Health and SQL requests do less repeated work: bot configuration is read once per request, status reads tasks for all bots at once, and concurrent audit writes share one commit. ([#282](https://github.com/ticoteam/tico/pull/282))
- Decisions answer within about 8 seconds: the server tries TypeSafe once, and when it is slow or down the company's own model answers instead of a 503 after up to a minute. ([#275](https://github.com/ticoteam/tico/pull/275))
- Clicking a meeting on the Meetings page opens it in the page; **Open in its own window** is a button in the meeting's top bar. ([#268](https://github.com/ticoteam/tico/pull/268))
- Health's Task worktrees no longer warns about a checkout of a commit (detached HEAD), and a real failure names the step and cause. ([#276](https://github.com/ticoteam/tico/pull/276))
- A turn stopped by a runner error says so, with the error's kind, instead of "Runner restarted during execution". ([#264](https://github.com/ticoteam/tico/pull/264))

## [0.3.33] - 2026-10-08

### Added
- **Stop a bot's turn.** While a bot is running in a chat, Stop takes Send's place in the composer for anyone who manages the bot. The runner interrupts the turn within seconds (on its next lease renewal), the run ends as stopped rather than held for review or retried, the chat shows "Stopped by <person>", and the next queued message starts as usual. ([#256](https://github.com/ticoteam/tico/pull/256))
- **Updates archive.** Archive an update to take it out of your Updates inbox for you only, browse the Archive view, and restore from there. A bot's newer daily or weekly update now replaces its older ones of the same kind in your inbox, without deleting history. Also `archive=true` on `GET /api/v2/updates` and `POST /api/v2/updates/archive`. ([#249](https://github.com/ticoteam/tico/pull/249), [#260](https://github.com/ticoteam/tico/pull/260))
- Add files to a task that already exists: drop them anywhere on the open task, paste a screenshot with Cmd+V, or use the **+** on Files. They upload straight away, and a comment being typed is kept. The new-task form takes dropped and pasted files too. Adding a file still needs the right to comment on the task. ([#235](https://github.com/ticoteam/tico/pull/235))
- The chat's **/** menu lists a Claude Code bot's own skills and commands from its repository, after the built-in ones. ([#253](https://github.com/ticoteam/tico/pull/253))
- **Over its spend limit:** a bot stopped by its daily limit shows a banner on its page with a Raise limit link and a "!" in the Team tree. Messages, tasks and questions sent to it are still saved, and the reply warns when it starts again. ([#262](https://github.com/ticoteam/tico/pull/262))
- Moving a task to Review wakes whoever asked for it, with "Ready for review: <title>" and the note. ([#247](https://github.com/ticoteam/tico/pull/247))
- Bots can read system metrics and Health: `GET /api/v2/system/metrics` answers the team's bots without who made each slow request, and `GET /api/v2/health` (and `hub health check`) answers them with the non-admin checks and which computers are up. ([#240](https://github.com/ticoteam/tico/pull/240))
- Health warns admins when a bot still in setup has held work for over an hour, with a Finish setup link, and that bot's watchers wait until setup is done. Health and readiness also name Claude Code deny rules that block commands a bot's instructions ask for. ([#251](https://github.com/ticoteam/tico/pull/251))
- Engineering templates: a Head of Engineering bot can review and merge the engineer bots' pull requests, never its own without another reviewer. A DevOps Engineer opens its own pull requests, and a reviewer bot can post reviews. Codex bots now read the repository's instructions through `AGENTS.md`. ([#240](https://github.com/ticoteam/tico/pull/240), [#251](https://github.com/ticoteam/tico/pull/251), [#246](https://github.com/ticoteam/tico/pull/246))

### Fixed
- Git calls in a bot's turn reuse a cached GitHub token instead of asking the server each time, and a cache hit no longer writes to the database. Fetching tokens had taken most of the server's time. Runners on this release still work with an older server. ([#239](https://github.com/ticoteam/tico/pull/239))
- The hourly database-size snapshot measures table sizes at most once a day instead of every hour (about 10 seconds each time). ([#238](https://github.com/ticoteam/tico/pull/238))
- A bot's question to a person is never lost: the question's id comes back at once, even if waiting for the answer times out. Health also flags a task that waits on a person who was never asked. ([#258](https://github.com/ticoteam/tico/pull/258))
- A bot can download an image attached in a chat it may read, instead of getting an error (HTTP 500). ([#255](https://github.com/ticoteam/tico/pull/255))
- Reopening a task quickly after closing it no longer empties its Files strip or shows the previous task's file. ([#261](https://github.com/ticoteam/tico/pull/261))
- Commits a bot makes on a Mac or Linux computer are authored by the bot (its name and `<slug>@bots.tico.invalid`), not the computer's owner, unless the bot's own environment sets a git identity. ([#241](https://github.com/ticoteam/tico/pull/241))
- A file left out of a bot's commit is reported once, not after every turn it is still in the checkout, and the notice stays out of the turn's summary. Files over 2 MB are left out by size without a secret scan and noted once as "too large to commit". ([#256](https://github.com/ticoteam/tico/pull/256))
- On a Mac, login shells keep `gh` on Tico's GitHub wrapper, so bots stay signed in to GitHub. ([#236](https://github.com/ticoteam/tico/pull/236))
- The Support Agent's watchers read `HQ_URL`, which reaches them, instead of `TICO_HQ_URL`, which the runner strips. ([#251](https://github.com/ticoteam/tico/pull/251))
- The release check runs the Docker isolation tests against the candidate's own runner image, fits its schedule to the computer's load, and its browser tests wait on conditions instead of fixed sleeps. ([#242](https://github.com/ticoteam/tico/pull/242)–[#245](https://github.com/ticoteam/tico/pull/245), [#248](https://github.com/ticoteam/tico/pull/248), [#252](https://github.com/ticoteam/tico/pull/252), [#254](https://github.com/ticoteam/tico/pull/254), [#257](https://github.com/ticoteam/tico/pull/257), [#259](https://github.com/ticoteam/tico/pull/259))

### Removed
- Trello guidance: the connect-tools guide and BotOps's connect-a-tool playbook no longer cover Trello; a generic REST skill example replaces it. ([#234](https://github.com/ticoteam/tico/pull/234))

## [0.3.32] - 2026-10-07

### Fixed
- A Claude Code turn whose saved session is missing on its computer (after a move, a new HOME or cleaned sessions) restarts once as a fresh session with the same conversation's messages, instead of failing with "No conversation found with session ID" on every new job.
- A bot's Files tab lists quickly on large teams: a file's provenance is read by index, and a run's tasks are read once per page instead of once per file version (2,000 files: 1.7 s to 44 ms).
- The media worker reads only pending rows instead of scanning every stored file every few seconds.
- Updates remove the untagged images older releases leave behind, keeping the one to roll back to. Without this a server's disk filled up over many releases.
- Health's Computer sign-in names only a model the computer's bots run on, not the team's default model on every computer where it is installed.
- A turn's GitHub token no longer takes 15-20 seconds for a bot whose own repository does not exist: Tico finds missing
  repositories with one listing of what the GitHub App can see instead of asking about each granted repository, and stops
  retrying a known-missing own repository beside the others until its five-minute mark expires.
- Health's Tool credentials check and the bot's Tools row count a GitHub tool's `GH_TOKEN`/`GITHUB_TOKEN` as present when
  the connected GitHub App mints it for the bot's repository.

## [0.3.31] - 2026-10-07

### Added
- **Stuck bots** on Overview and in Health: a bot with queued work that its computer cannot start (no model sign-in, no repository, computer offline) shows at once with the computer's reason, for example "Claude login required on Team box", and one fix: Sign in, Move to a computer that can run it, or the settings page. Work not started after 15 minutes shows too, unless the bot or its computer is busy with other turns.
- The bot Model control greys out harnesses its computer cannot run ("sign in", "not installed", "no key"), judged by the same rule as a model change and a move, and its editor offers Sign in or Move to a computer that can. Bulk "Change model" lists those bots as "Can't run" instead of sending the change.
- Health warns about a bot over 100M uncached input tokens in a day, with the number (cache reads do not count). `TICO_TOKEN_ALERT_INPUT` sets the threshold; 0 turns it off.
- **Windows PCs run bots through WSL 2.** Settings > Computers > Add computer > Windows PC (WSL 2, beta) shows one PowerShell line: `install-wsl.ps1`, now published with each release, sets up WSL 2 and Ubuntu if needed and runs the Linux runner inside it ([Install](docs/install.md#windows-pc-wsl-2)). Not yet tested on a real Windows PC.
- **Add computer shows one command** for a Mac (the Docker installer; a Tico checkout is still an option), a Linux server or a Windows PC, each with Copy.
- **Settings > Computers lists each computer's AI tools** with version and sign-in state. Owners, admins and the computer's operator can sign Codex and Claude Code in from there; Grok Build and Cursor show the command to run on the computer.
- A published release moves the Waiting tasks it contains to Review with "Shipped in vX.Y.Z": a pushed `vX.Y.Z` tag, a published GitHub release, or the release the server runs, when the task's merged pull requests or linked commits are on main at or before it. Apps created earlier: tick the **Release** event in the GitHub App; pushed tags work without it ([GitHub App](docs/github-app.md#task-pr-events)).
- Health flags a task whose status changes 20 times in 24 hours as a likely loop, naming who moves it, and tells its owner and requester once a day. Nothing is blocked.
- Health shows "<computer>: GitHub sign-in failed" when a bot's GitHub App token gets 401 there, and "<computer>: browser does not start" when the Chromium a bot installed cannot launch. Each computer checks every 15 minutes and reports two failures in a row ([GitHub App](docs/github-app.md#how-bots-sign-in-to-github), [harnesses](docs/harnesses.md)).
- `hub human list` shows each person's GitHub login, and a bot that opens a pull request for a person requests their review by it.
- **BotOps keeps the asking person's rights** when it follows through later on a task that person filed: it cites the task (`--on-behalf-of <task id>` on `hub api` and `hub bot model`), acts with that person's role, and every such change is recorded with the person, the cited request and whether it came from a live chat or a follow-through run. Runs started by Slack, meetings, watchers or comments, and bot requests, keep only the bot's rights ([Permissions](docs/permissions.md)).
- A refused BotOps action says what the person should click, with a link to the exact setting.
- A model change, by BotOps or `hub bot model`, is checked against the bot's own sign-in on its computer (its subscription, its own key, or the computer's shared sign-in) and refused with the fix when the computer cannot run it; the answer says whether the computer can run the new model.

### Changed
- A task worktree whose pull requests are all merged or closed frees itself once its bot is idle, even while the task stays open; one with unsaved or unpushed work is kept, with the reason. The worktree limit error says which pull requests to merge and which worktrees free themselves.
- A bot's new question to a person on a task replaces its earlier unanswered one to that person, and a finished task's open questions close (done keeps review questions). Questions older than three days move to a folded **Older questions** group at the end of Needs you and leave its count. `hub_question_ask` returns each ask's `message_id`, timeouts included.
- Engineering bot templates and `policies/writing.md`: plain notes, asks to someone on the roster, and only the Release Manager publishes releases ([Releasing](docs/releasing.md)).

### Fixed
- The Docker sign-in command for Claude Code was `claude setup-token`, which prints a token and leaves the CLI signed out; it is now `claude auth login`, run as `bot`.
- Moving a bot to another computer works again: the destination is judged by whether it can run the bot (its runtime installed and signed in, for the bot's own subscription or key), instead of always answering "Update the destination runner".
- A bot set up from a catalog template that lands on a new computer clones its GitHub history instead of starting from a fresh template copy. A copy that shares no history with GitHub never gets a turn, and a computer the bot returns to brings its clean old clone up to GitHub's history before the first turn.
- Credentials can no longer be saved or given to a bot under a name Tico reserves (`TICO_`, `DYLD_`, `LD_`, the runner's own `HUB_` names); the release-notes template now uses `UPDATE_KEY_*`. One stored before is left out of the bot's turns instead of stopping every turn, and Health names it under **Credential names** until its variable is renamed.
- A turn that fails before it starts logs why (secrets scrubbed), not only the server's refusal of its result.
- `gh pr create` in a bot's turn could fail with HTTP 401 Bad credentials while `git push` worked: a login shell (Codex runs `bash -lc`) dropped the turn's `gh` wrapper from PATH, and `gh` fell back to a token minted at turn start that could expire minutes later. The runner image now keeps the turn's PATH in login shells, and turn tokens are minted with at least 45 minutes left.
- A `GH_TOKEN` credential granted to a bot no longer competes with the GitHub App: the App's token wins in the connected organization, and the stored token serves only repositories outside it.
- The Docker runner image has the system libraries Chromium needs, so a Playwright browser a bot installs starts (it failed on missing `libglib-2.0.so.0`).

## [0.3.30] - 2026-10-07

### Added
- When GitHub asks someone to review a pull request linked to a task, Tico can put them on the task in a role, and take them off if the request is withdrawn. Off unless `TICO_GITHUB_REVIEW_ROLE` names the role (for example `reviewer`); people are matched by a new **GitHub** login in the Contact card of their profile (or `POST /api/v2/people/{id}` with `{"github": "login"}`). Two people cannot share a login (409 `github_taken`), and a login a roster already shares names nobody ([GitHub App](docs/github-app.md#task-pr-events)).
- An Owner's agent can create an empty private product repository through the MCP tool `hub_repo_product_create`: the first call previews, the second must repeat the exact `org/name`. Only Owners see the tool ([GitHub App](docs/github-app.md#creating-a-product-repository)).
- **Bot messages in Slack**, under Profile → Notifications: turn it off to keep every bot's messages to you in Tico instead of your Tico DM in Slack, end-of-run reports included, or mute only some bots. A bot you are talking to in Slack still answers there. Also `notify_slack_bot_messages` and `slack_muted_bots` on `POST /api/v2/humans/<id>` ([Slack](docs/slack.md#bot-messages)).
- The Assistant answers "hi" and "what task types do we use?" at once, from the workspace's own task types, without waiting on a model or starting a job.
- **Connect on your own row** of the Team chart until one of your personal tokens has reached Tico; it opens the connect dialog. Only shown to someone who may make a personal token (`can_create` on `GET /api/v2/me/tokens`).

### Changed
- A bot's page on a phone: the top line is only back, the bot and its tabs. Its goals lead More, then Learnings, branches and temporary assignments; the cards follow in groups, compact, with tools one line each and tables one line a row; status history, runs, tools, access and the session show three and "Show all". The rotating task line under the name is gone (the "needs you" card above the chat says it), and so are its two task reads per refresh.
- "Temporary assignments need Allow branches" no longer shows on every bot's page; the switch stays in the bot's Settings.
- The goal target beside a bot's chat box is now a mode instead of a form: while it is on, Send reads "Set goal", the box says "Goal for <bot>…", and what you send becomes the chat's pinned goal. It stays on for that bot until you turn it off; attached files still go as a message.
- Task and note cards in a row fold into one line in the chat, "4 updates" with the latest time; a single card shows as it is.
- A human's synced Grok Bots show as chips beside their name on the Team chart (one by name, then "+N") instead of rows under them.

### Fixed
- A Codex goal whose resumed thread reported "no goal" is checked with the harness before it is stopped, so a new goal is no longer cancelled at once; once a goal is met, a later clear is left alone.

## [0.3.29] - 2026-10-06

### Fixed
- A computer's event stream opens behind Cloudflare: it sent Python's default user agent, which Cloudflare's bot protection refuses (403), so every computer silently kept polling. It now names itself as the runner's other requests do, and says once in its log when the stream is unavailable.

## [0.3.28] - 2026-10-06

### Changed
- An idle computer no longer polls the server. A runner holds one event stream open (`GET /api/v2/runners/me/events`) and re-reads only what an event names: work for its bots, its assignments and their settings, credentials, repositories, worktree actions, sign-ins, harness actions, credential imports, quota reads and Restart. Events carry no values. The full readiness report goes when it changes and at least once a minute; everything is re-read once every five minutes as a backstop. While the stream is down, or against an older server, the runner polls as before; an older runner keeps polling the new server unchanged. With 8 idle computers, about 970 requests and 18,600 SQL statements a minute became about 30 requests and 1,100 statements ([How it works](docs/how-it-works.md#what-happens-when)).

### Fixed
- The bot list and a computer's polls cost about the same for 90 bots as for 7: each request reads every bot's configuration, assignment, status, queue, next-run tasks, notes and goals in one query per kind instead of one per bot, then checks privacy per task, job and message as before. Measured with the flight recorder at 90 bots: `GET /api/v2/bots` 1,375 statements to 25, a computer's heartbeat 633 to 32, `GET /api/v2/runners/me/repositories` 610 to 12 and `GET /api/v2/runners/assignments` 377 to 12. Answers are unchanged.
  - Whether a table exists is asked once per database file instead of on every check.
  - A privacy check no longer reads again a message it was handed straight from the database, a task's labels, or the tasks of a nested part of a response it has already checked as a whole.

## [0.3.27] - 2026-10-06

### Fixed
- Less work behind every poll from a computer and every privacy-checked read, found with the flight recorder on a busy install:
  - A computer's credential polls ask once per bot which credentials it holds, instead of once per stored credential (about 195,000 queries a minute on an install with 662 credentials).
  - A run's inputs are looked up by an index instead of scanning every message (about 10 ms each, the slowest query).
  - Privacy-checked reads load only the private tasks, by an index, instead of every task's full row.

## [0.3.26] - 2026-10-06

### Fixed
- A server no longer burns CPU on its own follow-up work while bots run. An open chat reads its conversation again at most every 3 seconds while a run streams output (it read the whole conversation for every step), and not in a hidden tab; a run starting or ending still shows at once. A computer's heartbeat only writes a worktree that changed, so it is no longer a task change every few seconds that every open board followed.
- Reads reuse their database connections instead of opening a new one each time: a new connection parsed the whole schema before its first statement, about 4-10 ms per read on a large install. A connection a caller changed (a function, an authorizer) is closed instead of reused.
- Privacy checks look up the tasks a message or run refers to in one query instead of one query per reference; on a busy server that was most of its SQL.
- A worktree cleanup the computer refuses (it keeps files) is asked again after 2, 4, ... 64 minutes and then left for a person, instead of on every heartbeat forever.

## [0.3.25] - 2026-10-06

### Added
- A **flight recorder**: each server keeps its own request, process and database history. Per minute and route template, by caller kind: calls, errors, p50/p95/max and bytes; each request over a second; CPU, memory, event-loop lag, write-lock waits and hold times; per hour, SQL time by statement shape and database size by table; each start's release, commit, package and settings hashes, and whether the image runs the code of its release tag (code changed after the build, or an image built from another commit, shows in Health); and when the event loop stalls two seconds, every busy thread's stack. Owners and admins read it under **Performance** on the Health page, at `GET /api/v2/system/metrics`, with `hub health metrics` or `hub_health_metrics`. Nothing leaves the server; `TICO_FLIGHT_RECORDER=0` turns it off ([Observability](docs/observability.md#the-flight-recorder)).
- A task list's refresh costs only what changed. The Tasks page's 2-minute refresh, a tab waking or coming back online, and the refresh of a browser without live events now ask `GET /api/v2/tasks?changed_after=<cursor>` for the tasks changed since the list was read and apply them in place (`gone` lists the ones that left), instead of reading every open task again; an unchanged list of tasks, labels or routines is answered `304` from its `ETag`. Tags, files, routines, open asks and steps now reach the change log too, so live events carry them ([Catching a task list up](docs/custom-frontend.md#catching-a-task-list-up)).

### Changed
- JSON answers are encoded by orjson in the request's own thread instead of FastAPI's pure-Python encoder on the event loop: a page of 500 tasks takes about 1 ms to encode instead of about 100 ms, during which no other request (health checks and runner heartbeats included) could run. Display names are added with orjson too. The server also uses uvloop and httptools, uvicorn's faster event loop and HTTP parser.
- The web and desktop app open their first page without waiting for health diagnostics or sidebar counts. Sign-in reads chat permissions directly, and API JSON (when no proxy such as Cloudflare compresses it) and standalone UI assets use gzip when accepted to reduce transfer time.
- New indexes keep busy installs fast as history grows: unanswered questions, answers, tasks carried by a run and the media check's file-version lookup. Linked tasks on a large page are found without probing unrelated task pairs.
- Live updates gather a busy team's writes for up to a second before sending them; a stream following an open conversation waits at most a quarter second, so messages and run output stay prompt.

### Fixed
- A failed roster refresh preserves the last complete roster and no longer marks a reachable server offline. Loading health checks show Connecting until their result arrives.
- A model sign-in that expires mid-run no longer replays a run that already acted: the run is saved for review, and the bot waits for a new sign-in on that computer.
- Operations alerts for container installs report the backup system they actually use, not the imported VM timers' old history.
- A Grok bot whose imported history has not synced lately shows a history-sync warning instead of a runtime failure, in Health and across the app.
- A saved demo reopens with its edits and owner session after a restart, and a demo refuses a directory holding other data.
- Container health probes run at their scheduled deadline instead of a probe's rounding skipping one.

### Removed
- Unused dependencies: `websockets` (Slack's Socket Mode uses slack_sdk's built-in client) and `pytest` from the server image (it stays a development dependency).

## [0.3.24] - 2026-10-06

### Fixed
- Open tabs no longer re-read the whole task list each time a task is created elsewhere: a new task arrives with its live event, like a changed one, and a bot's or person's task lists re-read at most every 15 seconds and not in a hidden tab. On a busy team v0.3.23 kept the server's CPU full and made it slow or unreachable.

### Added
- People on a task by role. Besides its owner, a task can list people and bots under roles the team names (`roles`: `{"developer": ["bob"], "reviewer": ["charlie"]}`); one person may hold several roles, and Tico gives roles no meaning or rights, so clients decide what they mean. `hub task update --role reviewer=charlie`, `hub task list --member me [--role reviewer]`, `hub_task_update`/`hub_task_create` (`roles`), `hub_task_list` and `GET /api/v2/tasks` (`member`, `role`); the task page has a People row ([Tasks](docs/tasks.md#people-on-a-task-by-role)).
- Changelog automatically includes the installed release history and shows **What's new** since each person's last look. Search, browse older releases and mark only the shown changes as read; that state follows the person across devices. Users can also list and acknowledge changes through `hub_changelog_list` / `hub_changelog_mark_read`, `hub changelog` and the stable `/api/v2/changelog` API ([Updates](docs/updates.md#what-changed-since-your-last-look)).

## [0.3.23] - 2026-10-06

### Breaking
- Task relationships moved to `task_relations`: `tasks.parent_id` and `tasks.blocked_by` are dropped (migrations 30 and cloud 60 move them over). Task answers no longer carry `parent_id`, `blocked_by`, `blocker` or `parent`; read `relations` instead. Task create takes `relations: [{task, kind}]` instead of `parent_id`, and task update no longer takes `parent_id` or `blocked_by`: use `POST /api/v2/tasks/{id}/relations`. `hub task parent`, `hub_task_reparent` and `hub task update --blocked-by` are gone (use `hub task relate --kind parent|blocked_by`); `hub_task_create` and `hub_task_child_create` take `parent` instead of `parent_id`. SQL that read the old columns joins `task_relations` ([Hub SQL](docs/hub-sql.md)).

### Added
- Long chats show an **Outline**: a list of every prompt a person sent, across all pages, that jumps to the one you pick ([Conversation](docs/conversation.md)).
- A **nightly learning run**: at 3:00 each night Tico reads the day's chats, task comments and notes, Slack channel messages, mail a teammate sent from their own mailbox and live company meetings once, scores each item with the decision model, and gives each working bot and the Librarian one task with its packet; **Learnings** in the account menu shows what each night changed. `POST /api/v2/learnings/run` runs it now ([Learnings](docs/learnings.md#nightly-learning-run)).
- A bot's **Learnings** button beside its name shows every change to its `memory/` and `knowledge/` as a history, newest first: the commit subject, who made it and when, the diff once pushed, a GitHub link, and the task or chat it came from when the reader may see it. The computer running the bot reports these commits after each run and every five minutes ([Learnings](docs/learnings.md)).
- **Update keys** and the Release Manager's rollout. The owner makes a service key with `--scope update` (`hub service-key create --label ... --scope update`); it can only check for updates, start an update to a named release and read the update status (`/api/v2/system/update`), and is refused everywhere else. `GET /api/v2/system/update` also counts the online computers by state. The Release Manager template gains `playbooks/rollout.md` and `software/rollout.py`: once the owner approves a version on its release task, it pushes the tag at the approved commit with its repository write grant, waits for the GitHub release, updates the team's own install as the canary and then the other installs together with their update keys, stops at the first failure or rollback, and reports each install's version before and after ([Releasing](docs/releasing.md#the-release-managers-rollout), [Service keys](docs/service-keys.md#update-keys)).
- Relate tasks to each other, the way cards are attached on a board: related, duplicate of, follow-up of, blocks and blocked by (several blockers now), and parent, all in one `task_relations` table. The task page's Related section groups them by kind, + asks which kind, and board cards show a related count. `hub task relate <id> <other> --kind ...`, `hub_task_relate` and `POST /api/v2/tasks/{id}/relations`; a bot's run sees the ones it may read. Builds on Steven's draft #110 ([Tasks](docs/tasks.md#related-tasks)).
- Live events: one stream, `GET /api/v2/events`, carries every change to tasks, the messages and runs of a conversation, bots' status lines and the person's own Needs you as it commits, so the web app no longer polls for them. Only what the viewer may read is sent; `after` resumes from a change number, `reset` says the client was away longer than the day the log keeps, and bots are refused. The chat, the Goal Manager, the Librarian, task boards, a bot's columns and ticker, the open task and the Needs-you counts follow it; the full refresh that remains (issues, roster, computers, Updates) runs every 2 minutes instead of 30 seconds while it is connected ([Live events](docs/custom-frontend.md#live-events)). Builds on the task change feed from #118.
- Delete a task made by mistake into a trash it can be restored from: its human requester, or anyone who may move any task, with `hub task delete`, `hub_task_delete` or `POST /api/v2/tasks/{id}/delete`. The task leaves every list, board, search and bot context at once and keeps its number; `hub task restore` puts it back with its conversation, comments and links. A task carrying work (a bot turn, a file, an approval, a subtask) is refused; bots close instead. `python -m backend.manage delete-tasks` deletes a list offline, such as a bulk import run twice, and `purge-deleted-tasks` empties the trash for good ([Tasks](docs/tasks.md#deleting-tasks-made-by-mistake)).

### Changed
- Releases are much faster. The release gate is `python scripts/release_checks.py --release`: it builds the candidate images once and runs the smoke, side-jobs and a shortened journey (install, upgrade, migrating rollback) at the same time, in a few minutes, instead of the full suite every PR already ran. The GitHub release is published as soon as the Docker images exist; desktop apps no longer hold it up, are built only when the shell changed (otherwise the previous app is carried forward), and use a Rust cache. Images copy the source last, so a release rebuilds one small layer, and health checks probe every 2 seconds while a container starts, so updates and rollbacks finish sooner ([Releasing](docs/releasing.md)).
- The Librarian is the market's curator: it builds the first market map and now keeps it current, with a daily **Curate the market** routine and an **Urgent market insight** routine, both seeded on a new install and added to an existing Librarian at its next start. The server accepts market graph writes, and serves the insight queue, only to the Librarian and the owner ([Librarian](docs/librarian.md)).
- BotOps writes a missing setup answer as a hold on the one step that needs it (publishing, sending, a rollout), never as "stop if setup is incomplete" for a whole routine; the routines guide says the same ([Routines](docs/routines.md#writing-one)).
- Overview turns the company into a solarpunk campus with one building per computer, bots at their assigned workstations and human collaborators shown on each computer they work with. Green roof terraces, solar canopies and illuminated cutaway tunnels connect the buildings. Select a building or teammate for details; keyboard navigation, reduced-motion support and an accessible roster remain available. Updates remains the default.
- License current Tico development under PolyForm Perimeter 1.0.1: internal use and modification remain permitted; providing competing products to others is restricted. Earlier Apache 2.0 versions and third-party licenses retain their terms. New contributions are licensed under both PolyForm Perimeter 1.0.1 and Apache 2.0 so they can be included in a future Apache release.

### Removed
- **Breaking:** `GET /api/v2/conversations/{id}/watch` and `GET /api/v2/conversations/{id}/stream`. Follow `GET /api/v2/events?topics=messages,runs&conversation={id}` and read `GET /api/v2/conversations/{id}/snapshot` when it says the conversation changed; `examples/custom-frontend` shows how ([Live events](docs/custom-frontend.md#live-events)).
- The Market Research Analyst bot and its `market` catalog template. Its playbooks and market knowledge moved into the Librarian. An install that still has a `market-analyst` bot archives it at its next start and hands its open tasks to the Librarian; its history and repository stay.

### Fixed
- A bot's Send never does nothing silently. A send the server did not confirm with a message says "Not sent" and keeps the draft; Return while a message is still sending sends the next one after it; text filled in without typing still sends on a click.
- A bot with a stopped run saved for later shows idle, not crashed, after each later run that finishes cleanly; the saved run stays in Health until reviewed.
- The server updater refuses an update when `compose.override.yaml` pins the `server` or `slack` image, and rolls back when the switched server does not run the pulled image or report the target release. A rollback from an untagged image returns to the release the server reported ([Updates](docs/updates.md#moving-a-hand-managed-install-onto-the-updater)).
- Docker installs pass `TICO_APP_NAME`, `TICO_ASSISTANT_NAME`, `TICO_INTEGRATIONS_DIR`, `AWS_REGION` and `AWS_DEFAULT_REGION` to the server and Slack, and `TICO_SLACK_SECRET_ARN` to Slack, when set; an empty AWS region is treated as unset.

## [0.3.22] - 2026-10-05

### Added
- Delete a task made by mistake into a trash it can be restored from: its human requester, or anyone who may move any task, with `hub task delete`, `hub_task_delete` or `POST /api/v2/tasks/{id}/delete`. The task leaves every list, board, search and bot context at once and keeps its number; `hub task restore` puts it back with its conversation, comments and links. A task carrying work (a bot turn, a file, an approval, a subtask) is refused; bots close instead. `python -m backend.manage delete-tasks` deletes a list offline, such as a bulk import run twice, and `purge-deleted-tasks` empties the trash for good ([Tasks](docs/tasks.md#deleting-tasks-made-by-mistake)).
- Live Meetings: bots join a text meeting, answer when named, and the meeting can be replayed; attaching a bot needs Write access to it, and the saved meeting waits in each person's review queue.
- Temporary assignment branches: a task can get its own short-lived engineer copy of a role, with a shared learning trunk, a three-slot limit and cleanup that never removes unpublished or stashed work.
- An Owner can create an empty private product repository from Settings or `hub repo product-create`, after a preview and an exact-name confirmation.
- Owners and admins can delete a group from the team chart; its teammates and groups move up a level.
- A bot can mark its task as waiting on a named person (`hub task update --status waiting --on <person>`); the task shows in that person's Needs you with a Waiting label and counts as needing them.
- Each computer checks every 15 minutes that it can really start a container; Health warns, naming the computer and its operator, after two failures in a row.

### Changed
- The Librarian is the market's curator: it builds the first market map and now keeps it current, with a daily **Curate the market** routine and an **Urgent market insight** routine, both seeded on a new install and added to an existing Librarian at its next start. The server accepts market graph writes, and serves the insight queue, only to the Librarian and the owner ([Librarian](docs/librarian.md)).
- BotOps writes a missing setup answer as a hold on the one step that needs it (publishing, sending, a rollout), never as "stop if setup is incomplete" for a whole routine; the routines guide says the same ([Routines](docs/routines.md#writing-one)).
- Overview turns the company into a solarpunk campus with one building per computer, bots at their assigned workstations and human collaborators shown on each computer they work with. Green roof terraces, solar canopies and illuminated cutaway tunnels connect the buildings. Select a building or teammate for details; keyboard navigation, reduced-motion support and an accessible roster remain available. Updates remains the default.
- License current Tico development under PolyForm Perimeter 1.0.1: internal use and modification remain permitted; providing competing products to others is restricted. Earlier Apache 2.0 versions and third-party licenses retain their terms. New contributions are licensed under both PolyForm Perimeter 1.0.1 and Apache 2.0 so they can be included in a future Apache release.

### Removed
- The Market Research Analyst bot and its `market` catalog template. Its playbooks and market knowledge moved into the Librarian. An install that still has a `market-analyst` bot archives it at its next start and hands its open tasks to the Librarian; its history and repository stay.

### Fixed
- A bot's Send never does nothing silently. A send the server did not confirm with a message says "Not sent" and keeps the draft; Return while a message is still sending sends the next one after it; text filled in without typing still sends on a click.
- A bot with a stopped run saved for later shows idle, not crashed, after each later run that finishes cleanly; the saved run stays in Health until reviewed.
- The server updater refuses an update when `compose.override.yaml` pins the `server` or `slack` image, and rolls back when the switched server does not run the pulled image or report the target release. A rollback from an untagged image returns to the release the server reported ([Updates](docs/updates.md#moving-a-hand-managed-install-onto-the-updater)).
- Docker installs pass `TICO_APP_NAME`, `TICO_ASSISTANT_NAME`, `TICO_INTEGRATIONS_DIR`, `AWS_REGION` and `AWS_DEFAULT_REGION` to the server and Slack, and `TICO_SLACK_SECRET_ARN` to Slack, when set; an empty AWS region is treated as unset.
- A runner input that was taken in and then requeued no longer fails every poll; it moves to the next run once, and a private task's input waits for its own run instead of joining another task's.
- Interrupted worktree creation resumes, and worktrees on computers still running an older release keep their state instead of looping restores.
- A Codex model at capacity counts as a usage limit, not a failed run.
- A task number given by hand can no longer take a deleted task's number, so a restored task keeps it.

## [0.3.21] - 2026-10-04

### Added
- Archive and restore KPIs through the owner interface, API and CLI while preserving definitions, readings and audit history. Archived KPIs leave active lists and freshness work by default; historical views remain available.

### Fixed
- Authenticate company desktop updates with the selected hub session, allow signed storage redirects without forwarding hub cookies, and retain updater signature verification and configured public runner feeds.
- Keep mail doctor read-only and support supervisor-mediated credentials without reading the supervisor key. Missing labels are reported with a separate repair hint.
- Derive company publisher OIDC trust from current GitHub repository settings, including immutable subjects, while restricting trust to this repository's version tags.
- Keep older matching Done tasks reachable when the first globally paginated page contains only other task types.

### Upgrade notes
- Update the server, runners and company desktop. Older shells unable to authenticate the company update feed need the documented recovery bridge or a verified manual company install; publication alone does not prove automatic updating.
- KPI archival is an explicit authorized action; no existing KPI definitions are automatically archived.

## [0.3.20] - 2026-10-04

### Fixed
- Let authorized development tests use disposable synthetic databases while keeping live team state behind the shared API and honoring task-specific restrictions.
- BotOps accepts assigned bot-maintenance work with the requester's existing rights and records its own blockers without requiring a new human instruction. Notices still cannot override a human request.
- BotOps isolates provider sessions by task and conversation by default, preventing unrelated jobs from sharing model history.
- Repeated stalls create a distinct, reusable diagnostic for each task. BotOps's own stalls and unavailable BotOps route to a human, and Health reports escalated public tasks with no progress.

### Upgrade notes
- Update both server and runner. BotOps starts separate provider sessions; existing task and chat records remain available. Paused routines stay paused.

## [0.3.19] - 2026-10-03

### Added
- Choose harness, model, effort and named subscription separately, and filter usage by those settings ([#62](https://github.com/ticoteam/tico/pull/62), [#67](https://github.com/ticoteam/tico/pull/67)).
- View approximate weekly subscription usage and reset information, refresh supported Codex readings, and distinguish sign-in health from stale or unknown usage ([#68](https://github.com/ticoteam/tico/pull/68), [#69](https://github.com/ticoteam/tico/pull/69)).
- Search bot templates, copy individual chat messages, and let bot managers mark setup complete from the bot page ([#63](https://github.com/ticoteam/tico/pull/63), [#74](https://github.com/ticoteam/tico/pull/74), [#79](https://github.com/ticoteam/tico/pull/79)).
- Let administrators explicitly grant repository creation to selected bots ([#78](https://github.com/ticoteam/tico/pull/78)).

### Changed
- Simplify chat attachments, computer cards, settings navigation and subscription controls; hide archived repositories by default ([#61](https://github.com/ticoteam/tico/pull/61), [#64](https://github.com/ticoteam/tico/pull/64), [#65](https://github.com/ticoteam/tico/pull/65), [#66](https://github.com/ticoteam/tico/pull/66), [#73](https://github.com/ticoteam/tico/pull/73)).
- Remove Overview from mobile bottom navigation and use inline chat for Goal Manager ([#75](https://github.com/ticoteam/tico/pull/75), [#76](https://github.com/ticoteam/tico/pull/76)).

### Fixed
- Include the copy-message icon in the shipped font and preserve updates explicitly marked unread while viewing the feed ([#80](https://github.com/ticoteam/tico/pull/80)).
- Preserve subscription identity, assignments and usage history when renaming, and keep subscription bindings fixed throughout active runs and fallback ([#70](https://github.com/ticoteam/tico/pull/70), [#71](https://github.com/ticoteam/tico/pull/71)).
- Correct release-candidate navigation and browser regressions ([#72](https://github.com/ticoteam/tico/pull/72)).
- Honor task blockers and bound stalled-task retries so blocked work does not repeatedly restart ([#77](https://github.com/ticoteam/tico/pull/77)).

### Upgrade notes
- Update both server and runner for the subscription changes. Named profiles begin a fresh provider session after the binding upgrade; app conversations remain intact.
- Weekly usage is approximate, may include activity outside Tico, and is not a spending cap. Replacing credentials inside an active login directory is outside the per-run binding guarantee.

## [0.3.18] - 2026-10-03

### Fixed
- Ready tasks can complete after missed push deliveries when bounded GitHub ancestry checks verify their merged PRs are included in the running release, preserving pending-PR, open-subtask and human-change safeguards ([#59](https://github.com/ticoteam/tico/pull/59)).

### Documentation
- Manual GitHub App setup guidance requires an Active webhook and the Push event subscription, explains how to check delivery, and clarifies that enabling events does not restore missed history ([#59](https://github.com/ticoteam/tico/pull/59)).

## [0.3.17] - 2026-10-03

### Fixed
- The bot page uses the existing Updates icon for its updates tab, including on mobile ([#55](https://github.com/ticoteam/tico/pull/55)).
- Model changes from More > Bot settings open the shared confirmation and checkpoint-progress dialog instead of silently reverting the selection ([#56](https://github.com/ticoteam/tico/pull/56)).
- Docker server and runner images include validated source commit and repository provenance for deployment-based task completion. Local journey image builds pass the same provenance arguments ([#57](https://github.com/ticoteam/tico/pull/57)).

## [0.3.16] - 2026-10-03

### Fixed
- Task details show worktree error explanations inline, readable on touch screens and with a keyboard, with long paths wrapping to fit ([#53](https://github.com/ticoteam/tico/pull/53)).

## [0.3.15] - 2026-10-02

### Added
- Overview turns the company into an interactive building with department floors, human and bot teammates, and reported bot status. It is available first in desktop and mobile navigation; Updates remains the default. The night scene has department signs on the back walls, direct floor exploration, reduced-motion support and an accessible roster when 3D is unavailable.

### Changed
- GitHub Actions builds and publishes artifacts without Python, browser or Docker smoke-test jobs.
- Company apps show short initials beside the macOS menu-bar icon, with an optional private label override; stable app identities and update feeds are preserved.
- Company download links open the described installer directly. Generic apps are labeled as requiring manual setup and no longer appear in company-app banners.

### Fixed
- Kanban columns keep a readable minimum width and scroll horizontally within the board, while empty columns remain compact.
- macOS windows use a native title bar; switching to the right rail no longer deadlocks, and window restoration accounts for title-bar height and Retina scaling.
- Unrelated chat messages no longer resolve questions on other tasks. Direct replies resolve only their referenced question; task replies stay within their task.

## [0.3.14] - 2026-10-02

### Changed
- Task rows no longer show circular status icons. Status appears once in each column or section header, with labels retained in ungrouped views and task details.

### Fixed
- Apply current task visibility to routine history, task comments, answers, file reviews and cached responses.
- Keep Done separate from Closed: comments that wake an assignee bot do not grant permission to close its task, and occupied pipeline steps cannot silently change their status meaning. Done tasks offer an explicit Close action.
- Removing a routine preserves unrelated queued work and already claimed occurrences.
- Task details preserve unsent answers through property saves, ignore stale file and link responses, and keep phone file previews full width.
- Deliver replies to distinct inputs even when the answer text matches an earlier reply, while avoiding duplicate sends from the same attempt.
- Redact quoted secrets and private URL hosts in reviewed support diagnostics, without increasing log volume.
- Preserve committed WAL data in a separate failed-update recovery copy before restoring a rollback snapshot.
- Retrying a completed task returns its existing result while the computer still owns the attempt and retains access.

## [0.3.13] - 2026-10-02

### Added
- Whoever wrote a task comment can change its text or delete it: `POST /api/v2/tasks/{tid}/comments/{mid}` and
  `.../delete` in the stable API, `hub task comment-edit` and `hub task comment-delete`, and MCP `hub_task_comment_edit`
  and `hub_task_comment_delete`. Neither wakes anyone. Deleted text is omitted from future supported reads, and
  the audit log keeps only change metadata. Copies already delivered to people, bots or external services remain. The task view shows "edited" beside an edited comment.
- Service keys: a key another system, such as your product's backend, uses to file, update, close and reopen tasks,
  and nothing else. `POST /api/v2/inbound/tasks` takes the system's own `key` for each piece of work and makes one task
  match what it says now, so calls may come in any order and twice. The owner and the admins manage keys with
  `hub service-key create|list|revoke` (or `/api/v2/service-keys`); there is no Settings page for them yet
  (docs/service-keys.md).
- Task types can let every bot comment and create subtasks (`read`), or also move, reassign and link tasks (`work`),
  through Settings → Types, the API, CLI and MCP. Ordinary company tasks are readable by default; these settings
  grant additional actions and never bypass a private task's participants.
- A task can be renamed: `title` on `POST /api/v2/tasks/{id}`, `hub_task_update` and `hub task update --title`, for
  whoever may change its other fields. The new title gets the checks a new task's title would, is kept in the task's
  history, and becomes the subject of the task's own conversation.
- Ticket numbers: a mover can make a custom type **numbered**, and each task created on it or moved onto it gets the
  team's next number (one sequence for the whole team), kept for good. A mover can keep an imported ticket's number
  (`number` on create, or once on a task that has none). `#18945` names the task wherever an id does, and
  `GET /api/v2/tasks?number=18945` finds it.
- A task has a place within its step, `step_rank`: a task that enters a step joins its end (its top with `top`), and
  the people on it and movers can move it. `GET /api/v2/tasks` takes `type` and `step` filters and `sort=step`, the
  board filtered to a type orders its columns that way, and `hub task list` and `hub_task_list` take the same.
- `GET /api/v2/tasks?updated_since=<time>` returns only the tasks changed after that time, for a client that polls, and
  `brief=true` leaves out their bodies and acceptance criteria.

### Fixed
- Completing a task keeps it Done, including self-requested bot tasks and recurring work. Closing is a separate decision; completed tasks no longer close automatically with age or when the next routine runs.
- Tasks opened from a teammate page use the full shared task detail, including files, questions, comments and code links. On phones it fills the screen, and Back returns to the teammate page.
- A task's `updated` time moves when a file is attached to it or archived, a link is removed, a linked pull request
  changes state, or a question on it is asked or answered, as it already did for its fields, comments and new links.

### Changed
- A task on a custom type is a ticket on that type's board, not an ask: the rule for a request to a person (a title
  that starts with a verb, the ask first, under 120 words) applies to General tasks only, in the API, MCP, `hub` and
  the dry run. A ticket still needs a title.
- A bot's ticket on a custom type keeps its reference numbers and all-caps words: the plain-English title check
  applies to General tasks only.
- Tickets on a numbered type stay out of their owner's Needs you, and the desktop count, unless one carries a question
  for that person; a declined ticket stays out of its requester's. General tasks and other types are listed as before.

### Security
- New ordinary tasks are readable by company people and bots. Private tasks limit future access to the requester and current assignee; bot defaults also protect requests assigned to sensitive bots. Existing known ordinary work remains visible; sensitive or ambiguous origins upgrade privately while retaining messages and attachments.

## [0.3.12] - 2026-10-02

### Added
- Imported meetings wait in a personal Pending queue before sharing, with approve, dismiss and restore actions, batch sharing, per-person auto-share, a Team review default, and CLI/MCP review tools.
- Private company desktop apps built on version tags, with encrypted CI artifacts and automatic updates from their own Tico server.
- Owners can set a public team icon in Settings or through the API and CLI.

### Improved
- Granola retries rate limits sooner, reports current sync counts and account status, and keeps sign-in responsive during background sync. Regenerated summaries update only notes that have not been edited by a person.
- Meetings bulk review applies to the visible rows, so a filtered view cannot accidentally share hidden meetings.
- Tasks always show one selected type. General and Dev tickets sit beside search, with other types in the arrow menu; switching views, clearing filters, creating tasks and pinning views retain the selected type.
- The team chart refreshes groups, people and bots during normal polling, so changes made by BotOps or another session appear without a reload while preserving collapsed groups.
- Help puts Support in the existing resizable right rail, with continuing conversations, a simpler overview, platform descriptions and an inline glossary.
- New support requests include editable, redacted diagnostics by default. Replies can attach a fresh capture; rejected attachments are never silently dropped.
- Support diagnostics group repeated server failures, include safe exception locations, runner heartbeat/recovery context and bounded browser failure counts, and report missing capture coverage without verbose logging.

### Fixed
- Listening uses a valid local question override consistently and reports invalid overrides without exposing their contents. Health shows invalid Listening categories, and repeated configuration checks avoid duplicate warnings.
- Oversized multipart headers return a consistent upload error and release temporary files. Linux runner configuration checks handle GNU and BSD file metadata tools consistently.
- The team chart shows personal branches only to their operator, labeled Your branch. Original bots remain visible in their groups; administrators can still inspect other branches through the branch picker and Settings.
- Human desktop downloads prefer valid company builds even when older than the server; company updater feeds reject generic manifests.
- S3 attachments and desktop downloads share the first credential source that passes a bounded write check, with optional explicit selection. Uploads wait for a writable source and keep that identity through multipart cleanup. Health reports the source and denied permission; failed checks retry every 30 minutes, and denied reads try other sources before retained local copies. Concurrent probes and download manifest fetches share bounded work, including delayed credential discovery.

### Changed
- Fireflies is no longer offered for new imports. Existing meetings, notes, recordings, file versions and historical source filters remain available.

### Security
- Backport the GLib string iterator pointer fix used by the Linux desktop app, with a checked vendored source and an optimized regression test.

## [0.3.11] - 2026-10-02

### Fixed
- Docker installs forward attachment bucket settings and optional dedicated file keys without importing the operator's AWS shell credentials. S3 files and downloads can reuse backup keys; non-AWS backup regions do not carry over to AWS file storage.
- S3 startup write checks use bounded timeouts and let shutdown finish promptly. Health distinguishes missing multipart cleanup permission from denied writes while retaining local read fallback; new backup policies include multipart permissions.

- Reading conversation messages no longer fails when a teammate created tasks during its turn.
- Assistant turns on older Computers fetch the Assistant's own credentials while acting with the person's rights.
- Jobs that repeatedly fail to start stop after ten expired leases since server startup, leaving old attempts out of the upgrade retry cap. Notices tolerate missing Computer and teammate records and ask to check the Computer; only an incompatible runner is asked to update Tico.

## [0.3.10] - 2026-10-02

### Fixed
- Messages and some task lists showed people and bots by id instead of name since 0.3.9.

## [0.3.9] - 2026-10-02

### Fixed
- Downloading a .json attachment returned reformatted content; files are served exactly as stored again.

## [0.3.8] - 2026-10-02

### Added
- Task files keep versions by name, with notes, questions and every answer.
- Tasks show file tiles, version previews and comparisons; board cards show covers and open questions.
- Pin task views under Pipelines and see linked pictures inline.
- Stream large attachments to local storage or S3, with video seeking, posters and thumbnails.
- Health shows file storage and background copy progress.

### Fixed
- Files attached to comments appear on every install.

### Security
- Update urllib3 to 2.8.0, PyJWT to 2.15.0 and DOMPurify to 3.4.16 to address dependency security advisories.

### Changed
- One generic Tico desktop app connects to any server: first launch asks for the server address, and Change server is available in the app and tray menus. Optional per-environment builds still work.
- Every GitHub release includes signed desktop updater bundles and installers for macOS, Windows and Linux, plus the public updater manifest. Generic apps update automatically from GitHub.
- Hubs offer desktop downloads from their running GitHub release when their bucket build is absent or older; a current or newer bucket build still wins, and unavailable GitHub downloads do not cause server errors.


## [0.3.7] - 2026-10-02

### Fixed
- Granola account sync reads notes preceded by provider text, fetches meetings in paced batches of ten, and retries rate limits without skipping meetings or losing sync progress.

## [0.3.6] - 2026-10-02

### Fixed
- Granola (account sign-in, including the free plan) now imports notes: Granola's answers with participant emails and markdown summaries are read correctly, dates like "Feb 4, 2026 7:30 PM" are understood, the free plan is recognized, and a failed sync names the step that failed.

### Changed
- Every release builds the desktop app first; if it fails, the release isn't published. The desktop app now carries the release's version (0.3.6, not 2.0.x) and the current Tico icon; an installed 2.0.x app needs one reinstall from the download page, then it updates itself with each release.

## [0.3.5] - 2026-10-02

### Fixed
- Task links: `#/tasks/<id>` opens the task, and the Tasks view (Open, Board, Recurring, Done) stays in the address, so a board link is stable.
- Task comments show a saved note's text, once, instead of "left a note".
- Images, video and audio in a task load straight into the player with a placeholder while loading; video starts playing before the whole file has downloaded.
- Health no longer calls a computer offline when a bot's work has only waited a while, and starter bots waiting for their first setup no longer count as slow work.
- A bot's push at computer start-up uses that bot's own GitHub access, so its commits no longer wait unpushed.
- A computer that enrolls again keeps its Google key for Mail and Calendar. Removing the key afterwards stays removed.
- Enrolling a computer no longer puts archived bots on it.
- Health warns when a Tool on an online computer is missing its credential.
- Close: one meeting deleted in Close no longer stops every later transcript pull.

## [0.3.4] - 2026-10-02

### Fixed
- Connecting a Granola account no longer fails as "unreachable": Granola's compressed answers were decoded twice.

## [0.3.3] - 2026-10-02

### Fixed
- Pages opened after a bot page (Docs, Meetings, Goals, Settings, Help) no longer keep the bot page's two-column layout.
- Goals: the Goal Manager has its own right rail beside the goals tree. The Assistant page uses the bot-page layout.
- "Set goal" replaces "No goal · Set goal". Tool icons show the account or repository, access and state on hover.
- Market reads well in the light theme and labels its graph. Tools names bots and credentials in words. The Assistant
  is never shown as "Tico". Model names, routine schedules, Health notes, GitHub status and many Settings, Docs, Runs
  and Meetings labels are clearer, and Settings fits a phone.
- A computer whose recent error lines ran past 300 characters had its whole status report refused and could not become ready. Computers now cut each line to 300 characters and the server trims instead of refusing.
- Connecting a Granola account works: Granola requires a redirect address when Tico registers, even for the code-based sign-in.

## [0.3.2] - 2026-10-02

### Added
- Connect your own Granola account in Meetings through its official MCP, including free-plan notes, encrypted per-person sign-in and background sync; API keys remain available for Business/Enterprise.
- Nested tasks: a parent shows its subtasks' progress ("4 of 10 done · 7 PRs merged") and its owner hears when the last one closes.
- Several PRs per task, with checks, conflicts and reviews that wake the assigned teammate.
- Task worktrees: separate branches and folders, saved work before cleanup, and restoration when a task reopens. Computers without a GitHub App can use their own Git login.
- Subscriptions assigned to a group or bot. An assigned bot waits with a reason when its subscription is unavailable, keeping its work on the chosen login.
- An Engineering Manager template.
- A redesigned Tasks page with one-line rows, tabs, groups, filter chips, side peek, a properties panel, bulk actions and keyboard controls.

### Changed
- Git maintenance keeps the Computer’s system and global credential settings, URL rewrites, SSH command, proxy and CA settings, and Git identity, without running repository-configured programs.
- Computers keep repository mirrors separately from bot-owned base clones. Base clones refresh from the mirror without a token and keep GitHub as their push destination.
- New task worktrees try to refresh the mirror immediately; an unavailable connection uses cached history and reports its age.
- Unused base clones retire after 30 days when no task worktrees remain and every local commit is saved remotely, even under a different branch name.

### Fixed
- A failed scheduled item no longer rolls back other scheduled work; reminder task titles describe the work to do.
- Computers report busy bots so the same bot cannot start a second run during a turn or worktree maintenance. Turns keep renewing their lease while waiting for maintenance.
- Repository credentials stay scoped to the acting bot and repository. Git maintenance filters credentials and disables repository hooks and fsmonitor, including during secret checks.
- Reopening during cleanup returns saved work to the task branch. Reusing a removed worktree enforces the bot's limit, and a reassigned task can create a fresh worktree while its previous owner awaits cleanup.
- Ignored Python build metadata, bytecode, desktop metadata and editor settings no longer prevent worktree cleanup.
- PR status checks and task queries avoid unnecessary full-table scans.

## [0.3.1] - 2026-10-02

### Changed
- The bot page's right rail runs full height in a denser style: Active first, then Updates (just "Updates · 10h
  ago"), Files (names only, a small "+N"), Recurring, with "Assigned to others" and Done folded at the bottom. The
  "Latest update" banner and the separate history button are gone.
- A bot's Tools show as up to three small icons next to its name, then "+N"; the full list is under More.

### Added
- Settings → Repositories: tick the GitHub repositories the team works on. Bot repositories are hidden unless you show
  them, and each repository's setup command comes from its `tico.json` or `conductor.json` or is typed in. "New bots
  get" picks own repository only or all ticked repositories. The list refreshes from GitHub on its own.
- A bot's Repositories setting replaces "Extra GitHub repositories": own repository only, all ticked repositories, or
  chosen ones, each read or write. Every bot keeps the extra repositories it had, with write access. Bots' Git and
  GitHub CLI commands use the matching read or write access per repository.
- Computers keep base clones of the repositories their bots can reach, in the team's folder, fetched in the
  background. Cloning pauses when disk space is low, and a clone no bot has needed for 30 days is removed. Operations
  shows each computer's clones.
- Chat goals: the target next to attach pins a goal to a Codex or Claude Code bot's chat, and the bot keeps working
  toward it. The goal sits above the chat (three lines, two on a phone; tap for all of it, Edit, Pause and Clear),
  shows Working, Paused, Met or Stopped, and a met or stopped goal becomes one line in the chat. The bot's row in the
  sidebar shows a target while it has one.
- Type `/` in a bot's chat for its commands: `/goal`, `/new`, `/task`, `/branch`, `/help` and the ones its harness
  takes, such as `/compact`. A `/word` that isn't a command now goes to the bot as ordinary text. Goals and commands
  are also in the API, MCP (`hub_chat_goal`) and CLI (`hub chat goal`).

### Fixed
- docs-eval ignores Markdown emphasis around a price, treats "about ($29)" as approximate, and accepts "no price
  variation" for an exact price.
- A request you DM to BotOps in Slack counts as yours, like your Tico chat. Channel and thread messages still change
  nothing for you.
- Update status says which release the server is running and which it ran before, from its own startup record, so
  BotOps no longer names a release the server never ran.
- When BotOps closes its own task for you, the close stays yours and its closing note reads as BotOps.
- A person with no photo gets initials and no photo request (it logged a 404 on every page).
- The Docs sample question asks "Who helps new hires?".
- The Librarian closes a fenced code block only on a line of the same fence character, so code with a mixed line stays
  literal.

## [0.3.0] - 2026-10-01

### Changed
- **Assistant** in the left rail opens your private chat as a page of its own (`#/assistant`), with the same thread,
  composer and live reply as a bot's chat and suggestions while it is empty. Your own page's Assistant tab and "Ask the
  Assistant…" in search open it too; the history is the same room.
- **A calmer look.** Dark is the default, on one neutral gray ramp; Light is the same ramp reversed, and Settings has
  Dark, Light and System for this browser. Text is 13px, page titles 16px at most, and names and labels 600 weight.
  Icons are one weight lighter. The teal accent is a little brighter in dark.
- **Chat reads as one column.** Bot chat, the Assistant, update replies and task comments sit in a centred column up to
  800px wide in 13.5px type on roomier lines; your words are in a bubble, bot replies stay plain.
- The sidebar is narrower (236px) and can be dragged wider or narrower; so can the tasks column on a bot page.
  Your widths follow you between computers. Double-click an edge to reset it.
- A bot's Slack channels are listed under Slack in its Tools, not under the bot in the sidebar. Message bots (such as
  the Inbox Manager) still show their mailboxes and channels there.
- Bot avatars are clearly blob-shaped: each bot gets one of ten soft shape families and a little more distortion,
  with a slow wiggle only while it works.
### Fixed
- Branches clone their original repository using your own git access when no GitHub App link is available.
- Health names missing repositories and the clone command on the computer that needs them; BotOps can fix them when asked.
- Make my branch offers a planned branch when your computers already run the original or a branch. It starts when you add another computer.
### Added
- Settings > Tags offers a one-click Release checklist starter, opening the existing tag when its key is already used.

### Fixed
- The Docs panel lets you reopen previous private conversations with their questions and answers.

## [0.2.42] - 2026-10-01

### Fixed
- The Librarian follows Markdown's own code rules (fences closed by a long enough line, equal-length backtick spans,
  indented code) and keeps single-quoted text across lines; "the runner syncs `AGENT.md`" still reads as the Computer.
- docs-eval treats "approximately: $29" as approximate.

## [0.2.41] - 2026-10-01

### Fixed
- The Librarian keeps every Markdown literal as written (tilde fences, any-length code spans, reference links, quotes
  across lines) and keeps the case of what it replaces (ALL CAPS, Title Case).
- docs-eval accepts "with no variation" for an exact price and treats "(approximately) $29" and "$29 USD
  approximately" as approximate.

## [0.2.40] - 2026-10-01

### Fixed
- The Librarian leaves quoted text and blockquotes alone, keeps ALL-CAPS, and changes "the runner pulls" only when an
  update, release, repository or the next run follows, so an athlete's hamstring stays theirs.
- docs-eval treats "(approximately)", "approximately USD $29" and "with some variation" as approximate.
## [0.2.39] - 2026-10-01

### Added
- Branches: a bot's owner can turn on **Allow branches**, and each person then chooses **Make my branch** on its page
  (or `hub bot branch`, or asks BotOps). A branch runs on that person's own computer and AI subscription, shares the
  original's instructions and repository, and merges its lessons back. New tasks and chats to the original go to your
  branch; a picker opens the original or anyone's branch. Branches follow the original's model settings, leave
  routines with the original, and archive and restore with it. `hub bot copy` still makes an independent bot.
- Tags: task labels become tags with a display label, metadata and a Markdown checklist. Templates hold a reusable
  checklist (for example a release checklist); **Make a tag** copies one into a new tag such as `release-2026-10-02`.
  Click a tag chip to see its checklist and tasks. Existing labels, `--label` and `hub task label` keep working.
- Task types: add types with named steps in **Settings > Types** (for example Draft, Legal review, Published). Each
  step maps to a status, so bots, GitHub pull request moves and older computers keep using statuses. Filter the board
  by type to use its steps as columns. Tasks start as **General**, which works as before.

## [0.2.38] - 2026-10-01

### Fixed
- The Librarian rewrites only Tico's own jargon (standing instructions, Hub docs, "the runner pulls"); a team's prose
  about its company, coworkers or machines stays as written.
- docs-eval counts a sentence's first word as another plan only before "plan" or "costs" ("Pricing is $99" now
  contradicts), and approximations after the amount ("give or take", "approximately") fail.
- Tools explains that the model and the bot's own repository are not changed there, in the docs and in the refusal.

## [0.2.37] - 2026-10-01

### Fixed
- Chat, notes, status and live-brief `since` filters accept a whole-second, fractional or offset time and no longer
  skip messages later in that same second.
- docs-eval reads a sentence's capitalized first word as a plan name only when the price follows it ("Team costs"), so
  "Ultimately, it costs $99" fails; "nearly", "almost", "close to", "or so" and "-ish" count as approximate.
- The Librarian's wording repair leaves "new machine" and "that machine" alone and keeps a sentence's capital letter.
- Settings > Health > Services shows Calendar Tool and Mail Tool.

## [0.2.36] - 2026-10-01

### Fixed
- BotOps' notes and progress on its own tasks are written as BotOps, not as the person who asked. Closing a task still
  needs the person's rights.
- docs-eval fails an answer when any later sentence gives the subject a different value ("The price is $99", "In
  reality, it costs $99", "To be clear..."), while a value for another plan stays fine.
- The Librarian's wording repair no longer changes every "company" and "machine" in its docs; docs the 0.2.35 repair
  changed are redone once from their earlier version.
- Health names the Calendar Tool and Mail Tool instead of `connector:` services, and operations returns `computers`
  (`machines` stays for older clients).
- The update guide says the computer pulls before the next run.

## [0.2.35] - 2026-10-01

### Fixed
- The Goal Manager panel keeps its conversation after a reload. With no AI provider, the Librarian rail and the Goal
  Manager say so and link to Settings > AI providers instead of looking busy.
- A computer lists only the bots assigned to it; archived bots and other computers' bots no longer show as not ready.
- The Librarian says Computer and Instructions in how-to answers, and older generated FAQs are repaired once (prior
  versions and human edits kept).
- docs-eval ties each fact to its subject and fails more contradictions ("Its price is", "Actually", approximate prices,
  ranges).
- Owner SQL can read a safe status view of the Slack outbound queue, and JSON table functions work read-only.
- BotOps' completion text says Setup; tool text and the built-in Instructions say that a bot's ordinary message is not
  its final answer.
- Install and update recipes point at the current release. Help says computers, not machines.

## [0.2.34] - 2026-10-01

### Fixed
- **BotOps could not start any turn a human asked for** (0.2.33). Every BotOps call switched to the person, including the
  run's own credential fetch, which only a bot may make, so the run failed before it began. Only what BotOps does for a
  person (the delegable routes) acts as that person now; the run's own plumbing, its status and its own messages stay
  BotOps'. A BotOps run always receives BotOps' own credentials, whoever asked.

## [0.2.33] - 2026-10-01

### Changed
- **BotOps acts with the rights of whoever asked.** A human's request: every BotOps tool uses that human's full rights, so
  BotOps finishes setup, reads the setup task and takes a template bot live without handing it back. A bot's request: only
  that bot's own narrower rights. No requester (scheduled work): BotOps' own. If a tool is refused, BotOps retries through
  `hub_api` before asking you. A guard test checks every tool in every case.
- **Built-in bots off the team chart.** Assistant and BotOps sit in the main left rail; the Librarian and Goal Manager are
  reached from Docs and Goals. Built-in bots get no goals. Message bots are one section, each mailbox or channel under its bot.
- **Goals:** a Goal Manager panel at the top shows what it keeps current, when it next checks goals and KPIs, its last run,
  and a box to ask it to change a goal.
- **Docs and Market:** "Ask AI" becomes an always-open **Ask the Librarian** rail (a sheet on a phone).
- **Help** explains how Tico works, with a diagram of the server, the built-ins, your computers, Tools and external agents,
  and a "who does what" table.

### Added
- A Slack DM when a task you asked for is finished or declined: the result, who handled it, the first line of their note and
  a link. On by default for everyone linked in Slack; "Task results in Slack" on your profile turns it off.
- Owner MCP can read a bot's current Instructions and archive a task attachment.

### Fixed
- A request typed in Tico's chat with BotOps was refused as "a Slack message" when that chat also mirrored a Slack DM. The
  message decides now, not the room; requests that really arrive through Slack are still refused, with plainer words.
- Going live no longer turns on a routine briefly when you asked for no schedule.
- A file type Tico doesn't publish gets a clear error naming the allowed types, not a server error.
- Task attachments keep their leading and trailing spaces. BotOps says Tico, not Hub.
- A credential the computer holds for a bot (other than the Google key) shows as ready and isn't sent to the mail-token check.
- Setup says plainly when an AI provider is missing; the demo on another port prints the right address; Health wording.
- Librarian: the Humans page ranks for adding a human behind a sign-in proxy; copied grants are explained; maps refresh after
  the upgrade so archived or unreadable sources drop out; docs-eval catches more contradictions; Hermes doctor accepts the
  manual heartbeat mode; old words in tool text.
- One long email no longer stops a mailbox's mail copies (#11).

### Docs
- BotOps' manifest, the onboarding guide, model sign-in examples and the restore recipe match the current behavior.

## [0.2.32] - 2026-10-01

Fixes from the v0.2.30 re-test (71 new findings and the items it left open).

### Fixed
- BotOps finishes the setup and tool-change tasks the server makes for it, with the requester's rights, so you no longer
  finish template builds or tool changes by hand. Starter templates build directly on the computer over MCP too.
- A new bot never brings back an archived bot with the same name. Removing a bot keeps its history, and BotOps can delete
  a bot's repository through the GitHub App when the owner asks.
- A correction sent to BotOps reaches the run it is about (`in_reply_to`); withdrawn tool requests close their tasks; a
  quiet close stays quiet; BotOps status follows the current task.
- Task questions stay with their own task, and a bot can ask its next question once a reply answers the last one.
  Routine "Run now" no longer disappears into other work. KPIs take freshness from the reading that gives the value.
- Docker passes the documented server settings (`TICO_BLOCK_EXTERNAL_INVITES`, `TICO_CREDENTIAL_ADMINS`,
  `TICO_CREDENTIAL_KMS_KEY` and the rest); Health says whether calendar lockdown and KMS wrapping are active.
  `TICO_TEAM_NAME` works beside the older `TICO_COMPANY_NAME`.
- Health shows the same checks in the app and over MCP, names computers with an unknown or newer version, and warns when
  a computer's disk is nearly full; an update that failed for disk space retries when space frees.
- A one-time runner step (such as 0.2.31's `HUB_` grants) is only used up by a computer new enough to do it.
- MCP: `hub_api` deletes without a body, non-JSON files can be read, malformed requests get a clear error, and
  `hub_bot_model` can choose a harness.
- Librarian: Ask AI includes the Tico manual and keeps long questions whole, plain questions find linked docs, a named team
  doc ranks first, and an answer can be collected after a timeout. docs-eval catches contradictory facts.
- The demo accepts browser writes on any port. OpenClaw profiles respect their own home. Undo on a finished task works,
  the Tools table fits a phone, and setup says exactly what suggestions send to Tico HQ.
- Named model keys such as `OPENAI_API_KEY` offer "Every computer" without an extra field.

### Docs
- Every guide and starter template stores tool secrets as Credentials granted to the bot; menu paths, the architecture and
  harness pages, and screenshots match the app. The first-bot guide uses a starter that needs only Tico.

Upgrade the server before its computers.

## [0.2.31] - 2026-10-01

### Fixed
- A team's own `HUB_` variables (such as `HUB_BUCKET`) reach bots again. 0.2.30 treated every `HUB_` name as Tico's own,
  so the upgrade left them out of the bots' grants and a grant under that name failed the run. Only the names the runner
  sets (`HUB_TOKEN`, `HUB_API_URL`, `HUB_BOT` and the rest) stay reserved. On upgrade, bots that already migrated get the
  `HUB_` keys their old files had (never one with a grant or a revoked grant).
- The updater removes older release images after a healthy update, keeping the new one and the one to roll back to.
  Every release used to stay on disk until the host filled up and an update failed with "no space left on device".

## [0.2.30] - 2026-10-01

A release built from a full QA pass (nine testers, 166 findings).

### Changed
- **No more "never without a human".** The setup step and its four limits are gone, and so are the Confirm steps for
  archiving or deleting bots, adding humans from outside the team's domain and updating Tico. The one switch that stays:
  a bot drafts messages to outsiders until sending is turned on for it. Starter bots act on requested work with their
  Tools instead of waiting for approvals; they keep their privacy, credential and evidence rules.
- **Bots get only the credentials granted to them.** A run no longer inherits the computer's environment or
  `_shared.env`. On upgrade, every existing bot is granted what it could read before (its own file, every `_shared.env`
  key except the Codex sign-in key, declared and runtime keys), so nothing it uses breaks. Upgrade the server before its
  computers.
- **Personal tokens can do what their human can:** `hub_api`, `hub_bot_update`, and new tools to archive bots, docs and
  files, delete meetings and run a routine now. A token's human can also reach their own Assistant over MCP.
- BotOps changes the owner's Team rules directly, changes routines by their real ids (`<bot>:<key>`, which failed
  before), reads routines with the requester's rights, keeps separate requests separate, and answers in one short reply.
- Credential administrators can delete a Credential (UI, API, MCP, CLI): grants and the stored value are erased, an
  audit line stays. Credential import works from an admin's personal token.
- "Built-in" means only the four system bots. Message bots such as the Inbox Manager are listed as Message bots.

### Added
- `TICO_PORT` (and `install.sh --port`): run Tico on another local port; public, runner and MCP addresses follow it.
  `--owner-name` and `--team-name` prefill Finish setup.
- Remove computer in Settings > Computers, Add bot and Add from template under Settings > Bots, an Edit Instructions path,
  Reopen and Undo for tasks, Undo and Restore for archived docs, New routine in Settings > Routines.
- Hermes and OpenClaw pairing previews the profile and host before it approves, activates a planned bot, and can recover.
- Template bots work: an empty model uses the team default, and creating one over MCP queues the BotOps build.
  `hub_template_list` shows each template's setup questions and first routine.
- Computers and Health show each computer's release and update state over MCP; Runs show failed and expired attempts.
- Owner SQL reads the current Docs and Files tables. Listening and Needs you batches have their own guides.

### Fixed
- Editing an event routine kept changing its trigger to `meeting.ready`.
- A task with labels and an attachment could not be created; reopening a task left it counted as done; a second
  clarifying question slipped through after the first answer.
- `hub_run_list since=24h` returned nothing (Health suggests it).
- An unknown model or other tool mistake crashed the MCP call with HTTP 500; validation errors now name the field.
- The generated Add computer command assumed the default Docker project; rehearsal mode could still call the decision
  model; Grok sync dropped distinct messages and could not clear metadata; proposal responses broke the OpenAPI schema.
- The Librarian's answer tool returned an unfinished first reply; search now keeps more than twelve words, returns
  sections with excerpts, maps old words to new ones and ranks the manual beside team docs.
- docs-eval passes only when facts are right, rejects unknown question ids and cleans up after a failed import. The demo
  shows runs, message bots and files.

### Docs
- Privacy, backup, credential, database and calendar pages now describe what the code does. Quick start continues to a
  first bot result; the docs have owner, member and operator paths with the glossary linked; old words replaced.

## [0.2.29] - 2026-09-30

### Fixed
- A credential shared with a bot through Credentials counts as present in the bot's Tools row, Health and `hub tool list`
  ("granted through the credential vault"). It showed as missing, although the bot's runs received it.
- Creating bot repositories follows the GitHub App installation's live Administration permission. Turning it on in GitHub
  (and accepting it for the organisation) is enough; Tico no longer needs GitHub reconnected. The Tools GitHub card says whether
  Tico can create repositories.

## [0.2.28] - 2026-09-30

Connect tools and copy bots.

### Added
- **MCP servers as a bot tool.** A bot's `tools:` can declare a vendor's remote MCP server (`mcp: {url, transport, headers}`);
  `${VAR}` in headers is filled only from credentials granted to that bot. The runner hands them to Claude Code, Codex, Gemini CLI
  and Grok Build next to Tico's own server (Cursor, Antigravity and pi show a warning instead). The Tools tab, `hub tool list` and
  readiness show each server as reachable, auth failed or unreachable; `hub tool add|update` take `--mcp-url`, `--transport` and
  `--header`. Bots use API tokens, never OAuth sign-ins that expire: docs/connect-tools.md says, for Jira, Linear, PostHog, Sentry
  and Trello, whether the vendor's MCP server takes a token or the bot should use its REST API.
- **Copy a bot.** "Copy Ana's backend-reviewer for me" makes an independent bot you own, with the original's instructions,
  skills and tools (no credentials; its notes only if you ask). "Update my copy from the original" merges later changes; "suggest
  this to the original" opens a pull request or a task for its owner. `hub bot copy|update-from-original|suggest-to-original`. The
  original's repository is fetched read-only from GitHub when it lives on another computer.
- **Copy a skill** between bots: `hub skill copy <skill> --from <bot> --to <bot>...`, or ask BotOps.
- **Slack channels in Settings** (Settings → Tools → Slack, `hub slack channel`, or BotOps): which channels bots may read and post
  in is stored in Tico, not in `registry/slack-channels.yaml`; an existing file is imported once.
- Every run's prompt names `hub conversation show` and how to page back; `hub conversation show` returns `has_more` and
  `next_before` (@cold-sats, #9).

### Fixed
- Linux runners installed from a checkout get systemd user units and update themselves (`scripts/tico install` once); Settings says
  so when a checkout runner has no supervisor.
- Browser and Slack connectors find bot repositories through `TICO_PROJECTS_DIR` (@cold-sats, #10), and refuse a bot name that is
  not a single folder name.
- Hermes `doctor` warns when the profile's gateway is not running (it runs scheduled jobs only then) and names chat services that
  bypass Tico's rules.

## [0.2.27] - 2026-09-30

### Added
- **Tico sync for external agents.** A `tico-sync` skill and a scheduled job on the agent itself keep a Hermes or OpenClaw bot in
  step with Tico: each run it checks in, answers its Tico messages, moves its tasks and marks messages read, stops at once when
  nothing waits, and updates its connector once a week. `pair` and `install` take `--sync 15m|1h|daily|<cron>|off` (default 1h);
  on Hermes an empty run costs no model call. `doctor` reports the job and its last run; `uninstall` removes it.
- **OpenClaw profiles can be bots** (`--harness openclaw`), paired the same way as Hermes (docs/openclaw-agents.md). OpenClaw has no
  MCP client, so the connector's new `call` command reaches Tico's tools for it; `check` prints what is waiting.

## [0.2.26] - 2026-09-30

### Added
- **Pair a Hermes profile with a code, no token to copy.** On the profile's computer:
  `curl -fsSL https://<runner host>/api/v2/agents/setup-script -o hermes_agent.py && python3 hermes_agent.py pair --profile <name> --url https://<runner host>`
  prints a code; tell BotOps "connect my Hermes profile <name>, code XXXX-XXXX", or use Pair in Settings → Bots. BotOps registers the
  bot if needed and approves as the human who asked; the credential goes straight to the profile and is never shown. `hub agent pair
  approve|decline`, `hub_agent_pair_approve|decline`. BotOps playbook `connect-a-hermes-profile`. The setup script downloads without a
  sign-in from the runner address.
- **Hermes connector:** `update` (the latest connector, in place), `reinstall`, and `doctor` (checks the wiring and lists old tool
  names in the profile's prompts and skills by file and line). Installs remove older heartbeat jobs for the same profile. An archived
  bot or a revoked credential gets one clear line and a retry once an hour.
- **Restore an archived bot:** a Restore button under Settings → Bots → Archived, `hub bot restore`, `POST /api/v2/bots/{bot}/restore`.
  BotOps restores, rotates and revokes a Hermes bot's credential as the human who asked.

### Changed
- Archiving a Hermes bot revokes its credential by default (a checkbox), and Health flags an archived bot whose agent still reports in.
- A call to a tool renamed in 0.2.21 answers with its new name ("`hub_say` was renamed `hub_message_send`").
- docs/hermes-agents.md leads with the two-minute pairing path, a cron recipe, upkeep and troubleshooting.

## [0.2.25] - 2026-09-30

### Added
- **Bots share credentials when BotOps grants them.** A bot uses only its own credentials and the ones granted to it. BotOps grants a
  stored credential to another bot at once, as the human who asked, when that human is a credential admin (the owner and admins by
  default): `hub credential grant <name> --to <bot>` and `revoke <name> --from <bot>`, and MCP `hub_credential_grant|revoke`. A
  secret that lives only in a bot's own secrets file moves into Credentials with `hub credential import <VAR> --from-bot <bot>`: the
  bot's computer uploads it on its own channel and the value is never shown or logged. BotOps has a share-a-credential playbook.
- **`hub tool update`** (and `hub_tool_update`) changes a bot's tool in place (`--can`, `--note`, `--scope`), as the human who asked.

### Changed
- **Credentials work with no KMS key.** Without `TICO_CREDENTIAL_KMS_KEY` the vault uses a key kept in `/data/credential.key`
  (made once, mode 0600). The backup copies it beside the database whenever it changes, `restore` puts it back, and Health warns while
  it has not been copied. A KMS key, when set, is still used.
- **Task IDs are forgiving.** A unique prefix of 8 or more characters works wherever a task ID does, a near-miss ID gets "Did you mean
  … (title)?" instead of a bare not-found, and listings show `short_id`.
- **BotOps** treats only a human's messages and tasks as instructions (a task a bot created is a record), and closes the Needs-you
  tasks it filed once they are resolved; its daily sweep reviews them.

### Fixed
- Mail and calendar sync no longer block a domain after one passing `unauthorized_client`: it takes 3 errors in a row, then backs
  off from 2 to 60 minutes and resets on the first success.
- A support Confirm card shows the whole message behind a short preview.
- The update status reports as "from" the release actually running.
- Tico HQ: staff stats (`/v1/staff/stats`) give exact installs per release; the public stats keep hiding counts under 5.

## [0.2.24] - 2026-09-30

### Changed
- **Message bots send once their owner turns sending on.** With `outbound_send: true`, a message bot sends without a per-message
  approval to the team's own domains, to the addresses listed in `forward_to:` in its `bot.yaml`, and to the sender of a thread it
  replies to (a reply with no added outside recipients). Anything else still needs an allowance or an approval; daily caps, the
  blocklist and owner-handles-personally still apply. BotOps turns sending on, and sets forward addresses, as the human who asked
  (playbook `turn-on-sending.md`); it never turns sending on unasked.

### Fixed
- **Docker computers have a mail policy.** With no `registry/mail-policy.yaml`, a built-in policy applies: sending on, internal
  domains from `TICO_INTERNAL_DOMAINS` or the bot's mailbox and the roster (never public mail providers), 20 sends a day, one
  outside recipient, no outside Cc or attachments. The Inbox Manager template ships its triage questions. A registry file still wins.
- BotOps changes a bot's tools (`hub tool add|remove`) as the human who asked, instead of being refused as BotOps.
- Message bots are linked to their human at start even when the mailbox is on another domain than the human's sign-in: the mailbox
  comes from the `Mailbox:` line, the bot's gmail tool or its instructions, and the human is the roster match or the bot's owner.
- The server receives `TYPESAFE_API_KEY`, `TICO_TYPESAFE_SECRET_ARN`, `XAI_API_KEY` and `OPENROUTER_API_KEY` from `.env`, so the
  decision model (spam and injection checks on inbound mail) can be switched on.

## [0.2.23] - 2026-09-30

### Fixed
- **Message bots on Docker computers read mail.** Creating a message bot for a human now links it: the human's message bot and
  the mailbox it manages (its `Mailbox:` line, else the human's email) are recorded, whether BotOps builds it, it is added from a
  template or the team builder makes it. Existing message bots are linked at start when it is unambiguous. Every isolated run gets
  the credential socket, so the bot's mail tool uses a short-lived token for its mailbox instead of looking for the key. A bot with
  no mailbox is told so plainly, and owners and admins (or BotOps, as them) change a bot's mailbox with
  `POST /api/v2/access/humans/<id> {"inbox_bot", "mailbox"}`.

## [0.2.22] - 2026-09-30

Fixes from rolling 0.2.21 out and from a clean install on a Mac.

### Fixed
- **The local quick start works on a Mac.** `install.sh --local` and `--runner` run with Docker Desktop (the team install on a
  server stays Linux). The Add computer line on a local install joins the server's Docker network, so the runner reaches it and
  the computer is named "This computer". A local install accepts `localhost` as well as `127.0.0.1`.
- **Local sign-in sticks.** The printed sign-in link keeps you signed in (the cookie is `SameSite=Lax`), and the cookie is named
  per install, so two local installs in one browser no longer sign each other out.
- **First run** asks to sign in to a model only after an AI provider is chosen, and "Add an AI provider" links to it.
- **Message bots on a Docker computer:** `mail.sh` no longer fails with "readonly database" (the mail files are shared between
  the runner and the bot), and Health no longer says the Gmail credential is missing when the computer holds the key.
- **GitHub needs attention** appears only when the GitHub App itself fails, with what to do. A bot whose repository is not on
  GitHub yet no longer marks GitHub unhealthy, and when the App cannot create repositories it says so (Administration is off).
- **Chat:** a message sent right after opening a bot page is no longer dropped when an older snapshot arrives; notices no longer
  cover the composer.
- **Wording:** "Accepts members' bots" and "Bot limit per member" (member is a role); "Settings > Computers" and "groups" in the
  docs and the team chart; Health on a local install notes that copies stay on this computer instead of warning.
- **Security:** PyJWT 2.14.0.

## [0.2.21] - 2026-09-30

One large release: new words across the product, cleaner API names, faster defaults, nested groups and a batch of fixes.
Existing installs update in-app as usual; read **Breaking changes** first if you call the API, MCP tools or `hub` commands
from your own scripts or agents.

### Breaking changes
- **MCP tools are renamed outright** (no aliases) to `hub_<thing>_<action>`, and each caller now sees only the tools it may use.
  The main renames: `hub_say`/`hub_notice` → `hub_message_send` (`fyi`); `hub_inbox` → `hub_message_list`; `hub_ack` →
  `hub_message_mark_read`; `hub_history` → `hub_conversation_show`; `hub_ask`/`hub_answer` → `hub_question_ask`/`_answer`;
  `hub_board`/`hub_task_stuck` → `hub_task_list` (`all`, `stuck`); `hub_goals` → `hub_goal_list`; `hub_goal_auto` →
  `hub_goal_status` (`auto`); `hub_kpi_add` → `hub_kpi_create`; `hub_docs_*` → `hub_doc_*`; `hub_context_search`/`_show` →
  `hub_doc_search`/`hub_doc_read`; `hub_files_*` → `hub_file_*`; `hub_meetings_transcript` → `hub_meeting_read`;
  `hub_bot_register`/`hub_bot_set` → `hub_bot_create`/`hub_bot_update`; `hub_bot_onboarded` → `hub_bot_setup_done`;
  `hub_status_*` → `hub_bot_status_*`; `hub_turns` → `hub_run_list`; `hub_fleet`/`hub_fleet-check` → `hub_health_check`;
  `hub_computers` → `hub_computer_list`; `hub_catalog` → `hub_template_list`; `hub_routine_on`/`_off` → `hub_routine_update`
  (`enabled`); `hub_people_*` → `hub_human_*`; `hub_org` → `hub_team_show`; `hub_updates` → `hub_update_list`;
  `hub_integrations`/`hub_integration`/`hub_queries`/`hub_learn`/`hub_tools_*` → `hub_tool_*`; `hub_batch_*` →
  `hub_needs_you_*`; `hub_listen_*`/`hub_intake_*` → `hub_listening_*`; `hub_decisions` → `hub_decision_ask`. The deprecated
  `hub_judge`, `hub_listen_judge` and `hub_person_*` aliases are gone.
- **`hub` commands** use the same names (`hub message send`, `hub health check`, `hub bot setup-done`, `hub human add`, …). The old
  spellings still work, hidden, for one release and print "renamed to …".
- **REST routes** (`/api/v2`): computers under `/computers` (`/runners/*` is only the runner software's own), `/people` →
  `/humans`, fleet check → `/health/issues`, integrations and connectors → `/tools`, `/judge` → `/decisions`, onboarding and
  getting started → `/setup`, `/goal-proposals` → `/proposals`, `/catalog` → `/templates`. Old paths answer for one release and
  are marked deprecated in the OpenAPI.
- **Bot files and settings.** `employee.yaml` → `bot.yaml`; `schedules:` → `routines:`; `access:` → `tools:`; `HUB_EMPLOYEE` →
  `HUB_BOT`; `departments.yaml` and `department:` → `groups.yaml` and `group:`. Both old and new are read for one release; bot
  templates use the new ones. The status `needs_onboarding` is now `needs_setup` (stored rows are migrated). New bot repositories
  are named `bot-<slug>`; existing `emp-*` repositories keep working.

### Changed
- **One set of words.** Team is everyone, humans and bots; a group is part of the team; teammates are humans or bots. The
  sidebar says Team (the team chart), Built-in (Assistant, BotOps, Librarian, Goal Manager), Message bots; the pages are Humans,
  Tools (formerly Integrations), Computers (formerly Devices) and Routines (formerly Recurring). A bot's status is Needs setup,
  with a Set up button; Finish setup is installing Tico. Needs you means you; anything else says whose it is ("Needs Thomaz").
  Outside agents you connect are external agents. The glossary (docs/glossary.md) is rewritten, and page titles match the sidebar.
- **Faster defaults.** Tico favours getting going; each of these can be tightened again:
  - Tico runs on your own computer with no domain or sign-in setup (`install.sh --local`); a public address still requires sign-in.
  - Create my team works before an AI provider or computer exists; bots are placed when a computer joins.
  - Set up and go-live turn on a starter bot's first routine, with no separate approval.
  - On teams whose owner uses Gmail, members can add coworkers at the team's own domain.
  - BotOps restarts computers, revokes credential grants, changes limits and providers, starts model sign-ins, messages bots, adds
    coworkers in the team's domain and turns on computer sharing for message bots without a Confirm card; the test run before
    going live is optional and it may delete merged branches. The Assistant acts directly on tasks, comments and messages to bots.
    Five owner switches under Settings > Humans bring the cards back.
  - New computers take members' bots; admins store credentials and see SQL; members make personal tokens.
  - Sign-in lasts 30 days idle and 90 days in all (`TICO_SESSION_IDLE_SECONDS`, `TICO_SESSION_ABSOLUTE_SECONDS`).
  - Tasks may contain outside links. Bots may start 10 conversations a day with a human (`TICO_UNSOLICITED_PER_DAY`). A bot
    reaching for another bot's files is quarantined on the 3rd try in a day (`TICO_ESCAPE_QUARANTINE_AT`).
  - Slack posting is on for registered internal channels unless a channel says `post: false`; calendar invites may include
    outside guests (`TICO_BLOCK_EXTERNAL_INVITES=1` restores the old rule). Undo and archive no longer ask to confirm.
  - A task filed from an email sent to a message bot may carry the message's text (other recipients and quoted history stay out).
  - Unchanged on purpose: nothing is sent outside the team unless sending is on; credentials are never shown, logged or pushed;
    outside humans, sign-in rules, updating Tico and deleting bots or repositories still need a Confirm card.

### Added
- **Nested groups.** A group is part of the team and may hold groups; its members are humans and bots. The team chart shows
  groups as nested sections that owners and admins add, rename, drag teammates into and nest. `GET/POST /api/v2/groups`,
  `PATCH/DELETE /api/v2/groups/{id}`, `hub group list|update`, `hub_group_list`, `hub_group_update`. Existing teams, org groups and
  departments become groups on first start, and the team builder puts each bot in its template's group.
- `install.sh --runner --name <name>` adds another computer on the same host (a message bot needs its own): its own
  directory, compose project, container, home volume and updater. The updater's helper container is named per project so two
  updaters on one host do not remove each other's.
- A computer whose model is not signed in takes the team's model key from Credentials (granted to "Every computer") and signs in
  by itself; a team that uses a subscription login sees "<computer>: sign in to <model>" in Health.

### Fixed
- **Mail and calendar sync target the message bots' mailboxes.** The `connectors` job used every human's roster sign-in address, so a
  team that signs in on one domain and runs Google Workspace on another failed on every mailbox the key could not act for. It now
  syncs each message bot's declared mailbox (the `gmail` identity in `bot.yaml`, from the `Mailbox:` line), and a human's own email
  only when the bot declares none. A domain the key cannot impersonate is skipped and shown once in Health.
- **Slack gateway.** Sign-in settings now reach it, fixing a crash loop on Cloudflare Access and built-in sign-in installs. Routing
  works with any number of bots (the decision questions go in batches), and a message that cannot be routed ends failed and the
  human is told in the thread instead of looping.
- Settings and Tools forms keep what you type while the page refreshes (GitHub, Slack, meeting importers, Add computer, API tokens,
  Humans, Routines).
- A message typed into a bot's chat while it is still loading is sent, not lost.
- A bot placed on or moved to a computer gets its repository cloned there. When it cannot be, the bot's readiness says why (not on
  GitHub, token refused) and `hub health check` lists it; a move that would strand an unpublished repository is refused, and the
  old computer no longer shows a moved bot as ready.
- A new Docker computer lets its message bot build the mail tools on first use (`workspace/runtime/mail` is the bot's).
- The runner's repo-escape check matches only real bot repositories, so ordinary text such as `bot-xyz/` is not refused.

## [0.2.20] - 2026-09-30

### Fixed
- **"Tell me issues to solve" works for BotOps.** A bot other than the Assistant that asked for `hub fleet` (the Assistant's
  live snapshot) was refused ("available only to people and the Assistant") and BotOps reported it could not check the bots.
  `hub fleet` and `hub_fleet` now answer everyone else with the fleet check (`hub fleet-check`): what is wrong with the bots
  they may see, most urgent first.

## [0.2.19] - 2026-09-30

### Added
- **BotOps finishes the job.** A person asks in chat and BotOps does what they could do in the app, as them: any v2 route through
  `hub api <METHOD> <path> ['{json}']` (the server answers as the requester, so a member is refused what only an owner may do and an
  owner is not; reads are the requester's reads), and friendly commands for the common ones: `hub bot place|go-live|model|pause|resume`,
  `hub routine on|off`, `hub person add`, `hub computers`, `hub fleet-check`, `hub support file`. What always needs their click (adding
  people and admin changes, deleting, computers for members, spending limits, providers, updating Tico, a message in their name) comes
  back as one Confirm card, never two; a member is told at once when only an owner or admin may ask. A secret never travels in a
  `hub api` body, and tokens, enrollment codes, approvals and ownership transfer are not reachable at all (docs/permissions.md).
- **An active bot always has a computer.** A bot that becomes active without one (added active, turned on, resumed, built by BotOps) is
  put on the company's only computer, or the least busy online one that takes it; a member's bot goes on its member's computer or one
  opened to members' bots. With none that takes it the answer says so and the scheduler places it when one can. `hub bot go-live` places,
  turns on and starts a starter bot's setup. A bot that is only set up, not yet on, reads "Setting up" everywhere a person sees it.
- **A credential card in the chat.** A bot that needs a secret opens a card where it asked (`hub credential request <VARIABLE> --for-bot
  <bot> --label ... --format ... --help-url ...`): what it is for, the exact format as the placeholder, a "Get one" link and Save. The value
  goes from the browser straight to Credentials under the variable's name and is granted to that one bot; the bot is woken with "Saved" and
  tests the connection. It is in no message, event, receipt or log, and never reaches the model. A wrong shape is refused without echoing
  it; only the person asked, or a credential admin, can fill it (docs/credential-vault.md).
- **`hub credential set`, and a pasted secret taken out of the chat.** BotOps stores a secret a person pastes (the value on standard input,
  never the command line), as them and for the bot they name, and the pasted words are replaced by `•••• saved as <VARIABLE>` in their
  messages, the run's recorded events and the retry receipts; later events of the run are scrubbed as they arrive. `hub credential list`
  shows names, variables and grants, never a value; `hub message redact <id>` does one message.
- **The runner scrubs granted secrets.** Each turn's granted values (as typed, URL-encoded or base64) are masked with `••••` in every event,
  the final reply and the runner log, and in the text files the turn changed; a commit that holds one is not pushed or published.
- **`hub fleet-check`.** Bots with no computer, computers offline, failing runs, a login a bot needs, setup that never finished, bots paused
  (or over their spending limit) or stopped, most urgent first, each with the one command that fixes it. `GET /api/v2/fleet/check`,
  `GET /api/v2/computers`.
- **The Tico manual in every install.** The release's `docs/*.md` is a read-only docs collection, indexed by heading and kept apart from
  company docs (no write routes, not in the company list). `hub docs search` lists its pages after the company's, labelled "Tico manual"
  with the file and a link at the release tag; `--manual` restricts to it and `hub docs read manual:<name>` reads a page. The Librarian
  and BotOps cite it. `GET /api/v2/docs/search?collection=company|manual|all`, `GET /api/v2/docs/manual[/{name}]`.
- **Built-in bots follow the release.** A built-in bot's instructions and playbooks (`AGENT.md`, `playbooks/`, `skills/`) are refreshed
  from the template when the template changed, once per runner start and before the bot's turn; a stamp keeps the digest, so what the
  bot improved stays until the product changes that file, and what it had is kept in the repository's history. Older installs are brought up once.
- **A spam and prompt-injection check on inbound support.** HQ judges each new ticket (`legit`, `spam`, `injection_risk`, `unchecked`) with
  the company's decision-model provider when `HQ_JUDGE_KEY` is set (PRIVACY.md names it); no key sends nothing and every ticket is `unchecked`. Spam is held out of the queue
  (`GET /v1/staff/tickets?status=held`) until staff release or correct it (`POST /v1/staff/tickets/{id}/verdict`, recorded); an
  `injection_risk` ticket is filed with a warning and the Support Agent handles it read-only. It fails open after 3 seconds and logs only the
  verdict and reason. `gh-support` screens issues and Discussions through `POST /v1/staff/judge`, and `hub classify` does the same for inbound
  email (docs/support.md, PRIVACY.md).
- **BotOps evals.** `evals/botops/` has six scenarios (build a Jira-like bot, read-only on GitHub, why isn't it live, tell me issues to
  solve, a member asks for an owner-only change, change a model), a scorer (done, person steps, jargon words, duplicate messages) and
  `run.py`, which drives a real BotOps on a dev install on demand and never in CI. A scripted layer replays each through the real tools with no model.

### Changed
- **BotOps acts, then reports.** Its instructions and playbooks now say: do what is reversible and within the requester's rights, then say
  what was done; ask only for a secret (the card), money, something irreversible or an outside send. One short message in plain words,
  ending with at most one next step, and no internal terms. "Tell me issues to solve" runs the fleet check and fixes what it can. It never
  sends a person to a settings page for what a command does; if the product cannot, it files `hub support file` (a card shows the exact
  words). `build-me-a-bot` ends with the bot live: built, on a computer, its logins, turned on, setup started, one test, one report.
- **Supporting BotOps in the app.** A support message BotOps drafts is sent only when the person confirms the card that shows it; the
  Librarian answers "how do I..." from the manual.

### Fixed
- Watchers no longer mask ordinary words in what they report: the bot's own name, a repository or a URL in a ticket title reached the task as "[redacted]". Only values from secrets files and variables named as secrets are masked.
- **Credentials could not be added from the app.** `#/credentials` led to Integrations, whose "Add credential" needed a vault that nothing
  loaded. Owners and credential admins now see a Credentials section on Integrations with Add, Edit and Grant access.

## [0.2.18] - 2026-09-30

### Added
- **Usage: estimated model spend per bot.** Account menu > Usage shows what the bots' runs cost, for Today, 7 days, 30 days, This month
  or a custom range, by department, one row per bot with its tokens, estimate and share; a bot opens to a daily chart, its top routines
  and a CSV. The runner sums each run's tokens (input, cached input, output) and sends them with the result; the server prices them from
  a per-model list-price table (`providers.PRICES`, dated `prices_as_of`). A model with no price shows its tokens and a dash. Runs on a
  ChatGPT or Claude sign-in are shown as "API-equivalent" and never added to spend. `GET /api/v2/usage` (owner and administrators see
  every bot, anyone else the bots they run) is in the v2 contract; the bot KPI `cost_7d` reads the same estimate (docs/usage.md).
  Two migrations add the token columns on `turns` and the limits tables.
- **Spend limits per bot.** A daily and a monthly limit in estimated USD, on the Usage row or in Settings > Bots, with a company default
  (Usage > Default limit; none until set). At 80% the bot's operator is told once; at 100% the bot takes no new job until the period
  turns over or the limit is raised ("Paused: over its daily limit", shown on the bot and in Tasks), and a run in progress finishes.
  Subscription runs count only if the company opts in. The person who runs a bot sets its limit within the company default.
- **Support diagnostics.** Contact support has an "Attach diagnostics" box, on by default, with a Preview link that shows exactly
  what will be sent: versions, containers, the last update, health check names, each computer's runtime readiness, counts and the recent
  WARN/ERROR log lines, with emails, keys, addresses, hostnames and the names of bots and people redacted. HQ keeps it with the ticket
  (`tickets.diagnostics`), for staff only, and `hq-tickets show` prints it for the Support Agent. PRIVACY.md lists every field. A name or
  slug is relabeled as a word from four characters; a shorter one (`coo`) only as an exact `bot:<slug>` or `human:<id>` reference, and
  an email is always relabeled or redacted, so a short slug no longer changes ordinary words.
- **Rehearsal mode.** `TICO_REHEARSAL=1` starts a server on a copy of real data for trying a migration. Migrations and
  initialization run as usual; nothing runs on a timer and nothing leaves the server: no scheduler or directory sync, no
  backups (no Litestream or replication loop, and nothing is written to `TICO_BACKUP_URL`, so restored production data cannot
  write into the production replica), no release check or usage count, no contact support or HQ calls (Contact support reports
  "rehearsal" as the reason it is off), no Slack gateway, GitHub App calls, error reporting or updater, and no uploads to an
  attachments bucket. A banner on every page says "Rehearsal: nothing runs or leaves this server", and the config carries
  `rehearsal: true`. See docs/install.md, "Rehearse a migration".
- **A bot's org chart carries each bot's department.** `hub org` (and `hub_org`, `GET /api/v2/org`) gives every bot the caller may see
  its `reports_to`, its `department` (its team, else its template's department, else its manager's) and its `template`, so a head of
  a department can tell which bots are on the team. Bots still follow each bot's See permission.
- **A bar offers a reload after an update.** A tab left open through an update shows "New version · Reload" when the server's release
  or the build of its script and stylesheet (`ui_build` in the config) differs from what the page loaded with. It checks on the
  config poll and the moment a background tab is shown again, and never reloads by itself.

### Changed
- **The UI is split into files and served as one script and one stylesheet.** `ui/index.html` no longer holds the inline scripts and
  styles: the code is in `ui/app/*.js` and `ui/styles/*.css`, one file per area, and the server concatenates the files index.html lists
  between its bundle markers into `app.bundle.js` and `app.bundle.css`, with the content hash in the URL. A browser fetches each once
  per release (immutable, gzip), so a page loads as fast as before or faster, on a phone too; editing the files needs no build step, and
  `TICO_UI_BUNDLE=off` serves them separately. See ui/README.md.
- **Mac helper jobs follow a release.** `connectors`, `close-calls` and `importers` are separate launchd jobs, and a release
  restarted only the bot job, so a helper kept the old code in memory while it loaded new modules and scripts from the switched
  checkout. After a healthy update the runner now restarts the helper jobs that are installed (`launchctl kickstart -k`, the way
  `scripts/tico restart` does; a job that is not installed is left alone), and each helper checks the checkout's revision about
  once a minute and exits with status 0 when it changes, so launchd starts it on the new code. Every restart is logged. A helper
  now also stops between mail batches and calendar actions on a stop signal. Docker runners are unchanged: the container is replaced.
- **Open-source basics.** SECURITY.md now names supported versions (the latest release) and what to expect from a report;
  CONTRIBUTING.md covers running the tests, releases by tag and a DCO sign-off (`git commit -s`, no CLA); a Contributor Covenant 2.1
  CODE_OF_CONDUCT.md, issue forms (bug report, feature request), a pull request template and a Community section in the README
  were added.

### Fixed
- **`hub updates` and `hub update ...` work for a bot.** The CLI parsed them, but the remote handler had no branch for them, so a bot
  reading the daily or weekly updates got "This command is not supported by the remote API" (the `hub_updates` tool over MCP was
  fine). They now run the same tools; `hub update list` reads like `hub updates`. Marking updates read and replying stay a person's.
- **The Docker server no longer overrides `TICO_SCHEDULER=0`.** The entrypoint forced `TICO_SCHEDULER=1`, so an explicit off was
  ignored (and compose did not pass the variable to the container at all). Unset still means on. compose.yaml now also passes
  `TICO_SUPPORT`, which docs/support.md already told owners to set in `.env`.
- **The HQ backup runs as the HQ image's user.** Litestream as root with no capabilities failed with "stat /data/hq.db: permission
  denied"; `hq/compose.yaml` now sets `user: "10005:10005"` on the backup service.

## [0.2.17] - 2026-09-30

### Added
- **Contact support, from the app.** Help > Contact support sends the Tico team a message: up to 4000 characters, an email for a
  reply (prefilled, removable) and "Include version and install ID" (on by default). The form shows in one line exactly what it will
  send. **Your requests** lists each ticket with its status and thread; a small notice and a dot on the Help `?` appear when the team
  replies, and you can write back or delete a request. It is only ever sent when you press Send, never in demo mode, and the anonymous
  count switch does not turn it off, because it is a message you chose to send (`TICO_SUPPORT=off` removes it). Tickets are kept at HQ
  until deleted; PRIVACY.md and docs/support.md say what is sent and kept.
- **HQ takes tickets.** `POST /v1/support` (a per-ticket secret comes back), `GET /v1/support/{id}` for status and replies, and staff
  routes behind `HQ_STAFF_KEY` to list, reply, set the status and delete. Strict input, rate limits in memory, no body or address
  logged, plain text throughout (docs/telemetry.md).
- **Watchers: a bot can be woken by a program instead of a model.** A `watchers:` entry in `employee.yaml` names a program in the
  bot's repository; the runner runs it as the bot on a schedule (1 minute to 24 hours, a timeout, never overlapping, no hub token,
  secrets removed from what it reports) and the hub opens a task, or wakes the bot on the task it has, only when the program prints
  something new. Failed or stopped watchers show in Settings > Health. BotOps has `set-up-a-watcher` (docs/watchers.md).
- **The Support Agent works HQ tickets and GitHub threads.** Its two watchers run every 5 minutes with no model: `hq-tickets` opens a
  task per ticket and adds a note when the person writes again; `gh-support` does the same for a repository's issues and Discussions,
  read-only. Replies are drafted, approved by a person, then posted by `software/hq-tickets reply` only for the exact approved text.
  Both do nothing until configured (docs/support.md).
- **First run: an optional "Your name" on the Names step.** It is saved on the owner's roster entry, so the org chart and the sidebar show
  a name and not `ana@example.com`. It is prefilled from the roster, or from the display name Cloudflare Access or the AWS load
  balancer vouches for (`name` claim; `sign_in_name` in `/api/me`); left blank, nothing changes.
- **First run: the computer step opens on "A Linux or cloud server (Docker)"** when the server itself runs in Docker (`in_docker` in the
  config), and an online computer folds the step to one line, `<label> online`.
- **Codex signs in with an API key by itself.** With `OPENAI_API_KEY` in `secrets/_shared.env` (or the runner's environment), a computer
  with a Codex bot runs `codex login --with-api-key` as the bot user, the key on standard input and never in a command line or a log.

### Changed
- `install.sh --runner` given `--code`, `--url` or `--label` replaces those keys in an existing `.env` and says so, and keeps every
  other setting; with none of them it still only repairs and updates. `--url` and `--code` are needed only when there is no `.env`.
- The sidebar stays fixed at the full height of the window when the page scrolls, and its Helpers group lists the Assistant, BotOps,
  the Librarian and the Goal Manager, as the Goals page does.
- The bot page's **Needs onboarding** card is the mark and the **Start setup** button, with no paragraph.
- **More in <department>** in the org builder is one full-width button, keyboard operable, that stays open through a redraw.

### Fixed
- **Start setup showed nothing.** Its message went to the person's own chat with the bot, but a Chat tab that had loaded empty kept saying
  "Nothing yet". Start setup now hands the answer to the open chat, and an empty chat looks itself up again.
- **A Start-setup turn went off script.** The generic chat rules ("do not end with a plan", "file each ask as a task", the
  "Human chat response contract") were read as a request for work, so the Support Agent edited its own AGENT.md, ran `mail.sh whoami`
  (which its onboarding playbook asked for) and filed tasks about the failure. While a starter is `needs_onboarding`, a person's chat
  turn now gets one Setup instruction instead: follow the template's onboarding, ask the questions and stop, and touch no tool, task or
  file before the answers. The Support playbook no longer tests mail before the person says where support arrives.
- **`PUT /api/v2/onboarding` took about 20 seconds at full CPU with 23 bots.** Each selected bot re-parsed all 94 catalog cards; the
  cards are now parsed once per change of the catalog files, so a save takes milliseconds.
- **The Assistant was named after the company** ("Tico Team" in Review) because the container defaulted `TICO_ASSISTANT_NAME` to
  `TICO_COMPANY_NAME`. It defaults to `Assistant`.
- **The mail tool failed on first use in the runner image** (`mkdir: cannot create directory /opt/runtime`). A turn is given
  `TICO_PROJECTS_DIR`, and the venv goes under the runner's workspace, which the bot user can write.
- **The Codex login home** (`~/.codex`) was made by the supervisor at mode 755, so the bot user could not write `auth.json`, and a login
  made as the bot user (mode 600) could not be read by the supervisor. It is now bot-owned, setgid and group-writable on every start,
  and the login files are group-readable.

## [0.2.16] - 2026-09-30

### Added
- **First run builds your org chart, one department at a time.** Step 3 is now **Your org chart**, in place of the starter team
  and full org chart. "What departments do you want?" offers nine tiles: Sales, Marketing, Customer Support, Finance, Operations,
  Legal, HR, Product and Engineering (Product and Engineering start picked only when software is the product). Then, for each: its
  icon, a one-line description and goal, one question ("What kind of sales do you do today?") and a one-line answer. **Recruit
  bots** shows "Recruiting bots…", then the suggested bots as cards to check, each with its avatar, summary and why it fits. The
  head and the `default` cards start checked, `common` ones are shown and the rest are under **More**; Back and Skip department are
  always there. The chart grows beside it (a strip above the card on a phone): the owner, each department and its bots, the head
  first. On the finished chart ("5 departments · 11 bots") a click renames a bot or changes who it reports to. Each head reports
  to the owner and each other bot to its head. The departments and answers are saved as `answers.departments` and
  `answers.briefings`, and reach BotOps's setup tasks. Create is unchanged. See [First run](docs/onboarding.md#the-org-builder).
- **Suggestions from Tico HQ, with a local fallback.** `GET /api/v2/onboarding/departments` serves the departments and cards, and
  `POST /api/v2/onboarding/recruit` one department's suggestions. While the card's toggle "Suggestions from Tico HQ (sends this
  answer)" is on, the server sends the department, the answer, three facts from About, the catalog version and (once the usage
  count's notice has been shown) its install id to `POST <TICO_HQ_URL>/v1/recruit`, waits at most 6 seconds and keeps only its own
  template ids. Demo mode, `TICO_TELEMETRY=off`, `DO_NOT_TRACK` and the usage count's setting turn the toggle off. Otherwise, or on
  any failure, `backend/recruit_rank.py` ranks on the install with no network. HQ stores none of it ([PRIVACY.md](PRIVACY.md)).
  Tico HQ's `/v1/recruit` (`hq/recruit.py`) checks its input strictly, ranks with GPT-6 Luna under a JSON schema of template ids,
  caps model calls a day (`HQ_RECRUIT_DAILY_CAP`), falls back to the same local ranking, caches answers in memory for an hour under
  a hash, never logs a body, and limits requests per address and per install id. `scripts/build_catalog_json.py` writes HQ's
  `hq/catalog.json` and its copy of the recommender; its `--check` fails when either is stale ([Tico HQ](docs/tico-hq.md)).
- **94 bot templates by department, each a real job title with an icon.** Sales (8), Marketing (14), Customer Support (10),
  Finance (11), Operations (11), Legal (8), HR (11), Product (8) and Engineering (10), a Leadership extra (Chief of Staff and
  Strategy Analyst) the picker does not offer, and one helper. 57 are new, among them Account Manager, Sales Engineer, Paid Media
  Manager, Retention Specialist, Accounts Payable Specialist, Payroll Specialist, Vendor Manager, IT Support Specialist,
  Dispatcher, Paralegal, Privacy Manager, Sourcer, Product Manager, Security Engineer and DevOps Engineer. Each does the work and
  prepares the action; a person's approval sends, posts, pays or signs, and every template still ships `outbound_send: false`,
  read-only access and a paused, draft-only first routine.
- **Department heads that hire.** Each department has one head (`lead: true`): Sales Manager, Head of Marketing, Head of Customer
  Support, Head of Finance, Operations Manager, General Counsel, Head of People, Head of Product and Head of Engineering. A head
  lists its department in `team_templates` and, when recurring work is not covered, proposes a specific worker with the reason and
  its first routine; it asks BotOps to set it up only after the owner confirms.
- **`templates/departments.yaml`**: each department's id, name, description, goal, briefing question with an example answer,
  icon, head and `software_only`. New card fields: `department`, `icon`, `tags`, `suggest` (`default`, `common` or `niche`),
  `team_templates` and `kind` (`helper`). Every card, built-in and department has a Material Symbols icon, and
  `scripts/build-icon-font.py` adds each to the app's subset font (144 glyphs, about 17 KB). The catalog test checks every card's
  department, head, icon, `suggest` and `tags`; [Starter bots](docs/starter-bots.md) lists every template by department.

### Changed
- **Bot avatars are soft blobs that breathe while the bot works**, with the template's icon inside. Every bot avatar (sidebar, bot
  page, chat, Settings > Bots, tasks, Updates, Goals, the org builder) is a slightly organic outline drawn from the bot's slug, so
  each bot keeps its own shape everywhere; people stay plain circles. While a bot runs a turn or answers in the open chat, its outline
  eases to a second shape and back; with reduced motion it stays still. `/api/employees` carries each bot's `icon`, from its definition or
  its template's card.
- **Helpers are not roles.** Roles are real job titles on the org chart; helpers (Assistant, BotOps, Librarian, Goal Manager and
  the Inbox Manager) have plain function names and live outside it. The `inbox` template is now the **Inbox Manager** (it was Mail
  Drafts; slug unchanged), a `kind: helper` card in no department. The org builder offers it under **Helpers** below the finished
  chart, off by default, with the mailbox picker; the chart itself shows no helpers. The sidebar lists helpers in a Helpers group
  after the org tree, and `/api/employees` marks a bot made from a helper card with `helper`; `reports_to` is unchanged.
- **Existing templates take job-title names** (slugs unchanged): Sales Lead is the Sales Manager, Issue Triage the QA Engineer,
  PR Reviewer the Senior Software Engineer, Spend Watcher the FP&A Analyst, and so on. Sales Drafter (`sales`) is rewritten as the
  Account Executive, which also takes Proposal Writer's proposals and RFP answers.
- **Tico HQ's address is now `https://updates.tico.team`** (was `hq.tico.team`, which never went live). It is the default
  `TICO_HQ_URL` and the address PRIVACY.md and docs/telemetry.md name. An install on 0.2.15 asks the old address, gets no answer,
  and checks GitHub directly until it updates.
- **Goals is one dense tree, like the org chart.** Every person and bot (goal or not) is one line, indented under whoever they
  report to, with the goal to the right (cut short, whole in the tooltip) and its KPIs as chips; helpers sit apart. The **+ Goal**,
  **+ Company goal** and **+ KPI** buttons are gone: tapping a line opens that owner's panel, where goals, colours, KPIs, targets,
  readings and check-ins are added and edited. Needs you is a short strip on top.
- **Connect an agent says what it does and walks each agent through it.** One line says what a connected agent can do,
  then a tile per agent with its mark: Grok, Dots, Muse, Claude, Cursor, Codex and Other. Picking one shows only its steps:
  the MCP server URL, **Create token** (named after the agent and the day, shown once), that agent's two to four steps with
  its menu path, and blocks to paste with the URL and token filled in (a Grok Bot's request and `grok mcp add`, Muse Code's
  `settings.json`, `claude mcp add`, Cursor's `mcp.json`, `codex mcp add`, and an `mcp-remote` config for anything else). The
  dialog turns to **Connected** when the agent's first call reaches Tico, and lists existing connections with when each was
  last used and **Revoke**. The token lives only in the dialog: never stored, logged or put in a URL. When Cloudflare Access
  guards the MCP URL, the dialog says to give `/api/v2/mcp` a Bypass policy (`GET /api/v2/agent-skill` now returns
  `access_bypass`). Muse, Claude and Dots show letters rather than marks: Meta and Anthropic allow theirs only with approval,
  and Dots has none. ChatGPT signs in to MCP servers with OAuth only, so it cannot use a token and has no tile; Dots reaches
  apps through ChatGPT's plugins and is expected to be the same. See `docs/connect-an-agent.md`.
- **Settings > People is one choice and one list.** At the top: **Add manually** or **Sync with directory** (a saved directory
  source means Sync; going back to manual stops the sync and keeps the people it added). Manual is one inline row, an email and an
  optional name. Sync shows the source, its last sync and **Sync now**, with the filters, the interval and the "ask me first if
  more than 10 would be marked as left" number under **Options**; every sync is still previewed and confirmed as before. **Anyone
  at <domain> can sign in** is one switch in place of the Who can join box: it adds or removes the company domain in
  `allowed_domains`. Nothing stored is rewritten: other domains and addresses already on the list keep working and show as chips
  the owner can remove, and a domain typed into the add row is added to the list. Each person is one row with their role (the
  owner switches Admin and Member in place), a **Can sign in** switch and a ⋯ menu (Can add bots, Can add people, Make owner,
  Mark as left, which keeps its confirm); the title and team column and the Edit dialog are gone, and everything saves when it
  changes. The Cloudflare Access sentence is gone: Tico does not change the Access policy, so after an add one line says "Also
  allow them in Cloudflare Access" (or your Cognito user pool), linking to docs/people.md.
- **Can sign in is a per-person switch.** Off keeps someone on the roster and the org chart but refuses their sign-in, their
  browser sessions and their API tokens until it is on again (`sign_in` on `POST /api/v2/access/people/{id}`). Owners and admins
  switch it for members, only the owner for an admin, nobody for themselves or the owner, and BotOps only through a Confirm card.
  Someone with it off cannot be made owner.
- **The bot limit per member defaults to 25** (it was 5). On upgrade, a company whose stored limit is 5 moves to 25 once: the old
  page saved the limit with every allow-list save, so a stored 5 cannot be told apart from the default. A 5 someone set by changing it
  from another number (an `access.limits_updated` event) stays, and so does any other number.
- Bot pages no longer show the automatic KPI tiles; link a bot's KPIs to a goal from the Goals page.
- The Done list on a bot page shows its focus ring only for the keyboard.
- **Settings > Recurring and Settings > Bots filters fit on one line**: a search box and compact menus with no labels
  beside them (each menu names itself, e.g. "All bots"); two menus a row on a phone.

### Removed
- **The Getting started checklist** (its page, its sidebar entry and its Help link; `#/getting-started` now opens Tasks),
  and the intro cards on Updates, Tasks and Goals. **Finish setup** is the only setup entry in the sidebar, while the first run
  is unfinished. The card slot above the page is gone too: the market research box is now the Market page's own empty
  state (one box, **Start research** and **Attach files**; "Nothing here yet." for everyone but the owner), and the
  "researching" notice fills the same place until the market has content; `GET /api/v2/getting-started` no longer
  returns `empty`. The tour stays.
- **The bot card in the sidebar** ("No bots of your own yet.", with **Connect a bot you already have**, **Build one with
  BotOps** and an X), its **What should your bot do?** dialog, `POST /api/v2/getting-started/bot` and card dismissal
  (`card` on `POST /api/v2/getting-started/state`, `cards_dismissed` on the read). While there are no bots of your own, one
  muted line under the org list says "Talk to BotOps to add or edit your AI employees", linking to BotOps's chat.
- **The Docs setup card** ("Where do your current docs live?") and `POST /api/v2/getting-started/docs`. The Docs page's
  own empty state already offers writing a doc, importing and adding a link.
- **The integration pages that were specific to one company.** A new company's **Integrations** page listed dozens of
  services one company happened to use. The release now ships a page only for an outside service Tico has built-in support
  for: GitHub, Slack, Mail (Gmail and Google Calendar), the Aside browser, Close (the call importer behind Meetings), and the
  databases `hub db` reads (PostgreSQL, MySQL, MongoDB, SQLite). Gone: AWS, Brex, Bright Data, Calendly, Click2Mail,
  ElevenLabs, Gemini, Geocodio, Google Ads, Google Search Console, HeyGen, LinkedIn, Mercury, Meta Ads, OpenRouter, Pangram,
  Peec, PostHog, Postiz, QuiverAI, Reddit, Sentry, Stripe, Upfluence, Web search, X, xAI and the decisions provider. Tico's own features are no
  longer listed as integrations: Credentials is `docs/credential-vault.md` (now with the secrets files, 1Password references and
  `scripts/vault-sync.sh`), the hub database is `docs/hub-sql.md`, files are `docs/files.md`, and decisions are
  `skills/decisions/SKILL.md` (now with its limits and what the audit keeps). A company adds its own pages, and their query
  catalogs, in `<TICO_REGISTRY_DIR>/integrations/` (or the folder `TICO_INTEGRATIONS_DIR` names); they are listed beside the
  shipped ones, and a page with the same name replaces the shipped one (`integrations/README.md`, `docs/databases.md`).
- The starter team and the full org chart: `choose()`, `full_chart()` and `recommend()` in `backend/onboarding.py`, and
  `recommended`, `recommendations`, `full_chart`, `held_back` and `pain_options` in the onboarding record.
- The Proposal Writer template, folded into the Account Executive.

### Fixed
- An integration page whose query catalog holds MongoDB entries no longer fails to open; the entry shows as JSON. The
  Integrations page shows the filter only when the list is long, and says "No integrations." when there are none.

## [0.2.15] - 2026-09-30

### Added
- **KPIs are records of their own, and goals are coloured from them.** A KPI has a name, definition, unit, direction (`up`,
  `down`, `range`), cadence (`daily`, `weekly`, `monthly`), one accountable owner (the company, a person or a bot), a source note
  and a `definition_version` that goes up when what it measures changes. A goal links to zero or more KPIs and one KPI can serve
  several goals; the target lives on the link, either an improvement (baseline, target, deadline, paced in a straight line) or a
  maintenance range (min and/or max). A reading is a fact: the value, the period it describes (`period_start`, `period_end`) apart
  from `collected_at`, an evidence link or note, a quality (`measured`, `estimate`, `partial`) and the definition version. Readings
  are never edited; a correction is a new reading that `supersedes` the old one. A KPI is fresh, stale (one period missed) or missing
  (never read, or two or more periods missed); missing is never zero and shows gray. The colour of a KPI is arithmetic: green on
  pace or inside the range, yellow within 10% of the needed pace or near an edge, red further behind or outside, gray with no fresh
  data. See `docs/goals-and-kpis.md`.
- **Goal colours are automatic, and a person can override.** A goal's colour is worked out from its KPIs (the worst of them, with a
  one-line reason such as "Activation 52% vs 58% needed on pace" or "Paying studios 148 vs 135 studios needed on pace": a word
  unit said once, counts as whole numbers, percentages and months to one decimal), or from its owner's check-ins and its tasks when it has none,
  or gray "no data". A colour a person sets sticks: it shows "set by <name>" with their note until a person chooses **Let Goal
  Manager set it** (`hub goal auto`), and the automatic pass only ever *suggests* a different colour on such a goal. `goals` and
  every `goal_events` row now say who set a colour (`status_by`) and how (`status_source`, `auto` or `person`). Automatic colours
  are worked out again on every reading, target, link and check-in, by the Goal Manager's status pass, and once an hour.
- **The Goal Manager**, a fourth built-in bot (with the Assistant, BotOps and the Librarian), created for every company and for existing
  companies on update once a computer and a model exist. It is the steward of every KPI: one folder per KPI in its repository
  (`kpis/<slug>/`: definition, sources, the query or script, a known-values check, a changelog), one scheduled pass that computes
  each KPI with a time budget and posts readings with evidence, stale and missing marked, a failing KPI skipped and reported. It
  also checks that goals make sense (vague, duplicate or unmeasured goals, as proposals), asks a goal's owner what is happening
  when a KPI slips and records the answer as a check-in, and sends the owner a short weekly goals review. It cannot change a
  definition or a target it is judged against: those are **proposals** the goal's or KPI's owner confirms. Its routines start
  paused; the daily KPI pass starts once the first KPI exists.
- **Automatic bot KPIs** computed from Tico's own data, no steward needed: tasks completed (7 days), median time to first response,
  approval rate, failed runs and model cost. A row on each bot's page shows them, and they link to goals like any KPI
  (`auto:<bot>:<metric>`).
- **The Goals page shows the KPIs.** Each goal has its colour dot and note, its KPIs inline (dot, name, latest value and period,
  target, sparkline), a panel with the history, evidence, definition and version, owner and the Goal Manager's latest check-in,
  **+ KPI** on each goal and at the bottom, an **Other KPIs** list, and **Needs you** for red KPIs on goals you own, stale data on
  KPIs you own and definitions or targets waiting for your confirmation.
- **API, `hub` and MCP.** New stable v2 routes for goals (list, tree, get, status override and hand-back, refresh, check-ins,
  needs-you), KPIs (list, get, create, update), readings, links and targets, bot KPIs and proposals, all in `docs/openapi/v2.json`.
  `hub goal auto|refresh|checkin|checkins|needs-you`, `hub kpi list|show|add|update|link|unlink|log|readings` and
  `hub proposal create|list|decide`, with MCP tools of the same meaning.
- **An anonymous count of active installs**, so the project can tell whether Tico is used, in the way Homebrew and Next.js
  do it: on by default, a first-run notice, four ways off, a debug mode, and public results. The update check asks Tico HQ
  (`https://hq.tico.team/v1/latest`, `TICO_HQ_URL`) with exactly four fields: a random install ID, the version, and two
  yes/no flags (a person used Tico in the last 7 days; a bot turn finished in the last 7 days). Nothing else is sent, no
  IP address is stored, and with counting off, when HQ does not answer, or with `TICO_RELEASES_URL` set, the check goes
  straight to GitHub as before. Turn it off with Settings > Privacy (which also has **Reset install ID**),
  `TICO_TELEMETRY=off`, `DO_NOT_TRACK=1` or `python -m backend.manage usage-count DB off`; `TICO_TELEMETRY_DEBUG=1` prints
  the payload without sending it. The owner sees a one-time notice in the app, the installer and the setup wizard, and
  nothing is sent before the app has shown it. Demo mode never sends. See [PRIVACY.md](PRIVACY.md) and
  [docs/telemetry.md](docs/telemetry.md).
- **`hq/`, the collector**, a separate service (its own FastAPI app, SQLite database, image and `hq/compose.yaml`, never part of a
  customer's Tico; it will not start without `TICO_HQ_KEY`). It stores an install ID, first and last seen, the last version
  and the last day each flag was true, deletes rows after 13 months, uses the address only in memory for a rate limit, and
  publishes aggregates at `/v1/stats`. Its `cloudflared` front door always runs with a route HQ writes at start (`HQ_DOMAIN`
  to `http://hq:8770`, a 404 for the rest, world-readable for cloudflared's non-root user), so a locally managed tunnel
  serves HQ instead of answering 503; a route set in the Cloudflare dashboard still wins.

### Changed
- **`hub kpi add` takes the KPI's name first.** `hub kpi add "<name>" [--goal ID]` makes a standalone KPI and links it when
  `--goal` is given, with `--baseline/--target/--deadline` or `--min/--max` for the target. A target needs a deadline. `hub kpi log`
  takes `--period-start`, `--period-end` (`--at` still works), `--evidence`, `--quality` and `--supersedes`.
- **Who may log a reading.** A KPI's owner (or anyone above them), the Goal Manager, or the owner of a goal that uses it. It used
  to be anyone signed in.
- **Existing KPIs migrate forward.** Every old KPI and reading is kept. Each KPI becomes standalone, owned by its goal's owner and
  monthly, linked to its old goal; an old `target` becomes an improvement target on that link (with no deadline until someone sets
  one), and a colour someone set on a goal is now their override.
- **First run has one step fewer: "What hurts, and what you use" is gone.** The pain chips grouped by team, "In your own words" and the
  "What do you already use?" tool checkboxes are removed, so the wizard is six steps, with AI providers first when none are chosen yet (Names, About the company, Your team,
  Add the computer, Connect your agent, Review and create). The team is now chosen from "About the company" alone. The **starter team** is
  Chief of Staff, Support Agent and Sales Drafter, plus Issue Triage when software is the product, and at most one more starter when the
  "What you do" text obviously matches a card's `pains` or summary. The **full org chart** is every starter template grouped by team with each
  pack's `lead: true` template as lead; Engineering is included only when software is the product, and a company that sells only to
  consumers skips templates whose `recommend_when` names `sells_to_businesses` but not `sells_to_consumers`. Tools no longer gate anything at
  onboarding: templates are never held back for a missing tool, the "Needs Mail" pills are gone, and each bot asks for what it needs in its own
  Start setup. `answers.pains`, `pains_text` and `tools` are still accepted and ignored, `held_back` is always empty and `pain_options` is no
  longer served. `FEATURED_PAINS`, the tool and signal tags (nothing sets `uses_github` or `uses_meetings` any more; cards may still list them) and the
  pain chip CSS are removed, and the Getting started "next bot" hint no longer quotes a pain.

## [0.2.14] - 2026-09-30

### Added
- **First run asks what hurts, then proposes a team.** The wizard now asks what the company does, its top one or two pains (chips taken
  from the starter cards, plus free text), the tools it already uses (mail, chat, CRM, GitHub, a meetings importer, docs), who it sells to
  and whether software is its product, and stores them in the onboarding record. A local chooser (no network call) matches them to the
  cards' `pains`, `recommend_when` and `prerequisites` and offers two starting points: a **starter team** (the best one or two pain matches,
  what a ticked GitHub or meetings tool names outright, and Chief of Staff; about three to five bots, each with a one-line why and its
  prerequisites) or a **full org chart** (every template that fits, grouped into Leadership, Sales, Marketing, Support, Operations and
  Engineering with a lead for each team). A template whose required tool was not ticked is held back and says what it needs. Both are fully
  editable before anything exists: rename a bot, choose who it reports to (a person or a bot, the owner by default), add or remove any
  template. There is no cap. Nothing is created until **Create my team**.
- **Starter bots are created parked.** Create makes every starter at once, `needs_onboarding`, with its template version recorded
  (`template_version`), its first routine seeded paused, and no BotOps task: its computer materializes the repository as soon as the bot
  is placed, and the screen shows *setting up* until it exists (25 bots take a fraction of a second to create and about two seconds to
  materialize). While parked a bot runs nothing on its own: the scheduler skips it and only a person's chat message is claimed for it.
  It does not count toward a member's bot limit; onboarding it counts it, and answers `bot_limit` when the member is at their limit.
  `onboarding_state` is on `GET /api/v2/bots`, `/api/v2/bots/{bot}` and `/api/v2/org`, and a **Needs onboarding** mark shows on the org
  chart, the bot's page and after Create.
- **Start setup, and `hub bot onboarded`.** **Start setup** on a parked bot's page sends it "Let's set you up.", which starts the
  onboarding conversation its `AGENT.md` describes (any first message from a person does too). When a person approves its first routine
  the bot calls `hub bot onboarded` (MCP `hub_bot_onboarded`, `POST /api/v2/bots/{bot}/onboarded`), which clears the mark; every starter's
  onboarding playbook now ends with it.
- **One screen after Create**: each bot's setup progress, an admin to invite (`people add`), a human owner to name for each bot (bot
  owners), and where to connect tools once. Secrets are entered in the hub's own fields, never in chat: the screen and the BotOps playbook
  say so.
- **Coaching**: Getting started names the next bot to set up (the one matching the top pain first) and a **First approved output** step,
  which completes when a starter bot's first routine is approved, in place of "Your new bot finished a task".
- `docs/onboarding-guide.md`: picking your first bots, writing a good brief, approval gates, what good looks like and reviewing a bot's
  first week.
- Six starter bots for a 10 to 50 person company, each draft-first: Chief of Staff (new: a weekly brief to the owner, stalled-goal
  follow-up, the Monday agenda), Support Triage, Sales Drafter and Mail Drafts (the existing `support`, `sales` and `inbox`
  templates, upgraded), Meeting Notes (new: a summary, decisions and proposed tasks per imported meeting) and Issue Triage
  (new: label and duplicate proposals, drafted repro requests and a weekly digest for companies on GitHub). Each has an
  onboarding conversation on its first message, a first routine that produces a reviewable draft, a sample of excellent
  output, and a list of what always needs a person's Confirm. See [Starter bots](docs/starter-bots.md).
- **A catalog for a whole company.** Thirty-two new templates take the catalog from 10 to 38, so the **Full org chart** builds a real
  company: a typical business-to-business software company that ticks mail, chat, a CRM, GitHub, meetings and docs gets about 35 bots in
  six teams instead of 5 in 4. Leadership: Strategy & Planning, Board & Investor Updates. Sales: Sales Lead, SDR & Lead Research, Sales
  Ops (read only on the CRM), Proposal Writer, Customer Success. Marketing: Marketing Lead, SEO & AI Visibility, Email Marketing (drafts,
  never sends), Product Marketing. Support: Support Lead, Support QA, Feedback Analyst, and the existing Support Triage becomes **Support Agent** (same `support` template, now working each ticket end to end as drafts and researching answers through the Librarian). Operations: Ops Manager,
  Recruiting Coordinator, People & HR Assistant, Legal Review (summaries for a person, not legal advice), Procurement, Bookkeeping Assistant
  and Spend Watcher and AR Follow-up (read exports, never post, pay or send). Engineering: Engineering Lead, PR Reviewer, Release Notes,
  Incident Scribe, Docs Writer (READMEs and API docs in the product repositories only), Product Researcher. The Librarian keeps owning the company's docs: no template writes them, and Support Agent and People & HR report a doc gap to it as a task. Each has an onboarding conversation, a paused first routine, a sample output for the
  fictional company Acme and the list of what needs a person's Confirm, and its method is cited in [Starter bots](docs/starter-bots.md).
  Bots start parked in Needs onboarding and cost nothing until set up.
- Catalog cards may carry `lead: true`: exactly one template per pack, the team's coordinator (Chief of Staff for Leadership). The four
  team-lead templates are small: a weekly team summary drafted from what the team's bots reported and routing proposals, never the
  team's own work.
- Catalog cards carry `pack`, `pains`, `prerequisites`, `onboarding`, `first_routine`, `approval_required` and `example_output`
  for a chooser to read. A test checks every starter for them.
- A catalog template's `schedules:` entry may say `enabled: false`: `hub bot create` and first-run setup then seed the
  routine paused, and the bot arms it after a person approves its first result.
- **BotOps starts with a goal.** "Keep the bots running smoothly": help people and bots create new bots and edit existing ones so they
  run smoothly, and watch for bot issues and resolve them. It is BotOps's own goal with no parent, set by the keeper (the actor the seeded
  goals use) when BotOps is set up, and once on start for a company that already has BotOps and no goal for it. Deleting it keeps it gone.

### Changed
- First run has seven steps (names, about, what hurts and what you use, your team, a computer, your agent, review) and finishes with **Create
  my team**. The catalog cards' `pack` is the team a template sits in; content, listening, market and reputation are `marketing`.
  The sample outputs in the starter templates are fenced as stand-ins.
- Content, Market Analyst, Listening and Reputation now have the full starter card (pains, prerequisites, onboarding, a first routine
  that starts paused, an approval list and a sample) and are created parked like the rest. Reputation declares `read` on its review
  surfaces and Slack and keeps `act` and `post` as a commented block the owner enables after the first approved batch; the three
  templates that allowed `gh issue *` no longer do; Market Analyst no longer names a `cmo` it has no bot for.
- The catalog test now covers every template generically (fields, line limit, paused routines, no send verbs, one lead per pack, no
  pain phrase used twice) instead of a list of six.
- The Mail Drafts (`inbox`) template now starts with one paused morning brief instead of three weekday passes and a weekend pass,
  reads only its own mailbox (no `org_read`), and does not label or archive until the person turns filing on.
- **The Goals page is simpler.** Company goals come first and only when there are some (the owner adds one with **+ Company goal**), then
  each person and bot that has goals in org-chart order, each goal with its status, progress and a "supports" chip when it is linked.
  Tapping a goal opens one form: Goal, an optional **Supports** select (Nothing by default) and Save; **+ Goal** makes a new one for
  anyone you may set goals for. It works on a phone and in both themes.
- **The org panel shows a bot's harness as a small icon.** Where it said "Codex" or "Claude" beside a bot's name there is now the
  OpenAI, Anthropic, Google, xAI, Cursor or OpenRouter mark, muted and about 13px, with "Runs on Codex" as its title and label. The
  name truncates before the mark, and a bot with no harness shows nothing. The same mark sits beside the name on a bot's page.
- **The Meetings page is redesigned.** It opens with the title, a search box and one **Add notes** button; the intro banner
  and the separate Import button are gone. Under the header a **Sources** strip has a tile for Granola, Fireflies, Zoom,
  Google Meet and Close, each with its logo and one word, **Connect** or **Connected** with the time of the last import. A tile
  opens that source's setup: Close its integration page, the others a dialog on the page. With no meetings yet the page says
  "Connect a source or add a note" and shows the same tiles large. Uploading or pasting a transcript is part of **Add notes**.
  The filters appear once there is a meeting, on one line, and each meeting is a row with its source logo, date, people and
  "N tasks" when bots pulled any.
- **Setting up the Market is one box, and the Librarian does the research.** The four-question card is now **Research your
  market**: one text box for a website, a description or links to anything about the market (with an **Attach files** link to the
  Docs import) and **Start research**, on the Market page and Getting started while the market is empty. It files a task,
  "Set up the market map", to the Librarian, so the Market Analyst is no longer needed first. The Librarian has a new playbook,
  `market-setup.md`: it reads every link with `hub docs fetch` (about 40 fetches), uses web search (declared in its `employee.yaml`),
  and writes the company, its competitors, segments, channels, people and rules into the graph and fills the eight market pages, with
  a source on every claim and no number a source does not state, then finishes with a note of what it found and what it could not
  read. It hands the upkeep to the Market Analyst if there is one. `POST /api/v2/getting-started/market` now takes `{"text"}` and
  answers `{"task_id", "bot": "librarian"}` (`409 librarian` while the Librarian is not running). While it works the Market page
  shows "The Librarian is researching your market. This usually takes 5–10 minutes." for at least two minutes and until the market
  has content (at most thirty, kept in the browser, polling every 30 seconds), then redraws. The Overview is the page the Librarian
  writes, or a one-line empty state, in place of the fixed placeholder text. The Librarian may now write the market graph
  alongside the Market Analyst and the owner, and `hub market apply` takes `--tier` and `--new-id`.
- **Settings > Bots is one compact row per bot.** The bot (with a small Built in badge, and a badge only when it is paused, planned or
  has a problem), Access as one word (Open, Requests only, Private, Custom), Model and Fallback as one field each, an owner avatar
  stack, the computer, and one **Edit** button; nothing overflows, and on a phone each row is a card. Who it works for, who owns it,
  access, model, fallback and computer moved into the bot editor.
- **Less text.** The muted subtitle beside every card heading is gone, and explanatory paragraphs, labels, placeholders and help
  text across Settings, dialogs, Docs, tasks, the connectors and the Help page are shorter. Settings > People has one **Sign-in**
  card in place of Company domain and Who may join: a single **Who can join** field for emails and domains (an entry without an
  `@`, or starting `*@` or `@`, is a domain), the company domain shown under it, the per-member bot limit, and one Save. AI providers
  are one line each, with the OpenRouter-backed vendors on one line.
- The built-in assistant shows as **Assistant** when its name is the company's, and an assistant still named after the company is
  renamed to Assistant once at startup (a name anyone chose is left alone).
- **The full org chart is led by each pack's lead template.** A card's `lead: true` now reaches the wizard (`lead` on every card and on
  each chart member). Every team leads with its pack's lead: Chief of Staff for Leadership, Sales Lead, Marketing Lead, Support Lead,
  Ops Manager and Engineering Lead. The lead is listed first and added even when no answer points at it (unless its required tool,
  GitHub for Engineering, was not ticked, when the team's first member leads); the rest report to it and the lead reports to the owner.
- **First run shows a dozen "What hurts" chips, grouped by team.** With 38 templates there were about 180 pain phrases. The screen now
  shows two for each of Leadership, Sales, Marketing, Support, Operations and Engineering (`FEATURED_PAINS`), plus the free-text box; a
  pain ticked earlier stays on screen, and every card's `pains` still match what is ticked or typed. `pain_options` carries each
  phrase's `team` and whether it is `featured`.
- **The first-run wizard has less text.** The paragraph under each step's title is gone, with the help lines under the fields and the
  subtitles on the after-Create cards. At most one short line is left: "Stays on this computer." on what hurts, "Nothing exists until you
  create it." on your team, "Optional." on your agent. The starting points say only how many bots they hold. A bot's reason on
  the team step is the card's own summary: the "It fits how you described the company." line is gone.

### Fixed
- **Who can join** (on the Sign-in card) was two boxes, and a domain typed into the address box (`*@company.com`) was stored as written and never matched
  anyone. It is now one box: an address lets that person join, a domain (`company.com`, `@company.com` or `*@company.com`) lets anyone
  at it join, and after saving the page shows what it understood ("Domain: company.com", "Person: ana@company.com"). The server sorts
  each entry into `allowed` or `allowed_domains` (the stored shape is unchanged) and refuses, naming the entry, anything that could
  never match (`a*@company.com`, a malformed address) and a public mail domain such as gmail.com, which would let anyone with such an
  account join.
- **A bot's own goal is no longer shown as the company goal.** The Goals page treated every goal with no parent as a company goal, so a
  goal a bot set for itself sat under the company's name, and bots were told to ask a person for a parent goal. A goal's level now comes
  from its owner (`company`, a person or a bot), and a goal needs no parent: a person or a bot sets its own with none, and it is simply
  not linked. `hub goal create --owner me --title "..."` works with no `--parent`; only `--owner company` is the owner's. Goals that
  were company goals before (a person's goal with no parent and goals under it) become company goals owned by `company` on upgrade.
- **The bot page's update history icon matches the Updates icon** in the left rail, and the Recurring card no longer has an icon the
  other cards lack.
- **Form inputs.** The Email field under Settings > People > Add a person, and every other email, url, number, date, search and
  untyped input, rendered as a small grey native box because the base style only covered `text`, `password` and `select`. One rule
  now styles every text-like control, with hover, focus, disabled, read-only, invalid and placeholder states, themed autofill, no
  number spinners, a clear button on search, "Choose file" buttons drawn like the app's buttons, and accent-coloured checkboxes and
  radios, in both themes. Inputs also carry the right `type`, `autocomplete`, `inputmode` and `spellcheck`.
- The docs no longer say the catalog has six starter templates: Creating bots, the Onboarding guide and the README say 38 and point to
  Starter bots, which lists them; First run documents the card's `lead` and the featured pains.
- **The Market page hid nothing on installs made before the market setup box.** The nine seed pages ("None in the seed.") are tagged
  `seeded` only at seed time, so an older install kept showing them next to the empty state. At start-up the hub now marks each of
  them `seeded` once, when its content is still exactly the seed's text; a page anyone wrote or edited, or one the graph has since
  outgrown, is left alone, and a second start marks nothing.
- **A Cloudflare tunnel reused with only its token routed nowhere.** A locally managed tunnel has no ingress of its own, so
  cloudflared logged `No ingress rules ... cloudflared will return 503`, its container showed as running and every request got a
  503, and `setup` still passed. The `cloudflared` service now always runs with a small config the server writes from `TICO_DOMAIN`
  at every start (into a new `tico-tunnel` volume the tunnel container reads): the domain goes to `http://server:8765`, anything
  else gets a 404. It ships in the image and `compose.yaml`, so an update from an older release needs no new file. A tunnel managed
  in the Cloudflare dashboard keeps its own Public Hostname route, which cloudflared prefers over the local file. `setup` and
  `setup doctor` now fail on a 502, 503 or 530 from the tunnel and on cloudflared's "No ingress rules" log line, with the fix.

### Security
- A bot may invite only people on the company roster to a calendar event (`hub calendar schedule`); an invitation to any other address is
  refused (`403 external_attendee`). It was open to every bot and sent invitations, which are email from the company's calendar, to anyone.
- Issue Triage no longer relies on a prompt to keep public GitHub comments behind an approval: the company's GitHub App token can write
  issues, so its access is now read-only and its harness settings deny `gh issue edit` and `gh issue comment`. It proposes, with the exact
  commands on the task, and a person runs them until the owner turns writing on. The audit of what each starter can send is in
  [Starter bots](docs/starter-bots.md#what-stops-a-starter-sending-things-outside-the-company).


## [0.2.13] - 2026-09-29

### Added
- **Per-bot permissions.** Every bot has three: **See** (the org chart and bot lists: name, role, who runs it, who it reports
  to), **Read** (its activity: tasks, updates, files, status and run log, routines, shared rooms, its page's activity) and
  **Write** (messages, chat, asking it, tasks, notes and comments that wake it). Each is Everyone, or chosen people, teams
  (the org chart's departments) and bots. Set them in Settings > Bots: the **Access** column shows
  `See: Everyone · Read: Legal · Write: Everyone`, and its editor has the presets **Open**, **Visible, requests only**
  (See and Write Everyone, Read chosen) and **Private**, or a custom mix per level. Changes are revisioned and undoable from
  the settings history. The owner, the bot itself, the people above it on the org chart and the bot's owners always have full
  access, and someone who may write without reading still sees their own conversations and tasks with the bot. A bot's page
  for someone who cannot read it shows its name, role, who runs it and a **Send a request** box, no activity. The org panel
  has a person icon beside the clock: on, it shows only the bots you can read or write to; it combines with the Recent sort
  and is kept with your account. See docs/permissions.md.
- Stable v2: `GET/PUT /api/v2/bots/{bot}/access` (owner, bot administrators and the people the bot reports up to),
  `GET /api/v2/bots/{bot}` (a bot's profile, and its status, queue and goals for whoever can read it, in place of the internal
  `/api/employees`) and `GET /api/v2/bots/{bot}/routines`; `GET /api/v2/bots` and `/api/v2/org` return only the bots the caller
  can see, each with `access: {see, read, write}`, and take `?can=read|write`.
- **Roles and members.** Company roles are Owner, Admin (the old bot administrators, read from either key for one release) and Member.
  Members may create bots (up to 5 active each by default, an admin sets it) and add people, and owners and admins switch either off per
  person in Settings > People. Adding people is on by default for coworkers in the company's email domain (the allowed sign-in domain, else the
  owner's own unless it is a public mail address); outside it needs an owner or admin. A new person goes on the roster and the sign-in list.
  Admins manage every bot but the built-in ones (the Assistant, BotOps and the Librarian are the owner's alone), people and computers; only
  owners make admins. Admins are not credential administrators: the vault stays with the owner and `TICO_CREDENTIAL_ADMINS`.
- **Bot owners.** A bot's creator and co-owners (and its operator, whoever it reports to and the admins) own it, and one rule now says who may
  manage a bot. Owners edit its configuration, access, status and routines, archive it and add co-owners (Settings > Bots, **Owned by**;
  `POST /api/v2/bots/{bot}/co-owners`).
- **Computers for members' bots.** A computer has **Accepts members' bots** (Settings > Devices, off until an owner or admin turns it on). A
  bot a member created is placed only on its operator's own computer or one that accepts them, never on another member's; admins may place it
  anywhere, and placing it never changes who operates it. Health warns when members' bots share a computer that holds `secrets/_shared.env` keys.
- **BotOps acts for the person who asked.** `hub bot register`, `hub bot access`, `hub bot owners`, `hub people add` and `hub people list` (and
  MCP tools), and `hub bot create` registers the bot with the server in a turn a person started: all as that person, checked with their rights,
  recorded "via BotOps". Adding people, roles, granting add_people, a stored-credential grant and a placement on a closed computer come back as a
  Confirm card in their chat with BotOps that runs only on their click. New BotOps playbook: build-me-a-bot. Fixes `hub bot set` on a bot with
  no server record, and adding a colleague no longer needs the owner in Settings.
- A Tools row at the top of a bot's page (the right column beside the chat, above it on a phone): a small round icon for the
  model and harness it runs on, its repository, and each `access:` entry of its `employee.yaml`, as the service's logo when Tico
  bundles one and the name's first two letters otherwise. Hover, focus or tap opens the identity it acts as, what it may do, its
  scope (database, channels, project, mailbox), the note and its status, such as "Credential missing on Test Mac"; past eight
  tools "+N" opens the whole list. The runner reports the declared access on its heartbeat (names, verbs and whether each
  variable is set, never a value; a runner from before it shows the model and repository only), and
  `GET /api/v2/bots/{bot}/tools` serves it to whoever can read the bot. See "What people see about a bot's tools" in `docs/creating-bots.md`.
- A bot's managers can register or remove a tool without opening its repository: `POST /api/v2/bots/{bot}/tools`,
  `DELETE /api/v2/bots/{bot}/tools/{id}` and the MCP tools and `hub tools` commands `hub_tools_add`, `hub_tools_list` and
  `hub_tools_remove`. The entry is checked against the `employee.yaml` access schema and kept as a pending request, and BotOps
  gets a task with the exact YAML to commit; the row shows it as pending until the bot's computer reports it. A credential
  value, or anything that looks like a key or token, is refused (`422 secret`): `env` is only a variable's name, and the
  operator installs the value on the bot's computer.
- Live replies for custom frontends: `execution.parts` in `/watch` and `/snapshot` lists the pieces of the run's reply so
  far, in order, as `{kind: "progress"|"reply"|"tool", text, at}`. A tool call is one short label ("Ran hub task create"),
  never its arguments or output. See [custom-frontend.md](docs/custom-frontend.md#live-events).
- Messages say which run handled them. On the `messages`, `snapshot` and `watch` routes a message a run has taken carries
  `run: {job_id, attempt_id, state}`, with `state` `started_run` or, for a follow-up delivered into a run already working,
  `added_to_run`. A bot's reply carries `run: {job_id, attempt_id}` and `answers`, the ids of every message that run
  handled (the one that started it, then the folded-in ones). Older replies have no `answers`.
- A Grok run now reports its tool calls (by kind only, never the title or input), which is what lets a frontend see where one
  message ends and the next begins.

### Changed
- Who may use a bot no longer depends on the "Can use" list (`owner_ids`), which now only says who a bot works for and who is in
  its shared room; adding a bot no longer asks for people. Everyone can chat with and give tasks to every bot unless its Write
  says otherwise. A person who writes to a shared-room bot without being one of the people it works for talks to it in a room of
  their own.
- **Every bot starts Open after the upgrade.** `private_owners` and `routing_permissions` in `registry/hub-access.yaml` are no
  longer read: if either was set, the first start logs one warning and the owner finds a note on Settings > Health ("hub-access.yaml
  private/routing lists are no longer used; bots are now Open; set access in Settings > Bots"). Set the access you meant there.
- A bot the caller cannot see is a `404` everywhere (it used to be a `403` "This bot is private" on some routes and invisible on
  others); one they can see but not read or write to is a `403 forbidden` that says which.
- `bot_contact` (Other bots: replies only, tasks only) now also limits notes and comments that wake a bot. Hub SQL holds the bots
  the caller can read, and tasks that involve a bot they cannot read only when the task is theirs.
- `execution.text` puts a blank line (`"\n\n"`) between a run's separate messages; it ran them together
  ("planned.I've filed"). Deltas within one message still join directly, the run's closing message no longer replaces the
  progress notes before it, and a model's thinking is no longer part of the text. Tico's own chat shows each message
  as its own paragraph.

### Fixed
- `tico setup --cloud aws` ignored `--tico-version`: the server ran `latest` and fetched compose.yaml from `main`. It now pins
  the images and the bundle to the release you name, as a local install does.
- `python -m backend.manage enrollment` (a join code without the web app) failed with "the owner of this environment (unset)":
  it now loads the stored owner first.
- A runner on the server's own machine can join the server's Docker network (`install.sh --runner --server-network tico_default
  --url http://server:8765`), which a Cloudflare Access install needs so its runners do not go through Access. The network
  lives in `runner.override.yaml`, which updates keep.

### Security
- Bot privacy was decided in a handful of places and left gaps: a task, a note or a comment to a private bot needed only that the
  caller could see it (a comment even woke it), a Slack message reached any bot its sender could name, and a page of tasks, updates
  or files could shrink or count differently for someone who was not allowed some of them. One check now covers every route, the
  MCP tools and personal API tokens, lists and SQL are cut in the query (so counts and pages leak nothing), and Slack routing
  follows the sender's Write. The old file lists could not do any of this per bot and are retired (see Changed).
- A person who may only write to a bot no longer sees, under its replies, the steps it took to answer, or the live output of its runs.
- BotOps borrows a person's authority only from their own chat message: not from one routed from Slack, from another person's message id
  or a room someone else spoke in, or from one over a day old when cited by id; someone who has left lends none. BotOps no longer changes
  routines or clears quarantine on any bot with its own authority: it does both as the requester, who must manage the bot.
- A member's bot can no longer be placed on another member's computer, and a computer a member enrols is not open to other members. Setup
  never places a member's bot on a computer that is not its operator's or open to members' bots, and placing one never makes the computer's
  operator its operator.
- SQL `events` and `refusals` show a member their own rows and what concerns what they may read; `registry_metadata` is for owners and
  admins; goals of a bot you cannot read are hidden from the goals tree, list and SQL, and the tree's counts follow the tasks you can read.
- Setting a bot's goals needs someone who manages it, not anyone who may write to it. A member cannot register the names `assistant`,
  `botops`, `librarian` or `coo`, and only the owner changes a built-in bot.
- A Confirm card shows every field the request carries, its server-written description names each field it changes (and, for a
  placement, whether the computer takes members' bots), and changing a person's email or team through BotOps needs their click.

### Tests
- The Python suite is about 46% smaller and runs in parallel (`pytest-xdist`, set in `pytest.ini`); the browser suite keeps one
  script per surface (`npm run test:ui`, three at a time). The two together run in a few minutes on a laptop.

## [0.2.12] - 2026-09-29

### Fixed
- A runner box's updater (0.2.10 and 0.2.11) stopped on every start after its first: it locked its token to the runner's
  user and then, without the right to change another user's file, failed changing it again ("PermissionError ... updater-token")
  and restarted in a loop, so the box could not update. It now leaves a token that is already locked alone and never stops over
  it. A box whose updater is restarting recovers with, in its directory (`/opt/tico-runner`):
  `sed -i 's/^TICO_UPDATER_TAG=.*/TICO_UPDATER_TAG=v0.2.12/' .env && docker compose -f runner.compose.yaml up -d updater`.

## [0.2.11] - 2026-09-29

### Added

### Changed
- Tico's icon and wordmark now match tico.team; the old robot icon is gone. The favicon, app icon (web, desktop, macOS menu bar, Slack) and the assistant's avatar use the new mark, the first-run setup and sign-in pages show the wordmark (reversed in dark mode, replaced by the app's name when a company has named its app), and the installed web app gains a maskable icon. `scripts/build-brand-icons.sh` regenerates every image from the SVGs in `ui/assets/tico/`.
- An inbox bot now gets a computer to itself. Its Google Workspace key opens every mailbox in the company, and every bot on a
  computer runs as the same user, so the server refuses (409 `inbox_isolation`, with what to do: add a computer) to place an
  inbox bot beside another bot, or another bot beside an inbox bot. Several inbox bots may share one computer only after the
  operator allows it (`POST /api/v2/runners/{id}/inbox-sharing`). Onboarding leaves such a bot unplaced rather than failing.
  Settings > Health warns about installs that already mix them; nothing running is moved.
- On a Docker runner with the two-user layout, the Google Workspace mail key no longer sits in `workspace/secrets`, where any
  bot could read it. The runner moves it (once, on its own) to its state directory, closed to bots, and an inbox bot's turn asks
  the runner over the credential socket for a one-hour token for its own person's mailbox (and the people below them). Any other
  bot, or another mailbox, is refused. A Mac, or Docker started the old way, keeps reading the key file, and Settings > Health
  warns ("Mail key") that bots there can read it. See docs/mail.md, "Who can read the key".

### Fixed
- Rolling back a failed server update no longer leaves Litestream able to upload the migrated database as the newest copy: the snapshot is written to a temporary file and swapped in only once complete, and Litestream's tracking directory is cleared before the old image starts. Rolling back by choice ([updates](docs/updates.md#rolling-back)) uses the same steps.
- Starting on a database that already had part of a schema change (a column added but the version not recorded) failed
  on every boot. Each schema change now runs in one transaction with its version bump and is safe to run twice.
- A database file that has Tico's tables but no version record is refused with a clear message instead of being
  migrated blind. An empty file still starts.
- The one-time removal of the old routine-manifest tables keeps what they held in `routine_*_retired` tables.
- Idle runners no longer keep the database's write lock busy. Each runner asked for work four times a second and every ask
  was a write transaction, so a fleet of 8 to 12 idle runners made 32 to 48 writes a second and starved the scheduler,
  backups and lease renewals. The server now checks with a read and writes only when there is something to do, and an idle
  runner backs off from 0.25 s to 2 s between asks. The API's database wait is 30 s, as the scheduler's already was.
- Due reminders and the three-day auto-close stopped for every task past the first 500: they read a capped task listing. They
  now query exactly the tasks they need.
- The Assistant tab says "Assistant" in its own copy ("Ask the Assistant…", "Assistant is thinking…"), not the assistant bot's
  name, which on a company named after its bot read "Ask the Acme…". Settings > Bots still shows the bot's name.
- The Assistant composer empties after a message is sent and keeps focus, so a second Enter no longer resends it. A failed
  send keeps the text and shows the error.
- The Assistant now makes the low-risk writes itself and replies with a link, instead of proposing a Confirm card: a task owned
  by the person with no bot on it (create or update, never done, declined, close or reassign), a comment on such a task, marking
  updates read, and a note to themself. Everything the server would refuse with `confirm_required` is still a proposal.
- A tab left open through an update now notices: when the server's version differs from the one the page loaded with (seen on
  the existing config poll), a small banner offers Reload. It never reloads by itself.

## [0.2.10] - 2026-09-29

### Added
- **Assistant**: every person has one private chat with the company's assistant, a personal operator that knows how Tico is
  organised and acts on their behalf (docs/assistant.md). An **Assistant** tab first on your own person page, and "Ask the
  Assistant…" in search (⌘K) that opens it prefilled; a phone layout; replies link tasks, meetings, docs, files and bots as in-app
  routes. Only you can read or post in your room (owner and administrators included), and the ordinary chat routes still refuse
  the assistant. With no assistant the tab says it is off, and the owner gets **Turn on Assistant** there and at the top of Settings > Bots:
  one click restores the archived assistant (a company that set it aside at setup, v0.2.1) or adds it from the catalog, places it on
  BotOps' computer and activates it, and everyone's tab starts working.
- Fast path, no model: "what's waiting on me", search (tasks, docs, meetings, files, people, bots), "open X", "what did <bot> do
  today" and "how do I …" (from `docs/*.md`) are answered on the server from Tico's own data; a `choice` decision question classifies
  an unclear message when the company has a decisions provider. Everything else is a turn of the assistant bot ("thinking").
- The Assistant acts as you and never more: its turn's `hub` and MCP tools are your own (`Auth.assistant_principal`), every write is
  recorded via assistant (`events`, task history, comments; "<name> (via Assistant)"). Direct writes are limited to what touches the person themself (their own tasks, comments, notes, marking updates read; never a task for a bot or someone else, a message to a bot, or settling a task); approving or
  declining a Needs-you item, anything sent outside the company, spending, changing people, access or settings, archiving, deleting
  and activating a bot are proposed (`hub assistant propose`, `hub_assistant_propose`) as a Confirm / Cancel card and run only on
  your click, as you, once; the bot cannot confirm (`assistant_actions` table). Only allowlisted, plain routes can be proposed, the card shows the server's description and the request body, results keep no answer body, and a message the Assistant wrote never lets BotOps act for the person.
- Stable v2: `GET /api/v2/assistant`, `POST /api/v2/assistant/messages|turn-on|actions`, `GET /api/v2/assistant/actions/{id}`,
  `POST /api/v2/assistant/actions/{id}/confirm|cancel`, in `docs/openapi/v2.json` and `docs/custom-frontend.md`.
- **The assistant and BotOps are built in.** Setup always builds both (the wizard's "skip the assistant" choice from v0.2.1 is gone; both
  are active once a computer is enrolled), and neither can be archived or deleted by anyone, owner included, through the UI, the API,
  `hub` or BotOps: `409 system_bot`. Pausing, renaming and editing instructions stay allowed. Settings > Bots lists them as **Built in**
  with no Archive control. A company whose assistant was archived keeps it archived on update (nothing auto-restores it); the owner
  turns it on with **Turn on Assistant** and it cannot be archived again.
- The assistant template's `AGENT.md` and a new `assistant-chat` playbook teach the Assistant how Tico is organised, how to route
  work to the right bot, to answer briefly with links, never to act beyond the person and to ask before any side effect.

### Changed
- Docs match the code. "The server calls no models" is replaced by what is true: with decisions or Slack routing on, the
  server sends the text of each question to the decision provider you configured, and SECURITY.md says what a compromised
  server exposes then. The bare `docker run` sample follows the release placeholder, the shared secrets file is described as
  readable by every bot on the computer, the removed VM path is gone, the Files page states who removes a file and that the
  owner sees direct chats (not personal Assistant rooms), and sizing says only a small pilot was measured.

### Security
- Files auto-publish and `hub files publish` refuse a regular file with more than one hard link, so a bot cannot hard-link a
  secrets file into `reports/`. Copies cannot be detected and are still published.
- The runner's updater token is now `ticorun`'s alone (10002, mode 0600, fixed on every updater start; the updater sidecar gains `CHOWN`), so a bot can no longer read it, and `POST /update` refuses a release older than the running one (409), so a bot cannot move the box back to a version that predates the separate bot user. Going back on purpose stays manual ([updates](docs/updates.md#rolling-back)).


## [0.2.9] - 2026-09-29

### Fixed
- An updater that replaced itself (0.2.6 to 0.2.8) came back with the helper's settings: a server's updater stopped refreshing
  the compose file and bundle, and a runner box's updater stopped pulling images, so the runner's next update failed with
  "No such image" and went back. The helper no longer passes them on. Updating the server to 0.2.9 repairs its updater. On a
  runner box that shows "No such image" under Settings > Health, run once in its directory:
  `docker compose -f runner.compose.yaml up -d --no-deps --force-recreate updater`.
- docs/custom-frontend.md lists `PATCH` among the CORS methods (Files uses it).

## [0.2.8] - 2026-09-29

### Fixed
- A Docker runner (v0.2.6 and v0.2.7) could not start again after its first bot turn: the turn hands the secrets folder to
  the bot user, and the next start failed changing its mode ("chmod: ... Operation not permitted") and restarted in a loop.
  The start now gives that folder to the bot user and sets its mode as that user. A runner stuck this way recovers with
  `docker run --rm -v tico-runner:/h alpine chown 10002:10002 /h/workspace/secrets`, then updating to 0.2.8.

## [0.2.7] - 2026-09-29

### Added
- **Files**: a bot's page lists what it created, revised or delivered, newest activity first (three rows and the total, "Show all"
  inline), in the main view beside its tasks. Three kinds: stored files (Tico's private blob store, opened through an authenticated
  route, every version kept), linked cloud documents (Google Docs, Sheets and Slides, Notion, Figma, any https document; Tico keeps the
  address only), and S3 objects copied by the bot's own computer with its own credentials. Tables `bot_files`, `bot_file_versions`
  and an append-only `bot_file_activity`. See `docs/files.md`.
- `hub files publish|add-link|touch|import|list` and the matching `hub_files_*` MCP tools. After a completed turn the runner
  uploads new or changed files under `reports/` and `artifacts/` (per bot: `files: {publish: [...]}` in `employee.yaml`) through a
  durable outbox with idempotency keys; credential-like names, symbolic links, paths outside the checkout, other types and files over
  25 MB are refused. A pushed commit adds "View on GitHub" at that exact commit.
- Files inherit the visibility of their task or conversation (a private chat leaks no name, count, version or download);
  the owner or a bot administrator can promote one to bot-wide or remove it from the list.
- Stable v2: `GET /api/v2/bots/{bot}/files`, `POST /api/v2/files/uploads|links|imports`, `PATCH /api/v2/files/{id}`,
  `GET /api/v2/files/{id}/activity|versions`, in `docs/openapi/v2.json`; CORS allows `PATCH`. The custom-frontend example shows a bot's Files.
- The bot templates' `AGENT.md` and the BotOps playbooks tell bots to publish their deliverables.

### Changed
- The bot page's old storage card (More) is gone, with `GET /api/employees/{bot}/storage`; task and chat attachments stay where they were.

## [0.2.6] - 2026-09-29

### Added
- `scripts/journey-test.sh`: an on-demand install-to-rollback check to run against Docker before a deploy (docs/releasing.md).

### Fixed
- A server started on an empty data volume no longer becomes a blank company when its backup cannot be restored. It records an
  environment marker outside the database (`.tico-environment` in the volume, `environment.json` beside the backup) and refuses
  to start, with a clear message, when a company exists (or may exist) and the restore failed. Starting a new company over an
  existing backup needs `TICO_INITIALIZE_EMPTY=1` or `server --initialize-empty`; a genuinely fresh install starts as before.
- "Check for updates" waits (up to 10 seconds) for the fresh answer instead of returning the cached one, bypasses the cache
  for it, and says "still checking" when GitHub is slow.
- Updates snapshot the database (`/data/snapshots`, last three kept) before switching the server image and restore it when the
  new version fails its health check and is rolled back; the update status reports the snapshot and whether it was restored.
- The server's updater and the runner box's updater replace themselves after a successful update (a short-lived helper
  recreates the service and puts the old updater back if the new one does not stay up), so updater fixes reach existing installs.

### Security
- The Docker runner keeps its own credential away from bot code. The supervisor stays the runner's own user (`ticorun`, 10002, as in every earlier image) and owns `runner.json` (0600), holding
  five ambient capabilities (`CHOWN`, `DAC_OVERRIDE`, `KILL`, `SETGID`, `SETUID`); each turn's model CLI, `git` in a
  bot's checkout and the sign-in flows run as an unprivileged `bot` user (uid 10003). A turn's git credential helper gets
  the bot's GitHub token from a supervisor socket with its attempt token, never from the registration. The current
  `runner.compose.yaml` sets `user: "0"` and the capabilities; an older compose file, or a bare `docker run` without them,
  keeps running as one user, and so does the previous image if an update is rolled back on a migrated volume. Bots still share the `bot` user with each other. SECURITY.md says what is and is not separated.
  On the first start of an existing volume the entrypoint changes its ownership once (logins and dotfiles to `bot`, group-writable; the workspace keeps its owner and
  the supervisor's files stay with `ticorun`); use `docker exec -u bot` to sign a model in.

### Tests
- `POST /api/v2/sql` and the JSON API are checked against each other for tasks, private rooms, messages and meetings, for the
  owner, a member, a person with a private room, a bot and a bot whose turn was reassigned.
- A Docker test starts the runner image on a volume from an older release and shows a turn cannot read the registration but can
  still run its harness and push through the credential helper.


## [0.2.5] - 2026-09-29

### Fixed
- The in-app update now updates the compose bundle too. The updater downloads the target release's `tico-bundle-vX.Y.Z.tar.gz` and
  `SHA256SUMS`, refuses a checksum mismatch or unsafe archive before changing anything, replaces `compose.yaml`, `.env.example`,
  `docker/runner.compose.yaml` and the rest of the bundle atomically (never `.env`), and keeps the old files in `.bundle-previous/`.
  A release that does not turn healthy is rolled back, image and bundle together. Slack and the front door are recreated from the
  new file. A runner box's updater does the same for `runner.compose.yaml`. The updater itself still moves to the new release at
  the next `docker compose up -d` (it cannot recreate itself mid-run), so it applies bundles from the update after its own.
  New settings therefore reach existing installs without re-running `install.sh`. The server keeps its explicit `environment:` list
  rather than `env_file: .env`, which would pass secrets meant for other services (such as the tunnel token) into it.
- `GET /api/v2/bots` no longer lists archived bots (`?include_archived=1` for admin views that need them), so a custom frontend's
  chat picker does not offer them.

### Added
- Display names in the stable v2 API, beside the ids and never instead of them: `owner_name`, `requester_name`, `from_name`,
  `to_name` and so on, an `actors` map (`{"human:ana": "Ana Alvarez"}`) on read answers, and notices the hub wrote ("New task from
  bot:x: ...") shown to people with names in `body` (the stored text is in `body_raw`). The example app shows task owners by name.

### Changed
- The demo company is now a neutral fictional software company (project-tracking software for small studios) instead of
  property management. Screenshots in `docs/images` are unchanged until `npm run screenshots` is run again.

## [0.2.4] - 2026-09-29

### Added
- Build your own web frontend on Tico: `docs/custom-frontend.md` (start here) and `docs/api.md`, with a no-build example app in
  `examples/custom-frontend/` (org chart, chat with streamed replies, tasks, Needs you) that a browser test runs against a real server.
- `TICO_CORS_ORIGINS` (also in `compose.yaml` and `.env.example`): exact origins, never `*`, that may call the API from a browser
  with credentials. Unset, the server adds no CORS behavior. A browser write from a listed origin passes the origin check.
- Sign-in for a frontend on another origin (built-in sign-in, `TICO_AUTH_PROXY=oidc`): `/auth/login?next=<listed origin>&code_challenge=<S256>`
  returns a one-time code in the URL fragment, `POST /auth/token` exchanges it (PKCE, and the same origin) for a bearer session
  (`Authorization: Bearer tico_st_...`, same 12-hour idle and 7-day lifetimes), and `POST /auth/token/revoke` ends it. Only listed
  origins are ever redirected to; any other `next` keeps falling back to `/`.
- `GET /api/v2/openapi.json` is now the stable v2 contract: the operations a frontend builds on, with tags, operation ids and the shapes of
  their answers, still for signed-in callers only. The committed copy is `docs/openapi/v2.json`; `python -m backend.openapi_v2` regenerates it
  and a test fails when it is stale. Before, the route returned every route of the server, internal ones included.
- A bot built on a computer publishes its history itself. After `hub github create-bot-repo <slug> --empty` and setting
  the bot's repository link, the runner, in the bot's own turn, sets `origin` to the resolved GitHub URL and runs
  `git push -u origin <branch>` with that bot's own token when the checkout has commits and no upstream. It never
  forces; different history on the remote, or an `origin` that points elsewhere, stops it and shows under Health, "Bot
  history". BotOps no longer pushes other bots' repositories (playbook step 5b).

### Changed
- "Judge" is now "decisions" in everything people read: docs, the UI, the setup prompts, the hub CLI and MCP tools, the
  mail flags and the bot skill. A decision question is a yes/no (`noul`), a `choice` or a `score`, answered with
  probabilities and acted on with thresholds, the same format as OpenRouter's Decisions API; the provider setup is unchanged. New names: `hub decisions` / `hub_decisions`, `hub listen decide` / `hub_listen_decide`,
  `skills/decisions`, `--decisions-provider`, `MAIL_DECISIONS`, `mail inbox --decisions`, and a `decision:` condition in
  `registry/mail-rules.yaml`. The old names (`hub judge`, `hub_judge`, `--judge-provider`, `MAIL_JUDGE`, `--judge`,
  `judge:` rules) still work and are deprecated. Internals are unchanged: `backend/judge.py`, `/api/v2/judge`,
  `judge.call` audit events, `routed_by: "judge"` and rule ids.
- Listening's inboxes are the company's own: `registry/listening.yaml` lists the destinations (category, threshold,
  receiver bot, optional readers and `unless`), and a company with none routes nowhere. The hub no longer ships
  company-specific destinations. The example question sets (`questions/listening-*.json`) describe a
  fictional software company; `listening-item` is version 5 with categories `lead` and `partner`. Companies that had those destinations copy them into `registry/listening.yaml` and keep their
  own questions.
- The UI no longer special-cases the `human-ops` and `success` teams: team names read from the data.
- The Version line in Health writes releases as `v0.2.3` everywhere.
- The writing check accepts far more imperative verbs ("Connect", "Authorize", "Configure", "Rotate", ...) and refuses
  only a title that clearly does not start with one: a label such as "Needs-you:", a noun phrase, a gerund or a
  third-person form.

### Fixed
- Health said "GitHub refused a token" and "give it access" when the bot's repository simply did not exist yet. It now
  says the repository does not exist yet and how to create it (`hub github create-bot-repo <slug>`, or `--empty`); a
  403, or a repository outside the installation, keeps the access message.
- "Check for updates" showed its answer twice, once as `0.2.3` and once as `v0.2.3`. It shows it once.
- `npm run screenshots` failed on the Devices capture because Settings remembers its last tab. Each Settings capture
  now picks its tab, and the Health capture is `settings-health-*`.


### Removed
- The older install stacks. Docker is now the only way to install and run the Tico server (`install.sh`, `tico setup`
  or the cloud-init files). Gone: `infra/aws` (the ALB + Cognito CloudFormation stack), `infra/ec2` (the EC2
  reference stack and its `deploy.py`, `install.py`, `deploy_from_ci.py`), and `infra/linux` (`install-vm.sh`,
  `install-runner.sh` and the systemd units). A Linux computer that runs bots uses the Docker runner
  (`install.sh --runner`); the Mac runner is unchanged. `scripts/tico` no longer drives a `tico-runner.service`
  unit on Linux, and `INVOCATION_ID` no longer marks a runner as supervised (set `TICO_SUPERVISED=1` under your own
  supervisor). Sign-in with an AWS ALB and Cognito (`TICO_AUTH_PROXY=aws-alb`) still works behind a load balancer you
  run yourself; see `docs/install-advanced.md`.
- `tico-release.tar.gz` and its checksum are no longer attached to releases. Releases publish `install.sh`, the
  compose bundle and `SHA256SUMS`; the images carry the version.
- If you run one of the removed stacks: nothing changes on your running server, but new releases will not update
  it. Move to Docker by installing with `install.sh` on a new server, restoring a backup bundle into it (see
  Backups and restore in `docs/install.md`), and pointing your DNS at it; the old stacks stay available in the v0.2.3 tag.

## [0.2.3] - 2026-09-29

### Added
- `hub github create-bot-repo --empty` (API `empty: true`) creates an empty private `<org>/emp-<slug>` for a bot whose
  repository already exists on a computer; BotOps' playbook and `docs/github-app.md` describe pushing its history in.

### Changed
- Health moved from the main navigation into Settings > Health. `#/health` still works and opens it; a red dot on
  the account button, Settings and the Health tab shows when something needs attention. Getting started, once done,
  just leaves the rail.
- The BotOps bot may create bot repositories (`emp-<slug>`, private, for a planned or active bot, in the connected
  organization) when the app has administration permission, audited with the acting bot; anyone else is refused with a reason.
- A bot repository link that is a bare `emp-<slug>` resolves to the connected GitHub organization for its token.
- Integration pages name the `owner` role instead of a person, served as the company owner's name; `integrations/github.md`
  describes the GitHub App model, and the shipped pages and templates no longer state fixture people or companies as facts.

### Fixed
- Health's Update button opens the update popup instead of closing it again on the same click.

## 0.2.2 - 2026-09-29

### Added
- Health > Version has an owner-only "Check for updates" that asks the server to look for a release now (at most once
  a minute; `POST /api/v2/system/update/check`) and shows the answer beside the line.
- The "What should your bot do?" dialog offers "Build another" after sending, and Done in place of Cancel.

### Fixed
- Settings > Bots and a bot's Setup show "company default (model · effort)" for a bot that follows the default, not "not set".
- "Build one with BotOps" creates the bot's planned record (on your computer, under you, on the default model) before
  filing the task, so the bot shows on the org chart and BotOps can attach routines instead of finding it unknown.
- Health and the sidebar's "New version" notice fetch fresh data on every visit and on the app's poll, so a release
  the server just learned of no longer waits for a page reload.
- Health > Computers and Settings > Devices list only the models the company's providers or an assigned bot use (plus
  any installed); a missing one is red only when something needs it.
- A GitHub connection that is not set up is shown as optional info on Health, not a green check.
- A Docker runner without an updater is told to move onto the compose file with `install.sh --runner` (pinned to the
  server's release); a compose runner keeps the `docker compose pull && docker compose up -d` hint.
- The first-run wizard no longer assumes a Mac: computers can be Macs or Linux or cloud boxes, models a subscription or
  an API key, and Review lists every online computer.
- The "What should your bot do?" dialog closes when you navigate elsewhere.
- Notices such as "New task from human:sam" show the person's or bot's name, not the raw id.
- A key or sign-in the provider refuses (401, "Incorrect API key", "not logged in") now marks that model on that
  computer "rejected" with the time and a redacted reason, instead of showing "signed in" while every turn fails.
  The computer takes no work that needs it until the key or sign-in changes (or one retry after five minutes), and
  the job waits in the queue rather than starting a new attempt every 15 seconds.

### Changed
- The server's updater is pinned to the release (`TICO_UPDATER_TAG`, default `TICO_TAG`) instead of `:latest`. An update
  leaves the updater running, since it cannot replace itself; it moves at the next `docker compose up -d` on the host.
- `python3 -m setup runner` and `infra/cloud-init/tico-runner.yaml` set the runner up with the release's
  `install.sh --runner`, so every documented path gets the updater sidecar instead of a bare `docker run`.

## 0.2.1 - 2026-09-29

### Added
- `python3 -m setup backup-storage --domain ... --aws-region ...` creates the AWS backup bucket and its scoped key from a
  laptop, for a server that has no AWS credentials; the installer then takes it as an `existing` bucket.
- `install.sh --runner --url ... --code ... --label ...` sets up a computer that runs bots: Docker, the release's
  `runner.compose.yaml` with its updater sidecar, a `.env` pinned to the release, and `docker compose up -d`. Settings >
  Devices > Add computer and the first-run wizard show this line for a Linux or cloud server, pinned to the server's own
  release; the bare `docker run` stays as a documented alternative that does not update itself. Re-running it on a
  machine with a bare-run runner reuses the `tico-runner` volume, so the enrollment carries over.
- The company assistant is optional in the first-run wizard: ticked to start with, with a line on when you want it (Slack,
  meetings sent to bots); BotOps stays required. Without an assistant, Slack routing asks through BotOps, meetings and
  work nobody was named for go to BotOps, refused-write reviews of BotOps go to the owner, and BotOps and new bots
  report to the owner. It can be added later from Settings > Bots > Add from catalog.

### Fixed
- The catalog card's Owns and Never lists no longer show a raw object for an entry written as `- Name: description`;
  the server serves every entry as one sentence.
- Provider descriptions say what the runner accepts: Codex works with a ChatGPT subscription or `OPENAI_API_KEY`, Claude
  Code with a Claude subscription or `ANTHROPIC_API_KEY`.
- The setup wizard's final checks retry the HTTPS certificate, `/healthz` and sign-in redirect for up to 3 minutes
  while Caddy is still getting its certificate, instead of failing right after `docker compose up`. `setup doctor`
  stays single-shot.
- The TLS "internal error" hint now says the certificate is probably still being issued and points to
  `docker compose logs caddy`; the "Nothing answers on 443" hint names the private-subnet (NAT gateway) case.
- `--cloud aws` launches an internet-facing server only in a subnet routed to an internet gateway, and stops with a
  clear error when there is none.

### Changed
- `docs/install.md`: the AWS server needs a public subnet (the bot box may be private), and "When something fails"
  covers the private-subnet case.

## 0.2.0 - 2026-09-29

The server runs in Docker and runs no bots; bots run on computers that join it. One command installs it.

### Added
- **Install:** `curl -fsSL https://github.com/ticoteam/tico/releases/download/v0.2.0/install.sh | sh` on
  any Linux server. It pins the release, verifies checksums, installs Docker if needed and runs
  `tico setup`, which covers DNS, sign-in, backups and health checks. Re-running it upgrades or repairs
  without touching `.env`. See [docs/install.md](docs/install.md).
- **Docker:** server, runner and updater images; a compose file with Caddy or a Cloudflare Tunnel in
  front; one-click updates with automatic rollback.
- **Where to run it:** `tico setup --cloud hetzner|digitalocean` creates the server with `hcloud` or
  `doctl`, and cloud-init files cover any Ubuntu cloud. The AWS load balancer + Cognito stack and the
  older Linux VM installer are in [docs/install-advanced.md](docs/install-advanced.md).
- **Computers:** a computer joins with one command and a one-time code (Settings > Devices > Add
  computer), as a Mac or a Linux box.
  - Computers follow the server's release, update at a quiet moment and roll back if they don't come
    back. See [docs/updates.md](docs/updates.md).
  - Linux computers also run mail and calendar sync, meeting importers and Close call sync.
- **Harnesses:** the runner installs only the model CLIs your providers need, keeps them updated
  between turns and honours pins (Settings > Devices). New providers: DeepSeek, Kimi, Meta Llama and
  Mistral (through pi and OpenRouter), and Cursor. See [docs/harnesses.md](docs/harnesses.md).
- **Sign-in:** built-in "Sign in with Google or Microsoft". Cloudflare Access and AWS Cognito remain
  supported.
- **Model sign-in from the browser:** the real Codex or Claude Code login link and code, relayed from
  the computer, so a company's own subscription signs in without SSH.
- **People:** people, access and ownership are managed in Settings > People. Directory sync from Google
  Workspace or Microsoft Entra ID (with a preview) and SCIM 2.0 for Okta, Entra and JumpCloud. Leavers
  are marked left, never deleted.
- **Backups on by default:** continuous copies to a separate volume, or to S3/R2 buckets that
  `tico setup` can create. `docker compose run --rm server restore` rebuilds a server.
- **Health page:** version, computers, models, queue, GitHub, backups, Slack, sign-in and failed runs,
  each with a fix.
- **"New version" notice** in the sidebar with the changelog and, for the owner, Update now.
- **Meetings:** importers for Fireflies, Zoom, Google Meet and Granola (Settings > Cloud services), and
  hand-written meeting notes.
- **Company databases:** `hub db` gives bots read-only access to PostgreSQL, MySQL, SQLite and MongoDB
  (including Atlas), with per-bot grants, row caps, timeouts and an audit trail. Companies can layer
  their own integration pages and query catalogs from private config. See
  [docs/databases.md](docs/databases.md).
- **Slack** in the Docker install: create the app from the manifest and paste two tokens in Settings.
- **GitHub:** per-bot extra repositories, and guidance to install the app on All repositories.
- **Demo:** `docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/ticoteam/tico:v0.2.0 demo` shows a fictional company on
  localhost with no setup and no outbound calls. See [docs/demo.md](docs/demo.md). The docs' screenshots are generated
  from it (`npm run screenshots`).
- Docs: [architecture](docs/architecture.md), [sizing](docs/sizing.md), and a plain-words threat model
  in [SECURITY.md](SECURITY.md).

### Changed
- Luna 6 at max effort is the recommended Codex model. Companies still choose their providers at setup.
- A computer older than the server's minimum runner release takes no work and says why.

### Fixed
- Settings no longer jumps back to the Devices tab or loses what you were typing.
- On a phone, the closed navigation drawer no longer casts a shadow on the page edge.

## 0.1.0 - 2026-09-29

First public release.

### Added
- Server: one company per environment, holding people, bots, tasks, chats, approvals, routines,
  meetings, files and an audit trail in SQLite, with the routine scheduler built in.
- Runner: executes AI employee bots one leased attempt at a time on a company machine, through the
  model CLIs the company is already signed in to, so credentials never leave that machine.
- Web interface and desktop app (macOS, Windows, Linux): the org chart, Updates (the bots' daily
  and weekly reports), Tasks, chat with bots, Needs you approvals, Integrations, Settings and help.
- Bots: created from templates in their own git repositories, with a first-run onboarding wizard
  and BotOps, the bot that sets the others up.
- Shared hub over MCP and the `hub` CLI, so any agent can use the same tasks, messages and
  company context as people.
- Connectors for mail and calendar, meetings, Slack and GitHub, plus a credential vault.
- Sign-in through an identity-aware proxy (Cloudflare Access or AWS ALB with Cognito), or loopback
  sign-in for a local install.
- Hosting: local only on a Mac, or self-hosted, including a reference AWS stack under `infra/ec2/`
  with Litestream backups.

[Unreleased]: https://github.com/ticoteam/tico/compare/v0.3.21...HEAD
[0.3.21]: https://github.com/ticoteam/tico/compare/v0.3.20...v0.3.21
[0.3.20]: https://github.com/ticoteam/tico/compare/v0.3.19...v0.3.20
[0.3.19]: https://github.com/ticoteam/tico/compare/v0.3.18...v0.3.19
[0.3.18]: https://github.com/ticoteam/tico/compare/v0.3.17...v0.3.18
[0.3.17]: https://github.com/ticoteam/tico/compare/v0.3.16...v0.3.17
[0.3.16]: https://github.com/ticoteam/tico/compare/v0.3.15...v0.3.16
[0.3.15]: https://github.com/ticoteam/tico/compare/v0.3.14...v0.3.15
[0.3.14]: https://github.com/ticoteam/tico/compare/v0.3.13...v0.3.14
[0.3.13]: https://github.com/ticoteam/tico/compare/v0.3.12...v0.3.13
[0.3.12]: https://github.com/ticoteam/tico/compare/v0.3.11...v0.3.12
[0.3.0]: https://github.com/ticoteam/tico/compare/v0.2.42...v0.3.0
[0.2.39]: https://github.com/ticoteam/tico/compare/v0.2.38...v0.2.39
[0.2.35]: https://github.com/ticoteam/tico/compare/v0.2.34...v0.2.35
[0.2.34]: https://github.com/ticoteam/tico/compare/v0.2.33...v0.2.34
[0.2.33]: https://github.com/ticoteam/tico/compare/v0.2.32...v0.2.33
[0.2.32]: https://github.com/ticoteam/tico/compare/v0.2.31...v0.2.32
[0.2.31]: https://github.com/ticoteam/tico/compare/v0.2.30...v0.2.31
[0.2.30]: https://github.com/ticoteam/tico/compare/v0.2.29...v0.2.30
[0.2.20]: https://github.com/ticoteam/tico/compare/v0.2.19...v0.2.20
[0.2.19]: https://github.com/ticoteam/tico/compare/v0.2.18...v0.2.19
[0.2.18]: https://github.com/ticoteam/tico/compare/v0.2.17...v0.2.18
[0.2.17]: https://github.com/ticoteam/tico/compare/v0.2.16...v0.2.17
[0.2.16]: https://github.com/ticoteam/tico/compare/v0.2.15...v0.2.16
[0.2.15]: https://github.com/ticoteam/tico/compare/v0.2.14...v0.2.15
[0.2.14]: https://github.com/ticoteam/tico/compare/v0.2.13...v0.2.14
[0.2.13]: https://github.com/ticoteam/tico/compare/v0.2.12...v0.2.13
[0.2.12]: https://github.com/ticoteam/tico/compare/v0.2.11...v0.2.12
[0.2.11]: https://github.com/ticoteam/tico/compare/v0.2.10...v0.2.11
[0.2.10]: https://github.com/ticoteam/tico/compare/v0.2.9...v0.2.10
[0.2.9]: https://github.com/ticoteam/tico/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/ticoteam/tico/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/ticoteam/tico/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/ticoteam/tico/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/ticoteam/tico/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/ticoteam/tico/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/ticoteam/tico/releases/tag/v0.2.3
