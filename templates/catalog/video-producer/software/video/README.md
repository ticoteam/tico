# Video toolkit

Four Node scripts that make video from HTML and from a browser session, then cut it together.
No npm dependencies except `playwright`. Needs `ffmpeg` and `ffprobe` on PATH and a browser: the installed Google Chrome
when there is one, else Playwright's Chromium.

| Script | What it does |
| --- | --- |
| `render.mjs` | Renders an HTML composition frame by frame to MP4. Same input, same frames. |
| `record.mjs` | Runs a scripted browser session (`shots.json`) and records it to MP4, with a visible cursor. |
| `edit.mjs` | Cuts clips, overlays, captions, voice and music from `edit.json` into one MP4. |
| `qa.mjs` | Checks a video: ffprobe summary, loudness, black and frozen parts, and a contact sheet PNG. |

## Setup

```sh
ffmpeg -version            # must be on PATH
npm i playwright@1.63.0    # in the folder you run from, or set VP_PLAYWRIGHT=/path/to/node_modules/playwright
npx playwright install chromium   # only without Google Chrome
```

Each script stops with one line when ffmpeg, Playwright or a browser (Chrome or Chromium) is missing.
ffmpeg needs libass for SRT/ASS burn-in with the `subtitles` filter. Without it, `edit.mjs` draws
SRT captions as images instead (same look, one note on stderr). ASS captions need libass.

## render.mjs

```sh
node render.mjs composition.html --out out.mp4 [--fps 30] [--size 1080x1920] [--audio a.wav] [--from 0] [--to 6000] [--duration 6000]
```

- Length: `window.__timeline.duration_ms`, else `--duration`, else the end of the longest finite CSS/WAAPI animation.
- Frames: frame *i* is at `from + round(i * 1000 / fps)` ms. Each frame is seeked, fonts and images are awaited,
  two animation frames pass, then a PNG screenshot (`Page.captureScreenshot`) goes to ffmpeg. Output is H.264,
  yuv420p, `+faststart`.
- `--audio` is trimmed to `--from`/`--to` and muxed as AAC.

### Seek contract

A composition is an HTML page. It should be deterministic: no `Math.random()`, no real clock, no network.

1. **Preferred:** set `window.__timeline = { duration_ms, fps?, seek(ms) }`. `seek` draws the frame at `ms`.
   It may return a Promise. The renderer awaits it.
2. **Otherwise:** the renderer pauses every `document.getAnimations()` entry and sets `currentTime` to the frame
   time, so CSS `@keyframes` and WAAPI animations seek. It also sets two custom properties on `:root`:
   `--t` (a CSS time, for example `1234ms`) and `--t-ms` (a plain number of ms). Use them for motion that
   is not an animation, for example `width: calc(var(--t-ms) / 60 * 1%)` for a 6 s progress bar.

See `examples/motion-css.html` (path 2) and `examples/motion-timeline.html` (path 1).

## record.mjs

```sh
node record.mjs shots.json --out out.mp4 [--size 1920x1080] [--headed | --headless] [--keep-webm] [--as-bot]
```

Headed is used on Linux when `DISPLAY` is set, unless `--headless` is given. The lead-in before the first
`goto` finishes is trimmed. A cursor (arrow) is drawn in every page of the session. It glides to each
target before a click or typing, and shows a ripple on press.

`--as-bot` records your company's Tico as this bot, with this bot's rights, and only during a bot turn (it needs
`HUB_API_URL` and `HUB_TOKEN` from the runner; `tico-file:<path>` works too). The bot token is sent only to the Tico
server, never to other sites. With `--as-bot`, a `goto` url starting with `/` opens that page on your Tico, for example
`"/tasks"` goes to `https://hub.tico.team/tasks`. Without it, `/` paths are files as before.

### shots.json

```json
{
  "viewport": "1920x1080",
  "steps": [
    { "do": "goto", "url": "site/index.html" },
    { "do": "type", "selector": "#name", "text": "Dana Example", "delay_ms": 70 },
    { "do": "click", "selector": "#submit" },
    { "do": "wait", "selector": "#result" },
    { "do": "wait", "ms": 800 },
    { "do": "highlight", "selector": "#result", "ms": 1400 },
    { "do": "zoom", "selector": "#result", "scale": 1.5, "ms": 600 },
    { "do": "zoom", "reset": true, "ms": 600 },
    { "do": "scroll", "y": 400, "ms": 600 },
    { "do": "scroll", "selector": "#pricing" },
    { "do": "pause", "ms": 500 }
  ]
}
```

| Step | Fields | Notes |
| --- | --- | --- |
| `goto` | `url` | A relative path is read from the shots file's folder. `http:`, `https:` and `file:` URLs are used as they are. |
| `click` | `selector` | Glides to the element centre, then clicks. |
| `type` | `selector`, `text`, `delay_ms?` | Clicks the element, then types one key at a time. Default delay 70 ms with a little variation. |
| `wait` | `selector` or `ms` | Waits until the selector is visible (15 s limit), or sleeps. |
| `pause` | `ms` | Sleeps. |
| `scroll` | `y` or `selector`, `ms?` | Smooth scroll by `y` pixels, or to the element. Waits `ms` (default 600). |
| `zoom` | `selector`, `scale` (1-4), `ms?` or `reset: true` | Scales the page about the element centre with a CSS transform on `<html>`. |
| `highlight` | `selector`, `ms?` | Draws an outline box around the element for `ms` (default 1200). |

Selectors are CSS selectors. Times are milliseconds. Steps run in order. A bad file stops the run before
the browser starts, and the problems are listed.

## edit.mjs

```sh
node edit.mjs edit.json --out out.mp4 [--size 9:16|1:1|16:9|WxH] [--dry-run]
```

`--dry-run` prints the ffmpeg command and the filtergraph and renders nothing. Presets: `9:16` 1080x1920,
`1:1` 1080x1080, `16:9` 1920x1080. Sizes must be even.

### edit.json

Paths are relative to the edit file. Times are seconds on the output timeline.

```json
{
  "size": "16:9",
  "fit": "fit",
  "fps": 30,
  "loudness": -14,
  "clips": [
    { "src": "media/motion-css.mp4", "in": 0, "out": 6 },
    { "src": "media/recording.mp4", "fade": 0.5 },
    { "image": "media/title.png", "duration": 3 }
  ],
  "overlays": [
    { "src": "media/avatar.mp4", "start": 6, "duration": 6, "x": "right", "y": "bottom", "width": 0.25, "margin": 48 },
    { "image": "media/logo.png", "start": 0, "duration": 20, "x": "left", "y": "top", "width": 180 }
  ],
  "captions": { "srt": "captions.srt" },
  "voice": { "src": "media/voice.wav", "volume": 1.0 },
  "music": { "src": "media/music.wav", "volume": 0.25, "fade_out": 2 }
}
```

- `size`: preset or `WxH`. `fit`: `"fit"` pads the source to fit (black bars), `"fill"` crops to cover.
- `fps`: default 30. `loudness`: integrated LUFS, default -14, checked by `qa.mjs`.
- `clips[]`: one of `src` (a video, with optional `in`/`out`) or `image` (with `duration`). `fade` is the
  crossfade seconds into this clip from the previous one; `0` or missing is a hard cut. `audio: false` mutes a clip.
  A clip without audio gets silence.
- `overlays[]`: one of `src` (a video, `in`/`out` or `duration`) or `image` (needs `duration`). `start` is when it
  appears on the timeline. `x`: `left`, `center`, `right` or a pixel number. `y`: `top`, `middle`, `bottom` or a
  number. `width`: fraction of the frame (0 < w <= 1) or pixels. `margin`: pixels from the edge (default 40).
  Video overlays play without their sound; use `voice` or `music` for sound. Images keep their alpha (PNG).
- `captions`: `{ "srt": "file.srt" }` or `{ "ass": "file.ass" }`. SRT gets a readable style scaled to the frame:
  bold, white, black outline, bottom-centred with a safe margin (14% of the height for 9:16). Override
  any style key with `"style": { "FontSize": 80 }`. ASS keeps its own styles.
- `voice`: spoken track, `volume` default 1. `music`: bed that loops to the full length, `volume` default 0.25,
  fades out over `fade_out` seconds (default 2). Music ducks under the voice (or under the clips' sound when there is no voice).
- The result is loudness-normalised with `loudnorm` to `loudness`.

Crossfades use `xfade`/`acrossfade`. Overlay video alpha (transparent video) is not supported.

## qa.mjs

```sh
node qa.mjs out.mp4 --out qa-dir [--cols 4] [--rows 3]
```

Prints and writes `qa-dir/qa.json`: duration, size, fps, codecs, audio channels and rate, `faststart`,
integrated LUFS, loudness range, true peak, black segments (0.5 s or more), frozen segments (2 s or more) and a
`warnings` list. Writes `qa-dir/contact.png`, a grid of evenly spaced frames (default 4x3), for the bot to look at.

## Examples

`examples/` has two 6-second compositions, a static page to record, a shots file, a cut list, captions, and a
script that makes placeholder media (sine tones, pink noise and a test pattern; no speech, music or stock footage).

```sh
sh examples/run-all.sh   # writes examples/media/ (git-ignored): renders, recording, 16:9 and 9:16 cuts, QA
```

## Tests

```sh
node --test test/*.test.mjs
```

The tests cover the cut-list-to-filtergraph build, SRT parsing, `shots.json` validation, the shipped examples and the
`--as-bot` token and URL rules.
