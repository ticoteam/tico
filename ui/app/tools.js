/* ui/app/tools.js — Tools (#/tools): every way bots and people reach the outside world, one row each, and the router
   for every page under it. The list is GET /api/v2/tool-index (backend: one row an API tool, mailbox, phone number or
   Tico address, mailing address, Slack, GitHub, meeting importer, Listening, outgoing webhook, or a tool a bot
   declares with no row of its own); the hub filters the rows per reader and this page only draws them. Each row opens
   its `href`.
   Addresses:
     #/tools[?show=needs|api|mail|websites]          the list
     #/tools/api/<id>[/activity]                      one API tool (ui/app/tools-api.js)
     #/tools/mailbox/<address or id>[/activity]       one mailbox  ┐
     #/tools/number/<id>                              one Tico address or phone number │ the inbox kinds' pages
     #/tools/address/<id>, #/tools/address/outbox     one mailing address, letters out  │ (ui/app/inboxes*.js)
     #/tools/settings[/<section>], #/tools/messages   mail and number settings, texts   ┘
     #/tools/keys[/<service> | /credentials/<id>]     every credential, by tool (ui/app/redesign-library.js)
     #/tools/webhooks[/<id>]                          outgoing webhooks, and one (ui/app/settings-webhooks.js)
     #/tools/<service>                                a declared tool's page, Slack's and GitHub's (same file)
   The old homes of all of it redirect here (toolsRoute): Library › Tools and › Connections, Settings › Inboxes and
   the tabs it replaced, #/integrations and #/credentials.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const TOOLS_ROUTE = '#/tools';
const TOOLS_KEYS = '#/tools/keys';
// The list's filters: key, label, which rows, and the counts field that numbers it.
const TOOLS_FILTERS = [
  ['', 'All', () => true, 'all'],
  ['needs', 'Needs you', r => !!r.needs_you, 'needs_you'],
  ['api', 'APIs', r => r.kind === 'api', 'api'],
  ['mail', 'Mail and numbers', r => ['mailbox', 'number', 'address'].includes(r.kind), 'mail'],
  ['websites', 'Websites', r => r.kind === 'website', 'websites'],
];
// Icons the UI's font holds (ui/vendor/fonts/icons.txt).
const TOOLS_ICONS = {api: 'api', mailbox: 'mail', number: 'sms', address: 'local_shipping', slack: 'forum', github: 'code',
  meetings: 'co_present', listening: 'campaign', webhook: 'open_in_new', declared: 'build', website: 'travel_explore'};
// The hub's tones as the house pills (ui/styles/components.css).
const TOOLS_TONE = {ok: 'ok', wait: 'waiting', fail: 'fail', run: 'in-progress', idle: ''};
const TOOLS_WHO_SHOWN = 4;
let TL = null;   // {run, rows, counts, error, q, show}
const TOOLS_PRIVATE = ' <span class="pill tt-private" data-private>Private</span>';
const toolsPill = status => `<span class="pill${TOOLS_TONE[status?.tone] ? ' ' + TOOLS_TONE[status.tone] : ''} tt-status" data-tone="${esc(status?.tone || 'idle')}"><span class="tt-dot" aria-hidden="true"></span>${esc(status?.text || '')}</span>`;
// One face: a bot's avatar, or a person's photo or initials.
const toolsFace = (w, size = 22) => w.kind === 'bot' ? avatar(w.id, size)
  : personAvatar((S.people || []).find(p => p.id === w.id) || {id: w.id, name: w.name}, size);
// Who can use it: up to four faces, each named on hover, then "+N more".
function toolsWhoHTML(row) {
  const who = row.who || [], total = Math.max(row.who_total || 0, who.length);
  if (!total) return '<span class="muted">Nobody yet</span>';
  const shown = who.slice(0, TOOLS_WHO_SHOWN), more = total - shown.length;
  return `<span class="tt-faces">${shown.map(w => `<span class="tt-face" title="${esc(w.name || w.id)}">${toolsFace(w)}</span>`).join('')}</span>${
    more > 0 ? `<span class="muted tt-more">+${more} more</span>` : ''}`;
}

// ----------------------------------------------------------------- the list
async function pageTools() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const show = TOOLS_FILTERS.some(([k]) => k === params.get('show')) ? params.get('show') : '';
  const run = (TL?.run || 0) + 1;
  TL = {run, rows: TL?.rows || null, counts: TL?.counts || null, error: '', q: TL?.q || '', show};
  $('#main').innerHTML = `<div class="tt" id="tools-root">
    <header class="tt-head"><h1>Tools</h1><span class="spacer"></span>
      <a class="ghost small" href="${TOOLS_KEYS}" data-tl-keys>Keys</a><a class="ghost small" href="#/tools/settings" data-tl-settings>Settings</a>
      ${toolsAddMenu()}</header>
    <div class="tt-bar"><div class="theme-seg tt-filters" role="radiogroup" aria-label="Show"></div>
      <input type="search" class="tt-q" placeholder="Search" aria-label="Search tools" autocomplete="off" spellcheck="false" value="${esc(TL.q)}"></div>
    <div class="tt-table" data-tl-table>${TL.rows ? '' : skelRows(5)}</div></div>`;
  const root = $('#tools-root');
  root.querySelector('.tt-q').oninput = ev => { TL.q = ev.target.value; toolsDraw(); };
  root.querySelector('.tt-filters').onclick = ev => {
    const b = ev.target.closest('[data-tl-show]'); if (!b) return;
    TL.show = b.dataset.tlShow;
    history.replaceState(null, '', TOOLS_ROUTE + (TL.show ? '?show=' + TL.show : ''));
    S.route = LAST_ROUTE = location.hash;
    toolsDraw();
  };
  toolsAddBind(root);
  if (TL.rows) toolsDraw();
  try {
    const d = await get('/v2/tool-index', {fresh: true});
    if (!TL || TL.run !== run) return;
    TL.rows = d.rows || []; TL.counts = d.counts || null;
  } catch (e) { if (!TL || TL.run !== run) return; TL.error = e.message; TL.rows ||= []; }
  if (root.isConnected) toolsDraw();
}

function toolsDraw() {
  const root = $('#tools-root'); if (!root || !TL) return;
  const rows = TL.rows || [], counts = TL.counts || {};
  const count = (k, test) => counts[k] ?? rows.filter(test).length;
  // Websites shows once there is one.
  const filters = TOOLS_FILTERS.filter(([k, , test, c]) => k !== 'websites' || count(c, test) > 0 || TL.show === 'websites');
  root.querySelector('.tt-filters').innerHTML = filters.map(([k, label, test, c]) =>
    `<button type="button" role="radio" data-tl-show="${k}" aria-checked="${TL.show === k}">${esc(label)}${
      TL.rows ? ` <span class="tt-n tnum">${count(c, test)}</span>` : ''}</button>`).join('');
  const box = root.querySelector('[data-tl-table]');
  if (!TL.rows) return;
  const test = (TOOLS_FILTERS.find(([k]) => k === TL.show) || TOOLS_FILTERS[0])[2];
  const words = TL.q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = rows.filter(r => test(r) && words.every(w => [r.name, r.detail, r.way, r.status?.text, ...(r.who || []).map(x => x.name)]
    .join(' ').toLowerCase().includes(w)));
  const err = TL.error ? `<p class="err" data-tl-error>${esc(TL.error)}</p>` : '';
  if (!shown.length) {
    box.innerHTML = err + `<div class="empty" data-tl-empty>${rows.length ? 'Nothing matches.' : 'No tools yet.'}</div>`;
    return;
  }
  box.innerHTML = err + `<table class="tt-list"><thead><tr><th>Tool</th><th>Way in</th><th>Who can use it</th><th>Status</th><th aria-hidden="true"></th></tr></thead>
    <tbody>${shown.map(r => `<tr data-tl-row="${esc(r.id)}" data-tl-kind="${esc(r.kind)}"${r.needs_you ? ' data-tl-needs' : ''}>
      <td class="tt-tool"><a href="${esc(r.href || '#/tools')}"><span class="tt-tile" aria-hidden="true"><span class="nav-icon">${TOOLS_ICONS[r.kind] || 'link'}</span></span>
        <span class="tt-nm"><strong>${esc(r.name)}${r.private ? TOOLS_PRIVATE : ''}</strong>${r.detail ? `<span class="muted">${esc(r.detail)}</span>` : ''}</span></a></td>
      <td class="tt-way">${r.way ? `<span class="pill">${esc(r.way)}</span>` : ''}</td>
      <td class="tt-who">${toolsWhoHTML(r)}</td>
      <td class="tt-st">${toolsPill(r.status)}</td>
      <td class="tt-chev" aria-hidden="true">›</td></tr>`).join('')}</tbody></table>`;
  // The whole row opens the tool; its own links (none today) keep theirs.
  box.querySelector('tbody').onclick = ev => {
    if (ev.target.closest('a')) return;
    const tr = ev.target.closest('[data-tl-row]');
    const href = tr?.querySelector('.tt-tool a')?.getAttribute('href');
    if (href) location.hash = href;
  };
}

// ----------------------------------------------------------------- Add tool
// An API (POST /api-tools) for whoever manages keys, then each inbox kind's own Add (ui/app/inboxes*.js).
function toolsAddMenu() {
  const admin = settingsIsAdmin();
  const kinds = Object.values(INBOX_KINDS).filter(k => k.add && (!k.add.admin || admin)).sort((a, b) => a.order - b.order);
  const items = [...(S.me?.credential_access || admin ? [['api', 'API']] : []), ...kinds.map(k => ['kind:' + k.key, k.add.label]),
    ['webhook', 'Outgoing webhook']];
  if (!items.length) return '';
  return `<details class="tt-add" data-tl-add><summary class="primary small">Add tool</summary><div class="tt-add-menu" role="menu">
    ${items.map(([k, label]) => `<button type="button" role="menuitem" data-tl-add-item="${esc(k)}">${esc(label)}</button>`).join('')}</div></details>`;
}
function toolsAddBind(root) {
  root.querySelector('[data-tl-add]')?.addEventListener('click', ev => {
    const b = ev.target.closest('[data-tl-add-item]'); if (!b) return;
    b.closest('details').removeAttribute('open');
    const item = b.dataset.tlAddItem;
    if (item === 'api') { toolsApiCreate(); return; }
    if (item === 'webhook') { location.hash = WH_HOOKS_ROUTE; void whHookDialog(null); return; }
    INBOX_KINDS[item.slice(5)]?.add?.open(inboxesCtx(IB.run));
  });
}

// ----------------------------------------------------------------- routes
// The redesign's router asks first (ui/app/redesign.js). Old addresses are replaced, so Back skips them.
function toolsRoute(r) {
  const [path, query = ''] = r.split('?'), q = query ? '?' + query : '';
  const go = to => { location.replace(to); return true; };
  // Library › Tools and its old names: a tool's page, a credential's, or the list.
  for (const old of ['#/library/tools', INTEGRATIONS]) {
    if (!rdUnder(path, old)) continue;
    const rest = path.slice(old.length).replace(/^\/+/, '');
    if (!rest) return go(new URLSearchParams(query).has('who') ? TOOLS_KEYS + q : TOOLS_ROUTE);
    return go((rest.startsWith('credentials/') ? TOOLS_KEYS + '/' : TOOLS_ROUTE + '/') + rest + q);
  }
  if (rdUnder(path, '#/library/connections')) {
    const rest = path.slice('#/library/connections'.length).replace(/^\/+/, '');
    return go(rest ? TOOLS_ROUTE + '/' + rest : TOOLS_ROUTE);
  }
  if (rdUnder(path, CREDENTIALS)) return go(TOOLS_KEYS + '?show=credentials');
  // Settings › Inboxes and the tabs it replaced (Mailboxes, Email & Phone, Mail; Google's sign-in return).
  if (rdUnder(path, '#/settings/inboxes')) return go(toolsFromInboxes(path.slice('#/settings/inboxes'.length).replace(/^\/+/, '')));
  const oldTab = path.match(/^#\/settings\/(mailboxes|inbound|company-inbox|email-phone|mail|inbox|google)(?:\/([^/]+))?\/?$/);
  if (oldTab) {
    const [, tab, rest] = oldTab;
    return go(tab === 'mailboxes' ? (rest ? '#/tools/mailbox/' + rest : TOOLS_ROUTE + '?show=mail')
      : tab === 'mail' ? '#/tools/settings/letters-in' : ['inbox', 'google'].includes(tab) || rest === 'set-inbound' ? '#/tools/settings/email'
      : TOOLS_ROUTE + '?show=mail');
  }
  if (!rdUnder(path, TOOLS_ROUTE)) return false;
  const parts = path.slice(TOOLS_ROUTE.length).split('/').filter(Boolean).map(p => { try { return decodeURIComponent(p); } catch { return p; } });
  if (!parts.length) { pageTools(); return true; }
  if (parts[0] === 'api' && parts[1]) { pageToolApi(parts[1], parts[2] === 'activity' ? 'activity' : ''); return true; }
  if (parts[0] === 'keys') { pageRdTools(); return true; }
  if (parts[0] === 'webhooks') { pageToolWebhooks(parts[1] || ''); return true; }
  const view = inboxesRoute(parts);
  if (view !== null) { toolsInboxPage(view); return true; }
  pageToolService(parts.join('/'));
  return true;
}
// An old Settings › Inboxes address (what follows #/settings/inboxes/) at its home under Tools.
function toolsFromInboxes(rest) {
  const [head, id = ''] = rest.split('/');
  if (!head) return TOOLS_ROUTE + '?show=mail';
  if (head === 'settings' || INBOX_SECTIONS.some(([k]) => k === head)) {
    const section = head === 'settings' ? id : head;
    return '#/tools/settings' + (INBOX_SECTIONS.some(([k]) => k === section) ? '/' + section : '');
  }
  if (head === 'messages') return '#/tools/messages';
  if (INBOX_KINDS[head] && id) return inboxesLink(head, decodeURIComponent(id));
  return TOOLS_ROUTE + '?show=mail';
}
// One inbox kind's page, mail settings or Messages, drawn by ui/app/inboxes.js into a host kept while the address stays
// under the same page, so a redraw keeps what it drew.
function toolsInboxPage(view) {
  INBOXES_VIEW = view;
  if (!$('#tools-inbox')) $('#main').innerHTML = '<div class="tt tt-page" id="tools-inbox"></div>';
  void inboxesRender();
}
// Leaving Tools drops the list, so coming back reads the hub again.
function toolsStop() { TL = null; INBOXES_VIEW = ''; rdToolsStop(); toolApiStop(); }
