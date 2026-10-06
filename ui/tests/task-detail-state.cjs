// Data safety: pending edits and reads stay with the task/dialog that owns them.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const {open} = require('./task-code.cjs');
const gate = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
const shots = process.env.TICO_DETAIL_SHOTS;
const shot = (page, name) => shots ? page.screenshot({path: path.join(shots, name + '.png')}) : Promise.resolve();
async function visit(browser) {
  const v = await open(browser, {viewport: {width: 390, height: 844}, rich: true, at: '#/bot/eng/tasks'});
  v.page.setDefaultTimeout(5000);
  return v;
}
async function show(page) {
  await page.locator('[data-task-detail="t-checkout"] [data-task-detail-open]').click();
  await page.locator('#task-modal .task-comments .ask').waitFor();
}
(async () => {
 const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
 try {
  {
   const {page, data, errors} = await visit(browser);
   data.comments[1].refs.target = {file: 'f-notes', version: 2};
   data.files[0].versions[0].ask = {questions: data.comments[1].refs.questions, by: 'bot:eng'};
   await show(page);
   await page.locator('#task-modal [data-tf-file="f-notes"]').click();
   await page.locator('#task-modal [data-tf-view] .ask-other').fill('File-specific draft');
   const draft = page.locator('#task-modal .task-comments .ask');
   await draft.locator('[data-label="Tuesday"]').click(); await draft.locator('.ask-other').fill('After the morning release');
   await page.locator('#task-modal [data-drop-label="checkout"]').click();
   await page.waitForFunction(() => document.querySelector('#task-modal').dataset.version === '3' && document.querySelector('#task-modal .ask'));
   assert.equal(await draft.locator('[data-label="Tuesday"]').getAttribute('aria-pressed'), 'true');
   assert.equal(await draft.locator('.ask-other').inputValue(), 'After the morning release');
   assert.equal(await page.locator('#task-modal [data-tf-view] .ask-other').inputValue(), 'File-specific draft');
   await shot(page, 'after-answer-draft');
   await page.route('**/api/v2/tasks/t-checkout/comments', r => r.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: {detail: 'Service unavailable'}})}));
   await page.locator('#task-modal .task-chat form textarea').fill('Please check the receipt total.');
   await page.locator('#task-modal .task-chat button[type=submit]').click();
   await page.locator('#task-modal [data-task-chat-status]', {hasText: 'Not saved'}).waitFor();
   assert.equal(await draft.locator('[data-label="Tuesday"]').innerText(), 'Tuesday');
   assert.equal(await page.locator('#task-modal .task-chat form textarea').inputValue(), 'Please check the receipt total.');
   await shot(page, 'after-comment-failure');
   // Losing comment permission clears the pending answer and removes its controls.
   await page.route('**/api/v2/tasks/t-checkout', r => r.fulfill({contentType: 'application/json', body: JSON.stringify({task: data.tasks[0], comments: data.comments, can_comment: false})}));
   await page.evaluate(() => taskChatRead(TASK_CHAT));
   assert.equal(await draft.locator('button.ask-opt').count(), 0);
   assert.equal(await page.locator('#task-modal [data-tf-view] button.ask-opt').count(), 0);
   assert.equal(await page.evaluate(() => document.querySelector('#task-modal').askDrafts.size), 0);
   assert.deepEqual(errors, []); await page.close();
  }
  {
   const {page, data, errors} = await visit(browser), held = gate(), reached = gate(); let requests = 0;
   const old = JSON.parse(JSON.stringify(data.files)); old[0].versions = old[0].versions.filter(v => v.n === 1); old[0].current_version = 1;
   await page.route('**/api/v2/tasks/t-checkout/files', async r => {
    const n = ++requests; if (n === 1) { reached.resolve(); await held.promise; }
    await r.fulfill({contentType: 'application/json', body: JSON.stringify({files: n === 1 ? old : data.files})});
   });
   await show(page); await reached.promise;
   // A change to the task arrives on the live stream while the first read is still out: the files are read again.
   await page.evaluate(() => taskChatLive(TASK_CHAT));
   await page.locator('#task-modal [data-tf-file="f-notes"] .tf-meta', {hasText: 'v2'}).waitFor({timeout: 12000});
   await page.locator('#task-modal [data-tf-file="f-notes"]').click();
   await page.locator('#task-modal [data-tf-view]', {hasText: 'Receipt check version 2.'}).waitFor();
   const delivered = page.waitForResponse(r => r.url().endsWith('/tasks/t-checkout/files')); held.resolve(); await delivered;
   await page.waitForTimeout(100);
   assert.equal(await page.evaluate(() => document.querySelector('#task-modal').taskFiles[0].current_version), 2);
   assert.equal(await page.evaluate(() => TF_CACHE.get('t-checkout')[0].current_version), 2);
   assert.match(await page.locator('#task-modal [data-tf-view]').innerText(), /Receipt check version 2/);
   const box = await page.locator('#task-modal').boundingBox(); assert.equal(Math.round(box.width), 390);
   await shot(page, 'after-file-response-order');
   assert.deepEqual(errors, []); await page.close();
  }
  for (const remove of [false, true]) {
   const {page, errors} = await visit(browser); await show(page);
   const held = gate(), reached = gate();
   await page.route('**/api/v2/tasks/t-checkout/links', async r => { reached.resolve(); await held.promise; await r.fulfill({contentType: 'application/json', body: '{}'}); });
   if (remove) await page.locator('#task-modal [data-drop-link="d1"]').click();
   else {
    await page.locator('#task-modal [data-link-add]').click();
    await page.locator('#task-modal [data-modal-link] input').fill('https://acme.example/spec');
    await page.locator('#task-modal [data-modal-link] input').press('Enter');
   }
   await reached.promise; await page.locator('#task-modal [data-modal-close]').click();
   await page.waitForFunction(() => !document.querySelector('#task-modal').open && !history.state?.taskModal);
   if (remove) await show(page); // Same task, different opening: the old save must not redraw it.
   const seq = await page.evaluate(() => document.querySelector('#task-modal').drawSeq);
   const fetched = page.waitForResponse(r => r.url().endsWith('/tasks/t-checkout') && r.request().method() === 'GET');
   held.resolve(); await fetched; await page.waitForTimeout(100);
   assert.equal(await page.evaluate(() => document.querySelector('#task-modal').open), remove);
   assert.equal(await page.evaluate(() => document.querySelector('#task-modal').drawSeq), seq);
   if (!remove) await shot(page, 'after-link-dismissal');
   assert.deepEqual(errors, []); await page.close();
  }
  console.log('Task detail: drafts, comment failure, file ordering, phone preview and link opening ownership passed');
 } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
