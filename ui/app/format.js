/* ui/app/format.js — Time, size and routine wording helpers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const mmss = ms => { const t = Math.max(0, Math.round((ms || 0) / 1000)); const h = Math.floor(t / 3600); const m = Math.floor(t % 3600 / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2,'0')}` : String(m)) + ':' + String(s).padStart(2, '0'); };
const ago = iso => { if (!iso) return ''; const s = (Date.now() - new Date(iso)) / 1000; if (s < 0) return 'just now'; if (s < 60) return `${Math.round(s)}s ago`; if (s < 3600) return `${Math.round(s/60)}m ago`; if (s < 86400) return `${Math.round(s/3600)}h ago`; return `${Math.round(s/86400)}d ago`; };
const until = iso => { if (!iso) return '—'; const s = (new Date(iso) - Date.now()) / 1000; if (s < 0) return 'due'; if (s < 3600) return `in ${Math.round(s/60)}m`; if (s < 86400) return `in ${Math.round(s/3600)}h`; return `in ${Math.round(s/86400)}d`; };
const fmt = iso => iso ? new Date(iso).toLocaleString(undefined, {weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
// A routine is a row in the hub (docs/routines.md): the owner or the bot's operator edits it here.
const routineMayEdit = r => S.me?.role === 'owner' || (!!S.me?.id && (S.emps || []).find(e => e.name === (r.employee || r.bot))?.operator === S.me.id);
const routinePaused = r => r.enabled === false || r.enabled === 0 ? ' <span class="pill" title="kept, not running">paused</span>' : '';
// A routine runs on a cron or `on:` a hub event (docs/routines.md); one phrase for either.
const EVENT_WORDS = {'meeting.ready': 'when a meeting is imported', 'recording.ready': 'when a meeting is imported',
  'market.insight.urgent': 'when an urgent market insight comes in'};
// An event with no words of its own still reads as words: "market.price.changed" is "when market price changed".
const eventWords = on => EVENT_WORDS[on] || `when ${String(on).replace(/[._-]+/g, ' ').trim()}`;
const cadenceWords = r => r.on ? eventWords(r.on) : cronWords(r.cron);
// A routine's time zone, short ("PDT"), and nothing when it is the viewer's own.
const zoneWord = tz => {
  if (!tz) return '';
  try {
    if (tz === Intl.DateTimeFormat().resolvedOptions().timeZone) return '';
    return new Intl.DateTimeFormat('en-US', {timeZone: tz, timeZoneName: 'short'}).formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || tz;
  } catch { return tz; }
};
const cadenceZoned = r => { const z = !r.on && zoneWord(r.timezone); return cadenceWords(r) + (z ? ` ${z}` : ''); };
const routineKind = r => r.kind || (r.on ? 'event' : 'cron');
const routineIsInbox = r => !!(r.inbox && ((r.inbox.mailboxes || []).length || r.inbox.org_read));
function routineMatches(r, filters) {
  const bot = (filters && filters.bot) || '';
  const kind = (filters && filters.kind) || 'all';
  const armed = (filters && filters.armed) || 'all';
  if (bot && (r.employee || r.bot) !== bot) return false;
  if (kind === 'cron' && routineKind(r) !== 'cron') return false;
  if (kind === 'event' && routineKind(r) !== 'event') return false;
  if (kind === 'inbox' && !routineIsInbox(r)) return false;
  if (armed === 'armed' && !r.active) return false;
  if (armed === 'idle' && r.active) return false;
  return true;
}
function durationWords(s) {
  if (s == null || s === '') return '';
  s = Math.max(0, Math.round(+s));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), r = s % 60;
  return r ? m + 'm ' + r + 's' : m + 'm';
}
function occurrenceLabel(o) {
  const t = String(o && o.occurrence || '');
  return /^\d{4}-\d{2}-\d{2}T/.test(t) ? fmt(t) : t;
}
function inboxHowHTML(r) {
  const boxes = (r.inbox && r.inbox.mailboxes) || [];
  if (!boxes.length && !(r.inbox && r.inbox.org_read)) return '';
  const title = boxes.join(', ') || 'team read';
  return ` <span class="pill inbox" title="${esc(title)}">Message bot</span>`;
}
function routineHowHTML(r, hideBot) {
  const bits = [];
  if (!hideBot) bits.push(empName(r.employee || r.bot));
  bits.push(esc(cadenceWords(r)));
  if (r.timezone && !r.on) bits.push(esc(r.timezone));
  const boxes = (r.inbox && r.inbox.mailboxes) || [];
  return `<span class="when">${bits.join(' · ')}${inboxHowHTML(r)}</span>`
    + (boxes.length ? `<span class="boxes" title="${esc(boxes.join(', '))}">${esc(boxes.join(', '))}</span>` : '');
}
function occurrenceTableHTML(rows) {
  if (!rows.length) return '<div class="empty">No runs recorded yet.</div>';
  const word = {created: 'created', coalesced: 'coalesced', event: 'event'};
  const pill = {created: '', coalesced: 'waiting', event: 'ready',
    done: 'ok', closed: 'ok', open: 'waiting', doing: 'in-progress', waiting: 'waiting',
    completed: 'ok', failed: 'fail', expired: 'fail', interrupted: 'fail'};
  return `<div class="scroll"><table><tr><th>Occurrence</th><th>Outcome</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr>`
    + rows.map(o => {
      const status = o.status || '';
      const exit = o.exit || '';
      return `<tr>
        <td>${esc(occurrenceLabel(o))}</td>
        <td><span class="pill ${pill[o.outcome] || ''}">${esc(word[o.outcome] || o.outcome || '—')}</span></td>
        <td>${status ? `<span class="pill ${pill[status] || ''}">${esc(status)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="tnum">${o.started ? esc(fmt(o.started)) : '<span class="muted">—</span>'}</td>
        <td class="tnum">${o.duration_s != null ? esc(durationWords(o.duration_s)) : (exit && exit !== 'completed' ? `<span class="pill ${pill[exit] || ''}">${esc(exit)}</span>` : '<span class="muted">—</span>')}</td>
        <td>${o.task_id ? `<a class="linkish" href="#/task/${esc(o.task_id)}">Open</a>` : ''}</td>
      </tr>`;
    }).join('') + '</table></div>';
}
function cronWords(expr) {
  const f = (expr || '').trim().split(/\s+/); if (f.length !== 5) return expr;
  const [mi, hr, dom, mon, dow] = f;
  // Every few minutes or hours, every day.
  if (mon === '*' && dom === '*' && dow === '*') {
    if (/^\*\/\d+$/.test(mi) && hr === '*') return `every ${mi.slice(2)} minutes`;
    if (/^\d+$/.test(mi) && hr === '*') return +mi ? `every hour at :${mi.padStart(2, '0')}` : 'every hour';
    if (/^\d+$/.test(mi) && /^\*\/\d+$/.test(hr)) return `every ${hr.slice(2)} hours` + (+mi ? ` at :${mi.padStart(2, '0')}` : '');
  }
  if (!/^\d+$/.test(mi) || mon !== '*') return expr;
  const times = hr.split(',').every(h => /^\d+$/.test(h)) ? hr.split(',').map(h => `${h.padStart(2,'0')}:${mi.padStart(2,'0')}`).join(' and ') : null;
  if (!times) return expr;
  const days = d => d.split(',').map(x => x.includes('-') ? x.split('-').map(Number) : [Number(x), Number(x)]).flatMap(([a,b]) => Array.from({length: b-a+1}, (_, i) => DAYS[a+i]));
  let when;
  if (dom === '*' && dow === '*') when = 'every day';
  else if (dom === '*' && dow === '1-5') when = 'weekdays';
  else if (dom === '*' && /^[\d,-]+$/.test(dow)) when = days(dow).join(', ');
  else if (dow === '*' && /^\*\/(\d+)$/.test(dom)) when = `every ${dom.slice(2)} days`;
  else if (dom === '1-7' && /^\d$/.test(dow)) when = `first ${DAYS[+dow]} of the month`;
  else if (dow === '*' && /^\d+$/.test(dom)) when = `day ${dom} of the month`;
  else return expr;
  return `${when} at ${times}`;
}

// One way to write a model everywhere: "Claude Opus 5", "GPT-5.5", "Gemini 3 Pro"; a provider prefix
// ("anthropic/") and a date stamp are dropped. A name it does not know stays as it came.
const HARNESS_WORDS = {claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI', antigravity: 'Antigravity', grok: 'Grok Build',
  cursor: 'Cursor', hermes: 'Hermes', openclaw: 'OpenClaw', grokbot: 'Grok Bot', dots: 'Dots', pi: 'pi'};
const harnessWords = id => HARNESS_WORDS[String(id || '').toLowerCase()] || id || '';
function modelWords(id) {
  const raw = String(id || '').trim().replace(/^[a-z][\w.-]*\//i, '');
  if (!raw) return '';
  const parts = raw.split(/[-_\s]+/).filter(p => !/^\d{6,}$/.test(p));
  const cap = w => w.charAt(0).toUpperCase() + w.slice(1);
  const version = list => list.filter(p => /^\d+(\.\d+)?$/.test(p)).join('.');
  const words = list => list.filter(p => !/^\d+(\.\d+)?$/.test(p)).map(cap).join(' ');
  if (/^claude$/i.test(parts[0]) || /^(opus|sonnet|haiku)$/i.test(parts[0])) {
    const rest = /^claude$/i.test(parts[0]) ? parts.slice(1) : parts;
    return ['Claude', words(rest), version(rest)].filter(Boolean).join(' ');
  }
  if (/^gpt/i.test(raw)) return raw.replace(/^gpt/i, 'GPT');
  if (/^gemini$/i.test(parts[0])) return ['Gemini', version(parts.slice(1)), words(parts.slice(1))].filter(Boolean).join(' ');
  return raw;
}
// A bot's model choice: "Claude Opus 5 · high", and the tool that runs it when that is not the model's own.
const modelChoiceWords = (model, effort, harness, ownHarness) => {
  const name = modelWords(model);
  if (!name) return '';
  const via = harness && ownHarness && harness !== ownHarness ? ` in ${harnessWords(harness)}` : '';
  return name + via + (effort ? ` · ${effort}` : '');
};
