import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Facts, LabelValue } from './LabelValue';

describe('LabelValue', () => {
  it('lists a label beside its value', () => {
    render(
      <Facts label="Plan">
        <LabelValue label="Model">Opus</LabelValue>
        <LabelValue label="Seats" num>
          12
        </LabelValue>
      </Facts>,
    );
    const group = screen.getByRole('group', { name: 'Plan' });
    expect(group.className).toBe('facts');
    expect(screen.getByText('Opus').className).toBe('fact-t');
    expect(screen.getByText('12').className).toBe('fact-v fact-num');
  });

  it('turns into a picker when the value can change', () => {
    const onEdit = vi.fn();
    render(
      <LabelValue label="Model" onEdit={onEdit}>
        Opus
      </LabelValue>,
    );
    const pick = screen.getByRole('button', { name: /Model\s*Opus/ });
    expect(pick.className).toBe('fact is-edit');
    fireEvent.click(pick);
    expect(onEdit).toHaveBeenCalledOnce();
  });
});
