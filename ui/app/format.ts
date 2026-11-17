/* ui/app/format.ts — Time, size and schedule wording helpers, and the date field (a calendar popover) that writes them
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A time the helpers below take: an ISO string, epoch milliseconds or a Date; nothing when it is missing. Other objects
// are accepted too (a caller's `{}` fallback) and read as an invalid time, which the words below leave blank.
type WhenInput = string | number | Date | object | null | undefined;
// The instant a time input stands for, as the Date constructor reads a string, a number or a Date (NaN for the rest).
const instantOf = (value: WhenInput): number => typeof value === 'string' || typeof value === 'number' || value instanceof Date ? new Date(value).getTime() : NaN;

const mmss = (ms: number | null | undefined): string => { const t = Math.max(0, Math.round((ms || 0) / 1000)); const h = Math.floor(t / 3600); const m = Math.floor(t % 3600 / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2,'0')}` : String(m)) + ':' + String(s).padStart(2, '0'); };
// Dates and times, to the style guide's rule ("Dates and times"): month first and a 12-hour clock ("Tue, Nov 11,
// 10:15 AM"); Today and Tomorrow replace the date; the year shows only when it is not this year; under a day old the
// time is how long ago ("35m", "2h"), after that the date ("Nov 6"). Always en-US, whatever the browser's locale: the
// rule is one way of writing a date, not the viewer's. Days are the viewer's own (local time).
const DATE_LOCALE = 'en-US';
// The moment a value stands for as a Date, or null. A bare day ("2026-11-15", a due date with no zone) is that day in the
// viewer's time zone: new Date() would read it as UTC midnight, the evening before west of Greenwich.
function dateOf(value: WhenInput): Date | null {
  if (typeof value === 'string') {
    const day = dfDate(value.trim());
    if (day) return day;
  }
  const t = instantOf(value);
  return Number.isFinite(t) ? new Date(t) : null;
}
// Whole days from today to the date's day, both in local time: 0 today, 1 tomorrow, -1 yesterday. Counted on the
// calendar (UTC of the local y/m/d), so a 23- or 25-hour day at a clock change still counts as one.
function dayOffset(d: Date, now: Date = new Date()): number {
  return Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
}
// "Today", "Tomorrow", "Nov 6", "Nov 6, 2025"; with weekday, "Tue, Nov 11". '' for a missing or unreadable value.
function dayWords(value: WhenInput, opts: {weekday?: boolean} = {}): string {
  const d = dateOf(value);
  if (!d) return '';
  const off = dayOffset(d);
  if (off === 0) return 'Today';
  if (off === 1) return 'Tomorrow';
  return dayFormat(!!opts.weekday, d.getFullYear() !== new Date().getFullYear()).format(d);
}
// The formats dayWords writes with, made once each: toLocaleDateString builds one per call, which a board of a thousand
// cards felt (task-pipelines.ts).
const DAY_FORMATS = new Map<string, Intl.DateTimeFormat>();
function dayFormat(weekday: boolean, year: boolean): Intl.DateTimeFormat {
  const key = `${weekday ? 'w' : ''}${year ? 'y' : ''}`;
  const kept = DAY_FORMATS.get(key);
  if (kept) return kept;
  const made = new Intl.DateTimeFormat(DATE_LOCALE, {weekday: weekday ? 'short' : undefined, month: 'short', day: 'numeric', year: year ? 'numeric' : undefined});
  DAY_FORMATS.set(key, made);
  return made;
}
// "10:15 AM", "9:00 PM": the clock alone, 12-hour, no leading zero.
const clockWords = (value: WhenInput): string => { const d = dateOf(value); return d ? d.toLocaleTimeString(DATE_LOCALE, {hour: 'numeric', minute: '2-digit'}) : ''; };
// A date with its time: "Today, 10:15 AM", "Tomorrow, 9:00 AM", "Tue, Nov 11, 10:15 AM", "Tue, Nov 11, 2025, 10:15 AM".
const timeWords = (value: WhenInput): string => { const day = dayWords(value, {weekday: true}); return day ? `${day}, ${clockWords(value)}` : ''; };
// How long ago, under a day ("now", "35m", "2h"); after that the date ("Nov 6"). A time more than a minute ahead says
// when it is ("Today, 3:00 PM", "Tomorrow, 9:00 AM"); one a little ahead (a clock running fast) is "now". A bare day
// (YYYY-MM-DD) has no time to count from: it is its day ("Today", "Nov 6").
const ago = (iso: WhenInput): string => {
  if (!iso) return '';
  if (typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(iso.trim())) return dayWords(iso);
  const s = (Date.now() - instantOf(iso)) / 1000;
  if (Number.isNaN(s)) return '';
  if (s < -60) return timeWords(iso);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.min(59, Math.max(1, Math.round(s / 60)))}m`;
  if (s < 86400) return `${Math.min(23, Math.max(1, Math.round(s / 3600)))}h`;
  return dayWords(iso);
};
// The words above inside a sentence. agoSaid: "Revoked just now", "2h ago", "Nov 6" (today and tomorrow in lower case).
// timeSaid: "until today, 10:15 AM", "since Tue, Nov 11, 10:15 AM".
const lowerDay = (words: string): string => words.replace(/^(Today|Tomorrow)\b/, w => w.toLowerCase());
const agoSaid = (iso: WhenInput): string => { const a = ago(iso); return a === 'now' ? 'just now' : /^\d+[mh]$/.test(a) ? `${a} ago` : lowerDay(a); };
const timeSaid = (iso: WhenInput): string => lowerDay(timeWords(iso));
const until = (iso: WhenInput): string => { if (!iso) return '—'; const s = (instantOf(iso) - Date.now()) / 1000; if (s < 0) return 'due'; if (s < 3600) return `in ${Math.max(1, Math.round(s/60))}m`; if (s < 86400) return `in ${Math.round(s/3600)}h`; return dayWords(iso); };
const fmt = (iso: WhenInput): string => iso ? timeWords(iso) : '';
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
  // 12-hour words without a leading zero: "9 AM", "3:30 PM", "12 PM" for noon, "12 AM" for midnight.
  const clock = (h: number): string => `${h % 12 || 12}${+mi ? ':' + mi.padStart(2, '0') : ''} ${h < 12 ? 'AM' : 'PM'}`;
  const times = hr.split(',').every(h => /^\d+$/.test(h)) ? hr.split(',').map(h => clock(+h)).join(' and ') : null;
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
  // A harness that compacts on its own default (`own_compaction` in its manifest) takes no Compact size.
  return !caps || (!!caps.compaction && !caps.own_compaction);
};

// Shared with the bot page's state pill: lifecycle pause takes precedence over a running turn.
function botPausedBadge(slug: string, link = false): string {
  if (stateOf(slug) !== 'paused') return '';
  const words = '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path fill="currentColor" d="M2 1h3v10H2zm5 0h3v10H7z"/></svg>Paused';
  return link ? `<a class="pill waiting bot-paused-badge" href="#/bot/${encodeURIComponent(slug)}">${words}</a>`
    : `<span class="pill waiting bot-paused-badge">${words}</span>`;
}

// ---- The date field (style guide, Fields > Date: "A field that opens a calendar popover. Same look on every browser.
// Phones keep the native picker.") A page marks a date input `<input type="date" data-date-field>` (or calls
// dateField(input) once it is in the page). On a computer the input becomes a text field that reads "Nov 13, 2026" and
// opens the calendar below on a click or the down arrow; on a phone, or with a touch screen, it stays the native picker.
// The element stays the page's own: its id, name, label and listeners are untouched, and its `value` is still the day as
// YYYY-MM-DD ('' when empty), so `form.elements.due.value` reads as before. It fires input and change once a day is
// picked or a typed one is taken (Enter, or leaving the field), never on each key. Typed days: "Nov 20", "Nov 20, 2026",
// "11/20/2026", "2026-11-20", "today", "tomorrow"; anything else puts the last day back.
// Keys in the calendar: arrows move a day or a week, Page Up/Down a month, Home/End the week's ends, Enter or Space
// picks, Esc closes; each returns focus to the field. Styles: ui/styles/forms.css (.df-*).
const DF_NATIVE = matchMedia('(max-width:760px), (pointer:coarse)');
const DF_VALUE = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value') as PropertyDescriptor & {get(this: HTMLInputElement): string; set(this: HTMLInputElement, v: string): void};
const DF_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const dfIso = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
// A bare day (YYYY-MM-DD) as that day in the viewer's time zone, or null.
function dfDate(iso: string): Date | null {
  const [, y = '', m = '', d = ''] = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso) || [];
  return y ? new Date(+y, +m - 1, +d) : null;
}
// A date field always shows the year (style guide "Dates and times").
const dfWords = (iso: string): string => dfDate(iso)?.toLocaleDateString(DATE_LOCALE, {month: 'short', day: 'numeric', year: 'numeric'}) || '';
// A typed day as YYYY-MM-DD; '' for an empty field; null when it is not a day.
function dfParse(text: string): string | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, ' ');
  if (!t) return '';
  const now = new Date(), year = now.getFullYear();
  const day = (y: number, m: number, d: number): string | null => { const x = new Date(y, m, d); return x.getFullYear() === y && x.getMonth() === m && x.getDate() === d ? dfIso(x) : null; };
  const yy = (y: string): number => !y ? year : y.length <= 2 ? 2000 + +y : +y;
  if (t === 'today') return dfIso(now);
  if (t === 'tomorrow') return dfIso(new Date(year, now.getMonth(), now.getDate() + 1));
  const month = (w: string): number => DF_MONTHS.findIndex(x => x.startsWith(w));
  // Each form's parts, read in order: [year, month, day] as typed ('' where a form leaves one out).
  const forms: [RegExp, (p: string[]) => [string, number, string]][] = [
    [/^(\d{4})-(\d{1,2})-(\d{1,2})$/, ([y = '', mo = '', d = '']) => [y, +mo - 1, d]],
    [/^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$/, ([mo = '', d = '', y = '']) => [y, +mo - 1, d]],   // month first: 11/20, 11/20/26
    [/^(?:[a-z]+,? )?([a-z]{3,})\.? (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?$/, ([mo = '', d = '', y = '']) => [y, month(mo), d]],   // (Tue,) Nov 20(, 2026)
    [/^(\d{1,2}) ([a-z]{3,})\.?,? ?(\d{4})?$/, ([d = '', mo = '', y = '']) => [y, month(mo), d]],   // 20 Nov 2026
  ];
  for (const [re, read] of forms) {
    const parts = re.exec(t);
    if (!parts) continue;
    const [y, mo, d] = read(parts.slice(1));
    return mo >= 0 ? day(yy(y), mo, +d) : null;
  }
  return null;
}
type DateFieldState = {iso: string; view: Date; active: string; pop: HTMLElement | null; firing: boolean};
const DF_STATE = new WeakMap<HTMLInputElement, DateFieldState>();
function dateField(input: HTMLInputElement): void {
  if (DF_STATE.has(input) || input.type !== 'date' || DF_NATIVE.matches) return;
  const st: DateFieldState = {iso: DF_VALUE.get.call(input), view: new Date(), active: '', pop: null, firing: false};
  DF_STATE.set(input, st);
  input.type = 'text';
  input.classList.add('df-input');
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('aria-haspopup', 'dialog');
  input.setAttribute('aria-expanded', 'false');
  const show = () => DF_VALUE.set.call(input, dfWords(st.iso));
  // `value` stays the day as YYYY-MM-DD for the page; the field shows its words.
  Object.defineProperty(input, 'value', {configurable: true, get: () => st.iso, set: (v: unknown) => { st.iso = dfParse(String(v ?? '')) || ''; show(); }});
  show();
  // The keys typed are not the page's input or change: those fire once a day is taken (dfTake).
  for (const type of ['input', 'change']) input.addEventListener(type, ev => { if (!st.firing) ev.stopImmediatePropagation(); }, true);
  input.addEventListener('input', () => {
    const typed = dfParse(DF_VALUE.get.call(input));
    if (typed && dfOpen(input)) { st.active = typed; st.view = dfDate(typed)!; dfDraw(input, false); }
  });
  input.addEventListener('click', () => { if (!dfOpen(input)) dfShow(input, false); });
  input.addEventListener('blur', () => { if (!dfOpen(input)) dfTakeTyped(input); });
  input.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowDown' || (ev.altKey && ev.key === 'ArrowUp')) { ev.preventDefault(); dfShow(input, true); return; }
    if (ev.key === 'Escape' && dfOpen(input)) { ev.preventDefault(); ev.stopPropagation(); dfHide(input); show(); return; }
    if (ev.key === 'Enter') { if (dfOpen(input)) { ev.preventDefault(); dfHide(input); } dfTakeTyped(input); }
    if (ev.key === 'Tab' && dfOpen(input)) dfHide(input);
  });
}
// A form read with FormData gets the day (YYYY-MM-DD) too, not the words the field shows.
document.addEventListener('formdata', (ev: FormDataEvent) => {
  if (!(ev.target instanceof HTMLFormElement)) return;
  for (const el of ev.target.elements) {
    const st = el instanceof HTMLInputElement && el.name && !el.disabled ? DF_STATE.get(el) : undefined;
    if (st && el instanceof HTMLInputElement) ev.formData.set(el.name, st.iso);
  }
});
const dfOpen = (input: HTMLInputElement): boolean => !!DF_STATE.get(input)?.pop?.matches(':popover-open');
// Take a day: the field shows it, and the page hears input and change (only when it changed).
function dfTake(input: HTMLInputElement, iso: string): void {
  const st = DF_STATE.get(input);
  if (!st) return;
  const changed = iso !== st.iso;
  st.iso = iso;
  DF_VALUE.set.call(input, dfWords(iso));
  input.removeAttribute('aria-invalid');
  if (!changed) return;
  st.firing = true;
  try { for (const type of ['input', 'change']) input.dispatchEvent(new Event(type, {bubbles: true})); } finally { st.firing = false; }
}
// What is typed in the field, taken if it is a day (and inside min and max), else the last day back.
function dfTakeTyped(input: HTMLInputElement): void {
  const st = DF_STATE.get(input), typed = dfParse(DF_VALUE.get.call(input));
  if (!st) return;
  if (typed === null || (typed && !dfAllowed(input, typed))) { DF_VALUE.set.call(input, dfWords(st.iso)); return; }
  dfTake(input, typed);
}
const dfAllowed = (input: HTMLInputElement, iso: string): boolean => (!input.min || iso >= input.min) && (!input.max || iso <= input.max);
function dfShow(input: HTMLInputElement, focusGrid: boolean): void {
  const st = DF_STATE.get(input);
  if (!st || input.disabled || input.readOnly) return;
  const typed = dfParse(DF_VALUE.get.call(input));
  st.active = typed || st.iso || dfIso(new Date());
  st.view = dfDate(st.active) || new Date();
  if (!st.pop) {
    const pop = document.createElement('div');
    pop.className = 'df-pop';
    pop.setAttribute('popover', 'manual');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', 'Choose a date');
    // after the field's label, so a click inside the calendar is not the label's; inside the same dialog, so it is not inert
    (input.closest('label') || input).after(pop);
    pop.addEventListener('click', ev => dfClick(input, ev));
    pop.addEventListener('keydown', ev => dfKey(input, ev));
    // Focus leaving the calendar for somewhere else closes it. Checked a moment later: a redraw of the month takes the
    // focused day out of the page for an instant, and the next day takes the focus right after.
    pop.addEventListener('focusout', () => setTimeout(() => {
      const at = document.activeElement;
      if (dfOpen(input) && at !== input && !pop.contains(at) && at !== document.body) { dfHide(input); dfTakeTyped(input); }
    }));
    st.pop = pop;
  }
  dfDraw(input, focusGrid);
  if (!dfOpen(input)) st.pop.showPopover();
  input.setAttribute('aria-expanded', 'true');
  dfPlace(input);
  if (focusGrid) st.pop.querySelector<HTMLElement>('.df-d[tabindex="0"]')?.focus();
}
function dfHide(input: HTMLInputElement): void {
  const pop = DF_STATE.get(input)?.pop;
  if (pop?.matches(':popover-open')) pop.hidePopover();
  input.setAttribute('aria-expanded', 'false');
}
// Under the field, its left edge on the field's; above it when there is no room below.
function dfPlace(input: HTMLInputElement): void {
  const pop = DF_STATE.get(input)?.pop;
  if (!pop?.matches(':popover-open')) return;
  const r = input.getBoundingClientRect(), h = pop.offsetHeight, w = pop.offsetWidth;
  const below = r.bottom + 4 + h <= innerHeight - 8 || r.top - 4 - h < 8;
  pop.style.top = `${below ? r.bottom + 4 : r.top - 4 - h}px`;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
}
addEventListener('resize', () => document.querySelectorAll<HTMLInputElement>('input.df-input').forEach(dfPlace));
document.addEventListener('scroll', () => document.querySelectorAll<HTMLInputElement>('input.df-input').forEach(dfPlace), true);
// A press outside the field and its calendar closes it.
document.addEventListener('pointerdown', ev => {
  const at = eventElement(ev);
  for (const input of document.querySelectorAll<HTMLInputElement>('input.df-input')) {
    const pop = DF_STATE.get(input)?.pop;
    if (pop?.matches(':popover-open') && at !== input && !pop.contains(at)) { dfHide(input); dfTakeTyped(input); }
  }
}, true);
function dfDraw(input: HTMLInputElement, focusGrid: boolean): void {
  const st = DF_STATE.get(input);
  if (!st?.pop) return;
  const y = st.view.getFullYear(), m = st.view.getMonth(), today = dfIso(new Date());
  const title = new Date(y, m, 1).toLocaleDateString(DATE_LOCALE, {month: 'long', year: 'numeric'});
  const start = new Date(y, m, 1 - new Date(y, m, 1).getDay());
  const weeks: string[] = [];
  for (let w = 0; w < 6; w++) {
    const cells: string[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7 + i), iso = dfIso(d);
      const cls = ['df-d', d.getMonth() !== m ? 'out' : '', iso === today ? 'today' : '', iso === st.iso ? 'sel' : ''].filter(Boolean).join(' ');
      const label = d.toLocaleDateString(DATE_LOCALE, {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'});
      cells.push(`<button type="button" role="gridcell" class="${cls}" data-df-day="${iso}" tabindex="${iso === st.active ? 0 : -1}" aria-label="${esc(label)}"`
        + `${iso === st.iso ? ' aria-selected="true"' : ''}${iso === today ? ' aria-current="date"' : ''}${dfAllowed(input, iso) ? '' : ' aria-disabled="true"'}>${d.getDate()}</button>`);
    }
    // a sixth row only when the month reaches it
    if (w < 5 || cells.some(c => !c.includes(' out'))) weeks.push(`<div role="row" class="df-row">${cells.join('')}</div>`);
  }
  const heads = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map((h, i) => `<span role="columnheader" class="df-h" aria-label="${['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][i]}">${h}</span>`).join('');
  st.pop.innerHTML = `<div class="df-head"><button type="button" class="df-nav" data-df-step="-1" aria-label="Previous month"><span class="nav-icon" aria-hidden="true">chevron_left</span></button>`
    + `<span class="df-month" aria-live="polite">${esc(title)}</span>`
    + `<button type="button" class="df-nav" data-df-step="1" aria-label="Next month"><span class="nav-icon" aria-hidden="true">chevron_right</span></button></div>`
    + `<div class="df-grid" role="grid" aria-label="${esc(title)}"><div role="row" class="df-row">${heads}</div>${weeks.join('')}</div>`;
  if (focusGrid) st.pop.querySelector<HTMLElement>('.df-d[tabindex="0"]')?.focus();
  dfPlace(input);
}
function dfClick(input: HTMLInputElement, ev: MouseEvent): void {
  const st = DF_STATE.get(input), at = eventElement(ev);
  if (!st || !at) return;
  const step = at.closest<HTMLElement>('[data-df-step]');
  if (step) {
    const n = Number(step.dataset['dfStep']);
    st.view = new Date(st.view.getFullYear(), st.view.getMonth() + n, 1);
    st.active = dfIso(dfMonthOn(dfDate(st.active) || new Date(), n));
    dfDraw(input, false);
    st.pop?.querySelector<HTMLElement>(`[data-df-step="${n}"]`)?.focus();
    return;
  }
  const day = at.closest<HTMLElement>('[data-df-day]');
  if (day && day.getAttribute('aria-disabled') !== 'true') dfPick(input, day.dataset['dfDay'] || '');
}
function dfPick(input: HTMLInputElement, iso: string): void {
  dfHide(input);
  dfTake(input, iso);
  input.focus();
}
// The same day `n` months on, or the month's last day when it has fewer (Jan 31 + 1 month is Feb 28).
const dfMonthOn = (a: Date, n: number): Date => new Date(a.getFullYear(), a.getMonth() + n, Math.min(a.getDate(), new Date(a.getFullYear(), a.getMonth() + n + 1, 0).getDate()));
function dfKey(input: HTMLInputElement, ev: KeyboardEvent): void {
  const st = DF_STATE.get(input);
  if (!st) return;
  if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); dfHide(input); input.focus(); return; }
  const cell = eventElement(ev)?.closest<HTMLElement>('[data-df-day]');
  if (!cell) return;
  if (ev.key === 'Enter' || ev.key === ' ') {
    ev.preventDefault();
    if (cell.getAttribute('aria-disabled') !== 'true') dfPick(input, cell.dataset['dfDay'] || '');
    return;
  }
  const a = dfDate(cell.dataset['dfDay'] || '')!;
  const to: Record<string, () => Date> = {
    ArrowLeft: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() - 1), ArrowRight: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() + 1),
    ArrowUp: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() - 7), ArrowDown: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() + 7),
    Home: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() - a.getDay()), End: () => new Date(a.getFullYear(), a.getMonth(), a.getDate() + 6 - a.getDay()),
    PageUp: () => dfMonthOn(a, ev.shiftKey ? -12 : -1), PageDown: () => dfMonthOn(a, ev.shiftKey ? 12 : 1),
  };
  const move = to[ev.key];
  if (!move) return;
  ev.preventDefault();
  const next = move();
  st.active = dfIso(next);
  st.view = new Date(next.getFullYear(), next.getMonth(), 1);
  dfDraw(input, true);
}
// Date inputs marked data-date-field turn into the field as they reach the page (a dialog drawn with innerHTML).
let DF_SCAN = 0;
const dfScan = () => { DF_SCAN = 0; document.querySelectorAll<HTMLInputElement>('input[type=date][data-date-field]').forEach(dateField); };
new MutationObserver(() => { if (!DF_SCAN) DF_SCAN = requestAnimationFrame(dfScan); }).observe(document.documentElement, {childList: true, subtree: true});
