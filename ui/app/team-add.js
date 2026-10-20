/* ui/app/team-add.js — The Team plus menu reuses the section, template and chat flows. */
'use strict';

const teamAddCanBot = () => !!S.me?.id && settingsCanCreateBots();
const teamAddCanHuman = () => orgCanGroups() || !!S.me?.can_add_people;
const TEAM_ADD_MENU = document.createElement('div');
TEAM_ADD_MENU.id = 'team-add-menu';
TEAM_ADD_MENU.setAttribute('role', 'menu');
TEAM_ADD_MENU.setAttribute('aria-label', 'Add to team');
TEAM_ADD_MENU.hidden = true;
TEAM_ADD_MENU.innerHTML = `<button type="button" role="menuitem" data-team-add="section">Add section</button>
  <button type="button" role="menuitem" data-team-add="bot">Add bot</button>
  <button type="button" role="menuitem" data-team-add="human">Add human</button>`;
document.body.append(TEAM_ADD_MENU);

function teamAddRefresh() {
  const allowed = {section: orgCanGroups(), bot: teamAddCanBot(), human: teamAddCanHuman()};
  TEAM_ADD_MENU.querySelectorAll('button').forEach(button => { button.hidden = !allowed[button.dataset.teamAdd]; });
  $('#org-add-group').hidden = !Object.values(allowed).some(Boolean);
  if ($('#org-add-group').hidden || TEAM_ADD_MENU.contains(document.activeElement) && document.activeElement.hidden) teamAddClose(true);
}
function teamAddClose(returnFocus = false) {
  const open = !TEAM_ADD_MENU.hidden;
  TEAM_ADD_MENU.hidden = true;
  $('#org-add-group').setAttribute('aria-expanded', 'false');
  if (open && returnFocus) teamAddReturnFocus();
}
function teamAddReturnFocus() {
  (drawerMedia.matches && !document.body.classList.contains('drawer') ? $('#mobile-more') : $('#org-add-group')).focus();
}
function teamAddPosition() {
  if (TEAM_ADD_MENU.hidden) return;
  const anchor = $('#org-add-group').getBoundingClientRect(), menu = TEAM_ADD_MENU.getBoundingClientRect();
  const rail = $('.side-scroll').getBoundingClientRect();
  if (anchor.bottom < rail.top || anchor.top > rail.bottom || drawerMedia.matches && !document.body.classList.contains('drawer')) {
    teamAddClose(); return;
  }
  TEAM_ADD_MENU.style.left = Math.max(8, Math.min(anchor.left, innerWidth - menu.width - 8)) + 'px';
  TEAM_ADD_MENU.style.top = Math.max(8, anchor.bottom + menu.height + 8 <= innerHeight ? anchor.bottom + 4 : anchor.top - menu.height - 4) + 'px';
}
function teamAddOpen(last = false) {
  teamAddRefresh();
  if ($('#org-add-group').hidden) return;
  TEAM_ADD_MENU.hidden = false;
  $('#org-add-group').setAttribute('aria-expanded', 'true');
  teamAddPosition();
  const items = [...TEAM_ADD_MENU.querySelectorAll('button:not([hidden])')];
  items[last ? items.length - 1 : 0]?.focus();
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
document.addEventListener('click', event => { if (!event.target.closest('#org-add-group, #team-add-menu')) teamAddClose(); });
document.addEventListener('focusin', event => { if (!event.target.closest('#org-add-group, #team-add-menu')) teamAddClose(); });
window.addEventListener('hashchange', () => teamAddClose());
window.addEventListener('resize', teamAddPosition);
$('.side-scroll').addEventListener('scroll', teamAddPosition, {passive: true});
TEAM_ADD_MENU.onclick = event => {
  const action = event.target.closest('[data-team-add]')?.dataset.teamAdd;
  if (!action) return;
  teamAddClose();
  if (action === 'section' && orgCanGroups()) {
    if (orgHistoryOn()) $('#org-history').click();
    const toggle = $('[data-section-toggle="organisation"]');
    if (toggle.getAttribute('aria-expanded') === 'false') toggle.click();
    orgGroupEdit({add: ''});
  } else if (action === 'bot' && teamAddCanBot()) teamAddBot();
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
function teamAddBot() {
  const dialog = teamAddDialog('Add bot', `<div class="team-add-choices">
    <button type="button" data-team-recruit><strong>Tico Recruit</strong><span>Describe the role, review candidate pitches, then create your bot.</span></button>
    <button type="button" data-team-template><strong>From template</strong><span>Choose a bot and adjust its instructions.</span></button>
    <button type="button" data-team-botops><strong>Ask BotOps</strong><span>Create a custom bot, or get help connecting one you already have. Share its folder and computer, or a GitHub URL.</span></button>
  </div>`);
  dialog.querySelector('[data-team-recruit]').onclick = () => {
    if (!teamAddCanBot()) return;
    dialog.close('next'); void settingsRecruitFlow();
  };
  dialog.querySelector('[data-team-template]').onclick = () => {
    if (!teamAddCanBot()) return;
    dialog.close('next'); void settingsCatalogPicker();
  };
  dialog.querySelector('[data-team-botops]').onclick = () => {
    if (!teamAddCanBot()) return;
    const bot = S.emps.find(row => row.name === 'botops' && row.can_chat && !botLimited(row));
    if (!bot) { toast('BotOps chat is unavailable. Ask your team owner for access.', true); return; }
    dialog.close('next');
    const pill = botPill('botops'), box = pq(pill, '.p-text');
    // Keep a request already being written, including its attachments.
    if (!box.value.trim()) {
      box.value = 'Help me add a bot. Here is what it should do, or where it already lives (folder path and computer, or GitHub URL):\n\n';
      box.dispatchEvent(new Event('input', {bubbles: true}));
    }
    pill.focusOnOpen = true;
    if (location.hash === '#/bot/botops/chat') showBotTab('chat');
    else location.hash = '#/bot/botops/chat';
  };
}
function teamAddHuman() {
  const dialog = teamAddDialog('Add human', `<form id="team-add-human">
    <label>Email<input name="email" type="email" autocomplete="email" autocapitalize="off" spellcheck="false" required></label>
    <label>Name <span class="muted">(optional)</span><input name="name" type="text" autocomplete="name"></label>
    <p class="muted">No email is sent.${S.config?.local ? ' Only the owner can sign in locally. Configure a domain and sign-in so humans can join.' : ''}</p>
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
