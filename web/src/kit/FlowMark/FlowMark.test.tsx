import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FlowMark } from './FlowMark';

describe('FlowMark', () => {
  it('is the small dot that says something waits on you', () => {
    render(<FlowMark size={10} />);
    expect(screen.getByRole('img', { name: 'Waits on you' }).className).toBe('dot flow');
  });

  it("is the Flow button's play mark with play, riding while live", () => {
    const { container } = render(<FlowMark play live />);
    const mark = container.querySelector('.flow-mark');
    expect(mark?.className).toBe('flow-mark live');
    expect(mark?.getAttribute('aria-hidden')).toBe('true');
    expect(mark?.querySelectorAll('path')).toHaveLength(2);
  });
});
