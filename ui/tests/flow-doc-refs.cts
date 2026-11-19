// Flow on a person's @ of Ana on a task whose text and chat name documents by reference (ui/app/decide.ts,
// decide-about.ts, markdown.ts docRefs), at 1200 px and on a 390 px phone, light and dark:
// - `page:<file id>@<n>` and `file:<file id>@<n>` in the task's text and in a chat message are chips with the
//   document's title and its version that open it in the review view, never the raw reference;
// - the task's chips name each file once: no "Page v1" chips, and a file attached twice is one chip;
// - the @ opens in its chat on the right (the message in view and the composer), with no headline repeating it and no
//   quote of it on the left; a reply goes to that chat.
// TICO_UI_SHOTS=<folder> saves each screen before the checks. Fixtures only (tasks-page.cts).
import type {Page, Route} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const fs: typeof import('node:fs') = require('node:fs');
const {open, fixtures, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Need = import('./tasks-page.cts').Need;
declare function decideOpen(id?: string): boolean;

const SPEC = 'file-0123456789abcdef01234567', ORIGINAL = 'file-89abcdef0123456789abcdef', SHOT = 'file-fedcba9876543210fedcba98';
const TITLES: Record<string, string> = {[SPEC]: 'Checkout spec, restored', [ORIGINAL]: 'Checkout spec'};
const AT = '2026-11-15T09:00:00Z';
const SAID = 'Ana, the spec is page:' + SPEC + '@2. Can you look before Friday?';

function world() {
  const tasks = fixtures();
  Object.assign(tasks.find(t => t.id === 't-1')!, {
    request: {text: `Make the checkout one page.\n\nSpec: page:${SPEC}@2\n\nIt copies page:${ORIGINAL}@1 and keeps file:${SHOT}@1 as the proof.`},
    attachments: [{id: 'f-shot', name: 'checkout-320.png'}, {id: 'f-shot', name: 'checkout-320.png'}, {id: 'f-man', name: 'manifest.json'}]});
  const ask = needsOf(tasks).find(n => n.kind === 'ask')!;
  const mention: Need = {kind: 'mention', id: 'm-sam', message_id: 'm-sam', conversation_id: 'c-t-1', root_id: 'c-t-1', task_id: 't-1',
    from_id: 'human:sam', line: SAID, since: AT, task: ask.task ?? null};
  return {tasks, needs: [mention, ask], comments: {'t-1': [{id: 'm-sam', kind: 'say', sender_id: 'human:sam', body: SAID, created_at: AT, refs: {task: 't-1'}}]}};
}
// The documents' own reads (GET /api/v2/files/{id}/info): SHOT is not the reader's, so its chip stays "Document".
const files = (page: Page) => page.route('**/api/v2/files/*/info', (route: Route) => {
  const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2) || '');
  const title = TITLES[id];
  return title ? route.fulfill({contentType: 'application/json', body: JSON.stringify({file: {id, title, name: 'spec.md', version: 2}})})
    : route.fulfill({status: 404, contentType: 'application/json', body: JSON.stringify({error: 'not_found', detail: 'Not found'})});
});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  const shots = process.env.TICO_UI_SHOTS;
  try {
    for (const viewport of [{width: 1200, height: 820}, {width: 390, height: 844}]) {
      for (const theme of ['light', 'dark']) {
        const phone = viewport.width <= 390, at = `${phone ? 'phone' : 'desktop'} ${theme}`;
        const {page, errors, posts} = await open(browser, {...world(), viewport, theme, beforeGoto: files});
        try {
          const card = page.locator('#need-card[open]'), chat = card.locator('.dc-side .dc-chat');
          await page.evaluate(() => { decideOpen('m-sam'); });
          await chat.locator('.th-msg, .thv-msg, [data-id="m-sam"]').first().waitFor({timeout: 8000}).catch(() => {});
          await page.waitForFunction(t => [...document.querySelectorAll('#need-card .md-doc-ref-t')].some(e => e.textContent === t), TITLES[SPEC], {timeout: 8000});
          await page.locator('.dc-start').waitFor({state: 'detached', timeout: 3000}).catch(() => {});
          await page.waitForTimeout(200);
          if (shots) { fs.mkdirSync(shots, {recursive: true}); await page.screenshot({path: path.join(shots, `flow-doc-refs-${at.replace(' ', '-')}.png`)}); }

          // The @ is its chat: the composer under it, no headline of its words beside it, no quote of it on the left.
          assert.equal(await card.locator('.dc-side .dc-q').count(), 0, `${at}: no headline repeats the message`);
          assert.equal(await card.locator('.dc-thing .dc-quote').count(), 0, `${at}: no quote of the message on the left`);
          assert.equal(await chat.locator('.p-text').count(), 1, `${at}: the chat's composer answers it`);
          // In the chat, the message's reference is its document's chip.
          const said = chat.locator('a.md-doc-ref');
          assert.ok(await said.count() >= 1, `${at}: the message names the document as a chip`);
          assert.match(await said.first().innerText(), /Checkout spec, restored\s*v2/, `${at}: its title and version`);
          assert.ok(!/page:file-/.test(await chat.innerText()), `${at}: no raw reference in the chat`);

          if (!phone) {
            const text = card.locator('.dc-thing .dc-md');
            const refs = await text.locator('a.md-doc-ref').evaluateAll(as => as.map(a => [a.textContent?.replace(/\s+/g, ' ').trim(), a.getAttribute('href')]));
            assert.deepEqual(refs, [
              [`${TITLES[SPEC]} v2`, `#/file/${SPEC}?v=2`],
              [`${TITLES[ORIGINAL]} v1`, `#/file/${ORIGINAL}?v=1`],
              ['Document v1', `#/file/${SHOT}?v=1`],
            ], 'each reference is a chip with its title and version, opening the document');
            assert.ok(!/(?:page|file):file-/.test(await text.innerText()), 'no raw reference in the task text');
            const chips = await card.locator('.dc-thing .dc-refs a').allInnerTexts();
            assert.deepEqual(chips, ['checkout-320.png', 'manifest.json'], 'each file once, and no "Page v1" chips');
            // A reply goes to the @'s chat, and Flow moves on.
            await chat.locator('.p-text').fill('Looking now.');
            await chat.locator('.p-send').click();
            await page.waitForFunction('typeof DEC !== "undefined" && !!DEC && DEC.at !== "m-sam"', null, {timeout: 5000});
            assert.ok(posts.some(x => x.p === '/api/v2/threads/c-t-1/messages' && x.body.text === 'Looking now.'), 'the reply is a message of that chat');
          } else {
            // On a phone Skip rides on the title row, as a ❓'s Snooze does: the composer is the bottom of the screen.
            const skip = card.locator('.dc-side [data-dc="skip"]'), title = card.locator('.dc-side > .dc-tabs');
            const [s, t] = await Promise.all([skip.boundingBox(), title.boundingBox()]);
            assert.ok(s && t && s.y < t.y + t.height, `${at}: Skip sits on the title row (${JSON.stringify([s, t])})`);
          }
          assert.deepEqual(errors, [], `${at}: no page errors`);
        } finally { await page.close(); }
      }
    }
    console.log('PASS: Flow names referenced documents by title and version, lists each file once, and opens an @ in its chat.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
