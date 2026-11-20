import type { AnyRouter } from '@tanstack/react-router';
import { useRouter } from '@tanstack/react-router';
import { useState } from 'react';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data';
import { flight, waitForTarget, wash } from '@/motion';
import { bound } from './bind';
import { registry } from './registry';
import type { AnyAction, Ctx, Result, Target, Via } from './types';

/* After Tico navigates, the target may not exist yet; motion.md 5, case 5: wait up to 1500ms, then give up. */
const WAIT_MS = 1500;

/** The context an input function reads (a shortcut's "the open task"), or null before the app has started. */
export function ctxFor(via: Via): Ctx | null {
  return bound.router && { router: bound.router, queryClient, via };
}

/** The single dispatch for a click, a shortcut, search and Tico. Never throws. */
export function run(id: string, input: unknown, opts: { via: Via }): Promise<Result> {
  return runIn(registry, id, input, opts.via, bound.router);
}

export async function runIn(
  actions: ReadonlyMap<string, AnyAction>,
  id: string,
  raw: unknown,
  via: Via,
  router: AnyRouter | null,
): Promise<Result> {
  const a = actions.get(id);
  if (!a) return { ok: false, error: `unknown_action: ${id}` };
  if (via === 'tico' && a.tico === false) return { ok: false, error: 'not_allowed' };
  const parsed = a.input.parse(raw);
  if (!parsed.ok) return { ok: false, error: `bad_input: ${parsed.error}` };
  if (!router) return { ok: false, error: 'not_ready: the app is still starting' };
  const input = parsed.value;
  const ctx: Ctx = { router, queryClient, via };
  if (!allowed(a, ctx, input)) return { ok: false, error: 'not_allowed' };
  let out: unknown;
  try {
    out = await a.run(ctx, input);
  } catch (e) {
    return refused(e);
  }
  // The change is made: from here nothing may turn it into a failure. Reads never get here (they are not actions),
  // and only Tico's hand flies the Dot.
  const did = didOf(a, input, out);
  if (via === 'tico') await land(() => a.target?.(input, out), a.kind === 'view', did);
  const result: Result = { ok: true, did };
  // The output stays on this screen: Tico's runs never carry it (a token or code must not reach the conversation).
  if (via !== 'tico' && out !== undefined) result.out = out;
  return result;
}

/** Tico already made the change (`done`): go where it shows, fly, and do not write again. */
export function show(id: string, input: unknown): Promise<Result> {
  return showIn(registry, id, input, bound.router);
}

export async function showIn(
  actions: ReadonlyMap<string, AnyAction>,
  id: string,
  raw: unknown,
  router: AnyRouter | null,
): Promise<Result> {
  const a = actions.get(id);
  if (!a) return { ok: false, error: `unknown_action: ${id}` };
  if (a.tico === false) return { ok: false, error: 'not_allowed' };
  const parsed = a.input.parse(raw);
  if (!parsed.ok) return { ok: false, error: `bad_input: ${parsed.error}` };
  if (!router) return { ok: false, error: 'not_ready: the app is still starting' };
  try {
    await a.show?.({ router, queryClient, via: 'tico' }, parsed.value);
  } catch (e) {
    return refused(e);
  }
  const did = didOf(a, parsed.value, undefined);
  await land(() => a.target?.(parsed.value, undefined), true, did);
  return { ok: true, did };
}

/** The result's words; a throw there falls back to the action's title. */
function didOf(a: AnyAction, input: unknown, out: unknown): string {
  try {
    return a.did?.(input, out) ?? a.title;
  } catch {
    return a.title;
  }
}

function allowed(a: AnyAction, ctx: Ctx, input: unknown): boolean {
  if (!a.can) return true;
  try {
    return a.can(ctx, input);
  } catch {
    return false;
  }
}

/** A thrown error as a refused result: its code, and the server's reason when it gave a safe one. */
export function refused(e: unknown): Result {
  const why = whyOf(e);
  const told = (e as { told?: unknown } | null)?.told === true;
  return { ok: false, error: problem(e), ...(why && { why }), ...(told && { told }) };
}

/** Mark a refusal the action has said itself (its toast), so the button that ran it does not say it again. */
export function toldOf<E>(e: E): E {
  if (typeof e === 'object' && e !== null) Object.assign(e, { told: true });
  return e;
}

const MAX_WHY = 140;
// What a toast never shows: JSON, a stack or a traceback, an address.
const UNSAFE = /^\s*[[{]|"\s*:|\n\s*at \S|Traceback \(|https?:|www\.|\/\/|\/api\//i;

/** The server's own reason for a refusal (a Problem's `detail`), as one short sentence; undefined when it gave none, or
    when it reads like data rather than words. A thrown error that is not the server's says nothing here. */
export function whyOf(e: unknown): string | undefined {
  if (!(e instanceof ApiError) || e.status < 400 || e.code.startsWith('http_')) return undefined;
  return sayable(e.message);
}

/** Words fit for a toast's second part: the first sentence, cut short; undefined when empty or when it reads like data. */
export function sayable(words: string): string | undefined {
  if (UNSAFE.test(words)) return undefined;
  const text = words.replace(/\s+/g, ' ').trim();
  if (!text) return undefined;
  const first = /^.+?[.!?](?=\s|$)/.exec(text)?.[0] ?? text;
  const space = first.lastIndexOf(' ', MAX_WHY - 1);
  const cut = first.length <= MAX_WHY ? first : `${first.slice(0, space > 0 ? space : MAX_WHY - 1).trimEnd()}…`;
  return cut.charAt(0).toUpperCase() + cut.slice(1);
}

function problem(e: unknown): string {
  const status = (e as { status?: unknown } | null)?.status;
  if (status === 401 || status === 403) return 'not_allowed';
  const code = (e as { code?: unknown } | null)?.code;
  if (typeof code === 'string' && code) return code;
  return `failed: ${e instanceof Error ? e.message : String(e)}`;
}

/* ---------- the flight after a Tico run (motion.md 5) ---------- */

/* Motion's findTarget resolves off-screen, folded and undrawn targets; `show` (a view action) jumps it into view.
   A target that throws or cannot be found only means no flight. */
async function land(target: () => Target | null | undefined, show: boolean, label: string): Promise<void> {
  try {
    const t = target();
    if (!t) return;
    const el = await waitForTarget(t.kind, t.id, WAIT_MS, { show, label });
    if (!el) return; // cases 5 and 6: nothing to land on; the chat line stands alone
    const from = bound.dot();
    // The flight resolves at landing; the result does not wait for it.
    if (from) flight(from, el, { kind: show ? 'open' : 'edit', label }).catch(() => {});
    else wash(el);
  } catch {
    // the flight just does not happen
  }
}

/* ---------- buttons ---------- */

/** For a button: `disabled` when `can` says no (given an input) or while it runs. */
export function useAction(id: string, input?: unknown) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const a = registry.get(id);
  let can = !!a;
  if (a && input !== undefined) {
    const parsed = a.input.parse(input);
    can = parsed.ok && allowed(a, { router, queryClient, via: 'click' }, parsed.value);
  }
  const press = async (i: unknown = input, via: Via = 'click'): Promise<Result> => {
    setPending(true);
    try {
      return await runIn(registry, id, i, via, router);
    } finally {
      setPending(false);
    }
  };
  return { run: press, pending, can, disabled: !can || pending };
}
