import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button, FillContext } from '@/kit/Button';
import { Dialog } from './Dialog';

function Harness({ onDelete = () => {} }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Delete
      </button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        alert
        title="Delete this task?"
        description="Its chat goes with it."
        actions={
          <>
            <Button onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => { onDelete(); setOpen(false); }}>Delete task</Button>
          </>
        }
      />
    </>
  );
}

describe('Dialog', () => {
  it('is a sheet of its own: opened from a head that draws plain, its act stays filled', async () => {
    render(
      <FillContext value={false}>
        <Button variant="primary">Add watch</Button>
        <Dialog open onOpenChange={() => {}} title="Add a watch" actions={<Button variant="primary">Save</Button>} />
      </FillContext>,
    );
    expect((await screen.findByRole('button', { name: 'Save' })).className).toBe('btn btn-fill');
    expect(screen.getByRole('button', { name: 'Add watch', hidden: true }).className).toBe('btn');
  });

  it('opens named by its title, keeps focus inside, and acts', async () => {
    const onDelete = vi.fn();
    render(<Harness onDelete={onDelete} />);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete this task?' });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Delete task' }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it('closes with Escape and gives focus back', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Delete' });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });
});
