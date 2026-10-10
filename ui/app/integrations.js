/* ui/app/integrations.js — Tools page and its credential dialogs
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- integrations
// One page per outside system (integrations/*.md, served by GET /api/v2/tools): what it
// is, how a bot uses it, the rules, the query list, and the learnings bots and humans add.
// Everyone signed in reads; anyone adds a learning; the owner deletes one.
const INTEGRATION_KIND = {api: 'API', sql: 'SQL', browser: 'Browser', mail: 'Email', cli: 'CLI', mcp: 'MCP'};
let INT_LOAD = 0, INT_ROWS = [], INT_CRED_SERVICE = '';
const credEnvs = texts => {
  const out = [];
  for (const t of texts || []) {
    for (const m of String(t).match(/\b[A-Z](?:[A-Z0-9_]|<[A-Z]+>){2,}/g) || []) if (!out.includes(m)) out.push(m);
  }
  return out;
};
// A credential in plain words: the saved credential's own name when the vault holds one for this tool, else what the
// variable is (CLOSE_API_KEY is an "API key", DB_<NAME>_URL a "Connection string"); the variable stays in the tooltip.
const CRED_WORDS = {api: 'API', sa: 'service account', url: 'connection string', cli: 'path to the CLI', oauth: 'OAuth'};
const credWords = (env, service) => {
  const skip = new Set(['db', '<name>', ...String(service || '').toLowerCase().split(/[^a-z0-9]+/), 'google', 'close', 'slack']);
  const words = env.toLowerCase().split('_').filter(w => w && !skip.has(w)).map(w => CRED_WORDS[w] || w);
  const text = words.join(' ') || env;
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const credSummary = (creds, row) => {
  const saved = row ? vaultForIntegration(row) : [];
  if (saved.length) return saved.map(c => `<span title="${esc(c.env || '')}">${esc(c.name || c.env)}</span>`).join(', ');
  const first = String((creds || [])[0] || '');
  if (/^none\b/i.test(first)) return '<span class="muted">None needed</span>';
  const envs = credEnvs(creds);
  if (envs.length) return envs.map(e => `<span title="${esc(e)}">${esc(credWords(e, row?.service))}</span>`).join(', ');
  return first ? esc(first) : '<span class="muted">—</span>';
};
function vaultForIntegration(row) {
  if (!VAULT?.credentials || !row) return [];
  const envs = new Set(credEnvs(row.credentials));
  const names = new Set([row.service, row.title, ...(row.aliases || [])].map(s => String(s || '').toLowerCase()));
  return VAULT.credentials.filter(c => {
    if (c.env && envs.has(c.env)) return true;
    const name = (c.name || '').toLowerCase(), source = (c.source || '').toLowerCase();
    if (names.has(name) || names.has(source)) return true;
    return source === row.service || source.startsWith(row.service + ':') || source.startsWith(row.service + '/');
  });
}
async function vaultFetch() {
  if (!S.me?.credential_access) return null;
  VAULT = await get('/v2/credentials');
  return VAULT;
}
function intCredPaint() {
  const dialog = $('#int-cred-dialog');
  const row = INT_ROWS.find(r => r.service === INT_CRED_SERVICE);
  if (!dialog || !row) return;
  const declared = (row.credentials || []).map(c => `<li>${esc(c)}</li>`).join('') || '<li class="muted">None declared.</li>';
  const stored = vaultForIntegration(row);
  const vaultHtml = !S.me?.credential_access ? ''
    : stored.length ? `<div class="scroll"><table class="vault-table"><thead><tr><th>Stored</th><th>Username / key</th><th>Access</th><th>Actions</th></tr></thead><tbody>${stored.map(item => `<tr>
        <td><strong>${esc(item.name)}</strong><div class="muted">${esc(item.env || item.kind.replace('_',' '))}</div>
          <div class="muted">${item.stored ? 'Stored encrypted' : 'Not connected'}${item.source ? ` · ${esc(item.source)}` : ''}</div></td>
        <td>${esc(item.username || '')}${item.preview ? `<div class="mono">${esc(item.preview)}</div>` : ''}</td>
        <td>${item.grants.length ? item.grants.map(grant => esc(vaultSubject(grant.subject))).join(', ') : '<span class="muted">Owners only</span>'}</td>
        <td><div class="row">${item.stored && item.can_reveal ? `<button class="ghost" data-vault-reveal="${esc(item.id)}" type="button">Reveal / copy</button>` : ''}
          ${VAULT.can_manage ? `<button class="ghost" data-vault-edit="${esc(item.id)}" type="button">Edit</button>` : ''}
          <button class="ghost" data-vault-share="${esc(item.id)}" type="button">${VAULT.can_manage ? 'Manage access' : 'Use with my bots'}</button></div></td></tr>`).join('')}</tbody></table></div>`
    : `<p class="empty">Nothing stored in Tico for this tool.</p>`;
  dialog.innerHTML = `<header><h2>Credentials · ${esc(row.title)}</h2>
      <button type="button" class="ghost" data-int-cred-close aria-label="Close credentials">Close</button></header>
    <div class="int-cred-body">
      <p class="muted" style="margin:0 0 8px">${esc(row.access || '')}</p>
      <ul class="int-cred-decl">${declared}</ul>
      ${vaultHtml}
      ${S.me?.credential_access && VAULT?.can_manage ? '<div class="row" style="margin-top:12px"><button class="primary" type="button" id="int-cred-add">Add credential</button></div>' : ''}
    </div>`;
  dialog.querySelector('[data-int-cred-close]').onclick = () => { INT_CRED_SERVICE = ''; dialog.close(); };
  dialog.oncancel = event => { event.preventDefault(); INT_CRED_SERVICE = ''; dialog.close(); };
  dialog.querySelector('#int-cred-add')?.addEventListener('click', () => vaultEdit());
  dialog.querySelectorAll('[data-vault-reveal]').forEach(b => b.onclick = () => vaultReveal(b.dataset.vaultReveal));
  dialog.querySelectorAll('[data-vault-edit]').forEach(b => b.onclick = () => vaultEdit(VAULT.credentials.find(c => c.id === b.dataset.vaultEdit)));
  dialog.querySelectorAll('[data-vault-share]').forEach(b => b.onclick = () => vaultShare(b.dataset.vaultShare));
}
async function intCredOpen(service) {
  const row = INT_ROWS.find(r => r.service === service);
  const dialog = $('#int-cred-dialog');
  if (!row || !dialog) return;
  INT_CRED_SERVICE = service;
  if (!dialog.open) dialog.showModal();
  dialog.innerHTML = `<header><h2>Credentials · ${esc(row.title)}</h2>
    <button type="button" class="ghost" data-int-cred-close aria-label="Close credentials">Close</button></header>
    <div class="int-cred-body"><p class="muted">Loading…</p></div>`;
  dialog.querySelector('[data-int-cred-close]').onclick = () => { INT_CRED_SERVICE = ''; dialog.close(); };
  if (S.me?.credential_access) {
    try { await vaultFetch(); }
    catch (e) { dialog.querySelector('.int-cred-body').innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  }
  if (INT_CRED_SERVICE !== service) return;
  intCredPaint();
}
async function pageIntegrations() {
  // A redraw of the Tools page already on screen keeps the connect forms while someone is typing in them.
  if (!S.route.startsWith(INTEGRATIONS + '/') && $('#int-list') && formBusy($('#main'))) return;
  const load = ++INT_LOAD;
  const service = S.route.startsWith(INTEGRATIONS + '/') ? decodeURIComponent(S.route.slice(INTEGRATIONS.length + 1)) : '';
  if (!service) {
    $('#main').innerHTML = `<div class="int-page"><div class="meeting-head"><div><h1>Tools</h1></div>
      ${S.me?.credential_access ? '<a class="ghost" href="#int-vault" data-int-vault-link>Credentials</a>' : ''}</div>
      <section class="card"><header><h2>Available tools</h2></header><input id="int-filter" class="int-search" type="search" autocomplete="off" placeholder="Filter by name, kind, credentials or summary…" aria-label="Filter tools">
      <div id="int-list"><div class="empty">Loading…</div></div></section>
      <section class="card" id="settings-granola"><header><h2>Granola</h2></header><div id="set-granola"><div class="empty">Loading…</div></div></section>
      ${S.me?.role === 'owner' ? '<section class="card" id="settings-github"><header><h2>GitHub</h2></header><div id="set-github"><div class="empty">Loading…</div></div></section>' : ''}
      ${S.me?.role === 'owner' ? '<section class="card" id="settings-meeting-importers"><header><h2>Meeting importers</h2></header><div id="set-meeting-importers"><div class="empty">Loading…</div></div></section>' : ''}
      ${S.me?.role === 'owner' ? '<section class="card" id="settings-slack"><header><h2>Slack</h2></header><div id="set-slack"><div class="empty">Loading…</div></div></section>' : ''}
      ${S.me?.bot_admin ? '<section class="card" id="settings-slack-channels"><header><h2>Slack channels</h2></header><div id="set-slack-channels"><div class="empty">Loading…</div></div></section>' : ''}
      <section class="card" id="int-vault" hidden></section>
      <dialog class="bot-editor" id="int-cred-dialog" aria-label="Tool credentials"></dialog>
      <dialog class="bot-editor" id="credential-dialog" aria-label="Credential"></dialog></div>`;
    window.mountGithubConnect?.($('#set-github'));   // ui/github-connect.js
    window.mountGranolaTool?.($('#set-granola'));   // ui/app/meetings-granola.js
    window.mountMeetingImporters?.($('#set-meeting-importers'));   // ui/meeting-importers.js
    window.mountSlackConnect?.($('#set-slack'));     // ui/slack-connect.js
    window.mountSlackChannels?.($('#set-slack-channels'));   // ui/slack-channels.js
    const draw = () => {
      const list = $('#int-list');
      if (!list) return;
      const q = ($('#int-filter')?.value || '').toLowerCase().trim();
      const shown = INT_ROWS.filter(r => !q || [r.service, r.title, r.kind, r.summary, r.access, ...(r.credentials || []), ...(r.aliases || [])].join(' ').toLowerCase().includes(q));
      list.innerHTML = shown.length ? `<div class="scroll"><table class="int-list"><thead><tr><th>Tool</th><th>Status</th><th>Bots</th><th>Credentials</th><th></th></tr></thead><tbody>${shown.map(r =>
        `<tr><td><a href="${INTEGRATIONS}/${esc(r.service)}" title="hub tool show ${esc(r.service)}">${esc(r.title)}</a>
          <div class="muted">${esc(INTEGRATION_KIND[r.kind] || r.kind)} · ${esc(r.writes)}</div></td>
          <td>${esc(({ready: 'Ready', problem: 'Needs attention', pending: 'Pending', unknown: 'Not checked', not_configured: 'Not configured', removed: 'Removed'})[r.status] || 'Not checked')}</td>
          <td>${(r.bots || []).map(bot => `<a href="#/bot/${encodeURIComponent(bot)}">${empName(bot)}</a>`).join(', ') || '—'}</td>
          <td class="int-creds">${credSummary(r.credentials, r)}</td>
          <td><button class="int-key" type="button" data-int-cred="${esc(r.service)}" title="Credentials for ${esc(r.title)}" aria-label="Credentials for ${esc(r.title)}">key_vertical</button></td></tr>`).join('')}</tbody></table></div>`
        : `<div class="empty">${INT_ROWS.length ? 'No tool matches.' : 'No tools.'}</div>`;
    };
    let rows;
    try { rows = (await get('/v2/tools')).integrations; }
    catch (e) {
      const list = $('#int-list');
      if (load === INT_LOAD && list) list.innerHTML = `<div class="empty">Could not load the tools: ${esc(e.message)}</div>`;
      return;
    }
    if (load !== INT_LOAD || !$('#int-list')) return;
    INT_ROWS = rows || [];
    // A short list reads at a glance; the filter earns its place once the team adds its own pages.
    $('#int-filter').hidden = INT_ROWS.length <= 10;
    draw();
    $('#int-filter').oninput = draw;
    $('#int-list').onclick = ev => {
      const b = ev.target.closest('[data-int-cred]');
      if (b) { ev.preventDefault(); intCredOpen(b.dataset.intCred); }
    };
    // The vault lists itself for whoever may open it (owners and credential admins add and share).
    if (S.me?.credential_access) void vaultLoad().then(() => { if (load === INT_LOAD) draw(); });   // saved credentials name themselves
    const jump = $('[data-int-vault-link]');
    if (jump) jump.onclick = ev => { ev.preventDefault(); $('#int-vault')?.scrollIntoView({behavior: 'smooth', block: 'start'}); };
    return;
  }
  $('#main').innerHTML = `<div class="int-page"><p class="muted"><a href="${INTEGRATIONS}">← Tools</a></p><div id="int-detail"><div class="empty">Loading…</div></div>
      <dialog class="bot-editor" id="int-cred-dialog" aria-label="Tool credentials"></dialog>
      <dialog class="bot-editor" id="credential-dialog" aria-label="Credential"></dialog></div>`;
  let page;
  try { page = await get('/v2/tools/' + encodeURIComponent(service)); }
  catch (e) {
    const detail = $('#int-detail');
    if (load === INT_LOAD && detail) detail.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  if (load !== INT_LOAD || !$('#int-detail')) return;
  if (page.service !== service) { location.hash = INTEGRATIONS + '/' + page.service; return; }
  // A query entry carries `sql`, or `mongo` for a MongoDB database; bots run either with `hub db`.
  const queryText = q => q.sql ? q.sql.trim() : JSON.stringify(q.mongo, null, 2);
  const queryHtml = q => `<details class="int-query" data-query="${esc(q.id)}"><summary><span class="id">${esc(q.id)}</span><strong>${esc(q.title)}</strong>${q.database ? `<span class="pill">${esc(q.database)}</span>` : ''}<span class="desc">${esc(q.description || '')}</span></summary>
      <pre>${esc(queryText(q))}</pre>
      ${q.params.length ? `<div class="params mono">${q.params.map(p => esc(p.name) + (p.required ? ' *' : '')).join(' · ')}</div>` : ''}
      <div class="row" style="gap:8px"><button class="ghost" type="button" data-copy-sql>Copy</button></div></details>`;
  const learningHtml = n => `<div class="int-learning" data-learning="${esc(n.id)}"><span class="who" title="${esc(n.created)}">${esc(n.created.slice(0, 10))} · ${esc(n.actor)}</span><span class="text">${esc(n.text)}</span>${S.me?.role === 'owner' ? '<button class="linkish danger" type="button" data-delete-learning>Delete</button>' : ''}</div>`;
  INT_ROWS = [{service: page.service, title: page.title, kind: page.kind, summary: page.summary,
               access: page.access, credentials: page.credentials, aliases: page.aliases || [],
               writes: page.writes, owner: page.owner}];
  $('#int-detail').innerHTML = `<div class="meeting-head"><div><h1>${esc(page.title)}</h1></div>
      <button class="int-key" type="button" data-int-cred="${esc(page.service)}" title="Credentials for ${esc(page.title)}" aria-label="Credentials for ${esc(page.title)}">key_vertical</button></div>
    <section class="card"><dl class="int-meta"><dt>Access</dt><dd>${esc(page.access)}</dd><dt>Credentials</dt><dd>${page.credentials.map(c => esc(c)).join('<br>')}</dd>
      <dt>Writes</dt><dd><span class="pill ${page.writes === 'never' ? 'ok' : page.writes === 'approval' ? 'waiting' : ''}">${esc(page.writes)}</span> · owner ${esc(page.owner)}${page.aliases?.length ? ' · also <span class="mono">' + page.aliases.map(esc).join(', ') + '</span>' : ''}</dd>
      <dt>Declared as</dt><dd><pre class="mono">${esc(page.declared_as.trim())}</pre></dd></dl></section>
    <section class="card"><div class="md docs-content" id="int-body">${safeMd(page.body)}</div></section>
    ${page.queries.length ? `<section class="card" id="int-queries"><header><h2>Queries</h2></header>
      <input id="int-query-filter" class="int-search" type="search" autocomplete="off" placeholder="Search title, description, tags, SQL…" aria-label="Search queries"><div id="int-query-list">${page.queries.map(queryHtml).join('')}</div></section>` : ''}
    <section class="card" id="int-learnings"><header><h2>Learnings</h2></header>
      <div class="int-learn"${page.read_only ? ' hidden' : ''}><textarea id="int-learn-text" maxlength="2000" placeholder="Something reusable you learned about ${esc(page.title)} — a gotcha, a working command, a limit."></textarea>
      <div class="row" style="gap:8px"><button class="primary" type="button" id="int-learn-add">Add a learning</button><span class="muted" style="font-size:12px">Anyone signed in may add one; a human folds them into the page over time.</span></div></div>
      <div id="int-learning-list">${page.learnings.map(learningHtml).join('') || '<div class="empty">Nothing learned yet.</div>'}</div></section>`;
  // Relative links in a page point at files in the Tico repository; a sibling page opens here.
  for (const a of document.querySelectorAll('#int-body a[href]')) {
    const href = a.getAttribute('href');
    if (/^(https?:|mailto:|#)/i.test(href)) continue;
    const sibling = href.match(/^([a-z0-9-]+)\.md$/);
    if (sibling) { a.setAttribute('href', INTEGRATIONS + '/' + sibling[1]); a.removeAttribute('target'); continue; }
    try { a.setAttribute('href', new URL(href, GH + '/blob/main/integrations/').href); } catch { a.removeAttribute('href'); }
  }
  const filter = $('#int-query-filter');
  if (filter) filter.oninput = () => {
    const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const el of document.querySelectorAll('#int-query-list .int-query')) {
      const q = page.queries.find(x => x.id === el.dataset.query);
      const text = [q.id, q.title, q.description, q.category, ...(q.tags || []), queryText(q)].join(' ').toLowerCase();
      el.hidden = !words.every(w => text.includes(w));
    }
  };
  $('#int-detail').onclick = async ev => {
    const cred = ev.target.closest('[data-int-cred]');
    if (cred) { ev.preventDefault(); intCredOpen(cred.dataset.intCred); return; }
    const copy = ev.target.closest('[data-copy-sql]');
    if (copy) {
      const q = page.queries.find(x => x.id === copy.closest('.int-query').dataset.query);
      try { await copyText(queryText(q)); toast('Copied'); } catch { toast('Could not copy', true); }
      return;
    }
    const del = ev.target.closest('[data-delete-learning]');
    if (del) {
      const row = del.closest('[data-learning]');
      if (!confirm('Delete this learning?')) return;
      try { await post(`/v2/tools/${encodeURIComponent(page.service)}/learnings/${encodeURIComponent(row.dataset.learning)}/delete`); row.remove(); toast('Deleted'); }
      catch (e) { toast(e.message, true); }
      if (!$('#int-learning-list').children.length) $('#int-learning-list').innerHTML = '<div class="empty">Nothing learned yet.</div>';
      return;
    }
    if (ev.target.closest('#int-learn-add')) {
      const box = $('#int-learn-text'), text = box.value.trim();
      if (!text) { box.focus(); return; }
      const btn = $('#int-learn-add'); btn.disabled = true;
      try {
        const n = await post(`/v2/tools/${encodeURIComponent(page.service)}/learnings`, {text});
        const list = $('#int-learning-list'); if (list.querySelector('.empty')) list.innerHTML = '';
        list.insertAdjacentHTML('afterbegin', learningHtml(n)); box.value = ''; toast('Learning added');
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; }
    }
  };
}
