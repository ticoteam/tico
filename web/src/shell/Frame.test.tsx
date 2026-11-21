import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Frame } from './Frame';
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
  it('tucks as the window narrows, saves none of it, and gives the layout back on widening', () => {
    resize(1440);
    render(<Frame {...slots} arrangement="page" />);
    expect(screen.getByRole('main', { name: 'Tasks' })).toBeTruthy();
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('300');
    expect(handle('the menu').getAttribute('aria-valuenow')).toBe('248');
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    expect([place('tico'), place('menu'), place('page')].map(el => el.style.zIndex)).toEqual(['3', '2', '1']); // the leftmost on top

    resize(1000); // the page the address opened stays; Tico collapses to its edge first
    expect(place('page').dataset.shut).toBeUndefined();
    expect(place('tico').dataset.shut).toBe('true');
    expect(place('tico').hasAttribute('inert')).toBe(true);
    expect(screen.getByRole('button', { name: 'Open Tico' })).toBeTruthy();
    expect(place('tico').dataset.cast).toBe('true');
    resize(800); // then the menu
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    expect([place('tico'), place('menu'), place('page')].map(el => el.style.zIndex)).toEqual(['3', '2', '1']);

    resize(1440);
    expect(handle('Tico').getAttribute('aria-valuenow')).toBe('300');
    expect(handle('the menu').getAttribute('aria-valuenow')).toBe('248');
    expect(screen.queryByRole('button', { name: /^Open / })).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('on a 1440 laptop, opening a task shows the task: Tico collapses to its edge, the task’s chat stays', () => {
    resize(1440);
    render(<Frame {...slots} arrangement="task" chat={<Sheet kind="chat" label="The task's chat" />} list={<nav className="pane nav list" aria-label="Flow" />} />);
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    expect(place('page').dataset.shut).toBeUndefined();
    expect(screen.getByRole('main', { name: 'Tasks' })).toBeTruthy();
    expect(place('tico').dataset.shut).toBe('true');
    expect(screen.getByRole('button', { name: 'Open Tico' })).toBeTruthy();
    expect(screen.getByRole('complementary', { name: "The task's chat" })).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Flow' })).toBeTruthy();
    expect(['tico', 'list', 'chat', 'page'].map(id => place(id).style.zIndex)).toEqual(['4', '3', '2', '1']);
  });

  it('a page with a second chat narrows from the right: the page goes under the thread first', () => {
    resize(1280);
    render(<Frame {...slots} arrangement="chat" chat={<Sheet kind="chat" label="Chat with Maya" />} />);
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    expect(place('page').dataset.shut).toBe('true');
    expect(screen.getByRole('button', { name: 'Open the page' })).toBeTruthy();
    expect(place('tico').dataset.shut).toBeUndefined();
  });

  it("opens a lower sheet by its edge: the sheets above it collapse to edges, Tico too, and Escape gives the layout back", () => {
    resize(1280);
    render(<Frame {...slots} arrangement="chat" chat={<Sheet kind="chat" label="Chat with Maya" />} />);
    const place = (id: string) => document.querySelector<HTMLElement>(`[data-sheet="${id}"]`)!;
    fireEvent.click(screen.getByRole('button', { name: 'Open the page' }));
    expect(place('page').dataset.shut).toBeUndefined();
    for (const id of ['tico', 'menu', 'chat']) expect(place(id).dataset.shut).toBe('true');
    expect(['tico', 'menu', 'chat', 'page'].map(id => place(id).style.zIndex)).toEqual(['4', '3', '2', '1']); // nothing raised
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

  it('folds the menu by hand with [, and it stays folded on a deeper page (a panel, a teammate)', () => {
    resize(1440);
    const { rerender } = render(<Frame {...slots} arrangement="page" />);
    fireEvent.keyDown(document.body, { key: '[' });
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}').fold.menu).toBe(true);
    rerender(<Frame {...slots} arrangement="page" panel={<Sheet kind="column" label="Panel" />} />);
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    rerender(<Frame {...slots} arrangement="agent" widgets={<Sheet kind="column" label="Widgets" />} />);
    expect(screen.getByRole('button', { name: 'Open the menu' })).toBeTruthy();
    expect(screen.queryByRole('separator', { name: 'Resize the menu' })).toBeNull();
  });
});
