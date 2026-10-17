/* ui/app/drawer.js — Account menu and the phone drawer
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

function setAccountMenu(open, returnFocus = false) {
  $('#account-menu').hidden = !open;
  $('#account').setAttribute('aria-expanded', String(open));
  if (returnFocus) $('#account').focus();
}
$('#account').onclick = () => setAccountMenu($('#account-menu').hidden);
document.addEventListener('click', ev => {
  if (!ev.target.closest('.account-wrap') || ev.target.closest('#account-menu :is(a, button)')) setAccountMenu(false);
});
document.addEventListener('focusin', ev => {
  if (!ev.target.closest('.account-wrap')) setAccountMenu(false);
});
$('.account-wrap').addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && !$('#account-menu').hidden) {
    ev.stopPropagation(); ev.preventDefault(); setAccountMenu(false, true);
  }
  if (ev.target.id === 'account' && ['ArrowDown', 'ArrowUp'].includes(ev.key)) {
    ev.preventDefault(); setAccountMenu(true);
    const shown = [...$('#account-menu').querySelectorAll(':is(a, button):not([hidden])')];
    shown[ev.key === 'ArrowDown' ? 0 : shown.length - 1]?.focus();
  }
});
// phone drawer
const drawerMedia = window.matchMedia('(max-width: 760px)');
let drawerTrigger = null;
function setDrawer(open, returnFocus = false, trigger = null) {
  setAccountMenu(false);
  open = !!open && drawerMedia.matches;
  if (open && trigger) drawerTrigger = trigger;
  // More opens with everything showing: the Org and Message bots sections open for this visit,
  // whatever a desktop session folded away (not saved, so the desktop keeps its choice).
  if (open && navCollapsed.size) { navCollapsed.clear(); renderNavSections(); }
  document.body.classList.toggle('drawer', open);
  $('#mobile-more').setAttribute('aria-expanded', String(open));
  $('#side-backdrop').hidden = !open;
  if (drawerMedia.matches) {
    $('#side').setAttribute('aria-hidden', String(!open));
    $('#side').inert = !open;
  } else {
    $('#side').removeAttribute('aria-hidden');
    $('#side').inert = false;
  }
  if (open) $('.side-close').focus();
  else {
    if (returnFocus && drawerMedia.matches) drawerTrigger?.focus();
    drawerTrigger = null;
  }
}
