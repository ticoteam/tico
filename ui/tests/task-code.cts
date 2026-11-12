// Offline regression for code work on tasks (docs: PLAN sections 4, 5 and 9). Fixtures only.
//  - the task modal's right rail: Code has one line per worktree (repo · branch · ↑ahead ↓behind · N files, a missing
//    or removed one says so) and per pull request (#212 title and its checks, conflict, review and comment chips), each
//    a GitHub link; a mover removes one with its ✕ (DELETE .../links/{id}), or asks again with Retry cleanup when a
//    worktree's cleanup stopped retrying;
//  - Subtasks: the roll-up line from children_summary, one level of children from GET /tasks/{id}/tree (the list's status
//    icon, PR badge, owner), a child's own children on expand, and "Add subtask" posting a task with a parent relation;
//  - a worktree with no repo names it from its folder, else says "unknown repo" (muted);
//  - a task with no code links and no children has no rail at all; on a phone the rail sits under the details;
//  - only the latest open shows: a late detail cannot replace it or reopen a task after leaving the page;
//  - Runs (GET /tasks/{id}/run-view): the checkpoint with its author, background work still running, each try with what
//    woke it and what followed it (a resume or dismiss decision beside its state: "Canceled: dismissed by BotOps at 09:50Z, <note>";
//    none on a try with no decision), and the subagent tree with a nested subagent under its parent; a task with no runs has no section.
// TASK_CODE_SHOTS=<dir> saves the screenshots for the owner.
import type {Browser} from 'playwright';
import type {Task} from './tasks-page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
const {html, uiFile, harnesses} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {t} = require('./support/load.cts') as import('./support/load.cts').Api;
const SHOTS = process.env.TASK_CODE_SHOTS || '';

// Fixture rows are typed by the fields the scripts read; the rest stay unknown.
interface Link {id: string; kind: string; url: string; title: string; detail?: string; pr_sha?: string; [field: string]: unknown}
export interface CodeTask extends Task {
  links: Link[];
  children_summary?: Record<string, number> | undefined;
}
// A subtask as GET /tasks/{id}/tree answers it: its own children nested.
interface Kid {id: string; title: string; status: string; stage?: Stage; owner_id: string; pr_state: string | null; children: Kid[]}
// The stage a task is in, as GET /v2/tasks/{id} sends it.
interface Stage {id: string; name: string; kind: 'work' | 'done' | 'closed'; by_hand: boolean}
const stage = (name: string): Stage => ({id: 's-' + name.toLowerCase(), name, kind: 'work', by_hand: false});
// A write the fake server saw (a DELETE has no body).
export interface Write {
  method: string;
  p: string;
  body?: unknown;
}
export interface OpenOptions {
  viewport?: {width: number; height: number};
  theme?: string;
  at?: string;
  mover?: boolean;
  rich?: boolean;
}
// The app's classic-script globals, read inside page.evaluate.
declare var taskCodeLineHTML: (link: Record<string, unknown>, mover: boolean, links?: Record<string, unknown>[]) => string;
declare var taskCodeHTML: (task: Record<string, unknown>) => string;
declare var taskWorktreeRepo: (link: Record<string, unknown>, links: Record<string, unknown>[]) => string;
declare var taskModalOpen: (id: string) => Promise<unknown>;
declare var commentLineHTML: (line: unknown, index: number, all: unknown[]) => string;
declare var BOT: unknown;
declare var TASKS_ST: unknown;
declare var S: {route: string};
declare const window: Window & typeof globalThis & {copied: string[]; botBefore: unknown; botWorkBefore: Element | null};

const now = new Date().toISOString();
const worktreeError = 'This bot needs write access to the attached repository. Literal <b>diagnostic</b>: /workspace/' + 'nested-directory-'.repeat(24) + '/checkout';
const task = (id: string, over: Partial<CodeTask> = {}): CodeTask => ({id, title: id, body: 'Details.', owner_id: 'bot:eng', requester_id: 'human:ana', status: 'open', stage: stage('Doing'),
  lane: 'company', rank: 1, labels: [], links: [], parts: {total: 0, done: 0}, version: 2, created_at: now, updated_at: now, ...over});
const pr = (id: string, number: number, title: string, over: Partial<Link> = {}): Link => ({id, kind: 'pr', repo: 'acme/web', number, url: `https://github.com/acme/web/pull/${number}`,
  title, state: 'open', checks: 'passing', mergeable: 'clean', review_state: null, pending_comments: 0, ...over});
function fixtures() {
  const parent = task('t-checkout', {title: 'Ship the new checkout', pr_state: 'failing',
    // As backend/hubdb.py children_summaries sends it: total/done over the whole tree, direct_* over the children.
    children_summary: {total: 10, open: 6, done: 4, prs_total: 9, prs_merged: 7, direct_total: 4, direct_done: 1},
    // Worktree rows as backend/worktrees.py writes them: url worktree:<hex>, the heartbeat's report in detail.
    links: [
      {id: 'w1', kind: 'worktree', url: 'worktree:9f2c1a', title: 'acme/web worktree', repo: 'acme/web', branch: 'tico/t-check-checkout',
       state: 'present', path: 'tasks/t-check/eng__web', computer_id: 'r1', number: null, checks: null,
       detail: JSON.stringify({link_id: 'w1', state: 'present', branch: 'tico/t-check-checkout', ahead: 2, behind: 1, dirty_files: 3, last_commit: 'Add the summary step', size_mb: 41})},
      // No repo on the row: the folder (<bot>__<repo>) still names it.
      {id: 'w2', kind: 'worktree', url: 'worktree:77ab03', title: 'worktree', repo: null, branch: 'tico/t-check-api', state: 'unknown', path: 'tasks/t-check/api__payments-api',
       detail: JSON.stringify({error: worktreeError})},
      {id: 'w3', kind: 'worktree', url: 'worktree:0c11de', title: 'acme/docs worktree', repo: 'acme/docs', branch: 'tico/t-check-docs', state: 'removed',
       detail: JSON.stringify({state: 'removed', ahead: 0, behind: 0, dirty_files: 0})},
      // Cleanup refused until it stopped retrying (backend/worktrees.py heartbeat): still present, Retry cleanup offered.
      {id: 'w4', kind: 'worktree', url: 'worktree:5e09aa', title: 'acme/web worktree', repo: 'acme/web', branch: 'tico/t-check-old', state: 'present',
       detail: JSON.stringify({delete_requested: true, cleanup_requested: true, cleanup_attempts: 6, cleanup_gave_up: true,
         error: 'Worktree action failed; history kept. kept: ignored files'})},
      pr('p1', 212, 'Checkout summary step', {checks: 'failing', pending_comments: 2, review_state: 'changes_requested'}),
      pr('p2', 214, 'Price rounding', {checks: 'pending', mergeable: 'conflict'}),
      pr('p3', 198, 'Cart badge', {state: 'merged'}),
      {id: 'd1', kind: 'doc', url: 'https://docs.example.com/checkout', title: 'Checkout spec', state: 'open'},
      // Session rows as backend/hubdb.py writes them: a linked one (url session:<value>), then the owning bot's primary.
      {id: 's2', kind: 'session', url: 'session:codex resume 019a2b', title: 'codex 019a2b', detail: JSON.stringify({primary: false, harness: 'codex', thread_id: '019a2b'})},
      {id: 's0', kind: 'session', url: 'session:api/thread-old', title: 'api', detail: JSON.stringify({primary: false, harness: 'codex', thread_id: 'thread-old', bot: 'api'})},
      {id: 's1', kind: 'session', url: 'session:eng/thread-abc', title: 'eng', detail: JSON.stringify({primary: true, harness: 'claude', thread_id: 'thread-abc', bot: 'eng'})},
    ]});
  // A stage name for a task still open (status 'open'), else 'done' or 'closed'.
  const kid = (id: string, title: string, at: string, owner: string, pr_state: string | null = null, children: Kid[] = []): Kid =>
    ({id, title, ...(['done', 'closed'].includes(at) ? {status: at} : {status: 'open', stage: stage(at)}), owner_id: owner, pr_state, children});
  const tree = [kid('k1', 'Summary step on web', 'Review', 'bot:eng', 'failing', [kid('k1a', 'Copy for the summary', 'done', 'human:sam', 'merged')]),
    kid('k2', 'Rounding in the API', 'Doing', 'bot:api', 'conflict'),
    kid('k3', 'Cart badge', 'done', 'bot:eng', 'merged'),
    kid('k4', 'Check the receipts email', 'To do', 'human:sam')];
  const plain = task('t-plain', {title: 'Book the team offsite', owner_id: 'human:ana'});
  const others = [task('t-review', {title: 'Review the pricing copy', pr_state: 'changes_requested'}),
    task('t-open', {title: 'Upgrade the image library', pr_state: 'open'}),
    task('t-none', {title: 'Write the release notes', pr_state: null})];
  // As backend/run_view.py answers: newest run first; a lapsed try the hub resumed, then the live retry.
  const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();
  const runView = {task_id: 't-checkout', checkpoint: {text: 'Summary step built; receipts next.', at: ago(300), by: 'bot:eng'},
    runs: [
      {id: 'a2', bot: 'eng', job: 'j1', try: 2, tries: 2, state: 'running', outcome: null, created_at: ago(120), started_at: ago(118), finished_at: null,
       took_s: null, ran_on: {segments: [{model: 'claude-opus-5-5', effort: 'high'}],
         task_model: {source: 'fallback', note: "Ran on the bot's model claude-opus-5-5 (high): task model grok-4.6 (low) could not run (grok is not signed in on this bot's computer)"}},
       lease: [], reason: null, next: null,
       wake: {class: 'batched', reason: 'Review comments', preset: 'token_saver', preset_label: 'Token saver', held_until: ago(125)},
       subagents: [{id: 'g1', parent_id: null, type: 'general-purpose', model: null, status: 'completed', background: true, tool_uses: 3, duration_ms: 42000, running: false},
         {id: 'g2', parent_id: 'g1', type: 'Explore', model: 'claude-haiku-5-5', status: null, background: false, tool_uses: null, duration_ms: null, running: true}],
       background: [{id: 'b1', tool: 'Bash', summary: 'npm', status: null, started_at: ago(60), ended: null, running: true},
         {id: 'b2', tool: 'Monitor', summary: 'tail', status: 'completed', started_at: ago(90), ended: ago(80), running: false}]},
      {id: 'a1', bot: 'eng', job: 'j1', try: 1, tries: 2, state: 'expired', outcome: 'interrupted', created_at: ago(900), started_at: ago(898), finished_at: ago(600),
       took_s: 298, ran_on: {segments: [{model: 'claude-opus-5-5'}]}, lease: [{kind: 'grace', at: ago(610)}],
       reason: 'Lease expired before completion', next: {kind: 'resumed', by: 'keeper', at: ago(130), note: 'Resumed automatically: it recorded no tool call.'},
       // Resumed by Tico, then the job ran try 2: the decision is history, with no state of the later try.
       job_state: 'running', decision: {decision: 'resume', run_id: 'a1', current: false, state: null, by: 'keeper', by_kind: 'tico',
         at: '2026-10-26T07:05:00Z', note: 'Resumed automatically: it recorded no tool call.'}, subagents: [], background: []},
      // The Dev 563 shape: cut at its time limit, then dismissed by BotOps with a note (job_recovery).
      {id: 'a0', bot: 'eng', job: 'j0', try: 1, tries: 1, state: 'interrupted', outcome: 'interrupted', created_at: ago(5400), started_at: ago(5398), finished_at: ago(1800),
       took_s: 3598, ran_on: {segments: [{model: 'claude-opus-5-5'}]}, lease: [], reason: 'Timeout: max_run_minutes (60 minutes)',
       next: {kind: 'dismissed', by: 'bot:botops', at: '2026-10-26T09:50:45.103088Z', note: 'Finished before the 60-min limit. Nothing to redo.'},
       job_state: 'canceled', decision: {decision: 'dismiss', run_id: 'a0', current: true, state: 'canceled', by: 'bot:botops', by_kind: 'botops',
         at: '2026-10-26T09:50:45.103088Z', note: 'Finished before the 60-min limit. Nothing to redo.'}, subagents: [], background: []}]};
  return {tasks: [parent, plain, ...others], tree, runView};
}

async function open(browser: Pick<Browser, 'newPage'>, {viewport = {width: 1440, height: 900}, theme = 'dark', at = '#/task/t-checkout', mover = true, rich = false}: OpenOptions = {}) {
  const data: Fixture & {comments?: unknown[]; events?: unknown[]; files?: unknown[]} = fixtures();
  if (rich) {
    Object.assign(data.tasks[0]!, {body: 'Keep the old checkout behind a flag.\n\nVerify receipts in both flows.',
      note: 'The draft is checked; the rollout still needs a decision.', labels: ['checkout'], acceptance_criteria: ['Both flows send a receipt.']});
    data.tasks.push(task('t-outward', {title: 'Choose the rollout day', owner_id: 'human:ben', requester_id: 'bot:eng', status: 'open'}),
      task('t-finished', {title: 'Check the receipt wording', status: 'closed', requester_id: 'bot:eng', done_at: now, closed_at: now}),
      ...data.tree.map(({children, ...t}) => task(t.id, {...t, status: t.status === 'done' || t.status === 'closed' ? t.status : 'open', relations: {parent: [{id: 't-checkout', title: 'Ship the checkout redesign', status: 'doing', owner_id: 'bot:eng', direction: 'out'}]}})));
    data.comments = [{id: 'm1', kind: 'say', sender_id: 'bot:eng', body: 'The draft is checked.', created_at: now, refs: {task: 't-checkout', note: true}},
      {id: 'm2', kind: 'ask', sender_id: 'bot:eng', body: 'Which rollout day?', created_at: now, answers: [], refs: {task: 't-checkout',
        questions: [{id: 'day', question: 'Which rollout day?', options: [{label: 'Tuesday'}, {label: 'Thursday'}], multi: true, other: true}]}}];
    data.events = [{id: 'e1', created_at: now, actor: 'bot:eng', field: 'status', old: 'open', new: 'doing', note: 'The draft is checked.'}];
    data.files = [{id: 'f-notes', name: 'review.md', mime: 'text/markdown', current_version: 2, archived: false,
      versions: [2, 1].map(n => ({n, size: 48, mime: 'text/markdown', created_at: now, by: 'bot:eng',
        url: `/api/v2/files/f-notes?v=${n}`, answers: []}))}];
  }
  const page = await browser.newPage({viewport, hasTouch: viewport.width <= 390, serviceWorkers: 'block'});
  const errors: string[] = [], writes: Write[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const me = {id: 'ana', name: 'Ana', email: 'ana@example.com', role: 'owner', mover, cloud: true, registered: true};
  const bots = [['eng', 'Engineer'], ['api', 'API Engineer'], ['botops', 'BotOps']].map(([name, display_name]) =>
    ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true, owner_id: 'ana', can_manage: true,
      users: [{id: 'ana', name: 'Ana'}], my_access: {see: true, read: true, write: true}}));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json(me);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}});
    if (p === '/api/issues') return json([]);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/models') return json({models: [], harnesses});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tasks' && method === 'GET') {
      const owner = url.searchParams.get('owner');
      // List rows leave out the links' detail (and pr_sha), as backend/app.py lists do; the detail has them.
      const requester = url.searchParams.get('requester');
      const rows = (owner ? data.tasks.filter(t => t.owner_id === (owner.includes(':') ? owner : 'bot:' + owner)) : requester ? data.tasks.filter(t => t.requester_id === 'bot:' + requester) : data.tasks)
        .map(t => ({...t, links: (t.links || []).map(({detail, pr_sha, ...l}) => l)}));
      return json({tasks: rows, next_offset: null});
    }
    // The default sidebar (backend/bot_task_tabs.py): Assigned to others is a filter tab the server counts.
    if (/^\/api\/v2\/bots\/[^/]+\/task-tabs$/.test(p)) return json({tabs: [{builtin: 'needs', name: 'Needs you'}, {builtin: 'active', name: 'Doing'},
      {builtin: 'blocked', name: 'Blocked'}, {builtin: 'done', name: 'Done recently'}, {name: 'Assigned to others', conditions: []}], widgets: null, can_edit: false});
    if (/^\/api\/v2\/bots\/[^/]+\/task-tabs\/tasks$/.test(p)) {
      const others = data.tasks.filter(t => t.requester_id === 'bot:eng' && !['human:ana', 'bot:eng'].includes(t.owner_id!) && ['open', 'doing', 'waiting'].includes(t.status!));
      return json({tabs: [{key: 'c4', name: 'Assigned to others', count: others.length, ids: others.map(t => t.id)}], tasks: others});
    }
    if (p === '/api/v2/tasks' && method === 'POST') {
      const body = req.postDataJSON() as {title: string; owner: string; relations?: {task: string; kind: string}[]}; writes.push({method, p, body});
      const owner = body.owner.includes(':') ? body.owner : 'bot:' + body.owner;
      const up = (body.relations || []).find(r => r.kind === 'parent');
      const made = task('k5', {title: body.title, owner_id: owner, status: 'open', relations: up ? {parent: [{id: up.task, direction: 'out'}]} : {}});
      data.tree.push({id: 'k5', title: body.title, status: 'open', owner_id: owner, pr_state: null, children: []});
      data.tasks[0]!.children_summary = {...data.tasks[0]!.children_summary, total: 11, open: 7, direct_total: 5};
      return json({task: made});
    }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    // As backend/hubdb.py task_tree answers: the direct children, each with its own nested children.
    if (tree) return json(tree[1] === 't-checkout' ? data.tree : []);
    const links = p.match(/^\/api\/v2\/tasks\/([^/]+)\/links$/);
    if (links && method === 'POST') {
      const body = req.postDataJSON() as {kind?: string; url: string}; writes.push({method, p, body});
      const t = data.tasks.find(x => x.id === links[1])!;
      t.links.push({id: 's' + t.links.length, kind: body.kind || 'url', url: 'session:' + body.url, title: body.url, detail: '{"primary":false}'});
      return json({links: t.links});
    }
    const link = p.match(/^\/api\/v2\/tasks\/([^/]+)\/links\/([^/]+)$/);
    if (link && method === 'DELETE') {
      writes.push({method, p});
      const t = data.tasks.find(x => x.id === link[1])!; t.links = t.links.filter(l => l.id !== link[2]);
      return json({links: t.links});
    }
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && method === 'POST') {
      const t = data.tasks.find(t => t.id === one[1])!, body = req.postDataJSON() as Partial<CodeTask>;
      writes.push({method, p, body}); Object.assign(t, body, {version: t.version + 1});
      return json({task: t});
    }
    if (one && method === 'GET') {
      const t = data.tasks.find(x => x.id === decodeURIComponent(one[1]!));
      if (!t) return json({error: {detail: 'Not found'}}, 404);
      return json({task: t, children: t.id === 't-checkout' ? data.tree.map(({children, ...k}) => k) : [],
        comments: rich && t.id === 't-checkout' ? data.comments : [],
        events: rich && t.id === 't-checkout' ? data.events : []});
    }
    if (p === '/api/v2/tasks/t-checkout/run-view') return json(data.runView);
    if (rich && p === '/api/v2/tasks/t-checkout/files') return json({files: data.files});
    if (rich && p === '/api/v2/files/f-notes') return route.fulfill({contentType: 'text/markdown', body: '# Review\n\nReceipt check version ' + url.searchParams.get('v') + '.'});
    if (rich && p.endsWith('/comments') && method === 'POST') {
      const body = req.postDataJSON() as {text: string}; writes.push({method, p, body});
      data.comments!.push({id: 'm3', kind: 'say', sender_id: 'human:ana', body: body.text, created_at: now, refs: {task: 't-checkout'}});
      return json({woke: true});
    }
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/' + at);
  return {page, errors, writes, data};
}

async function desktop(browser: Browser) {
  const {page, errors, writes} = await open(browser);
  const modal = page.locator('#task-modal');
  const rail = modal.locator('[data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  // Code: worktrees first, then pull requests; other links stay under Links.
  const lines = await rail.locator('.code-line').allInnerTexts();
  assert.equal(lines.length, 7);
  assert.match(lines[0]!, /^web\s*·\s*tico\/t-check-checkout\s*·\s*↑2 ↓1\s*·\s*3 files/);
  assert.match(lines[1]!, /^payments-api\s*·\s*tico\/t-check-api\s*unknown/);
  // With neither a repo, a folder nor a branch that names one: "unknown repo", muted; never the word "worktree".
  const bare = await page.evaluate(() => {
    const host = document.createElement('ul'); host.className = 'code-list';
    host.innerHTML = taskCodeLineHTML({id: 'w9', kind: 'worktree', url: 'worktree:1', repo: null, branch: 'tico/t-check-misc', state: 'present'}, true, [{kind: 'pr', repo: 'acme/web'}]);
    document.querySelector('#task-modal [data-task-rail]')!.append(host);
    const repo = host.querySelector('.code-repo')!;
    const out = {text: host.innerText, cls: repo.className, color: getComputedStyle(repo).color, muted: getComputedStyle(document.querySelector('#task-modal .tmodal-age')!).color};
    host.remove();
    return out;
  });
  assert.match(bare.text, /^unknown repo\s*·\s*tico\/t-check-misc/);
  const retired = await page.evaluate(() => {
    const host = document.createElement('ul');
    host.innerHTML = taskCodeLineHTML({id: 'retired', kind: 'worktree', repo: 'acme/web', branch: 'tico/retired',
      state: 'retired', detail: {checkout_state: 'retired', last_commit: 'abcdef0123456789', ahead: 2,
        cleanup_gave_up: true, error: 'Left on a retired computer: 2 unpushed commits'}}, true);
    const recovered = document.createElement('ul');
    recovered.innerHTML = taskCodeLineHTML({kind: 'worktree', repo: 'acme/web', state: 'present',
      detail: {retired_worktree: {ahead: 2, last_commit: 'abcdef0123456789'}}}, true);
    return {text: host.textContent, retry: !!host.querySelector('[data-code-retry]'), recovered: recovered.textContent};
  });
  assert.match(retired.text, /retired computer/);
  assert.match(retired.text, /abcdef012345/);
  assert.match(retired.text, /↑2/);
  assert.equal(retired.retry, false, 'a retired computer cannot retry cleanup');
  assert.match(retired.recovered, /2 unpushed commits stayed on the retired computer/);
  // A check waiting for a free reviewer (backend/check_pools.py) shows under Code, even on a task with no links yet.
  const waiting = await page.evaluate(() => {
    const host = document.createElement('div');
    host.innerHTML = taskCodeHTML({links: [], check_waits: [{id: 'cw1', title: 'Review 1: PR #212', label: 'Waiting for a reviewer',
      reason: 'every reviewer in the pool is at its limit of open checks', since: new Date().toISOString()}]});
    return {text: host.textContent, title: host.querySelector<HTMLElement>('[data-check-wait] .code-main')?.title};
  });
  assert.match(waiting.text, /Review 1: PR #212\s*·\s*Waiting for a reviewer/);
  assert.match(waiting.title!, /at its limit/);
  assert.equal(bare.cls, 'code-repo unknown');
  assert.equal(bare.color, bare.muted, 'unknown repo is muted');
  // A branch name is never taken for a repo (fix-api in the web repo is not "api"); only the folder says.
  assert.equal(await page.evaluate(() => taskWorktreeRepo({kind: 'worktree', branch: 'tico/fix-web'}, [{kind: 'pr', repo: 'acme/web'}])), '');
  assert.equal(await page.evaluate(() => taskWorktreeRepo({kind: 'worktree', path: 'tasks/t1/eng__web'}, [])), 'web');
  assert.match(lines[2]!, /^docs\s*·\s*tico\/t-check-docs\s*removed/);
  assert.match(lines[3]!, /^web\s*·\s*tico\/t-check-old\s*error/);
  assert.match(lines[4]!, /#212\s*Checkout summary step\s*✕\s*changes requested\s*2 comments/);
  assert.match(lines[5]!, /#214\s*Price rounding\s*checks running\s*conflict/, 'a chip says what it means');
  assert.match(lines[6]!, /#198\s*Cart badge\s*merged/);
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
  // Only a worktree whose cleanup stopped retrying offers Retry cleanup: visible text, no hover needed.
  assert.equal(await rail.locator('[data-code-retry]').count(), 1);
  const retry = rail.locator('.code-line[data-code-link="w4"] [data-code-retry]');
  assert.equal(await retry.isVisible(), true);
  assert.equal(await retry.getAttribute('aria-label'), 'Retry cleanup of web · tico/t-check-old');
  // Not offered on a removed worktree, nor on a released one of an open task (its cleanup resumes once work is saved),
  // nor when the flag is only truthy.
  const offered = await page.evaluate(() => [
    {state: 'removed', detail: JSON.stringify({cleanup_gave_up: true})},
    {state: 'present', detail: JSON.stringify({cleanup_gave_up: true, release_requested: true})},
    {state: 'present', detail: JSON.stringify({cleanup_gave_up: 'true'})},
    {state: 'present', detail: JSON.stringify({cleanup_gave_up: true})},
  ].map(l => taskCodeLineHTML({id: 'w9', kind: 'worktree', url: 'worktree:9', repo: 'acme/web', branch: 'tico/x', ...l}, true).includes('data-code-retry')));
  assert.deepEqual(offered, [false, false, false, true]);
  assert.equal(await rail.locator('.code-line[data-code-link="p1"] .code-chip[aria-label="Checks failing"]').count(), 1, 'a glyph chip has words');
  assert.equal(await rail.locator('.code-line.pr a').first().getAttribute('href'), 'https://github.com/acme/web/pull/212');
  assert.equal(await rail.locator('.code-line.pr a').first().getAttribute('target'), '_blank');
  assert.equal(await modal.locator('.tlinks a', {hasText: 'Checkout spec'}).count(), 1, 'a doc link stays in Links & files');
  assert.equal(await modal.locator('.tlinks a', {hasText: 'Checkout summary step'}).count(), 0, 'a PR is not listed twice');
  // Sessions: the owning bot's primary first (its resume command to copy, no ✕), then linked ones; none under Links.
  const sessions = modal.locator('.task-sessions .tlink');
  assert.equal(await sessions.count(), 3);
  assert.match(await sessions.first().innerText(), /Engineer · claude/);
  assert.match((await sessions.first().locator('[data-copy-session]').getAttribute('data-copy-session'))!, /&& claude --resume thread-abc$/);
  assert.equal(await sessions.first().locator('[data-drop-link]').count(), 0);
  assert.equal(await sessions.nth(1).locator('[data-copy-session]').getAttribute('data-copy-session'), 'codex resume 019a2b');
  // An earlier owner's session keeps its full resume command.
  assert.match((await sessions.nth(2).locator('[data-copy-session]').getAttribute('data-copy-session'))!, /emp-api && codex resume thread-old$/);
  assert.equal(await modal.locator('.task-links .tlink').count(), 1, 'Links keeps only the doc');
  await modal.locator('[data-session-add]').click();
  const sessionInput = modal.locator('[data-modal-session] input');
  await sessionInput.fill('claude --resume 4f1e'); await sessionInput.press('Enter');
  await sessions.nth(3).waitFor();
  assert.deepEqual(writes.at(-1), {method: 'POST', p: '/api/v2/tasks/t-checkout/links', body: {url: 'claude --resume 4f1e', kind: 'session'}});
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await modal.locator('.task-sessions').screenshot({path: path.join(SHOTS, 'task-sessions-dark.png')}); }
  // The "…" menu copies the primary session's id alone, for restoring it by hand.
  await page.evaluate(() => { window.copied = []; navigator.clipboard.writeText = async text => { window.copied.push(text); }; });
  await modal.locator('[data-task-more]').click();
  await modal.locator('.prop-pop .tl-mi', {hasText: 'Copy session ID'}).click();
  assert.deepEqual(await page.evaluate(() => window.copied), ['thread-abc']);
  // Runs: the checkpoint, what still runs in the background, then each try, newest open.
  const runs = modal.locator('[data-task-runs]');
  await runs.locator('.truns-try').first().waitFor();
  assert.match(await runs.locator('.truns-cp').innerText(), /Summary step built; receipts next\.\s*Engineer · 5m ago/);
  assert.deepEqual(await runs.locator('.truns-live li').allInnerTexts(), ['Bash · npm running · started 1m ago']);
  const tries = runs.locator('.truns-try');
  assert.equal(await tries.count(), 3);
  assert.match(await tries.first().locator('summary').innerText(), /^Try 2 of 2 · running · for 2m · claude-opus-5-5 \(high\)/);
  // What ran and why: the fallback from the bot's task model (backend/task_models.py).
  assert.match(await tries.first().innerText(), /Ran on the bot's model claude-opus-5-5 \(high\): task model grok-4\.6 \(low\) could not run \(grok is not signed in on this bot's computer\)/);
  assert.doesNotMatch(await tries.nth(1).innerText(), /task model/, 'a run with no task model says nothing about one');
  assert.equal(await tries.first().evaluate(x => (x as HTMLDetailsElement).open), true);
  assert.match(await tries.first().locator('.truns-wake').innerText(), /^Woke for: Review comments · Token saver · held until /);
  assert.equal(await tries.nth(1).locator('.truns-wake').count(), 0, 'a run from before presets says nothing about its wake');
  assert.equal(await tries.nth(1).evaluate(x => (x as HTMLDetailsElement).open), false);
  assert.match(await tries.first().locator('.truns-sub > .truns-tree > li').innerText(),
    /^general-purpose\s*claude-opus-5-5 \(the run’s model\) · completed · 42s · 3 tool calls · in the background\s*Explore\s*claude-haiku-5-5 · running$/);
  assert.equal(await tries.first().locator('.truns-tree .truns-tree li').count(), 1, 'a nested subagent sits under its parent');
  await tries.nth(1).locator('summary').click();
  // A decision about an earlier try is history once the job ran again: no "Running:" from the later try beside it.
  assert.match(await tries.nth(1).locator('summary').innerText(), /^Try 1 of 2 · lease expired · Resumed by Tico's auto-reconcile at 07:05Z · 4m 58s/);
  assert.match(await tries.nth(1).innerText(), /Lease expired before completion[\s\S]*Resumed by Tico's auto-reconcile at 07:05Z, Resumed automatically: it recorded no tool call\./);
  // A try with no recovery decision carries no decision label; a dismissed one says it beside its state.
  assert.equal(await runs.locator('.truns-decision').count(), 2);
  assert.doesNotMatch(await tries.first().locator('summary').innerText(), /dismissed|resumed/i);
  assert.match(await tries.nth(2).locator('summary').innerText(), /^Try 1 of 1 · stopped · Canceled: dismissed by BotOps at 09:50Z · /);
  await tries.nth(2).locator('summary').click();
  assert.equal(await tries.nth(2).locator('.truns-decision').innerText(),
    'Canceled: dismissed by BotOps at 09:50Z, Finished before the 60-min limit. Nothing to redo.');
  assert.doesNotMatch(await tries.nth(2).innerText(), /Not run again/, 'the decision line replaces the bare next line');
  if (SHOTS) await runs.screenshot({path: path.join(SHOTS, 'task-runs-decision-dark.png')});
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await runs.screenshot({path: path.join(SHOTS, 'task-runs-dark.png')}); }
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
  assert.equal(await first.locator('> .sub-line > .task-status').getAttribute('data-status'), 'open');
  assert.equal(await first.locator('> .sub-line > .task-status').getAttribute('aria-label'), 'Review', 'an open task reads its stage');
  assert.equal(await rail.locator('.sub-row[data-sub="k3"] > .sub-line > .task-status').getAttribute('data-status'), 'done');
  assert.equal(await rail.locator('.sub-row[data-sub="k4"] .pr-badge').count(), 0, 'no PRs, no badge');
  assert.equal(await rail.locator('[data-sub="k1a"]').count(), 0, 'one level shown');
  await first.locator('[data-sub-toggle]').click();
  await rail.locator('[data-sub="k1a"]').waitFor();
  assert.equal(await first.locator('[data-sub-toggle]').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.subToggle), 'k1', 'focus stays on the toggle');
  // Rail ordering and desktop placement are covered by the task manager full-task layout check.
  if (SHOTS) { fs.mkdirSync(SHOTS, {recursive: true}); await modal.screenshot({path: path.join(SHOTS, 'task-code-desktop-dark.png')}); }

  // Add subtask: a task with a parent relation, for whoever is picked (the parent's owner to start with).
  const form = rail.locator('[data-sub-add]');
  // For whom: an avatar picker, not a native select.
  assert.equal(await form.locator('select').count(), 0);
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'eng');
  await form.locator('[data-sub-owner]').click();
  await modal.locator('.prop-pop [data-prop-pick="api"]').click();
  await page.waitForFunction(() => document.querySelector<HTMLInputElement>('#task-modal [data-sub-add] input[name=owner]')?.value === 'api');
  assert.match((await form.locator('[data-sub-owner]').getAttribute('aria-label'))!, /^For API Engineer/);
  const add = form.locator('input[name=title]');
  await add.fill('Check the receipts in the app'); await add.press('Enter');
  await rail.locator('.sub-row[data-sub="k5"]').waitFor();
  assert.deepEqual(writes.at(-1), {method: 'POST', p: '/api/v2/tasks', body: {title: 'Check the receipts in the app', body: 'Check the receipts in the app', owner: 'bot:api', relations: [{task: 't-checkout', kind: 'parent'}]}});
  assert.equal(await rail.locator('#task-subs-h .cnt').innerText(), '5');
  assert.equal(await form.locator('input[name=owner]').inputValue(), 'api', 'the pick stays for the next one');
  assert.equal(await page.evaluate(() => (document.activeElement as HTMLInputElement | null)?.name), 'title', 'ready for the next subtask');
  // Remove a link with its ✕ (shown on hover).
  const line = rail.locator('.code-line[data-code-link="p2"]');
  await line.hover();
  await line.locator('[data-code-drop]').click();
  await rail.locator('.code-line[data-code-link="p2"]').waitFor({state: 'detached'});
  assert.deepEqual(writes.at(-1), {method: 'DELETE', p: '/api/v2/tasks/t-checkout/links/p2'});

  // Retry cleanup sends the same removal request (the server starts a fresh bounded cleanup) and says so.
  await rail.locator('.code-line[data-code-link="w4"] [data-code-retry]').click();
  await page.locator('.toast', {hasText: 'Cleanup asked again'}).waitFor();
  assert.deepEqual(writes.at(-1), {method: 'DELETE', p: '/api/v2/tasks/t-checkout/links/w4'});

  // A task with nothing in the rail has no rail and no empty headings.
  await page.evaluate(() => { location.hash = '#/task/t-plain'; });
  await modal.locator('.tmodal-title', {hasText: 'Book the team offsite'}).waitFor();
  // the full detail is drawn (its rail painted from it)
  await page.waitForFunction(() => document.querySelector<HTMLElement & {taskRail?: {full?: {id: string}}}>('#task-modal')?.taskRail?.full?.id === 't-plain');
  assert.equal(await modal.locator('[data-task-rail]').isHidden(), true);
  assert.equal(await modal.locator('[data-task-runs]').isHidden(), true, 'no runs, no Runs section');
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

async function phone(browser: Browser) {
  const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}});
  const modal = page.locator('#task-modal'), rail = modal.locator('[data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  const main = (await modal.locator('.tmodal-main').boundingBox())!, side = (await rail.boundingBox())!, chat = (await modal.locator('.task-chat').boundingBox())!;
  assert.ok(side.y >= main.y + main.height - 1 && chat.y >= side.y + side.height - 1, 'details, then the rail, then comments');
  assert.ok(side.x >= 0 && side.x + side.width <= 390, 'inside the screen');
  const overflow = await modal.evaluate(d => d.querySelector('.tmodal-body')!.scrollWidth - d.querySelector('.tmodal-body')!.clientWidth);
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

async function member(browser: Browser) {
  // Someone who may not move tasks sees the links but no ✕ and no Add subtask.
  const {page, errors} = await open(browser, {mover: false});
  const rail = page.locator('#task-modal [data-task-rail]');
  await rail.locator('.task-subs .sub-row').first().waitFor();
  assert.equal(await rail.locator('[data-code-drop]').count(), 0);
  assert.equal(await rail.locator('[data-code-retry]').count(), 0);
  assert.equal(await rail.locator('[data-sub-add]').count(), 0);
  assert.deepEqual(errors, []);
  console.log('task rail member: ok');
  await page.close();
}

// Only the latest open can show; a late detail response cannot bring a task back on another page.
// The earlier detail is held until the test lets it go; lateDone waits for its answer to finish and the app's next
// frames, so it has been taken in. The app reads it through api.ts `get` (a const), so the network is counted.
async function lateOpen(browser: Browser) {
  const {page, errors} = await open(browser);
  const modal = page.locator('#task-modal');
  await modal.locator('.tmodal-title', {hasText: 'Ship the new checkout'}).waitFor();
  // The dialog's close event is queued after close(): the opens below start once it has run.
  const closed = page.evaluate(() => new Promise(done => document.querySelector('#task-modal')!.addEventListener('close', done, {once: true})));
  await modal.locator('[data-modal-close]').click(); await closed;
  await page.waitForFunction(() => !history.state?.taskModal && !document.querySelector('#task-modal[open]'));
  const held: ((value?: unknown) => void)[] = [];
  const releaseHeld = async () => {
    for (const end = Date.now() + t(10000); !held.length && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert.ok(held.length, 'the detail request is held');
    held.splice(0).forEach(release => release());
  };
  await page.route('**/api/v2/tasks/t-checkout', route => new Promise(release => held.push(release)).then(() => route.fallback()));
  let finished = 0;
  page.on('requestfinished', request => { if (new URL(request.url()).pathname === '/api/v2/tasks/t-checkout') finished++; });
  const lateDone = async (n: number) => {
    for (const end = Date.now() + t(10000); finished < n && Date.now() < end;) await new Promise(r => setTimeout(r, 50));
    assert.equal(finished, n, 'the held detail answered');
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  };
  await page.evaluate(() => { void taskModalOpen('t-checkout'); void taskModalOpen('t-none'); });
  await modal.locator('.tmodal-title', {hasText: 'Write the release notes'}).waitFor();
  await releaseHeld(); await lateDone(1);
  assert.equal(await modal.locator('.tmodal-title').innerText(), 'Write the release notes', 'a slower earlier tap cannot replace the latest task');
  await modal.locator('[data-modal-close]').click();
  await page.waitForFunction(() => !history.state?.taskModal);
  await page.evaluate(() => { void taskModalOpen('t-checkout'); location.hash = '#/updates'; });
  await page.waitForFunction(() => S.route === '#/updates');
  await releaseHeld(); await lateDone(2);
  assert.equal(await modal.isVisible(), false, 'navigating away cancels a pending open');
  assert.equal(await page.evaluate(() => !!history.state?.taskModal || document.body.classList.contains('task-modal-open')), false, 'no stale history or scroll lock');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('late task open: ok');
}

async function retirementHistory(browser: Browser) {
  for (const width of [1440, 390]) {
    const {page, errors} = await open(browser, {viewport: {width, height: 900}});
    const rail = page.locator('#task-modal [data-task-rail]');
    await rail.locator('.code-line').first().waitFor();
    await rail.evaluate(el => {
      const first = {computer_id: 'first', ahead: 2, dirty_files: 1, last_commit: 'a'.repeat(40)};
      const second = {computer_id: 'second', ahead: 4, dirty_files: 3, last_commit: 'b'.repeat(40)};
      const host = document.createElement('ul'); host.className = 'code-list';
      host.innerHTML = taskCodeLineHTML({id: 'recovered-twice', kind: 'worktree', repo: 'acme/web', state: 'present',
        detail: {retired_worktree: second, retired_worktrees: [first, second]}}, true);
      el.append(host);
    });
    const warning = rail.locator('[data-code-link="recovered-twice"] .code-error');
    await warning.scrollIntoViewIfNeeded();
    assert.equal(await warning.isVisible(), true);
    const text = await warning.innerText();
    assert.match(text, /2 unpushed commits/); assert.match(text, /4 unpushed commits/);
    assert.match(text, /1 changed files/); assert.match(text, /3 changed files/);
    assert.ok(text.includes('a'.repeat(40)) && text.includes('b'.repeat(40)));
    assert.equal(text.split('4 unpushed commits').length - 1, 1, 'the latest report appears once');
    assert.ok(await warning.evaluate(el => el.scrollWidth - el.clientWidth <= 1), 'retired heads wrap inside the rail');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('worktree retirement history at desktop and phone widths: ok');
}

const api = {open};
export type Api = typeof api;
export type Fixture = ReturnType<typeof fixtures>;
export type Opened = Awaited<ReturnType<typeof open>>;
module.exports = api;
if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try { await desktop(browser); await phone(browser); await member(browser); await lateOpen(browser); await retirementHistory(browser); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
