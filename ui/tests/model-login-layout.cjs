// A sign-in begins empty and receives its provider instructions on a later poll. Intrinsic dialog sizing must
// give that content space in WebKit as well as Chromium, and keep Cancel reachable when the content must scroll.
const {chromium, webkit} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const css = [...html.matchAll(/<link\b[^>]*href="\/tico\/ui\/([^"?]+\.css)(?:\?[^"]*)?"/g)]
  .map(([, name]) => fs.readFileSync(uiFile(name), 'utf8')).join('\n');

async function check(engine) {
  const browser = await engine.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 640}]) {
      const page = await browser.newPage({viewport});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<!doctype html><html><head></head><body></body></html>');
      await page.addStyleTag({content: css});
      await page.evaluate(() => {
        window.S = {config: {hosted: false}};
        window.writes = [];
        window.row = {id: 'fixture', state: 'starting', created_at: new Date().toISOString(), lines: []};
        window.post = async (url, body) => {
          writes.push({url, body});
          if (url.endsWith('/cancel')) row.state = 'cancelled';
          if (url.endsWith('/code')) row.code_sent = true;
          return {...row};
        };
        window.get = async () => ({...row});
      });
      await page.addScriptTag({path: path.join(__dirname, '../model-login.js')});
      for (const runtime of ['codex', 'claude']) {
        await page.setViewportSize(viewport);
        await page.evaluate(runtime => {
          row = {id: 'fixture', state: 'starting', created_at: new Date().toISOString(), lines: []};
          TicoModelLogin.open({runnerId: 'box', runtime, machine: 'Acme computer', switchAccount: true});
        }, runtime);
        const dialog = page.locator('dialog.model-login[open]');
        await dialog.locator('[data-status][data-state=starting]').waitFor();
        await page.evaluate(runtime => Object.assign(row, {state: 'waiting', url: 'https://provider.example/sign-in',
          ...(runtime === 'codex' ? {code: 'TEST-12345'} : {accepts_code: true})}), runtime);
        await dialog.locator('[data-status][data-state=waiting]').waitFor();
        const instructions = dialog.locator('.model-login-steps');
        const body = dialog.locator('.model-login-body');
        const bounds = await body.boundingBox(), content = await instructions.boundingBox();
        assert.ok(content.height > 0 && bounds.height >= content.height, `${engine.name()} ${viewport.width} ${runtime}: instructions have space`);
        const link = dialog.locator('[data-link]');
        assert.equal(await link.evaluate(el => {
          const rect = el.getBoundingClientRect();
          return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) === el;
        }), true, 'provider link is painted and clickable, not clipped by the body');
        if (runtime === 'claude') {
          await dialog.locator('[name=code]').fill('fixture-code');
          await dialog.getByRole('button', {name: 'Send code'}).click();
          await dialog.getByText('Code sent.').waitFor();
          assert.ok(await page.evaluate(() => writes.some(w => w.url.endsWith('/code') && w.body.code === 'fixture-code')));
        }
        // A long CLI response must scroll only the body, without pushing Cancel or the dialog off screen.
        await page.evaluate(() => Object.assign(row, {url: '', code: '', accepts_code: false,
          lines: Array.from({length: 80}, (_, i) => `Provider instruction ${i}`)}));
        await dialog.locator('[data-lines]').waitFor();
        await page.setViewportSize({...viewport, height: 360});
        const geometry = await dialog.evaluate(el => {
          const body = el.querySelector('.model-login-body'), button = el.querySelector('[data-cancel]');
          const rect = el.getBoundingClientRect(), action = button.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, viewport: innerHeight, bodyHeight: body.clientHeight,
            bodyScroll: body.scrollHeight, actionTop: action.top, actionBottom: action.bottom};
        });
        assert.ok(geometry.top >= 0 && geometry.bottom <= geometry.viewport + 1, 'dialog stays within the viewport');
        assert.ok(geometry.bodyHeight > 16, 'scrolling content retains usable space');
        assert.ok(geometry.bodyScroll > geometry.bodyHeight, 'long instructions scroll inside the short dialog');
        assert.ok(geometry.actionTop >= 0 && geometry.actionBottom <= geometry.viewport, 'Cancel stays reachable');
        await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
        await dialog.locator('[data-status][data-state=cancelled]').waitFor();
        assert.match(await dialog.locator('[data-status]').innerText(), /account signed in before still works/);
        await dialog.getByRole('button', {name: 'Close', exact: true}).last().click();
        await dialog.waitFor({state: 'detached'});
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
}
(async () => {
  for (const engine of [webkit, chromium]) await check(engine);
  console.log('model sign-in layout: WebKit and Chromium, dynamic instructions, code entry, bounded content and cancel');
})().catch(error => { console.error(error); process.exit(1); });
