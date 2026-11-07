/* ui/app/thread-connect.js — A thread's Slack link, drawn in its Who can read sheet: an admin links the thread to a Slack
   channel or unlinks it (GET/POST/DELETE /api/v2/slack/links, backend/slack_threads.py). renderThreadConnect(thread, el)
   draws into `el`, and hides it for anyone who may not link. The rows here are shared with Slack's page under Tools
   (ui/app/redesign-library.js lcSlackLinks).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */

'use strict';

const TCN_SLACK = {requested: ['Waiting', 'wait'], active: ['Live', 'ok'], suspended: ['Suspended', 'fail'], refused: ['Refused', 'fail'],
                   unlinked: ['Unlinked', 'idle']};
const tcnAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
// Slack links a root thread whose audience is the company (a public channel) or its members (a private one).
const tcnSlackLinkable = t => !!t && !t.parent_id && ['company', 'members'].includes(t.audience);
const tcnChannel = link => '#' + String(link?.channel?.name || link?.channel?.id || '').replace(/^#/, '');
const tcnStatusHTML = ([label, tone]) => `<span class="cn-status cn-${tone}"><span class="cn-dot" aria-hidden="true"></span>${esc(label)}</span>`;

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

// A thread's title for a row that names only its id: from the sidebar's lists, else the thread itself. A thread this
// reader may not open has no title to show.
const TCN_TITLES = new Map();
async function tcnThreadTitle(id) {
  const known = [...(THREADS.pinned || []), ...(THREADS.threads || [])].find(t => t?.id === id);
  if (known) return threadTitle(known);
  if (!TCN_TITLES.has(id)) TCN_TITLES.set(id, get('/v2/threads/' + encodeURIComponent(id)).then(threadTitle, () => 'Thread'));
  return TCN_TITLES.get(id);
}
// ----------------------------------------------------------------- the thread's sheet
// `thread` is the thread view (docs/dev/threads-contract.md §10). Draws nothing, and hides `el`, for anyone who is not an
// admin, or a thread Slack does not apply to.
function renderThreadConnect(thread, el) {
  if (!el) return;
  const slack = tcnAdmin() && !!thread && !thread.parent_id;
  el.hidden = !slack;
  if (el.hidden) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="tcn"><section class="tcn-sec" data-tcn="slack" hidden><h3 class="thd-lbl">Slack</h3><div class="tcn-body"></div></section></div>`;
  // The sheet is a form: Enter in one of these fields does its own action, never the sheet's Save.
  el.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' || !ev.target.closest('.tcn input')) return;
    ev.preventDefault();
    ev.target.closest('.tcn-sec')?.querySelector('[data-tcn-go]')?.click();
  });
  void tcnSlackDraw(thread, el.querySelector('[data-tcn="slack"]'));
}

async function tcnSlackDraw(thread, sec) {
  const body = sec.querySelector('.tcn-body');
  const fail = e => { sec.hidden = false; body.innerHTML = `<span class="err" role="status">${esc(e.message)}</span>`; };
  let link, slackOn;
  try {
    const r = await get('/v2/slack/links?limit=1&thread=' + encodeURIComponent(thread.id), {fresh: true});
    link = r.links?.[0] || null; slackOn = r.available !== false;
  } catch (e) { fail(e); return; }
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
  // With Slack off a link has nothing to carry it; Connections says how to turn it on.
  sec.hidden = !slackOn || !tcnSlackLinkable(thread);
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
