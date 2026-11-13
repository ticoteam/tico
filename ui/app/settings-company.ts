/* ui/app/settings-company.ts — Settings > General > Tico company
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET and POST /v2/company/server-connection answer (backend/company_server.py).
interface SettingsCompanyInfo { name: string; }
interface SettingsCompanyConnection {
  company?: SettingsCompanyInfo | null;
  credit?: { balance_cents?: number | null } | null;
  portal_url?: string;
}

// Links this self-run server to its company on app.tico.dev (backend/company_server.py). Owners and admins, signed in
// directly. A server code from the portal is previewed first (spends nothing), then Connect; the server key HQ answers
// with stays on the server and never reaches the page.
const companyCredit = (credit: SettingsCompanyConnection['credit']): string => !credit || credit.balance_cents == null ? 'No credit yet'
  : (credit.balance_cents / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD'});
async function renderSettingsCompany(): Promise<void> {
  const host = document.querySelector<HTMLElement>('#set-company');
  if (!host || formBusy(host)) return;
  let v: SettingsCompanyConnection;
  try { v = await get<SettingsCompanyConnection>('/v2/company/server-connection', {fresh: true}); }
  catch (e) { host.innerHTML = `<p class="settings-cell-note err" role="status">${esc(errorMessage(e, ''))}</p>`; return; }
  // Settings can draw this twice at once: a code typed while the later read was out must not be wiped by it.
  if (formBusy(host)) return;
  const company = v.company;
  if (company) {
    host.innerHTML = `<dl class="settings-company">
        <dt>Company</dt><dd data-company-name>${esc(company.name)}</dd>
        <dt>Credit</dt><dd data-company-credit>${esc(companyCredit(v.credit))}</dd></dl>
      <div class="row"><a class="ghost" href="${esc(v.portal_url)}" target="_blank" rel="noopener noreferrer" data-company-open>Open on app.tico.dev</a>
        <button class="ghost" type="button" data-company-disconnect>Disconnect</button></div>
      <p class="settings-cell-note" role="status" data-company-status></p>`;
    // The status line and the button are drawn just above.
    const status = host.querySelector<HTMLElement>('[data-company-status]')!;
    const button = host.querySelector<HTMLButtonElement>('[data-company-disconnect]')!;
    button.onclick = async () => {
      if (!(await askConfirm(`Disconnect from ${company.name}?`, button))) return;
      button.disabled = true; status.textContent = 'Disconnecting…';
      try { await writeRequest('DELETE', '/v2/company/server-connection', undefined); await refreshConfig(); await renderSettingsCompany(); }
      catch (e) { status.textContent = errorMessage(e, ''); button.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form class="settings-company-form" data-company-form>
      <label>Server code <input name="code" type="password" autocomplete="off" spellcheck="false" minlength="43" maxlength="43" required></label>
      <button type="submit" class="ghost">Preview</button>
      <div data-company-preview hidden><p><strong data-company-preview-name></strong></p>
        <button type="button" class="primary" data-company-connect>Connect</button></div>
      <p class="settings-cell-note" role="status" data-company-status></p></form>`;
  // The form and every element looked up in it are drawn just above, so each lookup is present.
  const form = host.querySelector<HTMLFormElement>('[data-company-form]')!;
  const input = form.querySelector<HTMLInputElement>('input[name="code"]')!;
  const preview = form.querySelector<HTMLElement>('[data-company-preview]')!;
  const previewName = form.querySelector<HTMLElement>('[data-company-preview-name]')!;
  const connect = form.querySelector<HTMLButtonElement>('[data-company-connect]')!;
  const status = form.querySelector<HTMLElement>('[data-company-status]')!;
  const submit = form.querySelector<HTMLButtonElement>('[type="submit"]')!;
  let shown: SettingsCompanyInfo | null = null, busy = false;
  const clear = () => { shown = null; preview.hidden = true; };
  input.oninput = () => { clear(); status.textContent = ''; };
  const send = async (isPreview: boolean): Promise<void> => {
    if (busy || (!isPreview && !shown)) return;
    const code = input.value;
    busy = submit.disabled = connect.disabled = true;
    status.textContent = isPreview ? 'Checking…' : 'Connecting…';
    try {
      const result: SettingsCompanyConnection = await post('/v2/company/server-connection', {code, preview: isPreview});
      if (input.value !== code) return;
      if (isPreview) {
        // The server answers a preview with its company. A missing one throws on the next line, as it always has.
        shown = result.company!;
        previewName.textContent = shown.name;
        preview.hidden = false; status.textContent = '';
      } else { input.value = ''; await refreshConfig(); await renderSettingsCompany(); }
    } catch (e) { status.textContent = errorMessage(e, ''); if (!isPreview) clear(); }
    finally { busy = submit.disabled = connect.disabled = false; }
  };
  form.onsubmit = event => { event.preventDefault(); void send(true); };
  connect.onclick = () => void send(false);
}
