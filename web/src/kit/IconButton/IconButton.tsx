import type { ComponentProps } from 'react';
import { Icon } from '@/kit/Icon';
import './IconButton.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type IconButtonProps = Omit<ComponentProps<'button'>, 'children' | 'aria-label' | 'title'> & {
  icon: IconName;
  /** Read aloud and shown on hover. */
  label: string;
  size?: 'md' | 'sm';
  quiet?: boolean;
  /** A filter that is set, a panel that is open. Leave undefined for a plain button. */
  on?: boolean;
  busy?: boolean;
};

export function IconButton({ icon, label, size = 'md', quiet = false, on, busy = false, className, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={on}
      aria-busy={busy || undefined}
      className={['ib', size === 'sm' && 'ib-sm', quiet && 'ib-quiet', on && 'is-on', className].filter(Boolean).join(' ')}
    >
      <Icon name={icon} />
    </button>
  );
}
