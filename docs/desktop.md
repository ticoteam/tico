# Tico desktop app

Download **Tico** from your server's **Download for Mac**, **Download for Windows** or
**Download for Linux** link, or from the [latest GitHub release](https://github.com/ticoteam/tico/releases/latest).
The same app connects to any Tico server.

On macOS, open the universal `.dmg` and drag Tico to Applications. On Windows, run the
`-setup.exe` installer. On Linux, make the `.AppImage` executable and open it, or install the
`.deb` package. A Mac build without Apple signing and notarization may need **Open Anyway**
in System Settings > Privacy & Security on its first launch.

The first launch asks for the **Server** address, for example `https://tico.example.com`. Enter
your team's address and click **Connect**. Tico checks that the server is reachable and answers
its `/healthz` check as Tico, saves the address in the app's config folder, then opens your Team.
Sign in as you would in a browser. HTTPS is required; `http://localhost` and `http://127.0.0.1`
are allowed for a server on your Computer. A failed check shows an error below the field.

Use **Change server…** in the generic app's menu or tray menu to connect to another server.
The app saves the address and restarts automatically to replace its native permissions.
Only the selected server's origin can use native IPC; Access and external sign-in pages that
remain in the window receive no native permissions. Tico keeps
its tray icon when you close the main window. Click it to show or hide the window.

The app checks for signed updates automatically, installs them and relaunches. Generic builds
use the public GitHub release manifest; your server's web interface updates with the server.
Your team may also offer its own app.

## Optional per-environment builds

`scripts/app.sh --env <slug>` still builds an app with that environment's name, icon, bundle
identifier, server address and hub updater endpoint. See [Environments](environments.md).
`TICO_HUB_URL` can also bake in a server address at build time. An address supplied in the
process environment as `HUB_URL` wins over the baked address, which wins over the saved address.
Without any of those addresses, the local first-launch page opens. Environment builds hide
**Change server…** and ignore attempts to change the saved address; their built-in server
retains priority. Built-in, process and saved addresses may use HTTP for existing deployments;
only a newly entered first-launch or **Change server…** address requires HTTPS or loopback HTTP.

Servers offer their company app whenever a bucket build exists, including when it is older than
the server. Without one, they offer desktop assets from their running GitHub release. If
GitHub cannot be reached, the download link waits until the next successful check; failures are
cached for ten minutes. Source checkouts reporting `dev` need a bucket build or a download from
GitHub directly.

A built-in server address is saved to `server.txt` when no saved address exists, so a later
generic build retains it if the bundle identifier is unchanged. CI uses `team.tico.app` for
both generic and hub builds. `scripts/app.sh --env` uses `team.tico.env.<environment id>`:
its config folder differs from the generic app, so installing the generic app separately
does not inherit that environment's settings. Environment updater feeds never offer generic
GitHub builds.

## Your company's app

Download your company's app from your own Tico page. It has your team's name and logo, opens
your Team directly, and updates itself from your server. Each company has a stable bundle ID,
so its app can coexist with another company's app and the generic Tico app. The download API
`GET /api/download/{mac|windows|linux}` returns `app_kind: company` or `generic`; a frontend
can show **Download Tico for <team name>** for a company build.

If an older company app does not update itself, sign in to your team's Tico page, download the
company app again and install it over the existing copy. Keep the app's saved data; its stable
bundle ID preserves the selected server and sign-in state. The replacement shell uses the signed-in
hub session for protected update files. Downloads remain behind Cloudflare Access.

### Build-only recovery bridge for the 0.3.20 Team shell

Use this only when an installed company shell at version 0.3.20 needs to reach the official
CI-signed 0.3.21 release. Root supplies the installed `.app`, HTTPS company hub, the exact updater
feed already configured for that company, and its existing environment and tray settings from the
managed verification checkout. Keep those private values in the local shell; do not put them in this
public document or a commit. `UPDATE_URL` may be a separate configured public runner feed, so retain
its exact value rather than substituting the generic GitHub feed or assuming the hub hosts updates.

The recipe reads the installed app's bundle identifier, name, executable and version from its
`Info.plist`. It builds into a new temporary directory with `cargo tauri build`; it does not use
`scripts/app.sh build`, which copies a build over an installed app. The generated override inherits
the checked-in official updater public key, sets the shell back to version 0.3.20, preserves the
company endpoint, and disables updater artifact generation. No updater signing private key is
needed for this unsigned verification shell.

From a Tico checkout on the managed Mac, fill in the local values Root supplies, then run:

```sh
TEAM_APP="/absolute/path/to/the/current/Team.app"
HUB_URL="https://<company-hub-host>/"
UPDATE_URL="https://<existing-company-update-feed>/download/latest.json"
ENV_SLUG="<existing-company-environment-slug>"
TRAY_LABEL="<existing-tray-label-or-empty>"
BUILD_ROOT="$(mktemp -d /tmp/tico-bridge-0.3.20.XXXXXX)"
OVERRIDE="$BUILD_ROOT/tauri.override.json"
INFO="$TEAM_APP/Contents/Info.plist"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO")"
APP_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$INFO")"
APP_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$INFO")"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$INFO")"
test "$APP_VERSION" = "0.3.20"
python3 - "$BUNDLE_ID" "$APP_NAME" "$APP_EXECUTABLE" "$HUB_URL" "$UPDATE_URL" "$TRAY_LABEL" "$OVERRIDE" <<'PY'
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

bundle_id, name, executable, hub, feed, tray_label, output = sys.argv[1:8]
for label, value in (("company hub", hub), ("company updater feed", feed)):
    parsed = urlsplit(value)
    assert parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password \
        and not parsed.query and not parsed.fragment, f"{label} must be an HTTPS URL without credentials, query or fragment"
assert bundle_id and name and executable, "installed company app identity is incomplete"
assert len(tray_label) <= 4 and tray_label.isascii() and (not tray_label or tray_label.isalnum()), "tray label must be empty or 1-4 ASCII letters/digits"
config = json.loads(Path("app/tauri.conf.json").read_text())
assert config["plugins"]["updater"].get("pubkey"), "the checked-in official updater public key is missing"
generic_feed = "https://github.com/ticoteam/tico/releases/latest/download/latest.json"
assert feed != generic_feed, "supply the company's existing updater endpoint, not the generic GitHub feed"
config["version"] = "0.3.20"
config["productName"] = name
config["identifier"] = bundle_id
config["mainBinaryName"] = executable
config["plugins"]["updater"]["endpoints"] = [feed]
config["bundle"]["createUpdaterArtifacts"] = False
Path(output).write_text(json.dumps(config))
PY
(
  cd app
  TICO_HUB_URL="$HUB_URL" TICO_APP_NAME="$APP_NAME" TICO_ENV_SLUG="$ENV_SLUG" \
    TICO_TRAY_LABEL="$TRAY_LABEL" TICO_LOCAL_TOKEN_FILE="" CARGO_TARGET_DIR="$BUILD_ROOT/cargo-target" \
    cargo tauri build --locked --target universal-apple-darwin --bundles app --no-sign --config "$OVERRIDE"
)
```

The candidate is under `$BUILD_ROOT/cargo-target/universal-apple-darwin/release/bundle/macos/`.
`createUpdaterArtifacts=false` means this bridge needs no updater signing private key; its inherited
official public key still verifies the signed 0.3.21 update. The command writes only to the temporary
build directory and does not replace or install an app. Root owns installation and rollback: save the
current company `.app` before any install, restore it to its original path if the bridge cannot update
or relaunch, and keep the bundle identity and app data unchanged.

Owners can set the public team logo in **Settings → Team → Choose icon**, or with
`hub team icon logo.png`, using their owner credential
(`HUB_API_URL` and `HUB_TOKEN`). The API is `POST /api/v2/team/icon`, with an opaque binary PNG,
JPEG or WebP body up to 1 MB and an `Idempotency-Key`. Images up to 16 megapixels are normalized
to a square PNG, at most 1024 pixels per side and 1 MB, with transparent padding and metadata removed.
The answer is `{url: "/api/v2/team/icon", content_type: "image/png"}`. Only the owner may
write; `GET /api/v2/team/icon` is public, with a five-minute cache and ETag/304 support. It
returns 404 until a logo is set. This stable path is suitable for CI; expose it outside your
sign-in proxy, or use another public HTTPS PNG URL.
When blob storage uses S3, its server credential also needs DeleteObject for retired icon blobs;
a cleanup failure preserves the current icon and blocks another replacement until cleanup succeeds.

Operators configure private company builds as described in [Releasing](releasing.md#company-apps).
