import { createContext, use, type ReactNode } from 'react';
import { IconButton } from '@/kit/IconButton';
import type { SheetId, SheetState } from './layout';

/* What a sheet's content may ask of the frame it sits in. Outside a Frame (tests, pictures) every sheet is open. */
export interface SheetControls {
  id: SheetId | null;
  state: SheetState;
  /* open over its neighbours for now (layout.md 5.2) */
  peek: boolean;
  /* shown as its folded form: Tico's strip, the menu's icons */
  folded: boolean;
  canFold: boolean;
  toggleFold: () => void;
  close?: () => void;
  /* sheets out of the row that wait behind a button in this sheet's head (only the stretching sheet has them) */
  tucked: { id: SheetId; label: string; open: () => void }[];
}

const NONE: SheetControls = { id: null, state: 'open', peek: false, folded: false, canFold: false, toggleFold: () => {}, tucked: [] };
export const SheetContext = createContext<SheetControls>(NONE);
export const useSheet = () => use(SheetContext);

const FOLD_LABEL: Partial<Record<SheetId, string>> = { tico: 'Fold Tico', menu: 'Fold the menu', list: 'Fold the list', widgets: 'Hide widgets', side: 'Hide the side' };
const CLOSE_LABEL: Partial<Record<SheetId, string>> = { chat: 'Close chat', panel: 'Close' };

/* The sheet itself, with the mock's element and classes for each kind. */
type Kind = 'tico' | 'page' | 'chat' | 'panel' | 'list' | 'column';
const KIND: Record<Kind, { tag: 'aside' | 'main' | 'nav'; cls: string }> = {
  tico: { tag: 'aside', cls: 'pane tico' },
  page: { tag: 'main', cls: 'pane page' },
  chat: { tag: 'aside', cls: 'pane side chat chat-2' },
  panel: { tag: 'aside', cls: 'pane side' },
  list: { tag: 'nav', cls: 'pane nav list' },
  column: { tag: 'aside', cls: 'pane page' },
};

export interface SheetProps {
  kind: Kind;
  label: string;
  className?: string;
  children?: ReactNode;
}

export function Sheet({ kind, label, className, children }: SheetProps) {
  const { tag: Tag, cls } = KIND[kind];
  return (
    <Tag className={className ? `${cls} ${className}` : cls} aria-label={label}>
      {children}
    </Tag>
  );
}

export interface SheetHeadProps {
  /* 'page' is a page's head with an h1; 'side' a chat's, panel's or list's; 'tico' Tico's own */
  kind: 'page' | 'side' | 'tico';
  title: ReactNode;
  /* a quiet line under a side head's title (which chat this is) */
  sub?: ReactNode;
  /* before the title: a face, the Dot, a back link */
  lead?: ReactNode;
  /* the head's controls, right of the title */
  children?: ReactNode;
  /* "⋯": a function draws the button and calls it; a node (a menu with its own trigger) is placed as is */
  more?: ReactNode | (() => void);
  className?: string;
}

/* A sheet's head: lead, title, controls, "⋯", then the fold or close control the frame gives this sheet,
   and a button for each sheet tucked behind this one. */
export function SheetHead({ kind, title, sub, lead, children, more, className }: SheetHeadProps) {
  const sheet = useSheet();
  const id = sheet.id;
  const cls = `${kind}-head${className ? ` ${className}` : ''}`;
  return (
    <header className={cls}>
      {lead}
      {kind === 'page' ? (
        <h1>{title}</h1>
      ) : sub ? (
        <span className="head-who">
          <b>{title}</b>
          {sub}
        </span>
      ) : (
        <b>{title}</b>
      )}
      <span className="grow" />
      {children}
      {typeof more === 'function' ? <IconButton icon="more" label="More" onClick={more} /> : more}
      {sheet.tucked.map(t => (
        <IconButton key={t.id} icon="panel" label={t.label} quiet onClick={t.open} />
      ))}
      {id && sheet.canFold && !sheet.folded ? <IconButton icon="panel" label={FOLD_LABEL[id] ?? 'Fold'} quiet onClick={sheet.toggleFold} /> : null}
      {id && sheet.close ? <IconButton icon="close" label={CLOSE_LABEL[id] ?? 'Close'} quiet onClick={sheet.close} /> : null}
    </header>
  );
}
