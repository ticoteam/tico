import { describe, expect, it } from 'vitest';
import { C, forwardFor, layout, modeFor, openCount, press, type ArrangementSpec, type Layout, type Saved, type SheetId } from './layout';

const line = (L: Layout) => (L.mode === 'cards' ? 'cards' : L.sheets.map(s => (s.state === 'open' ? String(s.width) : 'edge')).join(', '));
const sheet = (L: Layout, id: SheetId) => {
  const s = L.sheets.find(o => o.id === id);
  if (!s) throw new Error(`no ${id}`);
  return s;
};

const ARRS: [string, ArrangementSpec][] = [
  ['page', 'page'],
  ['chat', 'chat'],
  ['chat + panel', { name: 'chat', panel: true }],
  ['panel only', { name: 'chat', chat: false, panel: true }],
  ['agent', 'agent'],
  ['task', 'task'],
  ['call', 'call'],
];

// left to right: an open sheet's width, or its edge. With the stretching sheet under, the open sheets share the window.
const TABLE: Record<string, Record<number, string>> = {
  page: { 2560: '300, 248, 1980', 1440: '300, 248, 860', 1280: '300, 248, 700', 1080: '300, 248, 500', 1024: '523, 433, edge', 840: '422, 350, edge', 800: '696, edge, edge', 760: 'cards' },
  chat: { 2560: '300, 248, 320, 1652', 1440: '300, 248, 320, 532', 1360: '443, 366, 475, edge', 1080: '347, 286, 371, edge', 1024: '499, 413, edge, edge', 840: '398, 330, edge, edge', 800: '652, edge, edge, edge', 760: 'cards' },
  'chat + panel': { 2560: '300, 248, 320, 1300, 344', 1440: '300, 248, 320, 488, edge', 1280: '400, 331, 429, edge, edge', 1024: '475, 393, edge, edge, edge' },
  'panel only': { 1728: '300, 248, 796, 344', 1440: '300, 248, 508, 344', 1280: '300, 248, 656, edge', 1024: '499, 413, edge, edge' },
  agent: { 1920: '300, 248, 972, 360', 1440: '300, 248, 492, 360', 1360: '300, 248, 736, edge', 1100: '540, 448, edge, edge', 800: '652, edge, edge, edge' },
  task: { 1920: '300, 288, 392, 900', 1728: '300, 288, 392, 708', 1440: '417, 400, 547, edge', 1024: '465, 447, edge, edge' },
  call: { 1920: 'edge, 1468, 384', 1024: 'edge, 572, 384', 900: 'edge, 796, edge' },
};

// the window width under which each sheet goes under its left neighbour, in order, at default sizes: the page under
// the thread first, then the thread under the company, then the company under Tico
const TUCKS: Record<string, [SheetId, number][]> = {
  page: [['page', 1080], ['menu', 840]],
  chat: [['page', 1388], ['chat', 1080], ['menu', 840]],
  'chat + panel': [['panel', 1740], ['page', 1432], ['chat', 1080], ['menu', 840]],
  'panel only': [['panel', 1412], ['page', 1104], ['menu', 840]],
  agent: [['widgets', 1428], ['chat', 1104], ['menu', 840]],
  task: [['page', 1500], ['chat', 1080], ['list', 840]],
  call: [['side', 932]],
};

describe('layout', () => {
  it.each(ARRS)('gives the widths in the table: %s', (name, arr) => {
    for (const [w, want] of Object.entries(TABLE[name] ?? {})) expect(`${w}: ${line(layout(Number(w), arr, {}))}`).toBe(`${w}: ${want}`);
  });

  it.each(ARRS)('tucks right to left while narrowing, and only there: %s', (name, arr) => {
    const seen: [SheetId, number][] = [];
    let before = layout(2600, arr, {});
    for (let W = 2599; W > C.CARDS_AT; W--) {
      const now = layout(W, arr, {});
      for (const s of now.sheets) {
        const was = before.sheets.find(o => o.id === s.id);
        if (was && was.state === 'open' && s.state !== 'open') seen.push([s.id, W + 1]);
        if (was && was.state !== 'open' && s.state === 'open') throw new Error(`${s.id} opened again at ${W} while narrowing`);
      }
      if (now.mode === 'sheets' && now.sheets.find(s => s.id === now.stretch.id)?.state === 'open') expect(now.stretch.width).toBeGreaterThanOrEqual(C.FLOOR);
      expect(now.sheets.filter(s => s.state === 'open' && s.id !== 'panel').length).toBeLessThanOrEqual(openCount(W));
      before = now;
    }
    expect(seen).toEqual(TUCKS[name]);
  });

  it('opens 4, 3, 2 or 1 sheets by the window, as the plan does', () => {
    expect([1680, 1360, 1359, 1080, 1079, 840, 839].map(openCount)).toEqual([4, 4, 3, 3, 2, 2, 1]);
  });

  it('keeps the stacking order: the leftmost sheet is on top, whatever is tucked', () => {
    for (const [, arr] of ARRS) {
      for (const W of [2000, 1200, 900]) {
        for (const f of [null, 'page', 'chat', 'menu'] as const) {
          const L = layout(W, arr, {}, f);
          const z = L.sheets.map(s => s.z);
          expect(z).toEqual(z.slice().sort((x, y) => y - x));
        }
      }
    }
    expect(layout(1920, 'chat', {}).sheets.map(s => s.z)).toEqual([4, 3, 2, 1]);
  });

  it('fills the window with what shows, for the largest saved sizes', () => {
    const big: Saved = { tico: 480, page: { w: { menu: 320 } }, chat: { fold: { menu: false, tico: false }, w: { chat: 520, panel: 480, menu: 320 } }, agent: { w: { widgets: 480, menu: 320 } }, task: { fold: { tico: false }, w: { list: 400, chat: 520 } }, call: { fold: { tico: false }, w: { side: 520 } } };
    for (let W = 761; W <= 2600; W += 7) {
      for (const [, arr] of ARRS) {
        const L = layout(W, arr, big);
        if (L.mode !== 'sheets') throw new Error('cards');
        const last = L.sheets[L.sheets.length - 1]!;
        expect((last.x ?? 0) + last.width + C.MARGIN).toBe(W);
        expect(L.sheets[0]!.x).toBe(C.MARGIN);
        for (let i = 1; i < L.sheets.length; i++) {
          const a = L.sheets[i - 1]!;
          const b = L.sheets[i]!;
          // a gap only between two open sheets; an edge sits against its neighbour
          expect(b.x).toBe((a.x ?? 0) + a.width + (a.state === 'open' && b.state === 'open' ? C.GAP : 0));
        }
      }
    }
  });

  it('draws a tucked card under its left neighbour, one step shorter, with the neighbour casting on it', () => {
    const L = layout(1200, 'chat', {});
    const chat = sheet(L, 'chat');
    const page = sheet(L, 'page');
    expect(page).toMatchObject({ state: 'tucked', width: C.EDGE, depth: 1 });
    expect(page.card!.x).toBe(chat.x); // reaches back under the thread, never past its left
    expect(page.card!.x + page.card!.width).toBe((page.x ?? 0) + C.EDGE);
    expect(chat.cast).toBe(true);
    expect(sheet(L, 'tico')).toMatchObject({ depth: 0 });
  });

  it('opens a lower sheet by its edge: the sheets above it collapse to edges, Tico too', () => {
    const L = layout(1200, 'chat', {}, 'page');
    expect(line(L)).toBe('edge, edge, edge, 1052');
    expect(L.sheets.map(s => s.depth)).toEqual([0, 1, 2, 3]);
    expect(sheet(L, 'page').card!.x).toBe((sheet(L, 'page').x ?? 0) - C.UNDER); // its corners hide under the thread's edge
    // the thread opened at 1024: Tico and the company draw in; the page below follows the window (2 open)
    expect(line(layout(1024, 'chat', {}, 'chat'))).toBe('edge, edge, 320, 592');
    // once the window has room for it, the usual layout is back
    expect(forwardFor(1200, 'chat', {}, 'page')).toBe('page');
    expect(forwardFor(1600, 'chat', {}, 'page')).toBeNull();
  });

  it('gives the wide layout back on widening and never writes what it decided', () => {
    const sv: Saved = { tico: 340, chat: { w: { chat: 400 } } };
    const before = JSON.stringify(sv);
    layout(900, 'chat', sv);
    expect(JSON.stringify(sv)).toBe(before);
    expect(sheet(layout(1920, 'chat', sv), 'tico').width).toBe(340);
    expect(sheet(layout(1920, 'chat', sv), 'chat').width).toBe(400);
  });

  it('takes a widened sheet back to its default before anything tucks', () => {
    const g = layout(1440, 'chat', { tico: 480 });
    expect(sheet(g, 'tico')).toMatchObject({ width: 352, state: 'open' });
    expect(g.mode === 'sheets' && g.stretch.width).toBe(480);
  });

  it("keeps Tico's width as one number and its fold per arrangement", () => {
    for (const n of ['page', 'chat', 'agent'] as const) expect(sheet(layout(1920, n, { tico: 340 }), 'tico').width).toBe(340);
    expect(sheet(layout(1920, 'page', { page: { fold: { tico: true } } }), 'tico').state).toBe('fold');
    expect(sheet(layout(1920, 'page', { page: { fold: { tico: true } } }), 'tico').width).toBe(C.EDGE);
    expect(sheet(layout(1920, 'agent', { page: { fold: { tico: true } } }), 'tico').state).toBe('open');
  });

  it('pulls saved widths inside the floor and ceiling', () => {
    expect(sheet(layout(1920, 'page', { tico: 9999 }), 'tico').width).toBe(480);
    expect(sheet(layout(1920, 'page', { tico: 1 }), 'tico').width).toBe(260);
  });

  it('opens a tucked sheet in the row when there is room, and by its edge for now when not', () => {
    const folded: Saved = { page: { fold: { tico: true } } };
    expect(press(1440, 'page', folded, 'tico')).toMatchObject({ forward: null, saved: { page: { fold: { tico: false } } } });
    expect(press(1200, 'chat', {}, 'page')).toEqual({ saved: {}, forward: 'page' });
    expect(press(1100, 'page', {}, 'tico').saved.page?.fold?.tico).toBe(true);
    expect(press(1200, 'chat', {}, 'chat', 'chat').forward).toBeNull(); // folding the opened one lets it go
  });

  it('switches to cards at 760 and back at 800', () => {
    expect(layout(760, 'page', {}).mode).toBe('cards');
    expect(layout(761, 'page', {}).mode).toBe('sheets');
    expect(modeFor(780, 'cards')).toBe('cards');
    expect(modeFor(780, 'sheets')).toBe('sheets');
    expect(modeFor(800, 'cards')).toBe('sheets');
    expect(modeFor(780)).toBe('sheets');
  });
});
