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
  // Contacts is a Library row, current on its page, and nothing is called Directory.
  // Each row's name without its icon's ligature text.
  const library = await page.locator('#rd-lib .nav-link').evaluateAll(links => links.map(a =>
    [...a.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent || '').join('').trim()));
  assert.ok(library.includes('Contacts') && !library.some(name => /directory/i.test(name)), tag + ': Contacts is a Library row, and no Directory');
  const row = page.locator('#side a[data-nav="contacts"]');
  assert.equal(await row.getAttribute('href'), '#/library/contacts', tag + ': the Library row opens Contacts');
  await page.locator('#side a[data-nav="contacts"].cur').waitFor({state: 'attached', timeout: t(10000)});
  assert.equal(await row.getAttribute('aria-current'), 'page', tag + ': the row is current on Contacts');
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
  // The relationship is plain muted words, not a pill; the count is the tab's, with no "8 people" line.
  assert.equal(await page.locator('.dir-list .pill').count(), 0, tag + ': no pills in the list');
  assert.equal(await page.locator('[data-dir-count]').count(), 0, tag + ': no count caption');
  assert.equal((await page.locator('[data-dir-tab="person"] .ph-n').innerText()).trim(), '8', tag + ': the People tab counts them');
  // A column no row has a value for is not drawn (the seeded people have no owner, last contact or source).
  const heads = await page.locator('.dir-list thead th').allInnerTexts();
  assert.deepEqual(heads.map(h => h.trim().toLowerCase()), ['name', 'organization', 'relationship'], tag + ': only the columns with values');
  // The one page header: the tabs on it, the search and the relationship filter as its tools, ⋯ last.
  assert.equal(await page.locator('#dir-root > header.ph [data-dir-tab]').count() > 0, true, tag + ': the tabs are in the header');
  // Prefix search narrows the rows; the relationship menu narrows by a link to us, through the person's organization.
  // A phone may keep the tools in ⋯: open it first (as filterReach does in mail-messaging-pages).
  if (!(await page.locator('[data-dir-q]').isVisible())) await page.locator('#dir-root > header.ph > .ph-more').click();
  await page.locator('[data-dir-q]').fill('Ra');
  await page.waitForFunction(() => document.querySelectorAll('[data-dir-row]').length === 1, null, {timeout: t(10000)});
  assert.equal(await page.locator('[data-dir-row]').getAttribute('data-dir-row'), 'person/raj-patel');
  // The search reads "Search", as every header search does; its name starts with that word and says what it matches.
  assert.equal(await page.locator('[data-dir-q]').getAttribute('placeholder'), 'Search', tag + ': the placeholder');
  assert.equal(await page.locator('[data-dir-q]').getAttribute('aria-label'), 'Search by name', tag + ': label in name');
  // A narrowed list keeps the columns the whole list had (no column jumps while typing).
  assert.deepEqual((await page.locator('.dir-list thead th').allInnerTexts()).map(h => h.trim().toLowerCase()), ['name', 'organization', 'relationship'], tag + ': columns stay');
  assert.match(page.url(), /q=Ra/);
  // The relationship filter: the shared filter icon opens the shared menu; a pick narrows the list and shows a chip.
  await page.goto(page.url().split('#')[0] + '#/library/contacts');
  await jane.waitFor({timeout: t(10000)});
  if (!(await page.locator('[data-dir-filter]').isVisible())) await page.locator('#dir-root > header.ph > .ph-more').click();
  await page.locator('[data-dir-filter]').click();
  await page.locator('#task-filter-pop a[data-dir-link="vendor_to"]').click();
  await page.waitForFunction(() => location.hash === '#/library/contacts?link=vendor_to', null, {timeout: t(10000)});
  await page.locator('.dir-active .tl-chip').filter({hasText: 'Vendor'}).waitFor({timeout: t(10000)});
  // The chip counts the matches (the tab keeps the whole type's count).
  await page.waitForFunction(() => document.querySelector('[data-dir-chip-n]')?.textContent === String(document.querySelectorAll('[data-dir-row]').length), null, {timeout: t(10000)});
  await page.locator('[data-dir-clear-link]').click();
  await page.waitForFunction(() => location.hash === '#/library/contacts', null, {timeout: t(10000)});
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
  // The contact's page has the one page header: "Contacts ›" back to its list, the name as the title, ⋯ last; no breadcrumb.
  const name = page.locator('#dir-root > header.ph > .ph-t');
  await page.locator('[data-dir-sub]').waitFor({timeout: t(10000)});
  assert.equal(await name.innerText(), 'Jane Ortiz');
  assert.equal((await page.locator('#dir-root > header.ph > .ph-back').innerText()).replace(/\s+/g, ''), 'Contacts›');
  assert.equal(await page.locator('#dir-root > header.ph > .ph-back').getAttribute('href'), '#/library/contacts');
  assert.equal(await page.locator('.dir-crumb, #dir-root h1:not(.ph-t)').count(), 0, tag + ': no separate breadcrumb or second title');
  // Where a value came from is muted words on its row ("You · Nov 11"), not a pill.
  assert.equal(await page.locator('[data-dir-field="title"] .pill').count(), 0, tag + ': provenance is not a pill');
  const meta = await page.locator('[data-dir-field="title"] .dir-meta').evaluate(m => {
    const mid = (e: Element) => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; };
    const v = mid(m.parentElement!.querySelector('.dir-val')!), src = mid(m.querySelector('[data-dir-src]')!), age = mid(m.querySelector('.dir-age')!);
    return {text: (m.textContent || '').trim(), same: Math.abs(src - v) < 4 && Math.abs(age - v) < 4};
  });
  assert.match(meta.text, /^\S.* · \S/, tag + ': source · age');
  assert.ok(meta.same, tag + `: provenance on the value's row, one line (${JSON.stringify(meta)})`);
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
  await page.waitForFunction(() => document.querySelector('#dir-root > header.ph > .ph-t')?.textContent === 'Bright Homes Retail', null, {timeout: t(10000)});
  assert.match(await page.locator('[data-dir-sub]').innerText(), /brighthomes\.example · Company · Austin/);
  assert.match(await page.locator('[data-dir-rel]').innerText(), /Customer/);
  assert.match(await page.locator('[data-dir-link-group="People"]').innerText(), /Jane Ortiz.*Sam Brooks/);
  await page.locator('[data-dir-rules-empty]').waitFor({timeout: t(10000)});
  await shot(page, `org-${tag}`);

  // A place from the shared tree: Austin, inside Texas, which we operate in.
  await page.locator('[data-dir-sub] a', {hasText: 'Austin'}).click();
  await page.waitForFunction(() => document.querySelector('#dir-root > header.ph > .ph-t')?.textContent === 'Austin', null, {timeout: t(10000)});
  await page.locator('[data-dir-shared]').waitFor({timeout: t(10000)});
  await page.locator('[data-dir-sub] a', {hasText: 'Texas'}).click();   // the place it is in, on its identity line
  await page.waitForFunction(() => document.querySelector('#dir-root > header.ph > .ph-t')?.textContent === 'Texas', null, {timeout: t(10000)});
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
