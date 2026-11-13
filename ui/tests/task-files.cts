/// <reference lib="dom.iterable" />
// A task's files and questions: the strip of tiles (lazy, sized, skeletons), a tile opened in place with its versions
// and Compare, image arrows and Esc; files a comment carried; questions on a file version and on comments, read only;
// the modal's poll bringing new files without breaking a playing video; a reader's view (no controls) and an older
// runner's plain answer; linked images inline; uploads (Files +, a drop, a paste) sending in-memory copies of every
// file; no sideways scroll at phone width. Every request is intercepted.
// TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
const zlib = require('node:zlib') as typeof import('node:zlib');
import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
// @ts-expect-error t is not called in this spec; the require stays for load.cjs's side effects
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const shots = process.env.TICO_SHOTS || '';

interface Ask {questions: unknown[]; who: null}
interface Version {n: number; size: number; mime: string; sha256: string; created_at: string; by: string; comment_id: string | null; note: string | null; ask: Ask | null; answers: unknown[]; width: number | null; height: number | null; duration_ms: number | null; media_state: string; url: string; poster_url: string | null; thumb_url: string | null}
interface FileRow {id: string; name: string; mime: string; current_version: number; archived: boolean; versions: Version[]}
interface CommentRow {id: string; kind: string; sender_id: string; body: string; created_at: string; answers?: unknown[]; answer?: unknown; refs: {task: string; comment?: boolean; questions?: unknown[]}}
interface PageWin {sentFiles: {name: string; size: number; copied: boolean}[]; readDone: string[]; fastPoll: boolean}
// App functions and page globals, called inside page.evaluate.
declare const TASKS_ST: {loading?: boolean};
declare const TASK_CHAT: unknown;
declare const readDone: string[];
declare function taskChatLive(chat: unknown): void;
declare function v2Get(path: string): Promise<unknown>;
declare function taskModalShow(task: unknown): Promise<void>;
declare function tfLoad(root: Element, id: string): void;
declare function $(selector: string): Element;
// A small striped PNG, made here so the test needs no image files.
function png(w: number, h: number, rgb: (x: number, y: number) => number[]): Buffer {
  const crcTable = Array.from({length: 256}, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf: Buffer) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff]! ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type: string, data: Buffer) => { const len = Buffer.alloc(4), sum = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); sum.writeUInt32BE(crc(td)); return Buffer.concat([len, td, sum]); };
  const head = Buffer.alloc(13); head.writeUInt32BE(w, 0); head.writeUInt32BE(h, 4); head[8] = 8; head[9] = 2;
  const rows = [];
  for (let y = 0; y < h; y++) { rows.push(0); for (let x = 0; x < w; x++) rows.push(...rgb(x, y)); }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', head), chunk('IDAT', zlib.deflateSync(Buffer.from(rows))), chunk('IEND', Buffer.alloc(0))]);
}
const TEAL = png(160, 90, (x, y) => [30 + (x >> 1), 120 + y, 140]);
const AMBER = png(160, 90, (x, y) => [220, 110 + (y >> 1), 40 + (x >> 2)]);

(async () => {
  const t0 = Date.now();
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [], posted: {path: string; body: Record<string, unknown>}[] = [], requested: string[] = [], uploadBodies: Buffer[] = [];
    page.on('pageerror', e => errors.push(e.message));
    // The open task reads again when live events say it changed (taskChatLive); with (window as unknown as PageWin).fastPoll set, a change
    // arrives every 200ms.
    await page.addInitScript(() => {
      setInterval(() => { if ((window as unknown as PageWin).fastPoll && typeof taskChatLive === 'function' && TASK_CHAT) taskChatLive(TASK_CHAT); }, 200);
    });
    // Whether an upload sends its files as copies made in memory: WebKit can send a file read from disk as an empty
    // body, so a request must never carry the picked or dropped file itself.
    await page.addInitScript(() => {
      const copies = new WeakSet();
      (window as unknown as PageWin).sentFiles = [];
      window.File = new Proxy(File, {construct(target, args) {
        const file = Reflect.construct(target, args);
        if ([...args[0] || []].some(part => part instanceof ArrayBuffer)) copies.add(file);
        return file;
      }});
      const send = window.fetch;
      window.fetch = (url, init = {}) => {
        if (init.body instanceof FormData) for (const value of init.body.values()) {
          if (value instanceof File) (window as unknown as PageWin).sentFiles.push({name: value.name, size: value.size, copied: copies.has(value)});
        }
        return send(url, init);
      };
    });
    const now = Date.now(), at = (mins: number) => new Date(now - mins * 60000).toISOString();
    const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: false};
    const bots = [{name: 'editor', display_name: 'Editor', host: 'keeper', status: 'active', can_chat: true}];
    const url = (id: string, n: number, part = '') => `/api/v2/files/${id}${part}?v=${n}`;
    const version = (id: string, n: number, over: Record<string, unknown>) => ({n, size: 2048, mime: '', sha256: 'x' + n, created_at: at(200 - n * 30), by: 'bot:editor', comment_id: null, note: null,
      ask: null, answers: [], width: null, height: null, duration_ms: null, media_state: 'none', url: url(id, n), poster_url: null, thumb_url: null, ...over} as Version);
    const verdict = {questions: [{id: 'verdict', header: 'Rough cut', question: 'Is v3 ready to publish?',
      options: [{label: 'Approve'}, {label: 'Request changes', description: 'Say what to change'}], multi: false, other: true}], who: null};
    const files: FileRow[] = [
      {id: 'f-cut', name: 'cut.mp4', mime: 'video/mp4', current_version: 3, archived: false, versions: [3, 2, 1].map(n => version('f-cut', n,
        {mime: 'video/mp4', width: 1920, height: 1080, duration_ms: 42000, media_state: 'ready', poster_url: url('f-cut', n, '/poster'), thumb_url: url('f-cut', n, '/thumb'),
         note: {3: 'Tighter intro, new music.', 2: 'Second pass.', 1: 'First cut.'}[n], comment_id: n === 2 ? 'm1' : null, ask: n === 3 ? verdict : null}))},
      {id: 'f-script', name: 'script.md', mime: 'text/markdown', current_version: 2, archived: false, versions: [2, 1].map(n => version('f-script', n, {mime: 'text/markdown'}))},
      {id: 'f-frame', name: 'frame.png', mime: 'image/png', current_version: 1, archived: false,
        versions: [version('f-frame', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-frame', 1, '/thumb'), comment_id: 'm1'})]},
      {id: 'f-still', name: 'still.png', mime: 'image/png', current_version: 1, archived: false,
        versions: [version('f-still', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-still', 1, '/thumb')})]},
      {id: 'f-rows', name: 'shots.csv', mime: 'text/csv', current_version: 1, archived: false, versions: [version('f-rows', 1, {mime: 'text/csv'})]},
      {id: 'f-logo', name: 'logo.svg', mime: 'image/svg+xml', current_version: 1, archived: false, versions: [version('f-logo', 1, {mime: 'image/svg+xml'})]},
      {id: 'f-page', name: 'page.html', mime: 'text/html', current_version: 1, archived: false, versions: [version('f-page', 1, {mime: 'text/html'})]},
      {id: 'f-site', name: 'site.html', mime: 'text/html', current_version: 1, archived: false, versions: [version('f-site', 1, {mime: 'text/html', size: 20_000_000})]},
    ];
    const scripts: Record<string, string> = {1: '# Script\n\nIntro line.\n\nScene one.\nScene two.\n', 2: '# Script\n\nA tighter intro line.\n\nScene one.\nScene two.\nScene three.\n'};
    const csv = 'shot,length\n' + Array.from({length: 260}, (_, i) => `s${i + 1},${i % 7 + 1}s`).join('\n');
    const task = {id: 't1', title: 'Cut the launch video', owner_id: 'bot:editor', requester_id: 'human:ana', status: 'review', lane: 'company', rank: 1,
      labels: [], links: [], parts: {total: 0, done: 0}, version: 4, created_at: at(400), updated_at: at(5), open_asks: 1,
      body: 'Reference frame: ![frame](https://img.example.com/ref.png)\n\nMood: https://img.example.com/mood.jpg',
      attachments: [{id: 'f-frame', name: 'frame.png', content_type: 'image/png', size: 2048, url: '/api/v2/files/f-frame'}]};
    const plain = {...task, id: 't2', title: 'Write the captions', status: 'doing', open_asks: 0, attachments: [], body: 'Captions for the cut.', updated_at: at(60)};
    // t2 is one Ana may read but not comment on; one of its questions was answered by an older runner: {by, text, at}
    const plainComments = [
      {id: 'p1', kind: 'ask', sender_id: 'bot:editor', body: 'Which tone?', created_at: at(50), answers: [{by: 'human:sam', text: 'Keep it warm and short.', at: at(40)}],
        refs: {task: 't2', questions: [{id: 'tone', question: 'Which tone?', options: [{label: 'Warm'}, {label: 'Plain'}], other: true}]}},
      {id: 'p2', kind: 'ask', sender_id: 'bot:editor', body: 'Which font?', created_at: at(30), answers: [],
        refs: {task: 't2', questions: [{id: 'font', question: 'Which font?', options: [{label: 'Serif'}, {label: 'Sans'}], other: true}]}},
    ];
    const plainFiles = [{id: 'f-notes', name: 'captions.md', mime: 'text/markdown', current_version: 1, archived: false,
      versions: [version('f-notes', 1, {mime: 'text/markdown', ask: {questions: [{id: 'ok', question: 'Good to go?', options: [{label: 'Yes'}, {label: 'No'}]}], who: null}})]}];
    const comments: CommentRow[] = [
      {id: 'm1', kind: 'say', sender_id: 'bot:editor', body: 'Second pass and a frame grab.', created_at: at(140), refs: {task: 't1', comment: true}},
      {id: 'm2', kind: 'ask', sender_id: 'bot:editor', body: 'Which music and thumbnail?', created_at: at(90), answers: [], refs: {task: 't1',
        questions: [{id: 'music', header: 'Music', question: 'Which tracks fit?', options: [{label: 'Upbeat'}, {label: 'Calm'}, {label: 'None'}], multi: true, other: true},
          {id: 'thumb', header: 'Thumbnail', question: 'Which thumbnail?', options: [{label: 'A', file: 'f-frame@1'}, {label: 'B', file: 'f-still@1'}], multi: false, other: false}]}},
      {id: 'm3', kind: 'ask', sender_id: 'bot:editor', body: 'Ship today?', created_at: at(80), answers: [], refs: {task: 't1',
        questions: [{id: 'ship', question: 'Ship today?', options: [{label: 'Yes'}, {label: 'No'}], multi: false, other: false}]}},
      {id: 'm4', kind: 'ask', sender_id: 'human:ana', body: 'Captions too?', created_at: at(70), answers: [], refs: {task: 't1',
        questions: [{id: 'cap', question: 'Captions too?', options: [{label: 'Yes'}, {label: 'No'}]}]}},
    ];
    let prefs: Record<string, unknown> = {};
    let thumbHold: {held: Promise<unknown>; release?: (value?: unknown) => void} | null = null;      // while set, thumbnails wait until the test lets them go
    await page.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url()), p = u.pathname;
      const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (u.origin === 'https://img.example.com') { requested.push(u.href); return route.fulfill({contentType: 'image/png', body: p.includes('mood') ? AMBER : TEAL}); }
      if (u.origin !== 'http://tico-ui.test') return route.abort();
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui) { const file = uiFile(ui[1]!); if (fs.existsSync(file)) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(file, 'utf8')}); }
      if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
        contentType: /\.(?:js|ts)$/.test(p) ? 'application/javascript' : p.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream'});
      if (p === '/api/me') return json(me);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'sam', name: 'Sam', email: 'sam@acme.example'}]});
      if (p === '/api/issues') return json([]);
      if (p === '/api/status') return json({active: [], employees: []});
      if (p === '/api/v2/status') return json({bots: []});
      if (p === '/api/v2/task-types') return json({types: []});
      if (p.startsWith('/api/v2/preferences/')) {
        const key = p.split('/').at(-1)!;
        if (req.method() === 'POST') { const body = req.postDataJSON(); posted.push({path: p, body}); prefs[key] = body.value; return json({key, value: body.value}); }
        requested.push(p);
        return json({key, value: key === 'tasks.view' ? {view: 'board'} : prefs[key] ?? null});
      }
      const rmFile = p.match(/^\/api\/v2\/files\/([^/]+)$/);
      if (rmFile && req.method() === 'PATCH') {
        posted.push({path: p, body: req.postDataJSON()});
        const at = files.findIndex(f => f.id === rmFile[1]);
        if (at >= 0) files.splice(at, 1);
        return json({file: {id: rmFile[1], archived: true}});
      }
      const file = p.match(/^\/api\/v2\/files\/([^/]+)(\/poster|\/thumb)?$/);
      if (file) {
        requested.push(p + u.search);
        const [id, part] = [file[1], file[2] || ''], n = u.searchParams.get('v');
        if (part || id === 'f-frame' || id === 'f-still') {
          if (thumbHold) await thumbHold.held;
          return route.fulfill({contentType: 'image/png', headers: {'Cache-Control': 'private, max-age=31536000, immutable'}, body: id === 'f-still' || (id === 'f-cut' && n === '1') ? AMBER : TEAL});
        }
        if (id === 'f-script' || id === 'f-notes') return route.fulfill({contentType: 'text/markdown', body: scripts[n!]!});
        if (id === 'f-page') return route.fulfill({contentType: 'application/octet-stream', body: '<h1>Landing</h1><script>document.body.dataset.ran = "yes"</script>'});
        if (id === 'f-rows') return route.fulfill({contentType: 'text/csv', body: csv});
        if (id === 'f-cut') return new Promise(() => {});        // a video that is still on its way: the poster shows
        return route.fulfill({contentType: 'application/octet-stream', body: 'not a real file'});
      }
      if (p === '/api/v2/uploads/tasks' && req.method() === 'POST') {
        uploadBodies.push(req.postDataBuffer() || Buffer.alloc(0));
        return json({task: {...task, id: 't9', title: 'Brief'}});
      }
      if (p === '/api/v2/tasks' && req.method() === 'GET') return json({tasks: [task, plain], next_offset: null});
      if (p === '/api/v2/tasks/t1/files' && req.method() === 'POST') {
        const raw = req.postDataBuffer() || Buffer.alloc(0), name = raw.toString('latin1').match(/filename="([^"]+)"/)?.[1]!;
        uploadBodies.push(raw);
        posted.push({path: p, body: {file: name}});
        files.push({id: 'f-up' + files.length, name, mime: name.endsWith('.pdf') ? 'application/pdf' : 'image/png', current_version: 1, archived: false, versions: [version('f-up' + files.length, 1, {mime: name.endsWith('.pdf') ? 'application/pdf' : 'image/png', by: 'human:ana'})]});
        return json({file: {id: files.at(-1)!.id, name}, file_id: files.at(-1)!.id, version: 1});
      }
      if (p === '/api/v2/tasks/t1/files') return json({files});
      if (p === '/api/v2/tasks/t2/files') return json({files: plainFiles});
      if (p === '/api/v2/tasks/t1/answers' && req.method() === 'POST') {
        const body = req.postDataJSON(); posted.push({path: p, body});
        const answer = {...body, by: 'human:ana', at: new Date().toISOString()};
        let text;
        if (body.target.file) {
          const f = files.find(x => x.id === body.target.file)!, v = f.versions.find(x => x.n === body.target.version)!;
          v.answers.push(answer);
          text = `Ana approved "${f.name}" v${v.n}.`;
        } else {
          const c = comments.find(x => x.id === body.target.comment) as unknown as {answers: unknown[]; refs: {questions: {question: string}[]}};
          c.answers.push(answer);
          text = body.dismiss ? 'Ana dismissed the question.' : `Ana answered "${c.refs.questions[0]!.question}": ${Object.values(body.answers).flat().join(', ')}.`;
        }
        task.open_asks = Math.max(0, task.open_asks - 1);
        comments.push({id: 'a' + comments.length, kind: 'answer', sender_id: 'human:ana', body: text, created_at: new Date().toISOString(), answer, refs: {task: 't1'}});
        return json({comment: comments.at(-1)});
      }
      const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
      if (one) {
        const t = [task, plain].find(x => x.id === one[1]);
        return json({task: t, comments: t === task ? comments : plainComments, events: [], children: [], parent: null, mover: true, messages: [], can_comment: t === task});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      return json(unrouted(p));
    });
    const theme = async (mode: string) => page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
    const shot = async (name: string) => { if (shots) await page.screenshot({path: path.join(shots, name + '.png')}); };
    const noSideways = async (where: string) => {
      const r = await page.evaluate(() => ({doc: document.documentElement.scrollWidth, win: innerWidth,
        dialogs: [...document.querySelectorAll('dialog[open]')].map(d => [d.scrollWidth, d.clientWidth] as [number, number])}));
      assert(r.doc <= r.win, `${where}: the page scrolls sideways ${JSON.stringify(r)}`);
      for (const [sw, cw] of r.dialogs) assert(sw <= cw + 1, `${where}: a dialog scrolls sideways ${JSON.stringify(r)}`);
    };

    await page.goto('http://tico-ui.test/#/tasks');
    await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading);

    // ---- the strip: one tile per file at its newest version, sized, a skeleton until the thumb arrives
    { let release!: (value?: unknown) => void; thumbHold = {held: new Promise(r => { release = r; })}; thumbHold.release = release; }
    await page.evaluate(async () => { const r = await v2Get('/v2/tasks/t1') as {task: unknown}; await taskModalShow(r.task); });
    const modal = page.locator('#task-modal');
    await modal.locator('.tf-tile[data-tf-file="f-cut"]').waitFor();
    assert.deepEqual(await modal.locator('.tf-tile .tf-name').allTextContents(), ['cut.mp4', 'script.md', 'frame.png', 'still.png', 'shots.csv', 'logo.svg', 'page.html', 'site.html']);
    const cutTile = modal.locator('.tf-tile[data-tf-file="f-cut"]');
    assert.match(await cutTile.locator('.tf-meta').innerText(), /^v3 · \d+h$/);
    assert.equal(await cutTile.locator('.ask-dot').count(), 1, 'a file with an open question has a dot');
    assert.equal(await cutTile.locator('.tf-thumb.tf-skel:not(.ready)').count(), 1, 'a skeleton until the thumb arrives');
    assert.equal(await cutTile.evaluate(el => el.style.width), '128px', 'sized from the version\'s width and height');
    thumbHold.release(); thumbHold = null;
    await cutTile.locator('.tf-thumb.ready').waitFor();
    assert(requested.includes('/api/v2/files/f-cut/thumb?v=3'), 'the versioned thumb');
    assert.equal(await modal.locator('.tf-tile[data-tf-file="f-logo"] .tf-thumb img').count(), 0, 'no picture: a type mark');
    // files reach an open task by the +, a drop anywhere on it, or a pasted screenshot; a comment being typed stays
    assert.equal(await modal.locator('.task-files [data-tf-add]').isVisible(), true);
    await modal.locator('.task-chat textarea').fill('Half-written note');
    await modal.evaluate(d => {
      const dt = new DataTransfer();
      dt.items.add(new File(['spec'], 'mockup.png', {type: 'image/png'}));
      for (const type of ['dragenter', 'dragover', 'drop']) d.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true}));
    });
    await modal.locator('.tf-tile .tf-name', {hasText: 'mockup.png'}).waitFor();
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tasks/t1/files', body: {file: 'mockup.png'}});
    // the + picks a file from disk: the request carries a full copy of it, not the picked file itself
    await modal.locator('.task-files [data-tf-input]').setInputFiles({name: 'brief.pdf', mimeType: 'application/pdf', buffer: TEAL});
    await modal.locator('.tf-tile .tf-name', {hasText: 'brief.pdf'}).waitFor();
    assert.deepEqual(posted.at(-1), {path: '/api/v2/tasks/t1/files', body: {file: 'brief.pdf'}});
    assert.ok(uploadBodies.at(-1)!.includes(TEAL), 'every byte of the picked file reaches the server');
    assert.deepEqual(await page.evaluate(() => (window as unknown as PageWin).sentFiles.at(-1)), {name: 'brief.pdf', size: TEAL.length, copied: true});
    await modal.locator('.task-chat textarea').evaluate(box => {
      const dt = new DataTransfer();
      dt.items.add(new File(['shot'], 'image.png', {type: 'image/png'}));
      box.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    });
    await modal.locator('.tf-tile .tf-name', {hasText: /^screenshot-/}).waitFor();
    assert.match(posted.at(-1)!.body.file as string, /^screenshot-\d{8}-\d{6}\.png$/);
    assert.equal(await modal.locator('.task-chat textarea').inputValue(), 'Half-written note');
    assert.equal(await modal.evaluate(d => d.classList.contains('over')), false);
    // a file is removed from its viewer: the first press asks, the second takes it off
    await modal.locator('.tf-tile', {hasText: 'mockup.png'}).click();
    const rm = modal.locator('[data-tf-remove]');
    await rm.click();
    assert.equal(await rm.textContent(), 'Really remove?');
    assert.equal(posted.filter(x => x.path.startsWith('/api/v2/files/')).length, 0, 'nothing sent on the first press');
    await rm.click();
    await modal.locator('.tf-tile .tf-name', {hasText: 'mockup.png'}).waitFor({state: 'detached'});
    assert.deepEqual(posted.at(-1)!.body, {archived: true});
    assert.match(posted.at(-1)!.path, /^\/api\/v2\/files\/f-up/);
    await modal.locator('.task-chat textarea').fill('');
    files.splice(-1);
    await page.evaluate(() => tfLoad($('#task-modal'), 't1'));
    await modal.locator('.tf-tile .tf-name', {hasText: 'mockup.png'}).waitFor({state: 'detached'});
    // a comment that carried files: a small thumb for the image, a chip for the video at its version
    const m1 = modal.locator('.tcomment', {hasText: 'Second pass and a frame grab.'});
    await m1.locator('.tc-thumb[data-tf-jump="f-frame"]').waitFor();
    assert.equal((await m1.locator('.tc-file[data-tf-jump="f-cut"]').textContent())!.replace(/\s+/g, ' ').trim(), 'VIDEOcut.mp4 v2');
    assert.equal(await m1.locator('video, .tf-media').count(), 0, 'no big media in a comment');
    // the linked images in the details show inline, lazily, and open full size
    await modal.locator('.tdesc img[src="https://img.example.com/ref.png"].ready').waitFor();
    assert.equal(await modal.locator('.tdesc > p > img').getAttribute('loading'), 'lazy');
    await modal.locator('.tdesc .inline-thumb.ready').waitFor();
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`task-files-strip-desktop-${mode}`); }
    await theme('dark');

    // ---- a tile opens in place: the video at v3 with its poster, the versions, who added it and its note
    await m1.locator('.tc-file[data-tf-jump="f-cut"]').click();
    const view = modal.locator('.tf-view');
    // the viewer is a modal over the task: fold the open file before reaching a tile or a comment under it
    const fold = async () => { if (await view.isVisible()) { await page.keyboard.press('Escape'); await view.waitFor({state: 'hidden'}); } };
    const openTile = async (id: string) => { await fold(); await modal.locator(`.tf-tile[data-tf-file="${id}"]`).click(); };
    await view.locator('video').waitFor();
    assert.equal(await view.evaluate(d => d.matches(':modal')), true, 'the file opens in its own modal');
    const box = (await view.boundingBox())!, vp = page.viewportSize()!;
    assert(box.width >= Math.min(1200, vp.width - 48) - 2 && box.height >= vp.height - 50, `near full screen ${JSON.stringify(box)}`);
    assert.equal(await view.locator('video').getAttribute('src'), '/api/v2/files/f-cut?v=2', 'the comment\'s chip opens its version');
    await view.locator('[data-tf-v="3"]').click();
    assert.equal(await view.locator('video').getAttribute('src'), '/api/v2/files/f-cut?v=3');
    assert.equal(await view.locator('video').getAttribute('poster'), '/api/v2/files/f-cut/poster?v=3');
    assert.deepEqual(await view.locator('.tf-vers button').allTextContents(), ['v1', 'v2', 'v3']);
    assert.match(await view.locator('.tf-vmeta').innerText(), /Editor · \d+h ago[\s\S]*Tighter intro, new music\./);
    assert.equal(await cutTile.getAttribute('aria-expanded'), 'true');
    // the file's question: chip, question, options with their description
    const fileAsk = view.locator('.ask');
    assert.match(await fileAsk.locator('.ask-line').innerText(), /ROUGH CUT|Rough cut/);
    assert.equal(await fileAsk.locator('.ask-opt').nth(1).getAttribute('title'), 'Say what to change');
    for (const mode of ['dark', 'light']) { await theme(mode); await shot(`task-video-versions-desktop-${mode}`); }
    await theme('dark');
    // Compare: two versions side by side
    await view.locator('[data-tf-cmp]').click();
    assert.deepEqual(await view.locator('.tf-pair .tf-side-v').allTextContents(), ['v2', 'v3']);
    assert.equal(await view.locator('.tf-pair video').count(), 2);
    await view.locator('[data-tf-with]').selectOption('1');
    assert.deepEqual(await view.locator('.tf-pair .tf-side-v').allTextContents(), ['v1', 'v3']);
    // Esc folds the file, not the task
    await page.keyboard.press('Escape');
    await view.waitFor({state: 'hidden'});
    assert.equal(await modal.isVisible(), true, 'Esc folded the file and left the task open');
    // markdown: compare reads as a line diff
    await modal.locator('.tf-tile[data-tf-file="f-script"]').click();
    await view.locator('.tf-doc .md h1').waitFor();
    await view.locator('[data-tf-cmp]').click();
    await view.locator('.tf-diff-lines').waitFor();
    assert.deepEqual(await view.locator('.tf-diff-lines .del').allTextContents(), ['- Intro line.']);
    assert.deepEqual(await view.locator('.tf-diff-lines .add').allTextContents(), ['+ A tighter intro line.', '+ Scene three.']);
    // CSV: the first 200 rows under a header that stays put
    await openTile('f-rows');
    await view.locator('table.csv').waitFor();
    assert.equal(await view.locator('table.csv tbody tr').count(), 200);
    assert.equal(await view.locator('table.csv th').first().evaluate(th => getComputedStyle(th).position), 'sticky');
    // SVG: a download card only
    await openTile('f-logo');
    assert.equal(await view.locator('.tf-card a[download]').getAttribute('href'), '/api/v2/files/f-logo?v=1');
    assert.equal(await view.locator('img, iframe, object').count(), 0);
    // HTML: shown as it looks in the sandboxed frame, no script; over the limit, a download card
    await openTile('f-page');
    const pageFrame = view.locator('.tf-html iframe.html-view');
    assert.equal(await pageFrame.getAttribute('sandbox'), 'allow-popups allow-popups-to-escape-sandbox');
    const landing = page.frameLocator('.tf-html iframe.html-view');
    assert.equal(await landing.locator('h1').innerText(), 'Landing');
    assert.equal(await landing.locator('body').getAttribute('data-ran'), null);
    await openTile('f-site');
    assert.equal(await view.locator('.tf-card .tf-glyph').innerText(), 'HTML');
    assert.equal(await view.locator('.tf-card a[download]').getAttribute('href'), '/api/v2/files/f-site?v=1');
    assert.equal(await view.locator('iframe').count(), 0);
    // images: full size, ←/→ through the task's images
    await openTile('f-frame');
    await view.locator('.tf-media.image.ready').waitFor();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('#task-modal .tf-view img')?.getAttribute('src') === '/api/v2/files/f-still?v=1');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('#task-modal .tf-view img')?.getAttribute('src') === '/api/v2/files/f-frame?v=1');
    // a press on the backdrop folds it too
    await page.mouse.click(4, 4);
    await view.waitFor({state: 'hidden'});

    // ---- the modal's poll brings a new file and version; a playing video waits for it
    const video = view.locator('video');
    // readDone: every answer the app has read in full (its handler runs in the same turn)
    await page.evaluate(() => {
      const send = fetch; (window as unknown as PageWin).readDone = [];
      window.fetch = async (...args) => {
        const r = await send(...args), text = r.text.bind(r);
        r.text = async () => { try { return await text(); } finally { readDone.push(new URL(r.url).pathname); } };
        return r;
      };
    });
    await page.evaluate(() => { (window as unknown as PageWin).fastPoll = true; });
    await cutTile.click();
    await video.evaluate(v => { v.dataset.keep = '1'; Object.defineProperty(v, 'paused', {configurable: true, get: () => false}); });
    files.push({id: 'f-new', name: 'end-card.png', mime: 'image/png', current_version: 1, archived: false,
      versions: [version('f-new', 1, {mime: 'image/png', width: 160, height: 90, thumb_url: url('f-new', 1, '/thumb')})]});
    files[0]!.versions.unshift(version('f-cut', 4, {mime: 'video/mp4', width: 1920, height: 1080, media_state: 'ready', poster_url: url('f-cut', 4, '/poster'), thumb_url: url('f-cut', 4, '/thumb')}));
    files[0]!.current_version = 4;
    comments.push({id: 'm7', kind: 'say', sender_id: 'bot:editor', body: 'End card added.', created_at: new Date().toISOString(), refs: {task: 't1', comment: true}});
    await modal.locator('.tf-tile[data-tf-file="f-new"]').waitFor();
    // two more polls of the files and the comments have been read and handled
    const polled = () => page.evaluate(() => [readDone.filter(p => p === '/api/v2/tasks/t1/files').length, readDone.filter(p => p === '/api/v2/tasks/t1').length]);
    const [filesRead, commentsRead] = await polled();
    await page.waitForFunction(([f, c]) => readDone.filter(p => p === '/api/v2/tasks/t1/files').length >= f + 2
      && readDone.filter(p => p === '/api/v2/tasks/t1').length >= c + 2, [filesRead, commentsRead] as [number, number]);
    assert.equal(await view.locator('video[data-keep="1"]').count(), 1, 'the playing video was left alone');
    assert.equal(await view.locator('[data-tf-v="4"]').count(), 0);
    // the video stops: the next poll catches up
    await video.evaluate(v => { delete (v as unknown as {paused?: boolean}).paused; });
    await view.locator('[data-tf-v="4"]').waitFor();
    await modal.locator('.tcomment', {hasText: 'End card added.'}).waitFor();
    await page.evaluate(() => { (window as unknown as PageWin).fastPoll = false; });
    assert.deepEqual(errors, []);

    // ---- phone width: no sideways scroll, with the strip and an open video
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await cutTile.getAttribute('aria-expanded'), 'true', 'still open');
    // A phone opens on Chat, its files in the Info tab (task #409); a file open stays in view on either.
    await view.locator('video').waitFor();
    await noSideways('phone, task with an open video');
    for (const mode of ['dark', 'light']) { await theme(mode); await modal.evaluate(d => { (d as HTMLElement).scrollTop = 0; }); await shot(`task-files-strip-phone-${mode}`); await view.scrollIntoViewIfNeeded(); await shot(`task-video-versions-phone-${mode}`); }
    await fold();
    await modal.locator('[data-modal-close]').click();

    // ---- a reader (can_comment false): the questions, their choices and answers, no controls and no comment box;
    // an older runner's answer reads as its text, folded
    const answersBefore = posted.filter(x => x.path.endsWith('/answers')).length;
    for (const [width, height, where] of [[390, 844, 'phone'], [1280, 900, 'desktop']] as [number, number, string][]) {
      await page.setViewportSize({width, height});
      await page.evaluate(async () => { const r = await v2Get('/v2/tasks/t2') as {task: unknown}; await taskModalShow(r.task); });
      const legacy = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="tone"]')});
      await legacy.locator('.ask.answered').waitFor();
      assert.match(await legacy.locator('.ask-answers').innerText(), /Sam\s*Keep it warm and short\./);
      assert.equal(await legacy.locator('.ask-opt').first().isVisible(), false, 'answered: folded');
      const font = modal.locator('.tcomment', {has: page.locator('.ask-q[data-qid="font"]')});
      assert.equal(await font.locator('.ask-opt.ro').count(), 2);
      assert.equal(await modal.locator('.task-chat .ask button, .task-chat .ask input').count(), 0, 'no answer controls');
      assert.equal(await modal.locator('.task-chat form').isHidden(), true, 'no comment box');
      assert.equal(await modal.locator('.task-files [data-tf-add]').isHidden(), true, 'a reader cannot add files');
      await font.locator('.ask-opt.ro').first().click();
      if (where === 'phone') await modal.getByRole('tab', {name: 'Info'}).click();
      await modal.locator('.tf-tile[data-tf-file="f-notes"]').click();
      await view.locator('.ask-q[data-qid="ok"]').waitFor();
      assert.equal(await view.locator('.ask button, .ask input').count(), 0);
      await noSideways(`${where}, a reader's task`);
      await fold();
      if (where === 'phone') await modal.getByRole('tab', {name: 'Chat'}).click();
      for (const mode of ['dark', 'light']) { await theme(mode); await legacy.scrollIntoViewIfNeeded(); await shot(`legacy-answer-${where}-${mode}`); }
      await modal.locator('[data-modal-close]').click();
    }
    assert.equal(posted.filter(x => x.path.endsWith('/answers')).length, answersBefore, 'a reader sends nothing');
    assert.deepEqual(errors, []);
    console.log(`PASS: files strip, viewer, versions, compare, read-only asks, comment files, linked images, phone (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
