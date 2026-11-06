/* ui/app/ask-rules.js — Ask's rules with no page in them: the peeks beside the dot
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   ui/tests/ask-rules.cjs runs them without a browser. */
'use strict';

// Peeks: one line beside the dot about something already waiting on the person (a bot's question, an ask), so Tico
// speaks up without nagging. One at a time; none while the panel is open, while typing or in the first few seconds;
// one dismissed stays dismissed; an item peeks at most once a day. `askPeekFresh`: the items that may peek now.
const ASK_PEEK_WAIT_MS = 3000;
const ASK_PEEK_DAY_MS = 24 * 3600 * 1000;
function askPeekFresh(items, {now = Date.now(), loadedAt = 0, typing = false, open = false, dismissed = [], shown = {}} = {}) {
  if (open || typing || now - loadedAt < ASK_PEEK_WAIT_MS) return [];
  return (items || []).filter(it => it?.id && !dismissed.includes(it.id) && !(now - (Number(shown[it.id]) || 0) < ASK_PEEK_DAY_MS));
}
const askPeekPick = (items, opts) => askPeekFresh(items, opts)[0] || null;
// What a peek says, and the items it speaks for: Tico talking, six words at most, about the fresh items of the first
// one's kind (bots' questions, or Confirm cards: `confirm` true). Never who asked, never the question's own words.
function askPeekLine(fresh) {
  const first = (fresh || [])[0];
  if (!first) return {text: '', ids: []};
  const same = fresh.filter(it => !!it.confirm === !!first.confirm), n = same.length;
  const text = first.confirm ? (n > 1 ? `${n} things need your OK` : 'Need your OK on something')
    : n > 1 ? `${n} questions are waiting on you` : 'Got a quick question for you';
  return {text, ids: same.map(it => it.id)};
}
// What the peek store keeps: the last 50 dismissed ids, and when each item last peeked (a day's worth is enough).
function askPeekTidy({dismissed = [], shown = {}} = {}, now = Date.now()) {
  return {dismissed: [...new Set(dismissed)].slice(-50),
    shown: Object.fromEntries(Object.entries(shown).filter(([, at]) => now - Number(at) < ASK_PEEK_DAY_MS).slice(-50))};
}

// While a turn works with no words yet: its label, how long it has run and its newest tool step, e.g.
// "Working · 4m · Closing tasks". A step is the server's tool label ("Ran hub", backend/turns.py tool_label:
// the tool's name only, never its arguments), said as a verb and what it acts on; a name it does not know stays as
// it is. `x` is a conversation snapshot's execution.
const ASK_TICK_MS = 15000;
const ASK_STEP_VERBS = {list: 'Reading', show: 'Reading', read: 'Reading', get: 'Reading', search: 'Searching', find: 'Searching',
  tree: 'Reading', many: 'Reading', create: 'Creating', add: 'Adding', import: 'Importing', update: 'Updating', edit: 'Updating',
  set: 'Updating', bulk: 'Updating', label: 'Updating', link: 'Linking', relate: 'Linking', close: 'Closing', done: 'Closing',
  delete: 'Deleting', remove: 'Removing', archive: 'Archiving', restore: 'Restoring', snooze: 'Snoozing', comment: 'Commenting on',
  send: 'Sending', post: 'Sending', reply: 'Replying in', publish: 'Publishing', write: 'Writing', ask: 'Asking about',
  approve: 'Approving', decline: 'Declining', run: 'Running'};
const ASK_STEP_NOUNS = {kpi: 'KPIs'}, ASK_STEP_ONE = {sql: 'Reading data', api: 'Using Tico'};
const ASK_STEP_TOOLS = {bash: 'Running a command', shell: 'Running a command', exec: 'Running a command', 'exec command': 'Running a command',
  'local shell': 'Running a command', read: 'Reading files', grep: 'Searching files', glob: 'Searching files', ls: 'Reading files',
  edit: 'Editing files', 'multi edit': 'Editing files', multiedit: 'Editing files', write: 'Editing files', 'apply patch': 'Editing files',
  'web search': 'Searching the web', websearch: 'Searching the web', 'web fetch': 'Reading the web', webfetch: 'Reading the web',
  task: 'Delegating', agent: 'Delegating', 'todo write': 'Planning', todowrite: 'Planning'};
function askToolStep(label) {
  const raw = String(label || '').replace(/^Ran\s+/, '').replace(/\s+/g, ' ').trim();
  if (!raw || raw === 'a tool') return '';
  let words = raw.toLowerCase().split(' ');
  if (words[0] === 'mcp') words = words.slice(1);
  if (ASK_STEP_TOOLS[words.join(' ')]) return ASK_STEP_TOOLS[words.join(' ')];
  const tico = ['hub', 'tico'].includes(words[0]);
  while (['hub', 'tico'].includes(words[0])) words = words.slice(1);
  if (tico && words.length === 1 && ASK_STEP_ONE[words[0]]) return ASK_STEP_ONE[words[0]];
  const verb = tico && words.slice(1).reverse().find(w => ASK_STEP_VERBS[w]);
  if (verb) {
    const noun = words[0], what = ASK_STEP_NOUNS[noun] || (noun.endsWith('s') ? noun : noun + 's');
    return ASK_STEP_VERBS[verb] + ' ' + what;
  }
  // A shell program the server named ("Ran npm"): said as the step it is.
  if (!tico && /^[a-z0-9._-]+$/.test(raw)) return 'Running ' + raw;
  const plain = (tico ? words.join(' ') : raw).slice(0, 40);
  return plain.charAt(0).toUpperCase() + plain.slice(1);
}
function askElapsed(startedAt, now = Date.now()) {
  const from = Date.parse(startedAt || '');
  if (!from) return '';
  const s = Math.max(0, Math.floor((now - from) / 1000));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  return m < 60 ? m + 'm' : Math.floor(m / 60) + 'h ' + (m % 60) + 'm';
}
// '' when the turn is not at work (queued, ended): the chat keeps its own line then.
function askWorkingLine(x, now = Date.now()) {
  if (!x || !['leased', 'running'].includes(x.state)) return '';
  const tool = (x.parts || []).filter(p => p?.kind === 'tool').at(-1);
  return [x.label || 'Working', askElapsed(x.started_at, now), askToolStep(tool?.text)].filter(Boolean).join(' · ');
}
