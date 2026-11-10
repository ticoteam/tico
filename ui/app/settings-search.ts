/* ui/app/settings-search.ts — Settings search: one box above the Settings tabs finds any setting and opens its card
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One row of GET /v2/settings/catalog (backend/settings_catalog.py public): the setting's name, where it sits, who may
// change it and its card's link (`#/settings/<tab>/<card>`, or the full address when the server has a public URL).
interface SettingsSearchHit { key: string; name: string; where: string; who: string; card: string; }

// The catalogue is the one list (backend/settings_entries.py): a setting added there is found here with nothing else to
// change. A person who may not open a card (an admin's, the owner's) is not offered it.
const SETTINGS_SEARCH = {seq: 0, timer: 0 as ReturnType<typeof setTimeout> | 0, hits: [] as SettingsSearchHit[], at: -1};
const settingsSearchOpenable = (hit: SettingsSearchHit) => hit.who === 'owner' ? S.me?.role === 'owner'
  : hit.who === 'admin' ? settingsIsAdmin() : true;
// The card's address in this app: the catalogue may give it with the server's own origin in front.
const settingsSearchHash = (card: string) => { const at = String(card || '').indexOf('#'); return at < 0 ? '' : card.slice(at); };

function settingsSearchHTML() {
  return `<div class="settings-search" id="settings-search">
    <input type="search" id="settings-search-q" autocomplete="off" spellcheck="false" enterkeyhint="search" aria-label="Search settings"
      placeholder="Search settings" role="combobox" aria-expanded="false" aria-controls="settings-search-hits" aria-autocomplete="list">
    <ul class="settings-search-hits" id="settings-search-hits" role="listbox" aria-label="Settings" hidden></ul></div>`;
}
function settingsSearchDraw() {
  const list = $('#settings-search-hits'), box = $('#settings-search-q');
  if (!list || !box) return;
  const q = box.value.trim(), {hits, at} = SETTINGS_SEARCH;
  list.hidden = !q;
  box.setAttribute('aria-expanded', String(!!q));
  list.innerHTML = hits.length ? hits.map((hit, i) => `<li role="option" id="settings-search-hit-${i}" aria-selected="${i === at}"
      ${i === at ? 'class="cur"' : ''}><a href="${esc(settingsSearchHash(hit.card))}" title="${esc(hit.where)}" data-settings-hit="${i}">${esc(hit.name)}</a></li>`).join('')
    : `<li class="settings-search-none" role="presentation">No settings found</li>`;
  if (at >= 0) box.setAttribute('aria-activedescendant', 'settings-search-hit-' + at); else box.removeAttribute('aria-activedescendant');
}
async function settingsSearchRun(q: string) {
  const seq = ++SETTINGS_SEARCH.seq;
  let found: SettingsSearchHit[] = [];
  try {
    const answer = await get('/v2/settings/catalog?limit=40&q=' + encodeURIComponent(q.slice(0, 200))) as {settings?: SettingsSearchHit[]};
    found = (answer?.settings || []).filter(hit => settingsSearchHash(hit.card) && settingsSearchOpenable(hit));
  } catch { found = []; }
  // A slower answer to older words never replaces a newer one.
  if (seq !== SETTINGS_SEARCH.seq || $('#settings-search-q')?.value.trim() !== q) return;
  SETTINGS_SEARCH.hits = found.slice(0, 8); SETTINGS_SEARCH.at = -1;
  settingsSearchDraw();
}
function settingsSearchClear() {
  const box = $('#settings-search-q');
  if (box) box.value = '';
  SETTINGS_SEARCH.seq++; SETTINGS_SEARCH.hits = []; SETTINGS_SEARCH.at = -1;
  clearTimeout(SETTINGS_SEARCH.timer);
  settingsSearchDraw();
}
// Opens a hit's card: the router turns `#/settings/<tab>/<card>` into the tab, scrolled to the card.
function settingsSearchOpen(hit: SettingsSearchHit | undefined) {
  const hash = hit && settingsSearchHash(hit.card);
  if (!hash) return;
  settingsSearchClear();
  $('#settings-search-q')?.blur();
  location.hash = hash;
}
function settingsSearchBind() {
  const root = $('#settings-search'), box = $('#settings-search-q');
  if (!root || !box || root.dataset.bound) return;
  root.dataset.bound = '1';
  box.addEventListener('input', () => {
    clearTimeout(SETTINGS_SEARCH.timer);
    const q = box.value.trim();
    if (!q) { settingsSearchClear(); return; }
    SETTINGS_SEARCH.timer = setTimeout(() => void settingsSearchRun(q), 150);
  });
  box.addEventListener('keydown', (e: KeyboardEvent) => {
    const {hits} = SETTINGS_SEARCH;
    if (e.key === 'Escape') { if (box.value) { e.preventDefault(); settingsSearchClear(); } return; }
    if (e.key === 'Enter') { e.preventDefault(); settingsSearchOpen(hits[Math.max(0, SETTINGS_SEARCH.at)]); return; }
    if ((e.key !== 'ArrowDown' && e.key !== 'ArrowUp') || !hits.length) return;
    e.preventDefault();
    SETTINGS_SEARCH.at = (SETTINGS_SEARCH.at + (e.key === 'ArrowDown' ? 1 : hits.length - 1 + (SETTINGS_SEARCH.at < 0 ? 1 : 0))) % hits.length;
    settingsSearchDraw();
  });
  root.addEventListener('click', (e: MouseEvent) => {
    const link = e.target instanceof Element ? e.target.closest<HTMLElement>('[data-settings-hit]') : null;
    if (!link) return;
    e.preventDefault();
    settingsSearchOpen(SETTINGS_SEARCH.hits[Number(link.dataset.settingsHit)]);
  });
}
