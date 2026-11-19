import { Icon } from '@/kit/Icon';

export interface FilterChip {
  /** What is set, as the chip reads ("QA Owner", "Late"). Unique within the row. */
  label: string;
  onClear: () => void;
}

export interface FilterRowProps {
  filters: readonly FilterChip[];
  onClearAll?: () => void;
}

/**
 * The row under the head while a filter is set (pages/1-tasks/list.js): a chip per filter, then Clear. Nothing when no
 * filter is set. On a phone it folds into the filter button, which shows it is on.
 */
export function FilterRow({ filters, onClearAll }: FilterRowProps) {
  if (!filters.length) return null;
  return (
    <div className="filter-row" role="group" aria-label="Filters">
      {filters.map((f) => (
        <span key={f.label} className="scope">
          {f.label}
          <button type="button" aria-label={`Clear ${f.label}`} onClick={f.onClear}>
            <Icon name="close" />
          </button>
        </span>
      ))}
      {onClearAll && (
        <button className="chip-clear" type="button" onClick={onClearAll}>
          Clear
        </button>
      )}
    </div>
  );
}
