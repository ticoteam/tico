import type { ReactNode } from 'react';
import { Switch as BaseSwitch } from '@base-ui/react/switch';

export type SwitchProps = {
  label: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  error?: boolean;
  name?: string;
};

/** Words on the left, the switch at the row's right edge; the whole row presses it. */
export function Switch({ label, checked, onCheckedChange, disabled = false, error = false, name }: SwitchProps) {
  return (
    <label className="opt">
      {label}
      <BaseSwitch.Root
        className={['switch', checked && 'is-on', disabled && 'is-disabled', error && 'is-error'].filter(Boolean).join(' ')}
        checked={checked}
        onCheckedChange={(next) => onCheckedChange(next)}
        disabled={disabled}
        name={name}
        aria-invalid={error || undefined}
      >
        <BaseSwitch.Thumb render={<i />} />
      </BaseSwitch.Root>
    </label>
  );
}
