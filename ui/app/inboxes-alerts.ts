/* ui/app/inboxes-alerts.ts — Personal phone alerts: Settings › General › Notifications (settings.js draws it into
   #settings-notifications with a ctx of its own). The pills and card come from ui/app/inboxes.ts. */
'use strict';

// The alert events a person can ask for, in the order the card draws them.
type IbAlertEvent = 'legal_mail';
// GET /v2/alerts/me, as this card reads it (ui/types/api.d.ts).
type IbAlertState = import('../types/api').PersonalAlerts;
type IbAlertPrefs = Partial<IbAlertState['preferences']>;
// The Notifications host; settingsNotificationsRender keeps the latest draw's number on it.
type IbAlertsHost = HTMLElement & { _alertsRun?: number };

const IB_ALERT_EVENTS: [IbAlertEvent, string][] = [
  ['legal_mail', 'Legal mail arrives'],
];
const IB_ALERT_STATUS: Record<string, [string, string]> = {
  none: ['off', 'No phone'], code_sent: ['pending', 'Verify your phone'], verified: ['ok', 'Verified'],
  opted_out: ['attention', 'Opted out'], unverified: ['attention', 'Verification removed'],
  removal_pending: ['attention', 'Removal pending'], unavailable: ['attention', 'Unavailable'],
};

function inboxesAlertsCheck(name: string, title: string, checked: boolean | null | undefined, disabled = false): string {
  return `<label class="ib-alert-check"><input type="checkbox" name="${name}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}><span>${title}</span></label>`;
}

// The named controls of each form in this card. form.elements has no typed names, so each form's local casts it once (erased).
interface IbAlertsAddControls {
  sms_consent: HTMLInputElement;
  phone: HTMLInputElement;
  call_consent: HTMLInputElement;
}
interface IbAlertsConfirmControls {
  code: HTMLInputElement;
}
// Every box below is drawn on every render: the IB_ALERT_EVENTS checkboxes, urgent_calls and emergency_exception.
interface IbAlertsPrefControls {
  legal_mail: HTMLInputElement;
  urgent_calls: HTMLInputElement;
  emergency_exception: HTMLInputElement;
  quiet: HTMLInputElement;
  quiet_start: HTMLInputElement;
  quiet_end: HTMLInputElement;
  timezone: HTMLInputElement;
  monthly_cap: HTMLInputElement;
}
interface IbAlertsCompanyControls {
  monthly_cap: HTMLInputElement;
}
// The keys the preferences form draws as checkboxes.
type IbAlertBox = IbAlertEvent | 'urgent_calls' | 'emergency_exception';

// A page element the drawn markup must hold: a missing one throws a plain Error that names it.
function alertsNeed<T extends Element = HTMLElement>(selector: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(selector);
  if (!el) throw new Error(`${selector} is not on the page`);
  return el;
}
async function inboxesAlerts(host: Element | null, ctx: Pick<InboxCtx, 'admin' | 'me' | 'live'>): Promise<void> {
  const current = await get<IbAlertState>('/v2/alerts/me', {fresh: true});
  if (!ctx.live() || !host?.isConnected) return;
  const p: IbAlertPrefs = current.preferences || {}, [tone, status] = IB_ALERT_STATUS[current.status] || ['attention', 'Unavailable'];
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
  host.innerHTML = inboxesCard('inboxes-alerts', 'Notifications', `
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
  const card = host.querySelector('#inboxes-alerts')!; // the card just drawn above
  const redraw = async () => { if (ctx.live() && host.isConnected) await inboxesAlerts(host, ctx); };
  const work = async (form: Element, run: () => Promise<void>) => {
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(b => { b.disabled = true; });
    alertsNeed('[data-alert-error]', card).textContent = '';
    try { await run(); }
    catch (e) { if (ctx.live() && card.isConnected) alertsNeed('[data-alert-error]', card).textContent = errorMessage(e, 'Try again'); }
    finally { buttons.forEach(b => { b.disabled = false; }); }
  };
  const add = card.querySelector<HTMLFormElement>('[data-alert-add]');
  if (add) {
    const addFields = add.elements as unknown as IbAlertsAddControls; // the form's named controls, cast once
    const sms = addFields.sms_consent, button = add.querySelector<HTMLButtonElement>('[data-alert-send-code]')!;
    button.disabled = !sms.checked;
    sms.onchange = () => { button.disabled = !sms.checked; };
    add.onsubmit = e => { e.preventDefault(); if (!add.reportValidity() || !sms.checked) return;
      void work(add, async () => {
        await post('/v2/alerts/me/phone', {phone: addFields.phone.value, sms_consent: true, call_consent: addFields.call_consent.checked});
        if (!ctx.live()) return;
        await redraw();
      });
    };
  }
  const confirm = card.querySelector<HTMLFormElement>('[data-alert-confirm]');
  if (confirm) {
    const confirmFields = confirm.elements as unknown as IbAlertsConfirmControls; // the form's named controls, cast once
    confirm.onsubmit = e => { e.preventDefault(); if (!confirm.reportValidity()) return;
      void work(confirm, async () => { await post('/v2/alerts/me/phone/confirm', {code: confirmFields.code.value}); if (ctx.live()) await redraw(); });
    };
  }
  const pref = card.querySelector<HTMLFormElement>('[data-alert-preferences]')!;
  const prefFields = pref.elements as unknown as IbAlertsPrefControls; // the form's named controls, cast once
  prefFields.quiet.onchange = () => { for (const key of ['quiet_start', 'quiet_end'] as const) prefFields[key].disabled = !prefFields.quiet.checked; };
  pref.onsubmit = e => { e.preventDefault(); if (!pref.reportValidity()) return;
    const boxes: IbAlertBox[] = [...IB_ALERT_EVENTS.map(([k]) => k), 'urgent_calls', 'emergency_exception'];
    const values: Record<string, unknown> = Object.fromEntries(boxes.map((k): [string, boolean] => [k, !prefFields[k].disabled && prefFields[k].checked]));
    Object.assign(values, {timezone: prefFields.timezone.value, monthly_cap: Number(prefFields.monthly_cap.value),
      quiet_start: prefFields.quiet.checked ? prefFields.quiet_start.value : '', quiet_end: prefFields.quiet.checked ? prefFields.quiet_end.value : ''});
    void work(pref, async () => { await put('/v2/alerts/me', values); if (ctx.live()) { await redraw(); toast('Alert preferences saved'); } });
  };
  const company = card.querySelector<HTMLFormElement>('[data-alert-company]');
  if (company) {
    const companyFields = company.elements as unknown as IbAlertsCompanyControls; // the form's named controls, cast once
    company.onsubmit = e => { e.preventDefault(); if (!company.reportValidity()) return;
      void work(company, async () => { await put('/v2/alerts/company', {monthly_cap: Number(companyFields.monthly_cap.value)}); if (ctx.live()) await redraw(); });
    };
  }
  // Settings renders several cards together; each Alerts action submits only its own form, including Enter.
  for (const form of card.querySelectorAll('form')) {
    const button = form.querySelector<HTMLButtonElement>('button')!;
    button.onclick = () => form.requestSubmit();
    form.onkeydown = e => {
      if (e.key === 'Enter' && !e.isComposing && eventElement(e)!.matches('input:not([type=checkbox])')) {
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
    let operation: string | null | undefined;
    try { operation = sessionStorage.getItem(storageKey); } catch { /* A blocked storage still uses API retry keys. */ }
    if (!operation && !await settingsConfirm({title: 'Send a test alert?', text: 'Your company pays $1 if this test alert is accepted. Monthly caps apply.', ok: 'Send test · $1', danger: false})) return;
    if (!ctx.live()) return;
    operation ||= newRequestId();
    try { sessionStorage.setItem(storageKey, operation); } catch { /* The API also keeps an uncertain request's id. */ }
    void work(card, async () => {
      const result: import('../types/api').TestMyAlertsResponse = await post('/v2/alerts/me/test', {accept_charge: true}, operation);
      if (!ctx.live()) return;
      if (result.status !== 'sending') { try { sessionStorage.removeItem(storageKey); } catch { /* Not stored. */ } }
      await redraw();
      toast(result.status === 'accepted' ? 'Test accepted · $1 paid by company'
        : result.status === 'spend_cap' ? 'Monthly cap reached; no test sent' : 'Test: ' + result.status, result.status !== 'accepted');
    });
  });
}

// Settings › Notifications: drawn when General is shown, with a ctx that lives while its host is on the page.
function settingsNotificationsRender() {
  const host = $('#settings-notifications') as IbAlertsHost | null;
  if (!host) return;
  const run = (host._alertsRun || 0) + 1;
  host._alertsRun = run;
  const ctx = {admin: settingsIsAdmin(), me: S.me, live: () => host.isConnected && host._alertsRun === run,
    once: (_name: string, load: () => unknown) => load(), refresh: settingsNotificationsRender};
  return inboxesAlerts(host, ctx).catch(e => { if (host.isConnected) host.innerHTML = `<section class="card"><p class="err">${esc(errorMessage(e, ''))}</p></section>`; });
}
