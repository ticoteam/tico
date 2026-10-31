// A real Tico hub for browser tests (ui/tests/support/real_hub_server.py): the app in-process on a fresh temporary
// database, from the scenario harness's world and its seeds (evals/redesign). startHub() starts one; pageAs() opens a
// page signed in as one of its teammates, on a phone (390x844, touch) or a desktop (1440x900). The page and the API
// see the same data and the same response shapes a person sees.
const {spawn} = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const {t} = require('./load.cjs');

const ROOT = path.join(__dirname, '..', '..', '..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
const PHONE = {
  viewport: {width: 390, height: 844},
  isMobile: true,
  hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) ' +
    'Version/17.5 Mobile/15E148 Safari/604.1',
};
const DESKTOP = {viewport: {width: 1440, height: 900}};

// Starts a hub with these seeds (default ['org']). Resolves to {url, seeds, skipped, tokens, runner, stop}: `tokens` maps
// each teammate id ("ana", "chris", "engineering-lead", ...) to its Bearer token, `runner` is the world's runner token
// (to claim a bot's run as its computer would), and `skipped` names seeds that waited for a route the app does not
// have yet. `stop()` kills the hub process this call started, by its pid.
function startHub({seeds = ['org'], env = {}} = {}) {
  const child = spawn(PYTHON, ['ui/tests/support/real_hub_server.py', '--seed', seeds.join(',')],
    {cwd: ROOT, env: {...process.env, ...env}, stdio: ['ignore', 'pipe', 'pipe']});
  let err = '';
  child.stderr.on('data', d => { err = (err + d).slice(-4000); });
  let stopping = null;
  const stop = () => stopping ??= new Promise(resolve => {
    if (child.exitCode !== null || child.signalCode !== null) return resolve();
    const killer = setTimeout(() => child.kill('SIGKILL'), t(15000));
    child.once('exit', () => { clearTimeout(killer); resolve(); });
    child.kill('SIGTERM');
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { stop(); reject(new Error('real_hub_server.py did not start: ' + err)); }, t(60000));
    child.once('exit', code => {
      clearTimeout(timer);
      reject(new Error(`real_hub_server.py exited ${code} before it was ready: ${err}`));
    });
    readline.createInterface({input: child.stdout}).on('line', line => {
      if (!line.startsWith('{')) return;
      let ready;
      try { ready = JSON.parse(line); } catch { return; }
      clearTimeout(timer);
      resolve({url: ready.url, seeds: ready.seeds, skipped: ready.skipped, tokens: ready.tokens, runner: ready.runner, stop});
    });
  });
}

// A page signed in as `teammate` (a key of hub.tokens), opened at the app's root. The teammate's token goes on every
// request the page makes, as the API takes it. The caller closes it with page.context().close().
async function pageAs(browser, hub, teammate, {phone = false, timezone = ''} = {}) {
  const token = hub.tokens[teammate];
  if (!token) throw new Error(`no teammate "${teammate}" on this hub; it has ${Object.keys(hub.tokens).join(', ')}`);
  const context = await browser.newContext({
    ...(phone ? PHONE : DESKTOP),
    ...(timezone ? {timezoneId: timezone} : {}),   // the viewer's zone, as the browser reports it
    serviceWorkers: 'block',
    extraHTTPHeaders: {Authorization: 'Bearer ' + token},
  });
  const page = await context.newPage();
  await page.goto(hub.url + '/');
  return page;
}

module.exports = {startHub, pageAs, PHONE, DESKTOP};
