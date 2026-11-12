/* Docs: writing and the dialogs around it (docs/docs.md). The editor (a Markdown textarea and a preview
   toggle), the history panel with restore, the linked-doc dialog and the import dialog. Text a person or
   a bot wrote is only ever shown through safeMd (which sanitizes) or textContent, never as raw HTML. */
'use strict';

// The shapes this file reads: the generated types in ui/types/api.d.ts, and the editor's own below.
type DocsDoc = import('./types/api').InternalDoc;
type DocsVersionRow = import('./types/api').InternalDocVersion;
type DocsVersionResult = import('./types/api').InternalDocVersionResult;
type DocsVersionsResult = import('./types/api').InternalDocVersions;
type DocsResult = import('./types/api').InternalDocResult;
type DocsSource = import('./types/api').DocSource;
type DocsResearchFiled = import('./types/api').LibrarianResearchFiled;

/** A version_conflict answer's details: who saved which version (the error body of the 409). A field the server did not send is undefined. */
interface DocsConflict {
  version: number | undefined;
  updated_by_name: string | undefined;
}

/** The editor's form. The template in editor() draws each named control, so every field is there. */
type DocsEditorForm = HTMLFormElement & {
  readonly elements: HTMLFormControlsCollection & {
    title: HTMLInputElement;
    path: HTMLInputElement;
    body: HTMLTextAreaElement;
    note: HTMLInputElement;
  };
};

/** The linked-doc dialog's form, drawn by linkDialog()'s template. */
type DocsLinkForm = HTMLFormElement & {
  readonly elements: HTMLFormControlsCollection & {
    url: HTMLInputElement;
    title: HTMLInputElement;
    description: HTMLInputElement;
    reference: HTMLInputElement;
  };
};

/** The research dialog's form, drawn by researchDialog()'s template (the section list only when the page has headings). */
type DocsResearchForm = HTMLFormElement & {
  readonly elements: HTMLFormControlsCollection & {
    instructions: HTMLTextAreaElement;
    section?: HTMLSelectElement;
  };
};

/** The import dialog's form, drawn by importDialog()'s template. */
type DocsImportForm = HTMLFormElement & {
  readonly elements: HTMLFormControlsCollection & {
    file: HTMLInputElement;
    title: HTMLInputElement;
    path: HTMLInputElement;
  };
};

interface DocsEditorOptions {
  doc?: DocsDoc | null | undefined;
  folder?: string | undefined;
  onSaved?: ((doc: DocsDoc) => void) | undefined;
  onCancel?: (() => void) | undefined;
  onReload?: (() => void) | undefined;
}

/** The API that docs-page.ts and the market pages call (window.DocsEditor). */
interface DocsEditorApi {
  editor(options: DocsEditorOptions): DocsEditorForm;
  history(doc: DocsDoc, options: {canRestore: boolean; onRestored?: ((doc: DocsDoc) => void) | undefined}): Promise<HTMLDialogElement>;
  linkDialog(options: {link?: DocsSource | null | undefined; onDone?: ((source: DocsSource) => void) | undefined}): HTMLDialogElement;
  importDialog(options: {onDone?: ((doc: DocsDoc) => void) | undefined}): HTMLDialogElement;
  researchDialog(options: {target?: string | undefined; body?: string | undefined}): HTMLDialogElement;
}
declare var DocsEditor: DocsEditorApi;

(function () {
  'use strict';
  const IMPORT_TYPES = '.md,.markdown,.txt,.html,.htm,.docx,.pdf';
  // The conflict details of a version_conflict answer (errorBody in ui/app/errors.ts gives {} when there are none). Each field
  // is read with a typeof check, so a field the server did not send is undefined.
  const docsConflictOf = (error: unknown): DocsConflict => {
    const detail = errorBody(error);
    return {
      version: typeof detail.version === 'number' ? detail.version : undefined,
      updated_by_name: typeof detail.updated_by_name === 'string' ? detail.updated_by_name : undefined,
    };
  };

  function dialog(html: string, {className = 'gs-form docs-dialog', label}: {className?: string; label?: string} = {}): HTMLDialogElement {
    const box = document.createElement('dialog');
    box.className = 'tmodal ' + className;
    if (label) box.setAttribute('aria-label', label);
    box.innerHTML = html;
    document.body.append(box);
    box.addEventListener('close', () => box.remove());
    // A click always lands on an element, so `closest` is there; the check only narrows the type.
    box.addEventListener('click', event => { if (event.target instanceof Element && event.target.closest('[data-close]')) box.close(); });
    box.showModal();
    return box;
  }

  // A lookup into markup a template just drew. A missing element throws where the old `!` assertion would have thrown.
  function drawn<T>(element: T | null, what: string): T {
    if (element === null) throw new Error(`The docs dialog did not draw ${what}`);
    return element;
  }

  // ------------------------------------------------------------------ the editor
  function editor({doc = null, folder = '', onSaved, onCancel, onReload}: DocsEditorOptions): DocsEditorForm {
    const isNew = !doc;
    let version = doc?.version || 0;
    // `as`: createElement returns a plain form, and the template just below draws every named control on it.
    const form = document.createElement('form') as DocsEditorForm;
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
    const area = form.elements.body, preview = form.querySelector<HTMLElement>('.docs-preview')!; // `!`: drawn by the template above
    const say = (text: string) => { drawn(form.querySelector<HTMLElement>('[data-status]'), 'the save status line').textContent = text || ''; };
    const fail = (text: string) => { const line = drawn(form.querySelector<HTMLElement>('[data-error]'), 'the error line'); line.textContent = text || ''; line.hidden = !text; };

    form.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(button => {
      button.onclick = () => {
        const show = button.dataset.tab === 'preview';
        form.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(b => { b.setAttribute('aria-pressed', String(b === button)); });
        area.hidden = show; preview.hidden = !show;
        if (show) preview.innerHTML = area.value.trim() ? safeMd(area.value, {documentImages: true}) : '<p class="muted">Nothing to preview yet.</p>';
      };
    });
    // Unsaved text: leaving by Cancel, the list or a reload asks first (ui/docs-page.ts reads data-dirty).
    const start = () => [form.elements.title.value, form.elements.path.value, area.value].join('\u0000');
    const initial = start();
    const onUnload = (event: BeforeUnloadEvent) => { if (form.isConnected && form.dataset.dirty === '1') { event.preventDefault(); event.returnValue = ''; } };
    form.addEventListener('input', () => { form.dataset.dirty = start() === initial ? '' : '1'; });
    window.addEventListener('beforeunload', onUnload);
    const done = () => { form.dataset.dirty = ''; window.removeEventListener('beforeunload', onUnload); };
    form.querySelector<HTMLButtonElement>('[data-cancel]')!.onclick = async () => { // `!`: drawn by the template above
      if (form.dataset.dirty === '1' && !(await askConfirm('Discard your changes to this doc?'))) return;
      done(); onCancel?.();
    };
    form.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); form.requestSubmit(); }
    });

    const conflict = (info: DocsConflict) => {
      const box = drawn(form.querySelector<HTMLElement>('.docs-conflict'), 'the conflict box');
      box.hidden = false; box.textContent = '';
      const who = info.updated_by_name || 'Someone';
      const text = document.createElement('p');
      text.textContent = `${who} saved version ${info.version} while you were editing. Your text is still here.`;
      const keep = Object.assign(document.createElement('button'), {type: 'button', className: 'ghost', textContent: 'Keep mine and replace theirs'});
      const drop = Object.assign(document.createElement('button'), {type: 'button', className: 'ghost', textContent: 'Discard mine and load theirs'});
      keep.onclick = () => { if (info.version !== undefined) version = info.version; box.hidden = true; say('Save again to replace version ' + info.version + '.'); };
      drop.onclick = () => onReload?.();
      const row = document.createElement('div'); row.className = 'docs-conflict-actions'; row.append(keep, drop);
      box.append(text, row);
      box.scrollIntoView?.({block: 'nearest'});
    };

    form.onsubmit = async event => {
      event.preventDefault();
      const button = drawn(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the save button');
      button.disabled = true; fail(''); say('Saving…');
      drawn(form.querySelector<HTMLElement>('.docs-conflict'), 'the conflict box').hidden = true;
      const title = form.elements.title.value.trim(), path = form.elements.path.value.trim(), note = form.elements.note.value.trim();
      try {
        let saved: DocsResult;
        if (isNew) saved = await post('/v2/docs', {title, body: area.value, ...(path && !path.endsWith('/') ? {path} : {}), note});
        else saved = await writeRequest('PATCH', '/v2/docs/' + encodeURIComponent(doc.id),
          {version, title, body: area.value, ...(path && path !== doc.path ? {path} : {}), note});
        say('');
        done();
        onSaved?.(saved.doc);
      } catch (error) {
        say('');
        if (errorCode(error) === 'version_conflict') conflict(docsConflictOf(error));
        else if (errorCode(error) === 'locked') fail('This doc was locked while you were editing. Only an owner or bot administrator can change it now.');
        else if (errorCode(error) === 'path_taken') fail('Another doc already uses that path. Choose a different one.');
        else fail(errorMessage(error, 'That did not save.'));
        button.disabled = false;
      }
    };
    return form;
  }

  // ------------------------------------------------------------------ history and restore
  async function history(doc: DocsDoc, {canRestore, onRestored}: {canRestore: boolean; onRestored?: ((doc: DocsDoc) => void) | undefined}): Promise<HTMLDialogElement> {
    const box = dialog(`<div class="docs-history-shell"><header><h2>History</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close history">✕</button></header>
      <p class="muted docs-history-sub"></p>
      <div class="docs-history-body"><ol class="docs-history-list" aria-label="Versions"></ol>
        <section class="docs-history-view" aria-live="polite"><p class="muted">Loading…</p></section></div></div>`,
      {className: 'docs-history', label: 'History of ' + doc.title});
    box.querySelector<HTMLElement>('.docs-history-sub')!.textContent = doc.title + ' · ' + doc.path; // `!`: drawn by the template above
    const list = box.querySelector<HTMLElement>('.docs-history-list')!, view = box.querySelector<HTMLElement>('.docs-history-view')!; // `!`: drawn above
    let versions: DocsVersionRow[] = [];
    const show = async (number: number) => {
      list.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.setAttribute('aria-current', String(Number(b.dataset.version) === number)); });
      view.replaceChildren(Object.assign(document.createElement('p'), {className: 'muted', textContent: 'Loading…'}));
      try {
        const {version} = await get<DocsVersionResult>(`/v2/docs/${encodeURIComponent(doc.id)}/versions/${number}`);
        const meta: Partial<DocsVersionRow> = versions.find(v => v.version === number) || {};
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
            try { const result: DocsResult = await post(`/v2/docs/${encodeURIComponent(doc.id)}/restore`, {version: number}); box.close(); onRestored?.(result.doc); }
            catch (error) {
              restore.disabled = false;
              const failure = drawn(view.querySelector<HTMLElement>('.err'), 'the version error line');
              failure.textContent = errorMessage(error, ''); failure.hidden = false;
            }
          };
          head.append(restore);
        }
        const err = Object.assign(document.createElement('p'), {className: 'err', hidden: true});
        err.setAttribute('role', 'alert');
        const body = document.createElement('div'); body.className = 'md docs-content docs-history-text';
        body.innerHTML = version.body.trim() ? safeMd(version.body, {documentImages: true}) : '<p class="muted">This version is empty.</p>';
        view.replaceChildren(head, err, body);
      } catch (error) { view.replaceChildren(Object.assign(document.createElement('p'), {className: 'err', textContent: errorMessage(error, '')})); }
    };
    try {
      versions = (await get<DocsVersionsResult>(`/v2/docs/${encodeURIComponent(doc.id)}/versions`)).versions || [];
      for (const v of versions) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.dataset.version = String(v.version);
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
      // `!`: versions has at least one row here (the length check), and TypeScript cannot follow that into the index.
      if (versions.length) await show((versions.find(v => !v.current) || versions[0]!).version);
    } catch (error) { view.replaceChildren(Object.assign(document.createElement('p'), {className: 'err', textContent: errorMessage(error, '')})); }
    return box;
  }

  // ------------------------------------------------------------------ linked docs
  function linkDialog({link = null, onDone}: {link?: DocsSource | null | undefined; onDone?: ((source: DocsSource) => void) | undefined}): HTMLDialogElement {
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
    const form = drawn(box.querySelector<DocsLinkForm>('form'), 'the source form'), detected = box.querySelector<HTMLElement>('[data-detected]')!; // `!`: drawn by the template above
    form.elements.url.value = link?.url || ''; form.elements.title.value = link?.title || ''; form.elements.description.value = link?.description || '';
    form.elements.reference.value = link?.reference_override || '';
    const detect = () => {
      const scope = DocsSearch.sourceScope(form.elements.url.value);
      detected.textContent = scope ? `${scope.repo}${scope.tail ? ' · ' + scope.tail : ''}` : form.elements.url.value ? 'Link only' : '';
      const scopeFold = drawn(form.querySelector<HTMLDetailsElement>('.docs-source-scope'), 'the source scope');
      scopeFold.hidden = !scope?.tail;
      if (link?.error_code === 'reference_needed') scopeFold.open = true;
    };
    form.elements.url.oninput = detect; detect(); form.elements.url.focus();
    const fail = (text: string) => { const line = drawn(form.querySelector<HTMLElement>('[data-error]'), 'the error line'); line.textContent = text; line.hidden = !text; };
    const send = async (body: {url?: string; title?: string; description?: string; reference?: string; archived?: boolean}) => {
      const submit = drawn(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button');
      submit.disabled = true; fail('');
      try {
        const result = link ? await writeRequest('PATCH', '/v2/doc-sources/' + encodeURIComponent(link.id), body) : await post('/v2/doc-sources', body);
        box.close(); onDone?.(result.source);
      } catch (error) {
        submit.disabled = false;
        fail(errorCode(error) === 'already_linked' ? 'That address is already linked.' : errorMessage(error, 'That did not save.'));
      }
    };
    form.onsubmit = event => {
      event.preventDefault();
      send({url: form.elements.url.value.trim(), title: form.elements.title.value.trim() || (link ? link.title : ''), description: form.elements.description.value.trim(), reference: form.elements.reference.value.trim()});
    };
    box.querySelector<HTMLButtonElement>('[data-remove]')?.addEventListener('click', async event => { if (await askConfirm('Remove this source?', eventElement(event))) send({archived: true}); });
    return box;
  }

  // ------------------------------------------------------------------ research
  // Research: the Librarian, a helper nobody chats with, researches the web and adds what it finds to one section
  // (backend/librarian.py, playbooks/research-a-section.md). `body` is the page's Markdown, for its headings.
  function researchDialog({target, body = ''}: {target?: string | undefined; body?: string | undefined}): HTMLDialogElement {
    const heads = [...new Set(String(body).split('\n').map(line => /^#{1,4}\s+(.+?)\s*#*\s*$/.exec(line)?.[1]).filter(Boolean))];
    const box = dialog(`<form data-research-form novalidate><header><h2>Research</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      ${heads.length ? `<label class="gs-field"><span>Section</span><select name="section"><option value="">Whole page</option>${heads.map(h => `<option>${esc(h)}</option>`).join('')}</select></label>` : ''}
      <label class="gs-field"><span>What to find</span><textarea name="instructions" rows="5" maxlength="4000" required placeholder="Add each competitor's public pricing, with sources"></textarea></label>
      <p class="err" data-error role="alert" hidden></p>
      <div class="gs-card-actions"><button class="primary" type="submit">Start research</button>
        <button class="ghost" type="button" data-close>Cancel</button></div></form>`, {label: 'Research'});
    const form = drawn(box.querySelector<DocsResearchForm>('form'), 'the research form'), line = box.querySelector<HTMLElement>('[data-error]')!; // `!`: drawn above
    form.elements.instructions.focus();
    form.onsubmit = async event => {
      event.preventDefault();
      const instructions = form.elements.instructions.value.trim();
      if (!instructions) { line.textContent = 'Say what to find.'; line.hidden = false; return; }
      const submit = drawn(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button');
      submit.disabled = true; line.hidden = true;
      try {
        const sent: DocsResearchFiled = await post('/v2/librarian/research', {target, section: form.elements.section?.value || '', instructions});
        form.innerHTML = `<header><h2>Research</h2><button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
          <p class="docs-dialog-lead">On it. The changes show in this page's history.</p>
          <div class="gs-card-actions"><a class="primary" role="button" href="#/task/${esc(sent.task_id)}" data-close>View task</a>
            <button class="ghost" type="button" data-close>Close</button></div>`;
      } catch (error) {
        submit.disabled = false;
        line.textContent = errorCode(error) === 'librarian_off' ? 'Research is off right now.' : errorMessage(error, 'That did not go through.');
        line.hidden = false;
      }
    };
    return box;
  }

  // ------------------------------------------------------------------ import
  function importDialog({onDone}: {onDone?: ((doc: DocsDoc) => void) | undefined}): HTMLDialogElement {
    const box = dialog(`<form data-import-form><header><h2>Import a file</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <p class="muted docs-dialog-lead">Word, PDF, HTML, Markdown or text, up to 20 MB.</p>
      <label class="gs-field"><span>File</span><input type="file" name="file" accept="${IMPORT_TYPES}" required></label>
      <label class="gs-field"><span>Title <em>(optional)</em></span><input name="title" type="text" maxlength="300" autocomplete="off" placeholder="Defaults to the first heading or the file name"></label>
      <label class="gs-field"><span>Path <em>(optional)</em></span><input name="path" type="text" maxlength="300" autocomplete="off" spellcheck="false" placeholder="sales/pricing.md"></label>
      <p class="err" data-error role="alert" hidden></p><p class="muted" data-status role="status"></p>
      <div class="gs-card-actions"><button class="primary" type="submit">Import</button><button class="ghost" type="button" data-close>Cancel</button></div></form>`,
      {label: 'Import a file'});
    const form = drawn(box.querySelector<DocsImportForm>('form'), 'the import form');
    form.onsubmit = async event => {
      event.preventDefault();
      // `files!`: a file input always has a FileList (the DOM types it nullable for other input kinds).
      const file = form.elements.file.files![0], line = drawn(form.querySelector<HTMLElement>('[data-error]'), 'the error line');
      line.hidden = true;
      if (!file) return;
      if (file.size > 20_000_000) { line.textContent = 'That file is larger than 20 MB.'; line.hidden = false; return; }
      const submit = drawn(form.querySelector<HTMLButtonElement>('[type=submit]'), 'the submit button');
      const status = drawn(form.querySelector<HTMLElement>('[data-status]'), 'the import status line');
      submit.disabled = true; status.textContent = 'Importing…';
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
        status.textContent = '';
        line.textContent = errorMessage(error, 'That file could not be imported.'); line.hidden = false;
        submit.disabled = false;
      }
    };
    return box;
  }

  window.DocsEditor = {editor, history, linkDialog, importDialog, researchDialog};
})();
