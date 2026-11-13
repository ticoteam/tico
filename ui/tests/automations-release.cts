// A scheduled routine's run settings (ui/app/automations-editors.ts auReleaseFields, backend/automation_flow.py
// `_check_release`) against the fake hub in support/automations-fake.cts: a schedule trigger's editor shows Wait for a
// warm session, its hours (usable only while the switch is on), At the deadline and Session, opening on the defaults
// when the flow has none, and Save sends them as the flow's `release`; a saved release opens as saved; another kind of
// trigger shows none of them and sends no release, and changing a schedule with one to another kind takes it away
// (the hub refuses a release on any other trigger). A computer, then a phone.
import type {Page} from 'playwright';
import type * as AutomationsFakeSupport from './support/automations-fake.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways} = require('./support/automations-fake.cts') as AutomationsFakeSupport.Api;

const base = {owner_id: 'person:dana', enabled: true, version: 1, steps: []};
const tweak = (fx: AutomationsFakeSupport.AutomationsFixture) => fx.automations.push(
  {...base, id: 'au_daily', name: 'Morning digest', trigger: {type: 'schedule', cron: '0 7 * * 1-5', tz: 'UTC'}},
  {...base, id: 'au_weekly', name: 'Weekly review', trigger: {type: 'schedule', cron: '0 9 * * 1', tz: 'UTC'},
    release: {warm: true, wait_hours: 12, deadline: 'run', session: 'continue'}},
  {...base, id: 'au_notice', name: 'From Tico', trigger: {type: 'event', name: 'tico.notice'}},
);
const CONTROLS = ['[data-b="release.warm"]', '[data-b="release.wait_hours"]', '[data-b="release.deadline"]', '[data-b="release.session"]'];
const ed = (page: Page, sel: string) => page.locator('.au-ed ' + sel);

async function openTrigger(page: Page) {
  await page.locator('.au-card[data-open="trigger"]').click();
  await page.locator('.au-ed:not([hidden]) [data-b="ttype"]').waitFor();
}
// Save the trigger and return the PATCH body it sent.
async function save(page: Page, writes: AutomationsFakeSupport.Write[]) {
  const n = writes.length;
  await ed(page, '.au-save').click();
  await page.waitForFunction(() => (document.querySelector('.au-ed') as HTMLElement).hidden);
  const w = writes.slice(n).find(x => x.method === 'PATCH');
  assert.ok(w, 'one PATCH');
  return w!.body!;
}

async function journey(browser: import('playwright').Browser, viewport: AutomationsFakeSupport.Viewport) {
  const {page, errors, writes, db} = await openAutomations(browser, {viewport, tweak, hash: '#/automations/au_daily'});
  await page.waitForSelector('.au-card[data-open="trigger"]');
  // A schedule with no settings opens on the defaults: off, 0 hours (disabled while off), run cold, fresh.
  await openTrigger(page);
  const warm = ed(page, '[data-b="release.warm"]'), hours = ed(page, '[data-b="release.wait_hours"]');
  assert.equal(await warm.isChecked(), false);
  assert.equal(await warm.getAttribute('role'), 'switch');
  assert.equal(await hours.isDisabled(), true, 'hours wait for the switch');
  assert.equal(await hours.inputValue(), '0');
  assert.equal(await ed(page, '[data-b="release.deadline"]').inputValue(), 'run');
  assert.equal(await ed(page, '[data-b="release.session"]').inputValue(), 'fresh');
  // Each control is reached by its label.
  await page.getByLabel(/^At the deadline/).selectOption('skip');
  await page.getByLabel(/^Session/).selectOption('continue');
  await warm.click();
  assert.equal(await hours.isDisabled(), false, 'the switch on frees the hours');
  await hours.fill('6');
  assert.equal(await page.getByLabel(/^At the deadline/).inputValue(), 'skip', 'a redraw keeps the picks');
  const body = await save(page, writes);
  assert.deepEqual(body.release, {warm: true, wait_hours: 6, deadline: 'skip', session: 'continue'});
  assert.deepEqual(body.trigger, {type: 'schedule', cron: '0 7 * * 1-5', tz: 'UTC'});
  // Opened again, it reads what was saved.
  await openTrigger(page);
  assert.equal(await warm.isChecked(), true);
  assert.equal(await hours.inputValue(), '6');
  assert.equal(await ed(page, '[data-b="release.deadline"]').inputValue(), 'skip');
  assert.equal(await sideways(page), false);
  // Turned to an event, the controls go and Save takes the saved settings away, which the hub accepts.
  await ed(page, '[data-b="ttype"]').selectOption('event');
  for (const sel of CONTROLS) assert.equal(await ed(page, sel).count(), 0, sel + ' gone for an event');
  const away = await save(page, writes);
  assert.equal(away.release, null);
  assert.equal((away.trigger as {type: string}).type, 'event');
  assert.equal('release' in db.list.find(a => a.id === 'au_daily')!, false);

  // A saved release opens as saved.
  await page.evaluate(() => { location.hash = '#/automations/au_weekly'; });
  await page.waitForSelector('.au-hd .au-name:text("Weekly review")');
  await openTrigger(page);
  assert.equal(await warm.isChecked(), true);
  assert.equal(await hours.inputValue(), '12');
  assert.equal(await ed(page, '[data-b="release.session"]').inputValue(), 'continue');
  await ed(page, '[data-op="close"]').click();

  // An event trigger with none: no controls, and its Save sends no release.
  await page.evaluate(() => { location.hash = '#/automations/au_notice'; });
  await page.waitForSelector('.au-hd .au-name:text("From Tico")');
  await openTrigger(page);
  for (const sel of CONTROLS) assert.equal(await ed(page, sel).count(), 0, sel + ' absent on an event');
  const ev = await save(page, writes);
  assert.equal('release' in ev, false, 'no release for an event');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    await journey(browser, DESKTOP);
    await journey(browser, PHONE);
    console.log('automations release ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
