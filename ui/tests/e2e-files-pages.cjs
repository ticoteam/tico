// Journey: bot files and pages, and Make page, as Dana and Priya. The Engineering Lead's pages and files (the pages seed) show in
// Library > Files with their titles; a page opens and draws its title, its version and its status line; a reader links a page to a
// task (filesPages). Then Make page's task box and progress card, which were make-page.cjs (makePage; the request and pin use a real hub,
// the authenticated publish and durable status contracts are in backend/tests/test_make_page.py). Runs on a phone and a desktop.
const assert = require('node:assert/strict');
const {journey, open, go, call, must, expectFailure, assertClean, drawn, shot, t, SEEDS} = require('./support/e2e.cjs');


async function filesPages({hub, browser, phone}) {
    const where = phone ? 'phone' : 'desktop';
    const page = await open(browser, hub, 'dana', {phone, hash: '#/library/files'});
    for (const title of ['Launch plan', 'Engineering weekly', 'Signup funnel']) {
      await page.locator('.lf-row', {hasText: title}).first().waitFor({timeout: t(20000)});
    }
    assertClean(page, `${where}: files listed`);

    // The bot's data file: its row draws its title, its kind and the bot that made it, and the hub serves its content
    // (a data file has no page view; its row links to the file itself).
    const csv = page.locator('.lf-row', {hasText: 'Signup funnel'}).first();
    assert.match((await csv.innerText()).replace(/\s+/g, ' '), /Data · Engineering Lead/, `${where}: the data file names its kind and bot`);
    const file = await call(hub, 'dana', 'GET', `files/${await csv.getAttribute('data-id')}`);
    assert.equal(file.status, 200, `${where}: the hub serves the data file`);
    assert.match(String(file.body), /landing,1200/, `${where}: the data file's rows are served`);

    // The replaced page ("Engineering weekly (old)") is listed too; the current one has the exact title.
    await page.locator('.lf-row', {has: page.locator('.lf-title', {hasText: /^\s*Engineering weekly\s*$/})}).first()
      .locator('.lf-title, a').first().click();
    const status = (await drawn(page, '#pg-view .pg-status', text => /v\d+/.test(text), `${where}: the page draws its version`))
      .replace(/\s+/g, ' ').trim();
    assert.match(status, /v\d+/, `${where}: the page shows its version`);
    const view = await drawn(page, '#pg-view', text => text.includes('Engineering weekly'), `${where}: the page draws its title`);
    assert.match(view, /Engineering weekly/, `${where}: the page shows its title`);
    const audienceReader = await open(browser, hub, 'priya', {phone, hash: new URL(page.url()).hash});
    await audienceReader.locator('#pg-view .pg-title').waitFor();
    assert.equal(await audienceReader.locator('[data-pg-audience]').count(), 0);
    expectFailure(audienceReader, 404, /^\/api\/v2\/pages\//, 'GET');
    const access = page.locator('[data-pg-audience]');
    await access.waitFor();
    assert.equal((await access.innerText()).trim(), "Everyone who can see where it's filed");
    await access.click();
    const editor = page.locator('#access-editor');
    await editor.locator('[data-access-everyone]').uncheck();
    await editor.locator('[data-access-pick="people"][value="dana"]').check();
    const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && /\/api\/v2\/pages\//.test(r.url()));
    await editor.locator('button[type=submit]').click();
    assert.equal((await saved).status(), 200);
    await page.locator('.pg-status .pg-lock').waitFor();
    await drawn(audienceReader, '#pg-view', text => text.includes('not yours to see'), `${where}: audienceReader loses the open page`);
    assert.equal(await audienceReader.locator('.pg-card').count(), 0);
    await page.locator('[data-pg-audience]').click();
    await editor.locator('[data-access-everyone]').check();
    const cleared = page.waitForResponse(r => r.request().method() === 'PATCH' && /\/api\/v2\/pages\//.test(r.url()));
    await editor.locator('button[type=submit]').click();
    assert.equal((await cleared).status(), 200);
    await drawn(page, '[data-pg-audience]', text => text.includes("Everyone who can see where it's filed"), `${where}: cleared audience`);
    await audienceReader.locator('#pg-view .pg-title').waitFor();
    assert.equal(await audienceReader.locator('[data-pg-audience], .pg-lock').count(), 0);
    assertClean(audienceReader, `${where}: audienceReader regains the page`);
    const audienceId = new URL((await saved).url()).pathname.split('/').pop();
    const legal = await must(hub, 'dana', 'POST', 'groups', {name: 'Legal'});
    const contracts = await must(hub, 'dana', 'POST', 'groups', {name: 'Contracts', parent: legal.id, add: {people: ['priya']}});
    await must(hub, 'dana', 'PATCH', 'pages/' + audienceId, {audience: {teams: [legal.id]}});
    await audienceReader.locator('#pg-view .pg-title').waitFor();
    await must(hub, 'dana', 'PATCH', 'groups/' + contracts.id, {remove: {people: ['priya']}});
    await drawn(audienceReader, '#pg-view', text => text.includes('not yours to see'), `${where}: group removal closes the open page`);
    assert.equal(await audienceReader.locator('#pg-view iframe').count(), 0);
    await must(hub, 'dana', 'PATCH', 'groups/' + contracts.id, {add: {people: ['priya']}});
    await audienceReader.locator('#pg-view .pg-title').waitFor();
    await must(hub, 'dana', 'PATCH', 'groups/' + contracts.id, {parent: ''});
    await drawn(audienceReader, '#pg-view', text => text.includes('not yours to see'), `${where}: moving a nested group closes the open page`);
    await must(hub, 'dana', 'PATCH', 'groups/' + contracts.id, {parent: legal.id});
    await audienceReader.locator('#pg-view .pg-title').waitFor();
    await must(hub, 'dana', 'PATCH', 'pages/' + audienceId, {audience: null});
    await must(hub, 'dana', 'DELETE', 'groups/' + contracts.id);
    await must(hub, 'dana', 'DELETE', 'groups/' + legal.id);
    assertClean(audienceReader, `${where}: group access resets reach the open page`);
    await audienceReader.context().close();
    assertClean(page, `${where}: page open`);
    await shot(page, `e2e-page-${where}`);

    go(page, '#/library/files');
    await page.locator('.lf-row', {hasText: 'Signup funnel'}).first().waitFor({timeout: t(20000)});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${where}: files page scrolls sideways by ${overflow}px`);
    assertClean(page, `${where}: back on files`);

    const task = await must(hub, 'dana', 'POST', 'tasks', {owner: 'person:dana', title: 'Review the linked plan', body: 'Review.'});
    const id = (task.task || task).id;
    const reader = await open(browser, hub, 'priya', {phone, hash: '#/task/' + id});
    const permissions = await must(hub, 'priya', 'GET', 'places/task:' + id);
    assert.equal(permissions.can_edit, false, `${where}: the reader cannot arrange the task's cards`);
    assert.equal(permissions.can_link, true, `${where}: the reader can link a page`);
    go(page, '#/task/' + id);
    const section = reader.locator('[data-task-pages]');
    await section.getByRole('button', {name: 'Link page', exact: true}).click();
    // A later task update in the same batch must not hide the earlier files invalidation.
    const refreshed = reader.waitForResponse(r => decodeURIComponent(new URL(r.url()).pathname) === '/api/v2/places/task:' + id);
    await reader.evaluate(id => liveEmit('tasks', {id, kind: 'update', kinds: ['files', 'update']}), id);
    assert.equal((await refreshed).status(), 200, `${where}: a coalesced files change refreshes the pages`);
    const search = reader.waitForResponse(r => new URL(r.url()).pathname === '/api/v2/files/search');
    await section.getByRole('searchbox', {name: 'Search pages'}).fill('Engineering weekly');
    assert.equal(new URL((await search).url()).searchParams.get('kind'), 'page', `${where}: the picker searches pages only`);
    const choice = section.getByRole('button', {name: 'Engineering weekly', exact: true});
    const linkedPage = await choice.getAttribute('data-tp-page');
    await choice.click();
    const card = `.pg-card[data-page="${linkedPage}"]`;
    await section.locator(card).waitFor({timeout: t(15000)});
    await page.locator('[data-task-pages] ' + card).waitFor({timeout: t(15000)});
    assert.equal(await section.locator('.tp-picker').isVisible(), false, `${where}: linking closes the picker`);
    assertClean(reader, `${where}: a task reader links a page`);
    const readerOverflow = await reader.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(readerOverflow <= 1, `${where}: the picker and page fit the task`);
    await shot(reader, `e2e-task-pages-${where}`);

    // The owner unlinks it in another tab; its files event refreshes the already loaded section in both tabs.
    await page.locator('[data-task-pages]').getByRole('button', {name: 'Unlink Engineering weekly', exact: true}).click();
    await section.locator('.pg-card').waitFor({state: 'detached', timeout: t(15000)});
    await page.locator('[data-task-pages] .pg-card').waitFor({state: 'detached', timeout: t(15000)});
    assertClean(page, `${where}: unlink reaches both task readers`);
    assertClean(reader, `${where}: linked pages refresh without reopening the task`);
    await reader.context().close();
}

async function makePage({hub, browser, phone}) {
    await must(hub, 'dana', 'POST', 'assistant/turn-on', {});
    const made = await must(hub, 'dana', 'POST', 'tasks', {owner: 'human:dana', title: 'Prepare the alert flow', body: 'Three scenarios.'});
    const id = (made.task || made).id;
    const page = await open(browser, hub, 'priya', {phone, hash: '#/task/' + id});
    let status = null, cap = false;
    await page.route('**/api/v2/tasks/' + id + '/make-page', async route => {
      if (route.request().method() === 'GET') return route.fulfill({json: {make_page: status}});
      if (cap) return route.fulfill({status: 409, json: {error: {code: 'conflict', detail: 'This task has 60 pins', retryable: false}}});
      const response = await route.fetch();
      assert.equal(response.status(), 200, await response.text());
      const sent = (await response.json()).message;
      assert.deepEqual(sent.refs.make_page, {task: id});
      assert.equal(sent.sender_id, 'human:priya');
      status = {message_id: sent.id, conversation_id: sent.conversation_id, state: 'making'};
      return route.fulfill({response});
    });
    const section = page.locator('[data-task-pages]'), field = section.getByRole('textbox', {name: 'Describe the page'});
    await field.fill('The alert flow: three scenarios as tabs.');
    await section.getByRole('button', {name: 'Make page', exact: true}).click();
    const card = section.locator('.tp-making');
    await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
    status.state = 'update_required';
    await page.evaluate(cid => liveEmit('runs', {conversation_id: cid, state: 'queued'}), status.conversation_id);
    const retry = card.getByRole('button', {name: 'Retry', exact: true});
    await retry.waitFor({timeout: t(10000)});
    assert.match(await card.innerText(), /Update this computer to make pages/);
    const pendingMessage = status.message_id;
    await retry.click();
    await page.waitForFunction(() => {
      const button = document.querySelector('[data-tp-retry]');
      return button && !button.disabled;
    }, null, {timeout: t(10000)});
    assert.equal(status.message_id, pendingMessage, 'Retry preserves the queued request');
    status.state = 'making';
    await card.getByRole('button', {name: 'Retry', exact: true}).click();
    await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
    assert.equal(await field.evaluate(el => el.matches(':user-invalid')), false, 'a sent request leaves no validation error');
    assert.equal(await section.getByRole('button', {name: 'Make page', exact: true}).isDisabled(), true);
    await shot(page, 'make-page-' + (phone ? 'phone' : 'desktop'));
    // Reopening the task must restore the placeholder rather than losing the in-flight request.
    await go(page, '#/tasks');
    await section.waitFor({state: 'hidden', timeout: t(10000)});
    await go(page, '#/task/' + id);
    await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
    await page.waitForFunction(cid => LIVE.es?.readyState === EventSource.OPEN &&
      new URL(LIVE.es.url).searchParams.get('conversation')?.split(',').includes(cid),
      status.conversation_id, {timeout: t(10000)});
    status.state = 'made';
    const published = await must(hub, 'engineering-lead', 'POST', 'pages', {name: 'alert-flow.html', title: 'Alert flow', task: id,
      text: '<h1>Alert flow</h1><p>Three scenarios.</p>'});
    await section.locator('.pg-card[data-page="' + published.page.file_id + '"]').waitFor({timeout: t(15000)}).catch(async e => {
      const state = await page.evaluate(() => ({route: location.hash, stream: LIVE.es?.url,
        open: document.querySelector('#task-modal')?.open, section: document.querySelector('[data-task-pages]')?.innerText}));
      assert.fail(e.message + '\n' + JSON.stringify({state, errors: page.watch.errors,
        pins: await must(hub, 'priya', 'GET', 'places/task:' + id)}));
    });
    await card.waitFor({state: 'hidden', timeout: t(10000)});

    await field.fill('A second page.');
    await section.getByRole('button', {name: 'Make page', exact: true}).click();
    await card.filter({hasText: 'Making page…'}).waitFor({timeout: t(10000)});
    status.state = 'failed';
    await page.evaluate(cid => liveEmit('runs', {conversation_id: cid, state: 'completed'}), status.conversation_id);
    await card.getByRole('link', {name: 'Open chat'}).waitFor({timeout: t(10000)});
    assert.match(await card.innerText(), /Not made/);
    await card.getByRole('link', {name: 'Open chat'}).click();
    await page.waitForURL(/#\/assistant$/, {timeout: t(10000)});
    await go(page, '#/task/' + id);
    await card.getByRole('link', {name: 'Open chat'}).waitFor({timeout: t(10000)});

    cap = true;
    expectFailure(page, 409, '/make-page$', 'POST');
    await field.fill('Keep this draft.');
    await section.getByRole('button', {name: 'Make page', exact: true}).click();
    await section.getByRole('alert').filter({hasText: 'This task has 60 pins'}).waitFor({timeout: t(10000)});
    assert.equal(await field.inputValue(), 'Keep this draft.');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, 'Make page fits the task at this width');
    assertClean(page, phone ? 'phone Make page' : 'desktop Make page');
    await page.context().close();
}

journey('e2e-files-pages', async ctx => {
  for (const [name, step] of Object.entries({filesPages, makePage})) {
    try { await step(ctx); }
    catch (error) { error.message = `[${name}, ${ctx.phone ? 'phone' : 'desktop'}] ${error.message}`; throw error; }
  }
}, [...SEEDS, 'pages']);
