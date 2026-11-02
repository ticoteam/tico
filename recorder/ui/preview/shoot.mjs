// Renders the preview scenes with Playwright (WebKit, as on macOS) into ui/screenshots/ and
// checks a few behaviours. Usage, from native/:
//   NODE_PATH=<dir with playwright> node ui/preview/shoot.mjs [scene ...]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { webkit, chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// WebKit is the macOS engine; TICO_PREVIEW_BROWSER=chromium runs the same scenes where WebKit's libraries are missing.
const engine = process.env.TICO_PREVIEW_BROWSER === 'chromium' ? chromium : webkit;
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const out = path.join(root, 'screenshots');
fs.mkdirSync(out, { recursive: true });

const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png' };
const server = http.createServer((req, res) => {
  const file = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/preview/index.html`;

const open = (id) => async (page) => {
  await page.click(`.row[data-id="${id}"]`);
  await page.waitForSelector('.title-input');
};
const transcript = async (page) => {
  await page.click('[data-act="toggle-transcript"]');
  await page.waitForSelector('#tbody > *');
};
const toChat = async (page) => page.evaluate(() => document.getElementById('chat').scrollIntoView({ block: 'end' }));
const scenes = {
  library: [],
  empty: [],
  signing: [],
  connect: [],
  demo: [],
  meeting: [open('m1')],
  owner: [open('m1'), async (page) => page.click('.owner.empty')],
  transcript: [open('m1'), transcript],
  live: [open('m2'), async (page) => page.click('.people-btn')],
  // "ti", then ↓ twice: the third match is lit (names with a word starting "ti" come first).
  picker: ['live', open('m2'), async (page) => page.click('.people-btn'), async (page) => {
    await page.keyboard.type('ti');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
  }],
  adding: [open('m2'), async (page) => page.click('.people-btn')],
  avatars: [open('m2')],
  mention: [open('m1'), async (page) => {
    await page.click('textarea.notes');
    await page.keyboard.press('Meta+ArrowDown');
    await page.keyboard.type('\nAsk @ma');
  }],
  mentions: [open('m1')],
  // Near the top of the notes the list opens below the caret.
  'mention-top': ['meeting', open('m1'), async (page) => {
    await page.click('textarea.notes');
    await page.keyboard.press('Meta+ArrowUp');
    await page.keyboard.type('@');
  }],
  notice: [open('m0')],
  // Search marks matches in place: the second of several is lit.
  search: ['meeting', open('m1'), transcript, async (page) => {
    await page.keyboard.press('Meta+f');
    await page.keyboard.type('item');
    await page.keyboard.press('Enter');
  }],
  // Drafts and removal questions belong to the meeting they were started in: an open edit does not
  // follow the person to a second shared meeting, and a removal question does not either.
  'shared-two': ['shared', open('m2'), transcript, async (page) => {
    const openOther = async (id) => {
      await page.click('[data-act="back"]');
      await page.click(`.row[data-id="${id}"]`);
      await page.waitForSelector('.title-input');
      await transcript(page);
    };
    await page.hover('.bubble:has([data-act="edit-line"][data-seq="1"])');
    await page.click('[data-act="edit-line"][data-seq="1"]');
    await page.fill('textarea.line-edit', 'Draft for the first call');
    await openOther('m3');
    if ((await page.locator('textarea.line-edit').count()) !== 0) throw new Error('an edit draft followed to another meeting');
    await openOther('m2');
    await page.hover('.bubble:has([data-act="ask-remove-line"][data-seq="2"])');
    await page.click('[data-act="ask-remove-line"][data-seq="2"]');
    await openOther('m3');
    if ((await page.getByText('Remove this line in Tico?').count()) !== 0) throw new Error('a removal question followed to another meeting');
  }],
  // Sent lines of a live meeting: edit one (the draft survives a refresh), then remove another.
  shared: ['shared', open('m2'), transcript, async (page) => {
    if ((await page.locator('[data-act="edit-line"]').count()) !== 2) throw new Error('two sent lines should be editable');
    await page.hover('.bubble:has([data-act="edit-line"][data-seq="1"])');
    await page.click('[data-act="edit-line"][data-seq="1"]');
    await page.fill('textarea.line-edit', 'Hi there, shall we start?');
    await page.waitForTimeout(1200);
    if ((await page.inputValue('textarea.line-edit')) !== 'Hi there, shall we start?') throw new Error('edit draft lost');
    await page.click('[data-act="save-line"]');
    await page.waitForSelector('.turn .bubble p:text("Hi there, shall we start?")');
    await page.hover('.bubble:has([data-act="ask-remove-line"][data-seq="2"])');
    await page.click('[data-act="ask-remove-line"][data-seq="2"]');
    await page.click('[data-act="remove-line"]');
    await page.waitForSelector('.turn.removed');
  }],
  sending: [open('m2')],
  recording: [open('m0'), transcript],
  retrying: [open('m0'), transcript],
  'error-library': ['error'],
  error: [open('m1'), transcript, async (page) => page.click('[data-act="toggle-audio"]')],
  'chat-live': ['live', open('m2'), async (page) => page.evaluate(() => document.getElementById('chat').scrollIntoView({ block: 'end' }))],
  'chat-private': [open('m1'), async (page) => page.evaluate(() => document.getElementById('chat').scrollIntoView({ block: 'end' }))],
  'chat-first': [open('m1'), async (page) => page.evaluate(() => document.getElementById('chat').scrollIntoView({ block: 'end' })), async (page) => {
    await page.click('[data-model="ask"]');
    await page.keyboard.type('Can someone own the hosted pricing cap?');
  }],
  'chat-first-sent': ['chat-first', open('m1'), async (page) => {
    await page.click('[data-model="ask"]');
    await page.keyboard.type('Can someone own the hosted pricing cap?');
    await page.keyboard.press('Enter');
  }],
  settings: [async (page) => page.click('[data-act="settings"]')],
  'settings-own': [async (page) => page.click('[data-act="settings"]')],
  'settings-nostream': [async (page) => page.click('[data-act="settings"]')],
  notices: [open('m2'), toChat],
  long: [open('m2')],
  'long-library': ['long'],
  'long-chat': ['long', open('m2'), toChat],
  'long-people': ['long', open('m2'), async (page) => page.click('.people-btn')],
  sources: [async (page) => page.click('.caret')],
};
const sizes = [[1040, 690], [760, 490], [640, 460]];
const themes = ['light', 'dark'];
const only = process.argv.slice(2);
const browser = await engine.launch();
const shots = [];
const errors = [];
for (const [name, steps] of Object.entries(scenes)) {
  if (only.length && !only.includes(name)) continue;
  const scene = typeof steps[0] === 'string' ? steps[0] : name;
  for (const theme of themes) {
    for (const [width, height] of sizes) {
      const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, colorScheme: theme });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`${name}: ${error.message}`));
      page.on('console', (msg) => msg.type() === 'error' && errors.push(`${name}: ${msg.text()}`));
      await page.goto(`${base}?scene=${scene}`);
      await page.waitForSelector('#r-page > *');
      for (const step of steps) if (typeof step === 'function') await step(page);
      await page.mouse.move(width - 2, height - 2);
      await page.waitForTimeout(450);
      const file = path.join(out, `${name}-${width}x${height}-${theme}.png`);
      await page.screenshot({ path: file });
      shots.push(file);
      await context.close();
    }
  }
}

// The top bar's avatar stack, close up.
if (!only.length || only.includes('avatars')) {
  for (const theme of themes) {
    const context = await browser.newContext({ viewport: { width: 1040, height: 690 }, deviceScaleFactor: 4, colorScheme: theme });
    const page = await context.newPage();
    await page.goto(`${base}?scene=avatars`);
    await page.waitForSelector('.row');
    await open('m2')(page);
    for (const hover of [false, true]) {
      if (hover) await page.hover('.people-btn');
      const file = path.join(out, `avatars-zoom${hover ? '-hover' : ''}-${theme}.png`);
      await page.locator('.people-btn').screenshot({ path: file });
      shots.push(file);
    }
    await context.close();
  }
}

// Behaviour: delete asks first, and only Delete removes the row.
if (!only.length || only.includes('checks')) {
  const context = await browser.newContext({ viewport: { width: 1040, height: 690 } });
  const page = await context.newPage();
  await page.goto(`${base}?scene=library`);
  await page.waitForSelector('.row');
  const rows = await page.locator('.row').count();
  await page.hover('.row[data-id="m5"]');
  await page.click('.row[data-id="m5"] [data-act="ask-delete"]');
  const asking = await page.locator('.row.confirm').innerText();
  if (!asking.includes('Removes its audio and transcript from this Mac.')) errors.push('delete: no confirmation');
  if ((await page.locator('.row').count()) !== rows) errors.push('delete: removed before confirming');
  await page.click('.row.confirm [data-act="cancel-delete"]');
  if ((await page.locator('.row').count()) !== rows) errors.push('delete: cancel removed the row');
  await page.hover('.row[data-id="m5"]');
  await page.click('.row[data-id="m5"] [data-act="ask-delete"]');
  await page.click('.row.confirm [data-act="delete"]');
  await page.waitForTimeout(100);
  if ((await page.locator('.row').count()) !== rows - 1) errors.push('delete: confirmed delete kept the row');
  // Sending an action item marks it sent with the owner's name.
  await page.click('.row[data-id="m1"]');
  await page.waitForSelector('.item');
  await page.hover('.item:has-text("Confirm the transcript link")');
  await page.click('.item:has-text("Confirm the transcript link") [data-act="send-item"]');
  await page.waitForSelector('.item.sent:has-text("Sent to Sam Okafor")');
  // Esc goes back to the library.
  await page.keyboard.press('Escape');
  await page.waitForSelector('.lib-head');
  await context.close();

  // People picker: ↑/↓ move the highlight, hover moves it too, Enter adds the lit one at once.
  const people = await (await browser.newContext({ viewport: { width: 1040, height: 690 } })).newPage();
  people.on('pageerror', (error) => errors.push(`checks: ${error.message}`));
  await people.goto(`${base}?scene=live`);
  await people.waitForSelector('.row');
  await open('m2')(people);
  await people.click('.people-btn');
  await people.keyboard.type('ti');
  const lit = () => people.locator('#popover [data-opt].hot').innerText();
  if ((await lit()) !== 'Tico Support') errors.push(`picker: first lit is ${await lit()}`);
  await people.keyboard.press('ArrowDown');
  await people.keyboard.press('ArrowDown');
  if ((await lit()) !== 'Marketing Lead') errors.push(`picker: ↓↓ lit ${await lit()}`);
  await people.keyboard.press('ArrowUp');
  if ((await lit()) !== 'Tina Alvarez') errors.push(`picker: ↑ lit ${await lit()}`);
  await people.hover('#popover [data-opt]:has-text("Justin Kim")');
  if ((await lit()) !== 'Justin Kim') errors.push(`picker: hover lit ${await lit()}`);
  await people.keyboard.press('ArrowUp');
  await people.keyboard.press('Enter');
  await people.waitForSelector('#popover .person.pending:has-text("Martin Diaz")', { timeout: 500 }).catch(() => errors.push('picker: no pending row at once'));
  await people.waitForSelector('#popover .person:not(.pending):has-text("Martin Diaz")');
  await people.keyboard.press('Escape');
  if (await people.locator('#popover').count()) errors.push('picker: Esc left it open');

  // @mention in Notes: filter, ↓, Enter inserts "@Name " as plain text.
  await people.keyboard.press('Escape');
  await open('m1')(people).catch(async () => {
    await people.goto(`${base}?scene=meeting`);
    await people.waitForSelector('.row');
    await open('m1')(people);
  });
  await people.click('textarea.notes');
  await people.keyboard.press('Meta+ArrowDown');
  await people.keyboard.type(' @ma');
  const names = await people.locator('#mention [data-opt]').evaluateAll((rows) => rows.map((row) => row.dataset.name));
  if (names.join('|') !== 'Marketing Lead|Mara Novak|Martin Diaz') errors.push(`mention: listed ${names.join('|')}`);
  await people.keyboard.press('ArrowDown');
  await people.keyboard.press('Enter');
  const notes = await people.locator('textarea.notes').inputValue();
  if (!notes.endsWith(' @Mara Novak ')) errors.push(`mention: notes end ${JSON.stringify(notes.slice(-30))}`);
  if (await people.locator('#mention').count()) errors.push('mention: list stayed open');
  if ((await people.locator('.notes-backdrop mark').allInnerTexts()).join('|') !== '@Mara Novak') errors.push('mention: no chip');
  await people.keyboard.type('@ju');
  await people.keyboard.press('Escape');
  if (await people.locator('#mention').count()) errors.push('mention: Esc left it open');

  // The @ list opens below the caret when it fits there.
  await people.goto(`${base}?scene=meeting`);
  await people.waitForSelector('.row');
  await open('m1')(people);
  await people.click('textarea.notes');
  await people.keyboard.press('Meta+ArrowUp');
  await people.keyboard.type('@');
  const area = await people.locator('textarea.notes').boundingBox();
  const list = await people.locator('#mention').boundingBox();
  if (!list || list.y < area.y + 10) errors.push(`mention: opened above the caret (list ${list?.y}, notes ${area.y})`);
  await people.keyboard.press('Escape');

  // Transcript search never reflows the list: rows stay put while typing, and matches are counted.
  await people.click('[data-act="toggle-transcript"]');
  await people.waitForSelector('#tbody > *');
  const before = await people.locator('#tbody > *').count();
  const height = await people.evaluate(() => document.getElementById('tbody').scrollHeight);
  await people.keyboard.press('Meta+f');
  for (const key of 'item') {
    await people.keyboard.type(key);
    const rows = await people.locator('#tbody > *').count();
    const now = await people.evaluate(() => document.getElementById('tbody').scrollHeight);
    if (rows !== before || now !== height) errors.push(`search: layout moved at "${key}" (${rows} rows, ${now}px)`);
  }
  const counted = await people.locator('#thits').innerText();
  if (!/^1\/\d+$/.test(counted)) errors.push(`search: counter reads ${counted}`);
  await people.keyboard.press('Enter');
  if (!(await people.locator('#thits').innerText()).startsWith('2/')) errors.push('search: Enter did not move to the next match');

  // The mic notice closes and stays closed.
  await people.goto(`${base}?scene=notice`);
  await people.waitForSelector('.row');
  await open('m0')(people);
  await people.click('[data-act="dismiss-notice"]');
  await people.waitForTimeout(400);
  if (await people.locator('.dock-notice').count()) errors.push('notice: still shown after closing');

  // Send on a live meeting finishes it and sends it, showing the step on the button.
  await people.goto(`${base}?scene=live`);
  await people.waitForSelector('.row');
  await open('m2')(people);
  await people.click('[data-pop="send"]');
  await people.click('[data-act="send-meeting"]');
  await people.waitForSelector('.topbar .sending:has-text("Finishing…")');
  await people.waitForSelector('.topbar .badge:has-text("In Tico")', { timeout: 4000 });
  if (await people.locator('[data-act="finish"]').count()) errors.push('send: Finish meeting still offered after sending');

  // Bot-turn notices sit between the messages in time order; only the failed one is in the warning tone.
  await people.goto(`${base}?scene=notices`);
  await people.waitForSelector('.row');
  await open('m2')(people);
  const notices = await people.locator('#chat > *').evaluateAll((rows) => rows.map((row) => row.className.includes('chat-notice') ? (row.className.includes('failed') ? 'F' : 'N') : row.className.includes('msg') ? 'm' : ''));
  if (notices.filter(Boolean).join('') !== 'mNmmmmNNmmFm') errors.push(`notices: order ${notices.join('')}`);

  // Speech: Your Tico hides the key fields; Own keys shows them and makes the form savable.
  await people.goto(`${base}?scene=settings`);
  await people.waitForSelector('.row');
  await people.click('[data-act="settings"]');
  if (await people.locator('[data-model="key"]').count()) errors.push('speech: key field shown under Your Tico');
  await people.click('[data-act="speech-source"][data-source="own"]');
  if (!(await people.locator('[data-model="key"]').count())) errors.push('speech: no key field under Own keys');
  if (await people.locator('[data-act="save-settings"]').isDisabled()) errors.push('speech: switching source left Save off');
  await people.click('[data-act="save-settings"]');
  await people.waitForTimeout(400);
  if ((await people.evaluate(() => window.__PREVIEW__.state.status.speech.source)) !== 'own') errors.push('speech: source not saved');

  // Library rows open from the keyboard.
  await people.goto(`${base}?scene=library`);
  await people.waitForSelector('.row');
  await people.focus('.row[data-id="m1"]');
  await people.keyboard.press('Enter');
  await people.waitForSelector('.title-input', { timeout: 1000 }).catch(() => errors.push('keyboard: Enter on a row did not open it'));
}
await browser.close();
server.close();
console.log(shots.join('\n'));
if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log('checks ok');
