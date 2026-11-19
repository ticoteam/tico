import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmCard } from './ConfirmCard';

const props = {
  mark: <i />,
  question: 'Send these 2 emails?',
  steps: [
    { icon: 'send', title: 'Welcome email', detail: 'To 2 new customers' },
    { icon: 'clock', title: 'Follow-up', detail: 'In 3 days' },
  ],
  note: 'Sent email cannot be taken back.',
  act: 'Send 2 emails',
} as const;

describe('ConfirmCard', () => {
  it('names the act on its one filled button', () => {
    const onAct = vi.fn();
    render(<ConfirmCard {...props} onAct={onAct} onNotNow={() => {}} />);
    expect(screen.getByRole('region', { name: 'Tico asks first' })).toBeTruthy();
    const yes = screen.getByRole('button', { name: 'Send 2 emails' });
    expect(yes.classList.contains('btn-fill')).toBe(true);
    fireEvent.click(yes);
    expect(onAct).toHaveBeenCalledOnce();
    expect(screen.getByText('Welcome email')).toBeTruthy();
  });

  it('can be put off', () => {
    const onNotNow = vi.fn();
    render(<ConfirmCard {...props} onAct={() => {}} onNotNow={onNotNow} />);
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    expect(onNotNow).toHaveBeenCalledOnce();
  });

  it('holds both buttons while the answer is on its way', () => {
    const onNotNow = vi.fn();
    render(<ConfirmCard {...props} busy onAct={() => {}} onNotNow={onNotNow} />);
    expect(screen.getByRole('button', { name: 'Send 2 emails' }).getAttribute('aria-busy')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    expect(onNotNow).not.toHaveBeenCalled();
  });
});
