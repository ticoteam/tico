/* How the sheets size, fold and snap: a port of `Sheets.layout` in the design's tico/sheets-live.js
   (rules and sources in tico/build/layout.md). Pure: it reads only its arguments and never writes `saved`,
   which is what keeps automatic folds out of memory. Keep it line for line with the reference. */

export type Arrangement = 'page' | 'chat' | 'agent' | 'task' | 'call';
export type ArrangementSpec = Arrangement | { name: 'chat'; chat?: boolean; panel?: boolean };
export type SheetId = 'tico' | 'menu' | 'list' | 'chat' | 'page' | 'panel' | 'widgets' | 'stage' | 'side';
export type SheetState = 'open' | 'fold' | 'over' | 'tucked';
export type Why = 'hand' | 'default' | 'room' | '';

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
  width: number;
  x?: number;
  /* fixed sheets only */
  wanted?: number;
  def?: number;
  min?: number;
  max?: number;
  foldW?: number;
  reduced?: boolean;
  canDrag?: boolean;
  dragMax?: number;
}

export type Layout =
  | { mode: 'cards'; arr: Arrangement; width: number; sheets: []; acting: string[] }
  | { mode: 'sheets'; arr: Arrangement; width: number; stretch: { id: SheetId; width: number }; sheets: SheetLayout[]; acting: string[] };

export const C = {
  MARGIN: 8, // sheets to the window edge        tokens.css --gap-pane
  GAP: 8, // between sheets                   tokens.css --gap-pane
  FLOOR: 480, // the stretching sheet is never narrower
  COMFORT: 700, // a default fold lets go once the stretching sheet keeps this much with the sheet open
  CARDS_AT: 760, // at this width and under: the phone's cards
  SHEETS_AT: 800, // coming back from cards: sheets return at this width (hysteresis)
  SNAP: 80, // drag this far past a floor: the sheet folds
  UNSNAP: 40, // drag back to within this of the floor: it opens again
} as const;

interface Spec {
  def: number;
  min: number;
  max: number;
  /* its width folded: 56 a strip, 0 leaves the row, undefined = it can only close */
  fold?: number;
}
export const SPEC = {
  tico: { def: 300, min: 260, max: 480, fold: 56 },
  menu: { def: 248, min: 220, max: 320, fold: 56 },
  list: { def: 288, min: 240, max: 400, fold: 56 }, // a task's list, in the menu's place; folded it is the menu's icons
  chat: { def: 320, min: 280, max: 520 },
  taskchat: { def: 392, min: 360, max: 520 },
  widgets: { def: 360, min: 300, max: 480, fold: 0 },
  panel: { def: 344, min: 300, max: 480 },
  side: { def: 384, min: 300, max: 520, fold: 0 }, // a call's chat, transcript and people
} satisfies Record<string, Spec>;
type SpecId = keyof typeof SPEC;

interface Arr {
  row: SheetId[];
  stretch: SheetId;
  side?: SheetId;
  comfort?: SheetId;
  spec?: Partial<Record<SheetId, SpecId>>;
  foldByDefault?: SheetId[];
}
export const ARR: Record<Arrangement, Arr> = {
  page: { row: ['tico', 'menu', 'page'], stretch: 'page' },
  chat: { row: ['tico', 'menu', 'chat', 'page', 'panel'], stretch: 'page', side: 'chat', comfort: 'menu' },
  agent: { row: ['tico', 'menu', 'chat', 'widgets'], stretch: 'chat', side: 'widgets' },
  task: { row: ['tico', 'list', 'chat', 'page'], stretch: 'page', side: 'chat', comfort: 'tico', spec: { chat: 'taskchat' } },
  call: { row: ['tico', 'stage', 'side'], stretch: 'stage', side: 'side', foldByDefault: ['tico'] },
};

export const specOf = (arr: Arrangement, id: SheetId): Spec => SPEC[(ARR[arr].spec ?? {})[id] ?? (id as SpecId)];
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
export const nameOf = (a: ArrangementSpec): Arrangement => (typeof a === 'string' ? a : a.name);

export function layout(width: number, arrangement: ArrangementSpec, saved: Saved | null | undefined): Layout {
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
  for (const id of ids) {
    if (id === A.stretch) {
      S[id] = { id, state: 'open', why: '', width: 0 };
      continue;
    }
    const sp = specOf(a.name, id);
    const want = id === 'tico' ? saved?.tico : sw[id];
    const w = clamp(want == null ? sp.def : want, sp.min, sp.max);
    S[id] = { id, state: 'open', why: '', width: w, wanted: w, def: sp.def, min: sp.min, max: sp.max, foldW: sp.fold };
  }
  const fixed = ids.filter(id => id !== A.stretch);
  const inRow = (id: SheetId) => S[id].state === 'open' || (S[id].state === 'fold' && (S[id].foldW ?? 0) > 0);
  const wOf = (id: SheetId) => (S[id].state === 'open' ? S[id].width : (S[id].foldW ?? 0));
  const room = () => {
    // what is left for the stretching sheet
    const row = fixed.filter(inRow);
    return width - 2 * C.MARGIN - C.GAP * row.length - row.reduce((n, id) => n + wOf(id), 0);
  };
  const set = (id: SheetId, state: SheetState, why: Why) => {
    S[id].state = state;
    S[id].why = why;
  };
  const acting: string[] = [];

  // 2. folds the person chose, then the defaults for the ones they have not touched
  for (const id of fixed) if (S[id].foldW != null && hand[id] === true) set(id, 'fold', 'hand');
  for (const id of A.foldByDefault ?? []) {
    if (hand[id] == null) {
      set(id, 'fold', 'default');
      acting.push(`${id} folded: the default here`);
    }
  }
  if (A.comfort && hand[A.comfort] == null && room() < C.COMFORT) {
    set(A.comfort, 'fold', 'default');
    acting.push(`${A.comfort} folded: the default until ${A.stretch} keeps ${C.COMFORT} with it open`);
  }

  // 3. no room (the stretching sheet would be under its floor): give way in a set order, stop as soon as it fits
  const short = () => room() < C.FLOOR;
  if (short()) {
    // 3a. sheets made wider than their default give the extra back, right to left
    for (const id of fixed.slice().reverse()) {
      const s = S[id];
      const need = C.FLOOR - room();
      if (need > 0 && s.state === 'open' && s.width > (s.def ?? 0)) {
        s.width = Math.max(s.def ?? 0, s.width - need);
        s.reduced = true;
        acting.push(`${id} at ${s.width}, saved ${s.wanted}: no room`);
      }
    }
  }
  if (short() && S.panel) {
    set('panel', 'over', 'room'); // 3b
    acting.push('panel over the page: no room');
  }
  const second = ids[1];
  const slot2 = second !== undefined && second !== A.stretch ? second : null; // the menu, or a task's list
  if (short() && slot2 && S[slot2].state === 'open') {
    set(slot2, 'fold', 'room'); // 3c
    acting.push(`${slot2} folded: no room`);
  }
  if (short() && S.tico.state === 'open') {
    set('tico', 'fold', 'room'); // 3d
    acting.push('tico folded: no room');
  }
  if (short() && A.side && S[A.side] && S[A.side].state === 'open') {
    // 3e. a chat is shown over the page; widgets and a call's side wait behind a button
    const lays = A.side === 'chat';
    set(A.side, lays ? 'over' : 'tucked', 'room');
    acting.push(`${A.side}${lays ? ` over the ${A.stretch}` : ' tucked'}: no room`);
  }
  if (S.panel && S.panel.state === 'over' && S.chat && S.chat.state === 'over') {
    set('chat', 'tucked', 'room');
    acting.push('chat tucked: the panel is over the page');
  }

  // 4. place them. Only the stretching sheet takes what is left.
  const stretchW = room();
  let x: number = C.MARGIN;
  for (const id of ids) {
    const s = S[id];
    if (id === A.stretch) {
      s.width = stretchW;
      s.x = x;
      x += stretchW + C.GAP;
      continue;
    }
    if (inRow(id)) {
      s.x = x;
      s.width = wOf(id);
      x += s.width + C.GAP;
    }
  }
  const st = S[A.stretch];
  const stX = st.x ?? C.MARGIN;
  for (const id of fixed) {
    const s = S[id];
    const right = ids.indexOf(id) > ids.indexOf(A.stretch);
    if (!inRow(id)) {
      s.width = s.state === 'fold' ? 0 : Math.min(s.width, st.width - C.GAP);
      s.x = right || id === 'widgets' || id === 'side' ? stX + st.width - s.width : stX;
    }
    // how far its edge may be dragged right now: its ceiling, or the point where the stretching sheet reaches its floor
    const min = s.min ?? 0;
    const max = s.max ?? 0;
    s.canDrag = s.state === 'open';
    s.dragMax = s.state === 'open' ? Math.max(min, Math.min(max, s.width + stretchW - C.FLOOR)) : max;
  }
  return { mode: 'sheets', arr: a.name, width, stretch: { id: A.stretch, width: stretchW }, sheets: ids.map(id => S[id]), acting };
}

/* Cards or sheets, with hysteresis: `was` is the mode before this resize (leave it out on first load). */
export const modeFor = (width: number, was?: 'cards' | 'sheets' | null): 'cards' | 'sheets' =>
  width <= C.CARDS_AT ? 'cards' : was === 'cards' && width < C.SHEETS_AT ? 'cards' : 'sheets';

/* Pressing a sheet's fold control. Pure: returns { saved, peek }.
   Open in the row -> fold it and save. Folded -> open it and save, but only if it then stands in the row and nothing
   else has to give way; if not, save nothing and open it over its neighbours for now (peek). */
export function press(
  width: number,
  arrangement: ArrangementSpec,
  saved: Saved,
  id: SheetId,
): { saved: Saved; peek: SheetId | null } {
  const name = nameOf(arrangement);
  const now = layout(width, arrangement, saved);
  const cur = now.sheets.find(s => s.id === id);
  const withFold = (v: boolean): Saved => {
    const n: Saved = structuredClone(saved ?? {});
    n.v = 1;
    const m = n[name] ?? {};
    n[name] = { ...m, fold: { ...m.fold, [id]: v } };
    return n;
  };
  if (!cur || cur.state === 'open') return { saved: withFold(true), peek: null };
  const next = withFold(false);
  const then = layout(width, arrangement, next);
  const same = then.sheets.every(s =>
    s.id === id ? s.state === 'open' : s.state === now.sheets.find(o => o.id === s.id)?.state,
  );
  return same ? { saved: next, peek: null } : { saved, peek: id };
}
