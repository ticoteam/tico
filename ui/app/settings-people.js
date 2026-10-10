/* ui/app/settings-people.js — Settings > Humans and access
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- people and access
// How people get here (a directory sync, or added by hand), who is on the roster, what each may do and who
// owns the environment (backend/access.py). Every control saves when it changes; nothing needs a restart.
// Identity proxies keep their own list: Tico does not change a Cloudflare Access policy or a Cognito pool.
const PEOPLE_PROXY = {cloudflare: 'Cloudflare Access', 'aws-alb': 'your Cognito user pool'};
const PEOPLE_ROLE = {owner: 'Owner', admin: 'Admin', member: 'Member'};
const PEOPLE_SOURCE = {google: 'Google Workspace', entra: 'Microsoft Entra ID', scim: 'SCIM'};
// Team rules the owner turns off to tighten (backend/team_rules.py): all on by default.
const PEOPLE_RULES = [['assistant_direct', 'Assistant acts without asking'], ['botops_direct', 'BotOps changes providers and limits without asking'],
  ['admin_credentials', 'Admins store credentials'], ['members_store_credentials', 'Members store credentials for their bots'], ['admin_sql', 'Admins see SQL'], ['member_tokens', 'Members make personal tokens']];
let PEOPLE_MODE = '';        // 'manual' or 'sync' picked on this page; '' follows the saved directory source
let PEOPLE_ADDED = '';       // who was just let in, for the one-line identity proxy reminder
function peopleDialog(title, body) {
  const d = $('#people-dialog');
  d.innerHTML = `<div class="tmodal-title">${esc(title)}</div><div class="tmodal-body">${body}</div>`;
  if (!d.open) d.showModal();
  return d;
}
function peopleSwitch(attrs, checked, disabled, label) {
  return `<input type="checkbox" role="switch" class="people-switch" ${attrs} aria-label="${esc(label)}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}>`;
}
// Owners and admins set what a member may do; only the owner makes admins, transfers ownership or marks someone as left.
function peopleMenu(p) {
  const owner = S.me?.role === 'owner', items = [];
  const check = (act, label, on) => `<button type="button" role="menuitemcheckbox" aria-checked="${on}" data-person-act="${act}"><span class="people-check" aria-hidden="true">${on ? '✓' : ''}</span>${label}</button>`;
  if (p.role === 'member') items.push(check('create_bots', 'Can add bots', !!p.create_bots), check('add_people', 'Can add humans', !!p.add_people));
  if (owner && !p.owner && p.email && p.can_sign_in) items.push('<button type="button" role="menuitem" data-person-act="owner">Make owner</button>');
  if (owner && !p.owner && p.id !== S.me?.id) items.push('<button type="button" role="menuitem" class="danger-text" data-person-act="left">Mark as left</button>');
  return items.join('');
}
function peopleRow(p) {
  const owner = S.me?.role === 'owner', menu = peopleMenu(p), local = !!S.config?.local;
  const face = personAvatar({...(S.people || []).find(x => x.id === p.id), ...p}, 32);
  const role = owner && !p.owner
    ? `<select data-person-role aria-label="Role">${['member', 'admin'].map(r => `<option value="${r}"${p.role === r ? ' selected' : ''}>${PEOPLE_ROLE[r]}</option>`).join('')}</select>`
    : `<span class="people-role" data-role="${esc(p.role)}">${PEOPLE_ROLE[p.role] || 'Member'}</span>`;
  const locked = p.owner || p.id === S.me?.id || !p.email || (!owner && p.role === 'admin');
  return `<li class="people-row" data-person="${esc(p.id)}">
    <div class="people-who">${face}<div class="people-id"><div class="people-name">${esc(p.name)}</div><div class="people-email">${esc(p.email || 'No email')}</div></div></div>
    <div class="people-cell-role">${role}</div>
    ${p.owner ? '<div class="people-cell-signin"><span class="muted" title="The owner can always sign in">Always</span></div>'   /* the owner's own sign-in is not a switch */
      : `<label class="people-cell-signin">${peopleSwitch('data-person-signin', p.can_sign_in, locked, local ? 'Sign-in access when configured' : 'Can sign in')}<span class="people-switch-label">${local ? 'When configured' : 'Can sign in'}</span></label>`}
    <div class="people-cell-more">${menu ? `<button class="people-more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button><div class="people-menu" role="menu" hidden>${menu}</div>` : ''}</div>
  </li>`;
}
function peopleCloseMenus(except) {
  document.querySelectorAll('.people-menu:not([hidden])').forEach(menu => {
    if (menu === except) return;
    menu.hidden = true; menu.previousElementSibling?.setAttribute('aria-expanded', 'false');
  });
}
document.addEventListener('click', event => { if (!event.target.closest?.('.people-cell-more')) peopleCloseMenus(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') peopleCloseMenus(); });
async function renderSettingsPeople() {
  const el = $('#set-people'); if (!el) return;
  try {
    const view = await get('/v2/access');
    if (!$('#set-people')) return;
    const owner = S.me?.role === 'owner', home = view.home_domain || '', added = PEOPLE_ADDED;
    PEOPLE_ADDED = '';                                     // the reminder shows once, right after the add
    const mode = PEOPLE_MODE || (view.directory ? 'sync' : 'manual');
    const active = view.people.filter(p => !p.left), left = view.people.filter(p => p.left);
    // Anything else on the allow list: addresses nobody on the roster has yet, and other domains.
    const onRoster = new Set(view.people.map(p => p.email).filter(Boolean));
    const extras = [...view.allowed_domains.filter(d => d !== home), ...view.allowed.filter(e => !onRoster.has(e))];
    const proxy = PEOPLE_PROXY[view.proxy];
    const help = `${GH}/blob/main/docs/people.md#the-identity-proxy-must-agree`;
    el.innerHTML = `
      <section class="card people-join">
        ${owner ? `<div class="people-mode" role="group" aria-label="How humans join">
          <button type="button" data-people-mode="manual" aria-pressed="${mode === 'manual'}">Add manually</button>
          <button type="button" data-people-mode="sync" aria-pressed="${mode === 'sync'}">Sync with directory</button></div>` : ''}
        ${mode === 'manual' ? `<form class="people-add" id="people-add" autocomplete="off" novalidate>
            <input name="email" type="text" inputmode="email" autocapitalize="off" spellcheck="false" placeholder="Email" aria-label="Email">
            <input name="name" type="text" placeholder="Name (optional)" aria-label="Name">
            <button class="primary" type="submit">Add</button></form>`
          : owner ? '<div id="directory-sync"></div>'
          : `<p class="muted people-synced">Synced from ${esc(PEOPLE_SOURCE[view.directory] || view.directory)}</p>`}
        <p class="people-note">No email is sent.${S.config?.local ? ` Only the owner can sign in locally. <a href="${GH}/blob/main/docs/install.md#add-a-domain-and-sign-in-later" target="_blank" rel="noopener noreferrer">Add a domain and sign-in</a> so humans can join.` : ''}</p>
        ${proxy && added ? `<p class="people-note" id="people-proxy-note">Also allow ${esc(added)} in <a href="${esc(help)}" target="_blank" rel="noopener noreferrer">${esc(proxy)}</a></p>` : ''}
        ${home ? `<label class="people-line">${peopleSwitch('id="people-domain"', view.domain_sign_in, !owner, `Anyone at ${home} has sign-in access`)}<span>Anyone at <b>${esc(home)}</b> ${S.config?.local ? 'has access when sign-in is configured' : 'can sign in'}</span></label>` : ''}
        ${extras.length ? `<div class="people-line people-extras"><span class="muted">Also allowed</span><ul class="allow-chips">${extras.map(x =>
          `<li class="allow-chip" data-allow="${esc(x)}">${esc(x)}${owner ? `<button type="button" class="people-chip-x" data-allow-remove="${esc(x)}" aria-label="Remove ${esc(x)}">×</button>` : ''}</li>`).join('')}</ul></div>` : ''}
      </section>
      <section class="card people-list-card">
        <header><h2>Humans</h2><span class="muted tnum">${active.length}</span></header>
        <div class="people-head" aria-hidden="true"><span>Human</span><span>Role</span><span>${S.config?.local ? 'Sign-in access' : 'Can sign in'}</span><span></span></div>
        <ul class="people-list">${active.map(peopleRow).join('')}</ul>
        ${left.length ? `<details class="people-left"><summary>Left <span class="tnum">${left.length}</span></summary><ul class="people-list">${left.map(p => `<li class="people-row is-left" data-person="${esc(p.id)}">
          <div class="people-who">${personAvatar(p, 32)}<div class="people-id"><div class="people-name">${esc(p.name)}</div><div class="people-email">${esc(p.email || 'No email')}</div></div></div>
          <div class="people-cell-more"><button class="ghost" type="button" data-person-act="restore">Restore</button></div></li>`).join('')}</ul></details>` : ''}
        <label class="people-line people-limit">Bot limit per member<input id="member-bot-limit" type="number" inputmode="numeric" min="0" max="1000" value="${esc(String(view.member_bot_limit))}"></label>
        ${owner ? `<div class="people-rules">${PEOPLE_RULES.map(([key, label]) => `<label class="people-line">${peopleSwitch(`data-rule="${key}"`, view.rules?.[key] !== false, false, label)}<span>${label}</span></label>`).join('')}</div>` : ''}
      </section>`;
    if (mode === 'sync' && owner) window.mountDirectorySync?.($('#directory-sync'), renderSettingsPeople);   // ui/directory-sync.js
    const person = id => view.people.find(p => p.id === id);
    const again = async message => { if (message) toast(message); await renderSettingsPeople(); };
    const change = async (work, message) => {
      try { await work(); await again(message); } catch (error) { toast(error.message, true); await renderSettingsPeople(); }
    };
    const allow = (people, domains) => put('/v2/access/allow', {allowed: people, allowed_domains: domains, expected_revision: view.revision});
    const confirmDialog = (title, text, label, work) => {
      const d = peopleDialog(title, `<form>${text ? `<p>${esc(text)}</p>` : ''}<div class="onb-actions"><button class="ghost" type="button" data-people-cancel>Cancel</button><span class="spacer"></span><span class="people-status muted"></span><button class="primary people-danger" type="submit">${esc(label)}</button></div></form>`);
      const form = d.querySelector('form');
      d.querySelector('[data-people-cancel]').onclick = () => d.close();
      form.onsubmit = async event => {
        event.preventDefault();
        form.querySelectorAll('button').forEach(b => b.disabled = true);
        try { await work(); d.close(); }
        catch (error) { form.querySelector('.people-status').innerHTML = `<span class="err">${esc(error.message)}</span>`; form.querySelectorAll('button').forEach(b => b.disabled = false); }
      };
    };
    el.querySelectorAll('[data-people-mode]').forEach(button => button.onclick = () => {
      const want = button.dataset.peopleMode;
      if (want === mode) return;
      if (want === 'sync' || !view.directory) { PEOPLE_MODE = want; void renderSettingsPeople(); return; }
      // Leaving a saved sync turns it off; the people it added stay.
      confirmDialog(`Stop syncing from ${PEOPLE_SOURCE[view.directory] || view.directory}?`, 'Synced humans stay.', 'Stop sync', async () => {
        const d = await get('/v2/directory');
        await put('/v2/directory', {source: '', filter: d.filter, interval_minutes: d.interval_minutes,
          mass_leave_limit: d.mass_leave_limit, expected_revision: d.revision});
        PEOPLE_MODE = 'manual'; await again('Sync stopped');
      });
    });
    const add = $('#people-add');
    if (add) add.onsubmit = async event => {
      event.preventDefault();
      const email = add.email.value.trim(), name = add.name.value.trim();
      if (!email) { add.email.focus(); return; }
      add.querySelectorAll('input,button').forEach(x => x.disabled = true);
      try {
        // A domain (`partner.com`, `@partner.com`) lets anyone at it sign in instead of adding one person.
        if (!/^[^@\s]+@/.test(email)) {
          const domain = email.replace(/^\*?@/, '').toLowerCase();
          await allow(view.allowed, [...view.allowed_domains, domain]);
          PEOPLE_ADDED = domain; await again(S.config?.local ? `Added ${domain}. Add a domain and sign-in so humans can join. No email sent.` : `Anyone at ${domain} can sign in`);
        } else {
          await post('/v2/access/humans', {email, name});
          PEOPLE_ADDED = 'them'; await again(`Added ${name || email}${S.config?.local ? '. Add a domain and sign-in so they can join. No email sent.' : '. No email sent.'}`);
          $('#people-add [name=email]')?.focus();
        }
      } catch (error) {
        toast(error.message, true);
        add.querySelectorAll('input,button').forEach(x => x.disabled = false);
      }
    };
    const domainSwitch = $('#people-domain');
    if (domainSwitch) domainSwitch.onchange = () => change(async () => {
      const on = domainSwitch.checked;
      await allow(view.allowed, on ? [...view.allowed_domains, home] : view.allowed_domains.filter(d => d !== home));
      PEOPLE_ADDED = on ? home : '';
    }, S.config?.local ? 'Sign-in access saved. Only the owner can sign in locally.' : domainSwitch.checked ? `Anyone at ${home} can sign in` : `Only humans added here can sign in`);
    el.querySelectorAll('[data-allow-remove]').forEach(button => button.onclick = () => {
      const x = button.dataset.allowRemove;
      void change(() => allow(view.allowed.filter(e => e !== x), view.allowed_domains.filter(d => d !== x)), `Removed ${x}`);
    });
    el.querySelectorAll('[data-rule]').forEach(box => box.onchange = () => change(() => put('/v2/access/rules', {[box.dataset.rule]: box.checked}), 'Saved'));
    $('#member-bot-limit').onchange = event => change(() => put('/v2/access/limits', {member_bot_limit: Number(event.target.value)}), 'Bot limit saved');
    el.querySelectorAll('.people-row').forEach(row => {
      const p = person(row.dataset.person), path = `/v2/access/humans/${encodeURIComponent(p.id)}`;
      const role = row.querySelector('[data-person-role]');
      if (role) role.onchange = () => change(() => post(path, {role: role.value}), `${p.name} is now ${role.value === 'admin' ? 'an admin' : 'a member'}`);
      const signIn = row.querySelector('[data-person-signin]');
      if (signIn) signIn.onchange = () => change(() => post(path, {sign_in: signIn.checked}), S.config?.local ? `Sign-in access saved for ${p.name}. Only the owner can sign in locally.` : `${p.name} ${signIn.checked ? 'can' : 'can no longer'} sign in`);
      const more = row.querySelector('.people-more');
      if (more) more.onclick = () => {
        const menu = more.nextElementSibling, open = menu.hidden;
        peopleCloseMenus(menu);
        menu.hidden = !open; more.setAttribute('aria-expanded', String(open));
        if (open) menu.querySelector('button')?.focus();
      };
      row.querySelectorAll('[data-person-act]').forEach(button => button.onclick = () => {
        const act = button.dataset.personAct;
        peopleCloseMenus();
        if (act === 'restore') void change(() => post(path, {left: false}), `${p.name} restored`);
        else if (act === 'create_bots') void change(() => post(path, {create_bots: !p.create_bots}), `${p.name} ${p.create_bots ? 'can no longer' : 'can'} add bots`);
        else if (act === 'add_people') void change(() => post(path, {add_people: !p.add_people}), `${p.name} ${p.add_people ? 'can no longer' : 'can'} add humans`);
        else if (act === 'left') confirmDialog(`Mark ${p.name} as left?`, 'Ends their sign-in and API tokens. Their history stays.', 'Mark as left',
          async () => { await post(`/v2/humans/${encodeURIComponent(p.id)}`, {left: true}); await again(`${p.name} marked as left`); });
        else if (act === 'owner') peopleOwnerDialog(p, view);
      });
    });
  } catch (error) { el.innerHTML = `<div class="err">${esc(error.message)}</div>`; }
}
function peopleOwnerDialog(p, view) {
  const old = view.people.find(q => q.owner);
  const d = peopleDialog('Make ' + p.name + ' the owner?', `<form>
    <p>${esc(p.name)} becomes owner now. You lose the owner's controls.</p>
    <label class="onb-field"><span class="k"><input type="checkbox" name="admin"> Keep ${esc(old?.name || 'the current owner')} as an admin</span></label>
    <label class="onb-field"><span class="k">Type ${esc(p.email)} to confirm</span><input name="typed" type="text" autocomplete="off" spellcheck="false"></label>
    <div class="onb-actions"><button class="ghost" type="button" data-people-cancel>Cancel</button><span class="spacer"></span><span class="people-status muted"></span><button class="primary people-danger" type="submit">Transfer ownership</button></div></form>`);
  const form = d.querySelector('form'), go = form.querySelector('[type=submit]');
  go.disabled = true;
  d.querySelector('[data-people-cancel]').onclick = () => d.close();
  form.typed.oninput = event => { go.disabled = event.target.value.trim().toLowerCase() !== p.email.toLowerCase(); };
  form.onsubmit = async event => {
    event.preventDefault();
    form.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      await post('/v2/access/owner', {person: p.id, previous_owner_bot_admin: form.admin.checked,
        expected_revision: view.owner.revision, confirm: true});
      // This page's own rights just changed: start over rather than render an owner-only tab.
      location.hash = '#/'; location.reload();
    } catch (error) {
      form.querySelector('.people-status').innerHTML = `<span class="err">${esc(error.message)}</span>`;
      form.querySelectorAll('button').forEach(b => b.disabled = false);
      go.disabled = form.typed.value.trim().toLowerCase() !== p.email.toLowerCase();
    }
  };
}
