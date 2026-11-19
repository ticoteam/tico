import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DateText } from './DateText';

vi.mock('@/lib', () => ({ fmtDate: () => 'Nov 19' }));

describe('DateText', () => {
  it('writes the date and keeps the exact moment on the time element', () => {
    render(<DateText at="2026-11-19T10:00:00.000Z" />);
    const el = screen.getByText('Nov 19');
    expect(el.tagName).toBe('TIME');
    expect(el.getAttribute('datetime')).toBe('2026-11-19T10:00:00.000Z');
  });
});
