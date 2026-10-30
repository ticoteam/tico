#!/bin/bash
# Local Tico Recorder.app for this Mac. The bundle comes from `cargo tauri build` (tauri.conf.json, Info.plist,
# entitlements.plist), like the release. Signed with a local Apple Development identity when one exists: a stable
# identity keeps keychain "Always Allow" and microphone permission across rebuilds, while an ad-hoc signature is a
# new app to macOS on every build. Override with TICO_SIGN_IDENTITY. No updater archive: that needs the release
# signing key, which only CI has (see README.md).
set -euo pipefail
cd "$(dirname "$0")"
identity="${TICO_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)}"
# An empty APPLE_SIGNING_IDENTITY makes the bundler try to sign with nothing, so it is only set when found.
if [ -n "$identity" ]; then export APPLE_SIGNING_IDENTITY="$identity"; fi
cargo tauri build --features desktop --bundles app --config '{"bundle":{"createUpdaterArtifacts":false}}'
app="$PWD/target/release/bundle/macos/Tico Recorder.app"
codesign --verify --deep --strict "$app"
printf 'Built %s\n' "$app"
