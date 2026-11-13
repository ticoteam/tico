/* ui/app/team-add.ts — The Team plus menu: hiring (Post a job, Build a team, Cloud agents, From scratch), sections and people. */
'use strict';

const teamAddCanBot = () => !!S.me?.id && settingsCanCreateBots();
const teamAddCanExisting = () => !!S.me?.id && settingsCanMakeTokens();
const teamAddCanHuman = () => orgCanGroups() || !!S.me?.can_add_people;
// One menu for the Team header's plus and each group's plus (ui/app/sidebar.ts). In a group it hires into that group
// and adds a group inside it; people are added from the header only. Build a team and Cloud agents show only where
// their pages are loaded (window.teamBuildOpen, window.cloudAgentsOpen).
let TEAM_ADD_GROUP: string | null = null;
const TEAM_ADD_MENU = document.createElement('div');
TEAM_ADD_MENU.id = 'team-add-menu';
TEAM_ADD_MENU.setAttribute('role', 'menu');
TEAM_ADD_MENU.setAttribute('aria-label', 'Add to team');
TEAM_ADD_MENU.hidden = true;
TEAM_ADD_MENU.innerHTML = `<button type="button" role="menuitem" data-team-add="bot">Post a job</button>
  <button type="button" role="menuitem" data-team-add="build">Build a team</button>
  <button type="button" role="menuitem" data-team-add="cloud">Cloud agents</button>
  <button type="button" role="menuitem" data-team-add="scratch">From scratch</button>
  <button type="button" role="menuitem" data-team-add="existing">Add existing bot</button>
  <button type="button" role="menuitem" data-team-add="section">Add section</button>
  <button type="button" role="menuitem" data-team-add="human">Add human</button>`;
document.body.append(TEAM_ADD_MENU);

// Which menu items the viewer may use here, by their data-team-add value.
type TeamAddAllowed = Record<string, boolean>;
type TeamAddForm = HTMLFormElement & {elements: HTMLFormControlsCollection & {email: HTMLInputElement; name: HTMLInputElement}};
function teamAddAllowed(group: string | null = null): TeamAddAllowed {
  const bot = teamAddCanBot();
  return {bot, build: bot && typeof window.teamBuildOpen === 'function', cloud: bot && typeof window.cloudAgentsOpen === 'function',
    scratch: bot, existing: teamAddCanExisting(), section: orgCanGroups(), human: group === null && teamAddCanHuman()};
}
function teamAddAny(group: string | null = null) { return Object.values(teamAddAllowed(group)).some(Boolean); }
function teamAddOpenFor(group: string | null): boolean { return !TEAM_ADD_MENU.hidden && TEAM_ADD_GROUP === group; }
const teamAddAnchor = (): HTMLElement | null => TEAM_ADD_GROUP === null ? $('#org-add-group')
  : document.querySelector<HTMLElement>(`#tree [data-group-plus="${CSS.escape(TEAM_ADD_GROUP)}"]`);
function teamAddRefresh() {
  const allowed = teamAddAllowed(TEAM_ADD_GROUP);
  TEAM_ADD_MENU.querySelectorAll('button').forEach(button => { button.hidden = !allowed[button.dataset.teamAdd ?? '']; });   // C: every item has its data-team-add
  const sectionItem = TEAM_ADD_MENU.querySelector<HTMLElement>('[data-team-add=section]');
  if (sectionItem) sectionItem.textContent = TEAM_ADD_GROUP === null ? 'Add section' : 'Add group';   // G
  $('#org-add-group').hidden = !teamAddAny();
  const active = document.activeElement;   // L
  if (!teamAddAny(TEAM_ADD_GROUP) || TEAM_ADD_MENU.contains(active) && active instanceof HTMLElement && active.hidden) teamAddClose(true);   // N
}
function teamAddClose(returnFocus = false) {
  const open = !TEAM_ADD_MENU.hidden;
  if (open) TEAM_ADD_MENU.hidden = true;     // every click and focus elsewhere lands here: no write when already shut
  teamAddAnchor()?.setAttribute('aria-expanded', 'false');
  if (open && returnFocus) teamAddReturnFocus();
}
function teamAddReturnFocus() {
  if (drawerMedia.matches && !document.body.classList.contains('drawer')) $('#mobile-more').focus();
  else (teamAddAnchor() || $('#org-add-group')).focus();
}
function teamAddPosition() {
  if (TEAM_ADD_MENU.hidden) return;
  const el = teamAddAnchor();
  if (!el) { teamAddClose(); return; }
  const anchor = el.getBoundingClientRect(), menu = TEAM_ADD_MENU.getBoundingClientRect();
  const rail = $('.side-scroll').getBoundingClientRect();
  if (anchor.bottom < rail.top || anchor.top > rail.bottom || drawerMedia.matches && !document.body.classList.contains('drawer')) {
    teamAddClose(); return;
  }
  TEAM_ADD_MENU.style.left = Math.max(8, Math.min(anchor.left, innerWidth - menu.width - 8)) + 'px';
  TEAM_ADD_MENU.style.top = Math.max(8, anchor.bottom + menu.height + 8 <= innerHeight ? anchor.bottom + 4 : anchor.top - menu.height - 4) + 'px';
}
function teamAddOpen(last = false, group: string | null = null) {
  if (!TEAM_ADD_MENU.hidden) teamAddClose();
  TEAM_ADD_GROUP = group;
  teamAddRefresh();
  const anchor = teamAddAnchor();
  if (!anchor || anchor.hidden || !teamAddAny(group)) return;
  TEAM_ADD_MENU.hidden = false;
  anchor.setAttribute('aria-expanded', 'true');
  teamAddPosition();
  const items = [...TEAM_ADD_MENU.querySelectorAll<HTMLButtonElement>('button:not([hidden])')];
  items[last ? items.length - 1 : 0]?.focus();
}
// A group's plus: opens the menu there, or closes it when it is already open there.
function teamAddToggle(group: string | undefined): void {
  const at = group ?? null;   // C: sidebar.ts passes a plus's data-group-plus, which is always there
  if (teamAddOpenFor(at)) teamAddClose(true); else teamAddOpen(false, at);
}
$('#org-add-group').onclick = () => TEAM_ADD_MENU.hidden ? teamAddOpen() : teamAddClose(true);
$('#org-add-group').onkeydown = (event: KeyboardEvent) => {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
  event.preventDefault(); teamAddOpen(event.key === 'ArrowUp');
};
TEAM_ADD_MENU.onkeydown = event => {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); teamAddClose(true); }
  else if (event.key === 'Tab') teamAddClose(true);
  else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const items = [...TEAM_ADD_MENU.querySelectorAll<HTMLButtonElement>('button:not([hidden])')];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);   // the focused item, or -1 when focus is elsewhere
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
};
const TEAM_ADD_KEEP = '#org-add-group, #team-add-menu, [data-group-plus]';
document.addEventListener('click', event => { if (!eventElement(event)?.closest(TEAM_ADD_KEEP)) teamAddClose(); });
document.addEventListener('focusin', event => { if (!eventElement(event)?.closest(TEAM_ADD_KEEP)) teamAddClose(); });
window.addEventListener('hashchange', () => teamAddClose());
window.addEventListener('resize', teamAddPosition);
$('.side-scroll').addEventListener('scroll', teamAddPosition, {passive: true});
TEAM_ADD_MENU.onclick = event => {
  const action = eventElement(event)?.closest<HTMLElement>('[data-team-add]')?.dataset.teamAdd;
  if (!action) return;
  const group = TEAM_ADD_GROUP;
  teamAddClose();
  if (action === 'section' && orgCanGroups()) {
    if (group !== null) { orgGroupEdit({add: group}); return; }
    if (orgHistoryOn()) orgHistoryToggle();
    const toggle = $('[data-section-toggle="organisation"]');
    if (toggle.getAttribute('aria-expanded') === 'false') toggle.click();
    orgGroupEdit({add: ''});
  } else if (action === 'bot' && teamAddCanBot()) teamAddBot(group);
  else if (action === 'build' && teamAddCanBot()) { setDrawer(false); window.teamBuildOpen?.(group); }
  else if (action === 'cloud' && teamAddCanBot()) { setDrawer(false); window.cloudAgentsOpen?.(group); }
  else if (action === 'scratch' && teamAddCanBot()) { setDrawer(false); recruitScratch(); }
  else if (action === 'existing' && teamAddCanExisting()) { setDrawer(false); window.connectAgent?.(); }
  else if (action === 'human' && teamAddCanHuman()) teamAddHuman();
};

function teamAddDialog(title: string, body: string): HTMLDialogElement {
  setDrawer(false);
  const dialog = document.createElement('dialog');
  dialog.id = 'team-add-dialog'; dialog.className = 'tmodal team-add-dialog';
  dialog.setAttribute('aria-labelledby', 'team-add-title');
  dialog.innerHTML = `<div class="tmodal-head"><h2 id="team-add-title">${esc(title)}</h2><span class="spacer"></span>
    <button class="ghost" type="button" data-team-close aria-label="Close">✕</button></div><div class="tmodal-body">${body}</div>`;
  document.body.append(dialog);
  const leave = () => dialog.close('next');
  window.addEventListener('hashchange', leave);
  dialog.onclose = () => {
    window.removeEventListener('hashchange', leave); dialog.remove();
    if (dialog.returnValue !== 'next') teamAddReturnFocus();
  };
  dialog.querySelectorAll<HTMLButtonElement>('[data-team-close]').forEach(button => { button.onclick = () => dialog.close(); });
  dialog.showModal();
  return dialog;
}
// Post a job makes a Recruiting seat and opens its Applicants page (ui/app/recruit.ts). From a group's plus the job is
// in that group and reports to its head: the bot in it whose manager is outside it.
function teamAddBot(group: string | null = null) {
  setDrawer(false);
  if (group === null) { void recruitPostJob(); return; }
  const bots = (S.emps || []).filter(e => e.team === group && e.status !== 'archived' && !isHelperBot(e));
  const names = new Set(bots.map(e => 'b:' + e.id));
  const head = bots.find(e => !names.has(orgBossKey({kind: 'bot', ...e})));
  void recruitPostJob({team: group, reports_to: head?.id || ''});
}
function teamAddHuman() {
  const dialog = teamAddDialog('Add human', `<form id="team-add-human">
    <label>Email<input name="email" type="email" autocomplete="email" autocapitalize="off" spellcheck="false" required></label>
    <label>Name <span class="muted">(optional)</span><input name="name" type="text" autocomplete="name"></label>
    <p class="muted">Adding sends no email.${S.config?.local ? ' Only the owner can sign in locally. Configure a domain and sign-in so humans can join.' : ''}</p>
    ${!orgCanGroups() ? '<p class="muted">You can add coworkers in your team’s domain.</p>' : ''}
    <p class="muted">Your sign-in provider may also need to allow this email. <a href="${GH}/blob/main/docs/people.md#the-identity-proxy-must-agree" target="_blank" rel="noopener noreferrer">Sign-in help</a></p>
    <p class="err" role="alert" data-team-error hidden></p>
    <div class="row"><button class="primary" type="submit">Add human</button><button class="ghost" type="button" data-team-close>Cancel</button></div>
  </form>`);
  const form = dialog.querySelector('form') as TeamAddForm | null;   // the dialog's own form, written above
  const submit = form?.querySelector<HTMLButtonElement>('[type=submit]'), error = form?.querySelector<HTMLElement>('[data-team-error]');
  if (!form || !submit || !error) return;   // G: the dialog always has them; the old code threw on a null here
  form.elements.email.focus();
  form.onsubmit = async event => {
    event.preventDefault();
    if (submit.disabled) return;
    if (!teamAddCanHuman()) { error.textContent = 'You no longer have permission to add humans.'; error.hidden = false; return; }
    submit.disabled = true; error.hidden = true;
    const email = form.elements.email.value.trim(), name = form.elements.name.value.trim();
    try {
      await post('/v2/access/humans', {email, name});
      dialog.close(); await refresh(true);
      if (S.route === SETTINGS && $('#settings-people')) void renderSettingsPeople();
      toast(`Added ${name || email}. No email sent.`);
    } catch (e) { error.textContent = errorMessage(e, ''); error.hidden = false; submit.disabled = false; }   // H
  };
}

// The Team header's ⋯: Contacts, and the list's two switches in words (ui/app/sidebar.ts orgMineToggle,
// orgHistoryToggle). A switch keeps the menu open; Contacts opens its page and closes it. Built like the plus menu above.
const TEAM_MORE_MENU = document.createElement('div');
TEAM_MORE_MENU.id = 'team-more-menu';
TEAM_MORE_MENU.setAttribute('role', 'menu');
TEAM_MORE_MENU.setAttribute('aria-label', 'Team options');
TEAM_MORE_MENU.hidden = true;
TEAM_MORE_MENU.innerHTML = `<a role="menuitem" href="#/library/contacts" data-nav="contacts"><span class="nav-icon" aria-hidden="true">contact_page</span><span>Contacts</span></a>
  <hr class="team-menu-line" role="separator">
  <button type="button" role="menuitemcheckbox" aria-checked="false" data-org-more="mine"><span class="nav-icon" aria-hidden="true">person</span><span>${ORG_MINE_WORDS}</span><span class="nav-icon team-menu-check" aria-hidden="true">check</span></button>
  <button type="button" role="menuitemcheckbox" aria-checked="false" data-org-more="history"><span class="nav-icon" aria-hidden="true">history</span><span>${ORG_HISTORY_WORDS}</span><span class="nav-icon team-menu-check" aria-hidden="true">check</span></button>`;
document.body.append(TEAM_MORE_MENU);
orgMoreSync();
const teamMoreItems = () => [...TEAM_MORE_MENU.querySelectorAll<HTMLElement>(':is(a,button):not([hidden])')];
function teamMoreClose(returnFocus = false) {
  const open = !TEAM_MORE_MENU.hidden;
  if (open) TEAM_MORE_MENU.hidden = true;
  $('#org-more').setAttribute('aria-expanded', 'false');
  if (open && returnFocus) {
    if (drawerMedia.matches && !document.body.classList.contains('drawer')) $('#mobile-more').focus();
    else $('#org-more').focus();
  }
}
// Its right edge on ⋯'s right edge, 4px below it, inside the window.
function teamMorePosition() {
  if (TEAM_MORE_MENU.hidden) return;
  const anchor = $('#org-more').getBoundingClientRect(), menu = TEAM_MORE_MENU.getBoundingClientRect();
  const rail = $('.side-scroll').getBoundingClientRect();
  if (anchor.bottom < rail.top || anchor.top > rail.bottom || drawerMedia.matches && !document.body.classList.contains('drawer')) {
    teamMoreClose(); return;
  }
  TEAM_MORE_MENU.style.left = Math.max(8, Math.min(anchor.right - menu.width, innerWidth - menu.width - 8)) + 'px';
  TEAM_MORE_MENU.style.top = Math.max(8, anchor.bottom + menu.height + 8 <= innerHeight ? anchor.bottom + 4 : anchor.top - menu.height - 4) + 'px';
}
function teamMoreOpen(last = false) {
  teamAddClose();
  orgMoreSync();
  TEAM_MORE_MENU.hidden = false;
  $('#org-more').setAttribute('aria-expanded', 'true');
  teamMorePosition();
  const items = teamMoreItems();
  items[last ? items.length - 1 : 0]?.focus();
}
$('#org-more').onclick = () => TEAM_MORE_MENU.hidden ? teamMoreOpen() : teamMoreClose(true);
$('#org-more').onkeydown = (event: KeyboardEvent) => {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
  event.preventDefault(); teamMoreOpen(event.key === 'ArrowUp');
};
TEAM_MORE_MENU.onkeydown = event => {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); teamMoreClose(true); }
  else if (event.key === 'Tab') teamMoreClose(true);
  else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const items = teamMoreItems();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
};
TEAM_MORE_MENU.onclick = event => {
  const target = eventElement(event);
  if (target?.closest('a[data-nav="contacts"]')) { teamMoreClose(); setDrawer(false); return; }   // the link navigates
  const which = target?.closest<HTMLElement>('[data-org-more]')?.dataset.orgMore;
  if (which === 'mine') orgMineToggle();
  else if (which === 'history') orgHistoryToggle();
  else return;
  orgMoreSync();
  teamMorePosition();
};
const TEAM_MORE_KEEP = '#org-more, #team-more-menu';
document.addEventListener('click', event => { if (!eventElement(event)?.closest(TEAM_MORE_KEEP)) teamMoreClose(); });
document.addEventListener('focusin', event => { if (!eventElement(event)?.closest(TEAM_MORE_KEEP)) teamMoreClose(); });
window.addEventListener('hashchange', () => teamMoreClose());
window.addEventListener('resize', teamMorePosition);
$('.side-scroll').addEventListener('scroll', teamMorePosition, {passive: true});
