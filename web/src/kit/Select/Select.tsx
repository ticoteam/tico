import { Select as BaseSelect } from '@base-ui/react/select';
import { Icon } from '@/kit/Icon';
import { MenuItemBody } from '@/kit/Menu';
import { popLayer, popMotion } from '@/kit/Popover';
import './Select.css';

export type SelectOption<V extends string = string> = {
  value: V;
  label: string;
  /** A quiet note at the right of its row, such as a model's price. */
  meta?: string;
  disabled?: boolean;
};

export type SelectProps<V extends string = string> = {
  label: string;
  /** Read the label aloud only; a row or a page head already says what this is. */
  labelHidden?: boolean;
  options: readonly SelectOption<V>[];
  value: V | null;
  onValueChange: (value: V) => void;
  /** Shown while nothing is chosen. */
  placeholder?: string;
  /** `field` in a form; `quiet` is the one quiet button for a row or a page head. */
  look?: 'field' | 'quiet';
  disabled?: boolean;
  error?: boolean;
};

/** A button that shows the chosen value and opens the list. */
export function Select<V extends string>({
  label,
  labelHidden = false,
  options,
  value,
  onValueChange,
  placeholder,
  look = 'field',
  disabled = false,
  error = false,
}: SelectProps<V>) {
  const chosen = options.find((o) => o.value === value);
  const shown = chosen ? <span>{chosen.label}</span> : <span className="ph">{placeholder}</span>;
  const trigger =
    look === 'field' ? (
      <BaseSelect.Trigger
        className={['field is-wide is-select', error && 'is-error', disabled && 'is-disabled'].filter(Boolean).join(' ')}
        aria-label={labelHidden ? label : undefined}
      >
        {shown}
        <Icon name="chevdown" />
      </BaseSelect.Trigger>
    ) : (
      <BaseSelect.Trigger className={chosen ? 'dd' : 'dd is-same'} aria-label={labelHidden ? label : undefined}>
        {shown}
        <Icon name="chevdown" />
      </BaseSelect.Trigger>
    );
  return (
    <BaseSelect.Root
      value={value}
      onValueChange={(next) => {
        if (next !== null) onValueChange(next as V);
      }}
      items={options.map((o) => ({ value: o.value, label: o.label }))}
      disabled={disabled}
      modal={false}
    >
      {labelHidden ? (
        trigger
      ) : (
        <div className="f">
          <BaseSelect.Label className="f-label">{label}</BaseSelect.Label>
          {trigger}
        </div>
      )}
      <BaseSelect.Portal>
        <BaseSelect.Positioner className={popLayer} alignItemWithTrigger={false} align="start" sideOffset={6}>
          <BaseSelect.Popup className={`menu is-select ${popMotion}`}>
            {options.map((o) => (
              <BaseSelect.Item
                key={o.value}
                value={o.value}
                label={o.label}
                disabled={o.disabled}
                render={<a />}
                className={(state) => [state.selected && 'is-on', o.disabled && 'is-disabled'].filter(Boolean).join(' ')}
              >
                <MenuItemBody label={o.label} meta={o.meta} on={o.value === value} />
              </BaseSelect.Item>
            ))}
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  );
}
