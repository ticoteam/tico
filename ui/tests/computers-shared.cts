// Shared connections on Settings > Computers, end to end against a real server (ui/tests/support/computers_server.py)
// with fake computers that fetch their keys as a runner does. Keys are Settings > Models subscriptions now (D11-5); the
// computers here hold the ones an upgrade moved there from Settings > Computers. Spec tests (Head of Product's numbering):
// 16 a computer's own key: a Claude Code chip, its line says "API key" and "this computer's key" by preview only, the key
//    reaches the computer and is never shown in full;
// 19 a key given to two computers: a chip on both, its line says Shared and lists both; one that has not used it yet,
//    and has reported nothing for that tool, shows it too, with no reading it has not taken;
// 20 replacing it in Settings > Models reaches every computer given it;
// 36 a computer signed out from here shows Use the shared key, which brings back the key it is given.
// Add, Replace and Clear key are gone from Settings > Computers: Add connection > API key points to Settings > Models.
import type {ChildProcess} from 'node:child_process';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {spawn} = require('node:child_process') as typeof import('node:child_process');

// What computers_server.py prints as its one JSON line, with the child process it runs in.
interface Server {
  child: ChildProcess;
  url: string;
  token: string;
  received: string;
  computers: Record<string, string>;
  subscriptions: Record<string, string>;
}

// Page-side globals the app defines, declared for the callbacks that read them.
declare const SETTINGS_PRESENCE: {interval: number};
declare function settingsMachinesWatch(): void;

const ROOT = path.join(__dirname, '../..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
const OWN = 'sk-ant-api03-own-test-key-0123456789';
const SHARED = 'sk-ant-api03-shared-test-key-0123456789';
const ROTATED = 'sk-ant-api03-rotated-test-key-987654321';

function start(): Promise<Server> {
  const child = spawn(PYTHON, ['ui/tests/support/computers_server.py'], {cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe']});
  let err = '';
  child.stderr.on('data', d => { err += d; });
  return new Promise<Server>((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => reject(new Error('computers_server.py did not start: ' + err.slice(-2000))), 60000);
    child.stdout.on('data', d => {
      out += d;
      const line = out.split('\n').find(l => l.startsWith('{'));
      if (line) { clearTimeout(timer); resolve({child, ...JSON.parse(line)}); }
    });
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`computers_server.py exited ${code}: ${err.slice(-2000)}`)); });
  });
}

(async () => {
  const probe = spawn(PYTHON, ['-c', 'import uvicorn,httpx,yaml,cryptography'], {stdio: 'ignore'});
  if (!await new Promise(resolve => probe.on('exit', code => resolve(code === 0)))) {
    if (process.env.CI) throw new Error(PYTHON + ' lacks the backend requirements (pip install -r backend/requirements-dev.txt)');
    console.log('skipped: ' + PYTHON + ' lacks the backend requirements; set TICO_PYTHON to a Python that has them');
    return;
  }
  const server = await start();
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  try {
    const team = server.computers['Team box']!, lab = server.computers['Lab box']!, spare = server.computers['Spare box']!;
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block',
      extraHTTPHeaders: {Authorization: 'Bearer ' + server.token}});
    const errors: string[] = [], bodies: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    // Every reply the page gets, to show no key value ever comes back to it.
    page.on('response', async response => { try { bodies.push(await response.text()); } catch { /* redirects */ } });
    const received = () => fs.readFileSync(server.received, 'utf8').split('\n').filter(Boolean);
    const shown = async () => (await page.content()) + (await page.evaluate(() => [...document.querySelectorAll('input')].map(i => i.value).join('\n')));
    const neverShown = async (secret: string) => {
      assert.ok(!(await shown()).includes(secret), 'the page never shows the key again');
      assert.ok(!bodies.some(b => b.includes(secret)), 'no reply sends the key back');
    };

    await page.goto(server.url + '/#/settings');
    // Stress runs exercise the same watcher more often without changing the production cadence.
    if (process.env.TICO_TEST_PRESENCE_INTERVAL_MS) await page.evaluate((interval: number) => {
      SETTINGS_PRESENCE.interval = interval; settingsMachinesWatch();
    }, Number(process.env.TICO_TEST_PRESENCE_INTERVAL_MS));
    await page.locator('[data-settings-tab=devices]').click();
    const row = (id: string) => page.locator(`#set-machines tr[data-computer-row="${id}"]`);
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const openDetail = async (id: string) => {
      if (await detail.count()) { await detail.locator('[data-computer-close], .tmodal-x').first().click(); }
      await row(id).locator('.machine-name').click();
      await detail.waitFor();
    };
    const line = detail.locator('[data-connection="own:claude"]');
    // The pop-up paints what the page last read; Settings reads the computers again every few seconds, so a line
    // another computer's change touches is checked by reopening until it shows (or 20 s pass).
    const lineShows = async (id: string, selector: string, text: string, exact = false) => {
      const until = Date.now() + 20000;
      for (;;) {
        await openDetail(id);
        const found = line.locator(selector);
        const said = await found.count() ? await found.first().innerText() : null;
        if (said !== null && (exact ? said === text : said.includes(text))) return;
        if (Date.now() > until) assert.fail(`${id}: ${selector} never showed "${text}"`);
        await page.waitForTimeout(500);
      }
    };

    // 16: Team box's own key.
    await row(team).locator('[data-chip="own:claude"]').waitFor({timeout: 20000});
    // Every computer on this Tico takes subscriptions (the runner floor is the gate), so its Claude Code cell is the picker;
    // the key keeps its chip beside it (subscriptions.ts subsPickerHTML).
    assert.equal(await row(team).locator('[data-subs-needs-update]').count(), 0, 'nothing says this computer needs an update');
    assert.equal(await row(team).locator('[data-subs-pick=claude]').count(), 1, 'a computer on this Tico has a picker');
    await lineShows(team, '.machine-key', "this computer's key sk-…789");
    assert.match(await line.locator('.computer-conn-signin').innerText(), /API key/);
    assert.equal(await page.locator('[data-model-key-add], [data-model-key-replace], [data-model-key-clear], [data-model-to-key], [data-model-to-subscription]').count(), 0,
      'keys are changed in Settings > Models');
    assert.ok(received().includes(OWN), 'the key reached Team box');
    await neverShown(OWN);

    // Add connection > Claude Code > API key: the key is added in Settings > Models.
    await detail.locator(`[data-add-connection="${team}"]`).click();
    const add = page.locator('dialog.computer-add-dialog[open]');
    await add.locator('[data-add-tool=claude]').click();
    await add.locator('[data-add-kind=api_key]').click();
    assert.match(await add.locator('[data-add-step=api_key]').innerText(), /Add the key in Settings > Models and give it to Team box there/);
    assert.equal(await add.locator('[data-add-key=claude]').getAttribute('href'), '#/settings/models');
    await add.locator('[data-add-close]').first().click();

    // 19: the team key on Lab box and Spare box: one chip on each, both lines say Shared and list both.
    for (const id of [lab, spare]) {
      await lineShows(id, '[data-shared-with]', 'Shared with Lab box, Spare box', true);
      assert.equal(await line.locator('[data-shared]').innerText(), 'Shared');
      assert.match(await line.locator('.computer-conn-head').innerText(), /API key · Team Claude key/);
      assert.equal(await row(id).locator('[data-chip="own:claude"]').count(), 1);
      assert.match((await row(id).locator('[data-chip="own:claude"]').getAttribute('title'))!, /Shared with Lab box, Spare box/);
    }
    // Spare box has not fetched it and reports no Claude Code at all: Shared and listed, but no reading, no red dot,
    // and the line says it is unchecked.
    assert.match(await line.locator('[data-shared-waiting]').innerText(), /Not checked on Spare box yet/);
    const spareChip = row(spare).locator('[data-chip="own:claude"]');
    assert.equal(await spareChip.locator('meter').count(), 0, 'no usage bar it has not read');
    assert.equal(await spareChip.evaluate((el: Element) => el.classList.contains('attention')), false);
    await lineShows(lab, '.machine-key', 'shared key sk-…789');
    assert.ok(received().includes(SHARED), 'the team key reached Lab box');
    await neverShown(SHARED);

    // 20: replacing it in Settings > Models reaches each computer given it.
    const replaced = await page.request.post(`${server.url}/api/v2/model-subscriptions/${server.subscriptions.team}`,
      {data: {secret: ROTATED, confirm: true}, headers: {'Idempotency-Key': `rotate-${Date.now()}`}});
    assert.equal(replaced.status(), 200, await replaced.text());
    // Settings > Models previews a key as its first 7 and last 4 characters (model_subscriptions.checked).
    await lineShows(lab, '.machine-key', 'sk-ant-…4321');
    const until = Date.now() + 20000;
    while (!received().includes(ROTATED) && Date.now() < until) await page.waitForTimeout(250);
    assert.ok(received().includes(ROTATED), 'Lab box took the new key');
    await neverShown(ROTATED);

    // 36: signed out from here, Lab box is not handed the key until Use the shared key brings it back.
    assert.equal((await page.request.post(`${server.url}/test/signed-out/${lab}`)).status(), 200);
    await lineShows(lab, '[data-model-shared-key]', 'Use the shared key', true);
    await line.locator('[data-model-shared-key]').click();
    await lineShows(lab, '.machine-key', 'shared key sk-ant-…4321');
    assert.equal(await line.locator('[data-model-shared-key]').count(), 0);

    assert.deepEqual(errors, []);
    console.log('computers-shared: ok');
  } finally {
    await browser.close();
    server.child.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exit(1); });
