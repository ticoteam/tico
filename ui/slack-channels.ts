/* Tools > Slack channels: which channels bots may read and post in (docs/slack.md). An owner or an admin adds one,
   names the bots that read it, and says whether bots may post there. Bots' Slack reads follow this list. */
'use strict';
window.mountSlackChannels = async function (host) {
  if (!host) return;
  const text = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  let state;
  try { state = await get('/v2/slack/channels'); }
  catch (error) { host.innerHTML = `<div class="empty">${text(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  const bots = state.bots || [];
  const botName = id => (bots.find(b => b.id === id) || {}).name || id;
  const file = state.registry_file || {};
  const importNote = file.present && !file.imported
    ? `<p data-slack-import><span>registry/slack-channels.yaml lists ${Number(file.channels)} channel${file.channels === 1 ? '' : 's'}.</span>
        <button type="button" class="ghost" data-slack-import-go>Import</button></p>` : '';
  const options = row => bots.filter(b => !row.readers.includes(b.id))
    .map(b => `<option value="${text(b.id)}">${text(b.name)}</option>`).join('');
  const rows = (state.channels || []).map(row => {
    const fromFile = row.source === 'file';
    return `<tr data-channel="${text(row.channel)}">
      <td><strong>${text(row.channel)}</strong>${row.id && row.name ? `<div class="muted">${text(row.id)}</div>` : ''}</td>
      <td>${row.readers.map(id => `<span class="fchip">${text(botName(id))}${fromFile ? '' : ` <button type="button" class="linkish" data-slack-unread="${text(id)}" aria-label="Stop ${text(botName(id))} reading">×</button>`}</span>`).join(' ') || '<span class="muted">None</span>'}
        ${!fromFile && options(row) ? `<select data-slack-reader aria-label="Add a reader"><option value="">Add reader</option>${options(row)}</select>` : ''}</td>
      <td><label><input type="checkbox" data-slack-post${row.post ? ' checked' : ''}${fromFile ? ' disabled' : ''}> Bots post</label></td>
      <td>${fromFile ? `<span class="muted">${text(row.note)}</span>` : `<input data-slack-note value="${text(row.note)}" maxlength="500" aria-label="Note" style="width:100%">`}</td>
      <td>${fromFile ? '' : '<button type="button" class="ghost danger" data-slack-remove>Remove</button>'}</td></tr>`;
  }).join('');
  host.innerHTML = `${importNote}
    ${rows ? `<table class="int-list"><thead><tr><th>Channel</th><th>Read by</th><th></th><th>Note</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
      : '<div class="empty">No channels yet.</div>'}
    <form data-slack-add style="display:grid;gap:10px;max-width:460px;margin-top:12px">
      <label>Channel<input name="channel" required autocomplete="off" spellcheck="false" placeholder="#customer_success or C0123456789"></label>
      <label>Read by<select name="reader" multiple size="${Math.min(6, Math.max(2, bots.length))}">${bots.map(b => `<option value="${text(b.id)}">${text(b.name)}</option>`).join('')}</select></label>
      <label><input type="checkbox" name="post" checked> Bots may post there</label>
      <label>Note<input name="note" maxlength="500" autocomplete="off"></label>
      <button class="primary" type="submit">Add channel</button></form>
    <p role="status" data-slack-status></p>`;
  const status = host.querySelector('[data-slack-status]');
  const refresh = () => window.mountSlackChannels(host);
  const change = async (path, body) => {
    try { await post(path, body); await refresh(); }
    catch (error) { status.textContent = error.message; }
  };
  host.querySelector('[data-slack-import-go]')?.addEventListener('click', event => {
    event.target.disabled = true;
    change('/v2/slack/channels/import', {});
  });
  host.querySelector('[data-slack-add]').onsubmit = event => {
    event.preventDefault();
    const form = event.target;
    change('/v2/slack/channels', {channel: form.channel.value.trim(), post: form.post.checked, note: form.note.value.trim(),
      readers: [...form.reader.selectedOptions].map(o => o.value)});
  };
  host.querySelectorAll('tr[data-channel]').forEach(tr => {
    const channel = tr.dataset.channel;
    tr.querySelector('[data-slack-remove]')?.addEventListener('click', () => {
      if (confirm(`Take ${channel} off the list?`)) change('/v2/slack/channels/remove', {channel});
    });
    tr.querySelectorAll('[data-slack-unread]').forEach(b => b.addEventListener('click',
      () => change('/v2/slack/channels/remove', {channel, reader: b.dataset.slackUnread})));
    tr.querySelector('[data-slack-reader]')?.addEventListener('change', event => {
      if (event.target.value) change('/v2/slack/channels', {channel, readers: [event.target.value]});
    });
    tr.querySelector('[data-slack-post]')?.addEventListener('change', event => change('/v2/slack/channels', {channel, post: event.target.checked}));
    tr.querySelector('[data-slack-note]')?.addEventListener('change', event => change('/v2/slack/channels', {channel, note: event.target.value.trim()}));
  });
};
