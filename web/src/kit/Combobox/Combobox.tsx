import type { ReactNode } from 'react';
import { Combobox as BaseCombobox } from '@base-ui/react/combobox';
import { MenuItemBody } from '@/kit/Menu';
import { popLayer, popMotion } from '@/kit/Popover';
import './Combobox.css';

export type PickItem = {
  value: string;
  label: string;
  /** An avatar or a mark before the name. */
  lead?: ReactNode;
  /** A quiet note at the right, such as a model's price. */
  meta?: string;
  disabled?: boolean;
};

export type ComboboxProps = {
  label: string;
  labelHidden?: boolean;
  items: readonly PickItem[];
  value: string | null;
  onValueChange: (value: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
  error?: boolean;
};

/** A picker you can type into: people, agents, models. */
export function Combobox({ label, labelHidden = false, items, value, onValueChange, placeholder, disabled = false, error = false }: ComboboxProps) {
  const chosen = items.find((i) => i.value === value) ?? null;
  const field = (
    <BaseCombobox.InputGroup
      className={['field is-wide', error && 'is-error', disabled && 'is-disabled'].filter(Boolean).join(' ')}
    >
      {chosen?.lead}
      <BaseCombobox.Input placeholder={placeholder} aria-label={label} />
    </BaseCombobox.InputGroup>
  );
  return (
    <BaseCombobox.Root
      items={items as PickItem[]}
      value={chosen}
      onValueChange={(next: PickItem | null) => onValueChange(next ? next.value : null)}
      itemToStringLabel={(item: PickItem) => item.label}
      isItemEqualToValue={(a: PickItem, b: PickItem) => a.value === b.value}
      disabled={disabled}
    >
      {labelHidden ? (
        field
      ) : (
        <div className="f">
          <BaseCombobox.Label className="f-label">{label}</BaseCombobox.Label>
          {field}
        </div>
      )}
      <BaseCombobox.Portal>
        <BaseCombobox.Positioner className={popLayer} align="start" sideOffset={6}>
          <BaseCombobox.Popup className={`menu is-select kit-pick ${popMotion}`}>
            <BaseCombobox.Empty className="sr-none">No matches</BaseCombobox.Empty>
            <BaseCombobox.List>
              {(item: PickItem) => (
                <BaseCombobox.Item
                  key={item.value}
                  value={item}
                  disabled={item.disabled}
                  render={<a />}
                  className={(state) => [state.selected && 'is-on', item.disabled && 'is-disabled'].filter(Boolean).join(' ')}
                >
                  <MenuItemBody label={item.label} lead={item.lead} meta={item.meta} on={item.value === value} />
                </BaseCombobox.Item>
              )}
            </BaseCombobox.List>
          </BaseCombobox.Popup>
        </BaseCombobox.Positioner>
      </BaseCombobox.Portal>
    </BaseCombobox.Root>
  );
}
