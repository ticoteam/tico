/* ui/app/threads-page.js — New thread and New DM, a thread's Members and menu, and Make task on a message. The Threads
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
  if (!dlg) { dlg = document.createElement('dialog'); dlg.id = id; dlg.className = 'tmodal tmodal-shell dash-dialog thd-dialog'; document.body.append(dlg); }
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
      if (t?.id) { threadsPut(t); threadsRecount(); location.hash = threadHref(t); }
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
    ${t.me?.can_post ? '<details class="thd-add"><summary>Add</summary><div class="thd-add-body"></div></details>' : ''}</div>
    <footer class="dash-form-foot"><span class="err" role="status" data-status></span><span class="spacer"></span><button type="button" class="ghost" data-close>Done</button>${
      t.me?.can_post ? '<button class="primary" type="submit" hidden>Add</button>' : ''}</footer></form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form'), status = form.querySelector('[data-status]');
  let members = [];
  const draw = () => {
    form.querySelector('.thd-list').innerHTML = members.map(m => `<li>${actorAvatar(m.actor, 22)}<span>${esc(actorName(m.actor))}</span>${
      m.role === 'owner' ? '<span class="muted">Owner</span>' : ''}<span class="spacer"></span>${
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

// ----------------------------------------------------------------- the thread menu
function threadMenuOpen(view, button) {
  const t = view.thread; if (!t) return;
  const manage = !!t.me?.can_manage, root = !t.parent_id;
  const items = [
    manage && ['rename', 'Rename'],
    manage && root && t.audience !== 'home' && !threadIsDm(t) && ['share', 'Who can read'],
    manage && root && !['task', 'meeting'].includes(t.audience) && ['keep', 'Keep messages'],
    manage && 'pinned_for_everyone' in t && threadOnRail(t) && (t.pinned_for_everyone ? ['unpin-all', 'Unpin for everyone'] : ['pin-all', 'Pin for everyone']),
    t.me?.following ? ['unfollow', 'Unfollow'] : ['follow', 'Follow'],
    ['notify', 'Notifications'],
    ['catchup', 'Summarize'],
    ['read', 'Mark all read'],
    t.earlier && ['earlier', 'Open earlier messages to Read'],
    manage && t.audience !== 'home' && [t.archived_at ? 'unarchive' : 'archive', t.archived_at ? 'Unarchive' : 'Archive'],
  ].filter(Boolean);
  thvMenuShow(button, items, async key => {
    const path = thvPath(t.id);
    try {
      if (key === 'rename') {
        const title = prompt('Title', threadTitle(t));
        if (title == null || !title.trim()) return;
        view.thread = {...t, ...(await patch(path, {title: title.trim()}))};
      } else if (key === 'share') { threadShareOpen(view); return; }
      else if (key === 'keep') { threadKeepOpen(view); return; }
      else if (key === 'notify') { threadNotifyOpen(view, button); return; }
      else if (key === 'catchup') { await threadCatchUp(t); return; }
      else if (key === 'earlier') { await threadEarlierOpen(view, view.id); return; }
      else if (key === 'follow') { await put(path + '/follow', {notify: 'live'}); view.thread = {...t, me: {...t.me, following: true}}; }
      else if (key === 'unfollow') { await writeRequest('DELETE', path + '/follow'); view.thread = {...t, me: {...t.me, following: false}}; }
      else if (key === 'read') { await post(path + '/read-all', {}); threadsMarkedRead(t.id, Number(t.last_seq) || 0); }
      else if (key === 'pin-all' || key === 'unpin-all') {
        view.thread = {...t, ...(await patch(path, {pinned_for_everyone: key === 'pin-all'}))};
        threadsRecount();
      }
      else if (key === 'archive' || key === 'unarchive') view.thread = {...t, ...(await patch(path, {archived: key === 'archive'}))};
      threadsPut(view.thread);
      thvDraw(view);
    } catch (e) { toast(e.message, true); }
  });
}
// Per reader, this thread only: how much of it reaches them. Choosing one follows the thread.
const THREAD_NOTIFY = [['live', 'Live'], ['mentions', 'Mentions'], ['digest', 'Digest'], ['muted', 'Muted']];
function threadNotifyOpen(view, button) {
  const now = view.thread?.me?.notify || 'live';
  thvMenuShow(button, THREAD_NOTIFY.map(([k, label]) => [k, label, k === now]), async notify => {
    const t = view.thread;
    try {
      const r = await put(thvPath(t.id) + '/follow', {notify});
      view.thread = {...t, me: {...t.me, following: true, notify: r?.notify || notify}};
      threadsPut(view.thread); thvDraw(view);
    } catch (e) { toast(e.message, true); }
  });
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
function threadShareOpen(view) {
  const t = view.thread;
  const dlg = threadDialog('thread-share', 'Who can read');
  dlg.innerHTML = `<form>${threadDialogHead('Who can read')}<div class="dash-form">${threadAudienceHTML(t, {titled: true})}<div class="tcn-host"></div></div>${threadDialogFoot('Save')}</form>`;
  threadDialogWire(dlg);
  renderThreadConnect(t, dlg.querySelector('.tcn-host'));   // Slack and Shared with (ui/app/thread-connect.js)
  const form = dlg.querySelector('form');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const audience = form.querySelector('[name=audience]:checked')?.value || 'members';
    try {
      const r = await patch(thvPath(t.id), {audience, audience_group_id: audience === 'team' ? form.elements.namedItem('group')?.value || null : null});
      view.thread = {...t, ...r}; threadsPut(view.thread); thvDraw(view);
      dlg.close();
    } catch (e) { form.querySelector('[data-status]').textContent = e.message; }
  };
  dlg.showModal();
}

// How long a thread keeps its messages (PATCH retention_days; null keeps them). A length set some other way stays listed.
function threadKeepOpen(view) {
  const t = view.thread, now = t.retention_days ?? '';
  const lengths = [['', 'Keep forever'], [30, '30 days'], [90, '90 days'], [365, '1 year']];
  if (!lengths.some(([days]) => days === now)) lengths.push([now, `${now} days`]);
  const dlg = threadDialog('thread-keep', 'Keep messages');
  dlg.innerHTML = `<form>${threadDialogHead('Keep messages')}<div class="dash-form"><select name="days" aria-label="Keep messages">${
    lengths.map(([days, label]) => `<option value="${days}"${days === now ? ' selected' : ''}>${label}</option>`).join('')}</select></div>${threadDialogFoot('Save')}</form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const days = form.elements.namedItem('days').value;
    try {
      const r = await patch(thvPath(t.id), {retention_days: days ? Number(days) : null});
      view.thread = {...t, retention_days: days ? Number(days) : null, ...r}; thvDraw(view);
      dlg.close();
    } catch (e) { form.querySelector('[data-status]').textContent = e.message; }
  };
  dlg.showModal();
}

// ----------------------------------------------------------------- make task
// A task from a message: the server copies the message, keeps its privacy and links back; the viewer picks the title
// and an owner they may give work to.
function threadMakeTask(m, view) {
  const dlg = threadDialog('thread-task', 'Make task');
  const first = v2Firstline(m.body).slice(0, 120);
  const owners = [['human:' + (S.me?.id || ''), 'Me'], ...shownEmps().filter(e => e.can_chat !== false && (!e.my_access || e.my_access.write))
    .map(e => [e.name, e.display_name || e.name])];
  dlg.innerHTML = `<form>${threadDialogHead('Make task')}<div class="dash-form">
      <label>Title<input name="title" maxlength="300" required value="${esc(first)}"></label>
      <label>Owner<select name="owner">${owners.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></label></div>
    ${threadDialogFoot('Make task')}</form>`;
  threadDialogWire(dlg);
  const form = dlg.querySelector('form');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const title = form.elements.namedItem('title').value.trim(); if (!title) return;
    try {
      const r = await post('/v2/threads/messages/' + encodeURIComponent(m.id) + '/task', {title, owner: form.elements.namedItem('owner').value});
      dlg.close();
      const id = r?.task?.id || r?.id;
      toast('Task made', false, id ? {label: 'Open', run: () => { location.hash = '#/task/' + id; }} : undefined);
    } catch (e) {
      const status = form.querySelector('[data-status]'), dup = e.body?.error?.code === 'duplicate', twin = dup && e.body.error.task_id;
      status.textContent = dup ? 'A task already asks for this' : e.message;
      if (twin) status.innerHTML = `<a href="#/task/${encodeURIComponent(twin)}" data-close>A task already asks for this</a>`;
      threadDialogWire(dlg);
    }
  };
  dlg.showModal();
  form.elements.namedItem('title').select();
}
