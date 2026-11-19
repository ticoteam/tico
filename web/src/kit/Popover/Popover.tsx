import type { ReactElement, ReactNode } from 'react';
import { Popover as BasePopover } from '@base-ui/react/popover';
import './Popover.css';

/** On a positioner: the overlay layer (menus, pickers). */
export const popLayer = 'kit-layer';
/** On a popup: opens from the corner nearest its button and leaves as a fade (motion.md G10). */
export const popMotion = 'kit-pop';

export type PopoverProps = {
  /** The button that opens it; it gets the open state and the ARIA wiring. */
  trigger: ReactElement;
  /** The mock's class for this card, such as `date-pick`. */
  className: string;
  /** Names the card for a screen reader. */
  label: string;
  children: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  side?: 'top' | 'bottom' | 'left' | 'right';
  align?: 'start' | 'center' | 'end';
};

/** A card that opens from a button, inside the overlay layer. Escape and a click outside close it. */
export function Popover({ trigger, className, label, children, open, onOpenChange, side = 'bottom', align = 'start' }: PopoverProps) {
  return (
    <BasePopover.Root open={open} onOpenChange={onOpenChange ? (next) => onOpenChange(next) : undefined}>
      <BasePopover.Trigger render={trigger} />
      <BasePopover.Portal>
        <BasePopover.Positioner className={popLayer} side={side} align={align} sideOffset={6}>
          <BasePopover.Popup className={`${className} ${popMotion}`} aria-label={label}>
            {children}
          </BasePopover.Popup>
        </BasePopover.Positioner>
      </BasePopover.Portal>
    </BasePopover.Root>
  );
}
