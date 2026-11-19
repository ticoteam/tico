import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Badge, Count } from './Count';

describe('Count', () => {
  it('uses the one count style and caps at 99+', () => {
    render(
      <>
        <Count n={7} label="7 tasks" />
        <Count n={140} label="140 tasks" />
      </>,
    );
    expect(screen.getByLabelText('7 tasks').className).toBe('count');
    expect(screen.getByLabelText('140 tasks').textContent).toBe('99+');
  });

  it('draws a badge on an icon and nothing for zero', () => {
    const { container } = render(
      <>
        <Badge n={3} label="3 new" />
        <Badge n={0} label="none" />
      </>,
    );
    expect(screen.getByLabelText('3 new').className).toBe('slot-n');
    expect(container.querySelectorAll('.slot-n')).toHaveLength(1);
  });
});
