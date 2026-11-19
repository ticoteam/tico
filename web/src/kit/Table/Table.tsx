import type { ReactNode, TdHTMLAttributes } from 'react';
import { Icon } from '@/kit/Icon';
import { FoldButton } from '@/kit/SectionHead';
import './Table.css';

export type TableProps = {
  /** What the table lists, read aloud. */
  label: string;
  /** The head row stays while the body scrolls. */
  stick?: boolean;
  children: ReactNode;
};

/** A table with a column head row: the only place rows are split by rules (parts.css `.table`, R25). */
export function Table({ label, stick, children }: TableProps) {
  return (
    <table className={stick ? 'table is-stick' : 'table'} aria-label={label}>
      {children}
    </table>
  );
}

export type ThProps = {
  children: ReactNode;
  num?: boolean;
  /** This column orders the rows. */
  sorted?: 'ascending' | 'descending';
  /** Makes the head a button that orders by this column. */
  onSort?: () => void;
};

/** A column head. Sorted: ink, strong, with the 12px arrow. */
export function Th({ children, num, sorted, onSort }: ThProps) {
  const cls = [num && 'num', sorted && 'is-sorted'].filter(Boolean).join(' ') || undefined;
  return (
    <th scope="col" className={cls} aria-sort={sorted}>
      {onSort ? (
        <button type="button" onClick={onSort}>
          {children}
          {sorted && <Icon name="chevdown" className={sorted === 'ascending' ? 'flip' : undefined} />}
        </button>
      ) : (
        children
      )}
    </th>
  );
}

export type TdProps = Omit<TdHTMLAttributes<HTMLTableCellElement>, 'className'> & {
  /** A figure: right-aligned, tabular, the open zero. */
  num?: boolean;
  /** A zero that should recede. */
  zero?: boolean;
};

export function Td({ num, zero, children, ...rest }: TdProps) {
  const cls = [num && 'num', zero && 'zero'].filter(Boolean).join(' ') || undefined;
  return (
    <td {...rest} className={cls}>
      {children}
    </td>
  );
}

export type TrProps = {
  /** A total line, or a quiet one (a projection, a line that does not count). */
  kind?: 'total' | 'quiet';
  selected?: boolean;
  /**
   * The row opens what the first link in its first cell opens, or in the cell marked `data-opens` when a mark column
   * comes first. That link is stretched over the whole row, so a click anywhere on it, Enter and a middle-click all
   * follow a real link. Other links and buttons in the row stay on top.
   */
  linked?: boolean;
  /** A feature's own row look, beside the kit's. */
  className?: string;
  children: ReactNode;
};

export function Tr({ kind, selected, linked, className, children }: TrProps) {
  const cls = [kind && `is-${kind}`, selected && 'is-selected', linked && 'is-link', className].filter(Boolean).join(' ') || undefined;
  return (
    <tr className={cls}>
      {children}
    </tr>
  );
}

export type GroupRowProps = {
  name: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  /** Columns the name spans. */
  span: number;
  /** The group's figure, in the last column. */
  total?: ReactNode;
};

/** A group's name inside a table, folding the rows under it, with its figure on the right. */
export function GroupRow({ name, count, open, onToggle, span, total }: GroupRowProps) {
  return (
    <tr className="is-group">
      <td colSpan={span}>
        <FoldButton name={name} count={count} open={open} onToggle={onToggle} />
      </td>
      {total !== undefined && <td className="num">{total}</td>}
    </tr>
  );
}

export type EmptyRowProps = { span: number; children: ReactNode };

/** "No orders yet": one quiet line inside the table, its head kept. */
export function EmptyRow({ span, children }: EmptyRowProps) {
  return (
    <tr className="table-empty">
      <td colSpan={span}>{children}</td>
    </tr>
  );
}
