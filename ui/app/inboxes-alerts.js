/* Personal phone alerts in Inboxes > Settings > Texts. Registry: ui/app/inboxes.js. */
'use strict';

const IB_ALERT_EVENTS = [
  ['urgent_needs', 'An overdue task needs me'],
  ['legal_mail', 'Legal mail arrives'],
  ['bot_stuck', 'A bot I manage has a stalled task'],
];
const IB_ALERT_STATUS = {
  none: ['off', 'No phone'], code_sent: ['pending', 'Verify your phone'], verified: ['ok', 'Verified'],
  opted_out: ['attention', 'Opted out'], unverified: ['attention', 'Verification removed'],
  removal_pending: ['attention', 'Removal pending'], unavailable: ['attention', 'Unavailable'],
};

function inboxesAlertsCheck(name, title, checked, disabled = false) {
  return `<label class="ib-alert-check"><input type="checkbox" name="${name}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}><span>${title}</span></label>`;
}

async function inboxesAlerts(host, ctx) {
  const current = await get('/v2/alerts/me', {fresh: true});
  if (!ctx.live() || !host?.isConnected) return;
  const p = current.preferences || {}, [tone, status] = IB_ALERT_STATUS[current.status] || ['attention', 'Unavailable'];
  const verified = current.status === 'verified', hasPhone = !!current.phone;
  const pending = current.status === 'code_sent', removing = current.status === 'removal_pending';
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const zone = p.timezone === 'UTC' && !hasPhone ? browserZone : p.timezone || browserZone;
  const timezones = [...new Set([zone, 'UTC', ...(Intl.supportedValuesOf?.('timeZone') || [])])];
  const quiet = !!(p.quiet_start && p.quiet_end);
  const message = current.status === 'opted_out' ? 'Reply START to the paging number to resume texts and calls.'
    : removing ? 'Local alerts are off. Retry removal to revoke your phone at HQ.'
    : current.status === 'unavailable' ? 'Cannot check consent. Alerts wait until the service is available.'
    : current.status === 'unverified' ? 'Remove this phone, then verify it again.' : '';
  const addPhone = !hasPhone ? `<form data-alert-add>
    <label class="ib-alert-input">Your phone<input name="phone" type="tel" inputmode="tel" autocomplete="tel" required maxlength="40" placeholder="+1 (415) 555-0123"></label>
    ${inboxesAlertsCheck('sms_consent', 'I agree to receive Tico alert texts at my own phone.', false)}
    ${inboxesAlertsCheck('call_consent', 'I also agree to receive urgent alert calls.', false)}
    <p class="muted ib-alert-note">US and Canada. Reply STOP to stop texts and calls; START to resume.</p>
    <button type="button" class="ghost" data-alert-send-code>Send code · free</button>
  </form>` : `<div class="ib-alert-phone"><strong data-alert-phone>${esc(current.phone)}</strong>${inboxesPill(tone, status)}</div>
    ${pending ? `<form class="ib-alert-confirm" data-alert-confirm><label class="ib-alert-input">Verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required></label><button type="button" class="ghost">Verify</button></form>` : ''}
    ${message ? `<p class="ib-alert-note" role="status">${esc(message)}</p>` : ''}
    <div class="ib-alert-actions">
      <button type="button" class="ghost" data-alert-test${verified ? '' : ' disabled'}>Send a test · $1</button>
      <button type="button" class="ghost" data-alert-refresh>Check status</button>
      <button type="button" class="ghost" data-alert-remove>${removing ? 'Retry removal' : 'Remove phone'}</button>
    </div>`;
  const recent = (current.recent || []).slice(0, 10);
  host.innerHTML = inboxesCard('inboxes-alerts', 'Alerts', `
    <p class="ib-alert-price">$1 per accepted alert, including tests. Paid by the company.</p>
    <div class="ib-field"><div class="ib-field-label">Your phone · only you</div><div>${addPhone}</div></div>
    <form data-alert-preferences>
      <div class="ib-field"><div class="ib-field-label">Text me when</div><div class="ib-alert-choices">
        ${IB_ALERT_EVENTS.map(([key, label]) => inboxesAlertsCheck(key, label, p[key])).join('')}
        ${inboxesAlertsCheck('urgent_calls', 'Call for urgent alerts', p.urgent_calls, !current.call_consent)}
        ${!current.call_consent ? '<span class="muted ib-alert-note">Calls need separate consent when you verify your phone.</span>' : ''}
      </div></div>
      <div class="ib-field"><div class="ib-field-label">Quiet hours</div><div>
        ${inboxesAlertsCheck('quiet', 'Use quiet hours', quiet)}
        <div class="ib-alert-hours"><label class="ib-alert-input">From<input name="quiet_start" type="time" value="${esc(p.quiet_start || '22:00')}"${quiet ? '' : ' disabled'} required></label>
        <label class="ib-alert-input">Until<input name="quiet_end" type="time" value="${esc(p.quiet_end || '07:00')}"${quiet ? '' : ' disabled'} required></label></div>
        <label class="ib-alert-input">Timezone<input name="timezone" list="ib-alert-timezones" value="${esc(zone)}" required maxlength="80"></label>
        <datalist id="ib-alert-timezones">${timezones.map(t => `<option value="${esc(t)}"></option>`).join('')}</datalist>
        ${inboxesAlertsCheck('emergency_exception', 'Allow urgent alerts during quiet hours', p.emergency_exception)}
      </div></div>
      <div class="ib-field"><label class="ib-field-label" for="ib-alert-cap">Your monthly cap ($)</label><div>
        <input id="ib-alert-cap" name="monthly_cap" type="number" inputmode="numeric" min="0" max="10000" step="1" value="${esc(p.monthly_cap ?? 10)}" required>
        <p class="muted ib-alert-note">$${esc(current.monthly_spend || 0)} accepted or reserved this UTC month. Zero turns paid alerts off.</p>
      </div></div>
      <button type="button" class="ghost">Save alert preferences</button>
    </form>
    <div class="ib-field"><div class="ib-field-label">Company monthly cap</div><div>
      ${ctx.admin ? `<form class="ib-alert-company" data-alert-company><label class="ib-alert-input">Dollars<input name="monthly_cap" type="number" inputmode="numeric" min="0" max="100000" step="1" value="${esc(current.company_monthly_cap)}" required></label><button type="button" class="ghost">Save company cap</button></form>`
        : `<span>$${esc(current.company_monthly_cap)}</span>`}
      <p class="muted ib-alert-note">$${esc(current.company_monthly_spend || 0)} accepted or reserved this UTC month.</p>
    </div></div>
    ${recent.length ? `<details class="ib-alert-history"><summary>Recent alerts</summary><ul>${recent.map(r => `<li><span>${esc(IB_ALERT_EVENTS.find(([k]) => k === r.kind)?.[1] || 'Test alert')}</span><span>${esc(r.state)}${r.error ? ' · ' + esc(String(r.error).replaceAll('_', ' ')) : ''}</span></li>`).join('')}</ul></details>` : ''}
    <p class="err" role="alert" data-alert-error></p>`);
  const card = host.querySelector('#inboxes-alerts');
  const redraw = async () => { if (ctx.live() && host.isConnected) await inboxesAlerts(host, ctx); };
  const work = async (form, run) => {
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(b => { b.disabled = true; });
    card.querySelector('[data-alert-error]').textContent = '';
    try { await run(); }
    catch (e) { if (ctx.live() && card.isConnected) card.querySelector('[data-alert-error]').textContent = e.message || 'Try again'; }
    finally { buttons.forEach(b => { b.disabled = false; }); }
  };
  const add = card.querySelector('[data-alert-add]');
  if (add) {
    const sms = add.elements.sms_consent, button = add.querySelector('[data-alert-send-code]');
    button.disabled = !sms.checked;
    sms.onchange = () => { button.disabled = !sms.checked; };
    add.onsubmit = e => { e.preventDefault(); if (!add.reportValidity() || !sms.checked) return;
      void work(add, async () => {
        await post('/v2/alerts/me/phone', {phone: add.elements.phone.value, sms_consent: true, call_consent: add.elements.call_consent.checked});
        if (!ctx.live()) return;
        await redraw();
      });
    };
  }
  const confirm = card.querySelector('[data-alert-confirm]');
  if (confirm) confirm.onsubmit = e => { e.preventDefault(); if (!confirm.reportValidity()) return;
    void work(confirm, async () => { await post('/v2/alerts/me/phone/confirm', {code: confirm.elements.code.value}); if (ctx.live()) await redraw(); });
  };
  const pref = card.querySelector('[data-alert-preferences]');
  pref.elements.quiet.onchange = () => { for (const key of ['quiet_start', 'quiet_end']) pref.elements[key].disabled = !pref.elements.quiet.checked; };
  pref.onsubmit = e => { e.preventDefault(); if (!pref.reportValidity()) return;
    const values = Object.fromEntries([...IB_ALERT_EVENTS.map(([k]) => k), 'urgent_calls', 'emergency_exception'].map(k => [k, !pref.elements[k].disabled && pref.elements[k].checked]));
    Object.assign(values, {timezone: pref.elements.timezone.value, monthly_cap: Number(pref.elements.monthly_cap.value),
      quiet_start: pref.elements.quiet.checked ? pref.elements.quiet_start.value : '', quiet_end: pref.elements.quiet.checked ? pref.elements.quiet_end.value : ''});
    void work(pref, async () => { await put('/v2/alerts/me', values); if (ctx.live()) { await redraw(); toast('Alert preferences saved'); } });
  };
  const company = card.querySelector('[data-alert-company]');
  if (company) company.onsubmit = e => { e.preventDefault(); if (!company.reportValidity()) return;
    void work(company, async () => { await put('/v2/alerts/company', {monthly_cap: Number(company.elements.monthly_cap.value)}); if (ctx.live()) await redraw(); });
  };
  // Settings renders several cards together; each Alerts action submits only its own form, including Enter.
  for (const form of card.querySelectorAll('form')) {
    const button = form.querySelector('button');
    button.onclick = () => form.requestSubmit();
    form.onkeydown = e => {
      if (e.key === 'Enter' && !e.isComposing && e.target.matches('input:not([type=checkbox])')) {
        e.preventDefault();
        if (!button.disabled) form.requestSubmit();
      }
    };
  }
  card.querySelector('[data-alert-refresh]')?.addEventListener('click', () => { void work(card, redraw); });
  card.querySelector('[data-alert-remove]')?.addEventListener('click', async () => {
    if (!removing && !await settingsConfirm({title: 'Remove your phone?', text: 'This revokes your phone and stops queued alerts.', ok: 'Remove phone'})) return;
    if (!ctx.live()) return;
    void work(card, async () => {
      try { await writeRequest('DELETE', '/v2/alerts/me/phone', {}); }
      catch (e) { if (ctx.live()) await redraw(); throw e; }
      if (ctx.live()) await redraw();
    });
  });
  card.querySelector('[data-alert-test]')?.addEventListener('click', async () => {
    const person = ctx.me?.actor || ctx.me?.id || 'me', storageKey = 'tico.alert-test.' + person;
    let operation;
    try { operation = sessionStorage.getItem(storageKey); } catch { /* A blocked storage still uses API retry keys. */ }
    if (!operation && !await settingsConfirm({title: 'Send a test alert?', text: 'Your company pays $1 if this test alert is accepted. Monthly caps apply.', ok: 'Send test · $1', danger: false})) return;
    if (!ctx.live()) return;
    operation ||= newRequestId();
    try { sessionStorage.setItem(storageKey, operation); } catch { /* The API also keeps an uncertain request's id. */ }
    void work(card, async () => {
      const result = await post('/v2/alerts/me/test', {accept_charge: true}, operation);
      if (!ctx.live()) return;
      if (result.status !== 'sending') { try { sessionStorage.removeItem(storageKey); } catch { /* Not stored. */ } }
      await redraw();
      toast(result.status === 'accepted' ? 'Test accepted · $1 paid by company'
        : result.status === 'spend_cap' ? 'Monthly cap reached; no test sent' : 'Test: ' + result.status, result.status !== 'accepted');
    });
  });
}

inboxesSetting('texts-alerts', {section: 'texts', order: 40, render: inboxesAlerts});
