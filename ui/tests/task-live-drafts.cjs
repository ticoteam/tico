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
   const {page, data, errors} = await open(browser, {viewport: {width, height: 900}});
   const task = {...data.tasks[0], title: 'Review launch', body: 'Original details', links: [], children_summary: undefined};
   const writes = []; let hold, offline = false;
   await page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor();
   await page.evaluate(() => { taskLiveStop(taskModal()); taskModal().taskRail = null; });
   await page.evaluate(init); await page.addInitScript(init);
   await page.route('**/api/v2/tasks/' + task.id, async route => {
    if (route.request().method() === 'GET') return route.fulfill({json: {task, comments: [], children: [], events: []}});
    const patch = route.request().postDataJSON(); writes.push(patch);
    if (hold) { const wait = hold; hold = null; await wait.promise; }
    if (offline) return route.abort('internetdisconnected');
    if (patch.version !== task.version) return route.fulfill({status: 409, json: {error: {code: 'version_conflict', detail: 'Task changed'}}});
    Object.assign(task, patch, {version: task.version + 1});
    return route.fulfill({json: {task}});
   });
   await page.route('**/api/v2/presence**', route => route.fulfill({json: {actors: []}}));
   await page.evaluate(async task => taskModalShow(task), task);
   const idle = () => page.waitForFunction(() => !taskModal().taskLive.reading && !taskModal().taskLive.draft.flight);
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
   offline = true;
   await fillTitle('New view failed draft'); await title.evaluate(el => el.blur());
   await error.waitFor(); await idle();
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
   // Sign-in recovery includes the contenteditable title, while sign-out removes local task drafts.
   offline = true;
   await fillTitle('Sign-in title to keep'); await title.evaluate(el => el.blur()); await error.waitFor(); await idle();
   await page.evaluate(() => { SESSION.seen = true; signedOut('/auth/login'); });
   assert.equal(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith(TASK_EDIT_STORAGE))), false);
   assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem(SESSION_RETURN)).fields.find(f => f.key === 'Title').value), 'Sign-in title to keep');
   offline = false;
   await page.reload();
   await page.waitForFunction(() => document.querySelector('#task-modal[open] .tmodal-title')?.textContent === 'Sign-in title to keep');
   await page.waitForFunction(() => !taskModal().taskLive.draft.flight && !Object.keys(taskModal().taskLive.draft.patch).length);
   assert.equal(task.title, 'Sign-in title to keep');
   assert.deepEqual(errors, []);
   await page.close();
  }
  console.log('Task draft regressions: phone/desktop stale revision, Add forms, refresh/presence, ownership, offline reload, debounce and sign-out passed');
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
