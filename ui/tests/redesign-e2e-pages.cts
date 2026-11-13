// Pages and Library › Files on a real hub (docs/pages.md): the Engineering Lead published a weekly page that replaced
// an older one, a launch plan on Dana's task and a signup funnel CSV (seed `pages`). Library › Files lists them, a kind
// filter and a search narrow it, a page opens full size with Tico's status line, a replaced page links on, the task
// shows the pages pinned to it at the top, the search box finds a file by its words, and the owner deletes a page for good. This hub
// has no visual address, so pages are plain rows; one framed page (its address faked) takes the height it reports in
// the full view, and a card holds its size's fixed height and fades the cut. Phone, then desktop.
import type {Page} from 'playwright';
import type * as R from './support/redesign-e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {run, open, go, ok, api, sideways, shot, t} = require('./support/redesign-e2e.cts') as R.Api;

// A page as the pages API lists it, and the page-side helpers the probe calls.
interface PageRecord {file_id: string; title: string; src: string | null; task_id: string}
declare function pageCard(p: PageRecord & {src: string}, opts: {size: string}): HTMLElement;
declare function searchOpen(): void;

const FRAME = 'https://visuals.acme.example/visuals/acme-page.sig';
const TALL = '<!doctype html><html><body style="margin:0"><div style="height:600px;background:#dfe">Tall page</div>'
  + '<script>parent.postMessage({ticoVisualHeight: 600}, "*")</script></body></html>';

// The filter popover (Library › Files): open it if it is shut, pick the item whose set and label match, or close it.
async function openFilters(page: Page) {
  if (!(await page.evaluate(() => document.getElementById('lf-filter-pop')!.matches(':popover-open')))) await page.locator('.lf-fbtn').click();
  await page.locator('#lf-filter-pop').waitFor({state: 'visible', timeout: t(5000)});
}
async function pick(page: Page, set: string, label: string) {
  await openFilters(page);
  await page.locator(`#lf-filter-pop [data-lf-set="${set}"]`, {hasText: label}).click();
}
async function closeFilters(page: Page) {
  if (!(await page.evaluate(() => document.getElementById('lf-filter-pop')!.matches(':popover-open')))) return;
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.getElementById('lf-filter-pop')!.matches(':popover-open'), null, {timeout: t(5000)});
}

run('redesign-e2e-pages', ['org', 'toured', 'groups', 'pages'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/library/files'});
  const rows = page.locator('.lf-row');
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 4, null, {timeout: t(15000)})
    .catch(() => assert.fail(`${tag}: four files listed (${JSON.stringify(page.errors)})`));
  // The first-result feedback question covers the phone's bottom bar; Dana skips it.
  const skip = page.locator('#feedback-ask:not([hidden]) [data-skip]');
  if (await skip.count()) await skip.click();
  const titles = await page.locator('.lf-title').allInnerTexts();
  for (const want of ['Engineering weekly', 'Engineering weekly (old)', 'Launch plan', 'Signup funnel'])
    assert.ok(titles.includes(want), `${tag}: lists ${want} (${titles})`);
  if (!phone) await page.locator('#side a[data-nav="files"].cur').waitFor({state: 'attached', timeout: t(5000)});
  // A computer lists a file on one line; a phone gives each file two (the name, then Kind · Bot · time).
  const h = await rows.first().evaluate(el => el.getBoundingClientRect().height);
  if (phone) assert.ok(h >= 48 && h <= 76, `${tag}: two lines a row (${h}px)`);
  else assert.ok(h >= 36 && h <= 44, `${tag}: one line a row (${h}px)`);
  assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
  const bots = await page.locator(phone ? '.lf-meta' : '.lf-by-n').allInnerTexts();
  assert.ok(bots.every(x => x.includes('Engineering Lead')), `${tag}: the bot's name`);
  assert.deepEqual(await page.locator('.lf-pins').allInnerTexts(), ['Pinned', 'Pinned'], `${tag}: one pin each, no count`);
  // The search and the filter button share the top row.
  const q = (await page.locator('.lf-q').boundingBox())!, f = (await page.locator('.lf-fbtn').boundingBox())!;
  assert.ok(Math.abs(q.y + q.height / 2 - (f.y + f.height / 2)) < 4, `${tag}: search and filter on one row`);
  await shot(page, `${tag}-01-files`);

  // Team: the team's bots' files and the files on its tasks.
  await openFilters(page);
  const teams = await page.locator('#lf-filter-pop [data-lf-set="team"]').allInnerTexts();
  assert.ok(['Engineering', 'Release'].every(x => teams.some(y => y.includes(x))), `${tag}: the teams (${teams})`);
  await pick(page, 'team', 'Release');
  await page.locator('.lf-list', {hasText: 'Nothing matches.'}).waitFor({timeout: t(5000)});
  await pick(page, 'team', 'Engineering');
  await page.waitForFunction(() => location.hash.includes('team=') && document.querySelectorAll('.lf-row').length === 4,
    null, {timeout: t(5000)});
  await pick(page, 'team', 'All teams');
  await page.waitForFunction(() => !location.hash.includes('team=') && document.querySelectorAll('.lf-row').length === 4,
    null, {timeout: t(5000)});

  // Kind narrows the list and rides in the address.
  await pick(page, 'kind', 'Pages');
  await page.waitForFunction(() => location.hash.includes('kind=page') && document.querySelectorAll('.lf-row').length === 3,
    null, {timeout: t(5000)});
  assert.ok(!(await page.locator('.lf-title').allInnerTexts()).includes('Signup funnel'), `${tag}: no CSV under Pages`);
  await pick(page, 'kind', 'All kinds');
  await closeFilters(page);

  // Search finds a CSV by a word in its header, with an excerpt.
  await closeFilters(page);
  await page.locator('.lf-q').fill('visitors');
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 1
    && /visitors/.test(document.querySelector('.lf-excerpt')?.textContent || ''), null, {timeout: t(5000)});
  assert.equal(await page.locator('.lf-title').innerText(), 'Signup funnel');
  await shot(page, `${tag}-02-search`);
  await page.locator('.lf-q').fill('');

  // A page opens full size: Tico's status line above it, the page in the sandboxed preview (no visual address here).
  const pages = (await ok<{pages: PageRecord[]}>(page, 'GET', 'pages?limit=10')).pages;
  const weekly = pages.find(p => p.title === 'Engineering weekly')!, old = pages.find(p => p.title === 'Engineering weekly (old)')!;
  const launch = pages.find(p => p.title === 'Launch plan')!;
  assert.equal(weekly.src, null, 'no visual address on this hub');
  await rows.filter({has: page.locator('.lf-title', {hasText: /^Engineering weekly$/})}).click();
  await page.waitForFunction((id: string) => location.hash === '#/page/' + id, weekly.file_id, {timeout: t(5000)});
  const status = page.locator('#pg-view .pg-status');
  await status.waitFor({timeout: t(5000)});
  assert.match(await status.innerText(), /^Updated .+ by Engineering Lead · v1 · weekly · data from Oct 27$/, `${tag}: the status line`);
  await page.locator('#pg-view .pg-preview iframe.html-view').waitFor({timeout: t(10000)});
  const shown = await page.locator('#pg-view iframe.html-view').evaluate((f: HTMLIFrameElement) => f.srcdoc);
  assert.ok(shown.includes('Shipped 14 changes') && !shown.includes('steal()'), `${tag}: the cleaned page, no script`);
  // One header line: back, title, the status line and ⋯; no sharing row or version picker; the page right below.
  assert.equal(await page.locator('#pg-view .pg-head .pg-headline .pg-status').count(), 1, `${tag}: the status line is on the header line`);
  assert.equal(await page.locator('#pg-view .pg-access, #pg-view .pg-ver, #pg-view .pg-body .pg-status').count(), 0, `${tag}: no sharing row, version picker or second status line`);
  assert.equal(await page.locator('#pg-view > .pg-head + .pg-body').count(), 1, `${tag}: the page starts below the header`);
  // One frame: no border, corner or white backdrop around the page.
  const bare = await page.locator('#pg-view .pg-preview').evaluate(box => [box, box.querySelector('iframe')!].map(el => {
    const c = getComputedStyle(el);
    return [c.borderTopWidth, c.borderTopLeftRadius, c.backgroundColor];
  }));
  assert.deepEqual(bare, [['0px', '0px', 'rgba(0, 0, 0, 0)'], ['0px', '0px', 'rgba(0, 0, 0, 0)']], `${tag}: no box around the page`);
  // ⋯ stays at the header's right edge.
  const head = (await page.locator('#pg-view .pg-head').boundingBox())!, more = (await page.locator('#pg-view .pg-menu summary').boundingBox())!;
  assert.ok(Math.abs(head.x + head.width - (more.x + more.width)) < 4, `${tag}: ⋯ on the right`);
  // A manager's first item is who can see it, with the audience under it; a page of one version has no history or diff.
  await page.locator('#pg-view .pg-menu summary').click();
  const menuNames = () => page.locator('#pg-view .pg-menu-list [role^=menuitem]').evaluateAll(els => els.map(el => (el.querySelector('.pg-mi-name') || el).textContent!.trim()));
  assert.deepEqual(await menuNames(), ['Who can see it', 'Archive', 'Download', 'Delete for good'], `${tag}: the owner's menu`);
  assert.equal(await page.locator('#pg-view [data-pg-act=access] .pg-mi-sub').innerText(), "Everyone who can see where it's filed");
  await page.locator('#pg-view .pg-menu summary').click();
  // Keyboard: Enter on ⋯ opens the menu at its first item, the arrows walk it, Esc closes it and returns to ⋯.
  await page.locator('#pg-view .pg-menu summary').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.activeElement?.matches('#pg-view .pg-menu-list [data-pg-act=access]'), null, {timeout: t(5000)});
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Archive', `${tag}: the arrows move through the menu`);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('#pg-view .pg-menu')!.hasAttribute('open')
    && document.activeElement?.matches('#pg-view .pg-menu summary'), null, {timeout: t(5000)});
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-03-page`);

  // Five versions (served here, as a bot would have published them): Version history and What's changed? in ⋯.
  const html = (body: string, style = '') => `<!doctype html><html><head><style>${style}</style></head><body><h2>Engineering weekly</h2>${body}</body></html>`;
  const bodies: Record<number, string> = {
    1: html('<p>Shipped 9 changes.</p>'),
    2: html('<p>Shipped 11 changes.</p><ul><li>Faster review</li></ul>'),
    3: html('<p>Shipped 11 changes.</p><ul><li>Faster review</li></ul>', 'p{color:red}'),
    4: html('<p>Shipped 12 changes.</p><ul><li>Faster review</li></ul><table><tr><td>Review</td><td>6h</td></tr></table>'),
    5: html('<p>Shipped 14 changes.</p><ul><li>Faster review</li><li>Fewer reverts</li></ul><table><tr><td>Review</td><td>6h</td></tr></table>'),
  };
  const versions = [5, 4, 3, 2, 1].map(v => ({version: v, current: v === 5, actor: 'bot:engineering-manager', created_at: `2026-11-0${v}T10:00:00Z`}));
  await page.route(url => new URL(url).pathname.endsWith('/api/v2/pages/' + weekly.file_id), async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({response, json: {...body, page: {...body.page, version: 5}}});
  });
  await page.route(url => new URL(url).pathname.endsWith(`/api/v2/files/${weekly.file_id}/versions`), route => route.fulfill({json: {versions}}));
  await page.route(url => { const u = new URL(url); return u.pathname.endsWith('/api/v2/files/' + weekly.file_id) && u.searchParams.has('v'); },
    route => route.fulfill({contentType: 'text/html', headers: {'content-disposition': 'inline; filename="weekly.html"'},
      body: bodies[Number(new URL(route.request().url()).searchParams.get('v'))] || ''}));
  await go(page, '#/library/files');
  await go(page, '#/page/' + weekly.file_id);
  await page.locator('#pg-view .pg-preview iframe.html-view').waitFor({timeout: t(10000)});
  await page.locator('#pg-view .pg-menu summary').click();
  await page.waitForFunction(() => document.querySelector('#pg-view [data-pg-act=history]'), null, {timeout: t(5000)});
  assert.deepEqual(await menuNames(), ['Who can see it', "What's changed?", 'Version history', 'Archive', 'Download', 'Delete for good'], `${tag}: a paged page's menu`);
  await page.locator('#pg-view [data-pg-act=history]').click();
  assert.deepEqual(await menuNames(), ['Version history', 'v5 · latest', 'v4', 'v3', 'v2', 'v1'], `${tag}: every version, newest first`);
  assert.equal(await page.locator('#pg-view [data-pg-v="3"] .pg-mi-sub').innerText(), 'Nov 3 · Engineering Lead', `${tag}: a version's date and publisher`);
  await page.locator('#pg-view [data-pg-v="3"]').click();
  await page.waitForFunction(() => document.querySelector('#pg-view .pg-head .pg-status')?.textContent === 'v3 · an older version · Latest', null, {timeout: t(5000)})
    .catch(async () => assert.fail(`${tag}: v3's status line (${await status.innerText()})`));
  await page.waitForFunction(() => /Shipped 11/.test(document.querySelector<HTMLIFrameElement>('#pg-view iframe.html-view')?.srcdoc || ''), null, {timeout: t(5000)});
  // The oldest version has nothing to compare with: no What's changed?, Version history still there.
  await page.locator('#pg-view .pg-menu summary').click();
  await page.locator('#pg-view [data-pg-act=history]').click();
  await page.locator('#pg-view [data-pg-v="1"]').click();
  await page.waitForFunction(() => document.querySelector('#pg-view .pg-head .pg-status')?.textContent === 'v1 · an older version · Latest', null, {timeout: t(5000)});
  assert.equal(await page.locator('#pg-view [data-pg-act=diff]').count(), 0, `${tag}: no What's changed? on the oldest version`);
  assert.equal(await page.locator('#pg-view [data-pg-act=history]').count(), 1, `${tag}: Version history on the oldest version`);
  await page.locator('#pg-view [data-pg-act=latest]').click();
  await page.waitForFunction(() => /· v5/.test(document.querySelector('#pg-view .pg-head .pg-status')?.textContent || ''), null, {timeout: t(5000)});

  // What's changed? on v5: v4 → v5, the removed line red with −, the added green with +; v2 → v5 on a pick; Esc closes.
  await page.locator('#pg-view .pg-menu summary').click();
  await page.locator('#pg-view [data-pg-act=diff]').click();
  const diffLines = page.locator('#pg-view .pg-diff .tf-diff-lines');
  await diffLines.waitFor({timeout: t(5000)});
  assert.equal(await page.locator('#pg-view .pg-diff-from').inputValue(), '4');
  assert.equal(await page.locator('#pg-view .pg-diff-to').innerText(), 'v5');
  assert.deepEqual(await diffLines.locator('.del').allInnerTexts(), ['- Shipped 12 changes.'], `${tag}: the removed line`);
  assert.deepEqual(await diffLines.locator('.add').allInnerTexts(), ['+ Shipped 14 changes.', '+ Fewer reverts'], `${tag}: the added lines, one per list item`);
  assert.ok((await diffLines.locator('.ctx').allInnerTexts()).some(x => x.includes('Review | 6h')), `${tag}: a table row is one line`);
  await shot(page, `${tag}-03b-diff`);
  await page.locator('#pg-view .pg-diff-from').selectOption('2');
  await page.waitForFunction(() => /Shipped 11 changes/.test(document.querySelector('#pg-view .pg-diff .del')?.textContent || ''), null, {timeout: t(5000)});
  await page.keyboard.press('Escape');
  await page.locator('#pg-view .pg-preview iframe.html-view').waitFor({timeout: t(5000)});
  assert.equal(await page.locator('#pg-view .pg-diff').count(), 0, `${tag}: Esc returns to the page`);
  // A version that changed only its styles has no text changes; Close returns to the page.
  await page.locator('#pg-view .pg-menu summary').click();
  await page.locator('#pg-view [data-pg-act=history]').click();
  await page.locator('#pg-view [data-pg-v="3"]').click();
  await page.locator('#pg-view .pg-menu summary').click();
  await page.locator('#pg-view [data-pg-act=diff]').click();
  await page.locator('#pg-view .pg-diff-lines', {hasText: 'No text changes'}).waitFor({timeout: t(5000)});
  await page.locator('#pg-view [data-pg-diff-close]').click();
  await page.locator('#pg-view .pg-preview iframe.html-view').waitFor({timeout: t(5000)});
  assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
  await page.locator('#pg-view .pg-menu summary').click();
  await shot(page, `${tag}-03c-menu`);
  await page.keyboard.press('Escape');
  await page.unrouteAll({behavior: 'wait'});

  // The older page says what replaced it and links there.
  await go(page, '#/page/' + old.file_id);
  const replaced = page.locator('#pg-view .pg-replaced a');
  await replaced.waitFor({timeout: t(5000)});
  assert.equal(await replaced.innerText(), 'Engineering weekly');
  await replaced.click();
  await page.waitForFunction((id: string) => location.hash === '#/page/' + id, weekly.file_id, {timeout: t(5000)});

  // A page filed on the task is one row in its Pages (task #409), its icon and name; the old weekly, filed there too, is
  // left out as replaced. The row opens its page, which says where it is pinned.
  await go(page, '#/task/' + launch.task_id);
  // A phone shows the pages in the Pages tab: wait for the window to draw before reading whether the tabs show.
  const tab = page.locator('#task-modal[open] [data-task-tab="pages"]');
  await tab.waitFor({state: 'attached', timeout: t(10000)});
  if (await tab.isVisible()) await tab.click();
  const cards = page.locator('.task-pages .tp-row');
  await cards.first().waitFor({timeout: t(10000)})
    .catch(() => assert.fail(`${tag}: a page row on the task (${JSON.stringify(page.errors)})`));
  assert.deepEqual(await cards.locator('.tp-name').allInnerTexts(), ['Launch plan']);
  assert.equal(await page.locator('.task-pages .pg-status').count(), 0, `${tag}: no Updated … · v1 line on the row`);
  await shot(page, `${tag}-04-task`);
  await cards.filter({hasText: 'Launch plan'}).click();
  await page.waitForFunction((id: string) => location.hash === '#/page/' + id, launch.file_id, {timeout: t(5000)});
  const pinned = page.locator('#pg-view .pg-pinned a');
  await pinned.waitFor({timeout: t(5000)});
  assert.equal(await pinned.innerText(), 'Q3 launch');
  assert.equal(await pinned.getAttribute('href'), '#/task/' + launch.task_id);

  // The search box finds files by their words.
  // On a phone with threads, Threads takes Search's place in the bottom bar (ui/app/threads.ts): open it as the menu does.
  if (phone && await page.locator('#mobile-search').isHidden()) await page.evaluate(() => searchOpen());
  else await page.locator(phone ? '#mobile-search' : '#search-open').click();
  await page.locator('#search-input').fill('funnel');
  const hit = page.locator('#search-results li', {hasText: 'Signup funnel'});
  await hit.waitFor({timeout: t(5000)});
  assert.match(await hit.innerText(), /file/);
  await shot(page, `${tag}-05-search-box`);
  // A file that is not a page opens Library › Files searched for the same words.
  await hit.click();
  await page.waitForFunction(() => location.hash.startsWith('#/library/files') && location.hash.includes('q=funnel')
    && !document.querySelector<HTMLDialogElement>('#search-modal')!.open, null, {timeout: t(5000)});
  await page.locator('.lf-row', {hasText: 'Signup funnel'}).waitFor({timeout: t(5000)});

  if (!phone) {
    // A framed page takes the height it reports in the full view, and a small card cuts it at its own height.
    await page.route('**/api/v2/pages/' + weekly.file_id, async route => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({response, json: {...body, page: {...body.page, src: FRAME}}});
    });
    await page.route(FRAME, route => route.fulfill({contentType: 'text/html', body: TALL}));
    await go(page, '#/page/' + weekly.file_id);
    const frame = page.locator('#pg-view iframe.page-frame');
    await frame.waitFor({timeout: t(5000)});
    assert.equal(await frame.getAttribute('sandbox'), 'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    await page.waitForFunction(() => document.querySelector<HTMLElement>('#pg-view iframe.page-frame')?.style.height === '600px', null, {timeout: t(5000)});
    assert.equal(await frame.getAttribute('src'), FRAME + '#tico-view=full&tico-size=full', 'the full view says full');
    const small = await page.evaluate(([p, src]: [PageRecord, string]) => {
      const card = pageCard({...p, src}, {size: 'small'});
      card.id = 'probe'; document.querySelector('#pg-view')!.append(card);
      return {tap: !!card.querySelector('a.pg-tap'), expand: card.querySelector('a.pg-expand')?.getAttribute('href'),
        bar: card.classList.contains('pg-framed') && card.firstElementChild!.classList.contains('pg-status'),
        src: card.querySelector('iframe')!.getAttribute('src')};
    }, [weekly, FRAME] as [PageRecord, string]);
    assert.ok(small.tap, 'a card opens its page on a tap');
    assert.equal(small.expand, '#/page/' + weekly.file_id, 'its expand icon opens the full view');
    assert.ok(small.bar, 'a framed card carries its status line as its top bar');
    assert.equal(small.src, FRAME + '#tico-view=card&tico-size=small', 'the frame says it is in a small card');
    // The 600px page is cut at the card's fixed 180, with the fade.
    await page.waitForFunction(() => document.querySelector<HTMLElement>('#probe iframe.page-frame')?.style.height === '180px'
      && document.querySelector('#probe .pg-box')?.classList.contains('pg-cut'), null, {timeout: t(5000)});
    await shot(page, `${tag}-06-framed`);
    await page.unrouteAll({behavior: 'wait'});

    // A member who neither manages the bot nor owns the team gets neither Archive nor Delete.
    const priya = await open(browser, hub, 'priya', {phone, hash: '#/page/' + weekly.file_id});
    await priya.locator('#pg-view .pg-status').waitFor({timeout: t(5000)});
    await priya.locator('#pg-view .pg-menu summary').click();
    // Neither Who can see it nor Archive for someone who cannot manage the page.
    assert.deepEqual(await priya.locator('#pg-view .pg-menu-list [role=menuitem]').allInnerTexts(), ['Download']);
    assert.deepEqual(priya.errors, []);
    await priya.context().close();
  }

  // The owner deletes the old page for good: gone from the list and from the API.
  await go(page, '#/page/' + old.file_id);
  await page.locator('#pg-view .pg-replaced').waitFor({timeout: t(5000)});
  await page.locator('#pg-view .pg-menu summary').click();
  page.once('dialog', d => d.accept());
  await page.locator('#pg-view [data-pg-act="delete"]').click();
  await page.waitForFunction(() => location.hash.startsWith('#/library/files'), null, {timeout: t(5000)});
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 3, null, {timeout: t(10000)});
  assert.equal((await api(page, 'GET', 'pages/' + old.file_id)).status, 404, `${tag}: the deleted page is gone`);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
