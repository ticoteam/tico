import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { OwnerButton } from './OwnerButton';

describe('OwnerButton', () => {
  it('is a link to their chat, named by who it opens, with only the face', () => {
    render(<OwnerButton name="QA Owner" kind="agent" hue={320} href="/tasks?chat=bot%3Aqa" />);
    const a = screen.getByRole('link', { name: 'Chat with QA Owner' });
    expect(a.getAttribute('href')).toBe('/tasks?chat=bot%3Aqa');
    expect(a.className).toBe('pg-owner');
    expect(a.textContent).toBe('QO');
    expect(a.querySelector('.av.av-28.is-agent')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('presses, and says when their chat is open', () => {
    const onPress = vi.fn();
    render(<OwnerButton name="Maya Okafor" kind="person" on onPress={onPress} />);
    const b = screen.getByRole('button', { name: 'Chat with Maya Okafor' });
    expect(b.getAttribute('aria-pressed')).toBe('true');
    expect(b.className).toBe('pg-owner is-on');
    fireEvent.click(b);
    expect(onPress).toHaveBeenCalledOnce();
  });
});
