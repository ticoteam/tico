/* ui/app/automations-flow.js — An automation's flow: the trigger card, step cards, Paths and the "+" between steps
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Steps run top to bottom; a Paths step forks into columns that never rejoin, so nothing is added after one. A computer
   shows the paths side by side; a phone swipes through them, with a dot per path (CONTRACT §2: 10 per fork, 3 levels). */
'use strict';

const AU_STEP_WORDS = {filter: 'Filter', decide: 'Decide', llm: 'LLM', find: 'Find', bot_turn: 'Bot turn', paths: 'Paths', action: 'Action'};
const AU_STEP_ICONS = {filter: 'filter_alt', decide: 'account_tree', llm: 'auto_awesome', find: 'manage_search', bot_turn: 'smart_toy',
  paths: 'alt_route', action: 'arrow_forward'};
const AU_ACTIONS = {archive: 'Archive', leave_for_you: 'Leave for you', draft_reply: 'Draft reply', send_reply: 'Send reply',
  create_task: 'Create task', label: 'Label', forward: 'Forward', wake_bot: 'Wake a bot', note: 'Note', post_update: 'Post update',
  call_webhook: 'Call a webhook', assign: 'Assign', unsubscribe: 'Unsubscribe'};
const AU_FIND = {mail: 'Mail', tasks: 'Tasks', prs: 'Pull requests', listening: 'Listening', docs: 'Docs'};
const AU_OPS = {is: '=', is_not: '≠', '>=': '≥', '<=': '≤', contains: 'has', matches: 'matches'};
const AU_MAX_PATHS = 10, AU_MAX_DEPTH = 3;
// A Filter rule's action: the hub keeps {do, args} (CONTRACT §2 ACTIONS); older copies said just the name.
const auThenDo = x => typeof x === 'string' ? x : x?.do || '';
const auThenWords = then => (then || []).map(x => AU_ACTIONS[auThenDo(x)] || auThenDo(x));

const auPct = p => Math.round(Number(p) * 100) + '%';
// "d1.bucket" reads "bucket": the step's output name is plumbing; a trigger's "message.from" reads "from".
const auField = f => String(f || '').includes('.') ? String(f).split('.').slice(1).join('.') : String(f || '');
// A condition as a chip: "bucket = reply ≥ 70%", "money ≥ 80%" (a yes/no answer), "subject has “invoice”".
function auCondText(c) {
  if (!c) return '';
  const f = auField(c.field), v = c.value, min = c.min != null && c.min !== '' ? ' ≥ ' + auPct(c.min) : '';
  if (c.op === 'is' && (v === true || v === 'yes') && min) return f + min;
  if (c.op === 'contains') return `${f} has “${v ?? ''}”`;
  if (c.op === 'matches') return `${f} matches /${v ?? ''}/`;
  // An event a condition names reads as its words ("Task finished"), not its id (task.done).
  if (['event', 'event.name'].includes(c.field) && typeof v === 'string' && v && [undefined, '', 'is', 'is_not'].includes(c.op))
    return (c.op === 'is_not' ? '≠ ' : '') + auEventWords(v);
  return `${f} ${AU_OPS[c.op] || c.op || '='} ${v ?? ''}${min}`.trim();
}
const auCondsText = (when, match) => (when || []).map(auCondText).join(match === 'any' ? ' or ' : ' and ');
const auSetWords = set => String(set || '').replace(/@(\d+)$/, ' v$1');
// A Decide step's question ids: inline, or the set's (GET sends a set's questions alongside, read-only).
function auQuestionIds(s) {
  const qs = s.questions || (AU?.sets || []).find(x => x.id === s.set)?.questions || {};
  return Object.keys(qs);
}
// An action's text under each name the hub reads, first to last (backend/automation_actions.py and
// connectors/mail/actions.py): the first one set runs. An edit writes `key` and drops the others, so what the editor
// shows is what runs (a task's label reads tag, then name; name works for mail and tasks alike).
const AU_ARG_NAMES = {label: {keys: ['label', 'tag', 'name'], key: 'name'}, draft_reply: {keys: ['body', 'text'], key: 'body'},
  send_reply: {keys: ['body', 'text'], key: 'body'}, wake_bot: {keys: ['text', 'prompt'], key: 'text'},
  note: {keys: ['text', 'note'], key: 'text'}, post_update: {keys: ['text', 'body'], key: 'text'},
  call_webhook: {keys: ['webhook'], key: 'webhook', drop: ['endpoint']},
  // A forward with a webhook posts there and mails no one (backend/automation_actions.py): an address replaces it.
  forward: {keys: ['webhook', 'to'], key: 'to'}};
function auArg(kind, a) {
  for (const k of AU_ARG_NAMES[kind]?.keys || []) if (a?.[k]) return a[k];
  return '';
}
const auActionArgs = s => {
  const a = s.args || {};
  const who = a.owner || a.to || a.bot;
  return who ? 'to ' + auActorName(String(who).includes(':') ? who : 'bot:' + who)
    : (s.do === 'label' || s.do === 'call_webhook' ? auArg(s.do, a) : '') || a.name || '';
};

// Finding steps: a step by id anywhere in the tree, and a container ("root", "<paths id>:<i>", "<paths id>:other").
function auFind(steps, id, depth = 0) {
  for (let i = 0; i < (steps || []).length; i++) {
    const s = steps[i];
    if (s.id === id) return {arr: steps, i, step: s, depth};
    if (s.type === 'paths') for (const p of [...(s.paths || []), s.otherwise || {steps: []}]) {
      const hit = auFind(p.steps || [], id, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}
function auContainer(steps, key) {
  if (key === 'root') return {arr: steps, depth: 0};
  const [sid, which] = key.split(':');
  const hit = auFind(steps, sid);
  if (!hit || hit.step.type !== 'paths') return null;
  const p = which === 'other' ? (hit.step.otherwise ||= {steps: []}) : hit.step.paths?.[+which];
  if (!p) return null;
  p.steps ||= [];
  return {arr: p.steps, depth: hit.depth + 1};
}

function auTriggerCard(a) {
  const t = a.trigger, system = a.kind === 'system', src = system ? a.schedule || a.controls?.schedule || '' : auSource(t);
  return `<div class="au-card au-trig${AU.ed?.kind === 'trigger' ? ' sel' : ''}" role="button" tabindex="0" data-open="trigger">
    <div class="au-cr"><span class="nav-icon" aria-hidden="true">${system ? 'settings' : AU_TRIGGER_ICONS[t.type] || 'schedule'}</span><span class="au-ct">${esc(system ? 'System job' : AU_TRIGGER_WORDS[t.type] || auCap(t.type))}</span></div>
    ${src || (t.filters || []).length ? `<div class="au-chips">${src ? `<span class="au-c1"${!system && t.type === 'event' && t.name ? ` title="${esc(t.name)}"` : ''}>${esc(src)}</span>` : ''}${(t.filters || []).map(c => `<span class="au-c1">${esc(auCondText(c))}</span>`).join('')}</div>` : ''}
  </div>`;
}
const auTag = type => `<span class="au-tag${['llm', 'bot_turn', 'paths'].includes(type) ? ' acc' : ''}">${esc(AU_STEP_WORDS[type] || type)}</span>`;
// A step's title: its card's first line, and its editor's heading.
function auStepTitle(s) {
  if (s.type === 'filter') { const n = (s.rules || []).length; return n + (n === 1 ? ' rule' : ' rules'); }
  if (s.type === 'decide') return s.set ? auSetWords(s.set) : 'Custom questions';
  if (s.type === 'llm' || s.type === 'bot_turn') return auActorName(s.type === 'llm' ? s.as : s.bot) || '—';
  if (s.type === 'find') return AU_FIND[s.source] || s.source || 'Find';
  if (s.type === 'action') return AU_ACTIONS[s.do] || s.do || 'Action';
  return AU_STEP_WORDS[s.type] || s.type;
}
function auStepCard(s) {
  const title = auStepTitle(s);
  let body = '';
  if (s.type === 'filter') {
    const rules = s.rules || [];
    body = rules.map(r => `<div class="au-rule"><span>${esc(auCondsText(r.when, r.match) || 'Always')}</span><span class="nav-icon" aria-hidden="true">arrow_forward</span>
      ${[...auThenWords(r.then), ...(r.to ? [auDestName(r.to)] : [])].map(x => `<span class="au-c1">${esc(x)}</span>`).join('')}</div>`).join('');
  } else if (s.type === 'decide') {
    const ids = auQuestionIds(s);
    body = `<div class="au-chips">${ids.map(q => `<span class="au-c1">${esc(q)}</span>`).join('') || '<span class="muted">No questions</span>'}</div>`;
  } else if (s.type === 'llm' || s.type === 'bot_turn') {
    body = `<div class="au-pr">${esc(s.prompt || 'No prompt yet')}</div>`;
  } else if (s.type === 'find') {
    const bits = [s.query, s.since === 'last_run' ? 'since last run' : '', s.limit ? 'up to ' + s.limit : ''].filter(Boolean);
    body = bits.length ? `<div class="au-pr">${esc(bits.join(' · '))}</div>` : '';
  } else if (s.type === 'action') {
    const who = auActionArgs(s);
    body = who ? `<div class="au-chips"><span class="muted">${esc(who)}</span></div>` : '';
  }
  return `<div class="au-card${s.enabled === false ? ' off' : ''}${AU.ed?.id === s.id ? ' sel' : ''}" role="button" tabindex="0" data-open="${esc(s.id)}">
    <div class="au-cr">${auTag(s.type)}<span class="au-ct">${esc(title)}</span>${auStepSwitch(s)}</div>${body}</div>`;
}
const auStepSwitch = s => AU.cur?.can_edit ? auSwitch(s.enabled !== false, (AU_STEP_WORDS[s.type] || 'Step') + ' on', `data-step="${esc(s.id)}"`) : '';
// Where a step goes in: a small "+" between two steps (a wider hit area than it looks), and an Add step button at the end
// of a list. A viewer without edit rights sees the line between steps only, and nothing after the last.
function auIns(key, i, end) {
  const at = `data-add="${esc(key)}|${i}" aria-haspopup="menu"`;
  if (!AU.cur?.can_edit) return end ? '' : '<div class="au-ln"></div>';
  return end ? `<div class="au-ln au-ln-end"><button class="au-add au-add-end" type="button" ${at}><span aria-hidden="true">+</span>Add step</button></div>`
    : `<div class="au-ln"><button class="au-add" type="button" ${at} aria-label="Add step">+</button></div>`;
}
function auStepsHTML(steps, key) {
  const list = steps || [];
  return auIns(key, 0, !list.length) + list.map((s, i) => (s.type === 'paths' ? auPathsHTML(s) : auStepCard(s) + auIns(key, i + 1, i + 1 === list.length))).join('');
}
function auPathsHTML(s) {
  const cols = [...(s.paths || []).map((p, i) => ({p, i, key: `${s.id}:${i}`})), {p: {name: 'Otherwise', steps: s.otherwise?.steps || []}, i: 'other', key: `${s.id}:other`, fb: true}];
  return `<div class="au-pb">
    <div class="au-card au-pcard${s.enabled === false ? ' off' : ''}${AU.ed?.id === s.id ? ' sel' : ''}" role="button" tabindex="0" data-open="${esc(s.id)}">
      <div class="au-cr">${auTag('paths')}<span class="au-ct muted">${(s.paths || []).length + 1} paths</span>${auStepSwitch(s)}</div></div>
    <div class="au-paths" data-paths="${esc(s.id)}">${cols.map(({p, i, key, fb}) => `<div class="au-pth${fb ? ' fb' : ''}" data-col="${i}">
      <button class="au-pname" type="button" data-open="${esc(s.id)}" data-path="${i}">${esc(p.name || 'Path')}</button>
      <div class="au-chips">${fb ? '<span class="muted">Nothing else</span>' : (p.when || []).length
        ? (p.when || []).map(c => `<span class="au-c1 au-cond">${esc(auCondText(c))}</span>`).join('') : '<span class="muted">No condition</span>'}</div>
      ${auStepsHTML(p.steps, key)}</div>`).join('')}</div>
    <div class="au-dots" role="tablist" aria-label="Paths">${cols.map(({p, i}, n) => `<button type="button" role="tab" data-dot="${i}" aria-label="${esc(p.name || 'Path')}" aria-selected="${n === 0}"></button>`).join('')}</div>
  </div>`;
}
// A built-in says what it does; one Tico runs from its own code also says it is fixed.
function auAboutHTML(a) {
  if (!a.builtin || !(a.description || a.readonly)) return '';
  return `<div class="au-about">${a.description ? `<p>${esc(a.description)}</p>` : ''}
    ${a.readonly ? '<p class="au-lock"><span class="nav-icon" aria-hidden="true">lock</span>Built-in: can’t be edited or deleted</p>' : ''}</div>`;
}
function auFlowHTML(a) {
  const b = auBoardShape(a);
  if (b) return auBoardHTML(a, b);
  return `<div class="au-track">${auAboutHTML(a)}${auTriggerCard(a)}${auStepsHTML(a.steps, 'root')}</div>`;
}

// The phone's dots follow the swipe; a tap on one scrolls to its path.
function auPathsWire(pane) {
  for (const strip of pane.querySelectorAll('.au-paths')) {
    const dots = strip.nextElementSibling;
    const sync = () => {
      const cols = [...strip.children], w = cols[0]?.offsetWidth || 1;
      const at = Math.min(cols.length - 1, Math.round(strip.scrollLeft / (w + 10)));
      [...dots.children].forEach((d, i) => d.setAttribute('aria-selected', String(i === at)));
    };
    strip.addEventListener('scroll', () => requestAnimationFrame(sync), {passive: true});
  }
}
function auPathsGo(dot) {
  const strip = dot.parentElement.previousElementSibling, i = [...dot.parentElement.children].indexOf(dot);
  const col = strip.children[i];
  if (col) strip.scrollTo({left: col.offsetLeft - strip.offsetLeft, behavior: 'smooth'});
}

// The "+" menu: every step type; Paths only at the end of a list that has none and under the nesting limit.
function auAddMenuHTML(at) {
  const [key, i] = at.split('|');
  const c = auContainer(AU.cur.steps, key);
  const paths = c && +i === c.arr.length && !c.arr.some(s => s.type === 'paths') && c.depth < AU_MAX_DEPTH;
  return Object.keys(AU_STEP_WORDS).filter(t => t !== 'paths' || paths).map(t =>
    `<button type="button" role="menuitem" data-ty="${t}"><span class="nav-icon" aria-hidden="true">${AU_STEP_ICONS[t]}</span>${AU_STEP_WORDS[t]}</button>`).join('');
}
const auNewId = () => 's_' + Math.random().toString(36).slice(2, 10);
// The next free output name for a step kind: d1, d2… for Decide, l1… for LLM.
function auNextOut(prefix) {
  const used = new Set(), walk = steps => (steps || []).forEach(s => { if (s.out) used.add(s.out); if (s.type === 'paths') [...(s.paths || []), s.otherwise || {}].forEach(p => walk(p.steps)); });
  walk(AU.cur.steps);
  let n = 1; while (used.has(prefix + n)) n++;
  return prefix + n;
}
function auNewStep(type) {
  const s = {id: auNewId(), type, enabled: true};
  if (type === 'filter') s.rules = [{when: [{field: 'message.from', op: 'contains', value: ''}], match: 'all', then: [{do: 'archive', args: {}}], stop: true}];
  if (type === 'decide') Object.assign(s, {questions: {question: {type: 'noul', instructions: ''}}, level: 'medium', out: auNextOut('d')});
  if (type === 'llm') Object.assign(s, {as: 'assistant:' + (S.me?.id || ''), prompt: '', out: auNextOut('l')});
  if (type === 'find') Object.assign(s, {source: 'mail', query: '', since: 'last_run', limit: 20});
  if (type === 'bot_turn') Object.assign(s, {bot: (S.emps || []).find(e => e.id !== AU_BOT)?.id || '', prompt: ''});
  if (type === 'paths') Object.assign(s, {paths: [{name: 'Path 1', when: [], steps: []}], otherwise: {steps: []}});
  if (type === 'action') Object.assign(s, {do: 'archive', args: {}});
  return s;
}
// A new step opens in its editor; it joins the flow (a new version) when saved.
function auInsert(type) {
  const [key, i] = AU.menu.at.split('|');
  auMenuClose();
  const step = auNewStep(type);
  auEdOpen({kind: 'step', id: step.id, step, insert: {key, index: +i}});
}

// A step's own switch saves at once: the flow changes, so it is a new version.
async function auStepToggle(id, on) {
  const steps = auClone(AU.cur.steps), hit = auFind(steps, id);
  if (!hit) return;
  hit.step.enabled = on;
  await auWrite(auFlowBody(steps), out => `${AU_STEP_WORDS[hit.step.type]} ${on ? 'on' : 'off'} · v${out.version}`);
}
// A steps write: the flow's destinations go back as they are, so a step saved from the list never drops them.
function auFlowBody(steps) {
  const dests = AU.cur?.destinations;
  return {steps: auCleanSteps(steps), ...(Array.isArray(dests) ? {destinations: auClone(dests)} : {})};
}
// What is written back: a Decide step on a saved set sends the set only (its questions came along read-only).
function auCleanSteps(steps) {
  for (const s of steps || []) {
    if (s.type === 'decide' && s.set) delete s.questions;
    if (s.type === 'paths') [...(s.paths || []), s.otherwise || {steps: []}].forEach(p => auCleanSteps(p.steps));
  }
  return steps;
}

/* The board: a flow shaped as rules → questions → LLM → destinations reads as one board (the inbox board). It is a view
   over ordinary steps: an optional filter of rules; a Decide step of yes/no questions and the filter that pairs question i
   with rule i; then the LLM, either Sort (a Decide step with one choice over the destination ids and the filter that
   routes each answer) or an llm step with `choose` (draft or reply); and the flow's destinations. auBoardShape reads
   those parts out of a flow, or null for any other flow, which keeps its step list; auBoardFlow writes them back. */
const AU_DEST_COLORS = ['amber', 'orange', 'violet', 'green', 'blue', 'teal', 'pink', 'gray', 'red'];
const AU_ACTION_ICONS = {create_task: 'task_alt', wake_bot: 'chat', leave_for_you: 'inbox', archive: 'archive', label: 'label',
  star: 'star', forward: 'forward_to_inbox', draft_reply: 'reply', send_reply: 'reply', unsubscribe: 'unsubscribe',
  call_webhook: 'webhook', post_update: 'dynamic_feed', note: 'sticky_note_2', assign: 'person', end: 'block', move: 'south'};
// The board's action words, mail actions first: they queue on the message, the rest run at once.
const AU_MAIL_ACTS = ['archive', 'label', 'star', 'leave_for_you', 'forward', 'draft_reply', 'send_reply', 'unsubscribe'];
const AU_BOARD_ACTS = {archive: 'Archive', label: 'Label', star: 'Star', leave_for_you: 'Leave in inbox', forward: 'Forward',
  draft_reply: 'Draft reply', send_reply: 'Reply', unsubscribe: 'Unsubscribe', create_task: 'Create a task', wake_bot: 'Message a bot',
  assign: 'Assign', note: 'Note', post_update: 'Post update', call_webhook: 'Call webhook'};
const AU_BOARD_OPS = {is: 'is', is_not: 'is not', contains: 'contains', matches: 'matches', '>=': '≥', '<=': '≤'};
// A new question's minimum and a new Sort's; a yes/no condition with no min holds at half (the engine's own default).
const AU_Q_MIN = 0.75, AU_SORT_MIN = 0.5, AU_NOUL_MIN = 0.5;
// Star is a label (STARRED); the board shows it as its own action.
function auActKey(x) {
  const d = auThenDo(x), a = (x && typeof x === 'object' && x.args) || {};
  return d === 'label' && /^starred$/i.test(String(auArg('label', a))) ? 'star' : d;
}
// Where a rule goes after its chain, as the engine reads it: its own next, else end when it has a destination or
// stops, else the next step.
const auRuleNext = r => r?.next || (r?.to || r?.stop === true ? 'end' : 'step');
// What Sort asks when no one has written instructions yet (a decide or an llm step needs some).
const auBoardAsk = mail => mail ? 'Pick where this message goes.' : 'Pick where this goes.';
const auDestColor = (d, i = 0) => `var(--au-${AU_DEST_COLORS.includes(d?.color) ? d.color : AU_DEST_COLORS[i % AU_DEST_COLORS.length]})`;
const auDestIcon = d => (d?.then || []).length ? AU_ACTION_ICONS[auActKey(d.then[0])] || 'bolt' : 'block';
function auDestName(id, dests = AU?.cur?.destinations) { return (dests || []).find(d => d.id === id)?.name || id; }
// Name and id, so a diff shows a move between two destinations that share a name.
function auDestLabel(id, dests) { const n = auDestName(id, dests); return n && n !== id ? `${n} (${id})` : String(id ?? ''); }

function auBoardShape(flow) {
  const dests = flow?.destinations;
  if (!Array.isArray(dests) || !dests.length || dests.some(d => !d || typeof d.id !== 'string' || !d.id)) return null;
  const ids = new Set(dests.map(d => d.id)), steps = Array.isArray(flow.steps) ? flow.steps : [];
  const copy = x => JSON.parse(JSON.stringify(x));
  const toOk = r => r.to == null || r.to === '' || ids.has(r.to);
  const inline = d => d?.type === 'decide' && !d.set && d.questions && typeof d.questions === 'object' && !Array.isArray(d.questions)
    && typeof d.out === 'string' && d.out;
  const one = (r, field, value) => {
    const c = (r.when || [])[0];
    return (r.when || []).length === 1 && c.field === field && (c.op || 'is') === 'is' && (c.value === value || (value === 'yes' && c.value === true));
  };
  // A switched-off step stays in the step list: the board has no off switch to show it, and a save would turn it on.
  if (steps.some(s => !s || s.enabled === false)) return null;
  // Sort with one destination: a last filter whose one rule, with no conditions or actions, goes there (no LLM call).
  const sortOne = s => s?.type === 'filter' && (s.rules || []).length === 1 && !(s.rules[0]?.when || []).length
    && !(s.rules[0]?.then || []).length && ids.has(s.rules[0]?.to);
  const b = {rules: [], questions: [], llm: null, dests: copy(dests), src: {}, mail: flow.trigger?.type === 'message'};
  let i = 0;
  if (steps[i]?.type === 'filter' && !(steps.length === 1 && sortOne(steps[0]))) {
    const rules = steps[i].rules || [];
    if (!rules.every(r => r && typeof r === 'object' && toOk(r) && ['end', 'rule'].includes(auRuleNext(r)) && !(r.to && auRuleNext(r) !== 'end'))) return null;
    b.src.rules = steps[i];
    b.rules = rules.map(r => ({...copy(r), next: auRuleNext(r)}));
    i++;
  }
  // Questions: every one yes/no, and each rule tests one of them (yes at its minimum) and ends. The board lists them in
  // rule order, not in the order of the questions' keys: the hub stores a Decide's questions with their keys sorted.
  const qd = steps[i], qf = steps[i + 1], qids = inline(qd) ? Object.keys(qd.questions) : [];
  if (qids.length && qf?.type === 'filter' && qids.every(q => qd.questions[q]?.type === 'noul')) {
    const rules = qf.rules || [], used = new Set();
    if (rules.length !== qids.length) return null;
    for (const r of rules) {
      const qid = qids.find(q => r && one(r, `${qd.out}.${q}`, 'yes')), q = qid && qd.questions[qid];
      if (!qid || used.has(qid) || !toOk(r) || auRuleNext(r) !== 'end' || typeof q.instructions !== 'string') return null;
      used.add(qid);
      const min = r.when[0].min;   // kept as stored: no min stays none
      b.questions.push({id: qid, text: q.instructions, ...(min == null || min === '' ? {} : {min}), then: copy(r.then || []), to: r.to || ''});
    }
    Object.assign(b.src, {qd, qf});
    i += 2;
  }
  // The LLM: Sort (one choice over destination ids, each answer routed, the last rule the unsure default) or a turn.
  const sd = steps[i], sf = steps[i + 1], sq = inline(sd) ? Object.keys(sd.questions) : [];
  if (sq.length === 1 && sd.questions[sq[0]]?.type === 'choice' && sf?.type === 'filter') {
    const q = sd.questions[sq[0]], crit = Array.isArray(q.criteria) ? q.criteria.map(String) : Object.keys(q.criteria || {});
    const rules = sf.rules || [], last = rules[rules.length - 1], field = `${sd.out}.${sq[0]}`, opts = [];
    if (typeof q.instructions !== 'string' || !crit.length || rules.length !== crit.length + 1 || !crit.every(o => ids.has(o))) return null;
    // The routes' order is the rules' order (the hub sorts a criteria map's keys): each of the first rules routes one
    // criterion to its own destination, and every criterion has one.
    for (let k = 0; k < crit.length; k++) {
      const r = rules[k], o = (r?.when || [])[0]?.value;
      if (!r || typeof o !== 'string' || !crit.includes(o) || opts.includes(o) || !one(r, field, o) || r.to !== o
        || (r.then || []).length || auRuleNext(r) !== 'end') return null;
      opts.push(o);
    }
    if ((last.when || []).length || !ids.has(last.to) || (last.then || []).length) return null;
    // The board shows one minimum for Sort, so routes with different ones keep the step list.
    const mins = rules.slice(0, -1).map(r => r.when[0].min ?? '');
    if (mins.some(m => m !== mins[0])) return null;
    b.llm = {mode: 'sort', prompt: q.instructions, to: opts, default: last.to, ...(mins[0] === '' ? {} : {min: mins[0]}), as: ''};
    Object.assign(b.src, {sd, sf, sq: sq[0]});
    i += 2;
  } else if (i === steps.length - 1 && sortOne(sd)) {
    b.llm = {mode: 'sort', prompt: '', to: [sd.rules[0].to], default: sd.rules[0].to, as: ''};
    b.src.sf = sd;
    i += 1;
  } else if (sd?.type === 'llm' && sd.choose && typeof sd.choose === 'object') {
    const c = sd.choose;
    // A draft or a reply runs mail actions: only a message flow has one to answer.
    if (flow.trigger?.type !== 'message' || !['draft', 'reply'].includes(c.mode) || !Array.isArray(c.to) || !c.to.length || !c.to.every(id => ids.has(id)) || !ids.has(c.default)) return null;
    b.llm = {mode: c.mode, prompt: String(sd.prompt || ''), to: [...c.to], default: c.default, as: sd.as || ''};
    b.src.ls = sd;
    i += 1;
  } else return null;
  return i === steps.length ? b : null;
}

// A board's parts back as a flow's steps and destinations: each step keeps its id and fields; a new one gets fresh ones.
function auBoardFlow(b) {
  const copy = x => JSON.parse(JSON.stringify(x)), used = new Set(), steps = [];
  const keep = (s, type) => s ? copy(s) : {id: auNewId(), type, enabled: true};
  const outName = (s, prefix) => {
    if (s.out && !used.has(s.out)) { used.add(s.out); return s.out; }
    let n = 1; while (used.has(prefix + n)) n++;
    used.add(prefix + n); return prefix + n;
  };
  for (const s of [b.src.qd, b.src.sd, b.src.ls]) if (s?.out) used.add(s.out);
  const rule = r => {
    const x = {...copy(r), match: r.match || 'all', when: copy(r.when || []), then: copy(r.then || [])};
    if (!x.to) delete x.to;
    x.next = x.to ? 'end' : x.next === 'rule' ? 'rule' : 'end';
    x.stop = x.next === 'end';
    return x;
  };
  if (b.rules.length) steps.push({...keep(b.src.rules, 'filter'), rules: b.rules.map(rule)});
  if (b.questions.length) {
    const d = keep(b.src.qd, 'decide'), f = keep(b.src.qf, 'filter'), old = b.src.qd?.questions || {};
    used.delete(d.out); delete d.set;
    // A new step starts at the board's level; a kept one keeps its own, none included (the engine runs none as low).
    d.out = outName(d, 'd'); if (!b.src.qd) d.level = 'medium';
    d.questions = Object.fromEntries(b.questions.map(q => [q.id, {...copy(old[q.id] || {}), type: 'noul', instructions: q.text}]));
    f.rules = b.questions.map(q => rule({when: [{field: `${d.out}.${q.id}`, op: 'is', value: 'yes', ...(q.min == null ? {} : {min: q.min})}],
      match: 'all', then: q.then, to: q.to, next: 'end'}));
    steps.push(d, f);
  }
  const L = b.llm;
  // One destination needs no choice: a single rule sends everything there (a one-option choice is refused).
  if (L.mode === 'sort' && L.to.length === 1) steps.push({...keep(b.src.sf, 'filter'), rules: [rule({when: [], match: 'all', then: [], to: L.to[0]})]});
  else if (L.mode === 'sort') {
    const d = keep(b.src.sd, 'decide'), f = keep(b.src.sf, 'filter'), qid = b.src.sq || 'to';
    const old = b.src.sd?.questions?.[qid] || {}, was = old.criteria && !Array.isArray(old.criteria) ? old.criteria : {};
    used.delete(d.out); delete d.set;
    d.out = outName(d, 'd'); if (!b.src.sd) d.level = 'high';
    d.questions = {[qid]: {...copy(old), type: 'choice', instructions: L.prompt || auBoardAsk(b.mail),
      criteria: Object.fromEntries(L.to.map(id => [id, was[id] ?? auDestName(id, b.dests)]))}};
    // Each route keeps the Sort's minimum as stored (none stays none); a new Sort gets the board's.
    const field = `${d.out}.${qid}`, min = b.src.sd ? L.min : L.min ?? AU_SORT_MIN;
    f.rules = [...L.to.map(id => rule({when: [{field, op: 'is', value: id, ...(min == null ? {} : {min})}], match: 'all', then: [], to: id})),
      rule({when: [], match: 'all', then: [], to: L.default})];
    steps.push(d, f);
  } else {
    const s = keep(b.src.ls, 'llm');
    used.delete(s.out);
    // Sort's unsure destination need not be one of its answers; a choose's default must be one of its own.
    const to = L.to.includes(L.default) ? [...L.to] : [...L.to, L.default];
    Object.assign(s, {as: L.as || s.as, prompt: L.prompt || auBoardAsk(b.mail), out: outName(s, 'l'), choose: {...(s.choose || {}), to, default: L.default, mode: L.mode}});
    steps.push(s);
  }
  return {steps, destinations: copy(b.dests)};
}

// The board's pieces. A condition reads as its field and check in words, the value as code; a payload's field is code too.
function auBoardCond(c, code) {
  const f = auField(c.field), op = AU_BOARD_OPS[c.op || 'is'] || c.op || 'is';
  const v = c.value === true ? 'yes' : c.value === false ? 'no' : c.value ?? '';
  return (code ? `<code>${esc(f)}</code><span class="f">${esc(op)}</span>` : `<span class="f">${esc(f)} ${esc(op)}</span>`) + `<code>${esc(v)}</code>`;
}
const auBoardConds = (r, code) => (r.when || []).length
  ? r.when.map(c => auBoardCond(c, code)).join(`<span class="f">${r.match === 'any' ? 'or' : 'and'}</span>`) : '<span class="f">Always</span>';
// An action as a chip's words: who or what it names, else the action.
function auActChip(x) {
  const k = auActKey(x), a = (x && typeof x === 'object' && x.args) || {};
  const who = k === 'create_task' || k === 'assign' ? a.owner : k === 'wake_bot' ? a.bot : '';
  if (who) return auActorName(String(who).includes(':') ? who : 'bot:' + who);
  if (k === 'label' && auArg('label', a)) return 'label ' + auArg('label', a);
  if (k === 'forward' && auArg('forward', a)) return String(auArg('forward', a));
  if (k === 'call_webhook' && a.webhook) return String(a.webhook);
  return AU_BOARD_ACTS[k] || AU_ACTIONS[k] || k;
}
// A destination's first action as a line: "Task, Legal Lead", "Leave in inbox"; nothing reads "Do nothing".
function auActLine(x) {
  if (!x) return 'Do nothing';
  const k = auActKey(x), a = (x && typeof x === 'object' && x.args) || {}, name = w => auActorName(String(w).includes(':') ? w : 'bot:' + w);
  if (k === 'create_task') return a.owner ? 'Task, ' + name(a.owner) : 'Task';
  if (k === 'assign') return a.owner ? 'Assign to ' + name(a.owner) : 'Assign';
  if (k === 'wake_bot') return a.bot ? 'Message ' + name(a.bot) : 'Message the bot';
  if (k === 'label') return auArg('label', a) ? 'Label ' + auArg('label', a) : 'Label';
  if (k === 'forward') return auArg('forward', a) ? 'Forward to ' + auArg('forward', a) : 'Forward';
  if (k === 'post_update') return 'Post to Updates';
  return AU_BOARD_ACTS[k] || AU_ACTIONS[k] || k;
}
const auChip = (icon, words, c, mv = false, last = false) => `<span class="au-bchip${mv ? ' mv' : ''}" style="--c:${c}"><span class="nav-icon" aria-hidden="true">${icon}</span>${mv ? esc(words) : `<b>${esc(words)}</b>`}${mv && last ? '<span class="nav-icon au-bdown" aria-hidden="true">south</span>' : ''}</span>`;
// A row's chain: its actions in order, then its destination; a move-on chain is dashed and points down; none says End.
function auChainHTML(then, to, next, dests) {
  const di = to ? dests.findIndex(d => d.id === to) : -1, d = dests[di], c = d ? auDestColor(d, di) : 'var(--au-gray)', mv = !d && next === 'rule';
  const chips = (then || []).map((x, j, all) => auChip(AU_ACTION_ICONS[auActKey(x)] || 'bolt', auActChip(x), c, mv, j === all.length - 1 && !d));
  if (d) chips.push(auChip(auDestIcon(d), d.name || d.id, c));
  if (!chips.length) chips.push(mv ? auChip('south', 'Move on', c, true) : auChip('block', 'End', c));
  return {c: mv ? 'var(--g4)' : c, mv, html: chips.join('<span class="nav-icon au-bsep" aria-hidden="true">arrow_forward</span>')};
}
function auBoardRow(key, cond, chain, cls = '') {
  const sel = AU.ed?.kind === 'board' && `${AU.ed.part}:${AU.ed.i}` === key;
  return `<div class="au-br${cls}${sel ? ' sel' : ''}" role="button" tabindex="0" data-board="${key}" style="--c:${chain.c}">
    <div class="au-bc">${cond}</div><span class="au-bw${chain.mv ? ' mv' : ''}" aria-hidden="true"></span><div class="au-bch">${chain.html}</div></div>`;
}
const auBoardAdd = (key, words) => AU.cur?.can_edit ? `<button class="linkish au-badd" type="button" data-board="${key}:new">+ ${esc(words)}</button>` : '';
// The LLM's modes: Sort, then Sort + draft and Sort + reply on a message flow (a draft or a reply answers the message).
// Off mail, and on a group's flow (no one to run a turn as), it sorts only.
function auBoardModes(a) {
  return [['sort', 'Sort'], ...(a.trigger?.type === 'message' && auOwnerAs(a) ? [['draft', 'Sort + draft'], ['reply', 'Sort + reply']] : [])];
}
// Who a draft or reply turn runs as: a person's Assistant, or the owning bot.
function auOwnerAs(a) {
  const o = String(a?.owner_id || a?.owner || '');
  if (/^(person|human):/.test(o)) return 'assistant:' + o.split(':').slice(1).join(':');
  if (o.startsWith('bot:')) return o;
  return '';
}
function auBoardHTML(a, b) {
  const t = a.trigger || {}, code = t.type !== 'message', edit = !!a.can_edit, dests = b.dests;
  const src = t.type === 'message' ? t.inbox || AU_TRIGGER_WORDS.message : [AU_TRIGGER_WORDS[t.type] || auCap(t.type), auSource(t)].filter(Boolean).join(' · ');
  const rules = b.rules.map((r, i) => auBoardRow(`rule:${i}`, auBoardConds(r, code), auChainHTML(r.then, r.to, r.next, dests))).join('');
  const qs = b.questions.map((q, i) => auBoardRow(`q:${i}`, `<span>${esc(q.text)}</span>`, auChainHTML(q.then, q.to, 'end', dests), ' q')).join('');
  const sec = (label, n, title, rows, add) => !rows && !edit ? '' : `<section class="au-bsec" aria-label="${esc(label)}">
      <div class="au-bh${rows ? '' : ' solo'}"><span class="au-tag">${n} · ${esc(label)}</span><b>${esc(title)}</b>${add}</div>${rows}</section>`;
  const L = b.llm, modes = auBoardModes(a);
  const seg = modes.length < 2 ? '' : `<span class="au-bseg" role="group" aria-label="Mode">${modes.map(([k, w]) =>
    `<button type="button" data-bmode="${k}" aria-pressed="${L.mode === k}"${edit ? '' : ' disabled'}>${esc(w)}</button>`).join('')}</span>`;
  const dsel = i => AU.ed?.kind === 'board' && AU.ed.part === 'dest' && AU.ed.i === i ? ' sel' : '';
  return `<div class="au-board">${auAboutHTML(a)}
    <div class="au-btrig${AU.ed?.kind === 'trigger' ? ' sel' : ''}" role="button" tabindex="0" data-open="trigger"><span class="nav-icon" aria-hidden="true">${AU_TRIGGER_ICONS[t.type] || 'schedule'}</span><b>${esc(src)}</b></div>
    <div class="au-bsp"></div>
    ${sec('Rules', 1, 'Match exactly', rules, auBoardAdd('rule', 'Rule'))}
    ${rules || edit ? '<div class="au-bsp"><span>no match: move on</span></div>' : ''}
    ${sec('Questions', 2, 'Ask a small model, in order', qs, auBoardAdd('q', 'Question'))}
    ${qs || edit ? '<div class="au-bsp"><span>all no: move on</span></div>' : ''}
    <section class="au-bllm${AU.ed?.kind === 'board' && AU.ed.part === 'llm' ? ' sel' : ''}" aria-label="LLM">
      <div class="au-bh"><span class="au-tag acc">3 · LLM</span><b>Read what’s left, pick one below</b>${seg}</div>
      <div class="au-bllm-b" role="button" tabindex="0" data-board="llm"><div class="au-bpr">${L.prompt ? esc(L.prompt) : '<span class="muted">No instructions</span>'}</div>
        ${L.to.length < 2 ? '' : `<span class="au-bun">unsure → ${esc(auDestName(L.default, dests))}</span>`}</div>
    </section>
    <svg class="au-branch" height="48" aria-hidden="true" fill="none" stroke-width="1.5"></svg>
    <div class="au-dests">${dests.map((d, i) => `<div class="au-dest${dsel(i)}" role="button" tabindex="0" data-board="dest:${i}"${L.to.includes(d.id) ? ` data-to="${esc(d.id)}"` : ''} style="--c:${auDestColor(d, i)}">
      <b>${esc(d.name || d.id)}</b><span><span class="nav-icon" aria-hidden="true">${auDestIcon(d)}</span> ${esc(auActLine((d.then || [])[0]))}${(d.then || []).length > 1 ? ` +${d.then.length - 1}` : ''}</span></div>`).join('')}
      ${edit ? '<button class="au-dest au-dest-add" type="button" data-board="dest:new">+ Destination</button>' : ''}</div>
  </div>`;
}
// The branch from the LLM into the destinations it may pick: drawn from where the boxes are, so it holds at any width
// (a wrapped row branches into its first line only), and again whenever the board's width changes.
function auBoardWire(pane) {
  AU.branchRO?.disconnect();
  const board = pane.querySelector('.au-board');
  if (!board) return;
  const draw = () => {
    const svg = board.querySelector('.au-branch'), llm = board.querySelector('.au-bllm');
    if (!svg || !llm) return;
    const box = svg.getBoundingClientRect(), w = Math.round(box.width), lr = llm.getBoundingClientRect();
    const all = [...board.querySelectorAll('.au-dest[data-to]')], top = all.length ? Math.min(...all.map(el => el.getBoundingClientRect().top)) : 0;
    const drops = all.filter(el => Math.abs(el.getBoundingClientRect().top - top) < 4)
      .map(el => { const r = el.getBoundingClientRect(); return {x: Math.round(r.left + r.width / 2 - box.left) + .5, c: el.style.getPropertyValue('--c')}; });
    const x0 = Math.round(lr.left + lr.width / 2 - box.left) + .5, xs = [x0, ...drops.map(d => d.x)];
    svg.setAttribute('viewBox', `0 0 ${w} 48`);
    svg.setAttribute('width', String(w));
    svg.innerHTML = !drops.length ? '' : `<path d="M${x0} 0 V22 M${Math.min(...xs)} 22 H${Math.max(...xs)}" style="stroke:var(--g4)"/>`
      + drops.map(d => `<path d="M${d.x} 22 V48 M${d.x - 4.5} 42 L${d.x} 48 L${d.x + 4.5} 42" style="stroke:${d.c}"/>`).join('');
  };
  draw();
  if (typeof ResizeObserver === 'function') { AU.branchRO = new ResizeObserver(() => requestAnimationFrame(draw)); AU.branchRO.observe(board); }
}
