/* ui/app/integrations.js — One tool's page (Library › Tools draws it beside its list) and the credential helpers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- integrations
// One page per outside system (integrations/*.md, served by GET /api/v2/tools): what it
// is, how a bot uses it, the rules, the query list, and the learnings bots and humans add.
// Everyone signed in reads; anyone adds a learning; the owner deletes one.
const INTEGRATION_KIND = {api: 'API', sql: 'SQL', browser: 'Browser', mail: 'Email', cli: 'CLI', mcp: 'MCP'};
let INT_LOAD = 0;
const credEnvs = texts => {
  const out = [];
  for (const t of texts || []) {
    for (const m of String(t).match(/\b[A-Z](?:[A-Z0-9_]|<[A-Z]+>){2,}/g) || []) if (!out.includes(m)) out.push(m);
  }
  return out;
};
function vaultForIntegration(row) {
  if (!VAULT?.credentials || !row) return [];
  const envs = new Set([...credEnvs(row.credentials), ...(row.connections || []).map(c => c.env).filter(Boolean)]);
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
// The old Tools page (#/integrations, #/integrations/<service>) is Library › Tools now (ui/app/redesign-library.js).
function pageIntegrations() {
  rdToolsOpen(RD_TOOLS + S.route.slice(INTEGRATIONS.length));
}
// A write level in words: what a bot may change there (backend/integrations.py `write_level`).
const TOOL_WRITES = {never: ['Read only', 'ok'], approval: ['Approval', 'waiting'], allowed: ['Freely', '']};
const toolWritePill = level => { const [word, tone] = TOOL_WRITES[level] || [level || '', '']; return `<span class="pill ${tone}">${esc(word)}</span>`; };
// One tool's page drawn into #int-detail, under Library › Tools' address (`base`): its heading (with `head`, the
// phone's back link, before the title), then `facts(page)` (who has it, its credentials, its issues: the slot
// #tool-facts, which the page redraws alone when they change), then what the page says, its queries and learnings.
// `after(page)` runs once it is drawn. Anyone signed in adds a learning; the owner deletes one.
async function integrationDetail(service, load, {base = RD_TOOLS, head = '', facts = null, after = null} = {}) {
  let page;
  try { page = await get('/v2/tools/' + encodeURIComponent(service)); }
  catch (e) {
    const detail = $('#int-detail');
    if (load === INT_LOAD && detail) detail.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  if (load !== INT_LOAD || !$('#int-detail')) return;
  if (page.service !== service) { location.replace(base + '/' + page.service + (location.hash.includes('?') ? '?' + location.hash.split('?')[1] : '')); return; }
  // A query entry carries `sql`, or `mongo` for a MongoDB database; bots run either with `hub db`.
  const queryText = q => q.sql ? q.sql.trim() : JSON.stringify(q.mongo, null, 2);
  const queryHtml = q => `<details class="int-query" data-query="${esc(q.id)}"><summary><span class="id">${esc(q.id)}</span><strong>${esc(q.title)}</strong>${q.database ? `<span class="pill">${esc(q.database)}</span>` : ''}<span class="desc">${esc(q.description || '')}</span></summary>
      <pre>${esc(queryText(q))}</pre>
      ${q.params.length ? `<div class="params mono">${q.params.map(p => esc(p.name) + (p.required ? ' *' : '')).join(' · ')}</div>` : ''}
      <div class="row" style="gap:8px"><button class="ghost" type="button" data-copy-sql>Copy</button></div></details>`;
  const learningHtml = n => `<div class="int-learning" data-learning="${esc(n.id)}"><span class="who" title="${esc(n.created_at)}">${esc(n.created_at.slice(0, 10))} · ${esc(n.actor)}</span><span class="text">${esc(n.text)}</span>${S.me?.role === 'owner' ? '<button class="linkish danger" type="button" data-delete-learning>Delete</button>' : ''}</div>`;
  // A fact with nothing to say is left out.
  const metaRow = (dt, dd) => dd ? `<dt>${dt}</dt><dd>${dd}</dd>` : '';
  $('#int-detail').innerHTML = `<div class="meeting-head">${head}<div><h1>${esc(page.title)}</h1></div></div>
    <div id="tool-facts">${facts ? facts(page) : ''}</div>
    <section class="card"><dl class="int-meta">${metaRow('Via', esc(page.access))}
      <dt>Writes</dt><dd>${toolWritePill(page.writes)} · owner ${esc(page.owner)}${page.aliases?.length ? ' · also <span class="mono">' + page.aliases.map(esc).join(', ') + '</span>' : ''}</dd>
      ${metaRow('Declared as', page.declared_as.trim() && `<pre class="mono">${esc(page.declared_as.trim())}</pre>`)}</dl></section>
    ${page.body?.trim() ? `<section class="card"><div class="md docs-content" id="int-body">${safeMd(page.body)}</div></section>` : ''}
    ${page.queries.length ? `<section class="card" id="int-queries"><header><h2>Queries</h2></header>
      <input id="int-query-filter" class="int-search" type="search" autocomplete="off" placeholder="Search queries" aria-label="Search queries"><div id="int-query-list">${page.queries.map(queryHtml).join('')}</div></section>` : ''}
    <section class="card" id="int-learnings"><header><h2>Learnings <span class="muted" data-learning-count>${page.learnings.length}</span></h2></header>
      ${page.read_only ? '' : `<div class="int-learn"><textarea id="int-learn-text" maxlength="2000" aria-label="A learning" placeholder="A gotcha, a working command, a limit"></textarea>
      <div class="row" style="gap:8px"><button class="primary" type="button" id="int-learn-add">Add a learning</button></div></div>`}
      <div id="int-learning-list">${page.learnings.map(learningHtml).join('') || '<div class="empty">Nothing learned yet.</div>'}</div></section>`;
  // Relative links in a page point at files in the Tico repository; a sibling page opens here.
  for (const a of document.querySelectorAll('#int-body a[href]')) {
    const href = a.getAttribute('href');
    if (/^(https?:|mailto:|#)/i.test(href)) continue;
    const sibling = href.match(/^([a-z0-9-]+)\.md$/);
    if (sibling) { a.setAttribute('href', base + '/' + sibling[1]); a.removeAttribute('target'); continue; }
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
  const recount = () => { const n = $('[data-learning-count]'); if (n) n.textContent = document.querySelectorAll('#int-learning-list .int-learning').length; };
  $('#int-detail').onclick = async ev => {
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
      recount();
      return;
    }
    if (ev.target.closest('#int-learn-add')) {
      const box = $('#int-learn-text'), text = box.value.trim();
      if (!text) { box.focus(); return; }
      const btn = $('#int-learn-add'); btn.disabled = true;
      try {
        const n = await post(`/v2/tools/${encodeURIComponent(page.service)}/learnings`, {text});
        const list = $('#int-learning-list'); if (list.querySelector('.empty')) list.innerHTML = '';
        list.insertAdjacentHTML('afterbegin', learningHtml(n)); box.value = ''; recount(); toast('Learning added');
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; }
    }
  };
  after?.(page);
}
