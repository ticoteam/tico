/* The Market page's charts (ui/app/redesign-market.ts): "Where is the money going?", one treemap of what the market
   spends (by offering or by buyer) beside one dot per segment for size against growth, each box and dot opening a panel
   with the arithmetic behind it; and "How does it reach buyers?", the value chain of who supplies, sells and buys
   around the team. Drawn from GET /v2/market/spend and /v2/market/value-chain. The layout and the charts are
   hand-written, with no library. */
'use strict';
// What the charts read from GET /v2/market/spend and /v2/market/value-chain (backend/market_spend.py).
interface MktFigure {
  metric: string;
  value: number | null;
  text?: string | null;
  kind?: string | null;
  as_of?: string | null;
  source_url?: string | null;
}
interface MktPlayer { entity_id?: string; name: string; figures?: MktFigure[] | null; }
interface MktEvidence { source_url?: string | null; source_kind?: string | null; quote?: string | null; }
interface MktSize {
  id?: string;
  spend_usd: number | null;
  low_usd: number | null;
  high_usd: number | null;
  growth_pct?: number | null;
  confidence?: 'high' | 'medium' | 'low' | null;
  accepted?: boolean;
  error?: string | null;
  overlaps?: string | null;
  spread?: string | null;
}
interface MktEstimate {
  id: string;
  method: string;
  status?: string | null;
  spend_usd: number | null;
  low_usd: number | null;
  high_usd: number | null;
  error?: string | null;
  arithmetic?: string | null;
  formula?: string | null;
  params?: unknown;
  evidence?: MktEvidence[] | null;
  overlaps?: string | null;
  spread?: string | null;
}
interface MktSegment {
  id: string;
  name: string;
  parent?: string | null | undefined;
  depth?: number;
  summary?: string | null;
  size?: MktSize | null;
  rolled_usd?: {spend_usd?: number | null} | null;
  estimates?: MktEstimate[] | null;
  disagree?: boolean;
  bases?: string[] | null;
  players?: MktPlayer[] | null;
  self?: boolean;
  next?: boolean;
}
interface MktDefinition {
  description?: string | null;
  year?: string | number | null;
  geography?: string | null;
  currency?: string | null;
  includes?: string[] | null;
  excludes?: string[] | null;
  counting_rules?: string[] | null;
  params?: unknown;
}
interface MktSpend {
  definition?: MktDefinition | null;
  year?: string | number | null;
  basis?: string | null;
  segments?: MktSegment[] | null;
  total?: {spend_usd?: number | null; low_usd?: number | null; high_usd?: number | null} | null;
}
/** A shared parameter: its value, whether it is a judgement, and where it came from. A bare value is shown as {value}. */
interface MktParam { value?: unknown; judgement?: boolean; source?: string | null; }
interface MktBet { id: string; statement: string; status?: string | null; cites?: string[] | null; }
interface MktStrategy { bets?: MktBet[] | null; draft?: {revision?: number | null} | null; }
interface MktChainNode { id: string; name: string; type?: string; self?: boolean; }
interface MktChainLayer { key: string; title: string; nodes?: MktChainNode[] | null; }
type MktChainLayerWithNodes = MktChainLayer & {nodes: MktChainNode[]};
interface MktChainEdge { src: string; rel?: string; dst: string; }
interface MktChain { layers?: MktChainLayer[] | null; edges?: MktChainEdge[] | null; }

const MKT_METHODS: Record<string, string> = {top_down: 'Top down', bottom_up: 'Bottom up', player_sum: 'Player sum', owner: 'Owner'};
const MKT_KINDS: Record<string, string> = {reported: 'Reported', estimated: 'Estimated', claimed: 'Claimed'};
const MKT_METRICS: Record<string, string> = {revenue: 'Revenue', arr: 'ARR', customers: 'Customers', seats: 'Seats'};
const MKT_BASES: Record<string, string> = {product: 'Product', platform: 'Platform'};
const MKT_COLOURS = 8;

// S.me is typed from its signed-out value (null) in state.js, so the role is read through this typed accessor.
const mktMe = (): {role?: string} | null => S.me;
const mktSized = (data: MktSpend | null | undefined): boolean => (data?.segments || []).some(s => s.size);
const mktOwner = (): boolean => mktMe()?.role === 'owner';
const mktTrim = (x: number, d: number): string => String(Number(x.toFixed(d)));
function mktMoney(n: number | null | undefined): string {
  n = Number(n) || 0;
  const a = Math.abs(n);
  if (a >= 1e12) return '$' + mktTrim(n / 1e12, 2) + 'T';
  if (a >= 1e9) return '$' + mktTrim(n / 1e9, a >= 1e10 ? 1 : 2) + 'B';
  if (a >= 1e6) return '$' + mktTrim(n / 1e6, a >= 1e8 ? 0 : 1) + 'M';
  if (a >= 1e3) return '$' + mktTrim(n / 1e3, a >= 1e5 ? 0 : 1) + 'K';
  return '$' + mktTrim(n, 0);
}
const mktRange = (size: {low_usd: number | null; high_usd: number | null}): string => `${mktMoney(size.low_usd)}–${mktMoney(size.high_usd)}`;
const mktLink = (u: string | null | undefined): string => /^https?:\/\//i.test(u || '')
  ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(String(u).replace(/^https?:\/\/(www\.)?/i, '').slice(0, 60))}</a>` : esc(u || '');
const mktObject = (v: unknown): Record<string, MktParam> => {
  if (typeof v === 'string') { try { v = JSON.parse(v); } catch { return {}; } }
  // A JSON object from the server, whose values are parameters (see MktParam).
  return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, MktParam> : {};
};
const mktNice = (raw: number): number => {            // a round step near raw: 1, 2 or 5 times a power of ten
  const p = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
};

interface MktRect { x: number; y: number; w: number; h: number; }

// Squarified treemap (Bruls, Huizing, van Wijk): items {value}, laid into the rectangle in rows that stay near square.
function mktSquarify<T extends {value: number}>(items: T[], x: number, y: number, w: number, h: number): Array<T & MktRect> {
  const out: Array<T & MktRect> = [], total = items.reduce((a, b) => a + b.value, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest: Array<T & {area: number}> = items.map(i => ({...i, area: i.value * scale})).sort((a, b) => b.area - a.area);
  const worst = (row: Array<{area: number}>, short: number): number => {
    // A row always holds a tile, so its first and last tiles exist.
    const s = row.reduce((a, b) => a + b.area, 0), mx = row[0]!.area, mn = row[row.length - 1]!.area;
    return Math.max(short * short * mx / (s * s), (s * s) / (short * short * mn));
  };
  while (rest.length) {
    // The loop runs while rest has a tile, so rest[0] exists.
    const short = Math.min(w, h), row: Array<T & {area: number}> = [rest[0]!];
    let i = 1;
    // i < rest.length is checked before each rest[i] is read.
    while (i < rest.length && worst([...row, rest[i]!], short) <= worst(row, short)) row.push(rest[i++]!);
    const s = row.reduce((a, b) => a + b.area, 0);
    if (w >= h) {
      const cw = s / h; let cy = y;
      row.forEach(r => { const ch = r.area / cw; out.push({...r, x, y: cy, w: cw, h: ch}); cy += ch; });
      x += cw; w -= cw;
    } else {
      const ch = s / w; let cx = x;
      row.forEach(r => { const rw = r.area / ch; out.push({...r, x: cx, y, w: rw, h: ch}); cx += rw; });
      y += ch; h -= ch;
    }
    rest = rest.slice(i);
  }
  return out;
}

interface MktTreeGroup { seg: MktSegment; colour: number; value: number; kids: Array<{seg: MktSegment; value: number; whole?: undefined}>; unsized: MktSegment[]; }
interface MktTree { drawn: MktTreeGroup[]; unsized: MktSegment[]; }
/** A top segment with a size of its own. */
interface MktSizedGroup extends MktTreeGroup { seg: MktSegment & {size: MktSize}; }
/** A sized top segment with the spend and growth the growth chart places. */
interface MktGrowthGroup extends MktSizedGroup { seg: MktSegment & {size: MktSize & {spend_usd: number; growth_pct: number}}; }

// The segments as the map draws them: each top segment with its sized sub-segments. A segment with no size of its own
// takes the API's rolled-up spend (its parts, at any depth), so the map and the total agree; a segment with nothing
// sized is not drawn, and is listed instead.
function mktTree(data: MktSpend): MktTree {
  const segs = data.segments || [], ids = new Set(segs.map(s => s.id));
  const size = (s: MktSegment): number => Number(s.rolled_usd?.spend_usd) || 0;
  const tops = segs.filter(s => !s.parent || !ids.has(s.parent));
  const tree = tops.map((seg, i) => {
    const kids = segs.filter(c => c.parent === seg.id).map(c => ({seg: c, value: size(c)}));
    const sized = kids.filter(k => k.value > 0);
    return {seg, colour: i % MKT_COLOURS, value: size(seg), kids: sized,
      unsized: kids.filter(k => !k.value).map(k => k.seg)};
  });
  return {drawn: tree.filter(g => g.value > 0), unsized: [...tree.filter(g => !(g.value > 0)).map(g => g.seg), ...tree.flatMap(g => g.unsized)]};
}

const mktChips = (seg: MktSegment, room: boolean): string => room ? [
  seg.disagree ? '<span class="pill waiting">Disagree</span>' : '',
  seg.self ? '<span class="pill us">Us</span>' : '',
  seg.size && !seg.size.accepted ? '<span class="pill">Proposed</span>' : '',
].join('') : '';

function mktTileHtml(seg: MktSegment, value: number, r: MktRect, colour: number, nested: boolean): string {
  const mid = r.w > 58 && r.h > 34, big = r.w > 96 && r.h > 70;
  const shown = Number(seg.size?.spend_usd) || value;
  const label = `${seg.name}, ${mktMoney(shown)}${seg.size?.confidence === 'low' ? ', low confidence' : ''}${seg.disagree ? ', estimates disagree' : ''}`;
  const cls = ['mkt-box', 'mkt-tile', seg.size?.confidence === 'low' ? 'low' : '', seg.self ? 'self' : '', nested ? 'nested' : ''].filter(Boolean).join(' ');
  return `<div class="${cls}" role="button" tabindex="0" data-seg="${esc(seg.id)}" data-value="${value}" aria-label="${esc(label)}" title="${esc(label)}"
    style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;--c:var(--mkt-c${colour + 1})">${mid
      ? `<span class="mkt-n">${esc(seg.name)}</span><span class="mkt-v">${mktMoney(shown)}</span>${big && seg.size?.spend_usd != null ? `<span class="mkt-r">${mktRange(seg.size)}</span>` : ''}${mktChips(seg, big)}` : ''}</div>`;
}

function mktBoxHtml(r: MktRect & {g: MktTreeGroup}): string {
  const g = r.g, seg = g.seg, head = 26;
  if (!(g.kids.length && r.w > 120 && r.h > 80)) return mktTileHtml(seg, g.value, r, g.colour, false);
  const sum = g.kids.reduce((a, k) => a + k.value, 0), rest = g.value - sum;
  // Parts that add up to less than the box keep their own dollars and a plain tile takes the rest, so each area matches
  // its label; parts that overlap (more than the box) are scaled down to fit it.
  const parts: MktPart[] = g.kids.map(k => ({seg: k.seg, value: rest < 0 ? k.value * g.value / sum : k.value}));
  if (rest > g.value * 0.01) parts.push({rest: true, value: rest});
  const inner = mktSquarify(parts, 0, head, r.w, r.h - head);
  const shown = Number(seg.size?.spend_usd) || g.value;
  return `<div class="mkt-box mkt-group${seg.self ? ' self' : ''}" data-value="${g.value}" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;--c:var(--mkt-c${g.colour + 1})">
    <button class="mkt-gh" type="button" data-seg="${esc(seg.id)}" aria-label="${esc(seg.name + ', ' + mktMoney(shown))}"><span class="mkt-n">${esc(seg.name)}</span><b>${mktMoney(shown)}</b>${mktChips(seg, r.w > 240)}</button>
    ${inner.map(k => k.rest ? mktRestHtml(seg, k) : mktTileHtml(k.seg, k.value, k, g.colour, true)).join('')}</div>`;
}
/** A part of a group box: one sized sub-segment, or the rest of the group (which has no segment of its own). */
type MktPart = {seg: MktSegment; rest?: false; value: number} | {rest: true; value: number; seg?: undefined};

function mktRestHtml(seg: MktSegment, r: MktRect & {value: number}): string {
  const label = `Rest of ${seg.name}`;
  return `<div class="mkt-box mkt-rest" data-value="${r.value}" aria-label="${esc(label + ', ' + mktMoney(r.value))}" title="${esc(label)}"
    style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px">${r.w > 58 && r.h > 34
      ? `<span class="mkt-n">${esc(label)}</span><span class="mkt-v">${mktMoney(r.value)}</span>` : ''}</div>`;
}

// Both charts stand the same height, so side by side they line up.
const mktChartH = (W: number): number => Math.round(W < 480 ? W * 1.05 : Math.min(420, Math.max(300, W * 0.7)));
// "Sure" when every top segment's size is high confidence; otherwise the figures are estimates.
const mktSure = (data: MktSpend): string => {
  const tops = mktTree(data).drawn.filter((g): g is MktSizedGroup => g.seg.size != null);
  return tops.length && tops.every(g => g.seg.size.confidence === 'high') ? 'Sure' : 'Estimated';
};
const mktSureHtml = (data: MktSpend): string => `<span class="mkt-sure${mktSure(data) === 'Sure' ? ' r' : ''}">${mktSure(data)}</span>`;
// The segments the growth chart can place: sized, with a growth figure.
const mktGrowthPts = (tree: MktTree): MktGrowthGroup[] => tree.drawn.filter((g): g is MktGrowthGroup => g.seg.size?.spend_usd != null && Number.isFinite(g.seg.size.growth_pct));

// ------------------------------------------------------------------ Where is the money going?

// Nothing sized yet: the owner describes the market in a sentence and the Librarian sizes it (POST
// /v2/market/spend/research); the notice then stands in for the form until the sizes arrive.
function mktSizeForm(host: HTMLElement, description: string | null | undefined): void {
  host.innerHTML = `<form class="mkt-card mkt-start" data-mkt-size novalidate>
      <label class="mkt-lab" for="mkt-describe">Describe the market in a sentence</label>
      <input id="mkt-describe" name="description" type="text" maxlength="400" autocomplete="off" value="${esc(description || '')}" placeholder="What small offices spend on cleaning services">
      <p class="err" data-mkt-error role="alert" hidden></p>
      <div class="market-start-actions"><button class="primary" type="submit">Size the market</button></div></form>`;
  // Each element was rendered just above, by this function.
  const form = host.querySelector<HTMLFormElement>('[data-mkt-size]')!;
  const line = form.querySelector<HTMLElement>('[data-mkt-error]')!;
  const submit = form.querySelector<HTMLButtonElement>('[type=submit]')!;
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (submit.disabled) return;
    // The form template above has this input.
    const description = form.querySelector<HTMLInputElement>('[name=description]')!.value.trim();
    if (!description) { line.textContent = 'Say what the market is.'; line.hidden = false; return; }
    line.hidden = true;
    submit.disabled = true;
    try {
      const sent = await post('/v2/market/spend/research', {description});
      host.innerHTML = `<div class="mkt-card market-researching" data-mkt-researching role="status"><span class="market-spin" aria-hidden="true"></span>
        <div><strong>On it.</strong>${sent?.task_id ? `<p><a href="#/task/${esc(sent.task_id)}">View task</a></p>` : ''}</div></div>`;
    } catch (error) {
      submit.disabled = false;
      line.textContent = errorMessage(error, 'That did not go through.'); line.hidden = false;
    }
  };
}

interface MktCiteLoading { loading: true; bets?: undefined; all?: undefined; revision?: undefined; }
interface MktCiteReady { loading?: false; bets: MktBet[]; all: MktBet[]; revision: number | null; }
/** The bet picker of an open segment panel: `loading` while the strategy is read, else the bets to cite into. */
type MktCite = MktCiteLoading | MktCiteReady;
interface MktSpot { tx: number; ty: number; anchor: string; x0: number; x1: number; y0: number; y1: number; }
type MktSpotRow = [number, number, string, number, number, number, number];
interface MktDot { g: MktGrowthGroup; x: number; y: number; r: number; text: string; at?: MktSpot; }
interface MktCtx {
  dead: boolean;
  dimension: string;
  data: MktSpend | null;
  basis: string | undefined;
  sel: string;
  cite: MktCite | null;
  load: number;
  observer: ResizeObserver | null;
  dialogs: Record<string, HTMLDialogElement>;
  cache: Map<string, MktSpend | null | undefined>;
  tree?: MktTree;
}

// `first` holds the page's first reads of both dimensions; the switch shows the ones that are sized.
function mktSpend(host: HTMLElement, first: Record<string, MktSpend | null>) {
  const dims = ([['offering', 'Offering'], ['buyer', 'Buyer']] satisfies Array<[string, string]>).filter(([key]) => mktSized(first[key]));
  // redesign-market.js calls this only when a dimension is sized, so dims is not empty.
  const ctx: MktCtx = {dead: false, dimension: dims[0]![0], data: null, basis: '', sel: '', cite: null, load: 0, observer: null, dialogs: {},
    cache: new Map(dims.map(([key]): [string, MktSpend | null | undefined] => [key + '|', first[key]]))};

  const seg = (id: string): MktSegment | undefined => (ctx.data?.segments || []).find(s => s.id === id);

  // A read of the dimension and basis shown; `fresh` after a write, which every copy kept here predates.
  async function load(fresh = false): Promise<void> {
    const mine = ++ctx.load, key = ctx.dimension + '|' + ctx.basis;
    if (fresh) ctx.cache.clear();
    let data = ctx.cache.get(key);
    if (!data) {
      try {
        data = await get<MktSpend>('/v2/market/spend?' + new URLSearchParams({dimension: ctx.dimension, ...(ctx.basis ? {basis: ctx.basis} : {})}), {fresh});
      } catch (error) { if (!ctx.dead) toast(errorMessage(error, 'The market did not load.'), true); return; }
      if (ctx.dead || mine !== ctx.load) return;
      ctx.cache.set(key, data);
    }
    ctx.data = data;
    ctx.basis = data.basis || ctx.basis;
    render();
    if (ctx.dialogs.seg?.open) fillPanel();
  }
  function show(dimension: string): void {
    if (!dims.some(([key]) => key === dimension) || dimension === ctx.dimension && ctx.data) return;
    ctx.dimension = dimension; ctx.basis = '';
    void load();
  }

  // ---- the section
  function render(): void {
    const data = ctx.data;
    ctx.observer?.disconnect();
    if (!data || !mktSized(data)) { host.innerHTML = ctlHtml() + '<p class="market-none">Not sized yet.</p>'; return; }
    const tree = mktTree(data);
    ctx.tree = tree;
    const growth = mktGrowthPts(tree).length > 0;
    host.innerHTML = ctlHtml() + `<div class="mkt-two${growth ? '' : ' solo'}">
        <div class="mkt-card"><div class="mkt-map"></div>
          <p class="mkt-legend"><span class="mkt-key"><i class="mkt-sw low"></i>Low confidence</span><span class="mkt-key"><i class="mkt-sw us"></i>Us</span></p></div>
        ${growth ? '<div class="mkt-card"><h4 class="mkt-lab">Size vs growth</h4><div class="mkt-growth"></div></div>' : ''}</div>`
      + (tree.unsized.length ? unsizedHtml(tree.unsized) : '');
    // render() has just written both containers; the growth chart's only exists when there is growth to show.
    const map = host.querySelector<HTMLElement>('.mkt-map')!, area = host.querySelector<HTMLElement>('.mkt-growth');
    const widths = (): string => map.clientWidth + ',' + (area?.clientWidth || 0);
    let frame = 0, drawn = '';
    const draw = (): void => {
      frame = 0; drawn = widths();
      if (map.clientWidth) drawMap(map, map.clientWidth);
      if (area?.clientWidth) drawGrowth(area, area.clientWidth);
    };
    ctx.observer = new ResizeObserver(() => { if (widths() !== drawn && !frame) frame = requestAnimationFrame(draw); });
    ctx.observer.observe(map);
    if (area) ctx.observer.observe(area);
    draw();
  }

  function ctlHtml(): string {
    const d: Partial<MktSpend> = ctx.data || {};
    const bases = [...new Set((d.segments || []).flatMap(s => s.bases || []))];
    return `<div class="mkt-ctl">${dims.length > 1 ? `<div class="mkt-seg" role="group" aria-label="Spend by">${dims.map(([key, label]) =>
        `<button type="button" data-dim="${key}" aria-pressed="${key === ctx.dimension}">${label}</button>`).join('')}</div>` : ''}
      ${bases.length > 1 ? `<div class="mkt-seg" role="group" aria-label="Basis">${bases.map(b =>
        `<button type="button" data-basis="${esc(b)}" aria-pressed="${b === ctx.basis}">${esc(MKT_BASES[b] || b)}</button>`).join('')}</div>` : ''}
      <span class="spacer"></span>
      ${d.definition ? '<button class="ghost small" type="button" data-mkt-def>Definition</button>' : ''}</div>`;
  }

  const unsizedHtml = (list: MktSegment[]): string => `<div class="mkt-sec"><h4 class="mkt-lab">Not sized yet</h4><div class="mkt-chips">${list.map(s =>
    `<button class="mkt-pick${s.self ? ' self' : ''}" type="button" data-seg="${esc(s.id)}">${esc(s.name)}</button>`).join('')}</div></div>`;

  // render() sets ctx.tree before it draws: a draw without it is a bug, and it throws a plain Error.
  function drawnTree(): MktTree {
    if (!ctx.tree) throw new Error('the market tree is not loaded');
    return ctx.tree;
  }

  function drawMap(map: HTMLElement, W: number): void {
    const H = mktChartH(W);
    map.style.height = H + 'px';
    // render() sets ctx.tree before it draws.
    const laid = mktSquarify(drawnTree().drawn.map(g => ({g, value: g.value})), 0, 0, W, H);
    map.innerHTML = laid.map(mktBoxHtml).join('');
  }

  // One dot per top segment: spend across (log when the range is wide), growth up, ours filled.
  function drawGrowth(area: HTMLElement, W: number): void {
    const pts = mktGrowthPts(drawnTree());
    const H = mktChartH(W), m = {l: 46, r: 14, t: 14, b: 34};
    const xs = pts.map(g => g.seg.size.spend_usd), lo = Math.min(...xs), hi = Math.max(...xs);
    const log = lo > 0 && hi / lo > 20;
    const x0 = log ? lo / 1.7 : 0, x1 = log ? hi * 1.7 : hi * 1.1;
    const fx = (v: number): number => m.l + (W - m.l - m.r) * (log ? Math.log(v / x0) / Math.log(x1 / x0) : (v - x0) / (x1 - x0));
    const gs = pts.map(g => g.seg.size.growth_pct);
    const y0 = Math.min(0, Math.min(...gs)), y1 = Math.max(10, Math.max(...gs) * 1.12);
    const ystep = mktNice((y1 - y0) / 5), fy = (v: number): number => H - m.b - (H - m.t - m.b) * (v - y0) / (y1 - y0);
    const yt: number[] = []; for (let t = Math.ceil(y0 / ystep) * ystep; t <= y1; t += ystep) yt.push(t);
    let xt: number[] = [];
    if (log) { for (let p = 10 ** Math.floor(Math.log10(x0)); p <= x1; p *= 10) for (const f of [1, 2, 5]) if (p * f >= x0 && p * f <= x1) xt.push(p * f); }
    else { const s = mktNice(x1 / 5); for (let t = 0; t <= x1; t += s) xt.push(t); }
    if (xt.length > 6) xt = xt.filter((_, i) => i % Math.ceil(xt.length / 6) === 0);
    const dots: MktDot[] = pts.map(g => ({g, x: fx(g.seg.size.spend_usd), y: fy(g.seg.size.growth_pct), r: 7,
      text: g.seg.name.length > 24 ? g.seg.name.slice(0, 23) + '…' : g.seg.name}));
    const spots: MktSpot[] = [];
    const free = (box: MktSpot, mine: MktDot): boolean => box.x0 >= 2 && box.x1 <= W - 2 && box.y0 >= 2 && box.y1 <= H - m.b + 6
      && ![...spots, ...dots.filter(d => d !== mine).map(d => ({x0: d.x - d.r - 1, x1: d.x + d.r + 1, y0: d.y - d.r - 1, y1: d.y + d.r + 1}))]
        .some(o => box.x0 < o.x1 && box.x1 > o.x0 && box.y0 < o.y1 && box.y1 > o.y0);
    [...dots].sort((a, b) => a.y - b.y).forEach(d => {
      const w = d.text.length * 6.3 + 2, spot = ([
        [d.x + d.r + 5, d.y + 4, 'start', d.x + d.r + 5, d.x + d.r + 5 + w, d.y - 8, d.y + 5],
        [d.x - d.r - 5, d.y + 4, 'end', d.x - d.r - 5 - w, d.x - d.r - 5, d.y - 8, d.y + 5],
        [d.x, d.y - d.r - 6, 'middle', d.x - w / 2, d.x + w / 2, d.y - d.r - 18, d.y - d.r - 4],
        [d.x, d.y + d.r + 14, 'middle', d.x - w / 2, d.x + w / 2, d.y + d.r + 3, d.y + d.r + 16]] satisfies MktSpotRow[])
        .map(([tx, ty, anchor, x0, x1, y0, y1]) => ({tx, ty, anchor, x0, x1, y0, y1}));
      // spot is a non-empty literal list of candidate positions.
      d.at = spot.find(s => free(s, d)) || spot[0]!;
      spots.push(d.at);
    });
    // Each dot's at was set by the loop above.
    area.innerHTML = `<svg class="mkt-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Segment size against growth">
      ${yt.map(t => `<line class="g" x1="${m.l}" x2="${W - m.r}" y1="${fy(t)}" y2="${fy(t)}"${t === 0 ? ' data-zero' : ''}/><text class="t" x="${m.l - 6}" y="${fy(t) + 4}" text-anchor="end">${mktTrim(t, 1)}%</text>`).join('')}
      ${xt.map(t => `<line class="g v" x1="${fx(t)}" x2="${fx(t)}" y1="${m.t}" y2="${H - m.b}"/><text class="t" x="${fx(t)}" y="${H - m.b + 16}" text-anchor="middle">${t ? mktMoney(t) : '0'}</text>`).join('')}
      <text class="t cap" x="${W - m.r}" y="${H - 2}" text-anchor="end">Spend${log ? ' (log scale)' : ''}</text>
      <text class="t cap" x="${m.l}" y="10">Growth</text>
      ${dots.map(d => `<g class="mkt-dot${d.g.seg.self ? ' self' : ''}" role="button" tabindex="0" data-seg="${esc(d.g.seg.id)}" aria-label="${esc(`${d.g.seg.name}, ${mktMoney(d.g.seg.size.spend_usd)}, growth ${mktTrim(d.g.seg.size.growth_pct, 1)}%`)}">
        <circle cx="${d.x}" cy="${d.y}" r="${d.r}"/><text x="${d.at!.tx}" y="${d.at!.ty}" text-anchor="${d.at!.anchor}">${esc(d.text)}</text></g>`).join('')}</svg>`;
  }

  // ---- panels
  function dialog(name: string, label: string): HTMLDialogElement {
    const known = ctx.dialogs[name];
    if (known) return known;
    const dlg = document.createElement('dialog');
    ctx.dialogs[name] = dlg;
    dlg.className = 'tmodal mkt-panel'; dlg.setAttribute('aria-label', label);
    dlg.addEventListener('click', ev => {
      if (ev.target === dlg || eventElement(ev)?.closest('[data-mkt-close]')) dlg.close();
    });
    bindPanel(dlg);
    document.body.appendChild(dlg);
    return dlg;
  }

  const head = (title: string, crumb?: string | null): string => `<div class="tmodal-head"><div class="mkt-ph">${crumb ? `<small>${esc(crumb)}</small>` : ''}<h2>${esc(title)}</h2></div><span class="spacer"></span>
    <button class="ghost tmodal-x" type="button" data-mkt-close aria-label="Close">✕</button></div>`;
  const kv = (rows: unknown[]): string => `<dl class="mkt-kv">${rows.filter((r): r is [string, string] => !!r).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
  const block = (title: string, body: string): string => body ? `<section class="mkt-blk"><h3 class="strat-k">${title}</h3>${body}</section>` : '';
  const list = (items: string[] | null | undefined): string => items?.length ? `<ul class="mkt-list">${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` : '';

  function openDefinition(): void {
    const def = ctx.data?.definition;
    if (!def) return;
    const dlg = dialog('def', 'Definition');
    const params = Object.entries(mktObject(def.params));
    dlg.innerHTML = head('Definition', '') + `<div class="tmodal-body mkt-body">
      ${def.description ? `<p>${esc(def.description)}</p>` : ''}
      ${kv([def.year && ['Year', esc(def.year)], def.geography && ['Where', esc(def.geography)], def.currency && ['Currency', esc(def.currency)]])}
      ${block('Includes', list(def.includes))}${block('Excludes', list(def.excludes))}${block('Counting rules', list(def.counting_rules))}
      ${block('Shared params', params.length ? `<ul class="mkt-list">${params.map(([name, p]) => { p = typeof p === 'object' && p ? p : {value: p};
        return `<li><code>${esc(name)}</code> ${esc(p.value)}${p.judgement ? ' <span class="pill">Judgement</span>' : ''}${p.source ? '<br>' + mktLink(p.source) : ''}</li>`; }).join('')}</ul>` : '')}</div>`;
    if (!dlg.open) dlg.showModal();
  }

  function openPanel(id: string): void {
    if (!seg(id)) return;
    ctx.sel = id; ctx.cite = null;
    const dlg = dialog('seg', 'Segment');
    fillPanel();
    if (!dlg.open) dlg.showModal();
  }

  function estimateHtml(e: MktEstimate, s: MktSegment): string {
    const params = Object.entries(mktObject(e.params)), owner = mktOwner();
    const accepted = e.status === 'accepted' || (s.size?.id === e.id && s.size.accepted);
    const evidence = (Array.isArray(e.evidence) ? e.evidence : []).filter(x => x && (x.source_url || x.quote));
    return `<li class="mkt-est" data-size="${esc(e.id)}"><div class="mkt-est-h"><b>${esc(MKT_METHODS[e.method] || e.method)}</b>
        ${e.spend_usd == null ? `<span>${esc(e.error || '')}</span>` : `<span>${mktMoney(e.spend_usd)} <small>${mktRange(e)}</small></span>`}
        ${accepted ? '<span class="pill active">Accepted</span>' : '<span class="pill">Proposed</span>'}
        ${owner && !accepted ? `<button class="ghost mkt-mini" type="button" data-mkt-accept="${esc(e.id)}">Accept</button>` : ''}</div>
      ${e.arithmetic ? `<p class="mkt-arith">${esc(e.arithmetic)}</p>` : ''}${e.formula ? `<p class="mkt-arith"><code>${esc(e.formula)}</code></p>` : ''}
      ${params.length ? `<ul class="mkt-params">${params.map(([name, p]) => { p = typeof p === 'object' && p ? p : {value: p};
        return `<li>${owner ? `<form data-mkt-param data-size="${esc(e.id)}" data-name="${esc(name)}"><label><code>${esc(name)}</code>
            <input name="value" type="number" step="any" value="${esc(p.value)}" aria-label="${esc(name)}"></label><button class="ghost mkt-mini" type="submit">Save</button></form>`
          : `<code>${esc(name)}</code> ${esc(p.value)}`}${p.judgement ? ' <span class="pill">Judgement</span>' : ''}${p.source ? ` <span class="mkt-src">${mktLink(p.source)}</span>` : ''}</li>`; }).join('')}</ul>` : ''}
      ${evidence.length ? `<ul class="mkt-list mkt-ev">${evidence.map(x => `<li>${x.source_url ? mktLink(x.source_url) : ''}${x.source_kind ? ` <span class="pill">${esc(x.source_kind)}</span>` : ''}${x.quote ? `<br><q>${esc(x.quote)}</q>` : ''}</li>`).join('')}</ul>` : ''}</li>`;
  }

  function figureHtml(f: MktFigure): string {
    const n = f.metric === 'revenue' || f.metric === 'arr' ? mktMoney(f.value) : Number(f.value).toLocaleString('en-US');
    const what = f.value === null || f.value === undefined ? esc(f.text || '') : `${esc(MKT_METRICS[f.metric] || f.metric)} ${n}`;
    return `<span class="mkt-fig">${what}${f.kind ? ` · ${esc(MKT_KINDS[f.kind] || f.kind)}` : ''}${f.as_of ? ` · ${esc(f.as_of)}` : ''}${f.source_url ? ' · ' + mktLink(f.source_url) : ''}</span>`;
  }

  function fillPanel(): void {
    const dlg = ctx.dialogs.seg, s = seg(ctx.sel);
    if (!dlg) return;
    if (!s) { dlg.close(); return; }
    const z = s.size, parent = s.parent ? seg(s.parent) : null, owner = mktOwner();
    const estimates = s.estimates || [], over = estimates.map(e => e.overlaps).find(Boolean) || z?.overlaps;
    const spread = estimates.map(e => e.spread).find(Boolean) || z?.spread;
    const span = z ? Math.max(Number(z.high_usd), Number(z.spend_usd)) * 1.08 || 1 : 1, pc = (v: number | null): string => (100 * Number(v) / span).toFixed(2) + '%';
    dlg.innerHTML = head(s.name, parent?.name) + `<div class="tmodal-body mkt-body">
      <div class="mkt-chips">${z ? `<span class="pill${z.confidence === 'low' ? '' : ' active'}">${esc(z.confidence)} confidence</span>` : '<span class="pill">Not sized yet</span>'}
        ${s.disagree ? '<span class="pill waiting">Disagree</span>' : ''}${s.self ? '<span class="pill us">Us</span>' : ''}${z && !z.accepted ? '<span class="pill">Proposed</span>' : ''}</div>
      ${s.summary ? `<p>${esc(s.summary)}</p>` : ''}
      ${z?.error ? `<p>${esc(z.error)}</p>` : ''}
      ${z && z.spend_usd != null ? kv([['Spend', mktMoney(z.spend_usd)], ['Range', mktRange(z)], typeof z.growth_pct === 'number' && Number.isFinite(z.growth_pct) ? ['Growth', mktTrim(z.growth_pct, 1) + '%'] : null, ctx.data?.year && ['Year', esc(ctx.data.year)]])
        + `<div class="mkt-bar solo" aria-hidden="true"><div class="span" style="left:${pc(z.low_usd)};width:calc(${pc(z.high_usd)} - ${pc(z.low_usd)})"></div><div class="mid" style="left:${pc(z.spend_usd)}"></div></div>` : ''}
      ${block('Estimates', estimates.length ? `<ul class="mkt-ests">${estimates.map(e => estimateHtml(e, s)).join('')}</ul>` : '')}
      ${block('Overlaps', over ? `<p>${esc(over)}</p>` : '')}${block('Spread', spread ? `<p>${esc(spread)}</p>` : '')}
      ${block('Players', (s.players || []).length ? `<ul class="mkt-list">${(s.players || []).map(p => `<li><b>${esc(p.name)}</b>${(p.figures || []).length ? '<br>' + (p.figures || []).map(figureHtml).join('<br>') : ''}</li>`).join('')}</ul>` : '')}
      ${owner ? `<div class="mkt-acts"><button class="ghost" type="button" data-mkt-research>Research this segment</button>
        <button class="ghost" type="button" data-mkt-cite-open>Cite in a bet</button></div>${citeHtml()}` : ''}</div>`;
  }

  function citeHtml(): string {
    const c = ctx.cite;
    if (!c) return '';
    if (c.loading) return '<p class="muted mkt-cite">Loading…</p>';
    if (!c.bets.length) return '<p class="muted mkt-cite">No active bets.</p>';
    return `<form class="mkt-cite" data-mkt-cite><select name="bet" aria-label="Bet">${c.bets.map(b =>
      `<option value="${esc(b.id)}">${esc(b.statement)}</option>`).join('')}</select><button class="primary" type="submit">Cite</button></form>`;
  }

  async function write(job: () => Promise<unknown>, ok?: string): Promise<void> {
    try { await job(); if (ok) toast(ok); await load(true); } catch (error) { toast(errorMessage(error, 'That did not save'), true); }
  }

  // ---- events
  // The seg a [data-seg] element names: the selector guarantees the attribute, so a missing one throws.
  function segOf(el: HTMLElement): string {
    const segName = el.dataset.seg;
    if (segName === undefined) throw new Error('a [data-seg] element has no seg');
    return segName;
  }

  host.addEventListener('click', ev => {
    // Each selector here matches only elements that carry the attribute its dataset read names.
    const target = eventElement(ev);
    const dim = target?.closest<HTMLElement>('[data-dim]');
    if (dim) { show(dim.dataset.dim!); return; }
    const basis = target?.closest<HTMLElement>('[data-basis]');
    if (basis) { if (basis.dataset.basis !== ctx.basis) { ctx.basis = basis.dataset.basis; void load(); } return; }
    if (target?.closest('[data-mkt-def]')) { openDefinition(); return; }
    const pick = target?.closest<HTMLElement>('[data-seg]');
    if (pick) openPanel(segOf(pick));
  });
  host.addEventListener('keydown', ev => {
    const pick = ev.key === 'Enter' || ev.key === ' ' ? eventElement(ev)?.closest<HTMLElement>('[role=button][data-seg]') : null;
    if (pick) { ev.preventDefault(); openPanel(segOf(pick)); }
  });
  // The panel is a dialog outside the page, so it listens for itself.
  function bindPanel(dlg: HTMLDialogElement): void {
    dlg.addEventListener('click', ev => {
      const target = eventElement(ev);
      // Each selector here matches only elements that carry the attribute its dataset read names.
      const accept = target?.closest<HTMLElement>('[data-mkt-accept]');
      if (accept) { void write(() => post(`/v2/market/sizes/${encodeURIComponent(accept.dataset.mktAccept!)}/accept`, {}), 'Accepted'); return; }
      if (target?.closest('[data-mkt-research]')) { window.DocsEditor?.researchDialog({target: ctx.sel}); return; }
      if (target?.closest('[data-mkt-cite-open]')) { void openCite(); }
    });
    dlg.addEventListener('submit', ev => {
      ev.preventDefault();
      const target = eventElement(ev);
      const param = target?.closest<HTMLFormElement>('[data-mkt-param]'), cite = target?.closest<HTMLFormElement>('[data-mkt-cite]');
      // estimateHtml writes data-size, data-name and the value input into each parameter form; citeHtml writes the bet select.
      if (param) {
        // estimateHtml writes this input into every parameter form.
        const input = param.querySelector<HTMLInputElement>('input[name=value]')!;
        const value = Number(input.value);
        if (input.value.trim() === '' || !Number.isFinite(value)) { toast('Enter a number', true); return; }
        void write(() => post(`/v2/market/sizes/${encodeURIComponent(param.dataset.size!)}/params`, {params: {[param.dataset.name!]: value}}));
      } else if (cite) void citeBet(cite.querySelector<HTMLSelectElement>('select[name=bet]')!.value);
    });
  }

  async function openCite(): Promise<void> {
    ctx.cite = {loading: true}; fillPanel();
    try {
      const strategy = await get<MktStrategy>('/v2/strategy', {fresh: true});
      if (ctx.dead) return;
      ctx.cite = {bets: (strategy.bets || []).filter(b => b.status === 'active' && !(b.cites || []).includes(ctx.sel)), all: strategy.bets || [], revision: strategy.draft?.revision ?? null};
    } catch (error) { ctx.cite = null; toast(errorMessage(error, 'Could not load the bets'), true); }
    fillPanel();
  }
  async function citeBet(id: string): Promise<void> {
    const cite = ctx.cite, bet = (cite?.all || []).find(b => b.id === id);
    if (!bet || !cite) return;
    try {
      await post(`/v2/strategy/bets/${encodeURIComponent(id)}`, {cites: [...(bet.cites || []), ctx.sel], revision: cite.revision});
      toast('Cited');
      ctx.cite = null; fillPanel();
    } catch (error) { toast(errorMessage(error, 'Could not cite it'), true); }
  }

  void load();
  return {show, stop() {
    ctx.dead = true; ctx.observer?.disconnect();
    Object.values(ctx.dialogs).forEach(d => { d.remove(); });
  }};
}

// ------------------------------------------------------------------ Value chain

// The layers that hold someone, each node a link to its note with the money it has where the page knows it (`money`:
// id to dollars a year). Empty when no layer holds anyone.
function mktChainHtml(data: MktChain | null | undefined, money: Map<string, number>): string {
  const layers = (data?.layers || []).filter((l): l is MktChainLayerWithNodes => (l.nodes || []).length > 0);
  if (!layers.length) return '';
  return `<div class="mkt-chain" style="--n:${layers.length}">${layers.map(l => `<section class="mkt-layer" data-layer="${esc(l.key)}"><h4 class="mkt-lab">${esc(l.title)}</h4>
      <div class="mkt-nodes">${l.nodes.map(n => `<a class="mkt-node${n.self ? ' self' : ''}" href="${marketHref(n.id)}" data-node="${esc(n.id)}" title="${esc(n.name)}">
        <span class="mkt-nn">${esc(n.name)}</span>${money.get(n.id) ? `<b>${mktMoney(money.get(n.id))}</b>` : ''}</a>`).join('')}</div></section>`).join('')}</div>`;
}

// A node lit shows what it connects to; the rest step back.
function mktChainBind(chain: HTMLElement, edges: MktChainEdge[] | null | undefined): void {
  const near = new Map<string, Set<string>>();
  // set() has just stored the set that get() reads back.
  (edges || []).forEach(e => { (near.get(e.src) || near.set(e.src, new Set()).get(e.src)!).add(e.dst); (near.get(e.dst) || near.set(e.dst, new Set()).get(e.dst)!).add(e.src); });
  const light = (id: string | undefined): void => {
    chain.classList.toggle('lit', !!id);
    // Every .mkt-node is rendered with data-node (mktChainHtml).
    chain.querySelectorAll<HTMLElement>('.mkt-node').forEach(a => { a.classList.toggle('near', !!id && (a.dataset.node === id || !!near.get(id)?.has(a.dataset.node!))); });
  };
  chain.addEventListener('pointerover', ev => { const a = eventElement(ev)?.closest<HTMLElement>('.mkt-node'); if (a) light(a.dataset.node); });
  chain.addEventListener('pointerout', () => light(''));
  chain.addEventListener('focusin', ev => { const a = eventElement(ev)?.closest<HTMLElement>('.mkt-node'); if (a) light(a.dataset.node); });
  chain.addEventListener('focusout', () => light(''));
}
