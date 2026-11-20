import { readPref, removePref, usePref, writePref } from '@/prefs';
import { ARR, foldOf, widthOf, withFold, type Arrangement, type Memory, type Saved, type SheetId } from './layout';

export { withFold };

/* Sheet sizes and hand folds, on this device (layout.md 6): one key, sparse. Tico's and the menu's width and fold are
   one value each, the same on every arrangement (layout.ts `shared`). Automatic folds never come here: layout()
   decides them from the window on every render. */
const KEY = 'sheets.v1';
const V = 1;

/* Bad JSON, a wrong type or an older version reads as an empty memory. Widths are clamped by layout(), not here. */
export function clean(raw: unknown): Saved {
  if (!raw || typeof raw !== 'object' || (raw as Saved).v !== V) return { v: V };
  const r = raw as Record<string, unknown>;
  const out: Saved = { v: V };
  if (typeof r.tico === 'number' && Number.isFinite(r.tico)) out.tico = r.tico;
  if (typeof r.menu === 'number' && Number.isFinite(r.menu)) out.menu = r.menu;
  for (const [id, f] of Object.entries((r.fold as Saved['fold']) ?? {})) if (typeof f === 'boolean') (out.fold ??= {})[id as SheetId] = f;
  for (const arr of Object.keys(ARR) as Arrangement[]) {
    const m = r[arr] as Memory | undefined;
    if (!m || typeof m !== 'object') continue;
    const mem: Memory = {};
    for (const [id, f] of Object.entries(m.fold ?? {})) if (typeof f === 'boolean') (mem.fold ??= {})[id as SheetId] = f;
    for (const [id, w] of Object.entries(m.w ?? {})) if (typeof w === 'number' && Number.isFinite(w)) (mem.w ??= {})[id as SheetId] = w;
    out[arr] = mem;
  }
  return out;
}
/* a newer build wrote it: not read, not overwritten */
const newer = (raw: unknown) => !!raw && typeof raw === 'object' && typeof (raw as Saved).v === 'number' && ((raw as Saved).v ?? 0) > V;

export function withWidth(s: Saved, arr: Arrangement, id: SheetId, w: number): Saved {
  if (id === 'tico' || id === 'menu') return { ...s, [id]: w };
  const m = s[arr] ?? {};
  return { ...s, [arr]: { ...m, w: { ...m.w, [id]: w } } };
}
export function withoutWidth(s: Saved, arr: Arrangement, id: SheetId): Saved {
  if (id === 'tico') {
    const { tico: _, ...rest } = s;
    return rest;
  }
  if (id === 'menu') {
    // the page's own menu width from before goes too, or it would be read again
    const { menu: _, page, ...rest } = s;
    const { menu: __, ...w } = page?.w ?? {};
    return page ? { ...rest, page: { ...page, w } } : rest;
  }
  const m = s[arr] ?? {};
  const { [id]: _, ...w } = m.w ?? {};
  return { ...s, [arr]: { ...m, w } };
}
/* After a drag, write only the one sheet's width and fold into what is stored now, so another window's change to
   another field is kept. */
export function keepOne(now: Saved, from: Saved, arr: Arrangement, id: SheetId): Saved {
  let out = now;
  const w = widthOf(from, arr, id);
  if (w != null) out = withWidth(out, arr, id, w);
  const f = foldOf(from, arr, id);
  if (f != null) out = withFold(out, arr, id, f);
  return out;
}

export function useSheetMemory(): [Saved, (change: (now: Saved) => Saved) => void] {
  const [raw] = usePref<unknown>(KEY, null);
  const update = (change: (now: Saved) => Saved) => {
    const cur = readPref<unknown>(KEY, null);
    if (!newer(cur)) writePref(KEY, { ...change(clean(cur)), v: V });
  };
  return [clean(raw), update];
}

/* "Reset sheet sizes" in Settings, General. Every open window follows at once. */
export function resetSheetSizes(): void {
  if (!newer(readPref<unknown>(KEY, null))) removePref(KEY);
}
