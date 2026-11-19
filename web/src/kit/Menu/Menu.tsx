import type { ComponentProps, ReactElement, ReactNode } from 'react';
import { ContextMenu as BaseContextMenu } from '@base-ui/react/context-menu';
import { Menu as BaseMenu } from '@base-ui/react/menu';
import { Icon } from '@/kit/Icon';
import { sheetScrim, usePlacement } from '@/kit/Popover';
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

/** The "⋯" menu: a short list that opens from a button. On a phone it is a sheet from the bottom edge, over a scrim. */
export function Menu({ trigger, children, side = 'bottom', align = 'end', open, onOpenChange }: MenuProps) {
  const at = usePlacement();
  return (
    <BaseMenu.Root open={open} onOpenChange={onOpenChange ? (next) => onOpenChange(next) : undefined}>
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        {at.sheet && <BaseMenu.Backdrop className={sheetScrim} />}
        <BaseMenu.Positioner className={at.layer} style={at.style} side={side} align={align} sideOffset={6}>
          <BaseMenu.Popup className={`menu ${at.look}`}>{children}</BaseMenu.Popup>
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
  const at = usePlacement();
  return (
    <BaseContextMenu.Root>
      <BaseContextMenu.Trigger render={children} />
      <BaseContextMenu.Portal>
        {at.sheet && <BaseContextMenu.Backdrop className={sheetScrim} />}
        <BaseContextMenu.Positioner className={at.layer} style={at.style}>
          <BaseContextMenu.Popup className={`menu ${at.look}`}>{items}</BaseContextMenu.Popup>
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

export type MenuSubProps = {
  label: string;
  icon?: IconName;
  /** `MenuItem`s: the second level. */
  children: ReactNode;
};

/** A row that opens a second level beside the menu, on the side with room (list.css `.menu a>.ico.is-next`). On a
    phone the second level is a sheet over the first, under the same scrim. */
export function MenuSub({ label, icon, children }: MenuSubProps) {
  const at = usePlacement();
  return (
    <BaseMenu.SubmenuRoot>
      <BaseMenu.SubmenuTrigger render={<a />}>
        {icon && <Icon name={icon} />}
        <span>{label}</span>
        <Icon name="chevron" className="is-next" />
      </BaseMenu.SubmenuTrigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner className={at.layer} style={at.style} side="left" align="start" sideOffset={6}>
          <BaseMenu.Popup className={`menu is-select ${at.look}`}>{children}</BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.SubmenuRoot>
  );
}
