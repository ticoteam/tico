# Releasing

A release is a version tag. Pushing `vX.Y.Z` runs `.github/workflows/release.yml`, which builds the
installer bundle, checks the tag against [CHANGELOG.md](../CHANGELOG.md) and publishes the GitHub
release. Running installations look for that release to show "New version" in the sidebar.
The installed `CHANGELOG.md` also supplies the in-app, API and MCP product changelog, so each
release's versioned notes automatically reach its users. No separate product announcement is required.
Keep those versioned sections on `main` after publishing a tag; move only new work into Unreleased,
so later builds retain the complete history and do not announce already shipped work again.

**One release path.** On a team that runs Tico with a Release Manager bot, only the Release Manager publishes
releases. A Claude Code session or any other outside session that finishes work hands it to the Release Manager on a
task ("Release vX.Y.Z: <what>") instead of pushing the tag itself; the Release Manager tags, rolls out and records it,
so the tasks waiting on the release learn it shipped. Tico itself never moves a task on a tag, release or deploy; it
only marks the merged PR links the running release contains as Shipped (backend/github.py).

## The fast path

1. **Every PR** runs only the tests for what it changed (below).
2. **Before you tag**, and only then, the full suite: the default suites, the opt-in tests and a short whole-product
   check, targeted under 8 minutes on a shared Mac: `python scripts/release_checks.py --release`.
3. **Tag and push.** The GitHub release is published about 2 minutes later, as soon as the Docker images exist.
4. **Server rollout** starts at once: the canary install first, then the rest ("Update now", about 2 minutes each).
5. **Desktop follows**: built only when the shell changed, and attached to the published release when it is done.

## Tests on a PR

Tests run on your computer, not in CI: nothing in GitHub Actions runs the suite on a push or a pull request. Write unit
tests while you build and run them as you go; once the change passes, keep only the highest-value ones. Before merging,
run the test files for what you touched and the checks the change obviously affects, not the full suite. The full
suite runs as late as possible: once, right before a release.

```
python scripts/release_checks.py
```

That is the default suite: pytest in parallel without the tests marked `@pytest.mark.slow` (about a minute), then the
core browser scripts (`CORE` in `scripts/ui-tests.cjs`) with the load-aware concurrency described below. The check records wall time and load and fails
if the combined run reaches 300 seconds; it should take about two minutes. This is a hard budget for any suite that runs on merge or on a schedule. Keep it by keeping few tests, the ones
that guard security and privacy boundaries, data safety and core contracts, and by cutting one when you add one. CI only
builds and publishes: the Docker workflow builds the three images for a `v*` tag, and the Release workflow publishes the
GitHub release; no GitHub Actions workflow runs tests. The manual screenshots workflow
generates documentation images from the Actions tab (Run workflow). `--release` runs this suite too, so a
release is the one place it runs.

## Before you tag

```
python scripts/release_checks.py --release                     # this checkout is the candidate (v9.9.9)
python scripts/release_checks.py --release --previous v0.3.21  # upgrade from a given release instead of the newest tag
```

This is the full suite, run once per release. It builds the server, runner and updater images once (BuildKit cache;
a source-only change rebuilds one layer per image), while the journey installs the previous release. After every build
finishes, it runs every Python test except Docker isolation, the opt-in `@pytest.mark.slow` ones included (real git,
Docker, servers and long timers), and every browser script (`node scripts/ui-tests.cjs --all`). Python uses half the
CPU count in workers (at least one; six on a 12-core Mac); `TICO_PYTHON_WORKERS` overrides that default.
The browser runner samples CPU counters over one second at startup. The idle fraction is the change
in idle time divided by the change in total CPU time across all cores. Its job count is
`clamp(floor(cores * idleFraction), 1, 4)`, and every child gets the same `TICO_UI_SLOWDOWN`:
`clamp(1 / max(idleFraction * 2, 0.25), 1, 4)`. It prints jobs, slowdown, cores and idle percentage
before running scripts. On macOS, load average can stay high while several cores are idle.
Explicit `TICO_UI_JOBS` (or `-j N` when invoking the browser runner) and `TICO_UI_SLOWDOWN` win;
the release coordinator passes those overrides through without forcing four jobs. On a 12-core Mac
at 30% idle, the defaults are three jobs and slowdown 1.67; at 50% idle, four jobs and slowdown one.
If CPU counters do not advance or the core count changes during sampling, it conservatively uses
one job and slowdown four. The selected settings stay fixed for that run.
Shared browser support applies `30000 * slowdown` milliseconds to context/page action and navigation
timeouts, including popups. `ui/tests/support/load.cjs` exports `SLOWDOWN`, `t(ms)` (rounded scaled
milliseconds), and `applyTimeouts(contextOrPage)` for scripts' explicit waits. Direct script runs
default to slowdown one unless the environment sets it. Explicit per-script timeout options still
override the shared defaults; those waits should use `t(...)` and wait on conditions.
Successful builds also start the following checks against the candidate
at the same time, each with its own Docker names; the journey proceeds once those images are ready:

- `docker/smoke.sh`: the server comes up, a runner joins with a one-time code and installs Codex once OpenAI is enabled,
  the server restarts and the runner reconnects, the data survives down and up, and a runner box updates to the
  server's release and rolls back one that does not turn healthy;
- `docker/side-jobs-smoke.sh`: a runner starts and stops the side job the hub assigns to it;
- `runner/tests/test_isolation_docker.py`: Docker isolation against the candidate runner, with
  `TICO_RUNNER_TEST_IMAGE=tico-rc-runner:local` and the same Python worker cap;
- `scripts/journey-test.sh --release`: install the previous release from its checksummed bundle, upgrade to the
  candidate with "Update now", and roll back an update that migrates the database and never turns healthy (the
  pre-update snapshot is restored, with the server's real entrypoint running Litestream).

It prints a phase table with each check's start offset, duration and exit code, plus total time and starting/ending
load. It keeps the logs in a temporary directory and prints the end of any failed log. If any build fails, the
Docker-dependent checks are reported as not run and the release check fails. The release target is under 8 minutes
(480 seconds); a run over budget still passes its assertions but reports that it is over budget. The default suite's
300-second hard budget is unchanged. Do not run two at once: the journey's candidate tags are fixed.

The full journey stays available on demand (about ten minutes), for a release that changes enrollment, the runner's
restart behavior, Litestream or backups:

```
scripts/journey-test.sh                  # this checkout is the candidate; starts from the newest release tag
scripts/journey-test.sh --tag vX.Y.Z     # a published candidate (its images and bundle must exist)
```

It installs the previous release into a throwaway directory (auth none), enrolls a computer with a one-time code, runs one
bot run through a fake `codex` (`scripts/journey-fake-codex.py`), restarts the server, upgrades to the candidate with
"Update now", rolls back an update that migrates the database and never turns healthy (checking the pre-update snapshot
is restored, with the server's real entrypoint running Litestream), wipes the data volume and checks the server restores
from its replica without the migration, restarts a runner started as `runner.compose.yaml` starts it after a run and
requires a second run to work, and finishes with `docker/backup-test.sh` (MinIO and a file replica, wipe, restore). It prints a table of
PASS, FAIL or SKIP per step and exits non-zero on a failure. `scripts/install.sh` itself needs Linux and root, so the
script does what the installer does after its preflight (checksummed bundle, `.env`, `docker compose up -d`). For a
release that adds updater or migration behavior, the upgrade step is done by the *previous* updater, so also read the
rollback step: it runs on the candidate's updater.

## Tag

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty
   `## [Unreleased]` above it, and update the link references at the bottom.
2. Commit that to `main` once the release check is green.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The workflow then:

- runs `scripts/build_install_bundle.py`, which attaches the one-line installers: `install.sh` and the Windows
  `install-wsl.ps1` with the tag baked into them, `tico-bundle-vX.Y.Z.tar.gz` (`compose.yaml`, `.env.example`,
  `docker/runner.compose.yaml` and the `setup/` wizard) and `SHA256SUMS` over all three. `install.sh` checks the bundle against `SHA256SUMS` before it unpacks anything;
- waits until `ghcr.io/ticoteam/{tico,tico-runner,tico-updater}:vX.Y.Z` exist (the Docker workflow builds them from the
  same tag, in about 2 minutes), so no release is published whose installer would fail on `docker compose pull`;
- uses the `[X.Y.Z]` section of the changelog, unchanged, as the release notes, and fails if the
  section is missing or empty. A tag with a suffix such as `v0.2.0-rc.1` is marked a prerelease,
  which the update check ignores;
- publishes the release with those four files and the notes. That is everything the installer, the server's update
  check and the updater read (`install.sh`, the bundle and `SHA256SUMS`), so the server rollout can start at once.

Docker images are published by a separate workflow (on the same `v*` tag, plus a manual run) and set `TICO_VERSION` in the image, which is how the running app
knows its version (a source checkout reports `dev`). There is no source archive: the server and the runners run from
the images, and a Mac runner is a git checkout that moves to the release's tag. The Dockerfile copies the source tree
last, after system packages, tools and the Python dependencies, so with the GitHub Actions layer cache a release
rebuilds and pushes one small layer per image.

## Desktop apps follow the release

The desktop app never holds up the server release. The Release workflow compares the tag with the previous release
tag: when nothing the shell is built from changed (`app/`, `scripts/app_release.py`, `scripts/company_apps.py` and the
two desktop workflows), it builds nothing and copies the previous release's desktop files and `latest.json` onto the new
release, with the manifest's URLs moved to the new tag. The app keeps its earlier version, so installed apps are not
offered an update, and the download links keep working. When the shell did change, `.github/workflows/app.yml` builds
it (with a Rust cache, so only the app crate recompiles) and the files are added to the already published release, the
updater manifest last. A failed desktop build leaves the server release published and the Release run red; rerun the
workflow from the tag (Run workflow, `desktop` checked) once it is fixed. The app's version is the release's
(v0.3.6 → app 0.3.6, including any prerelease suffix), stamped from the tag at build time.

Because the files arrive a few minutes after the release, a hub that looks for them in that window finds none and asks
again a minute later. A hub accepts an app version older than its own release when the files are that release's assets.

All public CI builds are the generic **Tico** app. They do not consume company or legacy
per-environment repository variables. The three build jobs upload their bundles as `app-<target>` artifacts. After those
jobs pass, the Release workflow downloads them and adds these assets to the published GitHub release:

- macOS universal `.dmg`, `.app.tar.gz` and `.app.tar.gz.sig`;
- Windows NSIS `-setup.exe` and `-setup.exe.sig`;
- Linux `.AppImage`, `.AppImage.sig` and `.deb`;
- `latest.json`, the Tauri updater manifest, with both macOS architectures pointing at the
  universal archive and every platform URL pointing at an asset of that GitHub release.

`TAURI_SIGNING_PRIVATE_KEY` (and its optional password) signs the updater artifacts. Keep the
corresponding public key in `app/tauri.conf.json`. Missing signatures or platform bundles stop
publication. Generic apps check
`https://github.com/ticoteam/tico/releases/latest/download/latest.json`; prereleases are not
selected by GitHub's latest-release endpoint. See [Desktop app](desktop.md) for installation.

Generate the public manifest locally from collected bundles without uploading anything:

```bash
python scripts/app_release.py --github --version X.Y.Z --tag vX.Y.Z --output latest.json bundles/
```

Public CI no longer runs the legacy variable-based S3 publisher: its variables and branded
artifacts could expose private company configuration in a public repository's Actions run.
Use the private company workflow below for CI publishing. `scripts/app.sh --env <slug>` and
manual `scripts/app_release.py` S3 publishing retain their per-environment behavior.
Hubs prefer a valid company bucket manifest even when older than the server;
without a bucket manifest they offer assets from the GitHub release of their running version, with a ten-minute
cache and no credentials sent to GitHub. Concurrent requests share one GitHub fetch;
other callers use the last cached result or wait at most 100 ms on a cold cache. This fallback applies only to human download routes
(`/download/{os}` and `/api/download/{os}`). `/download/latest.json` serves only the bucket
manifest, including older environment builds without an `app_kind` field, and returns 404 when
none exists. Explicitly generic manifests and updater URLs outside the hub's download-file
route are rejected. Company installer URLs are constructed from that same hub route. Download
storage uses the blob store's region, endpoint and bucket prefix; environment artifacts belong
under `<prefix>/releases/app/` when a prefix is configured.

## The Release Manager's rollout

A team that runs several installs can hand the whole rollout to its Release Manager bot. The owner's approval of a
named release on its release task is the only gate: an approval card the bot requested (`hub approval request --kind
publish`) that the owner approved, or the owner's own message on the task naming the version. From there the bot runs
`playbooks/rollout.md` (`software/rollout.py` in its repository) without waiting on anyone:

1. it creates the tag `vX.Y.Z` at the approved commit through the GitHub API, with its own write grant on the
   repository. A tag made with the GitHub App's installation token starts the Release and Docker workflows, which a
   tag made with a workflow's `GITHUB_TOKEN` would not;
2. it waits for the GitHub release, which installs update from, and stops if the Release or Docker workflow for the
   tag fails;
3. it updates the canary, the team's own install, through that install's update key, and waits for the updater to
   report `healthy` and `/healthz` to report the new release, then for its computers to follow (reported, never a gate);
4. it updates every other install together the same way;
5. it reports each install's version before and after on the task.

A failed or rolled-back update stops the rollout: the updater has already put that install back, a canary failure
leaves the other installs untouched, and the bot asks the owner what to do next.

Setup, once:

- **Write on the repository.** An owner or admin ticks the repository in Settings → Repositories and gives the bot
  write on it: `hub bot repos <slug> --chosen <owner>/<repo>:write` (list the bot's other chosen repositories in the
  same command; `--chosen` replaces them), or the same in the bot's settings ([Repositories](repositories.md)).
- **An update key per install.** The owner of each install, the team's own included, runs
  `hub service-key create --label "<team> Release Manager" --scope update` there ([Service keys](service-keys.md#update-keys)).
  Each key is stored in Tools → Credentials on the Release Manager's install, granted to the bot, with the variable
  named in its config.
- **The list of installs.** `knowledge/rollout.json` in the bot's repository: the repository, and each install's
  `name`, `url`, `key_env` and whether it is the `canary` (see `knowledge/rollout.example.json`).

A bot created before this template change does not get these files on its own: copy `playbooks/rollout.md`,
`software/rollout.py` and the `.claude/settings.json` line that allows it from `templates/catalog/release-notes`.

## What installations do

The server asks Tico HQ (`https://updates.tico.team/v1/latest`, which serves the same release from GitHub and counts the install
anonymously, see [PRIVACY.md](../PRIVACY.md)) at most every six hours, in the background, without credentials. With counting
off, when HQ does not answer, or with `TICO_RELEASES_URL` set, it asks `https://api.github.com/repos/ticoteam/tico/releases/latest`
directly and sends no ID.

| Variable | Meaning |
|---|---|
| `TICO_UPDATE_CHECK` | `off` stops the check and hides the notice |
| `TICO_RELEASES_URL` | Replaces the check with a GitHub-shaped URL, for a mirror or a test; nothing is counted |
| `TICO_HQ_URL` | Replaces `https://updates.tico.team` |
| `TICO_TELEMETRY`, `DO_NOT_TRACK` | `off` / `1` stops the count; the check then goes to GitHub |
| `TICO_VERSION` | The running version, set by the image |
| `TICO_UPDATER_URL`, `TICO_UPDATER_TOKEN` | An updater service the owner's "Update now" calls |

The updater contract is `POST {TICO_UPDATER_URL}/update` with `{"version": "X.Y.Z"}` and
`GET {TICO_UPDATER_URL}/status`, which answers `{state, from, to, message}` where `state` is one of
`idle`, `pulling`, `restarting`, `healthy`, `rolled_back` or `failed`. Both carry
`Authorization: Bearer <TICO_UPDATER_TOKEN>`. Without an updater, "Update now" shows the command to
run on the server: `docker compose pull && docker compose up -d`.

## Publish the release docs

The tagged repository is the source for that release's manual. Run `python -m backend.openapi_v2` and
`python scripts/build_api_docs.py` after changing API descriptions; check the spec and API guide together.

Publish the public site's docs from the same tag, including the docs landing page's three reader paths and its **Use Tico** and
**Glossary** links. The website source and publishing process live outside this repository. Check that its install and demo pages
use the latest-release installer and `latest` demo image by default, with version pinning shown separately, and mention Docker Desktop
on Mac. Keep the local install command visible on the home page and continue through model sign-in and a first bot result.

Validate the published copy against this release: server Decision calls and HQ suggestion disclosures, 25 active member bots by default,
Inbox Manager's single disabled weekday 07:30 Routine and assigned-mailbox access, and the complete KPI colour example in
[Goals and KPIs](goals-and-kpis.md#the-colours). Run `npm run screenshots` when local dependencies and browsers are available and publish
images from the same release as the UI. Preserve inbound links when moving pages; the transcript and old docs-sync pages link to their successors.

## Company apps

A version tag whose shell changed builds, beside the generic desktop app, `.github/workflows/company-app.yml` for
each configured company: its macOS universal DMG and signed updater archive, Windows NSIS
installer with signature, and Linux AppImage with signature plus Debian package. The app version
is stamped from the tag. Company builds restore the generic build's Rust cache (compiled dependencies and the Tauri CLI)
and never save to it, so no branded output reaches a cache a public build could read. A tag whose shell did not change
leaves each company's bucket manifest as it is; after adding a company, run the Release workflow from the current tag
with `desktop` checked. Every build uses the same updater signing key as the generic app,
with `team.tico.env.<stable UUID>` as its bundle ID, its own name and PNG icon, its own server
address, and `<runner_url or url>/download/latest.json` as the update endpoint.

The company shell preserves its configured updater endpoint, including a separate public
`runner_url`. If the configured endpoint is the generic default, it uses the selected hub's
`/download/latest.json`. A hub-origin feed may use the signed-in WebView session when one exists;
the cookie is optional for public feeds and is never attached to a different runner origin. Hub
feeds do not follow redirects, and artifact URLs from them must stay on the selected hub origin.
Tauri still verifies each updater signature before installation. An older shell that cannot reach
the protected feed must be replaced from the team's signed-in **Download** page; see
[Desktop app](desktop.md#your-companys-app).

Keep the JSON list in the private repository Actions **secret** `TICO_COMPANY_APPS`. A secret is
used instead of an Actions variable because the runner prints variables in its pre-step environment
banner before masking commands can run. Company names and URLs must never enter repository
files, public GitHub release assets or job summaries. Each list entry has these fields:

```json
{"slug":"acme","id":"12345678-1234-4234-8234-123456789abc","app_name":"Acme Tico",
 "url":"https://tico.example.com","runner_url":"https://runner.example.com",
 "icon_url":"/api/v2/team/icon","deploy_role_arn":"arn:aws:iam::123456789012:role/tico-company-app-publisher",
 "bucket":"acme-app-files","prefix":"team"}
```

`runner_url` and `prefix` are optional. `prefix` must match the server's storage prefix.
`icon_url` is an HTTPS PNG URL or a path on `url`. URLs require HTTPS except loopback HTTP.
The UUID must remain stable across renames to preserve installed preferences and login state.

On macOS, company apps show a short native text label beside the template menu-bar mark;
the Dock keeps the company logo. The label uses up to three uppercase initials from
`app_name` (ignoring the word `Tico`), or the first three characters for a single word:
`Acme Tico` becomes `ACM`, and `Blue Harbor Tico` becomes `BH`. Names with no remaining
letters or digits fall back to `TIC`. To distinguish colliding names, add optional
`"tray_label":"A1"` to the private entry: one to four ASCII letters/digits, displayed
uppercase. Omitted or empty means automatic. Labels are display-only: they never change
the UUID, sessions, updater endpoint, or company Dock icon. The native label and template
mark follow light/dark appearance; Windows/Linux and generic builds keep their existing tray.
Changes to the app name, company slug, or label trigger Rust recompilation. No generated
fonts or colored tray images are needed, and private labels are masked with other company
configuration in CI.

Prepare each company's IAM publisher role in its own AWS account, using the normal AWS
credential chain. This script defaults to a dry run that reads live OIDC settings with
`gh api repos/ticoteam/tico/actions/oidc/customization/sub`; authenticate `gh` to the repository
first. Inspect its output privately and run it with `--apply` when ready. It creates the GitHub
OIDC provider if absent, trusts only the repository's default version-tag subject
(`repo:ticoteam/tico:ref:refs/tags/v*` for legacy subjects or the configured repository-ID prefix
for immutable subjects) with audience `sts.amazonaws.com`, and grants only GetObject/PutObject
beneath `[prefix/]releases/app/*` and ListBucket for that prefix. Unsupported custom subject
templates and failed settings discovery are rejected.

```bash
scripts/aws/company-app-publisher.sh --slug acme --id 12345678-1234-4234-8234-123456789abc \
  --app-name 'Acme Tico' --url https://tico.example.com --runner-url https://runner.example.com \
  --icon-url /api/v2/team/icon --bucket acme-app-files --prefix team --account-id 123456789012
```

It prints the role ARN, trust and permission policies, and the JSON entry to add to the secret's
list. Its output is private operator configuration; do not commit it or run it in public CI.
The publisher uses short-lived GitHub OIDC credentials, with the AWS action pinned by commit.
It uploads via `scripts/app_release.py --complete --quiet --prefix ...` into the company's
bucket at `[prefix/]releases/app/<version>/` and then atomically replaces
`[prefix/]releases/app/latest.json` only after all signed platform assets have uploaded.

Matrices and artifact names contain only SHA-256 hashes of slugs. Every company value used is
masked before use; compiler/bundler output is suppressed since it can include derived company
filenames. GitHub Actions artifacts in a public repository can be read by other signed-in
users, so company bundles and their filenames are encrypted with streaming AES-GCM before
upload. A separately derived key uses the existing `TAURI_SIGNING_PRIVATE_KEY` secret; no
additional secret is required. That secret must contain the private signing key material,
rather than a predictable filename. Authentication binds each archive to its company hash, version
tag and CI run. The publisher authenticates each archive before extracting files, rejects paths
and duplicates, and bounds extraction to 32 files and 2 GiB across the three platforms.
Each pack uses a fresh random 96-bit nonce; the signing bytes are never used directly as an
AES key. Decryption uses one automatically closed anonymous temporary file at a time, and
removes extracted files on any failure. Downloaded ciphertext is bounded to 2 GiB plus archive
overhead, with at most 4 GiB of temporary/extracted plaintext on ephemeral publisher disk. Branded
plaintext exists only on ephemeral job disks and in the company's bucket. Ciphertext artifacts
expire after one day and are never matched by the public release's `app-*` artifact download.
The publisher does not fetch the logo again, so a logo change or outage after the build cannot
block publication of already built bundles. Apple signing flags describe only the Mac installer;
Windows and Linux updater signatures do not imply installer code signing.
Company jobs use `fail-fast: false`; one build or publish failure fails that company's workflow
and marks the run red, while other companies and the public release continue independently.
The public release depends on no desktop build. Entries are validated within
their own company job; a malformed company does not prevent valid companies from publishing.
Entries without a slug produce a generic warning and cannot be dispatched. Unparseable JSON
fails the matrix with a generic error and does not prevent the public release.

Validate a private list locally without printing it or fetching icons:
`python scripts/company_apps.py matrix --dry-run` with `TICO_COMPANY_APPS` supplied through
your environment. Publishing still requires the owner's release instruction.
