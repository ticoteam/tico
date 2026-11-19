import type { ComponentProps } from 'react';
import { Toggle } from '@base-ui/react/toggle';
import { ToggleGroup } from '@base-ui/react/toggle-group';
import { Icon } from '@/kit/Icon';
import './Segmented.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type SegmentedOption<V extends string = string> = {
  value: V;
  /** The words shown, or read aloud when the option is an icon. */
  label: string;
  icon?: IconName;
  disabled?: boolean;
};

export type SegmentedProps<V extends string = string> = {
  /** Names the group for a screen reader. */
  label: string;
  options: readonly SegmentedOption<V>[];
  value: V;
  onValueChange: (value: V) => void;
  disabled?: boolean;
};

/** A few choices side by side, one always on. Icons when every option has one, words otherwise. */
export function Segmented<V extends string>({ label, options, value, onValueChange, disabled = false }: SegmentedProps<V>) {
  const icons = options.every((o) => o.icon);
  return (
    <ToggleGroup
      className={icons ? 'seg' : 'seg is-words'}
      aria-label={label}
      value={[value]}
      disabled={disabled}
      onValueChange={(next) => {
        const picked = next[0];
        if (picked !== undefined) onValueChange(picked as V);
      }}
    >
      {options.map((o) => (
        <Toggle
          key={o.value}
          value={o.value}
          disabled={o.disabled}
          className={(state) => (state.pressed ? 'is-on' : '')}
          aria-label={icons ? o.label : undefined}
          title={icons ? o.label : undefined}
        >
          {icons && o.icon ? <Icon name={o.icon} /> : o.label}
        </Toggle>
      ))}
    </ToggleGroup>
  );
}
