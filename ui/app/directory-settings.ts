/* ui/app/directory-settings.ts — Settings › Directory (#/settings/directory): the Directory's types and their fields,
   whether each inbox adds every mail sender, and the shared places. ui/app/settings.ts owns the tab and calls
   directorySettingsShow when it opens.
   Reads: GET /api/v2/entities/types, /types/<key>, /counts and /api/v2/mailboxes. The one write is the inbox switch,
   PATCH /api/v2/mailboxes/<id>/directory ({on}); its person decides, or an admin for a shared inbox. The switch is
   drawn only for rows whose listing says `feeds_directory`, so a hub without that route shows no control at all.
   Where we operate reads GET /api/v2/entities/self; an owner or an admin adds a place (found with
   /api/v2/entities/find/place) through POST /api/v2/entities/links and removes one through POST /api/v2/entities/unlink.
   Everyone else sees the list without the controls, and the hub refuses their writes too. */
'use strict';

interface DirMailbox { id: string; address: string; kind: string; person_name?: string | null; feeds_directory?: boolean }
interface DirMailboxes { admin: boolean; mine: DirMailbox | null; shared: DirMailbox[]; mailboxes?: DirMailbox[] }

let DIRS_RUN = 0;

async function directorySettingsShow(box: HTMLElement): Promise<void> {
  const run = ++DIRS_RUN;
  box.innerHTML = skelRows(4, { card: true, dot: false });
  let types: DirType[], counts: DirCount[];
  try {
    const [t, n] = await Promise.all([
      get<{ types: DirType[] }>('/v2/entities/types', { fresh: true }),
      get<{ counts: DirCount[] }>('/v2/entities/counts', { fresh: true }),
    ]);
    types = t.types.filter(x => x.mode !== 'archived'); counts = n.counts;
  } catch (e) {
    if (run === DIRS_RUN) box.innerHTML = dirErr(e);
    return;
  }
  const mail = await get<DirMailboxes>('/v2/mailboxes', { fresh: true }).catch(() => null);
  if (run !== DIRS_RUN) return;
  const counted = new Map(counts.map(c => [c.type, c]));
  types.sort((a, b) => dirTabOrder(a.key) - dirTabOrder(b.key));
  const switches = dirsMailRows(mail);
  box.innerHTML = `<section class="card" data-dirs-types><header><h2>Types</h2></header>
      <table class="tt-list dir-kv-table"><thead><tr><th>Type</th><th>Count</th><th>Added by</th></tr></thead><tbody>
      ${types.map(t => { const n = counted.get(t.key); return `<tr data-dirs-type="${esc(t.key)}">
        <td><a href="${dirListHref(t.key)}">${esc(t.name)}</a></td>
        <td class="tnum" data-label="Count">${n ? esc(dirN(n.count, n.approximate)) : '0'}</td>
        <td class="muted" data-label="Added by">${t.built_in ? 'Built in' : esc(dirWho(t.created_by_type && t.created_by_id ? `${t.created_by_type}:${t.created_by_id}` : ''))}</td></tr>`; }).join('')}
      </tbody></table></section>
    <section class="card" data-dirs-fields><header><h2>Fields</h2><span class="spacer"></span>
      <select data-dirs-field-type aria-label="Type">${types.map(t => `<option value="${esc(t.key)}"${t.key === 'organization' ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select></header>
      <div data-dirs-field-body>${skelRows(3, { dot: false })}</div></section>
    <section class="card" data-dirs-operate hidden><header><h2>Where we operate</h2></header><div data-dirs-operate-body></div></section>
    ${switches ? `<section class="card" data-dirs-mail><header><h2>Every mail sender</h2></header>${switches}</section>` : ''}
    <section class="card" data-dirs-places><header><h2>Shared places</h2></header>
      <div class="dir-set-row"><span>Use shared places</span><span class="pill ok" data-dirs-places-on>On</span></div>
      <p class="muted">People, notes and mail stay here.</p></section>`;
  const select = box.querySelector<HTMLSelectElement>('[data-dirs-field-type]');
  const fields = box.querySelector<HTMLElement>('[data-dirs-field-body]');
  if (select && fields) {
    select.addEventListener('change', () => { void dirsFields(fields, select.value, run); });
    void dirsFields(fields, select.value, run);
  }
  const operate = box.querySelector<HTMLElement>('[data-dirs-operate]');
  if (operate) void dirsOperate(operate, run);
  box.querySelectorAll<HTMLInputElement>('[data-dirs-mailbox]').forEach(input => {
    input.addEventListener('change', () => { void dirsMailSwitch(input); });
  });
}
async function dirsFields(box: HTMLElement, key: string, run: number): Promise<void> {
  box.innerHTML = skelRows(3, { dot: false });
  try {
    const d = await get<DirDescribe>(`/v2/entities/types/${encodeURIComponent(key)}`, { fresh: true });
    if (run !== DIRS_RUN) return;
    box.innerHTML = d.attributes.length
      ? `<table class="tt-list dir-kv-table"><thead><tr><th>Field</th><th>Kind</th><th>Owner</th></tr></thead><tbody>
        ${d.attributes.map(a => `<tr data-dirs-field="${esc(a.key)}"><td><strong>${esc(a.name)}</strong></td>
          <td class="muted" data-label="Kind">${esc([a.value_type, a.multiple ? 'many' : '', a.sensitive ? 'sensitive' : ''].filter(Boolean).join(' · '))}</td>
          <td class="muted" data-label="Owner">${esc(a.owner.startsWith('connection:') ? dirCap(a.owner.slice(11)) : 'Tico')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted dir-none">None yet.</p>';
  } catch (e) {
    if (run === DIRS_RUN) box.innerHTML = dirErr(e);
  }
}
// The inboxes this person may switch: their own, and for an admin every shared one (a personal inbox is its person's
// alone). Only rows that say whether they feed the Directory get a switch.
function dirsMailRows(mail: DirMailboxes | null): string {
  if (!mail) return '';
  const rows = new Map<string, DirMailbox>();
  const shared = mail.admin ? (mail.mailboxes ?? []).filter(m => m.kind === 'shared') : [];
  for (const m of [mail.mine, ...shared]) {
    if (m && typeof m.feeds_directory === 'boolean') rows.set(m.id, m);
  }
  if (!rows.size) return '';
  return [...rows.values()].map(m => `<label class="dir-set-row" data-dirs-mail-row="${esc(m.id)}"><span>${esc(m.address)}${m.person_name && m.kind !== 'shared' ? ` <span class="muted">${esc(m.person_name)}</span>` : ''}</span>
    <input type="checkbox" role="switch" class="people-switch" data-dirs-mailbox="${esc(m.id)}" aria-label="Every mail sender: ${esc(m.address)}"${m.feeds_directory ? ' checked' : ''}></label>`).join('');
}
async function dirsMailSwitch(input: HTMLInputElement): Promise<void> {
  const id = input.dataset['dirsMailbox'];
  if (!id) return;
  const on = input.checked;
  input.disabled = true;
  try {
    const r: { feeds_directory?: boolean } = await patch('/v2/mailboxes/' + encodeURIComponent(id) + '/directory', { on });
    input.checked = r.feeds_directory ?? on;
  } catch (e) {
    input.checked = !on;
    toast(e instanceof Error ? e.message : String(e), true);
  } finally {
    input.disabled = false;
  }
}
// Where we operate: the places our company operates_in, which switch on the laws that apply there. Only an owner or an
// admin gets the search and the remove buttons (the hub refuses anyone else).
async function dirsOperate(card: HTMLElement, run: number): Promise<void> {
  const us = await get<DirSelf>('/v2/entities/self', { fresh: true }).catch(() => null);
  if (run !== DIRS_RUN) return;
  if (!us) { card.hidden = true; return; }
  card.hidden = false;
  const body = card.querySelector<HTMLElement>('[data-dirs-operate-body]');
  if (!body) return;
  const edit = settingsIsAdmin();
  const rows = us.operates_in.map(p => `<li class="dir-set-row" data-dirs-place="${esc(p.id)}">
      <a href="${dirHref(p.id)}">${esc(p.name)}</a>
      ${edit && p.link_id ? `<button type="button" class="ghost small dir-icon" data-dirs-unplace="${esc(p.link_id)}" aria-label="Remove ${esc(p.name)}"><span class="nav-icon" aria-hidden="true">close</span></button>` : ''}</li>`).join('');
  body.innerHTML = `${rows ? `<ul class="dir-plain dir-operate">${rows}</ul>` : '<p class="muted dir-none" data-dirs-operate-empty>None yet.</p>'}
    ${edit ? `<div class="dir-operate-add"><input type="search" class="dir-q" data-dirs-place-q placeholder="Add a place" aria-label="Add a place" autocomplete="off">
      <ul class="dir-plain dir-operate-found" data-dirs-place-found hidden></ul></div>` : ''}`;
  body.querySelectorAll<HTMLButtonElement>('[data-dirs-unplace]').forEach(b => {
    b.addEventListener('click', () => { void dirsOperateChange(card, run, b, '/v2/entities/unlink', { link_id: b.dataset['dirsUnplace'] }); });
  });
  const input = body.querySelector<HTMLInputElement>('[data-dirs-place-q]');
  const found = body.querySelector<HTMLElement>('[data-dirs-place-found]');
  if (!input || !found) return;
  const have = new Set(us.operates_in.map(p => p.id));
  let timer = 0, asked = 0;
  input.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const q = input.value.trim();
      const mine = ++asked;
      if (!q) { found.hidden = true; found.innerHTML = ''; return; }
      void get<DirFind>(`/v2/entities/find/place?${new URLSearchParams({ name: q, limit: '8' }).toString()}`, { fresh: true })
        .then(page => {
          if (mine !== asked || run !== DIRS_RUN) return;
          const rows = page.rows.filter(r => !have.has(r.id));
          found.hidden = false;
          found.innerHTML = rows.length
            ? rows.map(r => `<li><button type="button" class="ghost small" data-dirs-pick="${esc(r.id)}">${esc(r.name)}</button></li>`).join('')
            : '<li class="muted">No matches.</li>';
          found.querySelectorAll<HTMLButtonElement>('[data-dirs-pick]').forEach(b => {
            b.addEventListener('click', () => {
              void dirsOperateChange(card, run, b, '/v2/entities/links', { from_ref: us.id, link_key: 'operates_in', to_ref: b.dataset['dirsPick'] });
            });
          });
        })
        .catch((e: unknown) => { if (mine === asked) toast(e instanceof Error ? e.message : String(e), true); });
    }, 220);
  });
}
async function dirsOperateChange(card: HTMLElement, run: number, button: HTMLButtonElement, path: string, body: object): Promise<void> {
  button.disabled = true;
  try {
    await post(path, body);
  } catch (e) {
    button.disabled = false;
    toast(e instanceof Error ? e.message : String(e), true);
    return;
  }
  await dirsOperate(card, run);
}
