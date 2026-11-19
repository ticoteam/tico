import type { ComponentProps, ReactElement, ReactNode } from 'react';
import { ContextMenu as BaseContextMenu } from '@base-ui/react/context-menu';
import { Menu as BaseMenu } from '@base-ui/react/menu';
import { Icon } from '@/kit/Icon';
import { popLayer, popMotion } from '@/kit/Popover';
import './Menu.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type MenuProps = {
  /** The button that opens it, usually `<IconButton icon="more" label="More" />`. */
  trigger: ReactElement;
  /** `MenuItem`s and `MenuRule`s. A destructive item comes last, under a rule, and names the thing. */
  children: ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
  align?: 'start' | 'center' | 'end';
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
};

/** The "⋯" menu: a short list that opens from a button. */
export function Menu({ trigger, children, side = 'bottom', align = 'end', open, onOpenChange }: MenuProps) {
  return (
    <BaseMenu.Root open={open} onOpenChange={onOpenChange ? (next) => onOpenChange(next) : undefined}>
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner className={popLayer} side={side} align={align} sideOffset={6}>
          <BaseMenu.Popup className={`menu ${popMotion}`}>{children}</BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}

export type ContextMenuProps = {
  /** What a right click or a long press opens the menu on. */
  children: ReactElement;
  /** `MenuItem`s and `MenuRule`s. */
  items: ReactNode;
};

/** The same menu, opened by a right click or a long press on a row. */
export function ContextMenu({ children, items }: ContextMenuProps) {
  return (
    <BaseContextMenu.Root>
      <BaseContextMenu.Trigger render={children} />
      <BaseContextMenu.Portal>
        <BaseContextMenu.Positioner className={popLayer}>
          <BaseContextMenu.Popup className={`menu ${popMotion}`}>{items}</BaseContextMenu.Popup>
        </BaseContextMenu.Positioner>
      </BaseContextMenu.Portal>
    </BaseContextMenu.Root>
  );
}

export type MenuItemBodyProps = {
  label: string;
  icon?: IconName;
  /** In place of an icon: an avatar or a mark. */
  lead?: ReactNode;
  /** A quiet note at the right, such as a model's price. */
  meta?: string;
  /** The chosen one: a check at the right. */
  on?: boolean;
};

/** What a menu row shows. Select and Combobox rows use it too. */
export function MenuItemBody({ label, icon, lead, meta, on = false }: MenuItemBodyProps) {
  return (
    <>
      {icon ? <Icon name={icon} /> : lead}
      <span>{label}</span>
      {meta && <em>{meta}</em>}
      {on && <Icon name="check" />}
    </>
  );
}

export type MenuItemProps = MenuItemBodyProps & {
  onSelect: () => void;
  disabled?: boolean;
};

export function MenuItem({ label, icon, lead, meta, on = false, onSelect, disabled = false }: MenuItemProps) {
  return (
    <BaseMenu.Item
      render={<a />}
      label={label}
      disabled={disabled}
      className={[on && 'is-on', disabled && 'is-disabled'].filter(Boolean).join(' ') || undefined}
      onClick={() => onSelect()}
    >
      <MenuItemBody label={label} icon={icon} lead={lead} meta={meta} on={on} />
    </BaseMenu.Item>
  );
}

/** The rule above the last, destructive item. */
export function MenuRule() {
  return <BaseMenu.Separator render={<hr />} />;
}
