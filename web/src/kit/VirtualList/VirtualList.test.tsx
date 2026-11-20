import { act, render, screen, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { VirtualList } from './VirtualList';

// jsdom lays nothing out: give every element a 340 x 400 box so the list has a window to fill.
const box = { clientHeight: 340, clientWidth: 400 };
const saved = Object.keys(box).map((k) => [k, Object.getOwnPropertyDescriptor(Element.prototype, k)] as const);
beforeAll(() => {
  for (const [k, n] of Object.entries(box)) Object.defineProperty(Element.prototype, k, { configurable: true, get: () => n });
});
afterAll(() => {
  for (const [k, d] of saved) if (d) Object.defineProperty(Element.prototype, k, d);
});

// Read with node's fs (tests load no CSS, not even ?raw), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const css = fs.readFileSync(new URL('./VirtualList.css', here), 'utf8');

const items = Array.from({ length: 1000 }, (_, i) => ({ id: `t${i}`, title: `Task ${i}` }));

describe('VirtualList', () => {
  it('draws only the rows in view plus the overscan, keyed by id', () => {
    const onReachStart = vi.fn();
    render(
      <VirtualList
        items={items}
        getKey={(t) => t.id}
        estimateSize={34}
        label="Tasks"
        onReachStart={onReachStart}
        renderItem={(t) => <span>{t.title}</span>}
      />,
    );
    const rows = within(screen.getByRole('list', { name: 'Tasks' })).getAllByRole('listitem');
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(30);
    expect(rows[0]?.textContent).toBe('Task 0');
    expect(screen.queryByText('Task 999')).toBeNull();
    expect(onReachStart).toHaveBeenCalled();
  });
});

describe('a timeline', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('keeps its spacer whole in a column flex scroller (a chat body), so the scroller ends where the rows do', () => {
    expect(css).toMatch(/\.vl-space\{[^}]*flex:none/);
  });

  it('stays at its end when its window gets shorter or taller, and keeps its place when scrolled up', () => {
    // Every observer, with what it watches: a resize is told to the ones watching the scroller.
    const watching: { cb: ResizeObserverCallback; els: Element[] }[] = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        els: Element[] = [];
        constructor(public cb: ResizeObserverCallback) {
          watching.push(this);
        }
        observe(el: Element) {
          this.els.push(el);
        }
        unobserve() {}
        disconnect() {
          this.els = [];
        }
      },
    );
    render(<VirtualList items={items.slice(0, 50)} getKey={(t) => t.id} estimateSize={34} anchor="end" role="log" label="Chat" renderItem={(t) => <span>{t.title}</span>} />);
    const log = screen.getByRole('log', { name: 'Chat' });
    const END = 1700;
    let h = 340;
    let top = 0;
    Object.defineProperty(log, 'clientHeight', { configurable: true, get: () => h });
    Object.defineProperty(log, 'scrollHeight', { configurable: true, get: () => END });
    Object.defineProperty(log, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => (top = Math.max(0, Math.min(v, END - h))) });
    const resize = (to: number) => {
      h = to;
      top = Math.min(top, END - h);
      act(() => {
        for (const o of watching) if (o.els.includes(log)) o.cb([], o as unknown as ResizeObserver);
      });
    };

    top = END - 340;
    resize(300); // the asks over the box come back
    expect(top).toBe(END - 300);
    resize(340); // and go
    expect(top).toBe(END - 340);

    top = 1000;
    resize(260);
    expect(top).toBe(1000);
  });
});
