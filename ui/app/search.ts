/* ui/app/search.ts — Search dialog
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- search
// Bots, people and pages by name, then files by their words (GET /api/v2/files/search, ui/app/pages.ts). Opens from the button top right on
// desktop (⌘K / Ctrl+K anywhere), from the phone's bottom-nav slot, and closes on pick or Escape.

// One row of the search list: a page, a bot, a person, or a file from the file search.
interface SearchEntry {
  kind: string;
  label: string;
  alias?: string | undefined;
  href?: string | null | undefined;
  icon?: string | undefined;
  html?: string | undefined;
  temp?: boolean | undefined;
  avatar?: string | undefined;
}
// The app's Search is also the desktop app's View › Search… and tray item (app/src/main.rs).
interface Window {
  ticoSearch?: () => void;
}
function searchEntries(): SearchEntry[] {
  const out: SearchEntry[] = [];
  for (const a of document.querySelectorAll<HTMLElement>('#side .nav-link[data-nav]')) {
    // A closed account menu or a folded rail group still lists its pages.
    if (a.hidden || a.closest('[hidden]:not(#account-menu):not(.rd-lib)')) continue;
    const label = [...a.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    // data-alias: a page's old name still finds it (Company was Overview).
    out.push({kind: 'page', label, alias: a.dataset.alias || '', href: a.getAttribute('href'), icon: a.querySelector('.nav-icon')?.textContent || 'article'});
  }
  out.push({kind: 'page', label: 'Contacts', alias: 'Directory', href: DIR_ROUTE, icon: 'contact_page'});   // an icon on the Team header, not a row
  if (cpAllowed()) out.push({kind: 'page', label: 'Share', href: OVERVIEW_COMPANY, icon: 'newspaper'});   // a tab of Company, with no link of its own
  for (const e of shownEmps()) {
    // label is the plain name for matching and sorting; html is the same name as shown elsewhere, flag and all
    out.push({kind: 'bot', label: String(e.display_name || '').replace(TEMP_RE, ''), html: shownName(e), temp: isTempBot(e),
      alias: e.slug, href: `#/bot/${encodeURIComponent(e.id)}`, avatar: avatar(e.id, 22, stateOf(e.id))});
  }
  for (const p of (S.people || []).filter(p => !p.hidden)) {
    out.push({kind: 'person', label: p.name || p.id, alias: p.title || '', href: `#/person/${encodeURIComponent(p.id)}`, avatar: personAvatar(p, 22)});
  }
  return out;
}
function searchRank(entry: SearchEntry, q: string): number {
  const label = entry.label.toLowerCase(), alias = String(entry.alias || '').toLowerCase();
  if (label.startsWith(q)) return 0;
  if (label.split(/\s+/).some(w => w.startsWith(q))) return 1;
  if (label.includes(q) || alias.startsWith(q)) return 2;
  if (alias.includes(q)) return 3;
  if (entry.temp && 'temporary'.startsWith(q) && q.length >= 3) return 3;   // "temp" finds the flagged bots
  return -1;
}
// File hits arrive after the names: asked once typing pauses, kept for the words they answer.
const SEARCH_FILES: {q: string; hits: SearchEntry[]; timer: number} = {q: '', hits: [], timer: 0};
function searchFilesFor(raw: string): void {
  const q = raw.trim();
  if (q === SEARCH_FILES.q) return;
  clearTimeout(SEARCH_FILES.timer);
  SEARCH_FILES.timer = setTimeout(async () => {
    const hits = await searchFileHits(q).catch(() => []);
    const input: HTMLInputElement = $('#search-input');
    if (input.value.trim() !== q) return;
    Object.assign(SEARCH_FILES, {q, hits});
    searchRender();
  }, 200);
}
function searchResults(raw: string): SearchEntry[] {
  const q = raw.trim().toLowerCase();
  const all = searchEntries();
  if (!q) return all.filter(e => e.kind === 'page');
  const files = SEARCH_FILES.q === raw.trim() ? SEARCH_FILES.hits : [];
  return [...all.map((e): [number, SearchEntry] => [searchRank(e, q), e]).filter(([r]) => r >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].label.localeCompare(b[1].label)).slice(0, 14).map(([, e]) => e), ...files];
}
let SEARCH_SEL = 0;
function searchRender(): void {
  const list: HTMLElement = $('#search-results');
  const input: HTMLInputElement = $('#search-input');
  const q = input.value;
  const rows = searchResults(q);
  searchFilesFor(q);
  SEARCH_SEL = Math.min(SEARCH_SEL, Math.max(0, rows.length - 1));
  list.innerHTML = rows.length ? rows.map((e, i) => `<li role="option" data-href="${esc(e.href)}" aria-selected="${i === SEARCH_SEL}">${
      e.avatar || `<span class="nav-icon" aria-hidden="true">${esc(e.icon)}</span>`}<span>${e.html || esc(e.label)}</span><span class="search-kind">${
      e.kind === 'page' ? 'page' : e.kind === 'bot' ? 'bot' : e.kind === 'file' ? 'file' : esc(e.alias || 'human')}</span></li>`).join('')
    : `<li class="search-empty">Nothing named “${esc(q.trim())}”.</li>`;
  list.querySelector('[aria-selected=true]')?.scrollIntoView({block: 'nearest'});
}
function searchGo(href: string | null | undefined): void {
  if (!href) return;
  const dialog: HTMLDialogElement = $('#search-modal');
  dialog.close();
  setDrawer(false);
  location.hash = href;
}
function searchOpen(): void {
  const d: HTMLDialogElement | null = $('#search-modal'); if (!d) return;
  SEARCH_SEL = 0;
  const input: HTMLInputElement = $('#search-input');
  input.value = '';
  searchRender();
  if (!d.open) d.showModal();
  input.focus();
}
(() => {
  // In the phone's drawer it is More's Search row: the drawer gets out of the way first.
  const open: HTMLElement = $('#search-open');
  open.onclick = () => { if (document.body.classList.contains('drawer')) setDrawer(false); searchOpen(); };
  window.ticoSearch = searchOpen;
  const mobile: HTMLElement = $('#mobile-search');
  mobile.onclick = searchOpen;
})();
