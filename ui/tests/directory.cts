// Library › Contacts (it was Library › Directory) on a real hub seeded with the scenario org and its contacts
// (evals/redesign/seeds.py "directory", the same rows as the demo): the list → a person → their organization → a place,
// on a desktop and a phone. The Library list says Contacts, never Directory, and the old address lands on the new page. Set TICO_DIRECTORY_SHOTS to a folder to keep a screenshot of each page.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

const SHOTS = process.env.TICO_DIRECTORY_SHOTS || '';
const shot = async (page: Page, name: string) => { if (!SHOTS) return; await page.waitForTimeout(600); await page.screenshot({path: path.join(SHOTS, `directory-${name}.png`), fullPage: true}); };

async function flow(page: Page, tag: string) {
  await page.goto(page.url().split('#')[0] + '#/library/contacts');
  // Contacts is the icon on the Team header, not a Library row, and nothing is called Directory.
  // Each row's name without its icon's ligature text.
  const library = await page.locator('#rd-lib .nav-link').evaluateAll(links => links.map(a =>
    [...a.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent || '').join('').trim()));
  assert.ok(!library.some(name => /contacts|directory/i.test(name)), tag + ': no Contacts or Directory row in Library');
  assert.equal(await page.locator('#org-contacts-link').getAttribute('href'), '#/library/contacts', tag + ': the Team header opens Contacts');
  // The list: People first, the seeded people with their organization, relationship and where they are known from.
  const jane = page.locator('[data-dir-row="person/jane-ortiz"]');
  await jane.waitFor({timeout: t(20000)});
  assert.equal(await page.locator('[data-dir-tab].cur').getAttribute('data-dir-tab'), 'person');
  // Documents are files, not contacts: never a tab.
  const tabs = await page.locator('[data-dir-tab]').evaluateAll(tabs => tabs.map(a => a.dataset.dirTab));
  assert.deepEqual(tabs.slice(0, 3), ['person', 'organization', 'place']);
  assert.ok(!tabs.includes('document'), tag + ': no Documents tab');
  assert.match(await page.locator('#main h1').first().innerText(), /^Contacts$/);
  assert.match(await jane.innerText(), /Bright Homes Retail/);
  assert.match(await jane.locator('[data-dir-rel]').innerText(), /Customer/);
  assert.match(await page.locator('[data-dir-count]').innerText(), /^8 people$/);
  // Prefix search narrows the rows; the relationship menu narrows by a link to us, through the person's organization.
  await page.locator('[data-dir-search-toggle]').click();
  await page.locator('[data-dir-q]').fill('Ra');
  await page.waitForFunction(() => document.querySelectorAll('[data-dir-row]').length === 1, null, {timeout: t(10000)});
  assert.equal(await page.locator('[data-dir-row]').getAttribute('data-dir-row'), 'person/raj-patel');
  assert.match(page.url(), /q=Ra/);
  // The old address, from before it was Contacts, lands on the new page with its query.
  await page.goto(page.url().split('#')[0] + '#/library/directory?link=customer_of');
  await page.waitForFunction(() => location.hash === '#/library/contacts?link=customer_of', null, {timeout: t(10000)});
  // Customers' people (Bright Homes, Cedar Venues, Maple Street: backend/demo_sections/directory.py), in any order.
  await page.waitForFunction(() => [...(document.querySelectorAll('[data-dir-row]') as unknown as Iterable<HTMLElement>)].map(r => r.dataset.dirRow).sort().join()
    === 'person/dev-shah,person/jane-ortiz,person/priya-nair,person/sam-brooks', null, {timeout: t(10000)});
  await page.goto(page.url().split('#')[0] + '#/library/contacts');
  await jane.waitFor({timeout: t(10000)});
  await shot(page, `list-${tag}`);

  // The person: title at organization, the Customer chip, rules (none yet), a value's history, links, notes.
  await jane.locator('a').first().click();
  await page.locator('[data-dir-name]').waitFor({timeout: t(10000)});
  assert.equal(await page.locator('[data-dir-name]').innerText(), 'Jane Ortiz');
  assert.match(await page.locator('[data-dir-sub]').innerText(), /CFO at Bright Homes Retail · Austin/);
  await page.locator('[data-dir-rules-empty]').waitFor({timeout: t(10000)});
  assert.match(await page.locator('[data-dir-field="title"]').innerText(), /CFO/);
  await page.locator('[data-dir-history="title"]').click();
  await page.locator('[data-dir-history-row]').nth(1).waitFor({timeout: t(10000)});
  assert.deepEqual(await page.locator('[data-dir-history-row] strong').allInnerTexts(), ['CFO', 'Controller']);
  await shot(page, `history-${tag}`);
  await page.locator('.dir-drawer [data-dir-close]').click();
  assert.match(await page.locator('[data-dir-note-list]').innerText(), /sell-through/);
  // A note for the company shows at once.
  await page.locator('[data-dir-note-form] textarea').fill(`Prefers mail over calls (${tag}).`);
  await page.locator('[data-dir-note-form] button[type=submit]').click();
  await page.locator('[data-dir-note-list] li', {hasText: `(${tag})`}).waitFor({timeout: t(10000)});
  await shot(page, `person-${tag}`);

  // The organization: domain and place under the name, its people among the links.
  await page.locator('[data-dir-sub] a', {hasText: 'Bright Homes Retail'}).click();
  await page.waitForFunction(() => document.querySelector('[data-dir-name]')?.textContent === 'Bright Homes Retail', null, {timeout: t(10000)});
  assert.match(await page.locator('[data-dir-sub]').innerText(), /brighthomes\.example · Company · Austin/);
  assert.match(await page.locator('[data-dir-rel]').innerText(), /Customer/);
  assert.match(await page.locator('[data-dir-link-group="People"]').innerText(), /Jane Ortiz.*Sam Brooks/);
  await page.locator('[data-dir-rules-empty]').waitFor({timeout: t(10000)});
  await shot(page, `org-${tag}`);

  // A place from the shared tree: Austin, inside Texas, which we operate in.
  await page.locator('[data-dir-sub] a', {hasText: 'Austin'}).click();
  await page.waitForFunction(() => document.querySelector('[data-dir-name]')?.textContent === 'Austin', null, {timeout: t(10000)});
  await page.locator('[data-dir-shared]').waitFor({timeout: t(10000)});
  await page.locator('.dir-crumb a', {hasText: 'Texas'}).click();
  await page.waitForFunction(() => document.querySelector('[data-dir-name]')?.textContent === 'Texas', null, {timeout: t(10000)});
  await page.locator('[data-dir-operate]').waitFor({timeout: t(10000)});
  await page.locator('[data-dir-rules-empty]').waitFor({timeout: t(10000)});
  assert.match(await page.locator('[data-dir-inside]').innerText(), /Austin/);
  assert.match(await page.locator('[data-dir-why]').innerText(), /operates in/);
  await shot(page, `place-${tag}`);
  // Never the word the spec retired, on any page here.
  assert.doesNotMatch(await page.locator('#main').innerText(), /\bworld\b/i);
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured', 'directory']});
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    for (const phone of [false, true]) {
      const page = await pageAs(browser, hub, 'dana', {phone});
      try {
        await page.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
        await flow(page, phone ? 'phone' : 'desktop');
        if (phone) assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll on a phone');
        else {
          // Settings › Contacts: the types with their counts, and the shared places.
          await page.goto(page.url().split('#')[0] + '#/settings/contacts');
          await page.locator('[data-dirs-type="person"]').waitFor({timeout: t(20000)});
          assert.equal(await page.locator('[data-dirs-type="document"]').count(), 0, 'documents are not a contact type');
          assert.match(await page.locator('[data-dirs-type="person"]').innerText(), /8/);
          await page.locator('[data-dirs-field="title"]').count();
          await page.locator('[data-dirs-places-on]').waitFor({timeout: t(10000)});
          // Where we operate: the owner removes Texas, then finds it by name and adds it back (the phone pass needs it).
          const texas = page.locator('[data-dirs-place="place:us-tx"]');
          await texas.waitFor({timeout: t(10000)});
          await texas.locator('[data-dirs-unplace]').click();
          await texas.waitFor({state: 'detached', timeout: t(10000)});
          await page.locator('[data-dirs-place-q]').fill('Tex');
          await page.locator('[data-dirs-pick="place:us-tx"]').click();
          await texas.waitFor({timeout: t(10000)});
          await shot(page, 'settings-desktop');
          // Light theme too: the page uses theme tokens only.
          await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
          await page.goto(page.url().split('#')[0] + '#/library/contacts/' + encodeURIComponent('organization/bright-homes-retail'));
          await page.locator('[data-dir-rules-empty]').waitFor({timeout: t(10000)});
          await shot(page, 'org-light');
        }
      } finally {
        await page.context().close();
      }
    }
    // A member sees where we operate without the controls (the hub refuses their writes too).
    const member = await pageAs(browser, hub, 'omar');
    try {
      await member.waitForFunction(() => document.querySelector('#main')?.children.length! > 0, null, {timeout: t(20000)});
      await member.goto(member.url().split('#')[0] + '#/settings/contacts');
      await member.locator('[data-dirs-place="place:us-tx"]').waitFor({timeout: t(20000)});
      assert.equal(await member.locator('[data-dirs-unplace], [data-dirs-place-q]').count(), 0);
    } finally {
      await member.context().close();
    }
    console.log('contacts: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
