/* ui/app/connections.js — Library › Connections (#/library/connections): every way the outside world reaches the
   company, one row each, from GET /api/v2/connections (backend/connections.py): Slack, the GitHub App, mail bots'
   mailboxes, Email & Phone inboxes, meeting importers, incoming and outgoing webhooks, physical mail and Listening. A
   row says its status, whose it is, who reads it and its bots, and links to where it is set up (Slack's and GitHub's
   connect cards stay on their Tools pages). The hub filters the rows per reader; this page only draws them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const CN_ICONS = {slack: 'forum', github: 'code', mailbox: 'mail', inbox: 'inbox', meetings: 'videocam', trigger: 'webhook',
                  webhook: 'call_made', postal: 'local_post_office', listening: 'hearing'};
const CN_STATUS = {on: ['On', 'ok'], off: ['Off', 'idle'], waiting: ['Waiting', 'wait'], paused: ['Paused', 'wait'], error: ['Error', 'fail']};
const CN_SCOPE = {company: 'Company', team: 'Team', person: 'Personal'};
let CN_VIEW = 0;

function cnRowHTML(r) {
  const [label, tone] = CN_STATUS[r.status] || [r.status, 'idle'];
  const bots = r.bots.slice(0, 5).map(slug => `<a href="#/bot/${encodeURIComponent(slug)}" title="${esc(botDisplayName(slug))}">${avatar(slug, 20)}</a>`).join('');
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
    <div class="row cn-more" hidden><button class="ghost" type="button">More</button></div></div>`;
  const list = $('#cn-root .cn-list'), more = $('#cn-root .cn-more');
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
