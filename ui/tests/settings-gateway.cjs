// Settings > Computers > Models' Tico AI Gateway card against a fake server: the key is typed once, sent once and never
// shown again (only its preview); the Tico models appear only while the connection has a key, by their Tico names
// with what runs underneath; the Gateway's 80% warning, "Gateway cap reached" and a revoked key read plainly to the
// owner; members see only whether it is connected.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', dialog => dialog.accept());
    const KEY = 'tgw_fixture_0123456789abcdef';
    const tico = (id, name, under) => ({id, label: name, description: `Claude ${under} 5.5 underneath`, runtime: 'pi',
      provider: 'tico', efforts: ['low', 'high', 'max'], default_effort: 'high', harnesses: ['pi']});
    const catalog = {
      models: [
        {id: 'opus-5-5', label: 'opus 5.5', runtime: 'claude', provider: 'anthropic',
          efforts: ['low', 'high'], default_effort: 'high', harnesses: ['claude']},
        tico('tico-cheap-1.0', 'Tico Cheap 1.0', 'Haiku'), tico('tico-mid-1.0', 'Tico Mid 1.0', 'Sonnet'),
        tico('tico-power-1.0', 'Tico Power 1.0', 'Opus'),
      ],
      harnesses: [{id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude'}, {id: 'pi', label: 'pi', runtime: 'pi'}],
      default: {runtime: 'claude', model: 'opus-5-5'},
    };
    // The fake server's connection: what GET /v2/providers/gateway answers, by who asks.
    let gateway = {connected: false, source: '', revision: 0, state: 'none', working: false, message: 'No gateway key saved.'};
    const shown = owner => {
      const base = {label: 'Tico AI Gateway', connected: gateway.connected, source: gateway.source,
        url: 'https://updates.tico.team/v1/gateway', default_url: 'https://updates.tico.team/v1/gateway', editable: true};
      return owner ? {...base, preview: gateway.connected ? 'tgw…def' : '', revision: gateway.revision, state: gateway.state,
        working: gateway.working, message: gateway.message, used: gateway.used ?? null} : base;
    };
    const enabled = () => ['anthropic', ...(gateway.connected ? ['tico'] : [])];
    const sent = [];
    let role = 'owner';
    await page.route('**/*', async route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role, name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: enabled()});
      if (p === '/api/v2/providers') return json({enabled: enabled(), default: catalog.default, revision: 1, auto_fallback: true,
        providers: [{id: 'anthropic', label: 'Anthropic'}, {id: 'tico', label: 'Tico AI Gateway', gateway: true}]});
      if (p === '/api/v2/providers/gateway') {
        if (req.method() === 'GET') return json(shown(role === 'owner'));
        sent.push({method: req.method(), body: req.postDataJSON?.() ?? null});
        if (req.method() === 'PUT') {
          gateway = {connected: true, source: 'settings', revision: gateway.revision + 1, state: 'connected', working: true,
            message: 'Connected.'};
        } else if (req.method() === 'DELETE') {
          gateway = {connected: false, source: '', revision: gateway.revision + 1, state: 'none', working: false,
            message: 'No gateway key saved.'};
        }
        return json(shown(true));
      }
      if (p === '/api/v2/providers/gateway/check') { sent.push({method: 'CHECK'}); return json(shown(true)); }
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const card = page.locator('#set-gateway');
    const open = async () => {
      await page.evaluate(() => loadSettings());
      await page.locator('[data-settings-tab=devices]').click();
      await page.evaluate(() => renderSettingsProviders());
      await card.waitFor();
    };
    const modelOptions = () => page.locator('[data-provider-model] option').evaluateAll(
      options => options.map(o => ({value: o.value, text: o.textContent, title: o.title})));
    await page.goto('https://tico-ui.test/#/settings');
    await open();
    // Not connected: no Tico models, and the gateway is never a provider tick box of its own.
    assert.equal(await card.locator('[data-gateway-state]').getAttribute('data-gateway-state'), 'none');
    assert.equal(await page.locator('[data-provider][value=tico]').count(), 0, 'the gateway follows its key, not a tick box');
    assert.ok(!(await modelOptions()).some(o => o.value.startsWith('tico-')), 'no Tico models while not connected');
    // Connect: the key goes once in the PUT, the field empties, and the page never shows it again.
    await card.locator('[data-gateway-key]').fill(KEY);
    await card.locator('[data-gateway-save]').click();
    await page.locator('#set-gateway [data-gateway-state=connected]').waitFor();
    const put = sent.find(row => row.method === 'PUT');
    assert.deepEqual(put.body, {url: '', expected_revision: 0, key: KEY}, 'the key is sent once, with the revision shown');
    assert.equal(await card.locator('[data-gateway-key]').inputValue(), '', 'the key field is empty after save');
    assert.match(await card.locator('[data-gateway-key]').getAttribute('placeholder'), /tgw…def/, 'only the preview shows');
    assert.ok(!(await page.content()).includes(KEY), 'the key is nowhere on the page');
    // Connected: the Tico models are offered by their Tico names, saying what runs underneath.
    const tic = (await modelOptions()).filter(o => o.value.startsWith('tico-'));
    assert.deepEqual(tic.map(o => o.text), ['Tico Cheap 1.0', 'Tico Mid 1.0', 'Tico Power 1.0']);
    assert.equal(tic.find(o => o.value === 'tico-mid-1.0').title, 'Claude Sonnet 5.5 underneath');
    assert.equal(await page.evaluate(() => settingsModelPickName('tico-mid-1.0')), 'Tico Mid 1.0 (Claude Sonnet 5.5 underneath)',
      "a bot's model list says what runs underneath");
    assert.equal(await page.evaluate(() => modelWords('tico-power-1.0')), 'Tico Power 1.0');
    // A URL-only save keeps the saved key: no key in the body.
    await card.locator('[data-gateway-url]').fill('https://gateway.example.test/v1/gateway');
    await card.locator('[data-gateway-save]').click();
    await page.waitForFunction(() => document.querySelector('#set-gateway [data-gateway-url]')?.value === '');
    assert.deepEqual(sent.filter(row => row.method === 'PUT')[1].body,
      {url: 'https://gateway.example.test/v1/gateway', expected_revision: 1}, 'an empty field keeps the saved key');
    // What the Gateway says about the key reads plainly to the owner.
    const says = async (state, message, used, pill) => {
      gateway = {...gateway, state, message, used, working: !['revoked', 'unknown'].includes(state)};
      await open();
      assert.equal(await card.locator('.pill').innerText(), pill, `${state} badge`);
      assert.equal(await card.locator('[data-gateway-message]').innerText(), message, `${state} message`);
    };
    await says('warning', 'This key has used 80% of its monthly Gateway cap. Bots stop at 100% until next month.', 0.8, 'Connected');
    await says('cap_reached', 'Gateway cap reached: this key has used its monthly cap. Bots on Tico models stop until next month, or until Tico raises the cap.', 1, 'Gateway cap reached');
    await says('not_ready', "Not ready: the Gateway at this URL doesn't serve Tico models yet. Check the Gateway URL, or wait until Tico turns them on.", null, 'Not ready');
    await says('revoked', 'Not working: Tico has revoked this gateway key. Ask Tico for a new key and save it here.', null, 'Not working');
    await card.locator('[data-gateway-check]').click();
    await page.waitForFunction(() => !document.querySelector('#set-gateway [data-gateway-check]')?.disabled);
    assert.ok(sent.some(row => row.method === 'CHECK'), 'Check now asks the server');
    // A member sees whether it is connected, and nothing to change or read about the key.
    role = 'member';
    await page.evaluate(() => { S.me.role = 'member'; });
    await open();
    assert.equal(await card.locator('input, button').count(), 0, 'members get no key field or actions');
    assert.equal(await card.locator('[data-gateway-message]').count(), 0, "members don't see the Gateway's word on the key");
    role = 'owner';
    await page.evaluate(() => { S.me.role = 'owner'; });
    // Clear: the key goes, and so do the Tico models.
    await open();
    await card.locator('[data-gateway-clear]').click();
    await page.locator('#set-gateway [data-gateway-state=none]').waitFor();
    assert.ok(!(await modelOptions()).some(o => o.value.startsWith('tico-')), 'Tico models go with the key');
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
