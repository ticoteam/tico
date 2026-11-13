// The one page header every top-level screen draws (spec: "One page header on every screen", task #338): the title at
// the content's left edge, tabs on the title line in one look, quiet tools, a ⋯ menu and one worded, filled button last.
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
  actId?: string | undefined; // id of the .ph-act holder, where a page fills it later (#strat-actions)
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
    nav.innerHTML = html;
    if (nav.querySelector(':scope > button.ph-tab')) nav.setAttribute('role', 'tablist');
    pageHeadFitSoon(); return;
  }
  if (part === 'tools') { const t = q('.ph-tools'); if (t) t.innerHTML = html; pageHeadFitSoon(); return; }
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
  pageHeadFitSoon();
}

// Too narrow for one row (above a phone's 760px): the header gives way in steps and never hides a control. First the
// tools take a row of their own under it (.ph-wrap); if the title, tabs, ⋯ and the button still do not fit, the tabs
// take theirs too (.ph-wrap2), wrapping inside it. The filled button stays on the title's row. A phone's layout is CSS.
const PAGE_HEAD_PHONE = matchMedia('(max-width:760px)');
function pageHeadOver(head: HTMLElement): boolean {
  const box = head.getBoundingClientRect();
  const right = box.right - parseFloat(getComputedStyle(head).paddingRight) + 0.5;
  for (const part of head.children) {
    const r = part.getBoundingClientRect();
    if (r.width && r.right > right) return true;
    // a part narrower than what it holds: its last control is cut off, or slides under the next part
    if (part.matches('.ph-tabs, .ph-tools') && [...part.children].some(c => { const cr = c.getBoundingClientRect(); return cr.width > 0 && cr.right > r.right + 0.5; })) return true;
  }
  return false;
}
function pageHeadFit(head: HTMLElement): void {
  head.classList.remove('ph-wrap', 'ph-wrap2');
  if (PAGE_HEAD_PHONE.matches || !head.isConnected) return;
  if (!pageHeadOver(head)) return;
  head.classList.add('ph-wrap');
  if (pageHeadOver(head)) head.classList.add('ph-wrap2');
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
  PAGE_HEAD_FIT = requestAnimationFrame(() => { PAGE_HEAD_FIT = 0; pageHeadFitAll(); });
}
// A header is drawn into #main with innerHTML and filled later (counts, buttons, chips); the window resizes. Each of
// these fits the headers again.
addEventListener('resize', pageHeadFitSoon);
new MutationObserver(records => {
  // pageHeadFit's own class change on the header is not a change to fit again for
  if (records.some(r => r.target instanceof Element && !(r.type === 'attributes' && r.target.matches('header.ph')) && (r.target.closest('header.ph') || [...r.addedNodes].some(n => n instanceof Element && (n.matches('header.ph') || !!n.querySelector('header.ph')))))) pageHeadFitSoon();
}).observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class']});

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
