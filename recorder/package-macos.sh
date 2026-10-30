#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
cargo build --locked --release --features desktop --bin tico-recorder
app="$PWD/dist/Tico Recorder Native.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp target/release/tico-recorder "$app/Contents/MacOS/tico-recorder"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Tico Recorder Native</string>
<key>CFBundleDisplayName</key><string>Tico Recorder Native</string>
<key>CFBundleIdentifier</key><string>team.tico.recorder.native</string>
<key>CFBundleExecutable</key><string>tico-recorder</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.2.0</string>
<key>CFBundleVersion</key><string>2</string>
<key>LSMinimumSystemVersion</key><string>14.2</string>
<key>NSHighResolutionCapable</key><true/>
<key>CFBundleURLTypes</key><array><dict>
<key>CFBundleURLName</key><string>team.tico.recorder.native.connect</string>
<key>CFBundleURLSchemes</key><array><string>tico-recorder</string></array>
</dict></array>
<key>NSMicrophoneUsageDescription</key><string>Record your microphone when you start a meeting recording.</string>
<key>NSAudioCaptureUsageDescription</key><string>Record meeting audio from other apps when you include meeting audio.</string>
<key>NSScreenCaptureDescription</key><string>Capture meeting audio when you choose to include it. Video is never saved.</string>
</dict></plist>
PLIST
# A stable signing identity keeps keychain "Always Allow" and microphone permission across rebuilds;
# an ad-hoc signature is a new app to macOS every build. Override with TICO_SIGN_IDENTITY.
identity="${TICO_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)}"
codesign --force --sign "${identity:--}" "$app"
codesign --verify --deep --strict "$app"
printf 'Built %s\n' "$app"
