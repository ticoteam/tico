import { api } from '@/api/client';
import { newId } from '@/lib/ids';
import { onTopic } from '@/live';
import { run, show } from './run';
import type { Result } from './types';
import { isScreenEvent, RESULT_TEXT_MAX, resultPath, SCREEN_TOPIC, type ScreenEvent, type ScreenResult } from './wire';

/* Tico moves the person's screen (architecture.md 5 and 12.3). The server sends each request to every open stream of
   the person's; the most recently focused visible tab acts, agreed over a BroadcastChannel. Nobody visible: nothing
   happens and the server tells Tico the app did not answer. */

const CHANNEL = 'tico-next:tab';
const REMEMBER = 200;

type Channel = Pick<BroadcastChannel, 'postMessage' | 'close'> & {
  onmessage: ((e: MessageEvent) => void) | null;
};

export interface ScreenDeps {
  tab: string;
  channel: Channel | null;
  /** Visible and focused right now. */
  inFront: () => boolean;
  visible: () => boolean;
  act: (e: ScreenEvent) => Promise<Result>;
  post: (id: string, r: ScreenResult) => Promise<unknown>;
}

export function screenHandler(d: ScreenDeps) {
  let front: string | null = null;
  const handled = new Set<string>();
  let queue: Promise<unknown> = Promise.resolve();

  const claim = () => {
    front = d.tab;
    d.channel?.postMessage({ front: d.tab });
  };
  if (d.channel) {
    d.channel.onmessage = (m: MessageEvent) => {
      const f = (m.data as { front?: unknown } | null)?.front;
      if (typeof f === 'string') front = f;
    };
  }
  if (d.inFront()) claim();

  /** Resolves once this event is acted on (or skipped) and its result posted. */
  const handle = (raw: unknown): Promise<void> => {
    if (!isScreenEvent(raw) || handled.has(raw.id)) return Promise.resolve();
    const e = raw;
    if (front !== d.tab || !d.visible()) return Promise.resolve();
    handled.add(e.id);
    if (handled.size > REMEMBER) handled.delete(handled.values().next().value as string);
    // One at a time, in arrival order, so two navigations never interleave.
    const next = queue.then(async () => {
      const r: Result = await d.act(e).catch((err: unknown) => ({ ok: false, error: `failed: ${String(err)}` }));
      const cut = (t: string) => t.slice(0, RESULT_TEXT_MAX);
      await d.post(e.id, { ok: r.ok, ...(r.error && { error: cut(r.error) }), ...(r.did && { did: cut(r.did) }) }).catch(() => {});
    });
    queue = next;
    return next;
  };

  return { handle, claim, dispose: () => d.channel?.close() };
}

/** `done`: Tico already changed it, so only show it. Otherwise run it as Tico. */
export const actOn = (e: ScreenEvent): Promise<Result> =>
  e.done ? show(e.action, e.input) : run(e.action, e.input, { via: 'tico' });

const postResult = (id: string, r: ScreenResult) => api.post(resultPath(id), r);

/** Subscribe this tab to the `screen` topic. Returns how to stop. */
export function startScreen(): () => void {
  const visible = () => document.visibilityState === 'visible';
  const h = screenHandler({
    // This document's own id, not the tab id kept in sessionStorage: a duplicated tab copies that one, and two
    // windows holding one id would both take the same claim and run a request twice.
    tab: newId(),
    channel: typeof BroadcastChannel === 'function' ? new BroadcastChannel(CHANNEL) : null,
    inFront: () => visible() && document.hasFocus(),
    visible,
    act: actOn,
    post: postResult,
  });
  const onFront = () => {
    if (visible() && document.hasFocus()) h.claim();
  };
  window.addEventListener('focus', onFront);
  document.addEventListener('visibilitychange', onFront);
  const off = onTopic<unknown>(SCREEN_TOPIC, (batch) => {
    for (const e of batch) void h.handle(e);
  });
  return () => {
    off();
    window.removeEventListener('focus', onFront);
    document.removeEventListener('visibilitychange', onFront);
    h.dispose();
  };
}
