import { notifyManager } from '@tanstack/react-query';

// Events wait here and are applied together once per animation frame, inside one notify batch, so a burst of
// changes is one cache write and one redraw, not one per event. A hidden tab gets no frames: its events wait and
// are applied when it is shown again.
type Sink = (channel: string, batch: unknown[]) => void;

const queue = new Map<string, unknown[]>();
let sink: Sink = () => {};
let handle: number | null = null;

const raf: (fn: () => void) => number =
  typeof requestAnimationFrame === 'function' ? (fn) => requestAnimationFrame(fn) : (fn) => Number(setTimeout(fn, 16));
const caf: (h: number) => void =
  typeof cancelAnimationFrame === 'function' ? (h) => cancelAnimationFrame(h) : (h) => clearTimeout(h);

export function setSink(next: Sink): void {
  sink = next;
}

export function push(channel: string, data: unknown): void {
  const held = queue.get(channel);
  if (held) held.push(data);
  else queue.set(channel, [data]);
  if (handle == null) handle = raf(flushFrame);
}

/** Apply everything queued now (the frame callback; tests call it to stand in for a frame). */
export function flushFrame(): void {
  if (handle != null) {
    caf(handle);
    handle = null;
  }
  if (!queue.size) return;
  const batches = [...queue];
  queue.clear();
  notifyManager.batch(() => {
    for (const [channel, batch] of batches) sink(channel, batch);
  });
}

/** Forget what is queued (a reset reads everything again; sign-out). */
export function dropFrame(): void {
  if (handle != null) caf(handle);
  handle = null;
  queue.clear();
}
