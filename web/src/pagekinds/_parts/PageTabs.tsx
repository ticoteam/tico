import { createLink, useLocation, type LinkProps } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useRef, type AnchorHTMLAttributes, type KeyboardEvent, type ReactNode, type Ref } from 'react';
import { Count } from '@/kit/Count';
import type { PageTab } from '@/pagekinds/types';
import { rowMarks, scrollFor, stepTab } from './tabFit';

export interface PageTabsProps {
  tabs: readonly PageTab[];
  /** Names the row for a screen reader; the page's title by default. */
  label: string;
  /** At the row's end: the controls that act on the open tab (a version, Add). */
  after?: ReactNode;
}

const trim = (p: string) => (p.length > 1 ? p.replace(/\/+$/, '') : p);

/**
 * On at its address (or under it, when not exact), and while the address's search matches the tab's own for every key
 * the row's tabs use: a tab with no search of its own (Overview) is off once a sibling's `?tab=` is in the address.
 */
function isOn(t: PageTab, path: string, search: Record<string, unknown>, rowKeys: readonly string[]): boolean {
  const here = trim(path);
  const there = trim(t.to.split('?')[0] ?? t.to);
  const pathOn = (t.exact ?? true) ? here === there : here === there || here.startsWith(`${there}/`);
  return pathOn && rowKeys.every((k) => String(search[k] ?? '') === (t.search?.[k] ?? ''));
}

/* The router marks a link current by its own test, in which a tab with no search of its own (Overview) still matches
   while a sibling's `?at=` is open. The row decides alone: the anchor drops the router's marks and takes `on`, and no
   `active` class is added beside `is-on`. */
const NO_ACTIVE = {};
type TabAnchorProps = AnchorHTMLAttributes<HTMLAnchorElement> & { on: boolean; ref?: Ref<HTMLAnchorElement>; 'data-status'?: string };
function TabAnchor({ on, 'aria-current': _current, 'data-status': _status, ...rest }: TabAnchorProps) {
  return <a {...rest} aria-current={on ? 'page' : undefined} />;
}
const TabLink = createLink(TabAnchor);

const isPhone = () => document.documentElement.dataset.layout === 'cards';

/** Sets the row's fade marks from where it is scrolled (the classes the mock's finance.js toggles). */
function mark(row: HTMLElement) {
  const m = rowMarks(row, isPhone());
  row.classList.toggle('is-long', m.long);
  row.classList.toggle('is-scrolled', m.scrolled);
  row.classList.toggle('is-end', m.end);
}

/**
 * The one tab row: underlined, right under the head, one line that scrolls sideways and never wraps. Never the
 * segmented pill (aaa-audit-a Top 5); a pill only switches the view of the same data. Each tab is a link. A row wider
 * than its sheet fades where it runs off, and the open tab is scrolled into view.
 */
export function PageTabs({ tabs, label, after }: PageTabsProps) {
  const at = useLocation();
  const search = at.search as Record<string, unknown>;
  const rowKeys = [...new Set(tabs.flatMap((t) => Object.keys(t.search ?? {})))];
  const keyOf = (t: PageTab) => `${t.to}?${new URLSearchParams(t.search).toString()}`;
  const onKey = tabs.filter((t) => isOn(t, at.pathname, search, rowKeys)).map(keyOf)[0] ?? '';
  const row = useRef<HTMLDivElement>(null);

  // the open tab in view, each time it changes
  useLayoutEffect(() => {
    const el = row.current;
    if (!el) return;
    const on = el.querySelector<HTMLElement>('.doc-tab.is-on');
    const box = el.getBoundingClientRect();
    const tab = on?.getBoundingClientRect();
    const span = tab ? { left: tab.left - box.left + el.scrollLeft, right: tab.right - box.left + el.scrollLeft } : null;
    const to = scrollFor(el, span, isPhone());
    if (to !== null) el.scrollLeft = to;
    mark(el);
  }, [onKey]);

  // the fades follow the sheet's width and the person's scrolling
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    const update = () => mark(el);
    el.addEventListener('scroll', update, { passive: true });
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    ro?.observe(el);
    return () => {
      el.removeEventListener('scroll', update);
      ro?.disconnect();
    };
  }, []);

  const walk = (e: KeyboardEvent<HTMLDivElement>) => {
    const links = [...e.currentTarget.querySelectorAll<HTMLElement>('.doc-tab')];
    const next = stepTab(links.indexOf(e.target as HTMLElement), links.length, e.key);
    if (next === null || !links.includes(e.target as HTMLElement)) return;
    e.preventDefault();
    links[next]?.focus();
  };

  return (
    <nav className={after ? 'page-tabs pk-tabs has-after' : 'page-tabs pk-tabs'} aria-label={label}>
      <div className="docs" ref={row} onKeyDown={walk}>
        {tabs.map((t) => {
          const on = keyOf(t) === onKey;
          return (
            <TabLink
              key={keyOf(t)}
              className={on ? 'doc-tab is-on' : 'doc-tab'}
              on={on}
              activeProps={NO_ACTIVE}
              to={t.to as LinkProps['to']}
              search={t.search as never}
              data-target={t.target}
            >
              {t.mark}
              <span data-t={t.label}>{t.label}</span>
              {t.count !== undefined && <Count n={t.count} />}
            </TabLink>
          );
        })}
      </div>
      {after}
    </nav>
  );
}
