import { Link, type LinkProps } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { PageHead, PageTabs, SkRows, StateBody } from '@/pagekinds/_parts';
import type { PageBaseProps, PageTab } from '@/pagekinds/types';

export interface ListAndItemProps extends PageBaseProps {
  /** At the top of the list column, under the head: the search field (`label.search.search-wide`). */
  search?: ReactNode;
  /** The tab row, under the search, in the list column. */
  tabs?: readonly PageTab[];
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
        <PageHead {...head} />
        {state === 'ready' && search}
        {tabs && tabs.length > 0 && <PageTabs tabs={tabs} label={`${head.title} pages`} />}
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
      {state === 'ready' && (
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
      )}
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
}

/** The open item's head, one bar tall, its title in one line. */
export function ItemHead({ title, target, back, lead, actions, more }: ItemHeadProps) {
  return (
    <header className="conv-head">
      {back && (
        <Link className="ib back" to={back.to as LinkProps['to']} aria-label={`Back to ${back.label}`}>
          <Icon name="chevron" className="flip" />
        </Link>
      )}
      {lead}
      <h2 data-target={target} title={title}>
        {title}
      </h2>
      <span className="grow" />
      {actions}
      {typeof more === 'function' ? <IconButton icon="more" label="More" onClick={more} /> : more}
    </header>
  );
}
