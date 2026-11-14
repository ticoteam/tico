// The one page header every top-level screen draws (spec: "One page header on every screen", task #338; style guide
// "Top bars: one line"): the title at the content's left edge, tabs on the title line in one look, quiet tools, the one
// filled button (or a quiet +), then ⋯, last at the far right. It keeps itself to one line for every caller: tabs past
// the fifth go into ⋯, and when the row is tight a tool's word becomes its icon, then the tools go into ⋯, then the
// last tabs do. On a phone the tabs take a row of their own under the title line, by the same rules.
// Styles: ui/styles/page-head.css, linked after every page stylesheet so it wins ties with the old page rules.
'use strict';

type PageTab = {label: string; href?: string | undefined; count?: number | string | undefined; cur?: boolean | undefined; attrs?: string | undefined};
type PageHeadPart = 'title' | 'tabs' | 'tools' | 'more' | 'primary';
type PageHeadOptions = {
  title: string;                           // plain text, escaped here
  back?: {href: string; label: string} | undefined;    // a sub-page's parent, drawn first as "Updates ›"
  hook?: string | undefined; // the old header class, kept only as a test hook (au-ph, ru-ph…)
  pad?: boolean | undefined; // .ph-pad: the 28px gutter on layouts that zero #main's padding
  id?: string | undefined; // the header's id, where a page reads it back
  after?: string | undefined; // HTML right after the title (Tasks' scope menu)
  tabs?: PageTab[] | undefined;
  tabsHook?: string | undefined; // the old nav class kept as a hook (ru-tabs, use-tabs…)
  tabsAttrs?: string | undefined; // extra attributes on the nav (aria-label, data-*)
  tools?: string | undefined; // search, ghost and icon buttons, chips
  more?: string | undefined; // the ⋯ menu's items (buttons and links)
  moreId?: string | undefined; // ids of the ⋯ button and its menu, where a page already names them
  menuId?: string | undefined;
  primary?: string | undefined; // the filled button: <button class="primary ph-go" …>Words</button>
  add?: {label: string; attrs?: string | undefined} | undefined; // a quiet + where adding is rare, in the filled button's place
  actId?: string | undefined; // id of the .ph-act holder, where a page fills it later (#strat-actions)
};
// Tabs on the row at most (style guide: "Views of one thing are tabs: one row, five at most"); the rest go into ⋯.
const PAGE_HEAD_TABS_MAX = 5;
// ⋯ is on every header that has tools, so it is always the last control (style guide: "The ⋯ menu is last, at the far
// right, on every page"). A page that gives it nothing of its own still has this one item, so ⋯ never opens empty.
const PAGE_HEAD_COPY = '<button type="button" class="ph-copy">Copy link</button>';

let PAGE_HEAD_SEQ = 0;

// One tab: a link carries aria-current="page", a button is a tab with aria-selected.
function pageHeadTab(t: PageTab): string {
  const n = t.count === undefined || t.count === '' ? '' : `<span class="ph-n">${esc(String(t.count))}</span>`;
  const extra = t.attrs ? ' ' + t.attrs : '';
  const cls = 'ph-tab' + (t.cur ? ' cur' : '');
  return t.href !== undefined
    ? `<a class="${cls}" href="${esc(t.href)}"${t.cur ? ' aria-current="page"' : ''}${extra}>${esc(t.label)}${n}</a>`
    : `<button type="button" class="${cls}" role="tab" aria-selected="${t.cur ? 'true' : 'false'}"${extra}>${esc(t.label)}${n}</button>`;
}
function pageHeadTabs(tabs: PageTab[]): string { return tabs.map(pageHeadTab).join(''); }

// The quiet + (style guide: "A page whose adding is rare has a quiet + there instead"): an icon button the size of the
// row's controls, named for screen readers and on hover. Pages pass `add` to pageHead, or this to pageHeadSet('primary').
function pageHeadAdd(label: string, attrs = ''): string {
  return `<button type="button" class="ghost ph-add" aria-label="${esc(label)}" title="${esc(label)}"${attrs ? ' ' + attrs : ''}><span class="nav-icon" aria-hidden="true">add</span></button>`;
}

// ⋯ and its list. The list is a named group of plain buttons and links, not an ARIA menu: it can also hold the folded
// tools (a search box, a switch), which a menu may not. Items in it are buttons (aria-pressed for a choice that is on).
function pageHeadMore(items: string, moreId: string, menuId: string): string {
  return `<button type="button" class="ph-more" id="${esc(moreId)}" popovertarget="${esc(menuId)}" aria-label="More" title="More" aria-expanded="false">`
    + `<span class="nav-icon" aria-hidden="true">more_horiz</span></button><div class="ph-menu" id="${esc(menuId)}" popover role="group" aria-label="More">${items}</div>`;
}

// The header's HTML: title, tabs, tools, the filled button (or the quiet +), then ⋯ at the far right. Parts are written
// in order with no whitespace between tags, so :empty and :first-child hold; an empty part is left out, except the
// holders a page fills later (tabs nav when tabsHook is set, .ph-act with actId). ⋯ is drawn whenever there are tools
// or items for it; pageHeadFit moves tabs past the fifth into it once the header is in the page.
function pageHead(o: PageHeadOptions): string {
  const seq = ++PAGE_HEAD_SEQ;
  const moreId = o.moreId || `ph-more-${seq}`, menuId = o.menuId || `ph-menu-${seq}`;
  const cls = ['ph', o.pad ? 'ph-pad' : '', o.hook || ''].filter(Boolean).join(' ');
  const tabs = o.tabs?.length || o.tabsHook
    ? `<nav class="ph-tabs${o.tabsHook ? ' ' + o.tabsHook : ''}"${o.tabsAttrs ? ' ' + o.tabsAttrs : ''}${o.tabs?.some(t => t.href === undefined) ? ' role="tablist"' : ''}>${pageHeadTabs(o.tabs || [])}</nav>` : '';
  return `<header class="${cls}"${o.id ? ` id="${esc(o.id)}"` : ''}>`
    + (o.back ? `<a class="ph-back" href="${esc(o.back.href)}">${esc(o.back.label)} ›</a>` : '')
    + `<h1 class="ph-t">${esc(o.title)}</h1>`
    + (o.after || '')
    + tabs
    + '<span class="ph-sp"></span>'
    + `<div class="ph-tools">${o.tools || ''}</div>`
    + (o.primary || o.add || o.actId ? `<div class="ph-act"${o.actId ? ` id="${esc(o.actId)}"` : ''}>${o.primary || (o.add ? pageHeadAdd(o.add.label, o.add.attrs) : '')}</div>` : '')
    + (o.more || o.tools ? pageHeadMore(o.more || PAGE_HEAD_COPY, moreId, menuId) : '')
    + '</header>';
}

// Fill one part again without redrawing the header (Rules' tabs and per-tab button, Updates' title). A part the header
// was drawn without is added in its place.
function pageHeadSet(head: HTMLElement, part: PageHeadPart, html: string): void {
  const q = (sel: string) => head.querySelector<HTMLElement>(':scope > ' + sel);
  // tools folded into ⋯ go back to their row first, so the part is found and a new ⋯ list does not drop them
  if (part === 'tools' || part === 'more') pageHeadFold(head, false);
  if (part === 'more') pageHeadTabsShow(head, Infinity);   // tabs held in ⋯ come back out; the fit puts them in the new list
  if (part === 'title') { const t = q('.ph-t'); if (t) t.innerHTML = html; return; }
  if (part === 'tabs') {
    let nav = q('.ph-tabs');
    if (!nav) { nav = document.createElement('nav'); nav.className = 'ph-tabs'; q('.ph-sp')?.before(nav); }
    nav.innerHTML = html;
    if (nav.querySelector(':scope > button.ph-tab')) nav.setAttribute('role', 'tablist');
    pageHeadFitSoon(); return;
  }
  if (part === 'tools') { const t = q('.ph-tools'); if (t) t.innerHTML = html; pageHeadFitSoon(); return; }
  if (part === 'more') {
    const menu = q('.ph-menu'), btn = q('.ph-more');
    if (!html) { menu?.remove(); btn?.remove(); pageHeadFitSoon(); return; }   // the fit draws ⋯ again if there are tools
    if (menu) { menu.innerHTML = html; delete menu.dataset['phMade']; pageHeadFitSoon(); return; }
    const seq = ++PAGE_HEAD_SEQ;
    head.insertAdjacentHTML('beforeend', pageHeadMore(html, `ph-more-${seq}`, `ph-menu-${seq}`));
    pageHeadFitSoon();
    return;
  }
  let act = q('.ph-act');
  if (!act) { if (!html) return; act = document.createElement('div'); act.className = 'ph-act'; const more = q('.ph-more'); if (more) more.before(act); else head.append(act); }
  act.innerHTML = html;
  pageHeadFitSoon();
}

// Too narrow for one row, the header gives way in the style guide's order ("Top bars: one line") and never hides a
// control for good: a search shrinks first (to 160px, CSS); then a tool's word becomes its icon (.ph-icons: tools that
// carry a .nav-icon or an svg show only it); then the tools go into ⋯ (.ph-fold); then the last tabs do, the current one
// staying on the row. The title and the filled button keep the row, and a button's words never wrap (task #450). There
// is no second row on a computer. On a phone the tabs have a row of their own (CSS) and the same steps fit both rows:
// the title line gives way before the title is cut, the tab row before a tab is.
// A header's tools wherever they are: in the row, or folded into ⋯. Pages reach them through this (or pageHeadSet), never
// through `header > .ph-tools`, which misses them while they are folded.
const pageHeadTools = (head: HTMLElement | null | undefined): HTMLElement | null => head?.querySelector<HTMLElement>(':scope > .ph-tools, :scope > .ph-menu > .ph-tools') || null;
const pageHeadMenu = (head: HTMLElement) => head.querySelector<HTMLElement>(':scope > .ph-menu');
// The ⋯ menu, made when the header has none (its button last, after the filled button). A made menu goes again once
// nothing is left in it.
function pageHeadMakeMenu(head: HTMLElement): HTMLElement | null {
  const have = pageHeadMenu(head);
  if (have) return have;
  const seq = ++PAGE_HEAD_SEQ;
  head.insertAdjacentHTML('beforeend', pageHeadMore('', `ph-more-${seq}`, `ph-menu-${seq}`));
  const menu = pageHeadMenu(head);
  if (menu) menu.dataset['phMade'] = '';
  return menu;
}
function pageHeadTidyMenu(head: HTMLElement): void {
  const menu = pageHeadMenu(head);
  if (!menu || menu.dataset['phMade'] === undefined || menu.firstElementChild) return;
  if (menu.matches(':popover-open')) menu.hidePopover();
  head.querySelector(`:scope > .ph-more[popovertarget="${CSS.escape(menu.id)}"]`)?.remove();
  menu.remove();
}
// A header with tools always has ⋯ (pageHead draws it; this covers tools set later by pageHeadSet, and ⋯ emptied by it).
function pageHeadEnsureMore(head: HTMLElement): void {
  const tools = pageHeadTools(head);
  if (!tools?.firstChild) return;
  const menu = pageHeadMakeMenu(head);
  if (!menu) return;
  delete menu.dataset['phMade'];
  if (!menu.querySelector(':scope > :not(.ph-tools, .ph-mtabs)')) menu.insertAdjacentHTML('beforeend', PAGE_HEAD_COPY);
}
// Move the tools into the ⋯ menu (above its own items, under any tabs held there) or back to their place in the row,
// after the spacer.
function pageHeadFold(head: HTMLElement, fold: boolean): void {
  const tools = pageHeadTools(head);
  if (!tools || fold === (tools.parentElement !== head)) return;
  if (fold) {
    if (!tools.firstChild) return;
    const menu = pageHeadMakeMenu(head);
    if (!menu) return;
    const tabs = menu.querySelector(':scope > .ph-mtabs');
    if (tabs) tabs.after(tools); else menu.prepend(tools);
    head.classList.add('ph-fold');
    pageHeadHeld(head);
    return;
  }
  const menu = tools.parentElement as HTMLElement;
  if (menu.matches(':popover-open')) menu.hidePopover();
  head.querySelector(':scope > .ph-sp')?.after(tools);
  head.classList.remove('ph-fold');
  pageHeadTidyMenu(head);
  pageHeadHeld(head);
}
const pageHeadIsCur = (tab: Element) => tab.matches('.cur, [aria-current=page], [aria-selected=true]');
// Show at most `max` tabs on the row; the rest are hidden there (.ph-over) and listed at the top of ⋯ (.ph-mtabs), the
// current one among those shown. An item in ⋯ stands in for its tab: choosing it clicks the tab (below), so the page's
// own handlers and links work unchanged. Returns how many tabs show.
function pageHeadTabsShow(head: HTMLElement, max: number): number {
  const tabs = [...head.querySelectorAll<HTMLElement>(':scope > .ph-tabs > .ph-tab')];
  const cur = tabs.findIndex(pageHeadIsCur);
  const shown = new Set(tabs.slice(0, Math.max(1, max)));
  if (cur >= shown.size) { shown.delete(tabs[shown.size - 1]!); shown.add(tabs[cur]!); }
  // Nothing to change (the same tabs, the same split): leave the row and ⋯ as they are, so an open ⋯ keeps its items
  // and the focus in them.
  const sig = tabs.map(t => `${shown.has(t) ? 1 : 0}${pageHeadIsCur(t) ? 'c' : ''}${t.getAttribute('href') || ''}${t.innerHTML}`).join('|');
  const had = pageHeadMenu(head)?.querySelector<HTMLElement>(':scope > .ph-mtabs') || null;
  const marked = tabs.every(t => t.classList.contains('ph-over') !== shown.has(t));
  if (marked && (shown.size === tabs.length ? !had : had?.dataset['sig'] === sig)) return shown.size;
  had?.remove();
  for (const tab of tabs) if (tab.classList.contains('ph-over') && shown.has(tab)) tab.classList.remove('ph-over');
  if (shown.size === tabs.length) { pageHeadTidyMenu(head); return tabs.length; }
  const menu = pageHeadMakeMenu(head);
  if (!menu) return tabs.length;
  const group = document.createElement('div');
  group.className = 'ph-mtabs';
  group.dataset['sig'] = sig;
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'More views');
  tabs.forEach((tab, i) => {
    if (shown.has(tab)) return;
    if (!tab.classList.contains('ph-over')) tab.classList.add('ph-over');
    const href = tab instanceof HTMLAnchorElement ? tab.getAttribute('href') : null;
    const item = document.createElement(href === null ? 'button' : 'a');
    if (item instanceof HTMLButtonElement) item.type = 'button'; else item.setAttribute('href', href || '');
    item.className = 'ph-mtab' + (pageHeadIsCur(tab) ? ' cur' : '');
    if (pageHeadIsCur(tab)) item.setAttribute('aria-current', 'page');
    item.dataset['phTab'] = String(i);
    item.innerHTML = tab.innerHTML;
    group.append(item);
  });
  menu.prepend(group);
  return shown.size;
}
// A tab chosen from ⋯ clicks the tab it stands for (a modified click on a link opens the link, as a tab's would).
document.addEventListener('click', (event: MouseEvent) => {
  const item = eventElement(event)?.closest<HTMLElement>('.ph-menu > .ph-mtabs > .ph-mtab');
  if (!item || (item instanceof HTMLAnchorElement && (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0))) return;
  const tab = item.closest('header.ph')?.querySelectorAll<HTMLElement>(':scope > .ph-tabs > .ph-tab')[Number(item.dataset['phTab'])];
  if (!tab) return;
  event.preventDefault();
  // Closed here, before the tab's page acts: a button tab's page redraws the tabs, which rebuilds this list and takes
  // the chosen item out of the page before the close-on-pick listener below can find its menu.
  const menu = item.closest<HTMLElement>('.ph-menu');
  if (menu?.matches(':popover-open')) menu.hidePopover();
  tab.click();
});
// ⋯'s own item where a page gives it none: copy the page's address.
document.addEventListener('click', (event: MouseEvent) => {
  if (!eventElement(event)?.closest('.ph-menu .ph-copy')) return;
  navigator.clipboard?.writeText(location.href).then(() => toast('Link copied'), () => toast('Could not copy the link', true));
});
// A folded search that holds text still filters the page while ⋯ is closed: ⋯ shows a dot (.ph-held) and says so.
function pageHeadHeld(head: HTMLElement): void {
  const more = head.querySelector<HTMLElement>(':scope > .ph-more');
  if (!more) return;
  const held = [...head.querySelectorAll<HTMLInputElement>(':scope > .ph-menu > .ph-tools input[type=search]')].some(i => i.value.trim() !== '');
  more.classList.toggle('ph-held', held);
  more.setAttribute('aria-label', held ? 'More, a search is applied' : 'More');
}
// A tool kept in place while it had the focus fits again once the focus leaves it.
document.addEventListener('focusout', (event: Event) => { if (eventElement(event)?.closest('header.ph .ph-tools')) pageHeadFitSoon(); });
document.addEventListener('input', (event: Event) => {
  const head = eventElement(event)?.closest<HTMLElement>('.ph-menu > .ph-tools')?.closest<HTMLElement>('header.ph');
  if (head) pageHeadHeld(head);
});
// Whether the header is past its room: a part runs past the header's right edge, a row of controls holds more than it
// shows, or (a phone) the title line has cut the title. `tabs` false leaves out a tab row that holds more than it shows:
// on a phone that row is its own, and only the tabs give way for it.
function pageHeadOver(head: HTMLElement, tabs = true): boolean {
  const box = head.getBoundingClientRect();
  const right = box.right - parseFloat(getComputedStyle(head).paddingRight) + 0.5;
  // A phone's header wraps (its tabs take the next row), so a part that no longer fits drops under the title line
  // instead of running past the edge: that is past the room too.
  let line = Infinity;
  for (const part of head.children) {
    if (part.matches('[popover]')) continue;
    const r = part.getBoundingClientRect();
    if (r.width && r.right > right) return true;
    // a part narrower than what it holds: its last control is cut off, or slides under the next part
    if (part.matches(tabs ? '.ph-tabs, .ph-tools' : '.ph-tools') && pageHeadHolds(part)) return true;
    // the title line is the parts in their own order; one a page's CSS moves to a later row (order > 0) is meant there
    const cs = getComputedStyle(part);
    if (!r.width || part.matches('.ph-tabs') || cs.position !== 'static' || cs.order !== '0') continue;
    if (line === Infinity) line = r.bottom; else if (r.top >= line - 0.5) return true;
  }
  const title = head.querySelector<HTMLElement>(':scope > .ph-t');
  return !!title && getComputedStyle(title).position === 'static' && title.scrollWidth > title.clientWidth + 1;
}
// A row of controls holding more than it shows: its last control is cut off, or slides under the next part.
function pageHeadHolds(part: Element): boolean {
  const r = part.getBoundingClientRect();
  return [...part.children].some(c => { const cr = c.getBoundingClientRect(); return cr.width > 0 && cr.right > r.right + 0.5; });
}
// Before a tool's words give way to its icon (.ph-icons), a bare word in it is wrapped in .ph-w, so CSS can hide the
// words alone and keep its counts and badges. The tool says its words as a hover title (a screen reader still reads them).
function pageHeadIconWords(head: HTMLElement): void {
  for (const tool of head.querySelectorAll<HTMLElement>(':scope > .ph-tools > :is(button, a):is(:has(> .nav-icon), :has(> svg))')) {
    for (const node of [...tool.childNodes]) {
      if (node.nodeType !== Node.TEXT_NODE || !node.textContent?.trim()) continue;
      const words = document.createElement('span');
      words.className = 'ph-w';
      node.replaceWith(words);
      words.append(node);
    }
    const said = [...tool.querySelectorAll(':scope > .ph-w')].map(w => w.textContent?.trim()).filter(Boolean).join(' ');
    if (said && !tool.title && !tool.getAttribute('aria-label')) tool.title = said;
  }
}
// The fit measures the header right after changing its classes, so a transition on what it changes would hand back the
// old geometry. Reduced motion gives every element a 1ms transition on every property (ui/styles/base.css), which once
// kept a header on two rows until a reload (task #457). .ph-fit turns transitions off in the header while it fits
// (ui/styles/page-head.css); the style is read once more before it goes, so nothing the fit changed starts a transition after it.
function pageHeadFit(head: HTMLElement): void {
  head.classList.add('ph-fit');
  try { pageHeadFitNow(head); } finally { head.getBoundingClientRect(); head.classList.remove('ph-fit'); }
}
function pageHeadFitNow(head: HTMLElement): void {
  // Tools someone is using (typing in the search, in the row or in ⋯, or ⋯ open) stay where they are until they are
  // done: moving a focused field drops its focus. An open ⋯ keeps the tabs split as it was (data-ph-tabs), even when the
  // page draws its tabs again meanwhile, so its items stay right and the row never shows them all.
  const tools = pageHeadTools(head), menu = pageHeadMenu(head);
  const open = !!menu?.matches(':popover-open');
  const busy = !!tools && (tools.contains(document.activeElement) || (tools.parentElement !== head && open));
  head.classList.remove('ph-icons');
  if (!busy) pageHeadFold(head, false);
  pageHeadEnsureMore(head);
  let shown = pageHeadTabsShow(head, open ? Number(head.dataset['phTabs']) || PAGE_HEAD_TABS_MAX : PAGE_HEAD_TABS_MAX);
  try {
    if (!head.isConnected || !pageHeadOver(head)) return;
    if (pageHeadOver(head, false)) {
      head.classList.add('ph-icons');
      pageHeadIconWords(head);
      if (!pageHeadOver(head)) return;
      if (!busy && pageHeadOver(head, false)) {
        pageHeadFold(head, true);
        if (!pageHeadOver(head)) return;
      }
    }
    if (open) return;
    // The last tabs go into ⋯ while their row is too narrow. On a phone the tabs have a row of their own: only that row
    // running over counts there (a cut title is the title line's, and folding tabs would not free it any room).
    const nav = head.querySelector<HTMLElement>(':scope > .ph-tabs');
    const ownRow = !!nav && getComputedStyle(nav).order !== '0';
    const tabsOver = () => ownRow ? pageHeadHolds(nav) : pageHeadOver(head);
    while (shown > 1 && tabsOver()) shown = pageHeadTabsShow(head, shown - 1);
  } finally {
    head.dataset['phTabs'] = String(shown);
  }
}
// The sidebar opening or closing changes a header's width without a window resize: watch each header's width.
const PAGE_HEAD_WIDTH = new WeakMap<Element, number>();
const PAGE_HEAD_SIZES = new ResizeObserver(entries => {
  if (entries.some(e => PAGE_HEAD_WIDTH.get(e.target) !== Math.round(e.contentRect.width))) pageHeadFitSoon();
});
function pageHeadFitAll(): void {
  document.querySelectorAll<HTMLElement>('header.ph').forEach(head => {
    pageHeadFit(head);
    PAGE_HEAD_WIDTH.set(head, Math.round(head.getBoundingClientRect().width - parseFloat(getComputedStyle(head).paddingLeft) - parseFloat(getComputedStyle(head).paddingRight)));
    PAGE_HEAD_SIZES.observe(head);
  });
}
let PAGE_HEAD_FIT = 0;
// Once per frame however many changes ask: a page draws its header and fills its parts in quick succession.
function pageHeadFitSoon(): void {
  if (PAGE_HEAD_FIT) return;
  PAGE_HEAD_FIT = requestAnimationFrame(() => {
    PAGE_HEAD_FIT = 0;
    pageHeadFitAll();
    PAGE_HEAD_CHANGES.takeRecords();   // the fit's own moves (tools in and out of ⋯) are not changes to fit again for
  });
}
// A header is drawn into #main with innerHTML and filled later (counts, buttons, chips); the window resizes. Each of
// these fits the headers again. A change to a header fits it at once, before the frame is drawn (a mutation observer
// runs then), so a redrawn tab row never shows its sixth tab, or a tool that does not fit, for a frame.
addEventListener('resize', pageHeadFitSoon);
const PAGE_HEAD_CHANGES = new MutationObserver(records => {
  // pageHeadFit's own class change on the header is not a change to fit again for
  if (!records.some(r => r.target instanceof Element && !(r.type === 'attributes' && r.target.matches('header.ph')) && (r.target.closest('header.ph') || [...r.addedNodes].some(n => n instanceof Element && (n.matches('header.ph') || !!n.querySelector('header.ph')))))) return;
  if (PAGE_HEAD_FIT) { cancelAnimationFrame(PAGE_HEAD_FIT); PAGE_HEAD_FIT = 0; }
  pageHeadFitAll();
  PAGE_HEAD_CHANGES.takeRecords();   // the fit's own moves are not changes to fit again for
});
PAGE_HEAD_CHANGES.observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class']});

// A popover's toggle and beforetoggle events say whether it is opening (ToggleEvent).
const pageHeadOpening = (event: Event): boolean => (event as Event & {newState?: string}).newState === 'open';
// The ⋯ menu is a native popover (outside click and Escape close it): place it under its button, right-aligned, and
// close it once an item is chosen. One listener for every header; `toggle` does not bubble, so it is caught on the way down.
document.addEventListener('toggle', (event: Event) => {
  const menu = event.target;
  if (!(menu instanceof HTMLElement) || !menu.classList.contains('ph-menu')) return;
  const open = pageHeadOpening(event);
  const button = menu.parentElement?.querySelector<HTMLElement>(`:scope > .ph-more[popovertarget="${CSS.escape(menu.id)}"]`);
  button?.setAttribute('aria-expanded', String(open));
  if (menu.parentElement instanceof HTMLElement) pageHeadHeld(menu.parentElement);   // a pick clears the search without an input event
  if (!open) pageHeadFitSoon();   // tools kept folded while ⋯ was open may fit in the row again (the window widened)
  if (open && button) pageHeadPlaceMenu(menu, button);   // again, now its size is known
}, true);
// Placed as it opens (beforetoggle runs before the menu shows, in step with the click), so it is never seen where an
// earlier opening left it: under its button, right edge on the button's, inside the window.
document.addEventListener('beforetoggle', (event: Event) => {
  const menu = event.target;
  if (!(menu instanceof HTMLElement) || !menu.classList.contains('ph-menu') || !pageHeadOpening(event)) return;
  const button = menu.parentElement?.querySelector<HTMLElement>(`:scope > .ph-more[popovertarget="${CSS.escape(menu.id)}"]`);
  if (button) pageHeadPlaceMenu(menu, button);
}, true);
function pageHeadPlaceMenu(menu: HTMLElement, button: HTMLElement): void {
  const rect = button.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.top = `${Math.max(12, Math.min(rect.bottom + 4, innerHeight - h - 12))}px`;
  // before it shows its width is not known: its right edge is placed instead
  if (w) { menu.style.left = `${Math.max(12, Math.min(rect.right - w, innerWidth - w - 12))}px`; menu.style.right = 'auto'; }
  else { menu.style.left = 'auto'; menu.style.right = `${Math.max(12, innerWidth - rect.right)}px`; }
}
document.addEventListener('click', (event: MouseEvent) => {
  const item = eventElement(event)?.closest('.ph-menu :is(button,a)');
  const menu = item?.closest<HTMLElement>('.ph-menu');
  if (menu?.matches(':popover-open')) menu.hidePopover();
});
