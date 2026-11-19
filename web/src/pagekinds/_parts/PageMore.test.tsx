import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MenuItem } from '@/kit/Menu';
import { PageMore } from './PageMore';

describe('PageMore', () => {
  it('is the "⋯" named More, and opens the page’s items', async () => {
    const onSelect = vi.fn();
    render(
      <PageMore target="tasks:more">
        <MenuItem label="Archived" icon="archive" onSelect={onSelect} />
      </PageMore>,
    );
    const more = screen.getByRole('button', { name: 'More' });
    expect(more.getAttribute('data-target')).toBe('tasks:more');
    fireEvent.click(more);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archived' }));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it('draws nothing without items', () => {
    const show = false;
    const { container } = render(<PageMore>{show && <MenuItem label="Archived" onSelect={() => undefined} />}</PageMore>);
    expect(container.innerHTML).toBe('');
  });
});
