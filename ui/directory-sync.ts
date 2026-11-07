/* Settings > Humans > Sync with directory: pull humans from Google Workspace or Microsoft Entra ID, or
   accept them over SCIM (docs/people.md). Every sync is previewed first; the credentials never
   reach the browser after they are saved. */
'use strict';
window.mountDirectorySync = async function (host, onChange) {
  if (!host) return;
  const text = value => { const s = document.createElement('span'); s.textContent = value ?? ''; return s.innerHTML; };
  const lines = value => value.split('\n').map(x => x.trim()).filter(Boolean);
  let view;
  try {
    const got = await get('/v2/directory');
    view = {source: '', interval_minutes: 360, mass_leave_limit: 10, revision: 0, last: {}, ...got,
      filter: {groups: [], org_units: [], domains: [], ...got.filter}, credentials: {...got.credentials}, scim: {...got.scim}};
  } catch (error) { host.innerHTML = `<div class="empty">${text(error.message)}</div>`; return; }
  if (!host.isConnected) return;
  const GUIDE = 'https://github.com/ticoteam/tico/blob/main/docs/people.md#';
  const ANCHOR = {google: 'google-workspace', entra: 'microsoft-entra-id', scim: 'scim-okta-microsoft-entra-provisioning-jumpcloud'};
  const last = view.last?.at
    ? `Last sync ${text(new Date(view.last.at).toLocaleString())}: ${view.last.ok === false ? `failed, ${text(view.last.error)}` : view.last.held ? 'held for your confirmation' : 'ok'}`
    : 'Never synced';
  host.innerHTML = `
    <form data-ds-form class="ds-form">
      <div class="ds-row"><select name="source" aria-label="Directory">
          <option value="" disabled>Choose a directory</option><option value="google">Google Workspace</option>
          <option value="entra">Microsoft Entra ID</option><option value="scim">SCIM (Okta, Entra, JumpCloud)</option></select>
        <a data-ds-guide target="_blank" rel="noopener noreferrer">Setup guide</a></div>
      <div class="ds-row" data-ds-saved hidden><span class="muted" data-ds-hint></span><button class="ghost" type="button" data-ds-replace>Replace key</button></div>
      <div data-ds-creds="google" class="ds-stack" hidden>
        <label>Service account key <textarea name="service_account_json" rows="3" autocomplete="off" spellcheck="false" placeholder="{ &quot;type&quot;: &quot;service_account&quot;, ... }"></textarea></label>
        <label>Admin to act as <input name="admin_email" type="email" autocomplete="email" spellcheck="false" placeholder="admin@example.com"></label>
      </div>
      <div data-ds-creds="entra" class="ds-stack" hidden>
        <label>Tenant ID or domain <input name="tenant" type="text" autocomplete="off" spellcheck="false"></label>
        <label>Application (client) ID <input name="client_id" type="text" autocomplete="off" spellcheck="false"></label>
        <label>Client secret value <input name="client_secret" type="password" autocomplete="new-password"></label>
      </div>
      <div data-ds-scim class="ds-stack" hidden>
        <div class="ds-row">Base URL <code>${text(view.scim.url)}</code></div>
        <div class="ds-row"><button class="ghost" type="button" data-ds-token>${view.scim.enabled ? 'Replace token' : 'Create token'}</button>
          <span class="muted">${view.scim.enabled ? `Token created ${text(new Date(view.scim.created_at).toLocaleDateString())}` : ''}</span></div>
        <p data-ds-token-out></p>
      </div>
      <div class="ds-row" data-ds-status-row><span class="muted" data-ds-last>${last}</span>
        <button class="ghost" type="button" data-ds-now>Sync now</button></div>
      <details data-ds-options><summary>Options</summary><div>
        <div data-ds-filters class="ds-stack">
          <label><span data-ds-groups-label>Only these groups</span> <textarea name="groups" rows="2" spellcheck="false" placeholder="One per line. Empty: everyone">${text(view.filter.groups.join('\n'))}</textarea></label>
          <label data-ds-ou>Only these organizational units <textarea name="org_units" rows="2" spellcheck="false" placeholder="/Sales">${text(view.filter.org_units.join('\n'))}</textarea></label>
          <label>Only these email domains <textarea name="domains" rows="2" spellcheck="false" placeholder="example.com">${text(view.filter.domains.join('\n'))}</textarea></label>
          <label>Sync every <select name="interval_minutes"><option value="0">Only when I press Sync now</option><option value="60">hour</option><option value="360">6 hours</option><option value="1440">day</option></select></label>
        </div>
        <label class="ds-inline">Ask me first if more than<input name="mass_leave_limit" type="number" inputmode="numeric" min="0" max="10000" value="${text(view.mass_leave_limit)}">would be marked as left</label>
      </div></details>
      <div class="ds-row" data-ds-actions hidden><button class="primary" type="submit">Save</button></div>
    </form>
    <div data-ds-preview></div>
    <p role="status" data-ds-status></p>`;
  const form = host.querySelector('[data-ds-form]'), status = host.querySelector('[data-ds-status]');
  const $in = selector => host.querySelector(selector);
  form.source.value = view.source;
  form.interval_minutes.value = String(view.interval_minutes);
  let replacing = false, dirty = false;
  const show = () => {
    const source = form.source.value, pull = source === 'google' || source === 'entra', saved = view.source === source;
    const c = view.credentials[source], hasKey = pull && c?.configured;
    $in('[data-ds-guide]').href = GUIDE + (ANCHOR[source] || 'directory-sync');
    $in('[data-ds-saved]').hidden = !hasKey || replacing;
    $in('[data-ds-hint]').textContent = hasKey ? `Key saved: ${c.hint}` : '';
    host.querySelectorAll('[data-ds-creds]').forEach(el => { el.hidden = el.dataset.dsCreds !== source || (hasKey && !replacing); });
    $in('[data-ds-filters]').hidden = !pull;
    $in('[data-ds-ou]').hidden = source !== 'google';
    $in('[data-ds-groups-label]').textContent = source === 'entra' ? 'Only these groups (object IDs)' : 'Only these groups (email addresses)';
    $in('[data-ds-scim]').hidden = source !== 'scim';
    $in('[data-ds-options]').hidden = !source;
    // Status and Sync now belong to the saved source; Save shows once there is something to save.
    $in('[data-ds-status-row]').hidden = !source || !saved;
    $in('[data-ds-now]').hidden = !pull || !saved;
    $in('[data-ds-actions]').hidden = !source || (saved && !dirty);
  };
  form.source.onchange = () => { replacing = false; show(); };
  form.oninput = form.onchange = event => { if (event.target !== form.source) { dirty = true; show(); } };
  $in('[data-ds-replace]').onclick = () => { replacing = true; show(); };
  show();
  const body = () => {
    const source = form.source.value, fields = Object.fromEntries(new FormData(form));
    const out = {source, expected_revision: view.revision, interval_minutes: Number(fields.interval_minutes),
      mass_leave_limit: Number(fields.mass_leave_limit) || 0,
      filter: {groups: lines(fields.groups || ''), org_units: lines(fields.org_units || ''), domains: lines(fields.domains || '')}};
    const typed = source === 'google' ? fields.service_account_json?.trim() || fields.admin_email?.trim()
      : source === 'entra' ? fields.tenant?.trim() || fields.client_id?.trim() || fields.client_secret?.trim() : '';
    if (typed) out.credentials = Object.fromEntries(['service_account_json', 'admin_email', 'tenant', 'client_id', 'client_secret']
      .filter(k => fields[k] !== undefined).map(k => [k, fields[k]]));
    return out;
  };
  form.onsubmit = async event => {
    event.preventDefault();
    status.textContent = '';
    try {
      try { await put('/v2/directory', body()); }
      catch (error) {
        // github-app.key is lost with no backup copy: only an explicit confirm drops what it sealed (docs/install.md).
        if (error.body?.error?.code !== 'github_key_missing' || !confirm('The key for the saved GitHub App and directory sync secrets is lost, so they cannot be opened. Replace them? Any saved GitHub App and all directory sync credentials are removed, and you enter them again.')) throw error;
        await put('/v2/directory', {...body(), replace_lost_key: true});
      }
      toast?.('Directory saved'); onChange ? onChange() : window.mountDirectorySync(host);
    } catch (error) { status.textContent = error.message; }
  };
  $in('[data-ds-token]').onclick = async () => {
    if (view.scim.enabled && !confirm('Replace the SCIM token? The identity provider stops working until you give it the new one.')) return;
    try {
      const {token} = await post('/v2/directory/scim-token', {});
      $in('[data-ds-token-out]').innerHTML = `Token (copy it now): <code data-ds-token-value>${text(token)}</code>`;
    } catch (error) { status.textContent = error.message; }
  };
  const list = (title, rows, describe) => rows.length
    ? `<details ${rows.length <= 8 ? 'open' : ''}><summary>${text(title)} (${rows.length})</summary><ul>${rows.slice(0, 200).map(r => `<li>${describe(r)}</li>`).join('')}</ul></details>` : '';
  $in('[data-ds-now]').onclick = async event => {
    const button = event.target;
    button.disabled = true; status.textContent = 'Reading the directory…';
    const slot = $in('[data-ds-preview]');
    try {
      const preview = await post('/v2/directory/preview', {});
      const p = preview.plan, need = preview.needs_confirmation, needs = need.first || need.mass_leave;
      const any = ['adds', 'updates', 'restores', 'leaves'].some(k => p[k].length);
      slot.innerHTML = `<div data-ds-plan><h3>Preview: nothing is changed until you apply</h3>
        <p><b>${p.adds.length}</b> to add, <b>${p.updates.length}</b> to update, <b>${p.restores.length}</b> to restore, <b>${p.leaves.length}</b> to mark as left.</p>
        ${list('Add', p.adds, r => `${text(r.name || r.email)} <span class="muted">${text(r.email)}</span>`)}
        ${list('Update', p.updates, r => `${text(r.email)} <span class="muted">${text(Object.keys(r.changes).join(', '))}</span>`)}
        ${list('Restore', p.restores, r => text(r.email))}
        ${list('Mark as left', p.leaves, r => `${text(r.name || r.email)} <span class="muted">${text(r.email)}: ${text(r.reason)}</span>`)}
        ${list('Protected', p.protected, r => `${text(r.email)} <span class="muted">${text(r.reason)}</span>`)}
        ${list('Skipped', p.skipped, r => `${text(r.email)} <span class="muted">${text(r.reason)}</span>`)}
        ${need.first ? '<p class="muted">The first sync needs your confirmation.</p>' : ''}
        ${need.mass_leave ? `<p class="err">This would mark more than ${text(preview.mass_leave_limit)} humans as left. Check the list before you confirm.</p>` : ''}
        ${needs ? '<label><input type="checkbox" data-ds-confirm> I have reviewed this list and want to apply it</label>' : ''}
        <div class="row"><button class="primary" type="button" data-ds-apply ${any ? '' : 'disabled'}>${any ? 'Apply' : 'Nothing to apply'}</button></div></div>`;
      const apply = slot.querySelector('[data-ds-apply]'), box = slot.querySelector('[data-ds-confirm]');
      if (box) { apply.disabled = true; box.onchange = () => { apply.disabled = !box.checked; }; }
      apply.onclick = async () => {
        apply.disabled = true;
        try {
          const out = await post('/v2/directory/sync', {confirm: !!box?.checked, plan_hash: p.hash});
          if (!out.applied) { status.textContent = 'The directory changed since the preview; review it again.'; apply.disabled = false; return; }
          toast?.('Directory synced');
          onChange ? onChange() : window.mountDirectorySync(host);
        } catch (error) { status.textContent = error.message; apply.disabled = false; }
      };
      status.textContent = '';
    } catch (error) { status.textContent = error.message; }
    button.disabled = false;
  };
};
