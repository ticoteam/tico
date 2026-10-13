# Repositories

Settings → Repositories lists repositories in the connected GitHub organization. An Owner or admin ticks the repositories the team works on. Every human teammate can read the list. BotOps uses the rights of whoever asked it.

Bot repositories are marked by `bot-<slug>` names or by their use as a bot's own repository. The list reads GitHub on connection, when you refresh, and daily. Apps created since v0.3.1 also refresh on installation webhooks; older Apps may have an inactive webhook. A repository that disappears stays listed as not reachable; its setting is kept. Missing product repositories are omitted from tokens for five minutes before retrying and reported in Health when ticked. Missing-repository probes are cached for five minutes, including a missing own repository, so other grants are not repeatedly probed. A bot’s own token is always retried; a missing Instructions repository is reported for that bot without turning team GitHub health red. Creating a repository or successfully minting its token clears its missing mark.

A repository's default branch comes from GitHub. Its setup command comes from `tico.json` (`{"setup":"npm ci"}`), then `conductor.json` (`{"scripts":{"setup":"npm ci"}}`). Setup files are read only for ticked repositories; a temporary read failure or malformed file keeps the previous command. Only removing both setup files clears it. A command saved in Settings wins, including an empty command. Setup commands must fit within 4 KB of UTF-8 text. An oversized setup file keeps the previous command. Task worktrees run setup with process and toolchain settings, without credential tokens; see [Task worktrees](worktrees.md).

## Bot repository access

Each bot has one mode:

- **Own repo only:** its Instructions repository, with write access.
- **All ticked repos:** every reachable product repository ticked now and later (other bots’ Instructions repositories are excluded), with read or write access. Write is the default.
- **Chosen repos:** chosen ticked repositories, with read or write access for each.

The bot always keeps write access to its own repository. Read grants allow reading contents. Write grants also allow changing contents, issues and pull requests. Repositories must belong to the connected organization. Unticking a repository removes it from all and chosen effective access; its stored chosen grant stays available if it is ticked again. Bot settings can save these unticked choices without removing them. Tokens already issued expire within an hour.

Settings → Repositories also sets **New bots get**: own only (the default), or all ticked repositories. This applies when a bot is created; existing bots keep their access. Existing extra repositories migrate once to chosen write access and are ticked automatically, including archived bots' grants.

## Tools and commands

MCP tools: `hub_repo_list`, `hub_repo_update`, `hub_bot_repos_get`, `hub_bot_repos_set`.

```sh
hub repo list
hub repo tick example/product
hub repo untick example/product
hub bot repos sales
hub bot repos sales --all
hub bot repos sales --chosen example/product example/docs:read
hub bot repos sales --own
```

## API

- `GET /api/v2/repositories`: repositories, `new_bot_default`, `github_connected`.
- `POST /api/v2/repositories/refresh`: refresh GitHub and return the same answer.
- `PUT /api/v2/repositories/{owner}/{repo}`: `enabled` and/or `setup_command`.
- `PUT /api/v2/repositories/settings`: `new_bot_default` (`own` or `all`).
- `GET /api/v2/bots/{bot}/repositories`: `mode`, `all_access`, `chosen`, `effective`.
- `PUT /api/v2/bots/{bot}/repositories`: `mode`, optional `all_access`, optional `chosen` entries with `full_name` and `access`.

Changes are audited as `repositories.changed` or `bot.repos_changed`, with before and after values. The older `GET/PUT /api/v2/bots/{bot}/github-repos` API remains an alias for chosen write repositories; only an Owner or admin may tick new repositories through it. Other bot managers can choose already ticked product repositories. Legacy saves preserve existing grants and their read or write level, adding only new write grants. Use the current repository API to remove or change a grant. Changing an own or all mode through this alias asks the caller to use Settings. Only an Owner or admin can explicitly choose another bot’s repository. Existing chosen grants remain available; upgrading another bot’s repository from read to write requires an Owner or admin.

## Computers and tokens

`GET /api/v2/runners/me/repositories` returns ticked repositories accessible to active bots assigned to that Computer, including each repository's bots, default branch, setup command and widest access. `POST /api/v2/runners/me/repositories/token` issues a contents-read token for exactly those repositories. An empty list returns no token. Heartbeats may report clone state, fetch time, size and an error per repository; older Computers show unknown. A temporary missing report preserves the last known state. Only the latest report is stored.

GitHub applies permissions to a whole installation token, so mixed bot grants use separate write and read tokens. `POST /api/v2/github/token` keeps the existing `token` field for the write repositories (including the bot's own), and adds `tokens` entries containing `token`, `expires_at`, `repositories`, and `access`. Callers can also send `repository` with `bot` to obtain a token for one effective repository. This never expands access. Computers select the corresponding token for each Git remote through an environment-only credential helper. GitHub CLI commands select it from `--repo`/`-R`, a `gh repo view/clone/fork owner/repo` argument, a GitHub URL, a `repos/owner/repo` API path, `GH_REPO`, or the current checkout’s origin. Commands without a repository use the write token; for a read repository, specify the repository or run from its checkout. Each read and write token is redacted individually, including tokens refreshed through the supervisor’s credential socket during a turn. Tokens stay in memory and are never saved in Git config. Older Computers retain access to the bot's own repository and existing chosen write repositories.

The supervisor keeps one bare mirror per repository at `<runner state dir>/mirrors/<owner>__<repo>.git`. It fetches only the default branch, with no automatic garbage collection or maintenance and without writing FETCH_HEAD. It refuses symlinks anywhere in a mirror. Mirrors belong to the supervisor, with 0755 directories and 0644 files on isolated Linux Computers, so bots can read them but cannot write them. The state directory allows traversal without listing (0711); existing state entries remain private (0700 directories and 0600 files), and the state database is created with mode 0600.

Only the supervisor fetches GitHub into mirrors with the computer's read token. Hooks and fsmonitor are disabled, global and system Git configs are ignored, mirror HTTP overrides are neutralized, and standard proxy and CA environment settings are preserved. Git always starts in a supervisor-owned temporary directory. No authenticated supervisor Git command runs inside a bot-owned repository.

Base clones at `<workspace>/repos/<owner>__<repo>` are full working copies owned by the bot user. They clone and refresh from the local mirror as that user, without a token or network access. The `tico-mirror` remote fetches locally; `origin` points to GitHub for pushes using the bot's scoped turn token. Computers update both tracking namespaces so task worktrees can start at `origin/<default branch>` offline. Existing managed clones gain the mirror remote on their next refresh and keep local commits, files and worktrees. A broken clone is reported for repair without deleting local work. A clone that failed or was stopped partway, in a folder it created, is removed so the next try starts clean.

If copying the mirror into the workspace fails, or the mirror itself cannot be fetched because it is corrupt, half-written, locked or unreadable, the Computer deletes the mirror, fetches it again from GitHub and retries once. It holds only what GitHub has. If that still fails, both reasons are reported. A base clone made during a bot run that cannot be copied from the mirror is cloned from GitHub with the bot's own access, and the run notes it.

On a Computer that does not run bots as a separate user, such as a Mac, a folder already at `<workspace>/repos/<owner>__<repo>` is taken over when it is a Git checkout of that repository: its `origin` is GitHub's HTTPS or SSH address for it, it belongs to the user the runner runs as, and it does not share objects with another repository or keep its work tree elsewhere. Tico adds the `tico-mirror` remote and updates the tracking refs. It leaves `origin`, branch settings, files and local work as they are. When no bot needs the repository any more, Tico stops managing the folder but never deletes it. Any other folder is left alone, and the error says why.

Bots on one Computer can read each other’s base clones until per-subscription Unix users exist. Same-user computers, including Macs, also allow inspecting processes owned by that user; repository grants restrict issued tokens, not local files. Full object stores let task worktrees commit offline.

Clone and fetch operations check a free-space floor of 5 GB plus twice the repository's known size. They have at least 15 minutes to complete, and allow up to two hours based on repository size. A failure is retried after 5 minutes, then the wait doubles up to six hours. The error keeps Git's own reason, such as `Repository not found`, `RPC failed; HTTP 500`, `Filename too long`, an LFS error or a timeout. Credentials are masked, and the error says when the next try is. It shows on the Computer, and Health lists it under Repository copies. To retry now, restart that Computer's runner: in Settings > Computers press Restart, or as BotOps run `hub api POST /api/v2/runners/<computer id>/restart`. A restart clears the wait for every failed repository. Unused base clones and their mirrors are removed after 30 days when no live task worktrees, unpublished commits or local-only branches remain. Worktree bases share the managed marker, so clones created during bot runs are also refreshed and retired. Restarting or stopping a Computer interrupts in-flight Git and its children; stalled processes cannot interrupt the remaining shutdown cleanup.

When a GitHub App is connected, a failed token request blocks GitHub Git and CLI operations. The message names the cause, for example that the App is not installed on the repository or that the repository is outside the connected organization. Machine credential helpers and askpass cannot supply wider credentials. With no App connected, the computer's existing Git login keeps working. Text supplied through GitHub CLI options such as `--comment`, `--body`, `--title`, `--notes`, `--jq`, `-m`, `-f` and `-F` does not select a repository token.

Computer answers (`GET /api/v2/computers`, Operations and `hub_computer_list`) include `release`
for the installed Tico release. The legacy `version` field is the runner software version.
A Computer that does not report its release returns an empty `release`.
