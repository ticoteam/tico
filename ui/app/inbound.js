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
    ${settingsIsAdmin() ? `<details id="inbound-connection"><summary>Connect Inbox to Tico account/company</summary>
      <p>Get a connection code for your company from <a href="https://app.tico.dev/inbox" target="_blank" rel="noopener noreferrer">Inbox in the customer portal</a>. The code expires after five minutes.</p>
      <form id="inbound-connection-form"><label>Connection code <input name="code" type="password" autocomplete="off" spellcheck="false" minlength="43" maxlength="43" required></label>
        <button type="submit" class="ghost">Preview company</button>
        <div id="inbound-connection-preview" hidden><p id="inbound-connection-company"></p>
          <p>This workspace’s existing inboxes and messages will join the company’s shared Inbox. This workspace and its agents will also gain access to the company’s shared Inbox, including messages from other connected workspaces. Inbox IDs and addresses stay the same.</p>
          <p>Disconnecting in the portal stops future access. It cannot undo merged message history or remove copies already saved in a workspace.</p>
          <label><input name="consent" type="checkbox"> I agree to share Inbox in both directions with this company.</label>
          <button type="button" class="primary" id="inbound-connection-connect" disabled>Connect Inbox</button></div>
        <p id="inbound-connection-status" role="status"></p>
      </form></details>` : ''}
    <form id="inbound-create" class="row"><label>Inbox name <input name="name" maxlength="80" required placeholder="Account logins"></label>
      <button type="submit" class="primary">Create email inbox</button>
      ${settingsIsAdmin() ? '<button type="button" class="ghost" id="inbound-phone">Request a US mobile number</button>' : ''}</form>
    <details><summary>Manage inboxes</summary><div id="inbound-inboxes" class="inbound-scroll"></div></details>
    ${settingsIsAdmin() ? '<details><summary>Phone requests</summary><p>Monthly non-VoIP US numbers receive SMS only. Rentals require TICO approval until billing is available. Accepting a quote records your agreement to that price; it does not buy a number.</p><p id="inbound-phone-status" role="status"></p><div id="inbound-phone-requests" class="inbound-scroll"></div></details>' : ''}
    <form id="inbound-filter" class="inbound-filters">
      <label>Inboxes <select name="inboxes" multiple size="3" aria-describedby="inbound-scope-note"></select></label>
      <label>Channel <select name="kind"><option value="">All channels</option><option value="email">Email</option><option value="sms">SMS</option></select></label>
      <label>From <input name="sender" maxlength="200"></label><label>Search messages <input name="q" type="search" maxlength="200"></label>
      <label><input name="has_code" type="checkbox"> Has a code</label><label><input name="spam" type="checkbox"> Include spam</label>
      <button class="ghost" type="submit">Filter</button></form>
    <p id="inbound-scope-note" class="muted">No inbox selected means all inboxes. Select several to see them together.</p>
    <div class="inbound-scroll"><table class="inbound-table"><thead><tr><th>Received</th><th>Channel</th><th>To</th><th>From</th><th>Message</th><th>Code</th></tr></thead><tbody id="inbound-messages"></tbody></table></div>
    <div class="row"><button type="button" class="ghost" id="inbound-newest">Newest</button><button type="button" class="ghost" id="inbound-older" hidden>Older messages</button></div>`;
  let inboxes = [], phoneRequests = [], before = 0, next = null;
  const status = $('#inbound-status');
  const active = () => version === INBOUND_VIEW && host.isConnected && !host.closest('.settings-pane')?.hidden;
  const failure = error => { if (active()) status.textContent = error.message; };
  const price = value => (value/1000000).toLocaleString('en-US',{style:'currency',currency:'USD'});
  const connectionForm = $('#inbound-connection-form');
  if (connectionForm) {
    let shown = null, generation = 0, busy = false;
    const input = connectionForm.elements.code, consent = connectionForm.elements.consent;
    const preview = $('#inbound-connection-preview'), connect = $('#inbound-connection-connect');
    const connectionStatus = $('#inbound-connection-status'), submit = connectionForm.querySelector('[type="submit"]');
    const clearPreview = () => {
      generation++;shown=null;preview.hidden=true;consent.checked=false;connect.disabled=true;
      $('#inbound-connection-company').textContent='';
    };
    input.oninput = () => { clearPreview();connectionStatus.textContent=''; };
    consent.onchange = () => { connect.disabled=busy || !shown || !consent.checked; };
    async function connectionAction(action) {
      if (busy) return;
      if (action==='connect' && (!shown || !consent.checked)) return;
      if (action==='connect' && shown.expires_at*1000<=Date.now()) {
        clearPreview();connectionStatus.textContent='This code expired. Get a new code in the portal.';return;
      }
      const code=input.value, target=shown?.company.company_id;
      if (action==='preview') clearPreview();
      const pending=generation;
      busy=true;submit.disabled=true;connect.disabled=true;
      if (action==='connect') { input.disabled=true;consent.disabled=true; }
      connectionStatus.textContent=action==='preview' ? 'Checking company…' : 'Connecting Inbox…';
      try {
        const result=await post('/v2/inbound/company-connection',{action,code});
        if (!active() || generation!==pending || input.value!==code) return;
        if (action==='preview') {
          shown=result;
          $('#inbound-connection-company').textContent=`Company: ${result.company.name} · ${result.company.company_id}`;
          preview.hidden=false;connectionStatus.textContent='Review the company and shared Inbox access before connecting.';
        } else {
          if (result.company.company_id!==target) throw new Error('Could not confirm the selected company. Check the portal.');
          input.value='';clearPreview();
          connectionStatus.textContent=`Inbox connected to ${result.company.name} (${result.company.company_id}). Manage or disconnect it in the customer portal.`;
          await refreshInboxes();await messages();
        }
      } catch(error) {
        if (active() && generation===pending) {
          if ([403,409,422].includes(error.status)) clearPreview();
          connectionStatus.textContent=error.message;
        }
      } finally {
        busy=false;input.disabled=false;consent.disabled=false;
        if (active()) { submit.disabled=false;connect.disabled=!shown || !consent.checked; }
      }
    }
    connectionForm.onsubmit = event => {event.preventDefault();void connectionAction('preview');};
    connect.onclick = () => void connectionAction('connect');
  }
  async function refreshPhoneRequests() {
    const target = $('#inbound-phone-requests');
    if (!target) return;
    const data = await get('/v2/inbound/phone-requests');
    if (!active()) return;
    phoneRequests = data.phone_requests;
    let after = data.next;
    for (let n=0; after && n<9; n++) {
      const more = await get('/v2/inbound/phone-requests?after='+encodeURIComponent(after));
      if (!active()) return;
      phoneRequests.push(...more.phone_requests);after=more.next;
    }
    const at = Date.now()/1000;
    target.innerHTML = phoneRequests.length ? `<table class="inbound-table"><thead><tr><th>Name</th><th>Status</th><th>Monthly price</th><th>Actions</th></tr></thead><tbody>${phoneRequests.map(r => {
      const quoted = r.state==='quoted' && r.price_micros>0 && r.quoted_until>at;
      const cancellable = ['requested','quoted','accepted','approved'].includes(r.state);
      return `<tr><td>${esc(r.name)}</td><td>${esc(r.state)}${r.state==='quoted' && !quoted ? ' · quote expired' : ''}</td><td>${r.price_micros>0 ? esc(price(r.price_micros))+' / 30 days' : 'Awaiting quote'}${r.quoted_until ? `<br>Quote valid until ${esc(new Date(r.quoted_until*1000).toLocaleString())}` : ''}</td><td>${quoted ? `<button class="linkish" data-inbound-phone-accept="${esc(r.request_id)}">Accept quote</button> ` : ''}${cancellable ? `<button class="linkish" data-inbound-phone-cancel="${esc(r.request_id)}">Cancel request</button>` : '—'}</td></tr>`;
    }).join('')}</tbody></table>` : '<p>No phone requests yet.</p>';
    $('#inbound-phone-status').textContent = after ? 'Showing the first 1,000 requests.' : '';
  }
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
      : data.last_synced_at ? `Last synced ${new Date(data.last_synced_at*1000).toLocaleString()}.` : 'Waiting for the first sync.';
    $('#inbound-create').querySelector('button[type="submit"]').disabled = !data.configured;
    if ($('#inbound-phone')) {
      $('#inbound-phone').disabled = !data.configured;
      if (data.configured) void refreshPhoneRequests().catch(error => {
        if (active()) $('#inbound-phone-status').textContent=error.message;
      });
    }
    $('#inbound-inboxes').innerHTML = inboxes.length ? `<table class="inbound-table"><thead><tr><th>Name</th><th>Address / number</th><th>Status</th><th>Used for</th><th>Actions</th></tr></thead><tbody>${inboxes.map(i => `<tr><td>${esc(i.name)}</td><td>${esc(i.address)}</td><td>${esc(i.state)}${i.cancel_pending ? ' · renewal off' : ''}${i.period_end ? `<br>Paid through ${esc(new Date(i.period_end*1000).toLocaleDateString())}` : ''}</td><td>${esc(i.used_for)}</td><td>${i.kind!=='sms' || settingsIsAdmin() ? `<button class="linkish" data-inbound-edit="${esc(i.inbox_id)}">Edit</button>` : '—'}${settingsIsAdmin() && i.state !== 'released' ? ` <button class="linkish" data-inbound-release="${esc(i.inbox_id)}">${i.kind === 'sms' ? 'Cancel renewal' : 'Release'}</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<p>No inboxes yet.</p>';
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
    const button=$('#inbound-phone');button.disabled=true;
    try {
      const name = $('#inbound-create').elements.name.value || 'Mobile number';
      await post('/v2/inbound/phone-requests',{name});
      await refreshPhoneRequests();
      status.textContent='Phone request sent for TICO review. No number has been purchased.';
    } catch(error) { failure(error); }
    finally { button.disabled=false; }
  };
  host.onclick = async event => {
    const read=event.target.closest('[data-inbound-read]'),code=event.target.closest('[data-inbound-code]'),edit=event.target.closest('[data-inbound-edit]'),release=event.target.closest('[data-inbound-release]');
    const accept=event.target.closest('[data-inbound-phone-accept]'),cancel=event.target.closest('[data-inbound-phone-cancel]');
    try {
      if (accept || cancel) {
        const id=accept?.dataset.inboundPhoneAccept || cancel.dataset.inboundPhoneCancel;
        const request=phoneRequests.find(r=>r.request_id===id);
        if (!request) return;
        if (accept && !confirm(`Accept ${price(request.price_micros)} per 30 days for ${request.name}? Incoming SMS is included. TICO must still approve the rental; this does not buy a number.`)) return;
        if (cancel && !confirm(`Cancel the phone request for ${request.name}?`)) return;
        const button=accept || cancel;button.disabled=true;
        try {
          await post('/v2/inbound/phone-requests/'+encodeURIComponent(id)+(accept ? '/accept' : '/cancel'),accept ? {price_micros:request.price_micros} : {});
          await refreshPhoneRequests();
        } finally { button.disabled=false; }
      }
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
    } catch(error) {
      if (accept || cancel) await refreshPhoneRequests().catch(()=>{});
      failure(error);
    }
  };
  try { await refreshInboxes(); await messages(); } catch(error) { failure(error); }
  INBOUND_TIMER=setInterval(()=>{
    if (!active()) { clearInterval(INBOUND_TIMER);return; }
    if (document.hidden) return;
    void refreshInboxes().then(messages).catch(failure);
  },15000);
}
