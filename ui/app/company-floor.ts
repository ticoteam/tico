/* ui/app/company-floor.ts — the Floor, a card on Company (#/home, ui/app/dashboard.ts): every factory the reader sees
   as a hall, each pipeline a line, each work stage a small stack of boxes standing on it, as high as the stage is full (relative and capped, never
   one box a task). A hall's dim org chart is its manager over its reports and the teammates holding its work; pointing
   at one lights their stacks. Flow's mark stands over a stack holding something that waits on the reader, and opens Flow there. The
   two numbers at a line's ends are tasks in and out, as a rate; either flips every rate between a day and a week, kept
   in this browser. Nothing else is written on the floor: pointing at a stack shows its card (who holds it, the stage, how
   many, how many wait on you, the oldest tasks), a click or Enter pins the card, a phone opens it as a sheet.
   One factory (#/overview/floor/<id>, its own page, back to Company) draws each stage as one deck per teammate holding
   tasks there, the stage's name under the line with one mark beside it, and the factory's manager and teammates by name.
   One floor is on screen at a time: floorMount puts the company's in its card, pageFloorFactory one factory's page.
   Reads: GET /api/v2/company/floor (backend/company_floor.py) and, for a card's rows, GET /api/v2/tasks (task-api.ts).
   The live task and Needs-you signals read the floor again and patch each changed line in place; Company's safety read
   (dashboard.ts) reads it too and draws it only when the answer differs.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type FloorData = import('../types/api').CompanyFloor;
type FloorFactory = FloorData['factories'][number];
type FloorPipe = FloorFactory['pipelines'][number];
type FloorStage = FloorPipe['stages'][number];
type FloorUnit = 'day' | 'week';
/** What a card is about: a stage, or one teammate's deck in it (`actor`; '' for the whole stage, CF_REST for the deck
 *  of everyone past the most loaded and of tasks nobody holds). */
interface FloorPick { pipeline: string; stage: string; actor: string }
interface FloorState {
  factory: string;            // '' for the whole company
  host: HTMLElement;          // what the floor is drawn in: Company's Floor card, or one factory's page
  data: FloorData | null;
  run: number;
  pinned: FloorPick | null;
  shown: FloorPick | null;    // the card on screen, pinned or hovered
  anchor: HTMLElement | null;
  hoverTimer: number;
  rows: Map<string, {at: number; tasks: TaskBriefV3[]}>;
}

const CF_UNIT_KEY = 'tico.floor.unit';
const CF_LAYERS = 8;           // the tallest a stack or deck stands
const CF_ROWS = 5;             // task rows on a card
const CF_ROWS_FRESH_MS = 30e3;
const CF_MATES_MAX = 12;       // tiles on a hall's org chart
const CF_REST = '+';
let CF: FloorState | null = null;
let CF_REFOCUS = false;        // focus going back to a stack as its card closes: it does not open the card again

const cfHref = (factory: string): string => OVERVIEW_FLOOR + '/' + encodeURIComponent(factory);
const cfKey = (p: FloorPick): string => `${p.pipeline}|${p.stage}|${p.actor}`;
const cfPhone = (): boolean => matchMedia('(max-width:760px), (hover:none)').matches;

/** How many boxes a count stands as: relative and capped at CF_LAYERS, so 1, 3, 10 and 50 each stand at their own
 *  height and 400 stands as high as 50. Zero draws none (an empty spot on the line). */
function cfLayers(n: number): number {
  if (n <= 0) return 0;
  const i = [1, 2, 3, 5, 9, 19, 49].findIndex(top => n <= top);
  return i < 0 ? CF_LAYERS : i + 1;
}

// ---- the rate unit, kept per browser (storage may be blocked: then a day)
function cfUnit(): FloorUnit {
  try { return localStorage.getItem(CF_UNIT_KEY) === 'week' ? 'week' : 'day'; } catch { return 'day'; }
}
function cfUnitKeep(unit: FloorUnit): void {
  try { localStorage.setItem(CF_UNIT_KEY, unit); } catch { /* storage blocked: not kept */ }
}
const cfRateText = (el: HTMLElement, unit: FloorUnit): string => `${el.dataset[unit] ?? '0'}/${unit}`;
function cfRateHTML(rate: {day: number; week: number}, end: 'in' | 'out'): string {
  const unit = cfUnit();
  return `<button type="button" class="cf-rate cf-${end}" data-day="${rate.day}" data-week="${rate.week}" aria-label="${end === 'in' ? 'In' : 'Out'}">`
    + `${rate[unit]}/${unit}</button>`;
}
function cfUnitFlip(): void {
  const unit: FloorUnit = cfUnit() === 'day' ? 'week' : 'day';
  cfUnitKeep(unit);
  for (const el of document.querySelectorAll<HTMLElement>('.cf-rate')) el.textContent = cfRateText(el, unit);
}

// ---- who works in a factory
/** The factory's manager, else the one most of its pipelines are managed by. */
function cfManager(f: FloorFactory): string | null {
  if (f.manager_id) return f.manager_id;
  const tally = new Map<string, number>();
  for (const p of f.pipelines) if (p.manager_id) tally.set(p.manager_id, (tally.get(p.manager_id) ?? 0) + 1);
  return [...tally].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}
/** The manager's own reports on the org chart, as actors: the bots the reader may see, archived ones and helpers left out. */
function cfReports(manager: string | null): string[] {
  if (!manager) return [];
  const boss = manager.startsWith('bot:') ? manager.slice(4) : manager;
  return (S.emps || [])
    .filter(e => e.reports_to === boss && e.status !== 'archived' && e.state !== 'archived' && orgBranchVisible(e) && !isHelperBot(e))
    .map(e => 'bot:' + e.id);
}
/** Everyone on the factory's pipelines or reporting to its manager: those holding work first, the most loaded leading,
    then the manager's idle reports by name. The manager is left out. */
function cfMates(f: FloorFactory, manager: string | null): string[] {
  const load = new Map<string, number>();
  for (const a of cfReports(manager)) load.set(a, 0);
  for (const p of f.pipelines) for (const s of p.stages) for (const o of s.owners) load.set(o.actor, (load.get(o.actor) ?? 0) + o.count);
  return [...load].filter(([a]) => a !== manager).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([a]) => a);
}
const cfFace = (actor: string, size: number): string => actorFace(actor, size);

function cfFlowHTML(p: FloorPipe, s: FloorStage, size: number): string {
  return `<button type="button" class="cf-flow" data-cf-flow="${esc(s.id)}" data-cf-pipeline="${esc(p.id)}" aria-label="Needs you: ${esc(s.name)}">`
    + `${flowMarkHTML(size)}</button>`;
}

// ---- the company: halls, lines, stacks
function cfStackHTML(p: FloorPipe, s: FloorStage): string {
  const n = cfLayers(s.count);
  const boxes = n ? '<i class="cf-box"></i>'.repeat(n) : '<i class="cf-box cf-none"></i>';
  return `<div class="cf-slot">${s.needs_you > 0 ? cfFlowHTML(p, s, 12) : ''}`
    + `<button type="button" class="cf-stack" data-cf-pipeline="${esc(p.id)}" data-cf-stage="${esc(s.id)}" data-cf-actor=""`
    + ` data-cf-who="${esc(s.owners.map(o => o.actor).join(' '))}" aria-haspopup="dialog" aria-label="${esc(s.name)}, ${s.count}"`
    + ` style="--n:${n || 1}">${boxes}</button></div>`;
}
function cfLineHTML(p: FloorPipe, deep: boolean): string {
  const slots = p.stages.map(s => deep ? cfStageHTML(p, s) : cfStackHTML(p, s)).join('<i class="cf-chev" aria-hidden="true"></i>');
  return `<div class="cf-line${deep ? ' cf-deep' : ''}" data-cf-line="${esc(p.id)}" data-cf-sig="${esc(cfSig(p))}">`
    + `<span class="cf-pname">${esc(p.name)}</span>${cfRateHTML(p.in, 'in')}`
    + `<div class="cf-track"><div class="cf-rail">${slots || '<span class="cf-slot cf-gap"></span>'}<i class="cf-chev cf-end" aria-hidden="true"></i></div></div>`
    + `${cfRateHTML(p.out, 'out')}</div>`;
}
const cfSig = (p: FloorPipe): string => JSON.stringify(p);

function cfOrgHTML(manager: string | null, mates: string[]): string {
  const shown = mates.slice(0, CF_MATES_MAX);
  if (!manager && !shown.length) return '';
  const tile = (a: string, size: number) => `<span class="cf-mate" data-cf-mate="${esc(a)}" title="${esc(actorName(a))}">${cfFace(a, size)}</span>`;
  return `<div class="cf-org" aria-hidden="true">${manager ? `<div class="cf-org-top">${tile(manager, 16)}</div>` : ''}`
    + (shown.length ? `<div class="cf-org-row${manager ? ' cf-org-under' : ''}">${shown.map(a => tile(a, 14)).join('')}</div>` : '')
    + '</div>';
}
function cfHallHTML(f: FloorFactory): string {
  const manager = cfManager(f);
  return `<section class="cf-hall" data-cf-factory="${esc(f.id)}" aria-label="${esc(f.name)}">`
    + `<div class="cf-hall-head"><a class="cf-hall-name" href="${esc(cfHref(f.id))}">${esc(f.name)}</a>${cfOrgHTML(manager, cfMates(f, manager))}</div>`
    + `<div class="cf-lines">${f.pipelines.map(p => cfLineHTML(p, false)).join('')}</div></section>`;
}

// ---- one factory: a deck per teammate in each stage, the stage named under the line
function cfDeckHTML(p: FloorPipe, s: FloorStage, actor: string, count: number, label: string): string {
  const plies = '<i class="cf-ply"></i>'.repeat(Math.max(cfLayers(count) - 1, 0));
  const top = actor === CF_REST ? `<span class="cf-rest tnum">+${count}</span>` : cfFace(actor, 22);
  return `<button type="button" class="cf-deck" data-cf-pipeline="${esc(p.id)}" data-cf-stage="${esc(s.id)}" data-cf-actor="${esc(actor)}"`
    + ` data-cf-who="${esc(actor)}" aria-haspopup="dialog" aria-label="${esc(label)}, ${esc(s.name)}, ${count}"><span class="cf-cap">${top}</span>${plies}</button>`;
}
function cfStageHTML(p: FloorPipe, s: FloorStage): string {
  const held = s.owners.reduce((n, o) => n + o.count, 0);
  const rest = s.more.count + Math.max(s.count - held - s.more.count, 0);
  const decks = s.owners.map(o => cfDeckHTML(p, s, o.actor, o.count, actorName(o.actor))).join('')
    + (rest ? cfDeckHTML(p, s, CF_REST, rest, `${rest} more`) : '');
  return `<div class="cf-stage" data-cf-stage-id="${esc(s.id)}"><div class="cf-decks">${decks || '<i class="cf-box cf-none"></i>'}</div>`
    + `<div class="cf-sname"><span>${esc(s.name)}</span>${s.needs_you > 0 ? cfFlowHTML(p, s, 14) : ''}</div></div>`;
}
function cfFactoryHTML(f: FloorFactory): string {
  const manager = cfManager(f), mates = cfMates(f, manager);
  const person = (a: string) => `<li class="cf-mate" data-cf-mate="${esc(a)}">${cfFace(a, 20)}<span>${esc(actorName(a))}</span></li>`;
  const org = manager || mates.length
    ? `<div class="cf-roster">${manager ? `<ul class="cf-roster-top">${person(manager)}</ul>` : ''}`
      + (mates.length ? `<ul class="cf-roster-list">${mates.map(person).join('')}</ul>` : '') + '</div>'
    : '';
  return `<section class="cf-hall cf-one" data-cf-factory="${esc(f.id)}" aria-label="${esc(f.name)}"><div class="cf-hall-head"><span class="cf-hall-name">${esc(f.name)}</span>${org}</div>`
    + `<div class="cf-lines">${f.pipelines.map(p => cfLineHTML(p, true)).join('')}</div></section>`;
}

// ---- where it is drawn
/** The floor in `host`, read again now. The company's floor read last is drawn at once, so Company drawing its cards
 *  again (a write, a changed read) does not flash the skeleton. */
function cfStart(host: HTMLElement, factory: string): void {
  const kept = CF && CF.factory === factory ? CF.data : null;
  CF = {factory, host, data: kept, run: 0, pinned: null, shown: null, anchor: null, hoverTimer: 0, rows: new Map()};
  host.insertAdjacentHTML('beforeend', `<div class="cf-body${factory ? ' cf-body-one' : ''}" id="cf-body">${cfSkeleton(factory)}</div>`
    + '<div class="cf-scrim" hidden></div><div class="cf-card" id="cf-card" role="dialog" tabindex="-1" hidden></div>');
  host.addEventListener('click', cfClick);
  host.addEventListener('pointerover', cfOver);
  host.addEventListener('pointerout', cfOut);
  host.addEventListener('focusin', cfFocus);
  if (kept) cfDraw(CF, kept);
  void cfLoad();
}
/** Company's Floor card (ui/app/dashboard.ts DASH_BODY.floor): the whole company's floor in the card's body. */
function floorMount(host: HTMLElement): void {
  if (host.isConnected) cfStart(host, '');
}
/** One factory's page, under a header that leads back to Company. */
function pageFloorFactory(factory: string): void {
  if (!factory) { location.replace(HOME); return; }
  const main = $('#main'); main.classList.add('overview-layout');
  main.innerHTML = `<section class="overview-page cf-page" aria-label="Floor">`
    + `${pageHead({title: '', back: {href: HOME, label: 'Company'}, pad: true, more: PAGE_HEAD_COPY})}</section>`;
  const page = document.querySelector<HTMLElement>('#main > .cf-page');
  if (page) cfStart(page, factory);
}
function cfSkeleton(factory: string): string {
  const hall = '<div class="cf-hall cf-skel" aria-hidden="true"><span class="cf-skel-name"></span><span class="cf-skel-line"></span><span class="cf-skel-line"></span></div>';
  return `<div class="cf-halls" aria-busy="true" aria-label="Loading">${factory ? hall : hall.repeat(4)}</div>`;
}
/** Whether the floor is still on screen: its host is in the page and still holds it. */
const cfHere = (): boolean => !!CF && CF.host.isConnected && CF.host.querySelector('#cf-body') !== null;

async function cfLoad(poll = false): Promise<void> {
  const st = CF;
  if (!st) return;
  const run = ++st.run;
  let data: FloorData;
  try {
    data = await get<FloorData>('/v2/company/floor', {fresh: true});
  } catch {
    if (run !== st.run || CF !== st || !cfHere()) return;
    const body = st.host.querySelector<HTMLElement>('#cf-body');
    if (body && !st.data) body.innerHTML = '<div class="cf-empty"><p class="muted">Could not load</p><button type="button" class="ghost" data-cf-retry>Retry</button></div>';
    return;
  }
  if (run !== st.run || CF !== st || !cfHere()) return;
  if (poll && sameData(data, st.data)) return;  // the safety read draws only an answer that differs
  st.data = data;
  cfDraw(st, data);
}
/** Company's safety read (dashboard.ts dashReread) reads the floor again, every 2 minutes on screen or 5 with the live stream. */
function cfPoll(): void {
  if (CF && cfHere()) void cfLoad(true);
}

function cfDraw(st: FloorState, data: FloorData): void {
  const body = st.host.querySelector<HTMLElement>('#cf-body');
  if (!body) return;
  if (st.factory) {
    const f = data.factories.find(x => x.id === st.factory);
    // A factory this reader may not see is not drawn: the address goes back to the company.
    if (!f) { location.replace(HOME); return; }
    const title = document.querySelector<HTMLElement>('.cf-page > header.ph .ph-t');
    if (title) title.textContent = f.name;
    pageTitle(f.name);
    cfPatch(body, `<div class="cf-halls">${cfFactoryHTML(f)}</div>`, [f]);
  } else if (!data.factories.length) {
    body.innerHTML = '<div class="cf-empty"><p class="muted">No factories yet</p></div>';
  } else {
    cfPatch(body, `<div class="cf-halls">${data.factories.map(cfHallHTML).join('')}</div>`, data.factories);
  }
  cfCardFollow(st);
}

/** Puts a new reading in place: the first one whole, then only the halls whose people or lines changed, each line
 *  replaced only when its own numbers did. A pinned card, the scroll and the focus stay where they were. */
function cfPatch(body: HTMLElement, html: string, factories: FloorFactory[]): void {
  const halls = body.querySelector<HTMLElement>(':scope > .cf-halls:not([aria-busy])');
  const ids = factories.map(f => f.id);
  const had = halls ? [...halls.querySelectorAll<HTMLElement>(':scope > .cf-hall')].map(h => h.dataset['cfFactory'] ?? '') : [];
  if (!halls || had.join('|') !== ids.join('|')) { body.innerHTML = html; return; }
  const next = document.createElement('div');
  next.innerHTML = html;
  for (const fresh of next.querySelectorAll<HTMLElement>('.cf-halls > .cf-hall')) {
    const old = halls.querySelector<HTMLElement>(`:scope > .cf-hall[data-cf-factory="${CSS.escape(fresh.dataset['cfFactory'] ?? '')}"]`);
    if (!old) continue;
    const lines = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.cf-line')].map(l => l.dataset['cfLine'] ?? '');
    const org = (el: HTMLElement) => el.querySelector('.cf-org, .cf-roster')?.outerHTML ?? '';
    if (lines(old).join('|') !== lines(fresh).join('|') || org(old) !== org(fresh)) { old.replaceWith(fresh); continue; }
    for (const line of fresh.querySelectorAll<HTMLElement>('.cf-line')) {
      const was = old.querySelector<HTMLElement>(`.cf-line[data-cf-line="${CSS.escape(line.dataset['cfLine'] ?? '')}"]`);
      if (was && was.dataset['cfSig'] !== line.dataset['cfSig']) was.replaceWith(line);
    }
  }
}

// ---- the card: hover shows it, a click or Enter pins it, a phone opens it as a sheet
function cfFind(pick: FloorPick): {p: FloorPipe; s: FloorStage} | null {
  for (const f of CF?.data?.factories ?? []) for (const p of f.pipelines) {
    if (p.id !== pick.pipeline) continue;
    const s = p.stages.find(x => x.id === pick.stage);
    return s ? {p, s} : null;
  }
  return null;
}
const cfPickOf = (el: HTMLElement): FloorPick => ({pipeline: el.dataset['cfPipeline'] ?? '', stage: el.dataset['cfStage'] ?? '', actor: el.dataset['cfActor'] ?? ''});

/** How many tasks a card is about, and how many of them wait on the reader. */
function cfPickCounts(pick: FloorPick, s: FloorStage): {count: number; wait: number} {
  if (pick.actor === CF_REST) return {count: s.count - s.owners.reduce((n, o) => n + o.count, 0), wait: 0};
  if (!pick.actor) return {count: s.count, wait: s.needs_you};
  const own = s.owners.find(o => o.actor === pick.actor);
  return {count: own?.count ?? 0, wait: own?.needs_you ?? 0};
}
function cfCardHead(pick: FloorPick, p: FloorPipe, s: FloorStage): string {
  const {count, wait} = cfPickCounts(pick, s);
  let who: string;
  if (pick.actor === CF_REST) who = `<b class="cf-card-name tnum">+${count}</b>`;
  else if (pick.actor) who = `${cfFace(pick.actor, 32)}<b class="cf-card-name">${esc(actorName(pick.actor))}</b>`;
  else if (s.owners.length) {
    const faces = s.owners.slice(0, 3).map(o => cfFace(o.actor, 24)).join('');
    const names = s.owners.slice(0, 2).map(o => actorName(o.actor)).join(', ');
    const rest = s.owners.length - 2 + s.more.owners;
    who = `<span class="cf-card-faces">${faces}</span><b class="cf-card-name">${esc(names)}${rest > 0 ? ` +${rest}` : ''}</b>`;
  } else who = `<b class="cf-card-name">${esc(p.name)}</b>`;
  const line = `<span class="cf-card-stage">${esc(s.name)} · ${count}</span>${wait > 0 ? `<span class="cf-card-wait">${wait} waiting on you</span>` : ''}`;
  return `<div class="cf-card-head"><div class="cf-card-who">${who}</div>${wait > 0 ? cfFlowHTML(p, s, 22) : ''}`
    + `<div class="cf-card-line">${line}</div></div>`;
}

function cfRowsHTML(tasks: TaskBriefV3[] | null, count: number): string {
  if (!tasks) return `<div class="cf-rows">${skelRows(Math.min(Math.max(count, 1), 3), {dot: false})}</div>`;
  const rows = tasks.map(t => `<button type="button" class="cf-row" data-cf-task="${esc(t.id)}">`
    + `<span class="cf-row-id tnum">${t.number != null ? '#' + t.number : ''}</span><span class="cf-row-t">${esc(t.title)}</span>`
    + `<span class="cf-row-age tnum">${esc(ageShort(t.stage_since))}</span></button>`).join('');
  const more = count - tasks.length;
  return `<div class="cf-rows">${rows}${more > 0 ? `<span class="cf-row-more">${more} more</span>` : ''}</div>`;
}

function cfCardOpen(anchor: HTMLElement, pinned: boolean): void {
  const st = CF, card = document.getElementById('cf-card');
  if (!st || !card) return;
  const pick = cfPickOf(anchor), found = cfFind(pick);
  if (!found) return;
  if (!pinned && st.pinned) return;            // a pinned card stays until it is closed
  st.anchor?.classList.remove('cf-on');
  st.anchor = anchor; st.shown = pick; st.pinned = pinned ? pick : null;
  anchor.classList.add('cf-on');
  anchor.setAttribute('aria-expanded', String(pinned));
  card.setAttribute('aria-label', `${found.s.name}, ${found.p.name}`);
  cfCardFill(st, card);
  const sheet = pinned && cfPhone();
  card.classList.toggle('cf-sheet', sheet);
  card.classList.toggle('cf-pinned', pinned);
  card.hidden = false;
  const scrim = document.querySelector<HTMLElement>('.cf-scrim');
  if (scrim) scrim.hidden = !sheet;
  cfCardPlace(card, anchor, sheet);
  requestAnimationFrame(() => card.classList.add('cf-open'));
  if (pinned) card.focus({preventScroll: true});
  void cfRowsLoad(st, pick);
}
function cfCardFill(st: FloorState, card: HTMLElement): void {
  const pick = st.shown, found = pick && cfFind(pick);
  if (!pick || !found) { cfCardClose(false); return; }
  const {count} = cfPickCounts(pick, found.s);
  const held = st.rows.get(cfKey(pick));
  card.innerHTML = cfCardHead(pick, found.p, found.s) + cfRowsHTML(held ? held.tasks : null, count);
}
/** Beside its stack on a computer, kept on screen; a sheet on a phone (its own CSS). */
function cfCardPlace(card: HTMLElement, anchor: HTMLElement, sheet: boolean): void {
  if (sheet) { card.style.left = ''; card.style.top = ''; return; }
  const r = anchor.getBoundingClientRect(), w = card.offsetWidth, h = card.offsetHeight;
  const left = Math.min(Math.max(r.left + r.width / 2 - 48, 8), innerWidth - w - 8);
  let top = r.bottom + 10;
  if (top + h > innerHeight - 8) top = Math.max(r.top - h - 10, 8);
  card.style.left = `${Math.round(left)}px`; card.style.top = `${Math.round(top)}px`;
}
function cfCardClose(refocus: boolean): void {
  const st = CF, card = document.getElementById('cf-card');
  if (!card) return;
  const anchor = st?.anchor;
  card.classList.remove('cf-open');
  card.hidden = true;
  const scrim = document.querySelector<HTMLElement>('.cf-scrim');
  if (scrim) scrim.hidden = true;
  anchor?.classList.remove('cf-on');
  anchor?.removeAttribute('aria-expanded');
  if (st) { st.pinned = null; st.shown = null; st.anchor = null; }
  if (refocus && anchor?.isConnected) {
    CF_REFOCUS = true;
    try { anchor.focus({preventScroll: true}); } finally { CF_REFOCUS = false; }
  }
}
/** After a new reading: the card keeps its stack (drawn again, so found again) or closes when the stack is gone. */
function cfCardFollow(st: FloorState): void {
  const pick = st.shown, card = document.getElementById('cf-card');
  if (!pick || !card) return;
  const sel = `[data-cf-pipeline="${CSS.escape(pick.pipeline)}"][data-cf-stage="${CSS.escape(pick.stage)}"][data-cf-actor="${CSS.escape(pick.actor)}"]:is(.cf-stack, .cf-deck)`;
  const anchor = document.querySelector<HTMLElement>(sel);
  if (!anchor || !cfFind(pick)) { cfCardClose(false); return; }
  st.anchor = anchor; anchor.classList.add('cf-on');
  if (st.pinned) anchor.setAttribute('aria-expanded', 'true');
  st.rows.delete(cfKey(pick));
  cfCardFill(st, card);
  cfCardPlace(card, anchor, card.classList.contains('cf-sheet'));
  void cfRowsLoad(st, pick);
}
async function cfRowsLoad(st: FloorState, pick: FloorPick): Promise<void> {
  const key = cfKey(pick), held = st.rows.get(key);
  if (held && Date.now() - held.at < CF_ROWS_FRESH_MS) return;
  // Snoozed tasks too, as the counts include them. The overflow deck has no owner to filter by: it lists the stage's.
  try {
    const page = await taskBriefsPage({pipeline: pick.pipeline, stages: [pick.stage], status: 'open', snoozed: 'all', limit: CF_ROWS,
      ...(pick.actor && pick.actor !== CF_REST ? {owners: [pick.actor]} : {})});
    st.rows.set(key, {at: Date.now(), tasks: page.tasks});
  } catch {
    st.rows.set(key, {at: Date.now(), tasks: []});
  }
  const card = document.getElementById('cf-card');
  if (CF !== st || !card || card.hidden || !st.shown || cfKey(st.shown) !== key) return;
  cfCardFill(st, card);
  if (st.anchor) cfCardPlace(card, st.anchor, card.classList.contains('cf-sheet'));
}

// ---- pointing, clicking, keys
function cfMateLight(hall: HTMLElement | null, actor: string): void {
  if (!hall) return;
  hall.classList.toggle('cf-lit', !!actor);
  for (const el of hall.querySelectorAll<HTMLElement>('.cf-stack, .cf-deck')) {
    el.classList.toggle('cf-hit', !!actor && (el.dataset['cfWho'] ?? '').split(' ').includes(actor));
  }
  for (const el of hall.querySelectorAll<HTMLElement>('.cf-mate')) el.classList.toggle('cf-hit', !!actor && el.dataset['cfMate'] === actor);
}
function cfOver(ev: PointerEvent): void {
  if (ev.pointerType !== 'mouse' || !CF) return;
  const el = eventElement(ev);
  const mate = el?.closest<HTMLElement>('.cf-mate');
  if (mate) { cfMateLight(mate.closest<HTMLElement>('.cf-hall'), mate.dataset['cfMate'] ?? ''); return; }
  const stack = el?.closest<HTMLElement>('.cf-stack, .cf-deck');
  if (el?.closest('#cf-card')) { clearTimeout(CF.hoverTimer); return; }
  if (!stack || CF.pinned) return;
  clearTimeout(CF.hoverTimer);
  if (stack === CF.anchor) return;
  CF.hoverTimer = window.setTimeout(() => { if (stack.isConnected) cfCardOpen(stack, false); }, 90);
}
function cfOut(ev: PointerEvent): void {
  if (ev.pointerType !== 'mouse' || !CF) return;
  const el = eventElement(ev), to = ev.relatedTarget instanceof Element ? ev.relatedTarget : null;
  const mate = el?.closest<HTMLElement>('.cf-mate');
  if (mate && to?.closest('.cf-mate') !== mate) cfMateLight(mate.closest<HTMLElement>('.cf-hall'), '');
  if (CF.pinned) return;
  if (to?.closest('.cf-stack, .cf-deck, #cf-card')) return;
  clearTimeout(CF.hoverTimer);
  CF.hoverTimer = window.setTimeout(() => { if (CF && !CF.pinned) cfCardClose(false); }, 160);
}
// A stack reached by Tab shows its card unpinned, as pointing does; Enter pins it.
function cfFocus(ev: FocusEvent): void {
  const el = eventElement(ev)?.closest<HTMLElement>('.cf-stack, .cf-deck');
  if (el && CF && !CF_REFOCUS && !CF.pinned && el.matches(':focus-visible') && !cfPhone()) cfCardOpen(el, false);
}
function cfClick(ev: MouseEvent): void {
  const el = eventElement(ev);
  if (!el || !CF) return;
  const flow = el.closest<HTMLElement>('[data-cf-flow]');
  if (flow) { ev.preventDefault(); cfCardClose(false); void decideStageOpen(flow.dataset['cfFlow'] ?? '', flow.dataset['cfPipeline'] ?? ''); return; }
  if (el.closest('.cf-rate')) { cfUnitFlip(); return; }
  const body = document.getElementById('cf-body');
  if (body && el.closest('[data-cf-retry]')) { body.innerHTML = cfSkeleton(CF.factory); void cfLoad(); return; }
  const row = el.closest<HTMLElement>('[data-cf-task]');
  if (row) { const id = row.dataset['cfTask'] ?? ''; cfCardClose(false); void taskModalOpen(id); return; }
  if (el.closest('.cf-scrim')) { cfCardClose(true); return; }
  const stack = el.closest<HTMLElement>('.cf-stack, .cf-deck');
  if (stack) {
    const same = CF.pinned && CF.anchor === stack;
    if (same) cfCardClose(true); else { CF.pinned = null; cfCardOpen(stack, true); }
    return;
  }
  if (CF.pinned && !el.closest('#cf-card')) cfCardClose(false);
}
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || !CF?.shown || !cfHere()) return;
  ev.preventDefault();
  cfCardClose(true);
});
// A click outside the page (the sidebar) leaves a pinned card where it is until the page goes; the page's own click
// handler closes it on the floor. A resize moves it with its stack.
window.addEventListener('resize', () => {
  const card = document.getElementById('cf-card');
  if (CF?.anchor && card && !card.hidden) cfCardPlace(card, CF.anchor, card.classList.contains('cf-sheet'));
});

// ---- live: a task moved or Needs you changed; the floor reads again, at most every few seconds, and patches in place
function cfLive(): void {
  if (!CF || !cfHere()) { if (CF && !cfHere()) CF = null; return; }
  liveThrottle('company-floor', () => { if (CF && cfHere()) { CF.rows.clear(); void cfLoad(); } }, 3000);
}
liveOn('tasks', cfLive);
liveOn('needs-list', cfLive);
liveOn('reset', cfLive);
