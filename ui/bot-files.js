/* The Files section of a bot's right rail (docs/files.md): what the bot created, revised or delivered,
   newest activity first, as a plain list of names. Three rows; a small "+N" opens the rest inline, a
   page at a time, and "Less" folds them back. No files, no section. A stored file opens in Tico's own viewer (deps.openFile), a linked document at its
   provider in a new tab (the provider decides who may open it). Adding a link, promoting and removing
   are the API and the bot's own tools (PATCH /api/v2/files/{id}), not buttons here.
   ui/app/bot-page.js calls window.botFiles.mount(host, deps) once per bot page; deps keeps this file free
   of the page's globals (get, esc, openFile). */
'use strict';
(function () {
  const SHORT = 3, PAGE = 20;

  const css = `
.bot-files .bf-row{display:block;padding:3px 0;min-height:24px;font-size:13px;line-height:18px}
.bot-files .bf-name{min-width:0;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
.bot-files a.bf-name{color:var(--accent-quiet)}
.bot-files a.bf-name:hover{color:var(--accent);text-decoration:underline}
.bot-files .bf-foot{display:flex;gap:10px;align-items:center;padding-top:2px}
.bot-files .bf-err{color:var(--fail);font-size:12.5px}`;

  const httpsOnly = url => { try { return new URL(url).protocol === 'https:' ? url : ''; } catch { return ''; } };

  function mount(host, deps) {
    if (!host) return;
    if (!document.getElementById('bot-files-style')) {
      const style = document.createElement('style');
      style.id = 'bot-files-style';
      style.textContent = css;
      document.head.append(style);
    }
    const {slug, get, esc, openFile} = deps;
    const state = {rows: [], total: 0, cursor: null, expanded: false, error: ''};
    const base = `/v2/bots/${encodeURIComponent(slug)}/files`;

    async function load(more) {
      try {
        const limit = state.expanded ? PAGE : SHORT;
        const page = await get(`${base}?limit=${limit}` + (more && state.cursor ? `&cursor=${encodeURIComponent(state.cursor)}` : ''));
        if (!host.isConnected) return;
        const rows = page.files || [];
        state.rows = more ? state.rows.concat(rows) : rows;
        state.total = page.total || 0; state.cursor = page.next_cursor; state.error = '';
      } catch (e) { state.error = e.message || 'Could not load the files.'; }
      render();
    }

    function row(f) {
      const link = f.locator === 'remote_link';
      const url = link ? httpsOnly(f.open?.url) : f.open?.url;
      const name = esc(f.title);
      // A file that never synced has nothing to open: its name is plain text.
      const label = !url ? `<span class="bf-name" title="${esc(f.title)}">${name}</span>` : link
        ? `<a class="bf-name" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-bf-open="external" title="${esc(f.title)}">${name}</a>`
        : `<a class="bf-name" href="${esc(url)}" data-bf-open="tico" data-bf-id="${esc(f.id)}" data-bf-file="${esc(f.name || f.title)}" title="${esc(f.title)}">${name}</a>`;
      return `<div class="bf-row" data-file="${esc(f.id)}">${label}</div>`;
    }

    function render() {
      const shown = state.expanded ? state.rows : state.rows.slice(0, SHORT);
      const hasMore = state.expanded && state.cursor;
      const left = Math.max(0, state.total - shown.length);
      // Nothing to list is no section at all; an error still says so.
      host.hidden = !shown.length && !state.error;
      host.innerHTML = `<h2 class="rail-h">Files</h2>
        ${state.error ? `<div class="bf-err" role="alert">${esc(state.error)}</div>` : ''}
        ${shown.map(row).join('')}
        ${state.total > SHORT || hasMore ? `<div class="bf-foot">${state.expanded
          ? `${hasMore ? `<button class="rail-more" type="button" data-bf-more aria-label="Show ${left} more">+${left}</button>` : ''}<button class="rail-more" type="button" data-bf-less aria-label="Show fewer">Less</button>`
          : `<button class="rail-more" type="button" data-bf-all aria-label="Show all ${state.total}">+${left}</button>`}</div>` : ''}`;
    }

    host.onclick = async event => {
      const hit = selector => event.target.closest(selector);
      const stored = hit('[data-bf-open=tico]');
      if (stored) { event.preventDefault(); openFile(stored.dataset.bfId, stored.dataset.bfFile); return; }
      try {
        if (hit('[data-bf-all]')) { state.expanded = true; await load(); }
        else if (hit('[data-bf-less]')) { state.expanded = false; state.rows = state.rows.slice(0, SHORT); await load(); }
        else if (hit('[data-bf-more]')) { await load(true); }
      } catch (e) { state.error = e.message || 'Could not load the files.'; render(); }
    };
    load();
    // Background tabs used to keep hitting the Files list every minute; that endpoint is
    // statement-heavy (#1659) and multiplies across open bot pages. Resume when the tab is shown again.
    const onVis = () => {
      if (!host.isConnected) { document.removeEventListener('visibilitychange', onVis); return; }
      if (!document.hidden) load();
    };
    const timer = setInterval(() => {
      if (!host.isConnected) {
        clearInterval(timer); document.removeEventListener('visibilitychange', onVis); return;
      }
      if (!document.hidden) load();
    }, 60000);
    document.addEventListener('visibilitychange', onVis);
  }

  window.botFiles = {mount};
})();
