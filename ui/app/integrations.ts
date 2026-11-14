/* ui/app/integrations.ts — One declared tool's page (Tools › Keys draws it beside its list, #/tools/<service> on its
   own; ui/app/redesign-library.ts) and the credential helpers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- integrations
// One page per outside system (integrations/*.md, served by GET /api/v2/tools): what it
// is, how a bot uses it, the rules, the query list, and the learnings bots and humans add.
// Everyone signed in reads; anyone adds a learning; the owner deletes one.
const INTEGRATION_KIND: Record<string, string> = {api: 'API', sql: 'SQL', browser: 'Browser', mail: 'Email', cli: 'CLI', mcp: 'MCP'};
let INT_LOAD = 0;
/** One query a tool's page lists: SQL, or a MongoDB query (GET /v2/tools/<service>). */
interface IntegrationQuery { id: string; title: string; description?: string | null; database?: string | null; category?: string; tags?: string[]; sql?: string | null; mongo?: unknown; params: { name: string; required?: boolean }[]; }
/** A learning a person or a bot added to a tool. */
interface IntegrationLearning { id: string; created_at: string; actor: string; text: string; }
/** One tool's page as GET /v2/tools/<service> answers it. */
interface IntegrationPage { service: string; title: string; access: string; writes: string; owner: string; aliases?: string[]; declared_as: string; body?: string | null; read_only?: boolean; queries: IntegrationQuery[]; learnings: IntegrationLearning[]; }
/** The part of a tool's row the vault match reads: its service, title, aliases, credential variables and connections. */
interface VaultToolRow { service: string; title: string; aliases?: string[]; credentials?: string[]; connections?: { env?: string | null }[]; }
const credEnvs = (texts: string[] | null | undefined): string[] => {
  const out: string[] = [];
  for (const t of texts || []) {
    for (const m of String(t).match(/\b[A-Z](?:[A-Z0-9_]|<[A-Z]+>){2,}/g) || []) if (!out.includes(m)) out.push(m);
  }
  return out;
};
function vaultForIntegration(row: VaultToolRow | null | undefined): VaultCredential[] {
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
// A write level in words: what a bot may change there (backend/integrations.py `write_level`).
const TOOL_WRITES: Record<string, [string, string]> = {never: ['Read only', 'ok'], approval: ['Approval', 'waiting'], allowed: ['Freely', '']};
const toolWritePill = (level: string) => { const [word, tone] = TOOL_WRITES[level] || [level || '', '']; return `<span class="pill ${tone}">${esc(word)}</span>`; };
// One tool's page drawn into #int-detail, under a Tools address (`base`): its heading (with `head`, the
// phone's back link, before the title), then `facts(page)` (who has it, its credentials, its issues: the slot
// #tool-facts, which the page redraws alone when they change), then what the page says, its queries and learnings.
// `after(page)` runs once it is drawn. Anyone signed in adds a learning; the owner deletes one.
async function integrationDetail(service: string, load: number, {base = TOOLS_ROUTE, head = '', facts = null, after = null}: {base?: string; head?: string; facts?: ((page: IntegrationPage) => string) | null; after?: ((page: IntegrationPage) => void) | null} = {}) {
  let page: IntegrationPage;
  try { page = await get<IntegrationPage>('/v2/tools/' + encodeURIComponent(service)); }
  catch (e) {
    const detail = $('#int-detail');
    if (load === INT_LOAD && detail) detail.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`;
    return;
  }
  if (load !== INT_LOAD || !$('#int-detail')) return;
  // Another name for the same tool goes to its own; a page with no service (an older hub) stays put.
  if (page.service && page.service !== service) { location.replace(base + '/' + page.service + (location.hash.includes('?') ? '?' + location.hash.split('?')[1] : '')); return; }
  // A query entry carries `sql`, or `mongo` for a MongoDB database; bots run either with `tico db`.
  const queryText = (q: IntegrationQuery): string => q.sql ? q.sql.trim() : JSON.stringify(q.mongo, null, 2);
  const queryHtml = (q: IntegrationQuery) => `<details class="int-query" data-query="${esc(q.id)}"><summary><span class="id">${esc(q.id)}</span><strong>${esc(q.title)}</strong>${q.database ? `<span class="pill">${esc(q.database)}</span>` : ''}<span class="desc">${esc(q.description || '')}</span></summary>
      <pre>${esc(queryText(q))}</pre>
      ${q.params.length ? `<div class="params mono">${q.params.map(p => esc(p.name) + (p.required ? ' *' : '')).join(' · ')}</div>` : ''}
      <div class="row" style="gap:8px"><button class="ghost" type="button" data-copy-sql>Copy</button></div></details>`;
  const learningHtml = (n: IntegrationLearning) => `<div class="int-learning" data-learning="${esc(n.id)}"><span class="who" title="${esc(n.created_at)}">${esc(n.created_at.slice(0, 10))} · ${esc(n.actor)}</span><span class="text">${esc(n.text)}</span>${S.me?.role === 'owner' ? '<button class="linkish danger" type="button" data-delete-learning>Delete</button>' : ''}</div>`;
  // A fact with nothing to say is left out.
  const metaRow = (dt: string, dd: string) => dd ? `<dt>${dt}</dt><dd>${dd}</dd>` : '';
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
    if (href === null) continue;
    if (/^(https?:|mailto:|#)/i.test(href)) continue;
    const sibling = href.match(/^([a-z0-9-]+)\.md$/);
    if (sibling) { a.setAttribute('href', base + '/' + sibling[1]); a.removeAttribute('target'); continue; }
    try { a.setAttribute('href', new URL(href, GH + '/blob/main/integrations/').href); } catch { a.removeAttribute('href'); }
  }
  const filter = $('#int-query-filter');
  if (filter) filter.oninput = () => {
    const words: string[] = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const el of document.querySelectorAll<HTMLElement>('#int-query-list .int-query')) {
      const q = page.queries.find(x => x.id === el.dataset.query);
      if (!q) continue;
      const text = [q.id, q.title, q.description, q.category, ...(q.tags || []), queryText(q)].join(' ').toLowerCase();
      el.hidden = !words.every(w => text.includes(w));
    }
  };
  const recount = () => { const n = $('[data-learning-count]'); if (n) n.textContent = document.querySelectorAll('#int-learning-list .int-learning').length; };
  $('#int-detail').onclick = async (ev: MouseEvent) => {
    const target = eventElement(ev);
    const copy = target?.closest('[data-copy-sql]');
    if (copy) {
      const card = copy.closest<HTMLElement>('.int-query');
      if (!card) return;
      const q = page.queries.find(x => x.id === card.dataset.query);
      try { if (!q) throw new Error('No query for this card'); await copyText(queryText(q)); toast('Copied'); } catch { toast('Could not copy', true); }
      return;
    }
    const del = target?.closest('[data-delete-learning]');
    if (del) {
      const row = del.closest<HTMLElement>('[data-learning]');
      if (!(await askConfirm('Delete this learning?', del))) return;
      try {
        if (!row) throw new Error('No learning row for this delete');
        await post(`/v2/tools/${encodeURIComponent(page.service)}/learnings/${encodeURIComponent(String(row.dataset.learning))}/delete`); row.remove(); toast('Deleted');
      }
      catch (e) { toast(errorMessage(e, ''), true); }
      if (!$('#int-learning-list').children.length) $('#int-learning-list').innerHTML = '<div class="empty">Nothing learned yet.</div>';
      recount();
      return;
    }
    if (target?.closest('#int-learn-add')) {
      const box = $('#int-learn-text'), text = box.value.trim();
      if (!text) { box.focus(); return; }
      const btn = $('#int-learn-add'); btn.disabled = true;
      try {
        const n = await post(`/v2/tools/${encodeURIComponent(page.service)}/learnings`, {text});
        const list = $('#int-learning-list'); if (list.querySelector('.empty')) list.innerHTML = '';
        list.insertAdjacentHTML('afterbegin', learningHtml(n)); box.value = ''; recount(); toast('Learning added');
      } catch (e) { toast(errorMessage(e, ''), true); }
      finally { btn.disabled = false; }
    }
  };
  after?.(page);
}
