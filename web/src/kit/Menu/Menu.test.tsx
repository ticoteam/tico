import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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
