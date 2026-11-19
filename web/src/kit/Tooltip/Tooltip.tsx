import type { ReactElement, ReactNode } from 'react';
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip';
import './Tooltip.css';

export type TooltipProps = {
  /** A few words; `<b>` for a key. */
  label: ReactNode;
  /** The thing it explains. An icon button already has its own hover title; use this for a richer tip. */
  children: ReactElement;
  side?: 'top' | 'bottom' | 'left' | 'right';
};

export function Tooltip({ label, children, side = 'top' }: TooltipProps) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={children} />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner className="kit-tip-layer" side={side} sideOffset={6}>
          <BaseTooltip.Popup className="tip kit-tip">{label}</BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  );
}
