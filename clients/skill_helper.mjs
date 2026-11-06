#!/usr/bin/env node
// Tico skills helper: connects your own Claude Code to your company's skills in Tico (docs/skills-claude-code.md).
//
//   node skills-helper.mjs setup [--server URL]   connect this computer (opens the browser to approve)
//   node skills-helper.mjs sync [--quiet]         install, update and remove skills to match Tico
//   node skills-helper.mjs status                 what is connected and installed
//   node skills-helper.mjs remove                 disconnect: remove the skills, the settings and the device
//   node skills-helper.mjs auth-header            for Claude Code's headersHelper (prints one JSON object)
//   node skills-helper.mjs report --trigger model|person   hook handler: reads the hook's JSON on stdin
//
// Node 18 or later, no dependencies. Config lives in ~/.tico/skills.json (mode 0600). Usage reports carry the skill
// name, a sha256 of the session id, the tool use or prompt id and the time: never the prompt, arguments or files.
import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_SERVER = '__TICO_SERVER__';
const MARKETPLACE = 'tico';
const HOOKS_PLUGIN = 'tico-skills';
const MIN_CLAUDE = [2, 1, 238];
const NAME = /^[a-z0-9][a-z0-9._-]{0,79}$/;
const DIR = process.env.TICO_SKILLS_DIR || path.join(os.homedir(), '.tico');
const CONFIG = path.join(DIR, 'skills.json');
const INSTALLED = path.join(DIR, 'skills-helper.mjs');
const LOCK = path.join(DIR, 'skills-sync.lock');
const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const SETTINGS = path.join(CLAUDE_DIR, 'settings.json');
const CLAUDE = process.env.TICO_CLAUDE_BIN || 'claude';
const SELF = fileURLToPath(import.meta.url);

let quiet = false;
const say = (...a) => { if (!quiet) console.log(...a); };
const warn = (...a) => { if (!quiet) console.error(...a); };

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const [k, v] = a.slice(2).split('=', 2);
      if (v !== undefined) out[k] = v;
      else if (argv[i + 1] !== undefined && !argv[i + 1].startsWith('--')) out[k] = argv[++i];
      else out[k] = true;
    } else out._.push(a);
  }
  return out;
}

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) {
    if (e.code === 'ENOENT') return fallback;
    throw new Error(`${file} is not valid JSON; fix or move it, then run again`);
  }
}

function writeAtomic(file, text, mode) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  let keep = mode;
  try { keep = keep ?? (fs.statSync(file).mode & 0o777); } catch { /* new file */ }
  const tmp = `${file}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  fs.writeFileSync(tmp, text, { mode: keep ?? 0o644 });
  if (keep !== undefined) fs.chmodSync(tmp, keep);
  fs.renameSync(tmp, file);
}

const loadConfig = () => { try { return readJson(CONFIG, null); } catch { return null; } };
const saveConfig = cfg => writeAtomic(CONFIG, JSON.stringify(cfg, null, 2) + '\n', 0o600);

async function api(cfg, method, route, body, { timeout = 15000, token = cfg?.token } = {}) {
  const headers = { 'Accept': 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(cfg.server + '/api/v2/' + route, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeout),
  });
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  return { status: res.status, data, headers: res.headers };
}

function claude(argv, { capture = true } = {}) {
  const r = spawnSync(CLAUDE, argv, { encoding: 'utf8', stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit', timeout: 180000 });
  return { ok: r.status === 0, out: r.stdout || '', err: r.stderr || '', missing: r.error?.code === 'ENOENT' };
}

function claudeVersion() {
  const r = claude(['--version']);
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(r.out);
  return m ? m.slice(1, 4).map(Number) : null;
}

const atLeast = (v, min) => { for (let i = 0; i < 3; i++) { if (v[i] !== min[i]) return v[i] > min[i]; } return true; };

function openBrowser(url) {
  const cmd = process.platform === 'darwin' ? ['open', [url]] : process.platform === 'win32' ? ['cmd', ['/c', 'start', '', url]] : ['xdg-open', [url]];
  try { spawn(cmd[0], cmd[1], { stdio: 'ignore', detached: true }).on('error', () => {}).unref(); } catch { /* print only */ }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
const sha256 = text => crypto.createHash('sha256').update(String(text)).digest('hex');
// A shell word for headersHelper, which runs through sh: the helper's path may hold spaces.
const shellWord = s => `'${String(s).replace(/'/g, `'\\''`)}'`;

function headersHelper() {
  return `${shellWord(process.execPath)} ${shellWord(INSTALLED)} auth-header`;
}

function updateSettings(change) {
  const settings = readJson(SETTINGS, {});
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error(`${SETTINGS} is not a JSON object`);
  change(settings);
  writeAtomic(SETTINGS, JSON.stringify(settings, null, 2) + '\n');
}

function installSelf() {
  fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
  if (path.resolve(SELF) === path.resolve(INSTALLED)) return;
  const text = fs.readFileSync(SELF, 'utf8');
  let current = null;
  try { current = fs.readFileSync(INSTALLED, 'utf8'); } catch { /* first copy */ }
  if (current !== text) writeAtomic(INSTALLED, text, 0o700);
}

async function setup(opts) {
  const server = String(opts.server || DEFAULT_SERVER).replace(/\/+$/, '');
  if (!/^https?:\/\//.test(server)) throw new Error('Pass --server https://your-tico-address');
  if (!server.startsWith('https://')) warn('Warning: Claude Code sends the sign-in header only to an https:// marketplace.');
  const version = claudeVersion();
  if (!version) throw new Error('Claude Code not found: install it, then run setup again');
  if (!atLeast(version, MIN_CLAUDE)) throw new Error(`Claude Code ${version.join('.')} is too old: update to ${MIN_CLAUDE.join('.')} or later (claude update)`);
  const cfg = { server };
  const asked = await api(cfg, 'POST', 'skills/devices/codes', { label: os.hostname().slice(0, 120) });
  if (asked.status !== 201 && asked.status !== 200) throw new Error(asked.data?.error?.detail || `Could not reach ${server} (${asked.status})`);
  const { device_code, user_code, verification_url, expires_in, interval } = asked.data;
  say(`Open ${verification_url}\nand confirm the code ${user_code}`);
  openBrowser(verification_url);
  const until = Date.now() + expires_in * 1000;
  let wait = (interval || 5) * 1000, got = null;
  while (Date.now() < until) {
    await sleep(wait);
    let r;
    try { r = await api(cfg, 'POST', 'skills/devices/token', { device_code }, { token: null }); } catch { continue; }
    if (r.status === 200) { got = r.data; break; }
    if (r.status === 428) continue;
    if (r.status === 429) { wait += 5000; continue; }
    if (r.status >= 500) continue;
    throw new Error(r.data?.error?.detail || `Not connected (${r.status})`);
  }
  if (!got) throw new Error('The code expired. Run setup again');
  saveConfig({ server, token: got.token, device_id: got.device_id, reporting: !!got.reporting_enabled, skills: [] });
  installSelf();
  updateSettings(s => {
    s.extraKnownMarketplaces = s.extraKnownMarketplaces || {};
    s.extraKnownMarketplaces[MARKETPLACE] = {
      source: { source: 'url', url: `${server}/api/v2/skills/marketplace.json`, headersHelper: headersHelper() },
      autoUpdate: true,
    };
    // Enabled in user settings: a session start fetches it even if the install below could not run.
    s.enabledPlugins = s.enabledPlugins || {};
    s.enabledPlugins[`${HOOKS_PLUGIN}@${MARKETPLACE}`] = true;
  });
  claude(['plugin', 'marketplace', 'update', MARKETPLACE]);
  const hooks = claude(['plugin', 'install', `${HOOKS_PLUGIN}@${MARKETPLACE}`, '--scope', 'user']);
  if (!hooks.ok) warn(`Could not install ${HOOKS_PLUGIN} now; Claude Code fetches it at the next start.`);
  const done = await sync();
  say(`Connected. ${done.wanted.length} skill${done.wanted.length === 1 ? '' : 's'} installed. Reporting ${got.reporting_enabled ? 'on' : 'off'}.`);
}

function installedPlugins() {
  const r = claude(['plugin', 'list', '--json']);
  if (!r.ok) return null;
  try {
    const rows = JSON.parse(r.out);
    return new Map(rows.filter(p => typeof p.id === 'string' && p.id.endsWith('@' + MARKETPLACE) && p.scope === 'user')
      .map(p => [p.id.slice(0, -(MARKETPLACE.length + 1)), p]));
  } catch { return null; }
}

function lock() {
  try {
    fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
    try { if (Date.now() - fs.statSync(LOCK).mtimeMs > 10 * 60 * 1000) fs.rmSync(LOCK, { force: true }); } catch { /* none */ }
    fs.writeFileSync(LOCK, String(process.pid), { flag: 'wx' });
    return () => fs.rmSync(LOCK, { force: true });
  } catch { return null; }
}

async function sync() {
  const cfg = loadConfig();
  if (!cfg?.token) throw new Error('Not connected: run setup first');
  const release = lock();
  if (!release) return { wanted: cfg.skills || [], skipped: true };
  try {
    installSelf();
    const r = await api(cfg, 'GET', 'skills/marketplace.json');
    if (r.status === 401) throw new Error('This device was revoked: run setup again');
    if (r.status !== 200 || !Array.isArray(r.data?.plugins)) throw new Error(`Could not read the marketplace (${r.status})`);
    const plugins = r.data.plugins.filter(p => NAME.test(p.name || ''));
    const wanted = plugins.filter(p => p.name !== HOOKS_PLUGIN).map(p => p.name);
    // Only names from this list are ever reported (report), and only while reporting is on.
    saveConfig({ ...cfg, skills: wanted, reporting: r.headers.get('x-tico-reporting') === 'on', synced_at: new Date().toISOString() });
    let have = installedPlugins();
    if (have) {
      const pin = p => String(p.source?.sha256 || '').slice(0, 12);
      const todo = plugins.filter(p => !have.has(p.name) || have.get(p.name).version !== pin(p));
      const extra = [...have.keys()].filter(n => !plugins.some(p => p.name === n));
      if (todo.length || extra.length) claude(['plugin', 'marketplace', 'update', MARKETPLACE]);
      for (const p of todo) {
        const verb = have.has(p.name) ? 'update' : 'install';
        const done = claude(['plugin', verb, `${p.name}@${MARKETPLACE}`, '--scope', 'user']);
        if (!done.ok) warn(`Could not ${verb} ${p.name}: ${done.err.trim().split('\n').pop() || 'failed'}`);
      }
      for (const name of extra) claude(['plugin', 'uninstall', `${name}@${MARKETPLACE}`, '--scope', 'user']);
      have = installedPlugins() || have;
      const staged = plugins.filter(p => p.name !== HOOKS_PLUGIN && have.get(p.name)?.version === pin(p))
        .map(p => ({ name: p.name, sha256: p.metadata?.tico?.bundle_sha256 })).filter(s => s.sha256);
      const version = claudeVersion();
      await api(cfg, 'POST', 'skills/devices/receipt', { staged, claude_version: version ? version.join('.') : '' }).catch(() => {});
    }
    return { wanted };
  } finally { release(); }
}

function readStdin(ms) {
  return new Promise(resolve => {
    let text = '';
    const done = () => resolve(text);
    const timer = setTimeout(done, ms);
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', d => { text += d; if (text.length > 4_000_000) { clearTimeout(timer); done(); } });
    process.stdin.on('end', () => { clearTimeout(timer); done(); });
    process.stdin.on('error', () => { clearTimeout(timer); done(); });
  });
}

// "deploy:deploy" (a plugin's skill, as Claude Code names it) or "deploy", when deploy is one of this person's skills.
function skillName(raw, known) {
  const s = String(raw || '').trim().replace(/^\//, '').split(/\s/)[0];
  const [plugin, skill] = s.includes(':') ? s.split(':', 2) : [s, s];
  return plugin === skill && known.includes(skill) ? skill : null;
}

async function report(trigger) {
  const cfg = loadConfig();
  const input = await readStdin(1500);
  if (!cfg?.token || !cfg.reporting || !Array.isArray(cfg.skills)) return;
  let hook;
  try { hook = JSON.parse(input); } catch { return; }
  let raw, use;
  if (trigger === 'model') {
    if (hook.tool_name !== 'Skill') return;
    const ti = hook.tool_input || {};
    raw = ti.skill ?? ti.name ?? ti.command;
    use = hook.tool_use_id;
  } else if (trigger === 'person') {
    if (hook.expansion_type && hook.expansion_type !== 'slash_command') return;
    raw = hook.command_name;
    use = hook.prompt_id || crypto.randomUUID();
  } else return;
  const name = skillName(raw, cfg.skills);
  if (!name || !hook.session_id || !use) return;
  const event = { name, trigger, session_key: sha256(hook.session_id), tool_use_id: String(use).slice(0, 128), used_at: new Date().toISOString() };
  await api(cfg, 'POST', 'skills/usage', { events: [event] }, { timeout: 2000 }).catch(() => {});
}

async function status() {
  const cfg = loadConfig();
  if (!cfg?.token) { say('Not connected. Run: node skills-helper.mjs setup'); return; }
  const settings = readJson(SETTINGS, {});
  const declared = !!settings?.extraKnownMarketplaces?.[MARKETPLACE];
  const v = claudeVersion();
  say(`Server: ${cfg.server}\nDevice: ${cfg.device_id}\nReporting: ${cfg.reporting ? 'on' : 'off'}\nClaude Code: ${v ? v.join('.') : 'not found'}`
    + `\nMarketplace in settings: ${declared ? 'yes' : 'no'}\nSkills: ${(cfg.skills || []).join(', ') || 'none'}`
    + (cfg.synced_at ? `\nLast sync: ${cfg.synced_at}` : ''));
}

async function remove() {
  const cfg = loadConfig();
  // Removing the marketplace uninstalls every plugin installed from it.
  claude(['plugin', 'marketplace', 'remove', MARKETPLACE]);
  try {
    updateSettings(s => {
      if (s.extraKnownMarketplaces) delete s.extraKnownMarketplaces[MARKETPLACE];
      for (const id of Object.keys(s.enabledPlugins || {})) if (id.endsWith('@' + MARKETPLACE)) delete s.enabledPlugins[id];
    });
  } catch (e) { warn(String(e.message || e)); }
  if (cfg?.token && cfg.device_id) {
    const r = await api(cfg, 'DELETE', `skills/devices/${encodeURIComponent(cfg.device_id)}`).catch(() => null);
    if (!r || ![200, 401, 409].includes(r.status)) warn('Could not revoke this device in Tico; revoke it there.');
  }
  for (const f of [CONFIG, INSTALLED, LOCK]) fs.rmSync(f, { force: true });
  say('Disconnected.');
}

async function main() {
  const opts = args(process.argv.slice(2));
  const command = opts._[0];
  quiet = !!opts.quiet;
  if (command === 'report') {
    // A hook: never fails the session, never prints.
    quiet = true;
    try { await report(String(opts.trigger || '')); } catch { /* ignored */ }
    process.exit(0);
  }
  if (command === 'auth-header') {
    const cfg = loadConfig();
    process.stdout.write(JSON.stringify(cfg?.token ? { Authorization: `Bearer ${cfg.token}` } : {}) + '\n');
    return;
  }
  const commands = { setup: () => setup(opts), sync, status, remove };
  if (!commands[command]) {
    console.log('Usage: node skills-helper.mjs setup [--server URL] | sync [--quiet] | status | remove');
    process.exit(command ? 1 : 0);
  }
  try { await commands[command](); } catch (e) {
    warn(String(e.message || e));
    process.exit(quiet ? 0 : 1);
  }
}

await main();
