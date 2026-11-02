/* ui/app/thread-connect.js — A thread's links outside this Tico, drawn in its Who can read sheet: Slack, where an admin
   links the thread to a Slack channel or unlinks it (GET/POST/DELETE /api/v2/slack/links, backend/slack_threads.py), and
   Shared with, where an admin shares it with another company's Tico or stops (GET /api/v2/federation and
   /api/v2/federation/threads/{id}/installs, backend/federation_routes.py), and whoever manages the thread can retry what
   that company has not taken. renderThreadConnect(thread, el) draws into `el`, and hides it for anyone who may do none
   of these. The rows, labels and remote-author helpers here are shared with
   Library › Connections (ui/app/connections.js) and the timeline (ui/app/thread-view.js).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TCN_SLACK = {requested: ['Waiting', 'wait'], active: ['Live', 'ok'], suspended: ['Suspended', 'fail'], refused: ['Refused', 'fail'],
                   unlinked: ['Unlinked', 'idle']};
const TCN_GUEST = /^(human|bot):([^@\s]+)@(gi_[0-9a-f]{24})$/;
const tcnAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Slack links a root thread whose audience is the company (a public channel) or its members (a private one).
const tcnSlackLinkable = t => !!t && !t.parent_id && ['company', 'members'].includes(t.audience);
// Another company's Tico gets a members thread only.
const tcnShareable = t => !!t && !t.parent_id && t.audience === 'members';
const tcnChannel = link => '#' + String(link?.channel?.name || link?.channel?.id || '').replace(/^#/, '');
const tcnStatusHTML = ([label, tone]) => `<span class="cn-status cn-${tone}"><span class="cn-dot" aria-hidden="true"></span>${esc(label)}</span>`;

// ----------------------------------------------------------------- other companies' people
// Another company's Tico by name. The server knows only its id (gi_ and 24 hex digits) unless a peering carries a name.
function connectInstallName(install, named = null) {
  const name = named?.name || named?.display_name || '';
  const id = String(install || '');
  return name || (id.length > 12 ? id.slice(0, 9) + '…' : id);
}
const connectIsGuest = actor => TCN_GUEST.test(String(actor || ''));
// A teammate on another company's Tico (`human:ana@gi_…`): "Ana · Company" and a small guest tag.
function connectGuestHTML(actor) {
  const m = String(actor || '').match(TCN_GUEST);
  if (!m) return esc(actorName(actor));
  return `<span class="tcn-who" title="${esc(m[3])}">${esc(titleCase(m[2]))} · ${esc(connectInstallName(m[3]))}</span><span class="tcn-guest">guest</span>`;
}
// Who wrote a message: a message carried in from another company's Tico is stored as the keeper's, with the real
// author in refs.origin.actor.
const connectAuthor = m => (m?.from_actor === 'keeper' && connectIsGuest(m?.refs?.origin?.actor)) ? m.refs.origin.actor : m?.from_actor;

// ----------------------------------------------------------------- rows
function tcnSlackRowHTML(link, title, {manage = true} = {}) {
  const state = TCN_SLACK[link.state] || [link.state, 'idle'];
  const resume = ['suspended', 'refused'].includes(link.state);
  return `<li class="cn-row cn-xrow" data-link="${esc(link.id)}">
    <span class="nav-icon" aria-hidden="true">tag</span>
    <span class="cn-name"><strong>${title == null ? '' : `${esc(title)} → `}${esc(tcnChannel(link))}</strong>${
      link.reason && link.state !== 'active' ? `<span class="muted">${esc(link.reason)}</span>` : ''}</span>
    ${tcnStatusHTML(state)}
    <span class="cn-acts">${manage ? `${resume ? '<button type="button" class="ghost small" data-slack-resume>Resume</button>' : ''}
      <button type="button" class="ghost small" data-slack-unlink>Unlink</button>` : ''}</span></li>`;
}
// Unlink a Slack link, or ask the gateway to check a suspended or refused one again.
function tcnSlackAct(link, act) {
  const path = '/v2/slack/links/' + encodeURIComponent(link.id);
  return act === 'resume' ? post(path + '/resume', {}) : writeRequest('DELETE', path);
}
// A peering's state as people read it: Requested until this side approves (or, once it has, until the other side does),
// then Approved; Paused by either side; Revoked.
function tcnPeerState(p) {
  if (p.status === 'revoked') return ['Revoked', 'idle'];
  if (p.paused) return ['Paused', 'wait'];
  if (p.status === 'active') return ['Approved', 'ok'];
  return ['Requested', 'wait'];
}
function tcnPeerRowHTML(p) {
  const live = p.status !== 'revoked';
  const acts = !live ? '' : [
    p.status === 'pending' && !p.approved && '<button type="button" class="ghost small" data-peer="approve">Approve</button>',
    // Resume lifts this side's pause only; a pause the other side set is theirs to lift.
    p.paused ? p.paused_by === 'you' && '<button type="button" class="ghost small" data-peer="resume">Resume</button>'
      : p.status === 'active' && '<button type="button" class="ghost small" data-peer="pause">Pause</button>',
    '<button type="button" class="ghost small" data-peer="revoke">Revoke</button>',
  ].filter(Boolean).join('');
  const waiting = p.status === 'pending' && p.approved ? 'Waiting for them' : p.paused && p.paused_by === 'peer' ? 'Paused by them' : '';
  return `<li class="cn-row cn-xrow" data-peering="${esc(p.id)}">
    <span class="nav-icon" aria-hidden="true">handshake</span>
    <span class="cn-name"><strong class="mono" title="${esc(p.peer)}">${esc(p.peer)}</strong>${waiting ? `<span class="muted">${esc(waiting)}</span>` : ''}</span>
    ${tcnStatusHTML(tcnPeerState(p))}
    <span class="cn-acts">${acts}</span></li>`;
}
// One peering action: approve, pause, resume or revoke. Answers the peering as the server now has it.
async function tcnPeerAct(id, act) {
  const path = '/v2/federation/peerings/' + encodeURIComponent(id);
  if (act === 'approve') return post(path + '/approve', {});
  if (act === 'pause' || act === 'resume') return post(path + '/pause', {paused: act === 'pause'});
  if (act === 'revoke') return post(path + '/revoke', {});
  throw new Error('Unknown action');
}

// A thread's title for a row that names only its id: from the sidebar's lists, else the thread itself. A thread this
// reader may not open has no title to show.
const TCN_TITLES = new Map();
async function tcnThreadTitle(id) {
  const known = [...(THREADS.attention || []), ...(THREADS.threads || []), ...(THREADS.dms || [])].find(t => t?.id === id);
  if (known) return threadTitle(known);
  if (!TCN_TITLES.has(id)) TCN_TITLES.set(id, get('/v2/threads/' + encodeURIComponent(id)).then(threadTitle, () => 'Thread'));
  return TCN_TITLES.get(id);
}

// ----------------------------------------------------------------- the thread's sheet
// `thread` is the thread view (docs/dev/threads-contract.md §10). Draws nothing, and hides `el`, for a reader who neither
// manages the thread nor is an admin, or a thread neither Slack nor Shared with applies to.
function renderThreadConnect(thread, el) {
  if (!el) return;
  const slack = tcnAdmin() && !!thread && !thread.parent_id;
  const shared = (tcnAdmin() || !!thread?.me?.can_manage) && tcnShareable(thread);
  el.hidden = !slack && !shared;
  if (el.hidden) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="tcn">${slack ? '<section class="tcn-sec" data-tcn="slack" hidden><h3 class="thd-lbl">Slack</h3><div class="tcn-body"></div></section>' : ''}${
    shared ? '<section class="tcn-sec" data-tcn="shared"><h3 class="thd-lbl">Shared with</h3><div class="tcn-body"><span class="muted">Loading…</span></div></section>' : ''}</div>`;
  // The sheet is a form: Enter in one of these fields does its own action, never the sheet's Save.
  el.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' || !ev.target.closest('.tcn input')) return;
    ev.preventDefault();
    ev.target.closest('.tcn-sec')?.querySelector('[data-tcn-go]')?.click();
  });
  if (slack) void tcnSlackDraw(thread, el.querySelector('[data-tcn="slack"]'));
  if (shared) void tcnSharedDraw(thread, el.querySelector('[data-tcn="shared"]'));
}

async function tcnSlackDraw(thread, sec) {
  const body = sec.querySelector('.tcn-body');
  const fail = e => { sec.hidden = false; body.innerHTML = `<span class="err" role="status">${esc(e.message)}</span>`; };
  let link;
  try { link = (await get('/v2/slack/links?limit=1&thread=' + encodeURIComponent(thread.id), {fresh: true})).links?.[0] || null; }
  catch (e) { fail(e); return; }
  if (!sec.isConnected) return;
  if (link) {
    sec.hidden = false;
    body.innerHTML = `<ul class="cn-list tcn-list">${tcnSlackRowHTML(link, null)}</ul><span class="err" role="status" data-tcn-err></span>`;
    body.querySelector('ul').onclick = async ev => {
      const b = ev.target.closest('button'); if (!b) return;
      const drop = b.matches('[data-slack-unlink]');
      if (drop && !confirm(`Unlink ${tcnChannel(link)}?`)) return;
      b.disabled = true;
      try { await tcnSlackAct(link, drop ? 'unlink' : 'resume'); await tcnSlackDraw(thread, sec); }
      catch (e) { body.querySelector('[data-tcn-err]').textContent = e.message; b.disabled = false; }
    };
    return;
  }
  sec.hidden = !tcnSlackLinkable(thread);
  if (sec.hidden) return;
  body.innerHTML = `<div class="tcn-form"><input name="tcn-channel" placeholder="#channel" aria-label="Slack channel" maxlength="200" autocomplete="off">
      <label class="tcn-check"><input type="checkbox" name="tcn-history">Copy history</label>
      <button type="button" class="ghost small" data-tcn-go>Link</button></div><span class="err" role="status" data-tcn-err></span>`;
  body.querySelector('[data-tcn-go]').onclick = async ev => {
    const b = ev.currentTarget, channel = body.querySelector('[name=tcn-channel]').value.trim();
    const err = body.querySelector('[data-tcn-err]');
    if (!channel) { err.textContent = 'Name a channel'; return; }
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/slack/links', {thread_id: thread.id, channel, history: body.querySelector('[name=tcn-history]').checked});
      await tcnSlackDraw(thread, sec);
    } catch (e) { err.textContent = e.message; b.disabled = false; }
  };
}

// Someone who manages the thread and is no admin: Needs you names the companies it has not reached, and Retry is theirs.
function tcnUnsentDraw(thread, sec) {
  const body = sec.querySelector('.tcn-body');
  const stuck = (S.v2.needs || []).filter(it => it.kind === 'thread_unsent' && it.conversation_id === thread.id)
    .map(it => String(it.id).split(':peer:')[1]).filter(Boolean);
  sec.hidden = !stuck.length;
  if (sec.hidden) return;
  body.innerHTML = `<ul class="cn-list tcn-list">${stuck.map(install => `<li class="cn-row cn-xrow" data-install="${esc(install)}">
      <span class="nav-icon" aria-hidden="true">handshake</span><span class="cn-name"><strong class="mono" title="${esc(install)}">${esc(connectInstallName(install))}</strong></span>
      <span class="cn-status"></span><span class="cn-acts"><button type="button" class="ghost small" data-tcn-retry>Retry</button></span></li>`).join('')}</ul>
    <span class="err" role="status" data-tcn-err></span>`;
  body.querySelector('ul').onclick = async ev => {
    const b = ev.target.closest('[data-tcn-retry]'); if (!b) return;
    const row = b.closest('[data-install]'), err = body.querySelector('[data-tcn-err]');
    b.disabled = true; err.textContent = '';
    try {
      await post('/v2/federation/threads/' + encodeURIComponent(thread.id) + '/installs/' + encodeURIComponent(row.dataset.install) + '/retry', {});
      row.remove();
      if (!body.querySelector('[data-install]')) sec.hidden = true;
    } catch (e) { err.textContent = e.message; b.disabled = false; }
  };
}

async function tcnSharedDraw(thread, sec) {
  if (!tcnAdmin()) { tcnUnsentDraw(thread, sec); return; }
  const body = sec.querySelector('.tcn-body');
  let fed;
  try { fed = await get('/v2/federation', {fresh: true}); }
  catch (e) { body.innerHTML = `<span class="err" role="status">${esc(e.message)}</span>`; return; }
  if (!sec.isConnected) return;
  const entry = (fed.threads || []).find(t => t.thread_id === thread.id);
  const installs = '/v2/federation/threads/' + encodeURIComponent(thread.id) + '/installs';
  // Sends again what that company has not taken (Needs you lists it).
  const retry = (b, install, err) => {
    b.disabled = true; err.textContent = '';
    post(installs + '/' + encodeURIComponent(install) + '/retry', {}).then(() => tcnSharedDraw(thread, sec), e => { err.textContent = e.message; b.disabled = false; });
  };
  if (entry && !entry.home) {
    const stuck = entry.unsent?.[entry.home_install];
    body.innerHTML = `<span class="muted">Shared by <span class="mono" title="${esc(entry.home_install)}">${esc(connectInstallName(entry.home_install))}</span></span>${
      stuck ? ' <button type="button" class="ghost small" data-tcn-retry>Retry</button> <span class="err" role="status" data-tcn-err></span>' : ''}`;
    const again = body.querySelector('[data-tcn-retry]');
    if (again) again.onclick = () => retry(again, entry.home_install, body.querySelector('[data-tcn-err]'));
    return;
  }
  const members = (entry?.members || []).filter(m => m.status === 'active');
  const inside = new Set(members.map(m => m.install));
  const open = (fed.peerings || []).filter(p => p.status === 'active' && !p.paused && !inside.has(p.peer));
  body.innerHTML = `<ul class="cn-list tcn-list">${members.map(m => `<li class="cn-row cn-xrow" data-install="${esc(m.install)}">
      <span class="nav-icon" aria-hidden="true">handshake</span><span class="cn-name"><strong class="mono" title="${esc(m.install)}">${esc(m.install)}</strong></span>
      <span class="cn-status"></span><span class="cn-acts">${entry?.unsent?.[m.install] ? '<button type="button" class="ghost small" data-tcn-retry>Retry</button>' : ''}<button type="button" class="ghost small" data-tcn-remove>Remove</button></span></li>`).join('')
      || '<li class="muted tcn-none">Only this company</li>'}</ul>
    ${open.length ? `<div class="tcn-form"><select name="tcn-install" aria-label="Company">${open.map(p =>
      `<option value="${esc(p.peer)}">${esc(p.peer)}</option>`).join('')}</select><button type="button" class="ghost small" data-tcn-go>Add</button></div>`
      : members.length ? '' : `<a class="small" href="#/library/connections">Connect a company</a>`}
    <span class="err" role="status" data-tcn-err></span>`;
  const err = body.querySelector('[data-tcn-err]');
  const path = installs;
  body.querySelector('ul').onclick = async ev => {
    const again = ev.target.closest('[data-tcn-retry]');
    if (again) { retry(again, again.closest('[data-install]').dataset.install, err); return; }
    const b = ev.target.closest('[data-tcn-remove]'); if (!b) return;
    const install = b.closest('[data-install]').dataset.install;
    if (!confirm(`Stop sharing with ${install}?`)) return;
    b.disabled = true; err.textContent = '';
    try { await writeRequest('DELETE', path + '/' + encodeURIComponent(install)); await tcnSharedDraw(thread, sec); }
    catch (e) { err.textContent = e.message; b.disabled = false; }
  };
  const go = body.querySelector('[data-tcn-go]');
  if (go) go.onclick = async () => {
    go.disabled = true; err.textContent = '';
    try { await post(path, {install: body.querySelector('[name=tcn-install]').value}); await tcnSharedDraw(thread, sec); }
    catch (e) { err.textContent = e.message; go.disabled = false; }
  };
}
