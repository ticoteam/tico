import { registry } from './registry';
import { ctxFor, run } from './run';
import type { AnyAction, Ctx, Result } from './types';

/* One shortcut table, built from each action's `keys`. Combos: 'mod+k' (⌘ on macOS, Ctrl elsewhere), '[' or a
   two-step 'g t'. Single keys never fire while typing in a field; combos with mod do (⌘K from the composer). */

export interface Shortcut {
  combo: string;
  id: string;
  title: string;
  /** The input, or a function reading the screen when the key is pressed. */
  input: unknown;
}

const SEQUENCE_MS = 1000;
// Keys browsers keep for themselves (platforms.md 3). In Electron ⌘1-9 switch companies.
const RESERVED = /^mod\+([twnlrq1-9])$|^ctrl\+tab$|^f5$/;

export function shortcutsOf(actions: ReadonlyMap<string, AnyAction>): Shortcut[] {
  const out: Shortcut[] = [];
  const seen = new Map<string, string>();
  for (const a of actions.values()) {
    for (const [raw, input] of Object.entries(a.keys ?? {})) {
      const combo = normal(raw);
      if (combo.split(' ').some((step) => RESERVED.test(step))) throw new Error(`Shortcut "${raw}" on ${a.id} is kept by the browser`);
      const was = seen.get(combo);
      if (was) throw new Error(`Shortcut "${raw}" is on both ${was} and ${a.id}`);
      seen.set(combo, a.id);
      out.push({ combo, id: a.id, title: a.title, input });
    }
  }
  return out;
}

/** Every shortcut, for a help list or the search rows' hints. */
export const shortcuts = (): Shortcut[] => shortcutsOf(registry);

function normal(combo: string): string {
  return combo
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .map((step) => {
      const parts = step.split('+');
      const key = parts.pop() ?? '';
      const mods = ['mod', 'ctrl', 'alt', 'shift'].filter((m) => parts.includes(m));
      return [...mods, key].join('+');
    })
    .join(' ');
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement &&
  (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable || !!t.closest('[contenteditable]:not([contenteditable="false"])'));

/** The step a key press spells, in the same form as `normal`. */
export function stepOf(e: KeyboardEvent, mac: boolean): string {
  const key = e.key.toLowerCase();
  const letter = /^[a-z0-9]$/.test(key);
  const mods: string[] = [];
  if (mac ? e.metaKey : e.ctrlKey) mods.push('mod');
  if (mac && e.ctrlKey) mods.push('ctrl');
  if (e.altKey) mods.push('alt');
  // '?' and '[' already carry their shift; only letters and named keys spell it out.
  if (e.shiftKey && (letter || key.length > 1)) mods.push('shift');
  return [...mods, key].join('+');
}

/** A keydown handler over a table. Returns the shortcut it fired, for tests. */
export function keyHandler(
  table: Shortcut[],
  fire: (s: Shortcut) => void,
  mac: boolean,
): (e: KeyboardEvent) => Shortcut | null {
  let first: { step: string; at: number } | null = null;
  return (e) => {
    if (e.defaultPrevented || e.isComposing || e.repeat) return null;
    if (['Meta', 'Control', 'Alt', 'Shift'].includes(e.key)) return null;
    const step = stepOf(e, mac);
    const inField = typing(e.target);
    const prior = first && e.timeStamp - first.at <= SEQUENCE_MS ? first.step : null;
    first = null;
    const hit =
      (prior && table.find((s) => s.combo === `${prior} ${step}`)) || table.find((s) => s.combo === step) || null;
    if (hit) {
      if (inField && !hit.combo.startsWith('mod+')) return null;
      e.preventDefault();
      fire(hit);
      return hit;
    }
    if (!inField && table.some((s) => s.combo.startsWith(`${step} `))) first = { step, at: e.timeStamp };
    return null;
  };
}

const isMac = () => {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  return /mac|iphone|ipad/i.test(nav.userAgentData?.platform ?? nav.platform ?? '');
};

/** The one key listener at the root. Returns how to remove it. */
export function installKeys(onResult?: (r: Result, s: Shortcut) => void): () => void {
  const handler = keyHandler(
    shortcuts(),
    (s) => {
      const ctx = ctxFor('key');
      const input = typeof s.input === 'function' ? (ctx ? (s.input as (c: Ctx) => unknown)(ctx) : null) : s.input;
      if (input === null) return;
      void run(s.id, input, { via: 'key' }).then((r) => onResult?.(r, s));
    },
    isMac(),
  );
  const listener = (e: KeyboardEvent) => void handler(e);
  window.addEventListener('keydown', listener);
  return () => window.removeEventListener('keydown', listener);
}
