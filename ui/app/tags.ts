/* Tags: display chips, checklist pages and templates in Settings. */
'use strict';

// The fields a tag summary reads: a tag as the server sends it, or the stand-in a missing tag gets (its key and label).
interface TagSummaryInput {
  key?: string;
  label?: string | null;
  metadata?: Record<string, unknown> | null;
}
function tagSummary(tag: TagSummaryInput | null | undefined) {
  const values = Object.values(tag?.metadata || {}).slice(0, 2).map(value => {
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const date = new Date(value + 'T12:00:00Z');
      if (!Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value) return date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'});
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  });
  return [tag?.label || tag?.key || '', ...values].join(' · ');
}
function tagChips(keys: string[] | null | undefined, tags: import('../types/api').Tag[] = [], removable = false, interactive = true) {
  return (keys || []).map(key => {
    const tag = tags.find(tag => tag.key === key) || {key, label: key};
    return `<span class="tlabel"><span${interactive ? ` role="link" tabindex="0" data-tag-key="${esc(key)}"` : ''} title="${esc(key)}">${esc(tagSummary(tag))}</span>${removable ? `<button type="button" class="x" data-drop-label="${esc(key)}" aria-label="Remove ${esc(tag.label)}">×</button>` : ''}</span>`;
  }).join('');
}
function openTagChip(event: Event) {
  const chip = eventElement(event)?.closest<HTMLElement>('[data-tag-key]'), key = 'key' in event && typeof event.key === 'string' ? event.key : '';
  if (!chip || event.type === 'keydown' && !['Enter', ' '].includes(key)) return;
  event.preventDefault(); event.stopPropagation();
  location.hash = '#/tag/' + encodeURIComponent(String(chip.dataset.tagKey));
}
document.addEventListener('click', openTagChip, true);
document.addEventListener('keydown', openTagChip, true);

// The filter's state: the tag keys the filter has seen, the one it is set to, and the tags the page holds.
interface TagFilterState {
  labels?: string[] | null;
  label?: string | null;
  tags?: import('../types/api').Tag[] | null;
}
function tagFilterOptions(state: TagFilterState) {
  const keys = [...new Set([...(state.labels || []), ...(state.label ? [state.label] : [])])];
  return '<option value="">Any tag</option>' + keys.map(key => {
    const tag = (state.tags || []).find(tag => tag.key === key) || {key, label: key};
    return `<option value="${esc(key)}"${key === state.label ? ' selected' : ''}>${esc(tagSummary(tag))}</option>`;
  }).join('');
}

// A checklist box in a tag's notes: the line of the markdown it is on, and whether it is ticked.
interface TagCheck {
  line: number;
  checked: boolean;
}
// The fields a tag save sends: the ones the notes and the edit form change.
interface TagSaveFields {
  label?: string | undefined;
  markdown?: string | undefined;
  metadata?: object;
}
// Saves the tag. A checklist box passes its index (to keep focus on it); the edit form passes the version it started from.
type TagSave = (fields: TagSaveFields, checkIndex?: number | null, editVersion?: number | null) => Promise<void>;
function tagChecklist(host: Element, tag: import('../types/api').Tag, editable: boolean, save: TagSave) {
  const checks: TagCheck[] = [], prefix = 'TAGCHECK' + newRequestId().replace(/-/g, '') + 'X';
  let fence = '';
  const source = (tag.markdown || '').split('\n').map((line, index) => {
    const boundary = /^\s{0,3}(`{3,}|~{3,})/.exec(line), mark = boundary?.[1];
    if (mark) {
      if (!fence) fence = mark;
      else if (mark[0] === fence[0] && mark.length >= fence.length) fence = '';
      return line;
    }
    if (fence) return line;
    return line.replace(/^(\s*(?:[-*+]|\d+\.)\s+)\[([ xX])\]\s+/, (_: string, before: string, checked: string) => {
      const id = checks.length;
      checks.push({line: index, checked: checked.toLowerCase() === 'x'});
      return before + prefix + id + ' ';
    });
  }).join('\n');
  host.innerHTML = md(source) || '<p class="muted">No notes</p>';
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT), nodes: Text[] = [];
  while (walker.nextNode()) {
    const text = walker.currentNode;
    if (text instanceof Text && text.data.includes(prefix)) nodes.push(text);
  }
  for (const node of nodes) {
    const pieces = node.data.split(new RegExp('(' + prefix + '\\d+)'));
    const fragment = document.createDocumentFragment();
    for (const piece of pieces) {
      const match = new RegExp('^' + prefix + '(\\d+)$').exec(piece);
      if (!match) { fragment.append(document.createTextNode(piece)); continue; }
      const checkIndex = Number(match[1]), check = checks[checkIndex];
      if (!check) continue;
      if (node.parentElement?.closest('pre,code')) { fragment.append(document.createTextNode(check.checked ? '[x]' : '[ ]')); continue; }
      const input = document.createElement('input');
      input.dataset.tagCheck = String(checkIndex);
      input.type = 'checkbox'; input.checked = check.checked; input.disabled = !editable;
      input.onchange = async () => {
        host.querySelectorAll('input').forEach(input => { input.disabled = true; });
        const lines = (tag.markdown || '').split('\n'), line = lines[check.line];
        if (line === undefined) return;
        lines[check.line] = line.replace(/^(\s*(?:[-*+]|\d+\.)\s+)\[[ xX]\]/, '$1[' + (input.checked ? 'x' : ' ') + ']');
        await save({markdown: lines.join('\n')}, checkIndex);
      };
      fragment.append(input);
    }
    node.replaceWith(fragment);
  }
  for (const input of host.querySelectorAll('input[data-tag-check]')) {
    const parent = input.parentElement, label = document.createElement('label');
    if (!parent) continue;
    parent.insertBefore(label, input);
    const inline = new Set(['A', 'BR', 'STRONG', 'EM', 'DEL', 'CODE', 'IMG']);
    while (label.nextSibling && (label.nextSibling === input || label.nextSibling.nodeType === Node.TEXT_NODE || inline.has(label.nextSibling.nodeName))) label.append(label.nextSibling);
    if (!label.textContent?.trim()) input.setAttribute('aria-label', 'Checklist item');
  }
}

let TAG_PAGE_SEQ = 0;
async function pageTag(key: string) {
  const routeAt = S.route, sequence = ++TAG_PAGE_SEQ;
  const current = () => S.route === routeAt && TAG_PAGE_SEQ === sequence;
  $('#main').innerHTML = '<div class="empty" role="status">Loading tag…</div>';
  try {
    const data = await get<import('../types/api').TagDetail>('/v2/tags/' + encodeURIComponent(key));
    if (!current()) return;
    const tag = data.tag;
    $('#main').innerHTML = `<div class="page-tags">
      <div class="board-tools"><h1 data-tag-heading>${esc(tagSummary(tag))}</h1><a href="#/tags">Tags</a></div>
      ${tag.bot_writers.length ? `<p class="muted" data-tag-bot-writers>Bots that may set this tag on any task they can read: ${esc(tag.bot_writers.map(actor => botDisplayName(actor.replace(/^bot:/, ''))).join(', '))}</p>` : ''}
      <section class="card"><header><h2>${tag.is_template ? 'Template' : 'Checklist'}</h2>${data.editable ? '<button type="button" data-tag-edit>Edit</button>' : ''}</header><div class="tag-notes" data-tag-notes></div><div data-tag-editor hidden></div></section>
      ${tag.is_template ? `<section class="card"><header><h2>Make a tag</h2></header>${tagCreateForm(tag)}</section>` : ''}
      <section class="card"><header><h2>Tasks</h2></header><div data-tag-tasks>${tagTaskRows(data.tasks)}</div>${data.next_offset != null ? '<button type="button" data-tag-more>More</button>' : ''}</section></div>`;
    const save: TagSave = async (fields, checkIndex = null, editVersion = null) => {
      try {
        const result: import('../types/api').TagResult = await post('/v2/tags/' + encodeURIComponent(tag.id), {version: editVersion ?? tag.version, ...fields});
        if (!current()) return;
        Object.assign(tag, result.tag);
        $('[data-tag-heading]').textContent = tagSummary(tag);
        tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
        if (checkIndex != null) $('[data-tag-notes] [data-tag-check="' + checkIndex + '"]')?.focus({preventScroll: true});
        else $('[data-tag-editor]').hidden = true;
      } catch (error) {
        if (!current()) return;
        if (editVersion != null) throw error;
        toast(errorMessage(error, ''));
        if (errorStatus(error) === 409) {
          const notes = $('[data-tag-notes]');
          if (notes) notes.innerHTML = '<p role="status">Tag changed. <button type="button" data-tag-reload>Reload</button></p>';
          $('[data-tag-reload]')?.addEventListener('click', async () => {
            try {
              const latest = await get<import('../types/api').TagDetail>('/v2/tags/' + encodeURIComponent(tag.id));
              if (!current()) return;
              Object.assign(tag, latest.tag); data.editable = latest.editable;
              $('[data-tag-heading]').textContent = tagSummary(tag);
              tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
            } catch (error) { if (current()) toast(errorMessage(error, '')); }
          });
        } else tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
      }
    };
    tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
    $('[data-tag-edit]')?.addEventListener('click', () => tagEditForm(tag, save, data.editable));
    if (tag.is_template) bindTagCreate($('#main'), tag);
    const more = $('[data-tag-more]');
    if (more) more.onclick = async () => {
      more.disabled = true;
      try {
        const next = await get<import('../types/api').TagDetail>('/v2/tags/' + encodeURIComponent(tag.id) + '?offset=' + data.next_offset);
        if (!current()) return;
        $('[data-tag-tasks]').insertAdjacentHTML('beforeend', tagTaskRows(next.tasks));
        data.next_offset = next.next_offset;
        more.hidden = next.next_offset == null;
      } catch (error) { toast(errorMessage(error, '')); }
      finally { more.disabled = false; }
    };
  } catch (error) {
    if (current()) $('#main').innerHTML = `<div class="empty" role="alert">${esc(errorMessage(error, ''))}</div>`;
  }
}
function tagTaskRows(tasks: import('../types/api').Task[] | null | undefined) {
  return (tasks || []).map(task => `<a class="tag-task" href="#/task/${encodeURIComponent(task.id)}">${esc(task.title)} <span class="muted">${esc(taskLine(task))}</span></a>`).join('') || '<div class="empty">No tasks</div>';
}
// A new tag is a name and notes; its key (made from the name) and its metadata wait under Advanced. A tag made from a
// template asks for its key, since that names this instance (release-2026-10-02). Keys stop at 40 characters, where the
// server cuts them (hubdb._labels), and never end in a hyphen: the server strips a posted key's edge hyphens before its
// cut, so a key made here is saved exactly as the Key line shows it.
const TAG_KEY_MAX = 40;
const tagKeyOf = (name: string | null | undefined) => String(name || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  .slice(0, TAG_KEY_MAX).replace(/-+$/, '');
const tagKeyTyped = (key: string | null | undefined) => String(key || '').trim().toLowerCase().replace(/\s+/g, '-').replace(/^-+|-+$/g, '')
  .slice(0, TAG_KEY_MAX).replace(/-+$/, '');
// The named fields of the tag forms, as tagCreateForm and tagEditForm draw them. Each form has only some of them: the
// template form has no label, markdown or is_template, the edit form has no key, so those are optional here.
interface TagFormElements {
  key?: HTMLInputElement;
  label?: HTMLInputElement;
  markdown?: HTMLTextAreaElement;
  metadata: HTMLTextAreaElement;
  is_template?: HTMLInputElement;
}
interface TagForm extends HTMLFormElement {
  readonly elements: HTMLFormControlsCollection & TagFormElements;
}
const tagKeyFor = (form: TagForm) => tagKeyTyped(form.elements.key?.value) || tagKeyOf(form.elements.label?.value);
function tagCreateForm(template: import('../types/api').Tag | null = null) {
  const metadata = `<label>Metadata <textarea name="metadata" rows="2">${esc(JSON.stringify(template?.metadata || {}, null, 2))}</textarea></label>`;
  if (template) return `<form class="tag-form" data-tag-create>
    <label>Key <input name="key" required maxlength="${TAG_KEY_MAX}" placeholder="release-2026-10-02"></label>${metadata}
    <button type="submit">Create</button><p role="alert" data-tag-error></p></form>`;
  return `<form class="tag-form" data-tag-create>
    <label>Name <input name="label" required placeholder="bug"></label>
    <label>Notes <textarea name="markdown" rows="5" placeholder="- [ ] Check the result"></textarea></label>
    <label><input type="checkbox" name="is_template"> Template</label>
    <details class="tag-advanced"><summary>Advanced</summary><label>Key <input name="key" maxlength="${TAG_KEY_MAX}" placeholder="made from the name"></label>
      <small class="tag-key-line" data-tag-key-line aria-live="polite">Key: made from the name</small>${metadata}</details>
    <button type="submit">Create</button><p role="alert" data-tag-error></p></form>`;
}
function bindTagCreate(root: ParentNode, template: import('../types/api').Tag | null = null) {
  const form: TagForm | null = $('[data-tag-create]', root);
  if (!form) return;
  const line = $('[data-tag-key-line]', form);
  if (line) form.oninput = () => { line.textContent = 'Key: ' + (tagKeyFor(form) || 'made from the name'); };
  form.onsubmit = async (event: Event) => {
    event.preventDefault();
    const button = $('button[type=submit]', form); button.disabled = true;
    try {
      const metadata: unknown = JSON.parse(form.elements.metadata.value || '{}');
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Metadata must be a JSON object');
      const body = {key: tagKeyFor(form), metadata};
      if (!body.key) throw new Error('Give the tag a name');
      if (!template) Object.assign(body, {label: form.elements.label?.value || body.key, is_template: form.elements.is_template?.checked, markdown: form.elements.markdown?.value});
      const result: import('../types/api').TagResult = await post(template ? '/v2/tags/' + encodeURIComponent(template.id) + '/instances' : '/v2/tags', body);
      location.hash = '#/tag/' + encodeURIComponent(result.tag.key);
    } catch (error) { $('[data-tag-error]', form).textContent = errorMessage(error, ''); }
    finally { button.disabled = false; }
  };
}
function tagEditForm(tag: import('../types/api').Tag, save: TagSave, editable: boolean) {
  let version = tag.version;
  const editor = $('[data-tag-editor]');
  editor.hidden = false;
  editor.innerHTML = `<form class="tag-form"><label>Label <input name="label" value="${esc(tag.label)}" required></label>
    <label>Notes <textarea name="markdown" rows="8">${esc(tag.markdown)}</textarea></label>
    <details class="tag-advanced"><summary>Advanced</summary><label>Metadata <textarea name="metadata" rows="3">${esc(JSON.stringify(tag.metadata, null, 2))}</textarea></label></details>
    <button type="submit">Save</button><button type="button" data-tag-cancel>Cancel</button><p role="alert" data-tag-error></p><button type="button" data-tag-refresh hidden>Load current</button></form>`;
  $('[data-tag-cancel]', editor).onclick = () => { editor.hidden = true; };
  const form: TagForm = $('form', editor);
  form.onsubmit = async (event: Event) => {
    event.preventDefault();
    try {
      const metadata: unknown = JSON.parse(form.elements.metadata.value || '{}');
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Metadata must be a JSON object');
      $('button[type=submit]', form).disabled = true;
      await save({label: form.elements.label?.value, metadata, markdown: form.elements.markdown?.value}, null, version);
    } catch (error) {
      $('[data-tag-error]', form).textContent = errorMessage(error, '');
      const reload = $('[data-tag-refresh]', form);
      reload.hidden = errorStatus(error) !== 409;
      reload.onclick = async () => {
        reload.disabled = true;
        try {
          const latest = await get<import('../types/api').TagDetail>('/v2/tags/' + encodeURIComponent(tag.id));
          if (!editor.isConnected) return;
          Object.assign(tag, latest.tag); version = tag.version;
          $('[data-tag-heading]').textContent = tagSummary(tag);
          tagChecklist($('[data-tag-notes]'), tag, latest.editable, save);
          $('[data-tag-error]', form).textContent = 'Current notes loaded. Combine your changes before saving.';
          reload.hidden = true;
        } catch (error) { $('[data-tag-error]', form).textContent = errorMessage(error, ''); }
        finally { reload.disabled = false; }
      };
    }
    finally { $('button[type=submit]', form).disabled = false; }
  };
}
async function renderTags(host: Element) {
  try {
    const data = await get<import('../types/api').TagList>('/v2/tags');
    if (!host.isConnected) return;
    const list = (template: boolean) => data.tags.filter(tag => tag.is_template === template).map(tag =>
      `<a class="tag-task" href="#/tag/${encodeURIComponent(tag.key)}">${esc(tagSummary(tag))}</a>`).join('');
    // The release checklist starter: the way to begin when there are no templates, a small button once there are.
    const starter = (label: string) => `<button type="button" class="ghost" data-release-starter>${label}</button>`;
    const templates = list(true);
    // patchHTML keeps the New tag form's node (its markup never changes), so a refresh that lands while someone types
    // keeps their values, focus and an open Advanced. A full draw (the first, or after an error) reopens Advanced below.
    const advanced = !!$('[data-tag-create] .tag-advanced[open]', host);
    patchHTML(host, `<section class="card"><header><h2>Templates</h2>${templates ? starter('Release checklist') : ''}</header>${templates
        || `<div class="empty tags-empty">No templates. ${starter('Start with a release checklist')}</div>`}</section>
      <section class="card"><header><h2>Tags</h2></header>${list(false) || '<div class="empty">None yet</div>'}</section>
      <section class="card"><header><h2>New tag</h2></header>${tagCreateForm()}</section>`);
    if (advanced) $('[data-tag-create] .tag-advanced', host).open = true;
    bindTagCreate(host);
    const releaseStarter: HTMLButtonElement = $('[data-release-starter]', host);
    releaseStarter.onclick = async () => {
      const button = releaseStarter;
      button.disabled = true;
      try {
        let tag = data.tags.find(tag => tag.key === 'release-checklist');
        if (!tag) {
          try {
            const result: import('../types/api').TagResult = await post('/v2/tags', {key: 'release-checklist', label: 'release', is_template: true,
              markdown: '- [ ] Run release scripts and migrations\n- [ ] Deploy\n- [ ] Run smoke checks\n- [ ] Tell the team'});
            tag = result.tag;
          } catch (error) {
            // Another person can create the starter between listing and clicking it.
            if (errorCode(error) !== 'duplicate') throw error;
            tag = (await get<import('../types/api').TagResult>('/v2/tags/release-checklist')).tag;
          }
        }
        if (host.isConnected) location.hash = '#/tag/' + encodeURIComponent(tag.key);
      } catch (error) { if (host.isConnected) toast(errorMessage(error, '')); }
      finally { button.disabled = false; }
    };
  } catch (error) { if (host.isConnected) host.innerHTML = `<div class="empty" role="alert">${esc(errorMessage(error, ''))}</div>`; }
}
function pageTags() {
  $('#main').innerHTML = '<div class="page-tags"><h1>Tags</h1><div data-tags-list></div></div>';
  void renderTags($('[data-tags-list]'));
}
function settingsTags() {
  const pane = $('#settings-tags');
  if (!pane) return;
  pane.hidden = SETTINGS_TAB !== 'tasks';
  if (!pane.hidden && !formBusy(pane)) void renderTags(pane);
}
