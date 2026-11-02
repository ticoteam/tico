/* Internal documents and external sources share Docs search. Ask opens the Ask panel (ui/app/ask-panel.js), which
   sees the page. */
'use strict';
let DOC_DATA = {docs: [], linked: []};
let DOC_QUERY = '';
let DOC_LOAD = 0;
let DOC_BROWSER_SCROLL = 0;
const docsHref = (id = '', extra = {}) => {
  const q = new URLSearchParams(extra);
  return '#/docs' + (id ? '/' + encodeURIComponent(id) : '') + (q.size ? '?' + q.toString() : '');
};
const docsIsAdmin = () => S.me?.role === 'owner' || !!S.me?.bot_admin;
const docsDir = path => path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
// A folder as a person reads it: "support/how-to" is "Support / How to". Only the words change; the path stays the path.
const docsFolderWords = dir => String(dir || '').split('/').filter(Boolean)
  .map(part => part.replace(/^_+/, '').replace(/[-_]+/g, ' ').replace(/^./, c => c.toUpperCase()) || part).join(' / ');
const docsLockMark = () => `<span class="docs-lock" title="Locked" aria-label="Locked">${DocsSearch.LOCK}</span>`;

async function docsLoadAll(archived = false) {
  const docs = [];
  let cursor = '';
  do {
    const page = await get('/v2/docs?limit=500' + (archived ? '&archived=true' : '') + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''));
    docs.push(...(page.docs || []));
    cursor = page.next_cursor || '';
  } while (cursor && docs.length < 5000);
  const linked = [];
  if (!archived) {
    let after = '';
    do {
      const page = await get('/v2/doc-sources?limit=100' + (after ? '&cursor=' + encodeURIComponent(after) : ''));
      linked.push(...(page.sources || [])); after = page.next_cursor || '';
    } while (after && linked.length < 500);
  }
  return {docs, linked};
}

// A menu of actions in a popover, placed under the button that opens it.
function docsMenu(menu, button) {
  menu.addEventListener('toggle', event => {
    button.setAttribute('aria-expanded', String(event.newState === 'open'));
    if (event.newState !== 'open') return;
    const rect = button.getBoundingClientRect();
    menu.style.top = `${Math.min(rect.bottom + 4, innerHeight - menu.offsetHeight - 12)}px`;
    menu.style.left = `${Math.max(12, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 12))}px`;
  });
  menu.addEventListener('click', event => { if (event.target.closest('button,a')) menu.hidePopover(); });
}
// ---------------------------------------------------------------- setting up Docs
// The first-run question (backend/librarian_setup.py): where the docs are. The links become sources at once and one
// task has the Librarian, a helper nobody chats with, read them and write the map of the docs (playbooks/docs-setup.md).
// Skip is the person's own; "Set up Docs…" in the menu brings the question back.
// /api/me names every signed-in person owner or viewer; a member is a viewer there, not human.
const docsIsPerson = () => ['owner', 'viewer'].includes(S.me?.role);
let DOCS_SETUP_SENT = null;          // what was just filed, shown until closed

async function docsSetup(slot, {asked, current, openImport, reload}) {
  const sent = DOCS_SETUP_SENT;
  if (sent) {
    slot.innerHTML = `<section class="docs-setup" role="status" aria-labelledby="docs-setup-h">
        <h2 id="docs-setup-h">Setting up your Docs</h2>
        <p class="muted">${sent.count ? `${sent.count} ${sent.count === 1 ? 'source is' : 'sources are'} in Docs now. ` : ''}Reading them and mapping your docs, so anyone can find things. This usually takes 10–15 minutes.</p>
        <div class="gs-card-actions"><a class="docs-new" role="button" href="#/task/${esc(sent.task)}">View task</a>
          <button class="ghost" type="button" data-setup-close>Close</button></div></section>`;
    slot.querySelector('[data-setup-close]').onclick = () => { DOCS_SETUP_SENT = null; slot.innerHTML = ''; };
    return;
  }
  let setup;
  try { setup = await get('/v2/librarian/setup'); } catch { return; }
  if (!current() || !document.body.contains(slot)) return;
  if (!setup.available) {
    if (asked) slot.innerHTML = '<p class="docs-setup-off muted">Docs setup is off right now.</p>';
    return;
  }
  if (!asked && !setup.docs.show) return;
  slot.innerHTML = `<section class="docs-setup" aria-labelledby="docs-setup-h"><form data-docs-setup novalidate>
      <h2 id="docs-setup-h">Where are your docs?</h2>
      <p class="muted">Each place your team keeps what it knows is added to Docs, read, and mapped so anyone can find things.</p>
      <label class="gs-field"><span>Links, one per line</span><textarea name="sources" rows="3" maxlength="20000" autocomplete="off" spellcheck="false" placeholder="https://github.com/acme/handbook&#10;https://help.acme.example"></textarea></label>
      <label class="gs-field"><span>Anything else? <em>(optional)</em></span><input name="note" type="text" maxlength="4000" autocomplete="off" placeholder="Our policies are in Google Drive"></label>
      <p class="err" data-error role="alert" hidden></p>
      <div class="gs-card-actions"><button class="primary" type="submit">Set up Docs</button>
        <button class="ghost" type="button" data-setup-import>Import files</button>
        <button class="ghost" type="button" data-setup-skip>${asked ? 'Not now' : 'Skip for now'}</button></div></form></section>`;
  const form = slot.querySelector('form'), line = form.querySelector('[data-error]');
  const fail = message => { line.textContent = message; line.hidden = !message; form.querySelector('[type=submit]').disabled = false; };
  form.querySelector('[data-setup-import]').onclick = () => openImport();
  form.querySelector('[data-setup-skip]').onclick = async () => {
    slot.innerHTML = '';
    if (asked) { location.hash = docsHref(); return; }
    try { await post('/v2/librarian/setup/docs', {skip: true}); } catch { /* it asks again next visit */ }
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const sources = [...new Set(form.elements.sources.value.split(/[\s,]+/).map(word => word.trim()).filter(Boolean))];
    const note = form.elements.note.value.trim();
    if (!sources.length && !note) return fail('Add a link to your docs, or say where they are.');
    if (sources.length > 20) return fail('Up to 20 links at a time.');
    fail(''); form.querySelector('[type=submit]').disabled = true;
    try {
      const result = await post('/v2/librarian/setup/docs', {sources, note});
      DOCS_SETUP_SENT = {task: result.task_id, count: (result.sources || []).length};
      DOC_CACHE_KEY = '';                      // the new sources are in the list at once
      if (asked) location.hash = docsHref(); else reload();
    } catch (error) {
      fail(error?.body?.error?.code === 'librarian_off' ? 'Docs setup is off right now.' : error.message || 'That did not go through.');
    }
  };
}

// The list is drawn from the last load at once, so opening a doc does not blank it; a fresh load follows.
let DOC_CACHE_KEY = '';
// An editor with unsaved text asks before a click in the list leaves it.
const docsDirty = () => $('form.docs-editor')?.dataset.dirty === '1';

window.pageCompanyDocs = async function pageCompanyDocs() {
  $('#main').classList.add('docs-layout');
  if ($('#docs-browser')) DOC_BROWSER_SCROLL = $('#docs-browser').scrollTop;
  const [pathPart, queryPart = ''] = S.route.split('?');
  const params = new URLSearchParams(queryPart);
  if (params.get('collection') === 'notes') { location.hash = '#/docs'; return; }
  const selected = pathPart.startsWith(DOCS + '/') ? decodeURIComponent(pathPart.slice(DOCS.length + 1)) : '';
  const creating = selected === 'new', editing = !creating && params.get('edit') === '1';
  const load = ++DOC_LOAD;
  const admin = docsIsAdmin(), archived = params.get('archived') === '1';
  const mineId = S.me?.id ? 'human:' + S.me.id : '';
  const current = () => load === DOC_LOAD && S.route.startsWith(DOCS);
  $('#main').classList.toggle('docs-reading', !!selected);

  $('#main').innerHTML = `<div class="docs-heading"><h1>Docs</h1>
    <div class="docs-search-field"><span class="nav-icon" aria-hidden="true">search</span><input id="docs-search" class="docs-search" type="search" aria-label="Search docs" placeholder="Search docs" value="${esc(DOC_QUERY)}" autocomplete="off"><kbd class="docs-kbd" aria-hidden="true">/</kbd></div>
    <button class="docs-ask" id="docs-ask" type="button" data-ask-open aria-label="Ask" title="Ask">${ldotHTML({state: 'still', size: 16})}<span class="docs-ask-word">Ask</span></button>
    <div class="docs-actions"><button class="docs-new" id="docs-add-source" type="button">Add source</button><a class="docs-new" id="docs-new" href="${docsHref('new')}" role="button">New doc</a>
      <button class="docs-settings" id="docs-more" type="button" popovertarget="docs-menu" aria-label="More" aria-expanded="false" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button></div></div>
    <div id="docs-menu" class="docs-pop" popover aria-label="Docs actions"><button id="docs-import" type="button">Import a file…</button><button id="docs-link" type="button">Add source…</button>
      ${docsIsPerson() ? `<a id="docs-setup-open" href="${docsHref('', {setup: 1})}">Set up Docs…</a>` : ''}
      <a id="docs-archived" href="${docsHref('', archived ? {} : {archived: 1})}">${archived ? 'Active' : 'Archived'}</a></div>
    <p class="docs-feedback" id="docs-feedback" role="status" hidden></p>
    <div id="docs-setup"></div>
    <div class="docs-workspace${selected ? ' has-selection' : ''}"><aside class="docs-browser" aria-label="Docs" id="docs-browser"><p class="docs-loading">Loading…</p></aside>
    <article class="docs-reader" id="docs-reader">${selected ? `<a class="docs-back" href="${docsHref('', archived ? {archived: 1} : {})}">‹ Docs</a>` : ''}</article></div>`;
  const feedback = $('#docs-feedback');
  const say = message => { feedback.textContent = message || ''; feedback.hidden = !message; };
  const reload = () => { if (S.route.startsWith(DOCS)) pageCompanyDocs(); };
  docsMenu($('#docs-menu'), $('#docs-more'));
  const openImport = () => DocsEditor.importDialog({onDone: doc => { location.hash = docsHref(doc.id); }});
  const openLink = link => DocsEditor.linkDialog({link, onDone: source => {
    const target = docsHref(source ? 'source:' + source.id : 'sources');
    if (location.hash === target) reload(); else location.hash = target;
  }});
  $('#docs-add-source').onclick = () => openLink(null);
  $('#docs-import').onclick = () => openImport();
  $('#docs-link').onclick = () => openLink(null);
  // #docs-ask opens the Ask panel ([data-ask-open], ui/app/ask-panel.js); what it sends carries this page.

  const cacheKey = archived ? 'archived' : 'active';
  const fresh = docsLoadAll(archived);
  if (DOC_CACHE_KEY !== cacheKey) {
    try { DOC_DATA = await fresh; DOC_CACHE_KEY = cacheKey; }
    catch (error) { if (current()) { say('Could not load docs: ' + error.message); $('#docs-browser').innerHTML = ''; } return; }
    if (!current()) return;
  }
  let {docs, linked} = DOC_DATA;
  const browser = $('#docs-browser');
  if (params.get('import') === '1') { history.replaceState(null, '', location.pathname + location.search + docsHref()); openImport(); }
  // Docs asks where the docs are on a first visit, while Docs holds nothing a person wrote or linked.
  const ownDocs = docs.filter(d => !d.path.startsWith('_librarian/') && d.path !== 'FAQ.md');
  if (docsIsPerson() && !archived && !selected && (DOCS_SETUP_SENT || params.get('setup') === '1' || (!linked.length && !ownDocs.length)))
    void docsSetup($('#docs-setup'), {asked: params.get('setup') === '1', current, openImport, reload});

  // ---------------------------------------------------------------- the list
  const canEditLink = link => admin || (!!mineId && link.added_by === mineId);
  const docItem = d => `<a class="docs-item${d.id === selected ? ' selected' : ''}" ${d.id === selected ? 'aria-current="page"' : ''} href="${docsHref(d.id, archived ? {archived: 1} : {})}" title="${esc(d.path)}"><span>${esc(d.title)}</span>${d.locked ? docsLockMark() : ''}</a>`;
  const sourceStatus = source => {
    if (['queued', 'preparing'].includes(source.status)) return 'Preparing…';
    if (source.status === 'updating') return 'Updating…';
    if (source.status === 'failed') return "Couldn't update · Retry";
    if (source.status === 'access_needed') return 'GitHub access needed';
    if (source.status === 'empty') return `No docs found${source.skipped ? ' · ' + source.skipped + ' skipped' : ''}`;
    if (source.status !== 'ready') return 'Link only';
    return `${source.doc_count} ${source.doc_count === 1 ? 'doc' : 'docs'}${source.skipped ? ' · ' + source.skipped + ' skipped' : ''}${source.last_checked_at ? ' · ' + ago(source.last_checked_at) : ''}`;
  };
  const linkRow = link => {
    const kind = DocsSearch.kind(link.provider || link.kind);
    return `<div class="docs-link"><a class="docs-link-main" href="${docsHref('source:' + link.id)}" title="${esc(link.repo || DocsSearch.hostOf(link.url))}">
        <span class="docs-kind" data-kind="${esc(link.kind)}">${kind.icon}</span>
        <span class="docs-link-text"><strong>${esc(link.title)}</strong><small>${esc(link.repo || DocsSearch.hostOf(link.url))} · ${esc(sourceStatus(link))}</small>${link.description ? `<span>${esc(link.description)}</span>` : ''}</span></a>
      ${canEditLink(link) ? `<button class="docs-link-edit" type="button" data-link-edit="${esc(link.id)}" aria-label="Edit ${esc(link.title)}">Edit</button>` : ''}</div>`;
  };
  const renderList = () => {
    const folders = new Map();
    for (const d of docs) { const dir = docsDir(d.path); if (!folders.has(dir)) folders.set(dir, []); folders.get(dir).push(d); }
    const dirs = [...folders.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
    const internal = docs.length
      ? [...(folders.get('') || []).map(docItem),
         ...dirs.map(dir => `<details open data-docs-folder="${esc(dir)}"><summary title="${esc(dir)}">${esc(docsFolderWords(dir))} <span class="muted">${folders.get(dir).length}</span></summary>${folders.get(dir).map(docItem).join('')}</details>`)].join('')
      : `<div class="docs-empty-note"><p>No docs yet.</p>
          <div class="docs-empty-actions"><button class="docs-new" type="button" data-docs-import>Import a file</button></div></div>`;
    const links = linked.length ? linked.map(linkRow).join('')
      : '<div class="docs-empty-note"><p>No sources yet.</p></div>';
    browser.innerHTML = `<details class="docs-section docs-internal-section" data-docs-group="internal" open><summary class="docs-section-head"><h2 id="docs-h-internal">${archived ? 'Archived docs' : 'Internal docs'} <span class="muted">${docs.length}</span></h2></summary><div class="docs-group-scroll">${internal}</div></details>
      ${archived ? '' : `<details class="docs-section docs-external-section" data-docs-group="external" open><summary class="docs-section-head"><h2 id="docs-h-linked">External sources <span class="muted">${linked.length}</span></h2></summary><div class="docs-group-scroll"><a class="docs-source-all" href="${docsHref('sources')}">All sources</a>${links}</div></details>`}`;
    browser.querySelectorAll('[data-docs-group]').forEach(el => {
      const key = 'docs.group.' + (S.me?.id || '') + '.' + el.dataset.docsGroup;
      try { el.open = localStorage.getItem(key) !== '1'; } catch { /* open by default */ }
      el.ontoggle = () => { try { localStorage.setItem(key, el.open ? '0' : '1'); } catch { /* not remembered */ } };
    });
    browser.querySelectorAll('details[data-docs-folder]').forEach(el => {
      const key = 'docs.collapse.' + el.dataset.docsFolder;
      try { el.open = localStorage.getItem(key) !== '1' || !!el.querySelector('.selected'); } catch { /* the folder stays open */ }
      el.ontoggle = () => { try { localStorage.setItem(key, el.open ? '0' : '1'); } catch { /* not remembered */ } };
    });
  };
  const renderResults = ({results}) => {
    browser.innerHTML = `<div class="docs-results" aria-label="Search results">${results.map(r => r.type === 'internal'
      ? `<a class="docs-item docs-result${r.id === selected ? ' selected' : ''}" href="${docsHref(r.id)}"><span class="docs-badge internal">Internal</span><strong>${esc(r.title)}</strong><small>${esc(r.path)}</small>${r.excerpt ? `<span class="docs-excerpt">${esc(r.excerpt)}</span>` : ''}</a>`
      : r.type === 'external'
      ? `<a class="docs-item docs-result" href="${docsHref(r.id, {revision: r.commit})}"><span class="docs-badge linked">External · GitHub</span><strong>${esc(r.title)}</strong><small>${esc(r.source_title)} · ${esc(r.path)}</small>${r.excerpt ? `<span class="docs-excerpt">${esc(r.excerpt)}</span>` : ''}</a>`
      : `<a class="docs-item docs-result" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer"><span class="docs-badge linked">Linked · ${esc(DocsSearch.kind(r.kind).label)}</span><strong>${esc(r.title)} <span aria-hidden="true">↗</span></strong><small>${esc(DocsSearch.hostOf(r.url))}</small>${r.description ? `<span class="docs-excerpt">${esc(r.description)}</span>` : ''}</a>`).join('')
      || '<p class="empty">No docs match. Try fewer words, or ask AI.</p>'}</div>`;
  };
  const showBrowser = () => {
    $('.docs-workspace').classList.toggle('searching', !!selected && !!DOC_QUERY.trim());
    if (!DocsSearch.terms(DOC_QUERY).length) { renderList(); browser.scrollTop = DOC_BROWSER_SCROLL; return; }
    if (archived) {
      const terms = DocsSearch.terms(DOC_QUERY);
      browser.innerHTML = docs.filter(d => terms.every(word => (d.title + ' ' + d.path).toLowerCase().includes(word))).map(docItem).join('') || '<p class="empty">No archived docs match.</p>';
      return;
    }
    DocsSearch.run(get, DOC_QUERY).then(found => { if (!found.stale && current()) renderResults(found); })
      .catch(error => { if (current()) say('Search failed: ' + error.message); });
  };
  browser.onclick = event => {
    const item = event.target.closest('a.docs-item, a.docs-link-main, a.docs-source-all');
    if (item && docsDirty() && !confirm('Discard your changes to this doc?')) { event.preventDefault(); return; }
    if (event.target.closest('[data-docs-import]')) openImport();
    const add = event.target.closest('[data-docs-link]'); if (add) openLink(null);
    const edit = event.target.closest('[data-link-edit]');
    if (edit) openLink(linked.find(l => l.id === edit.dataset.linkEdit));
    if (item) DOC_BROWSER_SCROLL = browser.scrollTop;
    // A phone shows the list or the doc, not both: a result opened there leaves the search behind.
    if (item?.classList.contains('docs-result') && !item.target && matchMedia('(max-width: 800px)').matches) DOC_QUERY = '';
  };
  browser.onscroll = () => { if (!DOC_QUERY.trim()) DOC_BROWSER_SCROLL = browser.scrollTop; };
  let timer = 0;
  const search = $('#docs-search');
  search.oninput = () => { DOC_QUERY = search.value; clearTimeout(timer); timer = setTimeout(showBrowser, 180); };
  search.onkeydown = event => {
    if (event.key === 'Enter') { clearTimeout(timer); const first = browser.querySelector('.docs-result'); if (first) first.click(); else showBrowser(); }
    if (event.key === 'Escape') { DOC_QUERY = ''; search.value = ''; showBrowser(); search.blur(); }
    if (event.key === 'ArrowDown') { const first = browser.querySelector('.docs-result, .docs-item'); if (first) { event.preventDefault(); first.focus(); } }
  };
  showBrowser();
  // Swap in the fresh load when it differs from what was drawn.
  if (fresh) fresh.then(data => {
    DOC_CACHE_KEY = cacheKey;
    const changed = JSON.stringify(data) !== JSON.stringify(DOC_DATA);
    DOC_DATA = data;
    if (!changed || !current()) return;
    ({docs, linked} = data);
    if (!DocsSearch.terms(DOC_QUERY).length) { const top = browser.scrollTop; renderList(); browser.scrollTop = top; }
    if (!selected) drawLanding();
    if (selected === 'sources') drawSources();
  }, () => { /* the cached list stays */ });

  // ---------------------------------------------------------------- the reader
  const reader = $('#docs-reader');
  reader.addEventListener('click', event => {
    if (event.target.closest('.docs-back') && docsDirty() && !confirm('Discard your changes to this doc?')) event.preventDefault();
  });
  const back = `<a class="docs-back" href="${docsHref('', archived ? {archived: 1} : {})}">‹ Docs</a>`;
  // No doc open: the docs changed most recently, so the page opens on something to read.
  function drawLanding() {
    if (!docs.length && !linked.length) { reader.innerHTML = ''; return; }   // the list says so, and the header has New doc
    const recent = [...docs].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))).slice(0, 12);
    reader.innerHTML = `<div class="docs-welcome docs-recent"><h2 class="docs-recent-head">${archived ? 'Archived' : 'Recent'}</h2>${recent.map(d =>
      `<a class="docs-recent-row" href="${docsHref(d.id, archived ? {archived: 1} : {})}"><span class="docs-recent-title">${esc(d.title)}</span>
        <span class="docs-recent-folder">${esc(docsFolderWords(docsDir(d.path)))}</span>
        <span class="docs-recent-when" title="${esc(fmt(d.updated_at))}">${esc(ago(d.updated_at))}${d.updated_by_name ? ' · ' + esc(d.updated_by_name) : ''}</span></a>`).join('')
      || '<p class="muted">Nothing here.</p>'}</div>`;
  }
  const pollSources = () => {
    if (linked.some(source => ['queued', 'preparing', 'updating'].includes(source.status)))
      setTimeout(() => { if (current() && !docsDirty() && !DOC_QUERY && !document.querySelector('dialog[open]')) reload(); }, 5000);
  };
  const drawSources = () => {
    reader.innerHTML = back + `<header class="docs-reader-head"><h2>External sources</h2></header><div class="docs-source-list">${linked.map(linkRow).join('') || '<p class="muted">No sources yet.</p>'}</div>`;
    reader.querySelectorAll('[data-link-edit]').forEach(button => { button.onclick = () => openLink(linked.find(source => source.id === button.dataset.linkEdit)); });
    pollSources();
  };
  if (!selected) { drawLanding(); return; }
  if (selected === 'sources') { drawSources(); return; }
  if (selected.startsWith('source:')) {
    const sourceId = selected.slice(7);
    let source = linked.find(row => row.id === sourceId);
    try {
      let after = '', documents = [];
      do {
        const page = await get('/v2/doc-sources/' + encodeURIComponent(sourceId) + '/documents?limit=100' + (after ? '&cursor=' + encodeURIComponent(after) : ''));
        source = page.source; documents.push(...page.documents); after = page.next_cursor || '';
      } while (after && documents.length < 500);
      if (!current()) return;
      reader.innerHTML = back + `<header class="docs-reader-head"><div class="docs-reader-title"><h2>${esc(source.title)}</h2><p class="docs-meta">${esc(source.repo || DocsSearch.hostOf(source.url))}${source.reference ? ' · ' + esc(source.reference) : ''}${source.path ? ' · ' + esc(source.path) : ''}</p><p class="docs-meta">${esc(sourceStatus(source))}</p></div><div class="docs-tools"><a class="docs-tool" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">Open original</a>${source.repo ? '<button class="docs-tool" id="source-refresh">' + (source.status === 'failed' ? 'Retry' : 'Refresh now') + '</button>' : ''}${canEditLink(source) ? '<button class="docs-tool" id="source-edit">Edit</button>' : ''}</div></header>
        <div class="docs-source-files">${documents.map(doc => `<a class="docs-recent-row" href="${docsHref(doc.id, {revision: doc.commit})}"><span class="docs-recent-title">${esc(doc.title)}</span><span class="docs-recent-folder">${esc(doc.path)}</span></a>`).join('')}</div>`;
      $('#source-edit')?.addEventListener('click', () => openLink(source));
      $('#source-refresh')?.addEventListener('click', async event => {
        event.target.disabled = true;
        try { await post('/v2/doc-sources/' + encodeURIComponent(sourceId) + '/refresh', {}); reload(); }
        catch (error) { say(error.message); event.target.disabled = false; }
      });
      if (['queued', 'preparing', 'updating'].includes(source.status))
        setTimeout(() => { if (current() && !document.querySelector('dialog[open]')) reload(); }, 5000);
    } catch (error) { if (current()) reader.innerHTML = back + `<p class="err">${esc(error.message)}</p>`; }
    return;
  }
  if (selected.startsWith('external:')) {
    try {
      const {doc} = await get('/v2/docs/external/' + encodeURIComponent(selected.slice(9)) + (params.get('revision') ? '?revision=' + encodeURIComponent(params.get('revision')) : ''));
      if (!current()) return;
      const markdown = /\.(md|markdown)$/i.test(doc.path);
      reader.innerHTML = back + `<header class="docs-reader-head"><div class="docs-reader-title"><p class="docs-crumb muted">${esc(doc.source_title)} · ${esc(doc.path)}</p><h2>${esc(doc.title)}</h2><p class="docs-meta">${esc(doc.commit.slice(0, 7))}${doc.last_checked_at ? ' · ' + esc(ago(doc.last_checked_at)) : ''}</p></div><a class="docs-tool" href="${esc(doc.citation_url)}" target="_blank" rel="noopener noreferrer">Open original</a></header><div class="md docs-content">${markdown ? safeMd(doc.content) : '<pre>' + esc(doc.content) + '</pre>'}</div>`;
      reader.querySelectorAll('.docs-content a[href]').forEach(a => {
        const raw = a.getAttribute('href');
        if (raw && !/^[a-z][a-z0-9+.-]*:|^#|^\/\//i.test(raw)) {
          a.href = new URL(raw, doc.citation_url.split('?')[0].split('#')[0]).href;
          a.target = '_blank'; a.rel = 'noopener noreferrer';
        }
      });
    } catch (error) { if (current()) reader.innerHTML = back + `<p class="err">${esc(error.message)}</p>`; }
    return;
  }
  if (creating) {
    const folder = params.get('folder') || '';
    reader.innerHTML = back + '<header class="docs-reader-head docs-edit-head"><div class="docs-reader-title"><h2>New doc</h2></div></header>';
    reader.append(DocsEditor.editor({folder, onSaved: doc => { location.hash = docsHref(doc.id); }, onCancel: () => { location.hash = docsHref(); }}));
    reader.querySelector('input[name=title]').focus();
    return;
  }
  reader.insertAdjacentHTML('beforeend', '<p class="docs-loading">Loading…</p>');
  let doc;
  try { doc = (await get('/v2/docs/' + encodeURIComponent(selected))).doc; }
  catch (error) {
    if (!current()) return;
    reader.innerHTML = back + `<div class="empty">${error.status === 404 ? 'That doc does not exist, or it was archived.' : esc(error.message)}</div>`;
    return;
  }
  if (!current()) return;
  const canChange = admin || !doc.locked;
  const crumb = `<p class="muted docs-crumb" title="${esc(doc.path)}">${esc(docsFolderWords(docsDir(doc.path)))}</p>`;
  const meta = `Version ${doc.version} · Updated ${esc(ago(doc.updated_at))} by ${esc(doc.updated_by_name || doc.updated_by)}`;
  if (editing && canChange) {
    reader.innerHTML = back + `<header class="docs-reader-head docs-edit-head"><div class="docs-reader-title">${crumb}<h2>Edit</h2><p class="docs-meta">${meta}</p></div></header>`;
    reader.append(DocsEditor.editor({doc, onSaved: saved => { location.hash = docsHref(saved.id); }, onCancel: () => { location.hash = docsHref(doc.id); },
      onReload: () => { location.hash = docsHref(doc.id); }}));
    reader.querySelector('textarea').focus({preventScroll: true});
    return;
  }
  reader.innerHTML = `${back}<div class="docs-article"><div class="docs-main"><header class="docs-reader-head"><div class="docs-reader-title">${crumb}
      <h2>${esc(doc.title)}${doc.locked ? ` <span class="docs-lock-badge">${DocsSearch.LOCK}Locked</span>` : ''}</h2>
      <p class="docs-meta" title="${esc(fmt(doc.updated_at))}">${meta}</p></div>
      <div class="docs-tools"><a class="docs-tool" id="doc-edit" role="button" href="${docsHref(doc.id, {edit: 1})}"${canChange ? ' title="Edit (e)"' : ' aria-disabled="true" tabindex="-1" title="Locked: only an owner or bot administrator can change this doc"'}>Edit</a>
        <button class="docs-tool docs-tool-icon" id="doc-more" type="button" popovertarget="doc-menu" aria-label="More" aria-expanded="false" title="More"><span class="nav-icon" aria-hidden="true">more_horiz</span></button></div></header>
    <div id="doc-menu" class="docs-pop" popover aria-label="Doc actions">${canChange ? '<button id="doc-research" type="button">Research…</button>' : ''}<button id="doc-history" type="button">History</button>
      ${admin ? `<button id="doc-lock" type="button" aria-pressed="${doc.locked}">${doc.locked ? 'Unlock' : 'Lock'}</button>` : ''}
      ${canChange ? `<button class="docs-danger" id="doc-archive" type="button">${doc.archived ? 'Restore' : 'Archive'}</button>` : ''}</div>
    ${doc.locked ? `<p class="hint docs-locked-note">${canChange ? 'Locked: only owners and bot administrators can change it.' : 'Locked. Only an owner or bot administrator can change it.'}</p>` : ''}
    <div class="md docs-content">${doc.body.trim() ? safeMd(doc.body, {documentImages: true}) : '<p class="muted">This doc is empty. Choose Edit to write it.</p>'}</div></div></div>`;
  reader.scrollTop = 0;
  docsMenu($('#doc-menu'), $('#doc-more'));
  const editLink = $('#doc-edit');
  if (!canChange) editLink.onclick = event => event.preventDefault();
  $('#doc-research')?.addEventListener('click', () => DocsEditor.researchDialog({target: doc.id, body: doc.body}));
  $('#doc-history').onclick = () => DocsEditor.history(doc, {canRestore: canChange, onRestored: saved => { location.hash === docsHref(saved.id) ? reload() : location.hash = docsHref(saved.id); }});
  $('#doc-lock')?.addEventListener('click', async event => {
    event.target.disabled = true;
    try { await writeRequest('PATCH', '/v2/docs/' + encodeURIComponent(doc.id), {version: doc.version, locked: !doc.locked}); reload(); }
    catch (error) { say(error.message); event.target.disabled = false; }
  });
  $('#doc-archive')?.addEventListener('click', async event => {
    event.target.disabled = true;
    try {
      if (doc.archived) {
        const saved = await post(`/v2/docs/${encodeURIComponent(doc.id)}/restore`, {version: doc.version});
        location.hash = docsHref(saved.doc.id); toast('Doc restored');
      } else {
        const saved = await writeRequest('PATCH', '/v2/docs/' + encodeURIComponent(doc.id), {version: doc.version, archived: true, note: 'Archived'});
        location.hash = docsHref(); docsArchiveUndo(saved.doc);
      }
    }
    catch (error) { say(error.message); event.target.disabled = false; }
  });

  // On this page: headings get anchors, and a contents list when there is enough to navigate.
  const content = reader.querySelector('.docs-content');
  const lead = content.firstElementChild;                        // the title already heads the page
  if (lead?.tagName === 'H1' && lead.textContent.trim().toLowerCase() === doc.title.trim().toLowerCase()) lead.remove();
  const headings = [...content.querySelectorAll('h1,h2,h3')], used = new Set();
  headings.forEach(h => {
    const base = h.id || h.textContent.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section';
    let id = base, n = 2; while (used.has(id)) id = base + '-' + n++;
    used.add(id); h.id = id;
  });
  const outline = headings.filter(h => h.tagName !== 'H1');
  if (outline.length > 2) {
    // Beside the text when the reader is wide enough (styles/hub-v2.css); folded above it otherwise.
    const toc = document.createElement('details'); toc.className = 'docs-toc';
    toc.innerHTML = '<summary>On this page</summary><nav aria-label="On this page">' + outline.map(h => `<a href="${docsHref(doc.id, {section: h.id})}" data-section="${esc(h.id)}" class="toc-${h.tagName.toLowerCase()}">${esc(h.textContent)}</a>`).join('') + '</nav>';
    reader.querySelector('.docs-article').append(toc);
    const side = () => getComputedStyle(toc).position === 'sticky';
    toc.open = side();
    // The rail opening or closing changes the room: the contents move beside the text or fold above it.
    let wasSide = side();
    const watch = new ResizeObserver(() => { if (!toc.isConnected) { watch.disconnect(); return; } const now = side(); if (now !== wasSide) { wasSide = now; toc.open = now; } });
    watch.observe(reader);
    toc.querySelector('summary').onclick = event => { if (side()) event.preventDefault(); };
    // In place: the hash stays the doc's, and the heading scrolls into view.
    toc.querySelector('nav').onclick = event => {
      const a = event.target.closest('a[data-section]'); if (!a) return;
      event.preventDefault();
      headings.find(h => h.id === a.dataset.section)?.scrollIntoView({block: 'start', behavior: 'smooth'});
      history.replaceState(null, '', location.pathname + location.search + a.getAttribute('href'));
    };
    // The section being read is marked in the list.
    const links = new Map([...toc.querySelectorAll('a[data-section]')].map(a => [a.dataset.section, a]));
    const mark = () => {
      const top = reader.getBoundingClientRect().top + 80;
      let at = outline[0];
      for (const h of outline) { if (h.getBoundingClientRect().top <= top) at = h; else break; }
      links.forEach((a, id) => a.classList.toggle('current', id === at?.id));
    };
    reader.addEventListener('scroll', mark, {passive: true});
    mark();
  }
  const section = params.get('section');
  if (section) requestAnimationFrame(() => headings.find(h => h.id === section)?.scrollIntoView({block: 'start'}));
  // A relative link to another doc's .md file stays in the page.
  content.querySelectorAll('a[href]').forEach(a => {
    const raw = a.getAttribute('href') || '';
    if (/^[a-z][a-z0-9+.-]*:|^[#/]/i.test(raw) || !/\.(md|markdown)(#.*)?$/i.test(raw)) return;
    const [file, fragment = ''] = raw.split('#');
    const parts = [...docsDir(doc.path).split('/').filter(Boolean)];
    for (const part of file.split('/')) { if (part === '..') parts.pop(); else if (part && part !== '.') parts.push(part); }
    const target = docs.find(d => d.path.toLowerCase() === parts.join('/').toLowerCase());
    if (target) { a.setAttribute('href', docsHref(target.id, fragment ? {section: decodeURIComponent(fragment)} : {})); a.removeAttribute('target'); }
  });
};
// "/" searches the docs, and "e" edits the open doc, when focus is not in a field.
document.addEventListener('keydown', event => {
  if (typeof S === 'undefined' || !S.route?.startsWith(DOCS) || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
  const el = event.target;
  if (el?.closest?.('input,textarea,select,[contenteditable=""],[contenteditable=true],dialog,#ask-panel')) return;
  if (event.key === '/') { const field = $('#docs-search'); if (field) { event.preventDefault(); field.focus(); field.select(); } }
  if (event.key === 'e') { const edit = $('#doc-edit'); if (edit && edit.getAttribute('aria-disabled') !== 'true') { event.preventDefault(); location.hash = edit.getAttribute('href'); } }
});
window.addEventListener('tico-docs-changed', () => { if (typeof S !== 'undefined' && S.route?.startsWith(DOCS)) window.pageCompanyDocs(); });

function docsArchiveUndo(doc) {
  const notice = document.createElement('div'); notice.className = 'toast docs-archive-undo'; notice.setAttribute('role', 'status'); notice.append('Doc archived. ');
  const button = document.createElement('button'); button.type = 'button'; button.className = 'ghost'; button.textContent = 'Undo';
  button.onclick = async () => {
    button.disabled = true;
    try {
      const saved = await post(`/v2/docs/${encodeURIComponent(doc.id)}/restore`, {version: doc.version});
      notice.remove(); location.hash = docsHref(saved.doc.id); toast('Doc restored');
    } catch (error) { toast(error.message, true); button.disabled = false; }
  };
  notice.append(button); document.body.append(notice); setTimeout(() => notice.remove(), 15000);
}
