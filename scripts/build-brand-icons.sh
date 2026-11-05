#!/usr/bin/env bash
# Regenerates every raster brand file from the SVGs in ui/assets/tico/ (the square mark, the
# monochrome glyph; the wordmarks are used as SVG and need no export).
#
#   bash scripts/build-brand-icons.sh
#
# Needs rsvg-convert (macOS: brew install librsvg) and, for icon.icns, macOS's iconutil. Without
# iconutil the .icns is left as committed. The .ico is written here directly (PNG entries), so
# there is no Pillow dependency. Commit what it writes. After changing app/icons, rebuild the
# desktop app with scripts/app.sh build.
set -euo pipefail
cd "$(dirname "$0")/.."
BRAND=ui/assets/tico
WEB=ui/assets
APP=app/icons
MARK=$BRAND/tico-mark.svg      # 64x64 rounded tile, #172221, the flat Liquid Dot
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

png() { rsvg-convert --width "$2" --height "$2" --output "$3" "$1"; echo "$3"; }
inner() { sed -e '1d;$d' "$1"; }
# The mark on a square: $1 = viewBox, $2 = filled background rect (yes/no), $3 = source SVG (default: the desktop mark).
tile() {
  local bg="" src=${3:-$MARK}
  [ "$2" = yes ] && bg='<rect x="-100" y="-100" width="300" height="300" fill="#172221"/>'
  printf '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s">%s%s</svg>\n' "$1" "$bg" "$(inner "$src")"
}
# An .icns from a square SVG on Apple's grid: $1 = svg, $2 = output, $3 = a name for its iconset in $TMP.
icns() {
  local set=$TMP/$3.iconset
  mkdir "$set"
  for s in 16 32 128 256 512; do
    png "$1" $s "$set/icon_${s}x${s}.png" >/dev/null
    png "$1" $((s * 2)) "$set/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil --convert icns --output "$2" "$set"; echo "$2"
}

tile "0 0 64 64" yes    > "$TMP/fullbleed.svg"      # iOS and Slack round the corners themselves
tile "-8 -8 80 80" no   > "$TMP/mac.svg"            # Apple's grid: the tile is 80% of the canvas
tile "-16 -16 96 96" yes > "$TMP/maskable.svg"      # the mark inside the maskable safe zone

# Web: favicon fallbacks, apple-touch-icon, PWA icons (192, 512, 512 maskable).
png "$MARK" 32 $WEB/favicon-32.png
png "$MARK" 192 $WEB/icon-192.png
png "$MARK" 512 $WEB/icon-512.png
png "$TMP/maskable.svg" 512 $WEB/icon-maskable-512.png
png "$TMP/fullbleed.svg" 180 $WEB/apple-touch-icon.png

# Slack app icon (uploaded by hand in the Slack app settings) and the desktop app's master icon.
png "$TMP/fullbleed.svg" 512 $BRAND/tico-slack.png
png "$TMP/mac.svg" 1024 $BRAND/tico-1024.png


# Desktop app bundle (tauri.conf.json lists these).
png "$MARK" 32 $APP/32x32.png
png "$MARK" 64 $APP/64x64.png
png "$MARK" 128 $APP/128x128.png
png "$MARK" 256 $APP/128x128@2x.png
png "$MARK" 512 $APP/icon.png

if command -v iconutil >/dev/null; then
  icns "$TMP/mac.svg" $APP/icon.icns desktop
else
  echo "iconutil not found: $APP/icon.icns left as is"
fi

# Recorder app bundle (recorder/tauri.conf.json lists these): the Liquid Dot with a mic cut into it.
# The PNGs are flat like the desktop's; icon.icns sits on Apple's grid.
REC=$BRAND/tico-recorder-mark.svg
RICONS=recorder/icons
tile "-8 -8 80 80" no "$REC" > "$TMP/recorder-mac.svg"
png "$REC" 32 $RICONS/32x32.png
png "$REC" 128 $RICONS/128x128.png
png "$REC" 256 $RICONS/128x128@2x.png
png "$REC" 512 $RICONS/icon.png
if command -v iconutil >/dev/null; then
  icns "$TMP/recorder-mac.svg" $RICONS/icon.icns recorder
else
  echo "iconutil not found: $RICONS/icon.icns left as is"
fi

# Windows icon: PNG-compressed entries at the usual sizes.
python3 - "$MARK" "$APP/icon.ico" "$TMP" <<'PY'
import struct, subprocess, sys
mark, out, tmp = sys.argv[1:4]
sizes, blobs = [16, 32, 48, 64, 128, 256], []
for s in sizes:
    path = f"{tmp}/ico-{s}.png"
    subprocess.run(["rsvg-convert", "--width", str(s), "--height", str(s), "--output", path, mark], check=True)
    blobs.append(open(path, "rb").read())
head, body, offset = struct.pack("<HHH", 0, 1, len(sizes)), b"", 6 + 16 * len(sizes)
for s, blob in zip(sizes, blobs):
    head += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset + len(body))
    body += blob
open(out, "wb").write(head + body)
print(out)
PY
