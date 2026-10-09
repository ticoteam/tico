# Updates: the server and its computers

A Tico installation is a server plus the computers that run its bots (a Mac, a Linux computer with the
`tico-runner` container, or a Linux computer running from a Tico checkout). The server updates from Settings ("Update now", see [install.md](install.md#one-click-updates))
or by running the target release's installer again (below). Pulling the same pinned tag does not upgrade Tico. **Computers follow the server**: each one asks its server
which release it runs and moves to that release. GitHub is not consulted by a computer, so a server you have not
updated never drags its computers ahead of it.

## What changed since your last look

**What's new** in the sidebar and **Changelog** in the account menu show product changes you
have not acknowledged. **Since last look** includes every unread release and team announcement;
choose **All product updates** to browse history or **All activity** to include accessible bot outcomes.
Search changes, expand a release's remaining bullets, or open its full release notes. **Mark shown
as read** acknowledges just the product entries in the current view, including any search filter.
Read status belongs to each person and follows them across sign-ins and devices.

Release entries come directly from the installed `CHANGELOG.md`. They work offline and require
neither a Git checkout nor manual announcement posting. Unreleased notes and releases newer than
the running server are excluded. The owner can still publish team product announcements.

The same history is available to signed-in users through the stable API and MCP:

- `GET /api/v2/changelog`: `kind=product|activity|all` (default `all`), `unread=true`, `q` search,
  `since_version=0.3.0` for releases newer than that version, and `limit` (1–250) / `offset` paging.
  Each entry includes its `id`, `title`, `bullets`, `shipped_at` and `kind`; product entries include
  `unread`, and releases add `version` and a full-notes `url`. The response also gives
  `current_version`, `unread_count` and `next_offset` (`null` at the end). The unread count covers
  all product entries, independently of the current filter.
- `POST /api/v2/changelog/read` with `{"ids":["release-0.3.0"]}` and an `Idempotency-Key`
  acknowledges only those known product entries for the caller. Unknown or future ids are ignored.
- MCP `hub_changelog_list` accepts the same filters and defaults to product changes;
  `hub_changelog_mark_read` accepts `ids` from that result. Listing does not mark anything read.
  For a catch-up request, list unread changes, show the user the summary, and acknowledge the
  entries shown when they ask to mark them read.
- CLI: `hub changelog list --unread`, `hub changelog list --since-version 0.3.0`, and
  `hub changelog mark-read release-0.3.0`.

## Manual server update

From the install directory, run the installer for the release you want:

```bash
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh -o install.sh
sh install.sh --version vX.Y.Z --dir "$PWD"
```

Replace `vX.Y.Z` with the target release (for this release, `v0.3.3`). Update the server first;
computers follow over the next few minutes. A pinned or offline computer keeps its version until
unpinned or reconnected; existing bot Credential grants remain in place during that mixed-version window.
New one-time Credential migrations wait for a runner version that can perform them.

It verifies and refreshes the release bundle and changes `TICO_TAG`, keeping your other
`.env` settings. For a local install, add `--local --owner-email you@example.com`. Raw `docker compose pull` refreshes the configured
tag only; a raw-compose upgrade also requires changing that tag and refreshing the bundle first. Use **Update now** for the
health check and automatic rollback described below.

## How they relate

- The server knows its release (`GET /api/v2/runners/desired` returns it, and Settings > Computers shows it) and the
  oldest computer release it accepts, `MIN_RUNNER_RELEASE` in `backend/runner_versions.py`. The release that changes
  the contract between runner and server raises that number.
- Every heartbeat carries the computer's release, its kind (`mac`, `linux` or `docker`) and how its update stands.
  **Settings > Health** and **Settings > Computers** show one state per computer:

  | State | Meaning |
  |---|---|
  | Up to date | on the server's release, or newer |
  | Updating | switching now; it takes no new work meanwhile |
  | Needs update | older than the server but still accepted. It updates when no run is going, or it says why it cannot (pinned, local changes, no updater, no supervisor) next to the last update error |
  | Incompatible (bots paused) | older than the minimum. It takes no work, and says why, instead of failing runs. Its queued work waits and runs after it updates |
  | Version not reported | a computer that predates this, or a development build. It is never paused |

- A server that is a development build (no release) has nothing to compare, so computers keep following `main` as
  before and Settings > Health shows no version line. A computer talking to a server from before this existed does the same.

Health names computers that did not report a version and shows a newer computer's actual release separately.
Computers lists team service status once, alongside the computer reports. Archived bots are left out of readiness.
The MCP `hub_health_check` includes the same checks and fixes as Settings > Health, including watchers and queued work.
Computers report whether a rejected model key came from Credentials or the computer only after the server
advertises support. After a server rollback, they omit that field and keep reporting readiness; a later server
upgrade enables it again.

## Disk space

Health warns when a computer reports that its disk is over 85% full. Free space on that computer; for a Docker install,
`docker image prune -a` removes unused images. This can remove a cached rollback image, so the previous release may
need to be pulled again if you later roll back. Disk-space update failures say what ran out and retry when free space
increases, including while the server and computers are on different releases. Older computers that do not report disk
space keep working; they show the warning after their runner updates.

New base clones need at least 5 GB free or 10% of the workspace volume's capacity, whichever is
greater. Below that floor the computer reports `disk_low`, with the free space and space needed.
Free space on that volume to retry. Base clones absent from the computer's repository list for
30 days are removed; bot folders are left in place.

## A Mac (or Linux checkout)

The runner is a git checkout. When the server's release is newer, the runner:

1. stops taking new work and waits until no run is going (after 20 minutes it stops claiming so a long run
   cannot hold the update back for ever; it never interrupts one);
2. starts a separate update process, which fetches the tag `vX.Y.Z` from `origin` (the public repository) and
   checks that it exists and is not a tag that moved, then checks out that commit (detached);
3. re-runs the install step: `pip install -r backend/requirements.txt` when the release changed it;
4. restarts the runner through its supervisor (launchd `KeepAlive` on a Mac; on Linux `systemctl --user restart` on
   the runner's systemd unit, which has `Restart=always`) and waits up to three minutes for the new process to report in;
5. if it does not, checks the old commit out again, restores the old dependencies, restarts and reports
   `rolled_back` (or `failed` if the old code does not start either). It does not retry that release for six hours,
   or until the server's release changes. A disk-space failure retries as soon as free space increases.
6. once the runner is healthy on the new release, restarts the background jobs that are installed on this Mac
   (`connectors`, `close-calls`, `importers`; the same `launchctl kickstart -k` or `systemctl --user restart` as
   `scripts/tico restart`), so none keeps the old release in memory. A job that is not installed is left alone. Each job also checks the checkout's
   revision about once a minute and exits when it changes, so its supervisor starts it on the new code even after a
   `scripts/tico update` or a pull by hand. Each restart is a line in the job's log
   (`scripts/tico logs connectors`) and in `update.log`. A Docker runner replaces its container, jobs included.

A runner's release is the one it started on, not whatever the checkout names now. The runners of several company
environments (`scripts/tico -e <env> install`) may share one checkout: the first to update moves it, and each other
runner, still on the old release, finds the checkout already there, runs the install step for its own Python and
restarts onto it (with its own jobs). The same happens after a `git checkout` of a release by hand. Their updates take
turns on a lock in the checkout's git directory, so one never moves the checkout while another is mid-update.

### Linux from a checkout: run `scripts/tico install` once

A Mac has launchd to start the runner again after an update. A Linux computer running from a Tico checkout has nothing
unless you give it something, and an update that stopped the runner with no supervisor would leave the computer offline.
So a checkout runner that nothing supervises **refuses the update and says so** in Settings > Computers and Health:
"no supervisor would start this runner again after an update: run `scripts/tico install` once".

Run it once on that computer, as the user the runner runs as (enroll first: [install.md](install.md#a-linux-checkout-systemd)):

```
scripts/tico install            # the runner, connectors, close-calls and importers
```

It writes four systemd **user** units under `~/.config/systemd/user` (`tico-bot.service`, `tico-connectors.service`,
`tico-close-calls.service`, `tico-importers.service`; `tico-<env>-...` for a team environment), each with
`Restart=always`, and starts them. Nothing needs root. The units set `TICO_SUPERVISED=1` and `TICO_SYSTEMD_UNIT`, which is how
the runner knows something will start it again and how the update restarts it through `systemctl --user restart`. The update
process itself runs as a transient `tico-update-<pid>` unit so the restart does not kill it. Linger is checked at install
time; if it is off the command prints `loginctl enable-linger $USER`, because without it the jobs stop when the last session
logs out and do not start at boot. `scripts/tico status`, `restart`, `logs` and `uninstall` work the same as on a Mac.

A checkout with uncommitted changes, or on a branch other than `main`, is **refused, never touched**: nothing is
stashed, reset or discarded. Settings > Health and Computers show "the checkout has 2 changed files" until someone commits or
stashes them. The update process logs to `state-<runner id>/update.log`; the outcome is in `update-status.json` next
to it. `scripts/tico update` still works by hand and still follows `main`.

## The server's own updater

The server's `updater` service is pinned to the same release as the server (`TICO_UPDATER_TAG`, which defaults to
`TICO_TAG`). "Update now" first downloads that release's `tico-bundle-vX.Y.Z.tar.gz` and `SHA256SUMS` (the files
`install.sh` uses), checks the checksum, and only then replaces the bundle in the install directory (`compose.yaml`,
`.env.example`, `docker/runner.compose.yaml`, `setup/`, and so on) file by file with atomic renames, so new settings and
services that need a compose change reach existing installs. `.env` is never replaced (only its `TICO_TAG` line moves).
The previous files are kept in `.bundle-previous/`. A download or checksum failure refuses the update before anything
changes; a release that does not turn healthy is rolled back, image and bundle together. Slack and the front door are
recreated from the new file when it changes them.

Before anything changes, the updater also renders the compose files with the target tag (`docker compose config`) and
refuses the update when `server`, or `slack` when it runs, would not get that release's image: an override that sets
`image:` would keep it on the old one. After the switch the server must run the image just pulled and report the target
release on `/healthz`; otherwise the update counts as unhealthy and is rolled back.

`GET /api/v2/system/update` also says which release the server is `running`, the one it ran `previous`ly and a short
`history`. The server writes these itself at startup, so they stay right after an update done outside the updater.

Before it switches the server image, the updater takes a consistent SQLite snapshot (`sqlite3` backup API, run in the
server container) into `/data/snapshots/pre-update-<version>-<time>.sqlite` and keeps the last three. A new version can
migrate the database and then fail its health check, and the old image cannot read a newer schema, so a rollback also
stops the server (Litestream runs inside it), puts the snapshot back through a temporary file, clears Litestream's tracking directory so it does not take the restored file for a break (the migrated database stays beside it as `hub.sqlite.failed-update`) and starts
the old image. Changes made between the snapshot and the rollback are not in it. The update status says which snapshot
was taken and whether it was restored (`snapshot`, `restored`, and the message). If the snapshot cannot be taken the
update does not go ahead.

After a successful update the updater replaces itself, so updater fixes reach existing installs. It pulls the new updater
image and starts a short-lived container (`tico-updater-swap`) from it, which recreates the `updater` service,
checks that the new one stays running, and moves `TICO_UPDATER_TAG` in `.env` if the install pinned it. If the new
updater does not stay up that container puts the old one back, so the install is never left without an updater; its log is
`docker logs tico-updater-swap` (`tico-updater-swap-<project>` for a second computer added with `--name`). `TICO_UPDATER_SELF=never` turns this off. The same applies to the runner computer's updater
sidecar. The last update's outcome is kept in `.updater-status.json` so the new updater still reports it.

Settings still reach the server through the explicit `environment:` list in `compose.yaml`, not `env_file: .env`, which
would also pass credentials that belong to other services (such as `CLOUDFLARE_TUNNEL_TOKEN`) into the server.

### Updating from another install

Besides the owner's **Update now**, an [update key](service-keys.md#update-keys) made by the owner can check for, start and
follow an update through the same routes and the same updater, and do nothing else. A Release Manager on one install uses
one key per install to roll a release out to all of them, canary first ([Releasing](releasing.md#the-release-managers-rollout)).
`GET /api/v2/system/update` answers with `computers`, the online computers counted by state, so the caller can see them
follow the server.

## Moving a hand-managed install onto the updater

An install run with its own `compose.override.yaml` can use **Update now**. The override may keep its environment,
volumes, entrypoint and the `cloudflared` command; it must not set `image:` for `server` or `slack`, or the updater
refuses every update. Then in `.env`:

```
COMPOSE_PROFILES=cloudflared,updater    # your existing profiles, plus updater
TICO_UPDATER_URL=http://updater:8080
TICO_TAG=vX.Y.Z                         # the release running now
```

and run `docker compose up -d`. From then on the updater moves `TICO_TAG`.

## A Docker runner

The runner computer's `docker/runner.compose.yaml` has an `updater` sidecar, the same image and code as the server's
updater (`docker/updater.py`) in runner mode (`TICO_UPDATER_MODE=runner`). When no run is going, the runner
asks it for the release its server names; it pulls `ghcr.io/ticoteam/tico-runner:vX.Y.Z`, replaces `runner.compose.yaml` with the release's copy (same checksum check, rolled back with the image), recreates the runner
container, waits up to three minutes for the container's health check, and puts the old image back if it does not
turn healthy. The runner then reports the outcome (`rolled_back`, `failed`) from the sidecar's `/status`, and does not
ask for that release again unless the failure was disk space and space has since freed. The sign-in and the bots' repositories are in the `runner-home` volume and are kept.

The Docker socket is mounted into the `updater` service only, never into the runner. The two share a `runner-control`
volume that holds a token the updater writes and the runner reads; the sidecar answers on the compose network and
accepts only `vX.Y.Z` tags of one image. A bot on the runner can therefore ask its own runner for another
release, and nothing else: the token is readable by the runner supervisor only (bots run as another user), and the sidecar refuses a release older than the one running. The Add computer command sets this up: `install.sh --runner` writes the compose file and a
`.env` with the image and updater tags pinned to the server's release ([install](install.md#linux-or-cloud-server-docker)).

A runner started with a plain `docker run` has no sidecar: it says so in Settings > Health, and you update it as before
(`docker pull`, then start it again). To move one onto the compose setup, run the `install.sh --runner` line from Add
computer on the same computer: it reuses the `tico-runner` volume (`TICO_RUNNER_HOME_VOLUME` in `.env`), so the sign-in,
the repositories and the enrollment stay, and the runner then follows the server.

## Pinning

Pinning keeps a computer where it is. It then shows "Needs update (pinned)" when the server moves on, and once it
falls below the minimum it is paused, so pin only while you plan to update by hand.

- Mac or Linux checkout: `"pinned": true` in the runner's config (`runner.json`), or `TICO_RUNNER_PINNED=1` in its
  environment. `"self_update": false` and `TICO_RUNNER_SELF_UPDATE=0` also stop it.
- Docker runner: `TICO_RUNNER_PINNED=1` in the runner computer's `.env`, then `docker compose -f runner.compose.yaml up -d`.
  To remove the sidecar's socket access altogether, delete the `updater` service.
- The server: leave `updater` out of `COMPOSE_PROFILES`, or set `TICO_TAG` to a tag in `.env`.

## Rolling back

Automatic rollback covers a release that does not come up. To go back by choice:

- **Server:** if the release you are leaving changed the database, the older one cannot read it, so put back a snapshot
  from before that update (`/data/snapshots`, above) with the server stopped and Litestream's record of the file cleared,
  or the next start can upload the restored file as if it were newer than the migrated one:

  ```
  docker compose stop server
  docker compose run --rm --no-deps --entrypoint sh server -c '
    ls /data/snapshots    # pick one, then:
    cp /data/snapshots/pre-update-....sqlite /data/hub.sqlite.restoring && sync &&
    rm -rf /data/.hub.sqlite-litestream /data/hub.sqlite-wal /data/hub.sqlite-shm &&
    mv /data/hub.sqlite /data/hub.sqlite.failed-update && mv /data/hub.sqlite.restoring /data/hub.sqlite'
  ```

  Then set `TICO_TAG=vX.Y.Z` in `.env`, `docker compose pull` and `docker compose up -d`. Without a database change,
  skip the middle step. Computers newer than the server are left as they are (they still work, as long as they are at or above the minimum).
- **Mac:** `git -C <checkout> checkout vX.Y.Z`, `scripts/tico restart`, and pin it (above) or the next heartbeat moves it
  back to the server's release.
- **Docker runner:** set `TICO_TAG=vX.Y.Z` and `TICO_RUNNER_PINNED=1` in `.env`, then `docker compose -f runner.compose.yaml up -d`.

## The built-in bots' instructions

The Assistant, BotOps, the Librarian and the Goal Manager keep their instructions and playbooks in a repository on the computer that
runs them. When a release changes one of those files in its template (`AGENT.md`, `playbooks/`, `skills/`), the computer brings that file up
before the bot's next run, once per start of the runner; a file the release did not change keeps whatever the bot improved since. What the
bot had before is kept in the repository's history. The bot's own notes, knowledge, state and playbooks it wrote are never touched
(`clients/catalog.py`, `refresh`).

## Coming from an older version

- **v0.2.5 or older:** the updaters of these versions cannot replace themselves or install new configuration files, so
  run the release's install command once more on each computer (server, then each runner computer). From then on, Update
  in the app does everything.
- **v0.2.6 to v0.2.8:** Update in the app to v0.2.9 or later. An updater that replaced itself in these versions stopped
  pulling images, so a Linux runner computer may then show "No such image" under Settings > Health and stay on its old
  version. Run this once in its directory (`/opt/tico-runner`):

  ```
  docker compose -f runner.compose.yaml up -d --no-deps --force-recreate updater
  ```

  Its next update follows the server as usual. The server's updater repairs itself when the server updates.
- **v0.2.10 or v0.2.11 runner computer whose updater keeps restarting** (`docker ps` shows `tico-runner-updater-1 Restarting`):
  run once in `/opt/tico-runner`:

  ```
  sed -i 's/^TICO_UPDATER_TAG=.*/TICO_UPDATER_TAG=v0.3.3/' .env && docker compose -f runner.compose.yaml up -d updater
  ```

- **v0.2.16 to v0.2.18:** the update is in the app, with no manual steps. Browser tabs left open show "New version · Reload".
  A Mac runner's background jobs now restart on their own after an update; on v0.2.17 or older, run `scripts/tico restart all` once
  after updating. If you run your own HQ, its `backup` service in `hq/compose.yaml` must run as uid 10005 (fixed in v0.2.18).

## For maintainers

Bump `MIN_RUNNER_RELEASE` in the release that changes the runner/server contract, and say so in the changelog.
Ship the server and runner image for that release together, and expect computers to move within a few minutes of
the server's restart.

Computers and `hub_computer_list` show installed and wanted releases, update state and last error.
`hub_health_check` includes that Computer detail and service errors with the next step.
