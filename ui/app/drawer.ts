/* ui/app/drawer.ts — Account menu and the phone drawer
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Only people who can manage the company (owner or admin) see the portal link. S.me can arrive after the config, so
// opening the menu draws it again.
function renderAdministration(): void {
  const link: HTMLAnchorElement | null = $('#account-administration');
  if (!link) return;
  const url = (S.me?.role === 'owner' || !!S.me?.bot_admin) ? S.config.administration_url : '';
  if (link.hidden !== !url) link.hidden = !url;
  if (url) link.href = url;
  else link.removeAttribute('href');
}

// Every click and focus elsewhere closes it, so only a real change writes: a `hidden` set, even to the value it has,
// restyles the whole page (ui/app/ask-panel.ts askSet).
function setAccountMenu(open: boolean, returnFocus = false): void {
  const menu: HTMLElement | null = $('#account-menu'), account: HTMLElement | null = $('#account');
  if (menu && menu.hidden === open) {
    if (open) renderAdministration();
    menu.hidden = !open;
    account?.setAttribute('aria-expanded', String(open));
  }
  if (returnFocus) account?.focus();
}
(() => {
  const account: HTMLElement | null = $('#account');
  if (account) account.onclick = () => {
    const menu: HTMLElement | null = $('#account-menu');
    if (menu?.hidden) nativeOrgMenu();
    setAccountMenu(!!menu?.hidden);
  };
})();
document.addEventListener('click', ev => {
  const target = eventElement(ev);
  if (!target?.closest('.account-wrap') || target?.closest('#account-menu :is(a, button)')) setAccountMenu(false);
});
document.addEventListener('focusin', ev => {
  if (!eventElement(ev)?.closest('.account-wrap')) setAccountMenu(false);
});
(() => {
  const wrap: HTMLElement | null = $('.account-wrap');
  if (!wrap) return;
  wrap.addEventListener('keydown', ev => {
    const menu: HTMLElement | null = $('#account-menu');
    if (ev.key === 'Escape' && menu && !menu.hidden) {
      ev.stopPropagation(); ev.preventDefault(); setAccountMenu(false, true);
    }
    if (eventElement(ev)?.id === 'account' && ['ArrowDown', 'ArrowUp'].includes(ev.key)) {
      ev.preventDefault(); setAccountMenu(true);
      const shown = [...(menu?.querySelectorAll<HTMLElement>(':is(a, button):not([hidden])') ?? [])];
      shown[ev.key === 'ArrowDown' ? 0 : shown.length - 1]?.focus();
    }
  });
})();
// phone drawer
const drawerMedia = window.matchMedia('(max-width: 760px)');
let drawerTrigger: HTMLElement | null = null;
function setDrawer(open: boolean, returnFocus = false, trigger: HTMLElement | null = null): void {
  setAccountMenu(false);
  open = !!open && drawerMedia.matches;
  if (open && trigger) drawerTrigger = trigger;
  // More opens with everything showing: the Org and Message bots sections open for this visit,
  // whatever a desktop session folded away (not saved, so the desktop keeps its choice).
  if (open && navCollapsed.size) { navCollapsed.clear(); renderNavSections(); }
  document.body.classList.toggle('drawer', open);
  const more: HTMLElement | null = $('#mobile-more'), backdrop: HTMLElement | null = $('#side-backdrop'), side: HTMLElement | null = $('#side');
  more?.setAttribute('aria-expanded', String(open));
  if (backdrop) backdrop.hidden = !open;
  if (drawerMedia.matches) {
    side?.setAttribute('aria-hidden', String(!open));
    if (side) side.inert = !open;
  } else {
    side?.removeAttribute('aria-hidden');
    if (side) side.inert = false;
  }
  if (open) $('.side-close')?.focus();
  else {
    if (returnFocus && drawerMedia.matches) drawerTrigger?.focus();
    drawerTrigger = null;
  }
}
