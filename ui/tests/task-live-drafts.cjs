// Regressions for stale focused fields, redraws of every Add form, draft ownership and reload recovery.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./task-code.cjs');
const gate = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const init = () => {
 window.taskSources = []; window.taskSafety = [];
 window.EventSource = class extends EventTarget {
  constructor(url) { super(); this.url = url; taskSources.push(this); }
  close() { this.closed = true; }
 };
 const interval = window.setInterval.bind(window);
 window.setInterval = (fn, ms, ...args) => { if (ms === 180000) taskSafety.push(fn); return interval(fn, ms, ...args); };
};
(async () => {
 const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
 try {
  for (const width of [390, 1440]) {
   const context = await browser.newContext({viewport: {width, height: 900}, hasTouch: width <= 390, serviceWorkers: 'block'});
   const sharedBrowser = {newPage: async options => { const page = await context.newPage(); await page.setViewportSize(options.viewport); return page; }};
   const {page, data, errors} = await open(sharedBrowser, {viewport: {width, height: 900}});
   const task = {...data.tasks[0], title: 'Review launch', body: 'Original details', links: [], children_summary: undefined};
   data.tasks[0] = task;
   const writes = []; let hold, offline = false, expired = false;
   let me = {id: 'ana', name: 'Ana', role: 'owner', mover: true, cloud: true, registered: true, config: {environment_id: 'acme-org'}};
   await page.route('**/api/me', route => route.fulfill({json: me}));
   await page.route('**/api/v2/me', route => route.fulfill(expired
    ? {status: 401, json: {error: {code: 'identity', sign_in: '/auth/login'}}}
    : {json: {id: me.id}}));
   await page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor();
   await page.evaluate(() => { taskLiveStop(taskModal()); taskModal().taskRail = null; S.config.environment_id = 'acme-org'; sessionReturn(); });
   await page.evaluate(init); await page.addInitScript(init);
   await page.route('**/api/v2/tasks/' + task.id, async route => {
    if (route.request().method() === 'GET') return route.fulfill({json: {task, comments: [], children: [], events: []}});
    const patch = route.request().postDataJSON(); writes.push(patch);
    if (hold) { const wait = hold; hold = null; await wait.promise; }
    if (offline) return route.abort('internetdisconnected');
    if (patch.version !== task.version) return route.fulfill({status: 409, json: {error: {code: 'version_conflict', detail: 'Task changed'}}});
    Object.assign(task, patch, {version: task.version + 1});
    if ('due' in patch) task.due_at = patch.due;
    return route.fulfill({json: {task}});
   });
   await page.route('**/api/v2/presence**', route => route.fulfill({json: {actors: []}}));
   await page.evaluate(async task => taskModalShow(task), task);
   const idle = () => page.waitForFunction(() => !taskModal().taskLive.reading && !taskModal().taskLive.draft.flight);
   const tasksReady = () => page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tl-row, #task-body .bcard, #task-body .tl-empty'));
   const emit = topic => page.evaluate(topic => {
    for (const source of taskSources) if (!source.closed) source.dispatchEvent(new MessageEvent(topic, {data: JSON.stringify({kinds: ['update']})}));
   }, topic);
   const update = async () => {
    const seq = await page.evaluate(() => taskModal().drawSeq);
    await emit('tasks');
    await page.waitForFunction(seq => taskModal().drawSeq > seq && !taskModal().taskLive.reading, seq);
   };
   const refresh = async () => {
    const seq = await page.evaluate(() => taskModal().drawSeq);
    await page.evaluate(() => taskSafety.forEach(fn => fn()));
    await page.waitForFunction(seq => taskModal().drawSeq > seq && !taskModal().taskLive.reading, seq);
   };
   const title = page.locator('#task-modal .tmodal-title'), details = page.getByRole('textbox', {name: 'Details', exact: true});
   const fillTitle = async value => { await title.click(); await title.fill(value); };
   const error = page.locator('[data-task-edit-error]:not([hidden])');
   const retry = async () => { await page.locator('[data-task-edit-retry]').click(); await idle(); };
   const unload = () => page.evaluate(() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; });
   // A focuses clean text, B saves, A receives the event and appends. A must still send A's old revision.
   await page.getByRole('button', {name: 'Edit details', exact: true}).click();
   const originalVersion = task.version;
   task.body = 'B changed details'; task.version++;
   await update();
   assert.equal(await details.inputValue(), 'Original details');
   await details.press('End'); await details.pressSequentially(' plus A');
   await details.evaluate(el => el.blur());
   await error.waitFor(); await idle();
   assert.equal(writes.at(-1).version, originalVersion);
   assert.equal(task.body, 'B changed details', 'A cannot silently replace B with a newer version');
   assert.equal(await details.inputValue(), 'Original details plus A');
   await page.locator('[data-task-edit-current]:not([hidden])').waitFor();
   await retry(); assert.equal(task.body, 'Original details plus A');
   // The publication fields use the same field base as title/details, even through a clean focused redraw.
   task.type_id = 'draft-roadmap'; task.public_title = 'Public A'; task.public_summary = 'Summary A';
   await page.evaluate(() => { TASK_TYPES.push({id: 'draft-roadmap', role: 'product-roadmap', name: 'Roadmap', steps: []}); TASK_TYPES_READ = true; });
   await page.route('**/api/v2/task-types', route => route.fulfill({json: {types: [{id: 'draft-roadmap', role: 'product-roadmap', name: 'Roadmap', steps: []}]}}));
   await update();
   for (const [key, label] of [['public_title', 'Public title'], ['public_summary', 'Public summary']]) {
    const box = page.getByRole('textbox', {name: label, exact: true}), base = task.version, original = task[key];
    await box.focus();
    task[key] = 'Other editor ' + key; task.version++;
    await update();
    assert.equal(await box.inputValue(), original);
    const count = writes.length;
    await box.evaluate(el => el.blur()); await idle();
    assert.equal(writes.length, count, 'blur without typing cannot save stale ' + key);
    assert.equal(task[key], 'Other editor ' + key);
    // Restore/focus once more and type after a newer server value arrives.
    await box.focus();
    const focusedBase = await box.evaluate(el => taskModal().taskLive.fields[el.dataset.taskField].version);
    task[key] = 'Newer editor ' + key; task.version++;
    await update();
    await box.fill('My ' + key); await box.evaluate(el => el.blur());
    await error.waitFor(); await idle();
    assert.equal(writes.at(-1).version, focusedBase);
    assert.equal(task[key], 'Newer editor ' + key);
    if (key === 'public_title') {
     const count = writes.length;
     await page.waitForTimeout(800);
     assert.equal(writes.length, count, 'a cancelled debounce cannot retry a conflict');
     assert.equal(await error.count(), 1);
    }
    await retry(); assert.equal(task[key], 'My ' + key);
    assert.ok(focusedBase >= base);
   }
   // Size and the due-date fallback also retain the version of the field they displayed.
   for (const [key, selector, value, remote] of [
    ['size', '[data-task-size]', '3', 8], ['due', '[data-prop-date]', '2026-12-04', '2026-12-08T17:00:00.000Z']
   ]) {
    const box = page.locator('#task-modal ' + selector);
    await box.evaluate(el => { el.classList.add('shown'); el.focus(); });
    task[key === 'due' ? 'due_at' : key] = remote; task.version++;
    await update();
    const count = writes.length;
    await box.evaluate(el => el.blur()); await idle();
    assert.equal(writes.length, count, 'clean property blur never saves');
    await box.evaluate(el => el.focus());
    const base = await box.evaluate(el => taskModal().taskLive.fields[el.dataset.taskField].version);
    task.version++; await update();
    await box.evaluate((el, value) => { el.focus(); el.value = value; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); }, value);
    await error.waitFor(); await idle();
    assert.deepEqual(errors, [], key + ' conflict redraw');
    assert.equal(writes.at(-1).version, base);
    await box.evaluate(el => el.blur());
    assert.equal(await error.count(), 1, key + ' blur keeps its failed save until Retry');
    await retry();
   }
   // Invalid dates keep their feedback and send nothing; a successful date edit clears its own error.
   const beforeDate = writes.length, date = page.locator('#task-modal [data-prop-date]');
   await date.evaluate(el => { el.value = '0002-10-09'; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); });
   assert.equal(writes.length, beforeDate);
   assert.ok(await page.locator('#task-modal [data-props-msg]').textContent());
   await date.evaluate(el => { el.value = '2026-12-05'; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); });
   await idle();
   assert.equal(await page.locator('#task-modal [data-props-msg]').textContent(), '');
   // A routine's due day retains its calendar date across viewer timezones.
   task.occurrence = 'routine:2026-12-10'; task.due_at = '2026-12-10T23:59:59-11:00'; task.version++;
   await update();
   assert.equal(await page.locator('#task-modal [data-prop-date]').inputValue(), '2026-12-10');
   delete task.occurrence;
   // Every Add box and the comment box keep all their texts while each one has focus during both redraw paths.
   await page.locator('[data-task-more]').click(); await page.getByText('Add subtask', {exact: true}).click();
   await page.getByRole('button', {name: 'Add a link', exact: true}).click();
   await page.getByRole('button', {name: 'Add a session', exact: true}).click();
   const boxes = ['Add subtask', 'Add a link', 'Add a session', 'Message'];
   const values = ['Typed child', 'https://acme.example/typed', 'session to keep', 'Unsent comment'];
   for (let i = 0; i < boxes.length; i++) await page.getByRole('textbox', {name: boxes[i], exact: true}).fill(values[i]);
   for (let i = 0; i < boxes.length; i++) {
    const box = page.getByRole('textbox', {name: boxes[i], exact: true});
    await box.focus(); await box.press('Home'); await box.press('ArrowRight'); await box.press('ArrowRight');
    if (i === 0) { task.run_model = null; task.can_set_run_model = false; task.version++; }
    for (const redraw of [update, refresh]) {
     await redraw();
     for (let j = 0; j < boxes.length; j++) assert.equal(await page.getByRole('textbox', {name: boxes[j], exact: true}).inputValue(), values[j]);
     assert.equal(await box.evaluate(el => document.activeElement === el), true, boxes[i] + ' keeps focus');
    }
    await box.pressSequentially('X'); values[i] = values[i].slice(0, 2) + 'X' + values[i].slice(2);
    assert.equal(await box.inputValue(), values[i], boxes[i] + ' keeps its native caret');
   }
   const draw = await page.evaluate(() => taskModal().drawSeq);
   const presenceRead = page.waitForResponse(r => r.url().includes('/v2/presence?'));
   await emit('presence'); await presenceRead;
   assert.equal(await page.evaluate(() => taskModal().drawSeq), draw, 'presence alone never redraws the task');
   // Reopening transfers ownership while the previous save is still pending.
   const pending = gate(); hold = pending;
   await fillTitle('Pending old view'); await title.evaluate(el => el.blur());
   await page.waitForFunction(() => !!taskModal().taskLive.draft.flight);
   assert.equal(await unload(), true);
   const owner = await page.evaluate(() => { window.oldView = taskModal().taskLive; taskModal().close(); return oldView.owner; });
   await page.waitForFunction(() => !taskModal().taskLive);
   await page.evaluate(async task => taskModalShow(task), task);
   assert.notEqual(await page.evaluate(() => taskModal().taskLive.owner), owner);
   pending.resolve(); await idle();
   assert.equal(await page.evaluate(() => TASK_EDIT_DRAFTS.get(taskModal().taskLive.id) === taskModal().taskLive.draft), true, 'late success cannot evict the reopened draft');
   // B opens clean before A edits; both actual pages share localStorage in one browser context.
   const clean = await open(sharedBrowser, {viewport: {width, height: 900}});
   await clean.page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor();
   await clean.page.evaluate(async task => { taskLiveStop(taskModal()); S.config.environment_id = 'acme-org'; sessionReturn(); await taskModalShow(task); }, task);
   assert.equal(await clean.page.evaluate(() => taskModal().taskLive.identity), await page.evaluate(() => taskModal().taskLive.identity));
   offline = true;
   await fillTitle('New view failed draft'); await title.evaluate(el => el.blur());
   await error.waitFor(); await idle();
   await clean.page.evaluate(() => taskModal().close());
   await clean.page.waitForFunction(() => !taskModal().taskLive);
   await clean.page.close();
   assert.deepEqual(clean.errors, []);
   assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(taskLiveStorageKey(taskModal().dataset.task, 'title'))).value), 'New view failed draft', 'closing clean B preserves A’s failed text');
   await page.evaluate(() => taskModal().close()); await page.waitForFunction(() => !taskModal().taskLive);
   await page.evaluate(async task => taskModalShow(task), task);
   assert.equal(await title.textContent(), 'New view failed draft');
   // Offline title/details survive a real reload with their old revisions and still conflict before overwriting B.
   await page.getByRole('button', {name: 'Edit details', exact: true}).click();
   await details.fill('Offline details to keep');
   const keptVersion = task.version;
   assert.equal(await unload(), true);
   assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(taskLiveStorageKey(taskModal().dataset.task, 'body'))).base.version), keptVersion);
   task.title = 'B title during offline'; task.body = 'B details during offline'; task.version++;
   page.on('dialog', dialog => dialog.accept());
   await page.reload();
   await page.locator('#task-modal[open] [data-task-field="body"]').waitFor();
   assert.equal(await title.textContent(), 'New view failed draft');
   assert.equal(await details.inputValue(), 'Offline details to keep');
   offline = false;
   await retry(); await page.locator('[data-task-edit-current]:not([hidden])').waitFor();
   assert.equal(writes.at(-1).version, keptVersion);
   assert.equal(task.title, 'B title during offline'); assert.equal(task.body, 'B details during offline');
   assert.equal(await details.inputValue(), 'Offline details to keep');
   await retry();
   assert.equal(task.title, 'New view failed draft'); assert.equal(task.body, 'Offline details to keep');
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false, 'success clears persisted drafts');
   assert.equal(await unload(), false, 'a saved page needs no unload prompt');
   // Debounce text is already durable before a request starts, without an unload prompt.
   await page.evaluate(() => {
    const field = document.querySelector('[data-task-field="title"]'); field.click(); field.focus();
    field.textContent = 'Before debounce'; field.dispatchEvent(new Event('input', {bubbles: true}));
    clearTimeout(taskModal().taskLive.draft.timer);
   });
   assert.equal(await unload(), false);
   await page.reload();
   await page.waitForFunction(() => document.querySelector('#task-modal[open] .tmodal-title')?.textContent === 'Before debounce');
   await page.waitForFunction(() => !taskModal().taskLive.draft.flight && !Object.keys(taskModal().taskLive.draft.patch).length);
   // A closed task's failed title/details survive expiry with their bases, even after another editor saves.
   offline = true;
   await fillTitle('Closed task title'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.getByRole('button', {name: 'Edit details', exact: true}).click();
   await details.fill('Closed task details'); await details.evaluate(el => el.blur()); await idle();
   const closedDraft = await page.evaluate(id => Object.fromEntries(['title', 'body'].map(key =>
    [key, JSON.parse(localStorage.getItem(taskLiveStorageKey(id, key)))])), task.id);
   await page.evaluate(() => taskModal().close());
   await page.waitForFunction(() => !taskModal().taskLive && !TASK_MODAL_BACK);
   offline = false; expired = true;
   await page.evaluate(() => get('/v2/me', {fresh: true}).catch(() => {}));
   await page.locator('#signed-out:not([hidden])').waitFor();
   assert.deepEqual(await page.evaluate(id => Object.fromEntries(['title', 'body'].map(key =>
    [key, JSON.parse(localStorage.getItem(taskLiveStorageKey(id, key)))])), task.id), closedDraft, 'expiry preserves closed drafts and bases');
   task.title = 'Other title while signed out'; task.body = 'Other details while signed out'; task.version++;
   const beforeReturn = writes.length;
   expired = false;
   await page.goto('https://tico-ui.test/#/tasks');
   await page.waitForFunction(() => SESSION.identity === JSON.stringify(['acme-org', 'ana']));
   await tasksReady();
   assert.equal(await page.locator('#task-modal[open]').count(), 0, 'the closed task stays closed after sign-in');
   await page.evaluate(async task => taskModalShow(task), task);
   assert.equal(await title.textContent(), 'Closed task title');
   assert.equal(await details.inputValue(), 'Closed task details');
   assert.equal(writes.length, beforeReturn, 'failed drafts wait for Retry after reopening');
   await retry(); await page.locator('[data-task-edit-current]:not([hidden])').waitFor();
   assert.equal(writes.at(-1).version, Math.min(...Object.values(closedDraft).map(field => field.base.version)));
   assert.equal(task.title, 'Other title while signed out'); assert.equal(task.body, 'Other details while signed out');
   await retry();
   assert.equal(task.title, 'Closed task title'); assert.equal(task.body, 'Closed task details');
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false, 'saving the restored text consumes its stored drafts');
   // Sign-in recovery also keeps the open task's contenteditable title and property fields.
   offline = true;
   await fillTitle('Sign-in title to keep'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.evaluate(() => {
    for (const [key, value] of [['public_title', 'Recovered public title'], ['public_summary', 'Recovered public summary'], ['size', '5'], ['due', '2026-12-15']]) {
      const el = taskModal().querySelector(`[data-task-field="${key}"]`);
      el.value = value; el.dispatchEvent(new Event('input', {bubbles: true}));
    }
    SESSION.seen = true; signedOut('/auth/login');
   });
   assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem(SESSION_RETURN)).fields.filter(f => f.base).map(f => f.key).sort()),
     ['Public summary', 'Public title', 'Size', 'Title', 'due'].sort());
   assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith(TASK_EDIT_STORAGE)).length), 5);
   assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem(SESSION_RETURN)).fields.find(f => f.key === 'Title').value), 'Sign-in title to keep');
   const recoveryBase = await page.evaluate(() => Math.min(...JSON.parse(sessionStorage.getItem(SESSION_RETURN)).fields.filter(f => f.base).map(f => f.base.version)));
   offline = false;
   await page.reload();
   await page.waitForFunction(() => document.querySelector('#task-modal[open] .tmodal-title')?.textContent === 'Sign-in title to keep');
   await error.waitFor(); await idle(); await retry();
   await page.waitForFunction(() => taskModal().taskLive.draft.error || (!taskModal().taskLive.draft.flight && !Object.keys(taskModal().taskLive.draft.patch).length));
   assert.equal(writes.at(-1).version, recoveryBase);
   if (await error.count()) {
    assert.match(await page.evaluate(() => taskModal().taskLive.draft.error), /Task changed/);
    await retry();
   }
   assert.equal(task.title, 'Sign-in title to keep');
   assert.equal(task.public_title, 'Recovered public title'); assert.equal(task.public_summary, 'Recovered public summary');
   assert.equal(task.size, 5); assert.equal(task.due_at.slice(0, 10), '2026-12-15');
   // Ana's expired-session recovery never appears or autosaves when Ben signs into this same tab.
   offline = true;
   await fillTitle('Ana private title'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.getByRole('textbox', {name: 'Message', exact: true}).fill('Ana private comment');
   await page.evaluate(() => taskModal().close());
   await page.waitForFunction(() => !taskModal().taskLive);
   await page.evaluate(() => { SESSION.seen = true; signedOut('/auth/login'); });
   assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem(SESSION_RETURN)).identity), JSON.stringify(['acme-org', 'ana']));
   assert.equal(await page.evaluate(id => JSON.parse(localStorage.getItem(taskLiveStorageKey(id, 'title'))).value, task.id), 'Ana private title');
   const count = writes.length;
   me = {...me, id: 'ben', name: 'Ben'}; offline = false;
   await page.reload(); await page.waitForFunction(() => SESSION.identity === JSON.stringify(['acme-org', 'ben']));
   await tasksReady();
   await page.evaluate(async task => taskModalShow(task), task); await idle();
   assert.equal(await title.textContent(), task.title);
   assert.equal(await page.getByRole('textbox', {name: 'Message', exact: true}).inputValue(), '');
   assert.equal(await page.evaluate(() => sessionStorage.getItem(SESSION_RETURN)), null);
   assert.equal(await page.evaluate(() => TASK_EDIT_DRAFTS.get(taskModal().dataset.task)?.patch.title), undefined);
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false);
   assert.equal(writes.length, count, 'no text from Ana autosaves as Ben');
   // A reload as another org with the same person id also drops both recovery paths.
   offline = true;
   await fillTitle('Ben old org draft'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.evaluate(() => sessionKeep());
   me = {...me, config: {environment_id: 'other-org'}}; offline = false;
   await page.reload(); await page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor(); await idle();
   assert.equal(await title.textContent(), task.title);
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false);
   assert.equal(await page.evaluate(() => sessionStorage.getItem(SESSION_RETURN)), null);
   // Explicit sign-out discards recovery even when an expired session's pagehide watcher runs afterward.
   offline = true;
   await fillTitle('Discard on logout'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.evaluate(() => {
    SESSION.seen = true; signedOut('/auth/login');
    const link = document.createElement('a'); link.href = '/auth/logout';
    link.addEventListener('click', event => event.preventDefault()); taskModal().append(link); link.click();
    window.dispatchEvent(new Event('pagehide')); sessionKeep();
   });
   assert.equal(await page.evaluate(() => sessionStorage.getItem(SESSION_RETURN)), null);
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false);
   assert.deepEqual(errors, []);
   await page.close(); await context.close();
  }
  console.log('Task draft regressions: phone/desktop stale revision, Add forms, refresh/presence, ownership, offline reload, publication/property revisions, shared storage, debounce and identity-safe sign-in passed');
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
