// Offline browser regression: Settings > General (backend/company_storage.py) is the owner's alone; the form sends the
// bucket and keys once, shows the server's refusal where it was typed, clears the secret after a save and goes back to
// the server's storage on confirm. Health's "Set up S3" opens it. Every request is intercepted.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  try {
    const visit = async ({role = 'owner', hash = '#/settings/storage', tico = {state: 'off', label: 'Tico file hosting'}} = {}) => {
      const ctx = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
      const page = await ctx.newPage();
      const errors = [], calls = [];
      let storage = {active: null, earlier: [], server: {mode: 'local', location: ''}, copy: {done: 0, total: 0, failed: 0, running: false},
        doc: 'https://example.test/docs/files.md', tico};
      let move = null;
      const estimate = () => move ? {to: 'tico-files-us/c/acme', files: 0, bytes: 0, gb: 0}
        : {to: 'tico-files-us/c/acme', files: 3, bytes: 2500000000, gb: 2.5};
      page.on('pageerror', e => errors.push(e.message));
      page.on('dialog', d => d.accept());
      await page.route('**/*', route => {
        const req = route.request(), p = new URL(req.url()).pathname;
        const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
        if (ui && fs.existsSync(uiFile(ui[1])))
          return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
        if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
        if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
        const config = {version: '0.3.0', update: null, app_name: 'Tico'};
        if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
        if (p === '/api/v2/config') return json(config);
        if (p === '/api/v2/system/storage') {
          calls.push(req.method() + ' ' + (req.postData() || ''));
          if (req.method() === 'PUT') {
            const sent = JSON.parse(req.postData());
            if (sent.access_key_id === 'AKIAEXAMPLEDENY0001')
              return json({error: {code: 'storage_check', detail: "S3 storage can't write: AccessDenied on acme-files/tico (s3:PutObject)"}}, 422);
            storage = {...storage, active: {bucket: sent.bucket, prefix: sent.prefix, region: sent.region, location: sent.bucket + '/' + sent.prefix,
              access_key_id: '…0001', created_at: new Date().toISOString(), created_by: 'human:ana'}};
          }
          if (req.method() === 'DELETE') storage = {...storage, active: null, earlier: [{...storage.active, retired: new Date().toISOString()}]};
          return json(storage);
        }
        if (p === '/api/v2/system/storage/move') {
          calls.push(req.method() + ' move ' + (req.postData() || ''));
          if (req.method() === 'POST') move = {id: 'm1', target: 'tico-files-us/c/acme', state: 'done', estimate_files: 3, estimate_bytes: 2500000000,
            moved_files: 3, moved_bytes: 2500000000, failed: 0, error: '', matches: true};
          return json({estimate: estimate(), move});
        }
        if (p === '/api/v2/system/storage/tico/check') { calls.push('POST check'); return json(tico); }
        if (p === '/api/employees' || p === '/api/issues') return json([]);
        if (p === '/api/humans') return json({people: [], teams: {}});
        if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
        if (p === '/api/v2/status') return json({bots: []});
        if (p === '/api/v2/needs-you') return json({items: []});
        if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
        return json(unrouted(p));
      });
      await page.goto('https://tico-ui.test/' + hash);
      await page.waitForFunction(() => !document.querySelector('#account .account-email')?.textContent.includes('Signing in'));
      return {page, ctx, errors, calls};
    };

    let v = await visit();
    const form = v.page.locator('[data-storage-form]');
    await form.waitFor();
    assert.match(await v.page.locator('#set-storage').innerText(), /New files go to this server's disk/);
    const fill = async key => {
      await form.locator('[name=bucket]').fill('acme-files'); await form.locator('[name=region]').fill('us-west-2');
      await form.locator('[name=prefix]').fill('tico'); await form.locator('[name=access_key_id]').fill(key);
      await form.locator('[name=secret_access_key]').fill('example-secret');
      await form.locator('button[type=submit]').click();
    };
    await fill('AKIAEXAMPLEDENY0001');
    await v.page.waitForFunction(() => /AccessDenied/.test(document.querySelector('[data-storage-status]')?.textContent));
    assert.equal(await form.locator('[name=secret_access_key]').inputValue(), 'example-secret', 'a refusal keeps what was typed');
    await fill('AKIAEXAMPLEGOOD0001');
    await v.page.waitForFunction(() => /s3:\/\/acme-files\/tico/.test(document.querySelector('#set-storage')?.textContent));
    assert.deepEqual(JSON.parse(v.calls.filter(c => c.startsWith('PUT'))[1].slice(4)),
      {bucket: 'acme-files', region: 'us-west-2', prefix: 'tico', access_key_id: 'AKIAEXAMPLEGOOD0001', secret_access_key: 'example-secret'});
    assert.equal(await v.page.locator('[data-storage-form] [name=secret_access_key]').inputValue(), '', 'the secret is not kept on screen');
    await v.page.locator('[data-storage-off]').click();
    await v.page.waitForFunction(() => /Still read from \(1\)/.test(document.querySelector('#set-storage')?.textContent));
    assert.ok(v.calls.some(c => c.startsWith('DELETE')));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Tico file hosting on: nothing to type; Move existing files shows the estimate, confirms and reports it matched.
    v = await visit({tico: {state: 'on', label: 'Tico file hosting', address: 'acme-files.ticofiles.com', location: 'tico-files-us/c/acme', error: ''}});
    const hosting = v.page.locator('[data-storage-tico]');
    await hosting.waitFor();
    assert.match(await v.page.locator('#set-storage').innerText(), /New files go to Tico file hosting; the company's own storage is this server's disk/);
    assert.match(await hosting.innerText(), /Pictures, video and audio stored there open from acme-files\.ticofiles\.com/);
    await v.page.waitForFunction(() => /3 files \(2\.50 GB\) are not in Tico file hosting yet\./.test(document.querySelector('[data-move-line]')?.textContent));
    assert.equal(await hosting.locator('[data-move]').innerText(), 'Move existing files');
    assert.equal(await hosting.locator('[data-export]').getAttribute('href'), '/api/v2/system/storage/export');
    await hosting.locator('[data-move]').click();
    await v.page.waitForFunction(() => /Moved 3 files, 2\.50 GB, as estimated/.test(document.querySelector('[data-move-line]')?.textContent));
    assert.deepEqual(JSON.parse(v.calls.find(c => c.startsWith('POST move')).slice(10)), {expect_bytes: 2500000000});
    assert.equal(await hosting.locator('[data-move]').isHidden(), true, 'nothing left to move');
    await hosting.locator('[data-tico-check]').click();
    await v.page.waitForFunction(() => document.querySelector('[data-storage-tico]'));
    assert.ok(v.calls.includes('POST check'));
    assert.deepEqual(v.errors, []);
    await v.ctx.close();

    // Stopped: the same move goes the other way.
    v = await visit({tico: {state: 'stopped', label: 'Tico file hosting', address: '', location: 'tico-files-us/c/acme', keep_until: '2026-11-22T00:00:00Z', error: ''}});
    await v.page.locator('[data-storage-tico]').waitFor();
    assert.match(await v.page.locator('[data-storage-tico]').innerText(), /keeps the files it holds until November 2\d, 2026 .*download them or move them back first/);
    await v.page.waitForFunction(() => document.querySelector('[data-move]')?.textContent === 'Move files back');
    await v.ctx.close();

    v = await visit({role: 'human', hash: '#/settings'});
    await v.page.locator('#settings-tabs').waitFor();
    assert.equal(await v.page.locator('#settings-storage').count(), 0, 'only the owner has Storage');
    assert.equal(v.calls.length, 0);
    await v.ctx.close();
    console.log('PASS: Settings > General, owner only, refusal shown, secret cleared, back to server storage, Tico file hosting move and export');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
