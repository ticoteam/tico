/* ui/app/settings-triggers.js — Settings > Triggers
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Webhooks that start routines (backend/triggers.py). A trigger is only an address: a routine picks it under
// "On a trigger" and that routine says which bot runs and what it is told. The address with its secret is shown
// once, right after it is made or its secret is replaced; the list shows it without the secret. A signing
// secret is shown the same way, once, and each trigger's log of recent calls (accepted and refused) opens below it.
let TRIGGER_SHOWN = null;      // {id, url?, signing?}: the one trigger whose secrets are on screen
const triggerMayManage = t => S.me?.role === 'owner' || t.created_by === S.me?.id;
function triggerRoutinesHTML(t) {
  if (!t.routines.length) return '<span class="muted">No routine uses it yet</span>';
  return t.routines.map(r => `<a href="#" data-trigger-routine="${esc(r.id)}" data-bot="${esc(r.bot)}">${esc(r.title)}</a>
    <span class="muted">· ${esc(empName(r.bot))}</span>`).join('<br>');
}
function triggerSecretHTML(shown) {
  return (shown.url ? triggerAddressHTML(shown) : '') + (shown.signing ? triggerSigningHTML(shown) : '');
}
function triggerSigningHTML(shown) {
  return `<div class="trigger-secret" role="status">
    <p><strong>Copy this signing secret now.</strong> It is not shown again. Calls without a matching signature are refused.</p>
    <div class="row"><code class="trigger-url">${esc(shown.signing)}</code><button class="ghost" type="button" data-trigger-copy="signing">Copy</button></div>
    <p class="settings-cell-note">On GitHub, paste it under the webhook's <em>Secret</em>. Other senders put the HMAC-SHA256 of the body, in hex, in <code>X-Signature: sha256=…</code>.</p>
  </div>`;
}
function triggerAddressHTML(shown) {
  return `<div class="trigger-secret" role="status">
    <p><strong>Copy this address now.</strong> It carries the secret and is not shown again.</p>
    <div class="row"><code class="trigger-url">${esc(shown.url)}</code><button class="ghost" type="button" data-trigger-copy="url">Copy</button></div>
    <p class="settings-cell-note">Send a POST with any body: <code>curl -X POST -H 'Content-Type: application/json' -d '{"hello":"world"}' '${esc(shown.url)}'</code></p>
  </div>`;
}
async function renderSettingsTriggers() {
  const el = $('#set-triggers'); if (!el) return;
  let list;
  try { list = await routineTriggersLoad(); } catch (e) { el.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  el.innerHTML = `<form class="row trigger-new" data-trigger-new>
      <input name="name" type="text" maxlength="100" required autocomplete="off" placeholder="Webhook name, e.g. New order" aria-label="Webhook name">
      <button class="primary" type="submit">Add webhook</button><span class="muted" data-trigger-status></span></form>
    ${TRIGGER_SHOWN ? triggerSecretHTML(TRIGGER_SHOWN) : ''}
    ${list.length ? `<ul class="trigger-list">${list.map(t => `<li>
        <div class="trigger-main"><strong>${esc(t.name)}</strong>
          <span class="settings-cell-note"><code>${esc(t.url)}/…</code></span>
          <span class="settings-cell-note">${t.signed ? '<span class="pill">Signed</span> ' : ''}${t.last_received ? `Last called ${esc(ago(t.last_received))} · ${t.received_24h} in 24h` : 'Never called'}${t.refused_24h ? ` · ${t.refused_24h} refused` : ''}</span>
          <details class="trigger-log" data-trigger-log="${esc(t.id)}"><summary>Recent calls</summary><div class="muted">Loading…</div></details></div>
        <div class="trigger-used">${triggerRoutinesHTML(t)}</div>
        <div class="trigger-actions">${triggerMayManage(t) ? `<button class="ghost" type="button" data-trigger-rotate="${esc(t.id)}">New secret</button>
          <button class="ghost" type="button" data-trigger-sign="${esc(t.id)}">${t.signed ? 'New signing secret' : 'Require signature'}</button>
          ${t.signed ? `<button class="ghost" type="button" data-trigger-unsign="${esc(t.id)}">Stop signing</button>` : ''}
          <button class="ghost" type="button" data-trigger-delete="${esc(t.id)}">Delete</button>` : ''}</div></li>`).join('')}</ul>`
      : '<div class="empty">No triggers yet. Add a webhook, then pick it in a routine under “On a trigger”.</div>'}`;
  el.querySelectorAll('[data-trigger-log]').forEach(d => d.addEventListener('toggle', () => { if (d.open) void triggerLogLoad(d); }));
  const status = el.querySelector('[data-trigger-status]');
  el.querySelector('[data-trigger-new]').onsubmit = async ev => {
    ev.preventDefault();
    status.textContent = 'Adding…';
    try {
      const made = (await post('/v2/triggers', {name: ev.target.elements.name.value.trim()})).trigger;
      if (made.replayed) { TRIGGER_SHOWN = null; renderSettingsTriggers(); toast('Webhook added, but its address was lost on the way. Use New secret to get one.', true); return; }
      TRIGGER_SHOWN = {id: made.id, url: made.url};
      renderSettingsTriggers();
    } catch (e) { status.textContent = e.message; }
  };
  el.onclick = async ev => {
    const copy = ev.target.closest('[data-trigger-copy]');
    if (copy && TRIGGER_SHOWN) {
      const signing = copy.dataset.triggerCopy === 'signing';
      await copyText(signing ? TRIGGER_SHOWN.signing : TRIGGER_SHOWN.url); toast(signing ? 'Signing secret copied' : 'Address copied'); return;
    }
    const open = ev.target.closest('[data-trigger-routine]');
    if (open) {
      ev.preventDefault();
      const id = open.dataset.triggerRoutine, bot = open.dataset.bot;
      const r = routineById(id) || ((await get(`/v2/bots/${encodeURIComponent(bot)}/routines`).catch(() => ({}))).routines || []).find(x => x.id === id);
      if (r) routineEditorOpen({...r, employee: bot});
      return;
    }
    const rotate = ev.target.closest('[data-trigger-rotate]'), del = ev.target.closest('[data-trigger-delete]');
    const sign = ev.target.closest('[data-trigger-sign]'), unsign = ev.target.closest('[data-trigger-unsign]');
    try {
      if (sign) {
        const id = sign.dataset.triggerSign, t = list.find(x => x.id === id);
        if (t?.signed && !window.confirm('Make a new signing secret? Calls signed with the current one are refused at once.')) return;
        const made = (await post(`/v2/triggers/${encodeURIComponent(id)}/signing`, {})).trigger;
        if (made.replayed) { renderSettingsTriggers(); toast('Signing is on, but the secret was lost on the way. Use New signing secret to get one.', true); return; }
        TRIGGER_SHOWN = {...(TRIGGER_SHOWN?.id === id ? TRIGGER_SHOWN : {}), id, signing: made.signing_secret};
        renderSettingsTriggers();
      }
      if (unsign) {
        if (!window.confirm('Stop checking signatures? Anyone with the address can call it again.')) return;
        await post(`/v2/triggers/${encodeURIComponent(unsign.dataset.triggerUnsign)}/signing`, {off: true});
        if (TRIGGER_SHOWN?.id === unsign.dataset.triggerUnsign) { delete TRIGGER_SHOWN.signing; if (!TRIGGER_SHOWN.url) TRIGGER_SHOWN = null; }
        renderSettingsTriggers();
      }
      if (rotate) {
        if (!window.confirm('Make a new secret? The current address stops working at once.')) return;
        const t = (await post(`/v2/triggers/${encodeURIComponent(rotate.dataset.triggerRotate)}/rotate`, {})).trigger;
        if (t.replayed) {
          if (TRIGGER_SHOWN?.id === t.id) { delete TRIGGER_SHOWN.url; if (!TRIGGER_SHOWN.signing) TRIGGER_SHOWN = null; }
          renderSettingsTriggers(); toast('A new secret was made, but the address was lost on the way. Use New secret again.', true); return;
        }
        TRIGGER_SHOWN = {...(TRIGGER_SHOWN?.id === t.id ? TRIGGER_SHOWN : {}), id: t.id, url: t.url};
        renderSettingsTriggers();
      }
      if (del) {
        const t = list.find(x => x.id === del.dataset.triggerDelete);
        const using = t?.routines.length ? ` ${t.routines.length} routine${t.routines.length === 1 ? '' : 's'} will stop running on it.` : '';
        if (!window.confirm(`Delete “${t?.name || del.dataset.triggerDelete}”?${using}`)) return;
        await post(`/v2/triggers/${encodeURIComponent(del.dataset.triggerDelete)}/delete`, {});
        if (TRIGGER_SHOWN?.id === del.dataset.triggerDelete) TRIGGER_SHOWN = null;
        renderSettingsTriggers();
      }
    } catch (e) { toast(e.message, true); }
  };
}
async function triggerLogLoad(d) {
  const box = d.querySelector('div');
  try {
    const rows = (await get(`/v2/triggers/${encodeURIComponent(d.dataset.triggerLog)}/deliveries`)).deliveries || [];
    box.className = '';
    box.innerHTML = rows.length ? `<ul class="trigger-deliveries">${rows.map(r => `<li>
        <span class="pill ${r.outcome === 'refused' ? 'fail' : 'ok'}">${r.outcome === 'refused' ? 'Refused' : 'Accepted'}</span>
        <span>${esc(ago(r.received_at))}</span>
        <span class="muted">${r.outcome === 'refused' ? esc(r.reason) : `${r.tasks} task${r.tasks === 1 ? '' : 's'}`} · ${r.bytes} bytes${r.sent_id ? ` · <code>${esc(r.sent_id)}</code>` : ''}</span></li>`).join('')}</ul>`
      : '<span class="muted">No calls yet.</span>';
  } catch (e) { box.className = 'err'; box.textContent = e.message; }
}
