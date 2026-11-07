// The real SDK must scrub outgoing events and discard retries after sign-out, including late failures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
const {uiFile} = require('./support/page.cjs');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"});
    await page.addInitScript(() => Object.defineProperty(navigator, 'webdriver', {get: () => false}));
    await page.clock.install();
    const sent = [], late = [];
    let received;
    const captured = new Promise(resolve => { received = resolve; });
    const cors = {'access-control-allow-origin': '*', 'access-control-allow-methods': 'POST, OPTIONS',
      'access-control-allow-headers': '*'};
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'acme.test') {
        const file = url.pathname === '/observability.ts' ? path.join(__dirname, '..', '..', 'build', 'ui', 'observability.js')
          : url.pathname === '/assets/observability-vendors.js' ? path.join(__dirname, '..', 'assets', 'observability-vendors.js') : null;
        return route.fulfill({contentType: file ? 'text/javascript' : 'text/html',
          body: file ? fs.readFileSync(file, 'utf8') : '<script src="/observability.ts"></script>'});
      }
      if (url.hostname === 'us.i.posthog.com') {
        if (route.request().method() === 'OPTIONS') return route.fulfill({headers: cors, body: ''});
        const raw = route.request().postData();
        const body = raw.startsWith('{') ? JSON.parse(raw)
          : JSON.parse(Buffer.from(new URLSearchParams(raw).get('data'), 'base64').toString());
        sent.push(body);
        late.push(route);
        if (sent.length === 3) received();
        return;
      }
      throw new Error('Unexpected SDK destination: ' + url.hostname);
    });
    await page.goto('http://acme.test/');
    await page.evaluate(async () => {
      window.telemetry = TicoObservability;
      // Use the production filters and adapter, with fictional authenticated config and no real backend.
      const fetch = window.fetch.bind(window);
      window.fetch = (url, ...options) => url === '/api/v2/observability'
        ? Promise.resolve({ok: true, json: async () => ({app: 'tico', environment: 'test',
          distinct_id: 'tico:' + 'a'.repeat(64), posthog_key: 'phc_test', posthog_host: 'https://us.i.posthog.com'})})
        : fetch(url, ...options);
      await telemetry.start();
      await telemetry.run('/v2/tasks', async () => ({ok: true, text: 'private-task-content'}));
    });
    let deadline;
    try {
      await Promise.race([captured, new Promise((_, reject) => {
        deadline = setTimeout(() => reject(new Error('SDK did not send the three allowed events')), 10000);
      })]);
    } finally { clearTimeout(deadline); }
    assert.ok(sent.length > 0, 'real SDK sent an allowlisted event');
    assert.ok(sent.every(body => !/private-task-content|acme\.test|\$current_url|\$browser/.test(JSON.stringify(body))), 'wire payloads are scrubbed');
    await page.evaluate(() => telemetry.reset());
    const before = sent.length;
    // Failed responses arriving after reset must neither enqueue nor dispatch a retry.
    for (const route of late.splice(0)) await route.fulfill({status: 503, headers: cors, body: '{}'});
    await page.clock.fastForward(120000);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.clock.fastForward(120000);
    assert.equal(sent.length, before, 'retired adapter cannot send or retry');
    console.log('telemetry wire privacy and late retry cancellation: ok');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
