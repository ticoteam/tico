import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Menu, MenuItem } from '@/kit/Menu';
import { ValueButton } from './ValueButton';

describe('ValueButton', () => {
  it('shows its words and a chevron, is named by what it changes, and presses', () => {
    const onClick = vi.fn();
    render(
      <ValueButton label="Builder One's model" more="High" onClick={onClick}>
        Haiku 5.5
      </ValueButton>,
    );
    const b = screen.getByRole('button', { name: "Builder One's model" });
    expect(b.className).toBe('dd');
    expect(b.getAttribute('title')).toBe('Haiku 5.5');
    expect(b.textContent).toBe('Haiku 5.5, High');
    expect(b.querySelector('.ico')).toBeTruthy();
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('a default is muted; the head look is the scope pick; its words name it when no label is given', () => {
    render(
      <>
        <ValueButton muted>Same</ValueButton>
        <ValueButton look="scope">Engineering</ValueButton>
      </>,
    );
    expect(screen.getByRole('button', { name: 'Same' }).className).toBe('dd is-same');
    expect(screen.getByRole('button', { name: 'Engineering' }).className).toBe('scope-pick');
  });

  it('opens a menu as its trigger', async () => {
    render(
      <Menu trigger={<ValueButton label="Computer">Studio Mac</ValueButton>}>
        <MenuItem label="Office Mac" onSelect={() => undefined} />
      </Menu>,
    );
    const b = screen.getByRole('button', { name: 'Computer' });
    fireEvent.click(b);
    expect(await screen.findByRole('menuitem', { name: 'Office Mac' })).toBeTruthy();
    expect(b.getAttribute('aria-expanded')).toBe('true');
  });
});
