#!/usr/bin/env node
// Renders an HTML composition frame by frame into an MP4.
// Usage: node render.mjs <composition.html> --out out.mp4 [--fps 30] [--size 1080x1920] [--audio a.wav]
//        [--from ms] [--to ms] [--duration ms]
// Seek contract: the page sets window.__timeline = {duration_ms, fps?, seek(ms)}. Without it, every
// document.getAnimations() entry is paused at the frame time, and :root gets --t (CSS time, "1234ms") and
// --t-ms (plain number of ms).
import { mkdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { fail, launchChromium, loadPlaywright, parseArgs, parseSize, requireFfmpeg } from './lib.mjs';

const USAGE = 'usage: render.mjs <composition.html> --out out.mp4 [--fps N] [--size WxH|9:16] [--audio FILE] ' +
  '[--from MS] [--to MS] [--duration MS]';

// Runs in the page. Seeks every running animation to time t (ms).
const SEEK_FALLBACK = (t) => {
  for (const a of document.getAnimations()) {
    a.pause();
    a.currentTime = t;
  }
  const root = document.documentElement.style;
  root.setProperty('--t', `${t}ms`);
  root.setProperty('--t-ms', String(t));
};

// Finite end time of the longest CSS/WAAPI animation, in ms. 0 when there is none.
const longestAnimationMs = () => {
  let max = 0;
  for (const a of document.getAnimations()) {
    const end = a.effect.getComputedTiming().endTime;
    if (Number.isFinite(end) && end > max) max = end;
  }
  return max;
};

async function main() {
  const args = parseArgs(process.argv.slice(2), {
    out: 'value', fps: 'value', size: 'value', audio: 'value', from: 'value', to: 'value', duration: 'value',
  });
  const [htmlArg] = args._;
  if (!htmlArg || !args.out) fail(USAGE, 2);
  requireFfmpeg();

  const htmlPath = resolve(htmlArg);
  const outPath = resolve(args.out);
  const [width, height] = parseSize(args.size || '1920x1080');
  const audioPath = args.audio ? resolve(args.audio) : null;

  const pw = await loadPlaywright();
  const browser = await launchChromium(pw, {
    headless: true,
    args: ['--hide-scrollbars', '--font-render-hinting=none', '--force-color-profile=srgb', '--disable-lcd-text'],
  });
  let ffmpegExit = null;
  try {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => Promise.all([...document.images].map((img) => (img.complete ? null : img.decode().catch(() => null)))));
    if (errors.length) fail(`composition threw on load: ${errors[0]}`);

    const timeline = await page.evaluate(() => {
      const tl = window.__timeline;
      if (!tl) return null;
      return { duration_ms: tl.duration_ms, fps: tl.fps, hasSeek: typeof tl.seek === 'function' };
    });

    let durationMs = null;
    if (timeline && Number.isFinite(timeline.duration_ms)) durationMs = timeline.duration_ms;
    if (args.duration) durationMs = Number(args.duration);
    if (durationMs === null) durationMs = await page.evaluate(longestAnimationMs);
    if (!durationMs) {
      fail('cannot tell the length: set window.__timeline.duration_ms or pass --duration MS');
    }
    const fps = Number(args.fps || (timeline && timeline.fps) || 30);
    const from = Number(args.from || 0);
    const to = args.to !== undefined ? Number(args.to) : durationMs;
    if (!(fps > 0) || !(to > from) || from < 0) fail('bad --fps/--from/--to values', 2);
    const frames = Math.round(((to - from) / 1000) * fps);

    const hasSeek = timeline && timeline.hasSeek;
    const seekFrame = async (t) => {
      if (hasSeek) await page.evaluate((ms) => window.__timeline.seek(ms), t);
      else await page.evaluate(SEEK_FALLBACK, t);
      // Two frames so the seeked styles are painted before the screenshot.
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    };

    const cdp = await context.newCDPSession(page);
    mkdirSync(dirname(outPath), { recursive: true });
    const args264 = ['-hide_banner', '-loglevel', 'error', '-y',
      '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'png', '-i', 'pipe:0'];
    if (audioPath) args264.push('-ss', String(from / 1000), '-i', audioPath);
    args264.push('-map', '0:v', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
      '-r', String(fps), '-t', String(frames / fps));
    if (audioPath) args264.push('-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2');
    args264.push('-movflags', '+faststart', outPath);

    const ff = spawn('ffmpeg', args264, { stdio: ['pipe', 'ignore', 'pipe'] });
    let ffErr = '';
    ff.stderr.on('data', (d) => (ffErr += d));
    ff.on('close', (code) => (ffmpegExit = code));
    const stdin = ff.stdin;
    stdin.on('error', () => {}); // reported through the exit code below

    for (let i = 0; i < frames; i++) {
      const t = from + Math.round((i * 1000) / fps);
      await seekFrame(t);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      if (ffmpegExit !== null) break;
      if (!stdin.write(Buffer.from(data, 'base64'))) await once(stdin, 'drain');
    }
    stdin.end();
    await new Promise((r) => ff.on('close', r));
    if (ffmpegExit !== 0) fail(`ffmpeg failed: ${ffErr.trim().split('\n').slice(-3).join(' | ')}`);
    if (errors.length) process.stderr.write(`warning: composition logged ${errors.length} page error(s)\n`);

    process.stdout.write(`${JSON.stringify({ out: outPath, frames, fps, size: [width, height], duration_ms: to - from })}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => fail(err && err.message ? err.message : String(err)));
