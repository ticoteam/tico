import type { ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import './LabelValue.css';

/** A list of label and value lines. */
export function Facts({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className="facts" role={label ? 'group' : undefined} aria-label={label}>
      {children}
    </div>
  );
}

export type LabelValueProps = {
  label: string;
  children: ReactNode;
  /** A number: right-aligned and tabular. Nothing else right-aligns. */
  num?: boolean;
  /** Makes the line a picker: the value's words, then a chevron. */
  onEdit?: () => void;
  /** At the line's end, outside it: the line's ⋯ menu (`<Menu trigger={<IconButton icon="more" … />}>`). */
  trail?: ReactNode;
};

/** One label and its value (pages/8-settings `.fact`). */
export function LabelValue({ trail, ...line }: LabelValueProps) {
  if (!trail) return <Line {...line} />;
  return (
    <div className="fact-more">
      <Line {...line} />
      {trail}
    </div>
  );
}

function Line({ label, children, num, onEdit }: Omit<LabelValueProps, 'trail'>) {
  if (onEdit) {
    return (
      <button type="button" className="fact is-edit" onClick={onEdit}>
        <span>{label}</span>
        <span className="fact-v">
          <span className="fact-t">{children}</span>
          <span className="fact-pick">
            <Icon name="chevdown" />
          </span>
        </span>
      </button>
    );
  }
  return (
    <div className="fact">
      <span>{label}</span>
      {num ? (
        <span className="fact-v fact-num">{children}</span>
      ) : (
        <span className="fact-v">
          <span className="fact-t">{children}</span>
        </span>
      )}
    </div>
  );
}
