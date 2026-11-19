// Company › Risk's dates on a real hub (evals/redesign/seeds.py "risk" and "risk-dates"): the Obligations tab in its
// four groups, binding dates in ink and the other side's in grey, an unproved premium loud under Unconfirmed that a link
// moves to Past 30 days, a waiver that needs a note, a mark made elsewhere arriving live, the dates beside a live risk's
// matters and on its page. A member reads the same dates without the private matter's, and may not mark them.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

const go = (page: Page, hash: string) => page.goto(page.url().split('#')[0] + hash);
// A row's every column, shown or folded away by a narrow list (the phone, the docked chat).
const all = async (row: import('playwright').Locator) => (await row.evaluate(el => el.textContent || '')).replace(/\s+/g, ' ');
const due = (page: Page, title: string) => page.locator('[data-rk-dates] .rk-due').filter({hasText: title});
// The group a date sits under on the Obligations tab: the last label before its row.
const groupOf = (page: Page, title: string) => page.evaluate(title => {
  let group = '';
  for (const el of document.querySelectorAll<HTMLElement>('[data-rk-dates] [data-rk-group], [data-rk-dates] .rk-due')) {
    if (el.dataset['rkGroup']) group = el.dataset['rkGroup'];
    else if (el.textContent?.includes(title)) return group;
  }
  return null;
}, title);

async function openDate(page: Page, title: string) {
  await due(page, title).locator('.rk-more').click();
  await page.waitForFunction(() => /[?&]due=/.test(location.hash), null, {timeout: t(10000)});
  await page.locator('#rk-side [data-rk-due-facts]').waitFor({timeout: t(20000)});
}

type Hub = {url: string; tokens: Record<string, string>};
async function desktop(page: Page, hub: Hub) {
  // Risks: a live risk shows its dates beside its matters, and an unconfirmed date makes its risk live and says so.
  await go(page, '#/risk');
  const people = page.locator('[data-rk-level="live"][data-rk-risk="risk:people-benefits"]');
  await people.waitFor({timeout: t(20000)});
  assert.match(await people.locator('h2').innerText(), /1 unconfirmed/);
  assert.match(await people.locator('[data-rk-dates]').innerText(), /Principal premium/);
  const theft = page.locator('[data-rk-level="live"][data-rk-risk="risk:customer-theft-claims"]');
  assert.match(await theft.locator('[data-rk-dates]').innerText(), /Murphy discovery, Zoom notice/);

  // One risk: its dates under Obligations.
  await go(page, '#/risk/r/customer-theft-claims');
  const section = page.locator('[data-rk-obligations]');
  await section.locator('.rk-due').first().waitFor({timeout: t(20000)});
  assert.match(await section.innerText(), /Murphy final hearing/);
  assert.equal(await section.locator('.rk-due').filter({hasText: 'Paiva records'}).count(), 0, 'only this risk\'s dates');

  // Obligations: the four groups in order; binding dates in ink, the other side's grey, the unproved premium loud.
  await go(page, '#/risk/obligations');
  await page.locator('[data-rk-dates]').waitFor({timeout: t(20000)});
  assert.equal(await page.locator('.ph-tab[aria-current="page"]').innerText(), 'Obligations', 'the tab is current');
  assert.deepEqual(await page.locator('[data-rk-dates] [data-rk-group]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset['rkGroup'])),
    ['unconfirmed', 'next', 'later', 'past']);
  const premium = due(page, 'Principal premium');
  assert.equal(await groupOf(page, 'Principal premium'), 'unconfirmed');
  assert.match(await premium.getAttribute('class') || '', /rk-loud/);
  assert.match(await premium.locator('.rk-un').innerText(), /Unconfirmed/);
  assert.match(await all(premium), /People & benefits/);
  assert.match(await due(page, 'Paiva records').getAttribute('class') || '', /rk-bind/);
  assert.match(await due(page, 'Murphy claimant discovery').getAttribute('class') || '', /rk-soft/);
  assert.equal(await groupOf(page, 'Murphy final hearing'), 'later');
  assert.match(await all(due(page, 'Murphy production')), /files\.acme\.example/, 'a met date shows its link');
  assert.match(await all(due(page, 'Randazzo reply')), /Passed/);
  assert.equal(await due(page, 'Sealed claim answer').count(), 1, 'Dana reads the private matter\'s date');

  // The premium's panel: where it comes from; a link as proof moves it to Past 30 days with the link's name.
  await openDate(page, 'Principal premium');
  const side = page.locator('#rk-side');
  assert.match(await side.innerText(), /Contract/);
  assert.match(await side.innerText(), /Pay each insurance premium on time\./);
  await side.locator('[data-rk-act="proof"]').click();
  const form = side.locator('[data-rk-act-form="proof"]');
  await form.locator('input[name="link"]').fill('http://files.acme.example/receipt');
  await form.locator('button[type="submit"]').click();
  await form.locator('[data-error]:not([hidden])').waitFor({timeout: t(5000)});
  await form.locator('input[name="link"]').fill('https://files.acme.example/principal-receipt');
  await form.locator('button[type="submit"]').click();
  await side.locator('[data-rk-due-facts] .rk-pf').waitFor({timeout: t(20000)});
  assert.match(await side.locator('[data-rk-due-facts]').innerText(), /Met/);
  assert.equal(await side.locator('[data-rk-acts]').count(), 0, 'a proved date takes no more marks');
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>('[data-rk-dates] .rk-due')]
    .some(r => r.textContent!.includes('Principal premium') && r.dataset['rkState'] === 'met'), null, {timeout: t(10000)});
  assert.equal(await groupOf(page, 'Principal premium'), 'past');
  assert.match(await all(due(page, 'Principal premium')), /files\.acme\.example/);
  await page.keyboard.press('Escape');
  await side.waitFor({state: 'hidden', timeout: t(10000)});

  // A mark made elsewhere (here, the API) arrives on the open tab by its task event, well before the 3-minute poll.
  const headers = {Authorization: 'Bearer ' + hub.tokens['dana'], 'Content-Type': 'application/json'};
  const listed = await fetch(hub.url + '/api/v2/risk/obligations?limit=200', {headers})
    .then(r => r.json() as Promise<{obligations: {id: string; due_on: string; title: string}[]}>);
  const zoom = listed.obligations.find(d => d.title === 'Murphy discovery, Zoom notice');
  if (!zoom) throw new Error('the seed made no Zoom notice');
  const marked = await fetch(`${hub.url}/api/v2/rule-due/${zoom.id}/satisfied`,
    {method: 'POST', headers: {...headers, 'Idempotency-Key': 'zoom-waive'}, body: JSON.stringify({due_on: zoom.due_on, outcome: 'waived', note: 'The court dropped it.'})});
  assert.ok(marked.ok, await marked.text());
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>('[data-rk-dates] .rk-due')]
    .some(r => r.textContent!.includes('Murphy discovery, Zoom notice') && r.dataset['rkState'] === 'waived'), null, {timeout: t(15000)});
  assert.equal(await groupOf(page, 'Murphy discovery, Zoom notice'), 'past');
}

async function phone(page: Page) {
  // The proof from the desktop holds; a waiver needs a note and keeps it.
  await go(page, '#/risk/obligations');
  await page.locator('[data-rk-dates]').waitFor({timeout: t(20000)});
  assert.equal(await groupOf(page, 'Principal premium'), 'past');
  await openDate(page, 'Paiva records');
  const side = page.locator('#rk-side');
  assert.equal(await side.isVisible(), true, 'the panel shows full screen');
  await side.locator('[data-rk-act="waived"]').click();
  const form = side.locator('[data-rk-act-form="waived"]');
  await form.locator('button[type="submit"]').click();
  assert.match(await form.locator('[data-error]').innerText(), /Say why/);
  await form.locator('textarea[name="note"]').fill('The court moved it.');
  await form.locator('button[type="submit"]').click();
  await side.locator('[data-rk-due-facts]').filter({hasText: 'The court moved it.'}).waitFor({timeout: t(20000)});
  await page.locator('#rk-side .rk-close').click();
  await side.waitFor({state: 'hidden', timeout: t(10000)});
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLElement>('[data-rk-dates] .rk-due')]
    .some(r => r.textContent!.includes('Paiva records') && r.dataset['rkState'] === 'waived'), null, {timeout: t(10000)});
  assert.equal(await groupOf(page, 'Paiva records'), 'past');
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured', 'risk', 'risk-dates']});
  let browser: import('playwright').Browser | undefined;
  try {
    // The hub's clock is the scenario's, not this computer's: the browser reads the same day.
    const seeded = await fetch(hub.url + '/api/v2/matters?kind=dispute&open=true&limit=200', {headers: {Authorization: 'Bearer ' + hub.tokens['dana']}})
      .then(r => r.json() as Promise<{matters: {task: {title: string; created_at: string}}[]}>);
    const madeAt = seeded.matters.find(m => m.task.title === 'Randazzo')?.task.created_at;
    if (!madeAt) throw new Error('the seed made no Randazzo');
    const hubNow = new Date(Date.parse(madeAt) + 3600e3);
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    for (const onPhone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone: onPhone});
      await page.clock.setFixedTime(hubNow);
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(String(e)));
      try {
        await page.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
        await (onPhone ? phone(page) : desktop(page, hub));
        if (onPhone) assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
        assert.deepEqual(errors, []);
      } finally {
        await page.context().close();
      }
    }
    // A member reads every date but the private matter's, and may not mark them.
    const member = await pageAs(browser, hub, 'omar');
    await member.clock.setFixedTime(hubNow);
    try {
      await member.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
      await go(member, '#/risk/obligations');
      await member.locator('[data-rk-dates]').waitFor({timeout: t(20000)});
      assert.equal(await due(member, 'Murphy final hearing').count(), 1);
      assert.equal(await due(member, 'Sealed claim answer').count(), 0, 'the private matter\'s date stays private');
      await openDate(member, 'Murphy final hearing');
      assert.equal(await member.locator('#rk-side [data-rk-acts]').count(), 0, 'a member marks nothing');
    } finally {
      await member.context().close();
    }
    console.log('risk-obligations: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
