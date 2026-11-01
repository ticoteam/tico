/* Docs: writing and the dialogs around it (docs/docs.md). The editor (a Markdown textarea and a preview
   toggle), the history panel with restore, the linked-doc dialog and the import dialog. Text a person or
   a bot wrote is only ever shown through safeMd (which sanitizes) or textContent, never as raw HTML. */
'use strict';
(function () {
  'use strict';
  const IMPORT_TYPES = '.md,.markdown,.txt,.html,.htm,.docx,.pdf';
  const errorCode = error => error?.body?.error?.code || '';
  const errorInfo = error => error?.body?.error || {};

  function dialog(html, {className = 'gs-form docs-dialog', label} = {}) {
    const box = document.createElement('dialog');
    box.className = 'tmodal ' + className;
    if (label) box.setAttribute('aria-label', label);
    box.innerHTML = html;
    document.body.append(box);
    box.addEventListener('close', () => box.remove());
    box.addEventListener('click', event => { if (event.target.closest('[data-close]')) box.close(); });
    box.showModal();
    return box;
  }

  // ------------------------------------------------------------------ the editor
  function editor({doc = null, folder = '', onSaved, onCancel, onReload}) {
    const isNew = !doc;
    let version = doc?.version || 0;
    const form = document.createElement('form');
    form.className = 'docs-editor';
    form.setAttribute('aria-label', isNew ? 'New doc' : 'Edit ' + doc.title);
    form.innerHTML = `<div class="docs-editor-fields">
        <label class="gs-field"><span>Title</span><input name="title" type="text" maxlength="300" required autocomplete="off" placeholder="Refund policy"></label>
        <label class="gs-field"><span>Path <em>(folder and file)</em></span><input name="path" type="text" maxlength="300" autocomplete="off" spellcheck="false" placeholder="support/refund-policy.md"></label>
      </div>
      <div class="docs-editor-bar"><div class="docs-seg" role="group" aria-label="Write or preview">
          <button type="button" data-tab="write" aria-pressed="true">Write</button><button type="button" data-tab="preview" aria-pressed="false">Preview</button></div>
        <span class="muted">Markdown</span></div>
      <textarea name="body" class="docs-textarea" spellcheck="true" aria-label="Markdown" placeholder="# Heading"></textarea>
      <div class="docs-preview md docs-content" hidden aria-label="Preview"></div>
      <div class="docs-conflict" role="alert" hidden></div>
      <p class="err" data-error role="alert" hidden></p>
      <div class="docs-editor-actions"><input name="note" type="text" maxlength="300" autocomplete="off" aria-label="What changed? (optional)" placeholder="What changed? (optional)">
        <span class="muted" data-status role="status"></span><button class="ghost" type="button" data-cancel>Cancel</button><button class="primary" type="submit" title="Save (⌘S)">Save</button></div>`;
    form.elements.title.value = doc?.title || '';
    form.elements.path.value = doc?.path || (folder ? folder + '/' : '');
    form.elements.body.value = doc?.body || '';
    const area = form.elements.body, preview = form.querySelector('.docs-preview');
    const say = text => { form.querySelector('[data-status]').textContent = text || ''; };
    const fail = text => { const line = form.querySelector('[data-error]'); line.textContent = text || ''; line.hidden = !text; };

    form.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => {
      const show = button.dataset.tab === 'preview';
      form.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      area.hidden = show; preview.hidden = !show;
      if (show) preview.innerHTML = area.value.trim() ? safeMd(area.value, {documentImages: true}) : '<p class="muted">Nothing to preview yet.</p>';
    });
    // Unsaved text: leaving by Cancel, the list or a reload asks first (ui/docs-page.js reads data-dirty).
    const start = () => [form.elements.title.value, form.elements.path.value, area.value].join('\u0000');
    const initial = start();
    const onUnload = event => { if (form.isConnected && form.dataset.dirty === '1') { event.preventDefault(); event.returnValue = ''; } };
    form.addEventListener('input', () => { form.dataset.dirty = start() === initial ? '' : '1'; });
    window.addEventListener('beforeunload', onUnload);
    const done = () => { form.dataset.dirty = ''; window.removeEventListener('beforeunload', onUnload); };
    form.querySelector('[data-cancel]').onclick = () => {
      if (form.dataset.dirty === '1' && !confirm('Discard your changes to this doc?')) return;
      done(); onCancel?.();
    };
    form.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); form.requestSubmit(); }
    });

    const conflict = info => {
      const box = form.querySelector('.docs-conflict');
      box.hidden = false; box.textContent = '';
      const who = info.updated_by_name || 'Someone';
      const text = document.createElement('p');
      text.textContent = `${who} saved version ${info.version} while you were editing. Your text is still here.`;
      const keep = Object.assign(document.createElement('button'), {type: 'button', className: 'ghost', textContent: 'Keep mine and replace theirs'});
      const drop = Object.assign(document.createElement('button'), {type: 'button', className: 'ghost', textContent: 'Discard mine and load theirs'});
      keep.onclick = () => { version = info.version; box.hidden = true; say('Save again to replace version ' + info.version + '.'); };
      drop.onclick = () => onReload?.();
      const row = document.createElement('div'); row.className = 'docs-conflict-actions'; row.append(keep, drop);
      box.append(text, row);
      box.scrollIntoView?.({block: 'nearest'});
    };

    form.onsubmit = async event => {
      event.preventDefault();
      const button = form.querySelector('[type=submit]');
      button.disabled = true; fail(''); say('Saving…'); form.querySelector('.docs-conflict').hidden = true;
      const title = form.elements.title.value.trim(), path = form.elements.path.value.trim(), note = form.elements.note.value.trim();
      try {
        let saved;
        if (isNew) saved = await post('/v2/docs', {title, body: area.value, ...(path && !path.endsWith('/') ? {path} : {}), note});
        else saved = await writeRequest('PATCH', '/v2/docs/' + encodeURIComponent(doc.id),
          {version, title, body: area.value, ...(path && path !== doc.path ? {path} : {}), note});
        say('');
        done();
        onSaved?.(saved.doc);
      } catch (error) {
        say('');
        if (errorCode(error) === 'version_conflict') conflict(errorInfo(error));
        else if (errorCode(error) === 'locked') fail('This doc was locked while you were editing. Only an owner or bot administrator can change it now.');
        else if (errorCode(error) === 'path_taken') fail('Another doc already uses that path. Choose a different one.');
        else fail(error.message || 'That did not save.');
        button.disabled = false;
      }
    };
    return form;
  }

  // ------------------------------------------------------------------ history and restore
  async function history(doc, {canRestore, onRestored}) {
    const box = dialog(`<div class="docs-history-shell"><header><h2>History</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close history">✕</button></header>
      <p class="muted docs-history-sub"></p>
      <div class="docs-history-body"><ol class="docs-history-list" aria-label="Versions"></ol>
        <section class="docs-history-view" aria-live="polite"><p class="muted">Loading…</p></section></div></div>`,
      {className: 'docs-history', label: 'History of ' + doc.title});
    box.querySelector('.docs-history-sub').textContent = doc.title + ' · ' + doc.path;
    const list = box.querySelector('.docs-history-list'), view = box.querySelector('.docs-history-view');
    let versions = [];
    const show = async number => {
      list.querySelectorAll('button').forEach(b => b.setAttribute('aria-current', String(Number(b.dataset.version) === number)));
      view.replaceChildren(Object.assign(document.createElement('p'), {className: 'muted', textContent: 'Loading…'}));
      try {
        const {version} = await get(`/v2/docs/${encodeURIComponent(doc.id)}/versions/${number}`);
        const meta = versions.find(v => v.version === number) || {};
        const head = document.createElement('div'); head.className = 'docs-history-head';
        const title = document.createElement('div');
        const strong = Object.assign(document.createElement('strong'), {textContent: `Version ${number}${meta.current ? ' (current)' : ''}`});
        const by = Object.assign(document.createElement('span'), {className: 'muted',
          textContent: ` · by ${version.actor_name || version.actor} · ${fmt(version.created_at)}${version.note ? ' · ' + version.note : ''}`});
        title.append(strong, by);
        head.append(title);
        if (!meta.current) {
          const restore = Object.assign(document.createElement('button'), {type: 'button', className: 'primary', textContent: 'Restore this version'});
          restore.disabled = !canRestore;
          restore.title = canRestore ? '' : 'This doc is locked: only an owner or bot administrator can restore it';
          restore.onclick = async () => {
            restore.disabled = true;
            try { const result = await post(`/v2/docs/${encodeURIComponent(doc.id)}/restore`, {version: number}); box.close(); onRestored?.(result.doc); }
            catch (error) { restore.disabled = false; view.querySelector('.err').textContent = error.message; view.querySelector('.err').hidden = false; }
          };
          head.append(restore);
        }
        const err = Object.assign(document.createElement('p'), {className: 'err', hidden: true});
        err.setAttribute('role', 'alert');
        const body = document.createElement('div'); body.className = 'md docs-content docs-history-text';
        body.innerHTML = version.body.trim() ? safeMd(version.body, {documentImages: true}) : '<p class="muted">This version is empty.</p>';
        view.replaceChildren(head, err, body);
      } catch (error) { view.replaceChildren(Object.assign(document.createElement('p'), {className: 'err', textContent: error.message})); }
    };
    try {
      versions = (await get(`/v2/docs/${encodeURIComponent(doc.id)}/versions`)).versions || [];
      for (const v of versions) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.dataset.version = v.version;
        const top = document.createElement('span'); top.className = 'docs-history-top';
        top.append(Object.assign(document.createElement('strong'), {textContent: 'v' + v.version}));
        if (v.current) top.append(Object.assign(document.createElement('span'), {className: 'docs-badge internal', textContent: 'Current'}));
        const who = Object.assign(document.createElement('span'), {className: 'docs-history-who',
          textContent: `${v.actor_name || v.actor} · ${ago(v.created_at)}`});
        who.title = fmt(v.created_at);
        button.append(top, who);
        if (v.note) button.append(Object.assign(document.createElement('span'), {className: 'docs-history-note', textContent: v.note}));
        button.onclick = () => show(v.version);
        li.append(button); list.append(li);
      }
      if (versions.length) await show((versions.find(v => !v.current) || versions[0]).version);
    } catch (error) { view.replaceChildren(Object.assign(document.createElement('p'), {className: 'err', textContent: error.message})); }
    return box;
  }

  // ------------------------------------------------------------------ linked docs
  function linkDialog({link = null, onDone}) {
    const box = dialog(`<form data-link-form novalidate><header><h2>${link ? 'Edit source' : 'Add source'}</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <label class="gs-field"><span>Address</span><input name="url" type="url" maxlength="2000" required autocomplete="off" inputmode="url" spellcheck="false" placeholder="https://github.com/acme/handbook"></label>
      <p class="docs-detected muted" data-detected aria-live="polite"></p>
      <label class="gs-field"><span>Title <em>(optional)</em></span><input name="title" type="text" maxlength="300" autocomplete="off" placeholder="Defaults to the address"></label>
      <label class="gs-field"><span>Description <em>(optional)</em></span><input name="description" type="text" maxlength="300" autocomplete="off" placeholder="Help centre articles customers read"></label>
      <details class="docs-source-scope"><summary>Scope</summary><label class="gs-field"><span>Reference <em>(optional)</em></span><input name="reference" maxlength="256" autocomplete="off" placeholder="Branch, tag or commit"></label></details>
      <p class="err" data-error role="alert" hidden></p>
      <div class="gs-card-actions"><button class="primary" type="submit">${link ? 'Save' : 'Add source'}</button>
        <button class="ghost" type="button" data-close>Cancel</button>
        ${link ? '<button class="ghost docs-danger" type="button" data-remove>Remove source</button>' : ''}</div></form>`, {label: link ? 'Edit source' : 'Add source'});
    const form = box.querySelector('form'), detected = box.querySelector('[data-detected]');
    form.elements.url.value = link?.url || ''; form.elements.title.value = link?.title || ''; form.elements.description.value = link?.description || '';
    form.elements.reference.value = link?.reference_override || '';
    const detect = () => {
      const scope = DocsSearch.sourceScope(form.elements.url.value);
      detected.textContent = scope ? `${scope.repo}${scope.tail ? ' · ' + scope.tail : ''}` : form.elements.url.value ? 'Link only' : '';
      form.querySelector('.docs-source-scope').hidden = !scope?.tail;
      if (link?.error_code === 'reference_needed') form.querySelector('.docs-source-scope').open = true;
    };
    form.elements.url.oninput = detect; detect(); form.elements.url.focus();
    const fail = text => { const line = form.querySelector('[data-error]'); line.textContent = text; line.hidden = !text; };
    const send = async body => {
      form.querySelector('[type=submit]').disabled = true; fail('');
      try {
        const result = link ? await writeRequest('PATCH', '/v2/doc-sources/' + encodeURIComponent(link.id), body) : await post('/v2/doc-sources', body);
        box.close(); onDone?.(result.source);
      } catch (error) {
        form.querySelector('[type=submit]').disabled = false;
        fail(errorCode(error) === 'already_linked' ? 'That address is already linked.' : error.message || 'That did not save.');
      }
    };
    form.onsubmit = event => {
      event.preventDefault();
      send({url: form.elements.url.value.trim(), title: form.elements.title.value.trim() || (link ? link.title : ''), description: form.elements.description.value.trim(), reference: form.elements.reference.value.trim()});
    };
    box.querySelector('[data-remove]')?.addEventListener('click', () => { if (confirm('Remove this source?')) send({archived: true}); });
    return box;
  }

  // ------------------------------------------------------------------ research
  // Research: the Librarian, a helper nobody chats with, researches the web and adds what it finds to one section
  // (backend/librarian.py, playbooks/research-a-section.md). `body` is the page's Markdown, for its headings.
  function researchDialog({target, body = ''}) {
    const heads = [...new Set(String(body).split('\n').map(line => /^#{1,4}\s+(.+?)\s*#*\s*$/.exec(line)?.[1]).filter(Boolean))];
    const box = dialog(`<form data-research-form novalidate><header><h2>Research</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      ${heads.length ? `<label class="gs-field"><span>Section</span><select name="section"><option value="">Whole page</option>${heads.map(h => `<option>${esc(h)}</option>`).join('')}</select></label>` : ''}
      <label class="gs-field"><span>What to find</span><textarea name="instructions" rows="5" maxlength="4000" required placeholder="Add each competitor's public pricing, with sources"></textarea></label>
      <p class="err" data-error role="alert" hidden></p>
      <div class="gs-card-actions"><button class="primary" type="submit">Start research</button>
        <button class="ghost" type="button" data-close>Cancel</button></div></form>`, {label: 'Research'});
    const form = box.querySelector('form'), line = box.querySelector('[data-error]');
    form.elements.instructions.focus();
    form.onsubmit = async event => {
      event.preventDefault();
      const instructions = form.elements.instructions.value.trim();
      if (!instructions) { line.textContent = 'Say what to find.'; line.hidden = false; return; }
      form.querySelector('[type=submit]').disabled = true; line.hidden = true;
      try {
        const sent = await post('/v2/librarian/research', {target, section: form.elements.section?.value || '', instructions});
        form.innerHTML = `<header><h2>Research</h2><button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
          <p class="docs-dialog-lead">On it. The changes show in this page's history.</p>
          <div class="gs-card-actions"><a class="primary" role="button" href="#/task/${esc(sent.task_id)}" data-close>View task</a>
            <button class="ghost" type="button" data-close>Close</button></div>`;
      } catch (error) {
        form.querySelector('[type=submit]').disabled = false;
        line.textContent = errorCode(error) === 'librarian_off' ? 'Research is off right now.' : error.message || 'That did not go through.';
        line.hidden = false;
      }
    };
    return box;
  }

  // ------------------------------------------------------------------ import
  function importDialog({onDone}) {
    const box = dialog(`<form data-import-form><header><h2>Import a file</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <p class="muted docs-dialog-lead">Word, PDF, HTML, Markdown or text, up to 20 MB.</p>
      <label class="gs-field"><span>File</span><input type="file" name="file" accept="${IMPORT_TYPES}" required></label>
      <label class="gs-field"><span>Title <em>(optional)</em></span><input name="title" type="text" maxlength="300" autocomplete="off" placeholder="Defaults to the first heading or the file name"></label>
      <label class="gs-field"><span>Path <em>(optional)</em></span><input name="path" type="text" maxlength="300" autocomplete="off" spellcheck="false" placeholder="sales/pricing.md"></label>
      <p class="err" data-error role="alert" hidden></p><p class="muted" data-status role="status"></p>
      <div class="gs-card-actions"><button class="primary" type="submit">Import</button><button class="ghost" type="button" data-close>Cancel</button></div></form>`,
      {label: 'Import a file'});
    const form = box.querySelector('form');
    form.onsubmit = async event => {
      event.preventDefault();
      const file = form.elements.file.files[0], line = form.querySelector('[data-error]');
      line.hidden = true;
      if (!file) return;
      if (file.size > 20_000_000) { line.textContent = 'That file is larger than 20 MB.'; line.hidden = false; return; }
      form.querySelector('[type=submit]').disabled = true;
      form.querySelector('[data-status]').textContent = 'Importing…';
      const body = new FormData();
      body.append('file', file);
      if (form.elements.title.value.trim()) body.append('title', form.elements.title.value.trim());
      if (form.elements.path.value.trim()) body.append('path', form.elements.path.value.trim());
      try {
        const response = await formFetch(API + '/v2/docs/import', body);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(apiError(result, response));
        box.close(); onDone?.(result.doc);
      } catch (error) {
        form.querySelector('[data-status]').textContent = '';
        line.textContent = error.message || 'That file could not be imported.'; line.hidden = false;
        form.querySelector('[type=submit]').disabled = false;
      }
    };
    return box;
  }

  window.DocsEditor = {editor, history, linkDialog, importDialog, researchDialog};
})();
