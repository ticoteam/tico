/* ui/app/inboxes-sending.js — Email sending policy (backend/email_sending.py): the company's on Tools › Settings › Email,
   and each mailbox's Limits card on its page (what each bot with access may send, the mailbox's and company's caps,
   pacing, undo and who approves; Edit opens the whole form) with what is waiting to send. What was sent is the
   mailbox's Activity tab (ui/app/inboxes-email.js). Registry: ui/app/inboxes.js. */
'use strict';

const IB_SEND_MODES = [['ask_first', 'Ask first'], ['on_their_own', 'On their own'], ['never', 'Never']];
const IB_SEND_SOURCES = {company: 'Company', inbox: 'Inbox', bot: 'Bot'};
const ibSendPath = address => address ? `/v2/email/inboxes/${encodeURIComponent(address)}/sending-policy` : '/v2/email/sending-policy';
const ibSendOptions = (items, selected) => items.map(([key, label]) => `<option value="${esc(key)}"${key === selected ? ' selected' : ''}>${esc(label)}</option>`).join('');
const ibSendEffective = e => `${IB_SEND_MODES.find(([key]) => key === e.mode)?.[1] || 'Ask first'} · ${IB_SEND_SOURCES[e.sources?.mode] || 'Company'}`;
const IB_SEND_WHAT = {replies_only: 'Replies only', replies_and_new: 'Replies and new'};
const IB_SEND_ASKS = [['ask_new_address', 'New addresses'], ['ask_legal_money', 'Legal or money'], ['ask_attachments', 'Attachments'],
  ['ask_many_recipients', 'Many recipients'], ['ask_outside_cc', 'Outside Cc'], ['ask_lint', 'Writing check'],
  ['second_review', 'Second review']];
// The checkboxes of the form, in IB_SEND_ASKS order: a child layer can turn one on, never off (backend/email_sending.py).
const IB_SEND_ASK_LABELS = {ask_new_address: 'A first message to a new address', ask_legal_money: 'Legal or money matters',
  ask_attachments: 'Attachments', ask_many_recipients: 'More than 10 recipients, or more outside people than allowed',
  ask_outside_cc: 'Outside people in Cc', ask_lint: 'A bot\'s email fails the writing check',
  second_review: 'A second reviewer flags a bot\'s email'};
const IB_SEND_NUMBERS = ['daily_cap', 'mailbox_daily_cap', 'company_daily_cap', 'max_outside_recipients', 'pacing_seconds', 'undo_seconds'];
const IB_SEND_LISTS = ['blocked_recipients', 'owner_handles'];
const ibSendList = value => String(value).split(/[,;\s]+/).map(s => s.trim()).filter(Boolean);
const IB_SEND_APPROVERS = {bot_managers: 'Bot managers', inbox_admins: 'Inbox admins', named_people: 'Named people'};
const ibSendNum = n => Number(n || 0).toLocaleString('en-US');
const ibSendUndo = sec => !Number(sec) ? 'None' : Number(sec) % 60 ? `${Number(sec)} s` : `${Number(sec) / 60} min`;
const ibSendField = (label, control) => `<label class="ib-field"><span class="ib-field-label">${esc(label)}</span>${control}</label>`;
function ibSendForm(document, company = false, disabled = false) {
  const e = document.effective, stored = company ? document.company : document.inbox;
  const select = (key, label, items) => ibSendField(label, `<select name="${key}">${ibSendOptions(items, stored[key] ?? e[key])}</select>`);
  return `<form class="ib-send-form" data-send-form><fieldset${disabled ? ' disabled' : ''}>
    ${ibSendField(company ? 'Bots send' : 'Sends', `<select name="mode">${ibSendOptions(company ? IB_SEND_MODES : [['', 'Use company default'], ...IB_SEND_MODES], stored.mode || (company ? e.mode : ''))}</select>`)}
    ${select('what', 'What', [['replies_only', 'Replies only'], ['replies_and_new', 'Replies and new messages']])}
    ${select('to', 'To', [['anyone', 'Anyone'], ['known_addresses', 'Known addresses'], ['internal', 'Only inside the company']])}
    ${['daily_cap', 'mailbox_daily_cap', 'company_daily_cap'].map((key, i) => ibSendField(['Per bot, per day', 'Per inbox, per day', 'Per company, per day'][i], `<input name="${key}" type="number" min="0" max="${i === 2 ? 1000000 : 100000}" value="${Number(e[key])}">`)).join('')}
    ${ibSendField('Undo window', `<select name="undo_seconds">${ibSendOptions([['0', 'None'], ['30', '30 seconds'], ['120', '2 minutes'], ['600', '10 minutes']], String(e.undo_seconds))}</select>`)}
    ${ibSendField('Outside people per email', `<input name="max_outside_recipients" type="number" min="0" max="100" value="${Number(e.max_outside_recipients ?? 1)}">`)}
    ${ibSendField('Seconds between sends', `<input name="pacing_seconds" type="number" min="0" max="3600" value="${Number(e.pacing_seconds)}">`)}
    <div class="ib-field"><span class="ib-field-label">Always ask for</span><div class="ib-send-checks">${IB_SEND_ASKS.map(([key]) =>
      `<label><input type="checkbox" name="${key}"${e[key] ? ' checked' : ''}>${esc(IB_SEND_ASK_LABELS[key])}</label>`).join('')}</div></div>
    ${ibSendField('Never send to', `<input name="blocked_recipients" value="${esc((stored.blocked_recipients || []).join(', '))}" placeholder="Addresses or domains">`)}
    ${ibSendField('Owner handles personally', `<input name="owner_handles" value="${esc((stored.owner_handles || []).join(', '))}" placeholder="Addresses or domains">`)}
    ${select('who_can_ok', 'Who can OK', [['bot_managers', 'Bot managers'], ['inbox_admins', 'Inbox admins'], ['named_people', 'Named people']])}
    ${ibSendField('People', `<input name="approvers" value="${esc((stored.approvers || e.approvers || []).join(', '))}" placeholder="Person IDs, separated by commas">`)}
    <div class="ib-send-effective">${esc(ibSendEffective(e))}</div>
    ${(e.recipient_rules || []).length ? `<div class="ib-send-effective">${esc(e.recipient_rules.map(rule => `${({internal: 'Inside the company', known_addresses: 'Known addresses'})[rule.value]} · ${IB_SEND_SOURCES[rule.source]}`).join(' + '))}</div>` : ''}
    ${(e.approval_rules || []).length > 1 ? `<div class="ib-send-effective">${esc(e.approval_rules.map(rule => `${({bot_managers: 'Bot managers', inbox_admins: 'Inbox admins', named_people: 'Named people'})[rule.value]} · ${IB_SEND_SOURCES[rule.source]}`).join(' + '))}</div>` : ''}
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
      else if (IB_SEND_NUMBERS.includes(key)) body[key] = Number(value);
      else if (IB_SEND_LISTS.includes(key)) body[key] = ibSendList(value);
      else if (!IB_SEND_ASKS.some(([ask]) => ask === key)) body[key] = value;
    }
    for (const [key] of IB_SEND_ASKS) body[key] = data.has(key);
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
  const shown = rows.filter(Boolean);
  host.innerHTML = shown.length ? `<table class="ib-send-bots tt-limits"><thead><tr><th>Bot</th><th>Sends</th><th>What</th><th>A day</th></tr></thead><tbody>${shown.map(({bot, id, d}) => {
    const name = bot.display_name || bot.name || bot.slug || id, e = d.effective || {};
    return `<tr data-send-bot-row="${esc(id)}"><td class="tt-who-name">${avatar(id, 20)}<span>${esc(name)}</span></td>
      <td>${editable ? `<select aria-label="${esc(name)} sends" data-send-bot="${esc(id)}">${ibSendOptions([['', 'Use default'], ...IB_SEND_MODES], d.bot.mode || '')}</select>` : ''}<span class="ib-send-rule">${esc(ibSendEffective(e))}</span></td>
      <td>${esc(IB_SEND_WHAT[e.what] || e.what || '')}</td><td class="tnum">${esc(ibSendNum(e.daily_cap))}</td></tr>`;
  }).join('')}</tbody></table>` : '<p class="muted">No bots can send from it.</p>';
  host.querySelectorAll('[data-send-bot]').forEach(select => select.addEventListener('change', async () => {
    select.disabled = true;
    try { await put(`/v2/bots/${encodeURIComponent(select.dataset.sendBot)}/email-sending-policy`, select.value ? {mode: select.value} : {inherit: true}); if (ctx.live()) ctx.refresh(); }
    catch (e) { toast(e.message); select.disabled = false; }
  }));
  return shown;
}
// The rest of the policy, one line each: what always waits for an OK, the caps above each bot's, pacing, undo, who OKs.
function ibSendSummary(e) {
  const asks = IB_SEND_ASKS.filter(([k]) => e[k]).map(([, label]) => label);
  const row = (dt, dd) => dd ? `<dt>${esc(dt)}</dt><dd>${esc(dd)}</dd>` : '';
  const approvers = IB_SEND_APPROVERS[e.who_can_ok] || e.who_can_ok || '';
  return `<dl class="tt-kv ib-send-summary">${row('Always ask for', asks.join(', ') || 'Nothing')}
    ${row('This mailbox', `${ibSendNum(e.mailbox_daily_cap)} a day`)}${row('Company', `${ibSendNum(e.company_daily_cap)} a day`)}
    ${row('Pacing', `${ibSendNum(e.pacing_seconds)} s between sends`)}${row('Undo', ibSendUndo(e.undo_seconds))}
    ${row('Outside people', `${ibSendNum(e.max_outside_recipients ?? 1)} per email`)}
    ${e.blocked_recipients?.length ? row('Never send to', e.blocked_recipients.join(', ')) : ''}
    ${e.owner_handles?.length ? row('Owner handles', e.owner_handles.join(', ')) : ''}
    ${row('Who approves', approvers + (e.who_can_ok === 'named_people' && e.approvers?.length ? ' · ' + e.approvers.map(personDisplay).join(', ') : ''))}</dl>`;
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
  host.innerHTML = inboxesCard('inbox-sending', 'Limits', `<div data-send-bots></div>${ibSendSummary(document.effective || {})}
      <p class="muted tt-foot">People send as themselves, with no limits.</p>
      <div data-send-edit-body hidden>${ibSendForm(document, false, !editable)}</div>`,
      editable ? '<button type="button" class="ghost small settings-head-act" data-send-edit aria-expanded="false">Edit</button>' : '') +
    inboxesCard('inbox-waiting-to-send', 'Waiting to send', `<ul class="ib-send-pending">${(pending.sends || []).map(send =>
      `<li><div><strong>${esc(send.subject || '(No subject)')}</strong><span>${esc(send.recipients.join(', '))}</span><span>${esc(send.state === 'waiting_approval' ? 'Waiting for OK' : 'After ' + new Date(send.send_after).toLocaleTimeString())}</span><button type="button" class="ghost" data-send-preview="${esc(send.id)}">Preview</button><div data-send-preview-body></div></div>${send.can_cancel ? `<button type="button" class="ghost danger" data-send-cancel="${esc(send.id)}">Cancel</button>` : ''}</li>`).join('') || '<li class="muted">None</li>'}</ul>`);
  const edit = host.querySelector('[data-send-edit]'), body = host.querySelector('[data-send-edit-body]');
  if (edit) edit.onclick = () => { body.hidden = !body.hidden; edit.setAttribute('aria-expanded', String(!body.hidden)); edit.textContent = body.hidden ? 'Edit' : 'Close'; };
  if (editable) ibSendBind(host, ibSendPath(address), ctx);
  const grants = (inbox.accesses || []).filter(a => a.grantee_type === 'bot' && a.level === 'read_write').map(a => ({id: a.grantee_id, display_name: a.name}));
  await ibSendBots(host.querySelector('[data-send-bots]'), grants, ctx, address, editable && ctx.admin);
  if (!ctx.live()) return;
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
}});
