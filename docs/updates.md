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
Search changes, expand a release's remaining bullets, or open its full release notes. Opening the
page marks the product entries it shows as read, and **Load older changes** does the same for the ones
it adds; they stay listed and marked **New** until you leave the page.
Read status belongs to each person and follows them across sign-ins and devices.
A new company starts caught up: when the owner completes Finish setup, every release shipped before then is marked
read for them, so a fresh install shows no count on **What's new**. Later releases count as usual, everyone else keeps
their own read status, and completing setup a second time changes nothing.

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
- Every heartbeat carries the computer's release, the commit it runs (an image's `release-manifest.json`, else the
  checkout's HEAD), its kind (`mac`, `linux` or `docker`) and how its update stands.
  **Settings > Health** and **Settings > Computers** show one state per computer:

  | State | Meaning |
  |---|---|
  | Up to date | on the server's release, or newer |
  | Updating | switching now; it takes no new work until the update ends |
  | Needs update | older than the server but still accepted. It updates as soon as no run is going ([how it waits](#a-mac-or-linux-checkout)), or it says why it cannot (pinned, local changes, no updater, no supervisor) next to the last update error |
  | Incompatible (bots paused) | older than the minimum. It takes no work, and says why, instead of failing runs. Its queued work waits and runs after it updates |
  | Release not reported | a computer that predates this, or a development build. It is never paused |

- A server that is a development build (no release) has nothing to compare, so computers keep following `main` as
  before and Settings > Health shows no version line. A computer talking to a server from before this existed does the same.

A computer behind the server's release, or on no release at all, is **outdated**: its row in Settings > Computers carries
an Outdated badge, and the page opens with a banner naming the online ones and whose they are. When a computer has stayed
outdated for an hour (time for the self-update that normally follows), its owner gets one notice; the next notice comes
only with the next server release.

Health names computers that did not report a version and shows a newer computer's actual release separately.
Computers lists team service status once, alongside the computer reports. Archived bots are left out of readiness.
The MCP `hub_health_check` includes the same checks and fixes as Settings > Health, including watchers and queued work.
Computers report whether a rejected model key came from Credentials or the computer only after the server
advertises support. After a server rollback, they omit that field and keep reporting readiness; a later server
upgrade enables it again.

## Python environments on a Docker runner

The runner image brings its own Python, and a release can move it to a new minor version (3.12 to 3.15 in the
release that moved the images to Debian trixie). The image's own venv (`/opt/tico/.venv`) comes with it. The runner's
home volume (`/home/runner`) is kept across updates, and a Python environment there belongs to the Python that built it:

- **Tico's mail venv** (`~/tools/mail-venv`, or `TICO_MAIL_VENV`) rebuilds itself on the next `mail.sh`, which logs
  "built by another Python, rebuilding it". Nothing to do.
- **A venv a bot or person made** (a project's `.venv`, say) still points at the old interpreter, which the new image
  does not have, so its `python` fails with "No such file or directory". Delete it and build it again with the image's
  Python, or let the project's own setup do so.
- **`pip install --user` packages** sit under `~/.local/lib/python3.12/` and the new Python does not look there.
  Install them again with the new Python; delete the old folder once nothing needs it.

To list what an update left behind, from the computer that runs the runner (`tico-runner` is the container's name; a
computer added with `--name` has its own):

```bash
docker exec tico-runner sh -c 'v=$(python3 -c "import sys; print(\"%d.%d\" % sys.version_info[:2])");
  find /home/runner -name pyvenv.cfg -not -path "*/node_modules/*" 2>/dev/null \
    | xargs -r grep -LE "^version(_info)? *= *$v\." ; ls -d /home/runner/.local/lib/python3.* 2>/dev/null | grep -v "python$v\$"'
```

Each line is a venv (its `pyvenv.cfg`) or a user-install folder built by another Python.

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

1. waits until no run is going, then updates. For the first 20 minutes it keeps taking new work as usual; after
   that it takes no new work until every run that is going has ended, so a busy computer cannot hold the update
   back for ever. It never interrupts a run. Work sent to it after those 20 minutes waits for the longest run
   still going. Meanwhile that work says why in the chat and on its task: **Waiting for the computer to update**
   instead of Queued, until the computer takes work again (this also shows while the update itself runs).
   Settings > Health shows the computer as Needs update, says since when it has taken no new work, and names each
   run it waits for: the bot, its task and when the run started (a bot or task you may not read is not named). A run that only waits
   on something elsewhere (another computer's update, a review) can note the wait on its task and end, and pick it
   up on its next run. Stopping a run is left to a person (Runs); Tico never stops one to update;
2. starts a separate update process, which fetches the tag `vX.Y.Z` from `origin` (the public repository) and
   checks that it exists and is not a tag that moved, then checks out that commit (detached);
3. re-runs the install step: `pip install -r backend/requirements.txt` when the release changed it, and gets the
   release's runner binary ([The runner](#the-runner)); when that fails it goes back to the old commit;
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
stops the server (Litestream runs inside it), puts the snapshot back through a temporary file, clears Litestream's tracking directory so it does not take the restored file for a break (a readable copy of the migrated database stays beside it in a `hub.sqlite.failed-update-*` folder) and starts
the old image. Changes made between the snapshot and the rollback are not in it. The update status says which snapshot
was taken and whether it was restored (`snapshot`, `restored`, and the message). If the snapshot cannot be taken the
update does not go ahead. The restore runs in a one-off container (`docker compose run`); if it runs over its time limit,
stopping the command does not stop that container, so the updater says `uncertain` and starts the old server only once
no one-off container of the server is still running (one that was created but never started is removed). Until then the
server stays stopped and the status message says it is waiting for the one-off. Any running one-off server container
holds it, a restore you started by hand included. A restore normally takes seconds; if one has run for many minutes,
`docker ps --filter label=com.docker.compose.oneoff=True` shows it, `docker logs <id>` shows where it is, and stopping
it (`docker stop <id>`) lets the updater go on. The restore copies the migrated database into a
`hub.sqlite.failed-update-*` folder before it removes anything and puts the snapshot in place with one rename at the
end. Stopped before that rename, `/data/hub.sqlite` is the migrated database without its newest commits (the `-wal`
file is already removed; those commits are in the folder's `recovery.sqlite`), so check which one it is before the
server is used.

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

## The runner

Every computer runs the Rust runner (`tico-runner`, from `runner-rs/`). The launchd job and the systemd unit start
`python -m runner run`, which execs the binary; the Docker image runs the binary directly.

- **Docker:** the image ships the binary (`/usr/local/bin/tico-runner`), built from the same tree.
- **Mac or Linux checkout:** a checkout on a release tag downloads that release's `tico-runner-<os>-<arch>.gz` from
  GitHub, checked against the release's `tico-runner-SHA256SUMS`, into `runner-rs/target/dist/`. Any other checkout
  with `cargo` on PATH builds it (`cargo build --release`). `scripts/tico install` and every update get the binary
  before the runner restarts (`python -m runner.engine prepare`); an update that cannot get it backs out and the
  runner keeps running the old release.

A binary is only run when it was built from this checkout's `runner-rs` sources. When there is none and none can be
had, the runner does not start: it logs why and launchd or systemd tries again. A bot on a model CLI the runner does
not drive is not ready, with the reason on that bot; the other bots on the computer run.

The Python runner is gone. `"engine": "python"` in `runner.json` and `TICO_RUNNER_ENGINE=python` are ignored. Python
still runs the side jobs (connectors, Close calls, meeting importers), the catalog set-up and the update process.

## A Docker runner

The runner computer's `docker/runner.compose.yaml` has an `updater` sidecar, the same image and code as the server's
updater (`docker/updater.py`) in runner mode (`TICO_UPDATER_MODE=runner`). The runner waits until no run is going,
the same way as a checkout (it keeps taking work for 20 minutes, then takes none until every run has
ended, and never interrupts one), then asks the sidecar for the release its server names; it pulls `ghcr.io/ticoteam/tico-runner:vX.Y.Z`, replaces `runner.compose.yaml` with the release's copy (same checksum check, rolled back with the image), recreates the runner
container, waits up to three minutes for the container's health check, and puts the old image back if it does not
turn healthy. The runner then reports the outcome (`rolled_back`, `failed`) from the sidecar's `/status`, and does not
ask for that release again unless the failure was disk space and space has since freed. A sidecar that does not answer, or refuses the call, cannot start an update, so the runner reports `failed` ("the updater did not answer", or "the updater answered" and the status code) and keeps taking work; it asks the sidecar again on its next check. While the sidecar says `pulling` or `restarting` the runner takes no work, so the sidecar always finishes: each Docker command it runs has a time limit and is stopped when it runs over, an overrun is rolled back like any failure, and it says `rolled_back` or `failed` only after its last command. Stopping a command does not cancel what Docker already accepted (a stop it accepted runs to its end), so when a command that changes containers runs over, the outcome is unknown: the sidecar says `uncertain`, and the runner takes no work, whatever its version. The sidecar then recreates the runner on the old version until Docker answers and that recreate's container is the only one (an earlier stop can act only on the containers it was given, which the recreate removed), and only then says `rolled_back`, or `failed` if it is not healthy. It retries every minute, says why in its message, picks this up again after it restarts (it saves `restarting` before the switch, so an updater stopped mid-switch settles it too), and refuses a new update meanwhile. Once the runner sees `restarting` or `uncertain`, it keeps its hold in its state directory until the sidecar reports `rolled_back`, `failed` or `healthy` for that same update: a sidecar that stops answering, answers `idle` (its status lost) or reports an older update does not end it, and neither does a runner restart. The runner also keeps the hold in memory, so one it could not save (a full disk) still holds while it runs, its error says the save failed, and the save is tried again on every check (with or without an answer from the sidecar) until it is on disk; a saved hold that cannot be read holds until someone removes it. A sidecar that lost its status (it answers `idle`, for example an older updater restarted mid-switch) never reports the outcome, so that hold ends only by hand. In the install directory, check that `docker compose ps -a runner` shows a single runner container, then run `docker compose exec runner sh -c 'rm /home/runner/state-*/docker-update-hold.json' && docker compose restart runner`. The same ends a saved hold that cannot be read. Removing the file alone is not enough, because the running runner saves its hold again. An updater stopped after a switch that went well also settles back to the old version and says `rolled_back`; as after any rollback, the runner does not ask for that release again, and the next release updates it as usual. Only a sidecar that does not answer when no update was changing containers leaves the runner claiming, as above. The sign-in and the bots' repositories are in the `runner-home` volume and are kept.

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

Pinning keeps a computer where it is. When the server moves on it shows **Needs update** with an **Outdated** badge and
"pinned" beside it, and "Last update error: pinned to this release; the server runs X.Y.Z" under it; its owner gets no
reminder to update. Once it falls below the minimum it is paused, so pin only while you plan to update by hand.

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

The Assistant, BotOps, the Librarian and the CEO Bot keep their instructions and playbooks in a repository on the computer that
runs them. When a release changes one of those files in its template (`AGENT.md`, `playbooks/`, `skills/`), the computer brings that file up
before the bot's next run, once per start of the runner; a file the release did not change keeps whatever the bot improved since. What the
bot had before is kept in the repository's history. The bot's own notes, knowledge, state and playbooks it wrote are never touched
(`clients/catalog.py`, `refresh`).

## Coming from an older version

### You're on 0.2: update to get one-tap support

Tico 0.3 can reach the Tico team from anywhere in the app, with what went wrong attached, and shows replies, fixed-in notices
and **Known issues in your version** under Help. On 0.2, open **New version** in the sidebar and press **Update now** (the
owner), or follow the steps for your version below. Help > Contact support keeps working on 0.2 until you do.

Tico HQ can also show a short message for a version once in the app ([tico-hq.md](tico-hq.md#messages-known-issues-and-fixed-in-notices)).

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
  after updating.

## For maintainers

Bump `MIN_RUNNER_RELEASE` in the release that changes the runner/server contract, and say so in the changelog.
Ship the server and runner image for that release together, and expect computers to move within a few minutes of
the server's restart.

Computers shows installed and wanted releases, update state and last error. `hub_computer_list` gives each computer's
releases and update state (`current`, `update_available`, `updating`, `failed`, `incompatible` or `unknown`) in one
short row; `hub_computer_list computer=<label or id>` adds the last error.
`hub_health_check` includes that Computer detail and service errors with the next step.
