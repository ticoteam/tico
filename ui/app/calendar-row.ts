/* ui/app/calendar-row.ts — The sidebar's Calendar row (spec 2026-11-10-calendar §12.1): the icon and "Calendar" (the page),
   a day bar over the viewer's working hours with today's events and a now line, and the next event as quiet text that
   joins its call. The row's markup is in index.html from the first paint; this file only fills the bar and the text.
   Also the shapes every calendar read answers with (docs/dev/calendar-contract.md) and Join, which the page shares.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the contract's shapes
// GET /api/v2/calendar/* (backend/calendar_api.py). The OpenAPI models come later; until then these are the contract's.
type CalKind = 'event' | 'run' | 'due' | 'away';
interface CalCalendarRef { ref: string; type: string; id: string; name: string; }
interface CalAttendee { type: string; id?: string | null; email?: string | null; name: string; response?: string; }
interface CalLinks { thread?: string | null; meeting?: string | null; task?: string | null; automation?: string | null; attempt?: string | null; }
interface CalOccurrence {
  occurrence_id: string;
  event_id?: string | null;
  calendar: CalCalendarRef;
  home?: string;
  kind: CalKind;
  title: string;
  start: string;
  end: string;
  all_day?: boolean;
  time_zone?: string | null;
  attendees?: CalAttendee[];
  response?: string | null;
  busy_only?: boolean;
  recurring?: boolean;
  status?: string;
  meeting_url?: string | null;
  open_url?: string | null;
  links?: CalLinks | null;
  can?: {join?: boolean; edit?: boolean} | null;
  planned?: boolean;
  count?: number;
  children?: string[];
}
interface CalView {
  ref: string; type: string; id: string; name: string; home?: string; time_zone?: string | null;
  work_starts?: string | null; work_ends?: string | null; work_days?: string | null; can_write?: boolean; kind?: string;
}
interface CalMonthCount { bot_id: string; date: string; count: number; }
interface CalEventsAnswer {
  from: string; to: string; calendars: CalView[]; occurrences: CalOccurrence[];
  month_counts?: CalMonthCount[]; errors?: {ref: string; reason: string}[];
}
interface CalSettings { time_zone?: string | null; work_starts?: string | null; work_ends?: string | null; work_days?: string | null; record_on_join?: boolean; }

const CAL_ROUTE = '#/calendar';
const CAL_API = '/v2/calendar';

// ----------------------------------------------------------------- times
const calPad = (n: number) => String(n).padStart(2, '0');
const calDay = (d: Date) => `${d.getFullYear()}-${calPad(d.getMonth() + 1)}-${calPad(d.getDate())}`;
// A date or an instant: an all-day "YYYY-MM-DD" is local midnight, anything else the instant it names.
function calWhen(value: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value);
}
const calMidnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const calAddDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
// "08:00" as minutes after midnight; anything unreadable is the fallback.
function calMinutes(hhmm: string | null | undefined, fallback: number): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  return m ? Number(m[1]) * 60 + Number(m[2]) : fallback;
}
const calMinuteOf = (d: Date) => d.getHours() * 60 + d.getMinutes();
// The clock as the viewer's locale writes it, without am/pm: "9:30" where hours run 1-12, "09:30" where 0-23.
const CAL_H12 = (() => { try { const c = new Intl.DateTimeFormat(undefined, {hour: 'numeric'}).resolvedOptions().hourCycle; return c === 'h12' || c === 'h11'; } catch { return true; } })();
const calClock = (d: Date) => CAL_H12 ? `${d.getHours() % 12 || 12}:${calPad(d.getMinutes())}` : `${calPad(d.getHours())}:${calPad(d.getMinutes())}`;
const calRange = (o: CalOccurrence) => o.all_day ? '' : `${calClock(calWhen(o.start))}–${calClock(calWhen(o.end))}`;

// ----------------------------------------------------------------- Join
// Joining opens the call in a new tab and, on a Mac, Tico Recorder on it (decision 15), unless the person turned that off
// in the page's ⋯ menu. A browser follows the Recorder's link without leaving the page; with no Recorder installed nothing
// happens and the call is open anyway. The link is opened as a Meet call's is (ui/app/thread-call.ts recorderLinkOpen).
const CAL_RECORD_KEY = 'tico.calendar.record';
function calRecordOn(): boolean { try { return localStorage.getItem(CAL_RECORD_KEY) !== 'off'; } catch { return true; } }
function calRecordSet(on: boolean): void { try { localStorage.setItem(CAL_RECORD_KEY, on ? 'on' : 'off'); } catch { /* not kept */ } }
function calRecorder(o: CalOccurrence): void {
  if (!calRecordOn() || !recorderMac()) return;
  // The desktop app opens the Recorder itself (`recordEvent`, app/src/bridge.rs, on the page's own org): a page's hidden
  // frame cannot follow the link there. An app without `recordEvent` opens nothing, as it does for a call (thread-call.ts).
  if (window.ticoNative) {
    if (nativeHas('recordEvent')) try { void Promise.resolve(nativeHandler('recordEvent')?.postMessage({event: o.occurrence_id, title: o.title})).catch(() => {}); } catch {}
    return;
  }
  recorderLinkOpen(`tico-recorder://record?server=${encodeURIComponent(location.origin)}&title=${encodeURIComponent(o.title)}&event=${encodeURIComponent(o.occurrence_id)}`, 'cal-recorder-link');
}
// A Join link: the call itself, so a new tab opens it as any link would; the click also starts the Recorder.
const calJoinAttrs = (o: CalOccurrence) => o.meeting_url
  ? `href="${esc(o.meeting_url)}" target="_blank" rel="noopener noreferrer" data-cal-join="${esc(o.occurrence_id)}"`
  : `href="${esc(calOpenHref(o))}"`;
// The page, on the event's day with its card open.
const calOpenHref = (o: CalOccurrence) => `${CAL_ROUTE}?date=${calDay(calWhen(o.start))}&open=${encodeURIComponent(o.occurrence_id)}`;

// ----------------------------------------------------------------- the row
interface CalRowState { list: CalOccurrence[]; work: [number, number]; at: number; off: boolean; loading: boolean; sel: number; live: (() => void) | null; }
const CAL_ROW: CalRowState = {list: [], work: [8 * 60, 18 * 60], at: 0, off: false, loading: false, sel: -1, live: null};
const CAL_ROW_EVERY = 3 * 60 * 1000;
const calRowEl = () => document.getElementById('nav-calendar');

async function calRowLoad(): Promise<void> {
  if (CAL_ROW.off || CAL_ROW.loading || !S.me || SESSION.out) return;
  CAL_ROW.loading = true;
  const from = calMidnight(new Date()), to = calAddDays(from, 1);
  try {
    const q = new URLSearchParams({who: 'me', from: from.toISOString(), to: to.toISOString(), kinds: 'event'});
    const a = await get<CalEventsAnswer>(`${CAL_API}/events?${q}`, {fresh: true});
    if (!a || !Array.isArray(a.occurrences)) return;     // not a calendar answer: the row stays just "Calendar"
    const me = (a.calendars || [])[0];
    CAL_ROW.work = [calMinutes(me?.work_starts, 8 * 60), calMinutes(me?.work_ends, 18 * 60)];
    // Events the viewer is on: not declined, timed, today.
    CAL_ROW.list = a.occurrences.filter(o => o.kind === 'event' && !o.all_day && o.response !== 'declined')
      .sort((x, y) => calWhen(x.start).getTime() - calWhen(y.start).getTime());
    CAL_ROW.at = Date.now();
    calRowKeep(true);
    // Calendar writes arrive live once the server has shown it serves calendars (an older one refuses the topic).
    if (!CAL_ROW.live) {
      const soon = () => liveSoon('cal-row', () => void calRowLoad(), 500);
      const offs = [liveOn('calendar', soon), liveOn('reset', soon)];
      CAL_ROW.live = () => { for (const off of offs) off(); };
      liveTopicAdd('calendar');
    }
  } catch (e) {
    // A server without calendars (404): the row is just "Calendar", and nothing asks again.
    if ((e as {status?: number}).status === 404) { CAL_ROW.off = true; CAL_ROW.list = []; calRowKeep(false); }
  } finally {
    CAL_ROW.loading = false;
  }
  calRowDraw();
}
// Whether this server has calendars, kept so the next first paint holds the bar's line (ui/theme.ts).
function calRowKeep(on: boolean): void {
  document.documentElement.classList.toggle('nav-cal', on);
  try { localStorage.setItem('tico.nav.calendar', on ? 'on' : 'off'); } catch { /* not kept */ }
}
// Called on every route (router.ts) and on focus: reads when the last read is older than the refresh.
function calRowSync(): void {
  if (Date.now() - CAL_ROW.at >= CAL_ROW_EVERY) void calRowLoad();
}

function calRowDraw(): void {
  const row = calRowEl(); if (!row) return;
  const bar = row.querySelector<HTMLElement>('[data-cal-bar]'), next = row.querySelector<HTMLElement>('[data-cal-next]');
  if (!bar || !next) return;
  if (CAL_ROW.off || !CAL_ROW.at) { bar.innerHTML = ''; next.innerHTML = ''; row.classList.remove('cal-on'); return; }
  row.classList.add('cal-on');
  const now = new Date(), nowMin = calMinuteOf(now), list = CAL_ROW.list;
  // The bar spans working hours, stretched for an earlier or later event.
  let [lo, hi] = CAL_ROW.work;
  for (const o of list) { lo = Math.min(lo, calMinuteOf(calWhen(o.start))); hi = Math.max(hi, calEndMinute(o)); }
  if (hi - lo < 60) hi = lo + 60;
  const pc = (m: number) => Math.max(0, Math.min(100, (m - lo) / (hi - lo) * 100));
  const blocks = list.map((o, i) => {
    const s = calMinuteOf(calWhen(o.start)), e = calEndMinute(o), past = calWhen(o.end).getTime() <= now.getTime();
    return `<button type="button" class="cal-blk${past ? ' past' : ''}${CAL_ROW.sel === i ? ' sel' : ''}" style="left:${pc(s).toFixed(2)}%;width:${Math.max(0, pc(e) - pc(s)).toFixed(2)}%" data-cal-i="${i}" aria-label="${esc(o.title)}, ${esc(calRange(o))}"></button>`;
  }).join('');
  const nowLine = nowMin >= lo && nowMin <= hi ? `<i class="cal-now" style="left:${pc(nowMin).toFixed(2)}%"></i>` : '';
  const sel = list[CAL_ROW.sel];
  const bubble = sel ? `<span class="cal-bub" data-cal-bub><a ${calJoinAttrs(sel)}>${esc(sel.title)}</a><span>${esc(calRange(sel))}</span></span>` : '';
  bar.innerHTML = blocks + nowLine + bubble;
  if (sel) calRowPlaceBubble(row, bar);
  // The current event ("Standup now"), else the next ("Standup 9:30"), else Clear.
  const cur = list.find(o => calWhen(o.start).getTime() <= now.getTime() && calWhen(o.end).getTime() > now.getTime());
  const up = cur || list.find(o => calWhen(o.start).getTime() > now.getTime());
  next.innerHTML = up
    ? `<a class="cal-nx" ${calJoinAttrs(up)}><span class="cal-nm">${esc(up.title)}</span><span class="cal-tm">${cur ? 'now' : esc(calClock(calWhen(up.start)))}</span></a>`
    : '<span class="cal-nx cal-clear">Clear</span>';
}
// Minutes after today's midnight an event ends (past midnight is the end of the day).
function calEndMinute(o: CalOccurrence): number {
  const end = calWhen(o.end), day = calMidnight(new Date());
  return end.getTime() >= calAddDays(day, 1).getTime() ? 24 * 60 : calMinuteOf(end);
}
// The bubble sits under its block, kept inside the row.
function calRowPlaceBubble(row: HTMLElement, bar: HTMLElement): void {
  const bub = bar.querySelector<HTMLElement>('[data-cal-bub]'), blk = bar.querySelector<HTMLElement>('.cal-blk.sel');
  if (!bub || !blk) return;
  const r = row.getBoundingClientRect(), b = blk.getBoundingClientRect(), barR = bar.getBoundingClientRect(), w = bub.offsetWidth;
  const center = b.left + b.width / 2;
  const left = Math.max(r.left + 4, Math.min(center - w / 2, r.right - w - 4));
  bub.style.left = (left - barR.left) + 'px';
  bub.style.setProperty('--tip', (center - left) + 'px');
}
function calRowSelect(i: number): void {
  if (CAL_ROW.sel === i) return;
  CAL_ROW.sel = i; calRowDraw();
}

(() => {
  const row = calRowEl(); if (!row) return;
  row.addEventListener('mouseover', ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('.cal-blk');
    if (b) calRowSelect(Number(b.dataset.calI));
  });
  row.addEventListener('mouseleave', () => calRowSelect(-1));
  row.addEventListener('click', ev => {
    const t = eventElement(ev);
    const join = t?.closest<HTMLElement>('[data-cal-join]');
    if (join) {
      const o = CAL_ROW.list.find(x => x.occurrence_id === join.dataset.calJoin);
      if (o) calRecorder(o);
      return;     // the link itself opens the call
    }
    const b = t?.closest<HTMLElement>('.cal-blk');
    if (b) { const i = Number(b.dataset.calI); CAL_ROW.sel = CAL_ROW.sel === i ? -1 : i; calRowDraw(); }
  });
  row.addEventListener('focusout', ev => { if (!row.contains(ev.relatedTarget instanceof Node ? ev.relatedTarget : null)) calRowSelect(-1); });
  window.addEventListener('focus', calRowSync);
  // The now line moves each minute; a read every three while the window is shown.
  setInterval(() => { if (document.hidden) return; if (Date.now() - CAL_ROW.at >= CAL_ROW_EVERY) void calRowLoad(); else calRowDraw(); }, 60 * 1000);
})();
