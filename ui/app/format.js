/* ui/app/format.js — Time, size and schedule wording helpers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const mmss = ms => { const t = Math.max(0, Math.round((ms || 0) / 1000)); const h = Math.floor(t / 3600); const m = Math.floor(t % 3600 / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2,'0')}` : String(m)) + ':' + String(s).padStart(2, '0'); };
const ago = iso => { if (!iso) return ''; const s = (Date.now() - new Date(iso)) / 1000; if (s < 60) return 'just now'; if (s < 3600) return `${Math.round(s/60)}m ago`; if (s < 86400) return `${Math.round(s/3600)}h ago`; return `${Math.round(s/86400)}d ago`; };
const until = iso => { if (!iso) return '—'; const s = (new Date(iso) - Date.now()) / 1000; if (s < 0) return 'due'; if (s < 3600) return `in ${Math.round(s/60)}m`; if (s < 86400) return `in ${Math.round(s/3600)}h`; return `in ${Math.round(s/86400)}d`; };
const fmt = iso => iso ? new Date(iso).toLocaleString(undefined, {weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
// A relative time that keeps moving: the node holds its instant in data-ago and agoTick rewrites the words, so a
// page that stays open never reads "0s ago" for minutes. `agoSpan` is the markup; the full date is its hover.
const agoSpan = iso => `<span data-ago="${esc(iso)}" title="${esc(fmt(iso))}">${esc(ago(iso))}</span>`;
function agoTick() {
  for (const node of document.querySelectorAll('[data-ago]')) {
    const text = ago(node.dataset.ago);
    if (node.textContent !== text) node.textContent = text;
  }
}
setInterval(() => { if (!document.hidden) agoTick(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) agoTick(); });
const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
// A schedule runs on a cron or `on:` a hub event; one phrase for either.
const EVENT_WORDS = {'meeting.ready': 'when a meeting is imported', 'recording.ready': 'when a meeting is imported',
  'market.insight.urgent': 'when an urgent market insight comes in', release_published: 'when a release is published',
  'strategy.published': 'when a strategy version is published', 'market.briefing': 'when a market briefing is posted'};
// An event with no words of its own still reads as words: "market.price.changed" is "when market price changed".
const eventWords = on => EVENT_WORDS[on] || (String(on).startsWith('webhook.') ? `on the ${(WH_TRIGGERS || []).find(t => t.event === on)?.name || on.slice(8).replace(/-/g, ' ')} webhook`
  : `when ${String(on).replace(/[._-]+/g, ' ').trim()}`);
const cadenceWords = r => r.on ? eventWords(r.on) : cronWords(r.cron);
// A schedule's time zone, short ("PDT"), and nothing when it is the viewer's own.
const zoneWord = tz => {
  if (!tz) return '';
  try {
    if (tz === Intl.DateTimeFormat().resolvedOptions().timeZone) return '';
    return new Intl.DateTimeFormat('en-US', {timeZone: tz, timeZoneName: 'short'}).formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || tz;
  } catch { return tz; }
};
const cadenceZoned = r => { const z = !r.on && zoneWord(r.timezone); return cadenceWords(r) + (z ? ` ${z}` : ''); };
function durationWords(s) {
  if (s == null || s === '') return '';
  s = Math.max(0, Math.round(+s));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), r = s % 60;
  return r ? m + 'm ' + r + 's' : m + 'm';
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

// One way to write a model everywhere: "Claude Opus 5", "GPT 6.1 Sol", "Gemini 3 Pro"; a provider prefix
// ("anthropic/") and a date stamp are dropped. A name it does not know stays as it came.
const HARNESS_WORDS = {claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI', antigravity: 'Antigravity', grok: 'Grok Build',
  cursor: 'Cursor', musecode: 'Muse Code', hermes: 'Hermes', openclaw: 'OpenClaw', grokbot: 'Grok Bot', dots: 'Dots', muse: 'Muse', pi: 'pi'};
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
  if (/^gpt$/i.test(parts[0])) return ['GPT', version(parts.slice(1)), words(parts.slice(1))].filter(Boolean).join(' ');
  if (/^gpt/i.test(raw)) return raw.replace(/^gpt/i, 'GPT');
  if (/^gemini$/i.test(parts[0])) return ['Gemini', version(parts.slice(1)), words(parts.slice(1))].filter(Boolean).join(' ');
  // Tico's own models, served by the Tico AI Gateway: tico-sales-medium-fast-1.0 is "Sales · Medium · Fast 1.0", the same
  // without a version "Sales · Medium · Fast (auto)", and the ids from before the tiers "Mid 1.0 (old lineup)".
  if (/^tico$/i.test(parts[0]) && parts.length > 1) {
    const rest = parts.slice(1), ver = /^\d+(\.\d+)?$/.test(rest[rest.length - 1]) ? rest.pop() : '';
    if (rest.length === 1 && /^(cheap|mid|power|decide|classify)$/i.test(rest[0])) return `${cap(rest[0])} ${ver}`.trim() + ' (old lineup)';
    return rest.map(cap).join(' · ') + (ver ? ' ' + ver : ' (auto)');
  }
  return raw;
}
// A bot's model choice: "Claude Opus 5 · high", and the tool that runs it when that is not the model's own.
const modelChoiceWords = (model, effort, harness, ownHarness) => {
  const name = modelWords(model);
  if (!name) return '';
  const via = harness && ownHarness && harness !== ownHarness ? ` in ${harnessWords(harness)}` : '';
  return name + via + (effort ? ` · ${effort}` : '');
};

// Shared with the bot page's state pill: lifecycle pause takes precedence over a running turn.
function botPausedBadge(slug, link = false) {
  if (stateOf(slug) !== 'paused') return '';
  const words = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path fill="currentColor" d="M2 1h3v10H2zm5 0h3v10H7z"/></svg>Paused';
  return link ? `<a class="pill waiting bot-paused-badge" href="#/bot/${encodeURIComponent(slug)}">${words}</a>`
    : `<span class="pill waiting bot-paused-badge">${words}</span>`;
}
