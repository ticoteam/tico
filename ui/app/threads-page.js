/* ui/app/threads-page.js — The Threads page (#/threads, ?view=dms, ?with=<actor>), one thread's page (#/thread/<id>),
   New thread, a thread's Members and menu, and Make task on a message.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

let THREAD_PAGE = null;
// `?m=<message id>` (a search hit) opens the thread at that message.
function pageThread(id) {
  const main = $('#main'), focus = new URLSearchParams(S.route.split('?')[1] || '').get('m') || '';
  main.classList.add('chat-layout', 'thread-layout');
  if (THREAD_PAGE?.id === id && THREAD_PAGE.host.isConnected) {
    if (focus && THREAD_PAGE.view.state === 'loading') THREAD_PAGE.view.opts.focus = focus;
    else if (focus) void thvFocus(THREAD_PAGE.view, focus);
    return;
  }
  threadUnmount(THREAD_PAGE?.view);
  main.innerHTML = '<div class="thread-page" id="thread-page"></div>';
  const host = $('#thread-page');
  THREAD_PAGE = {id, host, view: threadMount(host, {id, ...(focus ? {focus} : {})})};
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
    <div class="thp-searchbar" hidden><input type="search" id="thp-q" placeholder="Search threads" aria-label="Search threads" autocomplete="off"></div>
    <div id="thp-hits" hidden></div>
    <div id="threads-all"><div class="muted">Loading…</div></div>
    <button type="button" class="ghost small" id="threads-more" hidden>More</button></div>`;
  threadSearchWire(main);
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

// ----------------------------------------------------------------- search
// Words in the threads the viewer may read (GET /v2/threads/search); a hit opens its thread at that message.
function threadSearchWire(main) {
  const bar = main.querySelector('.thp-searchbar'), q = $('#thp-q'), hits = $('#thp-hits');
  const route = S.route;
  let n = 0, timer = 0, next = null;
  const draw = (list, more, add) => {
    const html = list.map(h => `<li><a class="thp-hit" href="${threadHref(h.thread_id)}?m=${encodeURIComponent(h.message_id)}">
        <span class="thp-hit-head"><strong>${esc(h.title || 'Thread')}</strong><span class="muted">${esc(actorName(h.from_actor))}</span>
        <time class="muted">${esc(ago(h.created))}</time></span><span class="thp-hit-x">${esc(h.excerpt || '')}</span></a></li>`).join('');
    if (add) hits.querySelector('.thp-hits').insertAdjacentHTML('beforeend', html);
    else hits.innerHTML = list.length ? `<ul class="thp-hits">${html}</ul>` : '<p class="empty">No matches</p>';
    hits.querySelector('[data-thp-more]')?.remove();
    if (more) hits.insertAdjacentHTML('beforeend', '<button type="button" class="ghost small" data-thp-more>More</button>');
  };
  const run = async (add = false) => {
    const words = q.value.trim(), mine = ++n;
    hits.hidden = !words; main.querySelector('.threads-page')?.classList.toggle('searching', !!words);
    if (!words) return;
    const p = new URLSearchParams({q: words, limit: '20'});
    if (add && next) p.set('before', next);
    try {
      const r = await get('/v2/threads/search?' + p, {fresh: true});
      if (mine !== n || S.route !== route) return;
      next = r.next_cursor || null;
      draw(r.hits || [], !!next, add);
    } catch (e) { if (mine === n) hits.innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
  };
  q.oninput = () => { clearTimeout(timer); timer = setTimeout(() => void run(), 250); };
  q.onkeydown = ev => { if (ev.key === 'Escape') { q.value = ''; void run(); bar.hidden = true; } };
  hits.onclick = ev => { if (ev.target.closest('[data-thp-more]')) void run(true); };
  $('#th-search').onclick = () => {
    bar.hidden = !bar.hidden;
    if (bar.hidden) { q.value = ''; void run(); } else q.focus();
  };
}

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
      <div class="thd-members"><span class="thd-lbl">${dm ? 'With' : 'Members'}</span>${threadPickerHTML(members, new Set(), {groups: !dm})}</div>
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
    t.me?.following ? ['unfollow', 'Unfollow'] : ['follow', 'Follow'],
    ['notify', 'Notifications'],
    ['catchup', 'Catch up'],
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
  toast('Catching up…');
  const r = await post(thvPath(t.id) + '/catch-up', {scope: 'subtree'});
  if (r?.posted) toast('Catch-up ready', false, {label: 'Open', run: () => { location.hash = ASSISTANT; }});
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
  dlg.innerHTML = `<form>${threadDialogHead('Who can read')}<div class="dash-form">${threadAudienceHTML(t)}<div class="tcn-host"></div></div>${threadDialogFoot('Save')}</form>`;
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
      form.querySelector('[data-status]').textContent = e.body?.error?.code === 'duplicate' ? 'A task already asks for this' : e.message;
    }
  };
  dlg.showModal();
  form.elements.namedItem('title').select();
}
