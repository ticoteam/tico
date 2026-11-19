import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaskRow, ThreadRow, WhoRow } from './Row';

vi.mock('@/lib', () => ({ fmtAge: () => '2h', fmtDate: () => 'Nov 19' }));

const at = new Date('2026-11-19T08:00:00.000Z');

describe('Row', () => {
  it('draws a task as a title-only link with its cells in the grid order', () => {
    render(<TaskRow href="/tasks/t1" title="Ship the price page" late at={at} target="task:t1" selected />);
    const row = screen.getByRole('link', { name: /Ship the price page/ });
    expect(row.className).toBe('row task is-selected');
    expect(row.getAttribute('data-target')).toBe('task:t1');
    expect(row.getAttribute('aria-current')).toBe('true');
    const cells = Array.from(row.children).map((c) => c.className);
    expect(cells).toEqual(['row-mark', 'row-title', 'row-pills', 'av-gap', 'row-age']);
    expect(screen.getByText('Late').classList).toHaveLength(2);
  });

  it('marks an unread thread and counts what is new', () => {
    render(<ThreadRow href="/threads/c1" title="Pricing" kind="threads" unread count={4} at={at} />);
    const row = screen.getByRole('link', { name: /Pricing/ });
    expect(row.className).toBe('row tl-row is-unread');
    expect(screen.getByLabelText('4 new').className).toBe('count');
  });

  it('draws a teammate with a count', () => {
    render(<WhoRow href="/agents/b1" title="Builder" avatar={<span className="av" />} count={2} />);
    expect(screen.getByRole('link', { name: /Builder/ }).className).toBe('row who');
  });
});
