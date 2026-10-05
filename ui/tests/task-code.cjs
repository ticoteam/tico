// Offline regression for code work on tasks (docs: PLAN sections 4, 5 and 9). Fixtures only.
//  - the task modal's right rail: Code has one line per worktree (repo · branch · ↑ahead ↓behind · N files, a missing
//    or removed one says so) and per pull request (#212 title and its checks, conflict, review and comment chips), each
//    a GitHub link; a mover removes one with its ✕ (DELETE .../links/{id});
//  - Subtasks: the roll-up line from children_summary, one level of children from GET /tasks/{id}/tree (the list's status
//    icon, PR badge, owner), a child's own children on expand, and "Add subtask" posting a task with a parent relation;
//  - a worktree with no repo names it from its folder, else says "unknown repo" (muted);
//  - a task with no code links and no children has no rail at all; on a phone the rail sits under the details;
//  - the bot page's Active rows carry one small PR badge each, from the task's pr_state, and none without PRs.
// TASK_CODE_SHOTS=<dir> saves the screenshots for the owner.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const SHOTS = process.env.TASK_CODE_SHOTS || '';

const now = new Date().toISOString();
const worktreeError = 'This bot needs write access to the attached repository. Literal <b>diagnostic</b>: /workspace/' + 'nested-directory-'.repeat(24) + '/checkout';
const task = (id, over = {}) => ({id, title: id, body: 'Details.', owner: 'bot:eng', requester: 'human:ana', status: 'doing',
  lane: 'company', rank: 1, labels: [], links: [], parts: {total: 0, done: 0}, version: 2, created: now, updated: now, ...over});
const pr = (id, number, title, over = {}) => ({id, kind: 'pr', repo: 'acme/web', number, url: `https://github.com/acme/web/pull/${number}`,
  title, state: 'open', checks: 'passing', mergeable: 'clean', review_state: null, pending_comments: 0, ...over});
function fixtures() {
  const parent = task('t-checkout', {title: 'Ship the new checkout', pr_state: 'failing',
    // As backend/hubdb.py children_summaries sends it: total/done over the whole tree, direct_* over the children.
    children_summary: {total: 10, open: 6, done: 4, prs_total: 9, prs_merged: 7, direct_total: 4, direct_done: 1},
    // Worktree rows as backend/worktrees.py writes them: url worktree:<hex>, the heartbeat's report in detail_json.
    links: [
      {id: 'w1', kind: 'worktree', url: 'worktree:9f2c1a', title: 'acme/web worktree', repo: 'acme/web', branch: 'tico/t-check-checkout',
       state: 'present', path: 'tasks/t-check/eng__web', computer_id: 'r1', number: null, checks: null,
       detail_json: JSON.stringify({link_id: 'w1', state: 'present', branch: 'tico/t-check-checkout', ahead: 2, behind: 1, dirty_files: 3, last_commit: 'Add the summary step', size_mb: 41})},
      // No repo on the row: the folder (<bot>__<repo>) still names it.
      {id: 'w2', kind: 'worktree', url: 'worktree:77ab03', title: 'worktree', repo: null, branch: 'tico/t-check-api', state: 'unknown', path: 'tasks/t-check/api__payments-api',
       detail_json: JSON.stringify({error: worktreeError})},
      {id: 'w3', kind: 'worktree', url: 'worktree:0c11de', title: 'acme/docs worktree', repo: 'acme/docs', branch: 'tico/t-check-docs', state: 'removed',
       detail_json: JSON.stringify({state: 'removed', ahead: 0, behind: 0, dirty_files: 0})},
      pr('p1', 212, 'Checkout summary step', {checks: 'failing', pending_comments: 2, review_state: 'changes_requested'}),
      pr('p2', 214, 'Price rounding', {checks: 'pending', mergeable: 'conflict'}),
      pr('p3', 198, 'Cart badge', {state: 'merged'}),
      {id: 'd1', kind: 'doc', url: 'https://docs.example.com/checkout', title: 'Checkout spec', state: 'open'},
    ]});
  const kid = (id, title, status, owner, pr_state = null, children = []) => ({id, title, status, owner, pr_state, children});
  const tree = [kid('k1', 'Summary step on web', 'review', 'bot:eng', 'failing', [kid('k1a', 'Copy for the summary', 'done', 'human:sam', 'merged')]),
    kid('k2', 'Rounding in the API', 'doing', 'bot:api', 'conflict'),
    kid('k3', 'Cart badge', 'done', 'bot:eng', 'merged'),
    kid('k4', 'Check the receipts email', 'open', 'human:sam')];
  const plain = task('t-plain', {title: 'Book the team offsite', owner: 'human:ana'});
  const others = [task('t-review', {title: 'Review the pricing copy', pr_state: 'changes_requested'}),
    task('t-open', {title: 'Upgrade the image library', pr_state: 'open'}),
    task('t-none', {title: 'Write the release notes', pr_state: null})];
  return {tasks: [parent, plain, ...others], tree};
}

async function open(browser, {viewport = {width: 1440, height: 900}, theme = 'dark', at = '#/task/t-checkout', mover = true, rich = false} = {}) {
  const data = fixtures();
  if (rich) {
    Object.assign(data.tasks[0], {body: 'Keep the old checkout behind a flag.\n\nVerify receipts in both flows.',
      note: 'The draft is checked; the rollout still needs a decision.', labels: ['checkout'], acceptance_criteria: ['Both flows send a receipt.']});
    data.tasks.push(task('t-outward', {title: 'Choose the rollout day', owner: 'human:ana', requester: 'bot:eng', status: 'open'}),
      task('t-finished', {title: 'Check the receipt wording', status: 'closed', requester: 'bot:eng', done_at: now, closed_at: now}),
      ...data.tree.map(({children, ...t}) => task(t.id, {...t, relations: {parent: [{id: 't-checkout', title: 'Ship the checkout redesign', status: 'doing', owner: 'bot:eng', direction: 'out'}]}})));
    data.comments = [{id: 'm1', kind: 'say', from_actor: 'bot:eng', body: 'The draft is checked.', created: now, refs: {task: 't-checkout', note: true}},
      {id: 'm2', kind: 'ask', from_actor: 'bot:eng', body: 'Which rollout day?', created: now, answers: [], refs: {task: 't-checkout',
        questions: [{id: 'day', question: 'Which rollout day?', options: [{label: 'Tuesday'}, {label: 'Thursday'}], multi: true, other: true}]}}];
    data.events = [{id: 'e1', ts: now, actor: 'bot:eng', field: 'status', old: 'open', new: 'doing', note: 'The draft is checked.'}];
    data.files = [{id: 'f-notes', name: 'review.md', mime: 'text/markdown', current_version: 2, archived: false,
      versions: [2, 1].map(n => ({n, size: 48, mime: 'text/markdown', created: now, by: 'bot:eng',
        url: `/api/v2/files/f-notes?v=${n}`, answers: []}))}];
  }
  const page = await browser.newPage({viewport, hasTouch: viewport.width <= 390, serviceWorkers: 'block'});
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const me = {id: 'ana', name: 'Ana', email: 'ana@example.com', role: 'owner', mover, cloud: true, registered: true};
  const bots = [['eng', 'Engineer'], ['api', 'API Engineer']].map(([name, display_name]) =>
    ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true, operator: 'ana', can_manage: true,
      users: [{id: 'ana', name: 'Ana'}], my_access: {see: true, read: true, write: true}}));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tasks' && method === 'GET') {
      const owner = url.searchParams.get('owner');
      // List rows leave out the links' detail_json (and pr_sha), as backend/app.py lists do; the detail has them.
      const requester = url.searchParams.get('requester');
      const rows = (owner ? data.tasks.filter(t => t.owner === (owner.includes(':') ? owner : 'bot:' + owner)) : requester ? data.tasks.filter(t => t.requester === 'bot:' + requester) : data.tasks)
        .map(t => ({...t, links: (t.links || []).map(({detail_json, pr_sha, ...l}) => l)}));
      return json({tasks: rows, next_offset: null});
    }
    if (p === '/api/v2/tasks' && method === 'POST') {
      const body = req.postDataJSON(); writes.push({method, p, body});
      const owner = body.owner.includes(':') ? body.owner : 'bot:' + body.owner;
      const up = (body.relations || []).find(r => r.kind === 'parent');
      const made = task('k5', {title: body.title, owner, status: 'open', relations: up ? {parent: [{id: up.task, direction: 'out'}]} : {}});
      data.tree.push({id: 'k5', title: body.title, status: 'open', owner, pr_state: null, children: []});
      data.tasks[0].children_summary = {...data.tasks[0].children_summary, total: 11, open: 7, direct_total: 5};
      return json({task: made});
    }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    // As backend/hubdb.py task_tree answers: the direct children, each with its own nested children.
    if (tree) return json(tree[1] === 't-checkout' ? data.tree : []);
    const link = p.match(/^\/api\/v2\/tasks\/([^/]+)\/links\/([^/]+)$/);
    if (link && method === 'DELETE') {
      writes.push({method, p});
      const t = data.tasks.find(x => x.id === link[1]); t.links = t.links.filter(l => l.id !== link[2]);
      return json({links: t.links});
    }
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && method === 'POST') {
      const t = data.tasks.find(t => t.id === one[1]), body = req.postDataJSON();
      writes.push({method, p, body}); Object.assign(t, body, {version: t.version + 1});
      return json({task: t});
    }
    if (one && method === 'GET') {
      const t = data.tasks.find(x => x.id === decodeURIComponent(one[1]));
      if (!t) return json({error: {detail: 'Not found'}}, 404);
      return json({task: t, children: t.id === 't-checkout' ? data.tree.map(({children, ...k}) => k) : [],
        comments: rich && t.id === 't-checkout' ? data.comments : [],
        events: rich && t.id === 't-checkout' ? data.events : []});
    }
    if (rich && p === '/api/v2/tasks/t-checkout/files') return json({files: data.files});
    if (rich && p === '/api/v2/files/f-notes') return route.fulfill({contentType: 'text/markdown', body: '# Review\n\nReceipt check version ' + url.searchParams.get('v') + '.'});
    if (rich && p.endsWith('/comments') && method === 'POST') {
      const body = req.postDataJSON(); writes.push({method, p, body});
      data.comments.push({id: 'm3', kind: 'say', from_actor: 'human:ana', body: body.text, created: now, refs: {task: 't-checkout'}});
      return json({woke: true});
    }
    return json({});
  });
  await page.goto('https://tico-ui.test/' + at);
  return {page, errors, writes, data};
}

async function desktop(browser) {
  const {page, errors, writes} = await open(browser);
  const modal = page.locator('#task-modal');
  const rail = modal.locator('[data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  // Code: worktrees first, then pull requests; other links stay under Links.
  const lines = await rail.locator('.code-line').allInnerTexts();
  assert.equal(lines.length, 6);
  assert.match(lines[0], /^web\s*·\s*tico\/t-check-checkout\s*·\s*↑2 ↓1\s*·\s*3 files/);
  assert.match(lines[1], /^payments-api\s*·\s*tico\/t-check-api\s*unknown/);
  // With neither a repo, a folder nor a branch that names one: "unknown repo", muted; never the word "worktree".
  const bare = await page.evaluate(() => {
    const host = document.createElement('ul'); host.className = 'code-list';
    host.innerHTML = taskCodeLineHTML({id: 'w9', kind: 'worktree', url: 'worktree:1', repo: null, branch: 'tico/t-check-misc', state: 'present'}, true, [{kind: 'pr', repo: 'acme/web'}]);
    document.querySelector('#task-modal [data-task-rail]').append(host);
    const repo = host.querySelector('.code-repo');
    const out = {text: host.innerText, cls: repo.className, color: getComputedStyle(repo).color, muted: getComputedStyle(document.querySelector('#task-modal .tmeta')).color};
    host.remove();
    return out;
  });
  assert.match(bare.text, /^unknown repo\s*·\s*tico\/t-check-misc/);
  assert.equal(bare.cls, 'code-repo unknown');
  assert.equal(bare.color, bare.muted, 'unknown repo is muted');
  // A branch name is never taken for a repo (fix-api in the web repo is not "api"); only the folder says.
  assert.equal(await page.evaluate(() => taskWorktreeRepo({kind: 'worktree', branch: 'tico/fix-web'}, [{kind: 'pr', repo: 'acme/web'}])), '');
  assert.equal(await page.evaluate(() => taskWorktreeRepo({kind: 'worktree', path: 'tasks/t1/eng__web'}, [])), 'web');
  assert.match(lines[2], /^docs\s*·\s*tico\/t-check-docs\s*removed/);
  assert.match(lines[3], /#212\s*Checkout summary step\s*✕\s*changes requested\s*2 comments/);
  assert.match(lines[4], /#214\s*Price rounding\s*checks running\s*conflict/, 'a chip says what it means');
  assert.match(lines[5], /#198\s*Cart badge\s*merged/);
  assert.equal(await rail.locator('.code-line.wt a').first().getAttribute('href'), 'https://github.com/acme/web/tree/tico/t-check-checkout');
  // No repo, no GitHub link. Errors are readable inline without hovering or opening a tooltip.
  assert.equal(await rail.locator('.code-line[data-code-link="w2"] a').count(), 0);
  const explanation = rail.locator('.code-line[data-code-link="w2"] .code-error');
  assert.equal(await explanation.isVisible(), true);
  assert.equal(await explanation.innerText(), worktreeError);
  assert.equal(await explanation.locator('*').count(), 0, 'literal markup stays text');
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  assert.equal(await explanation.isVisible(), true, 'keyboard use needs no tooltip');
  assert.equal(await rail.locator('.code-line[data-code-link="w1"] .code-error, .code-line.pr .code-error').count(), 0, 'clean worktrees and PR rows have no empty explanation');
  // A live worktree cannot be taken off here (its computer cleans it up when the task closes); a removed one can.
  assert.equal(await rail.locator('.code-line[data-code-link="w1"] [data-code-drop]').count(), 0);
  assert.equal(await rail.locator('.code-line[data-code-link="w2"] [data-code-drop]').count(), 0);
  assert.equal(await rail.locator('.code-line[data-code-link="w3"] [data-code-drop]').getAttribute('aria-label'), 'Remove docs · tico/t-check-docs');
  assert.equal(await rail.locator('.code-line[data-code-link="p1"] [data-code-drop]').getAttribute('aria-label'), 'Remove #212');
  assert.equal(await rail.locator('.code-line[data-code-link="p1"] .code-chip[aria-label="Checks failing"]').count(), 1, 'a glyph chip has words');
  assert.equal(await rail.locator('.code-line.pr a').first().getAttribute('href'), 'https://github.com/acme/web/pull/212');
  assert.equal(await rail.locator('.code-line.pr a').first().getAttribute('target'), '_blank');
  assert.equal(await modal.locator('.tlinks a', {hasText: 'Checkout spec'}).count(), 1, 'a doc link stays in Links & files');
  assert.equal(await modal.locator('.tlinks a', {hasText: 'Checkout summary step'}).count(), 0, 'a PR is not listed twice');
  // Subtasks: the roll-up, one level, PR badges, expand a child.
  assert.equal(await rail.locator('.sub-sum').innerText(), '4 of 10 done · 7 PRs merged');
  assert.equal(await rail.locator('.sub-sum .sub-bar > i').evaluate(i => i.style.width), '40%', 'a thin bar beside it');
  assert.equal(await rail.locator('#task-subs-h .cnt').innerText(), '4', 'the heading counts the direct children');
  // One "Add subtask": the rail's field, and no second button in the controls.
  assert.equal(await modal.getByRole('button', {name: 'Add subtask'}).count(), 0);
  assert.equal(await rail.locator('.task-subs > .sub-list > .sub-row').count(), 4);
  const first = rail.locator('.sub-row[data-sub="k1"]');
  assert.match(await first.locator('> .sub-line').innerText(), /Summary step on web\s*PR ✕/);
  // The same status icon as the Tasks list, with its words for screen readers.
  assert.equal(await first.locator('> .sub-line > .task-status').getAttribute('data-status'), 'review');
  assert.equal(await first.locator('> .sub-line > .task-status').getAttribute('aria-label'), 'In review');
  assert.equal(await rail.locator('.sub-row[data-sub="k3"] > .sub-line > .task-status').getAttribute('data-status'), 'done');
  assert.equal(await rail.locator('.sub-row[data-sub="k4"] .pr-badge').count(), 0, 'no PRs, no badge');
  assert.equal(await rail.locator('[data-sub="k1a"]').count(), 0, 'one level shown');
  await first.locator('[data-sub-toggle]').click();
  await rail.locator('[data-sub="k1a"]').waitFor();
  assert.equal(await first.locator('[data-sub-toggle]').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.subToggle), 'k1', 'focus stays on the toggle');
  // The rail sits to the right of the details on a desktop.
  const main = await modal.locator('.tmodal-main').boundingBox(), side = await rail.boundingBox();
  assert.ok(side.x > main.x + main.width - 1, 'the rail is to the right');
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await modal.screenshot({path: path.join(SHOTS, 'task-code-desktop-dark.png')}); }

  // Add subtask: a task with a parent relation, for whoever is picked (the parent's owner to start with).
  const form = rail.locator('[data-sub-add]');
  // For whom: an avatar picker, not a native select.
  assert.equal(await form.locator('select').count(), 0);
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'eng');
  await form.locator('[data-sub-owner]').click();
  await modal.locator('.prop-pop [data-prop-pick="api"]').click();
  await page.waitForFunction(() => document.querySelector('#task-modal [data-sub-add] input[name=owner]')?.value === 'api');
  assert.match(await form.locator('[data-sub-owner]').getAttribute('aria-label'), /^For API Engineer/);
  const add = form.locator('input[name=title]');
  await add.fill('Check the receipts in the app'); await add.press('Enter');
  await rail.locator('.sub-row[data-sub="k5"]').waitFor();
  assert.deepEqual(writes.at(-1), {method: 'POST', p: '/api/v2/tasks', body: {title: 'Check the receipts in the app', body: 'Check the receipts in the app', owner: 'api', relations: [{task: 't-checkout', kind: 'parent'}]}});
  assert.equal(await rail.locator('#task-subs-h .cnt').innerText(), '5');
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'api', 'the pick stays for the next one');
  assert.equal(await page.evaluate(() => document.activeElement?.name), 'title', 'ready for the next subtask');
  // Remove a link with its ✕ (shown on hover).
  const line = rail.locator('.code-line[data-code-link="p2"]');
  await line.hover();
  await line.locator('[data-code-drop]').click();
  await rail.locator('.code-line[data-code-link="p2"]').waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {method: 'DELETE', p: '/api/v2/tasks/t-checkout/links/p2'});

  // A task with nothing in the rail has no rail and no empty headings.
  await page.evaluate(() => { location.hash = '#/task/t-plain'; });
  await modal.locator('.tmodal-title', {hasText: 'Book the team offsite'}).waitFor();
  await page.waitForTimeout(200);
  assert.equal(await modal.locator('[data-task-rail]').isHidden(), true);
  assert.equal(await modal.evaluate(d => d.classList.contains('has-rail')), false);
  assert.equal(await modal.locator('text=Subtasks').count(), 0);
  // "Add subtask" in the task's "…" menu opens the rail's field, which then is the only one.
  await modal.locator('[data-task-more]').click();
  await modal.locator('.prop-pop .tl-mi', {hasText: 'Add subtask'}).click();
  await modal.locator('[data-sub-add] input[name=title]').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Add subtask');
  assert.equal(await modal.getByRole('button', {name: 'Add subtask'}).count(), 0);
  assert.equal(await modal.locator('[data-sub-add] input[name=owner]').inputValue(), 'human:ana');
  assert.deepEqual(errors, []);
  console.log('task rail desktop: ok');
  await page.close();
}

async function phone(browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  const modal = page.locator('#task-modal'), rail = modal.locator('[data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  const main = await modal.locator('.tmodal-main').boundingBox(), side = await rail.boundingBox(), chat = await modal.locator('.task-chat').boundingBox();
  assert.ok(side.y >= main.y + main.height - 1 && chat.y >= side.y + side.height - 1, 'details, then the rail, then comments');
  assert.ok(side.x >= 0 && side.x + side.width <= 390, 'inside the screen');
  const overflow = await modal.evaluate(d => d.querySelector('.tmodal-body').scrollWidth - d.querySelector('.tmodal-body').clientWidth);
  assert.ok(overflow <= 1, 'no sideways scroll');
  const explanation = rail.locator('.code-line[data-code-link="w2"] .code-error');
  await explanation.scrollIntoViewIfNeeded();
  assert.equal(await explanation.isVisible(), true, 'touch users can read the error without hover');
  assert.equal(await explanation.innerText(), worktreeError);
  assert.equal(await explanation.locator('*').count(), 0, 'mobile also preserves literal markup');
  const textBox = await explanation.evaluate(el => ({height: el.clientHeight,
    line: parseFloat(getComputedStyle(el).lineHeight), overflow: el.scrollWidth - el.clientWidth}));
  assert.ok(textBox.height > textBox.line * 2, 'long paths wrap onto multiple lines');
  assert.ok(textBox.overflow <= 1, 'the error itself has no horizontal overflow');
  if (SHOTS) {
    await rail.scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(SHOTS, 'task-code-phone-dark.png')});
  }
  assert.deepEqual(errors, []);
  console.log('task rail phone: ok');
  await page.close();
}

async function member(browser) {
  // Someone who may not move tasks sees the links but no ✕ and no Add subtask.
  const {page, errors} = await open(browser, {mover: false});
  const rail = page.locator('#task-modal [data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  assert.equal(await rail.locator('[data-code-drop]').count(), 0);
  assert.equal(await rail.locator('[data-sub-add]').count(), 0);
  assert.deepEqual(errors, []);
  console.log('task rail member: ok');
  await page.close();
}

async function botPage(browser) {
  const {page, errors} = await open(browser, {at: '#/bot/eng'});
  const rows = page.locator('#t-open .trow');
  await rows.first().waitFor();
  const badge = title => page.locator('#t-open .trow', {hasText: title}).locator('.pr-badge');
  assert.equal(await badge('Ship the new checkout').innerText(), 'PR ✕');
  assert.equal(await badge('Ship the new checkout').getAttribute('aria-label'), 'Checks failing');
  assert.equal(await badge('Review the pricing copy').innerText(), 'PR changes');
  assert.equal(await badge('Upgrade the image library').innerText(), 'PR');
  assert.equal(await badge('Write the release notes').count(), 0, 'no PRs, no badge');
  for (const title of ['Ship the new checkout', 'Write the release notes']) {
    const box = await page.locator('#t-open .trow', {hasText: title}).locator('.trow-head').boundingBox();
    assert.ok(box.height <= 44, `${title}: still a compact row`);
  }
  if (SHOTS) await page.locator('#pane-tasks .bot-active').screenshot({path: path.join(SHOTS, 'bot-active-pr-badges-dark.png')});
  assert.deepEqual(errors, []);
  console.log('bot page badges: ok');
  await page.close();
}

// The bot rail is another entry to the manager's task detail, with no lost files, questions or history.
async function botDetail(browser) {
  const ready = async d => {
    await d.locator('[data-sub="k1"]').waitFor();
    await d.locator('[data-tf-file="f-notes"]').waitFor();
    await d.locator('.task-comments .ask').waitFor();
  };
  const contents = d => d.evaluate(el => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('time').forEach(el => el.remove());
    const text = selector => [...copy.querySelectorAll(selector)].map(x => x.textContent.trim());
    return {title: text('.tmodal-title'), details: text('.tdesc'), properties: text('[data-task-props] .prop'),
      criteria: text('.tcriteria'), code: text('.code-line'), children: text('.sub-line'), comments: text('.task-comments'),
      questions: text('.ask-text'), files: text('.tf-name'), links: text('.tlinks')};
  });
  const shots = async (page, d, name, phone) => {
    if (!SHOTS) return;
    fs.mkdirSync(SHOTS, {recursive: true});
    await page.mouse.move(0, 0);
    await page.evaluate(() => document.activeElement?.blur());
    await d.evaluate(el => { el.scrollTop = 0; });
    await page.screenshot({path: path.join(SHOTS, name + '-top.png')});
    if (phone) for (const [suffix, selector] of [['code', '[data-task-rail]'], ['comments', '.task-chat']]) {
      await d.locator(selector).scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(SHOTS, name + '-' + suffix + '.png')});
    }
  };
  for (const phone of [false, true]) {
    const viewport = phone ? {width: 390, height: 844} : {width: 1440, height: 900};
    const {page, errors, writes} = await open(browser, {at: '#/bot/eng/tasks', viewport, rich: true});
    const d = page.locator('#task-modal'), row = page.locator('[data-task-detail="t-checkout"]');
    await row.locator('[data-task-detail-open]').waitFor();
    assert.equal(await row.locator('details, .tbody').count(), 0, 'no separate inline detail');
    assert.equal(await row.locator('.task-status').count(), 1, 'Active needs per-task status');
    assert.equal(await page.locator('.bot-done .task-status, .bot-done .pill').count(), 0, 'Done already supplies status context');
    await page.evaluate(() => { window.botBefore = BOT; window.botWorkBefore = document.querySelector('#bot-work'); });
    await row.locator('[data-task-detail-open]').focus();
    await page.keyboard.press('Enter');
    await ready(d);
    assert.equal(await d.evaluate(el => el.matches(':modal')), true, 'the same full task dialog');
    assert.equal(await page.evaluate(() => TASKS_ST), null, 'opening from the bot does not create a task manager');
    const botContents = await contents(d);
    assert.equal((botContents.comments.join('').match(/The draft is checked\./g) || []).length, 1, 'a mirrored note is read once');
    assert.match(botContents.comments.join(''), /moved it to Doing/, 'its status event remains');
    assert.ok(botContents.code.some(line => /↑2 ↓1/.test(line)), 'the full worktree report is loaded');
    if (phone) {
      const box = await d.boundingBox();
      assert.ok(box.x === 0 && box.y === 0 && box.width >= 389 && box.height >= 843, 'full-screen: ' + JSON.stringify(box));
      assert.equal(await d.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true, 'no horizontal overflow');
    }
    await shots(page, d, `task-detail-bot-${phone ? 'phone' : 'desktop'}-dark`, phone);
    // File versions and comments are usable through this entry, as they are in the manager.
    await d.locator('[data-tf-file="f-notes"]').click();
    await d.locator('[data-tf-v="1"]').click();
    await d.locator('[data-tf-view]', {hasText: 'Receipt check version 1.'}).waitFor();
    await d.locator('[data-tf-close]').click();
    await d.locator('.task-chat textarea').fill('Check the receipt totals too.');
    await d.locator('.task-chat button[type="submit"]').click();
    await d.locator('.task-comments', {hasText: 'Check the receipt totals too.'}).waitFor();
    assert.deepEqual(writes.at(-1), {method: 'POST', p: '/api/v2/tasks/t-checkout/comments', body: {text: 'Check the receipt totals too.'}});
    // A subtask opens in the same dialog even though no manager state exists on the bot page.
    await d.locator('[data-sub-open="k1"]').click();
    await d.locator('.tmodal-title', {hasText: 'Summary step on web'}).waitFor();
    await d.locator('[data-modal-close]').click();
    await page.waitForFunction(() => !history.state?.taskModal && !document.querySelector('#task-modal[open]'));
    assert.equal(new URL(page.url()).hash, '#/bot/eng/tasks');
    assert.equal(await page.evaluate(() => BOT === window.botBefore && document.querySelector('#bot-work') === window.botWorkBefore), true, 'the bot context survives');
    assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-task-detail-open')), true, 'focus returns to the task row');
    for (const how of ['back', 'escape']) {
      await row.locator('[data-task-detail-open]').click(); await ready(d);
      if (how === 'back') await page.goBack(); else await page.keyboard.press('Escape');
      await page.waitForFunction(() => !history.state?.taskModal && !document.querySelector('#task-modal[open]'));
      assert.equal(new URL(page.url()).hash, '#/bot/eng/tasks', how + ' returns to the bot');
    }
    for (const id of ['t-outward', 't-finished']) {
      if (id === 't-finished') await page.locator('.bot-done > summary').click();
      await page.locator(`[data-task-detail="${id}"] [data-task-detail-open]`).click();
      await d.locator(`[data-task-props]`).waitFor();
      await page.waitForFunction(id => document.querySelector('#task-modal')?.dataset.task === id, id);
      await d.locator('[data-modal-close]').click();
      await page.waitForFunction(() => !history.state?.taskModal && !document.querySelector('#task-modal[open]'));
    }
    assert.deepEqual(errors, []);
    await page.close();

    const manager = await open(browser, {at: '#/issues', viewport, rich: true});
    await manager.page.locator('[data-task-key="tt-checkout"] .tl-title').click();
    const peek = manager.page.locator('#task-peek'); await ready(peek);
    assert.deepEqual(await contents(peek), botContents, 'bot and manager render every task section from the same content');
    await shots(manager.page, peek, `task-detail-manager-${phone ? 'phone' : 'desktop'}-dark`, phone);
    if (!phone) {
      await peek.locator('[data-task-more]').click();
      await peek.getByRole('menuitem', {name: 'Open full'}).click();
      const full = manager.page.locator('#task-modal'); await ready(full);
      assert.deepEqual(await contents(full), botContents);
      await shots(manager.page, full, 'task-detail-manager-full-desktop-dark', false);
      await full.locator('[data-modal-close]').click();
    } else {
      await peek.locator('[data-modal-close]').click();
      await manager.page.waitForFunction(() => !history.state?.taskPeek);
    }
    await manager.page.evaluate(() => { location.hash = '#/bot/eng/tasks'; });
    await manager.page.locator('[data-task-detail="t-checkout"] [data-task-detail-open]').click();
    const modal = manager.page.locator('#task-modal'); await ready(modal);
    assert.equal(await manager.page.evaluate(() => TASKS_ST), null, 'manager state is cleared before entering the bot');
    if (phone) await modal.locator('[data-modal-close]').click(); else await manager.page.mouse.click(5, 5);
    await manager.page.waitForFunction(() => !history.state?.taskModal && !document.querySelector('#task-modal[open]') && !document.body.classList.contains('task-modal-open'));
    assert.equal(new URL(manager.page.url()).hash, '#/bot/eng/tasks');
    // Only the latest selection can open; a late detail response cannot resurrect a task on another page.
    await manager.page.route('**/api/v2/tasks/t-checkout', async route => { await new Promise(resolve => setTimeout(resolve, 250)); await route.fallback(); });
    await manager.page.evaluate(() => { void taskModalOpen('tt-checkout'); void taskModalOpen('tt-none'); });
    await modal.locator('.tmodal-title', {hasText: 'Write the release notes'}).waitFor();
    await manager.page.waitForTimeout(350);
    assert.equal(await modal.locator('.tmodal-title').innerText(), 'Write the release notes', 'a slower earlier tap cannot replace the latest task');
    await modal.locator('[data-modal-close]').click();
    await manager.page.waitForFunction(() => !history.state?.taskModal);
    await manager.page.evaluate(() => { void taskModalOpen('tt-checkout'); location.hash = '#/goals'; });
    await manager.page.waitForTimeout(350);
    assert.equal(await modal.isVisible(), false, 'navigating away cancels a pending open');
    assert.equal(await manager.page.evaluate(() => !!history.state?.taskModal || document.body.classList.contains('task-modal-open')), false, 'no stale history or scroll lock');
    const historyNotes = await manager.page.evaluate(() => {
      const ts = new Date().toISOString(), note = 'Receipt check complete.';
      const events = ['done', 'closed'].map((status, i) => ({kind: 'event', ts, actor: 'bot:eng', field: 'status', old: i ? 'done' : 'doing', new: status, note}));
      const comment = {kind: 'comment', ts, message: {from_actor: 'bot:eng', body: note, kind: 'say', created: ts, refs: {}}};
      const all = [...events, comment];
      return {html: all.map((x, i) => commentLineHTML(x, i, all)).join(''),
        otherActor: commentLineHTML(events[0], 0, [events[0], {...comment, message: {...comment.message, from_actor: 'human:ana'}}]),
        later: commentLineHTML(events[0], 0, [events[0], {...comment, ts: new Date(Date.parse(ts) + 6000).toISOString()}])};
    });
    assert.match(historyNotes.html, /moved it to Done/); assert.match(historyNotes.html, /moved it to Closed/);
    assert.equal((historyNotes.html.match(/Receipt check complete\./g) || []).length, 1, 'both transitions remain while the result appears once');
    assert.match(historyNotes.otherActor, /Receipt check complete\./, 'another actor does not hide an event note');
    assert.match(historyNotes.later, /Receipt check complete\./, 'a later comment does not hide an event note');
    assert.deepEqual(manager.errors, []);
    await manager.page.close();
  }
  console.log('bot task detail parity, files, comments, related tasks and return: ok');
}

module.exports = {open};
if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try { await desktop(browser); await phone(browser); await member(browser); await botPage(browser); await botDetail(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
