/* ui/app/threads-page.js — New thread and New DM, a thread's Members and settings, and Make task on a message. The Threads
   page itself (the rail beside one thread) is ui/app/thread-rail.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- choosing teammates
// Whom the viewer may add: a bot, a person or (except to a DM, which names its people) a group they may write to. The
// helpers and the viewer are never listed.
function threadAddable({groups = true} = {}) {
  const bots = shownEmps().filter(e => e.can_chat !== false && (!e.my_access || e.my_access.write))
    .map(e => ({actor: 'bot:' + e.name, label: e.display_name || e.name}));
  const people = (S.people || []).filter(p => p.id && !p.hidden && p.id !== S.me?.id && p.my_access?.write !== false)
    .map(p => ({actor: 'human:' + p.id, label: p.name || p.id}));
  const teams = groups ? (S.orgGroups || []).filter(g => g.id && g.my_access?.write).map(g => ({actor: 'group:' + g.id, label: g.name || g.id})) : [];
  return [...people, ...teams, ...bots].sort((a, b) => a.label.localeCompare(b.label));
}
function threadPickerHTML(chosen, skip = new Set(), opts = {}) {
  return `<input type="search" class="thd-q" placeholder="Find a teammate" aria-label="Find a teammate">
    <div class="thd-picks" role="group" aria-label="Teammates">${threadAddable(opts).filter(t => !skip.has(t.actor)).map(t =>
      `<label class="dash-pick-row thd-pick" data-label="${esc(t.label.toLowerCase())}"><input type="checkbox" name="member" value="${esc(t.actor)}"${chosen.includes(t.actor) ? ' checked' : ''}>${actorAvatar(t.actor, 20)}<span>${esc(t.label)}</span></label>`).join('')
      || '<p class="muted">Nobody to add</p>'}</div>`;
}
function threadPickerWire(root) {
  const q = root.querySelector('.thd-q');
  if (q) q.oninput = () => {
    const w = q.value.trim().toLowerCase();
    root.querySelectorAll('.thd-pick').forEach(l => { l.hidden = !!w && !l.dataset.label.includes(w) && !l.querySelector('input').checked; });
  };
}
// `titled`: the dialog's own title already says Who can read.
function threadAudienceHTML(t = null, {titled = false} = {}) {
  const groups = S.orgGroups || [], aud = t?.audience || 'members';
  return `<fieldset class="thd-aud"${titled ? ' aria-label="Who can read"' : ''}>${titled ? '' : '<legend>Who can read</legend>'}
    <label class="dash-pick-row"><input type="radio" name="audience" value="members"${aud === 'members' ? ' checked' : ''}>Only members</label>
    ${groups.length ? `<label class="dash-pick-row"><input type="radio" name="audience" value="team"${aud === 'team' ? ' checked' : ''}><span class="thd-aud-word">A team</span>
      <select name="group" aria-label="Team">${groups.map(g => `<option value="${esc(g.id)}"${t?.audience_group_id === g.id ? ' selected' : ''}>${esc(g.name || g.id)}</option>`).join('')}</select></label>` : ''}
    <label class="dash-pick-row"><input type="radio" name="audience" value="company"${aud === 'company' ? ' checked' : ''}>Company</label></fieldset>`;
}
function threadDialog(id, label) {
  let dlg = document.getElementById(id);
  if (!dlg) {
    dlg = document.createElement('dialog'); dlg.id = id; dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog'; document.body.append(dlg);
    // A route change (a link, the phone's Back) leaves the page the dialog was for.
    addEventListener('hashchange', () => { if (dlg.open) dlg.close(); });
  }
  dlg.setAttribute('aria-label', label);
  return dlg;
}
const threadDialogHead = title => `<header class="tmodal-head"><h2>${esc(title)}</h2><span class="spacer"></span>
  <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>`;
const threadDialogFoot = action => `<footer class="dash-form-foot"><span class="err" role="status" data-status></span><span class="spacer"></span>
  <button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">${esc(action)}</button></footer>`;
function threadDialogWire(dlg) { dlg.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dlg.close(); }); }

// ----------------------------------------------------------------- new thread
// A named thread: its members, who else sees it (only them, a group, everyone) and a pin for each member. Every thread
// needs a name; a group chat (two or more others) says so. A DM (`dm`, from a teammate's page) is the DM form.
const NT_X = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
function threadNewOpen({dm = false, members = []} = {}) {
  if (dm) return threadDmOpen(members);
  const dlg = threadDialog('thread-new', 'New thread');
  dlg.className = 'nt-dialog';
  const groups = (S.orgGroups || []).filter(g => g.id);
  const chosen = [...members];
  let audience = 'members', hit = 0;
  dlg.innerHTML = `<form class="nt" novalidate>
    <div class="nt-head"><h2>New thread</h2><button type="button" class="nt-x" data-close aria-label="Close">${NT_X}</button></div>
    <div class="nt-body">
      <div class="nt-field"><label class="nt-lbl" for="nt-add">Members</label>
        <div class="nt-chips"><span class="nt-chiplist"></span><input id="nt-add" type="text" placeholder="Add" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="nt-sugg"></div>
        <ul class="nt-sugg" id="nt-sugg" role="listbox" aria-label="Teammates" hidden></ul></div>
      <div class="nt-field"><label class="nt-lbl" for="nt-name">Name</label>
        <div class="nt-name"><span aria-hidden="true">#</span><input id="nt-name" name="title" maxlength="200" placeholder="launch-plan" autocomplete="off" aria-describedby="nt-why"></div>
        <span class="nt-why" id="nt-why" role="status"></span></div>
      <fieldset class="nt-field"><legend class="nt-lbl">Who sees it</legend>
        <div class="nt-seg">${[['members', 'Members'], groups.length && ['team', 'A group'], ['company', 'Everyone']].filter(Boolean).map(([k, label]) =>
          `<button type="button" data-aud="${k}" aria-pressed="${k === audience}">${label}</button>`).join('')}</div>
        ${groups.length ? `<select class="nt-group" name="group" aria-label="Group" hidden>${groups.map(g => `<option value="${esc(g.id)}">${esc(g.name || g.id)}</option>`).join('')}</select>` : ''}</fieldset>
      <label class="nt-check"><input type="checkbox" name="pin" checked><span>Pin for members</span></label>
    </div>
    <div class="nt-foot"><span class="err" role="status" data-status></span><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary" disabled>Create</button></div></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form'), add = form.querySelector('#nt-add'), sugg = form.querySelector('.nt-sugg');
  const name = form.querySelector('#nt-name'), why = form.querySelector('.nt-why'), submit = form.querySelector('[type=submit]'), status = form.querySelector('[data-status]');
  const label = a => threadAddable().find(t => t.actor === a)?.label || actorName(a);
  const check = () => {
    const title = name.value.trim().replace(/^#+/, '').trim();
    why.textContent = !title && chosen.length >= 2 ? 'Group chats need a name.' : '';
    submit.disabled = !title;
  };
  const drawChips = () => {
    form.querySelector('.nt-chiplist').innerHTML = chosen.map(a => `<span class="nt-chip" data-actor="${esc(a)}">${actorAvatar(a, 18)}<span>${esc(label(a))}</span><button type="button" data-remove aria-label="Remove ${esc(label(a))}">×</button></span>`).join('');
    check();
  };
  const options = () => {
    const w = add.value.trim().toLowerCase();
    return threadAddable().filter(t => !chosen.includes(t.actor) && (!w || t.label.toLowerCase().includes(w))).slice(0, 8);
  };
  const drawSugg = (open = true) => {
    const list = open ? options() : [];
    hit = Math.min(hit, Math.max(0, list.length - 1));
    sugg.innerHTML = list.map((t, i) => `<li role="option" id="nt-opt-${i}" data-actor="${esc(t.actor)}" aria-selected="${i === hit}">${actorAvatar(t.actor, 20)}<span>${esc(t.label)}</span></li>`).join('');
    sugg.hidden = !list.length;
    add.setAttribute('aria-expanded', String(!!list.length));
    if (list.length) add.setAttribute('aria-activedescendant', 'nt-opt-' + hit); else add.removeAttribute('aria-activedescendant');
  };
  const pick = actor => { if (actor && !chosen.includes(actor)) chosen.push(actor); add.value = ''; hit = 0; drawChips(); drawSugg(); add.focus(); };
  add.oninput = () => { hit = 0; drawSugg(); };
  add.onfocus = () => drawSugg();
  add.onblur = () => setTimeout(() => drawSugg(false), 120);
  add.onkeydown = ev => {
    const n = sugg.querySelectorAll('li').length;
    if (ev.key === 'ArrowDown' && n) { ev.preventDefault(); hit = (hit + 1) % n; drawSugg(); }
    else if (ev.key === 'ArrowUp' && n) { ev.preventDefault(); hit = (hit - 1 + n) % n; drawSugg(); }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (n) pick(sugg.querySelectorAll('li')[hit]?.dataset.actor); }
    else if (ev.key === 'Backspace' && !add.value && chosen.length) { chosen.pop(); drawChips(); drawSugg(); }
    else if (ev.key === 'Escape' && !sugg.hidden) { ev.preventDefault(); ev.stopPropagation(); drawSugg(false); }
  };
  sugg.onmousedown = ev => { ev.preventDefault(); pick(ev.target.closest('[data-actor]')?.dataset.actor); };
  form.querySelector('.nt-chips').onclick = ev => {
    const b = ev.target.closest('[data-remove]');
    if (b) { chosen.splice(chosen.indexOf(b.closest('[data-actor]').dataset.actor), 1); drawChips(); }
    add.focus();
  };
  form.querySelector('.nt-seg').onclick = ev => {
    const b = ev.target.closest('[data-aud]'); if (!b) return;
    audience = b.dataset.aud;
    form.querySelectorAll('[data-aud]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    const group = form.querySelector('.nt-group'); if (group) group.hidden = audience !== 'team';
  };
  name.oninput = check;
  form.onsubmit = async ev => {
    ev.preventDefault();
    const title = name.value.trim().replace(/^#+/, '').trim();
    if (!title) { check(); return; }
    submit.disabled = true; status.textContent = '';
    try {
      const r = await post('/v2/threads', {title, audience, audience_group_id: audience === 'team' ? form.elements.namedItem('group')?.value || null : null,
        members: chosen, pin_for_members: form.elements.namedItem('pin').checked});
      const t = r?.thread || r;
      dlg.close();
      if (t?.id) { threadsPut(t); location.hash = threadHref(t); }
    } catch (e) {
      const code = e.body?.error?.code || e.body?.code;
      if (code === 'name_required') why.textContent = 'Group chats need a name.';
      else status.textContent = e.message;
      submit.disabled = false;
    }
  };
  drawChips();
  dlg.showModal();
  (chosen.length ? name : add).focus();
}
// A DM with the chosen teammates (always private), reusing the open one with exactly them.
function threadDmOpen(members = []) {
  const dlg = threadDialog('thread-new', 'New DM');
  dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog';
  dlg.innerHTML = `<form>${threadDialogHead('New DM')}
    <div class="dash-form">
      <div class="thd-members"><span class="thd-lbl">With</span>${threadPickerHTML(members, new Set(), {groups: false})}</div>
      <label>Message<textarea name="body" rows="3"></textarea></label></div>
    ${threadDialogFoot('Start')}</form>`;
  threadDialogWire(dlg); threadPickerWire(dlg);
  const form = dlg.querySelector('form');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const status = form.querySelector('[data-status]');
    const chosen = [...form.querySelectorAll('[name=member]:checked')].map(i => i.value);
    const body = form.elements.namedItem('body').value.trim();
    if (!chosen.length) { status.textContent = 'Choose someone'; return; }
    const submit = form.querySelector('[type=submit]'); submit.disabled = true; status.textContent = '';
    try {
      const r = await post('/v2/threads/dm', {members: chosen, ...(body ? {body} : {})});
      const t = r?.thread || r;
      if (r?.held) toast('Your first message was held by a rule', true);
      dlg.close();
      if (t?.id) { threadsPut(t); location.hash = threadHref(t); }
    } catch (e) { status.textContent = e.message; submit.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('.thd-q')?.focus();
}

// ----------------------------------------------------------------- members
async function threadMembersOpen(view) {
  const t = view.thread; if (!t) return;
  const dlg = threadDialog('thread-members', 'Members');
  const manage = !!t.me?.can_manage;
  // On a public thread everyone reads already: its members are the bots added to it.
  const onlyBots = ['company', 'team'].includes(t.audience);
  dlg.innerHTML = `<form>${threadDialogHead('Members')}<div class="dash-form"><ul class="thd-list"><li class="muted">Loading…</li></ul>
    ${t.me?.can_post ? `<details class="thd-add"><summary>${TL_ICON.plus}Add</summary><div class="thd-add-body"></div></details>` : ''}</div>
    <footer class="dash-form-foot"><span class="err" role="status" data-status></span><span class="spacer"></span><button type="button" class="ghost" data-close>Done</button>${
      t.me?.can_post ? '<button class="primary" type="submit" hidden>Add</button>' : ''}</footer></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form'), status = form.querySelector('[data-status]');
  let members = [];
  const draw = () => {
    form.querySelector('.thd-list').innerHTML = members.map(m => `<li>${actorAvatar(m.actor, 22)}<span class="thd-name">${esc(actorName(m.actor))}</span>${
      m.role === 'owner' ? '<span class="thd-tag">Owner</span>' : ''}${
      manage && m.actor !== myActor() && m.role !== 'owner' ? `<button type="button" class="ghost small" data-remove="${esc(m.actor)}">Remove</button>` : ''}</li>`).join('')
      || '<li class="muted">Nobody</li>';
    const add = form.querySelector('.thd-add-body');
    const people = onlyBots ? threadAddable().map(a => a.actor).filter(a => !a.startsWith('bot:')) : [];
    if (add) { add.innerHTML = threadPickerHTML([], new Set([...members.map(m => m.actor), ...people]), {groups: !threadIsDm(t)}); threadPickerWire(add); }
  };
  const load = async () => {
    const r = await get(thvPath(t.id) + '/members?limit=100', {fresh: true});
    members = r.members || [];
    draw();
  };
  form.querySelector('.thd-add')?.addEventListener('toggle', ev => { form.querySelector('[type=submit]').hidden = !ev.currentTarget.open; });
  form.querySelector('.thd-list').onclick = async ev => {
    const b = ev.target.closest('[data-remove]'); if (!b) return;
    b.disabled = true;
    try { await writeRequest('DELETE', thvPath(t.id) + '/members/' + encodeURIComponent(b.dataset.remove)); await load(); }
    catch (e) { status.textContent = e.message; b.disabled = false; }
  };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const chosen = [...form.querySelectorAll('[name=member]:checked')].map(i => i.value);
    if (!chosen.length) return;
    status.textContent = '';
    try { for (const actor of chosen) await post(thvPath(t.id) + '/members', {actor}); await load(); }
    catch (e) { status.textContent = e.message; }
  };
  dlg.showModal();
  try { await load(); } catch (e) { status.textContent = e.message; }
}

// ----------------------------------------------------------------- thread settings
// The ⋯ in a thread's header: one panel of rows, each saving as it changes, with nothing to Save. A row the viewer may not
// use is left out.
const THREAD_NOTIFY = [['live', 'All'], ['mentions', 'Mentions'], ['digest', 'Digest'], ['off', 'Off']];
// Off is a lasting mute (an unfollow comes back on the next post or mention). Not following, or muted, reads as Off.
const threadNotifyNow = t => !t.me?.following || t.me?.notify === 'muted' ? 'off' : t.me?.notify || 'live';
function threadSettingsHTML(view) {
  const t = view.thread, manage = !!t.me?.can_manage, root = !t.parent_id, dm = threadIsDm(t), home = t.audience === 'home';
  const oneToOne = dm && root && !threadIsGroupDm(t);   // a group DM and a sub-thread keep their name
  const members = root && ['members', 'personal', 'company', 'team'].includes(t.audience);
  const n = view.memberList ? view.memberList.length + (view.memberMore ? '+' : '') : '';
  const now = threadNotifyNow(t), days = t.retention_days ?? '';
  const lengths = [['', 'Keep forever'], [30, '30 days'], [90, '90 days'], [365, '1 year']];
  if (!lengths.some(([d]) => d === days)) lengths.push([days, `${days} days`]);
  return [
    manage && !oneToOne && !home && `<div class="ths-row ths-wide"><label class="ths-lbl" for="ths-name">Name</label>
      <input id="ths-name" name="title" maxlength="200" autocomplete="off" value="${esc(threadTitle(t))}"></div>`,
    manage && root && !home && !dm && `<div class="ths-row ths-wide"><span class="ths-lbl">Who can read</span>${threadAudienceHTML(t, {titled: true})}</div>
      <div class="ths-row ths-wide tcn-host"></div>`,
    members && `<button type="button" class="ths-row ths-link" data-ths="members"${n ? ` aria-label="Members, ${n}"` : ''}><span class="ths-lbl">Members</span>
      <span class="ths-val">${n}<span aria-hidden="true">›</span></span></button>`,
    `<div class="ths-row ths-wide"><span class="ths-lbl" id="ths-notify">Notifications</span><div class="nt-seg ths-seg" role="group" aria-labelledby="ths-notify">${
      THREAD_NOTIFY.map(([k, label]) => `<button type="button" data-notify="${k}" aria-pressed="${k === now}">${label}</button>`).join('')}</div></div>`,
    manage && 'pinned_for_everyone' in t && threadOnRail(t) && `<label class="ths-row" for="ths-pin"><span class="ths-lbl">Pin for everyone</span>
      <input id="ths-pin" type="checkbox" role="switch" class="people-switch"${t.pinned_for_everyone ? ' checked' : ''}></label>`,
    manage && root && !['task', 'meeting'].includes(t.audience) && `<div class="ths-row"><label class="ths-lbl" for="ths-keep">Keep messages</label>
      <select id="ths-keep">${lengths.map(([d, label]) => `<option value="${d}"${d === days ? ' selected' : ''}>${label}</option>`).join('')}</select></div>`,
    `<div class="ths-row ths-acts"><button type="button" class="ghost small" data-ths="catchup">Summarize</button>
      <button type="button" class="ghost small" data-ths="read">Mark all read</button>${
      t.earlier ? '<button type="button" class="ghost small" data-ths="earlier">Open earlier messages to Read</button>' : ''}${
      manage && !home ? `<span class="spacer"></span><button type="button" class="ghost small" data-ths="archive">${t.archived_at ? 'Unarchive' : 'Archive'}</button>` : ''}</div>`,
  ].filter(Boolean).join('');
}
function threadSettingsOpen(view) {
  if (!view.thread) return;
  const dlg = threadDialog('thread-settings', 'Thread settings');
  dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog ths-dialog';
  dlg.innerHTML = `<form>${threadDialogHead('Thread settings')}<div class="dash-form ths"></div>
    <footer class="dash-form-foot"><span class="err" role="status" data-status></span><span class="spacer"></span><button type="button" class="primary" data-close>Done</button></footer></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form'), box = form.querySelector('.ths'), status = form.querySelector('[data-status]');
  const draw = () => {
    box.innerHTML = threadSettingsHTML(view);
    renderThreadConnect(view.thread, box.querySelector('.tcn-host'));   // Slack (ui/app/thread-connect.js)
  };
  // `send` answers only what changed (with `me` as its own part), merged into the thread as it is when the answer
  // comes; saves go one at a time, so a slow one cannot put back what a later one changed. `rail`: the change shows on
  // the rail too. A refused change puts the panel back as the server has it.
  let queue = Promise.resolve();
  const save = (send, {rail = true} = {}) => (queue = queue.then(async () => {
    status.textContent = '';
    try {
      const {me, ...changed} = await send(thvPath(view.thread.id)) || {};
      view.thread = {...view.thread, ...changed, me: {...view.thread.me, ...me}};
      if (rail) threadsPut(view.thread);
      thvDraw(view);
      return true;
    } catch (e) { status.textContent = e.message; draw(); return false; }
  }));
  form.onsubmit = ev => ev.preventDefault();
  form.addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.id === 'ths-name') { ev.preventDefault(); ev.target.blur(); } });
  form.onchange = ev => {
    const el = ev.target;
    if (el.id === 'ths-name') {
      const title = el.value.trim();
      if (!title || title === threadTitle(view.thread)) { el.value = threadTitle(view.thread); return; }
      void save(async path => patch(path, {title}));
    } else if (el.closest('.thd-aud')) {
      const audience = box.querySelector('[name=audience]:checked')?.value || 'members', sel = box.querySelector('[name=group]');
      if (el.name === 'group' && audience !== 'team') return;
      // Switching to A team with more than one to choose from waits for the team, so history is never shared with
      // one nobody picked.
      if (el.name === 'audience' && audience === 'team' && view.thread.audience !== 'team' && sel && [...sel.options].filter(o => o.value).length > 1) {
        if (!sel.querySelector('option[value=""]')) sel.insertAdjacentHTML('afterbegin', '<option value="">Choose a team</option>');
        sel.value = ''; sel.focus();
        return;
      }
      const group = audience === 'team' ? sel?.value || '' : null;
      if (audience === 'team' && !group) return;
      const focus = el.name;
      void save(async path => patch(path, {audience, audience_group_id: group})).then(ok => {
        if (!ok) return;
        draw();   // the Slack row goes with who can read
        box.querySelector(focus === 'group' ? '[name=group]' : '[name=audience]:checked')?.focus();
      });
    } else if (el.id === 'ths-pin') {
      void save(async path => patch(path, {pinned_for_everyone: el.checked}));
    } else if (el.id === 'ths-keep') {
      const days = el.value ? Number(el.value) : null;
      void save(async path => ({retention_days: days, ...(await patch(path, {retention_days: days}))}), {rail: false});
    }
  };
  form.onclick = async ev => {
    const seg = ev.target.closest('[data-notify]');
    if (seg) {
      const notify = seg.dataset.notify;
      if (seg.getAttribute('aria-pressed') === 'true') return;
      const level = notify === 'off' ? 'muted' : notify;
      const ok = await save(async path => {
        const r = await put(path + '/follow', {notify: level});
        return {me: {following: true, notify: r?.notify || level}};
      });
      if (ok) box.querySelectorAll('[data-notify]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.notify === notify)));
      return;
    }
    const b = ev.target.closest('[data-ths]'); if (!b) return;
    const key = b.dataset.ths, t = view.thread;
    if (key === 'members') { void threadMembersOpen(view); return; }
    // The rest are one-off actions: each closes the panel, back to the thread its toast is about.
    b.disabled = true; status.textContent = '';
    try {
      if (key === 'catchup') { dlg.close(); await threadCatchUp(t); return; }
      if (key === 'earlier') { dlg.close(); await threadEarlierOpen(view, view.id); return; }
      if (key === 'read') { await post(thvPath(t.id) + '/read-all', {}); threadsMarkedRead(t.id, Number(t.last_seq) || 0); dlg.close(); return; }
      if (key === 'archive' && await save(async path => patch(path, {archived: !view.thread.archived_at}))) dlg.close();
    } catch (e) { if (dlg.open) status.textContent = e.message; else toast(e.message, true); }
    finally { b.disabled = false; }
  };
  draw();
  dlg.showModal();
}
// A summary of what is new here and under it, in the viewer's own Assistant DM (backend/thread_catchup.py).
async function threadCatchUp(t) {
  toast('Summarizing…');
  const r = await post(thvPath(t.id) + '/catch-up', {scope: 'subtree'});
  if (r?.posted) toast('Summary ready', false, {label: 'Open', run: () => { location.hash = ASSISTANT; }});
  else toast(r?.words || 'Nothing new');
}
// A bot's manager opens its Home's closed history to the Home's readers, from the Home or from the notice in a DM.
async function threadEarlierOpen(view, homeId) {
  await post(thvPath(homeId) + '/open-earlier', {});
  toast('Earlier messages open');
  if (homeId === view.id) await thvRefresh(view);
}
