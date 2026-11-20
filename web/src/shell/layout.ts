/* How the sheets size, tuck and snap: first a port of `Sheets.layout` in the design's tico/sheets-live.js (rules and
   sources in tico/build/layout.md), then changed for the layers decided Nov 19 (plan/layers.html, its narrowing demo):
   - One order, Tico › Company › Thread › Artifact, left to right. A layer may be missing; what shows keeps the order.
   - The stacking order never changes: the leftmost sheet is on top. A sheet's `z` is its place in the row from the right.
   - Narrowing: no sheet shrinks under its floor. The right sheet slides under its left neighbour and leaves an edge
     (C.EDGE): the page under the thread first, then the thread under the company, then the company under Tico. How many
     stay open comes from the window (C.OPEN_AT) and from the stretching sheet's floor, whichever tucks more.
   - Click a lower sheet's edge to open it (`forward`): the sheets above it collapse to edges, Tico too; the sheets below
     it follow the window.
   - The sheet you navigate to counts as opened (its arrangement's `lead`: a page, a task, an agent's page or settings):
     it stays open, the sheets below it follow the window, and the sheets above it collapse to edges only as needed,
     Tico first, then the menu, then the thread. A view a thread leads (a page with a second chat) narrows as above.
   - With the stretching sheet tucked, the open sheets share the window by their widths.
   Pure: it reads only its arguments and never writes `saved`, which is what keeps automatic tucks out of memory. */

export type Arrangement = 'page' | 'chat' | 'agent' | 'task' | 'call';
export type ArrangementSpec = Arrangement | { name: 'chat'; chat?: boolean; panel?: boolean };
export type SheetId = 'tico' | 'menu' | 'list' | 'chat' | 'page' | 'panel' | 'widgets' | 'stage' | 'side';
/* open; 'fold' tucked by hand or by default; 'tucked' for room or under a sheet opened below it. Tucked, a sheet shows
   its edge. */
export type SheetState = 'open' | 'fold' | 'tucked';
export type Why = 'hand' | 'default' | 'room' | 'forward' | '';

export interface Memory {
  fold?: Partial<Record<SheetId, boolean>>;
  w?: Partial<Record<SheetId, number>>;
}
export interface Saved extends Partial<Record<Arrangement, Memory>> {
  v?: number;
  tico?: number;
}

export interface SheetLayout {
  id: SheetId;
  state: SheetState;
  why: Why;
  /* what shows of it: open, its column; tucked, its edge (C.EDGE) */
  x?: number;
  width: number;
  /* the card drawn: it reaches back under the sheet on its left (a tucked sheet's card, or an open one after an edge) */
  card?: { x: number; width: number };
  /* stacking: the leftmost sheet is highest */
  z: number;
  /* steps back: each card that reaches under its left neighbour sits C.STEP shorter, top and bottom */
  depth: number;
  /* its right neighbour's card reaches under it: it casts a side shadow on it */
  cast?: boolean;
  /* fixed sheets only */
  wanted?: number;
  def?: number;
  min?: number;
  max?: number;
  canFold?: boolean;
  reduced?: boolean;
  canDrag?: boolean;
  dragMax?: number;
}

export type Layout =
  | { mode: 'cards'; arr: Arrangement; width: number; sheets: []; acting: string[] }
  | { mode: 'sheets'; arr: Arrangement; width: number; stretch: { id: SheetId; width: number }; sheets: SheetLayout[]; acting: string[] };

export const C = {
  MARGIN: 8, // sheets to the window edge        tokens.css --gap-pane
  GAP: 8, // between two open sheets          tokens.css --gap-pane
  EDGE: 44, // what shows of a tucked sheet     tokens.css --tuck-edge
  UNDER: 16, // an open sheet after an edge starts this far under it, so its corners hide
  STEP: 10, // each card under its neighbour sits this much shorter, top and bottom
  OPEN_AT: [1360, 1080, 840], // at or over these widths, 4, 3 or 2 sheets stay open; under the last, 1 (plan/layers.html)
  FLOOR: 480, // the stretching sheet, open, is never narrower
  CARDS_AT: 760, // at this width and under: the phone's cards
  SHEETS_AT: 800, // coming back from cards: sheets return at this width (hysteresis)
  SNAP: 80, // drag this far past a floor: the sheet tucks
  UNSNAP: 40, // drag back to within this of the floor: it opens again
} as const;

/* Tico's mark at the top of its sheet and on its edge, and on the phone's lip (tokens.css --tico-mark) */
export const TICO_MARK = 22;
export const TICO_MARK_LIP = 26;

interface Spec {
  def: number;
  min: number;
  max: number;
  /* it can be tucked by hand (its fold control, a shortcut) */
  fold?: true;
}
export const SPEC = {
  tico: { def: 300, min: 260, max: 480, fold: true },
  menu: { def: 248, min: 220, max: 320, fold: true },
  list: { def: 288, min: 240, max: 400, fold: true }, // a task's list, in the menu's place
  chat: { def: 320, min: 280, max: 520 },
  taskchat: { def: 392, min: 360, max: 520 },
  widgets: { def: 360, min: 300, max: 480, fold: true },
  panel: { def: 344, min: 300, max: 480 },
  side: { def: 384, min: 300, max: 520, fold: true }, // a call's chat, transcript and people
} satisfies Record<string, Spec>;
type SpecId = keyof typeof SPEC;

interface Arr {
  row: SheetId[];
  stretch: SheetId;
  spec?: Partial<Record<SheetId, SpecId>>;
  foldByDefault?: SheetId[];
  /* the sheet the address opens: it stays open while the sheets above it collapse (see the head). None: a thread leads. */
  lead?: SheetId;
}
export const ARR: Record<Arrangement, Arr> = {
  page: { row: ['tico', 'menu', 'page'], stretch: 'page', lead: 'page' },
  chat: { row: ['tico', 'menu', 'chat', 'page', 'panel'], stretch: 'page', lead: 'page' }, // the page leads only with no chat
  agent: { row: ['tico', 'menu', 'chat', 'widgets'], stretch: 'chat', lead: 'widgets' }, // the agent's page and settings
  task: { row: ['tico', 'list', 'chat', 'page'], stretch: 'page', spec: { chat: 'taskchat' }, lead: 'page' },
  call: { row: ['tico', 'stage', 'side'], stretch: 'stage', foldByDefault: ['tico'], lead: 'stage' },
};

export const specOf = (arr: Arrangement, id: SheetId): Spec => SPEC[(ARR[arr].spec ?? {})[id] ?? (id as SpecId)];
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
export const nameOf = (a: ArrangementSpec): Arrangement => (typeof a === 'string' ? a : a.name);
/* how many sheets the window keeps open */
export const openCount = (width: number): number => {
  const i = C.OPEN_AT.findIndex(w => width >= w);
  return i < 0 ? 1 : C.OPEN_AT.length + 1 - i;
};

/* `forward`: a tucked sheet whose edge was clicked: it opens, and the sheets above it collapse to edges. */
export function layout(width: number, arrangement: ArrangementSpec, saved: Saved | null | undefined, forward?: SheetId | null): Layout {
  const a = typeof arrangement === 'string' ? { name: arrangement } : arrangement;
  const A = ARR[a.name];
  const mem = saved?.[a.name] ?? {};
  const hand = mem.fold ?? {};
  const sw = mem.w ?? {};
  if (width <= C.CARDS_AT) return { mode: 'cards', arr: a.name, width, sheets: [], acting: [`cards: the window is ${C.CARDS_AT} or under`] };

  // 1. which sheets are showing, and the width each one wants: saved or default, inside its floor and ceiling
  const ids = A.row.filter(id =>
    id === 'panel' ? 'panel' in a && !!a.panel : id === 'chat' && a.name === 'chat' ? !('chat' in a) || a.chat !== false : true,
  );
  const S = {} as Record<SheetId, SheetLayout>;
  ids.forEach((id, i) => {
    const z = ids.length - i;
    if (id === A.stretch) {
      S[id] = { id, state: 'open', why: '', width: 0, z, depth: 0 };
      return;
    }
    const sp = specOf(a.name, id);
    const want = id === 'tico' ? saved?.tico : sw[id];
    const w = clamp(want == null ? sp.def : want, sp.min, sp.max);
    S[id] = { id, state: 'open', why: '', width: w, z, depth: 0, wanted: w, def: sp.def, min: sp.min, max: sp.max, canFold: !!sp.fold };
  });
  const fixed = ids.filter(id => id !== A.stretch);
  const F = forward && S[forward] ? forward : null;
  const fi = F ? ids.indexOf(F) : 0;
  const open = (id: SheetId) => S[id].state === 'open';
  const set = (id: SheetId, state: SheetState, why: Why) => {
    S[id].state = state;
    S[id].why = why;
  };
  /* everything but the stretching sheet's column: margins, open columns, edges, and a gap between two open sheets */
  const used = () =>
    ids.reduce(
      (n, id, i) => n + (open(id) ? (id === A.stretch ? 0 : S[id].width) : C.EDGE) + (open(id) && ids[i + 1] && open(ids[i + 1]!) ? C.GAP : 0),
      2 * C.MARGIN,
    );
  const room = () => width - used(); // the stretching sheet's column, or, tucked, what the open sheets share
  const acting: string[] = [];

  // 2. tucks the person chose, then the defaults for the ones they have not touched; a sheet opened by its edge stays
  // open, and the sheets above it collapse to edges
  for (const id of fixed) if (S[id].canFold && hand[id] === true && id !== F) set(id, 'fold', 'hand');
  for (const id of A.foldByDefault ?? []) {
    if (hand[id] == null && id !== F) {
      set(id, 'fold', 'default');
      acting.push(`${id} tucked: the default here`);
    }
  }
  if (F) {
    // a page's panel shares the page's layer: the page is not above it
    for (const id of ids.slice(0, fi)) if (open(id) && !(F === 'panel' && id === 'page')) set(id, 'tucked', 'forward');
    if (fi > 0) acting.push(`${F} opened: the sheets above it collapsed`);
  }

  // 3. no room: sheets made wider than their default give the extra back, right to left; then the right sheet slides
  // under its left neighbour, one at a time, until no more are open than the window keeps and the stretching sheet,
  // open, keeps its floor (open sheets alone, they fit)
  const short = () => (open(A.stretch) ? room() < C.FLOOR : room() < 0);
  // the window counts layers: a page's panel is in the page's layer
  const tooMany = () => ids.filter(id => open(id) && id !== 'panel').length > openCount(width);
  if (short()) {
    for (const id of fixed.slice().reverse()) {
      const s = S[id];
      const need = C.FLOOR - room();
      if (need > 0 && open(id) && s.width > (s.def ?? 0)) {
        s.width = Math.max(s.def ?? 0, s.width - need);
        s.reduced = true;
        acting.push(`${id} at ${s.width}, saved ${s.wanted}: no room`);
      }
    }
  }
  // the sheet the address opens leads (unless an edge was clicked, a second chat is open, or it was tucked by hand):
  // the sheets below it go first, right to left, then the ones above it, Tico first
  const L = !F && A.lead && S[A.lead] && open(A.lead) && !(a.name === 'chat' && ids.includes('chat')) ? A.lead : null;
  const li = L ? ids.indexOf(L) : -1;
  const order = L ? [...ids.slice(li + 1).reverse(), ...ids.slice(0, li)] : ids.slice().reverse();
  for (const id of order) {
    if (!tooMany() && !short()) break;
    if (id === F || !open(id)) continue;
    set(id, 'tucked', 'room');
    acting.push(`${id} under its left neighbour: ${tooMany() ? `the window keeps ${openCount(width)} open` : 'no room'}`);
  }

  // 4. widths. Open, the stretching sheet takes what is left; tucked, the open sheets share it by their widths.
  const spare = room();
  const stretchW = open(A.stretch) ? spare : 0;
  if (open(A.stretch)) S[A.stretch].width = stretchW;
  else if (spare > 0) {
    const wide = fixed.filter(open);
    const total = wide.reduce((n, id) => n + S[id].width, 0);
    let given = 0;
    wide.forEach((id, i) => {
      const add = i === wide.length - 1 ? spare - given : Math.floor((spare * S[id].width) / total);
      S[id].width += add;
      given += add;
    });
  }

  // 5. place them, left to right. A tucked sheet's card keeps its width and reaches back under its left neighbour (never
  // past that card's own left); an open sheet after an edge starts C.UNDER under it.
  let x: number = C.MARGIN;
  let prev: SheetLayout | null = null;
  for (const [i, id] of ids.entries()) {
    const s = S[id];
    const vl = x;
    const vis = open(id) ? s.width : C.EDGE;
    const vr = vl + vis;
    x = vr + (open(id) && ids[i + 1] && open(ids[i + 1]!) ? C.GAP : 0);
    const left = !prev ? vl : open(id) ? (open(prev.id) ? vl : vl - C.UNDER) : Math.max(C.MARGIN, vr - (s.wanted ?? C.FLOOR), prev.card?.x ?? C.MARGIN);
    s.x = vl;
    s.width = vis;
    s.card = { x: left, width: vr - left };
    const prevRight = prev?.card ? prev.card.x + prev.card.width : 0;
    s.depth = prev && left < prevRight - 0.5 ? prev.depth + 1 : 0;
    if (prev && left < prevRight - 0.5) prev.cast = true;
    prev = s;
  }
  const stretchOpen = open(A.stretch);
  for (const id of fixed) {
    const s = S[id];
    // how far its edge may be dragged right now: its ceiling, or the point where the stretching sheet reaches its floor
    const min = s.min ?? 0;
    const max = s.max ?? 0;
    s.canDrag = open(id) && stretchOpen;
    s.dragMax = s.canDrag ? Math.max(min, Math.min(max, s.width + stretchW - C.FLOOR)) : max;
  }
  return { mode: 'sheets', arr: a.name, width, stretch: { id: A.stretch, width: stretchW }, sheets: ids.map(id => S[id]), acting };
}

/* Cards or sheets, with hysteresis: `was` is the mode before this resize (leave it out on first load). */
export const modeFor = (width: number, was?: 'cards' | 'sheets' | null): 'cards' | 'sheets' =>
  width <= C.CARDS_AT ? 'cards' : was === 'cards' && width < C.SHEETS_AT ? 'cards' : 'sheets';

/* `forward` only while it is tucked without it: once the window has room for it, the usual layout is back. */
export function forwardFor(width: number, arrangement: ArrangementSpec, saved: Saved | null | undefined, forward: SheetId | null | undefined): SheetId | null {
  if (!forward) return null;
  const L = layout(width, arrangement, saved);
  return L.sheets.some(s => s.id === forward && s.state !== 'open') ? forward : null;
}

/* Pressing a sheet's fold control or its edge. Pure: returns { saved, forward }.
   Open -> tuck it by hand and save. Tucked -> open it and save, but only if it then stands in the row and nothing else
   has to tuck; if not, save nothing and open it by its edge for now: the sheets above it collapse. */
export function press(
  width: number,
  arrangement: ArrangementSpec,
  saved: Saved,
  id: SheetId,
  forward: SheetId | null = null,
): { saved: Saved; forward: SheetId | null } {
  const name = nameOf(arrangement);
  const now = layout(width, arrangement, saved, forward);
  const cur = now.sheets.find(s => s.id === id);
  const withFold = (v: boolean): Saved => {
    const n: Saved = structuredClone(saved ?? {});
    n.v = 1;
    const m = n[name] ?? {};
    n[name] = { ...m, fold: { ...m.fold, [id]: v } };
    return n;
  };
  if (!cur) return { saved, forward };
  if (cur.state === 'open') return { saved: cur.canFold ? withFold(true) : saved, forward: forward === id ? null : forward };
  const next = cur.canFold ? withFold(false) : saved;
  const then = layout(width, arrangement, next);
  const same = then.sheets.every(s => (s.id === id ? s.state === 'open' : s.state === now.sheets.find(o => o.id === s.id)?.state));
  return same ? { saved: next, forward: null } : { saved, forward: id };
}
