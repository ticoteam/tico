// A file review on the question card (ui/app/question-card.js, backend/file_reviews.py): the versions under review show
// inline, four at most and the rest as chips; Request changes waits for the note that says what to change; in a pick
// each option sits under its own picture, and the spend a pick approves is said under them.
// REVIEW_SHOTS=<dir> also saves the Decide card at desktop and phone width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const {open} = require('./tasks-page.cjs');
const SHOTS = process.env.REVIEW_SHOTS || '';
const NOW = Date.now(), at = hours => new Date(NOW - hours * 3600e3).toISOString();

// A plain 16:9 picture in one colour, so each card is told apart in the screenshots.
function png(r, g, b, w = 320, h = 180) {
  const crc = buf => { let c = ~0; for (const x of buf) { c ^= x; for (let k = 0; k < 8; k++) c = c >>> 1 ^ (0xedb88320 & -(c & 1)); } return ~c >>> 0; };
  const chunk = (type, data) => { const len = Buffer.alloc(4), sum = Buffer.alloc(4), body = Buffer.concat([Buffer.from(type), data]);
    len.writeUInt32BE(data.length); sum.writeUInt32BE(crc(body)); return Buffer.concat([len, body, sum]); };
  const head = Buffer.alloc(13); head.writeUInt32BE(w, 0); head.writeUInt32BE(h, 4); head[8] = 8; head[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(w * 3).fill(Buffer.from([r, g, b]))]);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', head),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(Array(h).fill(row)))), chunk('IEND', Buffer.alloc(0))]);
}
const COLOURS = [[214, 120, 64], [72, 132, 201], [96, 170, 110], [180, 90, 160], [200, 180, 70]];
const file = (n, title) => ({id: `file-${n}`, file_id: `file-${n}`, version: 2, ref: `file-${n}@2`, title, kind: 'image',
  name: `${title.toLowerCase().replace(/ /g, '-')}.png`, size: 4000, content_type: 'image/png', width: 1280, height: 720,
  url: `/api/v2/files/file-${n}?v=2`, src: `/visuals/tok${n}`});

function reviewItem(id, files, {pick = false, spend = null} = {}) {
  const options = pick
    ? [...files.map(f => ({label: f.title, then: 'This one is approved'})), {label: 'Request changes', then: 'Your note on what to change goes back'}]
    : [{label: 'Approve', then: 'This version is approved'}, {label: 'Request changes', then: 'Your note on what to change goes back'}];
  const question = pick ? 'Which thumbnail goes out?' : 'Is this cut ready?';
  return {id, kind: 'question', title: question, body: '', owner: 'bot:writer', requester: 'bot:writer', status: 'waiting',
    waiting_on: 'human:ana', version: 1, created_at: at(3), updated_at: at(3), open_asks: 1, labels: [], tags: [], links: [],
    lane: 'company', rank: null, parts: {total: 0, done: 0}, tier: 3,
    ask: {id: 'q-' + id, from_actor: 'bot:writer', to_actor: 'human:ana', body: question, created_at: at(3),
      refs: {task: id, questions: [{id: 'decision', header: '', question, multi: false, other: true, options}],
        review: {files: files.map(f => ({file: f.file_id, version: f.version, digest: 'd'.repeat(64), ...(pick ? {label: f.title} : {})})),
          ...(pick ? {pick: true} : {})},
        attachments: files, ...(spend ? {spend} : {})}}};
}

const cut = reviewItem('t-cut', ['Opening', 'Kitchen', 'Bathroom', 'Patio', 'Credits'].map((t, i) => file(i, t)));
const thumbs = reviewItem('t-thumbs', [file(0, 'Bright kitchen'), file(1, 'Blue door')],
  {pick: true, spend: {label: 'Bright kitchen', amount: '12.50', account: 'gateway', what: 'Render the final cut'}});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    let items = [cut];
    const {page, errors} = await open(browser, {viewport: {width: 1440, height: 1000}, hash: '#/tasks', extraTasks: [cut, thumbs].map(x => ({...x}))});
    const answers = [];
    await page.route('**/visuals/**', route => {
      const n = Number(new URL(route.request().url()).pathname.replace(/\D/g, '')) % COLOURS.length;
      return route.fulfill({contentType: 'image/png', body: png(...COLOURS[n])});
    });
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.route('**/api/v2/tasks/*/answers', route => {
      answers.push({task: decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2)), ...route.request().postDataJSON()});
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    const shoot = async name => {
      if (!SHOTS) return;
      fs.mkdirSync(SHOTS, {recursive: true});
      for (const [width, height, size] of [[1440, 1000, 'desktop'], [390, 900, 'phone']]) {
        await page.setViewportSize({width, height});
        await page.waitForTimeout(150);
        await page.screenshot({path: path.join(SHOTS, `${name}-${size}.png`)});
      }
      await page.setViewportSize({width: 1440, height: 1000});
    };
    const ready = () => page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));

    // Four of the five versions show as pictures, the fifth as a chip.
    await page.evaluate(async () => { await v2Refresh(); decideOpen('task:t-cut'); });
    await page.locator('.decide .dc-card[data-key="task:t-cut"]').waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.decide .qc-review .vcard img')].filter(i => i.complete && i.naturalWidth).length === 4);
    const shown = await page.evaluate(() => ({
      cards: [...document.querySelectorAll('.decide .qc-review .vcard')].map(c => c.querySelector('.vcard-title').textContent),
      chips: [...document.querySelectorAll('.decide .qc-review .qc-rchips .dc-file .dc-fname')].map(x => x.textContent),
      buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => b.querySelector('.qc-bl')?.textContent.replace(/[A-Z0-9]$/, '').trim())}));
    assert.deepEqual(shown, {cards: ['Opening', 'Kitchen', 'Bathroom', 'Patio'], chips: ['Credits'],
      buttons: ['Approve', 'Request changes']});
    await shoot('review');

    // Request changes with no note asks for it and sends nothing; with the note it goes.
    await ready();
    await page.locator('.decide .dc-actions [data-dc="pick"][data-i="1"]').click();
    const asked = await page.evaluate(() => ({focused: document.activeElement === document.querySelector('.decide .dc-note textarea'),
      placeholder: document.querySelector('.decide .dc-note textarea').placeholder}));
    assert.deepEqual(asked, {focused: true, placeholder: 'What should change?'});
    assert.equal(answers.length, 0);
    await page.keyboard.type('Trim the patio shot.');
    await ready();
    await page.locator('.decide .dc-actions [data-dc="pick"][data-i="1"]').click();
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-cut', target: {comment: 'q-t-cut'}, answers: {decision: ['Request changes']},
      other: 'Trim the patio shot.'});

    // A pick: each option under its own picture, then Request changes, and the spend the pick approves.
    items = [thumbs];
    await page.evaluate(async () => { decideStop(); await v2Refresh(); decideOpen('task:t-thumbs'); });
    await page.locator('.decide .dc-card[data-key="task:t-thumbs"]').waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.decide .qc-rpick .vcard img')].filter(i => i.complete && i.naturalWidth).length === 2);
    const picks = await page.evaluate(() => ({
      picks: [...document.querySelectorAll('.decide .qc-rpick')].map(p => [p.querySelector('.vcard-title').textContent, p.querySelector('.qc-bl').textContent.trim()]),
      changes: document.querySelector('.decide .qc-changes')?.textContent.trim(),
      spend: document.querySelector('.decide .qc-spend')?.textContent}));
    // Each option is its own button: the action row keeps only Skip.
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.decide .dc-actions button')].map(b => b.textContent.replace(/[A-Z]$/, '').trim())), ['Skip']);
    assert.deepEqual(picks, {picks: [['Bright kitchen', '1 Bright kitchen'], ['Blue door', '2 Blue door']], changes: 'Request changes',
      spend: 'Bright kitchen also approves spending 12.50 (gateway): Render the final cut'});
    await shoot('review-pick');
    await ready();
    await page.locator('.decide .qc-rpick [data-dc="pick"][data-i="0"]').click();
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-thumbs', target: {comment: 'q-t-thumbs'}, answers: {decision: ['Bright kitchen']}});

    // The task page's Needs you box shows the same files.
    items = [cut];
    await page.evaluate(async () => { decideStop(); location.hash = '#/task/t-cut'; await v2Refresh(); });
    await page.locator('.qcard[data-qcard="t-cut"] .qc-review .vcard').first().waitFor();
    assert.equal(await page.locator('.qcard[data-qcard="t-cut"] .qc-review .vcard').count(), 4);
    assert.deepEqual(errors, []);
    console.log('File review card: four versions inline and the rest as chips, Request changes waits for its note, '
      + 'a pick shows each option under its picture with the spend it approves, same files on the task page');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
