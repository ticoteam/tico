/* Company inbound messages. Bodies are external text, never HTML. */
'use strict';
let INBOUND_VIEW = 0;
let INBOUND_TIMER = null;

async function inboundMount() {
  const host = $('#set-inbound');
  if (!host) return;
  const version = ++INBOUND_VIEW;
  clearInterval(INBOUND_TIMER);
  host.innerHTML = `<header><h2>Email &amp; Phone</h2></header>
    <p>Incoming messages for your company’s agents. Every company agent can check these inboxes. Reading a message leaves it available to everyone else.</p>
    <p id="inbound-status" role="status"></p>
    <form id="inbound-create" class="row"><label>Inbox name <input name="name" maxlength="80" required placeholder="Account logins"></label>
      <button type="submit" class="primary">Create email inbox</button>
      ${settingsIsAdmin() ? '<button type="button" class="ghost" id="inbound-phone">Rent a US mobile number</button>' : ''}</form>
    <details><summary>Manage inboxes</summary><div id="inbound-inboxes" class="inbound-scroll"></div></details>
    <form id="inbound-filter" class="inbound-filters">
      <label>Inboxes <select name="inboxes" multiple size="3" aria-describedby="inbound-scope-note"></select></label>
      <label>Channel <select name="kind"><option value="">All channels</option><option value="email">Email</option><option value="sms">SMS</option></select></label>
      <label>From <input name="sender" maxlength="200"></label><label>Search messages <input name="q" type="search" maxlength="200"></label>
      <label><input name="has_code" type="checkbox"> Has a code</label><label><input name="spam" type="checkbox"> Include spam</label>
      <button class="ghost" type="submit">Filter</button></form>
    <p id="inbound-scope-note" class="muted">No inbox selected means all inboxes. Select several to see them together.</p>
    <div class="inbound-scroll"><table class="inbound-table"><thead><tr><th>Received</th><th>Channel</th><th>To</th><th>From</th><th>Message</th><th>Code</th></tr></thead><tbody id="inbound-messages"></tbody></table></div>
    <div class="row"><button type="button" class="ghost" id="inbound-newest">Newest</button><button type="button" class="ghost" id="inbound-older" hidden>Older messages</button></div>`;
  let inboxes = [], before = 0, next = null;
  const status = $('#inbound-status');
  const active = () => version === INBOUND_VIEW && host.isConnected && !host.closest('.settings-pane')?.hidden;
  const failure = error => { if (active()) status.textContent = error.message; };
  async function refreshInboxes() {
    const data = await get('/v2/inbound/inboxes');
    if (!active()) return;
    inboxes = data.inboxes;
    let after=data.next;
    for(let n=0; after && n<9; n++){
      const more=await get('/v2/inbound/inboxes?after='+encodeURIComponent(after));
      if(!active())return;
      inboxes.push(...more.inboxes);after=more.next;
    }
    const select = $('#inbound-filter').elements.inboxes;
    const selected = new Set([...select.selectedOptions].map(o => o.value));
    select.innerHTML = inboxes.map(i => `<option value="${esc(i.inbox_id)}"${selected.has(i.inbox_id) ? ' selected' : ''}>${esc(i.name)} · ${esc(i.address)}</option>`).join('');
    status.textContent = !data.configured ? 'Email & Phone is not connected. Ask your owner to set it up.'
      : data.error ? 'Messages could not sync. Tico will retry.'
      : data.gaps.length ? `${data.gaps.reduce((sum,g) => sum+g.count,0)} messages expired before Tico reconnected.`
      : data.last_sync ? `Last synced ${new Date(data.last_sync*1000).toLocaleString()}.` : 'Waiting for the first sync.';
    $('#inbound-create').querySelector('button[type="submit"]').disabled = !data.configured;
    if ($('#inbound-phone')) $('#inbound-phone').disabled = !data.configured;
    $('#inbound-inboxes').innerHTML = inboxes.length ? `<table class="inbound-table"><thead><tr><th>Name</th><th>Address / number</th><th>Status</th><th>Used for</th><th>Actions</th></tr></thead><tbody>${inboxes.map(i => `<tr><td>${esc(i.name)}</td><td>${esc(i.address)}</td><td>${esc(i.state)}${i.cancel_pending ? ' · renewal off' : ''}${i.period_end ? `<br>Paid through ${esc(new Date(i.period_end*1000).toLocaleDateString())}` : ''}</td><td>${esc(i.used_for)}</td><td><button class="linkish" data-inbound-edit="${esc(i.inbox_id)}">Edit</button>${settingsIsAdmin() && i.state !== 'released' ? ` <button class="linkish" data-inbound-release="${esc(i.inbox_id)}">${i.kind === 'sms' ? 'Cancel renewal' : 'Release'}</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<p>No inboxes yet.</p>';
  }
  async function messages() {
    const form = $('#inbound-filter'), params = new URLSearchParams({limit:'50'});
    const ids = [...form.elements.inboxes.selectedOptions].map(o => o.value);
    if (ids.length) params.set('inbox_ids',ids.join(','));
    for (const name of ['kind','q']) if (form.elements[name].value) params.set(name,form.elements[name].value);
    if (form.elements.sender.value) params.set('from',form.elements.sender.value);
    for (const name of ['has_code','spam']) if (form.elements[name].checked) params.set(name,'true');
    if (before) params.set('before',String(before));
    const data = await get('/v2/inbound/messages?'+params);
    if (!active()) return;
    next = data.next;
    $('#inbound-older').hidden = !next;
    $('#inbound-messages').innerHTML = data.messages.length ? data.messages.map(m => {
      const i = inboxes.find(i => i.inbox_id === m.inbox_id);
      return `<tr><td>${esc(new Date(m.received_at*1000).toLocaleString())}</td><td>${esc(m.kind.toUpperCase())}</td><td>${esc(i?.address || m.inbox_id)}<br><span class="muted">${esc(i?.name || '')}</span></td><td>${esc(m.from)}</td><td><button class="linkish" data-inbound-read="${esc(m.message_id)}">${esc(m.subject || m.preview || (m.retention_removed ? 'Removed by retention' : 'View message'))}</button>${m.subject ? `<p>${esc(m.preview)}</p>` : ''}${m.spam ? '<span class="muted">Spam</span>' : ''}${m.truncated ? '<span class="muted"> · Truncated</span>' : ''}</td><td>${m.has_code ? `<button class="linkish" data-inbound-code="${esc(m.message_id)}">Reveal code</button>` : '—'}</td></tr>`;
    }).join('') : '<tr><td colspan="6">No messages match these filters.</td></tr>';
  }
  $('#inbound-filter').onsubmit = event => { event.preventDefault(); before=0; void messages().catch(failure); };
  $('#inbound-newest').onclick = () => { before=0; void messages().catch(failure); };
  $('#inbound-older').onclick = () => { before=next; void messages().catch(failure); };
  $('#inbound-create').onsubmit = async event => {
    event.preventDefault(); const form=event.currentTarget,button=form.querySelector('button[type="submit"]');button.disabled=true;
    try { await post('/v2/inbound/inboxes',{kind:'email',name:form.elements.name.value}); form.reset(); await refreshInboxes(); }
    catch(error) { failure(error); }
    finally { button.disabled=false; }
  };
  if ($('#inbound-phone')) $('#inbound-phone').onclick = async () => {
    try {
      const quote = await get('/v2/inbound/phone-quote');
      if (!quote.purchases_enabled) { status.textContent='Phone rentals are not enabled for purchase yet.';return; }
      const price = (quote.price_micros/1000000).toLocaleString('en-US',{style:'currency',currency:'USD'});
      if (!confirm(`Rent one non-VoIP US mobile number for ${price} per 30 days? Incoming SMS is included. It renews until an admin cancels renewal.`)) return;
      const name = $('#inbound-create').elements.name.value || 'Mobile number';
      await post('/v2/inbound/inboxes',{kind:'sms',name,max_price_micros:quote.price_micros});
      await refreshInboxes();
    } catch(error) { failure(error); }
  };
  host.onclick = async event => {
    const read=event.target.closest('[data-inbound-read]'),code=event.target.closest('[data-inbound-code]'),edit=event.target.closest('[data-inbound-edit]'),release=event.target.closest('[data-inbound-release]');
    try {
      if (read || code) {
        const id=read?.dataset.inboundRead || code.dataset.inboundCode;
        const dialog=document.createElement('dialog');dialog.className='tmodal inbound-dialog';dialog.setAttribute('aria-label',code ? 'Verification code' : 'Incoming message');
        dialog.innerHTML='<h2>Incoming message</h2><pre></pre><div class="row"><button type="button" class="ghost" data-more hidden>Read more</button><button type="button" class="ghost" data-close>Close</button></div>';
        document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.showModal();
        let offset=0;
        const load=async()=>{
          const data=await get('/v2/inbound/messages/'+encodeURIComponent(id)+(code ? '/code' : '?offset='+offset));
          dialog.querySelector('pre').textContent += code ? data.code || 'No code found.' : data.text;
          dialog.querySelector('[data-more]').hidden = data.next_offset === null || code;
          offset=data.next_offset;
        };
        dialog.querySelector('[data-more]').onclick=()=>void load().catch(failure);
        await load();
      }
      if (edit) {
        const i=inboxes.find(i=>i.inbox_id===edit.dataset.inboundEdit);
        const name=prompt('Inbox name',i.name);if (name===null)return;
        const used_for=prompt('What accounts or workflows depend on this inbox?',i.used_for);if (used_for===null)return;
        await writeRequestOnce('PATCH','/v2/inbound/inboxes/'+i.inbox_id,{name,used_for});await refreshInboxes();
      }
      if (release) {
        const i=inboxes.find(i=>i.inbox_id===release.dataset.inboundRelease);
        const warning=i.kind==='sms' ? 'Cancel renewal? The number keeps receiving until the paid period ends.' : 'Release this address permanently? Future messages will no longer be delivered.';
        let confirmation='';
        if (i.used_for) { confirmation=prompt(`${warning}\nUsed for: ${i.used_for}\nType ${i.address} to confirm.`);if(confirmation!==i.address)return; }
        else if (!confirm(warning)) return;
        await post('/v2/inbound/inboxes/'+i.inbox_id+'/release',{confirm:confirmation});await refreshInboxes();
      }
    } catch(error) { failure(error); }
  };
  try { await refreshInboxes(); await messages(); } catch(error) { failure(error); }
  INBOUND_TIMER=setInterval(()=>{
    if (!active()) { clearInterval(INBOUND_TIMER);return; }
    void refreshInboxes().then(messages).catch(failure);
  },15000);
}
