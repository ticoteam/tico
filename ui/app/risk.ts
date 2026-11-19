/* ui/app/risk.ts — Company › Risk: the risks the company carries and the matters under them.
   A risk is a tag of kind risk; a matter is a task tagged with one, with its facts (kind, party, amount, forum).
   Backend: backend/risk.py (GET/POST /api/v2/risks, GET/POST /api/v2/risks/<key>, GET /api/v2/matters,
   POST /api/v2/tasks/<id>/matter).
   Addresses:
     #/risk                 Risks: live risks open with their matters, then Open and Quiet, one line each
     #/risk/disputes        Disputes: every open dispute, then the closed ones
     #/risk/r/<slug>        one risk: Matters, Rules, Sources and its page text
     …?matter=<task id>     the matter's facts in the side panel (it takes the page chat's place: PGC_HOLDS)
   A matter row opens its task; ⋯ on it opens the panel. A dot marks a matter that waits on the viewer (their Needs you).
   Live: a task event re-reads only the risks that task carries and their matters, never the whole page.
   The route and the nav dot live in the shell (ui/app/pages-shell.ts). Classic script: globals are shared with ui/app/. */
'use strict';

type RkRisk = import('../types/api').Risk;
type RkRow = import('../types/api').MatterRow;
type RkMatter = import('../types/api').Matter;
type RkMoney = RkRisk['claimed'][number];
type RkLink = {link_id: string; id: string; text?: string; name?: string; type?: string; status?: string};
type RkDetail = {risk: RkRisk; editable: boolean; matters: RkRow[]; next_offset: number | null; rules: RkLink[]; sources: RkLink[]; coverage: RkLink[]};
type RkTab = 'risks' | 'disputes' | 'one';
type RkPage = {run: number; tab: RkTab; slug: string; risks: RkRisk[]; rows: RkRow[]; closed: RkRow[]; detail: RkDetail | null; offs: (() => void)[]};

const RK_NEW_DAYS = 7;   // as backend/risk.py NEW_DAYS: a matter this new is marked New and makes its risk live
const RK_KINDS: readonly [string, string][] = [['dispute', 'Dispute'], ['request', 'Request'], ['notice', 'Notice'], ['incident', 'Incident']];
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

// ----------------------------------------------------------------- words
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

// ----------------------------------------------------------------- the page
function rkShell(tab: RkTab, head: string): string {
  return `<div class="rk" id="rk-root" data-rk-page="${tab}">${head}
    <div class="rk-body"><div class="rk-list" data-rk-body>${skelRows(5)}</div><aside class="rk-side" id="rk-side" aria-label="Matter" hidden></aside></div></div>`;
}
function rkStart(tab: RkTab, slug: string): RkPage | null {
  // The same page with only its panel changed: keep what is drawn and open or close the panel.
  if (RKP && RKP.tab === tab && RKP.slug === slug && document.getElementById('rk-root')) { void rkSideSync(); return null; }
  rkDrop();
  const page: RkPage = {run: ++RK_RUN, tab, slug, risks: [], rows: [], closed: [], detail: null, offs: []};
  RKP = page;
  page.offs.push(liveOn('tasks', (e: {id?: unknown; task_id?: unknown}) => rkLiveTask(page, String(e.id || e.task_id || ''))));
  page.offs.push(liveOn('needs-list', () => { if (rkLive(page.run)) rkDraw(page); }));
  return page;
}
function rkDrop(): void {
  if (!RKP) return;
  for (const off of RKP.offs) off();
  if (RK_LIVE.timer) clearTimeout(RK_LIVE.timer);
  RK_LIVE.ids.clear();
  RKP = null;
}

async function pageRisk(tab: 'risks' | 'disputes'): Promise<void> {
  const page = rkStart(tab, '');
  if (!page) return;
  const main = document.getElementById('main');
  if (!main) return;
  const tabs: PageTab[] = [
    {label: 'Risks', href: RISK_ROUTE, cur: tab === 'risks'},
    {label: 'Disputes', href: RISK_ROUTE + '/disputes', cur: tab === 'disputes'},
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
    } else {
      const [open, closed] = await Promise.all([
        get<{matters: RkRow[]}>('/v2/matters?kind=dispute&open=true&limit=200', {fresh: true}),
        get<{matters: RkRow[]}>('/v2/matters?kind=dispute&open=false&limit=50', {fresh: true}),
      ]);
      page.rows = open.matters; page.closed = closed.matters;
    }
  } catch (e) {
    const body = main.querySelector('[data-rk-body]');
    if (rkLive(page.run) && body) body.innerHTML = rkErr(e);
    return;
  }
  if (rkLive(page.run)) rkDraw(page);
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
  if (page.tab === 'risks') body.innerHTML = rkRisksHTML(page.risks, page.rows, waiting);
  else if (page.tab === 'disputes') body.innerHTML = rkDisputesHTML(page.rows, page.closed, waiting);
  else if (page.detail) {
    if (document.querySelector('#rk-root .ph-t')?.textContent !== page.detail.risk.label || !document.querySelector('[data-rk-edit]') === page.detail.editable) rkOneHead(page.detail);
    body.innerHTML = rkOneHTML(page.detail, waiting);
  }
}

// ----------------------------------------------------------------- Risks
function rkRisksHTML(risks: readonly RkRisk[], open: readonly RkRow[], waiting: Set<string>): string {
  if (!risks.length) return '<div class="empty" data-rk-empty>No risks yet.</div>';
  const under = (r: RkRisk) => open.filter(row => row.risks.some(x => x.id === r.id));
  const g = rkGroups(risks);
  const counts = (r: RkRisk) => [r.open ? `${r.open} open` : '', rkClaimed(r.claimed)].filter(Boolean).map(t => `<span>${esc(t)}</span>`).join('');
  const live = g.live.map(r => `<section class="rk-cat" data-rk-risk="${esc(r.key)}" data-rk-level="live">
      <h2><a href="${rkHref(r.key)}">${esc(r.label)}</a><em>${counts(r)}</em>${rkRules(r.has_rules)}</h2>
      <div class="rk-rows">${under(r).map(row => rkRowHTML(row, waiting, 'cat')).join('')}
        ${r.closed ? `<a class="rk-row rk-cl" href="${rkHref(r.key)}"><span class="rk-mk"><span class="nav-icon" aria-hidden="true">chevron_right</span></span><span class="rk-t">${r.closed} closed</span></a>` : ''}</div>
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

// ----------------------------------------------------------------- Disputes
function rkDisputesHTML(open: readonly RkRow[], closed: readonly RkRow[], waiting: Set<string>): string {
  if (!open.length && !closed.length) return '<div class="empty" data-rk-empty>No disputes.</div>';
  const sums = new Map<string, number>();
  for (const row of open) if (row.matter?.amount) sums.set(row.matter.currency || 'USD', (sums.get(row.matter.currency || 'USD') || 0) + row.matter.amount);
  const claimed = rkClaimed([...sums].map(([currency, amount]) => ({currency, amount})));
  return `<div class="rk-table" data-rk-disputes>
      <div class="rk-row rk-colh"><span class="rk-mk"></span><span class="rk-tw">${open.length} open</span><span class="rk-k">Risk</span>
        <span class="rk-a">${esc(claimed)}</span><span class="rk-f">Forum</span><span class="rk-m">Stage</span><span class="rk-d">Next</span><span class="rk-o"></span><span class="rk-more-gap"></span></div>
      ${open.map(row => rkRowHTML(row, waiting, 'dispute')).join('')}
      ${closed.length ? `<div class="rk-lbl">Closed</div>${closed.map(row => rkRowHTML(row, waiting, 'dispute')).join('')}` : ''}
    </div>`;
}

// ----------------------------------------------------------------- one risk
function rkOneHTML(d: RkDetail, waiting: Set<string>): string {
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
    <div class="rk-cols">
      <section class="rk-sec" data-rk-rules><div class="rk-gh">Rules</div>${d.rules.map(rule).join('') || none}</section>
      <section class="rk-sec" data-rk-sources><div class="rk-gh">Sources</div>${d.sources.map(source).join('') || none}
        ${d.coverage.length ? `<div class="rk-gh rk-gh2">Coverage</div>${d.coverage.map(source).join('')}` : ''}</section>
    </div>
    ${d.risk.markdown ? `<section class="rk-sec rk-text" data-rk-text>${safeMd(d.risk.markdown)}</section>` : ''}`;
}

// ----------------------------------------------------------------- events
function rkBind(page: RkPage): void {
  const root = document.getElementById('rk-root');
  if (!root) return;
  root.addEventListener('click', ev => {
    const at = eventElement(ev);
    if (!at || !rkLive(page.run)) return;
    const facts = at.closest<HTMLElement>('[data-rk-matter]');
    if (facts) { ev.preventDefault(); location.hash = rkWith('matter', facts.dataset['rkMatter'] || ''); return; }
    if (at.closest('[data-rk-next]')) { void rkMore(page); return; }
    // The whole row opens its task, as its name does.
    const row = at.closest<HTMLElement>('.rk-matter');
    if (row && !at.closest('a, button, input, select, textarea')) { const id = row.dataset['rkTask']; if (id) location.hash = rkTaskHref(id); }
  });
}
// The address with one query key set (or removed with '').
function rkWith(key: string, value: string): string {
  const [path = ''] = (S.route || location.hash).split('?'), q = rkQuery();
  if (value) q.set(key, value); else q.delete(key);
  const rest = q.toString();
  return path + (rest ? '?' + rest : '');
}
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
  if (side && !side.hidden) { ev.preventDefault(); location.hash = rkWith('matter', ''); }
});

// ----------------------------------------------------------------- live
// Task events, gathered for a moment: each task's risks are read again (GET /v2/risks?task=), with the matters of the
// risks it carries now and carried before, and the page redraws from what it holds.
const RK_LIVE: {ids: Set<string>; timer: ReturnType<typeof setTimeout> | null} = {ids: new Set(), timer: null};
function rkLiveTask(page: RkPage, id: string): void {
  if (!id || !rkLive(page.run)) return;
  RK_LIVE.ids.add(id);
  if (RK_LIVE.timer) return;
  RK_LIVE.timer = setTimeout(() => {
    RK_LIVE.timer = null;
    const ids = [...RK_LIVE.ids].slice(0, 20);
    RK_LIVE.ids.clear();
    void rkPatch(page, ids);
  }, 400);
}
const rkShown = (page: RkPage): RkRow[] => page.detail ? page.detail.matters : [...page.rows, ...page.closed];
async function rkPatch(page: RkPage, ids: readonly string[]): Promise<void> {
  for (const id of ids) {
    if (!rkLive(page.run)) return;
    let now: RkRisk[];
    try { now = (await get<{risks: RkRisk[]}>(`/v2/risks?task=${encodeURIComponent(id)}`, {fresh: true})).risks; } catch { now = []; }
    const before = rkShown(page).find(row => row.task.id === id)?.risks.map(r => r.id) || [];
    if (!now.length && !before.length) continue;   // not a matter, then or now
    let row: RkRow | null = null;
    const home = now[0];
    if (home) {
      try {
        const found = await get<{matters: RkRow[]}>(`/v2/matters?risk=${encodeURIComponent(home.id)}&limit=200`, {fresh: true});
        row = found.matters.find(x => x.task.id === id) || null;
      } catch { row = null; }
    }
    // A risk the task left: its own counts again, read with one matter (the counts are the risk's, not the page's).
    const left: RkRisk[] = [];
    for (const rid of before.filter(r => !now.some(x => x.id === r))) {
      try { left.push((await get<{risk: RkRisk}>(`/v2/risks/${encodeURIComponent(rid)}?limit=1`, {fresh: true})).risk); } catch { /* gone */ }
    }
    if (!rkLive(page.run)) return;
    rkApply(page, id, row, [...now, ...left]);
  }
  if (rkLive(page.run)) rkDraw(page);
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
  for (const r of risks) {
    const at = page.risks.findIndex(x => x.id === r.id);
    if (at >= 0) page.risks[at] = r;
    if (page.detail && page.detail.risk.id === r.id) page.detail.risk = {...r, markdown: page.detail.risk.markdown ?? ''};
  }
  if (page.tab === 'risks') {
    page.rows = put(page.rows, !!row && rkOpen(row));
    page.risks = [...page.risks].sort((a, b) => ['live', 'open', 'quiet'].indexOf(a.level) - ['live', 'open', 'quiet'].indexOf(b.level)
      || a.label.toLowerCase().localeCompare(b.label.toLowerCase()));
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

// ----------------------------------------------------------------- the matter's facts (side panel)
type RkTaskView = {id: string; title?: string; matter?: RkMatter | null};
async function rkSideSync(): Promise<void> {
  const side = document.getElementById('rk-side'), root = document.getElementById('rk-root');
  if (!side || !root || !RKP) return;
  const tid = rkQuery().get('matter') || '';
  root.classList.toggle('rk-open', !!tid);
  side.hidden = !tid;
  if (!tid) { side.innerHTML = ''; delete side.dataset['rkFor']; return; }
  if (side.dataset['rkFor'] === tid) return;
  side.dataset['rkFor'] = tid;
  const close = rkWith('matter', '');
  const headHTML = (title: string) => `<div class="rk-side-h"><h2>${esc(title)}</h2><span class="grow"></span><a class="rk-x" href="${esc(close)}" aria-label="Close">✕</a></div>`;
  side.innerHTML = headHTML('Facts') + `<div class="rk-pad">${skelRows(4)}</div>`;
  const run = RKP.run;
  let task: RkTaskView;
  try {
    task = (await get<{task: RkTaskView}>(`/v2/tasks/${encodeURIComponent(tid)}`, {fresh: true})).task;
  } catch (e) {
    if (rkLive(run) && side.dataset['rkFor'] === tid) side.innerHTML = headHTML('Facts') + `<div class="rk-pad">${rkErr(e)}</div>`;
    return;
  }
  if (!rkLive(run) || side.dataset['rkFor'] !== tid) return;
  const m = task.matter || null;
  side.innerHTML = headHTML(task.title || 'Facts') + rkFormHTML(m, close);
  rkFormBind(side, tid, m, run);
  side.querySelector<HTMLElement>('select, input')?.focus({preventScroll: true});
}
function rkFormHTML(m: RkMatter | null, close: string): string {
  const amount = m?.amount ? String(m.amount / 100) : '';
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
    if (!text) { found.hidden = true; found.innerHTML = ''; return; }
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
    const body: Record<string, unknown> = {
      kind: text('kind') || null, party_entity_id: text('party') || null, amount, currency: amount === null ? null : currency, forum: text('forum') || null,
    };
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

// ----------------------------------------------------------------- dialogs
function rkDialog(title: string, fields: string, save: string, work: (form: FormData) => Promise<void>): void {
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal rk-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="grow"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
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
