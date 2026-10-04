# Releasing

A release is a version tag. Pushing `vX.Y.Z` runs `.github/workflows/release.yml`, which builds the
installer bundle, checks the tag against [CHANGELOG.md](../CHANGELOG.md) and publishes the GitHub
release. Running installations look for that release to show "New version" in the sidebar.

## Before you tag

Tests run on your computer, not in CI: nothing in GitHub Actions runs the suite on a push or a pull request. Before
tagging, run the whole thing from the repository root:

```
python scripts/release_checks.py
```

That is the full suite (pytest in parallel, then the browser scripts three at a time) and it has to finish in under
5 minutes; the check records wall time and load and fails if the combined run reaches 300 seconds. This is a hard budget for any suite that runs on merge or on a schedule. Keep it by keeping few tests, the ones
that guard security and privacy boundaries, data safety and core contracts, and by cutting one when you add one. CI only
builds and publishes: the Docker workflow builds the three images for a `v*` tag, and the Release workflow publishes the
GitHub release. Docker smoke checks also run locally (`docker/smoke.sh` and
`docker/side-jobs-smoke.sh`); no GitHub Actions workflow runs tests. The manual screenshots workflow
generates documentation images from the Actions tab (Run workflow).

Before a deploy, run the journey check on a laptop with Docker (it is not part of CI or of the five-minute suite budget, and takes about ten minutes):

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

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty
   `## [Unreleased]` above it, and update the link references at the bottom.
2. Commit that to `main` once the local suite is green.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The workflow then:

- runs `scripts/build_install_bundle.py`, which attaches the one-line installer: `install.sh` with the tag baked into it,
  `tico-bundle-vX.Y.Z.tar.gz` (`compose.yaml`, `.env.example`, `docker/runner.compose.yaml` and the `setup/` wizard) and
  `SHA256SUMS` over both. `install.sh` checks the bundle against `SHA256SUMS` before it unpacks anything;
- waits until `ghcr.io/ticoteam/{tico,tico-runner,tico-updater}:vX.Y.Z` exist (the Docker workflow builds them from the
  same tag), so no release is published whose installer would fail on `docker compose pull`;
- uses the `[X.Y.Z]` section of the changelog, unchanged, as the release notes, and fails if the
  section is missing or empty. A tag with a suffix such as `v0.2.0-rc.1` is marked a prerelease,
  which the update check ignores.

Docker images are published by a separate workflow (on the same `v*` tag, plus a manual run) and set `TICO_VERSION` in the image, which is how the running app
knows its version (a source checkout reports `dev`). There is no source archive: the server and the runners run from
the images, and a Mac runner is a git checkout that moves to the release's tag.

The desktop app is built for every tag too (`.github/workflows/app.yml`, called from the Release workflow). If any
desktop build fails, the GitHub release is not created and the Release run is red: servers only offer a version that
has a release, so a failed desktop build stops the rollout. The app's version is the release's
(v0.3.6 → app 0.3.6, including any prerelease suffix), stamped from the tag at build time.

All public CI builds are the generic **Tico** app. They do not consume company or legacy
per-environment repository variables. The three build jobs upload their bundles as `app-<target>` artifacts. After those
jobs pass, the Release workflow downloads them and attaches these assets to the GitHub release:

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

Every version tag builds the generic desktop app first, then `.github/workflows/company-app.yml`
builds each configured company's macOS universal DMG and signed updater archive, Windows NSIS
installer with signature, and Linux AppImage with signature plus Debian package. The app version
is stamped from the tag. Every build uses the same updater signing key as the generic app,
with `team.tico.env.<stable UUID>` as its bundle ID, its own name and PNG icon, its own server
address, and `<runner_url or url>/download/latest.json` as the update endpoint.

The company shell uses the selected hub's `/download/latest.json` at runtime so its update check
can reuse the signed-in WebView session. It sends the Access cookie only to that hub origin,
refuses cross-origin artifacts and does not follow redirects. Tauri still verifies each updater
signature before installation. An older shell that cannot reach the protected feed must be
replaced from the team's signed-in **Download** page; see [Desktop app](desktop.md#your-companys-app).

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
The public release depends only on the generic desktop build. Entries are validated within
their own company job; a malformed company does not prevent valid companies from publishing.
Entries without a slug produce a generic warning and cannot be dispatched. Unparseable JSON
fails the matrix with a generic error and does not prevent the public release.

Validate a private list locally without printing it or fetching icons:
`python scripts/company_apps.py matrix --dry-run` with `TICO_COMPANY_APPS` supplied through
your environment. Publishing still requires the owner's release instruction.
