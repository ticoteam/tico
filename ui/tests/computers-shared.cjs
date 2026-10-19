// Shared connections on Settings > Computers, end to end against a real server (ui/tests/support/computers_server.py)
// with two fake computers that check keys as a runner does. Spec tests (Head of Product's numbering):
// 16 Add connection > Claude Code > API key > paste > This computer: a Claude Code chip, its line says "API key", the
//    key reaches the computer and is never shown again in full;
// 19 a key shared with two computers: a chip on both, its line says Shared and lists both; one that has not used it
//    yet shows it too, with no reading it has not taken;
// 20 editing it from one computer warns that it changes every listed computer, and both take the new key;
// 36 Clear key from one asks "this computer only" or "all listed computers", naming both; this computer only leaves
//    the other working.
// 21 (the "Every computer" key in Credentials on every computer) is backend/tests/test_shared_connections.py.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');

const ROOT = path.join(__dirname, '../..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
const OWN = 'sk-ant-api03-own-test-key-0123456789';
const SHARED = 'sk-ant-api03-shared-test-key-0123456789';
const ROTATED = 'sk-ant-api03-rotated-test-key-987654321';

function start() {
  const child = spawn(PYTHON, ['ui/tests/support/computers_server.py'], {cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe']});
  let err = '';
  child.stderr.on('data', d => { err += d; });
  return new Promise((resolve, reject) => {
    let out = '';
    child.stdout.on('data', d => {
      out += d;
      const line = out.split('\n').find(l => l.startsWith('{'));
      if (line) resolve({child, ...JSON.parse(line)});
    });
    child.on('exit', code => reject(new Error(`computers_server.py exited ${code}: ${err.slice(-2000)}`)));
    setTimeout(() => reject(new Error('computers_server.py did not start: ' + err.slice(-2000))), 60000);
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
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const team = server.computers['Team box'], lab = server.computers['Lab box'], spare = server.computers['Spare box'];
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block',
      extraHTTPHeaders: {Authorization: 'Bearer ' + server.token}});
    const errors = [], bodies = [];
    page.on('pageerror', e => errors.push(e.message));
    // Every reply the page gets, to show no key value ever comes back to it.
    page.on('response', async response => { try { bodies.push(await response.text()); } catch { /* redirects */ } });
    const received = () => fs.readFileSync(server.received, 'utf8').split('\n').filter(Boolean);
    const shown = async () => (await page.content()) + (await page.evaluate(() => [...document.querySelectorAll('input')].map(i => i.value).join('\n')));
    const neverShown = async secret => {
      assert.ok(!(await shown()).includes(secret), 'the page never shows the key again');
      assert.ok(!bodies.some(b => b.includes(secret)), 'no reply sends the key back');
    };

    await page.goto(server.url + '/#/settings');
    // A new install offers its tour first; skipped, as a person would.
    await page.locator('[data-tour-skip]').click({timeout: 5000}).catch(() => {});
    await page.locator('[data-settings-tab=devices]').click();
    const row = id => page.locator(`#set-machines tr[data-computer-row="${id}"]`);
    const detail = page.locator('#set-machines dialog.computer-detail[open]');
    const openDetail = async id => {
      if (await detail.count()) { await detail.locator('[data-computer-close], .tmodal-x').first().click(); }
      await row(id).locator('.machine-name').click();
      await detail.waitFor();
    };
    const line = detail.locator('[data-connection="own:claude"]');
    // The pop-up paints what the page last read; Settings reads the computers again every few seconds, so a line
    // another computer's change touches is checked by reopening until it shows (or 20 s pass).
    const lineShows = async (id, selector, text, exact = false) => {
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
    const keyDialog = page.locator('dialog.model-key[open]');
    const addKey = async id => {
      await openDetail(id);
      await detail.locator(`[data-add-connection="${id}"]`).click();
      const add = page.locator('dialog.computer-add-dialog[open]');
      await add.locator('[data-add-tool=claude]').click();
      await add.locator('[data-add-kind=api_key]').click();
      await add.locator('[data-model-key-add]').click();
      await keyDialog.locator('[name=secret]').waitFor();
      await keyDialog.locator('[name=env]').selectOption('ANTHROPIC_API_KEY');   // Claude Code also takes an OAuth token
    };

    // 16: Claude Code > API key > paste > This computer.
    await row(team).waitFor();
    assert.equal(await row(team).locator('[data-chip="own:claude"]').count(), 0, 'no Claude Code chip before a connection');
    await addKey(team);
    assert.equal(await keyDialog.locator('[name=key-share][value=this]').isChecked(), true, 'This computer is the default');
    await keyDialog.locator('[name=secret]').fill(OWN);
    await keyDialog.locator('[data-key-go]').click();
    await keyDialog.locator('[data-status][data-state=done]').waitFor({timeout: 20000});
    assert.match(await keyDialog.locator('[data-status]').innerText(), /Added\. Claude Code on Team box now uses sk-…789/);
    assert.equal(await keyDialog.locator('[name=secret]').inputValue(), '', 'the field is emptied once sent');
    await keyDialog.locator('[data-close]').last().click();
    await row(team).locator('[data-chip="own:claude"]').waitFor({timeout: 20000});
    assert.deepEqual(received(), [OWN], 'the key reached Team box');
    await openDetail(team);
    await line.waitFor();
    assert.match(await line.locator('.computer-conn-signin').innerText(), /API key/);
    assert.match(await line.innerText(), /this computer's key sk-…789/);
    await neverShown(OWN);

    // 19: Lab box adds a key shared with Team box: one chip on each, both lines say Shared and list both.
    await addKey(lab);
    await keyDialog.locator('[name=key-share][value=shared]').check();
    assert.equal(await keyDialog.locator('[data-key-go]').isEnabled(), false, 'pick at least one computer');
    await keyDialog.locator(`[name=key-share-computer][value="${team}"]`).check();
    await keyDialog.locator(`[name=key-share-computer][value="${spare}"]`).check();
    await keyDialog.locator('[name=secret]').fill(SHARED);
    await keyDialog.locator('[data-key-go]').click();
    await keyDialog.locator('[data-status][data-state=done]').waitFor({timeout: 20000});
    assert.match(await keyDialog.locator('[data-status]').innerText(), /Claude Code on Lab box, Spare box, Team box uses the shared key sk-…789/);
    await keyDialog.locator('[data-close]').last().click();
    // Team box keeps its own key, which comes before a shared one: clear it there for this computer, then it runs
    // on the shared key too.
    await openDetail(team);
    await line.locator('[data-model-key-clear]').click();
    await keyDialog.locator('[data-key-go]:not([disabled])').click();
    await keyDialog.locator('[data-status][data-state=signed_out]').waitFor({timeout: 20000}).catch(() => {});
    await keyDialog.locator('[data-close]').last().click();
    await openDetail(team);
    await line.locator('[data-model-shared-key]').click();
    for (const id of [team, lab]) {
      await lineShows(id, '[data-shared-with]', 'Shared with Lab box, Spare box, Team box');
      assert.equal(await line.locator('[data-shared-with]').innerText(), 'Shared with Lab box, Spare box, Team box');
      assert.equal(await line.locator('[data-shared]').innerText(), 'Shared');
      assert.match(await line.locator('.computer-conn-head').innerText(), /API key · ANTHROPIC_API_KEY shared from Lab box/);
      assert.equal(await row(id).locator('[data-chip="own:claude"]').count(), 1);
      assert.match(await row(id).locator('[data-chip="own:claude"]').getAttribute('title'), /Shared with Lab box, Spare box, Team box/);
    }
    assert.ok(received().includes(SHARED), 'the shared key reached the computers');
    // Spare box has not fetched it: Shared and listed, but no reading, no red dot, and the line says it is unchecked.
    await lineShows(spare, '[data-shared-with]', 'Shared with Lab box, Spare box, Team box');
    assert.match(await line.locator('[data-shared-waiting]').innerText(), /Not checked on Spare box yet/);
    const spareChip = row(spare).locator('[data-chip="own:claude"]');
    assert.equal(await spareChip.locator('meter').count(), 0, 'no usage bar it has not read');
    assert.equal(await spareChip.evaluate(el => el.classList.contains('attention')), false);
    await neverShown(SHARED);

    // 20: Replace key from Team box, for all listed computers: warned first; both computers take the new key.
    await openDetail(team);
    await line.locator('[data-model-key-replace]').click();
    await keyDialog.locator('[data-key-scope]').waitFor();
    assert.match(await keyDialog.locator('[data-key-scope] legend').innerText(), /shared with 3 computers/);
    assert.equal(await keyDialog.locator('[data-key-scope-warn]').isHidden(), true);
    await keyDialog.locator('[name=key-scope][value=all]').check();
    assert.equal(await keyDialog.locator('[data-key-scope-warn]').innerText(), 'This changes every listed computer: Lab box, Spare box, Team box.');
    await keyDialog.locator('[name=secret]').fill(ROTATED);
    await keyDialog.locator('[data-key-go]').click();
    await keyDialog.locator('[data-status]', {hasText: 'Replaced.'}).waitFor({timeout: 20000});
    await keyDialog.locator('[data-close]').last().click();
    for (const id of [team, lab]) await lineShows(id, '.machine-key', 'sk-…321');
    await neverShown(ROTATED);

    // 36: Clear key from Team box names both; this computer only leaves Lab box on the shared key.
    await openDetail(team);
    await line.locator('[data-model-key-clear]').click();
    await keyDialog.locator('[data-key-scope]').waitFor();
    assert.deepEqual(await keyDialog.locator('[data-scope-computer]').allInnerTexts(), ['Lab box', 'Spare box', 'Team box']);
    assert.match(await keyDialog.locator('[data-key-scope]').innerText(), /This computer only \(Team box\)/);
    assert.match(await keyDialog.locator('[data-key-scope]').innerText(), /All listed computers/);
    await keyDialog.locator('[data-key-go]:not([disabled])').click();
    await keyDialog.locator('[data-status]', {hasText: 'Cleared.'}).waitFor({timeout: 20000});
    await keyDialog.locator('[data-close]').last().click();
    await lineShows(lab, '[data-shared-with]', 'Shared with Lab box, Spare box', true);
    assert.match(await line.locator('.computer-conn-signin').innerText(), /shared key sk-…321/, 'Lab box keeps working on it');
    await openDetail(team);
    assert.equal(await line.locator('[data-shared]').count(), 0, 'Team box no longer runs on it');

    assert.deepEqual(errors, []);
    console.log('computers-shared: ok');
  } finally {
    await browser.close();
    server.child.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exit(1); });
