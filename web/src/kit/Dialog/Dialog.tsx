import type { ReactElement, ReactNode } from 'react';
import { Dialog as BaseDialog } from '@base-ui/react/dialog';
import './Dialog.css';

export type DialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A question or a name, in sentence case. */
  title: string;
  /** One line under the title. */
  description?: ReactNode;
  /** Fields, when the dialog asks for something. */
  children?: ReactNode;
  /** The buttons at the foot, plain then filled. The filled one names the act ("Delete task"). */
  actions: ReactNode;
  /** A confirm: it must be answered, so a click outside does not close it. */
  alert?: boolean;
  /** A button that opens it; without one, focus goes back to whatever had it. */
  trigger?: ReactElement;
};

/** A dialog over a dimmed app. Focus stays inside while it is open and goes back when it closes; Escape closes it. */
export function Dialog({ open, onOpenChange, title, description, children, actions, alert = false, trigger }: DialogProps) {
  return (
    <BaseDialog.Root open={open} onOpenChange={(next) => onOpenChange(next)} disablePointerDismissal={alert}>
      {trigger && <BaseDialog.Trigger render={trigger} />}
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="scrim kit-scrim" />
        <BaseDialog.Viewport className="scrim kit-viewport">
          <BaseDialog.Popup className="modal kit-modal" role={alert ? 'alertdialog' : 'dialog'}>
            <BaseDialog.Title>{title}</BaseDialog.Title>
            {description && <BaseDialog.Description>{description}</BaseDialog.Description>}
            {children}
            <div className="modal-row">{actions}</div>
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
