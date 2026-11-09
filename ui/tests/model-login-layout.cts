import type * as PageSupport from './support/page.cjs';
// A sign-in begins empty and receives its provider instructions on a later poll. Intrinsic dialog sizing must
// give that content space in WebKit, and keep Cancel reachable when the content must scroll.
const {webkit}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cjs') as typeof PageSupport;

// Browser-side state the fixture sets on window; the app's model-login script reads and writes the same row.
interface LoginRow {
  id: string;
  state: string;
  created_at: string;
  lines: string[];
  url?: string;
  code?: string;
  code_sent?: boolean;
  accepts_code?: boolean;
}
interface LoginWrite {
  url: string;
  body: {code?: string};
}
declare const window: Window & typeof globalThis & {
  S: {config: {hosted: boolean}; me: {id: string}; v2: {needs: unknown[]}; route: string};
  writes: LoginWrite[];
  row: LoginRow;
  // Shared with other specs' Window augmentations, so the signatures must match theirs exactly.
  post: (url: string, body?: unknown) => Promise<unknown>;
  get: (url: string) => Promise<unknown>;
};
declare let row: LoginRow;
declare const writes: LoginWrite[];
declare const TicoModelLogin: {
  open(options: {runnerId: string; runtime: string; machine: string; switchAccount: boolean}): void;
};

const css = [...html.matchAll(/<link\b[^>]*href="\/tico\/ui\/([^"?]+\.css)(?:\?[^"]*)?"/g)]
  .map(([, name]) => fs.readFileSync(uiFile(name!), 'utf8')).join('\n');

async function check(engine: import('playwright').BrowserType) {
  const browser = await engine.launch({headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 640}]) {
      const page = await browser.newPage({viewport});
      const errors: string[] = [];
      page.on('pageerror', (error: Error) => errors.push(error.message));
      await page.setContent('<!doctype html><html><head></head><body></body></html>');
      await page.addStyleTag({content: css});
      await page.evaluate(() => {
        // A minimal app S; the sign-in dialog reads only config.
        window.S = {config: {hosted: false}, me: {id: 'fixture'}, v2: {needs: []}, route: '/'};
        window.writes = [];
        window.row = {id: 'fixture', state: 'starting', created_at: new Date().toISOString(), lines: []};
        window.post = async (url: string, body?: unknown) => {
          writes.push({url, body: body as LoginWrite['body']});
          if (url.endsWith('/cancel')) row.state = 'canceled';
          if (url.endsWith('/code')) row.code_sent = true;
          return {...row};
        };
        window.get = async () => ({...row});
      });
      // model-login reads two shared helpers the app bundle defines first (eventElement, errorMessage).
      for (const name of ['app/dom.ts', 'app/errors.ts', 'model-login.ts']) await page.addScriptTag({path: uiFile(name)});
      for (const runtime of ['codex', 'claude']) {
        await page.setViewportSize(viewport);
        await page.evaluate((runtime: string) => {
          row = {id: 'fixture', state: 'starting', created_at: new Date().toISOString(), lines: []};
          TicoModelLogin.open({runnerId: 'box', runtime, machine: 'Acme computer', switchAccount: true});
        }, runtime);
        const dialog = page.locator('dialog.model-login[open]');
        await dialog.locator('[data-status][data-state=starting]').waitFor();
        await page.evaluate((runtime: string) => Object.assign(row, {state: 'waiting', url: 'https://provider.example/sign-in',
          ...(runtime === 'codex' ? {code: 'TEST-12345'} : {accepts_code: true})}), runtime);
        await dialog.locator('[data-status][data-state=waiting]').waitFor();
        const instructions = dialog.locator('.model-login-steps');
        const body = dialog.locator('.model-login-body');
        const bounds = (await body.boundingBox())!, content = (await instructions.boundingBox())!;
        assert.ok(content.height > 0 && bounds.height >= content.height, `${engine.name()} ${viewport.width} ${runtime}: instructions have space`);
        const link = dialog.locator('[data-link]');
        assert.equal(await link.evaluate((el: Element) => {
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
          const body = el.querySelector('.model-login-body')!, button = el.querySelector('[data-cancel]')!;
          const rect = el.getBoundingClientRect(), action = button.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, viewport: innerHeight, bodyHeight: body.clientHeight,
            bodyScroll: body.scrollHeight, actionTop: action.top, actionBottom: action.bottom};
        });
        assert.ok(geometry.top >= 0 && geometry.bottom <= geometry.viewport + 1, 'dialog stays within the viewport');
        assert.ok(geometry.bodyHeight > 16, 'scrolling content retains usable space');
        assert.ok(geometry.bodyScroll > geometry.bodyHeight, 'long instructions scroll inside the short dialog');
        assert.ok(geometry.actionTop >= 0 && geometry.actionBottom <= geometry.viewport, 'Cancel stays reachable');
        await dialog.getByRole('button', {name: 'Cancel sign-in'}).click();
        await dialog.locator('[data-status][data-state=canceled]').waitFor();
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
  // WebKit only: the sizing bug was WebKit's (ee04afbc6), and the Chromium dialog flow, code entry and cancel are driven
  // by model-login (loginHosted), so a second engine here repeated the same layout in the engine that was never wrong.
  await check(webkit);
  console.log('model sign-in layout: WebKit, dynamic instructions, code entry, bounded content and cancel');
})().catch(error => { console.error(error); process.exit(1); });
