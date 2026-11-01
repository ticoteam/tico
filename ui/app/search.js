/* ui/app/search.js — Search dialog
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- search
// Bots, people and pages by name, then files by their words (GET /api/v2/files/search, ui/app/pages.js). Opens from the button top right on
// desktop (⌘K / Ctrl+K anywhere), from the phone's bottom-nav slot, and closes on pick or Escape.
function searchEntries() {
  const out = [];
  for (const a of document.querySelectorAll('#side .nav-link[data-nav]')) {
    if (a.hidden || a.closest('[hidden]:not(#account-menu)')) continue;   // a closed account menu still lists its pages
    if (rdSectionOff(a.dataset.nav)) continue;   // the company hid this section (Settings > General > Sidebar)
    const label = [...a.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    out.push({kind: 'page', label, href: a.getAttribute('href'), icon: a.querySelector('.nav-icon')?.textContent || 'article'});
  }
  for (const e of shownEmps()) {
    // label is the plain name for matching and sorting; html is the same name as shown elsewhere, flag and all
    out.push({kind: 'bot', label: String(e.display_name || '').replace(TEMP_RE, ''), html: shownName(e), temp: isTempBot(e),
      alias: e.name, href: `#/bot/${encodeURIComponent(e.name)}`, avatar: avatar(e.name, 22, stateOf(e.name))});
  }
  for (const p of (S.people || []).filter(p => !p.hidden)) {
    out.push({kind: 'person', label: p.name || p.id, alias: p.title || '', href: `#/person/${encodeURIComponent(p.id)}`, avatar: personAvatar(p, 22)});
  }
  return out;
}
function searchRank(entry, q) {
  const label = entry.label.toLowerCase(), alias = String(entry.alias || '').toLowerCase();
  if (label.startsWith(q)) return 0;
  if (label.split(/\s+/).some(w => w.startsWith(q))) return 1;
  if (label.includes(q) || alias.startsWith(q)) return 2;
  if (alias.includes(q)) return 3;
  if (entry.temp && 'temporary'.startsWith(q) && q.length >= 3) return 3;   // "temp" finds the flagged bots
  return -1;
}
// File hits arrive after the names: asked once typing pauses, kept for the words they answer.
const SEARCH_FILES = {q: '', hits: [], timer: 0};
function searchFilesFor(raw) {
  const q = raw.trim();
  if (q === SEARCH_FILES.q) return;
  clearTimeout(SEARCH_FILES.timer);
  SEARCH_FILES.timer = setTimeout(async () => {
    const hits = await searchFileHits(q).catch(() => []);
    if ($('#search-input').value.trim() !== q) return;
    Object.assign(SEARCH_FILES, {q, hits});
    searchRender();
  }, 200);
}
function searchResults(q) {
  const raw = q;
  q = q.trim().toLowerCase();
  const all = searchEntries();
  if (!q) return all.filter(e => e.kind === 'page');
  const files = SEARCH_FILES.q === raw.trim() ? SEARCH_FILES.hits : [];
  return [...all.map(e => [searchRank(e, q), e]).filter(([r]) => r >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].label.localeCompare(b[1].label)).slice(0, 14).map(([, e]) => e), ...files];
}
let SEARCH_SEL = 0;
function searchRender() {
  const list = $('#search-results'), q = $('#search-input').value;
  const rows = searchResults(q);
  searchFilesFor(q);
  SEARCH_SEL = Math.min(SEARCH_SEL, Math.max(0, rows.length - 1));
  list.innerHTML = rows.length ? rows.map((e, i) => `<li role="option" data-href="${esc(e.href)}" aria-selected="${i === SEARCH_SEL}">${
      e.avatar || `<span class="nav-icon" aria-hidden="true">${esc(e.icon)}</span>`}<span>${e.html || esc(e.label)}</span><span class="search-kind">${
      e.kind === 'page' ? 'page' : e.kind === 'bot' ? 'bot' : e.kind === 'file' ? 'file' : esc(e.alias || 'human')}</span></li>`).join('')
    : `<li class="search-empty">Nothing named “${esc(q.trim())}”.</li>`;
  list.querySelector('[aria-selected=true]')?.scrollIntoView({block: 'nearest'});
}
function searchGo(href) {
  if (!href) return;
  $('#search-modal').close();
  setDrawer(false);
  location.hash = href;
}
function searchOpen() {
  const d = $('#search-modal'); if (!d) return;
  SEARCH_SEL = 0;
  $('#search-input').value = '';
  searchRender();
  if (!d.open) d.showModal();
  $('#search-input').focus();
}
$('#search-open').onclick = searchOpen;
window.ticoSearch = searchOpen;            // the desktop app's View › Search… and tray item (app/src/main.rs)
$('#mobile-search').onclick = searchOpen;
