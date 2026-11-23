import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FlowMark } from './FlowMark';

describe('FlowMark', () => {
  it('is the small dot that says something waits on you', () => {
    render(<FlowMark size={10} />);
    expect(screen.getByRole('img', { name: 'Waits on you' }).className).toBe('dot flow');
  });
});
