import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { findTarget, freeze, now, registerTargetResolver, seek, waitForTarget } from '@/motion';

const rect = (top: number, height = 28) => ({ top, bottom: top + height, height, left: 0, right: 300, width: 300, x: 0, y: top, toJSON() {} }) as DOMRect;

function place(el: Element, top: number) {
  el.getBoundingClientRect = () => rect(top);
}

beforeEach(() => {
  freeze(0);
  document.body.innerHTML = '';
});

afterEach(() => {
  seek(now() + 1e6);
});

describe('findTarget', () => {
  it('finds the marked thing on screen', () => {
    document.body.innerHTML = '<div data-target="field:fees" aria-label="Fees"></div>';
    place(document.querySelector('[data-target]')!, 100);
    expect(findTarget('field', 'fees')).toBe(document.querySelector('[aria-label="Fees"]'));
  });

  it('finds an id with a quote or a line break in it', () => {
    const el = document.createElement('div');
    el.dataset.target = 'doc:a"b\nc';
    document.body.append(el);
    place(el, 100);
    expect(findTarget('doc', 'a"b\nc')).toBe(el);
  });

  it('lands on the folded section that holds it', () => {
    document.body.innerHTML =
      '<section data-target="section:money" aria-label="Money"><div hidden><div data-target="field:fees"></div></div></section>';
    expect(findTarget('field', 'fees')).toBe(document.querySelector('[aria-label="Money"]'));
  });

  it('lands on the team row that stands for an agent it hides', () => {
    document.body.innerHTML = '<div data-target="team:ops" data-target-holds="agent:fo agent:gm" aria-label="Operations"></div>';
    expect(findTarget('agent', 'gm')).toBe(document.querySelector('[aria-label="Operations"]'));
  });

  it('gives nothing when the thing is not drawn', () => {
    document.body.innerHTML = '<div data-target="field:other"></div>';
    expect(findTarget('field', 'fees')).toBeNull();
  });

  it('off screen: an edge chip with the words, or with show, jumps it into view', () => {
    document.body.innerHTML = '<div data-target="row:software"></div>';
    const row = document.querySelector('[data-target]')!;
    place(row, window.innerHeight + 400);
    const chip = findTarget('row', 'software', { label: 'Software changed' });
    expect(chip).toBeInstanceOf(HTMLButtonElement);
    expect(chip!.matches('.fx-edge > .edge')).toBe(true);
    expect(chip!.classList.contains('up')).toBe(false);
    expect(chip!.textContent).toBe('Software changed');
    expect(findTarget('row', 'software')).toBe(chip);

    const jump = vi.fn();
    row.scrollIntoView = jump;
    expect(findTarget('row', 'software', { show: true })).toBe(row);
    expect(jump).toHaveBeenCalledOnce();
  });

  it('lets the edge chip go after three seconds when nothing landed on it', () => {
    document.body.innerHTML = '<div data-target="row:software"></div>';
    place(document.querySelector('[data-target]')!, -400);
    const chip = findTarget('row', 'software')!;
    expect(chip.classList.contains('up')).toBe(true);
    seek(2999);
    expect(chip.isConnected).toBe(true);
    seek(3000);
    expect(chip.isConnected).toBe(false);
  });

  it('asks a virtual list for a row it has not drawn', () => {
    const list = document.createElement('div');
    document.body.append(list);
    const reveal = vi.fn();
    const off = registerTargetResolver((kind, id) => (kind === 'row' && id === '7' ? { scroller: list, side: 'above', reveal } : null));
    const chip = findTarget('row', 7);
    expect(chip?.classList.contains('up')).toBe(true);
    expect(findTarget('row', 7, { show: true })).toBeNull();
    expect(reveal).toHaveBeenCalledOnce();
    off();
    chip?.parentElement?.remove();
    expect(findTarget('row', 7)).toBeNull();
  });

  it('waits for a thing drawn after Tico moves the page, and gives up after 1.5s', async () => {
    const found = waitForTarget('field', 'fees');
    const el = document.createElement('div');
    el.dataset.target = 'field:fees';
    document.body.append(el);
    await expect(found).resolves.toBe(el);

    const missing = waitForTarget('field', 'nope');
    seek(now() + 1500);
    await expect(missing).resolves.toBeNull();
  });
});
