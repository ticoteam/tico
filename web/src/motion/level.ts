/* The one Motion setting (motion.md 7): More, Less, Off, on the root as `<html data-motion="more|less|off">`. The
   person's choice is a device pref (`motion.v1`); "system" follows prefers-reduced-motion, which means Off. A person
   can pick More with the system setting on. `?motion=more|less|off` overrides both, for tests and pictures. */
import { useEffect, useSyncExternalStore } from 'react';
import { readPref, usePref, writePref } from '@/prefs';

export type MotionLevel = 'more' | 'less' | 'off';
export type MotionChoice = 'system' | MotionLevel;

const KEY = 'motion.v1';
const RANK: Record<MotionLevel, number> = { more: 2, less: 1, off: 0 };
const isLevel = (v: unknown): v is MotionLevel => v === 'more' || v === 'less' || v === 'off';

let current: MotionLevel = 'more';
const subs = new Set<() => void>();

function reduceQuery(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)')
    : null;
}

function fromAddress(): MotionLevel | null {
  if (typeof location === 'undefined') return null;
  const v = new URLSearchParams(location.search).get('motion');
  return isLevel(v) ? v : null;
}

/** The level a choice comes to, given the system's reduced-motion setting. */
export function resolveLevel(choice: unknown, reduce: boolean): MotionLevel {
  if (isLevel(choice)) return choice;
  return reduce ? 'off' : 'more';
}

function apply(choice: unknown = readPref<MotionChoice>(KEY, 'system')): void {
  const next = fromAddress() ?? resolveLevel(choice, !!reduceQuery()?.matches);
  if (typeof document !== 'undefined') document.documentElement.dataset.motion = next;
  if (next === current) return;
  current = next;
  for (const fn of [...subs]) fn();
}

/** 'more' | 'less' | 'off', now. */
export const motionLevel = (): MotionLevel => current;

/** 2, 1 or 0: what each motion's minimum level is compared with (FLIGHT.minLevel). */
export const levelRank = (level: MotionLevel = current): number => RANK[level];

/** Saves the person's choice on this device and applies it at once. */
export function setMotionChoice(choice: MotionChoice): void {
  writePref(KEY, choice);
  apply(choice);
}

const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => {
    subs.delete(fn);
  };
};

/** The level as state, for a component that draws differently at Less or Off. */
export function useMotionLevel(): MotionLevel {
  return useSyncExternalStore(subscribe, motionLevel, motionLevel);
}

/** Mounted once at the app's root: keeps `data-motion` in step with the pref (this tab, other tabs, Settings). */
export function useMotionRoot(): void {
  const [choice] = usePref<MotionChoice>(KEY, 'system');
  useEffect(() => apply(choice), [choice]);
}

reduceQuery()?.addEventListener?.('change', () => apply());
apply();
