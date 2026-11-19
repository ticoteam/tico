/**
 * Reads `read` now and again every `ms` while `more(answer)` holds, handing each answer to `seen`; returns the stop.
 * For answers that must stay out of the query cache (a sign-in's one-time code and page): the caller keeps them in its
 * component state. A failed read is tried again at the next tick.
 */
export function poll<T>(read: (signal: AbortSignal) => Promise<T>, seen: (answer: T) => void, more: (answer: T) => boolean, ms: number): () => void {
  const ctl = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = async () => {
    let again = true;
    try {
      const answer = await read(ctl.signal);
      if (ctl.signal.aborted) return;
      seen(answer);
      again = more(answer);
    } catch {
      if (ctl.signal.aborted) return;
    }
    if (again) timer = setTimeout(() => void tick(), ms);
  };
  void tick();
  return () => {
    ctl.abort();
    clearTimeout(timer);
  };
}
