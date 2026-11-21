import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AskCard } from './AskCard';

/* On a computer the note sits at the left of the buttons' row (ask.css), only while that row is the card's last line:
   with no buttons, or with what went wrong said under them, it stays in the card's top line. */
describe('AskCard', () => {
  const card = (props: Partial<Parameters<typeof AskCard>[0]>) => {
    const { unmount } = render(<AskCard name="Send the letter" note="Sends now" end={<span />} {...props} />);
    const row = screen.getByRole('region', { name: 'The decision' }).classList.contains('has-row');
    unmount();
    return row;
  };

  it('puts the note in the buttons row only when that row ends the card', () => {
    expect(card({ actions: <button>Approve</button> })).toBe(true);
    expect(card({ actions: <button>Approve</button>, error: 'It did not send' })).toBe(false);
    expect(card({})).toBe(false);
    expect(card({ error: 'It did not load' })).toBe(false);
  });
});
