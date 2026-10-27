/* Tags: display chips, checklist pages and templates in Settings. */
'use strict';

function tagSummary(tag) {
  const values = Object.values(tag?.metadata || {}).slice(0, 2).map(value => {
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const date = new Date(value + 'T12:00:00Z');
      if (!Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value) return date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'});
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  });
  return [tag?.label || tag?.key || '', ...values].join(' · ');
}
function tagChips(keys, tags = [], removable = false, interactive = true) {
  return (keys || []).map(key => {
    const tag = tags.find(tag => tag.key === key) || {key, label: key};
    return `<span class="tlabel"><span${interactive ? ` role="link" tabindex="0" data-tag-key="${esc(key)}"` : ''} title="${esc(key)}">${esc(tagSummary(tag))}</span>${removable ? `<button type="button" class="x" data-drop-label="${esc(key)}" aria-label="Remove ${esc(tag.label)}">×</button>` : ''}</span>`;
  }).join('');
}
function openTagChip(event) {
  const chip = event.target.closest('[data-tag-key]');
  if (!chip || event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
  event.preventDefault(); event.stopPropagation();
  location.hash = '#/tag/' + encodeURIComponent(chip.dataset.tagKey);
}
document.addEventListener('click', openTagChip, true);
document.addEventListener('keydown', openTagChip, true);

function tagFilterOptions(state) {
  const keys = [...new Set([...(state.labels || []), ...(state.label ? [state.label] : [])])];
  return '<option value="">Any tag</option>' + keys.map(key => {
    const tag = (state.tags || []).find(tag => tag.key === key) || {key, label: key};
    return `<option value="${esc(key)}"${key === state.label ? ' selected' : ''}>${esc(tagSummary(tag))}</option>`;
  }).join('');
}

function tagChecklist(host, tag, editable, save) {
  const checks = [], prefix = 'TAGCHECK' + newRequestId().replace(/-/g, '') + 'X';
  let fence = '';
  const source = (tag.markdown || '').split('\n').map((line, index) => {
    const boundary = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (boundary) {
      if (!fence) fence = boundary[1];
      else if (boundary[1][0] === fence[0] && boundary[1].length >= fence.length) fence = '';
      return line;
    }
    if (fence) return line;
    return line.replace(/^(\s*(?:[-*+]|\d+\.)\s+)\[([ xX])\]\s+/, (_, before, checked) => {
      const id = checks.length;
      checks.push({line: index, checked: checked.toLowerCase() === 'x'});
      return before + prefix + id + ' ';
    });
  }).join('\n');
  host.innerHTML = md(source) || '<p class="muted">No notes</p>';
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT), nodes = [];
  while (walker.nextNode()) if (walker.currentNode.textContent.includes(prefix)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const pieces = node.textContent.split(new RegExp('(' + prefix + '\\d+)'));
    const fragment = document.createDocumentFragment();
    for (const piece of pieces) {
      const match = new RegExp('^' + prefix + '(\\d+)$').exec(piece);
      if (!match) { fragment.append(document.createTextNode(piece)); continue; }
      const checkIndex = Number(match[1]), check = checks[checkIndex];
      if (node.parentElement.closest('pre,code')) { fragment.append(document.createTextNode(check.checked ? '[x]' : '[ ]')); continue; }
      const input = document.createElement('input');
      input.dataset.tagCheck = String(checkIndex);
      input.type = 'checkbox'; input.checked = check.checked; input.disabled = !editable;
      input.onchange = async () => {
        host.querySelectorAll('input').forEach(input => { input.disabled = true; });
        const lines = (tag.markdown || '').split('\n');
        lines[check.line] = lines[check.line].replace(/^(\s*(?:[-*+]|\d+\.)\s+)\[[ xX]\]/, '$1[' + (input.checked ? 'x' : ' ') + ']');
        await save({markdown: lines.join('\n')}, checkIndex);
      };
      fragment.append(input);
    }
    node.replaceWith(fragment);
  }
  for (const input of host.querySelectorAll('input[data-tag-check]')) {
    const parent = input.parentElement, label = document.createElement('label');
    parent.insertBefore(label, input);
    const inline = new Set(['A', 'BR', 'STRONG', 'EM', 'DEL', 'CODE', 'IMG']);
    while (label.nextSibling && (label.nextSibling === input || label.nextSibling.nodeType === Node.TEXT_NODE || inline.has(label.nextSibling.nodeName))) label.append(label.nextSibling);
    if (!label.textContent.trim()) input.setAttribute('aria-label', 'Checklist item');
  }
}

let TAG_PAGE_SEQ = 0;
async function pageTag(key) {
  const routeAt = S.route, sequence = ++TAG_PAGE_SEQ;
  const current = () => S.route === routeAt && TAG_PAGE_SEQ === sequence;
  $('#main').innerHTML = '<div class="empty" role="status">Loading tag…</div>';
  try {
    const data = await get('/v2/tags/' + encodeURIComponent(key));
    if (!current()) return;
    const tag = data.tag;
    $('#main').innerHTML = `<div class="page-tags">
      <div class="board-tools"><h1 data-tag-heading>${esc(tagSummary(tag))}</h1><a href="#/tags">Tags</a></div>
      <section class="card"><header><h2>${tag.is_template ? 'Template' : 'Checklist'}</h2>${data.editable ? '<button type="button" data-tag-edit>Edit</button>' : ''}</header><div class="tag-notes" data-tag-notes></div><div data-tag-editor hidden></div></section>
      ${tag.is_template ? `<section class="card"><header><h2>Make a tag</h2></header>${tagCreateForm(tag)}</section>` : ''}
      <section class="card"><header><h2>Tasks</h2></header><div data-tag-tasks>${tagTaskRows(data.tasks)}</div>${data.next_offset != null ? '<button type="button" data-tag-more>More</button>' : ''}</section></div>`;
    const save = async (fields, checkIndex = null, editVersion = null) => {
      try {
        const result = await post('/v2/tags/' + encodeURIComponent(tag.id), {version: editVersion ?? tag.version, ...fields});
        if (!current()) return;
        Object.assign(tag, result.tag);
        $('[data-tag-heading]').textContent = tagSummary(tag);
        tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
        if (checkIndex != null) $('[data-tag-notes] [data-tag-check="' + checkIndex + '"]')?.focus({preventScroll: true});
        else $('[data-tag-editor]').hidden = true;
      } catch (error) {
        if (!current()) return;
        if (editVersion != null) throw error;
        toast(error.message);
        if (error.status === 409) {
          const notes = $('[data-tag-notes]');
          if (notes) notes.innerHTML = '<p role="status">Tag changed. <button type="button" data-tag-reload>Reload</button></p>';
          $('[data-tag-reload]')?.addEventListener('click', async () => {
            try {
              const latest = await get('/v2/tags/' + encodeURIComponent(tag.id));
              if (!current()) return;
              Object.assign(tag, latest.tag); data.editable = latest.editable;
              $('[data-tag-heading]').textContent = tagSummary(tag);
              tagChecklist($('[data-tag-notes]'), tag, data.editable, save);
            } catch (error) { if (current()) toast(error.message); }
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
        const next = await get('/v2/tags/' + encodeURIComponent(tag.id) + '?offset=' + data.next_offset);
        if (!current()) return;
        $('[data-tag-tasks]').insertAdjacentHTML('beforeend', tagTaskRows(next.tasks));
        data.next_offset = next.next_offset;
        more.hidden = next.next_offset == null;
      } catch (error) { toast(error.message); }
      finally { more.disabled = false; }
    };
  } catch (error) {
    if (current()) $('#main').innerHTML = `<div class="empty" role="alert">${esc(error.message)}</div>`;
  }
}
function tagTaskRows(tasks) {
  return (tasks || []).map(task => `<a class="tag-task" href="#/task/${encodeURIComponent(task.id)}">${esc(task.title)} <span class="muted">${esc(STATUS_WORD[task.status] || task.status)}</span></a>`).join('') || '<div class="empty">No tasks</div>';
}
// A new tag is a name and notes; its key (made from the name) and its metadata wait under Advanced. A tag made from a
// template asks for its key, since that names this instance (release-2026-10-02). Keys stop at 40 characters, where the
// server cuts them (hubdb._labels), and never end in a hyphen: the server strips a posted key's edge hyphens before its
// cut, so a key made here is saved exactly as the Key line shows it.
const TAG_KEY_MAX = 40;
const tagKeyOf = name => String(name || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  .slice(0, TAG_KEY_MAX).replace(/-+$/, '');
const tagKeyTyped = key => String(key || '').trim().toLowerCase().replace(/\s+/g, '-').replace(/^-+|-+$/g, '')
  .slice(0, TAG_KEY_MAX).replace(/-+$/, '');
const tagKeyFor = form => tagKeyTyped(form.elements.key.value) || tagKeyOf(form.elements.label?.value);
function tagCreateForm(template = null) {
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
function bindTagCreate(root, template = null) {
  const form = $('[data-tag-create]', root);
  if (!form) return;
  const line = $('[data-tag-key-line]', form);
  if (line) form.oninput = () => { line.textContent = 'Key: ' + (tagKeyFor(form) || 'made from the name'); };
  form.onsubmit = async event => {
    event.preventDefault();
    const button = $('button[type=submit]', form); button.disabled = true;
    try {
      const metadata = JSON.parse(form.elements.metadata.value || '{}');
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Metadata must be a JSON object');
      const body = {key: tagKeyFor(form), metadata};
      if (!body.key) throw new Error('Give the tag a name');
      if (!template) Object.assign(body, {label: form.elements.label.value || body.key, is_template: form.elements.is_template.checked, markdown: form.elements.markdown.value});
      const result = await post(template ? '/v2/tags/' + encodeURIComponent(template.id) + '/instances' : '/v2/tags', body);
      location.hash = '#/tag/' + encodeURIComponent(result.tag.key);
    } catch (error) { $('[data-tag-error]', form).textContent = error.message; }
    finally { button.disabled = false; }
  };
}
function tagEditForm(tag, save, editable) {
  let version = tag.version;
  const editor = $('[data-tag-editor]');
  editor.hidden = false;
  editor.innerHTML = `<form class="tag-form"><label>Label <input name="label" value="${esc(tag.label)}" required></label>
    <label>Notes <textarea name="markdown" rows="8">${esc(tag.markdown)}</textarea></label>
    <details class="tag-advanced"><summary>Advanced</summary><label>Metadata <textarea name="metadata" rows="3">${esc(JSON.stringify(tag.metadata, null, 2))}</textarea></label></details>
    <button type="submit">Save</button><button type="button" data-tag-cancel>Cancel</button><p role="alert" data-tag-error></p><button type="button" data-tag-refresh hidden>Load current</button></form>`;
  $('[data-tag-cancel]', editor).onclick = () => { editor.hidden = true; };
  $('form', editor).onsubmit = async event => {
    event.preventDefault();
    const form = event.target;
    try {
      const metadata = JSON.parse(form.elements.metadata.value || '{}');
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Metadata must be a JSON object');
      $('button[type=submit]', form).disabled = true;
      await save({label: form.elements.label.value, metadata, markdown: form.elements.markdown.value}, null, version);
    } catch (error) {
      $('[data-tag-error]', form).textContent = error.message;
      const reload = $('[data-tag-refresh]', form);
      reload.hidden = error.status !== 409;
      reload.onclick = async () => {
        reload.disabled = true;
        try {
          const latest = await get('/v2/tags/' + encodeURIComponent(tag.id));
          if (!editor.isConnected) return;
          Object.assign(tag, latest.tag); version = tag.version;
          $('[data-tag-heading]').textContent = tagSummary(tag);
          tagChecklist($('[data-tag-notes]'), tag, latest.editable, save);
          $('[data-tag-error]', form).textContent = 'Current notes loaded. Combine your changes before saving.';
          reload.hidden = true;
        } catch (error) { $('[data-tag-error]', form).textContent = error.message; }
        finally { reload.disabled = false; }
      };
    }
    finally { $('button[type=submit]', form).disabled = false; }
  };
}
async function renderTags(host) {
  try {
    const data = await get('/v2/tags');
    if (!host.isConnected) return;
    const list = template => data.tags.filter(tag => tag.is_template === template).map(tag =>
      `<a class="tag-task" href="#/tag/${encodeURIComponent(tag.key)}">${esc(tagSummary(tag))}</a>`).join('');
    // The release checklist starter: the way to begin when there are no templates, a small button once there are.
    const starter = label => `<button type="button" class="ghost" data-release-starter>${label}</button>`;
    const templates = list(true);
    host.innerHTML = `<section class="card"><header><h2>Templates</h2>${templates ? starter('Release checklist') : ''}</header>${templates
        || `<div class="empty tags-empty">No templates. ${starter('Start with a release checklist')}</div>`}</section>
      <section class="card"><header><h2>Tags</h2></header>${list(false) || '<div class="empty">None yet</div>'}</section>
      <section class="card"><header><h2>New tag</h2></header>${tagCreateForm()}</section>`;
    bindTagCreate(host);
    $('[data-release-starter]', host).onclick = async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        let tag = data.tags.find(tag => tag.key === 'release-checklist');
        if (!tag) {
          try {
            const result = await post('/v2/tags', {key: 'release-checklist', label: 'release', is_template: true,
              markdown: '- [ ] Run release scripts and migrations\n- [ ] Deploy\n- [ ] Run smoke checks\n- [ ] Tell the team'});
            tag = result.tag;
          } catch (error) {
            // Another person can create the starter between listing and clicking it.
            if (error.body?.error?.code !== 'duplicate') throw error;
            tag = (await get('/v2/tags/release-checklist')).tag;
          }
        }
        if (host.isConnected) location.hash = '#/tag/' + encodeURIComponent(tag.key);
      } catch (error) { if (host.isConnected) toast(error.message); }
      finally { button.disabled = false; }
    };
  } catch (error) { if (host.isConnected) host.innerHTML = `<div class="empty" role="alert">${esc(error.message)}</div>`; }
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
