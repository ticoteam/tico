// A sign-in return must not refill confirmed Assistant chat text from its saved restoration snapshot.
// All requests and sign-in pages are synthetic; the real UI, navigation and browser storage run unchanged.
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type * as AssistantChat from './support/assistant-chat.cts';
import type {AddressInfo} from 'node:net';
import type {Request as PwRequest} from 'playwright';
const {chromium, webkit}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const http: typeof import('node:http') = require('node:http');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
const {assistantView} = require('./support/assistant-chat.cts') as AssistantChat.Api;
const {t}: {t: (ms: number) => number} = require('./support/load.cts');
const engines = (process.env.TICO_UI_ENGINES || 'chromium,webkit').split(',');
type Field = {scope: string; key: string; value: string};
type Msg = {id: string; sender_id: string; body: string; created_at: string};
type Step = <T>(label: string, run: () => T | PromiseLike<T>, ms?: number) => Promise<T>;
declare function chatDrafts(): Record<string, string>;
declare function sessionIdentity(): string;
declare const SESSION_RETURN: string;
declare const CHAT_DRAFTS: string;
declare const LIVE_SOON: Set<unknown>;
declare const SESSION: {seen: boolean};
declare const V2C: {loaded: boolean} | undefined;
declare const LIVE: {es: EventSource | null | undefined};
declare const ASSISTANT_PILL: {sending: boolean};
declare function liveConnected(): boolean;
declare function liveUrl(): string;
declare function get(path: string): Promise<unknown>;
declare function sessionScope(el: Element | null): string;
declare function sessionKey(el: Element | null): string;
declare function sessionSave(snapshot: {fields: Field[]}): void;

// Playwright's defaults do not bound evaluate promises or browser/server cleanup. Keep the deadline in Node (scaled by
// the runner's TICO_UI_SLOWDOWN like other waits) and report the step before a stalled cleanup can hide the failure.
function steps(context: string): Step {
  return async <T,>(label: string, run: () => T | PromiseLike<T>, ms = t(30000)): Promise<T> => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([Promise.resolve().then(run), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new assert.AssertionError({message: `${context}: ${label} exceeded ${ms}ms`})), ms);
      })]);
    } catch (error) {
      console.error(`${context}: ${label}: ${(error as Error).message}`);
      throw error;
    } finally { clearTimeout(timer); }
  };
}

async function scenario(browser: import('playwright').Browser, engine: string, mode: string, edit: boolean, editedBefore = false) {
  const step = steps(`${engine}, ${mode}, ${editedBefore ? 'edited restored draft' : edit ? 'later edits' : 'sign-in return'}`);
  const page = await step('create page', () => browser.newPage({viewport: {width: 1200, height: 800}, serviceWorkers: 'block'}));
  const errors: string[] = [], posts: {text: string; method: string}[] = [], delivered: Msg[] = [];
  const reads = new Set<PwRequest>(), readWaiters = new Set<(() => void) | undefined>();
  let navigating = false;
  const readsDrained = () => { if (!reads.size) for (const done of [...readWaiters]) done!(); };
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigating = true;
    if (request.method() === 'GET' && path !== '/api/v2/events' && !path.endsWith('/watch')) reads.add(request);
  });
  // A read still out when the page loads a new document is canceled with the old one, and Chromium reports neither
  // requestfinished nor requestfailed for it: left counted, it held the next wait to its deadline ("Fixture reads did
  // not settle"). A new document in the main frame (not a hash change) ends every read made before it.
  page.on('framenavigated', frame => {
    if (frame !== page.mainFrame() || !navigating) return;
    navigating = false;
    reads.clear();
    readsDrained();
  });
  const readFinished = (request: PwRequest) => {
    reads.delete(request);
    readsDrained();
  };
  page.on('requestfinished', readFinished);
  page.on('requestfailed', readFinished);
  page.on('pageerror', e => errors.push(e.message));
  let expired = false, hold: Promise<void> | null = null, release: (() => void) | undefined;
  // A real same-origin fixture keeps event streams open across sign-in. Fulfilling an intercepted
  // stream ends it immediately and creates reconnect/probe requests that WebKit can reject during navigation.
  const serverErrors: string[] = [];
  const server = http.createServer((request, response) => {
    void (async () => {
      const url = new URL(request.url!, 'http://127.0.0.1'), p = url.pathname;
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      const reply = (body: string, contentType: string, status = 200) => { response.writeHead(status, {'Content-Type': contentType}); response.end(body); };
      const json = (body: unknown, status = 200) => reply(JSON.stringify(body), 'application/json', status);
      const identity = () => json({error: {code: 'identity', detail: 'Sign in again', sign_in: '/auth/login'}}, 401);
      if (p === '/auth/login') return reply('<title>Fixture sign-in</title><a id="return" style="display:inline-block;padding:8px" href="' + url.searchParams.get('next') + '">Return after sign-in</a>', 'text/html');
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return reply(fs.readFileSync(uiFile(ui[1]!), 'utf8'), ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript');
      if (/\.(?:js|ts)$/.test(p)) return reply('', 'application/javascript');
      if (p === '/') return reply(html, 'text/html');
      if (p === '/api/v2/me' && expired) return identity();
      if (p === '/api/me' || p === '/api/v2/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true});
      if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', can_chat: true, schedules: []}]);
      if (p === '/api/issues') return json([]);
      if (p === '/api/v2/assistant') return json(assistantView('c1', {messages: delivered}));
      if (p.endsWith('/snapshot')) return json({messages: delivered, execution: null});
      if (p === '/api/v2/events' || p.endsWith('/watch')) {
        response.writeHead(200, {'Content-Type': 'text/event-stream'});
        response.write('event: ready\ndata: {"seq":0}\n\n');
        return;
      }
      if (p === '/api/v2/assistant/messages') {
        const text = (JSON.parse(body) as {text: string}).text;
        posts.push({text, method: request.method!});
        if (posts.length === 1) {
          expired = true;
          return identity();
        }
        if (hold) await hold;
        const message = {id: 'm' + posts.length, sender_id: 'human:ana', body: text, created_at: new Date().toISOString()};
        delivered.push(message);
        return json({message});
      }
      return json(unrouted(p));
    })().catch(error => { serverErrors.push(error.message); if (!response.headersSent) response.writeHead(500); response.end(); });
  });
  const box = page.locator('#chat-composer textarea'), send = page.locator('#chat-composer .p-send');
  const stored = () => step('read saved drafts and return snapshot', () => page.evaluate(() => ({draft: chatDrafts().assistant, otherDraft: chatDrafts().other,
    fields: (JSON.parse(sessionStorage.getItem(SESSION_RETURN) || 'null') as {fields?: Field[]} | null)?.fields || []})));
  const frames = (label: string) => step(label, () => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))));
  const readsSettled = (label: string) => step(label, async () => {
    let done: (() => void) | undefined;
    try {
      do {
        // A send schedules a snapshot read after a short burst; it can start after current reads finish.
        await page.waitForFunction(() => !LIVE_SOON.size);
        if (reads.size) await new Promise<void>(resolve => { done = resolve; readWaiters.add(done); });
        readWaiters.delete(done);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      } while (reads.size || await page.evaluate(() => LIVE_SOON.size > 0));
    } finally { readWaiters.delete(done); }
  });
  // A loaded conversation can precede its stream subscription. Navigating again while WebKit is
  // still opening that connection reports a canceled request as an access-control page error.
  const ready = (label: string) => step(label, () => page.waitForFunction(() => {
    if (!SESSION.seen || !(typeof V2C !== 'undefined' && V2C?.loaded)
      || !liveConnected() || LIVE.es?.readyState !== EventSource.OPEN) return false;
    const subscribed = new URL(LIVE.es.url), expected = new URL(liveUrl(), location.href);
    // The ready event advances the cursor; only a change of subscription requires a new connection.
    subscribed.searchParams.delete('after'); expected.searchParams.delete('after');
    return subscribed.href === expected.href;
  }));
  const idle = (label: string) => step(label, () => page.waitForFunction(() => !ASSISTANT_PILL.sending && !(document.querySelector('#chat-composer .p-send') as HTMLButtonElement).disabled));
  const text = '  Keep this draft through sign-in  ';
  try {
    await step('start fixture server', () => new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); }));
    const origin = 'http://127.0.0.1:' + (server.address() as AddressInfo).port;
    await step('open Assistant', () => page.goto(origin + '/#/assistant'));
    await ready('initial conversation and event subscription ready');
    await step('type initial draft', () => box.fill(text));
    await step('save other chat draft', () => page.evaluate(() => localStorage.setItem(CHAT_DRAFTS, JSON.stringify({identity: sessionIdentity(), value: {...chatDrafts(), other: 'Another bot draft'}}))));
    if (mode !== 'unsent') {
      await step('send with expired sign-in', () => send.click());
      await idle('rejected send finishes');
    } else {
      expired = true;
      await step('expire sign-in without sending', () => page.evaluate(() => get('/v2/me').catch(() => {})));
    }
    await step('signed-out banner appears', () => page.locator('#signed-out:not([hidden])').waitFor());
    assert.equal(await step('read signed-out draft', () => box.inputValue()), text, 'sign-out keeps the unsent text');
    await step('seed older return snapshot', () => page.evaluate(text => {
      const kept = JSON.parse(sessionStorage.getItem(SESSION_RETURN)!) as {fields: Field[]};
      // A snapshot saved by an older page can still contain the chat draft. New pages keep only the
      // per-bot copy, but a confirmed send must consume the older saved copy and its live watcher too.
      const input = document.querySelector('#chat-composer textarea');
      kept.fields.push({scope: sessionScope(input), key: sessionKey(input), value: text});
      kept.fields.push({scope: 'task:other', key: 'Message', value: 'Another task draft'});
      sessionSave(kept);
    }, text));
    await readsSettled('fixture reads settle before sign-in');
    await step('open sign-in page', () => page.click('#signed-out button'));
    await step('arrive at sign-in page', () => page.waitForURL('**/auth/login**'));
    expired = false;
    await step('return from sign-in', () => page.click('#return'));
    await step('arrive back at Assistant', () => page.waitForURL(origin + '/#/assistant'));
    await ready('returned conversation and event subscription ready');
    await readsSettled('fixture reads settle after sign-in');
    assert.equal(await step('read restored draft', () => box.inputValue()), text, 'return restores the draft');
    assert.ok((await stored()).fields.some(f => f.value === text), 'the return snapshot still holds a copy beside the restored local draft');

    if (mode !== 'unsent') {
      const sentText = editedBefore ? 'Edited after sign-in, before sending' : text.trim();
      if (editedBefore) await step('edit restored draft', () => box.fill(sentText));
      if (edit) hold = new Promise(done => release = done);
      await step('resend restored draft', () => send.click());
      await step('resend starts or clears composer', () => page.waitForFunction(() => ASSISTANT_PILL.sending || (document.querySelector('#chat-composer .p-send') as HTMLButtonElement).disabled || !(document.querySelector('#chat-composer textarea') as HTMLTextAreaElement).value));
      const next = 'Next draft typed while the resend is out\nA second line\nA third line';
      if (edit) {
        await step('type later draft while resend is held', () => box.fill(next));
        release!(); hold = null;
      }
      await idle('confirmed resend finishes');
      await frames('return watcher paints after resend');
      assert.equal(await step('read composer after resend', () => box.inputValue()), edit ? next : '', 'confirmed text never comes back; later edits stay');
      if (edit) assert.ok(await step('measure later multiline draft', () => box.evaluate(el => el.clientHeight >= el.scrollHeight - 2)), 'later multiline edits keep the composer expanded');
      const saved = await stored();
      assert.equal(saved.draft, edit ? next : undefined, 'the persistent draft agrees with the composer');
      assert.ok(!saved.fields.some(f => f.value === text), 'the saved sign-in snapshot consumed the composer draft, including its pre-edit copy');
      assert.equal(delivered.length, 1, 'one successful delivery');
      assert.equal(delivered[0]!.body, sentText, 'the edited text is the text sent');
      if (!edit) {
        await step('empty Send finishes fading', () => page.waitForFunction(() => getComputedStyle(document.querySelector('#chat-composer .p-send')!).opacity === '0.45'));
        assert.equal(await step('read empty Send opacity', () => send.evaluate(button => getComputedStyle(button).opacity)), '0.45', 'the empty composer dims Send');
        await step('press Enter on empty composer', () => box.press('Enter'));
        await step('click empty Send', () => send.evaluate(button => (button as HTMLElement).click()));
        await frames('empty composer actions paint');
      }
      assert.equal(posts.length, 2, 'the interrupted attempt and explicit resend are the only POSTs');
      await readsSettled('fixture reads settle before confirmed-send reload');
      await step('reload after confirmed resend', () => page.reload());
      await ready('reloaded conversation and event subscription ready');
      await frames('reloaded composer paints');
      assert.equal(await step('read reloaded composer', () => box.inputValue()), edit ? next : '', 'a later reload cannot revive confirmed text');
      assert.equal(posts.length, 2, 'reload sends nothing');
    } else {
      await readsSettled('fixture reads settle before unsent-draft reload');
      await step('reload unsent draft', () => page.reload());
      await ready('unsent conversation and event subscription ready after reload');
      assert.equal(await step('read reloaded unsent draft', () => box.inputValue()), text, 'an existing draft never sent survives return and another reload');
      assert.equal(posts.length, 0, 'an unsent draft makes no POST');
    }
    const final = await stored();
    assert.equal(final.otherDraft, 'Another bot draft', 'other chats keep their unsent drafts');
    assert.ok(final.fields.some(f => f.scope === 'task:other' && f.value === 'Another task draft'), 'other boxes keep their sign-in snapshots');
    assert.ok(posts.every(request => request.method === 'POST'), 'all send requests use POST');
    assert.deepEqual(errors, [], 'no browser errors');
    assert.deepEqual(serverErrors, [], 'no fixture server errors');
    console.log('ok -', mode, editedBefore ? 'edited restored draft' : edit ? 'preserves later edits' : 'sign-in return');
  } finally {
    release?.();
    try { await step('close page', () => page.close(), t(10000)); } finally {
      await step('close fixture server', () => new Promise<void>((resolve, reject) => {
        server.close(error => error && (error as NodeJS.ErrnoException).code !== 'ERR_SERVER_NOT_RUNNING' ? reject(error) : resolve());
        server.closeAllConnections();
      }), t(10000));
    }
  }
}

(async () => {
  for (const engine of engines) {
    const step = steps(engine);
    const browser = await step('launch browser', () => (({chromium, webkit} as Record<string, import('playwright').BrowserType>)[engine]!).launch({headless: true,
      ...(engine === 'chromium' ? {channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string} : {})}));
    try {
      await scenario(browser, engine, '401', false, true);
      await scenario(browser, engine, '401', false);
      await scenario(browser, engine, '401', true);
      await scenario(browser, engine, 'unsent', false);
      console.log('ok - chat send sign-in:', engine);
    } finally { await step('close browser', () => browser.close(), t(10000)); }
  }
})().catch(error => { console.error(error); process.exit(1); });
