/* ui/app/ask-rules.js — Ask's rules with no page in them: what a turn at work says
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   ui/tests/ask-rules.cjs runs them without a browser. */
'use strict';

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
