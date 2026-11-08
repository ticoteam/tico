/* ui/app/settings-listening.ts — Listening's destinations, drawn in Strategy › Market › Listening (ui/app/redesign-market.ts)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One destination, as backend/listening.py saves it and GET /v2/listening/destinations lists it.
interface ListenDestination {
  key: string; category: string; receiver: string; threshold: number; readers?: string[] | null;
  unless?: { category: string; threshold: number } | null; what?: string | null; source: string;
}
// What GET /v2/listening/destinations answers: the categories and receivers the form offers, and the destinations.
interface ListenData {
  categories: { id: string; label: string }[];
  receivers: { actor: string; name: string }[];
  destinations: ListenDestination[];
}
interface ListenOption { value: string; label: string; }
// The form's named controls, as listenFormHTML draws them. A downcast of form.elements, read once in the submit handler.
interface ListenFormControls extends HTMLFormControlsCollection {
  key: HTMLInputElement; category: HTMLSelectElement; receiver: HTMLSelectElement; threshold: HTMLInputElement;
  readers: HTMLSelectElement; unless_category: HTMLSelectElement; unless_threshold: HTMLInputElement; what: HTMLInputElement;
}
// The body PUT /v2/listening/destinations/:key takes.
interface ListenBody { category: string; receiver: string; threshold: number; readers: string[]; what: string; unless?: { category: string; threshold: number }; }

// Where Listening sends the posts it saves (backend/listening.py). A destination is a category the decision model scores,
// a threshold and the bot that receives what clears it. The owner adds, edits and removes the ones saved here; the owner and
// admins read all of them, and the ones from the server's listening.yaml are shown read-only.
let LISTEN_EDIT: string | null = null;        // null, 'new', or the key being edited
const listenOwner = (): boolean => S.me?.role === 'owner';
const listenNum = (n: unknown): string => String(Math.round(Number(n) * 100) / 100);
function listenOptions(items: ListenOption[], value: string | null | undefined, blank?: string): string {
  return (blank ? `<option value="">${esc(blank)}</option>` : '') + items.map(i =>
    `<option value="${esc(i.value)}"${i.value === value ? ' selected' : ''}>${esc(i.label)}</option>`).join('');
}
function listenFormHTML(data: ListenData, d: Partial<ListenDestination>): string {
  const cats = data.categories.map(c => ({value: c.id, label: c.label}));
  const bots = data.receivers.map(r => ({value: r.actor, label: r.name}));
  const readers = new Set(d.readers || []);
  return `<form class="listen-form" data-listen-form novalidate>
    <label>Name<input name="key" type="text" maxlength="40" required autocomplete="off" value="${esc(d.key || '')}"${d.key ? ' readonly' : ''}></label>
    <label>Category<select name="category" required>${listenOptions(cats, d.category)}</select></label>
    <label>Send to<select name="receiver" required>${listenOptions(bots, d.receiver)}</select></label>
    <label>Threshold<input name="threshold" type="number" min="0.01" max="1" step="0.01" required value="${esc(listenNum(d.threshold ?? 0.75))}"></label>
    <label>Can read<select name="readers" multiple size="3">${bots.map(b => `<option value="${esc(b.value)}"${readers.has(b.value) ? ' selected' : ''}>${esc(b.label)}</option>`).join('')}</select></label>
    <label>Skip if<select name="unless_category">${listenOptions(cats, d.unless?.category, 'Never')}</select></label>
    <label>Skip at or above<input name="unless_threshold" type="number" min="0.01" max="1" step="0.01" value="${esc(listenNum(d.unless?.threshold ?? 0.7))}"></label>
    <label class="listen-wide">What<input name="what" type="text" maxlength="300" autocomplete="off" value="${esc(d.what || '')}"></label>
    <div class="row listen-wide"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-listen-cancel>Cancel</button>
      <span class="muted" data-listen-status role="status"></span></div>
  </form>`;
}
function listenRowHTML(d: ListenDestination, names: Record<string, string>, canEdit: boolean): string {
  const rule = `${esc(d.category.replace(/_/g, ' '))} at ${esc(listenNum(d.threshold))} or above → ${esc(names[d.receiver] || d.receiver)}`
    + (d.unless ? `, not when ${esc(d.unless.category.replace(/_/g, ' '))} is ${esc(listenNum(d.unless.threshold))} or above` : '');
  const who = d.readers || [];
  const readers = who.length ? `<span class="settings-cell-note">Can read: ${who.map(r => esc(names[r] || r)).join(', ')}</span>` : '';
  return `<li data-listen-dest="${esc(d.key)}">
    <div class="listen-main"><strong>${esc(d.key)}</strong><span class="settings-cell-note">${rule}</span>
      ${d.what ? `<span class="settings-cell-note">${esc(d.what)}</span>` : ''}${readers}</div>
    <div class="listen-source"><span class="pill" data-listen-source>${d.source === 'yaml' ? 'From listening.yaml' : 'Settings'}</span></div>
    <div class="listen-actions">${canEdit && d.source === 'settings' ? `<button class="ghost" type="button" data-listen-edit="${esc(d.key)}">Edit</button>
      <button class="ghost" type="button" data-listen-remove="${esc(d.key)}">Remove</button>` : ''}</div></li>`;
}
async function renderSettingsListening(): Promise<void> {
  const el = document.querySelector<HTMLElement>('#set-listening'); if (!el) return;
  let data: ListenData;
  try { data = await get('/v2/listening/destinations'); } catch (e) { el.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  const canEdit = listenOwner();
  const names: Record<string, string> = Object.fromEntries(data.receivers.map((r): [string, string] => [r.actor, r.name]));
  const editing = LISTEN_EDIT && LISTEN_EDIT !== 'new' ? data.destinations.find(d => d.key === LISTEN_EDIT) : null;
  if (LISTEN_EDIT && LISTEN_EDIT !== 'new' && !editing) LISTEN_EDIT = null;
  el.innerHTML = `${data.destinations.length ? `<ul class="listen-list">${data.destinations.map(d => listenRowHTML(d, names, canEdit)).join('')}</ul>`
      : '<div class="empty">No destinations yet. Posts are saved and scored but go nowhere.</div>'}
    ${canEdit ? (LISTEN_EDIT ? listenFormHTML(data, editing || {}) : '<div class="row"><button class="primary" type="button" data-listen-add>Add destination</button></div>') : settingsWhoCanHTML('where posts go')}`;
  el.querySelector('[data-listen-add]')?.addEventListener('click', () => { LISTEN_EDIT = 'new'; void renderSettingsListening(); });
  el.querySelector('[data-listen-cancel]')?.addEventListener('click', () => { LISTEN_EDIT = null; void renderSettingsListening(); });
  el.querySelectorAll<HTMLElement>('[data-listen-edit]').forEach(b => { b.addEventListener('click', () => { LISTEN_EDIT = b.dataset.listenEdit ?? null; void renderSettingsListening(); }); });
  el.querySelectorAll<HTMLElement>('[data-listen-remove]').forEach(b => { b.addEventListener('click', async () => {
    const key = b.dataset.listenRemove ?? '';
    if (!await settingsConfirm({title: `Remove ${key}?`, ok: 'Remove', text: 'Posts already in its inbox stay.'})) return;
    try { await post(`/v2/listening/destinations/${encodeURIComponent(key)}/remove`, {}); LISTEN_EDIT = null; toast('Removed'); void renderSettingsListening(); }
    catch (e) { settingsFieldError(b.closest('li') || b, errorMessage(e, '')); }
  }); });
  const form = el.querySelector<HTMLFormElement>('[data-listen-form]');
  if (form) form.onsubmit = async ev => {
    ev.preventDefault();
    // listenFormHTML draws the form's controls and its status line, so both lookups find them.
    const f = form.elements as ListenFormControls, status = form.querySelector<HTMLElement>('[data-listen-status]')!;
    const body: ListenBody = {category: f.category.value, receiver: f.receiver.value, threshold: Number(f.threshold.value),
      readers: [...f.readers.selectedOptions].map(o => o.value), what: f.what.value.trim()};
    if (f.unless_category.value) body.unless = {category: f.unless_category.value, threshold: Number(f.unless_threshold.value)};
    const key = f.key.value.trim().toLowerCase();
    status.classList.remove('err');
    if (!key) { status.classList.add('err'); status.textContent = 'Name it.'; return; }
    status.textContent = 'Saving…';
    try { await put(`/v2/listening/destinations/${encodeURIComponent(key)}`, body); LISTEN_EDIT = null; toast('Saved'); void renderSettingsListening(); }
    catch (e) { status.classList.add('err'); status.textContent = errorMessage(e, ''); }
  };
}
