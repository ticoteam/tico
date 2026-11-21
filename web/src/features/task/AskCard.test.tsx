import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AskCard, Docked } from './AskCard';

/* On a computer the note sits in the buttons' row, at its left, only while that row is the card's last line: with no
   buttons, or with what went wrong said under them, it stays in the card's top line. On a phone it is always there. */
describe('AskCard', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  const where = (props: Partial<Parameters<typeof AskCard>[0]>) => {
    const { unmount } = render(<AskCard name="Send the letter" note="Sends now" end={<span />} {...props} />);
    const note = screen.queryByText('Sends now');
    // ask.css .askcard-note: a long note shortens with an ellipsis rather than push out the ask and the face
    if (note) expect(note.className).toBe('askcard-note');
    const at = note?.closest('.an-row') ? 'row' : note?.closest('.askcard-top') ? 'top' : null;
    unmount();
    return at;
  };
  const approve = <button>Approve</button>;

  it('puts the note in the buttons row only when that row ends the card', () => {
    expect(where({ actions: approve })).toBe('row');
    expect(where({ actions: approve, error: 'It did not send' })).toBe('top');
    expect(where({})).toBe('top');
    expect(where({ error: 'It did not load' })).toBe('top');
    expect(where({ actions: approve, asked: true })).toBe('top');
    expect(where({ actions: approve, asking: true })).toBe(null);
  });

  it('keeps the note in the top line on a phone', () => {
    document.documentElement.dataset.layout = 'cards';
    expect(where({ actions: approve })).toBe('top');
    expect(where({ actions: approve, asking: true })).toBe('top');
  });

  /* #4820: a phone opened a task with its decision under the Chat sheet and Tico's bar while they sprang away. The dock
     is fresh on its first frames (phone.css sends the cards resting over it off at once then), and only then. */
  it('marks a new dock fresh on its first frames only', async () => {
    const { container } = render(
      <Docked dock>
        <AskCard name="Send the letter" end={<span />} actions={approve} />
      </Docked>,
    );
    const dock = container.querySelector('[data-dock]');
    expect(dock?.hasAttribute('data-fresh')).toBe(true);
    await waitFor(() => expect(dock?.hasAttribute('data-fresh')).toBe(false));
  });
});
