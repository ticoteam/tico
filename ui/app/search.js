/* ui/app/search.js — Search dialog
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- search
// Bots, people and pages by name; nothing inside them yet. Opens from the button top right on
// desktop (⌘K / Ctrl+K anywhere), from the phone's bottom-nav slot, and closes on pick or Escape.
function searchEntries() {
  const out = [];
  for (const a of document.querySelectorAll('#side .nav-link[data-nav]')) {
    if (a.hidden || a.closest('[hidden]:not(#account-menu)')) continue;   // a closed account menu still lists its pages
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
// "Ask the Assistant..." closes every list: the words typed go to your private Assistant chat, prefilled
// (ui/app/assistant-page.js), so anything the names above do not answer is one Enter away.
function searchAsk(q) {
  return S.me?.id ? [{kind: 'assistant', label: 'Ask the Assistant…', alias: 'assistant', ask: q.trim(), icon: 'auto_awesome',
    href: ASSISTANT}] : [];
}
function searchResults(q) {
  const raw = q;
  q = q.trim().toLowerCase();
  const all = searchEntries();
  if (!q) return [...all.filter(e => e.kind === 'page'), ...searchAsk(raw)];
  return [...all.map(e => [searchRank(e, q), e]).filter(([r]) => r >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].label.localeCompare(b[1].label)).slice(0, 14).map(([, e]) => e), ...searchAsk(raw)];
}
let SEARCH_SEL = 0;
function searchRender() {
  const list = $('#search-results'), q = $('#search-input').value;
  const rows = searchResults(q);
  SEARCH_SEL = Math.min(SEARCH_SEL, Math.max(0, rows.length - 1));
  list.innerHTML = rows.length ? rows.map((e, i) => `<li role="option" data-href="${esc(e.href)}"${e.kind === 'assistant' ? ` data-ask="${esc(e.ask)}"` : ''} aria-selected="${i === SEARCH_SEL}">${
      e.avatar || `<span class="nav-icon" aria-hidden="true">${esc(e.icon)}</span>`}<span>${e.html || esc(e.label)}</span><span class="search-kind">${
      e.kind === 'page' ? 'page' : e.kind === 'bot' ? 'bot' : esc(e.alias || 'human')}</span></li>`).join('')
    : `<li class="search-empty">Nothing named “${esc(q.trim())}”.</li>`;
  list.querySelector('[aria-selected=true]')?.scrollIntoView({block: 'nearest'});
}
function searchGo(href, ask) {
  if (!href) return;
  $('#search-modal').close();
  setDrawer(false);
  if (ask !== undefined) assistantPrefill(ask);
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
