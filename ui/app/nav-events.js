/* ui/app/nav-events.js — Event wiring for the search dialog and the phone drawer, after org-fan.js so listeners register in the original order
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

$('#search-close').onclick = () => $('#search-modal').close();
$('#search-modal').addEventListener('click', ev => {
  if (ev.target === $('#search-modal')) return $('#search-modal').close();
  const li = ev.target.closest('li[data-href]'); if (li) searchGo(li.dataset.href, li.dataset.ask);
});
$('#search-input').oninput = () => { SEARCH_SEL = 0; searchRender(); };
$('#search-input').onkeydown = ev => {
  const n = $('#search-results').querySelectorAll('li[data-href]').length;
  if (ev.key === 'ArrowDown') { ev.preventDefault(); SEARCH_SEL = Math.min(SEARCH_SEL + 1, n - 1); searchRender(); }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); SEARCH_SEL = Math.max(SEARCH_SEL - 1, 0); searchRender(); }
  else if (ev.key === 'Enter') { ev.preventDefault(); const li = $('#search-results [aria-selected=true]'); searchGo(li?.dataset.href, li?.dataset.ask); }
};
document.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'k') { ev.preventDefault(); searchOpen(); }
});
$('#mobile-more').onclick = () => {
  setDrawer(true, false, $('#mobile-more'));
  $('.side-scroll').scrollTop = 0;
};
$('.side-close').onclick = () => setDrawer(false, true);
$('#side-backdrop').onclick = () => setDrawer(false, true);
$('#side').addEventListener('click', ev => { if (ev.target.closest('a[href^="#/"]')) setDrawer(false); });
window.addEventListener('keydown', ev => { if (ev.key === 'Escape' && document.body.classList.contains('drawer')) setDrawer(false, true); });
window.addEventListener('hashchange', () => setDrawer(false));
