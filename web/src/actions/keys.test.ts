import { afterEach, describe, expect, it, vi } from 'vitest';
import { keyHandler, shortcutsOf, type Shortcut } from './keys';
import { s } from './schema';
import { defineAction, type AnyAction } from './types';

vi.mock('@/data', () => ({ queryClient: {} }));
vi.mock('@/search', () => ({ openSearch: vi.fn() }));
vi.mock('@/motion', () => ({ flight: vi.fn(), waitForTarget: vi.fn(), wash: vi.fn() }));

const act = (id: `${string}.${string}`, keys: Record<string, object>) =>
  defineAction({ id, title: id, kind: 'view', input: s.object({}), run: () => {}, keys }) as AnyAction;
const table = (...as: AnyAction[]) => shortcutsOf(new Map(as.map((a) => [a.id, a])));

const fired: Shortcut[] = [];
let off = () => {};
function listen(t: Shortcut[]) {
  const h = keyHandler(t, (sc) => fired.push(sc), true);
  const l = (e: KeyboardEvent) => void h(e);
  window.addEventListener('keydown', l);
  off = () => window.removeEventListener('keydown', l);
}
const press = (on: Element, key: string, o: KeyboardEventInit = {}) =>
  on.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...o }));

afterEach(() => {
  off();
  fired.length = 0;
  document.body.innerHTML = '';
});

describe('shortcuts', () => {
  it('fires single keys outside fields and ignores them while typing', () => {
    listen(table(act('shell.fold', { '[': { sheet: 'menu' } })));
    const input = document.body.appendChild(document.createElement('input'));
    const box = document.body.appendChild(document.createElement('div'));
    box.setAttribute('contenteditable', 'true');
    const area = document.body.appendChild(document.createElement('textarea'));
    press(input, '[');
    press(area, '[');
    press(box, '[');
    expect(fired).toEqual([]);
    press(document.body, '[');
    expect(fired.map((f) => f.combo)).toEqual(['[']);
  });

  it('fires mod combos even in a field, and stops the browser default', () => {
    listen(table(act('search.open', { 'mod+k': {} })));
    const input = document.body.appendChild(document.createElement('input'));
    const e = new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true });
    input.dispatchEvent(e);
    expect(fired.map((f) => f.id)).toEqual(['search.open']);
    expect(e.defaultPrevented).toBe(true);
    press(document.body, 'k');
    expect(fired).toHaveLength(1);
  });

  it('follows a two-step sequence', () => {
    listen(table(act('shell.goTo', { 'g t': { to: '/tasks' } })));
    press(document.body, 'g');
    expect(fired).toEqual([]);
    press(document.body, 't');
    expect(fired.map((f) => f.input)).toEqual([{ to: '/tasks' }]);
  });

  it('refuses keys the browser keeps and the same key twice', () => {
    expect(() => table(act('a.b', { 'mod+w': {} }))).toThrow(/kept by the browser/);
    expect(() => table(act('a.b', { '[': {} }), act('c.d', { '[': {} }))).toThrow(/on both a.b and c.d/);
  });
});
