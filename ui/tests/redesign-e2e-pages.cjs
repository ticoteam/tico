// Pages and Library › Files on a real hub (docs/pages.md): the Engineering Lead published a weekly page that replaced
// an older one, a launch plan on Dana's task and a signup funnel CSV (seed `pages`). Library › Files lists them, a kind
// filter and a search narrow it, a page opens full size with Tico's status line, a replaced page links on, the task
// shows the pages pinned to it at the top, the search box finds a file by its words, and the owner deletes a page for good. This hub
// has no visual address, so pages are Open cards; one framed page (its address faked) takes the height it reports,
// cut at a card's size. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, ok, api, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const FRAME = 'https://visuals.acme.example/visuals/acme-page.sig';
const TALL = '<!doctype html><html><body style="margin:0"><div style="height:600px;background:#dfe">Tall page</div>'
  + '<script>parent.postMessage({ticoVisualHeight: 600}, "*")</script></body></html>';

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
  const h = await rows.first().evaluate(el => el.getBoundingClientRect().height);
  assert.ok(h < 64, `${tag}: one dense row (${h}px)`);
  assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
  assert.ok((await page.locator('.lf-meta').allInnerTexts()).every(x => x.includes('Engineering Lead')), `${tag}: the bot's name`);
  assert.deepEqual(await page.locator('.lf-pins').allInnerTexts(), ['Pinned', 'Pinned'], `${tag}: one pin each, no count`);
  // The filters never run off the screen: they fit, or scroll sideways with the right edge faded.
  const frow = await page.locator('.lf-frow').evaluate(el => ({right: el.getBoundingClientRect().right, width: innerWidth,
    over: el.scrollWidth > el.clientWidth + 1, fade: el.classList.contains('more')}));
  assert.ok(frow.right <= frow.width && frow.over === frow.fade, `${tag}: filters fit or fade (${JSON.stringify(frow)})`);
  await shot(page, `${tag}-01-files`);

  // Team: the team's bots' files and the files on its tasks.
  const teams = await page.locator('.lf-team option').allInnerTexts();
  assert.ok(['Engineering', 'Release'].every(x => teams.includes(x)), `${tag}: the teams (${teams})`);
  await page.locator('.lf-team').selectOption({label: 'Release'});
  await page.locator('.lf-list', {hasText: 'Nothing matches.'}).waitFor({timeout: t(5000)});
  await page.locator('.lf-team').selectOption({label: 'Engineering'});
  await page.waitForFunction(() => location.hash.includes('team=') && document.querySelectorAll('.lf-row').length === 4,
    null, {timeout: t(5000)});
  await page.locator('.lf-team').selectOption('');

  // Kind narrows the list and rides in the address.
  await page.locator('.lf-kind').selectOption('page');
  await page.waitForFunction(() => location.hash.includes('kind=page') && document.querySelectorAll('.lf-row').length === 3,
    null, {timeout: t(5000)});
  assert.ok(!(await page.locator('.lf-title').allInnerTexts()).includes('Signup funnel'), `${tag}: no CSV under Pages`);
  await page.locator('.lf-kind').selectOption('');

  // Search finds a CSV by a word in its header, with an excerpt.
  await page.locator('.lf-q').fill('visitors');
  await page.waitForFunction(() => document.querySelectorAll('.lf-row').length === 1
    && /visitors/.test(document.querySelector('.lf-excerpt')?.textContent || ''), null, {timeout: t(5000)});
  assert.equal(await page.locator('.lf-title').innerText(), 'Signup funnel');
  await shot(page, `${tag}-02-search`);
  await page.locator('.lf-q').fill('');

  // A page opens full size: Tico's status line above it, the page in the sandboxed preview (no visual address here).
  const pages = (await ok(page, 'GET', 'pages?limit=10')).pages;
  const weekly = pages.find(p => p.title === 'Engineering weekly'), old = pages.find(p => p.title === 'Engineering weekly (old)');
  const launch = pages.find(p => p.title === 'Launch plan');
  assert.equal(weekly.src, null, 'no visual address on this hub');
  await rows.filter({has: page.locator('.lf-title', {hasText: /^Engineering weekly$/})}).click();
  await page.waitForFunction(id => location.hash === '#/page/' + id, weekly.file_id, {timeout: t(5000)});
  const status = page.locator('#pg-view .pg-status');
  await status.waitFor({timeout: t(5000)});
  assert.match(await status.innerText(), /^Updated .+ by Engineering Lead · v1 · weekly · data from Oct 27$/, `${tag}: the status line`);
  await page.locator('#pg-view .pg-preview iframe.html-view').waitFor({timeout: t(10000)});
  const shown = await page.locator('#pg-view iframe.html-view').evaluate(f => f.srcdoc);
  assert.ok(shown.includes('Shipped 14 changes') && !shown.includes('steal()'), `${tag}: the cleaned page, no script`);
  await page.locator('#pg-view .pg-menu summary').click();
  assert.deepEqual(await page.locator('#pg-view .pg-menu-list [role=menuitem]').allInnerTexts(),
    ['Archive', 'Download', 'Delete for good'], `${tag}: the owner's menu`);
  await page.locator('#pg-view .pg-menu summary').click();
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-03-page`);

  // The older page says what replaced it and links there.
  await go(page, '#/page/' + old.file_id);
  const replaced = page.locator('#pg-view .pg-replaced a');
  await replaced.waitFor({timeout: t(5000)});
  assert.equal(await replaced.innerText(), 'Engineering weekly');
  await replaced.click();
  await page.waitForFunction(id => location.hash === '#/page/' + id, weekly.file_id, {timeout: t(5000)});

  // A page filed on the task is pinned to its top; the old weekly, filed there too, is left out as replaced. A card
  // opens its page, which says where it is pinned.
  await go(page, '#/task/' + launch.task_id);
  const cards = page.locator('.task-pages .pg-card');
  await cards.first().waitFor({timeout: t(10000)})
    .catch(() => assert.fail(`${tag}: a page card on the task (${JSON.stringify(page.errors)})`));
  assert.deepEqual(await cards.locator('.pg-open-title').allInnerTexts(), ['Launch plan']);
  // Its status line is inside the card, under the page's title, not loose under the task's.
  assert.match(await cards.locator('.pg-open .pg-status').innerText(), /^Updated .+ by Engineering Lead · v1$/, `${tag}: status in the card`);
  assert.equal(await cards.locator(':scope > .pg-status').count(), 0, `${tag}: no loose status line`);
  await shot(page, `${tag}-04-task`);
  await cards.locator('a.pg-open', {hasText: 'Launch plan'}).click();
  await page.waitForFunction(id => location.hash === '#/page/' + id, launch.file_id, {timeout: t(5000)});
  const pinned = page.locator('#pg-view .pg-pinned a');
  await pinned.waitFor({timeout: t(5000)});
  assert.equal(await pinned.innerText(), 'Q3 launch');
  assert.equal(await pinned.getAttribute('href'), '#/task/' + launch.task_id);

  // The search box finds files by their words.
  // On a phone with threads, Threads takes Search's place in the bottom bar (ui/app/threads.js): open it as the menu does.
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
    && !document.querySelector('#search-modal').open, null, {timeout: t(5000)});
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
    await page.waitForFunction(() => document.querySelector('#pg-view iframe.page-frame')?.style.height === '600px', null, {timeout: t(5000)});
    const small = await page.evaluate(([p, src]) => {
      const card = pageCard({...p, src}, {size: 'small'});
      card.id = 'probe'; document.querySelector('#pg-view').append(card);
      return {tap: !!card.querySelector('a.pg-tap'),
        bar: card.classList.contains('pg-framed') && card.firstElementChild.classList.contains('pg-status')};
    }, [weekly, FRAME]);
    assert.ok(small.tap, 'a card opens its page on a tap');
    assert.ok(small.bar, 'a framed card carries its status line as its top bar');
    await page.waitForFunction(() => document.querySelector('#probe iframe.page-frame')?.style.height === '180px', null, {timeout: t(5000)});
    await shot(page, `${tag}-06-framed`);
    await page.unrouteAll({behavior: 'wait'});

    // A member who neither manages the bot nor owns the team gets neither Archive nor Delete.
    const priya = await open(browser, hub, 'priya', {phone, hash: '#/page/' + weekly.file_id});
    await priya.locator('#pg-view .pg-status').waitFor({timeout: t(5000)});
    await priya.locator('#pg-view .pg-menu summary').click();
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
