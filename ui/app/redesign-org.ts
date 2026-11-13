/* ui/app/redesign-org.ts — Strategy › Org (redesign PLAN §6 wave 6): the org chart, top down, the same chart on a phone
   (pinch and pan) and a desktop. The owner on top; under them the teammates outside groups and every top-level group,
   a group a dashed outline with its name on a tab that folds it. Inside a group the lead comes first and teammates hang
   under whoever they report to. The built-in bots sit in a Built-in group, folded until someone opens it; explicitly
   marked helpers are off the chart.
   Tapping a teammate opens a sheet: their home group, and Move to… for the owner (any group) and a lead (the
   groups it leads), which moves them at once (PATCH /api/v2/groups/{id}). A built-in bot has no Move to… (the server
   refuses it). Anyone else sees the group only; org changes beyond that are asked of the person's Assistant, in words
   (redesign CONTRACT §3).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** The org page's state (RDO, below): the groups GET /v2/groups gave, the zoom, the folded group ids, and whether the
 *  person has zoomed or panned by hand. */
interface RdoState {
  groups: OrgGroup[] | null;
  groupsTried?: boolean;
  zoom: number | null;
  folded: Set<string>;
  manual?: boolean;
  cvW?: number | null;
  cvH?: number | null;
  ro?: ResizeObserver;
}
/** A chart's transform: its scale and its offset in the pane. */
interface RdoPose { x: number; y: number; s: number }
/** Per-draw context: the nodes drawn so far, and (Rules › Internal) a group renderer in place of rdoGroupBlock. */
type RdoCtx = {seen: Set<string>; group?: typeof rdoGroupBlock};

const RDO_FOLDED_KEY = 'tico.org.chart.folded.v2';
let RDO: RdoState | null = null;             // {groups: [GET /v2/groups rows], zoom, folded: Set of group ids, manual: zoomed or panned by hand}
let RDO_SEQ = 0;
// The General group starts folded. A chart saved under the key before it (v1) keeps the folds it had, plus this one.
const rdoFoldedLoad = (): Set<string> => {
  try {
    const saved = localStorage.getItem(RDO_FOLDED_KEY);
    if (saved === null) return new Set<string>([...JSON.parse(localStorage.getItem('tico.org.chart.folded') || '[]'), BUILT_IN_GROUP]);
    return new Set<string>(JSON.parse(saved));
  } catch { return new Set<string>([BUILT_IN_GROUP]); }
};
const rdoFoldedSave = () => { try { if (!RDO) return; localStorage.setItem(RDO_FOLDED_KEY, JSON.stringify([...RDO.folded])); } catch {} };

// The groups with their leads: GET /v2/groups when it has answered (a list, or {groups} from an older hub), else the
// roster's copy, which carries no leads.
const rdoGroups = (): OrgGroup[] => RDO?.groups || S.orgGroups || [];
// Ready once the groups' answer is in; if it failed, the sheet falls back to the roster's groups as before.
const rdoReady = () => !!S.me && (RDO?.groups != null || !!RDO?.groupsTried);
const rdoGroup = (id: string): OrgGroup | null => rdoGroups().find(g => g.id === id) || (S.orgGroups || []).find(g => g.id === id) || null;
const rdoGroupName = (id: string): string => id ? rdoGroup(id)?.name || id : 'No group';
const rdoRef = (n: OrgNode): string => n.kind === 'person' ? 'person:' + n.id : 'bot:' + n.id;
const rdoKey = (n: OrgNode): string => n.kind === 'group' ? 'g:' + n.id : n.kind === 'person' ? 'p:' + n.id : 'b:' + n.id;
const rdoName = (n: OrgNode): string => n.kind === 'group' ? n.name ?? '' : n.kind === 'person' ? (n.person?.name || n.id) : botDisplayName(n.id);
function rdoRefName(ref: string): string {
  const [kind, id] = [String(ref).split(':')[0], String(ref).slice(String(ref).indexOf(':') + 1)];
  if (kind === 'person' || kind === 'human') return (S.people || []).find(p => p.id === id)?.name || id;
  return botDisplayName(id);
}
// A teammate's home group: the `team` its roster row carries (backend/groups.py), else the group whose members list (GET
// /v2/groups `bots`) holds the bot, which is where a built-in bot is.
function rdoHome(ref: string): string {
  const id = ref.slice(ref.indexOf(':') + 1);
  if (ref.startsWith('person:')) return (S.people || []).find(p => p.id === id)?.team || '';
  return botRow(id)?.team || rdoGroups().find((g: {id: string; bots?: string[]}) => (g.bots || []).includes(id))?.id || '';
}
const rdoLeadOf = (ref: string): string[] => rdoGroups().filter(g => g.lead === ref).map(g => g.id);
// The leads of a group and of every group above it.
function rdoLeadsAbove(gid: string): string[] {
  const out: string[] = [];
  for (let g = rdoGroup(gid), hops = 0; g && hops < 20; g = g.parent ? rdoGroup(g.parent) : null, hops++) if (g.lead) out.push(g.lead);
  return out;
}
// The groups this viewer may move a teammate of `from` into: every one (and No group) for the owner and bot admins,
// who edit groups in the sidebar; for a lead over `from`, the groups it leads and those below (backend/groups.py
// `_lead_move`); else none. The hub has the last word.
function rdoMoveTargets(from: string): string[] | null {
  if (orgCanGroups()) return null;
  const me = 'person:' + (S.me?.id || ''), mine = (g: string) => rdoLeadsAbove(g).includes(me);
  return from && mine(from) ? rdoGroups().filter(g => mine(g.id)).map(g => g.id) : [];
}

// ----------------------------------------------------------------- the tree
// The sidebar's chart (orgTreeByParent: groups, reports_to) without helpers (the Strategy Owner too) and recruiting seats,
// drawn as an org chart: the top person (the owner nobody is above, wherever their group is) at the root, and every
// top-level group under the one it answers to (orgGroupHead), so a department sits under the person who runs it.
// The top person's own group is the top of the chart, not an outline: whoever is in it hangs from them. `leadOf` gives a
// group's lead (Rules › Internal reads the groups itself).
function rdoTree(leadOf: (id: string) => string = id => rdoGroup(id)?.lead || ''): OrgByParent {
  const by: OrgByParent = {};
  for (const [k, kids] of Object.entries(orgTreeWithHelpers(orgTreeByParent()))) by[k] = kids.filter(n => n.kind !== 'seat' && !(n.kind === 'group' && n.helpers));
  rdoMembers(by);
  orgChartShape(by, rdoKey, leadOf);
  by[''] = by[''] || [];
  return by;
}
// The members the server lists for each group (GET /v2/groups `bots`) that the chart's own rules left out: the built-in
// bots (the General Manager's team), which orgTreeWithHelpers takes off the tree, and a group the roster does not carry yet. A bot already on the chart
// stays where it is, and a bot the caller may not see is left out here as it is in the tree.
function rdoMembers(by: OrgByParent): void {
  const kidsOf = (key: string): OrgNode[] => {
    const kids = by[key] || [];
    by[key] = kids;
    return kids;
  };
  const placed = new Set<string>(), groups = new Set<string>();
  for (const kids of Object.values(by)) for (const n of kids) {
    if (n.kind === 'bot') placed.add(n.id);
    if (n.kind === 'group') groups.add(n.id);
  }
  for (const g of rdoGroups()) {
    if (!groups.has(g.id)) { kidsOf(g.parent ? 'g:' + g.parent : '').push({kind: 'group', id: g.id, name: g.name, order: g.order || 0}); groups.add(g.id); }
    for (const id of g.bots || []) {
      const e = S.emps.find(row => row.id === id);
      if (!e || placed.has(id) || !orgBranchVisible(e) || !orgMineKeep(e)) continue;
      placed.add(id);
      // A member sits under the member it reports to (the General Manager's reports under it), else in the group itself.
      const boss = orgBossKey({kind: 'bot', ...e});
      kidsOf(boss.startsWith('b:') && boss !== 'b:' + id && (g.bots || []).includes(boss.slice(2)) ? boss : 'g:' + g.id).push({kind: 'bot', ...e});
    }
  }
  // A built-in in no group (the Assistant) hangs from the person it reports to.
  for (const e of S.emps) {
    if (placed.has(e.id) || !isBuiltInBot(e.id) || e.team || !orgBranchVisible(e) || !orgMineKeep(e)) continue;
    const boss = orgBossKey({kind: 'bot', ...e});
    if (boss.startsWith('p:')) { placed.add(e.id); kidsOf(boss).push({kind: 'bot', ...e}); }
  }
}
// The server keeps the built-in bots in their group and refuses to move one (422), so the sheet offers no Move to….
const rdoBuiltInBot = (id: string): boolean => (rdoGroups().find((g: {id: string; bots?: string[]}) => g.id === BUILT_IN_GROUP)?.bots || []).includes(id);
const rdoTopPerson = () => orgChartTop();
function rdoTop(by: OrgByParent): OrgPersonNode | null {
  const roots = (by[''] || []).filter((n): n is OrgPersonNode => n.kind === 'person'), person = rdoTopPerson();
  return roots.find(n => n.id === person?.id) || roots.find(n => n.id === S.me?.owner_id) || roots.find(n => n.person?.role === 'owner') || roots[0] || null;
}
// People first, then teammates (in the chart's order), then groups by their order; in a group its lead first.
function rdoSorted(kids: OrgNode[], gid: string, lead: string | undefined = gid ? rdoGroup(gid)?.lead : ''): OrgNode[] {
  const rank = (n: OrgNode) => n.kind === 'group' ? 2 : lead && rdoRef(n) === lead ? -1 : n.kind === 'person' ? 0 : 1;
  return kids.map((n, i): [OrgNode, number] => [n, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || (a[0].kind === 'group' && b[0].kind === 'group' ? (a[0].order || 0) - (b[0].order || 0) : 0) || a[1] - b[1]).map(x => x[0]);
}
function rdoCount(by: OrgByParent, key: string, seen: Set<string> = new Set<string>()): number {
  if (seen.has(key) || seen.size > 2000) return 0;
  seen.add(key);
  return (by[key] || []).reduce((t, n) => t + (n.kind === 'group' ? 0 : 1) + rdoCount(by, rdoKey(n), seen), 0);
}

// ----------------------------------------------------------------- drawing
function rdoCard(n: OrgNode, {top = false}: {top?: boolean; [key: string]: unknown} = {}): string {
  const ref = rdoRef(n), name = rdoName(n);
  const lead = rdoLeadOf(ref).length > 0;
  const bot = n.kind === 'bot' ? botRow(n.id) : null;
  const title = n.kind === 'person' ? String(n.person?.title || '').trim() : '';
  const role = title || (top ? 'Owner' : lead ? 'Lead' : String(bot?.role || '').trim().length <= 24 ? String(bot?.role || '').trim() : '');
  const av = n.kind === 'person' ? personAvatar(n.person || {name}, 16) : avatar(n.id, 16);
  const cls = ['rdo-card', n.kind === 'person' && 'person', top && 'top', lead && 'lead'].filter(Boolean).join(' ');
  const label = title ? `${name} · ${title}` : name;
  return `<button type="button" class="${cls}" data-rdo-ref="${esc(ref)}" aria-label="${esc(label)}" title="${esc(label)}"><b>${esc(name)}</b>
    <span class="rdo-r">${av}${role ? `<span class="rdo-rl">${esc(role)}</span>` : ''}</span></button>`;
}
// A teammate and, under it on a rail, whoever reports to it inside the same group. A person who heads people or groups
// has them side by side underneath instead, as the top person does. `ctx.group` draws a group in place of
// rdoGroupBlock (Rules › Internal: always open, its tab selects it).
function rdoNode(by: OrgByParent, n: OrgNode, ctx: RdoCtx, depth: number): string {
  if (depth > 14 || ctx.seen.has(rdoKey(n))) return '';
  ctx.seen.add(rdoKey(n));
  if (n.kind === 'group') return (ctx.group || rdoGroupBlock)(by, n, ctx, depth);
  const kids = rdoSorted(by[rdoKey(n)] || [], '');
  if (n.kind === 'person' && kids.some(k => k.kind === 'person' || k.kind === 'group')) {
    const cols = kids.map(k => rdoNode(by, k, ctx, depth + 1)).filter(Boolean);
    return `<div class="rdo-br"><div class="rdo-top has-row">${rdoCard(n, ctx)}</div><ul class="rdo-row">${cols.map(c => `<li>${c}</li>`).join('')}</ul></div>`;
  }
  const stack = kids.length ? `<ul class="rdo-stack">${kids.map(k => `<li class="${k.kind === 'group' ? 'is-g' : ''}">${rdoNode(by, k, ctx, depth + 1)}</li>`).join('')}</ul>` : '';
  return rdoCard(n, ctx) + stack;
}
function rdoGroupBlock(by: OrgByParent, g: OrgGroup, ctx: RdoCtx, depth: number): string {
  const folded = RDO?.folded.has(g.id), kids = rdoSorted(by['g:' + g.id] || [], g.id);
  const count = rdoCount(by, 'g:' + g.id);
  const body = kids.map(k => `<div class="rdo-gi${k.kind === 'group' ? ' is-g' : ''}">${rdoNode(by, k, ctx, depth + 1)}</div>`).join('');
  return `<section class="rdo-g${folded ? ' folded' : ''}" data-rdo-group="${esc(g.id)}">
    <button type="button" class="rdo-tab" data-rdo-fold="${esc(g.id)}" aria-expanded="${folded ? 'false' : 'true'}" title="${esc(g.name)}"><span class="rdo-cx" aria-hidden="true">›</span><span class="rdo-tn">${esc(g.name)}</span><span class="rdo-n">${count}</span></button>
    <div class="rdo-gb">${body}</div></section>`;
}
function rdoChartHtml(by: OrgByParent = rdoTree(), ctx: RdoCtx = {seen: new Set<string>()}): string {
  const top = rdoTop(by);
  const row = top ? [...(by[rdoKey(top)] || []), ...(by[''] || []).filter(n => rdoKey(n) !== rdoKey(top))] : (by[''] || []);
  if (top) ctx.seen.add(rdoKey(top));
  // The General group is a column in the row when the Rules map draws it (ctx.group). On the Strategy chart it sits under
  // the row, centred on the owner: a folded tab then never widens the row, so the owner stays over the teams' middle.
  type ChartNode = OrgNode;
  const inTray = (n: ChartNode) => !ctx.group && n.kind === 'group' && n.id === BUILT_IN_GROUP;
  const tray: ChartNode[] = row.filter(inTray);
  const cols = rdoSorted(row.filter((n: ChartNode) => !inTray(n)), '').map((n: ChartNode) => rdoNode(by, n, ctx, 0)).filter(Boolean);
  const under = tray.map(n => rdoNode(by, n, ctx, 0)).join('');
  if (!top && !cols.length) return '<p class="muted rd-empty">No one on the chart yet.</p>';
  return (top ? `<div class="rdo-top${cols.length ? ' has-row' : ''}">${rdoCard(top, {top: true})}</div>` : '')
    + (cols.length ? `<ul class="rdo-row">${cols.map(c => `<li>${c}</li>`).join('')}</ul>` : '')
    + (under ? `<ul class="rdo-tray"><li>${under}</li></ul>` : '');
}
function rdoDraw({refit = false}: {refit?: boolean} = {}): void {
  const cv = $('#rdo-cv'), pane = $('#rdo-pane');
  if (!cv || !pane || !RDO) return;
  cv.innerHTML = rdoChartHtml();
  // The roster has no leads: sheets need the viewer and the full groups before deciding who may move whom.
  if (!rdoReady()) cv.querySelectorAll('[data-rdo-ref]').forEach((card: HTMLButtonElement) => { card.disabled = true; });
  rdoFitHeight();
  if (RDO.zoom == null || (refit && !RDO.manual)) rdoFit();
  else rdoSize();
}
// On a phone the chart's frame runs down to the tab bar, so the page itself does not scroll.
function rdoFitHeight(): void {
  const pane = $('#rdo-pane');
  if (!pane) return;
  if (!matchMedia('(max-width:760px)').matches) { pane.style.height = ''; return; }
  const bar = $('#mobile-nav'), bottom = bar && bar.getClientRects().length ? bar.getBoundingClientRect().top : innerHeight;
  pane.style.height = Math.max(240, Math.floor(bottom - pane.getBoundingClientRect().top - 12)) + 'px';
}

// The chart is scaled with a transform, which leaves its layout box alone, so a sizer under it holds the scaled size for
// the pane to scroll: at least the pane's width (a narrower chart sits in its middle), else the chart plus RDO_PAD a side,
// so the first and last columns show whole when panned to either end.
const RDO_PAD = 12;
const rdoMotion = (): boolean => !matchMedia('(prefers-reduced-motion: reduce)').matches;
// The transform as painted now, mid-transition too, so a zoom that interrupts another starts from what is on screen.
function rdoNow(): RdoPose {
  const t = getComputedStyle($('#rdo-cv')).transform;
  if (!t || t === 'none') return {s: RDO?.zoom || 1, x: 0, y: 0};
  const m = new DOMMatrixReadOnly(t);
  return {s: m.a, x: m.e, y: m.f};
}
function rdoLayout(z: number): RdoPose {
  const cv = $('#rdo-cv'), sz = $('#rdo-sz'), pane = $('#rdo-pane');
  if (RDO) { RDO.cvW = cv.offsetWidth; RDO.cvH = cv.offsetHeight; }
  const w = cv.offsetWidth * z, h = cv.offsetHeight * z, width = Math.max(pane.clientWidth, Math.ceil(w + 2 * RDO_PAD));
  sz.style.width = width + 'px'; sz.style.height = Math.ceil(h) + 'px';
  return {x: (width - w) / 2, y: 0, s: z};
}
// Paint the chart at `at`. Animated, it first takes back the scroll the zoom just made (so nothing jumps), then eases to
// `at`; the point under the fingers or the pane's middle stays put the whole way, as both ends hold it there.
function rdoPaint(at: RdoPose, from?: RdoPose | null): void {
  const cv = $('#rdo-cv'), end = `translate(${at.x}px,${at.y}px) scale(${at.s})`;
  cv.style.transition = 'none';
  if (from && rdoMotion()) {
    cv.style.transform = `translate(${from.x}px,${from.y}px) scale(${from.s})`;
    void cv.offsetWidth;
    cv.style.transition = '';
  }
  cv.style.transform = end;
  const pct = $('#rdo-pct');
  if (pct) pct.textContent = Math.round(at.s * 100) + '%';
}
// The chart changed size (a fold, a redraw) or the pane did: lay it out again at the same zoom.
function rdoSize(): void {
  if (RDO?.zoom == null || !$('#rdo-cv')) return;
  rdoPaint(rdoLayout(RDO.zoom));
}
// The whole width when it fits with names still readable (12px or more), the owner then over the middle of its row; else
// readable and the owner in the middle of the frame, the rest a drag away.
function rdoFit({animate = false}: {animate?: boolean} = {}): void {
  const cv = $('#rdo-cv'), pane = $('#rdo-pane');
  if (!cv || !pane || !RDO) return;
  const name = parseFloat(getComputedStyle(cv.querySelector('.rdo-card b') || cv).fontSize) || 13;
  const fit = (pane.clientWidth - 2 * RDO_PAD) / Math.max(1, cv.offsetWidth);
  const readable = Math.ceil(12 / name * 1000) / 1000;
  RDO.manual = false;
  rdoZoom(fit >= readable ? Math.min(1, fit) : readable, {animate, centre: true});
}
function rdoZoom(z: number, {at, animate = false, centre = false}: {at?: {x: number; y: number}; animate?: boolean; centre?: boolean} = {}): void {
  const cv = $('#rdo-cv'), pane = $('#rdo-pane');
  if (!cv || !pane || !RDO) return;
  z = Math.max(0.35, Math.min(1.6, z));
  const now = RDO.zoom == null ? null : rdoNow(), left = pane.scrollLeft, top = pane.scrollTop;
  const mx = at ? at.x : pane.clientWidth / 2, my = at ? at.y : pane.clientHeight / 2;
  const owner = centre && cv.querySelector('.rdo-card.top');
  RDO.zoom = z;
  const lay = rdoLayout(z);
  if (centre) {
    // The owner's middle in the chart's own (unscaled) pixels.
    let x = cv.offsetWidth / 2;
    if (owner) { x = owner.offsetWidth / 2; for (let el = owner; el && el !== cv; el = el.offsetParent) x += el.offsetLeft; }
    pane.scrollLeft = lay.x + x * z - pane.clientWidth / 2;
    pane.scrollTop = 0;
  } else if (now) {
    // Keep the point under the fingers (or the pane's middle) where it was.
    pane.scrollLeft = lay.x + (left + mx - now.x) / now.s * z - mx;
    pane.scrollTop = lay.y + (top + my - now.y) / now.s * z - my;
  }
  rdoPaint(lay, animate && now ? {x: now.x + pane.scrollLeft - left, y: now.y + pane.scrollTop - top, s: now.s} : null);
}

// Fold or unfold a group where it stands: folding fades and lifts its teammates out, then the outline closes; unfolding
// opens the outline, then fades them in. The height changes at once; only opacity and transform move.
function rdoFold(id: string): void {
  if (!RDO) return;
  const folded = RDO.folded;
  folded.has(id) ? folded.delete(id) : folded.add(id);
  rdoFoldedSave();
  const fold = folded.has(id);
  const g = $(`#rdo-cv .rdo-g[data-rdo-group="${CSS.escape(id)}"]`), tab = g?.querySelector(':scope>.rdo-tab'), body = g?.querySelector(':scope>.rdo-gb');
  if (!g || !tab || !body) { rdoDraw(); return; }
  tab.setAttribute('aria-expanded', String(!fold));
  body.getAnimations().forEach((a: Animation) => a.cancel());
  const set = () => { g.classList.toggle('folded', folded.has(id)); rdoSize(); };
  if (!rdoMotion()) { set(); return; }
  const css = getComputedStyle(document.documentElement);
  const timing = {duration: parseFloat(css.getPropertyValue('--dur')) || 180, easing: css.getPropertyValue('--ease').trim() || 'ease-out'};
  const hidden = {opacity: 0, transform: 'translateY(-6px)'}, shown = {opacity: 1, transform: 'none'};
  if (fold) body.animate([shown, hidden], timing).onfinish = set;
  else { set(); body.animate([hidden, shown], timing); }
}

// ----------------------------------------------------------------- the page
async function pageRdOrg(): Promise<void> {
  const main = $('#main'), seq = ++RDO_SEQ;
  main.classList.add('strategy-layout', 'rd-org-layout');
  RDO ||= {groups: null, zoom: null, folded: rdoFoldedLoad()};
  RDO.zoom = null; RDO.manual = false;
  main.innerHTML = strategyHeadHtml('org') + `<div class="rdo-wrap">
      <div class="rdo-bar" role="toolbar" aria-label="Zoom"><button type="button" class="ghost" data-rdo-zoom="out" aria-label="Zoom out">−</button>
        <span class="rdo-pct" id="rdo-pct" aria-hidden="true">100%</span>
        <button type="button" class="ghost" data-rdo-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" class="ghost rdo-fit" data-rdo-zoom="fit" title="Fit the chart to the frame">Fit</button></div>
      <div class="rdo-pane" id="rdo-pane"><div class="rdo-sz" id="rdo-sz"><div class="rdo-cv rd-org" id="rdo-cv"></div></div></div></div>
      <div class="org-fac-host" id="org-fac-host"></div>`;
  rdoWire($('#rdo-pane'));
  window.removeEventListener('resize', rdoFitHeight); window.addEventListener('resize', rdoFitHeight);
  rdoDraw();
  // The locked Factories section below the chart (ui/app/org-factories.ts).
  void orgFactoriesMount($('#org-fac-host'));
  // Once the web font is in (or given up on), fit again: names in another face measure differently.
  document.fonts?.ready.then(() => { if (seq === RDO_SEQ && $('#rdo-cv') && !RDO?.manual) rdoFit(); });
  // The groups with their leads.
  const groups: OrgGroup[] | {groups?: OrgGroup[]} | null = await get<OrgGroup[] | {groups?: OrgGroup[]}>('/v2/groups').catch(() => null);
  if (seq !== RDO_SEQ || !$('#rdo-cv') || !RDO) return;
  RDO.groupsTried = true;
  if (groups) RDO.groups = Array.isArray(groups) ? groups : groups.groups || [];
  rdoDraw({refit: true});
}
// After a move: the roster (homes) and the groups (leads) again.
async function rdoReload(): Promise<void> {
  await refresh(true);
  const groups: OrgGroup[] | {groups?: OrgGroup[]} | null = await get<OrgGroup[] | {groups?: OrgGroup[]}>('/v2/groups', {fresh: true}).catch(() => null);
  if (groups && RDO) RDO.groups = Array.isArray(groups) ? groups : groups.groups || [];
  if ($('#rdo-cv')) rdoDraw();
}

function rdoWire(pane: HTMLElement): void {
  if (!RDO) return;
  const st: RdoState = RDO;
  // A new width, or the chart changing size on its own (a web font arriving), fits it again unless the person has zoomed
  // or panned since the last fit; a fold or a redraw has laid it out already. A frame later, so the change is not made
  // inside the observer's own pass.
  st.ro?.disconnect();
  let width = 0, queued = false;
  st.ro = new ResizeObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (!pane.isConnected) { st.ro?.disconnect(); return; }
      // The pane's own height follows the chart, so only its width counts, and its outer width at that: a scrollbar
      // coming or going is not a new frame, and refitting on it could see-saw. Anything else would cut a zoom's easing short.
      const cv = $('#rdo-cv'), w = pane.offsetWidth, wider = width && w !== width;
      const moved = st.cvW != null && cv.offsetWidth !== st.cvW, taller = st.cvH != null && cv.offsetHeight !== st.cvH;
      width = w;
      if ((wider || moved) && !st.manual) rdoFit();
      else if (wider || moved || taller) rdoSize();
    });
  });
  st.ro.observe(pane); st.ro.observe($('#rdo-cv'));
  const byHand = () => { st.manual = true; };
  // Pinch to zoom; one finger pans the pane natively (touch-action: pan-x pan-y).
  let pinch: {d: number; z: number} | null = null;
  const fingers = (t: TouchList): [Touch, Touch] | null => t[0] && t[1] ? [t[0], t[1]] : null;
  const mid = (f: [Touch, Touch], box: DOMRect) => ({x: (f[0].clientX + f[1].clientX) / 2 - box.left, y: (f[0].clientY + f[1].clientY) / 2 - box.top});
  const dist = (f: [Touch, Touch]) => Math.hypot(f[0].clientX - f[1].clientX, f[0].clientY - f[1].clientY);
  pane.addEventListener('touchstart', ev => { const f = fingers(ev.touches); if (ev.touches.length === 2 && f) pinch = {d: dist(f), z: st.zoom ?? 0}; }, {passive: true});
  pane.addEventListener('touchmove', ev => {
    const f = fingers(ev.touches);
    if (!pinch || ev.touches.length !== 2 || !f) return;
    ev.preventDefault();
    byHand();
    rdoZoom(pinch.z * dist(f) / pinch.d, {at: mid(f, pane.getBoundingClientRect())});
  }, {passive: false});
  pane.addEventListener('touchmove', byHand, {passive: true});
  pane.addEventListener('touchend', ev => { if (ev.touches.length < 2) pinch = null; });
  // A mouse drags the chart about (a finger pans natively); a drag is not a tap on the card it started on.
  dragPan(pane, {onDrag: byHand});
  // A trackpad's pinch arrives as a wheel with ctrlKey.
  pane.addEventListener('wheel', ev => {
    byHand();
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    const box = pane.getBoundingClientRect();
    rdoZoom((st.zoom ?? 0) * Math.exp(-ev.deltaY / 200), {at: {x: ev.clientX - box.left - pane.clientLeft, y: ev.clientY - box.top - pane.clientTop}});
  }, {passive: false});
}

document.addEventListener('click', ev => {
  const target = eventElement(ev);
  if (!target?.closest('.rd-org-layout') || !RDO) return;
  const fold = target.closest<HTMLElement>('[data-rdo-fold]');
  if (fold) { rdoFold(String(fold.dataset.rdoFold)); return; }
  const zoom = target.closest<HTMLElement>('[data-rdo-zoom]');
  if (zoom) {
    const how = zoom.dataset.rdoZoom;
    if (how === 'fit') rdoFit({animate: true});
    else { RDO.manual = true; rdoZoom((RDO.zoom ?? 0) * (how === 'in' ? 1.25 : 0.8), {animate: true}); }
    return;
  }
  const card = target.closest<HTMLElement>('[data-rdo-ref]');
  if (card) rdoSheet(String(card.dataset.rdoRef));
});

// Move to…: the groups `allowed` names (all of them when null) in chart order, nested ones indented; No group for those
// who may move anyone.
function rdoGroupOptions(current: string, allowed: string[] | null): string {
  const groups = S.orgGroups || [], out: [string, string][] = [];
  const walk = (parent: string, depth: number): void => groups.filter(g => (g.parent || '') === parent).sort((a, b) => (a.order || 0) - (b.order || 0)).forEach(g => {
    if (depth > 10 || out.some(([id]) => id === g.id)) return;
    out.push([g.id, ' '.repeat(depth * 2) + String(g.name)]); walk(g.id, depth + 1);
  });
  walk('', 0);
  for (const g of groups) if (!out.some(([id]) => id === g.id)) out.push([g.id, g.name ?? '']);
  const shown: [string, string][] = allowed ? out.filter(([id]) => allowed.includes(id)) : [['', 'No group'], ...out];
  return shown.map(([id, label]) => `<option value="${esc(id)}"${id === current ? ' selected' : ''}>${esc(label)}</option>`).join('');
}
function rdoSheet(ref: string): void {
  if (!rdoReady()) return;
  const name = rdoRefName(ref), from = rdoHome(ref), led = rdoLeadOf(ref);
  const targets = ref.startsWith('bot:') && rdoBuiltInBot(ref.slice(4)) ? [] : rdoMoveTargets(from);
  const may = !targets || targets.length > 0;
  const id = ref.slice(ref.indexOf(':') + 1), href = ref.startsWith('person:') ? `#/person/${encodeURIComponent(id)}` : `#/bot/${encodeURIComponent(id)}`;
  const body = `<div class="bd-fields rdo-sheet">
      <div class="bd-field"><span>Group</span><span data-rdo-home>${esc(rdoGroupName(from))}${led.length ? ` · lead of ${esc(led.map(rdoGroupName).join(', '))}` : ''}</span></div>
      ${may ? `<label class="bd-field"><span>Move to…</span><select name="to">${rdoGroupOptions(from, targets)}</select></label>` : ''}
      <a class="rdo-open" href="${href}">Open ${esc(name)}</a></div>`;
  const dlg = useDialog(name, body, async (form: HTMLFormElement) => {
    const to = form.to.value;
    if (to === from) throw new Error('Already in ' + rdoGroupName(to) + '.');
    await rdoMove(ref, from, to);
    toast(`${name} moved to ${rdoGroupName(to)}`);
  }, () => void rdoReload());
  dlg.classList.add('rd-sheet', 'rdo-dialog');
  const submit = dlg.querySelector<HTMLElement>('[type=submit]');
  if (may) { if (submit) submit.textContent = 'Move'; }
  else dlg.querySelector('.use-dialog-row')?.remove();
  const open = dlg.querySelector<HTMLAnchorElement>('.rdo-open');
  if (open) open.onclick = () => dlg.close();
}
// The move itself: into a group (it leaves the one it was in), or out of its group.
async function rdoMove(ref: string, from: string, to: string): Promise<void> {
  const kind = ref.startsWith('person:') ? 'people' : 'bots', id = ref.slice(ref.indexOf(':') + 1);
  if (to) await patch(`/v2/groups/${encodeURIComponent(to)}`, {add: {[kind]: [id]}});
  else await patch(`/v2/groups/${encodeURIComponent(from)}`, {remove: {[kind]: [id]}});
}
