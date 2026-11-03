// Dream mode in the Learnings panel and the bot page's question card: numbered questions become cards whose answers
// go back as one numbered reply to the question itself, the done view lists only this dream's commits, Wake up
// returns to the history, and polling stops when the panel closes. Desktop and phone widths.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const QUESTIONS = 'Two things only you can settle.\n1. Should weekly reports go to Ana or the whole team? Suggested: the whole team\n'
  + '2. Drop the old "deploy on Fridays" rule? Suggested: yes';

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    await page.setContent('<button id="bot-learn" class="bot-learn"><span class="bl-n"></span></button><div id="bot-dream-host"></div>');
    await page.addStyleTag({path: path.join(__dirname, '../styles/bot-learnings.css')});
    await page.evaluate(questions => {
      window.$ = (selector, root = document) => root.querySelector(selector);
      window.esc = value => String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
      window.md = text => `<p>${esc(text)}</p>`;
      window.ago = () => 'today';
      window.toast = message => { throw new Error('toast: ' + message); };
      window.BOT = {slug: 'ops'};
      const row = (subject, task) => ({subject, author: 'ops', when: '2026-11-02T10:00:00Z', files: ['memory/learnings.md'],
        source: task ? {task, title: 'Dream'} : null});
      window.state = {
        dream: {task: 't1', status: 'waiting', state: 'asking', questions: {message_id: 'm1', text: questions, mine: true},
                summary: null, closed_at: null, started_at: '2026-11-02T09:00:00Z'},
        posts: [], dreamReads: 0,
      };
      window.get = async url => {
        if (url.endsWith('/dream')) { state.dreamReads++; return {dream: state.dream}; }
        if (url.startsWith('/v2/tasks/')) return {task: {id: 't1', version: 3}};
        return {documents: {}, document_status: {}, updates: [row('Dream: merged report rules', 't1'), row('Older rule', 'other')], next_before: null};
      };
      window.post = async (url, body) => {
        state.posts.push({url, body});
        if (url.includes('/answer')) state.dream = {...state.dream, state: 'done', status: 'done', questions: null,
          summary: 'Merged two report rules. Dropped the Friday deploy rule.', closed_at: '2026-11-02T10:05:00Z'};
        if (url.endsWith('/dream')) return {dream: state.dream};
        return {};
      };
    }, QUESTIONS);
    await page.addScriptTag({path: path.join(__dirname, '../app/bot-learnings.js')});

    // The split: an intro, numbered questions, suggestions as chips.
    const cards = await page.evaluate(q => dreamSplit(q), QUESTIONS);
    assert.deepEqual(cards.map(c => [c.n, c.suggested]), [['1', 'the whole team'], ['2', 'yes']]);
    assert.ok(cards[0].intro.startsWith('Two things') && !cards[0].text.includes('Suggested'));
    assert.equal((await page.evaluate(() => dreamSplit('Just one question?'))).length, 1);

    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 800});
      await page.evaluate(text => {
        state.dream = {task: 't1', status: 'waiting', state: 'asking', questions: {message_id: 'm1', text, mine: true},
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
          assert.ok(box.height >= 44, `${sel} is a 44px tap target at 390px (${box.height})`);
        }
        const overflow = await page.locator('.learn-panel').evaluate(el => el.scrollWidth - el.clientWidth);
        assert.ok(overflow <= 0, 'nothing runs off the sheet sideways');
      }
      await page.locator('.learn-panel [data-dream-fill="0"]').click();
      await page.locator('.learn-panel textarea').nth(1).fill('No, keep it');
      await page.locator('.learn-panel .dream-actions .primary').click();

      // One numbered reply to the question itself; then the done view with this dream's commit only.
      await page.locator('[data-dream-wake]').waitFor();
      const answer = await page.evaluate(() => state.posts.find(p => p.url.includes('/answer')));
      assert.equal(answer.url, '/v2/messages/m1/answer');
      assert.equal(answer.body.text, '1. the whole team\n2. No, keep it');
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
    console.log('bot-learnings-dream: cards, numbered answer, done view, wake up, start and polling passed at 1280 and 390');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
