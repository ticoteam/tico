import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Popover } from './Popover';

describe('Popover', () => {
  it('opens from its button and closes with Escape', async () => {
    render(
      <Popover trigger={<button type="button">Due</button>} className="date-pick" label="Pick a day">
        <button type="button">Today</button>
      </Popover>,
    );
    const opener = screen.getByRole('button', { name: 'Due' });
    fireEvent.click(opener);
    const card = await screen.findByRole('dialog', { name: 'Pick a day' });
    expect(card.className).toContain('date-pick');
    fireEvent.keyDown(card, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(opener);
  });
});
