/* ui/app/calendar-row.ts — The sidebar's Calendar row (spec 2026-11-10-calendar §12.1, redrawn by "Next event on Home's
   Calendar row" v3): the icon and "Calendar" (the page), then the viewer's next event in plain text with ‹ › to step
   through the week. The row's markup is in index.html from the first paint; this file only fills the text.
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
// One line: "Calendar", then the viewer's next event as plain text ("Sales call · in 3 min"), its name opening the event on
// the page. › steps to the event after it and ‹ back; a new page resets it to the very next. Nothing in the next week
// reads "Nothing scheduled". Only the viewer's own timed events: no runs, nothing due, nothing declined, no all-day ones.
interface CalRowState { list: CalOccurrence[]; at: number; off: boolean; failed: boolean; loading: boolean; pick: string | null; live: (() => void) | null; }
const CAL_ROW: CalRowState = {list: [], at: 0, off: false, failed: false, loading: false, pick: null, live: null};
const CAL_ROW_EVERY = 3 * 60 * 1000;
const CAL_ROW_DAYS = 7;
const calRowEl = () => document.getElementById('nav-calendar');

async function calRowLoad(): Promise<void> {
  if (CAL_ROW.off || CAL_ROW.loading || !S.me || SESSION.out) return;
  CAL_ROW.loading = true;
  const from = calMidnight(new Date()), to = calAddDays(from, CAL_ROW_DAYS + 1);
  try {
    const q = new URLSearchParams({who: 'me', from: from.toISOString(), to: to.toISOString(), kinds: 'event'});
    const a = await get<CalEventsAnswer>(`${CAL_API}/events?${q}`, {fresh: true});
    if (!a || !Array.isArray(a.occurrences)) return;     // not a calendar answer: the row stays just "Calendar"
    CAL_ROW.list = a.occurrences.filter(o => o.kind === 'event' && !o.all_day && o.response !== 'declined')
      .sort((x, y) => calWhen(x.start).getTime() - calWhen(y.start).getTime());
    // A calendar that would not answer is not an empty week: say nothing rather than "Nothing scheduled".
    CAL_ROW.failed = !!a.errors?.length;
    CAL_ROW.at = Date.now();
    // Calendar writes arrive live once the server has shown it serves calendars (an older one refuses the topic).
    if (!CAL_ROW.live) {
      const soon = () => liveSoon('cal-row', () => void calRowLoad(), 500);
      const offs = [liveOn('calendar', soon), liveOn('reset', soon)];
      CAL_ROW.live = () => { for (const off of offs) off(); };
      liveTopicAdd('calendar');
    }
  } catch (e) {
    // A server without calendars (404): the row is just "Calendar", and nothing asks again.
    if ((e as {status?: number}).status === 404) { CAL_ROW.off = true; CAL_ROW.list = []; }
  } finally {
    CAL_ROW.loading = false;
  }
  calRowDraw();
}
// Called on every route (router.ts, `home`: a new page starts again at the very next event) and on focus: reads when
// the last read is older than the refresh.
function calRowSync(home = false): void {
  if (home && CAL_ROW.pick) { CAL_ROW.pick = null; calRowDraw(); }
  // The first read may add the `calendar` topic: the first live stream waits for it (live.ts liveProbe).
  if (Date.now() - CAL_ROW.at >= CAL_ROW_EVERY) { const read = calRowLoad(); if (!CAL_ROW.at) liveProbe(read); }
}

// The events still to come in the next week, the one under way first. The week is today and the six days after it, so a
// weekday on the line ("Wed 09:00") never means today's.
function calRowUpcoming(now: Date): CalOccurrence[] {
  const t = now.getTime(), until = calAddDays(calMidnight(now), CAL_ROW_DAYS).getTime();
  return CAL_ROW.list.filter(o => calWhen(o.end).getTime() > t && calWhen(o.start).getTime() < until);
}
// When it starts, as the line says it: "now", "in 3 min", "15:30", "tomorrow 09:00", "Thu 10:00".
function calRowWhen(o: CalOccurrence, now: Date): string {
  const s = calWhen(o.start), ms = s.getTime() - now.getTime();
  if (ms <= 0) return 'now';
  if (ms < 60 * 60 * 1000) return `in ${Math.ceil(ms / 60000)} min`;
  const days = Math.round((calMidnight(s).getTime() - calMidnight(now).getTime()) / 86400e3);
  if (days === 0) return calClock(s);
  if (days === 1) return `tomorrow ${calClock(s)}`;
  return `${s.toLocaleDateString(undefined, {weekday: 'short'})} ${calClock(s)}`;
}

// `dir` slides the new event in from the side it came from (› from the right, ‹ from the left).
function calRowDraw(dir = 0): void {
  const row = calRowEl(); if (!row) return;
  const next = row.querySelector<HTMLElement>('[data-cal-next]');
  if (!next) return;
  if (CAL_ROW.off || !CAL_ROW.at) { next.innerHTML = ''; row.classList.remove('cal-on'); return; }
  row.classList.add('cal-on');
  const now = new Date(), up = calRowUpcoming(now);
  if (!up.length) { CAL_ROW.pick = null; next.innerHTML = CAL_ROW.failed ? '' : '<span class="cal-nx cal-none">Nothing scheduled</span>'; return; }
  let i = CAL_ROW.pick ? up.findIndex(o => o.occurrence_id === CAL_ROW.pick) : 0;
  if (i < 0) { i = 0; CAL_ROW.pick = null; }
  const o = up[i]!, when = calRowWhen(o, now);
  // The minute's redraw replaces the buttons: a keyboard user's focus stays on the same control.
  const focused = document.activeElement instanceof HTMLElement && next.contains(document.activeElement)
    ? document.activeElement.dataset.calStep || 'event' : null;
  const step = (d: number, label: string, glyph: string) => `<button type="button" class="cal-step" data-cal-step="${d}" aria-label="${label}">${glyph}</button>`;
  next.innerHTML = (i > 0 ? step(-1, 'Previous event', '‹') : '')
    + `<a class="cal-nx${dir > 0 ? ' cal-in-r' : dir < 0 ? ' cal-in-l' : ''}" href="${esc(calOpenHref(o))}" title="${esc(o.title)} · ${esc(when)}">`
    + `<span class="cal-nm">${esc(o.title)}</span><span class="cal-tm">· ${esc(when)}</span></a>`
    + (i < up.length - 1 ? step(1, 'Next event', '›') : '');
  if (focused) (next.querySelector<HTMLElement>(focused === 'event' ? '.cal-nx' : `[data-cal-step="${focused}"]`) ?? next.querySelector<HTMLElement>('.cal-nx'))?.focus();
}
function calRowStep(d: number): void {
  const up = calRowUpcoming(new Date());
  const i = CAL_ROW.pick ? Math.max(0, up.findIndex(o => o.occurrence_id === CAL_ROW.pick)) : 0;
  const to = Math.max(0, Math.min(up.length - 1, i + d));
  if (to === i) return;
  CAL_ROW.pick = to === 0 ? null : up[to]!.occurrence_id;
  calRowDraw(d);
}

(() => {
  const row = calRowEl(); if (!row) return;
  row.addEventListener('click', ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-cal-step]');
    if (b) { ev.preventDefault(); calRowStep(Number(b.dataset.calStep)); }
  });
  window.addEventListener('focus', () => calRowSync());
  // "in 3 min" counts down and turns to "now" each minute; a read every three while the window is shown.
  setInterval(() => { if (document.hidden) return; if (Date.now() - CAL_ROW.at >= CAL_ROW_EVERY) void calRowLoad(); else calRowDraw(); }, 60 * 1000);
})();
