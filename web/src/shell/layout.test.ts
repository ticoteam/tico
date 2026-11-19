import { describe, expect, it } from 'vitest';
import { C, layout, modeFor, press, type ArrangementSpec, type Layout, type Saved, type SheetId } from './layout';

const line = (L: Layout) =>
  L.mode === 'cards'
    ? 'cards'
    : L.sheets.map(s => (s.state === 'open' ? String(s.width) : s.state === 'fold' ? (s.width ? String(s.width) : 'shut') : s.state)).join(', ');
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

// layout.md 2.6 "Every width asked for" (and the panel-only line under it), left to right
const TABLE: Record<string, Record<number, string>> = {
  page: { 2560: '300, 248, 1980', 1920: '300, 248, 1340', 1728: '300, 248, 1148', 1440: '300, 248, 860', 1280: '300, 248, 700', 1100: '300, 248, 520', 1024: '300, 56, 636', 900: '300, 56, 512', 760: 'cards' },
  chat: { 2560: '300, 248, 320, 1652', 1920: '300, 248, 320, 1012', 1728: '300, 248, 320, 820', 1440: '300, 56, 320, 724', 1280: '300, 56, 320, 564', 1100: '56, 56, 320, 628', 1024: '56, 56, 320, 552', 900: '56, 56, over, 756', 760: 'cards' },
  'chat + panel': { 2560: '300, 248, 320, 1300, 344', 1920: '300, 56, 320, 852, 344', 1728: '300, 56, 320, 660, 344', 1440: '300, 56, 320, 724, over', 1280: '300, 56, 320, 564, over', 1100: '56, 56, 320, 628, over', 1024: '56, 56, 320, 552, over', 900: '56, 56, tucked, 756, over', 760: 'cards' },
  'panel only': { 1728: '300, 248, 796, 344', 1440: '300, 56, 700, 344', 1280: '300, 56, 540, 344', 1100: '300, 56, 712, over', 1024: '300, 56, 636, over', 900: '300, 56, 512, over' },
  agent: { 2560: '300, 248, 1612, 360', 1920: '300, 248, 972, 360', 1728: '300, 248, 780, 360', 1440: '300, 248, 492, 360', 1280: '300, 56, 524, 360', 1100: '56, 56, 588, 360', 1024: '56, 56, 512, 360', 900: '56, 56, 756, tucked', 760: 'cards' },
  task: { 2560: '300, 288, 392, 1540', 1920: '300, 288, 392, 900', 1728: '300, 288, 392, 708', 1440: '56, 288, 392, 664', 1280: '56, 288, 392, 504', 1100: '56, 56, 392, 556', 1024: '56, 56, 392, 480', 900: '56, 56, over, 756', 760: 'cards' },
  call: { 2560: '56, 2088, 384', 1920: '56, 1448, 384', 1728: '56, 1256, 384', 1440: '56, 968, 384', 1280: '56, 808, 384', 1100: '56, 628, 384', 1024: '56, 552, 384', 900: '56, 820, tucked', 760: 'cards' },
};

// layout.md 2.5: the window width under which each fold happens, in order, at default sizes
const FOLDS: Record<string, [SheetId, number][]> = {
  page: [['menu', 1060], ['tico', 868]],
  chat: [['menu', 1608], ['tico', 1196], ['chat', 952]],
  'chat + panel': [['menu', 1960], ['panel', 1548], ['tico', 1196], ['chat', 952]],
  'panel only': [['menu', 1632], ['panel', 1220], ['tico', 868]],
  agent: [['menu', 1428], ['tico', 1236], ['widgets', 992]],
  task: [['tico', 1720], ['list', 1256], ['chat', 1024]],
  call: [['side', 952]],
};

describe('layout', () => {
  it.each(ARRS)('gives the widths in the table: %s', (name, arr) => {
    for (const [w, want] of Object.entries(TABLE[name] ?? {})) expect(`${w}: ${line(layout(Number(w), arr, {}))}`).toBe(`${w}: ${want}`);
  });

  it.each(ARRS)('folds in the set order while narrowing, and only there: %s', (name, arr) => {
    const seen: [SheetId, number][] = [];
    let before = layout(2600, arr, {});
    for (let W = 2599; W > C.CARDS_AT; W--) {
      const now = layout(W, arr, {});
      for (const s of now.sheets) {
        const was = before.sheets.find(o => o.id === s.id);
        if (was && was.state === 'open' && s.state !== 'open') seen.push([s.id, W + 1]);
        if (was && was.state !== 'open' && s.state === 'open') throw new Error(`${s.id} opened again at ${W} while narrowing`);
      }
      expect(now.mode === 'sheets' && now.stretch.width).toBeGreaterThanOrEqual(C.FLOOR);
      before = now;
    }
    expect(seen).toEqual(FOLDS[name]);
  });

  it('never squeezes the stretching sheet under its floor, and fills the window, for the largest saved sizes', () => {
    const big: Saved = { tico: 480, page: { w: { menu: 320 } }, chat: { fold: { menu: false, tico: false }, w: { chat: 520, panel: 480, menu: 320 } }, agent: { w: { widgets: 480, menu: 320 } }, task: { fold: { tico: false }, w: { list: 400, chat: 520 } }, call: { fold: { tico: false }, w: { side: 520 } } };
    for (let W = 761; W <= 2600; W += 7) {
      for (const [, arr] of ARRS) {
        const L = layout(W, arr, big);
        if (L.mode !== 'sheets') throw new Error('cards');
        expect(L.stretch.width).toBeGreaterThanOrEqual(C.FLOOR);
        const used = L.sheets.filter(s => s.x != null && (s.state === 'open' || (s.state === 'fold' && s.width))).reduce((m, s) => m + s.width + C.GAP, C.MARGIN as number);
        expect(used).toBe(W);
      }
    }
  });

  it('gives the wide layout back on widening and never writes what it decided', () => {
    const sv: Saved = { tico: 340, chat: { w: { chat: 400 } } };
    const before = JSON.stringify(sv);
    layout(900, 'chat', sv);
    expect(JSON.stringify(sv)).toBe(before);
    expect(sheet(layout(1920, 'chat', sv), 'tico').width).toBe(340);
    expect(sheet(layout(1920, 'chat', sv), 'chat').width).toBe(400);
  });

  it('takes a widened sheet back to its default before anything folds', () => {
    const g = layout(1280, 'chat', { tico: 480 });
    expect(sheet(g, 'tico')).toMatchObject({ width: 384, state: 'open' });
    expect(g.mode === 'sheets' && g.stretch.width).toBe(480);
  });

  it("keeps Tico's width as one number and its fold per arrangement", () => {
    for (const n of ['page', 'chat', 'agent'] as const) expect(sheet(layout(1920, n, { tico: 340 }), 'tico').width).toBe(340);
    expect(sheet(layout(1920, 'page', { page: { fold: { tico: true } } }), 'tico').state).toBe('fold');
    expect(sheet(layout(1920, 'agent', { page: { fold: { tico: true } } }), 'tico').state).toBe('open');
  });

  it('pulls saved widths inside the floor and ceiling', () => {
    expect(sheet(layout(1920, 'page', { tico: 9999 }), 'tico').width).toBe(480);
    expect(sheet(layout(1920, 'page', { tico: 1 }), 'tico').width).toBe(260);
  });

  it('opens in the row when there is room, and over the neighbours when not', () => {
    expect(press(1440, 'chat', {}, 'menu').peek).toBeNull();
    expect(press(1440, 'task', {}, 'tico').peek).toBe('tico');
    expect(press(1100, 'page', {}, 'tico').saved.page?.fold?.tico).toBe(true);
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
