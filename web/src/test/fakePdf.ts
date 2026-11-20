import { vi } from 'vitest';

/** A PDF of `n` letter pages, as PDF.js answers `openPdf`: each page says it drew into the canvas it was given. */
export function fakePdf(n: number) {
  const drawn: number[] = [];
  const destroy = vi.fn(() => Promise.resolve());
  const doc = {
    numPages: n,
    getPage: (p: number) =>
      Promise.resolve({
        getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale }),
        render: () => {
          drawn.push(p);
          return { promise: Promise.resolve(), cancel: () => undefined };
        },
      }),
  };
  return { open: { doc: Promise.resolve(doc), destroy } as never, drawn, destroy };
}
