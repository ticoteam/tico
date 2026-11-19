import { describe, expect, it } from 'vitest';

/* package.json `sideEffects` lets the build drop what a page does not use, so the first load carries the shell only.
   A file listed there is kept even when nothing uses it. A file whose only work is a bare stylesheet import (a barrel
   like conversation/index.ts) is skipped when it is not listed, and its styles go with it. Every such file must be
   listed. */

const pkg = import.meta.glob<{ sideEffects: string[] }>('/package.json', { eager: true, import: 'default' })['/package.json']!;
const sources = import.meta.glob<string>(['/src/**/*.{ts,tsx}', '!/src/**/*.test.{ts,tsx}'], {
  eager: true,
  query: '?raw',
  import: 'default',
});

describe('sideEffects', () => {
  it('lists every file that imports a stylesheet but draws nothing itself', () => {
    const missing = Object.entries(sources)
      .filter(([, src]) => {
        if (!/^import ['"][^'"]+\.css['"]/m.test(src)) return false;
        // a file that draws or does work keeps its stylesheet whenever it is used
        return !/^export (default )?(async )?(function|const|class|let)\b/m.test(src);
      })
      .map(([file]) => `.${file}`)
      .filter((f) => !pkg.sideEffects.includes(f));
    expect(missing).toEqual([]);
  });
});
