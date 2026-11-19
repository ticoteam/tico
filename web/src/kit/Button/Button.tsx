import type { ComponentProps, ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import './Button.css';

export type ButtonVariant = 'plain' | 'primary' | 'quiet';

type IconName = ComponentProps<typeof Icon>['name'];

export type ButtonProps = Omit<ComponentProps<'button'>, 'children'> & {
  /** `primary` is the filled button: one to a sheet. */
  variant?: ButtonVariant;
  /** A leading icon. */
  icon?: IconName;
  /** Waiting on the server: the label dims and breathes; no spinner. */
  busy?: boolean;
  children: ReactNode;
};

const VARIANT: Record<ButtonVariant, string | undefined> = { plain: undefined, primary: 'btn-fill', quiet: 'btn-quiet' };

export function Button({ variant = 'plain', icon, busy = false, className, children, ref, type = 'button', ...rest }: ButtonProps) {
  const own = variant === 'primary' && import.meta.env.DEV ? warnOnSecondPrimary : undefined;
  return (
    <button
      {...rest}
      ref={
        own
          ? (el: HTMLButtonElement | null) => {
              own(el);
              if (typeof ref === 'function') ref(el);
              else if (ref) ref.current = el;
            }
          : ref
      }
      type={type}
      className={['btn', VARIANT[variant], className].filter(Boolean).join(' ')}
      aria-busy={busy || undefined}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

const warned = new WeakSet<Element>();

/** Dev only: a sheet (a pane, a dialog, the date picker) holds one filled button. */
function warnOnSecondPrimary(el: HTMLButtonElement | null) {
  const sheet = el?.closest('.pane, .modal, .date-pick');
  if (!sheet || warned.has(sheet)) return;
  if (sheet.querySelectorAll('.btn-fill').length > 1) {
    warned.add(sheet);
    console.warn('Two filled buttons in one sheet; keep one primary.', sheet);
  }
}
