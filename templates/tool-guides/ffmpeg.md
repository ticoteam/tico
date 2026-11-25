---
name: FFmpeg
category: marketing
summary: "Cut, convert, resize and caption video and audio"
commands: [ffmpeg, ffprobe]
---
## What it is for
Making clips and stills, changing formats and sizes, and reading a media file's length and streams.

## How to use it

    ffprobe -v error -show_entries format=duration -of csv=p=0 input.mp4
    ffmpeg -ss 00:01:05 -to 00:01:35 -i input.mp4 -c copy clip.mp4
    ffmpeg -i input.mp4 -vf "scale=1080:-2" -c:a copy small.mp4
    ffmpeg -ss 5 -i input.mp4 -frames:v 1 still.png

## Good to know
- `-c copy` is fast and lossless but cuts on keyframes; re-encode for an exact cut.
