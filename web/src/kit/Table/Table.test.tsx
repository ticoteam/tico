import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EmptyRow, GroupRow, Table, Td, Th, Tr } from './Table';

describe('Table', () => {
  it('right-aligns figures with the tabular, open-zero class and marks the sorted head', () => {
    const onSort = vi.fn();
    render(
      <Table label="Orders" stick>
        <thead>
          <tr>
            <Th>Customer</Th>
            <Th num sorted="descending" onSort={onSort}>
              Total
            </Th>
          </tr>
        </thead>
        <tbody>
          <Tr>
            <Td>North</Td>
            <Td num>1,240</Td>
          </Tr>
          <Tr kind="total">
            <Td>All</Td>
            <Td num zero>
              0
            </Td>
          </Tr>
        </tbody>
      </Table>,
    );
    const table = screen.getByRole('table', { name: 'Orders' });
    expect(table.className).toBe('table is-stick');
    const head = screen.getByRole('columnheader', { name: 'Total' });
    expect(head.className).toBe('num is-sorted');
    expect(head.getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(within(head).getByRole('button'));
    expect(onSort).toHaveBeenCalledOnce();
    expect(screen.getByRole('cell', { name: '1,240' }).className).toBe('num');
    expect(screen.getByRole('cell', { name: '0' }).className).toBe('num zero');
    expect(screen.getByRole('cell', { name: 'All' }).parentElement?.className).toBe('is-total');
  });

  it('folds a group from its name and keeps the head over an empty body', () => {
    const onToggle = vi.fn();
    render(
      <Table label="Ledger">
        <tbody>
          <GroupRow name="Revenue" count={4} open span={1} total="48,210" onToggle={onToggle} />
          <EmptyRow span={2}>No orders yet</EmptyRow>
        </tbody>
      </Table>,
    );
    const fold = screen.getByRole('button', { name: /Revenue/ });
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(fold);
    expect(onToggle).toHaveBeenCalledOnce();
    expect(screen.getByRole('cell', { name: '48,210' }).className).toBe('num');
    expect(screen.getByRole('cell', { name: 'No orders yet' }).parentElement?.className).toBe('table-empty');
  });
});
