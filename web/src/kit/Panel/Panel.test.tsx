import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Panel } from './Panel';

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Grow revenue
      </button>
      {open && (
        <Panel label="Goal" head={<b>Grow revenue</b>} onClose={() => setOpen(false)}>
          <section className="rail-sec">KPIs</section>
        </Panel>
      )}
    </>
  );
}

describe('Panel', () => {
  it('closes with its x', () => {
    const onClose = vi.fn();
    render(<Panel label="Goal" head={<b>Grow revenue</b>} onClose={onClose}>body</Panel>);
    expect(screen.getByRole('complementary', { name: 'Goal' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('closes with Escape and gives focus back to the row it came from', () => {
    render(<Harness />);
    const row = screen.getByRole('button', { name: 'Grow revenue' });
    row.focus();
    fireEvent.click(row);
    expect(document.activeElement).toBe(screen.getByRole('complementary', { name: 'Goal' }));
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('complementary')).toBeNull();
    expect(document.activeElement).toBe(row);
  });
});
