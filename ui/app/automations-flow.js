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
  call_webhook: 'Call a webhook', assign: 'Assign'};
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
const auActionArgs = s => {
  const a = s.args || {};
  const who = a.owner || a.to || a.bot;
  return who ? 'to ' + auActorName(String(who).includes(':') ? who : 'bot:' + who) : a.label || a.endpoint || '';
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
      ${auThenWords(r.then).map(x => `<span class="au-c1">${esc(x)}</span>`).join('')}</div>`).join('');
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
  await auWrite({steps: auCleanSteps(steps)}, out => `${AU_STEP_WORDS[hit.step.type]} ${on ? 'on' : 'off'} · v${out.version}`);
}
// What is written back: a Decide step on a saved set sends the set only (its questions came along read-only).
function auCleanSteps(steps) {
  for (const s of steps || []) {
    if (s.type === 'decide' && s.set) delete s.questions;
    if (s.type === 'paths') [...(s.paths || []), s.otherwise || {steps: []}].forEach(p => auCleanSteps(p.steps));
  }
  return steps;
}
