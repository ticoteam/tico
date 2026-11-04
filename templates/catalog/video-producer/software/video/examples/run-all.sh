#!/bin/sh
# Runs every example end to end and writes outputs to examples/media/ (ignored by git).
# Needs ffmpeg, ffprobe, Node 20+ and a browser: Google Chrome, or Chromium (npx playwright install chromium).
set -e
here=$(cd "$(dirname "$0")" && pwd)
tool=$(dirname "$here")
media="$here/media"
sh "$here/make-placeholders.sh" "$media"
node "$tool/render.mjs" "$here/motion-css.html" --out "$media/motion-css.mp4" --duration 6000
node "$tool/render.mjs" "$here/motion-timeline.html" --out "$media/motion-timeline.mp4"
node "$tool/record.mjs" "$here/shots.json" --out "$media/recording.mp4"
node "$tool/edit.mjs" "$here/edit.json" --out "$media/final-16x9.mp4"
node "$tool/edit.mjs" "$here/edit.json" --size 9:16 --out "$media/final-9x16.mp4"
node "$tool/qa.mjs" "$media/final-16x9.mp4" --out "$media/qa-16x9"
node "$tool/qa.mjs" "$media/final-9x16.mp4" --out "$media/qa-9x16"
