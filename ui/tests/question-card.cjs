// The one question card (ui/app/question-card.js), in the Needs you card and in the task page's Needs you box: the real asker,
// "asked N days ago" and what it holds up; the question and its why line; four picks as checkboxes, the recommended
// ones ticked and labelled; "Yes to all" and "Send ticked (n)", each with its "what happens" line; the fallback small
// at the foot; ids, paths and error text only under Details. Ticking 1, 2 and 4 sends those three, and the bot hears
// pick 3 as "not this one" (the server's answer text); Yes to all sends all four. A check from Tico says "Tico".
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const NOW = Date.now(), at = hours => new Date(NOW - hours * 3600e3).toISOString();
const BODY = 'Website sales chat: go with these picks?\n\nRun 8719e1a6 failed: ValueError: bad date in reports/chat-plan.md';
const PICKS = [
  {label: 'Calls', then: 'Booked on your calendar; you take calls for now', recommended: true},
  {label: 'Visitor notes', then: 'Each becomes a private task for you', recommended: true},
  {label: 'Pages', then: 'Home, pricing and docs get the chat'},
  {label: 'Spend cap', then: 'Chat stops for the day at $10', recommended: true},
];
const question = (id, over = {}) => ({id, kind: 'question', title: 'Website sales chat picks', body: BODY, owner_id: 'bot:writer',
  requester_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana', version: 2, created_at: at(80), updated_at: at(72), open_asks: 1,
  labels: [], tags: [], links: [], lane: 'company', rank: null, parts: {total: 0, done: 0}, why: "Engineering can't start until these are set.",
  holds_up: {id: 'r-chat', title: 'Website sales chat build', kind: 'roadmap', step: 'Building'}, tier: 1,
  ask: {id: 'q-' + id, sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Website sales chat: go with these picks?', created_at: at(72),
    goes_ahead_at: '2026-10-23T16:00:00.000000Z',
    refs: {task: id, questions: [{id: 'decision', header: '', question: 'Website sales chat: go with these picks?', multi: true, other: true,
      why: "Engineering can't start until these are set.", options: PICKS}], if_no_answer: {do: 'It stays a draft', kind: 'reversible'}}},
  ...over});
const stuck = {id: 't-stuck', kind: 'question', title: 'Is this still moving?', body: '', owner_id: 'bot:writer', requester_id: 'bot:writer',
  status: 'waiting', version: 1, created_at: at(2), updated_at: at(2), open_asks: 1, from_person: 'human:ana', tier: 2,
  ask: {id: 'q-stuck', sender_id: 'keeper', recipient_id: 'human:ana', body: 'No progress for 60 min after a status check.', created_at: at(2),
    refs: {questions: [{id: 'stuck', header: 'Stuck', question: 'Keep going or stop?', multi: false, other: true,
      options: [{label: 'Keep going', then: 'The bot is woken again'}, {label: 'Stop', then: 'The task is closed'}]}]}}};

// The same question asked another way: `qs` replaces its question.
const asked = (id, q) => { const x = question(id); x.ask.refs.questions = [{id: 'decision', header: '', other: true, why: '', ...q}]; return x; };
const BANNERS = [{label: 'Banner A', file: 'f-a@1', then: 'Banner A goes up'}, {label: 'Banner B', file: 'f-b@1', then: 'Banner B goes up'}];
const pair = id => asked(id, {question: 'Which banner?', multi: false, options: BANNERS});
const several = id => asked(id, {question: 'Which banners go up?', multi: true,
  options: [...BANNERS.map(o => ({...o, recommended: true})), {label: 'Banner C', file: 'f-c@1', then: 'Banner C goes up'}]});
// A Yes / No asked with no options: the server gives it Yes and No with no "what happens" lines of their own.
const yesNo = id => asked(id, {question: 'Archive the September drafts?', multi: false, options: [{label: 'Yes', description: '', then: ''},
  {label: 'No', description: '', then: ''}]});
const FILES = ['a', 'b', 'c'].map(x => ({id: `f-${x}`, name: `banner-${x}.png`, current_version: 1, versions: [{n: 1, size: 68, mime: 'image/png'}]}));
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');
const pictures = root => [...document.querySelectorAll(`${root} img[data-tf-src]`)].map(i => new URL(i.dataset.tfSrc, location.href).pathname + new URL(i.dataset.tfSrc, location.href).search);

const card = () => ({
  who: document.querySelector('.decide .dc-who-name')?.textContent.trim(),
  sub: document.querySelector('.decide .dc-who-sub')?.textContent.trim(),
  q: document.querySelector('.decide .dc-q')?.textContent.trim(),
  why: document.querySelector('.decide .qc-why')?.textContent.trim(),
  picks: [...document.querySelectorAll('.decide .qc-pick')].map(p => ({text: p.querySelector('.qc-l').textContent.trim(),
    then: p.querySelector('.qc-then').textContent.trim(), ticked: p.querySelector('input').checked})),
  buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => ({label: b.querySelector('.qc-bl')?.textContent.replace(/[A-Z]$/, '').trim(),
    then: b.querySelector('.qc-then')?.textContent.trim() || ''})),
});

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    let items = [question('t-four'), stuck];
    const {page, errors} = await open(browser, {viewport: {width: 390, height: 900}, hash: '#/tasks', extraTasks: [...items, question('t-page'), question('t-draft'), question('t-swap'), pair('t-pair'), several('t-several'), yesNo('t-yn')].map(x => ({...x}))});
    const answers = [];
    await page.route('**/api/v2/needs-you*', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({items})}));
    await page.route('**/api/v2/tasks/*/comments', route => route.fulfill({contentType: 'application/json', body: '{"comments":[]}'}));
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: '{"files":[]}'}));
    await page.route('**/api/v2/tasks/*/answers', route => {
      answers.push({task: decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2)), ...route.request().postDataJSON()});
      return route.fulfill({contentType: 'application/json', body: '{}'});
    });
    await page.evaluate(async () => { await v2Refresh(); decideOpen('task:t-four'); });
    await page.locator('.decide .dc-card[data-key="task:t-four"]').waitFor();

    // 10, 14, 15: all four picks, the recommended ones ticked and labelled; every pick and button says what happens;
    // the header names the bot, the age and what it holds up.
    const shown = await page.evaluate(card);
    assert.deepEqual(shown, {
      who: 'Writer', sub: 'asked 3 days ago · holds up: Website sales chat build',
      q: 'Website sales chat: go with these picks?', why: "Why it matters: Engineering can't start until these are set.",
      picks: [
        {text: '1. Calls (recommended)', then: PICKS[0].then, ticked: true},
        {text: '2. Visitor notes (recommended)', then: PICKS[1].then, ticked: true},
        {text: '3. Pages', then: PICKS[2].then, ticked: false},
        {text: '4. Spend cap (recommended)', then: PICKS[3].then, ticked: true}],
      buttons: [{label: 'Yes to all', then: 'All 4 go to Writer as yes'},
        {label: 'Send ticked (3)', then: 'These 3 as yes; Writer hears "not this one" for the rest'}]});
    // 16, 17: the fallback is a small line at the foot, not a button and not styled as a pick; the run id, the error
    // and the path are only under Details, closed.
    const foot = await page.evaluate(() => {
      const body = document.querySelector('.decide .dc-body'), line = body.querySelector('.qc-fallback'), details = body.querySelector('.qc-details');
      const above = [...body.children].filter(x => x !== details).map(x => x.textContent).join(' ');
      return {line: line.textContent, last: [...body.children].slice(-2)[0] === line && body.lastElementChild === details, open: details.open,
        small: parseFloat(getComputedStyle(line).fontSize) < parseFloat(getComputedStyle(body.querySelector('.qc-then')).fontSize),
        weight: getComputedStyle(line).fontWeight, inButton: !!line.closest('button'),
        leaks: ['8719e1a6', 'ValueError', 'reports/chat-plan.md'].filter(x => above.includes(x)),
        detail: ['8719e1a6', 'ValueError', 'reports/chat-plan.md'].every(x => details.textContent.includes(x))};
    });
    const day = new Date('2026-10-23T16:00:00Z').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
    assert.deepEqual(foot, {line: `If you don't answer by ${day}: It stays a draft`, last: true, open: false, small: true, weight: '400',
      inButton: false, leaks: [], detail: true});

    // 12: untick pick 3 stays unticked; tick changes count. Untick 4, tick it back: Send ticked (3) sends 1, 2 and 4.
    await page.locator('.decide [data-qc-tick="3"]').uncheck();
    assert.equal((await page.evaluate(card)).buttons[1].label, 'Send ticked (2)');
    await page.keyboard.press('4');                       // a number ticks or unticks, as on the keyboard row
    assert.equal((await page.evaluate(card)).buttons[1].label, 'Send ticked (3)');
    await page.keyboard.press('c');                       // C: note works on a several-picks card too
    assert.ok(await page.evaluate(() => document.activeElement === document.querySelector('.decide .dc-note textarea')));
    await page.keyboard.type('Pages: only pricing for now.');
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    await page.locator('.decide .dc-actions [data-dc="ticked"]').click();
    assert.equal(await page.locator('.decide .dc-toast span').textContent(), 'Sent 3 ticked · sent to Writer');
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-four', target: {comment: 'q-t-four'},
      answers: {decision: ['Calls', 'Visitor notes', 'Spend cap']}, other: 'Pages: only pricing for now.'});

    // 15: Tico's own check says Tico, never the assistant's name or "You asked"; each of its buttons says what happens.
    await page.locator('.decide .dc-card[data-key="task:t-stuck"]').waitFor();
    const check = await page.evaluate(() => ({who: document.querySelector('.decide .dc-who-name').textContent.trim(),
      header: document.querySelector('.decide .dc-who').textContent,
      buttons: [...document.querySelectorAll('.decide .dc-actions button')].map(b => b.querySelector('.qc-then')?.textContent)}));
    assert.equal(check.who, 'Tico');
    assert.ok(!/Assistant|You asked/.test(check.header), check.header);
    assert.deepEqual(check.buttons, ['The bot is woken again', 'The task is closed']);

    // 13: Yes to all sends every pick.
    items = [question('t-all')];
    await page.evaluate(async () => { decideStop(); await v2Refresh(); decideOpen('task:t-all'); });
    await page.locator('.decide .dc-card[data-key="task:t-all"]').waitFor();
    await page.waitForFunction(() => DEC && !DEC.busy && performance.now() >= (DEC.lockUntil || 0));
    await page.locator('.decide .dc-actions [data-dc="all"]').click();
    await page.waitForFunction(() => !DEC.pending && !DEC_OUTBOX.size, null, {timeout: 8000});
    assert.deepEqual(answers.shift(), {task: 't-all', target: {comment: 'q-t-all'},
      answers: {decision: ['Calls', 'Visitor notes', 'Pages', 'Spend cap']}});

    // 11: the task page's Needs you box is the same card: same header, question, why line, picks and buttons.
    items = [question('t-page')];
    await page.evaluate(async () => { location.hash = '#/tasks'; await v2Refresh(); });
    const decided = await page.evaluate(() => {
      const it = S.v2.needs.find(x => x.id === 't-page'), q = qcardQuestion(it);
      return {body: qcardBodyHTML(it, q, {attr: 'qc'}), actions: qcardActionsHTML(it, q, {attr: 'qc'}), sub: qcardSub(it)};
    });
    await page.evaluate(() => { location.hash = '#/task/t-page'; });
    const box = page.locator('.qcard[data-qcard="t-page"]');
    await box.waitFor();
    const onPage = await box.evaluate(el => ({who: el.querySelector('.qc-who').textContent, sub: el.querySelector('.qc-sub').textContent,
      picks: [...el.querySelectorAll('.qc-pick')].map(p => p.textContent.replace(/\s+/g, ' ').trim()),
      buttons: [...el.querySelectorAll('.qc-actions button')].map(b => b.textContent.replace(/\s+/g, ' ').trim()),
      fallback: el.querySelector('.qc-fallback').textContent, details: el.querySelector('.qc-details').open}));
    assert.equal(onPage.who, 'Writer');
    assert.equal(onPage.sub, ' · ' + decided.sub);
    assert.deepEqual(onPage.picks, [
      `1. Calls (recommended)${PICKS[0].then}`, `2. Visitor notes (recommended)${PICKS[1].then}`,
      `3. Pages${PICKS[2].then}`, `4. Spend cap (recommended)${PICKS[3].then}`].map(s => s.replace(/\s+/g, ' ')));
    assert.deepEqual(onPage.buttons, ['Yes to allAll 4 go to Writer as yes', 'Send ticked (3)These 3 as yes; Writer hears "not this one" for the rest']);
    assert.equal(onPage.details, false);
    // Unticking on the task page sends the rest, as on the card.
    await box.locator('[data-qc-tick="0"]').uncheck();
    await box.locator('[data-qc="ticked"]').click();
    for (let i = 0; i < 100 && !answers.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(answers.shift(), {task: 't-page', target: {comment: 'q-t-page'}, answers: {decision: ['Visitor notes', 'Spend cap']}});

    // A redraw while the person is mid-answer keeps their ticks and note: the task's details land after they started.
    items = [question('t-draft')];
    let release;
    const held = new Promise(r => { release = r; });
    await page.route(url => url.pathname.endsWith('/api/v2/tasks/t-draft'), async route => { await held; await route.fallback(); });
    await page.evaluate(async () => { location.hash = '#/tasks'; await v2Refresh(); location.hash = '#/task/t-draft'; });
    const draft = page.locator('.qcard[data-qcard="t-draft"]');
    await draft.waitFor();
    await draft.locator('[data-qc-tick="0"]').uncheck();
    await draft.locator('[data-qc-tick="2"]').check();
    await draft.locator('.qc-note').fill('Calls once the phone line is in.');
    const before = await draft.elementHandle();
    release();
    await page.waitForFunction(el => !el.isConnected, before);            // the dialog was drawn again
    const kept = () => page.locator('.qcard[data-qcard="t-draft"]').evaluate(el => ({
      ticked: [...el.querySelectorAll('[data-qc-tick]:checked')].map(x => Number(x.dataset.qcTick)),
      note: el.querySelector('.qc-note').value, count: el.querySelector('.qc-count').textContent}));
    assert.deepEqual(await kept(), {ticked: [1, 2, 3], note: 'Calls once the phone line is in.', count: '3'});
    // Any other redraw of the box (a property saved) keeps them too.
    await page.evaluate(() => { const el = document.querySelector('.qcard[data-qcard="t-draft"]'); el.outerHTML = qcardBoxHTML({id: 't-draft'}); });
    assert.deepEqual(await kept(), {ticked: [1, 2, 3], note: 'Calls once the phone line is in.', count: '3'});
    await page.locator('.qcard[data-qcard="t-draft"] [data-qc="ticked"]').click();
    for (let i = 0; i < 100 && !answers.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(answers.shift(), {task: 't-draft', target: {comment: 'q-t-draft'},
      answers: {decision: ['Visitor notes', 'Pages', 'Spend cap']}, other: 'Calls once the phone line is in.'});

    // The bot rewords its question while the old box is still open (the Needs you data refreshes on its own): the old
    // answer is never sent against the new question, nor put back into it, and the box says the question changed.
    items = [question('t-swap')];
    await page.evaluate(async () => { location.hash = '#/tasks'; await v2Refresh(); location.hash = '#/task/t-swap'; });
    const swap = () => page.locator('.qcard[data-qcard="t-swap"]');
    await swap().waitFor();
    await swap().locator('[data-qc-tick="2"]').check();
    await swap().locator('.qc-note').fill('Old answer');
    const reword = label => page.evaluate(label => {
      const q = S.v2.needs.find(x => x.id === 't-swap').ask.refs.questions[0];
      q.options = [...q.options].reverse().map(o => o.label === 'Pages' || o.label === 'Docs pages' ? {...o, label} : o);
    }, label);
    const fresh = () => swap().evaluate(el => ({warned: !!el.querySelector('.qc-changed'), note: el.querySelector('.qc-note').value,
      ticked: [...el.querySelectorAll('[data-qc-tick]:checked')].map(x => x.closest('.qc-pick').querySelector('.qc-l').textContent.trim())}));
    const NEW_TICKS = ['1. Spend cap (recommended)', '3. Visitor notes (recommended)', '4. Calls (recommended)'];
    // Sent before any redraw: refused here, and the box is drawn again from the new question.
    await reword('Docs pages');
    await swap().locator('.qc-note').type('!');
    await swap().locator('[data-qc="ticked"]').click();
    await page.waitForTimeout(100);
    assert.equal(answers.length, 0);
    assert.deepEqual(await fresh(), {warned: true, note: '', ticked: NEW_TICKS});
    // Ticked, with no redraw and no Send: the tick on the old box redraws it from the new question, its new labels
    // and recommended ticks, with the changed line and an empty note, and sends nothing.
    await swap().locator('.qc-note').fill('Third try');
    await reword('Pages');
    await swap().locator('[data-qc-tick="1"]').check();             // the old box's unticked "Docs pages"
    await page.waitForTimeout(100);
    assert.equal(answers.length, 0);
    assert.deepEqual(await fresh(), {warned: true, note: '', ticked: ['1. Calls (recommended)', '2. Visitor notes (recommended)', '4. Spend cap (recommended)']});
    assert.deepEqual(await swap().evaluate(el => [...el.querySelectorAll('.qc-pick .qc-l')].map(x => x.textContent.trim())),
      ['1. Calls (recommended)', '2. Visitor notes (recommended)', '3. Pages', '4. Spend cap (recommended)']);

    // Typed into, then redrawn: the old draft is dropped, not carried into the newer wording.
    await swap().locator('[data-qc-tick="1"]').uncheck();
    await swap().locator('.qc-note').fill('Second try');
    await reword('Docs pages');
    await swap().locator('.qc-note').type('?');
    await page.evaluate(() => { const el = document.querySelector('.qcard[data-qcard="t-swap"]'); el.outerHTML = qcardBoxHTML({id: 't-swap'}); });
    assert.deepEqual(await fresh(), {warned: true, note: '', ticked: NEW_TICKS});
    await swap().locator('[data-qc="ticked"]').click();
    for (let i = 0; i < 100 && !answers.length; i++) await page.waitForTimeout(20);
    assert.deepEqual(answers.shift(), {task: 't-swap', target: {comment: 'q-t-swap'}, answers: {decision: ['Spend cap', 'Visitor notes', 'Calls']}});

    // Picks with pictures show the same pictures on the card and on the task page, several picks included; a Yes / No
    // asked with no options still says what each button does.
    await page.route('**/api/v2/tasks/*/files', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({files: FILES})}));
    await page.route('**/api/v2/files/**', route => route.fulfill({contentType: 'image/png', body: PNG}));
    const seen = {};
    for (const id of ['t-pair', 't-several', 't-yn']) {
      items = [id === 't-pair' ? pair(id) : id === 't-several' ? several(id) : yesNo(id)];
      // A page change draws a frame later (its fade): the card opens over the Tasks page once it is drawn.
      await page.evaluate(async id => {
        location.hash = '#/tasks';
        await new Promise(done => { const drawn = () => S.route === '#/tasks' ? done() : requestAnimationFrame(drawn); drawn(); });
        await v2Refresh(); decideOpen('task:' + id);
      }, id);
      await page.locator(`.decide .dc-card[data-key="task:${id}"]`).waitFor();
      if (id !== 't-yn') await page.waitForFunction(() => document.querySelectorAll('.decide .dc-body img[data-tf-src]').length >= 2);
      const decide = await page.evaluate(([pictures]) => ({pics: eval(pictures)('.decide .dc-body'),
        thens: [...document.querySelectorAll('.decide .dc-body .qc-then, .decide .dc-actions .qc-then')].map(x => x.textContent.trim())}), [pictures.toString()]);
      await page.evaluate(id => { location.hash = '#/tasks'; location.hash = `#/task/${id}`; }, id);
      const box = page.locator(`.qcard[data-qcard="${id}"]`);
      await box.waitFor();
      if (id !== 't-yn') await page.waitForFunction(id => document.querySelectorAll(`.qcard[data-qcard="${id}"] img[data-tf-src]`).length >= 2, id);
      const task = await page.evaluate(([pictures, id]) => ({pics: eval(pictures)(`.qcard[data-qcard="${id}"]`),
        thens: [...document.querySelectorAll(`.qcard[data-qcard="${id}"] .qc-then`)].map(x => x.textContent.trim())}), [pictures.toString(), id]);
      seen[id] = {decide, task};
    }
    const pics = n => ['f-a', 'f-b', 'f-c'].slice(0, n).map(f => `/api/v2/files/${f}?v=1`);
    assert.deepEqual(seen['t-pair'].decide.pics.map(u => u.replace(/^.*(\/api\/)/, '/api/')), pics(2));
    assert.deepEqual(seen['t-pair'].task.pics, seen['t-pair'].decide.pics);
    assert.deepEqual(seen['t-several'].decide.pics.map(u => u.replace(/^.*(\/api\/)/, '/api/')), pics(3));
    assert.deepEqual(seen['t-several'].task.pics, seen['t-several'].decide.pics);
    assert.deepEqual(seen['t-yn'].decide.thens, ['Writer hears "No"', 'Writer hears "Yes"']);
    assert.deepEqual(seen['t-yn'].task.thens, seen['t-yn'].decide.thens);
    assert.deepEqual(errors, []);
    console.log('Question card: four picks ticked by recommendation, Send ticked and Yes to all, what-happens lines, '
      + 'asker/age/holds up, Tico for checks, fallback at the foot, ids under Details, same card on the task page');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
