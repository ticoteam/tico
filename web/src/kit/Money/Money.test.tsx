import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Money } from './Money';

vi.mock('@/lib', () => ({
  fmtMoney: (n: number, o?: { cents?: boolean }) => `$${o?.cents ? n.toFixed(2) : Math.round(n)}`,
}));

describe('Money', () => {
  it('writes whole dollars unless cents are asked for', () => {
    render(
      <>
        <Money amount={1240.5} className="a" />
        <Money amount={12.5} cents className="b" />
      </>,
    );
    expect(screen.getByText('$1241').className).toBe('a');
    expect(screen.getByText('$12.50').className).toBe('b');
  });
});
