// Offline browser regression: a person's chat message an org rule holds is not a failed send. Nothing is sent, the box
// keeps the text, and the chat shows the held message with its rule. Edit and resend withdraws the hold and puts the
// text back in the box to send again; a reopened chat lists what is still held.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const TEXT = 'Our favorite color is gold.';
const HOLD = {id: 'h-own', operation: 'message', author: 'human:ana', author_name: 'Ana', state: 'held', outcome: 'blocked',
  rules: [{id: 'r-color', kind: 'internal', text: 'Acme does not name a favorite color.', version: 1, status: 'active', if_broken: 'warn'}],
  text: TEXT, to: ['Sales'], reviewer: null, reviewer_name: null, task_id: null, decided_at: null, decided_by: null,
  reason: 'Your chat message was held: it broke the rule \'Acme does not name a favorite color.\'.',
  created: new Date().toISOString(), can_withdraw: true, can_send: false};

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors = [], withdrawn = [];
    let listed = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/conversations')
        return json({conversations: url.searchParams.get('chat_with') ? [{id: 'c1', kind: 'chat', scope: 'personal', participants: ['human:ana', 'bot:sales']}] : []});
      if (p.endsWith('/snapshot')) return json({messages: [], execution: null});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/rules/stats') return json({on: true, checks_today: 1, unchecked_today: 0, usd_left: 10, people_usd: 0.42});
      if (p === '/api/v2/chat/sales') return json({held: HOLD});
      if (p === '/api/v2/rule-holds') return json({holds: listed.map(({text, ...h}) => h), next_cursor: null});
      if (p === '/api/v2/rule-holds/h-own') return json(listed[0] || HOLD);
      if (p === '/api/v2/rule-holds/h-own/withdraw') {
        withdrawn.push(p);
        listed = [];
        return json({...HOLD, state: 'kept', can_withdraw: false, text: undefined});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/sales');
    const box = page.locator('#chat-composer textarea');
    await box.waitFor();
    await page.waitForFunction(() => V2C?.loaded);

    // The send is held: the card shows the rule, the text stays in the box, and no failure is reported.
    await box.click(); await box.pressSequentially(TEXT);
    await page.keyboard.press('Enter');
    const card = page.locator('#conv-thread .rh-card[data-hold="h-own"]');
    await card.waitFor();
    assert.match(await card.innerText(), /Acme does not name a favorite color/);
    assert.equal(await box.inputValue(), TEXT, 'the held text stays in the box');
    assert.deepEqual(await page.locator('.toast.err').allInnerTexts(), [], 'a held message is not an error');

    // Edit and resend: the hold is withdrawn and its text is back in the box, for a new send.
    await box.fill('Something else entirely.');
    listed = [HOLD];
    await card.locator('[data-hold-edit]').click();
    await page.waitForFunction(() => !document.querySelector('#conv-thread .rh-card[data-hold="h-own"]'));
    assert.equal(withdrawn.length, 1, 'the hold is withdrawn once');
    assert.equal(await box.inputValue(), TEXT, 'the held text is back in the box');

    // A reopened chat lists the held messages it still has.
    listed = [HOLD];
    await page.evaluate(() => { V2C.holdsFor = null; v2ChatRender(V2C); });
    await page.locator('#conv-thread .rh-card[data-hold="h-own"]').waitFor();
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
