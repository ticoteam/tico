/* ui/app/settings-company.js — Settings > General > Tico company
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Links this self-run server to its company on app.tico.dev (backend/company_server.py). Owners and admins, signed in
// directly. A server code from the portal is previewed first (spends nothing), then Connect; the server key HQ answers
// with stays on the server and never reaches the page.
const companyCredit = credit => !credit || credit.balance_cents == null ? 'No credit yet'
  : (credit.balance_cents / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD'});
async function renderSettingsCompany() {
  const host = $('#set-company');
  if (!host || formBusy(host)) return;
  let v;
  try { v = await get('/v2/company/server-connection', {fresh: true}); }
  catch (e) { host.innerHTML = `<p class="settings-cell-note err" role="status">${esc(e.message)}</p>`; return; }
  if (v.company) {
    host.innerHTML = `<dl class="settings-company">
        <dt>Company</dt><dd data-company-name>${esc(v.company.name)}</dd>
        <dt>Credit</dt><dd data-company-credit>${esc(companyCredit(v.credit))}</dd></dl>
      <div class="row"><a class="ghost" href="${esc(v.portal_url)}" target="_blank" rel="noopener noreferrer" data-company-open>Open on app.tico.dev</a>
        <button class="ghost" type="button" data-company-disconnect>Disconnect</button></div>
      <p class="settings-cell-note" role="status" data-company-status></p>`;
    const status = host.querySelector('[data-company-status]'), button = host.querySelector('[data-company-disconnect]');
    button.onclick = async () => {
      if (!window.confirm(`Disconnect from ${v.company.name}?`)) return;
      button.disabled = true; status.textContent = 'Disconnecting…';
      try { await writeRequest('DELETE', '/v2/company/server-connection'); await renderSettingsCompany(); }
      catch (e) { status.textContent = e.message; button.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form class="settings-company-form" data-company-form>
      <label>Server code <input name="code" type="password" autocomplete="off" spellcheck="false" minlength="43" maxlength="43" required></label>
      <button type="submit" class="ghost">Preview</button>
      <div data-company-preview hidden><p><strong data-company-preview-name></strong></p>
        <button type="button" class="primary" data-company-connect>Connect</button></div>
      <p class="settings-cell-note" role="status" data-company-status></p></form>`;
  const form = host.querySelector('[data-company-form]'), input = form.elements.code;
  const preview = form.querySelector('[data-company-preview]'), connect = form.querySelector('[data-company-connect]');
  const status = form.querySelector('[data-company-status]'), submit = form.querySelector('[type="submit"]');
  let shown = null, busy = false;
  const clear = () => { shown = null; preview.hidden = true; };
  input.oninput = () => { clear(); status.textContent = ''; };
  const send = async isPreview => {
    if (busy || (!isPreview && !shown)) return;
    const code = input.value;
    busy = submit.disabled = connect.disabled = true;
    status.textContent = isPreview ? 'Checking…' : 'Connecting…';
    try {
      const result = await post('/v2/company/server-connection', {code, preview: isPreview});
      if (input.value !== code) return;
      if (isPreview) {
        shown = result.company;
        form.querySelector('[data-company-preview-name]').textContent = shown.name;
        preview.hidden = false; status.textContent = '';
      } else { input.value = ''; await renderSettingsCompany(); }
    } catch (e) { status.textContent = e.message; if (!isPreview) clear(); }
    finally { busy = submit.disabled = connect.disabled = false; }
  };
  form.onsubmit = event => { event.preventDefault(); void send(true); };
  connect.onclick = () => void send(false);
}
