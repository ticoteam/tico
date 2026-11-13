// Dream mode in the Learnings panel and the bot page's question card: numbered questions become cards whose answers
// go back as one numbered reply to the question itself, the done view lists only this dream's commits, Wake up
// returns to the history, polling stops when the panel closes, and Stop closes the dream's task. Desktop and phone widths.
// Task bodies are checked against the server's own body models (support/task_body_check.py), as the real routes do.
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {execFileSync}: typeof import('node:child_process') = require('node:child_process');
const {chromium} = require('playwright') as typeof import('playwright');

// Page-side globals: the test keeps its fake hub in window.state, and bot-learnings.js defines the rest in the page.
interface DreamRecord {
  task: string;
  status: string;
  state: string;
  questions: {task_id: string; requirement_id: string; text: string; mine: boolean} | null;
  summary: string | null;
  closed_at: string | null;
  started_at: string;
}
interface DreamState {
  dream: Partial<DreamRecord> | null;
  posts: {url: string; body?: unknown}[];
  dreamReads: number;
  toasts: string[];
  gate?: Promise<unknown>;
  release?: (value?: unknown) => void;
}
interface DreamCard {
  n: string;
  suggested: string;
  intro: string;
  text: string;
}
declare const esc: (value: unknown) => string;
declare const state: DreamState;
declare function learnOpen(bot: string, data: {updates?: unknown[]; documents?: Record<string, string>}, dream?: Partial<DreamRecord> | null): void;
declare function botDreamLoad(bot: string): Promise<void>;
declare function dreamSplit(text: string): DreamCard[];
declare const window: Window & typeof globalThis & {
  $: (selector: string, root?: ParentNode) => Element | null;
  esc: (value: unknown) => string;
  md: (text: string) => string;
  ago: () => string;
  toast: (message: string) => void;
  errorMessage: (e: unknown) => string;
  newRequestId: () => string;
  BOT: {slug: string};
  state: DreamState;
  get: (url: string) => Promise<unknown>;
  post: (url: string, body?: unknown) => Promise<unknown>;
  taskBodyCheck: (url: string, body: unknown) => Promise<string>;
};

const ROOT = path.join(__dirname, '../..');
const PYTHON = process.env.TICO_PYTHON || 'python3';
// The real route's answer to a task body: '' when it takes it, else its 422 detail.
const taskBodyCheck = (url: string, body: unknown): string =>
  execFileSync(PYTHON, ['ui/tests/support/task_body_check.py', url, JSON.stringify(body)], {cwd: ROOT}).toString().trim();

const QUESTIONS = 'Two things only you can settle.\n1. Should weekly reports go to Ana or the whole team? Suggested: the whole team\n'
  + '2. Drop the old "deploy on Fridays" rule? Suggested: yes';

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    await page.exposeFunction('taskBodyCheck', taskBodyCheck);
    await page.setContent('<button id="bot-learn" class="bot-learn"><span class="bl-n"></span></button><div id="bot-dream-host"></div>');
    await page.addStyleTag({path: path.join(__dirname, '../styles/bot-learnings.css')});
    await page.evaluate(questions => {
      Object.assign(window, {$: (selector: string, root: ParentNode = document) => root.querySelector(selector)});
      window.esc = value => String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'} as Record<string, string>)[ch]!);
      window.md = text => `<p>${esc(text)}</p>`;
      window.ago = () => 'today';
      // errors.ts's errorMessage, so a refused request reaches toast with the server's detail.
      window.errorMessage = (e: unknown) => String((e as Error)?.message ?? e);
      window.toast = message => { state.toasts.push(message); throw new Error('toast: ' + message); };
      window.BOT = {slug: 'ops'};
      window.newRequestId = () => 'req-1';
      const row = (subject: string, task?: string) => ({subject, author: 'ops', when: '2026-11-02T10:00:00Z', files: ['memory/learnings.md'],
        source: task ? {task, title: 'Dream'} : null});
      window.state = {
        dream: {task: 't1', status: 'waiting', state: 'asking', questions: {task_id: 't1', requirement_id: 'r1', text: questions, mine: true},
                summary: null, closed_at: null, started_at: '2026-11-02T09:00:00Z'},
        posts: [], dreamReads: 0, toasts: [],
      };
      window.get = async url => {
        if (url.endsWith('/dream')) { state.dreamReads++; return {dream: state.dream}; }
        if (url.startsWith('/v2/tasks/')) return {task: {id: 't1', version: 3}};
        return {documents: {}, document_status: {}, updates: [row('Dream: merged report rules', 't1'), row('Older rule', 'other')], next_before: null};
      };
      window.post = async (url, body) => {
        state.posts.push({url, body});
        if (/^\/v2\/tasks\/[^/]+(\/move)?$/.test(url)) {
          const refused = await window.taskBodyCheck(url, body);
          if (refused) throw new Error('422 ' + refused);
          if (url.endsWith('/move') && (body as {to?: string}).to === 'closed')
            state.dream = {...state.dream, state: 'stopped', status: 'closed', closed_at: '2026-11-02T13:05:00Z'};
          return {task: {id: 't1', version: 4}};
        }
        if (url.includes('/answer')) state.dream = {...state.dream, state: 'done', status: 'done', questions: null,
          summary: 'Merged two report rules. Dropped the Friday deploy rule.', closed_at: '2026-11-02T10:05:00Z'};
        if (url.endsWith('/dream')) { await state.gate; return {dream: state.dream}; }
        return {};
      };
    }, QUESTIONS);
    await page.addScriptTag({path: path.join(__dirname, '../../build/ui/app/task-api.js')});
    await page.addScriptTag({path: path.join(__dirname, '../../build/ui/app/bot-learnings.js')});

    // The split: an intro, numbered questions, suggestions as chips.
    const cards = await page.evaluate(q => dreamSplit(q), QUESTIONS);
    assert.deepEqual(cards.map(c => [c.n, c.suggested]), [['1', 'the whole team'], ['2', 'yes']]);
    assert.ok(cards[0]!.intro.startsWith('Two things') && !cards[0]!.text.includes('Suggested'));
    assert.equal((await page.evaluate(() => dreamSplit('Just one question?'))).length, 1);

    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 800});
      await page.evaluate(text => {
        state.dream = {task: 't1', status: 'waiting', state: 'asking', questions: {task_id: 't1', requirement_id: 'r1', text, mine: true},
                       summary: null, closed_at: null, started_at: '2026-11-02T09:00:00Z'};
        state.posts = [];
      }, QUESTIONS);

      // The bot page: the mark on the button, and the questions as a card.
      await page.evaluate(() => botDreamLoad('ops'));
      assert.ok(await page.locator('#bot-learn').evaluate(el => el.classList.contains('dreaming')));
      assert.equal(await page.locator('#bot-dream-host .dream-q').count(), 2);

      // An open dream opens the panel straight into dream mode.
      await page.evaluate(() => learnOpen('ops', {updates: [], documents: {}}, state.dream));
      await page.locator('.learn-panel.dreaming .dream-q').nth(1).waitFor();
      assert.equal(await page.locator('.learn-panel .dream-line').innerText(), 'A few questions');
      assert.equal(await page.locator('[data-dream-start]').isVisible(), false);
      if (width === 390) {
        for (const sel of ['.learn-panel [data-dream-fill="0"]', '.learn-panel .dream-actions .primary', '.learn-panel [data-learn-close]']) {
          const box = await page.locator(sel).boundingBox();
          assert.ok(box!.height >= 44, `${sel} is a 44px tap target at 390px (${box!.height})`);
        }
        const overflow = await page.locator('.learn-panel').evaluate(el => el.scrollWidth - el.clientWidth);
        assert.ok(overflow <= 0, 'nothing runs off the sheet sideways');
      }
      await page.locator('.learn-panel [data-dream-fill="0"]').click();
      await page.locator('.learn-panel textarea').nth(1).fill('No, keep it');
      await page.locator('.learn-panel .dream-actions .primary').click();

      // One numbered answer to the dream's ask; then the done view with this dream's commit only.
      await page.locator('[data-dream-wake]').waitFor();
      const answer = await page.evaluate(() => state.posts.find(p => p.url.includes('/answer')));
      assert.equal(answer!.url, '/v2/tasks/t1/asks/r1/answer');
      assert.deepEqual(answer!.body, {value: '1. the whole team\n2. No, keep it'});
      assert.equal(await page.locator('.learn-panel .dream-line').innerText(), 'Awake');
      assert.ok((await page.locator('.dream-summary').innerText()).includes('Merged two report rules'));
      assert.deepEqual(await page.locator('.dream-commits .lr-subject').allInnerTexts(), ['Dream: merged report rules']);
      assert.equal(await page.locator('#bot-dream-host .dream-q').count(), 0, 'answered: the bot page card goes');
      assert.equal(await page.locator('#bot-learn').evaluate(el => el.classList.contains('dreaming')), false);

      // Wake up: back to the history, refreshed, with the Dream button.
      await page.locator('[data-dream-wake]').click();
      await page.locator('.learn-panel:not(.dreaming) .learn-list').waitFor();
      assert.equal(await page.locator('[data-dream-start]').isVisible(), true);
      assert.equal(await page.locator('.learn-row').count(), 2);

      // Dream starts one from the history; closing the panel stops polling.
      await page.evaluate(() => { state.dream = {task: 't2', status: 'open', state: 'starting', questions: null, summary: null,
                                                closed_at: null, started_at: '2026-11-02T11:00:00Z'}; });
      await page.locator('[data-dream-start]').click();
      await page.locator('.learn-panel.dreaming .dream-line').waitFor();
      assert.equal(await page.locator('.learn-panel .dream-line').innerText(), 'Falling asleep…');
      assert.ok(await page.evaluate(() => state.posts.some(p => p.url === '/v2/bots/ops/dream')));
      await page.locator('[data-learn-close]').click();
      const reads = await page.evaluate(() => state.dreamReads);
      await page.waitForTimeout(4500);
      assert.equal(await page.evaluate(() => state.dreamReads), reads, 'a closed panel polls no more');
    }

    // Closed while the start is in flight: polling never starts after the close, and the page still shows the dream.
    await page.evaluate(() => {
      state.dream = {task: 't3', status: 'open', state: 'starting', questions: null, summary: null, closed_at: null,
                     started_at: '2026-11-02T12:00:00Z'};
      state.gate = new Promise(resolve => { state.release = resolve; });
      learnOpen('ops', {updates: [], documents: {}}, null);
    });
    await page.locator('[data-dream-start]').click();
    await page.locator('[data-learn-close]').click();
    await page.locator('.learn-panel').waitFor({state: 'detached'});     // the close, and its cleanup, have run
    const before = await page.evaluate(() => { state.release!(); return state.dreamReads; });
    await page.waitForTimeout(4500);
    assert.equal(await page.evaluate(() => state.dreamReads), before, 'a start that lands after close polls nothing');
    assert.ok(await page.locator('#bot-learn').evaluate(el => el.classList.contains('dreaming')), 'the brain shows it');

    // Stop on a running dream closes its task through the move route, with the version just read; the dream is over.
    // The page clock is paused, so the panel's 4 s poll never fires: Stop itself must redraw the dream.
    await page.clock.install({time: new Date('2026-11-02T13:01:00Z')});
    await page.clock.pauseAt(new Date('2026-11-02T13:01:01Z'));
    await page.evaluate(() => {
      state.dream = {task: 't4', status: 'open', state: 'dreaming', questions: null, summary: null, closed_at: null,
                     started_at: '2026-11-02T13:00:00Z'};
      state.posts = []; state.toasts = [];
      learnOpen('ops', {updates: [], documents: {}}, state.dream);
    });
    await page.locator('.learn-panel [data-dream-stop]').click();
    await page.locator('.learn-panel [data-dream-wake]').waitFor({timeout: 5000}).catch(async () => {
      assert.fail('Stop left the dream running: ' + JSON.stringify(await page.evaluate(() => ({posts: state.posts, toasts: state.toasts}))));
    });
    assert.deepEqual(await page.evaluate(() => state.posts), [{url: '/v2/tasks/t4/move', body: {to: 'closed', version: 3}}]);
    assert.equal(await page.locator('.learn-panel .dream-line').innerText(), 'Awake');
    assert.equal(await page.locator('.learn-panel [data-dream-stop]').count(), 0, 'a stopped dream has no Stop');
    assert.equal(await page.locator('#bot-learn').evaluate(el => el.classList.contains('dreaming')), false);
    console.log('bot-learnings-dream: cards, numbered answer, done view, wake up, start, polling, a start landing after close and stop passed at 1280 and 390');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
