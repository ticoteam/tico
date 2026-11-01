#!/usr/bin/env node
// Cuts clips together into one MP4 from an edit.json cut list. Builds one ffmpeg filtergraph.
// Usage: node edit.mjs <edit.json> --out out.mp4 [--size 9:16|1:1|16:9|WxH] [--dry-run]
// The schema is in README.md.
import { mkdtempSync, copyFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fail, launchChromium, loadPlaywright, parseArgs, parseSize, probe, requireFfmpeg, run } from './lib.mjs';

const IMAGE_EXT = new Set(['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff']);
const RATE = 48000;

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const t3 = (x) => Number(x).toFixed(3);
const isImage = (path) => IMAGE_EXT.has(extname(path).toLowerCase());
const isPos = (v) => isNum(v) && v >= 0;

// Returns a list of problems in an edit document. An empty list means it is valid.
export function validateEdit(edit) {
  const errors = [];
  if (!edit || typeof edit !== 'object' || Array.isArray(edit)) return ['edit file must be a JSON object'];
  try { parseSize(edit.size || '1920x1080'); } catch (e) { errors.push(e.message); }
  if (edit.fps !== undefined && !(isNum(edit.fps) && edit.fps >= 1 && edit.fps <= 120)) errors.push('fps must be 1-120');
  if (edit.fit !== undefined && !['fit', 'fill'].includes(edit.fit)) errors.push('fit must be "fit" or "fill"');
  if (edit.loudness !== undefined && !(isNum(edit.loudness) && edit.loudness >= -40 && edit.loudness <= -5)) {
    errors.push('loudness must be -40 to -5 LUFS');
  }
  if (!Array.isArray(edit.clips) || edit.clips.length === 0) errors.push('clips must be a non-empty array');

  const checkMedia = (item, at) => {
    const hasSrc = typeof item.src === 'string';
    const hasImage = typeof item.image === 'string';
    if (hasSrc === hasImage) errors.push(`${at} needs exactly one of src or image`);
    if (item.in !== undefined && !isPos(item.in)) errors.push(`${at}.in must be >= 0`);
    if (item.out !== undefined && !isPos(item.out)) errors.push(`${at}.out must be >= 0`);
    if (isNum(item.in) && isNum(item.out) && item.out <= item.in) errors.push(`${at}.out must be after in`);
    if (hasImage && !(isNum(item.duration) && item.duration > 0)) errors.push(`${at}.duration must be > 0 for an image`);
  };

  (edit.clips || []).forEach((c, i) => {
    const at = `clips[${i}]`;
    if (!c || typeof c !== 'object') return errors.push(`${at} must be an object`);
    checkMedia(c, at);
    if (c.fade !== undefined && !isPos(c.fade)) errors.push(`${at}.fade must be >= 0`);
    if (c.audio !== undefined && typeof c.audio !== 'boolean') errors.push(`${at}.audio must be true or false`);
  });

  (edit.overlays || []).forEach((o, i) => {
    const at = `overlays[${i}]`;
    if (!o || typeof o !== 'object') return errors.push(`${at} must be an object`);
    checkMedia(o, at);
    if (o.start !== undefined && !isPos(o.start)) errors.push(`${at}.start must be >= 0`);
    if (o.duration !== undefined && !(isNum(o.duration) && o.duration > 0)) errors.push(`${at}.duration must be > 0`);
    if (!(o.width === undefined || (isNum(o.width) && o.width > 0))) errors.push(`${at}.width must be > 0`);
    if (o.x !== undefined && !(isNum(o.x) || ['left', 'center', 'right'].includes(o.x))) {
      errors.push(`${at}.x: left, center, right or a number`);
    }
    if (o.y !== undefined && !(isNum(o.y) || ['top', 'middle', 'bottom'].includes(o.y))) {
      errors.push(`${at}.y: top, middle, bottom or a number`);
    }
  });

  if (edit.captions !== undefined) {
    const c = edit.captions;
    const kinds = ['srt', 'ass'].filter((k) => typeof c[k] === 'string');
    if (kinds.length !== 1) errors.push('captions needs exactly one of srt or ass');
  }
  for (const key of ['voice', 'music']) {
    if (edit[key] === undefined) continue;
    const m = edit[key];
    if (typeof m.src !== 'string') errors.push(`${key}.src must be a file path`);
    if (m.volume !== undefined && !(isNum(m.volume) && m.volume >= 0)) errors.push(`${key}.volume must be >= 0`);
  }
  return errors;
}

// Output size for the frame: 1 means fit the whole source (pad), "fill" crops to cover.
function fitFilter(fit, w, h) {
  if (fit === 'fill') return `scale=${w}:${h}:force_original_aspect_ratio=increase,crop=${w}:${h},setsar=1`;
  return `scale=${w}:${h}:force_original_aspect_ratio=decrease,pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1`;
}

function silence(label, dur) {
  return `anullsrc=sample_rate=${RATE}:channel_layout=stereo,atrim=duration=${t3(dur)},asetpts=PTS-STARTPTS,` +
    `aformat=sample_fmts=fltp:channel_layouts=stereo[${label}]`;
}

function anchor(value, margin, axis) {
  if (isNum(value)) return String(value);
  if (axis === 'x') {
    if (value === 'left') return String(margin);
    if (value === 'right') return `main_w-overlay_w-${margin}`;
    return '(main_w-overlay_w)/2';
  }
  if (value === 'top') return String(margin);
  if (value === 'bottom') return `main_h-overlay_h-${margin}`;
  return '(main_h-overlay_h)/2';
}

// Subtitle style scaled to the frame. Bottom margin keeps 9:16 text above the app UI bars.
export function captionStyleFor(w, h) {
  return captionStyle(w, h);
}

function captionStyle(w, h, override = {}) {
  const portrait = h > w;
  const base = {
    FontName: 'Noto Sans',
    FontSize: Math.round(Math.min(w, h) * 0.065),
    Bold: 1,
    PrimaryColour: '&H00FFFFFF',
    OutlineColour: '&H00000000',
    BackColour: '&H00000000',
    BorderStyle: 1,
    Outline: 5,
    Shadow: 0,
    Alignment: 2,
    MarginV: Math.round(h * (portrait ? 0.14 : 0.07)),
    MarginL: Math.round(w * 0.06),
    MarginR: Math.round(w * 0.06),
  };
  return Object.assign(base, override);
}

// Builds the ffmpeg command for an edit. Pure: media facts come from the probe callback.
// probe(absPath) -> {duration, hasAudio} for video and audio files.
// Returns {args, graph, duration, copies, commandLine} where copies are files to put in the work folder.
export function buildPlan(editIn, { baseDir = '.', probe = () => ({}), out = 'out.mp4', size } = {}) {
  const edit = size ? { ...editIn, size } : editIn;
  const errors = validateEdit(edit);
  if (errors.length) throw new Error(errors.join('; '));

  const [W, H] = parseSize(edit.size || '1920x1080');
  const fps = edit.fps ?? 30;
  const fit = edit.fit ?? 'fit';
  const abs = (p) => resolve(baseDir, p);
  const inputs = [];
  const addInput = (pre, file) => {
    inputs.push({ pre, file });
    return inputs.length - 1;
  };
  const parts = [];
  const copies = [];

  // Clips become same-size, same-rate video and matching audio segments.
  const clips = edit.clips.map((c, i) => {
    if (c.image) {
      const dur = c.duration;
      const k = addInput(['-loop', '1', '-framerate', String(fps), '-t', t3(dur)], abs(c.image));
      parts.push(`[${k}:v]setpts=PTS-STARTPTS,${fitFilter(fit, W, H)},fps=${fps},format=yuv420p[v${i}]`);
      parts.push(silence(`a${i}`, dur));
      return { dur, fade: c.fade ?? 0 };
    }
    const info = probe(abs(c.src)) || {};
    const inT = c.in ?? 0;
    const outT = c.out ?? info.duration;
    if (!isNum(outT)) throw new Error(`clips[${i}]: cannot read the length of ${c.src}; set "out"`);
    const dur = outT - inT;
    const k = addInput([], abs(c.src));
    parts.push(`[${k}:v]trim=start=${t3(inT)}:end=${t3(outT)},setpts=PTS-STARTPTS,${fitFilter(fit, W, H)},` +
      `fps=${fps},format=yuv420p[v${i}]`);
    if (c.audio !== false && info.hasAudio) {
      parts.push(`[${k}:a]atrim=start=${t3(inT)}:end=${t3(outT)},asetpts=PTS-STARTPTS,aresample=${RATE},` +
        `aformat=sample_fmts=fltp:channel_layouts=stereo[a${i}]`);
    } else {
      parts.push(silence(`a${i}`, dur));
    }
    return { dur, fade: c.fade ?? 0 };
  });

  // Join clips: crossfade (xfade/acrossfade) when fade > 0, else a hard cut (concat).
  let v = 'v0';
  let a = 'a0';
  let len = clips[0].dur;
  for (let i = 1; i < clips.length; i++) {
    const c = clips[i];
    const f = Math.min(c.fade, len, c.dur);
    if (f > 0.0005) {
      parts.push(`[${v}][v${i}]xfade=transition=fade:duration=${t3(f)}:offset=${t3(len - f)}[vx${i}]`);
      parts.push(`[${a}][a${i}]acrossfade=d=${t3(f)}:c1=tri:c2=tri[ax${i}]`);
      len += c.dur - f;
      v = `vx${i}`;
      a = `ax${i}`;
    } else {
      parts.push(`[${v}][v${i}]concat=n=2:v=1:a=0[vc${i}]`);
      parts.push(`[${a}][a${i}]concat=n=2:v=0:a=1[ac${i}]`);
      len += c.dur;
      v = `vc${i}`;
      a = `ac${i}`;
    }
  }
  const total = len;

  // Overlays: video or image layers on a time window (picture-in-picture, logos, lower-third renders).
  let vcur = v;
  (edit.overlays || []).forEach((o, j) => {
    const start = o.start ?? 0;
    let dur;
    let chain;
    if (o.image) {
      dur = o.duration;
      const k = addInput(['-loop', '1', '-framerate', String(fps), '-t', t3(dur)], abs(o.image));
      chain = `[${k}:v]setpts=PTS-STARTPTS+${t3(start)}/TB`;
    } else {
      const info = probe(abs(o.src)) || {};
      const inT = o.in ?? 0;
      dur = o.duration ?? ((o.out ?? info.duration) - inT);
      if (!isNum(dur)) throw new Error(`overlays[${j}]: cannot read the length of ${o.src}; set "out"`);
      const k = addInput([], abs(o.src));
      chain = `[${k}:v]trim=start=${t3(inT)}:end=${t3(inT + dur)},setpts=PTS-STARTPTS+${t3(start)}/TB`;
    }
    const wpx = o.width === undefined ? Math.round(W * 0.25 / 2) * 2
      : o.width <= 1 ? Math.round((W * o.width) / 2) * 2 : Math.round(o.width);
    const margin = o.margin ?? 40;
    // Images keep their alpha (rgba) so logos and caption cards blend; video overlays are plain yuv420p.
    parts.push(`${chain},scale=${wpx}:-2,format=${o.image ? 'rgba' : 'yuv420p'}[ov${j}]`);
    const x = anchor(o.x ?? 'center', margin, 'x');
    const y = anchor(o.y ?? 'middle', margin, 'y');
    parts.push(`[${vcur}][ov${j}]overlay=x=${x}:y=${y}:enable='between(t,${t3(start)},${t3(start + dur)})':` +
      `eof_action=pass[vo${j}]`);
    vcur = `vo${j}`;
  });

  // Burned-in captions. SRT gets the frame-scaled style; ASS keeps its own styles.
  if (edit.captions) {
    const isSrt = typeof edit.captions.srt === 'string';
    const src = isSrt ? edit.captions.srt : edit.captions.ass;
    const name = isSrt ? 'captions.srt' : 'captions.ass';
    copies.push({ from: abs(src), to: name });
    if (isSrt) {
      const style = Object.entries(captionStyle(W, H, edit.captions.style || {}))
        .map(([k, val]) => `${k}=${val}`).join(',');
      parts.push(`[${vcur}]subtitles=${name}:force_style='${style}'[vsub]`);
    } else {
      parts.push(`[${vcur}]subtitles=${name}[vsub]`);
    }
    vcur = 'vsub';
  }
  parts.push(`[${vcur}]format=yuv420p[vout]`);

  // Audio: program audio + voice + music. Music ducks under the voice (or the program audio when no voice).
  const mixed = [];
  let program = a;
  let voiceLabel = null;
  if (edit.voice) {
    const k = addInput([], abs(edit.voice.src));
    parts.push(`[${k}:a]volume=${edit.voice.volume ?? 1},aresample=${RATE},` +
      `aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[voice]`);
    voiceLabel = 'voice';
  }
  let musicLabel = null;
  if (edit.music) {
    const k = addInput(['-stream_loop', '-1'], abs(edit.music.src));
    const fade = Math.min(edit.music.fade_out ?? 2, total);
    parts.push(`[${k}:a]volume=${edit.music.volume ?? 0.25},aresample=${RATE},` +
      `aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration=${t3(total)},asetpts=PTS-STARTPTS,` +
      `afade=t=in:st=0:d=1,afade=t=out:st=${t3(total - fade)}:d=${t3(fade)}[music_raw]`);
    const duck = 'threshold=0.03:ratio=8:attack=20:release=400';
    if (voiceLabel) {
      parts.push(`[voice]asplit=2[voice_key][voice_mix]`);
      parts.push(`[music_raw][voice_key]sidechaincompress=${duck}[music]`);
      mixed.push(program, 'voice_mix', 'music');
    } else {
      parts.push(`[${program}]asplit=2[prog_mix][prog_key]`);
      parts.push(`[music_raw][prog_key]sidechaincompress=${duck}[music]`);
      mixed.push('prog_mix', 'music');
    }
    musicLabel = 'music';
  }
  if (!musicLabel) {
    if (voiceLabel) mixed.push(program, voiceLabel);
    else mixed.push(program);
  }
  const mixIn = mixed.map((l) => `[${l}]`).join('');
  if (mixed.length > 1) {
    parts.push(`${mixIn}amix=inputs=${mixed.length}:duration=longest:dropout_transition=0:normalize=0[amixed]`);
  } else {
    parts.push(`[${mixed[0]}]anull[amixed]`);
  }
  const lufs = edit.loudness ?? -14;
  parts.push(`[amixed]atrim=end=${t3(total)},asetpts=PTS-STARTPTS,loudnorm=I=${lufs}:TP=-1.5:LRA=11,` +
    `aresample=${RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]`);

  const graph = parts.join(';');
  const args = ['-hide_banner', '-y'];
  for (const input of inputs) args.push(...input.pre, '-i', input.file);
  args.push('-filter_complex', graph, '-map', '[vout]', '-map', '[aout]',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(fps),
    '-c:a', 'aac', '-b:a', '192k', '-ar', String(RATE), '-ac', '2',
    '-t', t3(total), '-movflags', '+faststart', out);
  const commandLine = ['ffmpeg', ...args].map(shellQuote).join(' ');
  return { args, graph, duration: total, copies, commandLine, size: [W, H], fps };
}

function shellQuote(s) {
  if (/^[A-Za-z0-9_./:=+,-]+$/.test(s)) return s;
  return `'${s.replace(/'/g, "'\\''")}'`;
}

// Parses SRT text into [{start, end, text}] with times in seconds.
export function parseSrt(text) {
  const cues = [];
  const blocks = text.replace(/\r/g, '').split(/\n\s*\n/);
  const time = (t) => {
    const m = /^(\d+):(\d{2}):(\d{2})[,.](\d{1,3})$/.exec(t.trim());
    if (!m) throw new Error(`bad SRT time "${t}"`);
    return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4].padEnd(3, '0')) / 1000;
  };
  for (const block of blocks) {
    const lines = block.split('\n').map((l) => l.trimEnd()).filter((l, i, arr) => !(i === 0 && l === '' ));
    const arrow = lines.findIndex((l) => l.includes('-->'));
    if (arrow < 0) continue;
    const [a, b] = lines[arrow].split('-->');
    const body = lines.slice(arrow + 1).join('\n').trim();
    if (!body) continue;
    cues.push({ start: time(a), end: time(b), text: body });
  }
  return cues;
}

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Draws each cue as a full-frame transparent PNG with Chromium. Used when ffmpeg has no subtitles filter.
async function renderCaptionCards(cues, w, h, dir, style) {
  const pw = await loadPlaywright();
  const browser = await launchChromium(pw, { headless: true });
  const cards = [];
  try {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const fontSize = style.FontSize;
    const stroke = style.Outline * 2;
    for (const [i, cue] of cues.entries()) {
      const html = `<!doctype html><html><head><meta charset="utf-8"><style>
        html,body{margin:0;width:${w}px;height:${h}px;background:transparent;overflow:hidden}
        .cap{position:absolute;left:${style.MarginL}px;right:${style.MarginR}px;bottom:${style.MarginV}px;
          text-align:center;font-family:"Noto Sans",Arial,sans-serif;font-weight:700;font-size:${fontSize}px;
          line-height:1.2;color:#fff;-webkit-text-stroke:${stroke}px #000;paint-order:stroke fill}
        </style></head><body><div class="cap">${escapeHtml(cue.text).replace(/\n/g, '<br>')}</div></body></html>`;
      await page.setContent(html, { waitUntil: 'load' });
      await page.evaluate(() => document.fonts.ready);
      const file = join(dir, `cue-${String(i).padStart(4, '0')}.png`);
      await page.screenshot({ path: file, omitBackground: true, type: 'png' });
      cards.push({ file, start: cue.start, duration: cue.end - cue.start });
    }
  } finally {
    await browser.close();
  }
  return cards;
}

async function hasSubtitlesFilter() {
  const r = await run('ffmpeg', ['-hide_banner', '-filters']);
  return /\ssubtitles\s/.test(r.stdout);
}

async function main() {
  const args = parseArgs(process.argv.slice(2), { out: 'value', size: 'value', 'dry-run': 'bool' });
  const [editArg] = args._;
  if (!editArg || !args.out) fail('usage: edit.mjs <edit.json> --out out.mp4 [--size 9:16|1:1|16:9] [--dry-run]', 2);
  requireFfmpeg();

  const editPath = resolve(editArg);
  const baseDir = dirname(editPath);
  let edit;
  try {
    edit = JSON.parse(readFileSync(editPath, 'utf8'));
  } catch (e) {
    fail(`cannot read ${editPath}: ${e.message}`, 2);
  }
  const problems = validateEdit({ ...edit, size: args.size || edit.size });
  if (problems.length) fail(`bad edit file: ${problems.join('; ')}`, 2);

  // Facts about each media file (duration and audio), probed once.
  const media = new Map();
  const wanted = [];
  for (const c of [...(edit.clips || []), ...(edit.overlays || [])]) if (c.src) wanted.push(resolve(baseDir, c.src));
  if (edit.voice) wanted.push(resolve(baseDir, edit.voice.src));
  if (edit.music) wanted.push(resolve(baseDir, edit.music.src));
  for (const file of new Set(wanted)) {
    let p;
    try {
      p = await probe(file);
    } catch (e) {
      fail(e.message, 2);
    }
    const dur = Number(p.format && p.format.duration);
    media.set(file, { duration: Number.isFinite(dur) ? dur : undefined, hasAudio: (p.streams || []).some((s) => s.codec_type === 'audio') });
  }

  const outPath = resolve(args.out);
  const workDir = mkdtempSync(join(tmpdir(), 'vp-edit-'));
  try {
    if (edit.captions && !(await hasSubtitlesFilter())) {
      // ffmpeg built without libass: draw each cue as a PNG and overlay it instead.
      if (typeof edit.captions.ass === 'string') {
        fail('this ffmpeg has no subtitles filter (built without libass), so ASS captions cannot be burned; use SRT', 2);
      }
      const cues = parseSrt(readFileSync(resolve(baseDir, edit.captions.srt), 'utf8'));
      const [w, h] = parseSize(args.size || edit.size || '1920x1080');
      const style = { ...captionStyleFor(w, h), ...(edit.captions.style || {}) };
      const cards = await renderCaptionCards(cues, w, h, workDir, style);
      edit = {
        ...edit,
        captions: undefined,
        overlays: [...(edit.overlays || []), ...cards.map((c) => ({
          image: c.file, start: c.start, duration: c.duration, x: 0, y: 0, width: w, margin: 0,
        }))],
      };
      process.stderr.write('note: ffmpeg has no subtitles filter; captions drawn as images\n');
    }
  } catch (err) {
    rmSync(workDir, { recursive: true, force: true });
    throw err;
  }
  const plan = buildPlan(edit, { baseDir, probe: (file) => media.get(file) || {}, out: outPath, size: args.size });
  if (args['dry-run']) {
    process.stdout.write(`${plan.commandLine}\n\n# filtergraph\n${plan.graph.split(';').join(';\n')}\n`);
    if (plan.copies.length) {
      process.stdout.write(`\n# run from a folder that holds: ${plan.copies.map((c) => `${c.to} (from ${c.from})`).join(', ')}\n`);
    }
    return;
  }

  try {
    for (const c of plan.copies) copyFileSync(c.from, join(workDir, c.to));
    const ff = await run('ffmpeg', plan.args.slice(), { cwd: workDir });
    if (ff.code !== 0) {
      fail(`ffmpeg failed: ${ff.stderr.trim().split('\n').slice(-3).join(' | ')}`);
    }
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
  process.stdout.write(`${JSON.stringify({ out: outPath, duration_s: Number(plan.duration.toFixed(3)), size: plan.size, fps: plan.fps })}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((err) => fail(err && err.message ? err.message : String(err)));
}
