/* ui/app/threads-page.js — The Threads page (#/threads, ?view=dms, ?with=<actor>), one thread's page (#/thread/<id>),
   New thread, a thread's Members and menu, and Make task on a message.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let THREAD_PAGE = null;
function pageThread(id) {
  const main = $('#main');
  main.classList.add('chat-layout', 'thread-layout');
  if (THREAD_PAGE?.id === id && THREAD_PAGE.host.isConnected) return;
  threadUnmount(THREAD_PAGE?.view);
  main.innerHTML = '<div class="thread-page" id="thread-page"></div>';
  const host = $('#thread-page');
  THREAD_PAGE = {id, host, view: threadMount(host, {id})};
}

// ----------------------------------------------------------------- the list
async function pageThreads() {
  const q = new URLSearchParams(S.route.split('?')[1] || '');
  const withActor = q.get('with') || '', dms = q.get('view') === 'dms';
  const title = dms ? 'DMs' : withActor ? `Threads with ${actorName(withActor)}` : 'Threads';
  const main = $('#main');
  main.innerHTML = `<div class="threads-page"><header class="threads-head"><h1>${esc(title)}</h1><span class="spacer"></span>
      <button type="button" class="ghost thp-search" id="th-search" aria-label="Search" title="Search"><span class="nav-icon" aria-hidden="true">search</span></button>
      <button type="button" class="primary" data-thp-new>${dms ? 'New DM' : 'New thread'}</button></header>
    ${withActor ? `<p class="thp-filter"><a class="linkish" href="${THREADS_ROUTE}">All threads</a></p>` : ''}
    <div id="threads-all"><div class="muted">Loading…</div></div>
    <button type="button" class="ghost small" id="threads-more" hidden>More</button></div>`;
  $('#th-search').onclick = () => searchOpen();
  main.querySelector('[data-thp-new]').onclick = () => threadNewOpen({dm: dms, members: withActor ? [withActor] : []});
  const route = S.route;
  const st = {attention: [], threads: [], dms: [], next: null};
  const load = async cursor => {
    const p = new URLSearchParams({limit: '50'});
    if (cursor) p.set('cursor', cursor);
    if (withActor) p.set('with', withActor);
    const r = await get('/v2/threads?' + p, {fresh: true});
    if (S.route !== route) return;
    for (const key of ['attention', 'threads', 'dms']) {
      const have = new Set(st[key].map(t => t.id));
      st[key].push(...(r[key] || []).filter(t => !have.has(t.id)));
    }
    st.next = r.next_cursor || null;
    draw();
  };
  const section = (head, list, opts) => list.length ? `<section class="thp-sec">${head ? `<h2>${esc(head)}</h2>` : ''}<ul class="th-list thp-list">${
    list.map(t => threadRowHTML(t, opts).replace('</a></li>', `<time class="muted thp-at">${esc(ago(t.last_message_at))}</time></a></li>`)).join('')}</ul></section>` : '';
  const draw = () => {
    const seen = new Set(st.attention.map(t => t.id));
    const html = dms ? section('', threadsOrder(st.dms))
      : section('Needs attention', st.attention, {attention: true}) + section(st.attention.length ? 'Threads' : '', threadsOrder(st.threads, seen))
        + section('DMs', threadsOrder(st.dms, seen));
    $('#threads-all').innerHTML = html || `<p class="empty">${dms ? 'No DMs yet' : 'No threads yet'}</p>`;
    $('#threads-more').hidden = !st.next;
  };
  $('#threads-more').onclick = ev => { ev.currentTarget.disabled = true; load(st.next).finally(() => { ev.currentTarget.disabled = false; }); };
  try { await load(''); }
  catch (e) { if (S.route === route) $('#threads-all').innerHTML = `<p class="empty">${esc(e.status === 404 ? 'Threads are not on this server yet' : e.message)}</p>`; }
}

// ----------------------------------------------------------------- choosing teammates
// Whom the viewer may add: a bot they may write to, a person they may write to (everyone, unless the server says
// otherwise). The helpers and the viewer are never listed.
function threadAddable() {
  const bots = shownEmps().filter(e => e.can_chat !== false && (!e.my_access || e.my_access.write))
    .map(e => ({actor: 'bot:' + e.name, label: e.display_name || e.name}));
  const people = (S.people || []).filter(p => p.id && !p.hidden && p.id !== S.me?.id && p.my_access?.write !== false)
    .map(p => ({actor: 'human:' + p.id, label: p.name || p.id}));
  return [...people, ...bots].sort((a, b) => a.label.localeCompare(b.label));
}
function threadPickerHTML(chosen, skip = new Set()) {
  return `<input type="search" class="thd-q" placeholder="Find a teammate" aria-label="Find a teammate">
    <div class="thd-picks" role="group" aria-label="Teammates">${threadAddable().filter(t => !skip.has(t.actor)).map(t =>
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
function threadAudienceHTML(t = null) {
  const groups = S.orgGroups || [], aud = t?.audience || 'members';
  return `<fieldset class="thd-aud"><legend>Who can read</legend>
    <label class="dash-pick-row"><input type="radio" name="audience" value="members"${aud === 'members' ? ' checked' : ''}>Only members</label>
    ${groups.length ? `<label class="dash-pick-row"><input type="radio" name="audience" value="team"${aud === 'team' ? ' checked' : ''}>A team
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
// Private to the chosen members unless the starter opens it to a team or the company. A DM is two or more members,
// always private.
function threadNewOpen({dm = false, members = []} = {}) {
  const dlg = threadDialog('thread-new', dm ? 'New DM' : 'New thread');
  dlg.innerHTML = `<form>${threadDialogHead(dm ? 'New DM' : 'New thread')}
    <div class="dash-form">
      ${dm ? '' : '<label>Title<input name="title" maxlength="200" autocomplete="off"></label>'}
      <div class="thd-members"><span class="thd-lbl">${dm ? 'With' : 'Members'}</span>${threadPickerHTML(members)}</div>
      ${dm ? '' : threadAudienceHTML()}
      <label>Message<textarea name="body" rows="3"></textarea></label></div>
    ${threadDialogFoot(dm ? 'Start' : 'Create')}</form>`;
  threadDialogWire(dlg); threadPickerWire(dlg);
  const form = dlg.querySelector('form');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const status = form.querySelector('[data-status]');
    const chosen = [...form.querySelectorAll('[name=member]:checked')].map(i => i.value);
    const body = form.elements.namedItem('body').value.trim();
    const audience = dm ? 'personal' : form.querySelector('[name=audience]:checked')?.value || 'members';
    if (dm && !chosen.length) { status.textContent = 'Choose someone'; return; }
    const submit = form.querySelector('[type=submit]'); submit.disabled = true; status.textContent = '';
    try {
      const r = dm ? await post('/v2/threads/dm', {members: chosen, ...(body ? {body} : {})})
        : await post('/v2/threads', {title: form.elements.namedItem('title').value.trim(), audience,
          audience_group_id: audience === 'team' ? form.elements.namedItem('group')?.value || null : null, members: chosen, ...(body ? {body} : {})});
      const t = r?.thread || r;
      if (r?.held) toast('Your first message was held by a rule', true);
      dlg.close();
      if (t?.id) { threadsPut(t); location.hash = threadHref(t); }
    } catch (e) { status.textContent = e.message; submit.disabled = false; }
  };
  dlg.showModal();
  (form.elements.namedItem('title') || form.querySelector('.thd-q'))?.focus();
}

// ----------------------------------------------------------------- members
async function threadMembersOpen(view) {
  const t = view.thread; if (!t) return;
  const dlg = threadDialog('thread-members', 'Members');
  const manage = !!t.me?.can_manage;
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
    if (add) { add.innerHTML = threadPickerHTML([], new Set(members.map(m => m.actor))); threadPickerWire(add); }
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
    t.me?.following ? ['unfollow', 'Unfollow'] : ['follow', 'Follow'],
    ['read', 'Mark all read'],
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
      else if (key === 'follow') { await put(path + '/follow', {notify: 'live'}); view.thread = {...t, me: {...t.me, following: true}}; }
      else if (key === 'unfollow') { await writeRequest('DELETE', path + '/follow'); view.thread = {...t, me: {...t.me, following: false}}; }
      else if (key === 'read') { await post(path + '/read-all', {}); threadsMarkedRead(t.id, Number(t.last_seq) || 0); }
      else if (key === 'archive' || key === 'unarchive') view.thread = {...t, ...(await patch(path, {archived: key === 'archive'}))};
      threadsPut(view.thread);
      thvDraw(view);
    } catch (e) { toast(e.message, true); }
  });
}
function threadShareOpen(view) {
  const t = view.thread;
  const dlg = threadDialog('thread-share', 'Who can read');
  dlg.innerHTML = `<form>${threadDialogHead('Who can read')}<div class="dash-form">${threadAudienceHTML(t)}</div>${threadDialogFoot('Save')}</form>`;
  threadDialogWire(dlg);
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

// ----------------------------------------------------------------- make task
// A task from a message: its text, a link back to the message, and an owner the viewer may give work to.
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
    const link = `${location.origin}${location.pathname}${threadHref(m.thread_id || view.id)}`;
    const body = `${m.body || ''}\n\n[From ${actorName(m.from_actor)} in ${threadTitle(view.thread) || 'a thread'}](${link})`;
    try {
      const r = await post('/v2/tasks', {title, body, owner: form.elements.namedItem('owner').value});
      dlg.close();
      const id = r?.task?.id || r?.id;
      toast('Task made', false, id ? {label: 'Open', run: () => { location.hash = '#/task/' + id; }} : undefined);
    } catch (e) { form.querySelector('[data-status]').textContent = e.message; }
  };
  dlg.showModal();
  form.elements.namedItem('title').select();
}
