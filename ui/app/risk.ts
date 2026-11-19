/* ui/app/risk.ts — Company › Risk: the risks the company carries and the matters under them.
   A risk is a tag of kind risk; a matter is a task tagged with one, with its facts (kind, party, amount, forum).
   An obligation is a rule's due date with a basis (law, order, agency, contract, ours, asked, theirs), met with proof.
   Backend: backend/risk.py (GET/POST /api/v2/risks, GET/POST /api/v2/risks/<key>, GET /api/v2/matters,
   POST /api/v2/tasks/<id>/matter, GET /api/v2/risk/obligations), backend/org_rules.py
   (POST /api/v2/rule-due/<id>/satisfied).
   Addresses:
     #/risk                 Risks: live risks open with their matters and dates, then Open and Quiet, one line each
     #/risk/disputes        Disputes: every open dispute, then the closed ones
     #/risk/obligations     Obligations: Unconfirmed, Next 14 days, Later, Past 30 days
     #/risk/r/<slug>        one risk: Matters, Obligations, Rules, Sources and its page text
     …?matter=<task id>     the matter's facts in the side panel (it takes the page chat's place: PGC_HOLDS)
     …?due=<id>&on=<day>    one date in the side panel: where it comes from, its proof, Add proof, Waive, Missed
   A matter row opens its task; ⋯ on it opens the panel. A dot marks a matter that waits on the viewer (their Needs you).
   Live: a task event re-reads only the risks that task carries, their matters and, for a date's change, their dates;
   a date with no task sends no event, so the dates on screen are read again every few minutes.
   The route and the nav dot live in the shell (ui/app/pages-shell.ts). Classic script: globals are shared with ui/app/. */
'use strict';

type RkRisk = import('../types/api').Risk;
type RkRow = import('../types/api').MatterRow;
type RkMatter = import('../types/api').Matter;
type RkMoney = RkRisk['claimed'][number];
type RkLink = {link_id: string; id: string; text?: string; name?: string; type?: string; status?: string};
type RkDue = import('../types/api').RiskObligation;
type RkProof = NonNullable<NonNullable<RkDue['mark']>['proof']>;
type RkDetail = {risk: RkRisk; editable: boolean; matters: RkRow[]; next_offset: number | null; rules: RkLink[]; sources: RkLink[]; coverage: RkLink[]; obligations?: RkDue[]};
type RkTab = 'risks' | 'disputes' | 'obligations' | 'one';
// `dates`: the obligations on screen (every risk's on Obligations, the live risks' on Risks, the one risk's on its page).
type RkPage = {run: number; tab: RkTab; slug: string; risks: RkRisk[]; rows: RkRow[]; closed: RkRow[]; detail: RkDetail | null; dates: RkDue[]; loaded: boolean; offs: (() => void)[]};

const RK_NEW_DAYS = 7;   // as backend/risk.py NEW_DAYS: a matter this new is marked New and makes its risk live
const RK_KINDS: readonly [string, string][] = [['dispute', 'Dispute'], ['request', 'Request'], ['notice', 'Notice'], ['incident', 'Incident']];
const RK_BASIS: Readonly<Record<string, string>> = {law: 'Law', order: 'Order', agency: 'Agency', contract: 'Contract', ours: 'Ours', asked: 'Asked', theirs: 'Theirs'};
const RK_BINDING: ReadonlySet<string> = new Set(['law', 'order', 'agency', 'contract']);   // as backend/rule_due.py BINDING
const RK_DUE_GROUPS: readonly [RkDue['group'], string][] = [['unconfirmed', 'Unconfirmed'], ['next', 'Next 14 days'], ['later', 'Later'], ['past', 'Past 30 days']];
const RK_CAT_DATES = 6;
const RK_DETAIL_DATES = 200;      // as backend/risk.py LIMIT_MAX: the most dates a risk's page read holds          // a live risk shows this many of its dates; the rest are on its page
const RK_POLL_MS = 3 * 60000;    // dates with no task send no event: the ones on screen are read again this often
let RKP: RkPage | null = null;   // the page on screen: a late answer for a page left behind draws nothing
let RK_RUN = 0;

const rkSlug = (key: string): string => key.startsWith('risk:') ? key.slice(5) : key;
const rkHref = (key: string): string => `${RISK_ROUTE}/r/${encodeURIComponent(rkSlug(key))}`;
const rkApiKey = (slug: string): string => encodeURIComponent(slug.startsWith('risk:') ? slug : 'risk:' + slug);
const rkLive = (run: number): boolean => RKP !== null && RKP.run === run;
const rkQuery = (): URLSearchParams => new URLSearchParams((S.route || location.hash).split('?')[1] || '');
const rkTaskHref = (id: string): string => '#/task/' + encodeURIComponent(id);
const rkErr = (e: unknown): string => `<p class="rk-err" data-rk-error>${esc(errorMessage(e, 'Something went wrong.'))}</p>`;
const rkRules = (on: boolean): string => on ? '<span class="nav-icon rk-rules" role="img" aria-label="Rules" title="Rules">gavel</span>' : '';

// Money in minor units: exact on a row ("$19,244.88", "$50,000"), compact for a total ("$182k").
function rkMoney(amount: number, currency: string | null | undefined, compact = false): string {
  const cur = (currency || 'USD').toUpperCase(), major = amount / 100;
  const whole = Number.isInteger(major);
  try {
    const text = new Intl.NumberFormat('en-US', compact
      ? {style: 'currency', currency: cur, notation: 'compact', maximumFractionDigits: major >= 1000 ? 0 : 2, minimumFractionDigits: 0}
      : {style: 'currency', currency: cur, minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2}).format(major);
    return text.replace(/(\d)K\b/, '$1k');
  } catch {
    return `${major.toLocaleString('en-US')} ${cur}`;
  }
}
// The amounts claimed, one per currency: "$182k claimed", "$12k + €3k claimed"; '' when nothing is claimed.
function rkClaimed(claimed: readonly RkMoney[]): string {
  const shown = claimed.filter(m => m.amount > 0);
  return shown.length ? shown.map(m => rkMoney(m.amount, m.currency, true)).join(' + ') + ' claimed' : '';
}
// Live risks first (each opened with its matters), then open, then quiet, each by name as the server sorts them.
function rkGroups(risks: readonly RkRisk[]): Record<RkRisk['level'], RkRisk[]> {
  const out: Record<RkRisk['level'], RkRisk[]> = {live: [], open: [], quiet: []};
  for (const r of risks) out[r.level].push(r);
  return out;
}
const rkIsNew = (row: RkRow): boolean => {
  const at = Date.parse(row.matter?.created_at || row.task.created_at || '');
  return Number.isFinite(at) && Date.now() - at < RK_NEW_DAYS * 86400000;
};
// A matter's name: its party when the viewer may see it, else its task's title.
const rkName = (row: RkRow): string => row.matter?.party?.name || row.task.title || '';
const rkOpen = (row: RkRow): boolean => row.task.status === 'open';
function rkWaiting(): Set<string> {
  return new Set((S.v2?.needs || []).map(n => String(n.task_id || '')).filter(Boolean));
}
const rkAmount = (m: RkMatter | null): string => {
  if (!m) return '';
  const money = m.amount ? rkMoney(m.amount, m.currency) : '';
  return [money, m.claim || ''].filter(Boolean).join(' ');
};

function rkShell(tab: RkTab, head: string): string {
  return `<div class="rk" id="rk-root" data-rk-page="${tab}">${head}
    <div class="rk-body"><div class="rk-list" data-rk-body>${skelRows(5)}</div><aside class="rk-side" id="rk-side" aria-label="Matter" hidden></aside></div></div>`;
}
function rkStart(tab: RkTab, slug: string): RkPage | null {
  // The same page with only its panel changed: keep what is drawn and open or close the panel.
  if (RKP && RKP.tab === tab && RKP.slug === slug && document.getElementById('rk-root')) { void rkSideSync(); return null; }
  rkDrop();
  const page: RkPage = {run: ++RK_RUN, tab, slug, risks: [], rows: [], closed: [], detail: null, dates: [], loaded: false, offs: []};
  RKP = page;
  page.offs.push(liveOn('tasks', (e: {id?: unknown; task_id?: unknown; kinds?: unknown}) =>
    rkLiveTask(page, String(e.id || e.task_id || ''), Array.isArray(e.kinds) && e.kinds.includes('obligations'))));
  const poll = setInterval(() => { if (!document.hidden) void rkPoll(page); }, RK_POLL_MS);
  page.offs.push(() => clearInterval(poll));
  // Needs you changed: each task that started or stopped waiting on the viewer has its risks read again (a risk may
  // turn live or back), and the dots redraw from what the page holds.
  let waits = rkWaiting();
  page.offs.push(liveOn('needs-list', () => {
    if (!rkLive(page.run)) return;
    const now = rkWaiting();
    for (const id of new Set([...now, ...waits])) if (now.has(id) !== waits.has(id)) rkLiveTask(page, id);
    waits = now;
    rkDraw(page);
  }));
  return page;
}
function rkDrop(): void {
  if (!RKP) return;
  for (const off of RKP.offs) off();
  if (RK_LIVE.timer) clearTimeout(RK_LIVE.timer);
  RK_LIVE.timer = null;
  RK_LIVE.ids.clear();
  RK_LIVE.dates.clear();
  RKP = null;
}

async function pageRisk(tab: 'risks' | 'disputes' | 'obligations'): Promise<void> {
  const page = rkStart(tab, '');
  if (!page) return;
  const main = document.getElementById('main');
  if (!main) return;
  const tabs: PageTab[] = [
    {label: 'Risks', href: RISK_ROUTE, cur: tab === 'risks'},
    {label: 'Disputes', href: RISK_ROUTE + '/disputes', cur: tab === 'disputes'},
    {label: 'Obligations', href: RISK_ROUTE + '/obligations', cur: tab === 'obligations'},
  ];
  const head = pageHead({title: 'Risk', hook: 'rk-head', tabs, primary: '<button type="button" class="primary ph-go" data-rk-add>Add risk</button>', more: PAGE_HEAD_COPY});
  main.innerHTML = rkShell(tab, head);
  main.querySelector('[data-rk-add]')?.addEventListener('click', () => rkAddDialog());
  rkBind(page);
  void rkSideSync();
  try {
    if (tab === 'risks') {
      const [risks, open] = await Promise.all([
        get<{risks: RkRisk[]}>('/v2/risks', {fresh: true}),
        get<{matters: RkRow[]}>('/v2/matters?open=true&limit=200', {fresh: true}),
      ]);
      page.risks = risks.risks; page.rows = open.matters;
      // A live risk shows every open matter: one past the first 200 is read under its own risk.
      const short = page.risks.filter(r => r.level === 'live' && page.rows.filter(row => row.risks.some(x => x.id === r.id)).length < r.open);
      const more = await Promise.all(short.map(r => rkAll(`/v2/matters?risk=${encodeURIComponent(r.id)}&open=true`)));
      for (const row of more.flat()) if (!page.rows.some(x => x.task.id === row.task.id)) page.rows.push(row);
      page.dates = (await Promise.all(page.risks.filter(r => r.level === 'live').map(r => rkDatesAll(r.key)))).flat();
    } else if (tab === 'obligations') {
      page.dates = await rkDatesAll(null);
    } else {
      // Every open dispute, so the count and the total are whole; the latest closed ones under them.
      const [open, closed] = await Promise.all([
        rkAll('/v2/matters?kind=dispute&open=true'),
        get<{matters: RkRow[]}>('/v2/matters?kind=dispute&open=false&limit=50', {fresh: true}),
      ]);
      page.rows = open; page.closed = closed.matters;
    }
    page.loaded = true;
  } catch (e) {
    const body = main.querySelector('[data-rk-body]');
    if (rkLive(page.run) && body) body.innerHTML = rkErr(e);
    return;
  }
  if (rkLive(page.run)) rkDraw(page);
}

// Every page of a matter list, up to RK_ALL_MAX rows.
const RK_ALL_MAX = 2000;
async function rkAll(path: string): Promise<RkRow[]> {
  const rows: RkRow[] = [];
  let offset: number | null = 0;
  while (offset !== null && rows.length < RK_ALL_MAX) {
    const page: {matters: RkRow[]; next_offset: number | null} = await get(`${path}&limit=200&offset=${offset}`, {fresh: true});
    rows.push(...page.matters);
    offset = page.next_offset;
  }
  return rows;
}
// Every page of the dates of one risk (by its key), or of every risk with null, up to RK_ALL_MAX.
async function rkDatesAll(key: string | null): Promise<RkDue[]> {
  const out: RkDue[] = [];
  let offset: number | null = 0;
  while (offset !== null && out.length < RK_ALL_MAX) {
    const page: {obligations: RkDue[]; next_offset: number | null} = await get(`/v2/risk/obligations?${key ? `risk=${encodeURIComponent(key)}&` : ''}limit=200&offset=${offset}`, {fresh: true});
    out.push(...page.obligations);
    offset = page.next_offset;
  }
  return out;
}

async function pageRiskOne(slug: string): Promise<void> {
  const page = rkStart('one', slug);
  if (!page) return;
  const main = document.getElementById('main');
  if (!main) return;
  const back = {href: RISK_ROUTE, label: 'Risk'};
  main.innerHTML = rkShell('one', pageHead({title: 'Risk', hook: 'rk-head', back, more: PAGE_HEAD_COPY}));
  rkBind(page);
  void rkSideSync();
  try {
    page.detail = await get<RkDetail>(`/v2/risks/${rkApiKey(slug)}`, {fresh: true});
    page.dates = page.detail.obligations || [];
    // The page's read holds the first RK_DETAIL_DATES dates; a risk with more reads them all.
    if (page.dates.length >= RK_DETAIL_DATES) page.dates = page.detail.obligations = await rkDatesAll(page.detail.risk.key);
    page.loaded = true;
  } catch (e) {
    const body = main.querySelector('[data-rk-body]');
    if (rkLive(page.run) && body) body.innerHTML = rkErr(e);
    return;
  }
  if (rkLive(page.run)) rkDraw(page);
}

// The head of one risk: its name, and ⋯ with Edit when the viewer may change it.
function rkOneHead(d: RkDetail): void {
  const head = document.querySelector('#rk-root > .ph');
  if (!head) return;
  const more = (d.editable ? '<button type="button" data-rk-edit>Edit</button>' : '') + PAGE_HEAD_COPY;
  head.outerHTML = pageHead({title: d.risk.label, hook: 'rk-head', back: {href: RISK_ROUTE, label: 'Risk'}, more});
  document.querySelector('[data-rk-edit]')?.addEventListener('click', () => { if (RKP?.detail) rkEditDialog(RKP, RKP.detail); });
}

function rkDraw(page: RkPage): void {
  const body = document.querySelector<HTMLElement>('[data-rk-body]');
  if (!body) return;
  const waiting = rkWaiting();
  if (page.tab === 'risks') body.innerHTML = rkRisksHTML(page.risks, page.rows, waiting, page.dates);
  else if (page.tab === 'disputes') body.innerHTML = rkDisputesHTML(page.rows, page.closed, waiting);
  else if (page.tab === 'obligations') body.innerHTML = rkDatesHTML(page.dates);
  else if (page.detail) {
    if (document.querySelector('#rk-root .ph-t')?.textContent !== page.detail.risk.label || !document.querySelector('[data-rk-edit]') === page.detail.editable) rkOneHead(page.detail);
    body.innerHTML = rkOneHTML(page.detail, waiting, page.dates);
  }
  rkDueSideRedraw(page);
}

function rkRisksHTML(risks: readonly RkRisk[], open: readonly RkRow[], waiting: Set<string>, dates: readonly RkDue[] = []): string {
  if (!risks.length) return '<div class="empty" data-rk-empty>No risks yet.</div>';
  const under = (r: RkRisk) => open.filter(row => row.risks.some(x => x.id === r.id));
  const g = rkGroups(risks);
  const counts = (r: RkRisk) => [r.open ? `${r.open} open` : '', r.unconfirmed ? `${r.unconfirmed} unconfirmed` : '', rkClaimed(r.claimed)]
    .filter(Boolean).map(t => `<span>${esc(t)}</span>`).join('');
  const chev = (href: string, words: string) => `<a class="rk-row rk-cl" href="${href}"><span class="rk-mk"><span class="nav-icon" aria-hidden="true">chevron_right</span></span><span class="rk-t">${esc(words)}</span></a>`;
  // A live risk's dates sit beside its matters: the first few in the tab's order, the rest on its page.
  const datesOf = (r: RkRisk) => {
    const mine = rkDueSort(dates.filter(d => d.risks.some(x => x.id === r.id)));
    if (!mine.length) return '';
    return `<div class="rk-rows rk-dts" data-rk-dates>${mine.slice(0, RK_CAT_DATES).map(d => rkDueHTML(d, 'cat')).join('')}
      ${mine.length > RK_CAT_DATES ? chev(rkHref(r.key), 'All dates') : ''}</div>`;
  };
  const live = g.live.map(r => `<section class="rk-cat" data-rk-risk="${esc(r.key)}" data-rk-level="live">
      <h2><a href="${rkHref(r.key)}">${esc(r.label)}</a><em>${counts(r)}</em>${rkRules(r.has_rules)}</h2>
      <div class="rk-cat-b"><div class="rk-rows">${under(r).map(row => rkRowHTML(row, waiting, 'cat')).join('')}
        ${r.closed ? chev(rkHref(r.key), `${r.closed} closed`) : ''}</div>${datesOf(r)}</div>
    </section>`).join('');
  const line = (r: RkRisk, quiet: boolean) => {
    const what = quiet ? dayWords(r.next_date, {weekday: true}) : under(r).map(rkName).filter(Boolean).join(', ');
    const n = quiet ? (r.closed ? `${r.closed} closed` : 'None') : `${r.open} open`;
    return `<a class="rk-row rk-one${quiet ? ' q' : ''}" href="${rkHref(r.key)}" data-rk-risk="${esc(r.key)}" data-rk-level="${r.level}">
      <span class="rk-mk"></span><span class="rk-t">${esc(r.label)}</span><span class="rk-x">${esc(what)}</span><span class="rk-c">${esc(n)}</span><span class="rk-r">${rkRules(r.has_rules)}</span></a>`;
  };
  return live
    + (g.open.length ? `<div class="rk-lbl">Open</div><div class="rk-ones">${g.open.map(r => line(r, false)).join('')}</div>` : '')
    + (g.quiet.length ? `<div class="rk-lbl">Quiet</div><div class="rk-ones">${g.quiet.map(r => line(r, true)).join('')}</div>` : '');
}

// One matter: the dot when it waits on the viewer, its name, New or its forum, its stage, and ⋯ for its facts.
// `cat` is the short row of a live risk; `one` the row on a risk's page; `dispute` the Disputes table's.
function rkRowHTML(row: RkRow, waiting: Set<string>, shape: 'cat' | 'one' | 'dispute'): string {
  const t = row.task, m = row.matter, isNew = rkIsNew(row) && rkOpen(row);
  const dot = waiting.has(t.id) ? needsMarkHTML(10) : '';
  const pill = isNew ? '<span class="pill rk-pill">New</span>' : shape === 'cat' && m?.forum ? `<span class="pill rk-pill">${esc(m.forum)}</span>` : '';
  const more = `<button type="button" class="ghost rk-more" data-rk-matter="${esc(t.id)}" aria-label="Facts" title="Facts"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`;
  const name = `<a class="rk-t" href="${rkTaskHref(t.id)}">${esc(rkName(row))}</a>`;
  const stage = `<span class="rk-m">${esc(rkOpen(row) ? t.stage || '' : 'Closed')}</span>`;
  const face = t.owner_id ? `<span class="rk-o">${actorAvatar(t.owner_id, 16)}</span>` : '<span class="rk-o"></span>';
  const cls = `rk-row rk-matter${rkOpen(row) ? '' : ' rk-done'}`;
  if (shape === 'cat') return `<div class="${cls}" data-rk-task="${esc(t.id)}"><span class="rk-mk">${dot}</span>${name}${pill}${stage}${more}</div>`;
  if (shape === 'one') {
    return `<div class="${cls}" data-rk-task="${esc(t.id)}"><span class="rk-mk">${dot}</span>${name}${pill}
      <span class="rk-a">${esc(rkAmount(m))}</span><span class="rk-f">${esc(m?.forum || '')}</span>${stage}${face}${more}</div>`;
  }
  const risks = row.risks.map(r => esc(r.label)).join(', ');
  return `<div class="${cls}" data-rk-task="${esc(t.id)}"><span class="rk-mk">${dot}</span><span class="rk-tw">${name}${pill}</span>
    <span class="rk-k" data-label="Risk">${risks}</span><span class="rk-a" data-label="Amount">${esc(m?.amount ? rkMoney(m.amount, m.currency) : '')}</span>
    <span class="rk-f" data-label="Forum">${esc(m?.forum || '')}</span>${stage}<span class="rk-d" data-label="Next">${esc(rkOpen(row) ? dayWords(t.due_at, {weekday: true}) : '')}</span>${face}${more}</div>`;
}

function rkDisputesHTML(open: readonly RkRow[], closed: readonly RkRow[], waiting: Set<string>): string {
  if (!open.length && !closed.length) return '<div class="empty" data-rk-empty>No disputes.</div>';
  const sums = new Map<string, number>();
  // What the other side claims, as the risks' totals count it (backend/risk.py CLAIMED_SQL): not our own claims.
  for (const row of open) if (row.matter?.amount && row.matter.side !== 'ours') sums.set(row.matter.currency || 'USD', (sums.get(row.matter.currency || 'USD') || 0) + row.matter.amount);
  const claimed = rkClaimed([...sums].map(([currency, amount]) => ({currency, amount})));
  return `<div class="rk-table" data-rk-disputes>
      <div class="rk-row rk-colh"><span class="rk-mk"></span><span class="rk-tw">${open.length} open</span><span class="rk-k">Risk</span>
        <span class="rk-a">${esc(claimed)}</span><span class="rk-f">Forum</span><span class="rk-m">Stage</span><span class="rk-d">Next</span><span class="rk-o"></span><span class="rk-more-gap"></span></div>
      ${open.map(row => rkRowHTML(row, waiting, 'dispute')).join('')}
      ${closed.length ? `<div class="rk-lbl">Closed</div>${closed.map(row => rkRowHTML(row, waiting, 'dispute')).join('')}` : ''}
    </div>`;
}

function rkOneHTML(d: RkDetail, waiting: Set<string>, dates: readonly RkDue[] = []): string {
  const open = d.matters.filter(rkOpen), closed = d.matters.filter(row => !rkOpen(row));
  const claimed = rkClaimed(d.risk.claimed);
  const none = '<div class="rk-row rk-none"><span class="rk-mk"></span><span class="rk-t">None</span></div>';
  const rule = (r: RkLink) => `<a class="rk-row rk-rule" href="${RULES_ROUTE}?rule=${encodeURIComponent(r.id)}"><span class="rk-mk"><span class="nav-icon" aria-hidden="true">gavel</span></span>
    <span class="rk-t">${esc(r.text || '')}</span></a>`;
  const source = (s: RkLink) => `<a class="rk-row rk-src" href="${DIR_ROUTE}/${encodeURIComponent(s.id)}"><span class="rk-mk"><span class="nav-icon" aria-hidden="true">description</span></span>
    <span class="rk-t">${esc(s.name || '')}</span></a>`;
  return `<section class="rk-sec" data-rk-matters><div class="rk-gh">Matters${d.risk.open ? ` <em>${d.risk.open} open</em>` : ''}<span class="grow"></span>${claimed ? `<em>${esc(claimed)}</em>` : ''}</div>
      ${open.map(row => rkRowHTML(row, waiting, 'one')).join('') || (closed.length ? '' : none)}
      ${closed.length ? `<div class="rk-lbl">Closed</div>${closed.map(row => rkRowHTML(row, waiting, 'one')).join('')}` : ''}
      ${d.next_offset !== null ? '<button type="button" class="ghost small rk-next" data-rk-next>More</button>' : ''}</section>
    <section class="rk-sec" data-rk-obligations><div class="rk-gh">Obligations</div>${rkDueSort(dates).map(x => rkDueHTML(x, 'one')).join('') || none}</section>
    <div class="rk-cols">
      <section class="rk-sec" data-rk-rules><div class="rk-gh">Rules</div>${d.rules.map(rule).join('') || none}</section>
      <section class="rk-sec" data-rk-sources><div class="rk-gh">Sources</div>${d.sources.map(source).join('') || none}
        ${d.coverage.length ? `<div class="rk-gh rk-gh2">Coverage</div>${d.coverage.map(source).join('')}` : ''}</section>
    </div>
    ${d.risk.markdown ? `<section class="rk-sec rk-text" data-rk-text>${safeMd(d.risk.markdown)}</section>` : ''}`;
}

// The tab's order, as backend/risk.py _order: unconfirmed and past newest first (by the day or its mark, whichever is
// later), next and later soonest first. The server sends them so; a date patched in live is put back in its place.
function rkDueSort(dates: readonly RkDue[]): RkDue[] {
  const at = (d: RkDue) => d.group === 'next' || d.group === 'later' ? d.due_on : '';
  const recent = (d: RkDue) => { const marked = (d.mark?.satisfied_at || '').slice(0, 10); return marked > d.due_on ? marked : d.due_on; };
  const g = (d: RkDue) => RK_DUE_GROUPS.findIndex(([k]) => k === d.group);
  return [...dates].sort((a, b) => g(a) - g(b) || (at(a) ? at(a).localeCompare(at(b)) : recent(b).localeCompare(recent(a))) || a.id.localeCompare(b.id));
}
const rkDueKey = (d: {id: string; due_on: string}): string => `${d.id}|${d.due_on}`;
function rkHost(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}
// A mark's proof: a link by its host, a file by its name, a sent email as Sent mail. One the viewer cannot open keeps
// its kind and nothing else (the server sends no ref and no name).
function rkProofHTML(p: RkProof | null | undefined): string {
  if (!p) return '';
  const icon = p.kind === 'email' ? 'mail' : p.kind === 'file' ? 'description' : 'link';
  const words = p.kind === 'email' ? 'Sent mail' : p.kind === 'file' ? p.name || 'File' : p.ref ? rkHost(p.ref) : 'Link';
  const face = `<span class="nav-icon" aria-hidden="true">${icon}</span><span>${esc(words)}</span>`;
  if (p.kind === 'link' && p.ref) return `<a class="rk-pf" href="${esc(p.ref)}" target="_blank" rel="noopener noreferrer" title="${esc(p.ref)}">${face}</a>`;
  if (p.kind === 'file' && p.ref) return `<a class="rk-pf" href="${API}/v2/files/${encodeURIComponent(p.ref)}" target="_blank" rel="noopener" title="${esc(words)}">${face}</a>`;
  return `<span class="rk-pf"${p.name ? ` title="${esc(p.name)}"` : ''}>${face}</span>`;
}
// What an occurrence's last column says: Unconfirmed (loud), a tick with the day and who met it, Waived with who,
// Missed, Passed, the owner's face while it is due, and nothing for a date that is theirs to meet.
function rkDueState(d: RkDue): string {
  const m = d.mark;
  if (d.state === 'unconfirmed') return '<span class="rk-un">Unconfirmed</span>';
  if (d.state === 'met' && m) return `<span class="nav-icon rk-tick" role="img" aria-label="Met">check</span><span>${esc(dayWords(m.satisfied_at, {weekday: true}))}</span>${actorAvatar(m.satisfied_by, 16)}`;
  if (d.state === 'waived') return `<span>Waived</span>${m ? actorAvatar(m.satisfied_by, 16) : ''}`;
  if (d.state === 'missed') return '<span>Missed</span>';
  if (d.state === 'passed') return '<span>Passed</span>';
  if (d.state === 'due' && d.owner && d.basis !== 'theirs') return actorAvatar(d.owner, 16);
  return '';
}
// One date: its day, what is due, (its risks on the tab), its basis, its proof, its state, and ⋯ for the panel. A date
// that binds us (law, order, agency, contract) reads in ink; one the other side asked for or must meet reads grey; an
// unconfirmed binding date is bold. `cat` is the short row beside a live risk's matters.
function rkDueHTML(d: RkDue, shape: 'tab' | 'cat' | 'one'): string {
  const tone = RK_BINDING.has(d.basis) ? ' rk-bind' : d.basis === 'asked' || d.basis === 'theirs' ? ' rk-soft' : '';
  const loud = d.state === 'unconfirmed' && RK_BINDING.has(d.basis) ? ' rk-loud' : '';
  const risks = shape === 'tab' ? `<span class="rk-k">${esc(d.risks.map(r => r.label).join(', '))}</span>` : '';
  const proof = shape === 'cat' ? '' : `<span class="rk-ev">${rkProofHTML(d.mark?.proof)}</span>`;
  const more = `<button type="button" class="ghost rk-more" aria-label="Date" title="Date"><span class="nav-icon" aria-hidden="true">more_horiz</span></button>`;
  return `<div class="rk-row rk-due${tone}${loud}" data-rk-due="${esc(d.id)}" data-rk-on="${esc(d.due_on)}" data-rk-state="${esc(d.state || 'watched')}">
    <span class="rk-mk"></span><span class="rk-rd">${esc(dayWords(d.due_on, {weekday: true}))}</span><span class="rk-t">${esc(d.title || d.rule.text)}</span>${risks}
    <span class="rk-bs">${esc(RK_BASIS[d.basis] || '')}</span>${proof}<span class="rk-os">${rkDueState(d)}</span>${more}</div>`;
}
// Obligations: Unconfirmed, Next 14 days, Later, Past 30 days, each under its label.
function rkDatesHTML(dates: readonly RkDue[]): string {
  if (!dates.length) return '<div class="empty" data-rk-empty>No dates.</div>';
  const sorted = rkDueSort(dates);
  return `<div class="rk-table rk-dues" data-rk-dates>
      <div class="rk-row rk-colh"><span class="rk-mk"></span><span class="rk-rd"></span><span class="rk-t"></span><span class="rk-k">Risk</span>
        <span class="rk-bs">Basis</span><span class="rk-ev">Proof</span><span class="rk-os"></span><span class="rk-more-gap"></span></div>
      ${RK_DUE_GROUPS.map(([g, label]) => {
        const rows = sorted.filter(d => d.group === g);
        return rows.length ? `<div class="rk-lbl" data-rk-group="${g}">${label}</div>${rows.map(d => rkDueHTML(d, 'tab')).join('')}` : '';
      }).join('')}
    </div>`;
}

function rkBind(page: RkPage): void {
  const root = document.getElementById('rk-root');
  if (!root) return;
  root.addEventListener('click', ev => {
    const at = eventElement(ev);
    if (!at || !rkLive(page.run)) return;
    const facts = at.closest<HTMLElement>('[data-rk-matter]');
    if (facts) { ev.preventDefault(); location.hash = rkWithAll({matter: facts.dataset['rkMatter'] || '', due: '', on: ''}); return; }
    if (at.closest('[data-rk-next]')) { void rkMore(page); return; }
    // A date's row, or its ⋯, opens it in the panel; its proof link opens the proof.
    const due = at.closest<HTMLElement>('[data-rk-due]');
    if (due && !at.closest('a')) { location.hash = rkWithAll({due: due.dataset['rkDue'] || '', on: due.dataset['rkOn'] || '', matter: ''}); return; }
    // The whole row opens its task, as its name does.
    const row = at.closest<HTMLElement>('.rk-matter');
    if (row && !at.closest('a, button, input, select, textarea')) { const id = row.dataset['rkTask']; if (id) location.hash = rkTaskHref(id); }
  });
}
// The address with query keys set (or removed with '').
function rkWithAll(set: Readonly<Record<string, string>>): string {
  const [path = ''] = (S.route || location.hash).split('?'), q = rkQuery();
  for (const [key, value] of Object.entries(set)) { if (value) q.set(key, value); else q.delete(key); }
  const rest = q.toString();
  return path + (rest ? '?' + rest : '');
}
const rkWith = (key: string, value: string): string => rkWithAll({[key]: value});
// The address with the panel closed, whichever it shows.
const rkClosed = (): string => rkWithAll({matter: '', due: '', on: ''});
async function rkMore(page: RkPage): Promise<void> {
  const d = page.detail;
  if (!d || d.next_offset === null) return;
  try {
    const next = await get<{matters: RkRow[]; next_offset: number | null}>(`/v2/matters?risk=${encodeURIComponent(d.risk.id)}&limit=50&offset=${d.next_offset}`, {fresh: true});
    if (!rkLive(page.run)) return;
    d.matters = [...d.matters, ...next.matters.filter(row => !d.matters.some(x => x.task.id === row.task.id))];
    d.next_offset = next.next_offset;
    rkDraw(page);
  } catch (e) { toast(errorMessage(e, 'Something went wrong.'), true); }
}
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || !RKP || document.querySelector('dialog[open]')) return;
  const side = document.getElementById('rk-side');
  if (side && !side.hidden) { ev.preventDefault(); location.hash = rkClosed(); }
});

// Task events, gathered for a moment: each task's risks are read again (GET /v2/risks?task=), with the matters of the
// risks it carries now and carried before, and the page redraws from what it holds. `dates`: the event was about the
// task's own dates (kind obligations), so the dates of its risks are read again too.
const RK_LIVE: {ids: Set<string>; dates: Set<string>; timer: ReturnType<typeof setTimeout> | null} = {ids: new Set(), dates: new Set(), timer: null};
function rkLiveTask(page: RkPage, id: string, dates = false): void {
  if (!id || !rkLive(page.run)) return;
  RK_LIVE.ids.add(id);
  if (dates) RK_LIVE.dates.add(id);
  if (RK_LIVE.timer) return;
  RK_LIVE.timer = setTimeout(() => {
    RK_LIVE.timer = null;
    const ids = [...RK_LIVE.ids], dated = new Set(RK_LIVE.dates);
    RK_LIVE.ids.clear();
    RK_LIVE.dates.clear();
    void rkPatch(page, ids, dated);
  }, 400);
}
const rkShown = (page: RkPage): RkRow[] => page.detail ? page.detail.matters : [...page.rows, ...page.closed];
async function rkPatch(page: RkPage, ids: readonly string[], dated: ReadonlySet<string> = new Set()): Promise<void> {
  const redate = new Map<string, string>();   // risk id -> key: the risks whose dates are read again after the loop
  let unknown = false;   // a date changed on a task with no risk: it may sit under a risk by its rule alone
  for (const id of ids) {
    if (!rkLive(page.run)) return;
    let now: RkRisk[];
    try { now = (await get<{risks: RkRisk[]}>(`/v2/risks?task=${encodeURIComponent(id)}`, {fresh: true})).risks; } catch { now = []; }
    const before = rkShown(page).find(row => row.task.id === id)?.risks.map(r => r.id) || [];
    // The task's dates go with its risks: when they changed, or the task gained or lost a risk, those risks' dates are
    // read again (the ones it carries now, carried before, and the ones its dates on screen are listed under).
    const held = page.dates.filter(d => d.task?.id === id).flatMap(d => d.risks);
    if (dated.has(id) || held.length || now.map(r => r.id).sort().join() !== [...before].sort().join()) {
      for (const r of [...now, ...held]) redate.set(r.id, r.key);
      for (const rid of before) if (!redate.has(rid)) { const key = rkKeyOf(page, rid); if (key) redate.set(rid, key); }
    }
    if (dated.has(id) && !now.length && !held.length && !before.length) unknown = true;
    if (!now.length && !before.length) continue;   // not a matter, then or now
    let row: RkRow | null = null;
    if (now.length) {
      try { row = (await get<{matters: RkRow[]}>(`/v2/matters?task=${encodeURIComponent(id)}&limit=1`, {fresh: true})).matters[0] || null; }
      catch { continue; }   // unread: the row stays as it was rather than vanish
    }
    // A risk the task left: its own counts again, read with one matter (the counts are the risk's, not the page's).
    const left: RkRisk[] = [];
    for (const rid of before.filter(r => !now.some(x => x.id === r))) {
      try { left.push((await get<{risk: RkRisk}>(`/v2/risks/${encodeURIComponent(rid)}?limit=1`, {fresh: true})).risk); } catch { /* gone */ }
    }
    if (!rkLive(page.run)) return;
    rkApply(page, id, row, [...now, ...left]);
  }
  if (unknown) {
    for (const r of page.risks) if (r.level === 'live') redate.set(r.id, r.key);
    if (page.detail) redate.set(page.detail.risk.id, page.detail.risk.key);
  }
  if (redate.size || (unknown && page.tab === 'obligations')) await rkDatesFor(page, redate);
  if (rkLive(page.run)) rkDraw(page);
}
// A risk's key from its id, from what the page holds.
function rkKeyOf(page: RkPage, id: string): string {
  return page.risks.find(r => r.id === id)?.key || (page.detail?.risk.id === id ? page.detail.risk.key : '')
    || page.dates.flatMap(d => d.risks).find(r => r.id === id)?.key || '';
}
// The dates of these risks ({id: key}) read again and put in place of the ones the page holds under them. Only the
// risks whose dates are on screen: on Risks the live ones (one that just turned live gets its dates here), on one
// risk's page that risk, on Obligations every one.
async function rkDatesFor(page: RkPage, risks: ReadonlyMap<string, string>): Promise<void> {
  // Obligations lists every date: one read of them all keeps each date's full list of risks.
  if (page.tab === 'obligations') {
    try { const all = await rkDatesAll(null); if (rkLive(page.run)) page.dates = all; } catch { /* unread: as they were */ }
    return;
  }
  const shown = [...risks].filter(([id]) => (page.tab === 'risks' && page.risks.some(r => r.id === id && r.level === 'live'))
    || (page.tab === 'one' && page.detail?.risk.id === id));
  for (const [id, key] of shown) {
    let fresh: RkDue[];
    try { fresh = await rkDatesAll(key); } catch { continue; }   // unread: the dates stay as they were
    if (!rkLive(page.run)) return;
    // A read by risk lists only that risk on each date, so a date under other risks too keeps them: this read says
    // only whether the date is under this one.
    const was = new Map(page.dates.map(d => [rkDueKey(d), d]));
    const now = new Set(fresh.map(rkDueKey));
    const next: RkDue[] = [];
    for (const d of page.dates) {
      if (now.has(rkDueKey(d))) continue;
      const others = d.risks.filter(r => r.id !== id);
      if (others.length === d.risks.length) next.push(d);
      else if (others.length) next.push({...d, risks: others});
    }
    for (const d of fresh) {
      const others = (was.get(rkDueKey(d))?.risks || []).filter(r => r.id !== id && !d.risks.some(x => x.id === r.id));
      next.push(others.length ? {...d, risks: [...d.risks, ...others]} : d);
    }
    page.dates = next;
  }
  if (page.detail) page.detail.obligations = page.dates;
}
// Every few minutes, for the dates no task event tells of: the risks' levels and counts and the dates on screen.
async function rkPoll(page: RkPage): Promise<void> {
  if (!rkLive(page.run) || !page.loaded) return;
  try {
    if (page.tab === 'risks') {
      const risks = (await get<{risks: RkRisk[]}>('/v2/risks', {fresh: true})).risks;
      const dates = (await Promise.all(risks.filter(r => r.level === 'live').map(r => rkDatesAll(r.key)))).flat();
      if (!rkLive(page.run)) return;
      page.risks = risks; page.dates = dates;
    } else if (page.tab === 'obligations') {
      const dates = await rkDatesAll(null);
      if (!rkLive(page.run)) return;
      page.dates = dates;
    } else if (page.tab === 'one' && page.detail) {
      const dates = await rkDatesAll(page.detail.risk.key);
      if (!rkLive(page.run) || !page.detail) return;
      page.dates = page.detail.obligations = dates;
    } else return;
  } catch { return; }   // the next poll tries again
  rkDraw(page);
}
// Puts one task's row and its risks' counts into what the page holds, where its filters say they belong.
function rkApply(page: RkPage, id: string, row: RkRow | null, risks: readonly RkRisk[]): void {
  const drop = (rows: RkRow[]) => rows.filter(x => x.task.id !== id);
  const put = (rows: RkRow[], keep: boolean) => {
    const at = rows.findIndex(x => x.task.id === id);
    if (!row || !keep) return drop(rows);
    if (at >= 0) { const next = [...rows]; next[at] = row; return next; }
    return [row, ...rows];
  };
  rkApplyRisks(page, risks);
  if (page.tab === 'risks') {
    page.rows = put(page.rows, !!row && rkOpen(row));
  } else if (page.tab === 'obligations') {
    // No matter rows here: the dates are read under their risks (rkDatesFor).
  } else if (page.tab === 'disputes') {
    const dispute = row?.matter?.kind === 'dispute';
    page.rows = put(page.rows, dispute && !!row && rkOpen(row));
    page.closed = put(page.closed, dispute && !!row && !rkOpen(row));
  } else if (page.detail) {
    const d = page.detail, mine = !!row && row.risks.some(r => r.id === d.risk.id);
    d.matters = put(d.matters, mine);
    d.matters.sort((a, b) => Number(!rkOpen(a)) - Number(!rkOpen(b)));
  }
}

// Risks read again put in place of the ones the page holds, in the server's order (live, open, quiet, then by name).
function rkApplyRisks(page: RkPage, risks: readonly RkRisk[]): void {
  for (const r of risks) {
    const at = page.risks.findIndex(x => x.id === r.id);
    if (at >= 0) page.risks[at] = r;
    // The page text and its version stay as read: an edit sends both, so one changed elsewhere meets a conflict.
    if (page.detail && page.detail.risk.id === r.id) page.detail.risk = {...r, markdown: page.detail.risk.markdown ?? '', version: page.detail.risk.version};
  }
  const rank = (r: RkRisk) => ['live', 'open', 'quiet'].indexOf(r.level);
  page.risks = [...page.risks].sort((a, b) => rank(a) - rank(b) || a.label.toLowerCase().localeCompare(b.label.toLowerCase()));
}

type RkTaskView = {id: string; title?: string; matter?: RkMatter | null};
async function rkSideSync(): Promise<void> {
  const side = document.getElementById('rk-side'), root = document.getElementById('rk-root');
  if (!side || !root || !RKP) return;
  const q = rkQuery(), tid = q.get('matter') || '', due = q.get('due') || '';
  const key = tid ? 'matter:' + tid : due ? `due:${due}|${q.get('on') || ''}` : '';
  root.classList.toggle('rk-open', !!key);
  side.hidden = !key;
  side.setAttribute('aria-label', due && !tid ? 'Date' : 'Matter');
  if (!key) { side.innerHTML = ''; delete side.dataset['rkFor']; return; }
  if (side.dataset['rkFor'] === key) return;
  side.dataset['rkFor'] = key;
  if (!tid) { rkDueSide(RKP, side); return; }
  const close = rkClosed();
  const headHTML = (title: string) => rkSideHead(title, close);
  side.innerHTML = headHTML('Facts') + `<div class="rk-pad">${skelRows(4)}</div>`;
  const run = RKP.run;
  let task: RkTaskView;
  try {
    task = (await get<{task: RkTaskView}>(`/v2/tasks/${encodeURIComponent(tid)}`, {fresh: true})).task;
  } catch (e) {
    if (rkLive(run) && side.dataset['rkFor'] === key) side.innerHTML = headHTML('Facts') + `<div class="rk-pad">${rkErr(e)}</div>`;
    return;
  }
  if (!rkLive(run) || side.dataset['rkFor'] !== key) return;
  const m = task.matter || null;
  side.innerHTML = headHTML(task.title || 'Facts') + rkFormHTML(m, close);
  rkFormBind(side, tid, m, run);
  side.querySelector<HTMLElement>('select, input')?.focus({preventScroll: true});
}
const rkSideHead = (title: string, close: string): string => `<div class="rk-side-h"><h2>${esc(title)}</h2><span class="grow"></span><a class="rk-close" href="${esc(close)}" aria-label="Close" title="Close"><span class="nav-icon" aria-hidden="true">close</span></a></div>`;

// The date the address names (due and on), from what the page holds.
function rkDueNamed(page: RkPage): RkDue | null {
  const q = rkQuery(), id = q.get('due') || '', on = q.get('on') || '';
  return page.dates.find(d => d.id === id && (!on || d.due_on === on)) || null;
}
// One date in the panel: its facts and, for whoever may mark it, Missed, Waive and Add proof. Drawn from the dates the
// page holds; before they are read it waits, and a date the page does not hold (out of the list, or not the viewer's
// to see) says so.
function rkDueSide(page: RkPage, side: HTMLElement): void {
  const d = rkDueNamed(page), close = rkClosed();
  side.dataset['rkWait'] = d || page.loaded ? '' : '1';
  if (!d) { side.innerHTML = rkSideHead('Date', close) + `<div class="rk-pad">${page.loaded ? '<p class="rk-err">Not found.</p>' : skelRows(4)}</div>`; return; }
  side.dataset['rkShown'] = JSON.stringify(d);
  side.innerHTML = rkSideHead(d.title || 'Date', close) + rkDueFactsHTML(d) + rkDueActsHTML(d);
  rkDueBind(page, side, d);
}
// After a redraw: the panel's date drawn again when it was waiting for the dates or anything on it changed, unless the
// viewer is typing in it.
function rkDueSideRedraw(page: RkPage): void {
  const side = document.getElementById('rk-side');
  if (!side || side.hidden || !side.dataset['rkFor']?.startsWith('due:') || side.querySelector('[data-rk-act-form]')) return;
  const d = rkDueNamed(page);
  const shown = d ? JSON.stringify(d) : '';
  if (side.dataset['rkWait'] || shown !== side.dataset['rkShown']) rkDueSide(page, side);
}
function rkDueFactsHTML(d: RkDue): string {
  const kv = (label: string, value: string) => value ? `<div class="rk-kv"><span>${esc(label)}</span><div>${value}</div></div>` : '';
  const who = (actor: string) => `${actorAvatar(actor, 16)}<span>${esc(actorLabel(actor))}</span>`;
  const m = d.mark, source = d.rule.source && typeof d.rule.source['name'] === 'string' ? d.rule.source['name'] : '';
  const repeats = d.repeats && d.repeats !== 'once' ? `<span class="rk-note">${esc(d.repeats[0]?.toUpperCase() + d.repeats.slice(1))}</span>` : '';
  const outcome = m ? {met: 'Met', waived: 'Waived', missed: 'Missed'}[m.outcome] : '';
  const state = d.state === 'unconfirmed' ? '<span class="rk-un">Unconfirmed</span>' : d.state === 'passed' ? 'Passed' : '';
  return `<div class="rk-pad rk-facts" data-rk-due-facts>
      ${kv('Due', `<span>${esc(dayWords(d.due_on, {weekday: true}))}</span>${repeats}`)}
      ${kv('Basis', RK_BASIS[d.basis] ? `<span>${esc(RK_BASIS[d.basis] || '')}</span>${source ? `<span class="rk-note">${esc(source)}</span>` : ''}` : '')}
      ${kv('Rule', `<a href="${RULES_ROUTE}?rule=${encodeURIComponent(d.rule.id)}">${esc(d.rule.text)}</a>`)}
      ${kv('Matter', d.task ? `<a class="pill" href="${rkTaskHref(d.task.id)}">${esc(d.task.title || d.task.label || '')}</a>` : '')}
      ${kv('Risk', d.risks.map(r => `<a class="pill" href="${rkHref(r.key)}">${esc(r.label)}</a>`).join(''))}
      ${kv('Owner', d.owner ? who(d.owner) : '')}
      ${kv('State', m && d.state !== 'unconfirmed' ? '' : state)}
      ${m ? kv(outcome, `<span>${esc(dayWords(m.satisfied_at, {weekday: true}))}</span>${who(m.satisfied_by)}`) : ''}
      ${kv('Proof', rkProofHTML(m?.proof))}
      ${kv('Note', m?.note ? esc(m.note) : '')}
    </div>`;
}
// Missed, Waive (a person, with a note) and Add proof, for whoever may mark the date, while it has no proof. A date
// that is theirs to meet is only watched.
function rkDueActsHTML(d: RkDue): string {
  if (!d.can_satisfy || d.basis === 'theirs' || d.mark?.proof) return '';
  const now = d.mark?.outcome;
  return `<div class="rk-foot rk-acts" data-rk-acts>
      ${now === 'missed' ? '' : '<button type="button" class="ghost" data-rk-act="missed">Missed</button>'}
      ${d.can_waive && now !== 'waived' ? '<button type="button" class="ghost" data-rk-act="waived">Waive</button>' : ''}
      <button type="button" class="primary" data-rk-act="proof">Add proof</button></div>`;
}
function rkActFormHTML(act: string): string {
  const fields = act === 'proof'
    ? `<label><span>Proof</span><select name="kind"><option value="link">Link</option><option value="file">File</option></select></label>
       <label data-rk-kind="link"><span>Link</span><input name="link" type="url" inputmode="url" autocomplete="off" placeholder="https://"></label>
       <label data-rk-kind="file" hidden><span>File</span><span class="rk-party"><input name="file_q" type="search" autocomplete="off" placeholder="Files" aria-label="File">
         <input name="file" type="hidden"><span class="rk-found" data-rk-found hidden></span></span></label>`
    : `<label><span>Note</span><textarea name="note" rows="3" maxlength="2000"${act === 'waived' ? ' required' : ''}></textarea></label>`;
  const go = act === 'proof' ? 'Save' : act === 'waived' ? 'Waive' : 'Missed';
  return `<form class="rk-form" data-rk-act-form="${act}" novalidate>${fields}<p class="rk-err" data-error hidden></p>
      <div class="rk-foot"><button type="button" class="ghost" data-rk-cancel>Cancel</button><button type="submit" class="primary">${go}</button></div></form>`;
}
function rkDueBind(page: RkPage, side: HTMLElement, d: RkDue): void {
  const acts = side.querySelector<HTMLElement>('[data-rk-acts]');
  acts?.addEventListener('click', ev => {
    const act = eventElement(ev)?.closest<HTMLElement>('[data-rk-act]')?.dataset['rkAct'];
    if (!act) return;
    acts.outerHTML = rkActFormHTML(act);
    const form = side.querySelector<HTMLFormElement>('[data-rk-act-form]');
    if (form) rkActBind(page, side, d, form, act);
  });
}
function rkActBind(page: RkPage, side: HTMLElement, d: RkDue, form: HTMLFormElement, act: string): void {
  const err = form.querySelector<HTMLElement>('[data-error]');
  const kind = form.querySelector<HTMLSelectElement>('[name=kind]');
  const fail = (words: string) => { if (err) { err.textContent = words; err.hidden = false; } };
  const show = () => form.querySelectorAll<HTMLElement>('[data-rk-kind]').forEach(el => { el.hidden = el.dataset['rkKind'] !== kind?.value; });
  kind?.addEventListener('change', () => { show(); form.querySelector<HTMLElement>(`[data-rk-kind="${kind.value}"] input`)?.focus(); });
  form.querySelector('[data-rk-cancel]')?.addEventListener('click', () => rkDueSide(page, side));
  rkFilePick(form, page.run);
  form.querySelector<HTMLElement>('input:not([type=hidden]):not([hidden]), textarea')?.focus({preventScroll: true});
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const data = new FormData(form), text = (k: string) => String(data.get(k) ?? '').trim();
    const body: Record<string, unknown> = {due_on: d.due_on};
    if (act === 'proof') {
      const k = kind?.value === 'file' ? 'file' : 'link', ref = text(k);
      if (!ref) return fail(k === 'file' ? 'Pick a file.' : 'Paste a link.');
      if (k === 'link' && !/^https:\/\/\S+$/i.test(ref)) return fail('A link starts with https://');
      body['proof'] = {kind: k, ref};
    } else {
      if (act === 'waived' && !text('note')) return fail('Say why.');
      body['outcome'] = act;
      if (text('note')) body['note'] = text('note');
    }
    const go = form.querySelector<HTMLButtonElement>('[type=submit]');
    const mine = side.dataset['rkFor'];
    if (err) err.hidden = true;
    if (go) go.disabled = true;
    try {
      await post(`/v2/rule-due/${encodeURIComponent(d.id)}/satisfied`, body);
    } catch (e) {
      // Proved by someone else meanwhile (409): their proof is read and shown in place of the form.
      if ((e as {status?: number}).status !== 409) {
        if (go) go.disabled = false;
        return fail(errorMessage(e, 'Something went wrong.'));
      }
    }
    if (!rkLive(page.run)) return;
    // Its risks' dates and counts read again: the date may move group, and a risk may turn quiet.
    await rkDatesFor(page, new Map(d.risks.map(r => [r.id, r.key])));
    const counts = await Promise.all(d.risks.map(r => get<{risk: RkRisk}>(`/v2/risks/${encodeURIComponent(r.key)}?limit=1`, {fresh: true}).then(x => x.risk).catch(() => null)));
    if (!rkLive(page.run)) return;
    rkApplyRisks(page, counts.filter((r): r is RkRisk => r !== null));
    rkDraw(page);
    // The panel is drawn again only while it still shows this date: the viewer may have opened another meanwhile.
    if (side.dataset['rkFor'] === mine && form.isConnected) rkDueSide(page, side);
  });
}
// The file search under Add proof: the files the viewer may read, by title (GET /v2/files/search).
function rkFilePick(form: HTMLFormElement, run: number): void {
  const q = form.querySelector<HTMLInputElement>('[name=file_q]'), id = form.querySelector<HTMLInputElement>('[name=file]');
  const found = form.querySelector<HTMLElement>('[data-rk-found]');
  if (!q || !id || !found) return;
  let ask = 0, timer: ReturnType<typeof setTimeout> | null = null;
  q.addEventListener('input', () => {
    id.value = '';
    if (timer) clearTimeout(timer);
    const text = q.value.trim();
    if (!text) { ask++; found.hidden = true; found.innerHTML = ''; return; }
    timer = setTimeout(async () => {
      const mine = ++ask;
      const rows = await get<{results: {id: string; title: string}[]}>(`/v2/files/search?q=${encodeURIComponent(text)}&limit=6`).then(r => r.results).catch(() => []);
      if (mine !== ask || !rkLive(run)) return;
      found.innerHTML = rows.length
        ? rows.map(r => `<button type="button" class="rk-pick" data-id="${esc(r.id)}" data-name="${esc(r.title)}">${esc(r.title)}</button>`).join('')
        : '<span class="rk-none-found">None</span>';
      found.hidden = false;
    }, 200);
  });
  found.addEventListener('click', ev => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-id]');
    if (!pick) return;
    id.value = pick.dataset['id'] || '';
    q.value = pick.dataset['name'] || '';
    found.hidden = true;
  });
}

function rkFormHTML(m: RkMatter | null, close: string): string {
  const amount = m && m.amount !== null && m.amount !== undefined ? String(m.amount / 100) : '';
  return `<form class="rk-form" data-rk-form novalidate>
      <label><span>Kind</span><select name="kind"><option value="">—</option>${RK_KINDS.map(([k, w]) => `<option value="${k}"${m?.kind === k ? ' selected' : ''}>${w}</option>`).join('')}</select></label>
      <label><span>Party</span><span class="rk-party"><input name="party_q" type="search" autocomplete="off" placeholder="Contacts" aria-label="Party" value="${esc(m?.party?.name || '')}">
        <input name="party" type="hidden" value="${esc(m?.party_entity_id || '')}"><span class="rk-found" data-rk-found hidden></span></span></label>
      <label><span>Amount</span><span class="rk-amt"><input name="amount" inputmode="decimal" autocomplete="off" aria-label="Amount" value="${esc(amount)}">
        <input name="currency" maxlength="3" autocomplete="off" aria-label="Currency" value="${esc((m?.currency || 'USD').toUpperCase())}"></span></label>
      <label><span>Forum</span><input name="forum" maxlength="200" autocomplete="off" value="${esc(m?.forum || '')}"></label>
      <p class="rk-err" data-error hidden></p>
      <div class="rk-foot"><a class="btn ghost" href="${esc(close)}">Cancel</a><button type="submit" class="primary">Save</button></div>
    </form>`;
}
// Minor units from what was typed ("1,240.50" is 124050), or null for a blank, or NaN for anything else.
function rkMinor(typed: string): number | null {
  const plain = typed.replace(/[,\s$€£]/g, '');
  if (!plain) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(plain)) return NaN;
  return Math.round(Number(plain) * 100);
}
function rkFormBind(side: HTMLElement, tid: string, m: RkMatter | null, run: number): void {
  const form = side.querySelector<HTMLFormElement>('[data-rk-form]');
  const err = side.querySelector<HTMLElement>('[data-error]'), found = side.querySelector<HTMLElement>('[data-rk-found]');
  if (!form || !err || !found) return;
  const q = form.querySelector<HTMLInputElement>('[name=party_q]'), pid = form.querySelector<HTMLInputElement>('[name=party]');
  let ask = 0, timer: ReturnType<typeof setTimeout> | null = null;
  q?.addEventListener('input', () => {
    if (pid) pid.value = '';
    if (timer) clearTimeout(timer);
    const text = q.value.trim();
    if (!text) { ask++; found.hidden = true; found.innerHTML = ''; return; }
    timer = setTimeout(async () => {
      const mine = ++ask;
      const one = (kind: string) => get<{rows: {id: string; name: string}[]}>(`/v2/entities/find/${kind}?name=${encodeURIComponent(text)}&limit=5`).then(r => r.rows).catch(() => []);
      const rows = [...await one('person'), ...await one('organization')];
      if (mine !== ask || !rkLive(run)) return;
      found.innerHTML = rows.length
        ? rows.map(r => `<button type="button" class="rk-pick" data-id="${esc(r.id)}" data-name="${esc(r.name)}">${esc(r.name)}</button>`).join('')
        : '<span class="rk-none-found">None</span>';
      found.hidden = false;
    }, 200);
  });
  found.addEventListener('click', ev => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-id]');
    if (!pick || !q || !pid) return;
    pid.value = pick.dataset['id'] || '';
    q.value = pick.dataset['name'] || '';
    found.hidden = true;
  });
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const data = new FormData(form), text = (k: string) => String(data.get(k) ?? '').trim();
    const amount = rkMinor(text('amount')), currency = text('currency').toUpperCase();
    const fail = (words: string) => { err.textContent = words; err.hidden = false; };
    if (Number.isNaN(amount)) return fail('Amount is a number.');
    if (amount !== null && !/^[A-Z]{3}$/.test(currency)) return fail('Currency is three letters.');
    if (text('party_q') && !text('party')) return fail('Pick a party from Contacts.');
    // Only the facts that changed: a party the viewer may not see reads as none, and saving another fact keeps it.
    const was = {kind: m?.kind || '', party: m?.party_entity_id || '', amount: m?.amount ?? null, currency: (m?.currency || '').toUpperCase(), forum: m?.forum || ''};
    const body: Record<string, unknown> = {};
    if (text('kind') !== was.kind) body['kind'] = text('kind') || null;
    if (text('party') !== was.party && (text('party') || text('party_q') !== (m?.party?.name || ''))) body['party_entity_id'] = text('party') || null;
    if (amount !== was.amount || (amount !== null && currency !== was.currency)) { body['amount'] = amount; body['currency'] = amount === null ? null : currency; }
    if (text('forum') !== was.forum) body['forum'] = text('forum') || null;
    if (!Object.keys(body).length) { location.hash = rkWith('matter', ''); return; }
    if (m) body['version'] = m.version;
    const go = form.querySelector<HTMLButtonElement>('[type=submit]');
    err.hidden = true;
    if (go) go.disabled = true;
    try {
      await post(`/v2/tasks/${encodeURIComponent(tid)}/matter`, body);
    } catch (e) {
      if (go) go.disabled = false;
      return fail(errorMessage(e, 'Something went wrong.'));
    }
    if (!rkLive(run) || !RKP) return;
    location.hash = rkWith('matter', '');
    void rkPatch(RKP, [tid]);
  });
}

function rkDialog(title: string, fields: string, save: string, work: (form: FormData) => Promise<void>): void {
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal rk-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><h2 class="tmodal-title">${esc(title)}</h2><button type="button" class="ghost tmodal-x" data-close aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button></div>
    <form class="tmodal-body rk-form" novalidate>${fields}<p class="rk-err" data-error hidden></p>
      <div class="rk-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(save)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll<HTMLElement>('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector<HTMLElement>('[data-error]'), go = dlg.querySelector<HTMLButtonElement>('[type=submit]');
  if (!form || !err || !go) return;
  form.onsubmit = async ev => {
    ev.preventDefault();
    err.hidden = true; go.disabled = true;
    try { await work(new FormData(form)); dlg.close(); }
    catch (e) { err.textContent = errorMessage(e, 'Something went wrong.'); err.hidden = false; go.disabled = false; }
  };
  dlg.showModal();
  form.querySelector<HTMLElement>('input, textarea')?.focus();
}
function rkAddDialog(): void {
  rkDialog('Add risk', '<input name="label" maxlength="200" aria-label="Name" placeholder="Name" required autocomplete="off">', 'Add', async form => {
    const label = String(form.get('label') ?? '').trim();
    if (!label) throw new Error('Give it a name.');
    const made = await post('/v2/risks', {label}) as {risk: RkRisk};
    location.hash = rkHref(made.risk.key);
  });
}
function rkEditDialog(page: RkPage, d: RkDetail): void {
  const fields = `<input name="label" maxlength="200" aria-label="Name" placeholder="Name" required autocomplete="off" value="${esc(d.risk.label)}">
    <textarea name="markdown" rows="8" maxlength="20000" aria-label="Page" placeholder="Page">${esc(d.risk.markdown || '')}</textarea>`;
  rkDialog('Edit', fields, 'Save', async form => {
    const label = String(form.get('label') ?? '').trim();
    if (!label) throw new Error('Give it a name.');
    const out = await post(`/v2/risks/${encodeURIComponent(d.risk.key)}`, {version: d.risk.version, label, markdown: String(form.get('markdown') ?? '')}) as {risk: RkRisk};
    if (!rkLive(page.run) || !page.detail) return;
    page.detail.risk = out.risk;
    rkOneHead(page.detail);
    rkDraw(page);
  });
}

// Leaving the page drops its state and its live listeners, so a late answer draws nothing.
function riskStop(r: string): void {
  if (!rdUnder((r || '').split('?')[0] ?? '', RISK_ROUTE)) rkDrop();
}
