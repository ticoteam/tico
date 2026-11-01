/* Tools > Meeting importers (owner). Turns Zoom, Google Meet and
   Granola on, picks the enrolled computer that runs each one, and shows how it is doing. Their
   credentials are never typed here: they are files on that computer (docs/meetings.md).
   `only` shows one importer (the setup dialog on the Meetings page) and `onChange` runs after a save. */
'use strict';
window.mountMeetingImporters = async function (host, options) {
  if (!host) return;
  const {only = '', onChange} = options || {};
  const esc = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  const when = value => value ? (typeof ago === 'function' ? ago(value) : new Date(value).toLocaleString()) : 'never';
  const labels = {off: 'Off', waiting: 'Waiting for the computer', syncing: 'Syncing', delayed: 'Delayed', error: 'Error'};
  if (only === 'fireflies') {
    host.innerHTML = '<div class="empty">Fireflies is no longer available as an importer. Existing meetings and files remain available.</div>';
    return;
  }
  let state;
  try { state = await get('/v2/meeting-importers'); }
  catch (error) { host.innerHTML = `<div class="empty">${esc(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  const machines = state.computers || [];
  if (!Array.isArray(state.importers)) { host.innerHTML = '<div class="empty">Meeting importers are unavailable.</div>'; return; }
  host.innerHTML = (state.importers || []).filter(i => i.source !== 'fireflies' && (!only || i.source === only)).map(i => {
    const options = ['<option value="">Computer</option>'].concat(machines.map(m =>
      `<option value="${esc(m.id)}"${m.id === i.runner_id ? ' selected' : ''}>${esc(m.label)}${m.platform ? ' (' + esc(m.platform) + ')' : ''}${m.online ? '' : ' - offline'}</option>`)).join('');
    const facts = i.enabled ? `<p class="muted" data-imp-facts>Last sync ${esc(when(i.last_succeeded_at))} · last import ${esc(when(i.last_import))} · ${Number(i.imported_total) || 0} imported</p>` : '';
    const error = i.error ? `<p role="alert" data-imp-error>${esc(i.error)}</p>` : '';
    return `<form class="meeting-importer" data-importer="${esc(i.source)}" style="display:grid;gap:8px;padding:12px 0;${only ? '' : 'border-top:1px solid var(--line)'}">
      <div class="row"><strong>${esc(i.name)}</strong><span class="muted" data-imp-status>${esc(labels[i.status] || i.status)}</span></div>
      <div class="row"><label><input type="checkbox" name="enabled"${i.enabled ? ' checked' : ''} aria-label="Enable ${esc(i.name)}"> Enabled</label>
        <label>Computer <select name="runner" aria-label="Computer that runs ${esc(i.name)}">${options}</select></label>
        <button class="primary" type="submit" aria-label="Save ${esc(i.name)}">Save</button></div>
      ${facts}${error}
      <details${only && !i.enabled ? ' open' : ''}><summary>Set up credentials</summary>
        <p class="muted">On that computer, create <code>${esc(i.setup.file)}</code> (mode 600) with:</p>
        <pre>${i.setup.keys.map(k => esc(k) + '=').join('\n')}</pre>
        <p class="muted">Steps: ${esc(i.setup.doc)}. Check, from the Tico folder: <code>runtime/runner-venv/bin/python -m runner importers-doctor</code></p></details>
      <p role="status" data-imp-message></p></form>`;
  }).join('') + (machines.length ? '' : '<p class="empty">Add a computer under Computers first.</p>');
  host.querySelectorAll('form[data-importer]').forEach(form => {
    form.onsubmit = async event => {
      event.preventDefault();
      const message = form.querySelector('[data-imp-message]'), button = form.querySelector('button[type=submit]');
      message.textContent = '';
      button.disabled = true;
      try {
        await post('/v2/meeting-importers/' + form.dataset.importer, {enabled: form.enabled.checked, runner_id: form.runner.value});
        window.mountMeetingImporters(host, options);
        if (onChange) onChange();
      } catch (error) { message.textContent = error.message; button.disabled = false; }
    };
  });
};

/* The Meetings page's source tiles open one importer's setup in a dialog, so nobody leaves the page. */
window.openMeetingImporter = function (source, name, onChange) {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal import-modal';
  dialog.setAttribute('aria-label', 'Connect ' + name);
  const title = document.createElement('h2'), close = document.createElement('button'), host = document.createElement('div');
  title.textContent = name; title.style.margin = '0';
  close.type = 'button'; close.className = 'ghost'; close.textContent = '✕'; close.setAttribute('aria-label', 'Close');
  host.innerHTML = '<div class="empty">Loading…</div>';
  const head = document.createElement('header');
  head.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:16px 18px 0';
  head.append(title, close);
  host.style.padding = '0 18px 14px';
  dialog.append(head, host);
  document.body.appendChild(dialog);
  close.onclick = () => dialog.close();
  dialog.onclick = e => { if (e.target === dialog) dialog.close(); };
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
  window.mountMeetingImporters(host, {only: source, onChange});
};
