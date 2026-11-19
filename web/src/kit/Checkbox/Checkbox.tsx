import type { ReactNode } from 'react';
import { Checkbox as BaseCheckbox } from '@base-ui/react/checkbox';
import { Icon } from '@/kit/Icon';

export type CheckboxProps = {
  label: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  error?: boolean;
  name?: string;
};

/** A box and its words; the whole row presses it. */
export function Checkbox({ label, checked, onCheckedChange, disabled = false, error = false, name }: CheckboxProps) {
  return (
    <label className={['check', checked && 'is-on', disabled && 'is-disabled', error && 'is-error'].filter(Boolean).join(' ')}>
      <BaseCheckbox.Root
        className="check-box"
        checked={checked}
        onCheckedChange={(next) => onCheckedChange(next)}
        disabled={disabled}
        name={name}
        aria-invalid={error || undefined}
      >
        <Icon name="check" />
      </BaseCheckbox.Root>
      {label}
    </label>
  );
}
