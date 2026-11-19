import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Tooltip } from './Tooltip';

describe('Tooltip', () => {
  it('shows its words while the thing has keyboard focus and hides on Escape', async () => {
    render(
      <Tooltip label="Search everything">
        <button type="button">Search</button>
      </Tooltip>,
    );
    const b = screen.getByRole('button', { name: 'Search' });
    fireEvent.focus(b);
    const tip = await screen.findByText('Search everything');
    expect(tip.className).toContain('tip');
    fireEvent.keyDown(b, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Search everything')).toBeNull());
  });
});
