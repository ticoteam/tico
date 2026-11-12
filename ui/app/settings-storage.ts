/* ui/app/settings-storage.ts — Settings > General
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/system/storage and its move answer (backend/company_storage.py, tico_storage.py). A bucket is the
// company's own S3 bucket; `active` is the one new files go to, `earlier` the ones still read from.
interface SettingsStorageBucket {
  location: string;
  region: string;
  bucket: string;
  prefix?: string | null;
  access_key_id: string;
  retired_at?: string;
}
// Tico file hosting as the view carries it: its state, the address its files open from, and what went wrong.
interface SettingsStorageTico {
  state: string;
  label?: string;
  address?: string;
  error?: string;
  keep_until?: string | null;
}
interface SettingsStorageMove {
  state: string;
  moved_files: number;
  estimate_files: number;
  moved_bytes: number;
  estimate_bytes: number;
  matches?: boolean;
  error?: string | null;
}
interface SettingsStorageMoveView {
  estimate: { files?: number; bytes: number };
  move?: SettingsStorageMove | null;
}
interface SettingsStorageView {
  active: SettingsStorageBucket | null;
  server: { mode: string; location?: string };
  tico?: SettingsStorageTico | null;
  copy: { running?: boolean; failed?: number; done: number; total: number };
  earlier: SettingsStorageBucket[];
  doc: string;
}

// Where the company's files are stored (backend/company_storage.py): the owner's own S3 bucket, or the server's
// storage. Saving is checked on the server by writing and reading back a small object; the secret access key is
// sent once, sealed there and never shown again. A bucket replaced or turned off is still read from.
const storageWhere = (place: SettingsStorageBucket): string => `<code>s3://${esc(place.location)}</code> <span class="muted">· ${esc(place.region)}</span>`;
// Tico file hosting (backend/tico_storage.py): bought and stopped on the Gateway page; Tico HQ hands over the bucket,
// its short-lived keys and the company's ticofiles.com address, so there is nothing to type here.
const TICO_HOSTING: Record<string, string> = {
  on: 'New files go to Tico file hosting.',
  credits_out: "Gateway credits have run out: files in Tico file hosting still open, and new files go to the company's own storage below until credits are added.",
  stopped: "Tico file hosting is stopped: new files go to the company's own storage below. Tico keeps the files it holds {until} and emails the owner before deleting them, so download them or move them back first.",
};
const keptUntil = (t: SettingsStorageTico): string => t.keep_until ? `until ${new Date(t.keep_until).toLocaleDateString(undefined, {dateStyle: 'long'})}` : 'for 30 days';
const gb = (n: number): string => `${(n / 1e9).toFixed(n < 1e7 ? 3 : 2)} GB`;
function storageTicoHtml(t: SettingsStorageTico | null | undefined): string {
  if (!t || t.state === 'off') return '';
  return `<section class="storage-tico" data-storage-tico>
      <h4>${esc(t.label || 'Tico file hosting')}</h4>
      <p>${esc((TICO_HOSTING[t.state] || '').replace('{until}', keptUntil(t)))}${t.address ? ` Pictures, video and audio stored there open from <code>${esc(t.address)}</code>.` : ''}</p>
      ${t.error ? `<p class="settings-cell-note" role="status">${esc(t.error)}</p>` : ''}
      <p class="settings-cell-note" data-move-line role="status"></p>
      <div class="row"><button class="primary" type="button" data-move hidden></button>
        <a href="${API}/v2/system/storage/export" download data-export>Download all files</a>
        <button class="ghost" type="button" data-tico-check>Check now</button></div>
    </section>`;
}
async function renderStorageMove(host: HTMLElement, t: SettingsStorageTico): Promise<void> {
  const line = host.querySelector<HTMLElement>('[data-move-line]');
  // The move button is drawn with the line, in the same section.
  const button = host.querySelector<HTMLButtonElement>('[data-move]')!;
  if (!line) return;
  let m: SettingsStorageMoveView;
  try { m = await get<SettingsStorageMoveView>('/v2/system/storage/move'); } catch (e) { line.textContent = errorMessage(e, ''); return; }
  const {estimate: e, move} = m;
  const running = move && move.state === 'running';
  if (running) line.textContent = `Moving files: ${move.moved_files} of ${move.estimate_files} (${gb(move.moved_bytes)} of ${gb(move.estimate_bytes)}).`;
  else if (move && move.state === 'done') line.textContent = `Moved ${move.moved_files} files, ${gb(move.moved_bytes)}${move.matches ? ', as estimated' : ''}. Every copy was checked; nothing was deleted where it was.`;
  else if (move) line.textContent = `The last move stopped after ${move.moved_files} of ${move.estimate_files} files: ${move.error || 'start it again to finish'}.`;
  else line.textContent = '';
  if (!running && e.files) line.textContent += ` ${e.files} files (${gb(e.bytes)}) are not in ${t.state === 'on' ? 'Tico file hosting' : "the company's own storage"} yet.`;
  button.hidden = running || !e.files;
  button.textContent = t.state === 'on' ? 'Move existing files' : 'Move files back';
  button.onclick = async () => {
    const cost = t.state === 'on' ? ' Tico file hosting bills what it stores in Gateway credits.' : '';
    if (!(await askConfirm(`Copy ${e.files} files (${gb(e.bytes)}), every version, to ${t.state === 'on' ? 'Tico file hosting' : "the company's own storage"}?${cost} Each copy is checked before it is used, and nothing is deleted where it is now.`))) return;
    try { await post('/v2/system/storage/move', {expect_bytes: e.bytes}); toast('Moving files'); } catch (err) { toast(errorMessage(err, ''), true); }
    void renderStorageMove(host, t);
  };
  if (running) setTimeout(() => { if (host.isConnected) void renderStorageMove(host, t); }, 3000);
}
async function renderSettingsStorage(): Promise<void> {
  const host = document.querySelector<HTMLElement>('#set-storage');
  if (!host || formBusy(host)) return;
  let v: SettingsStorageView;
  try { v = await get<SettingsStorageView>('/v2/system/storage'); } catch (e) { host.innerHTML = `<div class="empty">${esc(errorMessage(e, ''))}</div>`; return; }
  const a = v.active, server = v.server.mode === 's3' ? `the server's bucket <code>s3://${esc(v.server.location)}</code>` : "this server's disk";
  const t: SettingsStorageTico = v.tico || {state: 'off'};
  const copy = v.copy.running || v.copy.failed ? `<p class="settings-cell-note" role="status">Copying files from this server's disk: ${v.copy.done} of ${v.copy.total}${v.copy.failed ? `, ${v.copy.failed} failed (Settings › Health says why)` : ''}</p>` : '';
  const own = a ? `${storageWhere(a)}, read and written with key <code>${esc(a.access_key_id)}</code>` : server;
  host.innerHTML = `<p>${t.state === 'on' ? `New files go to Tico file hosting; the company's own storage is ${own}.` : `New files go to ${own}.`}
      Files already stored keep opening from where they are. <a href="${esc(v.doc)}" target="_blank" rel="noopener noreferrer">How it works</a></p>
    ${storageTicoHtml(t)}
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
      `<li>${storageWhere(p)} <span class="muted">· replaced ${esc(ago(p.retired_at))}</span></li>`).join('')}</ul></details>` : ''}`;
  // The form and its status line are drawn just above.
  const form = host.querySelector<HTMLFormElement>('[data-storage-form]')!;
  const status = host.querySelector<HTMLElement>('[data-storage-status]')!;
  // The form's inputs by name: each one is drawn once, above.
  const field = (name: string): HTMLInputElement => form.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
  form.onsubmit = async ev => {
    ev.preventDefault();
    status.textContent = 'Checking the bucket…';
    try {
      await put('/v2/system/storage', {bucket: field('bucket').value.trim(), region: field('region').value.trim(), prefix: field('prefix').value.trim(),
        access_key_id: field('access_key_id').value.trim(), secret_access_key: field('secret_access_key').value});
      form.reset();
      toast('New files now go to the bucket');
      void renderSettingsStorage();
    } catch (e) { status.textContent = errorMessage(e, ''); }
  };
  if (t.state !== 'off') void renderStorageMove(host, t);
  const check = host.querySelector<HTMLButtonElement>('[data-tico-check]');
  if (check) check.onclick = async () => {
    try { await post('/v2/system/storage/tico/check', {}); toast('Checked with Tico'); } catch (e) { toast(errorMessage(e, ''), true); }
    void renderSettingsStorage();
  };
  const off = host.querySelector<HTMLButtonElement>('[data-storage-off]');
  if (off) off.onclick = async () => {
    if (!(await askConfirm("Store new files on the server's storage again? Files already in the bucket keep opening from it."))) return;
    try { await writeRequest('DELETE', '/v2/system/storage', undefined); toast("New files now go to the server's storage"); } catch (e) { toast(errorMessage(e, ''), true); }
    void renderSettingsStorage();
  };
}
