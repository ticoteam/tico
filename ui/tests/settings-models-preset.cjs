// Settings > AI providers' models choice (backend/models_preset.py) against a fake server: the four choices in plain
// words, the recommended one marked; picking a subscription choice with nothing ticked ticks Claude and ChatGPT; Save
// sends the choice; the same form serves the first-run step. Its wording names no logo, partner or approval, and
// says nothing about what Tico keeps of a sign-in.
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
    const catalog = {
      models: [
        {id: 'opus-5-5', label: 'opus 5.5', runtime: 'claude', provider: 'anthropic', efforts: ['high'], default_effort: 'high', harnesses: ['claude']},
        {id: 'gpt-5.5', label: 'gpt 5.5', runtime: 'codex', provider: 'openai', efforts: ['high'], default_effort: 'high', harnesses: ['codex']},
      ],
      harnesses: [{id: 'claude', label: 'Claude Code (Anthropic)', runtime: 'claude'}, {id: 'codex', label: 'Codex (OpenAI)', runtime: 'codex'}],
      default: {runtime: '', model: ''},
    };
    let saved = {enabled: [], preset: '', revision: 1};
    const view = () => ({...saved, default: catalog.default, auto_fallback: true,
      presets: ['subscriptions-gateway', 'subscriptions', 'gateway', 'own-keys'],
      providers: [{id: 'anthropic', label: 'Anthropic'}, {id: 'openai', label: 'OpenAI'}, {id: 'tico', label: 'Tico AI Gateway', gateway: true}]});
    const sent = [];
    await page.route('**/*', async route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.com', cloud: true, registered: true});
      if (p === '/api/v2/config') return json({version: '0.1.0', app_name: 'Tico', local: true});
      if (p === '/api/employees') return json([]);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/operations') return json({machines: [], services: []});
      if (p === '/api/v2/models') return json({...catalog, enabled_providers: saved.enabled});
      if (p === '/api/v2/providers') {
        if (req.method() === 'PUT') {
          const body = req.postDataJSON();
          sent.push(body);
          saved = {enabled: body.enabled, preset: body.preset ?? saved.preset, revision: saved.revision + 1};
        }
        return json(view());
      }
      if (p === '/api/v2/providers/gateway') return json({label: 'Tico AI Gateway', connected: false, source: '', editable: true,
        url: 'https://updates.tico.team/v1/gateway', default_url: 'https://updates.tico.team/v1/gateway', preview: '', revision: 0,
        state: 'none', working: false, message: 'No gateway key saved.'});
      if (p === '/api/v2/usage/limits') return json({bots: {}});
      if (p === '/api/v2/settings/history') return json({changes: [], transitions: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    const choices = page.locator('#set-prov [data-provider-presets]');
    const open = async () => {
      await page.evaluate(() => loadSettings());
      await page.locator('[data-settings-tab=providers]').click();
      await page.evaluate(() => renderSettingsProviders());
      await choices.waitFor();
    };
    await page.goto('https://tico-ui.test/#/settings');
    await open();
    const labels = await choices.locator('label strong').allInnerTexts();
    assert.deepEqual(labels, ['Your Claude and ChatGPT subscriptions, plus Tico Gateway', 'Your Claude and ChatGPT subscriptions only',
      'Tico Gateway only', "Your company's plans or API keys"]);
    const recommended = choices.locator('label', {has: page.locator('.pill')});
    assert.equal(await recommended.count(), 1);
    assert.match(await recommended.innerText(), /^Your Claude and ChatGPT subscriptions, plus Tico Gateway/);
    const words = await choices.innerText();
    assert.match(words, /Sign in with your own Claude and ChatGPT accounts\./);
    assert.match(words, /Don't share one login across your team\./);
    // Plain words only: no logos, and no claim of a partnership or approval, nor of what is kept of a sign-in.
    assert.equal(await choices.locator('img, svg').count(), 0, 'no provider logos');
    for (const claim of [/partner/i, /approved/i, /official/i, /endorse/i, /token/i, /password/i, /\bstor/i, /never see/i])
      assert.doesNotMatch(words, claim);
    // Nothing ticked: the recommended choice ticks Claude and ChatGPT and offers their models; Save sends the choice.
    assert.equal(await page.locator('#set-prov [data-provider]:checked').count(), 0);
    await choices.locator('[data-provider-preset][value=subscriptions-gateway]').check();
    assert.deepEqual(await page.locator('#set-prov [data-provider]:checked').evaluateAll(boxes => boxes.map(b => b.value)), ['anthropic', 'openai']);
    await page.locator('#set-prov-save').click();
    await page.waitForFunction(() => document.querySelector('#set-prov [data-provider-preset][value=subscriptions-gateway]')?.checked);
    assert.equal(sent.at(-1).preset, 'subscriptions-gateway');
    assert.deepEqual(sent.at(-1).enabled, ['anthropic', 'openai']);
    // Another choice keeps what is ticked.
    await choices.locator('[data-provider-preset][value=own-keys]').check();
    assert.equal(await page.locator('#set-prov [data-provider]:checked').count(), 2);
    // The first-run step is the same form.
    const first = await page.evaluate(() => providersFormHTML({presets: ['subscriptions-gateway', 'subscriptions', 'gateway', 'own-keys'],
      providers: [], enabled: []}, [], 'onb-prov'));
    assert.match(first, /data-provider-presets/);
    assert.match(first, /Your Claude and ChatGPT subscriptions, plus Tico Gateway/);
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
