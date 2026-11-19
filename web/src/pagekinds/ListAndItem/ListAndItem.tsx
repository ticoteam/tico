import { Link, type LinkProps } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { FillContext } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { EditableTitle, headFills, PageHead, PageTabs, SkRows, StateBody } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface ListAndItemProps extends PageBaseProps {
  /** At the top of the list column, under the head: the search field (`label.search.search-wide`). */
  search?: ReactNode;
  /** The tab row, under the search, in the list column. */
  tabs?: readonly PageTab[];
  /** At the tab row's end: the controls that act on the open tab. */
  tabsAfter?: ReactNode;
  /** The rows (`.tl-row`). They scroll in the column. */
  list: ReactNode;
  /** Names the list column ("Threads"). */
  listLabel?: string;
  /** The open item's head (`<ItemHead>`). */
  itemHead?: ReactNode;
  /** The open item, in its own scroller in the reading column. */
  item?: ReactNode;
  /** A conversation that brings its own scroller and composer (`@/conversation`); stands in for `item` and `foot`. */
  conversation?: ReactNode;
  /** Under the item, outside the scroller: the composer, the item's buttons. */
  foot?: ReactNode;
  /** Names the item column. */
  itemLabel?: string;
  /** An item is open. A phone shows the list until then, and the item alone after. */
  open?: boolean;
}

/** A list column on the left, the open item filling the rest. A phone shows the list, then the item. */
export function ListAndItem({
  search,
  tabs,
  tabsAfter,
  list,
  listLabel,
  itemHead,
  item,
  conversation,
  foot,
  itemLabel = 'Open item',
  open = false,
  className,
  state = 'ready',
  empty,
  onRetry,
  noAccess,
  ...head
}: ListAndItemProps) {
  const cls = `pane page page-thread pk pk-split ${open ? 'is-open' : 'is-list'}${className ? ` ${className}` : ''}`;
  if (state === 'no-access') {
    return (
      <main className={cls} aria-label={head.title}>
        <StateBody state={state} title={head.title} noAccess={noAccess} skeleton={null}>
          {null}
        </StateBody>
      </main>
    );
  }
  return (
    <main className={cls} aria-label={head.title}>
      <section className="tl" aria-label={listLabel ?? head.title}>
        <FillContext value={headFills({ state, empty, onRetry })}>
          <PageHead {...head} />
          {search}
          {tabs && tabs.length > 0 && <PageTabs tabs={tabs} label={`${head.title} pages`} after={tabsAfter} />}
        </FillContext>
        <StateBody
          state={state}
          title={head.title}
          empty={empty}
          onRetry={onRetry}
          skeleton={
            <div className="tl-list" aria-hidden="true">
              <SkRows rows={9} />
            </div>
          }
        >
          <div className="tl-list" data-scroll>
            {list}
          </div>
        </StateBody>
      </section>
      {/* In every state, as the mocks draw it: the column stays while the list loads or is empty. */}
      <section className="conv" aria-label={itemLabel}>
        {itemHead}
        {conversation ?? (
          <>
            <div className="conv-body" data-scroll>
              <div className="conv-col">{item}</div>
            </div>
            {foot && (
              <footer className="conv-foot">
                <div className="conv-col">{foot}</div>
              </footer>
            )}
          </>
        )}
      </section>
    </main>
  );
}

export interface ItemHeadProps {
  title: string;
  /** `<kind>:<id>` for Tico's flights. */
  target?: string;
  /** Where a phone's back button goes: the list. Shown only on a phone. */
  back?: { to: string; label: string };
  /** Before the title: a face (36 at most). */
  lead?: ReactNode;
  /** Right of the title, before "⋯". */
  actions?: ReactNode;
  /** "⋯": a menu node, or a function the plain "⋯" button calls. */
  more?: ReactNode | (() => void);
  /**
   * The title renames in place: a click on it turns it into a field on the same line; Enter or leaving it saves (when
   * the words changed), Escape keeps the old name.
   */
  onRename?: (title: string) => void;
  /** Start renaming from outside (a menu's Rename); `onRenamingChange` hears when it starts and ends. */
  renaming?: boolean;
  onRenamingChange?: (renaming: boolean) => void;
  /** Names the field ("Thread name"). */
  renameLabel?: string;
}

/** The open item's head, one bar tall, its title in one line. */
export function ItemHead({ title, target, back, lead, actions, more, onRename, renaming, onRenamingChange, renameLabel = 'Name' }: ItemHeadProps) {
  const [own, setOwn] = useState(false);
  const editing = !!onRename && (renaming ?? own);
  const setEditing = (on: boolean) => {
    setOwn(on);
    onRenamingChange?.(on);
  };
  if (editing)
    return (
      <header className="conv-head">
        {lead}
        <EditableTitle
          title={title}
          label={renameLabel}
          onDone={(next) => {
            setEditing(false);
            if (next && next !== title) onRename(next);
          }}
        />
      </header>
    );
  return (
    <header className="conv-head">
      {back && (
        <Link className="ib back" to={back.to as LinkProps['to']} aria-label={`Back to ${back.label}`}>
          <Icon name="chevron" className="flip" />
        </Link>
      )}
      {lead}
      <h2 data-target={target} title={title} className={onRename ? 'is-editable' : undefined} onClick={onRename ? () => setEditing(true) : undefined}>
        {title}
      </h2>
      <span className="grow" />
      {actions}
      {typeof more === 'function' ? <IconButton icon="more" label="More" onClick={more} /> : more}
    </header>
  );
}
