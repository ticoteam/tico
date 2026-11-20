import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Frame, foldSheet } from './Frame';
import { Sheet } from './Sheet';

// the frame alone: no motion, a plain Dot, no phone cards
vi.mock('@/motion', () => ({ motionLevel: () => 'off' }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" /> }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/shell/phone', () => ({ PhoneCards: () => <div>cards</div> }));

const KEY = 'tico-next:sheets.v1';
const resize = (w: number) =>
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });
const slots = {
  tico: <Sheet kind="tico" label="Tico" />,
  menu: <nav className="pane nav" aria-label="Main" />,
  page: <Sheet kind="page" label="Tasks" />,
};
const handle = (name: string) => screen.getByRole('separator', { name: `Resize ${name}` });

describe('Frame', () => {
  it('tucks right to left as the window narrows, saves none of it, and gives the layout back on widening', () => {
    resize(1440);
    render(<Frame {...slots} arrangement="page" />);
    expect(screen.getByRole('main', { name: 'Tasks' })).toBeTruthy();
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('300');
    expect(handle('the menu').getAttribute('aria-valuenow')).toBe('248');
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    expect([place('tico'), place('menu'), place('page')].map(el => el.style.zIndex)).toEqual(['3', '2', '1']); // the leftmost on top

    resize(1000); // the page goes under the menu, leaving its edge
    expect(place('page').dataset.shut).toBe('true');
    expect(place('page').hasAttribute('inert')).toBe(true);
    expect(screen.getByRole('button', { name: 'Open the page' })).toBeTruthy();
    expect(place('menu').dataset.cast).toBe('true');
    resize(820); // then the menu under Tico
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    expect([place('tico'), place('menu'), place('page')].map(el => el.style.zIndex)).toEqual(['3', '2', '1']);

    resize(1440);
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('300');
    expect(handle('the menu').getAttribute('aria-valuenow')).toBe('248');
    expect(screen.queryByRole('button', { name: /^Open / })).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("opens a lower sheet by its edge: the sheets above it collapse to edges, Tico too, and Escape gives the layout back", () => {
    resize(1000);
    render(<Frame {...slots} arrangement="page" />);
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    fireEvent.click(screen.getByRole('button', { name: 'Open the page' }));
    expect(place('page').dataset.shut).toBeUndefined();
    expect(place('tico').dataset.shut).toBe('true');
    expect(place('menu').dataset.shut).toBe('true');
    expect([place('tico'), place('menu'), place('page')].map(el => el.style.zIndex)).toEqual(['3', '2', '1']); // nothing raised
    expect(localStorage.getItem(KEY)).toBeNull();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(place('page').dataset.shut).toBe('true');
    expect(place('tico').dataset.shut).toBeUndefined();
  });

  it('sizes by keyboard and remembers it; double-click puts it back', () => {
    resize(1440);
    render(<Frame {...slots} arrangement="page" />);
    fireEvent.keyDown(handle('Tico'), { key: 'ArrowRight' });
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('316');
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}').tico).toBe(316);
    fireEvent.keyDown(handle('Tico'), { key: 'End' });
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('480');
    fireEvent.doubleClick(handle('Tico'));
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('300');
  });

  it('folds the menu by hand with [ and keeps it for this arrangement only', () => {
    resize(1440);
    const { rerender } = render(<Frame {...slots} arrangement="page" />);
    fireEvent.keyDown(document.body, { key: '[' });
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}').page.fold.menu).toBe(true);
    rerender(<Frame {...slots} arrangement="agent" widgets={<Sheet kind="column" label="Widgets" />} />);
    expect(handle('the menu').getAttribute('aria-valuenow')).toBe('248');
  });

  it("in the Mac app, marks the sheets under the window's buttons: a head moves right, an edge starts below", async () => {
    const root = document.documentElement;
    root.dataset.desktop = 'mac';
    root.style.setProperty('--desktop-inset-left', '92px');
    root.style.setProperty('--desktop-inset-top', '52px');
    try {
      resize(1440);
      render(<Frame {...slots} arrangement="page" />);
      const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
      expect(place('tico').dataset.lights).toBe('left');
      expect(place('tico').style.getPropertyValue('--lights-left')).toBe('84px');
      expect(place('menu').dataset.lights).toBeUndefined();
      act(() => foldSheet('tico', true)); // Tico draws in to its edge; the menu beside it starts at 52, under the buttons
      expect(place('tico').dataset.lights).toBe('top');
      expect(screen.getByRole('button', { name: 'Open Tico' }).dataset.lights).toBe('top');
      expect(place('menu').dataset.lights).toBe('left');
      expect(place('menu').style.getPropertyValue('--lights-left')).toBe('40px');
      await act(async () => root.setAttribute('style', '--desktop-inset-left: 0px; --desktop-inset-top: 0px')); // full screen
      expect(place('tico').dataset.lights).toBeUndefined();
    } finally {
      delete root.dataset.desktop;
      root.removeAttribute('style');
    }
  });
});
