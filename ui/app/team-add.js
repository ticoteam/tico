/* ui/app/team-add.js — The Team plus menu: hiring (Post a job, Build a team, Cloud agents, From scratch), sections and people. */
'use strict';

const teamAddCanBot = () => !!S.me?.id && settingsCanCreateBots();
const teamAddCanExisting = () => !!S.me?.id && settingsCanMakeTokens();
const teamAddCanHuman = () => orgCanGroups() || !!S.me?.can_add_people;
// One menu for the Team header's plus and each group's plus (ui/app/sidebar.js). In a group it hires into that group
// and adds a group inside it; people are added from the header only. Build a team and Cloud agents show only where
// their pages are loaded (window.teamBuildOpen, window.cloudAgentsOpen).
let TEAM_ADD_GROUP = null;
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

function teamAddAllowed(group = null) {
  const bot = teamAddCanBot();
  return {bot, build: bot && typeof window.teamBuildOpen === 'function', cloud: bot && typeof window.cloudAgentsOpen === 'function',
    scratch: bot, existing: teamAddCanExisting(), section: orgCanGroups(), human: group === null && teamAddCanHuman()};
}
function teamAddAny(group = null) { return Object.values(teamAddAllowed(group)).some(Boolean); }
function teamAddOpenFor(group) { return !TEAM_ADD_MENU.hidden && TEAM_ADD_GROUP === group; }
const teamAddAnchor = () => TEAM_ADD_GROUP === null ? $('#org-add-group')
  : document.querySelector(`#tree [data-group-plus="${CSS.escape(TEAM_ADD_GROUP)}"]`);
function teamAddRefresh() {
  const allowed = teamAddAllowed(TEAM_ADD_GROUP);
  TEAM_ADD_MENU.querySelectorAll('button').forEach(button => { button.hidden = !allowed[button.dataset.teamAdd]; });
  TEAM_ADD_MENU.querySelector('[data-team-add=section]').textContent = TEAM_ADD_GROUP === null ? 'Add section' : 'Add group';
  $('#org-add-group').hidden = !teamAddAny();
  if (!teamAddAny(TEAM_ADD_GROUP) || TEAM_ADD_MENU.contains(document.activeElement) && document.activeElement.hidden) teamAddClose(true);
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
function teamAddOpen(last = false, group = null) {
  if (!TEAM_ADD_MENU.hidden) teamAddClose();
  TEAM_ADD_GROUP = group;
  teamAddRefresh();
  const anchor = teamAddAnchor();
  if (!anchor || anchor.hidden || !teamAddAny(group)) return;
  TEAM_ADD_MENU.hidden = false;
  anchor.setAttribute('aria-expanded', 'true');
  teamAddPosition();
  const items = [...TEAM_ADD_MENU.querySelectorAll('button:not([hidden])')];
  items[last ? items.length - 1 : 0]?.focus();
}
// A group's plus: opens the menu there, or closes it when it is already open there.
function teamAddToggle(group) {
  if (teamAddOpenFor(group)) teamAddClose(true); else teamAddOpen(false, group);
}
$('#org-add-group').onclick = () => TEAM_ADD_MENU.hidden ? teamAddOpen() : teamAddClose(true);
$('#org-add-group').onkeydown = event => {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
  event.preventDefault(); teamAddOpen(event.key === 'ArrowUp');
};
TEAM_ADD_MENU.onkeydown = event => {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); teamAddClose(true); }
  else if (event.key === 'Tab') teamAddClose(true);
  else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const items = [...TEAM_ADD_MENU.querySelectorAll('button:not([hidden])')], index = items.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
};
const TEAM_ADD_KEEP = '#org-add-group, #team-add-menu, [data-group-plus]';
document.addEventListener('click', event => { if (!event.target.closest(TEAM_ADD_KEEP)) teamAddClose(); });
document.addEventListener('focusin', event => { if (!event.target.closest(TEAM_ADD_KEEP)) teamAddClose(); });
window.addEventListener('hashchange', () => teamAddClose());
window.addEventListener('resize', teamAddPosition);
$('.side-scroll').addEventListener('scroll', teamAddPosition, {passive: true});
TEAM_ADD_MENU.onclick = event => {
  const action = event.target.closest('[data-team-add]')?.dataset.teamAdd;
  if (!action) return;
  const group = TEAM_ADD_GROUP;
  teamAddClose();
  if (action === 'section' && orgCanGroups()) {
    if (group !== null) { orgGroupEdit({add: group}); return; }
    if (orgHistoryOn()) $('#org-history').click();
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

function teamAddDialog(title, body) {
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
  dialog.querySelectorAll('[data-team-close]').forEach(button => { button.onclick = () => dialog.close(); });
  dialog.showModal();
  return dialog;
}
// Post a job makes a Recruiting seat and opens its Applicants page (ui/app/recruit.js). From a group's plus the job is
// in that group and reports to its head: the bot in it whose manager is outside it.
function teamAddBot(group = null) {
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
  const form = dialog.querySelector('form'), submit = form.querySelector('[type=submit]'), error = form.querySelector('[data-team-error]');
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
    } catch (e) { error.textContent = e.message; error.hidden = false; submit.disabled = false; }
  };
}
