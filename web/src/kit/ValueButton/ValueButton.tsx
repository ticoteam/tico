import type { ComponentProps, ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import './ValueButton.css';

export type ValueButtonProps = Omit<ComponentProps<'button'>, 'children' | 'aria-label'> & {
  /** The value's words ("Haiku 5.5, High"). */
  children: ReactNode;
  /** What it changes, read aloud ("Builder One's model"). Leave out when the words say it. */
  label?: string;
  /** A quiet tail after the words (", High"), shown on a wide table only. */
  more?: string;
  /** The value is a default ("Same", "None"): muted. */
  muted?: boolean;
  /** `value`: in a row or a panel (settings.css `.dd`). `scope`: in a page head, after the title (`.scope-pick`). */
  look?: 'value' | 'scope';
};

/**
 * A value changed where it stands: its words and a small chevron. A button only; it opens whatever the caller hangs
 * on it (a `Menu`'s trigger, a dialog). Disabled, it shows the words without the chevron.
 */
export function ValueButton({ children, label, more, muted = false, look = 'value', className, title, type = 'button', ...rest }: ValueButtonProps) {
  const base = look === 'scope' ? 'scope-pick' : 'dd';
  const cls = [base, muted && 'is-same', className].filter(Boolean).join(' ');
  return (
    <button {...rest} type={type} className={cls} aria-label={label} title={title ?? (typeof children === 'string' ? children : undefined)}>
      <span>
        {children}
        {more && <span className="dd-more">{`, ${more}`}</span>}
      </span>
      <Icon name="chevdown" />
    </button>
  );
}
