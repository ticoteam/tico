// New message on a real hub (ui/tests/support/real-hub.cts): the rail's + opens the pane (#/threads/new); Omar and Priya
// as chips offer the thread with exactly them, and Enter opens it; Omar alone is his DM, made by the first message; Omar
// and a bot with no thread yet need a name, then make a private thread pinned for its people; a #thread picked from the
// To box opens; at phone width (390px) the pane takes the screen with a back arrow, and a teammate's DM button opens it
// with them chosen. TICO_SCREENSHOT_DIR=<dir> saves desktop (dark and light) and phone screenshots.
import type {Page} from 'playwright';
import type * as RealHubSupport from './support/real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cts') as RealHubSupport.Api;
const {t} = require('./support/load.cts') as {t: (ms: number) => number};

const WAIT = t(15000);
const shots = process.env.TICO_SCREENSHOT_DIR;
interface Thread {id: string; title?: string; me?: {pinned?: boolean}}

async function api(hub: RealHubSupport.Hub, teammate: string, method: string, p: string, body?: unknown) {
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + hub.tokens[teammate],
    'content-type': 'application/json', ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})},
    body: (body === undefined ? undefined : JSON.stringify(body)) as string});
  const json = await r.json().catch(() => ({}));
  assert.ok(r.status < 300, `${method} ${p} as ${teammate}: ${r.status} ${JSON.stringify(json)}`);
  return json;
}
// A 1x1 PNG, as a pasted screenshot.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
// A thread's messages as Dana reads them: each one's words and the names of the files attached to it.
async function attached(hub: RealHubSupport.Hub, id: string): Promise<{text: string; files: string[]}[]> {
  const r = await api(hub, 'dana', 'GET', `/v2/threads/${encodeURIComponent(id)}/timeline?flat=1`);
  return (r.messages as {body?: string; refs?: {attachments?: {name?: string}[]}}[])
    .map(m => ({text: m.body || '', files: (m.refs?.attachments || []).map(f => f.name || '')}));
}
// The screen in both themes (desktop) or as it is (phone): new-message-<name>-<dark|light>.png.
async function shot(page: Page, name: string, themes = ['dark', 'light']) {
  if (!shots) return;
  fs.mkdirSync(shots, {recursive: true});
  for (const theme of themes) {
    await page.evaluate(th => { document.documentElement.dataset.theme = th; }, theme);
    await page.waitForTimeout(150);
    await page.screenshot({path: path.join(shots, `new-message-${name}-${theme}.png`)});
  }
}
const hashIs = (page: Page, hash: string) => page.waitForFunction(h => location.hash === h, hash, {timeout: WAIT});
// The rows the list shows now, by their name.
const rows = (page: Page) => page.locator('#nm-list li .nm-name').allTextContents();
async function openPane(page: Page) {
  await page.locator('#thread-rail [data-tr-new]').click();
  await hashIs(page, '#/threads/new');
  await page.locator('#nm-q').waitFor({timeout: WAIT});
}
async function add(page: Page, words: string, name: string) {
  await page.locator('#nm-q').fill(words);
  await page.locator('#nm-list li[aria-selected="true"] .nm-name', {hasText: name}).waitFor({timeout: WAIT});
  await page.keyboard.press('Enter');
  await page.locator('.nm-chip', {hasText: name}).waitFor({timeout: WAIT});
}

(async () => {
  const hub = await startHub({seeds: ['org', 'toured']});
  let browser;
  try {
    const plan: Thread = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Launch plan', audience: 'members', members: ['human:omar', 'human:priya'], body: 'Tuesday'});
    // Omar's thread with Priya, without Dana: never a match for her, though its members are exactly the ones she names.
    const theirs: Thread = await api(hub, 'omar', 'POST', '/v2/threads', {title: 'Side plan', audience: 'members', members: ['human:priya', 'human:dana'], body: 'Quiet'});
    await api(hub, 'omar', 'DELETE', `/v2/threads/${theirs.id}/members/human:dana`);
    const hall: Thread = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'All hands', audience: 'company', members: [], body: 'Welcome'});
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
    const page = await pageAs(browser, hub, 'dana');
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.setViewportSize({width: 1280, height: 800});
    await page.goto(hub.url + '/#/thread/' + hall.id);
    await page.getByText('Welcome').waitFor({timeout: WAIT});

    // + opens the pane; typing finds people and threads in one list.
    await openPane(page);
    assert.equal(await page.locator('#thread-rail [data-tr-new]').getAttribute('aria-label'), 'New message');
    await page.locator('#nm-q').fill('o');
    await page.locator('#nm-list li .nm-name', {hasText: 'Omar'}).waitFor({timeout: WAIT});
    await shot(page, 'typing');

    // Two people: the thread with exactly them comes first (not Omar's thread she left), and Enter opens it.
    await add(page, 'om', 'Omar');
    await add(page, 'pri', 'Priya');
    await page.locator('#nm-list li[aria-selected="true"] .nm-name', {hasText: 'Launch plan'}).waitFor({timeout: WAIT});
    assert.deepEqual(await rows(page), ['Launch plan', 'New thread']);
    await shot(page, 'match');
    await page.locator('#nm-q').press('Enter');
    await hashIs(page, '#/thread/' + plan.id);
    await page.getByText('Tuesday').waitFor({timeout: WAIT});

    // Backspace takes the last chip off; Esc closes the pane.
    await openPane(page);
    await add(page, 'om', 'Omar');
    await page.locator('#nm-q').press('Backspace');
    assert.equal(await page.locator('.nm-chip').count(), 0);
    await page.locator('#nm-q').press('Escape');
    await page.waitForFunction(() => location.hash !== '#/threads/new', null, {timeout: WAIT});

    // One person: their DM, made by the first message, which lands in it.
    await openPane(page);
    await add(page, 'om', 'Omar');
    await page.locator('#nm-list li[aria-selected="true"] .nm-name', {hasText: 'Omar'}).waitFor({timeout: WAIT});
    await page.locator('.nm-composer .p-text').fill('Lunch at noon?');
    await page.locator('.nm-composer .p-text').press('Enter');
    await page.waitForFunction(() => location.hash.startsWith('#/thread/'), null, {timeout: WAIT});
    await page.locator('.th-msg', {hasText: 'Lunch at noon?'}).waitFor({timeout: WAIT});
    const dm: Thread = await api(hub, 'dana', 'GET', '/v2/threads/dm?with=human:omar&create=false');
    assert.equal(await page.evaluate(() => location.hash), '#/thread/' + dm.id);
    // Asked again, Omar is that DM, and a blank Send just opens it.
    await openPane(page);
    await add(page, 'om', 'Omar');
    await page.locator('.nm-composer .p-send:not([disabled])').waitFor({timeout: WAIT});
    await page.locator('.nm-composer .p-send').click();
    await hashIs(page, '#/thread/' + dm.id);

    // A pasted screenshot is a chip, and the first message carries it: Priya has no DM yet, so it is made, then the
    // message with the image goes in it (as the thread composer sends files).
    await openPane(page);
    await add(page, 'pri', 'Priya');
    await page.locator('#nm-list li[aria-selected="true"] .nm-name', {hasText: 'Priya'}).waitFor({timeout: WAIT});
    const nmBox = page.locator('.nm-composer .p-text'), nmChips = page.locator('.nm-composer .p-chips > span');
    await nmBox.fill('See the header');
    const pasted = await nmBox.evaluate((el: HTMLTextAreaElement, b64: string) => {
      const dt = new DataTransfer();
      dt.items.add(new File([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], 'shot.png', {type: 'image/png'}));
      const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    }, PNG.toString('base64'));
    assert.equal(pasted, true, 'a screenshot paste is the composer\'s');
    await nmChips.locator('.p-thumb').waitFor({timeout: WAIT});
    assert.equal(await nmBox.inputValue(), 'See the header', 'the words stay as typed');
    // The clip adds a file; × takes it off again.
    await page.locator('.nm-composer .p-attach').click();
    await page.locator('.nm-composer .p-file').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('notes')});
    await page.waitForFunction(() => document.querySelectorAll('.nm-composer .p-chips > span').length === 2, null, {timeout: WAIT});
    await nmChips.nth(1).locator('[data-rm]').click();
    await page.waitForFunction(() => document.querySelectorAll('.nm-composer .p-chips > span').length === 1, null, {timeout: WAIT});
    await page.locator('.nm-composer .p-send').click();
    await page.waitForFunction(() => location.hash.startsWith('#/thread/'), null, {timeout: WAIT});
    await page.locator('.th-msg', {hasText: 'See the header'}).waitFor({timeout: WAIT});
    const priya: Thread = await api(hub, 'dana', 'GET', '/v2/threads/dm?with=human:priya&create=false');
    assert.equal(await page.evaluate(() => location.hash), '#/thread/' + priya.id);
    const sent = (await attached(hub, priya.id)).find(m => m.text === 'See the header');
    assert.deepEqual(sent?.files, ['shot.png'], 'the image went with the first message');
    // Omar's DM is there already: a file alone, from the clip with no words, is sent to it (not just opened).
    await openPane(page);
    await add(page, 'om', 'Omar');
    await page.locator('.nm-composer .p-send:not([disabled])').waitFor({timeout: WAIT});
    await page.locator('.nm-composer .p-attach').click();
    await page.locator('.nm-composer .p-file').setInputFiles({name: 'plan.png', mimeType: 'image/png', buffer: PNG});
    await nmChips.locator('.p-thumb').waitFor({timeout: WAIT});
    await page.locator('.nm-composer .p-send').click();
    await hashIs(page, '#/thread/' + dm.id);
    await page.waitForFunction(() => !document.querySelector('.nm-composer'), null, {timeout: WAIT});
    for (let i = 0; i < 50 && !(await attached(hub, dm.id)).some(m => m.files.includes('plan.png')); i++) await page.waitForTimeout(100);
    assert.ok((await attached(hub, dm.id)).some(m => m.files.includes('plan.png')), 'a file alone is a message');
    // A row picked from the list only goes there: neither the words nor the files waiting are sent (only Send sends).
    const before = (await attached(hub, dm.id)).length;
    await openPane(page);
    await add(page, 'om', 'Omar');
    await page.locator('.nm-composer .p-file').setInputFiles({name: 'pick.png', mimeType: 'image/png', buffer: PNG});
    await nmChips.locator('.p-thumb').waitFor({timeout: WAIT});
    await nmBox.fill('Not yet');
    await page.locator('#nm-list li .nm-name', {hasText: 'Omar'}).click();
    await hashIs(page, '#/thread/' + dm.id);
    await page.waitForTimeout(500);
    assert.equal((await attached(hub, dm.id)).length, before, 'a pick sends nothing');

    // An upload refused after a new thread was made for it: the next Send posts into that thread, not a second one.
    await openPane(page);
    await add(page, 'pri', 'Priya');
    await add(page, 'engineering', 'engineering-manager');
    await page.locator('#nm-list li[aria-selected="true"].nm-new').waitFor({timeout: WAIT});
    await page.locator('#nm-title').fill('shots');
    await page.locator('.nm-composer .p-file').setInputFiles({name: 'retry.png', mimeType: 'image/png', buffer: PNG});
    await nmChips.locator('.p-thumb').waitFor({timeout: WAIT});
    await page.route('**/api/v2/uploads/threads/**', route => route.fulfill({status: 413, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'too_large', message: 'Too large'}})}), {times: 1});
    await page.locator('.nm-composer .p-send').click();
    await page.locator('.nm-composer .p-send:not([disabled])').waitFor({timeout: WAIT});
    assert.equal(await page.evaluate(() => location.hash), '#/threads/new', 'a refused upload stays to try again');
    assert.equal(await nmChips.count(), 1, 'the file waits');
    await page.locator('.nm-composer .p-send').click();
    await page.waitForFunction(() => location.hash.startsWith('#/thread/'), null, {timeout: WAIT});
    const retried = decodeURIComponent((await page.evaluate(() => location.hash)).slice('#/thread/'.length));
    const named = ((await api(hub, 'dana', 'GET', '/v2/threads?limit=200')).threads as Thread[]).filter(t => t.title === 'shots');
    assert.deepEqual(named.map(t => t.id), [retried], 'one thread named shots');
    assert.ok((await attached(hub, retried)).some(m => m.files.includes('retry.png')), 'the file went in on the retry');

    // Omar and a bot, no thread yet: a name is asked for, then a private thread, pinned for its people, takes the message.
    await openPane(page);
    await add(page, 'om', 'Omar');
    await add(page, 'engineering', 'engineering-manager');
    await page.locator('#nm-list li[aria-selected="true"].nm-new').waitFor({timeout: WAIT});
    assert.deepEqual(await rows(page), ['New thread']);
    await page.locator('.nm-composer .p-text').fill('Kickoff at ten');
    await page.locator('.nm-composer .p-send').click();
    await page.locator('#nm-title[aria-invalid="true"]').waitFor({timeout: WAIT});
    assert.equal(await page.evaluate(() => location.hash), '#/threads/new', 'no name, no thread');
    await shot(page, 'name');
    await page.locator('#nm-title').fill('eng-sync');
    await page.locator('#nm-title').press('Enter');
    await page.waitForFunction(() => location.hash.startsWith('#/thread/'), null, {timeout: WAIT});
    await page.locator('.th-msg', {hasText: 'Kickoff at ten'}).waitFor({timeout: WAIT});
    const made = decodeURIComponent((await page.evaluate(() => location.hash)).slice('#/thread/'.length));
    const view = await api(hub, 'dana', 'GET', '/v2/threads/' + made);
    assert.equal(view.title, 'eng-sync');
    assert.equal(view.audience, 'members');
    assert.equal(view.me.pinned, true);
    assert.deepEqual((view.members.members as {actor: string}[]).map(m => m.actor).sort(), ['bot:engineering-manager', 'human:dana', 'human:omar']);

    // A #thread picked from the To box opens it.
    await openPane(page);
    await page.locator('#nm-q').fill('all h');
    await page.locator('#nm-list li .nm-name', {hasText: 'All hands'}).click();
    await hashIs(page, '#/thread/' + hall.id);
    assert.deepEqual(errors, []);
    await page.context().close();

    // Phone: the pane takes the screen, with a back arrow to the rail; a teammate's DM button opens it with them chosen.
    const phone = await pageAs(browser, hub, 'dana', {phone: true});
    await phone.goto(hub.url + '/#/threads');
    await phone.locator('#thread-rail [data-tr-new]').tap();
    await hashIs(phone, '#/threads/new');
    // New message is a page part (ui/parts.json): its pane draws once that code has loaded.
    await phone.locator('.tr-pane #nm-list').waitFor();
    const box = await phone.locator('.tr-pane').boundingBox();
    assert.ok(box && box.width >= 380, 'the pane is the screen: ' + JSON.stringify(box));
    assert.equal(await phone.locator('#thread-rail').isVisible(), false);
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    await phone.locator('#nm-q').fill('pri');
    await phone.locator('#nm-list li .nm-name', {hasText: 'Priya'}).waitFor({timeout: WAIT});
    await shot(phone, 'phone', ['dark']);
    await phone.locator('.nm-head .thv-back-rail').tap();
    await hashIs(phone, '#/threads');
    await phone.goto(hub.url + '/#/person/priya');
    await phone.locator('#person-dm').tap();
    await phone.locator('.nm-chip', {hasText: 'Priya'}).waitFor({timeout: WAIT});
    await phone.locator('#nm-list li[aria-selected="true"] .nm-name', {hasText: 'Priya'}).waitFor({timeout: WAIT});
    await shot(phone, 'phone-dm', ['light']);
    await phone.context().close();
    console.log('new-message: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
