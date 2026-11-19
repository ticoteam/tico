// Company › Risk on a real hub seeded with three risks and four disputes (evals/redesign/seeds.py "risk"): the Company
// row, the live risk open with its matters and the others one line each, a risk's own page, the Disputes table, a matter's
// facts edited from ⋯ (its product picked from ours), on a desktop and a phone. A member reads the same page without the private dispute.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

const go = (page: Page, hash: string) => page.goto(page.url().split('#')[0] + hash);
const text = async (page: Page, selector: string) => (await page.locator(selector).first().innerText()).replace(/\s+/g, ' ').trim();
// A row's every column, shown or folded away by a narrow list (the phone, the docked chat).
const all = async (row: import('playwright').Locator) => (await row.evaluate(el => el.textContent || '')).replace(/\s+/g, ' ');

async function flow(page: Page, tag: string, phone: boolean) {
  await go(page, '#/risk');
  // Risk is a Company row, after Growth, current on its page.
  const row = page.locator('#side a[data-nav="risk"]');
  assert.equal(await row.getAttribute('href'), '#/risk', tag + ': the Company row opens Risk');
  const company = await page.locator('#rd-co .nav-link').evaluateAll(links => links.map(a => (a as HTMLElement).dataset['nav']));
  assert.equal(company.indexOf('risk'), company.indexOf('growth') + 1, tag + ': Risk follows Growth');
  await page.locator('#side a[data-nav="risk"][aria-current="page"]').waitFor({state: 'attached', timeout: t(10000)});
  // The nav row turns current before the lazy page draws, so the heading is waited for, not read once.
  await page.waitForFunction(() => document.querySelector('#main h1')?.textContent?.trim() === 'Risk', null, {timeout: t(20000)});

  // The live risk: loud and open, its matters under it, the rules icon (never a count) beside its counts.
  const live = page.locator('[data-rk-level="live"][data-rk-risk="risk:customer-theft-claims"]');
  await live.waitFor({timeout: t(20000)});
  const head = await text(page, '[data-rk-risk="risk:customer-theft-claims"] h2');
  assert.match(head, /Customer theft claims/);
  assert.match(head, /2 open/);
  assert.match(head, /\$140k claimed/);
  assert.equal(await live.locator('h2 .rk-rules').count(), 1, tag + ': the rules icon');
  assert.match(await live.locator('.rk-matter').filter({hasText: 'Randazzo'}).innerText(), /New/, tag + ': today\'s matter is New');
  assert.match(await live.locator('.rk-matter').filter({hasText: 'Murphy'}).innerText(), /AAA/, tag + ': an older one shows its forum');
  // Open and quiet risks: one line each; no icon on a risk with no rules.
  const open = page.locator('[data-rk-level="open"][data-rk-risk="risk:pro-disputes"]');
  assert.match(await all(open), /Flores/);
  assert.match(await all(open), /2 open/, tag + ': Dana reads the private dispute too');
  const quiet = page.locator('[data-rk-level="quiet"][data-rk-risk="risk:trademarks"]');
  assert.match(await quiet.innerText(), /None/);
  assert.equal(await quiet.locator('.rk-rules').count(), 0, tag + ': no rules, no icon');

  // One risk: its matters with amounts and forums, and the rule that guards it.
  await live.locator('h2 a').click();
  await page.waitForFunction(() => location.hash === '#/risk/r/customer-theft-claims', null, {timeout: t(10000)});
  const matters = page.locator('[data-rk-matters]');
  await matters.locator('.rk-matter').first().waitFor({timeout: t(20000)});
  await page.waitForFunction(() => /Customer theft claims/.test(document.querySelector('#main h1')?.textContent || ''), null, {timeout: t(20000)});
  const randazzo = matters.locator('.rk-matter').filter({hasText: 'Randazzo'});
  assert.match(await randazzo.innerText(), /\$50,000 jewelry/);
  assert.match(await all(randazzo), /Demand/);
  assert.match(await page.locator('.rk-rule').first().innerText(), /Every pro is checked before a first job\./);
  assert.equal(await page.locator('.rk-rule').first().getAttribute('href').then(h => (h || '').startsWith('#/rules?rule=')), true, tag + ': a rule opens on Rules');

  // Disputes: every open dispute with its amount, forum and risk; the claimed total heads the amounts.
  await go(page, '#/risk/disputes');
  const table = page.locator('[data-rk-disputes]');
  await table.waitFor({timeout: t(20000)});
  assert.match(await text(page, '.rk-colh'), /4 open/);
  assert.match(await all(page.locator('.rk-colh')), /\$145k claimed/);
  const murphy = table.locator('.rk-matter').filter({hasText: 'Murphy'});
  assert.match(await murphy.innerText(), /\$90,400/);
  assert.match(await all(murphy), /Customer theft claims/);
  assert.match(await all(murphy), /AAA/);
  assert.equal(await table.locator('.rk-matter').filter({hasText: 'Sealed claim'}).count(), 1, tag + ': Dana reads the private dispute');

  // ⋯ opens the matter's facts beside the list (full screen on a phone); a saved forum shows on its row. The desktop
  // also picks the matter's product, which the phone's save (the forum alone) keeps.
  const flores = table.locator('.rk-matter').filter({hasText: 'Flores'});
  await flores.locator('[data-rk-matter]').click();
  await page.waitForFunction(() => /[?&]matter=/.test(location.hash), null, {timeout: t(10000)});
  const form = page.locator('#rk-side [data-rk-form]');
  await form.waitFor({timeout: t(20000)});
  assert.equal(await page.locator('#rk-side').isVisible(), true, tag + ': the facts panel shows');
  const forum = phone ? 'Court' : 'Small claims';
  await form.locator('input[name="forum"]').fill(forum);
  if (!phone) await form.locator('select[name="product"]').selectOption({label: 'Acme Kits'});
  else assert.equal(await form.locator('select[name="product"] option:checked').innerText(), 'Acme Kits', tag + ': the form holds the product');
  await form.locator('button[type="submit"]').click();
  await page.locator('#rk-side').waitFor({state: 'hidden', timeout: t(10000)});
  assert.equal(/[?&]matter=/.test(await page.evaluate(() => location.hash)), false, tag + ': saving closes the panel');
  await page.waitForFunction(f => [...document.querySelectorAll('[data-rk-disputes] .rk-matter')].some(r => r.textContent!.includes('Flores') && r.textContent!.includes(f)), forum, {timeout: t(10000)});
  // Escape closes the panel too.
  await table.locator('.rk-matter').filter({hasText: 'Murphy'}).locator('[data-rk-matter]').click();
  await form.waitFor({timeout: t(20000)});
  await page.keyboard.press('Escape');
  await page.locator('#rk-side').waitFor({state: 'hidden', timeout: t(10000)});
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured', 'risk']});
  let browser: import('playwright').Browser | undefined;
  try {
    // The hub's clock is the scenario's, not this computer's: the browser reads the same time, so a matter the seed
    // made just now is New on the page as it is live on the hub.
    const seeded = await fetch(hub.url + '/api/v2/matters?kind=dispute&open=true&limit=200', {headers: {Authorization: 'Bearer ' + hub.tokens['dana']}})
      .then(r => r.json() as Promise<{matters: {task: {title: string; created_at: string}}[]}>);
    const madeAt = seeded.matters.find(m => m.task.title === 'Randazzo')?.task.created_at;
    if (!madeAt) throw new Error('the seed made no Randazzo');
    const hubNow = new Date(Date.parse(madeAt) + 3600e3);
    const dana = {Authorization: 'Bearer ' + hub.tokens['dana'], 'Content-Type': 'application/json'};
    const made = await fetch(hub.url + '/api/v2/products', {method: 'POST', headers: {...dana, 'Idempotency-Key': 'acme-kits'}, body: JSON.stringify({name: 'Acme Kits'})});
    assert.ok(made.ok, await made.clone().text());
    const kits = ((await made.json()) as {product: {key: string}}).product.key;
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      await page.clock.setFixedTime(hubNow);
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(String(e)));
      try {
        await page.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
        await flow(page, phone ? 'phone' : 'desktop', phone);
        if (phone) assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
        assert.deepEqual(errors, []);
      } finally {
        await page.context().close();
      }
    }
    // The product's matters hold the one the desktop picked it for.
    const about = await fetch(`${hub.url}/api/v2/matters?product=${encodeURIComponent(kits)}`, {headers: dana})
      .then(r => r.json() as Promise<{matters: {task: {title: string}}[]}>);
    assert.deepEqual(about.matters.map(m => m.task.title), ['Flores']);
    // A member reads the risks and disputes, without the private one.
    const member = await pageAs(browser, hub, 'omar');
    await member.clock.setFixedTime(hubNow);
    try {
      await member.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
      await go(member, '#/risk/disputes');
      await member.locator('[data-rk-disputes]').waitFor({timeout: t(20000)});
      assert.match(await text(member, '.rk-colh'), /3 open/);
      assert.equal(await member.locator('[data-rk-disputes] .rk-matter').filter({hasText: 'Sealed claim'}).count(), 0, 'the private dispute stays private');
      await go(member, '#/risk');
      await member.locator('[data-rk-risk="risk:pro-disputes"]').waitFor({timeout: t(20000)});
      assert.match(await member.locator('[data-rk-risk="risk:pro-disputes"]').innerText(), /1 open/);
    } finally {
      await member.context().close();
    }
    console.log('risk: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
