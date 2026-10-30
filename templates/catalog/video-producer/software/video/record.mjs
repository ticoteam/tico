#!/usr/bin/env node
// Records a scripted browser session to MP4 with a visible cursor.
// Usage: node record.mjs <shots.json> --out out.mp4 [--size 1920x1080] [--headed | --headless] [--keep-webm] [--as-bot]
// Steps are described in README.md. Headed is the default on Linux when DISPLAY is set.
// --as-bot records your company's Tico as this bot (during a bot turn only); paths starting with / go to your Tico.
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { fail, launchChromium, loadPlaywright, parseArgs, parseSize, requireFfmpeg, run, sleep } from './lib.mjs';

const STEP_KINDS = ['goto', 'click', 'type', 'wait', 'scroll', 'zoom', 'highlight', 'pause'];

const isNum = (v, min, max) => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;
const isStr = (v) => typeof v === 'string' && v.trim().length > 0;

// Returns a list of problems in a shots document. An empty list means it is valid.
export function validateShots(doc) {
  const errors = [];
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return ['shots file must be a JSON object'];
  if (doc.viewport !== undefined) {
    try { parseSize(doc.viewport); } catch (e) { errors.push(`viewport: ${e.message}`); }
  }
  if (!Array.isArray(doc.steps) || doc.steps.length === 0) return [...errors, 'steps must be a non-empty array'];
  doc.steps.forEach((s, i) => {
    const at = `steps[${i}]`;
    if (!s || typeof s !== 'object') return errors.push(`${at} must be an object`);
    if (!STEP_KINDS.includes(s.do)) return errors.push(`${at}.do must be one of ${STEP_KINDS.join(', ')}`);
    switch (s.do) {
      case 'goto':
        if (!isStr(s.url)) errors.push(`${at}.url must be a non-empty string`);
        break;
      case 'click':
      case 'highlight':
        if (!isStr(s.selector)) errors.push(`${at}.selector must be a non-empty string`);
        break;
      case 'type':
        if (!isStr(s.selector)) errors.push(`${at}.selector must be a non-empty string`);
        if (typeof s.text !== 'string') errors.push(`${at}.text must be a string`);
        if (s.delay_ms !== undefined && !isNum(s.delay_ms, 0, 2000)) errors.push(`${at}.delay_ms must be 0-2000`);
        break;
      case 'wait':
        if (s.selector === undefined && s.ms === undefined) errors.push(`${at} needs selector or ms`);
        if (s.ms !== undefined && !isNum(s.ms, 0, 120000)) errors.push(`${at}.ms must be 0-120000`);
        break;
      case 'pause':
        if (!isNum(s.ms, 0, 120000)) errors.push(`${at}.ms must be 0-120000`);
        break;
      case 'scroll':
        if (s.y === undefined && s.selector === undefined) errors.push(`${at} needs y or selector`);
        if (s.y !== undefined && !isNum(s.y, -100000, 100000)) errors.push(`${at}.y must be a number`);
        break;
      case 'zoom':
        if (s.reset !== true) {
          if (!isStr(s.selector)) errors.push(`${at} needs selector (or reset: true)`);
          if (!isNum(s.scale, 1, 4)) errors.push(`${at}.scale must be 1-4`);
        }
        break;
      default:
        break;
    }
    if (s.ms !== undefined && s.do !== 'wait' && s.do !== 'pause' && !isNum(s.ms, 0, 120000)) {
      errors.push(`${at}.ms must be 0-120000`);
    }
  });
  return errors;
}

const TOKEN_FILE = 'tico-file:';

// Reads the Tico server and this bot's token from the turn's environment. Throws one plain line when one is missing.
// The token is never put in a message.
export function hubAuth(env) {
  const base = (env.HUB_API_URL || '').trim();
  if (!base) throw new Error('--as-bot needs HUB_API_URL, which the runner sets during a bot turn');
  let origin;
  try {
    origin = new URL(base).origin;
  } catch {
    throw new Error(`HUB_API_URL is not a URL: ${base}`);
  }
  let token = (env.HUB_TOKEN || '').trim();
  if (!token) throw new Error('--as-bot needs HUB_TOKEN, which the runner sets during a bot turn');
  if (token.startsWith(TOKEN_FILE)) {
    const file = resolve(token.slice(TOKEN_FILE.length));
    try {
      token = readFileSync(file, 'utf8').trim();
    } catch {
      throw new Error(`cannot read the bot token file ${file}`);
    }
    if (!token) throw new Error(`the bot token file ${file} is empty`);
  }
  return { origin, token };
}

// Headers for one request. Only requests to the Tico server get the bot token; other sites get null (no change).
export function botHeaders(url, hub, headers) {
  if (!hub) return null;
  let origin = null;
  try {
    origin = new URL(url).origin;
  } catch {
    return null;
  }
  if (origin !== hub.origin) return null;
  return { ...headers, authorization: `Bearer ${hub.token}` };
}

// Where a goto step points. With a Tico origin (--as-bot), "/path" is on the Tico server.
// Otherwise relative paths are files next to the shots file.
export function resolveStepUrl(url, shotsDir, hubOrigin) {
  if (hubOrigin && url.startsWith('/')) return new URL(url, hubOrigin).href;
  return /^(https?|file):/.test(url) ? url : pathToFileURL(join(shotsDir, url)).href;
}

// In-page cursor overlay. Injected into every page of the recording context (top frame only).
// The overlay lives inside <html>, so zoom (a transform on <html>) is undone for its position.
const CURSOR_JS = () => {
  if (window.top !== window || window.__vpCursor) return;
  const zoom = { s: 1, ox: 0, oy: 0 };
  let raw = { x: -100, y: -100 };
  const toLocal = (x, y) => (zoom.s === 1 ? [x, y] : [zoom.ox + (x - zoom.ox) / zoom.s, zoom.oy + (y - zoom.oy) / zoom.s]);
  const style = document.createElement('style');
  style.textContent = `
    #__vp_cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 2147483647;
      pointer-events: none; transition: transform 70ms linear; will-change: transform; }
    #__vp_cursor.down svg { transform: scale(0.86); }
    #__vp_cursor svg { transition: transform 80ms ease; display: block; }
    .__vp_ripple { position: fixed; width: 40px; height: 40px; margin: -20px 0 0 -20px; border-radius: 50%;
      border: 3px solid #2456d6; pointer-events: none; z-index: 2147483646; animation: __vp_ripple 500ms ease-out forwards; }
    @keyframes __vp_ripple { from { transform: scale(0.4); opacity: 0.9; } to { transform: scale(1.6); opacity: 0; } }
    .__vp_box { position: fixed; border: 4px solid #f59e0b; border-radius: 8px; box-shadow: 0 0 0 9999px rgba(0,0,0,0.0);
      pointer-events: none; z-index: 2147483645; transition: opacity 200ms ease; }
  `;
  const place = (x, y) => {
    const cur = document.getElementById('__vp_cursor');
    if (!cur) return;
    const [lx, ly] = toLocal(x, y);
    cur.style.transform = `translate(${lx}px, ${ly}px)`;
  };
  const add = () => {
    if (document.getElementById('__vp_cursor')) return;
    document.documentElement.appendChild(style);
    const cur = document.createElement('div');
    cur.id = '__vp_cursor';
    cur.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M3 2 L3 19 L8 14.5 L11 21 L14 19.7 L11 13.2 L18 13 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(cur);
    place(raw.x, raw.y);
  };
  if (document.documentElement) add();
  document.addEventListener('DOMContentLoaded', add);
  window.addEventListener('mousemove', (e) => { raw = { x: e.clientX, y: e.clientY }; place(raw.x, raw.y); }, true);
  window.addEventListener('mousedown', () => {
    const cur = document.getElementById('__vp_cursor');
    if (cur) cur.classList.add('down');
    const r = document.createElement('div');
    r.className = '__vp_ripple';
    const [lx, ly] = toLocal(raw.x, raw.y);
    r.style.left = `${lx}px`;
    r.style.top = `${ly}px`;
    document.documentElement.appendChild(r);
    setTimeout(() => r.remove(), 600);
  }, true);
  window.addEventListener('mouseup', () => {
    const cur = document.getElementById('__vp_cursor');
    if (cur) cur.classList.remove('down');
  }, true);
  window.__vpCursor = {
    zoom(scale, cx, cy, ms) {
      const root = document.documentElement;
      root.style.transition = `transform ${ms}ms ease-in-out`;
      root.style.transformOrigin = `${cx}px ${cy}px`;
      root.style.transform = scale === 1 ? '' : `scale(${scale})`;
      zoom.s = scale;
      zoom.ox = cx;
      zoom.oy = cy;
      place(raw.x, raw.y);
    },
    highlight(x, y, w, h, ms) {
      const box = document.createElement('div');
      box.className = '__vp_box';
      const [lx, ly] = toLocal(x, y);
      box.style.left = `${lx - 6}px`;
      box.style.top = `${ly - 6}px`;
      box.style.width = `${w + 12}px`;
      box.style.height = `${h + 12}px`;
      document.documentElement.appendChild(box);
      setTimeout(() => { box.style.opacity = '0'; setTimeout(() => box.remove(), 220); }, Math.max(0, ms - 220));
    },
  };
};

// Seeded so the same shots file always types with the same rhythm.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2), {
    out: 'value', size: 'value', headed: 'bool', headless: 'bool', 'keep-webm': 'bool', 'as-bot': 'bool',
  });
  const [shotsArg] = args._;
  if (!shotsArg || !args.out) fail('usage: record.mjs <shots.json> --out out.mp4 [--size WxH] [--headed] [--as-bot]', 2);
  let hub = null;
  if (args['as-bot']) {
    try {
      hub = hubAuth(process.env);
    } catch (e) {
      fail(e.message, 2);
    }
  }
  requireFfmpeg();

  const shotsPath = resolve(shotsArg);
  const shotsDir = dirname(shotsPath);
  let doc;
  try {
    doc = JSON.parse(readFileSync(shotsPath, 'utf8'));
  } catch (e) {
    fail(`cannot read ${shotsPath}: ${e.message}`, 2);
  }
  const problems = validateShots(doc);
  if (problems.length) fail(`bad shots file: ${problems.join('; ')}`, 2);

  const [width, height] = parseSize(args.size || doc.viewport || '1920x1080');
  const outPath = resolve(args.out);
  const headed = args.headed || (!args.headless && process.platform === 'linux' && Boolean(process.env.DISPLAY));
  const workDir = mkdtempSync(join(tmpdir(), 'vp-record-'));

  const pw = await loadPlaywright();
  const browser = await launchChromium(pw, {
    headless: !headed,
    args: ['--force-device-scale-factor=1', '--hide-scrollbars'],
  });
  let leadSeconds = 0;
  let webmPath = null;
  try {
    const context = await browser.newContext({
      viewport: { width, height },
      recordVideo: { dir: workDir, size: { width, height } },
    });
    if (hub) {
      // The bot token goes to the Tico server only. Every other request is passed on unchanged.
      await context.route('**/*', (route, request) => {
        const headers = botHeaders(request.url(), hub, request.headers());
        return headers ? route.continue({ headers }) : route.continue();
      });
    }
    await context.addInitScript(CURSOR_JS);
    const page = await context.newPage();
    const pageStarted = Date.now();
    const video = page.video();
    let firstLoaded = null;
    const cursor = { x: width / 2, y: height / 2 };
    const rand = rng(7);

    // Glides the cursor from where it is to (x, y) over about 350 ms.
    const glideTo = async (x, y) => {
      const steps = 24;
      for (let i = 1; i <= steps; i++) {
        const k = i / steps;
        const e = 1 - Math.pow(1 - k, 3);
        await page.mouse.move(cursor.x + (x - cursor.x) * e, cursor.y + (y - cursor.y) * e);
        await sleep(14);
      }
      cursor.x = x;
      cursor.y = y;
    };
    const resolveUrl = (u) => resolveStepUrl(u, shotsDir, hub && hub.origin);
    const locate = async (selector) => {
      const loc = page.locator(selector).first();
      await loc.waitFor({ state: 'visible', timeout: 15000 });
      return loc;
    };
    const centerOf = async (loc) => {
      await loc.scrollIntoViewIfNeeded();
      const box = await loc.boundingBox();
      if (!box) fail('element has no box to point at');
      return { x: box.x + box.width / 2, y: box.y + box.height / 2, box };
    };

    for (const [i, step] of doc.steps.entries()) {
      switch (step.do) {
        case 'goto':
          await page.goto(resolveUrl(step.url), { waitUntil: 'load' });
          if (firstLoaded === null) firstLoaded = Date.now();
          break;
        case 'click': {
          const loc = await locate(step.selector);
          const c = await centerOf(loc);
          await glideTo(c.x, c.y);
          await page.mouse.down();
          await sleep(90);
          await page.mouse.up();
          break;
        }
        case 'type': {
          const loc = await locate(step.selector);
          const c = await centerOf(loc);
          await glideTo(c.x, c.y);
          await page.mouse.down();
          await page.mouse.up();
          const base = step.delay_ms ?? 70;
          for (const ch of step.text) {
            await page.keyboard.type(ch);
            await sleep(Math.round(base * (0.7 + rand() * 0.6)));
          }
          break;
        }
        case 'wait':
          if (step.selector !== undefined) await locate(step.selector);
          if (step.ms !== undefined) await sleep(step.ms);
          break;
        case 'pause':
          await sleep(step.ms);
          break;
        case 'scroll':
          if (step.selector !== undefined) {
            await (await locate(step.selector)).scrollIntoViewIfNeeded();
          } else {
            await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), step.y);
          }
          await sleep(step.ms ?? 600);
          break;
        case 'zoom':
          if (step.reset) {
            await page.evaluate((ms) => window.__vpCursor && window.__vpCursor.zoom(1, 0, 0, ms), step.ms ?? 500);
          } else {
            const loc = await locate(step.selector);
            const c = await centerOf(loc);
            await page.evaluate(([s, x, y, ms]) => window.__vpCursor.zoom(s, x, y, ms), [step.scale, c.x, c.y, step.ms ?? 600]);
          }
          await sleep(step.ms ?? 600);
          break;
        case 'highlight': {
          const loc = await locate(step.selector);
          const c = await centerOf(loc);
          await page.evaluate(([x, y, w, h, ms]) => window.__vpCursor.highlight(x, y, w, h, ms),
            [c.x - c.box.width / 2, c.y - c.box.height / 2, c.box.width, c.box.height, step.ms ?? 1200]);
          await sleep(step.ms ?? 1200);
          break;
        }
        default:
          fail(`step ${i}: unknown ${step.do}`, 2);
      }
    }
    await sleep(300);
    await page.close();
    await context.close();
    webmPath = await video.path();
    leadSeconds = Math.max(0, ((firstLoaded ?? pageStarted) - pageStarted) / 1000 - 0.1);
  } finally {
    await browser.close();
  }

  // Re-encode to H.264, trimming the blank lead-in before the first page load.
  const ff = await run('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-ss', leadSeconds.toFixed(3), '-i', webmPath,
    '-an', '-vf', 'fps=30', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', outPath]);
  if (ff.code !== 0) fail(`ffmpeg failed: ${ff.stderr.trim().split('\n').slice(-2).join(' | ')}`);
  if (args['keep-webm']) spawnSync('cp', [webmPath, `${outPath}.webm`]);
  rmSync(workDir, { recursive: true, force: true });
  process.stdout.write(`${JSON.stringify({ out: outPath, trimmed_lead_in_s: Number(leadSeconds.toFixed(3)), size: [width, height], headed })}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((err) => fail(err && err.message ? err.message : String(err)));
}
