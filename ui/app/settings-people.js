/* ui/app/settings-people.js — Settings > People and access
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
const PEOPLE_RULES = [['assistant_direct', 'Assistant acts without asking'], ['assistant_approvals', 'People may let their Assistant decide their approvals'], ['botops_direct', 'BotOps changes providers and limits without asking'],
  ['botops_manages_bots', 'BotOps manages every bot'],
  ['managers_manage_reports', 'Managers change their direct reports (model, task model, name, description)'],
  ['admin_credentials', 'Admins store credentials'], ['members_store_credentials', 'Members store credentials for their bots'], ['admin_sql', 'Admins see SQL'], ['member_tokens', 'Members make personal tokens'],
  ['bots_go_ahead', 'Bots go ahead on their recommendation when nobody answers'], ['support_access', 'Owners and admins may let Tico support in']];
let PEOPLE_MODE = '';        // 'manual' or 'sync' picked on this page; '' follows the saved directory source
let PEOPLE_ADDED = '';       // who was just let in, for the one-line identity proxy reminder
let PEOPLE_INVITE_FAILED = null;   // {id, message}: added, but the invite did not go; shown on their row once
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
function peopleMenu(p, view) {
  const owner = S.me?.role === 'owner', items = [];
  // An invite comes from Tico's own sign-in mail (backend/invites.py), never the company's mailbox.
  if (view?.invite_email && !p.owner && p.id !== S.me?.id && p.email && p.can_sign_in)
    items.push(`<button type="button" role="menuitem" data-person-act="invite">${view.invites?.[p.id] ? 'Send invite again' : 'Send invite'}</button>`);
  // Or the issuer makes them a Tico account with a generated password, shown here once (backend/invites.py).
  if (view?.account_password && !p.owner && p.id !== S.me?.id && p.email && p.can_sign_in)
    items.push('<button type="button" role="menuitem" data-person-act="password">Create password</button>');
  const check = (act, label, on) => `<button type="button" role="menuitemcheckbox" aria-checked="${on}" data-person-act="${act}"><span class="people-check" aria-hidden="true">${on ? '✓' : ''}</span>${label}</button>`;
  if (p.role === 'member') items.push(check('create_bots', 'Can add bots', !!p.create_bots), check('add_people', 'Can add people', !!p.add_people));
  if (owner && !p.owner && p.email && p.can_sign_in) items.push('<button type="button" role="menuitem" data-person-act="owner">Make owner</button>');
  if (owner && !p.owner && p.id !== S.me?.id) items.push('<button type="button" role="menuitem" class="danger-text" data-person-act="left">Mark as left</button>');
  if (owner && p.owner && p.id === S.me?.id) items.push('<button type="button" role="menuitem" data-person-act="email">Change my sign-in email</button>');
  return items.join('');
}
function peopleRow(p, view) {
  const owner = S.me?.role === 'owner', menu = peopleMenu(p, view), local = !!S.config?.local;
  const invited = view?.invites?.[p.id];
  const face = personAvatar({...(S.people || []).find(x => x.id === p.id), ...p}, 32);
  const role = owner && !p.owner
    ? `<select data-person-role aria-label="Role">${['member', 'admin'].map(r => `<option value="${r}"${p.role === r ? ' selected' : ''}>${PEOPLE_ROLE[r]}</option>`).join('')}</select>`
    : `<span class="people-role" data-role="${esc(p.role)}">${PEOPLE_ROLE[p.role] || 'Member'}</span>`;
  const locked = p.owner || p.id === S.me?.id || !p.email || (!owner && p.role === 'admin');
  return `<li class="people-row" data-person="${esc(p.id)}">
    <div class="people-who">${face}<div class="people-id"><div class="people-name">${esc(p.name)}</div><div class="people-email">${esc(p.email || 'No email')}</div>${invited ? `<div class="people-invited muted" data-invited>Invited ${agoSpan(invited)}</div>` : ''}</div></div>
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
        ${owner ? `<div class="people-mode" role="group" aria-label="How people join">
          <button type="button" data-people-mode="manual" aria-pressed="${mode === 'manual'}">Add manually</button>
          <button type="button" data-people-mode="sync" aria-pressed="${mode === 'sync'}">Sync with directory</button></div>` : ''}
        ${mode === 'manual' ? `<form class="people-add" id="people-add" autocomplete="off" novalidate>
            <input name="email" type="text" inputmode="email" autocapitalize="off" spellcheck="false" placeholder="Email" aria-label="Email">
            <input name="name" type="text" placeholder="Name (optional)" aria-label="Name">
            ${view?.invite_email ? '<button class="primary" type="submit" data-people-add-invite>Add and send invite</button><button class="ghost" type="submit">Add</button>'
              : '<button class="primary" type="submit">Add</button>'}</form>`
          : owner ? '<div id="directory-sync"></div>'
          : `<p class="muted people-synced">Synced from ${esc(PEOPLE_SOURCE[view.directory] || view.directory)}</p>`}
        <p class="people-note">${view?.invite_email ? 'Add and send invite emails them a sign-in link from Tico. Add sends no email.' : 'No email is sent.'}${S.config?.local ? ` Only the owner can sign in locally. <a href="${GH}/blob/main/docs/install.md#add-a-domain-and-sign-in-later" target="_blank" rel="noopener noreferrer">Add a domain and sign-in</a> so people can join.` : ''}</p>
        ${proxy && added ? `<p class="people-note" id="people-proxy-note">Also allow ${esc(added)} in <a href="${esc(help)}" target="_blank" rel="noopener noreferrer">${esc(proxy)}</a></p>` : ''}
        ${home ? `<label class="people-line">${peopleSwitch('id="people-domain"', view.domain_sign_in, !owner, `Anyone at ${home} has sign-in access`)}<span>Anyone at <b>${esc(home)}</b> ${S.config?.local ? 'has access when sign-in is configured' : 'can sign in'}</span></label>` : ''}
        ${extras.length ? `<div class="people-line people-extras"><span class="muted">Also allowed</span><ul class="allow-chips">${extras.map(x =>
          `<li class="allow-chip" data-allow="${esc(x)}">${esc(x)}${owner ? `<button type="button" class="people-chip-x" data-allow-remove="${esc(x)}" aria-label="Remove ${esc(x)}">×</button>` : ''}</li>`).join('')}</ul></div>` : ''}
      </section>
      <div id="people-from-setup"></div>
      <section class="card people-list-card">
        <header><h2>People</h2><span class="muted tnum">${active.length}</span></header>
        ${owner ? '' : settingsWhoCanHTML('roles')}
        <div class="people-head" aria-hidden="true"><span>Person</span><span>Role</span><span>${S.config?.local ? 'Sign-in access' : 'Can sign in'}</span><span></span></div>
        <ul class="people-list">${active.map(p => peopleRow(p, view)).join('')}</ul>
        ${left.length ? `<details class="people-left"><summary>Left <span class="tnum">${left.length}</span></summary><ul class="people-list">${left.map(p => `<li class="people-row is-left" data-person="${esc(p.id)}">
          <div class="people-who">${personAvatar(p, 32)}<div class="people-id"><div class="people-name">${esc(p.name)}</div><div class="people-email">${esc(p.email || 'No email')}</div></div></div>
          <div class="people-cell-more">${owner && p.removable ? '<button class="ghost danger-text" type="button" data-person-act="remove">Remove</button>' : ''}<button class="ghost" type="button" data-person-act="restore">Restore</button></div></li>`).join('')}</ul></details>` : ''}
        <label class="people-line people-limit">Bot limit per member<input id="member-bot-limit" type="number" inputmode="numeric" min="0" max="1000" value="${esc(String(view.member_bot_limit))}"></label>
        ${owner ? `<div class="people-rules">${PEOPLE_RULES.map(([key, label]) => `<label class="people-line">${peopleSwitch(`data-rule="${key}"`, view.rules?.[key] !== false, false, label)}<span>${label}</span></label>`).join('')}
          <label class="people-line"><span>Days a bot waits before it goes ahead (reversible, internal steps; sends, spending and deletes always wait)</span><input type="number" min="1" max="14" step="1" data-rule-days="go_ahead_days" value="${esc(String(view.rules?.go_ahead_days ?? 3))}" aria-label="Days a bot waits before it goes ahead"></label>
          <label class="people-line"><span>Reports a manager may create a week (BotOps asks; over the limit, the person above decides)</span><input type="number" min="0" max="50" step="1" data-rule-count="report_creates_per_week" value="${esc(String(view.rules?.report_creates_per_week ?? 3))}" aria-label="Reports a manager may create a week"></label>
          <label class="people-line"><span>Reports a manager may archive or restore a week</span><input type="number" min="0" max="50" step="1" data-rule-count="report_archives_per_week" value="${esc(String(view.rules?.report_archives_per_week ?? 3))}" aria-label="Reports a manager may archive or restore a week"></label></div>` : ''}
      </section>`;
    if (mode === 'sync' && owner) window.mountDirectorySync?.($('#directory-sync'), renderSettingsPeople);   // ui/directory-sync.js
    if (owner) void peopleFromSetup($('#people-from-setup'));
    const person = id => view.people.find(p => p.id === id);
    const again = async message => { if (message) toast(message); await renderSettingsPeople(); };
    // A refusal stays as a red line under the control that was changed, found again once the list redraws.
    const change = async (work, message = 'Saved', control) => {
      const person = control?.closest?.('[data-person]')?.dataset.person;
      const find = () => person ? $(`#set-people [data-person="${CSS.escape(person)}"]`)
        : control?.id ? $(`#${CSS.escape(control.id)}`)
        : control?.dataset?.rule ? $(`#set-people [data-rule="${CSS.escape(control.dataset.rule)}"]`)
        : control?.dataset?.ruleDays ? $(`#set-people [data-rule-days="${CSS.escape(control.dataset.ruleDays)}"]`)
        : control?.matches?.('.people-extras') ? $('#set-people .people-extras') : null;
      try { await work(); await again(message); }
      catch (error) {
        await renderSettingsPeople();
        const where = find();
        if (where) settingsFieldError(where, error.message); else toast(error.message, true);
      }
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
      confirmDialog(`Stop syncing from ${PEOPLE_SOURCE[view.directory] || view.directory}?`, 'Synced people stay.', 'Stop sync', async () => {
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
      const invite = !!event.submitter?.matches('[data-people-add-invite]');
      settingsFieldClear(add);
      if (!email) { add.email.focus(); return; }
      add.querySelectorAll('input,button').forEach(x => x.disabled = true);
      try {
        // A domain (`partner.com`, `@partner.com`) lets anyone at it sign in instead of adding one person.
        if (!/^[^@\s]+@/.test(email)) {
          const domain = email.replace(/^\*?@/, '').toLowerCase();
          await allow(view.allowed, [...view.allowed_domains, domain]);
          PEOPLE_ADDED = domain; await again(S.config?.local ? `Added ${domain}. Add a domain and sign-in so people can join. No email sent.` : `Anyone at ${domain} can sign in`);
        } else {
          const added = await post('/v2/access/humans', {email, name});
          PEOPLE_ADDED = 'them';
          // One click adds and invites. Added but not invited still added them: their row says why, and its … menu
          // keeps Send invite to try again.
          let sent = false;
          if (invite && added?.person) {
            try { await post(`/v2/access/humans/${encodeURIComponent(added.person)}/invite`, {}); sent = true; }
            catch (error) { PEOPLE_INVITE_FAILED = {id: added.person, message: `Added, but the invite was not sent: ${error.message} Try Send invite in the … menu.`}; }
          }
          await again(sent ? `Added ${name || email} and sent the invite` : `Added ${name || email}${S.config?.local ? '. Add a domain and sign-in so they can join. No email sent.' : invite ? '' : '. No email sent.'}`);
          $('#people-add [name=email]')?.focus();
        }
      } catch (error) {
        add.querySelectorAll('input,button').forEach(x => x.disabled = false);
        settingsFieldError(add, error.message);
      }
    };
    if (PEOPLE_INVITE_FAILED) {
      const failed = el.querySelector(`[data-person="${CSS.escape(PEOPLE_INVITE_FAILED.id)}"]`);
      if (failed) settingsFieldError(failed, PEOPLE_INVITE_FAILED.message); else toast(PEOPLE_INVITE_FAILED.message, true);
      PEOPLE_INVITE_FAILED = null;
    }
    const domainSwitch = $('#people-domain');
    if (domainSwitch) domainSwitch.onchange = async () => {
      const on = domainSwitch.checked;
      // Turning it off can lock people out, so it asks first.
      if (!on && !await settingsConfirm({title: `Stop sign-in for everyone at ${home}?`, ok: 'Turn off',
        text: `Only people added here keep signing in. Anyone else at ${home} can no longer sign in.`})) { domainSwitch.checked = true; return; }
      void change(async () => {
        await allow(view.allowed, on ? [...view.allowed_domains, home] : view.allowed_domains.filter(d => d !== home));
        PEOPLE_ADDED = on ? home : '';
      }, S.config?.local ? 'Saved. Only the owner can sign in locally.' : 'Saved', domainSwitch);
    };
    el.querySelectorAll('[data-allow-remove]').forEach(button => button.onclick = async () => {
      const x = button.dataset.allowRemove, domain = !x.includes('@');
      if (!await settingsConfirm({title: `Remove ${x}?`, ok: 'Remove',
        text: domain ? `People at ${x} who are not added here can no longer sign in.` : `${x} can no longer sign in.`})) return;
      void change(() => allow(view.allowed.filter(e => e !== x), view.allowed_domains.filter(d => d !== x)), `Removed ${x}`, button.closest('.people-extras'));
    });
    el.querySelectorAll('[data-rule]').forEach(box => box.onchange = () => change(() => put('/v2/access/rules', {[box.dataset.rule]: box.checked}), 'Saved', box));
    el.querySelectorAll('[data-rule-days]').forEach(box => box.onchange = () => {
      const days = Math.round(Number(box.value));
      if (days >= 1 && days <= 14) change(() => put('/v2/access/rules', {[box.dataset.ruleDays]: days}), 'Saved', box);
      else settingsFieldError(box, 'Choose a number of days from 1 to 14.');
    });
    el.querySelectorAll('[data-rule-count]').forEach(box => box.onchange = () => {
      const count = Math.round(Number(box.value));
      if (count >= 0 && count <= 50) change(() => put('/v2/access/rules', {[box.dataset.ruleCount]: count}), 'Saved', box);
      else settingsFieldError(box, 'Choose a number from 0 to 50.');
    });
    $('#member-bot-limit').onchange = event => change(() => put('/v2/access/limits', {member_bot_limit: Number(event.target.value)}), 'Saved', event.target);
    el.querySelectorAll('.people-row').forEach(row => {
      const p = person(row.dataset.person), path = `/v2/access/humans/${encodeURIComponent(p.id)}`;
      const role = row.querySelector('[data-person-role]');
      if (role) role.onchange = () => change(() => post(path, {role: role.value}), 'Saved', role);
      const signIn = row.querySelector('[data-person-signin]');
      if (signIn) signIn.onchange = async () => {
        if (!signIn.checked && !await settingsConfirm({title: `Turn off sign-in for ${p.name}?`, ok: 'Turn off',
          text: `${p.name} can no longer sign in until it is turned back on. Their tasks, bots and history stay.`})) { signIn.checked = true; return; }
        void change(() => post(path, {sign_in: signIn.checked}), S.config?.local ? 'Saved. Only the owner can sign in locally.' : 'Saved', signIn);
      };
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
        if (act === 'restore') void change(() => post(path, {left: false}), `${p.name} restored`, row);
        else if (act === 'create_bots') void change(() => post(path, {create_bots: !p.create_bots}), 'Saved', row);
        else if (act === 'add_people') void change(() => post(path, {add_people: !p.add_people}), 'Saved', row);
        else if (act === 'left') confirmDialog(`Mark ${p.name} as left?`, 'Ends their sign-in and API tokens. Their history stays.', 'Mark as left',
          async () => { await post(`/v2/humans/${encodeURIComponent(p.id)}`, {left: true}); await again(`${p.name} marked as left`); });
        // Only a left person with no history (backend/people_remove.py); anyone else stays as left.
        else if (act === 'remove') confirmDialog(`Remove ${p.name}?`, "This can't be undone.", 'Remove',
          async () => { await writeRequest('DELETE', `/v2/humans/${encodeURIComponent(p.id)}`); await again(`${p.name} removed`); });
        else if (act === 'owner') peopleOwnerDialog(p, view);
        else if (act === 'invite') void change(() => post(`${path}/invite`, {}), `Invite sent to ${p.email}`, row);
        else if (act === 'password') void post(`${path}/password`, {}).then(peoplePasswordDialog, error => settingsFieldError(row, error.message));
        else if (act === 'email') peopleOwnerEmailDialog(p, view);
      });
    });
  } catch (error) { el.innerHTML = `<div class="err">${esc(error.message)}</div>`; }
}
// A made account's password, shown once: Tico keeps no copy, so closing this is the last time anyone sees it.
function peoplePasswordDialog(made) {
  const d = peopleDialog('Account created', `<form>
    <label class="onb-field"><span class="k">Email</span><input name="email" readonly value="${esc(made.email)}"></label>
    <label class="onb-field"><span class="k">Password</span><input name="password" readonly autocomplete="off" spellcheck="false" value="${esc(made.password)}" data-made-password></label>
    <p class="muted">Shown once. They can change it with Forgot password.</p>
    <div class="onb-actions"><button class="ghost" type="button" data-made-copy>Copy</button><span class="spacer"></span><button class="primary" type="submit">Done</button></div></form>`);
  const form = d.querySelector('form');
  d.querySelector('[data-made-copy]').onclick = async () => {
    try { await copyText(`${made.email}\n${made.password}`); toast('Copied'); } catch { form.password.focus(); form.password.select(); }
  };
  form.onsubmit = event => { event.preventDefault(); d.close(); };
  d.addEventListener('close', () => { d.innerHTML = ''; void renderSettingsPeople(); }, {once: true});   // not left in the page
}
// The owner's own sign-in address, changed in place (backend/access.py change_owner_email): typed twice, and the
// old address stops signing in at once, so the page sends a proxy session to sign in again.
function peopleOwnerEmailDialog(p, view) {
  const d = peopleDialog('Change your sign-in email?', `<form>
    <p>You stay the same person: your tasks, approvals, bots and history stay yours. Once you change it you sign in as the new address, and ${esc(p.email)} stops working here.</p>
    <p>Set up the new address in your sign-in provider (Cloudflare Access) first, or you can't get back in. <a href="${GH}/blob/main/docs/people.md#change-the-owners-sign-in-email" target="_blank" rel="noopener noreferrer">How to</a></p>
    <label class="onb-field"><span class="k">New sign-in email</span><input name="email" type="email" autocomplete="off" spellcheck="false"></label>
    <label class="onb-field"><span class="k">Type it again</span><input name="again" type="email" autocomplete="off" spellcheck="false"></label>
    <div class="onb-actions"><button class="ghost" type="button" data-people-cancel>Cancel</button><span class="spacer"></span><span class="people-status muted"></span><button class="primary people-danger" type="submit">Change sign-in email</button></div></form>`);
  const form = d.querySelector('form'), go = form.querySelector('[type=submit]');
  const typed = () => form.email.value.trim().toLowerCase();
  const ready = () => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(typed()) && typed() === form.again.value.trim().toLowerCase() && typed() !== p.email.toLowerCase();
  go.disabled = true;
  d.querySelector('[data-people-cancel]').onclick = () => d.close();
  form.email.oninput = form.again.oninput = () => { go.disabled = !ready(); };
  form.onsubmit = async event => {
    event.preventDefault();
    if (!ready()) return;
    form.querySelectorAll('button,input').forEach(b => b.disabled = true);
    try {
      await post('/v2/access/owner/email', {email: typed(), confirm_email: form.again.value.trim().toLowerCase(),
        expected_revision: view.owner.revision, confirm: true});
      // The address this browser signed in with no longer signs in: sign in again as the new one.
      if (S.me?.proxy_session) location.href = '/api/v2/logout';
      else { location.hash = '#/'; location.reload(); }
    } catch (error) {
      form.querySelector('.people-status').innerHTML = `<span class="err">${esc(error.message)}</span>`;
      form.querySelectorAll('button,input').forEach(b => b.disabled = false);
      go.disabled = !ready();
    }
  };
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

// The people the company file from setup names (backend/company_import.py `add_invitee`): the owner types each one's
// email and clicks Invite. They join the roster, the Recruiting seats that waited with the owner report to them, and
// Tico's own sign-in mail invites them. A failure stays on the row, in words, with Invite to try again.
async function peopleFromSetup(el) {
  if (!el) return;
  let record;
  try { record = await get('/v2/setup'); } catch { return; }
  const people = record?.company_import?.people || [];
  if (!people.length || !el.isConnected) { el.innerHTML = ''; return; }
  const row = p => `<li class="people-row setup-person" data-setup-person="${esc(p.id)}">
      <div class="people-who">${personAvatar({id: p.id, name: p.name}, 32)}<div class="people-id"><div class="people-name">${esc(p.name)}</div><div class="people-email">${esc(p.title || '')}</div></div></div>
      ${p.invited_at ? `<div class="muted" data-setup-invited>Invited ${agoSpan(p.invited_at)}</div>`
        : `<form class="people-add setup-invite" novalidate><input name="email" type="text" inputmode="email" autocapitalize="off" spellcheck="false" placeholder="Email" aria-label="Email for ${esc(p.name)}"${p.person ? ' hidden' : ''}>
           <button class="primary" type="submit">Invite</button><span class="people-status" role="status"></span></form>`}</li>`;
  el.innerHTML = `<section class="card people-list-card" id="setup-people"><header><h2>From your setup</h2><span class="muted tnum">${people.length}</span></header>
    <p class="people-note">Invites come from Tico's sign-in email, like sign-in codes, not your company's mailbox.</p>
    <ul class="people-list">${people.map(row).join('')}</ul></section>`;
  el.querySelectorAll('.setup-invite').forEach(form => form.onsubmit = async event => {
    event.preventDefault();
    const key = form.closest('[data-setup-person]').dataset.setupPerson, email = form.email.value.trim();
    const status = form.querySelector('.people-status');
    if (!email && !form.email.hidden) { form.email.focus(); return; }
    form.querySelectorAll('input,button').forEach(x => x.disabled = true);
    status.textContent = 'Sending…';
    try {
      await post(`/v2/setup/company/people/${encodeURIComponent(key)}/invite`, {email});
      toast('Invite sent');
      await renderSettingsPeople();
    } catch (error) {
      // Added but not sent still added them: the row keeps Invite to send again.
      status.innerHTML = `<span class="err">${esc(error.message)}</span>`;
      form.querySelectorAll('input,button').forEach(x => x.disabled = false);
      if (String(error.body?.error?.code || '').startsWith('invite_')) form.email.hidden = true;
    }
  });
}
