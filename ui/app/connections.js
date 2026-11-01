/* ui/app/connections.js — Library › Connections (#/library/connections): every way the outside world reaches the
   company, one row each, from GET /api/v2/connections (backend/connections.py): Slack, the GitHub App, mail bots'
   mailboxes, Email & Phone inboxes, meeting importers, incoming and outgoing webhooks, physical mail and Listening. A
   row says its status, whose it is, who reads it and its bots, and links to where it is set up (Slack's and GitHub's
   connect cards stay on their Tools pages). The hub filters the rows per reader; this page only draws them.
   Below the list, admins see Slack (threads linked to Slack channels) and Other companies (this Tico's ID and key, and
   its peerings with other companies' Ticos); the shared rows are in ui/app/thread-connect.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Icons the UI's font holds (ui/vendor/fonts/icons.txt).
const CN_ICONS = {slack: 'forum', github: 'code', mailbox: 'mail', inbox: 'inbox', meetings: 'co_present', trigger: 'bolt',
                  webhook: 'open_in_new', postal: 'local_shipping', listening: 'campaign'};
const CN_STATUS = {on: ['On', 'ok'], off: ['Off', 'idle'], waiting: ['Waiting', 'wait'], paused: ['Paused', 'wait'], error: ['Error', 'fail']};
const CN_SCOPE = {company: 'Company', team: 'Team', person: 'Personal'};
let CN_VIEW = 0;

function cnRowHTML(r) {
  const [label, tone] = CN_STATUS[r.status] || [r.status, 'idle'];
  // Each bot is {slug, name}, only bots the reader may see (backend/connections.py).
  const bots = r.bots.slice(0, 5).map(({slug, name}) => `<a href="#/bot/${encodeURIComponent(slug)}" title="${esc(name || slug)}">${avatar(slug, 20)}</a>`).join('');
  const more = r.more_bots + Math.max(0, r.bots.length - 5);
  return `<li class="cn-row" data-id="${esc(r.id)}">
    <span class="nav-icon" aria-hidden="true">${CN_ICONS[r.kind] || 'link'}</span>
    <span class="cn-name"><strong>${esc(r.name)}</strong>${r.detail ? `<span class="muted">${esc(r.detail)}</span>` : ''}</span>
    <span class="cn-status cn-${tone}"><span class="cn-dot" aria-hidden="true"></span>${esc(label)}</span>
    <span class="cn-scope muted">${esc(CN_SCOPE[r.scope] || r.scope)}</span>
    <span class="cn-readers" title="Readers">${esc(r.readers)}</span>
    <span class="cn-bots">${bots}${more ? `<span class="muted">+${more}</span>` : ''}</span>
    <span class="cn-go">${r.can_configure ? `<a href="${esc(r.configure)}">Configure</a>` : ''}</span></li>`;
}

async function pageConnections() {
  const view = ++CN_VIEW, main = $('#main');
  main.classList.add('cn-layout');
  main.innerHTML = `<div class="cn" id="cn-root"><header class="lc-ph"><h1>Connections</h1><span class="spacer"></span>
      <a class="ghost small" href="${RD_TOOLS}">Tools</a></header>
    <ul class="cn-list" aria-label="Connections"><li>${skelRows(5)}</li></ul>
    <div class="row cn-more" hidden><button class="ghost" type="button">More</button></div>
    <section class="cn-sec" id="cn-slack" aria-label="Slack" hidden></section>
    <section class="cn-sec" id="cn-peers" aria-label="Other companies" hidden></section></div>`;
  const list = $('#cn-root .cn-list'), more = $('#cn-root .cn-more');
  void cnSlackSection(view);
  void cnPeersSection(view);
  let cursor = '', rows = [];
  const load = async () => {
    const data = await get('/v2/connections?limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
    if (view !== CN_VIEW || !list.isConnected) return;
    rows = rows.concat(data.connections);
    cursor = data.next || '';
    list.innerHTML = rows.length ? rows.map(cnRowHTML).join('') : '<li class="muted cn-none">No connections yet</li>';
    more.hidden = !cursor;
  };
  more.querySelector('button').onclick = () => void load().catch(e => { more.insertAdjacentHTML('beforeend', `<span class="err">${esc(e.message)}</span>`); });
  try { await load(); }
  catch (e) { if (view === CN_VIEW && list.isConnected) list.innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
}

// ----------------------------------------------------------------- Slack: threads linked to channels (admins only)
async function cnSlackSection(view) {
  const sec = $('#cn-slack');
  if (!sec || !tcnAdmin()) return;
  sec.hidden = false;
  sec.innerHTML = `<header class="cn-sec-head"><h2>Slack</h2><span class="spacer"></span>
      <button type="button" class="ghost small" data-cn-open aria-expanded="false">Link a thread</button></header>
    <div class="cn-form" hidden><select name="thread" aria-label="Thread"></select>
      <button type="button" class="ghost small" data-cn-threads hidden>More threads</button>
      <input name="channel" placeholder="#channel" aria-label="Slack channel" maxlength="200" autocomplete="off">
      <label class="tcn-check"><input type="checkbox" name="history">Copy history</label>
      <button type="button" class="primary small" data-cn-link>Link</button></div>
    <span class="err cn-err" role="status"></span>
    <ul class="cn-list" aria-label="Slack links"><li>${skelRows(2)}</li></ul>
    <div class="row cn-xmore" hidden><button class="ghost" type="button">More</button></div>`;
  const list = sec.querySelector('.cn-list'), more = sec.querySelector('.cn-xmore'), err = sec.querySelector('.cn-err');
  const form = sec.querySelector('.cn-form'), pick = form.querySelector('[name=thread]');
  const failed = e => { err.textContent = e.message; };
  let rows = [], cursor = '';
  const draw = async () => {
    const titles = await Promise.all(rows.map(l => tcnThreadTitle(l.thread_id)));
    if (view !== CN_VIEW || !sec.isConnected) return;
    list.innerHTML = rows.map((l, i) => tcnSlackRowHTML(l, titles[i])).join('') || '<li class="muted cn-none">No linked threads</li>';
  };
  const load = async (fresh = false) => {
    const data = await get('/v2/slack/links?limit=50' + (!fresh && cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
    if (view !== CN_VIEW || !sec.isConnected) return;
    rows = (fresh ? [] : rows).concat(data.links || []);
    cursor = data.next_cursor || '';
    more.hidden = !cursor;
    await draw();
  };
  more.querySelector('button').onclick = () => void load().catch(failed);
  // The threads a channel can be linked to: roots open to the company or to their members, a page at a time.
  let threadCursor = '', threadsRead = false;
  const offered = new Set();
  const loadThreads = async () => {
    const r = await get('/v2/threads?limit=50' + (threadCursor ? '&cursor=' + encodeURIComponent(threadCursor) : ''), {fresh: true});
    threadsRead = true;
    threadCursor = r.next_cursor || '';
    const linked = new Set(rows.map(l => l.thread_id));
    const add = [...(r.attention || []), ...(r.threads || [])]
      .filter(t => tcnSlackLinkable(t) && !t.archived_at && !offered.has(t.id) && !linked.has(t.id));
    add.forEach(t => offered.add(t.id));
    pick.insertAdjacentHTML('beforeend', add.map(t => `<option value="${esc(t.id)}">${esc(threadTitle(t))}</option>`).join(''));
    form.querySelector('[data-cn-threads]').hidden = !threadCursor;
    if (!offered.size && !threadCursor) pick.innerHTML = '<option value="" disabled selected>No threads to link</option>';
  };
  const toggle = open => { form.hidden = !open; sec.querySelector('[data-cn-open]').setAttribute('aria-expanded', String(open)); };
  sec.querySelector('[data-cn-open]').onclick = () => {
    toggle(form.hidden);
    if (form.hidden) return;
    if (!threadsRead) void loadThreads().catch(failed);
    form.querySelector('[name=channel]').focus();
  };
  form.querySelector('[data-cn-threads]').onclick = () => void loadThreads().catch(failed);
  form.querySelector('[name=channel]').onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); form.querySelector('[data-cn-link]').click(); } };
  form.querySelector('[data-cn-link]').onclick = async ev => {
    const b = ev.currentTarget, channel = form.querySelector('[name=channel]').value.trim();
    if (!pick.value) { err.textContent = 'Choose a thread'; return; }
    if (!channel) { err.textContent = 'Name a channel'; return; }
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/slack/links', {thread_id: pick.value, channel, history: form.querySelector('[name=history]').checked});
      pick.selectedOptions[0]?.remove();
      form.querySelector('[name=channel]').value = '';
      toggle(false);
      await load(true);
    } catch (e) { failed(e); }
    finally { b.disabled = false; }
  };
  list.onclick = async ev => {
    const b = ev.target.closest('button'); if (!b) return;
    const link = rows.find(l => l.id === b.closest('[data-link]')?.dataset.link); if (!link) return;
    const drop = b.matches('[data-slack-unlink]');
    if (drop && !confirm(`Unlink ${tcnChannel(link)}?`)) return;
    b.disabled = true; err.textContent = '';
    try { await tcnSlackAct(link, drop ? 'unlink' : 'resume'); await load(true); }
    catch (e) { failed(e); b.disabled = false; }
  };
  try { await load(); }
  catch (e) { if (view === CN_VIEW && sec.isConnected) { list.innerHTML = ''; failed(e); } }
}

// ----------------------------------------------------------------- Other companies: peerings (admins only)
// GET /api/v2/federation lists at most 50 peerings, newest first, and is not cursor-paged (docs/federation.md).
async function cnPeersSection(view) {
  const sec = $('#cn-peers');
  if (!sec || !tcnAdmin()) return;
  sec.hidden = false;
  sec.innerHTML = `<header class="cn-sec-head"><h2>Other companies</h2><span class="spacer"></span>
      <button type="button" class="ghost small" data-cn-open aria-expanded="false">Connect a company</button></header>
    <p class="cn-key"></p>
    <div class="cn-form" hidden><input name="peer" class="mono" placeholder="gi_…" aria-label="Their Tico ID" maxlength="40" autocomplete="off" spellcheck="false">
      <button type="button" class="primary small" data-cn-request>Request</button></div>
    <span class="err cn-err" role="status"></span>
    <ul class="cn-list" aria-label="Other companies"><li>${skelRows(2)}</li></ul>`;
  const list = sec.querySelector('.cn-list'), err = sec.querySelector('.cn-err'), keyLine = sec.querySelector('.cn-key');
  const form = sec.querySelector('.cn-form');
  const failed = e => { err.textContent = e.message; };
  let fed = {};
  const draw = () => {
    const f = fed.federation || {}, key = f.key;
    keyLine.innerHTML = !f.gateway ? '<span class="muted">Connect the Tico Gateway first</span> · <a href="#/settings/gateway">Gateway</a>'
      : `<span class="muted">ID</span> <span class="mono" data-cn-install>${esc(f.install || '')}</span>
        <span class="muted">· Key</span> ${key ? `<span class="mono" data-cn-fingerprint>${esc(key.key_id)}</span>${
          key.registered ? '' : ' <button type="button" class="ghost small" data-cn-key>Register</button>'}`
          : '<button type="button" class="ghost small" data-cn-key>Make key</button>'}`;
    list.innerHTML = (fed.peerings || []).map(tcnPeerRowHTML).join('') || '<li class="muted cn-none">No other companies</li>';
  };
  const load = async () => {
    const answer = await get('/v2/federation', {fresh: true});
    if (view !== CN_VIEW || !sec.isConnected) return;
    fed = answer || {};
    draw();
  };
  keyLine.onclick = async ev => {
    const b = ev.target.closest('[data-cn-key]'); if (!b) return;
    b.disabled = true; err.textContent = '';
    try { fed = await post('/v2/federation/keys', {}) || {}; draw(); }
    catch (e) { failed(e); b.disabled = false; }
  };
  const toggle = open => { form.hidden = !open; sec.querySelector('[data-cn-open]').setAttribute('aria-expanded', String(open)); };
  sec.querySelector('[data-cn-open]').onclick = () => { toggle(form.hidden); if (!form.hidden) form.querySelector('[name=peer]').focus(); };
  form.querySelector('[name=peer]').onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); form.querySelector('[data-cn-request]').click(); } };
  form.querySelector('[data-cn-request]').onclick = async ev => {
    const b = ev.currentTarget, peer = form.querySelector('[name=peer]').value.trim();
    if (!peer) { err.textContent = 'Paste their Tico ID'; return; }
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/federation/peerings', {peer});
      form.querySelector('[name=peer]').value = '';
      toggle(false);
      await load();
    } catch (e) { failed(e); }
    finally { b.disabled = false; }
  };
  list.onclick = async ev => {
    const b = ev.target.closest('[data-peer]'); if (!b) return;
    const id = b.closest('[data-peering]').dataset.peering, act = b.dataset.peer;
    const p = (fed.peerings || []).find(x => x.id === id);
    if (act === 'revoke' && !confirm(`Revoke ${p?.peer || 'this company'}? Connecting again needs both approvals.`)) return;
    b.disabled = true; err.textContent = '';
    try { await tcnPeerAct(id, act); await load(); }
    catch (e) { failed(e); b.disabled = false; }
  };
  try { await load(); }
  catch (e) { if (view === CN_VIEW && sec.isConnected) { list.innerHTML = ''; failed(e); } }
}
