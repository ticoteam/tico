/* ui/app/settings-storage.js — Settings > General
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Where the company's files are stored (backend/company_storage.py): the owner's own S3 bucket, or the server's
// storage. Saving is checked on the server by writing and reading back a small object; the secret access key is
// sent once, sealed there and never shown again. A bucket replaced or turned off is still read from.
const storageWhere = place => `<code>s3://${esc(place.location)}</code> <span class="muted">· ${esc(place.region)}</span>`;
async function renderSettingsStorage() {
  const host = $('#set-storage');
  if (!host || formBusy(host)) return;
  let v;
  try { v = await get('/v2/system/storage'); } catch (e) { host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const a = v.active, server = v.server.mode === 's3' ? `the server's bucket <code>s3://${esc(v.server.location)}</code>` : "this server's disk";
  const copy = v.copy.running || v.copy.failed ? `<p class="settings-cell-note" role="status">Copying files from this server's disk: ${v.copy.done} of ${v.copy.total}${v.copy.failed ? `, ${v.copy.failed} failed (Settings › Health says why)` : ''}</p>` : '';
  host.innerHTML = `<p>${a ? `New files go to ${storageWhere(a)}, read and written with key <code>${esc(a.access_key_id)}</code>.` : `New files go to ${server}.`}
      Files already stored keep opening from where they are. <a href="${esc(v.doc)}" target="_blank" rel="noopener noreferrer">How it works</a></p>
    ${copy}
    <form class="storage-form" data-storage-form autocomplete="off">
      <label>Bucket<input name="bucket" required maxlength="63" placeholder="acme-files" value="${esc(a?.bucket || '')}" spellcheck="false"></label>
      <label>Region<input name="region" required maxlength="32" placeholder="us-east-1" value="${esc(a?.region || '')}" spellcheck="false"></label>
      <label>Folder in the bucket <span class="muted">(optional)</span><input name="prefix" maxlength="200" placeholder="tico" value="${esc(a?.prefix || '')}" spellcheck="false"></label>
      <label>Access key ID<input name="access_key_id" required maxlength="128" placeholder="AKIA…" spellcheck="false"></label>
      <label>Secret access key<input name="secret_access_key" type="password" maxlength="128" autocomplete="new-password"
        placeholder="${a ? 'Leave empty to keep the saved one for the same key ID' : ''}"></label>
      <p class="settings-cell-note">The keys need <code>s3:PutObject</code>, <code>s3:GetObject</code> and <code>s3:AbortMultipartUpload</code> on the bucket, and <code>s3:DeleteObject</code> to replace the team icon. Tico writes and reads back a small check object before saving.</p>
      <div class="row"><button class="primary" type="submit">${a ? 'Save changes' : 'Use this bucket'}</button>
        ${a ? `<button class="ghost" type="button" data-storage-off>Use the server's storage</button>` : ''}</div>
      <p class="settings-cell-note" data-storage-status role="status"></p>
    </form>
    ${v.earlier.length ? `<details><summary>Still read from (${v.earlier.length})</summary><ul class="storage-earlier">${v.earlier.map(p =>
      `<li>${storageWhere(p)} <span class="muted">· replaced ${esc(ago(p.retired))}</span></li>`).join('')}</ul></details>` : ''}`;
  const form = host.querySelector('[data-storage-form]'), status = host.querySelector('[data-storage-status]');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = form.elements;
    status.textContent = 'Checking the bucket…';
    try {
      await put('/v2/system/storage', {bucket: f.bucket.value.trim(), region: f.region.value.trim(), prefix: f.prefix.value.trim(),
        access_key_id: f.access_key_id.value.trim(), secret_access_key: f.secret_access_key.value});
      form.reset();
      toast('New files now go to the bucket');
      void renderSettingsStorage();
    } catch (e) { status.textContent = e.message; }
  };
  const off = host.querySelector('[data-storage-off]');
  if (off) off.onclick = async () => {
    if (!window.confirm("Store new files on the server's storage again? Files already in the bucket keep opening from it.")) return;
    try { await writeRequest('DELETE', '/v2/system/storage'); toast("New files now go to the server's storage"); } catch (e) { toast(e.message, true); }
    void renderSettingsStorage();
  };
}
