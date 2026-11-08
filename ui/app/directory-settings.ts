/* ui/app/directory-settings.ts — Settings › Directory (#/settings/directory): the Directory's types and their fields,
   whether each inbox adds every mail sender, and the shared places. ui/app/settings.ts owns the tab and calls
   directorySettingsShow when it opens.
   Reads: GET /api/v2/entities/types, /types/<key>, /counts and /api/v2/mailboxes. The one write is the inbox switch,
   PATCH /api/v2/mailboxes/<id>/directory ({on}); its person decides, or an admin for a shared inbox. The switch is
   drawn only for rows whose listing says `feeds_directory`, so a hub without that route shows no control at all. */
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
