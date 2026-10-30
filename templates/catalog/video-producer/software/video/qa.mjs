#!/usr/bin/env node
// Checks a finished video: ffprobe summary, loudness, black and frozen segments, and a contact sheet.
// Usage: node qa.mjs <video.mp4> --out DIR [--cols 4] [--rows 3]
// Writes DIR/qa.json and DIR/contact.png and prints the report as JSON.
import { closeSync, mkdirSync, openSync, readSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fail, parseArgs, probe, requireFfmpeg, run } from './lib.mjs';

const LOUDNESS_TARGET = -14;

// Reads only the top-level MP4 box headers, so we know whether moov comes before mdat (faststart).
export function topLevelBoxes(file) {
  const fd = openSync(file, 'r');
  const size = statSync(file).size;
  const boxes = [];
  const head = Buffer.alloc(16);
  let pos = 0;
  try {
    while (pos + 8 <= size && boxes.length < 64) {
      readSync(fd, head, 0, 16, pos);
      let len = head.readUInt32BE(0);
      const type = head.toString('latin1', 4, 8);
      let hdr = 8;
      if (len === 1) {
        len = Number(head.readBigUInt64BE(8));
        hdr = 16;
      } else if (len === 0) {
        len = size - pos;
      }
      if (len < hdr) break;
      boxes.push(type);
      pos += len;
    }
  } finally {
    closeSync(fd);
  }
  return boxes;
}

export function parseLoudness(stderr) {
  const idx = stderr.lastIndexOf('Summary:');
  if (idx < 0) return null;
  const tail = stderr.slice(idx);
  const num = (re) => {
    const m = re.exec(tail);
    return m ? Number(m[1]) : null;
  };
  return {
    integrated_lufs: num(/Integrated loudness:\s*\n\s*I:\s+(-?[\d.]+) LUFS/),
    loudness_range_lu: num(/Loudness range:\s*\n\s*LRA:\s+([\d.]+) LU/),
    true_peak_dbtp: num(/True peak:\s*\n\s*Peak:\s+(-?[\d.]+) dBFS/),
  };
}

export function parseBlack(stderr) {
  const out = [];
  const re = /black_start:([\d.]+) black_end:([\d.]+)/g;
  let m;
  while ((m = re.exec(stderr))) out.push({ start_s: Number(m[1]), end_s: Number(m[2]) });
  return out;
}

export function parseFreeze(stderr, duration) {
  const starts = [...stderr.matchAll(/freeze_start: ([\d.]+)/g)].map((m) => Number(m[1]));
  const ends = [...stderr.matchAll(/freeze_end: ([\d.]+)/g)].map((m) => Number(m[1]));
  return starts.map((s, i) => {
    const end = i < ends.length ? ends[i] : duration;
    return { start_s: s, end_s: end, duration_s: Number((end - s).toFixed(3)) };
  });
}

function summarize(p, file, boxes) {
  const v = p.streams.find((s) => s.codec_type === 'video');
  const a = p.streams.find((s) => s.codec_type === 'audio');
  const [num, den] = v && v.r_frame_rate ? v.r_frame_rate.split('/').map(Number) : [0, 1];
  return {
    file,
    duration_s: Number(Number(p.format.duration || 0).toFixed(3)),
    bytes: Number(p.format.size),
    width: v ? v.width : null,
    height: v ? v.height : null,
    fps: den ? Number((num / den).toFixed(3)) : null,
    video_codec: v ? v.codec_name : null,
    pixel_format: v ? v.pix_fmt : null,
    audio_codec: a ? a.codec_name : null,
    audio_channels: a ? a.channels : null,
    audio_sample_rate: a ? Number(a.sample_rate) : null,
    faststart: boxes.indexOf('moov') >= 0 && boxes.indexOf('moov') < boxes.indexOf('mdat'),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2), { out: 'value', cols: 'value', rows: 'value' });
  const [videoArg] = args._;
  if (!videoArg || !args.out) fail('usage: qa.mjs <video.mp4> --out DIR [--cols 4] [--rows 3]', 2);
  requireFfmpeg();
  const file = resolve(videoArg);
  const outDir = resolve(args.out);
  const cols = Number(args.cols || 4);
  const rows = Number(args.rows || 3);
  mkdirSync(outDir, { recursive: true });

  const p = await probe(file);
  const boxes = topLevelBoxes(file);
  const summary = summarize(p, file, boxes);
  const hasAudio = summary.audio_codec !== null;
  const dur = summary.duration_s;

  const ebu = await run('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-af', 'ebur128=peak=true', '-f', 'null', '-']);
  const loudness = hasAudio ? parseLoudness(ebu.stderr) : null;
  const black = parseBlack((await run('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-an',
    '-vf', 'blackdetect=d=0.5:pix_th=0.10', '-f', 'null', '-'])).stderr);
  const frozen = parseFreeze((await run('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-an',
    '-vf', 'freezedetect=n=-60dB:d=2', '-f', 'null', '-'])).stderr, dur);

  // One frame per slot, evenly spaced. The fps filter gives at least cols*rows frames, tile keeps the grid.
  const n = cols * rows;
  const sheetPath = join(outDir, 'contact.png');
  const sheet = await run('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', file,
    '-vf', `fps=${n}/${Math.max(dur, 0.1)},scale=320:-2,tile=${cols}x${rows}`, '-frames:v', '1', sheetPath]);
  const sheetTimes = Array.from({ length: n }, (_, i) => Number(((i * dur) / n).toFixed(3)));

  const warnings = [];
  if (!hasAudio) warnings.push('no audio track');
  if (loudness && loudness.integrated_lufs !== null && Math.abs(loudness.integrated_lufs - LOUDNESS_TARGET) > 1) {
    warnings.push(`loudness ${loudness.integrated_lufs} LUFS, target ${LOUDNESS_TARGET}`);
  }
  for (const b of black) {
    if (b.start_s > 0.1 && b.end_s - b.start_s >= 1) warnings.push(`black for ${(b.end_s - b.start_s).toFixed(1)}s at ${b.start_s}s`);
  }
  for (const f of frozen) warnings.push(`frozen picture for ${f.duration_s}s at ${f.start_s}s`);
  if (sheet.code !== 0) warnings.push(`contact sheet failed: ${sheet.stderr.trim().split('\n').pop()}`);

  const report = {
    ...summary,
    loudness: loudness,
    black_segments: black,
    frozen_segments: frozen,
    contact_sheet: { path: sheetPath, cols, rows, times_s: sheetTimes },
    warnings,
  };
  writeFileSync(join(outDir, 'qa.json'), `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((err) => fail(err && err.message ? err.message : String(err)));
}
