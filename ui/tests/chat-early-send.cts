// Offline browser regression: typing into the Assistant's composer while its conversation is still loading.
// The page redraws (a re-route) and the held snapshot lands after Return; the typed text and the
// cursor survive the redraw, Return sends once, and the late snapshot does not wipe the sent message.
// Second, the way a person meets it: from another page a hash change opens the Assistant, its stream's snapshots land on
// their own schedule, the box is clicked after a few seconds and 250 characters are typed; a snapshot the server built
// before the send (a lagging stream) arrives afterwards and must not take the message off the page.
import type * as PageSupport from './support/page.cjs';
import type * as Routes from './support/routes.cjs';
import type * as AssistantChat from './support/assistant-chat.cjs';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cjs') as typeof PageSupport;
const {unrouted} = require('./support/routes.cjs') as typeof Routes;
const {assistantView} = require('./support/assistant-chat.cjs') as typeof AssistantChat;
declare const V2C: {loaded: boolean; liveOff: boolean};
declare const applied: string[][];
declare const v2ChatApply: (state: unknown, d: {messages?: {id: string}[]}) => void;
declare function route(): void;
interface PageWin { applied: string[][]; v2ChatApply: (state: unknown, d: {messages?: {id: string}[]}) => void }
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cjs') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const until = async (cond: () => unknown) => { for (const end = Date.now() + t(10000); !cond() && Date.now() < end;) await new Promise(r => setTimeout(r, 50)); };
const launch = () => chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
async function whileLoading(browser: import('playwright').Browser) {
    const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
    const errors: string[] = [], sends: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    let release!: () => void; const snapshotHeld = new Promise<void>(done => release = done);
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname;
      const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/) as [string, string] | null;
      if (ui && fs.existsSync(uiFile(ui[1])))
        return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
      if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/assistant') return json(assistantView('c1', {messages: []}));
      if (p.endsWith('/snapshot')) { await snapshotHeld; return json({messages: [], execution: null}); }   // the slow, empty load
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      if (p === '/api/v2/assistant/messages') {
        sends.push((route.request().postDataJSON() as {text: string}).text);
        return json({message: {id: 'm1', sender_id: 'human:ana', body: sends[0], created_at: new Date().toISOString()}});
      }
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/assistant');
    const box = page.locator('#chat-composer textarea');
    await box.waitFor();
    const text = 'A long message typed before the conversation finished loading';
    await box.click(); await box.pressSequentially(text);
    // The page redraws under the cursor.
    await page.evaluate(() => route());
    assert.equal(await box.inputValue(), text, 'the text survives the redraw');
    assert.equal(await page.evaluate(() => document.activeElement?.matches('#chat-composer textarea')), true, 'the cursor stays in the box');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector<HTMLTextAreaElement>('#chat-composer textarea')!.value);
    assert.deepEqual(sends, [text], 'Return sends it once');
    release();                                       // the load that started before the send finishes
    await page.waitForFunction(() => V2C.loaded);
    const thread = await page.locator('#conv-thread').innerText();
    assert.match(thread, /A long message typed/, 'the sent message stays in the chat');
    assert.doesNotMatch(thread, /Nothing yet/);
    assert.deepEqual(errors, []);
    console.log('typing while loading: ok');
    await page.close();
}
async function afterHashNav(browser: import('playwright').Browser) {
  const page = await browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
  const errors: string[] = [], sends: string[] = [], served: string[] = [], streams: import('playwright').Route[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const now = () => new Date().toISOString();
  let server = [{id: 'old', sender_id: 'bot:coo', body: 'Welcome back', created_at: '2026-01-01T00:00:00Z'}], sentAt = 0;
  let releaseCurrent!: () => void;
  const currentHeld = new Promise<void>(done => releaseCurrent = done);
  await page.route('**/*', async route => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/) as [string, string] | null;
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
    if (p === '/api/employees') return json([{name: 'coo', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/assistant') return json(assistantView('c1', {messages: server.slice()}));
    if (p === '/api/v2/events') { streams.push(route); return; }          // held until the test sends a change
    if (p.endsWith('/snapshot')) {
      // The first snapshot read after the send was built just before the message landed.
      const phase = sentAt ? served.includes('lagging') ? 'current' : 'lagging' : 'initial';
      if (phase === 'current') await currentHeld;
      served.push(phase);
      return json({messages: phase === 'lagging' ? server.slice(0, 1) : server.slice(), execution: null});
    }
    if (p === '/api/v2/assistant/messages') {
      sends.push((route.request().postDataJSON() as {text: string}).text);
      const message = {id: 'm1', sender_id: 'human:ana', body: sends[0] as string, created_at: now()};
      server = [...server, message]; sentAt = Date.now();
      return json({message});
    }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings');
  await page.locator('#settings-tabs').waitFor();
  await page.evaluate(() => { location.hash = '#/assistant'; });
  const box = page.locator('#chat-composer textarea');
  await box.waitFor();
  await page.waitForFunction(() => V2C?.loaded && V2C.liveOff);
  // Record each snapshot the chat puts on the page from here on (the ids it listed).
  await page.evaluate(() => { const apply = v2ChatApply; (window as unknown as PageWin).applied = []; (window as unknown as PageWin).v2ChatApply = (state, d) => { apply(state, d); applied.push((d.messages || []).map(m => m.id)); }; });
  const text = 'Please set up a bot for our support inbox. '.repeat(6).slice(0, 250);
  await box.click(); await box.pressSequentially(text);
  assert.equal(await box.inputValue(), text, 'everything typed is in the box');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => !document.querySelector<HTMLTextAreaElement>('#chat-composer textarea')!.value);
  assert.deepEqual(sends, [text], 'Return sends it once');
  await page.waitForFunction(() => applied.length >= 1, null, {timeout: t(10000)});   // the lagging snapshot is on the page
  assert.deepEqual([served.includes('lagging'), await page.evaluate(() => applied[0])], [true, ['old']]);
  assert.match(await page.locator('#conv-thread').innerText(), /Please set up a bot/, 'the sent message stays in the chat');
  // The message's change arrives on the live stream; the snapshot read it causes lists it.
  releaseCurrent();
  await until(() => streams.length);
  assert.ok(streams.length, 'the live stream is open');
  await streams.at(-1)!.fulfill({contentType: 'text/event-stream', body: 'id: 5\nevent: messages\ndata: ' +
    JSON.stringify({seq: 5, id: 'm1', conversation_id: 'c1'}) + '\n\n'});
  await page.waitForFunction(() => applied.some(ids => ids.includes('m1')), null, {timeout: t(10000)});
  assert.equal(served.includes('current'), true);
  const thread = await page.locator('#conv-thread').innerText();
  assert.equal((thread.match(/Please set up a bot/g) || []).length > 0, true);
  assert.equal(await page.locator('#conv-thread .bubble.you').count(), 1, 'listed once, not twice');
  assert.deepEqual(errors, []);
  console.log('hash navigation, typing after the snapshots: ok');
  await page.close();
}
(async () => {
  const browser = await launch();
  try { await whileLoading(browser); await afterHashNav(browser); console.log('ok'); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
