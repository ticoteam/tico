import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Select } from './Select';

// Read with node's fs (tests load no CSS, not even ?raw), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const popoverCss = fs.readFileSync(new URL('../Popover/Popover.css', here), 'utf8');

const TEAMS = [
  { value: 'product', label: 'Product' },
  { value: 'engineering', label: 'Engineering' },
  { value: 'growth', label: 'Growth', disabled: true },
];

describe('Select', () => {
  it('shows the chosen value, opens and picks another', async () => {
    const onValueChange = vi.fn();
    render(<Select label="Team" options={TEAMS} value="engineering" onValueChange={onValueChange} />);
    const trigger = screen.getByRole('combobox', { name: /Team/ });
    expect(trigger.textContent).toBe('Engineering');
    fireEvent.click(trigger);
    const option = await screen.findByRole('option', { name: 'Product' });
    fireEvent.pointerDown(option, { pointerType: 'mouse' });
    fireEvent.click(option);
    expect(onValueChange).toHaveBeenCalledWith('product');
  });

  it('shows the placeholder, and closes with Escape', async () => {
    render(<Select label="Pipeline" options={TEAMS} value={null} placeholder="Pipeline" onValueChange={() => {}} />);
    const trigger = screen.getByRole('combobox', { name: /Pipeline/ });
    expect(trigger.querySelector('.ph')?.textContent).toBe('Pipeline');
    fireEvent.click(trigger);
    const list = await screen.findByRole('listbox');
    fireEvent.keyDown(list, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it('does not open while disabled', () => {
    render(<Select label="Team" look="quiet" labelHidden options={TEAMS} value="product" disabled onValueChange={() => {}} />);
    fireEvent.click(screen.getByRole('combobox', { name: 'Team' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('Select on a phone', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('opens its list as a sheet over a scrim, and a tap on the scrim closes it', async () => {
    document.documentElement.dataset.layout = 'cards';
    render(<Select label="Team" options={TEAMS} value="engineering" onValueChange={() => {}} />);
    fireEvent.click(screen.getByRole('combobox', { name: /Team/ }));
    const list = await screen.findByRole('listbox');
    expect(list.className).toContain('is-sheet');
    const scrim = document.querySelector<HTMLElement>('.scrim');
    expect(scrim).not.toBeNull();
    fireEvent.pointerDown(scrim!);
    fireEvent.mouseDown(scrim!);
    fireEvent.click(scrim!);
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it("keeps its scrim hidden while closed, so a dialog's buttons stay pressable", async () => {
    document.documentElement.dataset.layout = 'cards';
    // parts.css gives every .scrim a display; Popover.css must let [hidden] win over it
    const style = document.createElement('style');
    style.textContent = `.scrim{position:fixed;inset:0;display:grid}${popoverCss}`;
    document.head.append(style);
    render(<Select label="Hand its work to" options={TEAMS} value="product" onValueChange={() => {}} />);
    // a dialog focuses its first field: the select mounts its closed list and scrim
    fireEvent.focus(screen.getByRole('combobox', { name: /Hand its work to/ }));
    const scrim = await waitFor(() => {
      const s = document.querySelector<HTMLElement>('.kit-dim');
      expect(s).not.toBeNull();
      return s!;
    });
    expect(scrim.hidden).toBe(true);
    expect(getComputedStyle(scrim).display).toBe('none');
    style.remove();
  });
});
