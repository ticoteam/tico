import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Ago } from './Ago';

vi.mock('@/lib', () => ({ fmtAge: () => '4m', fmtDate: () => 'Nov 19' }));

describe('Ago', () => {
  it('writes the age with the date on hover', () => {
    render(<Ago at={new Date('2026-11-19T10:00:00.000Z')} className="row-age" />);
    const el = screen.getByText('4m');
    expect(el.className).toBe('row-age');
    expect(el.getAttribute('title')).toBe('Nov 19');
    expect(el.getAttribute('datetime')).toBe('2026-11-19T10:00:00.000Z');
  });
});
