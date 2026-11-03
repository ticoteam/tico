#!/usr/bin/env bash
# Build and install the desktop app (app/, Tauri): one window on the hub, with its Needs you count
# on the Dock icon. macOS from this Mac; Windows and Linux
# from CI (.github/workflows/app.yml), which runs the same build with the same variables.
#   scripts/app.sh check     compile only, nothing installed: the safe way to verify a change while
#                            the installed app is running
#   scripts/app.sh build     build the .app into app/target and copy it to ~/Applications/<Name>.app
#   scripts/app.sh open      launch it
#   scripts/app.sh install   build, replace the running copy, and open it
#   scripts/app.sh release   build the signed, notarized macOS bundles (dmg + update archive) and
#                            publish them to the hub (scripts/app_release.py): the site offers the
#                            download and installed apps update themselves. Env: APPLE_SIGNING_IDENTITY
#                            (default: the first "Developer ID Application" in the keychain), APPLE_ID,
#                            APPLE_PASSWORD, APPLE_TEAM_ID for notarization (or skip: unsigned build),
#                            TAURI_SIGNING_PRIVATE_KEY_PATH (default ~/.config/tico/app-signing/tico.key),
#                            TICO_APP_BUCKET (the storage bucket), AWS credentials for it.
#   --env <slug>             build one environment's own app from
#                            ~/.config/tico/environments/<slug>/environment.json: that company's
#                            app name, icon and server URL, and its own bundle identifier, which
#                            is what the OS keys the Dock entry, login state and preferences to.
#                            Two companies' apps then coexist on one machine. TICO_ENV=<slug> does
#                            the same. Without it this script builds the default Tico.
#                            Env: TICO_ENVIRONMENTS_DIR overrides the environments root;
#                            TICO_APP_PATH writes the bundle somewhere other than ~/Applications.
set -euo pipefail
HUB="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$HUB/app"

ENV_SLUG="${TICO_ENV:-}"
ARGV=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env) [ $# -ge 2 ] || { echo "--env needs an environment slug"; exit 1; }; ENV_SLUG="$2"; shift 2 ;;
    --env=*) ENV_SLUG="${1#--env=}"; shift ;;
    *) ARGV+=("$1"); shift ;;
  esac
done
set -- ${ARGV[@]+"${ARGV[@]}"}

NAME="Tico"
BUNDLE_ID="team.tico.app"
HUB_URL="${HUB_APP_URL:-${TICO_SERVER_URL:-${TICO_HUB_URL:-}}}"
UPDATE_URL="https://github.com/ticoteam/tico/releases/latest/download/latest.json"
if [ -n "$HUB_URL" ]; then UPDATE_URL="${TICO_APP_BASE:-${HUB_URL%/}}/download/latest.json"; fi
ICON_SRC="${ICON_SRC:-$HUB/ui/assets/tico/tico-1024.png}"
LOCAL_TOKEN_FILE=""

json() { plutil -extract "$2" raw -o - "$1" 2>/dev/null || true; }

if [ -n "$ENV_SLUG" ]; then
  ENV_DIR="${TICO_ENVIRONMENTS_DIR:-$HOME/.config/tico/environments}/$ENV_SLUG"
  ENV_JSON="$ENV_DIR/environment.json"
  [ -f "$ENV_JSON" ] || { echo "no environment '$ENV_SLUG': $ENV_JSON does not exist"; exit 1; }
  ENV_ID="$(json "$ENV_JSON" id)"; ENV_APP="$(json "$ENV_JSON" app_name)"; ENV_URL="$(json "$ENV_JSON" url)"
  ENV_SERVER="$(json "$ENV_JSON" server)"
  ENV_RUNNER="$(json "$ENV_JSON" runner_url)"
  [ -n "$ENV_ID" ] && [ -n "$ENV_APP" ] && [ -n "$ENV_URL" ] || { echo "$ENV_JSON needs id, app_name, and url"; exit 1; }
  NAME="$ENV_APP"
  # The identifier comes from the environment ID, which never changes: a slug can be renamed and
  # a name is not unique. Changing it later would orphan the app's login state and preferences.
  BUNDLE_ID="team.tico.env.$ENV_ID"
  case "$ENV_URL" in */) ;; *) ENV_URL="$ENV_URL/" ;; esac
  HUB_URL="$ENV_URL"
  # The updater takes https only; a local hub has no builds to offer and the app skips the check.
  case "${ENV_RUNNER:-$ENV_URL}" in https://*) UPDATE_URL="${ENV_RUNNER:-$ENV_URL}"; UPDATE_URL="${UPDATE_URL%/}/download/latest.json" ;; *) UPDATE_URL="https://localhost.invalid/download/latest.json" ;; esac
  [ -f "$ENV_DIR/icon.png" ] && ICON_SRC="$ENV_DIR/icon.png"
  # A local server has no browser sign-in: the app reads this file and trades the owner token
  # for a session cookie on its first load. The token never enters the bundle.
  [ "$ENV_SERVER" = "local" ] && LOCAL_TOKEN_FILE="$ENV_DIR/local-owner.token"
fi

APP="${TICO_APP_PATH:-$HOME/Applications/$NAME.app}"
BUILD="$APP_DIR/target"
ICONS="$BUILD/icons-${ENV_SLUG:-tico}"

# What the Rust build reads (app/build.rs re-runs config.rs when these change).
export TICO_SERVER_URL="$HUB_URL" TICO_HUB_URL="$HUB_URL" TICO_APP_NAME="$NAME" TICO_ENV_SLUG="$ENV_SLUG" TICO_LOCAL_TOKEN_FILE="$LOCAL_TOKEN_FILE"

icons() {
  if [ ! -f "$ICONS/icon.icns" ] || [ "$ICON_SRC" -nt "$ICONS/icon.icns" ]; then
    (cd "$APP_DIR" && cargo tauri icon "$ICON_SRC" -o "$ICONS" >/dev/null)
    rm -rf "$ICONS/android" "$ICONS/ios"
  fi
}

config_override() {
  # Everything per environment that tauri.conf.json cannot know: name, identifier, icons, updater.
  python3 - "$NAME" "$BUNDLE_ID" "$ICONS" "$UPDATE_URL" <<'PY'
import json, sys
name, ident, icons, update = sys.argv[1:5]
exe = "".join(ch for ch in name if ch.isalnum()) or "TicoApp"
print(json.dumps({"productName": name, "identifier": ident, "mainBinaryName": exe,
                  "bundle": {"icon": [f"{icons}/32x32.png", f"{icons}/128x128.png", f"{icons}/128x128@2x.png", f"{icons}/icon.icns", f"{icons}/icon.ico"]},
                  "plugins": {"updater": {"endpoints": [update]}}}))
PY
}

check() { (cd "$APP_DIR" && cargo check --locked) && echo "ok: app compiles"; }

build() {
  icons
  local override; override="$(config_override)"
  echo "building $NAME ($HUB_URL)…"
  (cd "$APP_DIR" && cargo tauri build --bundles app --no-sign --config "$override" >/dev/null)
  local built="$BUILD/release/bundle/macos/$NAME.app"
  [ -d "$built" ] || { echo "build failed: $built missing"; exit 1; }
  mkdir -p "$(dirname "$APP")"
  rm -rf "$APP.new"; cp -R "$built" "$APP.new"; rm -rf "$APP"; mv "$APP.new" "$APP"
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true
  touch "$APP"
  echo "built $APP"
}

release() {
  icons
  local identity="${APPLE_SIGNING_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | grep -o '"Developer ID Application: [^"]*"' | head -1 | tr -d '"')}"
  local key="${TAURI_SIGNING_PRIVATE_KEY_PATH:-$HOME/.config/tico/app-signing/tico.key}"
  [ -f "$key" ] || { echo "no update signing key at $key (cargo tauri signer generate -w $key)"; exit 1; }
  local bucket="${TICO_APP_BUCKET:-}"
  [ -n "$bucket" ] || { echo "TICO_APP_BUCKET (the storage bucket) is not set"; exit 1; }
  local override; override="$(config_override)"
  local version; version="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$APP_DIR/tauri.conf.json")"
  TAURI_SIGNING_PRIVATE_KEY="$(cat "$key")"
  export TAURI_SIGNING_PRIVATE_KEY
  export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-$(cat "$key.password" 2>/dev/null || true)}"
  local signed=false
  if [ -n "$identity" ]; then export APPLE_SIGNING_IDENTITY="$identity"; signed=true; echo "signing as $identity"
  else echo "no Developer ID Application identity: unsigned build (Gatekeeper will need Open Anyway)"; fi
  local notarized=false
  [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && notarized=true
  echo "building $NAME $version for release (universal)…"
  (cd "$APP_DIR" && cargo tauri build --target universal-apple-darwin --bundles app,dmg --config "$override")
  local out="$BUILD/universal-apple-darwin/release/bundle"
  local stage="$BUILD/release-$version"; rm -rf "$stage"; mkdir -p "$stage"
  cp "$out"/dmg/*.dmg "$out"/macos/*.app.tar.gz "$out"/macos/*.app.tar.gz.sig "$stage"/
  local base="${TICO_APP_BASE:-${UPDATE_URL%/download/latest.json}}"
  local flags=(); $signed && flags+=(--signed); $notarized && flags+=(--notarized)
  python3 "$HUB/scripts/app_release.py" --version "$version" --bucket "$bucket" --base "$base" ${flags[@]+"${flags[@]}"} "$stage"
}

case "${1:-build}" in
  check) check ;;
  build) build ;;
  release) release ;;
  open) open "$APP" ;;
  install) build
    pkill -f "$APP/Contents/MacOS/" 2>/dev/null || true
    sleep 0.5; open "$APP"; echo "opened" ;;
  *) sed -n '2,24p' "$0"; exit 1 ;;
esac
