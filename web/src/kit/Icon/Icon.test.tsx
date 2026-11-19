import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Icon } from './Icon';

describe('Icon', () => {
  it('is hidden from screen readers unless it carries a label', () => {
    const { container } = render(<Icon name="search" />);
    expect(container.querySelector('svg.ico')?.getAttribute('aria-hidden')).toBe('true');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('draws the mock shapes and names itself when labelled', () => {
    render(<Icon name="tasks" label="Tasks" />);
    const svg = screen.getByRole('img', { name: 'Tasks' });
    expect(svg.querySelector('circle')?.getAttribute('r')).toBe('9');
    expect(svg.querySelector('path')?.getAttribute('d')).toBe('M8 12.5l2.8 2.8L16 9.5');
  });
});
