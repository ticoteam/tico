// Renders the preview scenes with Playwright (WebKit, as on macOS) into ui/screenshots/ and
// checks a few behaviours. Usage, from recorder/:
//   PLAYWRIGHT=<dir>/node_modules/playwright node ui/preview/shoot.mjs [scene ...|checks]
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
const settingsPage = async (page) => page.click('[data-act="settings"]');
const share = async (page) => { await page.click('[data-act="share-open"]'); await page.waitForSelector('#sheet.share'); };
const finish = async (page) => { await page.click('.topbar [data-act="finish-open"]'); await page.waitForSelector('#sheet.finish'); };
const more = async (page) => page.click('[data-pop="more"]');
const scenes = {
  library: [],
  signin: [],
  'signin-address': [],
  signing: [],
  connect: [],
  demo: [],
  meeting: [open('m1')],
  // A saved meeting's live task card: Title and Owner, then Send.
  'live-card': ['library', async (page) => page.evaluate(() => {
    const a = window.__PREVIEW__.state.meetings.m1.actions;
    a.saved = true; a.shared_url = 'https://hub.tico.team';
    a.shared = { can_send: true, presence: { actors: [] }, sections: { task: [{ version: 1, id: 's1', section: 'task', text: 'Send the pricing recap to Dana', quote: "I'll send Dana the pricing recap.", at_ms: 1150000, status: 'proposed', detail: { owner: 'human:dana', due: '2026-12-01T17:00:00.000Z', body: 'Older details' } }] } };
  }), open('m1')],
  // A created task's row is the way into Tico.
  'task-open': ['meeting', open('m1'), async (page) => { await page.focus('[data-act="add-open"]'); await page.keyboard.press('Tab'); }],
  'task-picker': ['meeting', open('m1'), async (page) => { await page.click('[data-act="task-picker-open"]'); await page.waitForSelector('[data-act="task-pick"]'); await page.click('[data-id="t42"]'); await page.click('[data-id="t44"]'); }],
  owner: [open('m1'), async (page) => page.click('.owner.empty')],
  'add-mention': ['meeting', open('m1'), async (page) => { await page.click('[data-act="add-open"]'); await page.keyboard.type('Send notes to @me'); }],
  'stop-finish': ['recording', open('m0'), async (page) => { await page.click('[data-act="stop"]'); await page.waitForSelector('#sheet.finish'); }],
  recording: [open('m0')],
  retrying: [open('m0')],
  notice: [open('m0')],
  live: [open('m2')],
  paused: [open('m2')],
  // Share live before anything is in Tico: org, who can join, people staged as chips.
  share: ['meeting', open('m1'), share, async (page) => {
    await page.waitForFunction(() => window.__PREVIEW__.state.meetings.m1.people.candidates.length);
    await page.keyboard.type('sa');
    await page.waitForSelector('#sheet [data-opt].hot');
    await page.keyboard.press('Enter');
    await page.keyboard.type('lib');
  }],
  'share-live': ['live-private', open('m2'), share],
  adding: [open('m2'), share],
  finish: ['meeting', open('m1'), finish],
  'finish-live': ['live-private', open('m2'), finish],
  'finish-delete': ['meeting', open('m1'), finish, async (page) => page.click('[data-act="finish-ask-delete"]')],
  'finish-library': ['library', async (page) => { await page.hover('.row[data-id="m1"]'); await page.click('.row[data-id="m1"] [data-act="finish-open"]'); }],
  more: ['live', open('m2'), more],
  'more-discard': ['live', open('m2'), more, async (page) => page.click('[data-confirm="discard"]')],
  discarding: [open('m2')],
  ask: [open('m1')],
  'ask-live': ['live', open('m2'), async (page) => page.evaluate(() => document.querySelector('#tbody .asked').scrollIntoView({ block: 'center' }))],
  'paused-task': [open('m2'), async (page) => page.click('.item:has-text("Confirm the transcript link") [data-act="create-item"]')],
  'screen-sheet': ['live', open('m2'), more, async (page) => page.click('[data-act="share-screen"]')],
  'screen-sharing': ['live', open('m2'), more, async (page) => { await page.click('[data-act="share-screen"]'); await page.click('[data-act="screen-go"]'); await page.waitForSelector('.screen-stop'); }],
  mention: [open('m1'), async (page) => {
    await page.click('textarea.notes');
    await page.keyboard.press('Meta+ArrowDown');
    await page.keyboard.type('\nAsk @ma');
  }],
  mentions: [open('m1')],
  // Search marks matches in place: the second of several is lit.
  search: ['meeting', open('m1'), async (page) => {
    await page.keyboard.press('Meta+f');
    await page.keyboard.type('task');
    await page.keyboard.press('Enter');
  }],
  'no-sidebar': ['meeting', open('m1'), async (page) => page.keyboard.press('Meta+t')],
  // Sent lines of a live meeting: one being edited.
  shared: ['shared', open('m2'), async (page) => {
    await page.hover('.turn:has([data-act="edit-line"][data-seq="1"])');
    await page.click('[data-act="edit-line"][data-seq="1"]');
  }],
  sending: [open('m2')],
  'error-library': ['error'],
  error: [open('m1'), async (page) => page.click('[data-act="toggle-audio"]')],
  settings: [settingsPage],
  'settings-own': [settingsPage],
  'settings-nostream': [settingsPage],
  'settings-add': [settingsPage],
  orgs: ['library', async (page) => page.click('[data-pop="orgs"]')],
  'orgs-add': ['settings-add', async (page) => page.click('[data-pop="orgs"]')],
  long: [open('m2')],
  'long-library': ['long'],
  sources: [async (page) => page.click('.record-pill .caret')],
};
const sizes = [[1040, 690], [760, 490]];
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
      try {
        await page.goto(`${base}?scene=${scene}`);
        await page.waitForSelector('#r-page > *');
        for (const step of steps) if (typeof step === 'function') await step(page);
        await page.mouse.move(width - 2, height - 2);
        await page.waitForTimeout(450);
        // Nothing may push the page sideways.
        const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
        if (wide) errors.push(`${name} ${width}x${height}: horizontal overflow`);
        const file = path.join(out, `${name}-${width}x${height}-${theme}.png`);
        await page.screenshot({ path: file });
        shots.push(file);
      } catch (error) {
        errors.push(`${name} ${width}x${height} ${theme}: ${error.message.split('\n')[0]}`);
      }
      await context.close();
    }
  }
}

// Behaviour checks on the mock controller only.
if (!only.length || only.includes('checks')) {
  const check = async (label, run) => {
    const context = await browser.newContext({ viewport: { width: 1040, height: 690 } });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(`${label}: ${error.message}`));
    try { await run(page); } catch (error) { errors.push(`${label}: ${error.message.split('\n')[0]}`); }
    await context.close();
  };
  const go = async (page, scene) => { await page.goto(`${base}?scene=${scene}`); await page.waitForSelector('#r-page > *'); };
  await check('task links and drafts', async (page) => {
    await go(page, 'meeting'); await open('m1')(page);
    await page.click('[data-act="task-picker-open"]');
    await page.waitForSelector('[data-act="task-pick"]');
    await page.click('[data-id="t42"]'); await page.click('[data-id="t43"]');
    await page.click('[data-act="task-picker-link"]');
    await page.waitForSelector('.item.task:has-text("Review pricing")');
    await page.evaluate(() => { window.__PREVIEW__.state.meetings.m1.actions.items.find((it) => it.id === 't42').pending = true; });
    await page.waitForSelector('.item.task:has-text("Review pricing") .sub:has-text("Linking")');
    if (await page.locator('.item.task:has-text("Review pricing") .check.done').count()) throw Error('pending link shown as committed');
    await page.evaluate(() => { window.__PREVIEW__.state.meetings.m1.actions.items.find((it) => it.id === 't42').pending = false; });
    await page.waitForSelector('.item.task:has-text("Review pricing") .check.done');
    await page.click('.item.task:has-text("Review pricing") [data-act="unlink-item"]');
    await page.waitForSelector('.item.task:has-text("Review pricing")', { state: 'detached' });
    if (!(await page.locator('.item.task:has-text("Test recovery")').count())) throw Error('second link lost');
    if (await page.locator('.item.proposal :text-is("Details")').count()) throw Error('draft still has a Details control');
    // An older draft with a due date and details shows no editor for them.
    await page.evaluate(() => Object.assign(window.__PREVIEW__.state.meetings.m1.actions.items.find((i) => i.id === 'i1'), { due: '2026-12-01T22:00:00.000Z', body: 'Synthetic details' }));
    if (await page.locator('.item.proposal :is(input, textarea, select)').count()) throw Error('draft shows a field editor');
    await page.click('[data-act="create-item"][data-id="i1"]');
    await page.waitForSelector('.item.task:has-text("Confirm the transcript link")');
  });


  // Delete asks first, and only Delete removes the row.
  await check('delete', async (page) => {
    await go(page, 'library');
    const rows = await page.locator('.row').count();
    await page.hover('.row[data-id="m6"]');
    await page.click('.row[data-id="m6"] [data-act="ask-delete"]');
    if (!(await page.locator('.row.confirm').innerText()).includes('Delete')) errors.push('delete: no confirmation');
    if ((await page.locator('.row').count()) !== rows) errors.push('delete: removed before confirming');
    await page.click('.row.confirm [data-act="cancel-delete"]');
    await page.hover('.row[data-id="m6"]');
    await page.click('.row[data-id="m6"] [data-act="ask-delete"]');
    await page.click('.row.confirm [data-act="delete"]');
    await page.waitForTimeout(100);
    if ((await page.locator('.row').count()) !== rows - 1) errors.push('delete: confirmed delete kept the row');
  });

  // Create turns a proposal into a numbered task; × dismisses one; Esc goes back.
  await check('tasks', async (page) => {
    await go(page, 'meeting');
    await open('m1')(page);
    await page.click('.item.proposal:has-text("Confirm the transcript link") [data-act="create-item"]');
    await page.waitForSelector('.item.task:has-text("Confirm the transcript link") .num');
    const before = await page.locator('.item.proposal').count();
    await page.click('.item.proposal:has-text("Draft the calendar") [data-act="remove-item"]');
    await page.waitForTimeout(400);
    if ((await page.locator('.item.proposal').count()) !== before - 1) errors.push('tasks: × did not dismiss');
    if (await page.locator('.item.proposal:has-text("Draft the calendar") [data-act="create-item"]:not([disabled])').count()) errors.push('tasks: Create offered without an owner');
    await page.click('[data-act="add-open"]');
    await page.keyboard.type('Book the follow-up');
    await page.keyboard.press('Enter');
    await page.waitForSelector('.item.proposal:has-text("Book the follow-up")');
    await page.click('#main');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.lib-head');
  });

  // A proposal that may quote paused speech asks twice.
  await check('paused', async (page) => {
    await go(page, 'paused-task');
    await open('m2')(page);
    const create = '.item:has-text("Confirm the transcript link") [data-act="create-item"]';
    await page.click(create);
    if ((await page.locator(create).innerText()) !== 'Create anyway') errors.push('paused: no second ask');
    await page.click(create);
    await page.waitForSelector('.item.task:has-text("Confirm the transcript link")');
  });

  // Share live: nothing reaches Tico until Share live; picks wait, ↑/↓ move the highlight.
  await check('share', async (page) => {
    await go(page, 'meeting');
    await open('m1')(page);
    await share(page);
    await page.waitForFunction(() => window.__PREVIEW__.state.meetings.m1.people.candidates.length);
    if (!(await page.locator('.radio.on').innerText()).includes('Only people I add')) errors.push('share: not private by default');
    await page.keyboard.type('ti');
    await page.waitForSelector('#sheet [data-opt].hot');
    const lit = () => page.locator('#sheet [data-opt].hot').innerText();
    const first = await lit();
    await page.keyboard.press('ArrowDown');
    if ((await lit()) === first) errors.push('share: ↓ did not move');
    await page.keyboard.press('ArrowUp');
    if ((await lit()) !== first) errors.push('share: ↑ did not move back');
    await page.keyboard.press('Enter');
    await page.waitForSelector(`#sheet .chip-person:has-text("${first.trim()}")`);
    if (await page.evaluate(() => window.__PREVIEW__.state.meetings.m1.live)) errors.push('share: went live before Share live');
    await page.click('[data-act="share-go"]');
    await page.waitForSelector('#sheet', { state: 'detached', timeout: 3000 });
    await page.waitForSelector('.share-btn.live');
    const people = await page.evaluate(() => window.__PREVIEW__.state.meetings.m1.people.rows.map((r) => r.name));
    if (!people.includes(first.trim())) errors.push('share: pick not added');
    // Open again: Esc closes the sheet without leaving the meeting.
    await share(page);
    await page.keyboard.press('Escape');
    if (await page.locator('#sheet').count()) errors.push('share: Esc left it open');
    if (!(await page.locator('.title-input').count())) errors.push('share: Esc left the meeting too');
  });

  // Finish: Save to an org for participants only sends that audience; This Mac keeps it local.
  await check('finish', async (page) => {
    await go(page, 'live-private');
    await open('m2')(page);
    await finish(page);
    if (!(await page.locator('#sheet [data-act="finish-audience"].on').innerText()).includes('Participants only')) errors.push('finish: private share did not default to participants');
    await page.fill('[data-model="finish.title"]', 'Weekly product sync (final)');
    await page.click('[data-act="finish-save"]');
    await page.waitForSelector('.topbar .badge:has-text("In Tico")', { timeout: 5000 });
    const row = await page.evaluate(() => window.__PREVIEW__.state.library.rows.find((r) => r.id === 'm2'));
    if (row.title !== 'Weekly product sync (final)') errors.push('finish: title not saved');
    if (row.audience !== 'participants') errors.push(`finish: saved for ${row.audience}, not participants`);
  });
  await check('finish-delete-recording', async (page) => {
    await go(page, 'recording');
    await open('m0')(page);
    const id = 'm0';
    // Stop is the end action while recording: it stops and opens Finish.
    await page.click('[data-act="stop"]');
    await page.click('[data-act="finish-ask-delete"]');
    await page.click('[data-act="finish-delete"]');
    await page.waitForTimeout(300);
    const left = await page.evaluate((rid) => ({ recording: window.__PREVIEW__.state.status.recording, row: window.__PREVIEW__.state.library.rows.some((r) => r.id === rid) }), id);
    if (left.recording || left.row) errors.push('finish: Delete while recording left it recording or listed');
  });
  await check('finish-local', async (page) => {
    await go(page, 'library');
    await page.hover('.row[data-id="m1"]');
    await page.click('.row[data-id="m1"] [data-act="finish-open"]');
    await page.click('[data-act="finish-dest"][data-dest="local"]');
    if (await page.locator('[data-act="finish-audience"]').count()) errors.push('finish: audience shown for This Mac');
    await page.click('[data-act="finish-save"]');
    await page.waitForTimeout(300);
    if (await page.evaluate(() => window.__PREVIEW__.state.library.rows.find((r) => r.id === 'm1').sent)) errors.push('finish: This Mac sent it');
  });

  // Sign in with the desktop app's orgs lands in the library.
  await check('signin', async (page) => {
    await go(page, 'signin');
    if ((await page.locator('.org-row [data-act="signin-desktop"]').count()) !== 3) errors.push('signin: not one Allow per desktop org');
    await page.click('.org-row [data-act="signin-desktop"][data-url="https://tidy.tico.page"]');
    await page.waitForSelector('.lib-head');
    const connected = await page.evaluate(() => window.__PREVIEW__.state.desktop_orgs.filter((org) => org.connected).map((org) => org.name));
    if (connected.join() !== 'TIDY') errors.push(`signin: Allow signed in to ${connected.join() || 'nothing'}, not TIDY`);
  });
  // After the first org, the desktop app's other orgs stay one tap away: Settings and the org menu.
  await check('add-org', async (page) => {
    await go(page, 'settings-add');
    await settingsPage(page);
    const rows = await page.locator('.settings-group [data-act="signin-desktop"]').evaluateAll((b) => b.map((x) => x.dataset.url));
    if (rows.join() !== 'https://tidy.tico.page') errors.push(`add-org: Settings offers ${rows.join() || 'nothing'}, not TIDY`);
    if (!(await page.locator('.other-address [data-model="signin"]').count())) errors.push('add-org: no Other address');
    if (await page.locator('.settings-group .btn.primary[data-act="signin"]').count()) errors.push('add-org: typed address is primary');
    await page.click('.settings-group [data-act="signin-desktop"]');
    await page.waitForSelector('.settings-group [data-act="sign-out"][data-id="tidy"]');
    if (await page.locator('.settings-group [data-act="signin-desktop"]').count()) errors.push('add-org: TIDY still offered once connected');
    await go(page, 'settings-add');
    await page.click('[data-pop="orgs"]');
    await page.click('#popover [data-act="signin-desktop"][data-url="https://tidy.tico.page"]');
    await page.waitForSelector('.settings-group [data-act="sign-out"][data-id="tidy"]');
  });
  await check('signin-address', async (page) => {
    await go(page, 'signin-address');
    if (!(await page.locator('[data-model="signin"]').count())) errors.push('signin: no address field without desktop orgs');
  });

  // @mention in Notes: filter, ↓, Enter inserts "@Name " as plain text.
  await check('mention', async (page) => {
    await go(page, 'meeting');
    await open('m1')(page);
    await page.click('textarea.notes');
    await page.keyboard.press('Meta+ArrowDown');
    await page.keyboard.type(' @ma');
    const names = await page.locator('#mention [data-opt]').evaluateAll((rows) => rows.map((row) => row.dataset.name));
    if (names.join('|') !== 'Marketing Lead|Mara Novak|Martin Diaz') errors.push(`mention: listed ${names.join('|')}`);
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    const notes = await page.locator('textarea.notes').inputValue();
    if (!notes.endsWith(' @Mara Novak ')) errors.push(`mention: notes end ${JSON.stringify(notes.slice(-30))}`);
    if (await page.locator('#mention').count()) errors.push('mention: list stayed open');
    await page.keyboard.type('@ju');
    await page.keyboard.press('Escape');
    if (await page.locator('#mention').count()) errors.push('mention: Esc left it open');
  });

  // Transcript search never reflows the list, and matches are counted.
  await check('search', async (page) => {
    await go(page, 'meeting');
    await open('m1')(page);
    await page.waitForSelector('#tbody > *');
    const before = await page.locator('#tbody > *').count();
    const height = await page.evaluate(() => document.getElementById('tbody').scrollHeight);
    await page.keyboard.press('Meta+f');
    await page.waitForSelector('.side-search input');
    for (const key of 'task') {
      await page.keyboard.type(key);
      const rows = await page.locator('#tbody > *').count();
      const now = await page.evaluate(() => document.getElementById('tbody').scrollHeight);
      if (rows !== before || now !== height) errors.push(`search: layout moved at "${key}" (${rows} rows, ${now}px)`);
    }
    if (!/^1\/\d+$/.test(await page.locator('#thits').innerText())) errors.push('search: no counter');
    await page.keyboard.press('Enter');
    if (!(await page.locator('#thits').innerText()).startsWith('2/')) errors.push('search: Enter did not move to the next match');
  });

  // Ask: a private card appears in the sidebar.
  await check('ask', async (page) => {
    await go(page, 'meeting');
    await open('m1')(page);
    await page.click('[data-model="ask"]');
    await page.keyboard.type('Who owns the pricing cap?');
    await page.keyboard.press('Enter');
    await page.waitForSelector('#tbody .asked:not(.pending)');
    if (!(await page.locator('#tbody .asked .head').first().innerText()).includes('only you see this')) errors.push('ask: not marked private');
  });

  // The mic notice closes and stays closed.
  await check('notice', async (page) => {
    await go(page, 'notice');
    await open('m0')(page);
    await page.click('[data-act="dismiss-notice"]');
    await page.waitForTimeout(400);
    if (await page.locator('.dock-notice').count()) errors.push('notice: still shown after closing');
  });

  // Speech: Your Tico hides the key fields; Own keys shows them and makes the form savable.
  await check('speech', async (page) => {
    await go(page, 'settings');
    await settingsPage(page);
    if (await page.locator('[data-model="key"]').count()) errors.push('speech: key field shown under Your Tico');
    await page.click('[data-act="speech-source"][data-source="own"]');
    if (!(await page.locator('[data-model="key"]').count())) errors.push('speech: no key field under Own keys');
    await page.click('[data-act="save-settings"]');
    await page.waitForTimeout(400);
    if ((await page.evaluate(() => window.__PREVIEW__.state.status.speech.source)) !== 'own') errors.push('speech: source not saved');
  });

  // Library rows open from the keyboard.
  await check('keyboard', async (page) => {
    await go(page, 'library');
    await page.focus('.row[data-id="m1"]');
    await page.keyboard.press('Enter');
    await page.waitForSelector('.title-input', { timeout: 1000 });
  });

  // Screen share from More: Stop stays inside the small window and names the meeting from the library.
  await check('screen', async (page) => {
    await page.setViewportSize({ width: 760, height: 490 });
    await go(page, 'meeting');
    await open('m1')(page);
    await more(page);
    await page.click('[data-act="share-screen"]');
    await page.click('[data-act="screen-org"][data-id="team"]');
    await page.click('[data-act="screen-go"]');
    await page.waitForSelector('.screen-stop');
    const stop = await page.locator('.screen-stop').boundingBox();
    if (!stop || stop.x < 0 || stop.x + stop.width > 760) errors.push('screen: Stop outside the window');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.row[data-id="m1"]');
    await page.click('[data-act="stop-screen"]');
    await page.waitForSelector('.screen-stop', { state: 'detached' });
  });
}
await browser.close();
server.close();
console.log(shots.join('\n'));
if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log('checks ok');
