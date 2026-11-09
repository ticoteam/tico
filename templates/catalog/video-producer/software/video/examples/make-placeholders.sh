#!/bin/sh
# Makes placeholder media for the examples with ffmpeg only. No recorded speech, music or stock footage.
# Usage: sh make-placeholders.sh [OUT_DIR]   (default: media)
set -e
out="${1:-media}"
mkdir -p "$out"
ff="ffmpeg -hide_banner -loglevel error -y"

# Voice: a 180 Hz tone, gated every 0.6 s, standing in for narration.
$ff -f lavfi -i "aevalsrc=0.4*sin(2*PI*180*t)*lt(mod(t\,0.6)\,0.3):s=48000:d=20" -ac 2 "$out/voice.wav"

# Music bed: a soft three-note chord with low pink noise under it.
$ff -f lavfi -i "sine=frequency=220:sample_rate=48000:duration=20" \
    -f lavfi -i "sine=frequency=277:sample_rate=48000:duration=20" \
    -f lavfi -i "sine=frequency=330:sample_rate=48000:duration=20" \
    -f lavfi -i "anoisesrc=color=pink:amplitude=0.02:sample_rate=48000:duration=20" \
    -filter_complex "[0][1][2]amix=inputs=3:normalize=0,volume=0.15[c];[c][3]amix=inputs=2:normalize=0" \
    -ac 2 "$out/music.wav"

# Avatar stand-in for the picture-in-picture overlay: a moving test pattern.
$ff -f lavfi -i "testsrc2=size=480x480:rate=30:duration=8" -c:v libx264 -pix_fmt yuv420p "$out/avatar.mp4"

# Logo stand-in for an image overlay.
$ff -f lavfi -i "color=c=0x3ddc97:s=200x80:d=1" -frames:v 1 "$out/logo.png"
echo "placeholders in $out"
