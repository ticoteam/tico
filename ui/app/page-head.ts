// The one page header every top-level screen draws (spec: "One page header on every screen", task #338): the title at
// the content's left edge, tabs on the title line in one look, quiet tools, a ⋯ menu and one worded, filled button last.
// Styles: ui/styles/page-head.css, linked after every page stylesheet so it wins ties with the old page rules.
'use strict';

type PageTab = {label: string; href?: string; count?: number | string; cur?: boolean; attrs?: string};
type PageHeadPart = 'title' | 'tabs' | 'tools' | 'more' | 'primary';
type PageHeadOptions = {
  title: string;                           // plain text, escaped here
  back?: {href: string; label: string};    // a sub-page's parent, drawn first as "Updates ›"
  hook?: string;                           // the old header class, kept only as a test hook (au-ph, ru-ph…)
  pad?: boolean;                           // .ph-pad: the 28px gutter on layouts that zero #main's padding
  id?: string;                             // the header's id, where a page reads it back
  after?: string;                          // HTML right after the title (Tasks' scope menu)
  tabs?: PageTab[];
  tabsHook?: string;                       // the old nav class kept as a hook (ru-tabs, use-tabs…)
  tabsAttrs?: string;                      // extra attributes on the nav (aria-label, data-*)
  tools?: string;                          // search, ghost and icon buttons, chips
  more?: string;                           // the ⋯ menu's items (buttons and links)
  moreId?: string;                         // ids of the ⋯ button and its menu, where a page already names them
  menuId?: string;
  primary?: string;                        // the filled button: <button class="primary ph-go" …>Words</button>
  actId?: string;                          // id of the .ph-act holder, where a page fills it later (#strat-actions)
};

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

function pageHeadMore(items: string, moreId: string, menuId: string): string {
  return `<button type="button" class="ph-more" id="${esc(moreId)}" popovertarget="${esc(menuId)}" aria-label="More" title="More" aria-expanded="false">`
    + `<span class="nav-icon" aria-hidden="true">more_horiz</span></button><div class="ph-menu" id="${esc(menuId)}" popover aria-label="More">${items}</div>`;
}

// The header's HTML. Parts are written in order with no whitespace between tags, so :empty and :first-child hold;
// an empty part is left out, except the holders a page fills later (tabs nav when tabsHook is set, .ph-act with actId).
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
    + (o.more ? pageHeadMore(o.more, moreId, menuId) : '')
    + (o.primary || o.actId ? `<div class="ph-act"${o.actId ? ` id="${esc(o.actId)}"` : ''}>${o.primary || ''}</div>` : '')
    + '</header>';
}

// Fill one part again without redrawing the header (Rules' tabs and per-tab button, Updates' title). A part the header
// was drawn without is added in its place.
function pageHeadSet(head: HTMLElement, part: PageHeadPart, html: string): void {
  const q = (sel: string) => head.querySelector<HTMLElement>(':scope > ' + sel);
  if (part === 'title') { const t = q('.ph-t'); if (t) t.innerHTML = html; return; }
  if (part === 'tabs') {
    let nav = q('.ph-tabs');
    if (!nav) { nav = document.createElement('nav'); nav.className = 'ph-tabs'; q('.ph-sp')?.before(nav); }
    nav.innerHTML = html; return;
  }
  if (part === 'tools') { const t = q('.ph-tools'); if (t) t.innerHTML = html; return; }
  if (part === 'more') {
    const menu = q('.ph-menu'), btn = q('.ph-more');
    if (!html) { menu?.remove(); btn?.remove(); return; }
    if (menu) { menu.innerHTML = html; return; }
    const seq = ++PAGE_HEAD_SEQ;
    q('.ph-tools')?.insertAdjacentHTML('afterend', pageHeadMore(html, `ph-more-${seq}`, `ph-menu-${seq}`));
    return;
  }
  let act = q('.ph-act');
  if (!act) { if (!html) return; act = document.createElement('div'); act.className = 'ph-act'; head.append(act); }
  act.innerHTML = html;
}

// The ⋯ menu is a native popover (outside click and Escape close it): place it under its button, right-aligned, and
// close it once an item is chosen. One listener for every header; `toggle` does not bubble, so it is caught on the way down.
document.addEventListener('toggle', (event: Event) => {
  const menu = event.target;
  if (!(menu instanceof HTMLElement) || !menu.classList.contains('ph-menu')) return;
  const open = (event as Event & {newState?: string}).newState === 'open';
  const button = menu.parentElement?.querySelector<HTMLElement>(`:scope > .ph-more[popovertarget="${CSS.escape(menu.id)}"]`);
  button?.setAttribute('aria-expanded', String(open));
  if (!open || !button) return;
  const rect = button.getBoundingClientRect();
  menu.style.top = `${Math.min(rect.bottom + 4, innerHeight - menu.offsetHeight - 12)}px`;
  menu.style.left = `${Math.max(12, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 12))}px`;
}, true);
document.addEventListener('click', (event: MouseEvent) => {
  const item = eventElement(event)?.closest('.ph-menu :is(button,a)');
  const menu = item?.closest<HTMLElement>('.ph-menu');
  if (menu?.matches(':popover-open')) menu.hidePopover();
});
