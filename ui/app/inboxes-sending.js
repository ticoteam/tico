/* Email sending policy, pending sends and audit. Registry: ui/app/inboxes.js. */
'use strict';

const IB_SEND_MODES = [['ask_first', 'Ask first'], ['on_their_own', 'On their own'], ['never', 'Never']];
const IB_SEND_SOURCES = {company: 'Company', inbox: 'Inbox', bot: 'Bot'};
const ibSendPath = address => address ? `/v2/email/inboxes/${encodeURIComponent(address)}/sending-policy` : '/v2/email/sending-policy';
const ibSendOptions = (items, selected) => items.map(([key, label]) => `<option value="${esc(key)}"${key === selected ? ' selected' : ''}>${esc(label)}</option>`).join('');
const ibSendEffective = e => `${IB_SEND_MODES.find(([key]) => key === e.mode)?.[1] || 'Ask first'} · ${IB_SEND_SOURCES[e.sources?.mode] || 'Company'}${e.legacy ? ' · kept from before' : ''}`;
const ibSendField = (label, control) => `<label class="ib-field"><span class="ib-field-label">${esc(label)}</span>${control}</label>`;
const ibSendAudit = send => `<li><strong>${esc(send.subject || '(No subject)')}</strong><span>${esc(send.state)} · ${esc(send.actor)}${send.via ? ' · ' + esc(send.via) : ''}</span>
  <span>${esc(send.recipients.join(', '))}</span><span>${esc(send.decision.legacy ? 'On their own · kept from before' : send.decision.policy ? ibSendEffective(send.decision.policy) : 'Policy passed')}</span>
  <span>${esc(send.decision.reasons?.join(', ') || '')}</span><code>${esc(send.content_sha256)}</code>
  ${(send.attachments || []).map(file => `<span>${esc(file.name)}</span><code>${esc(file.sha256)}</code>`).join('')}
  ${send.approval_id ? `<span>Approval: ${esc(send.approval_id)}</span>` : ''}${send.provider_message_id ? `<span>Receipt: ${esc(send.provider_message_id)}</span>` : ''}</li>`;

function ibSendForm(document, company = false, disabled = false) {
  const e = document.effective, stored = company ? document.company : document.inbox;
  const select = (key, label, items) => ibSendField(label, `<select name="${key}">${ibSendOptions(items, e[key])}</select>`);
  return `<form class="ib-send-form" data-send-form><fieldset${disabled ? ' disabled' : ''}>
    ${ibSendField(company ? 'Bots send' : 'Sends', `<select name="mode">${ibSendOptions(company ? IB_SEND_MODES : [['', 'Use company default'], ...IB_SEND_MODES], stored.mode || (company ? e.mode : ''))}</select>`)}
    ${select('what', 'What', [['replies_only', 'Replies only'], ['replies_and_new', 'Replies and new messages']])}
    ${select('to', 'To', [['anyone', 'Anyone'], ['known_addresses', 'Known addresses'], ['internal', 'Only inside the company']])}
    ${['daily_cap', 'mailbox_daily_cap', 'company_daily_cap'].map((key, i) => ibSendField(['Per bot, per day', 'Per inbox, per day', 'Per company, per day'][i], `<input name="${key}" type="number" min="0" max="${i === 2 ? 1000000 : 100000}" value="${Number(e[key])}">`)).join('')}
    ${ibSendField('Undo window', `<select name="undo_seconds">${ibSendOptions([['0', 'None'], ['30', '30 seconds'], ['120', '2 minutes'], ['600', '10 minutes']], String(e.undo_seconds))}</select>`)}
    ${ibSendField('Seconds between sends', `<input name="pacing_seconds" type="number" min="0" max="3600" value="${Number(e.pacing_seconds)}">`)}
    <div class="ib-field"><span class="ib-field-label">Always ask for</span><div class="ib-send-checks">${[
      ['ask_new_address', 'A first message to a new address'], ['ask_legal_money', 'Legal or money matters'],
      ['ask_attachments', 'Attachments'], ['ask_many_recipients', 'More than 10 recipients']].map(([key, label]) =>
      `<label><input type="checkbox" name="${key}"${e[key] ? ' checked' : ''}>${esc(label)}</label>`).join('')}</div></div>
    ${select('who_can_ok', 'Who can OK', [['bot_managers', 'Bot managers'], ['inbox_admins', 'Inbox admins'], ['named_people', 'Named people']])}
    ${ibSendField('People', `<input name="approvers" value="${esc((e.approvers || []).join(', '))}" placeholder="Person IDs, separated by commas">`)}
    <div class="ib-send-effective">${esc(ibSendEffective(e))}</div>
    ${document.legacy_automation_count ? `<p class="muted"><a href="#/automations">${Number(document.legacy_automation_count)} automations</a> · On their own · kept from before</p>` : ''}
    ${disabled ? '' : '<div class="ib-actions"><button class="primary" type="submit">Save</button>' + (company ? '' : '<button type="button" class="ghost" data-send-inherit>Use company default</button>') + '</div>'}
    <p class="err" data-send-error role="alert"></p></fieldset></form>`;
}

function ibSendBind(host, path, ctx) {
  const form = host.querySelector('[data-send-form]');
  if (!form) return;
  const save = async body => {
    const button = form.querySelector('button[type="submit"]'), error = form.querySelector('[data-send-error]');
    button.disabled = true;
    error.textContent = '';
    try {
      await put(path, body);
      if (ctx.live()) {
        form.querySelectorAll('input, textarea').forEach(field => { field.defaultValue = field.value; field.blur(); });
        toast('Sending policy saved'); ctx.refresh();
      }
    }
    catch (e) { if (ctx.live()) error.textContent = e.message; }
    finally { if (button.isConnected) button.disabled = false; }
  };
  form.addEventListener('submit', event => {
    event.preventDefault();
    const data = new FormData(form), body = {};
    for (const [key, value] of data.entries()) {
      if (key === 'approvers') body[key] = String(value).split(',').map(s => s.trim()).filter(Boolean);
      else if (['daily_cap', 'mailbox_daily_cap', 'company_daily_cap', 'pacing_seconds', 'undo_seconds'].includes(key)) body[key] = Number(value);
      else if (!key.startsWith('ask_')) body[key] = value;
    }
    for (const key of ['ask_new_address', 'ask_attachments', 'ask_many_recipients', 'ask_legal_money']) body[key] = data.has(key);
    if (body.mode === '') body.mode = null;
    save(body);
  });
  form.querySelector('[data-send-inherit]')?.addEventListener('click', () => save({inherit: true}));
}

async function ibSendBots(host, bots, ctx, mailbox = '', editable = false) {
  const rows = await Promise.all(bots.slice(0, 50).map(async bot => {
    const id = bot.id || bot.slug;
    try {
      const d = await get(`/v2/bots/${encodeURIComponent(id)}/email-sending-policy${mailbox ? '?mailbox=' + encodeURIComponent(mailbox) : ''}`, {fresh: true});
      return {bot, id, d};
    } catch { return null; }
  }));
  if (!ctx.live()) return;
  host.innerHTML = rows.filter(Boolean).length ? `<table class="ib-send-bots"><thead><tr><th>Bot</th><th>Sends</th></tr></thead><tbody>${rows.filter(Boolean).map(({bot, id, d}) =>
    `<tr><td>${esc(bot.display_name || bot.name || bot.slug || id)}</td><td>${editable ? `<select aria-label="${esc(bot.display_name || bot.slug || id)} sends" data-send-bot="${esc(id)}">${ibSendOptions([['', 'Use default'], ...IB_SEND_MODES], d.bot.mode || '')}</select>` : ''}<span class="ib-send-rule">${esc(ibSendEffective(d.effective))}</span></td></tr>`).join('')}</tbody></table>` : '<p class="muted">No bot overrides.</p>';
  host.querySelectorAll('[data-send-bot]').forEach(select => select.addEventListener('change', async () => {
    select.disabled = true;
    try { await put(`/v2/bots/${encodeURIComponent(select.dataset.sendBot)}/email-sending-policy`, select.value ? {mode: select.value} : {inherit: true}); if (ctx.live()) ctx.refresh(); }
    catch (e) { toast(e.message); select.disabled = false; }
  }));
  return rows.filter(Boolean);
}

function ibSendAccessColumn(host, inbox, rows, effective) {
  const table = host.closest('.ib-detail')?.querySelector('#mailbox-access table');
  if (!table) return;
  if (!table.querySelector('thead')) table.insertAdjacentHTML('afterbegin', '<thead><tr><th>Who</th><th>Access</th><th>Sends</th><th></th></tr></thead>');
  for (const grant of inbox.accesses || []) {
    const tr = table.querySelector(`[data-mb-access-row="${CSS.escape(String(grant.id || grant.grantee_type))}"]`);
    if (!tr) continue;
    let cell = tr.querySelector('[data-send-access]');
    if (!cell) { cell = document.createElement('td'); cell.dataset.sendAccess = ''; tr.insertBefore(cell, tr.lastElementChild); }
    const bot = rows.find(row => row.id === grant.grantee_id);
    cell.textContent = grant.level !== 'read_write' ? 'Never · Read only' : inbox.may?.send === false ? 'Never · Send mail off' : grant.grantee_type === 'bot' ?
      (bot ? ibSendEffective(bot.d.effective) : 'Unknown') : ibSendEffective(effective);
  }
}

inboxesSetting('email.sending', {section: 'email', order: 40, render: async (host, ctx) => {
  if (!ctx.admin) return;
  const [document, org] = await Promise.all([get(ibSendPath(''), {fresh: true}), ctx.once('org', () => get('/v2/org'))]);
  if (!ctx.live()) return;
  host.innerHTML = inboxesCard('inboxes-email-sending', 'Sending', ibSendForm(document, true) + '<div class="ib-field"><span class="ib-field-label">Per bot</span><div data-send-bots></div></div>');
  ibSendBind(host, ibSendPath(''), ctx);
  await ibSendBots(host.querySelector('[data-send-bots]'), org.bots || [], ctx, '', true);
}});

inboxesPanel('email.sending', {kinds: ['mailbox'], order: 40, render: async (host, inbox, ctx) => {
  const address = inbox.address || inbox.mailbox?.address;
  if (!address || inbox.mailbox?.level === 'none' || inbox.may?.read === false) return;
  const [document, pending] = await Promise.all([get(ibSendPath(address), {fresh: true}), get(`/v2/email/sends?mailbox=${encodeURIComponent(address)}&pending=true`, {fresh: true})]);
  if (!ctx.live()) return;
  const editable = !!inbox.can_manage;
  host.innerHTML = inboxesCard('inbox-sending', 'Sending', ibSendForm(document, false, !editable) + '<div data-send-bots></div>') +
    inboxesCard('inbox-waiting-to-send', 'Waiting to send', `<ul class="ib-send-pending">${(pending.sends || []).map(send =>
      `<li><div><strong>${esc(send.subject || '(No subject)')}</strong><span>${esc(send.recipients.join(', '))}</span><span>${esc(send.state === 'waiting_approval' ? 'Waiting for OK' : 'After ' + new Date(send.send_after).toLocaleTimeString())}</span><button type="button" class="ghost" data-send-preview="${esc(send.id)}">Preview</button><div data-send-preview-body></div></div>${send.can_cancel ? `<button type="button" class="ghost danger" data-send-cancel="${esc(send.id)}">Cancel</button>` : ''}</li>`).join('') || '<li class="muted">None</li>'}</ul>`) +
    inboxesCard('inbox-send-audit', 'Send audit', '<button type="button" class="ghost" data-send-audit>Show activity</button><div data-send-activity></div>');
  if (editable) ibSendBind(host, ibSendPath(address), ctx);
  const grants = (inbox.accesses || []).filter(a => a.grantee_type === 'bot').map(a => ({id: a.grantee_id, display_name: a.name}));
  const botRows = await ibSendBots(host.querySelector('[data-send-bots]'), grants, ctx, address, editable && ctx.admin);
  if (!ctx.live()) return;
  ibSendAccessColumn(host, inbox, botRows, document.effective);
  host.querySelectorAll('[data-send-preview]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    const target = button.nextElementSibling;
    target.textContent = 'Loading frozen email…';
    try {
      const data = await get(`/v2/email/sends/${encodeURIComponent(button.dataset.sendPreview)}`, {fresh: true});
      if (ctx.live()) target.innerHTML = emailSendPreviewHTML(data);
    } catch (e) { if (ctx.live()) target.textContent = e.message; }
    finally { if (button.isConnected) button.disabled = false; }
  }));
  host.querySelectorAll('[data-send-cancel]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await post(`/v2/email/sends/${encodeURIComponent(button.dataset.sendCancel)}/cancel`, {}); if (ctx.live()) { button.closest('li').remove(); ctx.refresh(); } }
    catch (e) { toast(e.message); if (button.isConnected) button.disabled = false; }
  }));
  const activity = host.querySelector('[data-send-activity]'), auditButton = host.querySelector('[data-send-audit]');
  let cursor = '';
  auditButton.onclick = async () => {
    auditButton.disabled = true;
    try {
      const data = await get(`/v2/email/sends?mailbox=${encodeURIComponent(address)}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`, {fresh: true});
      if (!ctx.live()) return;
      if (!cursor) activity.innerHTML = '';
      activity.insertAdjacentHTML('beforeend', `<ul class="ib-send-audit">${data.sends.map(ibSendAudit).join('') || '<li class="muted">None</li>'}</ul>`);
      cursor = data.next_cursor || '';
      auditButton.textContent = cursor ? 'More activity' : 'Refresh activity';
    } catch (e) { if (ctx.live()) toast(e.message); }
    finally { if (auditButton.isConnected) auditButton.disabled = false; }
  };
}});
