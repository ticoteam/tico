import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, Menu, MenuItem, MenuRule } from './Menu';

function TaskMenu({ onRename = () => {}, onDelete = () => {} }) {
  return (
    <Menu trigger={<IconButton icon="more" label="More" />}>
      <MenuItem icon="edit" label="Rename" onSelect={onRename} />
      <MenuItem icon="archive" label="Archive" disabled onSelect={() => {}} />
      <MenuRule />
      <MenuItem label="Delete task" onSelect={onDelete} />
    </Menu>
  );
}

describe('Menu', () => {
  it('opens from its button and picks an item, then closes', async () => {
    const onRename = vi.fn();
    render(<TaskMenu onRename={onRename} />);
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const item = await screen.findByRole('menuitem', { name: 'Rename' });
    expect(screen.getByRole('menu').className).toContain('menu');
    fireEvent.click(item);
    expect(onRename).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('closes with Escape and gives focus back to its button', async () => {
    render(<TaskMenu />);
    const more = screen.getByRole('button', { name: 'More' });
    fireEvent.click(more);
    const menu = await screen.findByRole('menu');
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(document.activeElement).toBe(more);
  });

  it('does not pick a disabled item', async () => {
    render(<TaskMenu />);
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const item = await screen.findByRole('menuitem', { name: 'Archive' });
    expect(item.getAttribute('aria-disabled')).toBe('true');
    expect(item.className).toContain('is-disabled');
  });
});

describe('ContextMenu', () => {
  it('marks the long-pressed row so a touch does not select its text', () => {
    render(
      <ContextMenu items={<MenuItem label="Open" onSelect={() => {}} />}>
        <div>Launch plan</div>
      </ContextMenu>,
    );
    expect(screen.getByText('Launch plan').hasAttribute('data-long-press')).toBe(true);
  });

  it('opens on a right click', async () => {
    const onSelect = vi.fn();
    render(
      <ContextMenu items={<MenuItem label="Open" onSelect={onSelect} />}>
        <div>Launch plan</div>
      </ContextMenu>,
    );
    act(() => {
      fireEvent.contextMenu(screen.getByText('Launch plan'));
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open' }));
    expect(onSelect).toHaveBeenCalledOnce();
  });
});

describe('Menu on a phone', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('is a sheet over a scrim on the cards, and a tap on the scrim closes it', async () => {
    document.documentElement.dataset.layout = 'cards';
    render(<TaskMenu />);
    const more = screen.getByRole('button', { name: 'More' });
    fireEvent.click(more);
    const menu = await screen.findByRole('menu');
    expect(menu.className).toContain('is-sheet');
    const scrim = document.querySelector<HTMLElement>('.scrim');
    expect(scrim).not.toBeNull();
    // pinned to the bottom edge, not placed beside its button
    expect(menu.parentElement?.style.bottom).toBe('0px');
    fireEvent.pointerDown(scrim!);
    fireEvent.mouseDown(scrim!);
    fireEvent.click(scrim!);
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(document.querySelector('.scrim')).toBeNull();
  });

  it('opens beside its button, with no scrim, on a wide window', async () => {
    document.documentElement.dataset.layout = 'sheets';
    render(<TaskMenu />);
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const menu = await screen.findByRole('menu');
    expect(menu.className).not.toContain('is-sheet');
    expect(menu.className).toContain('kit-pop');
    expect(document.querySelector('.scrim')).toBeNull();
  });
});
