import { createContext, use, type ComponentProps, type ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import { usePress, type PressHandler } from './press';
import './Button.css';

export type ButtonVariant = 'plain' | 'primary' | 'quiet';

type IconName = ComponentProps<typeof Icon>['name'];

export type ButtonProps = Omit<ComponentProps<'button'>, 'children' | 'onClick'> & {
  /** `primary` is the filled button: one to a sheet. */
  variant?: ButtonVariant;
  /** A leading icon. */
  icon?: IconName;
  /** Waiting on the server: the label dims and breathes; no spinner. A busy button takes no press. */
  busy?: boolean;
  /** Return the promise of the work it starts: the button is busy until it settles, and a failure is said (press.ts). */
  onClick?: PressHandler<HTMLButtonElement>;
  children: ReactNode;
};

const VARIANT: Record<ButtonVariant, string | undefined> = { plain: undefined, primary: 'btn-fill', quiet: 'btn-quiet' };

/**
 * False: this sheet's one filled button stands elsewhere (an empty page's action in its empty state), so a `primary`
 * button inside draws plain. A dialog or a popover is a sheet of its own and sets it back to true.
 */
export const FillContext = createContext(true);

export function Button({ variant = 'plain', icon, busy: told = false, onClick, disabled, className, children, ref, type = 'button', ...rest }: ButtonProps) {
  const fill = use(FillContext);
  const { busy, onClick: press } = usePress(onClick, told);
  const look = variant === 'primary' && !fill ? 'plain' : variant;
  const own = look === 'primary' && import.meta.env.DEV ? warnOnSecondPrimary : undefined;
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
      className={['btn', VARIANT[look], className].filter(Boolean).join(' ')}
      onClick={press}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

const warned = new WeakSet<Element>();

const SHEET = '.pane, .modal, .date-pick, [data-sheet]';

/** Dev only: a sheet (a pane, a dialog, the date picker, anything marked `data-sheet`) holds one filled button; one in
    a sheet inside it counts there. */
function warnOnSecondPrimary(el: HTMLButtonElement | null) {
  const sheet = el?.closest(SHEET);
  if (!sheet || warned.has(sheet)) return;
  if (Array.from(sheet.querySelectorAll('.btn-fill')).filter((b) => b.closest(SHEET) === sheet).length > 1) {
    warned.add(sheet);
    console.warn('Two filled buttons in one sheet; keep one primary.', sheet);
  }
}
