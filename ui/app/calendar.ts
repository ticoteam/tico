/* ui/app/calendar.ts — The Calendar page (#/calendar; spec 2026-11-10-calendar §12.3-12.5, docs/dev/calendar-contract.md).
   A standard week grid that opens on the viewer alone, with Day, 3 days, Month and List. Colour says who (each calendar
   in view has a hue), shape says what, and nothing is drawn as an outline: an event is a solid block with a strong left
   edge, a bot run a lighter solid tint of its bot's colour (striped until it starts), a pill in the top row something
   due, a hatched block Busy. One header line, no title (spec calendar-page-cleanup): Today, the arrows, the range, the
   chips and Add (people, bots, "Bots that report to me", "Everyone" and what to show), then search folded to an icon,
   the view switch and ⋯. The search box adds calendars, finds events, suggests narrowing words and hands the words to
   the Assistant. People only view here (decision 13): Google events open in Google.
   Address: #/calendar?who=<refs>&view=day|3day|week|month|list&date=YYYY-MM-DD&q=<words>&open=<occurrence id>
   Shapes, Join and the time helpers are in calendar-row.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type CalViewName = 'day' | '3day' | 'week' | 'month' | 'list';
type CalToggle = 'event' | 'run' | 'due';
// One thing drawn: an occurrence, the calendars in view it is on (a shared event is drawn once), and for a block of
// short runs the runs it holds.
interface CalItem { key: string; o: CalOccurrence; refs: string[]; s: Date; e: Date; runs?: CalOccurrence[]; }
interface CalDrop { cals: CalView[]; events: CalOccurrence[]; i: number; open: boolean; calSeq: number; evSeq: number; }
// The Add menu: whether it is open, the words in its box and the people and bots it lists (read once it first opens).
interface CalAdd { open: boolean; q: string; cals: CalView[] | null; }
interface CalPage {
  run: number; who: string[]; view: CalViewName; date: string; q: string; open: string;
  data: CalEventsAnswer | null; error: string; loaded: number; card: string; drop: CalDrop; add: CalAdd;
  items: Map<string, CalItem>; hues: Map<string, number>;
}
const CAL_VIEWS: [CalViewName, string][] = [['day', 'Day'], ['3day', '3 days'], ['week', 'Week'], ['month', 'Month'], ['list', 'List']];
const CAL_TOGGLES: [CalToggle, string][] = [['event', 'Events'], ['run', 'Runs'], ['due', 'Due']];
const CAL_HUES = [152, 214, 278, 28, 338, 188, 52, 0, 248, 104];
const CAL_WORDS: [string, string][] = [['with:', 'with:'], ['bot:', 'bot:'], ['kind:', 'kind:'], ['before:', 'before:'], ['after:', 'after:'],
  ['has:transcript', 'has:transcript'], ['automation:', 'automation:'], ['trigger:', 'trigger:'], ['longer:', 'longer:10m']];
const CAL_KINDS_KEY = 'tico.calendar.kinds';
const CAL_WHO_MAX = 20;           // the server reads at most this many calendars at once (calendar_api.py WHO_MAX)
const CAL_HOUR = 44;              // px per hour in Day and Week
const CAL_SHORT_RUN = 10;         // runs shorter than this that touch group into one block (spec §9)
let CALP: CalPage | null = null;

function calKinds(): Record<CalToggle, boolean> {
  const on = {event: true, run: true, due: true};
  try {
    const off = (localStorage.getItem(CAL_KINDS_KEY) || '').split(',');
    for (const [k] of CAL_TOGGLES) if (off.includes(k)) on[k] = false;
  } catch { /* all on */ }
  return on;
}
function calKindsSet(k: CalToggle, shown: boolean): void {
  const on = calKinds(); on[k] = shown;
  try { localStorage.setItem(CAL_KINDS_KEY, CAL_TOGGLES.filter(([t]) => !on[t]).map(([t]) => t).join(',')); } catch { /* not kept */ }
}
const calPhone = () => matchMedia('(max-width:760px)').matches;
const calMe = () => 'human:' + String(S.me?.id || '');
const calNorm = (ref: string) => ref === 'me' ? calMe() : ref;

// ----------------------------------------------------------------- the address
function calParse(r: string): Pick<CalPage, 'who' | 'view' | 'date' | 'q' | 'open'> {
  const q = new URLSearchParams(r.split('?')[1] || '');
  const who = (q.get('who') || '').split(',').map(s => s.trim()).filter(Boolean).map(calNorm);
  const view = CAL_VIEWS.some(([v]) => v === q.get('view')) ? q.get('view') as CalViewName : calPhone() ? 'list' : 'week';
  const date = /^\d{4}-\d{2}-\d{2}$/.test(q.get('date') || '') ? q.get('date') || '' : calDay(new Date());
  return {who: who.length ? [...new Set(who)].slice(0, CAL_WHO_MAX) : [calMe()], view, date, q: q.get('q') || '', open: q.get('open') || ''};
}
function calHref(p: CalPage): string {
  const q = new URLSearchParams();
  if (!(p.who.length === 1 && p.who[0] === calMe())) q.set('who', p.who.map(r => r === calMe() ? 'me' : r).join(','));
  if (p.view !== (calPhone() ? 'list' : 'week')) q.set('view', p.view);
  if (p.date !== calDay(new Date())) q.set('date', p.date);
  if (p.q) q.set('q', p.q);
  const s = q.toString();
  return CAL_ROUTE + (s ? '?' + s : '');
}
// The page writes its own address (no new history entry, no redraw from the router).
function calWrite(p: CalPage): void {
  const h = calHref(p);
  if (location.hash !== h) { history.replaceState(history.state, '', h); S.route = h; }
}

// ----------------------------------------------------------------- the window
const calWeekStart = (d: Date) => calAddDays(calMidnight(d), -((d.getDay() + 6) % 7));
function calWindow(view: CalViewName, date: string): [Date, Date] {
  const d = calWhen(date);
  if (view === 'day') return [calMidnight(d), calAddDays(d, 1)];
  if (view === '3day') return [calMidnight(d), calAddDays(d, 3)];
  if (view === 'month') { const first = new Date(d.getFullYear(), d.getMonth(), 1), s = calWeekStart(first); return [s, calAddDays(s, 42)]; }
  const s = calWeekStart(d); return [s, calAddDays(s, 7)];
}
function calStep(p: CalPage, dir: number): string {
  const d = calWhen(p.date);
  if (p.view === 'day') return calDay(calAddDays(d, dir));
  if (p.view === '3day') return calDay(calAddDays(d, 3 * dir));
  if (p.view === 'month') return calDay(new Date(d.getFullYear(), d.getMonth() + dir, 1));
  return calDay(calAddDays(d, 7 * dir));
}
function calTitle(p: CalPage): string {
  const d = calWhen(p.date), [s, e] = calWindow(p.view, p.date), last = calAddDays(e, -1);
  const md = (x: Date) => x.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  if (p.view === 'day') return d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
  if (p.view === 'month') return d.toLocaleDateString(undefined, {month: 'long', year: 'numeric'});
  return s.getMonth() === last.getMonth() ? `${md(s)} – ${last.getDate()}` : `${md(s)} – ${md(last)}`;
}

// ----------------------------------------------------------------- page and load
function calendarRoute(r: string): boolean {
  if (!rdUnder((r || '').split('?')[0] ?? '', CAL_ROUTE)) return false;
  pageCalendar(r);
  return true;
}
function calendarStop(r: string): void {
  if (rdUnder((r || '').split('?')[0] ?? '', CAL_ROUTE)) return;
  CALP = null; calCardClose();
}
function pageCalendar(r: string): void {
  const want = calParse(r), root = document.getElementById('cal-root');
  pageTitle('Calendar');
  $('#main').classList.add('cal-layout');
  if (CALP && root) {
    // The same page with a new address (a sidebar link, Back): keep the shell and the box, read again if the window moved.
    const before = calWindow(CALP.view, CALP.date).map(d => d.getTime()).join(), whoBefore = CALP.who.join();
    Object.assign(CALP, want);
    calHead(); calWrite(CALP);
    if (before !== calWindow(CALP.view, CALP.date).map(d => d.getTime()).join() || whoBefore !== CALP.who.join()) void calLoad();
    else calBody();
    return;
  }
  const run = (CALP?.run || 0) + 1;
  CALP = {run, ...want, data: null, error: '', loaded: 0, card: '', drop: {cals: [], events: [], i: 0, open: false, calSeq: 0, evSeq: 0},
    add: {open: false, q: '', cals: null}, items: new Map(), hues: new Map()};
  // One line, no title: the sidebar says where you are and the tab still says Calendar. A phone takes two lines.
  $('#main').innerHTML = `<div class="cal" id="cal-root">
    <header class="cal-h">
      <button type="button" class="ghost small cal-today" data-cal-today>Today</button>
      <span class="cal-nav"><button type="button" class="ghost small cal-ib" data-cal-step="-1" aria-label="Previous"><span class="nav-icon" aria-hidden="true">chevron_left</span></button><button type="button" class="ghost small cal-ib" data-cal-step="1" aria-label="Next"><span class="nav-icon" aria-hidden="true">chevron_right</span></button></span>
      <b class="cal-range" data-cal-range></b>
      <i class="cal-br" aria-hidden="true"></i>
      <div class="cal-chips" data-cal-chips></div>
      <div class="cal-add"><button type="button" class="ghost small cal-add-b" data-cal-add aria-haspopup="dialog" aria-expanded="false"><span class="nav-icon" aria-hidden="true">add</span>Add</button>
        <div class="cal-addm" role="dialog" aria-label="Add people and bots" data-cal-addm hidden></div></div>
      <div class="cal-sr" role="combobox" aria-expanded="false" aria-haspopup="listbox" aria-owns="cal-dd"><button type="button" class="ghost small cal-ib cal-sr-ic" data-cal-find aria-label="Search (/)" title="Search (/)"><span class="nav-icon" aria-hidden="true">search</span></button>
        <input type="search" id="cal-q" placeholder="Name, event, with:" autocomplete="off" spellcheck="false" aria-label="Search calendars and events" aria-controls="cal-dd" aria-autocomplete="list">
        <div class="cal-dd" id="cal-dd" role="listbox" hidden></div></div>
      <div class="theme-seg cal-views" role="radiogroup" aria-label="View" data-cal-views></div>
      <div class="cal-more"><button type="button" class="ghost small cal-ib" data-cal-more aria-label="More" aria-haspopup="menu" aria-expanded="false"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>
        <div class="cal-menu" role="menu" data-cal-menu hidden><button type="button" role="menuitemcheckbox" data-cal-record aria-checked="true">Record calls I join</button></div></div>
    </header>
    <div class="cal-strip" data-cal-strip></div>
    <div class="cal-body" data-cal-body>${skelRows(6)}</div>
  </div>`;
  const q = document.querySelector<HTMLInputElement>('#cal-q');
  if (q) q.value = CALP.q;
  if (CALP.q) calFindOpen(false);
  calBind();
  calHead(); calWrite(CALP);
  void calLoad();
  void calSettingsRead();
}
async function calLoad(): Promise<void> {
  const p = CALP; if (!p) return;
  const run = p.run, [from, to] = calWindow(p.view, p.date), key = p.who.join() + from.toISOString();
  const q = new URLSearchParams({who: p.who.map(r => r === calMe() ? 'me' : r).join(','), from: from.toISOString(), to: to.toISOString()});
  try {
    const a = await get<CalEventsAnswer>(`${CAL_API}/events?${q}`, {fresh: true});
    if (CALP !== p || p.run !== run || key !== p.who.join() + calWindow(p.view, p.date)[0].toISOString()) return;
    p.data = {...a, calendars: a.calendars || [], occurrences: Array.isArray(a.occurrences) ? a.occurrences : []};
    p.error = ''; p.loaded = Date.now();
    liveTopicAdd('calendar');      // this server serves calendars, so it takes the topic
  } catch (e) {
    if (CALP !== p || p.run !== run) return;
    p.error = errorMessage(e, 'Could not read the calendar');
  }
  calHues(p);
  calHead();
  calBody();
  if (p.open) { const id = p.open; p.open = ''; calOpenById(id); }
}
// Hues: the calendars in view in order, then anyone else drawn (an attendee's calendar).
function calHues(p: CalPage): void {
  p.hues = new Map();
  for (const c of p.data?.calendars || []) if (!p.hues.has(c.ref)) p.hues.set(c.ref, p.hues.size);
  for (const o of p.data?.occurrences || []) if (!p.hues.has(o.calendar.ref)) p.hues.set(o.calendar.ref, p.hues.size);
}
const calHue = (ref: string) => CAL_HUES[(CALP?.hues.get(ref) ?? 0) % CAL_HUES.length] ?? 152;
async function calSettingsRead(): Promise<void> {
  try {
    const s = await get<CalSettings>(`${CAL_API}/settings`);
    if (typeof s?.record_on_join === 'boolean') { calRecordSet(s.record_on_join); calMenuDraw(); }
  } catch { /* the browser's own choice stands */ }
}

// ----------------------------------------------------------------- the header
function calHead(): void {
  const p = CALP, root = document.getElementById('cal-root'); if (!p || !root) return;
  const set = (sel: string, html: string) => { const el = root.querySelector<HTMLElement>(sel); if (el) el.innerHTML = html; };
  set('[data-cal-views]', CAL_VIEWS.map(([v, label]) => `<button type="button" role="radio" aria-checked="${p.view === v}" data-cal-view="${v}">${label}</button>`).join(''));
  set('[data-cal-range]', esc(calTitle(p)));
  const views = p.data?.calendars?.length ? p.data.calendars : p.who.map(ref => ({ref, type: ref.split(':')[0] || '', id: ref.split(':')[1] || '', name: calRefName(ref)}));
  set('[data-cal-chips]', views.map(c => `<span class="cal-chip" style="--h:${calHue(c.ref)}" data-cal-chip="${esc(c.ref)}"><span class="cal-chip-n">${esc(c.name || calRefName(c.ref))}</span>${
    views.length > 1 || c.ref !== calMe() ? `<button type="button" data-cal-rm="${esc(c.ref)}" aria-label="Remove ${esc(c.name)}">×</button>` : ''}</span>`).join('')
    + '<button type="button" class="cal-chip cal-chip-more" data-cal-chips-more aria-label="More calendars in view" hidden></button>');
  calChipsFit();
  calAddDraw();
  // Phone: a week strip over the list.
  const [s] = calWindow('week', p.date);
  set('[data-cal-strip]', Array.from({length: 7}, (_, i) => calAddDays(s, i)).map(d => `<button type="button" data-cal-date="${calDay(d)}" aria-pressed="${calDay(d) === p.date}"${calDay(d) === calDay(new Date()) ? ' class="today"' : ''}><span>${esc(d.toLocaleDateString(undefined, {weekday: 'narrow'}))}</span><b>${d.getDate()}</b></button>`).join(''));
  calMenuDraw();
}
// A calendar named by its ref before the server answers: the roster's name for a person or bot.
function calRefName(ref: string): string {
  const [type = '', id = ''] = ref.split(':');
  if (ref === calMe()) return String(S.me?.name || 'Me');
  if (type === 'bot') return botDisplayName(id);
  if (type === 'human') return personDisplay(id);
  return id;
}
function calMenuDraw(): void {
  const b = document.querySelector<HTMLElement>('#cal-root [data-cal-record]');
  if (b) b.setAttribute('aria-checked', String(calRecordOn()));
}

// Chips past the header's one line fold into "+N", which opens Add (where each can be unticked). A phone scrolls them.
function calChipsFit(): void {
  const box = document.querySelector<HTMLElement>('#cal-root [data-cal-chips]'), more = box?.querySelector<HTMLElement>('[data-cal-chips-more]');
  if (!box || !more) return;
  const chips = [...box.querySelectorAll<HTMLElement>('[data-cal-chip]')];
  for (const c of chips) c.hidden = false;
  more.hidden = true;
  if (calPhone() || box.scrollWidth <= box.clientWidth) return;
  more.hidden = false; more.textContent = '+' + chips.length;      // its widest, so the room is kept for it
  let hidden = 0;
  for (let i = chips.length - 1; i > 0 && box.scrollWidth > box.clientWidth; i--) { chips[i]!.hidden = true; hidden++; }
  more.textContent = '+' + hidden;
}
window.addEventListener('resize', () => { if (CALP) calChipsFit(); });

// ----------------------------------------------------------------- Add: people, bots and what to show
// The bots that report to the viewer directly, as refs (the rule the server used to apply to "Me" on its own).
const calMyBots = (): string[] => (S.emps || []).filter(e => e.reports_to === calMe() && e.status !== 'archived' && e.state !== 'archived').map(e => 'bot:' + e.id);
// Everyone Add can list: the server's people and bots, the viewer first; the roster stands in until it answers.
function calAddList(p: CalPage): CalView[] {
  const roster: CalView[] = [
    ...(S.people || []).map(x => ({ref: 'human:' + x.id, type: 'human', id: String(x.id), name: String(x.name || x.id)})),
    ...(S.emps || []).filter(e => e.status !== 'archived' && e.state !== 'archived').map(e => ({ref: 'bot:' + e.id, type: 'bot', id: e.id, name: botDisplayName(e.id)})),
  ];
  const listed = (p.add.cals || roster).filter(c => c.type === 'human' || c.type === 'bot'), me = calMe();
  const mine = listed.find(c => c.ref === me) || {ref: me, type: 'human', id: me.slice(6), name: calRefName(me)};
  return [mine, ...listed.filter(c => c.ref !== me)];
}
async function calAddRead(p: CalPage): Promise<void> {
  if (p.add.cals) return;
  try {
    const a = await get<{calendars: CalView[]}>(`${CAL_API}/calendars`);
    if (CALP !== p) return;
    p.add.cals = (a.calendars || []).filter(c => c.type === 'human' || c.type === 'bot');
  } catch { return; }      // the roster stands in
  calAddDraw();
}
// The menu's box is drawn once when it opens (it keeps its words and focus); the rows under it redraw.
function calAddOpen(open: boolean): void {
  const p = CALP, root = document.getElementById('cal-root'); if (!p || !root) return;
  const menu = root.querySelector<HTMLElement>('[data-cal-addm]'), btn = root.querySelector<HTMLElement>('[data-cal-add]'); if (!menu || !btn) return;
  p.add.open = open; menu.hidden = !open; btn.setAttribute('aria-expanded', String(open));
  if (!open) { menu.innerHTML = ''; p.add.q = ''; return; }
  menu.innerHTML = `<label class="cal-addm-sr"><span class="nav-icon" aria-hidden="true">search</span><input type="search" data-cal-addq placeholder="Find a person or bot" autocomplete="off" spellcheck="false" aria-label="Find a person or bot"></label>
    <div data-cal-addrows></div>`;
  calAddDraw();
  menu.querySelector<HTMLInputElement>('[data-cal-addq]')?.focus({preventScroll: true});
  void calAddRead(p);
}
function calAddDraw(): void {
  const p = CALP, host = document.querySelector<HTMLElement>('#cal-root [data-cal-addrows]'); if (!p || !host || !p.add.open) return;
  const words = p.add.q.trim().toLowerCase(), mine = calMyBots(), on = calKinds(), everyone = calAddList(p);
  const full = p.who.length >= CAL_WHO_MAX, tooMany = everyone.length > CAL_WHO_MAX;
  // "Bots that report to me" adds them all or none: a choice that would leave some out is off, and the note says why.
  const missing = mine.filter(r => !p.who.includes(r)), mineFits = missing.length > 0 && missing.length <= CAL_WHO_MAX - p.who.length;
  const crowded = full || (missing.length > 0 && !mineFits);
  const rows = everyone.filter(c => !words || c.name.toLowerCase().split(/\s+/).some(w => w.startsWith(words)) || c.name.toLowerCase().startsWith(words));
  const quick = words ? '' : `<div class="cal-addm-quick">
      <button type="button" data-cal-quick="mine"${mineFits ? '' : ' disabled'}><span class="nav-icon" aria-hidden="true">smart_toy</span><span class="l">Bots that report to me</span><span class="n" data-cal-quick-n>${mine.length}</span></button>
      <button type="button" data-cal-quick="all"${tooMany || full ? ' disabled' : ''}><span class="nav-icon" aria-hidden="true">groups</span><span class="l">Everyone</span></button>
      ${tooMany || crowded ? `<p class="cal-addm-note">At most ${CAL_WHO_MAX} calendars show at a time${crowded ? ': untick some to add more' : ''}.</p>` : ''}</div>`;
  const list = rows.map(c => {
    const ticked = p.who.includes(c.ref);
    return `<label class="cal-addm-row"><input type="checkbox" data-cal-pick="${esc(c.ref)}"${ticked ? ' checked' : full ? ' disabled' : ''}><i class="cal-cd${ticked ? '' : ' off'}" style="--h:${calHue(c.ref)}" aria-hidden="true"></i><span class="l">${esc(c.ref === calMe() ? `${c.name} (me)` : c.name)}</span>${c.type === 'bot' ? '<span class="m">Bot</span>' : ''}</label>`;
  }).join('') || '<p class="cal-addm-none">No one by that name</p>';
  const show = `<div class="cal-addm-h">Show</div><div class="cal-lays" role="group" aria-label="Show">${CAL_TOGGLES.map(([k, label]) =>
    `<button type="button" aria-pressed="${on[k]}" data-cal-lay="${k}"><i class="cal-gl ${k}" aria-hidden="true"></i>${label}</button>`).join('')}</div>`;
  // Redrawn under the pointer: the control that had focus keeps it, so the keyboard and Escape still work.
  const had = host.contains(document.activeElement) ? document.activeElement as HTMLElement : null;
  const again = had ? ['calPick', 'calQuick', 'calLay'].map(k => [k, had.dataset[k]] as const).find(([, v]) => v) : undefined;
  host.innerHTML = `${quick}<div class="cal-addm-list" role="group" aria-label="People and bots">${list}</div>${show}`;
  if (again) {
    const attr = again[0].replace(/[A-Z]/g, c => '-' + c.toLowerCase());
    host.querySelector<HTMLElement>(`[data-${attr}="${CSS.escape(again[1] || '')}"]`)?.focus({preventScroll: true});
  }
}
// Add these calendars to the view (or, for one unticked, take it away), then read again.
function calWhoSet(p: CalPage, who: string[]): void {
  p.who = who.length ? [...new Set(who)].slice(0, CAL_WHO_MAX) : [calMe()];
  calCardClose(); calWrite(p); calHead(); void calLoad();
}
// Search folds to an icon; the icon and "/" open the same box with the cursor in it.
function calFindOpen(focus = true): void {
  const sr = document.querySelector<HTMLElement>('#cal-root .cal-sr'); if (!sr) return;
  sr.classList.add('open'); calChipsFit();
  if (focus) document.querySelector<HTMLInputElement>('#cal-q')?.focus();
}
function calFindFold(): void {
  const input = document.querySelector<HTMLInputElement>('#cal-q');
  if (input && !input.value.trim() && document.activeElement !== input) { document.querySelector('#cal-root .cal-sr')?.classList.remove('open'); calChipsFit(); }
}

// ----------------------------------------------------------------- what is drawn
// The occurrences in view as items: hidden kinds left out, a shared event once with every calendar it is on, and on
// Day and Week the short runs that touch grouped into one block.
function calBuild(p: CalPage, group: boolean): CalItem[] {
  const on = calKinds(), occ = p.data?.occurrences || [], out = new Map<string, CalItem>();
  for (const o of occ) {
    const k = o.kind === 'away' ? 'event' : o.kind;
    if (!on[k as CalToggle]) continue;
    const key = o.kind === 'event' && !o.busy_only ? `ev|${o.event_id || o.occurrence_id}|${calWhen(o.start).getTime()}` : o.occurrence_id;
    const had = out.get(key);
    if (had) { if (!had.refs.includes(o.calendar.ref)) had.refs.push(o.calendar.ref); continue; }
    out.set(key, {key, o, refs: [o.calendar.ref], s: calWhen(o.start), e: calWhen(o.end)});
  }
  // The colour is the first calendar in view the item is on.
  const order = (p.data?.calendars || []).map(c => c.ref);
  for (const it of out.values()) it.refs.sort((a, b) => calOrder(order, a) - calOrder(order, b));
  let items = [...out.values()];
  // A block the server already grouped lists the runs it was sent as well.
  for (const it of items) if (it.o.count && it.o.children?.length) it.runs = occ.filter(o => it.o.children?.includes(o.occurrence_id));
  if (group) items = calGroupRuns(items);
  p.items = new Map(items.map(it => [it.key, it]));
  for (const it of items) for (const r of it.runs || []) p.items.set(r.occurrence_id, {key: r.occurrence_id, o: r, refs: [r.calendar.ref], s: calWhen(r.start), e: calWhen(r.end)});
  return items;
}
const calOrder = (order: string[], ref: string) => { const i = order.indexOf(ref); return i < 0 ? order.length : i; };
function calGroupRuns(items: CalItem[]): CalItem[] {
  const short = (it: CalItem) => it.o.kind === 'run' && !it.o.count && !it.o.all_day && (it.e.getTime() - it.s.getTime()) < CAL_SHORT_RUN * 60000;
  const rest = items.filter(it => !short(it)), byCal = new Map<string, CalItem[]>();
  for (const it of items.filter(short)) { const k = it.o.calendar.ref + '|' + calDay(it.s); byCal.set(k, [...(byCal.get(k) || []), it]); }
  for (const list of byCal.values()) {
    list.sort((a, b) => a.s.getTime() - b.s.getTime());
    let cluster: CalItem[] = [], end = 0;
    const flush = () => {
      const first = cluster[0];
      if (!first) return;
      if (cluster.length === 1) rest.push(first);
      else {
        const e = new Date(Math.max(...cluster.map(c => c.e.getTime())));
        const o: CalOccurrence = {...first.o, occurrence_id: `runs:${first.o.calendar.id}:${first.s.toISOString()}`, title: `${cluster.length} runs`,
          end: e.toISOString(), count: cluster.length, children: cluster.map(c => c.o.occurrence_id), links: null, planned: false};
        rest.push({key: o.occurrence_id, o, refs: first.refs, s: first.s, e, runs: cluster.map(c => c.o)});
      }
      cluster = [];
    };
    for (const it of list) {
      // Touching or overlapping (a minute's gap still touches at this scale).
      if (cluster.length && it.s.getTime() > end + 60000) flush();
      cluster.push(it); end = Math.max(cluster.length === 1 ? 0 : end, it.e.getTime());
    }
    flush();
  }
  return rest;
}
// The label a block carries: a run on a bot not in view says whose it is.
function calLabel(it: CalItem): string {
  const o = it.o;
  if (o.busy_only) return 'Busy';
  if (o.kind === 'away') return `${o.calendar.name} away`;
  const inView = (CALP?.data?.calendars || []).some(c => c.ref === o.calendar.ref);
  return o.kind === 'run' && !inView ? `${o.calendar.name}: ${o.title}` : o.title;
}
// Other calendars in view this item is on, as dots (the item takes the first one's colour).
function calDots(it: CalItem, col?: string): string[] {
  const views = CALP?.data?.calendars || [], own = col || it.refs[0];
  const on = new Set(it.refs);
  for (const a of it.o.attendees || []) for (const c of views) if (a.id && a.type === c.type && a.id === c.id && a.response !== 'declined') on.add(c.ref);
  return views.map(c => c.ref).filter(r => on.has(r) && r !== own);
}
// Search narrowing on the grid: matches stay bright, the rest fades. Words are read as the server reads them.
function calMatch(it: CalItem, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const o = it.o, names = [o.calendar.name, ...(o.attendees || []).map(a => a.name || a.email || '')].map(n => String(n).toLowerCase());
  const title = o.busy_only ? '' : String(o.title).toLowerCase(), mins = (it.e.getTime() - it.s.getTime()) / 60000;
  return words.every(w => {
    const m = /^([a-z]+):(.*)$/.exec(w), [, k = '', v = ''] = m || [];
    if (!m) return title.includes(w) || names.some(n => n.split(/\s+/).some(part => part.startsWith(w)));
    if (!v && k !== 'has') return true;
    if (k === 'with') return names.some(n => n.startsWith(v) || n.split(/\s+/).some(part => part.startsWith(v)));
    if (k === 'bot') return o.calendar.type === 'bot' && o.calendar.name.toLowerCase().startsWith(v);
    if (k === 'kind') return o.kind.startsWith(v);
    if (k === 'has') return v === 'transcript' ? !!o.links?.meeting : true;
    if (k === 'before') return it.s.getTime() < calWhen(v).getTime();
    if (k === 'after') return it.s.getTime() >= calWhen(v).getTime();
    if (k === 'longer') return mins > (parseInt(v, 10) || 0);
    if (k === 'automation') return !!o.links?.automation;
    return true;
  });
}

// ----------------------------------------------------------------- the body
function calBody(): void {
  const p = CALP, body = document.querySelector<HTMLElement>('#cal-root [data-cal-body]'); if (!p || !body) return;
  if (!p.data) { if (p.error) body.innerHTML = `<p class="err">${esc(p.error)}</p>`; return; }
  const errs = (p.data.errors || []).map(e => `<p class="err cal-err">${esc(calRefName(e.ref))}: ${esc(e.reason)}</p>`).join('');
  const html = p.view === 'month' ? calMonth(p) : p.view === 'list' ? calList(p) : calGrid(p);
  body.innerHTML = (p.error ? `<p class="err">${esc(p.error)}</p>` : '') + errs + html;
  body.dataset.view = p.view;
  if (p.view === 'day' || p.view === '3day' || p.view === 'week') {
    // Open on the working day: the first hour shown is the top, and now is in view when it is today.
    const now = body.querySelector<HTMLElement>('.cal-nowl');
    const scroller = body.querySelector<HTMLElement>('.cal-scroll');
    if (scroller && now && !body.dataset.scrolled) { scroller.scrollTop = Math.max(0, now.offsetTop - 160); body.dataset.scrolled = '1'; }
  }
}
// The hours Day and Week show: working hours of the calendars in view, stretched for anything earlier or later.
function calHours(p: CalPage, items: CalItem[]): [number, number] {
  const cals = p.data?.calendars || [];
  let lo = Math.min(...(cals.length ? cals.map(c => calMinutes(c.work_starts, 480)) : [480]));
  let hi = Math.max(...(cals.length ? cals.map(c => calMinutes(c.work_ends, 1080)) : [1080]));
  for (const it of items) {
    if (it.o.all_day || it.o.kind === 'due' || it.o.kind === 'away') continue;
    const s = calMinuteOf(it.s), sameDay = calDay(it.s) === calDay(it.e);
    lo = Math.min(lo, s); hi = Math.max(hi, sameDay ? calMinuteOf(it.e) : 1440);
  }
  return [Math.floor(lo / 60) * 60, Math.min(1440, Math.ceil(hi / 60) * 60)];
}
const calTop = (it: CalItem) => !!it.o.all_day || it.o.kind === 'due' || it.o.kind === 'away';
function calGrid(p: CalPage): string {
  const items = calBuild(p, true), [lo, hi] = calHours(p, items), cals = p.data?.calendars || [];
  const [s, e] = calWindow(p.view, p.date), today = calDay(new Date());
  // Columns: the days, or on Day with two or more calendars, the calendars side by side.
  type Col = {day: Date; ref: string; head: string; today: boolean};
  let cols: Col[];
  if (p.view === 'day' && cals.length >= 2) cols = cals.map(c => ({day: s, ref: c.ref, head: `<span class="cal-cd" style="--h:${calHue(c.ref)}"></span>${esc(c.name)}`, today: false}));
  else {
    const days: Date[] = [];
    for (let d = s; d < e; d = calAddDays(d, 1)) days.push(d);
    // Week shows the working days, and a weekend day only when something is on it; Day and 3 days show every day.
    const work = (cals[0]?.work_days || 'mon,tue,wed,thu,fri').split(',');
    const wd = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
    const shown = p.view !== 'week' ? days : days.filter(d => work.includes(wd[d.getDay()] || '') || items.some(it => calOn(it, d)));
    cols = shown.map(d => ({day: d, ref: '', today: calDay(d) === today,
      head: `${esc(d.toLocaleDateString(undefined, {weekday: 'short'}))} <span>${d.getDate()}</span>`}));
  }
  const n = cols.length, span = hi - lo, px = (m: number) => (m - lo) / 60 * CAL_HOUR;
  const inCol = (it: CalItem, c: Col) => calOn(it, c.day) && (!c.ref || it.refs.includes(c.ref) || calDots(it, c.ref).length < calDots(it).length + (it.refs[0] === c.ref ? 0 : 1) && calAttends(it, c.ref));
  const top = cols.map(c => `<div class="cal-adc">${items.filter(it => calTop(it) && inCol(it, c)).map(it => calPill(p, it, c.ref)).join('')}</div>`).join('');
  const gutter = `<div class="cal-gut" style="height:${px(hi)}px">${Array.from({length: span / 60 - 1}, (_, i) => `<span style="top:${(i + 1) * CAL_HOUR}px">${esc(calHourLabel(lo + (i + 1) * 60))}</span>`).join('')}</div>`;
  const free = p.view === 'day' && cals.length >= 2 ? calFree(p, items, s, lo, hi) : [];
  const nowMin = calMinuteOf(new Date());
  const colsHtml = cols.map(c => {
    const timed = items.filter(it => !calTop(it) && inCol(it, c));
    const freeHtml = free.map(([a, b], i) => `<i class="cal-free" style="top:${px(a)}px;height:${px(b) - px(a)}px">${i === 0 && c === cols[0] ? '' : ''}</i>`).join('');
    const nowHtml = (c.today || (c.ref && calDay(c.day) === today)) && nowMin >= lo && nowMin <= hi ? `<i class="cal-nowl" style="top:${px(nowMin)}px"></i>` : '';
    return `<div class="cal-col${c.today ? ' today' : ''}" style="height:${px(hi)}px">${freeHtml}${calBlocks(p, timed, c, lo)}${nowHtml}</div>`;
  }).join('');
  return `<div class="cal-grid" style="--n:${n}">
    <div class="cal-ghd"><div></div>${cols.map(c => `<div class="cal-chd${c.today ? ' today' : ''}">${c.head}</div>`).join('')}</div>
    <div class="cal-gad"><div></div>${top}</div>
    <div class="cal-scroll"><div class="cal-gbd">${gutter}${colsHtml}</div></div></div>`;
}
// Whether an attendee in view is this calendar (a shared event drawn in each person's column on Day).
function calAttends(it: CalItem, ref: string): boolean {
  const c = (CALP?.data?.calendars || []).find(x => x.ref === ref);
  return !!c && (it.o.attendees || []).some(a => a.id && a.type === c.type && a.id === c.id && a.response !== 'declined');
}
// Whether an item falls on a day (an all-day span covers each day up to its exclusive end).
function calOn(it: CalItem, day: Date): boolean {
  const d0 = calMidnight(day).getTime(), d1 = calAddDays(day, 1).getTime();
  if (it.o.all_day || it.o.kind === 'due') return it.s.getTime() < d1 && Math.max(it.e.getTime(), it.s.getTime() + 1) > d0;
  return it.s.getTime() < d1 && it.e.getTime() > d0 || it.s.getTime() === d0;
}
const calHourLabel = (m: number) => { const h = Math.floor(m / 60); return CAL_H12 ? String(h % 12 || 12) : calPad(h); };
// Timed blocks in one column, packed into lanes where they overlap.
function calBlocks(p: CalPage, list: CalItem[], c: {day: Date; ref: string}, lo: number): string {
  const d0 = calMidnight(c.day).getTime();
  const span = (it: CalItem): [number, number] => {
    const s = Math.max(0, (it.s.getTime() - d0) / 60000), e = Math.min(1440, (it.e.getTime() - d0) / 60000);
    return [s, Math.max(e, s + 15)];      // a short run is still a block you can point at
  };
  const rows = list.map(it => ({it, se: span(it), lane: 0, lanes: 1})).sort((a, b) => a.se[0] - b.se[0] || b.se[1] - a.se[1]);
  let cluster: typeof rows = [], lanes: number[] = [], end = -1;
  const flush = () => { for (const r of cluster) r.lanes = lanes.length; cluster = []; lanes = []; };
  for (const r of rows) {
    if (cluster.length && r.se[0] >= end) flush();
    let l = lanes.findIndex(x => x <= r.se[0]);
    if (l < 0) { l = lanes.length; lanes.push(r.se[1]); } else lanes[l] = r.se[1];
    r.lane = l; cluster.push(r); end = cluster.length === 1 ? r.se[1] : Math.max(end, r.se[1]);
  }
  flush();
  const now = Date.now();
  return rows.map(({it, se, lane, lanes: of}) => {
    const o = it.o, ref = c.ref || it.refs[0] || o.calendar.ref, dots = calDots(it, ref);
    const cls = ['cal-ev', o.kind, o.busy_only ? 'busy' : '', o.kind === 'run' && (o.planned || it.s.getTime() > now) ? 'planned' : '',
      it.runs ? 'grp' : '', calMatch(it, p.q) ? '' : 'dim', p.card === it.key ? 'sel' : ''].filter(Boolean).join(' ');
    const top = (se[0] - lo) / 60 * CAL_HOUR, h = (se[1] - se[0]) / 60 * CAL_HOUR, label = calLabel(it);
    const short = h < 24 ? ' short' : '';
    return `<button type="button" class="${cls}${short}" style="--h:${calHue(ref)};top:${top + 1}px;height:${Math.max(14, h - 2)}px;left:calc(${lane / of * 100}% + 2px);width:calc(${100 / of}% - 4px)" data-cal-item="${esc(it.key)}" title="${esc(label)} ${esc(calRange(o))}"><b>${esc(label)}</b>${
      h >= 34 && !o.all_day ? `<small>${esc(calRange(o))}</small>` : ''}${dots.length ? `<span class="cal-dots">${dots.map(r => `<i style="--h:${calHue(r)}"></i>`).join('')}</span>` : ''}</button>`;
  }).join('');
}
// A pill in the top row: due, away, an all-day event.
function calPill(p: CalPage, it: CalItem, col: string): string {
  const o = it.o, ref = col || it.refs[0] || o.calendar.ref;
  const cls = ['cal-pill', o.kind, o.busy_only ? 'busy' : '', calMatch(it, p.q) ? '' : 'dim', p.card === it.key ? 'sel' : ''].filter(Boolean).join(' ');
  return `<button type="button" class="${cls}" style="--h:${calHue(ref)}" data-cal-item="${esc(it.key)}" title="${esc(calLabel(it))}">${o.kind === 'due' ? '<span class="nav-icon" aria-hidden="true">task_alt</span>' : ''}<span>${esc(calLabel(it))}</span></button>`;
}
// Time every person in view is free within their working hours, in 15-minute steps, as [start, end] minutes.
function calFree(p: CalPage, items: CalItem[], day: Date, lo: number, hi: number): [number, number][] {
  const cals = p.data?.calendars || [], people = cals.filter(c => c.type === 'human').length ? cals.filter(c => c.type === 'human') : cals;
  const wd = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][day.getDay()] || '';
  const d0 = calMidnight(day).getTime(), out: [number, number][] = [];
  const busy = (c: CalView, m: number) => {
    if (!(c.work_days || 'mon,tue,wed,thu,fri').split(',').includes(wd)) return true;
    if (m < calMinutes(c.work_starts, 480) || m >= calMinutes(c.work_ends, 1080)) return true;
    const t = d0 + m * 60000;
    return items.some(it => (it.o.kind === 'event' || it.o.kind === 'away') && it.o.response !== 'declined'
      && (it.refs.includes(c.ref) || calAttends(it, c.ref)) && it.s.getTime() <= t && it.e.getTime() > t);
  };
  for (let m = lo; m < hi; m += 15) {
    const free = people.every(c => !busy(c, m)), last = out[out.length - 1];
    if (!free) continue;
    if (last && last[1] === m) last[1] = m + 15; else out.push([m, m + 15]);
  }
  return out;
}
function calMonth(p: CalPage): string {
  const items = calBuild(p, false), [s] = calWindow('month', p.date), month = calWhen(p.date).getMonth(), today = calDay(new Date());
  const counts = new Map<string, Map<string, number>>();
  if (calKinds().run) for (const c of p.data?.month_counts || []) {
    const day = counts.get(c.date) || new Map<string, number>(); day.set(c.bot_id, (day.get(c.bot_id) || 0) + c.count); counts.set(c.date, day);
  }
  const heads = Array.from({length: 7}, (_, i) => `<div class="cal-mhd">${esc(calAddDays(s, i).toLocaleDateString(undefined, {weekday: 'short'}))}</div>`).join('');
  const cells = Array.from({length: 42}, (_, i) => {
    const d = calAddDays(s, i), key = calDay(d), on = items.filter(it => calOn(it, d)).sort((a, b) => Number(calTop(b)) - Number(calTop(a)) || a.s.getTime() - b.s.getTime());
    const shown = on.slice(0, 3).map(it => calTop(it) ? calPill(p, it, '') : `<button type="button" class="cal-mev ${it.o.kind}${calMatch(it, p.q) ? '' : ' dim'}" style="--h:${calHue(it.refs[0] || it.o.calendar.ref)}" data-cal-item="${esc(it.key)}"><i></i><span class="t">${esc(calClock(it.s))}</span><span>${esc(calLabel(it))}</span></button>`).join('');
    const runs = [...(counts.get(key) || new Map<string, number>()).entries()].map(([bot, n]) =>
      `<button type="button" class="cal-mruns" style="--h:${calHue('bot:' + bot)}" data-cal-date="${key}" data-cal-to="day" title="${esc(botDisplayName(bot))}">${n} ${n === 1 ? 'run' : 'runs'}</button>`).join('');
    const more = on.length > 3 ? `<button type="button" class="cal-mmore" data-cal-date="${key}" data-cal-to="day">+${on.length - 3}</button>` : '';
    return `<div class="cal-mc${d.getMonth() !== month ? ' out' : ''}${key === today ? ' today' : ''}"><button type="button" class="cal-mn" data-cal-date="${key}" data-cal-to="day">${d.getDate()}</button>${shown}${runs}${more}</div>`;
  }).join('');
  return `<div class="cal-month">${heads}${cells}</div>`;
}
// List: the agenda, every run its own row.
function calList(p: CalPage): string {
  const items = calBuild(p, false).sort((a, b) => Number(calTop(b)) - Number(calTop(a)) || a.s.getTime() - b.s.getTime());
  const [s, e] = calWindow(p.view, p.date), out: string[] = [];
  for (let d = s; d < e; d = calAddDays(d, 1)) {
    const rows = items.filter(it => calOn(it, d)).sort((a, b) => Number(calTop(b)) - Number(calTop(a)) || a.s.getTime() - b.s.getTime());
    if (!rows.length) continue;
    out.push(`<div class="cal-ld${calDay(d) === calDay(new Date()) ? ' today' : ''}" data-cal-day="${calDay(d)}">${esc(d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}))}</div>`);
    for (const it of rows) {
      const o = it.o, ref = it.refs[0] || o.calendar.ref, when = o.kind === 'due' ? 'Due' : o.all_day || o.kind === 'away' ? '' : calClock(it.s);
      const kind = o.kind === 'run' && (o.planned || it.s.getTime() > Date.now()) ? 'run planned' : o.kind;
      out.push(`<button type="button" class="cal-lr${calMatch(it, p.q) ? '' : ' dim'}${p.card === it.key ? ' sel' : ''}" data-cal-item="${esc(it.key)}"><span class="t">${esc(when)}</span><i class="cal-kd ${kind}${o.busy_only ? ' busy' : ''}" style="--h:${calHue(ref)}"></i><span class="l">${esc(calLabel(it))}</span><span class="m">${esc(it.refs.map(r => calViewName(r)).join(', '))}</span></button>`);
    }
  }
  return `<div class="cal-list">${out.join('') || '<p class="empty">Clear</p>'}</div>`;
}
const calViewName = (ref: string) => (CALP?.data?.calendars || []).find(c => c.ref === ref)?.name || (CALP?.data?.occurrences || []).find(o => o.calendar.ref === ref)?.calendar.name || calRefName(ref);

// ----------------------------------------------------------------- the card
function calCardClose(): void {
  document.getElementById('cal-card')?.remove();
  if (CALP && CALP.card) { CALP.card = ''; for (const el of document.querySelectorAll('#cal-root .sel[data-cal-item]')) el.classList.remove('sel'); }
}
function calOpenById(id: string): void {
  const p = CALP; if (!p) return;
  let it = p.items.get(id);
  if (!it) for (const x of p.items.values()) if (x.o.occurrence_id === id || x.o.children?.includes(id) || x.runs?.some(r => r.occurrence_id === id)) { it = x; break; }
  if (!it) return;
  calCard(it, document.querySelector<HTMLElement>(`#cal-root [data-cal-item="${CSS.escape(it.key)}"]`));
}
// A small card of links: no buttons, no editor (decision 13).
function calCard(it: CalItem, anchor: HTMLElement | null): void {
  const p = CALP; if (!p) return;
  calCardClose();
  p.card = it.key; anchor?.classList.add('sel');
  const o = it.o, links: string[] = [];
  const a = (href: string, label: string, extra = '') => `<a href="${esc(href)}"${extra}>${label}</a>`;
  if (!o.busy_only) {
    if (o.kind === 'event' && o.meeting_url) links.push(`<a ${calJoinAttrs(o)}>Join</a>`);
    if (o.links?.thread) links.push(a(THREAD_ROUTE + encodeURIComponent(o.links.thread), 'Thread'));
    if (o.links?.meeting) links.push(a(MEETINGS + '?meeting=' + encodeURIComponent(o.links.meeting), 'Transcript'));
    if (o.links?.automation) links.push(a(auHref(o.links.automation), 'Automation'), a(auHref(o.links.automation, 'runs'), 'Last run'));
    if (o.links?.task) links.push(a('#/task/' + encodeURIComponent(o.links.task), 'Task'));
    if (o.open_url) links.push(a(o.open_url, 'Open in Google', ' target="_blank" rel="noopener noreferrer"'));
  }
  const day = it.s.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
  const when = o.kind === 'due' || o.all_day ? day : `${day} ${calRange({...o, start: it.s.toISOString(), end: it.e.toISOString()})}`;
  const who = o.busy_only ? '' : o.attendees?.length ? o.attendees.map(x => x.name || x.email || '').filter(Boolean).join(', ') : o.calendar.name;
  const runs = it.runs ? `<div class="cal-card-runs">${it.runs.map(r => `<button type="button" data-cal-child="${esc(r.occurrence_id)}"><span class="t">${esc(calClock(calWhen(r.start)))}</span><span>${esc(r.title)}</span></button>`).join('')}</div>` : '';
  const card = document.createElement('div');
  card.id = 'cal-card'; card.className = 'cal-card'; card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', calLabel(it));
  card.style.setProperty('--h', String(calHue(it.refs[0] || o.calendar.ref)));
  card.innerHTML = `<button type="button" class="cal-card-x" data-cal-x aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button>
    <b>${esc(calLabel(it))}</b><span class="w">${esc(when)}${o.recurring ? ' · ↻' : ''}</span>${who ? `<span class="w">${esc(who)}</span>` : ''}${runs}${links.length ? `<div class="lk">${links.join('')}</div>` : ''}`;
  document.body.append(card);
  // Beside what was clicked, kept on screen; centred when nothing was (an address that names it).
  const r = anchor?.getBoundingClientRect(), w = card.offsetWidth, h = card.offsetHeight;
  let x = r ? (r.right + 8 + w < innerWidth ? r.right + 8 : r.left - 8 - w) : (innerWidth - w) / 2;
  let y = r ? r.top : (innerHeight - h) / 3;
  x = Math.max(8, Math.min(x, innerWidth - w - 8)); y = Math.max(8, Math.min(y, innerHeight - h - 8));
  if (calPhone()) { x = 8; y = Math.max(8, innerHeight - h - 80); }
  card.style.left = x + 'px'; card.style.top = y + 'px';
  card.querySelector<HTMLElement>('a, button:not([data-cal-x])')?.focus({preventScroll: true});
}

// ----------------------------------------------------------------- search
let CAL_DD_TIMER = 0, CAL_EV_TIMER = 0;
function calSearchInput(value: string): void {
  const p = CALP; if (!p) return;
  p.q = value; calWrite(p);
  calBody();
  p.drop.open = true; p.drop.i = 0;
  calDropDraw();
  clearTimeout(CAL_DD_TIMER); clearTimeout(CAL_EV_TIMER);
  const plain = value.trim().split(/\s+/).filter(w => w && !w.includes(':')).join(' ');
  if (!value.trim()) { p.drop.cals = []; p.drop.events = []; calDropDraw(); return; }
  CAL_DD_TIMER = setTimeout(async () => {
    const seq = ++p.drop.calSeq;
    if (!plain) { p.drop.cals = []; calDropDraw(); return; }
    try {
      const a = await get<{calendars: CalView[]}>(`${CAL_API}/calendars?q=${encodeURIComponent(plain)}`);
      if (CALP !== p || seq !== p.drop.calSeq) return;
      p.drop.cals = (a.calendars || []).filter(c => !p.who.includes(c.ref)).slice(0, 5);
    } catch { p.drop.cals = []; }
    calDropDraw();
  }, 120);
  CAL_EV_TIMER = setTimeout(async () => {
    const seq = ++p.drop.evSeq;
    try {
      const q = new URLSearchParams({q: value.trim(), who: p.who.map(r => r === calMe() ? 'me' : r).join(',')});
      const a = await get<{occurrences: CalOccurrence[]}>(`${CAL_API}/search?${q}`);
      if (CALP !== p || seq !== p.drop.evSeq) return;
      p.drop.events = (a.occurrences || []).slice(0, 6);
    } catch { p.drop.events = []; }
    calDropDraw();
  }, 280);
}
// The rows, in the spec's order: calendars, events, narrowing words, Ask Tico.
function calDropRows(): string[] {
  const p = CALP; if (!p) return [];
  const q = p.q.trim(); if (!q) return [];
  const rows: string[] = [];
  const kindIcon: Record<string, string> = {person: 'person', human: 'person', bot: 'smart_toy', team: 'groups', shared: 'calendar_month', google: 'calendar_month'};
  // At the server's 20 a calendar is still found, and says why it cannot be added.
  const full = p.who.length >= CAL_WHO_MAX;
  for (const c of p.drop.cals) rows.push(`<button type="button" role="option" data-dd-cal="${esc(c.ref)}"${full ? ' aria-disabled="true"' : ''}><span class="nav-icon" aria-hidden="true">${kindIcon[c.kind || c.type] || 'calendar_month'}</span><span class="l">${esc(c.name)}</span>${full ? `<span class="m">At most ${CAL_WHO_MAX} in view</span>` : ''}</button>`);
  for (const o of p.drop.events) {
    const s = calWhen(o.start);
    rows.push(`<button type="button" role="option" data-dd-ev="${esc(o.occurrence_id)}"><span class="t">${esc(s.toLocaleDateString(undefined, {month: 'short', day: 'numeric'}))} ${o.all_day ? '' : esc(calClock(s))}</span><span class="l">${esc(o.busy_only ? 'Busy' : o.title)}</span><span class="m">${esc(o.calendar.name)}</span></button>`);
  }
  const last = q.split(/\s+/).pop() || '';
  if (!last.includes(':')) for (const [word, fill] of CAL_WORDS) {
    if (last && word.startsWith(last.toLowerCase()) && word !== last) rows.push(`<button type="button" role="option" data-dd-word="${esc(fill)}"><span class="nav-icon" aria-hidden="true">search</span><span class="l">${esc(fill)}</span></button>`);
  }
  rows.push(`<button type="button" role="option" data-dd-ask><span class="cal-dd-dot" aria-hidden="true">${ldotHTML({state: 'still', size: 14})}</span><span class="l">Ask Tico</span></button>`);
  return rows;
}
function calDropDraw(): void {
  const p = CALP, dd = document.getElementById('cal-dd'); if (!p || !dd) return;
  const rows = p.drop.open ? calDropRows() : [];
  p.drop.i = Math.max(0, Math.min(p.drop.i, rows.length - 1));
  dd.innerHTML = rows.map((r, i) => i === p.drop.i ? r.replace('role="option"', 'role="option" aria-selected="true" class="on"') : r).join('');
  dd.hidden = !rows.length;
  dd.closest('.cal-sr')?.setAttribute('aria-expanded', String(!dd.hidden));
}
async function calDropTake(btn: HTMLElement): Promise<void> {
  const p = CALP, input = document.querySelector<HTMLInputElement>('#cal-q'); if (!p || !input) return;
  if (btn.dataset.ddCal) {
    const ref = btn.dataset.ddCal;
    if (p.who.length >= CAL_WHO_MAX) return;      // the row says why
    input.value = ''; p.q = ''; p.drop.open = false; calDropDraw();
    calWhoSet(p, [...p.who, ref]);
    return;
  }
  if (btn.dataset.ddEv) {
    const o = p.drop.events.find(x => x.occurrence_id === btn.dataset.ddEv); if (!o) return;
    p.drop.open = false; calDropDraw();
    if (!p.who.includes(o.calendar.ref) && o.calendar.ref !== calMe() && o.kind !== 'run' && p.who.length < CAL_WHO_MAX) p.who = [...p.who, o.calendar.ref];
    p.date = calDay(calWhen(o.start)); p.open = o.occurrence_id;
    if (p.view === 'month') p.view = 'week';
    calWrite(p); calHead(); void calLoad();
    return;
  }
  if (btn.dataset.ddWord) {
    const words = input.value.split(/(\s+)/); words[words.length - 1] = btn.dataset.ddWord;
    input.value = words.join(''); input.focus(); calSearchInput(input.value);
    return;
  }
  if (btn.dataset.ddAsk !== undefined) {
    const words = input.value.trim();
    p.drop.open = false; calDropDraw();
    if (words) askDraft(words);
  }
}

// ----------------------------------------------------------------- events
function calBind(): void {
  const root = document.getElementById('cal-root'), input = document.querySelector<HTMLInputElement>('#cal-q'); if (!root || !input) return;
  input.addEventListener('input', () => calSearchInput(input.value));
  input.addEventListener('focus', () => { if (CALP && input.value.trim()) { CALP.drop.open = true; calDropDraw(); } });
  input.addEventListener('blur', () => setTimeout(() => { if (CALP && document.activeElement !== input) { CALP.drop.open = false; calDropDraw(); calFindFold(); } }, 150));
  input.addEventListener('keydown', ev => {
    const p = CALP, dd = document.getElementById('cal-dd'); if (!p || !dd) return;
    const rows = [...dd.querySelectorAll<HTMLElement>('button')];
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      if (!rows.length) return;
      ev.preventDefault();
      p.drop.open = true; p.drop.i = (p.drop.i + (ev.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length; calDropDraw();
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      const row = rows[p.drop.i] || rows[0];
      if (row) void calDropTake(row);
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      input.value = ''; calSearchInput(''); p.drop.open = false; calDropDraw(); input.blur();
    }
  });
  root.addEventListener('input', ev => {
    const t = eventElement(ev);
    if (CALP && t instanceof HTMLInputElement && t.matches('[data-cal-addq]')) { CALP.add.q = t.value; calAddDraw(); }
  });
  root.addEventListener('change', ev => {
    const p = CALP, t = eventElement(ev);
    if (!p || !(t instanceof HTMLInputElement) || !t.dataset.calPick) return;
    const ref = t.dataset.calPick;
    calWhoSet(p, t.checked ? [...p.who, ref] : p.who.filter(r => r !== ref));
  });
  const dd = document.getElementById('cal-dd');
  dd?.addEventListener('mousedown', ev => ev.preventDefault());      // the box keeps focus
  dd?.addEventListener('click', ev => { const b = eventElement(ev)?.closest<HTMLElement>('button'); if (b) void calDropTake(b); });
  root.addEventListener('click', ev => {
    const p = CALP, t = eventElement(ev); if (!p || !t) return;
    const at = (sel: string) => t.closest<HTMLElement>(sel);
    const moved = () => { calCardClose(); calWrite(p); calHead(); void calLoad(); };
    const view = at('[data-cal-view]'), step = at('[data-cal-step]'), date = at('[data-cal-date]'), rm = at('[data-cal-rm]');
    const lay = at('[data-cal-lay]'), more = at('[data-cal-more]'), item = at('[data-cal-item]'), quick = at('[data-cal-quick]');
    if (at('[data-cal-add]')) { calAddOpen(!p.add.open); return; }
    if (at('[data-cal-chips-more]')) { calAddOpen(true); return; }
    if (quick) {
      const all = quick.dataset.calQuick === 'all' ? calAddList(p).map(c => c.ref) : calMyBots();
      calWhoSet(p, [...p.who, ...all]); return;
    }
    if (at('[data-cal-find]')) { calFindOpen(); return; }
    if (view) { const v = CAL_VIEWS.find(([name]) => name === view.dataset.calView); if (v) p.view = v[0]; moved(); return; }
    if (step) { p.date = calStep(p, Number(step.dataset.calStep)); moved(); return; }
    if (at('[data-cal-today]')) { p.date = calDay(new Date()); moved(); return; }
    if (date) {
      const day = date.dataset.calDate || p.date;
      const sameWeek = calWindow('week', day)[0].getTime() === calWindow('week', p.date)[0].getTime();
      p.date = day; if (date.dataset.calTo === 'day') p.view = 'day';
      calWrite(p); calHead();
      if (p.view === 'list' && sameWeek) { calBody(); root.querySelector<HTMLElement>(`[data-cal-day="${day}"]`)?.scrollIntoView({block: 'start', behavior: 'smooth'}); }
      else void calLoad();
      return;
    }
    if (rm) { const ref = rm.dataset.calRm || ''; calWhoSet(p, p.who.filter(r => r !== ref)); return; }
    if (lay) {
      const k = CAL_TOGGLES.find(([name]) => name === lay.dataset.calLay)?.[0];
      if (k) { calKindsSet(k, !calKinds()[k]); calCardClose(); calAddDraw(); calBody(); }
      return;
    }
    if (more) {
      const menu = root.querySelector<HTMLElement>('[data-cal-menu]'); if (!menu) return;
      menu.hidden = !menu.hidden; more.setAttribute('aria-expanded', String(!menu.hidden)); calMenuDraw(); return;
    }
    if (at('[data-cal-record]')) {
      const on = !calRecordOn(); calRecordSet(on); calMenuDraw();
      void put(`${CAL_API}/settings`, {record_on_join: on}).catch(() => {});
      return;
    }
    if (item) {
      const it = p.items.get(item.dataset.calItem || '');
      if (it && p.card === it.key) calCardClose(); else if (it) calCard(it, item);
    }
  });
}
// The card's links and close, "/" to search, Escape and a click elsewhere to close (once, for the whole app).
document.addEventListener('click', ev => {
  const t = eventElement(ev), card = document.getElementById('cal-card');
  if (!t || !CALP) return;
  const join = t.closest<HTMLElement>('#cal-card [data-cal-join]');
  if (join) { const it = [...CALP.items.values()].find(x => x.o.occurrence_id === join.dataset.calJoin); if (it) calRecorder(it.o); return; }
  const child = t.closest<HTMLElement>('#cal-card [data-cal-child]');
  if (child) { const it = CALP.items.get(child.dataset.calChild || ''); if (it) calCard(it, card); return; }
  if (t.closest('#cal-card [data-cal-x]')) { calCardClose(); return; }
  if (card && !card.contains(t) && !t.closest('[data-cal-item]')) calCardClose();
  if (CALP.add.open && !t.closest('.cal-add, [data-cal-chips-more]') && t.isConnected) calAddOpen(false);
  const menu = document.querySelector<HTMLElement>('#cal-root [data-cal-menu]');
  if (menu && !menu.hidden && !t.closest('.cal-more')) { menu.hidden = true; document.querySelector('#cal-root [data-cal-more]')?.setAttribute('aria-expanded', 'false'); }
});
document.addEventListener('keydown', ev => {
  if (!CALP || !document.getElementById('cal-root')) return;
  const t = eventElement(ev);
  if (ev.key === 'Escape' && CALP.add.open) { ev.preventDefault(); calAddOpen(false); document.querySelector<HTMLElement>('#cal-root [data-cal-add]')?.focus(); return; }
  if (ev.key === 'Escape' && document.getElementById('cal-card')) { calCardClose(); return; }
  if (ev.key === '/' && !ev.metaKey && !ev.ctrlKey && !t?.closest('input, textarea, select, [contenteditable]')) {
    ev.preventDefault(); calFindOpen();
  }
});
// Fresh on focus and every three minutes while shown: Google calendars are read live, never pushed.
window.addEventListener('focus', () => { if (CALP && Date.now() - CALP.loaded > 30000) void calLoad(); });
setInterval(() => { if (CALP && !document.hidden && Date.now() - CALP.loaded >= CAL_ROW_EVERY) void calLoad(); }, 30000);
liveOn('calendar', () => { if (CALP) liveSoon('cal-page', () => void calLoad(), 500); });
