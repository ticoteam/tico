/* ui/app/messaging.js — Message bot review page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- Message bot review
// Bots are the primary unit. Provider messages remain evidence behind a run or action.
let MESSAGING_LOAD = 0, MESSAGING_LIST = null;
function messagingParams() {
  const raw = S.route.startsWith(MESSAGING) ? S.route.slice(MESSAGING.length) : '';
  const q = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : '');
  return {bot: q.get('bot') || '', source: q.get('source') || ''};
}
function messagingGo(patch) {
  const state = {...messagingParams(), ...patch}, q = new URLSearchParams();
  if (state.bot) q.set('bot', state.bot);
  if (state.source) q.set('source', state.source);
  const next = MESSAGING + (q.size ? '?' + q.toString() : '');
  if (next !== S.route) location.hash = next;
  else pageMessaging();
}
function messagingLabel(m, bot) {
  const labels = m.labels || [];
  if (labels.includes('hub/handled/' + bot)) return 'Handled by ' + bot;
  if (labels.includes('hub/triaged/' + bot)) return 'Triaged by ' + bot;
  if (labels.some(l => l.startsWith('hub/needs-'))) return 'Marked for review';
  if (labels.includes('hub/drafted')) return 'Drafted';
  return 'Source message';
}
function messagingEvidence(data) {
  const bot = data.bot.bot;
  const events = [
    ...(data.mail || []).map(m => ({kind: 'mail', at: +(new Date(m.date)) || m.epoch * 1000,
      html: `<button class="msg-evidence" type="button" data-msg-mail="${esc(m.thread_id)}" data-msg-mailbox="${esc(m.mailbox)}">
        <span class="msg-evidence-top"><strong>${esc(m.subject || '(no subject)')}</strong><span class="spacer"></span><span class="muted">${esc(mailWhen(m.date, m.epoch))}</span></span>
        <span class="msg-evidence-meta"><span>Email · ${esc(m.mailbox)}</span><span>${esc(mailFrom(m))}</span><span>${esc(messagingLabel(m, bot))}</span></span>
        <div class="preview">${esc(m.snippet || '')}</div><div class="tags">${mailTags(m.labels)}${(m.rule_hits || []).map(h => `<span class="pill">Rule ${esc(h.id || 'matched')}</span>`).join('')}</div>
      </button>`})),
    ...(data.slack || []).map(m => ({kind: 'slack', at: Number(m.ts) * 1000,
      html: `<button class="msg-evidence" type="button" data-msg-slack="${esc(m.thread_ts)}" data-msg-channel="${esc(m.channel)}">
        <span class="msg-evidence-top"><strong>${esc(m.author)}</strong><span class="spacer"></span><span class="muted">${esc(mailWhen('', Number(m.ts)))}</span></span>
        <span class="msg-evidence-meta"><span>Slack · ${esc(data.bot.sources.find(s => s.id === 'slack:' + m.channel)?.name || m.channel)}</span><span>Channel message</span></span>
        <div class="preview">${esc(m.text || '')}</div>
      </button>`})),
    ...(data.slack_posts || []).map(p => ({kind: 'post', at: +(new Date(p.created_at)) || 0,
      html: `<button class="msg-evidence" type="button" data-msg-slack="${esc(p.thread_ts)}" data-msg-channel="${esc(p.channel)}">
        <span class="msg-evidence-top"><strong>Bot reply in ${esc(data.bot.sources.find(s => s.id === 'slack:' + p.channel)?.name || p.channel)}</strong><span class="spacer"></span><span class="muted">${esc(ago(p.created_at))}</span></span>
        <span class="msg-evidence-meta"><span>Slack action · ${esc(p.state)}</span>${p.error ? `<span>${esc(p.error)}</span>` : ''}</span>
        <div class="preview">${esc(p.text || '')}</div>
      </button>`})),
  ];
  events.sort((a, b) => b.at - a.at);
  return events.length ? events.slice(0, 70).map(e => e.html).join('') : '<div class="empty">No synced messages for these sources yet.</div>';
}
// The Slack checks this bot ran; its schedules and other triggers are automations (#/automations, searched by its name).
function messagingRuns(data) {
  return (data.slack_digests || []).map(d => `<div class="msg-run">
    <div class="msg-run-top"><strong>Slack channel check</strong><span class="spacer"></span><span class="muted">${esc(ago(d.created_at))}</span></div>
    <div class="msg-run-meta"><span>${esc(d.channels.join(' · '))}</span><span>${d.count} message${d.count === 1 ? '' : 's'}</span>${d.skipped ? `<span>${d.skipped} skipped in high volume</span>` : ''}<span>${esc(d.attempt?.state || 'Queued')}</span></div>
    <button class="ghost" type="button" data-msg-digest="${esc(d.id)}" style="margin-top:8px">View messages reviewed</button>
  </div>`).join('');
}
function messagingDetail(data) {
  const b = data.bot, state = messagingParams();
  const sources = `<button type="button" class="msg-source${state.source ? '' : ' on'}" data-msg-source="">All sources</button>` +
    b.sources.map(s => `<button type="button" class="msg-source${state.source === s.id ? ' on' : ''}" data-msg-source="${esc(s.id)}" title="${esc(s.purpose || s.error || s.name)}">${s.kind === 'email' ? '✉' : '#'} ${esc(s.name.replace(/^#/, ''))}</button>`).join('');
  const sourceControl = b.sources.length > 8
    ? `<div class="msg-source-picker"><select id="msg-source-select" aria-label="Choose a messaging source"><option value="">All ${b.sources.length} sources</option>${b.sources.map(s => `<option value="${esc(s.id)}"${state.source === s.id ? ' selected' : ''}>${s.kind === 'email' ? 'Email' : 'Slack'} · ${esc(s.name)}</option>`).join('')}</select><details><summary>Show all sources</summary><div class="msg-sources" aria-label="Sources covered by ${esc(b.name)}">${sources}</div></details></div>`
    : `<div class="msg-sources" aria-label="Sources covered by ${esc(b.name)}">${sources}</div>`;
  const shownSources = state.source ? b.sources.filter(s => s.id === state.source) : b.sources.length > 8 ? [] : b.sources;
  const checks = messagingRuns(data);
  $('#msg-detail').innerHTML = `<div class="msg-workspace">
    <div class="msg-config">
      <div class="msg-detail-head"><div><h1>${esc(b.name)}</h1><div class="muted">${esc(b.description || b.bot)}</div></div>
        <div class="msg-actions"><a class="ghost" href="${AUTOMATIONS}?q=${encodeURIComponent(b.name)}" data-msg-automations>Automations</a><a class="ghost" href="#/bot/${encodeURIComponent(b.bot)}">Bot settings</a>${b.can_manage && ['active', 'paused'].includes(b.state) ? `<button class="ghost" type="button" data-msg-pause="${esc(b.bot)}">${b.state === 'active' ? 'Pause bot' : 'Resume bot'}</button>` : ''}</div></div>
      ${sourceControl}
      <section class="msg-panel" aria-labelledby="msg-instructions-title">
        <div class="msg-panel-title"><h2 id="msg-instructions-title">Instructions</h2><div class="muted">${data.instructions.published ? 'Published by its computer' + (data.instructions.updated_at ? ' · ' + ago(data.instructions.updated_at) : '') : 'Published summary; its instructions have not been reported yet'}</div></div>
        <div class="msg-panel-body">
          <div class="msg-facts"><span><strong>${b.sources.length}</strong> source${b.sources.length === 1 ? '' : 's'}</span><span>Last run <strong>${b.last_run ? esc(ago(b.last_run)) : 'not recorded'}</strong></span><span>Next <strong>${b.next_run ? esc(fmt(b.next_run)) : b.slack_interval_minutes ? `Slack every ${b.slack_interval_minutes} minutes when new` : 'not scheduled'}</strong></span></div>
          ${shownSources.length ? `<div class="msg-facts" style="margin-top:10px">${shownSources.map(s => `<span>${esc(s.name)}${s.kind === 'email' ? ` · ${s.error ? 'sync error' : s.synced_at ? 'synced ' + ago(s.synced_at) : 'not synced'}` : ` · ${s.post ? 'can post' : 'reads only'}`}</span>`).join('')}</div>` : ''}
          <pre>${esc(data.instructions.content || 'No instructions have been published.')}</pre>
        </div>
      </section>
      ${checks ? `<section class="msg-section msg-scheduled"><h2>Slack checks</h2>${checks}</section>` : ''}
    </div>
    <section class="msg-example-inbox" aria-labelledby="msg-examples-title">
      <header class="msg-example-head"><h2 id="msg-examples-title">Example messages</h2></header>
      ${messagingEvidence(data)}
    </section>
  </div>`;
}
async function messagingOpenMail(mailbox, thread) {
  const d = $('#msg-message-dialog'), body = $('#msg-message-body');
  $('#msg-message-title').textContent = 'Email thread'; body.innerHTML = '<div class="empty">Loading…</div>'; d.showModal();
  try {
    const data = await get(`/v2/mail/threads/${encodeURIComponent(thread)}?mailbox=${encodeURIComponent(mailbox)}`);
    $('#msg-message-title').textContent = data.messages?.[0]?.subject || 'Email thread';
    body.innerHTML = (data.messages || []).map(m => `<article class="mail-msg"><div class="meta"><strong>${esc(mailFrom(m))}</strong><span class="muted">${esc(fmt(m.date))}</span></div>
      <div class="tags">${mailTags(m.labels)}${(m.rule_hits || []).map(h => `<span class="pill">Rule ${esc(h.id || 'matched')}</span>`).join('')}</div>
      <pre>${esc(m.body || '')}</pre></article>`).join('') || '<div class="empty">No messages.</div>';
  } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function messagingOpenSlack(bot, channel, thread) {
  const d = $('#msg-message-dialog'), body = $('#msg-message-body');
  $('#msg-message-title').textContent = 'Slack thread'; body.innerHTML = '<div class="empty">Loading…</div>'; d.showModal();
  try {
    const qs = new URLSearchParams({channel, thread_ts: thread});
    const data = await get(`/v2/messaging/bots/${encodeURIComponent(bot)}/slack-thread?${qs}`);
    body.innerHTML = (data.messages || []).map(m => `<article class="mail-msg"><div class="meta"><strong>${esc(m.author)}</strong><span class="muted">${esc(mailWhen('', Number(m.ts)))}</span><a href="${esc(m.url)}" target="_blank" rel="noopener">Open in Slack</a></div>
      <pre>${esc(m.deleted ? '(message removed)' : m.text)}</pre></article>`).join('');
  } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function messagingOpenDigest(bot, digest) {
  const d = $('#msg-message-dialog'), body = $('#msg-message-body');
  $('#msg-message-title').textContent = 'Slack check'; body.innerHTML = '<div class="empty">Loading…</div>'; d.showModal();
  try {
    const data = await get(`/v2/messaging/bots/${encodeURIComponent(bot)}/slack-digests/${encodeURIComponent(digest)}`);
    body.innerHTML = `<p class="muted">${data.count} message${data.count === 1 ? '' : 's'} delivered to the bot${data.skipped ? ` · ${data.skipped} skipped in high volume` : ''} · ${esc(fmt(data.created_at))}</p>` +
      ((data.messages || []).map(m => `<article class="mail-msg"><div class="meta"><strong>${esc(m.author)}</strong><span class="muted">${esc(mailWhen('', Number(m.ts)))}</span><a href="${esc(m.url)}" target="_blank" rel="noopener">Open in Slack</a></div><pre>${esc(m.deleted ? '(message removed)' : m.text)}</pre></article>`).join('') || '<div class="empty">The delivered message records are unavailable.</div>');
  } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function pageMessaging() {
  if (S.me && !S.me.mail_access && S.me.role !== 'owner') { location.hash = TASKS; return; }
  $('#main').classList.add('messaging-layout');
  const load = ++MESSAGING_LOAD;
  if (!$('#message-bot-page')) {
    $('#main').innerHTML = `<div id="message-bot-page"><div class="msg-detail" id="msg-detail"><div class="empty">Loading message bot…</div></div>
      <dialog class="msg-message-dialog" id="msg-message-dialog"><div class="msg-message-head"><strong id="msg-message-title"></strong><button class="ghost" type="button" id="msg-message-close" aria-label="Close message">×</button></div><div class="msg-message-body" id="msg-message-body"></div></dialog></div>`;
    $('#msg-message-close').onclick = () => $('#msg-message-dialog').close();
    $('#msg-detail').onclick = async ev => {
      const source = ev.target.closest('[data-msg-source]'); if (source) { messagingGo({source: source.dataset.msgSource}); return; }
      const bot = messagingParams().bot;
      const mail = ev.target.closest('[data-msg-mail]'); if (mail) { messagingOpenMail(mail.dataset.msgMailbox, mail.dataset.msgMail); return; }
      const slack = ev.target.closest('[data-msg-slack]'); if (slack) { messagingOpenSlack(bot, slack.dataset.msgChannel, slack.dataset.msgSlack); return; }
      const digest = ev.target.closest('[data-msg-digest]'); if (digest) { messagingOpenDigest(bot, digest.dataset.msgDigest); return; }
      const pause = ev.target.closest('[data-msg-pause]');
      if (!pause) return;
      pause.disabled = true;
      try {
        const current = MESSAGING_LIST.bots.find(b => b.bot === bot);
        await post(`/v2/bots/${encodeURIComponent(bot)}/definition`, {status: current.state === 'active' ? 'paused' : 'active', expected_revision: current.revision});
        MESSAGING_LIST = null;
        pageMessaging();
      } catch (e) { toast(e.message, true); pause.disabled = false; }
    };
    $('#msg-detail').onchange = ev => {
      if (ev.target.id === 'msg-source-select') messagingGo({source: ev.target.value});
    };
  }
  try { if (!MESSAGING_LIST) MESSAGING_LIST = await get('/v2/messaging/bots'); }
  catch (e) { if (load === MESSAGING_LOAD) $('#msg-detail').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (load !== MESSAGING_LOAD) return;
  let state = messagingParams();
  if (!state.bot && MESSAGING_LIST.bots.length) { messagingGo({bot: MESSAGING_LIST.bots[0].bot}); return; }
  if (state.bot && !MESSAGING_LIST.bots.some(b => b.bot === state.bot)) { messagingGo({bot: MESSAGING_LIST.bots[0]?.bot || '', source: ''}); return; }
  if (!state.bot) { $('#msg-detail').innerHTML = '<div class="empty">No bots are assigned to a visible messaging source.</div>'; return; }
  $('#msg-detail').innerHTML = '<div class="empty">Loading bot activity…</div>';
  try {
    const query = state.source ? `?source=${encodeURIComponent(state.source)}` : '';
    const detail = await get(`/v2/messaging/bots/${encodeURIComponent(state.bot)}${query}`);
    if (load !== MESSAGING_LOAD) return;
    messagingDetail(detail);
  } catch (e) {
    if (load === MESSAGING_LOAD) $('#msg-detail').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
