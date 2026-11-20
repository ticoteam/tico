import type { ComponentProps } from 'react';
import { usePress, type PressHandler } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import './IconButton.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type IconButtonProps = Omit<ComponentProps<'button'>, 'children' | 'aria-label' | 'title' | 'onClick'> & {
  icon: IconName;
  /** Read aloud and shown on hover. */
  label: string;
  size?: 'md' | 'sm';
  quiet?: boolean;
  /** A filter that is set, a panel that is open. Leave undefined for a plain button. */
  on?: boolean;
  /** Waiting on the server: the icon breathes and takes no press. */
  busy?: boolean;
  /** Return the promise of the work it starts: busy until it settles, and a failure is said (kit/Button/press.ts). */
  onClick?: PressHandler<HTMLButtonElement>;
};

export function IconButton({ icon, label, size = 'md', quiet = false, on, busy: told = false, onClick, disabled, className, type = 'button', ...rest }: IconButtonProps) {
  const { busy, onClick: press } = usePress(onClick, told);
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={on}
      onClick={press}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={['ib', size === 'sm' && 'ib-sm', quiet && 'ib-quiet', on && 'is-on', className].filter(Boolean).join(' ')}
    >
      <Icon name={icon} />
    </button>
  );
}
