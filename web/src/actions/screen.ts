import { api } from '@/api/client';
import { onTopic } from '@/live';
import { run, show } from './run';
import type { Result } from './types';
import { isScreenEvent, resultPath, SCREEN_TOPIC, type ScreenEvent, type ScreenResult } from './wire';

/* Tico moves the person's screen (architecture.md 5 and 12.3). The tab that sent the message acts; for a background
   turn, the most recently focused visible tab does, agreed over a BroadcastChannel. Nobody visible: nothing happens and
   the server answers Tico `{sent: true}` on its own. */

const TAB_KEY = 'tico-next:tab';
const CHANNEL = 'tico-next:tab';
const REMEMBER = 200;

let memoryTab: string | null = null;

/** This tab's id: a uuid kept in sessionStorage, so a reload keeps it and another tab has its own. */
export function tabId(): string {
  try {
    const kept = sessionStorage.getItem(TAB_KEY);
    if (kept) return kept;
    const id = crypto.randomUUID();
    sessionStorage.setItem(TAB_KEY, id);
    return id;
  } catch {
    memoryTab ??= crypto.randomUUID();
    return memoryTab;
  }
}

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
    if (e.client ? e.client !== d.tab : front !== d.tab || !d.visible()) return Promise.resolve();
    handled.add(e.id);
    if (handled.size > REMEMBER) handled.delete(handled.values().next().value as string);
    // One at a time, in arrival order, so two navigations never interleave.
    const next = queue.then(async () => {
      const r: Result = await d.act(e).catch((err: unknown) => ({ ok: false, error: `failed: ${String(err)}` }));
      await d.post(e.id, { ok: r.ok, ...(r.error && { error: r.error }), ...(r.did && { did: r.did }) }).catch(() => {});
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
    tab: tabId(),
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
