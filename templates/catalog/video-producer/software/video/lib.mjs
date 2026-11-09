// Shared helpers for the video toolkit: tool checks, sizes, process calls, Playwright loading.
import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export const PRESETS = {
  '9:16': [1080, 1920],
  '1:1': [1080, 1080],
  '16:9': [1920, 1080],
};

// "9:16", "1:1", "16:9" or "WxH" -> [w, h]. Both sides must be even (H.264 needs it).
export function parseSize(text) {
  const s = String(text).trim();
  if (PRESETS[s]) return PRESETS[s];
  const m = /^(\d+)x(\d+)$/.exec(s);
  if (!m) throw new Error(`bad size "${s}": use 9:16, 1:1, 16:9 or WxH`);
  const w = Number(m[1]);
  const h = Number(m[2]);
  if (!w || !h || w % 2 || h % 2) throw new Error(`bad size "${s}": width and height must be even and above 0`);
  return [w, h];
}

// Stops with one clear line when ffmpeg or ffprobe is not on PATH.
export function requireFfmpeg() {
  for (const bin of ['ffmpeg', 'ffprobe']) {
    const r = spawnSync(bin, ['-version'], { stdio: 'ignore' });
    if (r.error || r.status !== 0) {
      fail(`${bin} not found on PATH: install ffmpeg (macOS: brew install ffmpeg, Debian: apt-get install ffmpeg)`);
    }
  }
}

export function fail(message, code = 1) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(code);
}

// Runs a command, resolves with {code, stdout, stderr}. Never rejects.
export function run(cmd, args, { cwd, input } = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('error', (err) => resolve({ code: -1, stdout, stderr: String(err) }));
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    if (input) child.stdin.end(input);
    else child.stdin.end();
  });
}

// ffprobe a file, returns the parsed JSON (streams + format).
export async function probe(file) {
  const r = await run('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file]);
  if (r.code !== 0) throw new Error(`ffprobe failed on ${file}: ${r.stderr.trim().split('\n').pop()}`);
  return JSON.parse(r.stdout);
}

// Loads the playwright package. Tries the normal module lookup, then the current folder,
// then VP_PLAYWRIGHT (the package directory, for example /path/node_modules/playwright).
export async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {
    // fall through
  }
  const tries = [];
  try {
    tries.push(createRequire(join(process.cwd(), 'noop.js')).resolve('playwright'));
  } catch {
    // not in the current folder
  }
  if (process.env.VP_PLAYWRIGHT) tries.push(process.env.VP_PLAYWRIGHT);
  for (const p of tries) {
    try {
      const file = p.endsWith('.js') || p.endsWith('.mjs') ? p : join(p, 'index.mjs');
      return await import(pathToFileURL(file).href);
    } catch {
      // try the next one
    }
  }
  fail('playwright not found: run `npm i playwright@1.63.0` in the folder you run from, or set VP_PLAYWRIGHT');
}

// Launches the installed Google Chrome when there is one, else Playwright's bundled Chromium. A missing browser
// prints one line instead of a stack trace.
const missing = (err) => /Executable doesn't exist|playwright install|is not found at/i.test(String(err && err.message ? err.message : err));

export async function launchChromium(pw, options) {
  try {
    return await pw.chromium.launch({ ...options, channel: 'chrome' });
  } catch (err) {
    if (!missing(err)) throw err;
  }
  try {
    return await pw.chromium.launch(options);
  } catch (err) {
    if (missing(err)) fail('no browser found: install Google Chrome, or run `npx playwright install chromium`');
    throw err;
  }
}

export function parseArgs(argv, spec) {
  // spec: { flagName: 'value' | 'bool' }. Returns {_: [...positional], flagName: value}.
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) {
      out._.push(a);
      continue;
    }
    const name = a.slice(2);
    if (!(name in spec)) fail(`unknown option --${name}`, 2);
    if (spec[name] === 'bool') {
      out[name] = true;
    } else {
      const v = argv[++i];
      if (v === undefined) fail(`--${name} needs a value`, 2);
      out[name] = v;
    }
  }
  return out;
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
