// Offline browser regression: a person's message to a bot (their DM thread) that an org rule holds is not a failed
// send. Nothing is posted, no error shows, and the DM shows the held message with its rule. A reopened DM lists what is
// still held; Edit withdraws the hold and puts the text back in the box to send again.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const TEXT = 'Our favorite color is gold.';
const HOLD = {id: 'h-own', operation: 'threads', author: 'human:ana', author_name: 'Ana', state: 'held', outcome: 'blocked',
  rules: [{id: 'r-color', kind: 'internal', text: 'Acme does not name a favorite color.', version: 1, status: 'active', if_broken: 'warn'}],
  text: TEXT, to: ['Sales'], reviewer: null, reviewer_name: null, task_id: null, decided_at: null, decided_by: null,
  reason: 'Your chat message was held: it broke the rule \'Acme does not name a favorite color.\'.',
  created: new Date().toISOString(), can_withdraw: true, can_send: false};
const me = {member: true, following: true, notify: 'live', last_read_seq: 0, unread: 0, activity: false, can_post: true, can_manage: false};
const DM = {id: 't-dm', title: '', audience: 'personal', root_id: 't-dm', last_seq: 0, last_unread_seq: 0, me, members: []};

(async () => {
  const browser = await launch();
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce'});
    const errors = [], withdrawn = [], posted = [];
    let listed = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', host: 'keeper', status: 'active', can_chat: true, schedules: [],
        my_access: {see: true, read: true, write: true}}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/events') return route.abort();
      if (p === '/api/v2/threads' && req.method() === 'GET') return json({attention: [], threads: [], dms: [DM], next_cursor: null});
      if (p === '/api/v2/threads/dm') return json(DM);
      if (p === '/api/v2/threads/home/bot%3Asales' || p === '/api/v2/threads/home/bot:sales') return json({error: 'not_found'}, 404);
      if (p === '/api/v2/threads/t-dm/timeline') return json({thread: DM, breadcrumb: [], messages: [], next_cursor: null});
      if (p === '/api/v2/threads/t-dm/messages' && req.method() === 'POST') {
        posted.push(JSON.parse(req.postData() || '{}').text);
        listed = [HOLD];
        return json({held: HOLD});
      }
      if (p === '/api/v2/rules/stats') return json({on: true, checks_today: 1, unchecked_today: 0, usd_left: 10, people_usd: 0.42});
      if (p === '/api/v2/rule-holds') return json({holds: listed.map(({text, ...h}) => h), next_cursor: null});
      if (p === '/api/v2/rule-holds/h-own') return json(listed[0] || HOLD);
      if (p === '/api/v2/rule-holds/h-own/withdraw') {
        withdrawn.push(p);
        listed = [];
        return json({...HOLD, state: 'kept', can_withdraw: false, text: undefined});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/sales/dm');
    const box = page.locator('#bot-thread .thv-composer .p-text');
    await box.waitFor();

    // The send is held: the DM shows the held message with its rule, and no failure is reported.
    await box.click(); await box.pressSequentially(TEXT);
    await page.keyboard.press('Enter');
    const held = page.locator('#bot-thread .th-held');
    await held.getByText('Acme does not name a favorite color.').waitFor();
    assert.deepEqual(posted, [TEXT]);
    assert.deepEqual(await page.locator('.toast.err').allInnerTexts(), [], 'a held message is not an error');

    // A reopened DM lists the held messages it still has.
    await page.reload();
    await held.getByText('Acme does not name a favorite color.').waitFor();

    // Edit: the hold is withdrawn and its text is back in the box, for a new send.
    await held.locator('[data-held-edit]').click();
    await page.waitForFunction(() => !document.querySelector('#bot-thread .th-held'));
    assert.equal(withdrawn.length, 1, 'the hold is withdrawn once');
    assert.equal(await box.inputValue(), TEXT, 'the held text is back in the box');
    assert.deepEqual(errors, []);
    console.log('ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
