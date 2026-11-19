import type { ReactElement, ReactNode } from 'react';
import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import './Dialog.css';

export type DialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Over the title: where you are in a few steps, a way back, the Dot. */
  lead?: ReactNode;
  /** A question or a name, in sentence case. */
  title: string;
  /** One line under the title. */
  description?: ReactNode;
  /** Fields, when the dialog asks for something. */
  children?: ReactNode;
  /** The buttons at the foot, plain then filled. The filled one names the act ("Delete task"). Left out when the
      children end in their own foot (a form whose submit button is in it). */
  actions?: ReactNode;
  /** A confirm: it must be answered, so a click outside does not close it. */
  alert?: boolean;
  /** A button that opens it; without one, focus goes back to whatever had it. */
  trigger?: ReactElement;
  /** A dialog with its own look beside `modal` (the first run's `wl`). */
  className?: string;
};

/** A dialog over a dimmed app. Focus stays inside while it is open and goes back when it closes; Escape closes it. */
export function Dialog({ open, onOpenChange, lead, title, description, children, actions, alert = false, trigger, className }: DialogProps) {
  return (
    <BaseDialog.Root open={open} onOpenChange={(next) => onOpenChange(next)} disablePointerDismissal={alert}>
      {trigger && <BaseDialog.Trigger render={trigger} />}
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="scrim kit-scrim" />
        <BaseDialog.Viewport className="scrim kit-viewport">
          <BaseDialog.Popup className={className ? `modal kit-modal ${className}` : 'modal kit-modal'} role={alert ? 'alertdialog' : 'dialog'}>
            {lead}
            <BaseDialog.Title>{title}</BaseDialog.Title>
            {description && <BaseDialog.Description>{description}</BaseDialog.Description>}
            {children}
            {actions !== undefined && <div className="modal-row">{actions}</div>}
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
