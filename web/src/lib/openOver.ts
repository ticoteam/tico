import type { AnyRouter } from '@tanstack/react-router';
import { OVER_PAGE } from './places';
import { coldOf, openOf, withOpen, type TaskWords } from './taskAddress';

/* A task opens over the page you are on (plan: the task modal). Every way in goes through `router.navigate` (a link, a
   card, an action, Tico's open): one to a task's own address (`/task/<n>`, `/tasks/<p>/<n>/<tab>`) opens it over this
   page instead, so its links stay its own (a new tab, a copied link, a cold load: its route opens it over its board).
   Moving to another task while one is open replaces it, so one Back closes it. The list it opens with is the one its
   link was clicked in (`data-list` on the list: a board's pipeline, an agent's id, empty for Needs you); clicked
   anywhere else, the list already open, else Needs you. */

let clickedList: string | undefined;

/** Hear which list each click is in, for the navigation it starts (capture first, forgotten once it is done). */
const hearing = new WeakSet<Document>();
export function hearListClicks(doc: Document = document): void {
  if (hearing.has(doc)) return;
  hearing.add(doc);
  doc.addEventListener(
    'click',
    (e) => {
      const at = e.target instanceof Element ? e.target.closest('[data-list]') : null;
      clickedList = at ? (at.getAttribute('data-list') ?? '') : undefined;
    },
    true,
  );
  doc.defaultView?.addEventListener('click', () => (clickedList = undefined));
}

/** The words a task's code reads as its own search (`ask`, `v`, `session`): the open task's, its tab in its path. */
const plainWords = ({ tab: _, ...rest }: TaskWords): Record<string, unknown> => rest;

type Navigate = AnyRouter['navigate'];
type Opts = Parameters<Navigate>[0] & { search?: unknown; href?: string; replace?: boolean };

/** `r.navigate`, opening a task's address over the page it is on. */
const opening = new WeakSet<AnyRouter>();
export function openTasksOver(r: AnyRouter): void {
  if (opening.has(r)) return;
  opening.add(r);
  const go: Navigate = r.navigate;
  r.navigate = ((opts: Opts) => {
    const here = r.latestLocation;
    if (!opts || opts.reloadDocument || (opts.href && /^[a-z][a-z\d+.-]*:/i.test(opts.href)) || coldOf(here.pathname)) return go(opts);
    let to;
    try {
      to = r.buildLocation(opts as never);
    } catch {
      return go(opts);
    }
    if (!coldOf(to.pathname)) return go(opts);
    const open = openOf(here.search as Record<string, unknown>);
    // the task's own code writes its words as its old address had them: read against the open task's, never the page's
    const plain = open ? plainWords(open.words) : {};
    const own = typeof opts.search === 'function' ? (opts.search as (p: unknown) => unknown)(plain) : opts.search === true ? plain : undefined;
    if (own !== undefined) to = r.buildLocation({ ...(opts as object), search: own } as never);
    const cold = coldOf(to.pathname, to.search as Record<string, unknown>)!;
    const list = clickedList !== undefined ? clickedList || undefined : open?.list;
    return go({
      to: here.pathname,
      search: withOpen(here.search as Record<string, unknown>, { task: cold.task, list, words: cold.words }),
      hash: here.hash || undefined,
      replace: !!open || !!opts.replace,
      // the step that opened it over the page: closing steps back to it (lib closeTask); kept as tasks replace it
      state: { [OVER_PAGE]: open ? !!(here.state as unknown as Record<string, unknown> | undefined)?.[OVER_PAGE] : !opts.replace },
      resetScroll: false,
    } as never);
  }) as Navigate;
}
