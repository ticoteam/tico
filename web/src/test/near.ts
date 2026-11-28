import { act } from '@testing-library/react';
import { vi } from 'vitest';

/* jsdom has no IntersectionObserver. `watchNear()` puts in one that only reports when told: `nearEnd()` says every
   element it watches has come near the screen, as scrolling to a list's end does. `vi.unstubAllGlobals()` takes it out. */
type Watch = { cb: IntersectionObserverCallback; io: IntersectionObserver; els: Set<Element>; init?: IntersectionObserverInit };
const watches = new Set<Watch>();

export function watchNear(): { watched: () => Element[]; inits: () => (IntersectionObserverInit | undefined)[] } {
  watches.clear();
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      readonly w: Watch;
      constructor(cb: IntersectionObserverCallback, init?: IntersectionObserverInit) {
        this.w = { cb, io: this as unknown as IntersectionObserver, els: new Set(), init };
        watches.add(this.w);
      }
      observe(el: Element) {
        this.w.els.add(el);
      }
      unobserve(el: Element) {
        this.w.els.delete(el);
      }
      disconnect() {
        watches.delete(this.w);
      }
      takeRecords() {
        return [];
      }
    },
  );
  return {
    watched: () => [...watches].flatMap((w) => [...w.els]),
    inits: () => [...watches].map((w) => w.init),
  };
}

/** Every watched element comes near the screen. */
export async function nearEnd(): Promise<void> {
  await act(async () => {
    for (const w of [...watches]) {
      const entries = [...w.els].map((target) => ({ target, isIntersecting: true, intersectionRatio: 1 }) as unknown as IntersectionObserverEntry);
      if (entries.length) w.cb(entries, w.io);
    }
  });
}
