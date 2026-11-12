// Saved message-box text is the signed-in person's in their org (ui/app/pill.ts chatOwnedRead): the Assistant's unsent
// draft and its pending send come back after a reload only to the same account in the same company, are read only once
// /me has answered, and are dropped for anyone else, as is an older record that names no one. Synthetic accounts and
// answers; the real page, storage and reloads.
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as AssistantChat from './support/assistant-chat.cts';
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {assistantView} = require('./support/assistant-chat.cts') as AssistantChat.Api;
declare const SESSION: {seen: boolean; identity: string};
declare const CHAT_PENDING: Map<string, {text: string; signature: string; key: string; at: number}>;
declare const ASSISTANT_PILL: {unconfirmed: {text: string} | null} | null;
declare function chatPendingSave(): void;

type Me = {id: string; name: string; role: string; cloud: boolean; registered: boolean; config: {environment_id?: string}};
const ana: Me = {id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true, config: {environment_id: 'company-a'}};
const box = '#chat-composer textarea';

async function scenario(browser: import('playwright').Browser, name: string, next: Me | null, options: {legacy?: boolean; delay?: boolean} = {}) {
  const context = await browser.newContext({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'});
  const page = await context.newPage();
  const errors: string[] = [], posts: string[] = [];
  let me = ana, hold: Promise<void> | null = null;
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (req.method() === 'POST') posts.push(p);
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') { if (hold) await hold; return json(me); }
    if (p === '/api/v2/me') return json({id: me.id});
    if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: me.id, name: me.name}], teams: {}});
    if (p === '/api/v2/assistant') return json(assistantView('c1'));
    if (p.endsWith('/snapshot')) return json({messages: [], execution: null});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  const ready = async () => {
    await page.waitForFunction(() => typeof SESSION !== 'undefined' && !!SESSION.identity);
    await page.locator(box).waitFor();
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  };
  const saved = () => page.evaluate(() => ({drafts: localStorage.getItem('hub.chat.drafts'), pending: sessionStorage.getItem('hub.chat.pending'),
    threads: localStorage.getItem('hub.thread.drafts')}));
  try {
    await page.goto('https://tico-ui.test/#/assistant');
    await ready();
    await page.locator(box).fill('Ana draft');
    await page.evaluate(() => {
      CHAT_PENDING.set('assistant', {text: 'Ana draft', signature: 'POST /v2/chat/assistant\n{}', key: 'fixture-key', at: Date.now()});
      chatPendingSave();
    });
    const mine = await saved();
    const owner = JSON.stringify(['company-a', 'ana']);
    assert.equal(JSON.parse(mine.drafts!).identity, owner, 'a draft names its account and company');
    assert.equal(JSON.parse(mine.pending!).identity, owner, 'a pending send names its account and company');
    // A bot's Home or DM keeps its words too (thread-view.ts), under the same owner rule; this page draws no thread box.
    await page.evaluate(o => localStorage.setItem('hub.thread.drafts', JSON.stringify({identity: o, value: {'d:bot:coo': 'Ana thread words'}})), owner);
    if (options.legacy) await page.evaluate(() => {
      localStorage.setItem('hub.chat.drafts', JSON.stringify({coo: 'Ana legacy draft'}));
      sessionStorage.setItem('hub.chat.pending', JSON.stringify([['assistant', {text: 'Ana legacy pending', signature: 'old', key: 'old-key', at: Date.now()}]]));
    });

    let release: (() => void) | undefined;
    if (options.delay) hold = new Promise<void>(done => { release = done; });
    if (next) me = next;
    await page.reload({waitUntil: options.delay ? 'domcontentloaded' : 'load'});
    if (options.delay) {
      // Until /me answers, nothing saved is read into memory or dropped.
      await page.waitForFunction(() => typeof CHAT_PENDING !== 'undefined');
      assert.equal(await page.evaluate(() => CHAT_PENDING.size), 0, 'no pending send before the identity answer');
      assert.ok((await saved()).drafts?.includes('Ana draft'), 'nothing is dropped before the identity answer');
      release!(); hold = null;
    }
    await ready();
    const keep = !options.legacy && (!next || (next.id === ana.id && next.config.environment_id === ana.config.environment_id));
    assert.equal(await page.locator(box).inputValue(), keep ? 'Ana draft' : '', name + ': the draft is shown only to its account in its company');
    assert.equal(await page.evaluate(() => ASSISTANT_PILL?.unconfirmed?.text || ''), keep ? 'Ana draft' : '', name + ': so is the pending send');
    const after = await saved();
    if (!keep) {
      assert.ok(!after.drafts?.includes('Ana'), name + ': the other draft is gone from storage');
      assert.ok(!after.pending?.includes('Ana'), name + ': the other pending send is gone from storage');
    }
    if (!keep && !options.legacy) assert.equal(after.threads, null, name + ': the other thread words are gone from storage at sign-in');
    if (keep) assert.ok(after.threads?.includes('Ana thread words'), name + ': the thread words stay for their account');
    // Opening the Assistant marks its chat read (assistant-page.ts assistantRead): a read, not a restored send.
    assert.deepEqual(posts.filter(p => !p.endsWith('/read-all')), [], name + ': restoring sends nothing');
    assert.deepEqual(errors, [], name + ': no page errors');
  } finally { await context.close(); }
  console.log('ok -', name);
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    await scenario(browser, 'same account, same company', null);
    await scenario(browser, 'another account', {...ana, id: 'ben', name: 'Ben'});
    await scenario(browser, 'same account, another company', {...ana, config: {environment_id: 'company-b'}});
    await scenario(browser, 'older records that name no one', null, {legacy: true});
    await scenario(browser, 'identity answer arrives late', {...ana, id: 'ben', name: 'Ben'}, {delay: true});
  } finally { await browser.close(); }
  console.log('chat-drafts-owner ok');
})().catch(error => { console.error(error); process.exit(1); });
