import type { ReactNode } from 'react';
import { Tabs as BaseTabs } from '@base-ui/react/tabs';
import './Tabs.css';

export type TabItem<V extends string = string> = {
  value: V;
  label: string;
  /** Before the words: an icon or a hollow ring. */
  lead?: ReactNode;
  /** After the words: a count. */
  after?: ReactNode;
  /** Nothing here yet: the tab reads quieter. */
  none?: boolean;
  disabled?: boolean;
};

export type TabsProps<V extends string = string> = {
  /** Names the tab row for a screen reader. */
  label: string;
  tabs: readonly TabItem<V>[];
  value: V;
  onValueChange: (value: V) => void;
  /** The page's own tab row under its head, with the rule across the sheet. */
  strip?: boolean;
};

/** One tab row, underlined. Content under it is the caller's. */
export function Tabs<V extends string>({ label, tabs, value, onValueChange, strip = false }: TabsProps<V>) {
  const row = (
    <BaseTabs.List className="docs" aria-label={label}>
      {tabs.map((t) => (
        <BaseTabs.Tab
          key={t.value}
          value={t.value}
          disabled={t.disabled}
          nativeButton={false}
          render={<a />}
          className={(state) => ['doc-tab', state.active && 'is-on', t.none && 'is-none'].filter(Boolean).join(' ')}
        >
          {t.lead}
          <span data-t={t.label}>{t.label}</span>
          {t.after}
        </BaseTabs.Tab>
      ))}
    </BaseTabs.List>
  );
  return (
    <BaseTabs.Root value={value} onValueChange={(v) => onValueChange(v as V)}>
      {strip ? <div className="page-tabs">{row}</div> : row}
    </BaseTabs.Root>
  );
}
