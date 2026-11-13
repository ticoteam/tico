/* ui/app/format.ts — Time, size and schedule wording helpers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A time the helpers below take: an ISO string, epoch milliseconds or a Date; nothing when it is missing. Other objects
// are accepted too (a caller's `{}` fallback) and read as an invalid time (ago shows NaN for them, as before).
type WhenInput = string | number | Date | object | null | undefined;
// The instant a time input stands for, as the Date constructor reads a string, a number or a Date (NaN for the rest).
const instantOf = (value: WhenInput): number => typeof value === 'string' || typeof value === 'number' || value instanceof Date ? new Date(value).getTime() : NaN;

const mmss = (ms: number | null | undefined): string => { const t = Math.max(0, Math.round((ms || 0) / 1000)); const h = Math.floor(t / 3600); const m = Math.floor(t % 3600 / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2,'0')}` : String(m)) + ':' + String(s).padStart(2, '0'); };
const ago = (iso: WhenInput): string => { if (!iso) return ''; const s = (Date.now() - instantOf(iso)) / 1000; if (s < 60) return 'just now'; if (s < 3600) return `${Math.round(s/60)}m ago`; if (s < 86400) return `${Math.round(s/3600)}h ago`; return `${Math.round(s/86400)}d ago`; };
const until = (iso: WhenInput): string => { if (!iso) return '—'; const s = (instantOf(iso) - Date.now()) / 1000; if (s < 0) return 'due'; if (s < 3600) return `in ${Math.round(s/60)}m`; if (s < 86400) return `in ${Math.round(s/3600)}h`; return `in ${Math.round(s/86400)}d`; };
const fmt = (iso: WhenInput): string => iso ? new Date(instantOf(iso)).toLocaleString(undefined, {weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
// A relative time that keeps moving: the node holds its instant in data-ago and agoTick rewrites the words, so a
// page that stays open never reads "0s ago" for minutes. `agoSpan` is the markup; the full date is its hover.
const agoSpan = (iso: WhenInput): string => `<span data-ago="${esc(iso)}" title="${esc(fmt(iso))}">${esc(ago(iso))}</span>`;
function agoTick(root: ParentNode = document) {
  for (const node of root.querySelectorAll<HTMLElement>('[data-ago]')) {
    const text = ago(node.dataset.ago);
    if (node.textContent !== text) node.textContent = text;
  }
}
// A thread's HTML without those words, to compare two draws on: "1m ago" turning "2m ago" alone rebuilds nothing
// (typed replies, focus kept); agoTick(thread) moves the words instead.
const agoStill = (html: string): string => html.replace(/( data-ago="[^"]*"[^>]*>)[^<]*/g, '$1');
setInterval(() => { if (!document.hidden) agoTick(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) agoTick(); });
const DAYS: string[] = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
// A schedule runs on a cron or `on:` a hub event; one phrase for either.
const EVENT_WORDS: Record<string, string> = {'meeting.ready': 'when a meeting is imported', 'recording.ready': 'when a meeting is imported',
  'market.insight.urgent': 'when an urgent market insight comes in', release_published: 'when a release is published',
  'strategy.published': 'when a strategy version is published', 'market.briefing': 'when a market briefing is posted'};
// An event with no words of its own still reads as words: "market.price.changed" is "when market price changed".
const eventWords = (on: string): string => EVENT_WORDS[on] || (String(on).startsWith('webhook.') ? `on the ${(webhookTriggers() || []).find(t => t.event === on)?.name || on.slice(8).replace(/-/g, ' ')} webhook`
  : `when ${String(on).replace(/[._-]+/g, ' ').trim()}`);
const cadenceWords = (r: {on?: string | null; cron?: string | null}): string | null | undefined => r.on ? eventWords(r.on) : cronWords(r.cron);
// A schedule's time zone, short ("PDT"), and nothing when it is the viewer's own.
const zoneWord = (tz: string | null | undefined): string => {
  if (!tz) return '';
  try {
    if (tz === Intl.DateTimeFormat().resolvedOptions().timeZone) return '';
    return new Intl.DateTimeFormat('en-US', {timeZone: tz, timeZoneName: 'short'}).formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || tz;
  } catch { return tz; }
};
const cadenceZoned = (r: {on?: string | null; cron?: string | null; timezone?: string | null}): string => { const z = !r.on && zoneWord(r.timezone); return cadenceWords(r) + (z ? ` ${z}` : ''); };
function durationWords(value: number | string | null | undefined): string {
  if (value == null || value === '') return '';
  const s = Math.max(0, Math.round(+value));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), r = s % 60;
  return r ? m + 'm ' + r + 's' : m + 'm';
}
// A string in gives a string out; a missing value (null or undefined) comes back as it went in.
function cronWords(expr: string): string;
function cronWords(expr: string | null | undefined): string | null | undefined;
function cronWords(expr: string | null | undefined): string | null | undefined {
  const f = (expr || '').trim().split(/\s+/); if (f.length !== 5) return expr;
  const [mi = '', hr = '', dom = '', mon = '', dow = ''] = f;
  // Every few minutes or hours, every day.
  if (mon === '*' && dom === '*' && dow === '*') {
    if (/^\*\/\d+$/.test(mi) && hr === '*') return `every ${mi.slice(2)} minutes`;
    if (/^\d+$/.test(mi) && hr === '*') return +mi ? `every hour at :${mi.padStart(2, '0')}` : 'every hour';
    if (/^\d+$/.test(mi) && /^\*\/\d+$/.test(hr)) return `every ${hr.slice(2)} hours` + (+mi ? ` at :${mi.padStart(2, '0')}` : '');
  }
  if (!/^\d+$/.test(mi) || mon !== '*') return expr;
  const times = hr.split(',').every(h => /^\d+$/.test(h)) ? hr.split(',').map(h => `${h.padStart(2,'0')}:${mi.padStart(2,'0')}`).join(' and ') : null;
  if (!times) return expr;
  const days = (d: string): (string | undefined)[] => d.split(',').map(x => x.includes('-') ? x.split('-').map(Number) : [Number(x), Number(x)]).flatMap(([a = 0, b = 0]) => Array.from({length: b-a+1}, (_, i) => DAYS[a+i]));
  let when: string;
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
const HARNESS_WORDS: Record<string, string> = {claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI', antigravity: 'Antigravity', grok: 'Grok Build',
  cursor: 'Cursor', musecode: 'Muse Code', hermes: 'Hermes', openclaw: 'OpenClaw', grokbot: 'Grok Bot', dots: 'Dots', muse: 'Muse', pi: 'pi'};
// A harness id is read as text: a caller's fallback chain can pass any value.
const harnessWords = (id: unknown): string => HARNESS_WORDS[String(id || '').toLowerCase()] || String(id || '');
function modelWords(id: string | null | undefined): string {
  const raw = String(id || '').trim().replace(/^[a-z][\w.-]*\//i, '');
  if (!raw) return '';
  const parts = raw.split(/[-_\s]+/).filter(p => !/^\d{6,}$/.test(p));
  // `?? ''` is for the type: with no parts left (every part a date stamp), the head matches none of the tests below, as undefined did.
  const head = parts[0] ?? '';
  const cap = (w: string): string => w.charAt(0).toUpperCase() + w.slice(1);
  const version = (list: string[]): string => list.filter(p => /^\d+(\.\d+)?$/.test(p)).join('.');
  const words = (list: string[]): string => list.filter(p => !/^\d+(\.\d+)?$/.test(p)).map(cap).join(' ');
  if (/^claude$/i.test(head) || /^(opus|sonnet|haiku)$/i.test(head)) {
    const rest = /^claude$/i.test(head) ? parts.slice(1) : parts;
    return ['Claude', words(rest), version(rest)].filter(Boolean).join(' ');
  }
  if (/^gpt$/i.test(head)) return ['GPT', version(parts.slice(1)), words(parts.slice(1))].filter(Boolean).join(' ');
  if (/^gpt/i.test(raw)) return raw.replace(/^gpt/i, 'GPT');
  if (/^gemini$/i.test(head)) return ['Gemini', version(parts.slice(1)), words(parts.slice(1))].filter(Boolean).join(' ');
  // Tico's own models, served by the Tico AI Gateway: tico-xhigh-fast-1.0 is "X-High · Fast 1.0", the same without a
  // version "X-High · Fast (auto)".
  if (/^tico$/i.test(head) && parts.length > 1) {
    const rest = parts.slice(1), last = rest[rest.length - 1] ?? '', ver = /^\d+(\.\d+)?$/.test(last) ? rest.pop() : '';
    const level = (w: string): string => { const names: Record<string, string> = {xlow: 'X-Low', xhigh: 'X-High'}; return names[w.toLowerCase()] || cap(w); };
    return rest.map(level).join(' · ') + (ver ? ' ' + ver : ' (auto)');
  }
  return raw;
}
// A bot's model choice: "Claude Opus 5 · high", and the tool that runs it when that is not the model's own.
const modelChoiceWords = (model: string | null | undefined, effort: string | null | undefined, harness: string | null | undefined, ownHarness: string | null | undefined): string => {
  const name = modelWords(model);
  if (!name) return '';
  const via = harness && ownHarness && harness !== ownHarness ? ` in ${harnessWords(harness)}` : '';
  return name + via + (effort ? ` · ${effort}` : '');
};
// "1M", "1.05M" or "500K": a model's context window in tokens (`context_tokens` on a MODEL_CATALOG row); '' when unknown.
const modelContextWords = (tokens: number | null | undefined): string => {
  if (!(tokens && tokens > 0)) return '';
  return tokens >= 1000000 ? `${+(tokens / 1000000).toFixed(2)}M` : `${Math.round(tokens / 1000)}K`;
};
// Where a conversation compacts, in tokens (backend/providers.py COMPACT_SIZES), as a Compact list reads each size.
const COMPACT_SIZES: [number, string][] = [[100000, '100K'], [150000, '150K'], [200000, '200K'], [250000, '250K'],
  [300000, '300K'], [400000, '400K'], [500000, '500K'], [600000, '600K'], [700000, '700K'], [800000, '800K'],
  [900000, '900K'], [1000000, '1M']];
const compactWords = (tokens: number | null | undefined): string => COMPACT_SIZES.find(([size]) => size === tokens)?.[1]
  || (tokens ? `${Math.round(tokens / 1000)}K` : '');
// A Compact select's options: `first` (the fallback's, a run's or a step's "Same as bot", bulk change's "Keep each
// bot's") with the value '', then only the sizes a model of `window` tokens can hold (unknown: all twelve). A saved
// size off that list stays, so the select never shows something else than what is saved.
function compactOptions(current: number | null | undefined, window: number | null | undefined, first = 'Same as bot'): string {
  const fits = COMPACT_SIZES.filter(([size]) => !(window && window > 0) || size <= window);
  const list: [number | '', string][] = [['', first], ...fits];
  if (current && !fits.some(([size]) => size === current)) list.push([current, compactWords(current)]);
  return list.map(([value, label]) => `<option value="${value}"${(value || null) === (current || null) ? ' selected' : ''}>${esc(label)}</option>`).join('');
}
// Whether a harness compacts at a window (its manifest's `compaction`, spec scope 6); one whose manifest has not been
// read here is taken to, and the server ignores a window its harness does not take.
const compactsOn = (harness: string | undefined): boolean => {
  const caps = harness && typeof harnessRow === 'function' ? harnessRow(harness)?.capabilities : undefined;
  // A harness that compacts on its own default (Claude Code, as for a person) takes no Compact size.
  return !caps || (!!caps.compaction && !caps.own_compaction);
};

// Shared with the bot page's state pill: lifecycle pause takes precedence over a running turn.
function botPausedBadge(slug: string, link = false): string {
  if (stateOf(slug) !== 'paused') return '';
  const words = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path fill="currentColor" d="M2 1h3v10H2zm5 0h3v10H7z"/></svg>Paused';
  return link ? `<a class="pill waiting bot-paused-badge" href="#/bot/${encodeURIComponent(slug)}">${words}</a>`
    : `<span class="pill waiting bot-paused-badge">${words}</span>`;
}
