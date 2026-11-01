/* ui/app/mail.js — Message bots page (synced mail)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Mail
// Server-stored copies only (GET /api/v2/mail). Bodies stay in <pre>; never md() them.
const MAIL_CHIPS = [
  ['INBOX', 'Messages'], ['UNREAD', 'Unread'], ['STARRED', 'Starred'],
];
const MAIL_LABEL = {INBOX: 'Messages', UNREAD: 'Unread', STARRED: 'Starred', SENT: 'Sent',
  IMPORTANT: 'Important', DRAFT: 'Drafts', SPAM: 'Spam', TRASH: 'Trash'};
let MAIL_LOAD = 0, MAIL_SEARCH = 0;
let MAIL_ACC = {key: '', messages: [], cursor: null};
function mailForget() { MAIL_ACC = {key: '', messages: [], cursor: null}; }
function mailParams() {
  const raw = (S.route.startsWith(MAIL) ? S.route.slice(MAIL.length) : '');
  const q = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : '');
  return {mailbox: q.get('mailbox') || '', q: q.get('q') || '', label: q.get('label') || '',
          attach: q.get('attach') === '1', thread: q.get('thread') || '', tbox: q.get('tbox') || '',
          view: q.get('view') === 'agent' ? 'agent' : 'inbox'};
}
function mailHash(st) {
  const q = new URLSearchParams();
  if (st.mailbox) q.set('mailbox', st.mailbox);
  if (st.q) q.set('q', st.q);
  if (st.label) q.set('label', st.label);
  if (st.attach) q.set('attach', '1');
  if (st.view === 'agent') q.set('view', 'agent');
  // `tbox` is which mailbox the open thread belongs to: reading across every mailbox still has to
  // survive a reload, and the list the thread was picked from may be pages away by then.
  if (st.thread) { q.set('thread', st.thread); if (st.tbox) q.set('tbox', st.tbox); }
  const s = q.toString();
  return s ? MAIL + '?' + s : MAIL;
}
function mailBoxParam(mailbox) {
  return mailbox && mailbox !== 'all' ? mailbox : '';
}
function mailFilterKey(st) {
  return [st.mailbox, st.q, st.label, st.attach ? '1' : ''].join('\0');
}
function mailGo(patch) {
  const next = mailHash({...mailParams(), ...patch});
  if (next !== S.route) location.hash = next;
  else pageMail();
}
function mailWhen(iso, epoch) {
  const d = iso ? new Date(iso) : (epoch ? new Date(epoch * 1000) : null);
  if (!d || Number.isNaN(+d)) return '';
  const same = d.toDateString() === new Date().toDateString();
  return same ? d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
              : d.toLocaleDateString([], {month: 'short', day: 'numeric'});
}
function mailFrom(m) {
  const h = (m.from_header || '').trim();
  if (h) return h.replace(/\s*<[^>]+>\s*$/, '').trim() || m.from_addr;
  return m.from_addr || '';
}
function mailLabelName(label) {
  if (MAIL_LABEL[label]) return MAIL_LABEL[label];
  if (label.startsWith('CATEGORY_')) return '';
  return label.startsWith('hub/') ? label.slice(4) : label;
}
function mailTags(labels) {
  return (labels || []).map(l => { const n = mailLabelName(l); return n ? `<span class="pill">${esc(n)}</span>` : ''; }).join('');
}
function mailFresh(box) {
  if (!box) return 'Choose a mailbox to browse the synced copy.';
  if (box.error) return `Last sync failed${box.synced_at ? ' · ' + ago(box.synced_at) : ''}.`;
  if (!box.synced_at) return `${box.address} has not been synced yet.`;
  const n = box.message_count || 0;
  return `Last synced ${ago(box.synced_at)} · ${n} message${n === 1 ? '' : 's'} on the server`;
}
function mailQuery(st, cursor) {
  const qs = new URLSearchParams();
  const mailbox = mailBoxParam(st.mailbox);
  if (mailbox) qs.set('mailbox', mailbox);
  if (st.q) qs.set('q', st.q);
  if (st.label) qs.set('label', st.label);
  if (st.attach) qs.set('has_attachment', 'true');
  qs.set('limit', '50');
  if (cursor) qs.set('cursor', cursor);
  return '/v2/mail/messages?' + qs.toString();
}
async function mailFill(st, append) {
  const page = await get(mailQuery(st, append ? MAIL_ACC.cursor : ''));
  MAIL_ACC.messages = append ? MAIL_ACC.messages.concat(page.messages || []) : (page.messages || []);
  MAIL_ACC.cursor = page.next_cursor || null;
}
function mailDrawList(st, listed, boxes) {
  if (!listed.length) {
    $('#mail-list').innerHTML = `<div class="empty">${st.q || st.label || st.attach
      ? 'Nothing matches this search.'
      : (boxes.some(b => b.synced_at) ? 'No messages in this mailbox.' : 'No synced messages yet.')}</div>`;
    return;
  }
  const filterBox = mailBoxParam(st.mailbox);
  $('#mail-list').innerHTML = listed.map(m => `<button type="button" class="mail-row${st.thread === m.thread_id && (!filterBox || filterBox === m.mailbox) ? ' on' : ''}"
      data-thread="${esc(m.thread_id)}" data-mailbox="${esc(m.mailbox)}">
      <span class="when" title="${esc(fmt(m.date))}">${esc(mailWhen(m.date, m.epoch))}</span>
      <span>
        <div class="who">${esc(mailFrom(m))}</div>
        <div class="subj">${esc(m.subject || '(no subject)')}</div>
        <div class="snip">${esc(m.snippet || '')}</div>
        <div class="tags">${mailTags(m.labels)}${m.has_attachment ? '<span class="pill">Attachment</span>' : ''}</div>
      </span></button>`).join('') +
    (MAIL_ACC.cursor ? '<div class="mail-more"><button type="button" class="ghost" data-mail-more>Load more</button></div>' : '');
}
async function mailMore() {
  const st = mailParams();
  if (!MAIL_ACC.cursor) return;
  const load = MAIL_LOAD;
  try { await mailFill(st, true); }
  catch (e) { toast(e.message, true); return; }
  if (load !== MAIL_LOAD) return;
  mailDrawList(st, MAIL_ACC.messages, []);
}
async function pageMail() {
  if (S.me && !S.me.mail_access) { location.hash = TASKS; return; }
  $('#main').classList.add('mail-layout');
  const st = mailParams();
  const load = ++MAIL_LOAD;
  if (!$('#mail-page')) {
    $('#main').innerHTML = `<div id="mail-page">
      <div class="meeting-head"><div><h1>Message bots${betaPill('message_bots')}</h1></div></div>
      <div class="mail-shell">
        <nav class="mail-boxes" aria-label="Message bots"><h2>Message bots</h2><div class="mail-box-list" id="mail-box-list"></div></nav>
        <div class="mail-main">
          <div class="mail-main-head"><h2 id="mail-current"></h2><div class="mail-tabs" role="tablist" aria-label="Mailbox view">
            <button class="mail-tab" type="button" role="tab" data-mail-view="inbox" aria-controls="mail-inbox-view">Messages</button>
            <button class="mail-tab" type="button" role="tab" data-mail-view="agent" aria-controls="mail-agent-view" hidden>Instructions</button>
          </div></div>
          <div id="mail-inbox-view" role="tabpanel">
            <div class="mail-toolbar"><input id="mail-q" type="search" autocomplete="off" placeholder="Search subject, body, or sender…" aria-label="Search messages"></div>
            <div class="mail-chips" id="mail-chips"></div>
            <p class="mail-fresh" id="mail-fresh" role="status"></p>
            <div class="mail-split" id="mail-split">
              <div class="mail-list" id="mail-list"><div class="empty">Loading…</div></div>
              <div class="mail-thread" id="mail-thread"><div class="empty">Select a message.</div></div>
            </div>
          </div>
          <section class="mail-agent-view" id="mail-agent-view" role="tabpanel" hidden></section>
        </div>
      </div>
    </div>`;
    $('#mail-box-list').onclick = ev => {
      const button = ev.target.closest('[data-mailbox]'); if (!button) return;
      mailGo({mailbox: button.dataset.mailbox, thread: '', tbox: '', view: 'inbox'});
    };
    $('.mail-tabs').onclick = ev => {
      const button = ev.target.closest('[data-mail-view]'); if (!button) return;
      mailGo({view: button.dataset.mailView});
    };
    $('.mail-tabs').onkeydown = ev => {
      if (!['ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
      const tabs = [...document.querySelectorAll('.mail-tab:not([hidden])')];
      const index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      ev.preventDefault();
      const next = tabs[(index + (ev.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      next.focus(); next.click();
    };
    $('#mail-q').oninput = () => {
      clearTimeout(MAIL_SEARCH);
      MAIL_SEARCH = setTimeout(() => mailGo({q: $('#mail-q').value.trim(), thread: ''}), 280);
    };
    $('#mail-q').onkeydown = ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); clearTimeout(MAIL_SEARCH); mailGo({q: $('#mail-q').value.trim(), thread: ''}); }
    };
    $('#mail-chips').onclick = ev => {
      const b = ev.target.closest('[data-mail-chip]'); if (!b) return;
      const cur = mailParams();
      if (b.dataset.mailChip === 'attach') mailGo({attach: !cur.attach, thread: ''});
      else mailGo({label: cur.label === b.dataset.mailChip ? '' : b.dataset.mailChip, thread: ''});
    };
    $('#mail-list').onclick = ev => {
      if (ev.target.closest('[data-mail-more]')) { mailMore(); return; }
      const row = ev.target.closest('[data-thread]'); if (!row) return;
      const cur = mailParams();
      mailGo({mailbox: cur.mailbox || row.dataset.mailbox, thread: row.dataset.thread,
              tbox: row.dataset.mailbox});
    };
    $('#mail-thread').onclick = ev => {
      if (ev.target.closest('.mail-back')) mailGo({thread: ''});
    };
  }
  if (load !== MAIL_LOAD) return;
  let boxes = [];
  try { boxes = (await get('/v2/mail/mailboxes')).mailboxes || []; }
  catch (e) { if (load === MAIL_LOAD) $('#mail-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (load !== MAIL_LOAD) return;
  const mine = (S.me?.email || '').toLowerCase();
  if (!st.mailbox && boxes.length) {
    const prefer = boxes.find(b => b.address === mine) || boxes[0];
    mailGo({mailbox: prefer.address});
    return;
  }
  const filterBox = mailBoxParam(st.mailbox);
  const box = boxes.find(b => b.address === filterBox);
  $('#mail-box-list').innerHTML = (boxes.length > 1
    ? `<button class="mail-box-link${st.mailbox === 'all' ? ' on' : ''}" type="button" data-mailbox="all"${st.mailbox === 'all' ? ' aria-current="true"' : ''}><span class="name">All message bots</span></button>` : '') +
    boxes.map(b => `<button class="mail-box-link${st.mailbox === b.address ? ' on' : ''}" type="button" data-mailbox="${esc(b.address)}" title="${esc(b.address)}"${st.mailbox === b.address ? ' aria-current="true"' : ''}>
      <span class="name">${esc(b.name || b.address)}</span>${b.agent_bot ? '<span class="agent-mark" aria-hidden="true">mail</span>' : ''}<span class="count">${esc(b.message_count || 0)}</span></button>`).join('');
  $('#mail-current').textContent = filterBox ? (box?.name ? `${box.name} · ${filterBox}` : filterBox) : 'All message bots';
  const agentTab = $('[data-mail-view="agent"]');
  agentTab.hidden = !box?.agent_instructions;
  if (st.view === 'agent' && agentTab.hidden) { mailGo({view: 'inbox'}); return; }
  document.querySelectorAll('[data-mail-view]').forEach(button => {
    const active = button.dataset.mailView === st.view;
    button.classList.toggle('on', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $('#mail-inbox-view').hidden = st.view === 'agent';
  $('#mail-agent-view').hidden = st.view !== 'agent';
  if (st.view === 'agent') {
    const target = $('#mail-agent-view');
    target.innerHTML = '<div class="empty">Loading instructions…</div>';
    try {
      const response = await get(`/v2/mail/agent?mailbox=${encodeURIComponent(filterBox)}`);
      if (load !== MAIL_LOAD) return;
      target.innerHTML = `<div class="mail-agent-meta">Current AGENT.md · updated ${esc(ago(response.updated_at))}</div>
        <pre class="mail-agent-text">${esc(response.instructions)}</pre>`;
    } catch (e) {
      if (load === MAIL_LOAD) target.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
    return;
  }
  const qbox = $('#mail-q'); if (qbox && qbox.value !== st.q) qbox.value = st.q;
  $('#mail-chips').innerHTML = MAIL_CHIPS.map(([id, label]) =>
    `<button type="button" class="mail-chip${st.label === id ? ' on' : ''}" data-mail-chip="${id}">${esc(label)}</button>`).join('') +
    `<button type="button" class="mail-chip${st.attach ? ' on' : ''}" data-mail-chip="attach">Has attachment</button>`;
  const freshBox = filterBox ? box : boxes.filter(b => b.synced_at).sort((a, b) => (b.synced_at || '').localeCompare(a.synced_at || ''))[0];
  $('#mail-fresh').textContent = boxes.some(b => b.synced_at)
    ? (filterBox ? mailFresh(box || {address: filterBox}) : `Showing every mailbox you can see · newest sync ${ago(freshBox.synced_at)}`)
    : 'No messages have been synced yet.';
  const key = mailFilterKey(st);
  if (MAIL_ACC.key !== key) MAIL_ACC = {key, messages: [], cursor: null};
  if (!MAIL_ACC.messages.length) {
    try { await mailFill(st, false); }
    catch (e) { if (load === MAIL_LOAD) $('#mail-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  }
  if (load !== MAIL_LOAD) return;
  const listed = MAIL_ACC.messages;
  const split = $('#mail-split');
  if (split) split.classList.toggle('thread-open', !!st.thread);
  mailDrawList(st, listed, boxes);
  if (!st.thread) {
    $('#mail-thread').innerHTML = '<div class="empty">Select a message to read the thread.</div>';
    return;
  }
  $('#mail-thread').innerHTML = '<div class="empty">Loading thread…</div>';
  try {
    const t = await get(`/v2/mail/threads/${encodeURIComponent(st.thread)}?mailbox=${encodeURIComponent(filterBox || st.tbox || listed.find(m => m.thread_id === st.thread)?.mailbox || '')}`);
    if (load !== MAIL_LOAD) return;
    const subject = (t.messages[0] && t.messages[0].subject) || '(no subject)';
    $('#mail-thread').innerHTML = `<button type="button" class="mail-back">← Messages</button>
      <h2>${esc(subject)}</h2>
      ${t.messages.map(m => `<article class="mail-msg">
        <div class="meta"><span class="who">${esc(mailFrom(m))}</span>
          <span class="muted">${esc(m.from_addr)}</span>
          <span class="muted tnum" title="${esc(fmt(m.date))}">${esc(fmt(m.date) || mailWhen(m.date, m.epoch))}</span></div>
        <div class="tags" style="margin-bottom:8px">${mailTags(m.labels)}${(m.rule_hits || []).map(h => `<span class="pill">${esc(h.id || 'rule')}</span>`).join('')}</div>
        <pre class="mail-body">${esc(m.body || '')}</pre>
        ${m.body_truncated ? '<p class="muted" style="margin:6px 0 0">Body was truncated when synced.</p>' : ''}
        ${(m.attachments || []).length ? `<p class="muted" style="margin:8px 0 0">${m.attachments.map(a => esc(a.name || a.filename || 'attachment')).join(' · ')}</p>` : ''}
      </article>`).join('')}`;
  } catch (e) {
    if (load === MAIL_LOAD) $('#mail-thread').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
