/* ui/app/overview-graph.ts — Company › Graph (#/overview/graph): the team chart drawn top down, each teammate carrying
   its rules or its goals (?view=rules or ?view=goals, so a reload or a link keeps the choice). The tree is the sidebar's
   (orgTreeByParent, orgTreeWithHelpers, orgSorted): the same groups, report order and built-ins left off. Rules come
   from GET /api/v2/rules/by-teammate, goals from GET /api/v2/goals/tree; both are read with the viewer's rights, and
   nothing here adds a name or a rule they did not return. Tapping a teammate opens its panel: its rules, goals and
   reporting line, each link moving the selection, with Add rule (the Rules page's dialog) and a goal's sheet (the Goals
   page's) for whoever may use them. The chart pans and zooms in its own frame, on a phone too.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/overview-graph.js, which the page runs in this file's place. */
'use strict';

// A row of the sidebar's org tree (orgTreeWithHelpers): a person, a bot, a group or a seat (which the chart leaves off).
interface OgRow {
  kind: string; id: string; name?: string | undefined;
  person?: { id?: string | undefined; name?: string | undefined } | undefined; helpers?: boolean | undefined;
}
// A node of the chart: its key and what it stands for. The layout sets x, y, w and h (0 until then).
interface OgNode { key: string; kind: string; id: string; n: OgRow; parent: string; x: number; y: number; w: number; h: number; }
// A subtree's box: its size, and for a stacked column (stack > 0) its number of columns and row height.
interface OgBox { w: number; h: number; stack: number; rowH?: number | undefined; }
interface OgTree { nodes: Map<string, OgNode>; kids: Record<string, string[]>; edges: string[]; w: number; h: number; }
interface OgRule { id: string; text: string; kind: string; if_broken?: string | undefined; can_edit?: boolean | undefined; }
interface OgRuleHit { rule: OgRule; who: Set<string>; with: Set<string>; }
interface OgRuleIndex { byRule: Map<string, OgRuleHit>; byKey: Map<string, { who: string[]; with: string[] }>; }
// GET /api/v2/rules/by-teammate, as the chart keeps it; `index` is built from the answer on first use.
interface OgRules {
  rules: OgRule[];
  bots: Array<{ slug: string; who?: string[] | undefined; with?: string[] | undefined }>;
  people: Array<{ id: string; who?: string[] | undefined; with?: string[] | undefined }>;
  index?: OgRuleIndex | undefined;
}
interface OgKpi {
  name: string; unit?: string | undefined; status?: string | undefined; target?: number | null | undefined;
  target_label?: string | undefined; archived_at?: string | null | undefined;
  latest?: { value: number } | null | undefined; link?: { target?: number | null | undefined } | null | undefined;
}
// A goal as GET /api/v2/goals/tree answers it, with the fields the chart reads.
interface OgGoal {
  id: string; title: string; status: string; owner_id: string | null; parent_id?: string | null | undefined;
  rank?: number | undefined; kpis?: OgKpi[] | undefined;
}
interface OgPan { x: number; y: number; x0: number; y0: number; moved: boolean; id: number; type: string; }
interface OgPinch { d: number; s: number; mx: number; my: number; x0: number; y0: number; }
// The chart's state while the page is open.
interface OgState {
  view: 'rules' | 'goals'; abort: AbortController; tree: OgTree;
  rules: OgRules | 'missing' | 'error' | null; goals: OgGoal[] | 'error' | null;
  sel: string | null; rule: string | null; edit: Record<string, boolean>; tf: { x: number; y: number; s: number }; seq: number;
}

// Node size, gaps and the stacked-column spacing, in chart pixels.
const OG_W = 184, OG_H = 68, OG_GH = 30, OG_GX = 20, OG_GY = 40, OG_SY = 10, OG_PAD = 24;
const OG_ZMIN = 0.2, OG_ZMAX = 2;
// {view, abort, tree: {nodes, kids, w, h, edges}, rules: null|'missing'|'error'|{...}, goals: null|'error'|[...],
//  sel, rule, edit: {ruleId: bool}, tf: {x, y, s}, seq}
let OG: OgState | null = null;
// The open state, for the code that runs only while the page is open: pageOverviewGraph sets OG before any of it runs,
// and graphStop clears OG only after the listeners are gone. With OG null a read throws, as the untyped read did.
const ogNow = (): OgState => OG!;

const ogViewOf = (route: string | null | undefined): 'goals' | 'rules' =>
  new URLSearchParams(String(route || '').split('?')[1] || '').get('view') === 'goals' ? 'goals' : 'rules';
const ogPhone = (): boolean => matchMedia('(max-width:760px)').matches;
const ogMotion = (): boolean => !matchMedia('(prefers-reduced-motion: reduce)').matches;
const ogBotKey = (ref: string): string => 'b:' + (botByRef(ref)?.id || ref);
function ogActorKey(actor: string | null | undefined): string {
  const s = String(actor || '');
  return s.startsWith('bot:') ? ogBotKey(s.slice(4)) : s.startsWith('human:') ? 'p:' + s.slice(6) : '';
}
const ogKeyOf = (n: OgRow): string => (n.kind === 'person' ? 'p:' : n.kind === 'group' ? 'g:' : 'b:') + n.id;
const ogName = (n: OgRow): string => n.kind === 'group' ? n.name || n.id : n.kind === 'person' ? n.person?.name || n.id : botDisplayName(n.id);
const ogHref = (n: OgRow): string => n.kind === 'person' ? '#/person/' + encodeURIComponent(n.id) : n.kind === 'bot' ? '#/bot/' + encodeURIComponent(n.id) : TEAM_PAGE + encodeURIComponent(n.id);

// ----------------------------------------------------------------- the tree and its layout
function ogTree(): OgTree {
  // orgTreeWithHelpers (sidebar.js) has no JSDoc result type, so TypeScript reads it as {}. The cast gives its rows the shape below;
  // the JSDoc is left alone because strategy-budget.js reads the same rows untyped and its tsc baseline would grow.
  const raw = orgTreeWithHelpers(orgTreeByParent()) as Record<string, OgRow[]>, kids: Record<string, string[]> = {}, nodes = new Map<string, OgNode>();
  const keep = (n: OgRow): boolean => n.kind !== 'seat' && !(n.kind === 'group' && n.helpers);
  // As in the sidebar (renderTree): the top person heads the chart, and the groups, then the humans and bots, at the top
  // (theirs first, then the rest) hang under them. With no top person every top row is a root.
  const top: OgRow | null = orgTopPerson(raw), topKey = top ? 'p:' + top.id : '';
  const listOf = (key: string): OgRow[] => {
    if (top && key === '') return [top];
    const own: OgRow[] = orgSorted(key, (raw[key] || []).filter(keep));
    if (!top || key !== topKey) return own;
    const rest: OgRow[] = orgSorted('', (raw[''] || []).filter(n => n !== top && keep(n))), group = (n: OgRow): boolean => n.kind === 'group';
    return [...own.filter(group), ...rest.filter(group), ...own.filter(n => !group(n)), ...rest.filter(n => !group(n))];
  };
  // Walk from the top so a loop or a parent nobody reaches cannot draw a teammate twice.
  const walk = (key: string, depth: number): void => {
    const list = listOf(key).filter(n => !nodes.has(ogKeyOf(n)));
    const out: string[] = [];
    for (const n of list) {
      const k = ogKeyOf(n);
      if (nodes.has(k) || depth > 30) continue;
      nodes.set(k, {key: k, kind: n.kind, id: n.id, n, parent: key, x: 0, y: 0, w: 0, h: 0});
      walk(k, depth + 1);
      // A group with nobody in it is left off.
      if (n.kind === 'group' && !(kids[k] || []).length) { nodes.delete(k); continue; }
      out.push(k);
    }
    kids[key] = out;
  };
  walk('', 0);
  return ogLayout({nodes, kids});
}
const ogNodeH = (node: OgNode): number => node.kind === 'group' ? OG_GH : OG_H;
// Subtree boxes, then places. Leaves under one parent stack in a column (two past eight) instead of a long row, so a
// manager with many reports stays narrow; anything with reports of its own lays out in a row.
// Every key here is a node the walk added (so nodes.get(key)! holds), and box[key] is read only for a key measure() has
// already boxed: measure() boxes a key's children before the key, and place() runs after it, so each `box[...]!` holds.
function ogLayout(tree: { nodes: Map<string, OgNode>; kids: Record<string, string[]> }): OgTree {
  const {nodes, kids} = tree, box: Record<string, OgBox> = {};
  const leaf = (k: string): boolean => !(kids[k] || []).length;
  const measure = (k: string): OgBox => {
    const list = kids[k] || [], self = k ? ogNodeH(nodes.get(k)!) : 0, gap = k ? OG_GY : 0;   // `!`: k is a child walk() gave a node
    list.forEach(measure);
    if (!list.length) { const b: OgBox = {w: OG_W, h: self, stack: 0}; box[k] = b; return b; }
    if (list.length > 3 && list.every(leaf) && k) {
      const cols = list.length > 8 ? 2 : 1, rows = Math.ceil(list.length / cols);
      const h = list.reduce((m, c) => Math.max(m, ogNodeH(nodes.get(c)!)), 0);   // `!`: c is a child walk() gave a node
      const b: OgBox = {w: Math.max(OG_W, cols * OG_W + (cols - 1) * OG_GX + (cols === 1 ? 24 : 0)), h: self + gap + rows * h + (rows - 1) * OG_SY, stack: cols, rowH: h};
      box[k] = b; return b;
    }
    const w = list.reduce((t, c) => t + box[c]!.w, 0) + (list.length - 1) * OG_GX;   // `!`: measure() boxed every child above
    const b: OgBox = {w: Math.max(k ? OG_W : 0, w), h: self + gap + Math.max(...list.map(c => box[c]!.h)), stack: 0};   // `!`: measure() boxed every child above
    box[k] = b; return b;
  };
  measure('');
  const edges: string[] = [];
  const place = (k: string, x: number, y: number): void => {
    const b = box[k]!, list = kids[k] || [];   // `!`: measure("") boxed k before placing
    const node = k ? nodes.get(k) : null, self = node ? ogNodeH(node) : 0;
    if (node) Object.assign(node, {x: x + (b.w - OG_W) / 2, y, w: OG_W, h: self});
    const top = y + self + (node ? OG_GY : 0), cx = x + b.w / 2;
    if (b.stack) {
      const cols = b.stack, colW = cols * OG_W + (cols - 1) * OG_GX, left = x + (b.w - colW) / 2 + (cols === 1 ? 12 : 0);
      const spine = cols === 1 ? left - 12 : left + OG_W + OG_GX / 2;
      list.forEach((c, i) => {
        const col = cols === 1 ? 0 : i % cols, row = cols === 1 ? i : Math.floor(i / cols);
        // A stacked box always has its row height (rowH is set where stack is).
        const cy = top + row * (b.rowH! + OG_SY);
        place(c, left + col * (OG_W + OG_GX), cy);
        const cn = nodes.get(c)!, mid = cy + cn.h / 2, end = col === 0 && cols === 2 ? cn.x + OG_W : cn.x;   // `!`: a stacked leaf is a node walk() gave
        edges.push(`M${spine} ${mid}H${end}`);
      });
      // A stacked column has more than three leaves, so list[list.length - 1] exists.
      const last = nodes.get(list[list.length - 1]!)!, mid = last.y + last.h / 2;
      edges.push(`M${cx} ${y + self}V${y + self + OG_GY / 2}H${spine}V${mid}`);
      return;
    }
    let at = x + (b.w - (list.reduce((t, c) => t + box[c]!.w, 0) + (list.length - 1) * OG_GX)) / 2;   // `!`: measure() boxed every child above
    for (const c of list) {
      place(c, at, top);
      if (node) {
        const ccx = at + box[c]!.w / 2, midY = y + self + OG_GY / 2;   // `!`: measure() boxed every child above
        edges.push(`M${cx} ${y + self}V${midY}H${ccx}V${top}`);
      }
      at += box[c]!.w + OG_GX;   // `!`: measure() boxed every child above
    }
  };
  place('', 0, 0);
  return {...tree, edges, w: box['']!.w, h: box['']!.h};   // `!`: measure("") boxed the root
}

// ----------------------------------------------------------------- what each node carries
// Rule id -> {who: Set of node keys, with: Set}; node key -> {who: [ids], with: [ids]}.
function ogRuleIndex(): OgRuleIndex | null {
  const R = ogNow().rules;
  if (!R || typeof R !== 'object') return null;
  if (R.index) return R.index;
  const byRule = new Map<string, OgRuleHit>((R.rules || []).map((r): [string, OgRuleHit] => [r.id, {rule: r, who: new Set<string>(), with: new Set<string>()}]));
  const byKey = new Map<string, { who: string[]; with: string[] }>();
  const add = (key: string, row: { who?: string[] | undefined; with?: string[] | undefined }): void => {
    byKey.set(key, {who: row.who || [], with: row.with || []});
    for (const id of row.who || []) byRule.get(id)?.who.add(key);
    for (const id of row.with || []) byRule.get(id)?.with.add(key);
  };
  for (const b of R.bots || []) add(ogBotKey(b.slug), b);
  for (const p of R.people || []) add('p:' + p.id, p);
  const index: OgRuleIndex = {byRule, byKey};
  R.index = index;
  return index;
}
function ogRulesOf(key: string): { who: OgRule[]; with: OgRule[]; all: OgRule[] } {
  const ix = ogRuleIndex(), row = ix?.byKey.get(key);
  if (!ix || !row) return {who: [], with: [], all: []};
  const get = (id: string): OgRule | undefined => ix.byRule.get(id)?.rule;
  const present = (r: OgRule | undefined): r is OgRule => Boolean(r);
  const who = row.who.map(get).filter(present), withs = row.with.map(get).filter(present);
  return {who, with: withs, all: [...new Map<string, OgRule>([...who, ...withs].map((r): [string, OgRule] => [r.id, r])).values()]};
}
const ogGoalsAll = (): OgGoal[] => {
  const goals = ogNow().goals;
  return Array.isArray(goals) ? goals : [];
};
const ogGoalsOf = (key: string): OgGoal[] => ogGoalsAll().filter(g => goalLive(g) && ogActorKey(g.owner_id) === key).sort(goalRank);
const ogGoalStatus = (g: OgGoal): string => g.status === 'done' ? 'done' : ['green', 'yellow', 'red', 'none'].includes(g.status) ? g.status : 'gray';
const ogGoalWord = (g: OgGoal): string | undefined => {
  const labels: Record<string, string | undefined> = KPI_LABEL;
  return g.status === 'done' ? 'Done' : labels[ogGoalStatus(g)];
};

function ogMetaHtml(node: OgNode): string {
  if (node.kind === 'group') return '';
  if (ogNow().view === 'rules') {
    if (!ogNow().rules || typeof ogNow().rules !== 'object') return '';
    const {all} = ogRulesOf(node.key);
    const flag = all.some(r => r.if_broken === 'block') ? 'block' : all.some(r => r.if_broken === 'escalate') ? 'escalate' : '';
    return `<span class="og-count${all.length ? '' : ' none'}"><b class="tnum">${all.length}</b> ${all.length === 1 ? 'rule' : 'rules'}</span>`
      + (flag ? `<span class="og-flag ${flag}">${flag === 'block' ? 'Stop' : 'Ask first'}</span>` : '');
  }
  if (!Array.isArray(ogNow().goals)) return '';
  const goals = ogGoalsOf(node.key);
  if (!goals.length) return '<span class="og-count none">No goals</span>';
  return goals.slice(0, 2).map(g => `<span class="og-chip ${ogGoalStatus(g)}" title="${esc(ogGoalWord(g) + ': ' + g.title)}">${esc(g.title)}</span>`).join('')
    + (goals.length > 2 ? `<span class="og-more">+${goals.length - 2}</span>` : '');
}
function ogNodeLabel(node: OgNode): string {
  const name = ogName(node.n);
  if (node.kind === 'group') return name;
  if (ogNow().view === 'rules' && ogNow().rules && typeof ogNow().rules === 'object') { const n = ogRulesOf(node.key).all.length; return `${name}, ${n} ${n === 1 ? 'rule' : 'rules'}`; }
  if (ogNow().view === 'goals' && Array.isArray(ogNow().goals)) { const n = ogGoalsOf(node.key).length; return `${name}, ${n} ${n === 1 ? 'goal' : 'goals'}`; }
  return name;
}
function ogNodeHtml(node: OgNode): string {
  const style = `left:${node.x}px;top:${node.y}px;width:${node.w}px;height:${node.h}px`;
  if (node.kind === 'group') return `<a class="og-node og-group" href="${ogHref(node.n)}" data-og="${esc(node.key)}" style="${style}" title="${esc(ogName(node.n))}"><span class="og-name">${esc(ogName(node.n))}</span></a>`;
  const av = node.kind === 'person' ? personAvatar(node.n.person || {id: node.id, name: ogName(node.n)}, 20) : avatar(node.id, 20);
  return `<button type="button" class="og-node og-${node.kind}" data-og="${esc(node.key)}" style="${style}" aria-label="${esc(ogNodeLabel(node))}" title="${esc(ogName(node.n))}">
    <span class="og-top">${av}<span class="og-name">${esc(ogName(node.n))}</span></span><span class="og-meta">${ogMetaHtml(node)}</span></button>`;
}

// ----------------------------------------------------------------- drawing
function ogStatusHtml(): string {
  const og = ogNow();
  if (og.view === 'rules') {
    if (og.rules === 'missing') return '<p class="og-line muted">Rules need a newer Tico.</p>';
    if (og.rules === 'error') return '<p class="og-line err">Could not load rules.</p>';
    if (og.rules && !(og.rules.rules || []).length) return `<p class="og-line muted">No rules yet · <a href="${RULES_ROUTE}">Rules</a></p>`;
  } else {
    if (og.goals === 'error') return '<p class="og-line err">Could not load goals.</p>';
    if (Array.isArray(og.goals) && !og.goals.some(goalLive)) return `<p class="og-line muted">No goals yet · <a href="${goalsBase()}">Goals</a></p>`;
  }
  return '';
}
function ogDraw(): void {
  const cv = document.querySelector<HTMLElement>('#og-cv'); if (!cv || !OG) return;
  const t = OG.tree;
  cv.style.width = t.w + 'px'; cv.style.height = t.h + 'px';
  cv.innerHTML = `<svg class="og-svg" width="${t.w}" height="${t.h}" viewBox="0 0 ${t.w} ${t.h}" aria-hidden="true">
      <defs><marker id="og-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L8 4L0 8z"/></marker></defs>
      <path class="og-edges" d="${t.edges.join('')}"/><g class="og-links"></g></svg>
    ${[...t.nodes.values()].map(ogNodeHtml).join('')}`;
  const line = document.querySelector<HTMLElement>('#og-status'); if (line) { line.innerHTML = ogStatusHtml(); line.hidden = !line.innerHTML; }
  const empty = !t.nodes.size;
  // pageOverviewGraph writes #og-empty into the page before the first draw.
  document.querySelector<HTMLElement>('#og-empty')!.hidden = !empty;
  ogMarks();
}
// Endpoints on the two cards' edges, so a link shows between them rather than under them.
function ogLinkPath(a: OgNode, b: OgNode): {d: string} {
  const ax = a.x + a.w / 2, bx = b.x + b.w / 2;
  if (b.y + b.h < a.y) return {d: `M${ax} ${a.y}C${ax} ${a.y - 40} ${bx} ${b.y + b.h + 40} ${bx} ${b.y + b.h}`};
  if (b.y > a.y + a.h) return {d: `M${ax} ${a.y + a.h}C${ax} ${a.y + a.h + 40} ${bx} ${b.y - 40} ${bx} ${b.y}`};
  const right = bx > ax, x1 = right ? a.x + a.w : a.x, x2 = right ? b.x : b.x + b.w, my = Math.min(a.y, b.y) - 24;
  return {d: `M${x1} ${a.y + a.h / 2}C${x1} ${my} ${x2} ${my} ${x2} ${b.y + b.h / 2}`};
}
// Goal links where a goal supports another owner's; the selected rule's reach and its Who-With links; the selection.
function ogGoalLinks(): Array<[string, string]> {
  const goals = ogGoalsAll(), byId = new Map<string, OgGoal>(goals.map((g): [string, OgGoal] => [g.id, g])), seen = new Set<string>(), out: Array<[string, string]> = [];
  for (const g of goals) {
    const p = goalLive(g) && g.parent_id ? byId.get(g.parent_id) : null;
    if (!p) continue;
    const a = ogActorKey(g.owner_id), b = ogActorKey(p.owner_id);
    if (!a || !b || a === b || !ogNow().tree.nodes.has(a) || !ogNow().tree.nodes.has(b) || seen.has(a + '>' + b)) continue;
    seen.add(a + '>' + b); out.push([a, b]);
  }
  return out;
}
function ogMarks(): void {
  const cv = document.querySelector<HTMLElement>('#og-cv'); if (!cv || !OG) return;
  // ogDraw writes the .og-links group into the chart just before ogMarks runs.
  const nodes = OG.tree.nodes, links = cv.querySelector<SVGGElement>('.og-links')!;
  let lines: Array<{ a: string; b: string; cls: string }> = [], who = new Set<string>(), withs = new Set<string>();
  if (OG.view === 'rules' && OG.rule) {
    const hit = ogRuleIndex()?.byRule.get(OG.rule);
    if (hit) {
      who = hit.who; withs = hit.with;
      if (hit.rule.kind === 'internal') for (const a of who) for (const b of withs)
        if (a !== b && nodes.has(a) && nodes.has(b) && lines.length < 400) lines.push({a, b, cls: 'og-rl'});
    }
  }
  if (OG.view === 'goals') lines = ogGoalLinks().map(([a, b]) => ({a, b, cls: 'og-gl' + (ogNow().sel && (a === ogNow().sel || b === ogNow().sel) ? ' on' : ogNow().sel ? ' dim' : '')}));
  // a and b are each checked with nodes.has() where their line is pushed above, so the nodes exist.
  links.innerHTML = lines.map(({a, b, cls}) => `<path class="${cls}" data-a="${esc(a)}" data-b="${esc(b)}" d="${ogLinkPath(nodes.get(a)!, nodes.get(b)!).d}"${cls.startsWith('og-gl') ? ' marker-end="url(#og-arrow)"' : ''}/>`).join('');
  const reach = who.size || withs.size;
  for (const el of cv.querySelectorAll<HTMLElement>('[data-og]')) {
    const k = el.dataset.og ?? '';
    el.classList.toggle('sel', k === OG.sel);
    el.classList.toggle('og-who', who.has(k));
    el.classList.toggle('og-with', withs.has(k));
    el.classList.toggle('dim', !!reach && !who.has(k) && !withs.has(k) && k !== OG.sel);
  }
}

// ----------------------------------------------------------------- pan and zoom
function ogApply(animate = false): void {
  const cv = document.querySelector<HTMLElement>('#og-cv'); if (!cv) return;
  cv.classList.toggle('og-anim', animate && ogMotion());
  const {x, y, s} = ogNow().tf;
  cv.style.transform = `translate(${x}px,${y}px) scale(${s})`;
  const pct = document.querySelector<HTMLElement>('#og-pct'); if (pct) pct.textContent = Math.round(s * 100) + '%';
}
function ogZoomAt(factor: number, px: number, py: number, animate = false): void {
  const t = ogNow().tf, s = Math.max(OG_ZMIN, Math.min(OG_ZMAX, t.s * factor));
  t.x = px - (px - t.x) * s / t.s; t.y = py - (py - t.y) * s / t.s; t.s = s;
  ogApply(animate);
}
const ogFrame = (): HTMLElement | null => document.querySelector<HTMLElement>('#og-frame');
function ogZoomMid(factor: number): void { const f = ogFrame(); if (f) ogZoomAt(factor, f.clientWidth / 2, f.clientHeight / 2, true); }
function ogFit(animate = false): void {
  const f = ogFrame(); if (!f || !OG) return;
  const {w, h} = OG.tree, W = f.clientWidth, H = f.clientHeight;
  if (!w || !h || !W || !H) return;
  const s = Math.max(OG_ZMIN, Math.min(1, (W - 2 * OG_PAD) / w, (H - 2 * OG_PAD) / h));
  OG.tf = {s, x: (W - w * s) / 2, y: h * s + 2 * OG_PAD <= H ? (H - h * s) / 2 : OG_PAD};
  ogApply(animate);
}
// The part of the frame the panel leaves uncovered: the left of it on a desktop, the top on a phone (the sheet).
function ogVisible(f: HTMLElement): {l: number; t: number; r: number; b: number} {
  const panel = document.querySelector<HTMLElement>('#og-panel'), box = {l: 0, t: 0, r: f.clientWidth, b: f.clientHeight};
  if (panel && !panel.hidden) {
    const fr = f.getBoundingClientRect(), pr = panel.getBoundingClientRect();
    if (ogPhone()) box.b = Math.max(80, Math.min(box.b, pr.top - fr.top));
    else box.r = Math.max(120, Math.min(box.r, pr.left - fr.left));
  }
  return box;
}
function ogShow(key: string, animate = true): void {
  const node = OG?.tree.nodes.get(key), f = ogFrame();
  if (!OG || !node || !f) return;
  const {x, y, s} = OG.tf, v = ogVisible(f), m = 16;
  const l = node.x * s + x, t = node.y * s + y, r = l + node.w * s, b = t + node.h * s;
  if (l >= v.l + m && r <= v.r - m && t >= v.t + m && b <= v.b - m) return;
  OG.tf.x += (v.l + v.r) / 2 - (l + r) / 2;
  OG.tf.y += (v.t + v.b) / 2 - (t + b) / 2;
  ogApply(animate);
}
function ogWire(frame: HTMLElement, signal: AbortSignal): void {
  const pts = new Map<number, {x: number; y: number}>();
  let pan: OgPan | null = null, pinch: OgPinch | null = null, lastTap: {x: number; y: number; at: number} | null = null;
  const local = (ev: MouseEvent): {x: number; y: number} => { const r = frame.getBoundingClientRect(); return {x: ev.clientX - r.left, y: ev.clientY - r.top}; };
  // The two pointers of a pinch. Called only while two or more are down, so both entries exist.
  const pair = (): [{x: number; y: number}, {x: number; y: number}] => { const v = [...pts.values()]; return [v[0]!, v[1]!]; };
  frame.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    if (eventElement(ev)?.closest('.og-tools')) return;
    pts.set(ev.pointerId, local(ev));
    if (pts.size === 1) pan = {...local(ev), x0: ogNow().tf.x, y0: ogNow().tf.y, moved: false, id: ev.pointerId, type: ev.pointerType};
    if (pts.size === 2) {
      const [a, b] = pair();
      pinch = {d: Math.hypot(a.x - b.x, a.y - b.y) || 1, s: ogNow().tf.s, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, x0: ogNow().tf.x, y0: ogNow().tf.y};
      if (pan) pan.moved = true;
    }
  }, {signal});
  frame.addEventListener('pointermove', ev => {
    if (!pts.has(ev.pointerId)) return;
    const p = local(ev); pts.set(ev.pointerId, p);
    if (pinch && pts.size >= 2) {
      const [a, b] = pair(), d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const s = Math.max(OG_ZMIN, Math.min(OG_ZMAX, pinch.s * d / pinch.d)), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      ogNow().tf = {s, x: mx - (pinch.mx - pinch.x0) * s / pinch.s, y: my - (pinch.my - pinch.y0) * s / pinch.s};
      ogApply();
      return;
    }
    if (!pan || pan.id !== ev.pointerId) return;
    const dx = p.x - pan.x, dy = p.y - pan.y;
    if (!pan.moved && Math.hypot(dx, dy) < 5) return;
    if (!pan.moved) { pan.moved = true; frame.setPointerCapture?.(ev.pointerId); frame.classList.add('dragging'); }
    ogNow().tf.x = pan.x0 + dx; ogNow().tf.y = pan.y0 + dy;
    ogApply();
  }, {signal});
  const up = (ev: PointerEvent): void => {
    if (!pts.has(ev.pointerId)) return;
    pts.delete(ev.pointerId);
    if (pts.size < 2) pinch = null;
    if (pan && pan.id === ev.pointerId) {
      if (pan.moved) { frame.dataset.dragged = '1'; setTimeout(() => { delete frame.dataset.dragged; }, 0); }
      else if (ev.type === 'pointerup' && pan.type !== 'mouse') {
        // A double tap zooms in where it lands (a mouse has dblclick).
        const p = local(ev), now = Date.now();
        if (lastTap && now - lastTap.at < 320 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 30) { ogZoomAt(1.6, p.x, p.y, true); lastTap = null; }
        else lastTap = {...p, at: now};
      }
      frame.classList.remove('dragging');
      pan = null;
    }
  };
  frame.addEventListener('pointerup', up, {signal});
  frame.addEventListener('pointercancel', up, {signal});
  frame.addEventListener('click', ev => { if (frame.dataset.dragged) { ev.stopPropagation(); ev.preventDefault(); } }, {capture: true, signal});
  frame.addEventListener('dblclick', ev => {
    if (eventElement(ev)?.closest('.og-tools')) return;
    const p = local(ev); ogZoomAt(1.6, p.x, p.y, true);
  }, {signal});
  frame.addEventListener('wheel', ev => {
    ev.preventDefault();
    const p = local(ev);
    // A sideways swipe pans; a wheel, or a trackpad's pinch (ctrlKey), zooms around the pointer.
    if (!ev.ctrlKey && Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) { ogNow().tf.x -= ev.deltaX; ogApply(); return; }
    const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1;
    ogZoomAt(Math.exp(-ev.deltaY * unit / (ev.ctrlKey ? 100 : 400)), p.x, p.y);
  }, {passive: false, signal});
  frame.addEventListener('keydown', ev => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === '+' || ev.key === '=') { ev.preventDefault(); ogZoomMid(1.25); }
    else if (ev.key === '-' || ev.key === '_') { ev.preventDefault(); ogZoomMid(0.8); }
    else if (ev.key === '0') { ev.preventDefault(); ogFit(true); }
  }, {signal});
  // Tabbing to a teammate out of view brings it into view.
  frame.addEventListener('focusin', ev => { const k = eventElement(ev)?.closest<HTMLElement>('[data-og]')?.dataset.og; if (k) ogShow(k, false); }, {signal});
}

// ----------------------------------------------------------------- the panel
const ogGoBtn = (key: string): string => {
  const node = ogNow().tree.nodes.get(key);
  return node ? `<button type="button" class="og-go" data-og-go="${esc(key)}">${esc(ogName(node.n))}</button>` : '';
};
const ogGoList = (keys: Iterable<string>): string => { const shown = [...keys].filter(k => ogNow().tree.nodes.has(k)); return shown.slice(0, 8).map(ogGoBtn).join('') + (shown.length > 8 ? `<span class="muted">+${shown.length - 8}</span>` : ''); };
function ogRuleHtml(r: OgRule, key: string, side: 'who' | 'with'): string {
  const hit = ogRuleIndex()?.byRule.get(r.id), other = side === 'who' ? hit?.with : hit?.who;
  const others = [...(other || [])].filter(k => k !== key);
  const edit = r.can_edit ?? ogNow().edit[r.id];
  const on = ogNow().rule === r.id;
  return `<li class="og-rule${on ? ' on' : ''}">
      <button type="button" class="og-rule-t" data-og-rule="${esc(r.id)}" aria-pressed="${on}">${ruTextHTML(r.text, 'plain')}</button>
      <div class="og-rule-m">${r.if_broken === 'block' || r.if_broken === 'escalate' ? `<span class="og-flag ${r.if_broken}">${r.if_broken === 'block' ? 'Stop' : 'Ask first'}</span>` : ''}
        ${others.length && ogGoList(others) ? `<span class="og-with-l">${side === 'who' ? 'With' : 'Who'}</span>${ogGoList(others)}` : ''}
        <span class="spacer"></span><a class="og-rule-a" href="${ruHref(r.kind, {rule: r.id})}">${edit ? 'Edit' : 'Open'}</a></div></li>`;
}
function ogRulesSection(node: OgNode): string {
  const may = S.me?.role !== 'bot' && ogNow().rules && typeof ogNow().rules === 'object';
  const head = `<div class="og-sh"><h3>Rules</h3><span class="spacer"></span>${may ? '<button type="button" class="ghost small" data-og-add-rule>Add rule</button>' : ''}</div>`;
  if (ogNow().rules === 'missing') return head + '<p class="muted og-pl">Rules need a newer Tico.</p>';
  if (ogNow().rules === 'error') return head + '<p class="err og-pl">Could not load rules.</p>';
  if (!ogNow().rules) return head + skelRows(2);
  const {who, with: withs} = ogRulesOf(node.key);
  if (!who.length && !withs.length) return head + '<p class="muted og-pl">No rules</p>';
  const list = (label: string, rules: OgRule[], side: 'who' | 'with'): string => rules.length ? `<h4>${label}</h4><ul class="og-rules">${rules.map(r => ogRuleHtml(r, node.key, side)).join('')}</ul>` : '';
  return head + list('Follows', who, 'who') + list('With', withs, 'with');
}
function ogKpiLine(g: OgGoal): string {
  const k = (g.kpis || []).find(x => !x.archived_at);
  if (!k) return '';
  // kpiFresh is false without a latest reading, so the reading is read only where there is one.
  const value = k.latest && kpiFresh(k) ? kpiNum(k.latest.value, k.unit) : '–';
  const target = k.target_label || (k.link?.target != null ? kpiNum(k.link.target, k.unit) : k.target != null ? kpiNum(k.target, k.unit) : '');
  return `<div class="og-kpi">${gdot(k.status)}<span>${esc(k.name)}</span><span class="spacer"></span><span class="tnum">${esc(value)}${target ? `<span class="muted"> / ${esc(target)}</span>` : ''}</span></div>`;
}
function ogGoalsSection(node: OgNode): string {
  const actor = node.kind === 'bot' ? 'bot:' + node.id : 'human:' + node.id, goals = ogGoalsAll();
  const mayAdd = Array.isArray(ogNow().goals) && (mePerson() || S.me?.role === 'owner') && goalMayEdit({owner_id: actor}, goals);
  const head = `<div class="og-sh"><h3>Goals</h3><span class="spacer"></span>${mayAdd ? '<button type="button" class="ghost small" data-og-add-goal>Add goal</button>' : ''}</div>`;
  if (ogNow().goals === 'error') return head + '<p class="err og-pl">Could not load goals.</p>';
  if (!ogNow().goals) return head + skelRows(2);
  const mine = ogGoalsOf(node.key);
  if (!mine.length) return head + '<p class="muted og-pl">No goals</p>';
  const byId = new Map<string, OgGoal>(goals.map((g): [string, OgGoal] => [g.id, g]));
  return head + `<ul class="og-goals">${mine.map(g => {
    const parent = g.parent_id ? byId.get(g.parent_id) : null, pk = parent ? ogActorKey(parent.owner_id) : '';
    const kids = [...new Set(goals.filter(x => goalLive(x) && x.parent_id === g.id).map(x => ogActorKey(x.owner_id)).filter(k => k && k !== node.key))];
    return `<li class="og-goal">
      <div class="og-gl-h">${gdot(g.status, ogGoalWord(g))}<a href="${goalsBase()}/${encodeURIComponent(g.id)}">${esc(g.title)}</a><span class="spacer"></span><span class="og-gw ${ogGoalStatus(g)}">${esc(ogGoalWord(g))}</span></div>
      ${ogKpiLine(g)}
      ${parent ? `<div class="og-gl-r"><span class="og-with-l">Supports</span><span class="og-ptitle">${esc(parent.title)}</span>${pk && pk !== node.key ? ogGoBtn(pk) : ''}</div>` : ''}
      ${kids.length && ogGoList(kids) ? `<div class="og-gl-r"><span class="og-with-l">Supported by</span>${ogGoList(kids)}</div>` : ''}
      ${goalMayEdit(g, goals) ? `<div class="og-gl-a"><button type="button" class="linkish" data-og-goal-edit="${esc(g.id)}">Update</button></div>` : ''}</li>`;
  }).join('')}</ul>`;
}
function ogTeamSection(node: OgNode): string {
  const boss = orgReportsTo(node.n), reports = [...ogNow().tree.nodes.values()].filter(x => x.kind !== 'group' && orgReportsTo(x.n) === node.key).map(x => x.key);
  const bossBtn = boss && ogNow().tree.nodes.has(boss) ? ogGoBtn(boss) : '';
  if (!bossBtn && !reports.length) return '';
  return `<div class="og-sh"><h3>Team</h3></div>
    ${bossBtn ? `<div class="og-gl-r"><span class="og-with-l">Reports to</span>${bossBtn}</div>` : ''}
    ${reports.length ? `<div class="og-gl-r"><span class="og-with-l">Reports</span>${ogGoList(reports)}</div>` : ''}`;
}
function ogPanelDraw(): void {
  const panel = document.querySelector<HTMLElement>('#og-panel'); if (!panel || !OG) return;
  const node = OG.sel && OG.tree.nodes.get(OG.sel);
  if (!node) { panel.hidden = true; panel.innerHTML = ''; document.querySelector<HTMLElement>('#og-wrap')?.classList.remove('og-open'); return; }
  const keepScroll = panel.dataset.key === node.key ? panel.querySelector<HTMLElement>('.og-pb')?.scrollTop || 0 : 0;
  const av = node.kind === 'person' ? personAvatar(node.n.person || {id: node.id, name: ogName(node.n)}, 24) : avatar(node.id, 24);
  const sections = OG.view === 'rules' ? [ogRulesSection, ogGoalsSection, ogTeamSection] : [ogGoalsSection, ogRulesSection, ogTeamSection];
  panel.dataset.key = node.key;
  panel.setAttribute('aria-label', ogName(node.n));
  panel.innerHTML = `<div class="og-ph">${av}<a class="og-pn" href="${ogHref(node.n)}">${esc(ogName(node.n))}</a><span class="spacer"></span>
      <button type="button" class="ghost tmodal-x" data-og-close aria-label="Close">✕</button></div>
    <div class="og-pb">${sections.map(f => { const h = f(node); return h ? `<section class="og-sec">${h}</section>` : ''; }).join('')}</div>`;
  panel.hidden = false;
  document.querySelector<HTMLElement>('#og-wrap')?.classList.add('og-open');
  // The body was drawn just above.
  panel.querySelector<HTMLElement>('.og-pb')!.scrollTop = keepScroll;
}
function ogSelect(key: string, {focus = false}: {focus?: boolean} = {}): void {
  // has(key) is checked first in the same condition, so the node exists for get(key).
  if (!OG?.tree.nodes.has(key) || OG.tree.nodes.get(key)!.kind === 'group') return;
  if (OG.sel !== key) OG.rule = null;
  OG.sel = key;
  ogPanelDraw(); ogMarks();
  requestAnimationFrame(() => ogShow(key));
  if (focus) document.querySelector<HTMLElement>('#og-panel [data-og-close]')?.focus({preventScroll: true});
}
function ogClose(): void {
  if (!OG?.sel) return;
  const was = OG.sel;
  OG.sel = null; OG.rule = null;
  ogPanelDraw(); ogMarks();
  document.querySelector<HTMLElement>(`#og-cv [data-og="${CSS.escape(was)}"]`)?.focus({preventScroll: true});
}
async function ogRulePick(id: string): Promise<void> {
  ogNow().rule = ogNow().rule === id ? null : id;
  ogPanelDraw(); ogMarks();
  // Edit shows only where the rule says the viewer may change it: read once when it is opened.
  const r = ogRuleIndex()?.byRule.get(id)?.rule;
  if (!ogNow().rule || !r || r.can_edit !== undefined || id in ogNow().edit) return;
  const state = ogNow();
  try {
    const full = await get<{can_edit?: boolean}>('/v2/rules/' + encodeURIComponent(id), {fresh: true});
    if (OG !== state) return;
    OG.edit[id] = !!full.can_edit;
  } catch { if (OG === state) OG.edit[id] = false; }
  if (OG === state && OG.sel) ogPanelDraw();
}

// ----------------------------------------------------------------- data
async function ogLoad(): Promise<void> {
  const state = ogNow(), seq = ++state.seq;
  const [rules, goals] = await Promise.all([
    get<{rules?: OgRule[]; bots?: OgRules['bots']; people?: OgRules['people']}>('/v2/rules/by-teammate', {fresh: true}).then(
      (r): OgRules => ({rules: r.rules || [], bots: r.bots || [], people: r.people || []}),
      (e: unknown): 'missing' | 'error' => errorStatus(e) === 404 ? 'missing' : 'error'),
    get<{goals?: OgGoal[]}>('/v2/goals/tree', {fresh: true}).then(
      r => (r.goals || []).filter(g => !goalBuiltInOwner(g.owner_id)),
      (): 'error' => 'error'),
  ]);
  if (OG !== state || seq !== state.seq) return;
  state.rules = rules; state.goals = goals;
  // Edit marks read from an earlier answer may be stale after a save.
  state.edit = {};
  ogDraw(); ogPanelDraw();
}
function ogSetView(view: 'rules' | 'goals'): void {
  if (!OG || OG.view === view) return;
  OG.view = view;
  const hash = OVERVIEW_GRAPH + (view === 'goals' ? '?view=goals' : '');
  // The page stays as it is (pan, zoom, selection): only the address changes, so no redraw from the router.
  history.replaceState(history.state, '', hash);
  S.route = LAST_ROUTE = hash;
  for (const b of document.querySelectorAll<HTMLElement>('[data-og-view]')) b.setAttribute('aria-pressed', String(b.dataset.ogView === view));
  ogDraw(); ogPanelDraw();
}

// ----------------------------------------------------------------- the page
function pageOverviewGraph(): void {
  // The app's shell always has #main.
  const main = document.querySelector<HTMLElement>('#main')!; main.classList.add('overview-layout');
  graphStop();
  const view = ogViewOf(S.route);
  main.innerHTML = `<section class="overview-page og-page" aria-label="Team graph">
    <div class="overview-tabs"><h1>Company</h1>${dashTabsHTML('graph')}</div>
    <div class="og-bar"><div class="og-seg" role="group" aria-label="Show">${[['rules', 'Rules'], ['goals', 'Goals']].map(([v, l]) =>
      `<button type="button" data-og-view="${v}" aria-pressed="${v === view}">${l}</button>`).join('')}</div>
      <div class="og-status" id="og-status" hidden></div></div>
    <div class="og-wrap" id="og-wrap">
      <div class="og-frame" id="og-frame" tabindex="0" role="group" aria-label="Team chart">
        <div class="og-cv" id="og-cv"></div>
        <p class="og-empty muted" id="og-empty" hidden>No one on the chart yet.</p>
      </div>
      <div class="og-tools" role="toolbar" aria-label="Zoom">
        <button type="button" class="ghost" data-og-zoom="out" aria-label="Zoom out">−</button>
        <span class="og-pct tnum" id="og-pct" aria-hidden="true">100%</span>
        <button type="button" class="ghost" data-og-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" class="ghost" data-og-zoom="fit">Fit</button></div>
      <aside class="og-panel" id="og-panel" role="dialog" aria-modal="false" hidden></aside>
    </div></section>`;
  const abort = new AbortController();
  OG = {view, abort, tree: ogTree(), rules: null, goals: null, sel: null, rule: null, edit: {}, tf: {x: 0, y: 0, s: 1}, seq: 0};
  ogDraw();
  ogFit();
  // pageOverviewGraph has just written #og-frame into the page.
  const frame = document.querySelector<HTMLElement>('#og-frame')!, signal = abort.signal;
  ogWire(frame, signal);
  // A new frame size (a window resize, a phone turned) fits again only until the person has moved the chart.
  let size = [frame.clientWidth, frame.clientHeight];
  const ro = new ResizeObserver(() => {
    const now = [frame.clientWidth, frame.clientHeight];
    if (now[0] !== size[0] || now[1] !== size[1]) { size = now; if (OG?.sel) ogShow(OG.sel, false); else ogFit(); }
  });
  ro.observe(frame); signal.addEventListener('abort', () => ro.disconnect());
  // The page's markup was written just above.
  const page = main.querySelector<HTMLElement>('.og-page')!;
  page.addEventListener('click', ev => {
    const t = eventElement(ev); if (!t) return;
    const v = t.closest<HTMLElement>('[data-og-view]'); if (v) { ogSetView(v.dataset.ogView === 'goals' ? 'goals' : 'rules'); return; }
    const z = t.closest<HTMLElement>('[data-og-zoom]');
    if (z) { const how = z.dataset.ogZoom; how === 'fit' ? ogFit(true) : ogZoomMid(how === 'in' ? 1.25 : 0.8); return; }
    if (t.closest('[data-og-close]')) { ogClose(); return; }
    const go = t.closest<HTMLElement>('[data-og-go]'); if (go) { ogSelect(go.dataset.ogGo ?? ''); return; }
    const rule = t.closest<HTMLElement>('[data-og-rule]'); if (rule) { void ogRulePick(rule.dataset.ogRule ?? ''); return; }
    if (t.closest('[data-og-add-rule]')) { ogAddRule(); return; }
    if (t.closest('[data-og-add-goal]')) { ogGoalSheet({owner: ogNow().sel}); return; }
    const ge = t.closest<HTMLElement>('[data-og-goal-edit]'); if (ge) { ogGoalSheet({goal: ge.dataset.ogGoalEdit}); return; }
    const node = t.closest<HTMLElement>('#og-cv [data-og]');
    if (node && node.tagName === 'BUTTON') { ogSelect(node.dataset.og ?? ''); return; }
    // A tap on the empty chart closes the panel.
    if (!node && t.closest('#og-frame') && ogNow().sel) ogClose();
  }, {signal});
  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || !OG?.sel || ev.defaultPrevented || document.querySelector('dialog[open]')) return;
    ev.preventDefault(); ogClose();
  }, {signal});
  document.fonts?.ready.then(() => { if (OG?.abort === abort && !OG.sel) ogFit(); });
  void ogLoad();
}
function graphStop(): void { OG?.abort.abort(); OG = null; }

// Add rule: the Rules page's dialog with Who set to this teammate; the graph reads its rules again after.
function ogAddRule(): void {
  const node = OG?.sel && OG.tree.nodes.get(OG.sel); if (!node) return;
  const who = node.kind === 'bot' ? {bots: [botSlug(node.id)]} : {people: [node.id]};
  const state = OG;
  void ruAddOpen({kind: 'internal', who, after: () => { if (OG === state) void ogLoad(); }});
}
// A goal's sheet from the Goals page (edit, colour, measure, remove) or a new goal for this teammate. Whatever it saved,
// the graph reads its goals again when it closes; Details opens the goal on the Goals page.
function ogGoalSheet({goal = '', owner = ''}: {goal?: string | undefined; owner?: string | null | undefined} = {}): void {
  if (!OG || !Array.isArray(OG.goals)) return;
  const node = owner && OG.tree.nodes.get(owner);
  const actor = node ? (node.kind === 'bot' ? 'bot:' + node.id : 'human:' + node.id) : '';
  const state = OG;
  goalSheetOpen({goals: OG.goals}, goal ? {goal} : {owner: actor});
  const dlg = document.querySelector<HTMLDialogElement>('#goal-sheet');
  if (!dlg?.open) return;
  const details = dlg.querySelector<HTMLElement>('[data-gs-details]');
  if (details) details.onclick = () => { dlg.close(); location.hash = goalsBase() + '/' + encodeURIComponent(goal); };
  dlg.addEventListener('close', () => { if (OG === state) void ogLoad(); }, {once: true});
}
