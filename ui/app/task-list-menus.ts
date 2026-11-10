/* ui/app/task-list-menus.ts — the one popover the Tasks page's menus share (the view button, the filter and its values),
   which other pages borrow too (the roadmap's, a bot's goals), and the small icons and the phone width the Tasks page and
   its neighbours draw with.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A menu's contents are wired by its caller once they are drawn in the shared popover (tasksMenu).
type TasksMenuBind = (pop: HTMLElement) => void;
// The page whose state keeps the open menu: the Tasks page's, or another page's that borrows the menu (the roadmap's).
type TasksMenuHost = {menuAnchor?: HTMLElement | null | undefined; menuField?: string | undefined};
const isTasksRoute = (r: string | null | undefined) => String(r || '').split('?')[0] === TASKS;
const PHONE = '(max-width:760px)';
// Small inline icons (the icon font is a fixed subset; these never need it).
const TL_ICON = {
  chev: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M4.5 3l3 3-3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M6 2v8M2 6h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  x: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 3l6 6M9 3l-6 6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  filter: filterIcon(13),
  search: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="6.2" cy="6.2" r="4.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9.3 9.3L12.3 12.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  chat: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9v6h-5l-2.5 2v-2h-1.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
  full: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M8.5 2.5h3v3M5.5 11.5h-3v-3M11.5 2.5L8 6M2.5 11.5L6 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  up: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 7.5l3-3 3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  team: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="5" cy="5" r="2" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="9.6" cy="5.6" r="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.8 11.2c.5-2 1.7-3 3.2-3s2.7 1 3.2 3M8.4 8.6c1.6-.4 3 .5 3.6 2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  note: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M3 2.5h8v6.5l-2.5 2.5H3z M8.5 11.5V9h2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  group: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9M4.5 7h7M4.5 10.5h7M2.5 7h.01M2.5 10.5h.01" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  chart: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M2 12h10M4 10V7M7 10V3.5M10 10V5.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  dots: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="3" cy="7" r="1.25" fill="currentColor"/><circle cx="7" cy="7" r="1.25" fill="currentColor"/><circle cx="11" cy="7" r="1.25" fill="currentColor"/></svg>',
  pin: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><path d="M5 1.8h4M5.6 1.8v3.6L3.6 8h6.8L8.4 5.4V1.8M7 8v4.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  caret: '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  list: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><path d="M2.5 3.5h9M2.5 7h9M2.5 10.5h9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  board: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.2" width="2.9" height="9.6" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="5.55" y="2.2" width="2.9" height="6.6" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="9.3" y="2.2" width="2.9" height="8.1" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  screen: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.2" width="10.4" height="7.6" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.2 12.2h3.6M7 9.8v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  archive: '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x="1.8" y="2.3" width="10.4" height="3" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2.8 5.3v5.4a1 1 0 0 0 1 1h6.4a1 1 0 0 0 1-1V5.3M5.6 7.8h2.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  all: '<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" focusable="false"><rect x="2" y="2" width="10" height="10" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4.6 7.2l1.7 1.6 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
// One popover for every menu on the page: a filter's values, the fields, Group by, and the bulk actions.
// ↑/↓ move between its items (and in and out of its find box), Home/End jump, Enter picks, Esc closes.
function tasksMenu(state: TasksMenuHost, anchor: HTMLElement | null | undefined, html: string, bind?: TasksMenuBind, toggle = true) {
  const pop = $('#task-filter-pop');
  if (!pop) return;
  if (toggle && pop.matches(':popover-open') && state.menuAnchor === anchor) { pop.hidePopover(); return; }
  state.menuAnchor = anchor;
  state.menuField = '';
  pop.innerHTML = html;
  pop.onclick = pop.onchange = null;
  bind?.(pop);
  if (!pop.matches(':popover-open')) pop.showPopover();
  tasksMenuPlace(pop, anchor);
  (pop.querySelector('input[type=search], input[type=text]') || pop.querySelector('[aria-checked="true"], input:checked') || pop.querySelector('.tl-mi, .tl-opt input') || pop).focus();
}
function tasksMenuKeys(ev: KeyboardEvent) {
  const pop = ev.currentTarget as HTMLElement;      // the popover the keys are bound on
  const items: (HTMLElement | null)[] = [...pop.querySelectorAll<HTMLElement>('.tl-mi, .tl-opt input, .tl-menu-q, .tl-menu-new input')].filter(el => !el.closest('[hidden]'));
  const active = document.activeElement as HTMLInputElement | null;     // its type is read only to tell a text box
  const at = items.indexOf(active);
  const go = (i: number) => { ev.preventDefault(); items[Math.max(0, Math.min(items.length - 1, i))]?.focus(); };
  if (ev.key === 'ArrowDown') go(at + 1);
  else if (ev.key === 'ArrowUp') go(at - 1);
  else if (ev.key === 'Home' && !/^(search|text)$/.test(active?.type || '')) go(0);
  else if (ev.key === 'End' && !/^(search|text)$/.test(active?.type || '')) go(items.length - 1);
  else if (ev.key === 'Enter' && active?.matches('.tl-opt input')) { ev.preventDefault(); active.click(); }
}
function tasksMenuPlace(pop: HTMLElement, anchor: HTMLElement | null | undefined) {
  if (matchMedia(PHONE).matches || !anchor?.isConnected) { pop.style.top = pop.style.left = ''; return; }
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
}
function tasksMenuClose() { const pop = $('#task-filter-pop'); if (pop?.matches(':popover-open')) pop.hidePopover(); }
