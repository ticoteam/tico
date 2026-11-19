import { readPref, usePref, writePref } from '@/prefs';
import { ARR, type Arrangement, type Memory, type Saved, type SheetId } from './layout';

/* Sheet sizes and hand folds, on this device (layout.md 6): one key, sparse, Tico's width one number.
   Automatic folds never come here: layout() decides them from the window on every render. */
const KEY = 'sheets.v1';
const V = 1;

/* Bad JSON, a wrong type or an older version reads as an empty memory. Widths are clamped by layout(), not here. */
export function clean(raw: unknown): Saved {
  if (!raw || typeof raw !== 'object' || (raw as Saved).v !== V) return { v: V };
  const r = raw as Record<string, unknown>;
  const out: Saved = { v: V };
  if (typeof r.tico === 'number' && Number.isFinite(r.tico)) out.tico = r.tico;
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
  if (id === 'tico') return { ...s, tico: w };
  const m = s[arr] ?? {};
  return { ...s, [arr]: { ...m, w: { ...m.w, [id]: w } } };
}
export function withoutWidth(s: Saved, arr: Arrangement, id: SheetId): Saved {
  if (id === 'tico') {
    const { tico: _, ...rest } = s;
    return rest;
  }
  const m = s[arr] ?? {};
  const { [id]: _, ...w } = m.w ?? {};
  return { ...s, [arr]: { ...m, w } };
}
export function withFold(s: Saved, arr: Arrangement, id: SheetId, folded: boolean): Saved {
  const m = s[arr] ?? {};
  return { ...s, [arr]: { ...m, fold: { ...m.fold, [id]: folded } } };
}
/* After a drag, write only the one sheet's width and fold into what is stored now, so another window's change to
   another field is kept. */
export function keepOne(now: Saved, from: Saved, arr: Arrangement, id: SheetId): Saved {
  let out = now;
  const w = id === 'tico' ? from.tico : from[arr]?.w?.[id];
  if (w != null) out = withWidth(out, arr, id, w);
  const f = from[arr]?.fold?.[id];
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
  if (!newer(readPref<unknown>(KEY, null))) writePref(KEY, { v: V });
}
