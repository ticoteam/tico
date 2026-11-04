/* The Tico team chat: one ongoing conversation with the Tico team (docs/support.md). The ? beside Search opens it
   (#support-open); so do a #/support link, Report this, Want help setting up? and old #/help links.
   Underneath it rides the ticket transport (backend/support.py): every one of the person's tickets is drawn as one
   conversation in time order, and a message goes to the newest ticket still open, or starts a new one with it. The
   person never sees a ticket, a status or a form. The draft stays on this page's account in memory; an attachment is an
   immutable server preview, never silently replaced when Send is pressed. */
'use strict';
(function () {
  'use strict';
  const API_BASE = () => (typeof API === 'string' ? API : '/api') + '/v2';
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const say = (msg, error, action) => typeof toast === 'function' && toast(msg, error, action);
  // randomUUID exists only in a secure context; a Tico served over plain http on a LAN still needs keys.
  const uid = () => crypto.randomUUID?.() || [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, '0')).join('');
  const NAME = 'Tico team';
  const browserFailures = [];
  function browserFailure(kind, event) {
    // No error message, rejection value, URLs, stack text or user input enters diagnostics.
    let file = '';
    try { file = new URL(event?.filename || '', location.href).pathname.split('/').pop(); } catch (_) {}
    const item = {kind, at: new Date().toISOString(), file: /^[A-Za-z0-9_-]+\.js$/.test(file) ? file : '',
      line: Math.max(0, Math.min(10000000, event?.lineno || 0)), column: Math.max(0, Math.min(10000000, event?.colno || 0)), count: 1};
    const prior = browserFailures.find(x => x.kind === item.kind && x.file === item.file && x.line === item.line && x.column === item.column);
    if (prior) { prior.at = item.at; prior.count = Math.min(1000000, prior.count + 1); }
    else { browserFailures.push(item); if (browserFailures.length > 20) browserFailures.shift(); }
  }
  window.addEventListener('error', e => browserFailure('error', e));
  window.addEventListener('unhandledrejection', () => browserFailure('unhandledrejection'));
  // Inside the desktop app: its own version, apart from the server's (app/src/main.rs bridge_script).
  const appVersion = () => typeof window.ticoNativeVersion === 'string' ? window.ticoNativeVersion : '';
  // The app's version and the end of its log file, from a shell that keeps one; anything else leaves it out.
  async function appShell() {
    if (!(window.ticoNativeFeatures || []).includes('appLog') || typeof window.ticoNativeLog !== 'function') return null;
    try {
      const got = await window.ticoNativeLog();
      return {version: String(got?.version || '').slice(0, 40), lines: (Array.isArray(got?.lines) ? got.lines : []).slice(-200).map(x => String(x).slice(0, 2000)),
        mac: /Macintosh|Mac OS X/.test(navigator.userAgent)};
    } catch (_) { return null; }
  }
  // `hours` of each computer's performance history go with the attachment (0: none); `report` is what Report this or
  // Setup check attached (backend/support.py Report).
  async function capture(hours, report) {
    const app = await appShell();
    return browserFailures.length || app || report
      ? call('POST', '/support/diagnostics/capture', {browser: browserFailures, performance_hours: hours, ...(app ? {app} : {}), ...(report ? {report} : {})})
      : call('GET', '/support/diagnostics?performance_hours=' + hours);
  }
  const REPORTED = {toast: 'Error', run: 'Failed run', health: 'Health', stuck: 'Stuck bot', setup_check: 'Setup check'};
  const prefill = report => REPORTED[report.kind] + ': details attached. ';
  // The message without any kind's untouched prefill: a new report or Remove replaces it rather than stacking on it.
  const unprefilled = message => { const kind = Object.values(REPORTED).find(k => message.startsWith(k + ': details attached. ')); return kind ? message.slice(kind.length + 20) : message; };
  // The same problem gets the same group wherever it is reported: numbers and quoted names don't split it.
  const group = (kind, words) => (kind + ':' + String(words || '').replace(/(["'“‘]).*?["'”’]/g, '"…"').replace(/\d+/g, '#').replace(/\s+/g, ' ').trim()).slice(0, 120);
  const clip = (s, n) => String(s ?? '').replace(/\s+/g, ' ').trim().slice(0, n);
  function asReport(r) {
    if (!r || !REPORTED[r.kind]) return null;
    const out = {kind: r.kind, title: clip(r.title, 200), detail: String(r.detail ?? '').slice(0, 2000), ref: clip(r.ref, 120)};
    out.group = clip(r.group, 120) || group(r.kind, r.title);
    if (Array.isArray(r.checks)) out.checks = r.checks.slice(0, 40).map(c => ({name: clip(c.name, 120) || 'Check', status: ['ok', 'bad', 'warn', 'info'].includes(c.status) ? c.status : 'info',
      detail: clip(c.detail, 600), fix: String(c.fix ?? '').slice(0, 1200), source: c.source === 'health' ? 'health' : 'doctor'}));
    return out;
  }
  // Report this pressed anywhere: the chat opens with the report attached as diagnostics.
  let pending = null;
  const PERF_WINDOWS = [[0, 'None'], [24, '1 day'], [168, '7 days'], [336, '14 days']];
  const ICON = {
    // The composers' paperclip (app/pill.js ICON_CLIP), drawn here because this file loads first.
    clip: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"/></svg>',
    send: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 12h15M13 6l6 6-6 6"/></svg>',
  };
  const AVATAR = '<span class="support-av" aria-hidden="true"><span class="nav-icon">headset_mic</span></span>';
  let state = {enabled: null, tickets: [], unread: 0}, pop = null, timer, busyRefresh = null, stateRevision = 0;
  // `inFlight`: the send on its way, settled either way; `deleting`: Delete chat history running.
  let inFlight = null, deleting = false;
  // `lastUnread`: the unread replies this tab has been told of. A rise above it opens the chat once (announceReply).
  let owner = '', lastUnread = 0, announceWaiting = false, compose = null, editor = null, opener = null, loading = null, sending = null;
  // The one draft: what is in the box and what goes with it. `kind` is only for a message that starts a new ticket.
  const newDraft = () => ({message: '', diag: false, perf: 24, bundle: null, key: uid(), kind: 'support', report: null});
  let draft = newDraft();
  const when = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}); };
  const dayOf = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toDateString(); };
  function dayLabel(iso) {
    const d = new Date(iso), today = new Date(), yesterday = new Date(Date.now() - 86400000);
    if (d.toDateString() === today.toDateString()) return 'Today';
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
    return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(d.getFullYear() === today.getFullYear() ? {} : {year: 'numeric'})});
  }
  const last = t => (t.messages || []).at(-1)?.created_at || t.created_at;
  const newestFirst = list => [...list].sort((a, b) => (Date.parse(last(b)) || 0) - (Date.parse(last(a)) || 0));
  const ended = t => ['closed', 'gone'].includes(t.status);
  // Where a message goes: the ticket written on most recently that is still open. None: a new one.
  const latest = () => newestFirst(state.tickets).find(t => !ended(t));
  const isOpen = () => !!pop?.open;
  async function call(method, path, body, key) {
    const r = await fetch(API_BASE() + path, {method, cache: 'no-store',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'Idempotency-Key': key || uid()},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.error?.detail || data.error?.message || (typeof data.detail === 'string' ? data.detail : '') || 'Could not reach the Tico team. Try again.'), {status: r.status, code: data.error?.code || ''});
    return data;
  }
  // The ? beside Search: gone where support is off, a dot on it while a reply is unread.
  function mark() {
    const n = state.unread, b = document.getElementById('support-open');
    if (b) {
      const off = state.enabled === false;
      if (b.hidden !== off) b.hidden = off;
      if (b.classList.contains('has-unread') !== !!n) b.classList.toggle('has-unread', !!n);
      const label = n ? `${NAME}, ${n} new` : NAME;
      if (b.getAttribute('aria-label') !== label) b.setAttribute('aria-label', label);
    }
    document.dispatchEvent(new CustomEvent('tico:support'));
  }
  // The unread replies on the person's own tickets: the ? counts them and they open the chat. A bot's report is not in
  // the chat at all, so its replies are not counted (backend/support.py OWN).
  function accept(data) {
    state = {enabled: data.enabled !== false, tickets: data.tickets || [], unread: data.unread || 0};
    if (state.unread > lastUnread) announceWaiting = true;
    lastUnread = state.unread;
    mark();
    announceReply();
    if (!isOpen()) return;
    render();
    // A reply that lands while the chat is in front of the person is read; while it is still opening, open() reads.
    if (!loading && state.tickets.some(t => t.unread) && document.hasFocus()) void readVisible();
  }
  // The chat opens by itself for a new reply on one of the person's own tickets, once. Never over anything the person
  // is in: a field they are typing in, a dialog, the Ask panel, the phone's drawer, Needs you, a hidden tab. Then it
  // waits, and opens when that ends: focus leaving a field, a dialog closing, the tab coming back, or the next check.
  const typing = el => !!el && (el.matches?.('input, textarea, select') || el.isContentEditable === true);
  const occupied = () => document.hidden || typing(document.activeElement) || !!document.querySelector('dialog[open], .needs-you')
    || document.body.classList.contains('ask-open') || document.body.classList.contains('drawer');
  function announceReply() {
    if (!announceWaiting) return;
    if (isOpen() || !state.unread) { announceWaiting = false; return; }
    if (occupied()) return;
    announceWaiting = false;
    void open();
  }
  function note(message, error = false) {
    const el = pop?.querySelector('[data-connection]');
    if (el) { el.textContent = message; el.classList.toggle('err', error); }
  }
  const POLL_FAILED = 'Could not check for replies just now. Tico will try again.';
  async function refresh() {
    if (busyRefresh) return busyRefresh;
    busyRefresh = (async () => {
      const revision = stateRevision;
      try {
        // The conversation in view is asked about every time; the server paces the rest by how recently each was written on.
        const shown = isOpen() ? latest()?.id : '';
        const data = await call('POST', '/support/tickets/refresh', shown ? {ticket: shown} : undefined);
        // A refresh started before a send or read settled can return an older list. Keep the local change; the next
        // refresh reconciles it with the server.
        if (revision === stateRevision) accept(data);
        note(data.refresh_failed ? POLL_FAILED : '', !!data.refresh_failed);
      } catch (_) { note(POLL_FAILED, true); }      // what was loaded before stays on screen
      finally { busyRefresh = null; }
      return state;
    })();
    return busyRefresh;
  }
  // Every 30 seconds while the chat is open, otherwise every 5 minutes, plus whenever the app comes back into focus.
  function schedule() {
    clearTimeout(timer);
    if (!state.enabled) return;
    timer = setTimeout(async () => {
      if (!document.hidden && state.tickets.some(t => !ended(t))) await refresh();
      schedule();
    }, isOpen() ? 30000 : 300000);
  }
  // Every reply on screen is read: the chat shows them all.
  async function readVisible() {
    if (document.hidden || !isOpen()) return;
    const unread = state.tickets.filter(t => t.unread);
    if (!unread.length) return;
    const revision = stateRevision;
    try {
      const read = await Promise.all(unread.map(t => call('POST', `/support/tickets/${encodeURIComponent(t.id)}/read`)));
      if (revision !== stateRevision) return;
      stateRevision++;
      state.tickets = state.tickets.map(x => read.find(n => n.id === x.id) || x);
      state.unread = state.tickets.reduce((n, t) => n + (t.unread || 0), 0);
      lastUnread = state.unread; mark(); render();
    } catch (_) { /* unread until the chat is opened again */ }
  }
  // While a message is on its way nothing that could send it again or change it can be pressed, however often the
  // chat is closed and reopened: every render locks what it draws until the send settles.
  const LOCKED = '[data-compose] :is(textarea, button)';
  const lockSending = (on = true) => {
    if (!pop || (on && !sending && !deleting)) return;
    if (!on && deleting) return;
    pop.querySelectorAll(LOCKED).forEach(el => { el.disabled = on; });
    // Delete chat history waits for a message on its way: what it repopulates would be left behind.
    const yes = pop.querySelector('[data-confirm-yes]');
    if (yes && !deleting) yes.disabled = on && !!sending;
    const submit = pop.querySelector('[data-compose] [type=submit]');
    if (on) submit?.setAttribute('aria-busy', 'true'); else submit?.removeAttribute('aria-busy');
  };
  const settled = () => { sending = null; lockSending(false); };
  function render(pin = false) {
    if (!pop) return;
    const off = state.enabled === false, known = state.enabled !== null;
    pop.querySelector('[data-loading]').hidden = known || !loading;
    pop.querySelector('[data-off]').hidden = !off;
    const asking = !pop.querySelector('[data-confirm]').hidden;
    pop.querySelector('[data-view]').hidden = !known || off || asking;
    pop.querySelector('[data-delete]').hidden = !known || off || !state.tickets.length;
    if (!known || off) return;
    renderThread(pin); renderAccess(); lockSending();
  }
  // Every ticket's messages as one conversation, oldest first. A ticket's first message is the person's.
  function lines() {
    const out = [];
    for (const t of state.tickets) {
      out.push({body: t.message, mine: true, at: t.created_at, diag: !!t.sent?.diagnostics});
      for (const m of t.messages || []) out.push({body: m.body, mine: m.from !== 'staff', at: m.created_at, diag: !!m.has_diagnostics,
        name: m.from === 'staff' ? clip(m.author_name || m.author || '', 60) || NAME : ''});
    }
    return out.map((r, i) => ({...r, i})).sort((a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0) || a.i - b.i);
  }
  // Bubbles: theirs on the left (the writer's name above when it changes), the person's on the right; a date line
  // where the day changes.
  function renderThread(pin = false) {
    const host = pop.querySelector('[data-thread]');
    let html = '', day = '', who = null;
    for (const r of lines()) {
      const d = dayOf(r.at);
      if (d && d !== day) { html += `<div class="support-day" role="separator">${escape(dayLabel(r.at))}</div>`; day = d; who = null; }
      const time = `title="${escape(when(r.at))}"`;
      if (r.mine) {
        html += `<div class="support-msg you" ${time}><div class="support-body">${escape(r.body)}</div>${r.diag ? '<small class="support-note">Diagnostics attached</small>' : ''}</div>`;
        who = null;
      } else {
        html += `<div class="support-msg them" ${time}>${who !== r.name ? `<small class="support-who">${escape(r.name)}</small>` : ''}<div class="support-body">${escape(r.body)}</div></div>`;
        who = r.name;
      }
    }
    // Stays on the newest message only while the reader is there, so a poll never moves someone reading back
    // (ui/app/stick-scroll.js); a reply that lands while they are scrolled up shows the "New reply" pill instead.
    const stick = stickScroll(host);
    if (pin) stick.toBottom();
    if (host.dataset.shown !== html) {
      const fresh = !pin && !!host.dataset.shown;
      stick.update(() => { host.innerHTML = html; host.dataset.shown = html; }, {fresh});
    }
  }
  // Letting the Tico team in (docs/support.md): a banner on every screen while a session lasts; over the box, the
  // button that starts one from the open conversation, for an owner or admin; and what the team did in each session.
  let access = {available: false, active: null, sessions: [], at: 0}, accessTimer = 0, accessRevision = 0;
  const accessLogs = new Map();
  const TIERS = {app: 'App only', full: 'Full access'};
  const remaining = () => access.active ? access.active.seconds_left - (Date.now() - access.at) / 1000 : 0;
  const timeLeft = () => {
    const m = Math.max(1, Math.ceil(remaining() / 60)), h = Math.floor(m / 60);
    return h ? `${h} h ${m % 60} min` : `${m} min`;
  };
  const canStop = () => !!access.active && (!!access.can_start || access.active.started_by === 'human:' + (typeof S === 'undefined' ? '' : S.me?.id || ''));
  // The same words on the banner and in the chat: the tier and the time left.
  const accessWords = live => `${NAME} has access (${TIERS[live.tier] || live.tier}), ${timeLeft()} left`;
  const liveAccess = () => access.active && remaining() > 0 ? access.active : null;
  function renderBanner() {
    const bar = document.getElementById('support-access-banner'), live = liveAccess();
    if (!bar) return;
    bar.hidden = !live;
    if (!live) return;
    bar.querySelector('[data-access-left]').textContent = accessWords(live);
    bar.querySelector('[data-access-stop]').hidden = !canStop();
  }
  async function loadLog(id) {
    const held = accessLogs.get(id);
    if (held && (held.busy || Date.now() - held.at < 30000)) return;
    accessLogs.set(id, {...held, busy: true, at: Date.now()});
    try { accessLogs.set(id, {at: Date.now(), items: (await call('GET', `/support/access/${encodeURIComponent(id)}/log`)).log || []}); }
    catch (_) { accessLogs.set(id, {at: Date.now(), items: held?.items || []}); }
    renderAccess();
  }
  // Over the box: a live session with Stop, else the start button (an open conversation, an owner or admin, no session
  // live), or why it is off; then what the team did in every session started from this chat, newest first.
  function renderAccess() {
    const host = pop?.querySelector('[data-access]'), t = latest();
    if (!host) return;
    const live = liveAccess(), mine = new Set(state.tickets.map(x => x.id)), here = live && mine.has(live.ticket_id) ? live : null;
    let html = '';
    if (here) html = `<p class="support-access-on">${escape(accessWords(here))}${canStop() ? ' · <button type="button" class="ghost" data-access-stop>Stop</button>' : ''}</p>`;
    else if (t && access.available && access.can_start && !access.active)
      html = `<button type="button" class="linkish" data-access-start>Let the ${NAME} in</button>`;
    const sessions = (access.sessions || []).filter(x => mine.has(x.ticket_id));
    if (sessions.length) {
      for (const x of sessions) if (!accessLogs.has(x.id) || x.id === here?.id) void loadLog(x.id);
      const items = sessions.flatMap(x => accessLogs.get(x.id)?.items || []).sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
      html += `<details class="support-access-log"${here ? ' open' : ''}><summary>What the ${NAME} did</summary>${items.length
        ? `<ol>${items.map(x => `<li><time datetime="${escape(x.created_at)}" data-ago="${escape(x.created_at)}" title="${escape(fmt(x.created_at))}">${escape(ago(x.created_at))}</time> ${escape(x.by === 'support-agent' ? 'Support Agent' : 'Staff')} · ${escape(x.method)} ${escape(x.path)} · ${escape(x.status)}</li>`).join('')}</ol>`
        : '<p class="support-note">Nothing yet.</p>'}</details>`;
    }
    host.hidden = !html;
    if (host.dataset.shown !== html) {
      // Opened or closed by hand, the log stays so.
      const open = host.querySelector('details')?.open;
      host.innerHTML = html; host.dataset.shown = html;
      if (open !== undefined && host.querySelector('details')) host.querySelector('details').open = open;
    }
  }
  async function pollAccess() {
    clearTimeout(accessTimer);
    if (typeof S !== 'undefined' && S.me && !document.hidden) {
      const revision = ++accessRevision;
      try {
        const data = await call('GET', '/support/access');
        if (revision === accessRevision) access = {...data, at: Date.now()};    // an older answer never undoes a Stop
      } catch (_) { /* the next poll asks again */ }
      renderBanner(); renderAccess();
    }
    // Every 30 s while the team is in, for the time left; otherwise every 5 minutes (access is given from this app, and
    // a tab that gives it asks again at once).
    accessTimer = setTimeout(pollAccess, typeof S !== 'undefined' && S.me ? (access.active ? 30000 : 300000) : 2000);
  }
  async function stopAccess() {
    if (!access.active) return;
    try {
      const stopped = await call('POST', `/support/access/${encodeURIComponent(access.active.id)}/stop`, {});
      // Show it ended at once, on the banner and in the chat, whatever a poll still in flight answers.
      accessRevision++;
      access = {...access, active: null, sessions: (access.sessions || []).map(x => x.id === stopped?.id ? stopped : x)};
      renderBanner(); renderAccess();
      say(`The ${NAME} no longer has access.`);
    } catch (e) { say(e.message, true); }
    await pollAccess();
  }
  function openAccess(t) {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal support-modal'; dialog.setAttribute('aria-label', `Let the ${NAME} in`);
    dialog.innerHTML = `<div class="tmodal-head"><strong>Let the ${NAME} in</strong><span class="spacer"></span><button type="button" class="ghost" data-close aria-label="Close">✕</button></div>
      <form class="tmodal-body" data-access-form>
      <label class="support-check"><input type="radio" name="tier" value="app" checked><span><strong>App only.</strong> The team sees settings, Health, computers, bots, tasks and runs, and can change setup (a bot's model, a computer's runner restart). It cannot see credential values, approve for you, send, spend, publish or run bots.</span></label>
      <label class="support-check"><input type="radio" name="tier" value="full"${access.can_full ? '' : ' disabled'}><span><strong>Full access.</strong> What the owner can do in Tico, except tokens, credential values and ownership. Its changes can outlast the session.${access.can_full ? '' : ' Only the owner can give it.'}</span></label>
      <label>How long <select name="hours"><option value="1">1 hour</option><option value="4">4 hours</option><option value="24" selected>24 hours</option><option value="168">7 days</option><option value="custom">Other…</option></select></label>
      <label data-custom hidden>Hours (1 to 720) <input name="custom" type="number" min="1" max="720" step="1" value="24"></label>
      <p class="support-note">A banner shows on every screen while it lasts, and Stop there ends it at once. Everything the team does is listed in the chat.</p>
      <p class="err" data-error role="alert" hidden></p>
      <div class="support-row"><button class="ghost" type="button" data-close>Cancel</button><button class="primary" type="submit">Start</button></div></form>`;
    document.body.appendChild(dialog); dialog.showModal();
    const form = dialog.querySelector('form'), err = dialog.querySelector('[data-error]');
    form.elements.hours.onchange = () => { dialog.querySelector('[data-custom]').hidden = form.elements.hours.value !== 'custom'; };
    form.onsubmit = async e => {
      e.preventDefault();
      const hours = Number(form.elements.hours.value === 'custom' ? form.elements.custom.value : form.elements.hours.value);
      if (!Number.isInteger(hours) || hours < 1 || hours > 720) { err.textContent = 'Choose 1 to 720 hours.'; err.hidden = false; return; }
      form.querySelectorAll('button, input, select').forEach(el => { el.disabled = true; });
      try {
        await call('POST', '/support/access', {ticket_id: t.id, tier: form.elements.tier.value, hours});
        dialog.close(); say(`The ${NAME} has access.`); await pollAccess();
      } catch (error) {
        err.textContent = error.message; err.hidden = false;
        form.querySelectorAll('button, input, select').forEach(el => { el.disabled = false; });
        if (!access.can_full) form.querySelector('[value=full]').disabled = true;
      }
    };
    dialog.querySelectorAll('[data-close]').forEach(b => { b.onclick = () => dialog.close(); });
    dialog.onclose = () => dialog.remove();
  }
  document.addEventListener('click', e => {
    if (e.target.closest('#support-access-banner [data-access-stop], [data-access] [data-access-stop]')) void stopAccess();
    else if (e.target.closest('[data-access] [data-access-start]') && latest()) openAccess(latest());
    // The banner itself opens the chat, where Stop and the log are.
    else if (e.target.closest('#support-access-banner [data-access-left]')) void open();
    else if (e.target.closest('#support-open')) { if (document.body.classList.contains('drawer') && typeof setDrawer === 'function') setDrawer(false); void open(); }
  });
  // The message box grows to what it holds (a "Want help setting up?" snapshot reads whole), up to the CSS cap and never
  // past the room the chat has left: the thread keeps its minimum and Send stays on screen. The rest scrolls in the box.
  function fitBox(box) {
    if (!box || !box.isConnected) return;
    box.style.height = 'auto';
    const want = box.scrollHeight + box.offsetHeight - box.clientHeight;
    const thread = pop?.querySelector('[data-thread]'), sheet = pop?.querySelector('.support-pop-in');
    let room = want;
    if (thread?.getClientRects().length && sheet) {
      const spare = Math.max(0, thread.offsetHeight - (parseFloat(getComputedStyle(thread).minHeight) || 0));
      const over = Math.max(0, box.form.getBoundingClientRect().bottom - sheet.getBoundingClientRect().bottom);
      room = Math.max(parseFloat(getComputedStyle(box).minHeight) || 0, box.offsetHeight + spare - over);
    }
    box.style.height = Math.min(want, room) + 'px';
  }
  const fitVisibleBox = () => { if (isOpen()) fitBox(pop.querySelector('[data-view]:not([hidden]) [name=message]')); };
  // The box, Send, and the paperclip that attaches diagnostics (reviewed before they go). Attached, a small line over
  // the box says so, with Review and ×.
  function renderComposer() {
    const d = draft, form = pop.querySelector('[data-compose]');
    form.innerHTML = `${d.diag ? `<p class="support-report" data-report><span class="nav-icon" aria-hidden="true">description</span><span>Diagnostics${d.report ? ': ' + escape(REPORTED[d.report.kind]) : ''}</span>
        <button class="linkish" type="button" data-preview>Review</button><button class="support-x" type="button" data-unreport aria-label="Remove diagnostics" title="Remove diagnostics"><span class="nav-icon" aria-hidden="true">close</span></button></p>` : ''}
      <div class="support-input">
        <button class="support-clip" type="button" data-attach aria-label="Attach diagnostics" title="Attach diagnostics">${ICON.clip}</button>
        <label class="sr-only" for="support-message">Message the ${NAME}</label>
        <textarea id="support-message" name="message" maxlength="4000" rows="1" placeholder="Message the ${NAME}" aria-describedby="support-keys">${escape(d.message)}</textarea>
        <button class="support-send" type="submit" aria-label="Send" title="Send (Enter)">${ICON.send}</button></div>
      <p class="err support-error" data-error role="alert"${d.error ? '' : ' hidden'}>${escape(d.error || '')}</p>
      <small class="sr-only" id="support-keys">Enter sends. Shift+Enter adds a new line.</small>`;
    fitBox(form.elements.message);
    form.querySelector('[data-unreport]')?.addEventListener('click', () => {
      // An untouched prefill would say "details attached" with nothing attached.
      d.message = unprefilled(d.message);
      if (d.report?.kind === 'setup_check') d.kind = d.kindBefore || 'support';
      d.report = null; d.diag = false; d.bundle = null; d.key = uid(); renderComposer(); form.elements.message?.focus();
    });
    form.oninput = () => { d.message = form.elements.message.value; fitBox(form.elements.message); d.key = uid(); };
    form.elements.message.onkeydown = e => {
      if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return;
      e.preventDefault(); form.requestSubmit();
    };
    form.querySelector('[data-attach]').onclick = () => openEditor(d);
    form.querySelector('[data-preview]')?.addEventListener('click', () => openEditor(d));
    // The outcome is kept on the draft and shown in whichever composer is on screen when it settles: the chat may have
    // been closed and reopened (a new form) since Send was pressed.
    const shown = text => {
      d.error = text;
      const err = pop.querySelector('[data-compose] [data-error]');
      if (err && draft === d) { err.textContent = text; err.hidden = !text; }
    };
    form.onsubmit = async e => {
      e.preventDefault();
      if (sending || deleting) return;
      shown('');
      if (!d.message.trim()) { form.elements.message.focus(); return; }
      // To the open conversation, or a new ticket that starts with this message; nothing about that is asked.
      // A new conversation already sent with this key (its reply lost) is sent again the same way, never as a follow-up
      // to the ticket it may have made: HQ answers a repeated key with that ticket, so nothing is added twice.
      const to = d.creating === d.key ? '' : latest()?.id || '', key = d.key;
      if (!to) d.creating = key;
      // What goes is what was in the box when Send was pressed, with its own attachment.
      const sent = {message: d.message, diag: d.diag, perf: d.perf, report: d.report, bundle: d.bundle, kind: d.kind};
      sending = d; lockSending();
      let done; inFlight = new Promise(r => { done = r; });
      try {
        let bundle = sent.bundle;
        if (sent.diag && !bundle) {
          bundle = await capture(sent.perf, sent.report);
          if (d.key === key) d.bundle = bundle;     // kept for a retry only while the draft is still the one sent
        }
        const body = {message: sent.message, diagnostics: sent.diag ? bundle.id : ''};
        const fresh = () => Object.assign({}, body, {email: compose?.email || (typeof S === 'undefined' ? '' : S.me?.email || ''), include_ids: true},
          appVersion() ? {app_version: appVersion()} : {}, sent.kind !== 'support' ? {kind: sent.kind} : {});
        let next, rolled = '';
        try { next = await call('POST', to ? `/support/tickets/${encodeURIComponent(to)}/messages` : '/support/tickets', to ? body : fresh(), key); }
        catch (error) {
          // HQ refuses a follow-up to a ticket that is full (60 messages), closed or gone: the same message starts a new
          // one, once, and shows as sent like any other.
          const refused = error.status === 404 || (error.status === 409 && (!error.code || error.code === 'support_closed'));
          if (!to || !refused) throw error;
          state.tickets = state.tickets.map(t => t.id === to ? {...t, status: 'closed'} : t);
          // The draft keeps the new ticket's key: a retry after a lost reply repeats this creation, not a second one.
          rolled = (key + '-new').slice(0, 200);
          if (d.key === key) d.key = d.creating = rolled;
          next = await call('POST', '/support/tickets', fresh(), rolled);
        }
        stateRevision++;
        state.tickets = [next, ...state.tickets.filter(t => t.id !== next.id)];
        // A draft that changed while this was on its way holds newer words: it stays.
        if (draft === d && (d.key === key || (rolled && d.key === rolled))) draft = newDraft();
        settled(); done(); mark();
        if (isOpen() && !deleting) { render(true); renderComposer(); focusBox(); }
        schedule();
      } catch (error) {
        settled(); done();
        // Said where the person is looking: the message did not arrive and is still in the box.
        shown(!error.status || error.status >= 500 ? `Not sent. Could not reach the ${NAME}. Try again in a moment.` : 'Not sent. ' + error.message);
        if (isOpen() && draft === d) pop.querySelector('[data-compose] [name=message]')?.focus();
      }
      // A Report this or setup snapshot that came while the message was on its way starts from what is left now.
      if (isOpen()) { openPending(); startPending(); }
    };
    lockSending();
  }
  async function openEditor(d) {
    if (editor) return;
    const dialog = document.createElement('dialog'); editor = dialog;
    dialog.className = 'tmodal support-modal'; dialog.setAttribute('aria-label', 'Review and edit diagnostics');
    dialog.innerHTML = `<div class="tmodal-head"><strong>Diagnostics</strong><span class="spacer"></span><button type="button" class="ghost" data-close aria-label="Close">✕</button></div>
      <div class="tmodal-body"><p>Remove sections or edit the JSON. Secrets are redacted again before it is attached. Nothing is sent here.</p>
      <label class="support-perf">Computer performance <select name="perf">${PERF_WINDOWS.map(([h, label]) => `<option value="${h}"${h === d.perf ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
      <div data-sections class="support-sections"></div><label for="support-json">Exact attachment</label><textarea id="support-json" class="support-json" spellcheck="false" aria-label="Diagnostics JSON"></textarea>
      <p class="support-note" data-result role="status">Loading diagnostics…</p><p class="err" data-error role="alert" hidden></p>
      <div class="support-row"><button class="ghost" type="button" data-refresh>Refresh capture</button><button class="ghost" type="button" data-save>Check edits</button><button class="primary" type="button" data-use disabled>Use diagnostics</button></div></div>`;
    document.body.appendChild(dialog); dialog.showModal();
    const area = dialog.querySelector('textarea'), err = dialog.querySelector('[data-error]'), result = dialog.querySelector('[data-result]'), use = dialog.querySelector('[data-use]');
    let base = d.bundle, checked = null, loading = false, perf = d.perf;
    const fail = e => { err.textContent = e.message; err.hidden = false; };
    const invalidate = () => { checked = null; use.disabled = true; result.textContent = 'Check edits to see the final redacted attachment.'; };
    area.oninput = invalidate;
    const show = bundle => {
      base = checked = bundle; area.value = bundle.text; use.disabled = false;
      result.textContent = 'This is the exact attachment (' + Math.ceil(bundle.bytes / 1024) + ' KB).';
      const data = JSON.parse(bundle.text), sections = dialog.querySelector('[data-sections]');
      sections.innerHTML = Object.keys(data).filter(k => !['format', 'created', 'capture'].includes(k)).map(k => `<label><input type="checkbox" data-section="${escape(k)}" checked> ${escape(k)}</label>`).join('');
      sections.onchange = e => {
        const key = e.target.dataset.section;
        if (!key) return;
        try { const value = JSON.parse(area.value); if (e.target.checked) value[key] = data[key]; else delete value[key]; area.value = JSON.stringify(value, null, 2); invalidate(); }
        catch (_) { fail(new Error('Fix the JSON before changing sections.')); e.target.checked = !e.target.checked; }
      };
    };
    const load = async () => {
      if (loading) return;
      loading = true; err.hidden = true; use.disabled = true;
      try { show(await capture(perf, d.report)); }
      catch (e) { fail(e); }
      finally { loading = false; }
    };
    dialog.querySelector('[name=perf]').onchange = e => { perf = Number(e.target.value); void load(); };
    dialog.querySelector('[data-refresh]').onclick = () => {
      if (area.value && !confirm('Replace this capture and its edits with current diagnostics?')) return;
      void load();
    };
    dialog.querySelector('[data-save]').onclick = async () => {
      if (loading || !base) return;
      err.hidden = true; loading = true; use.disabled = true;
      try {
        const input = area.value;
        JSON.parse(input);
        const value = await call('POST', '/support/diagnostics', {id: base.id, text: input});
        // An in-flight validation must not overwrite text the user continued editing.
        if (area.value !== input) { invalidate(); return; }
        show(value); result.textContent = 'Edits checked and redacted. Review the final text, then use this attachment.';
      } catch (e) { fail(e); }
      finally { loading = false; }
    };
    use.onclick = () => {
      if (!checked || loading) return;
      d.bundle = checked; d.diag = true; d.perf = perf; d.key = uid();
      if (isOpen() && draft === d) renderComposer();
      dialog.close();
    };
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.onclose = () => { editor = null; dialog.remove(); };
    if (base) show(base); else await load();
  }
  // A message still on its way is not changed under it: what came meanwhile waits until that send settles.
  const busy = () => !!sending && sending === draft;
  function openPending() {
    if (pending && busy()) return;
    const p = pending; pending = null;
    if (!p || !isOpen() || !state.enabled) return;
    const d = draft;
    if (p.report) {
      // A Setup check marks a new ticket setup (HQ's kind for help with setting up); replacing or removing it gives
      // back the kind it had before, which may be setup already ("Want help setting up?").
      if (d.report?.kind === 'setup_check') d.kind = d.kindBefore || 'support';
      if (p.report.kind === 'setup_check') { d.kindBefore = d.kind; d.kind = 'setup'; }
      d.report = p.report; d.diag = true; d.bundle = null; d.key = uid();
      // The words on screen go only in the attachment, which is redacted; the message is sent as typed, so it names
      // the kind and nothing else.
      const rest = unprefilled(d.message);
      if (!d.message.trim() || rest !== d.message) d.message = prefill(p.report) + rest;
    }
    renderComposer(); focusBox();
  }
  window.talkToUs = report => {
    pending = {report: asReport(report)};
    void open();
  };
  // Report this is offered unless this server has support turned off.
  // Pages re-render once support's state loads; a toast does not, so it asks for `known` and waits for a yes.
  window.supportOn = known => known ? state.enabled === true : state.enabled !== false;
  // `icon`: a small flag button for a dense row (Settings > History > Runs); the words become its label and tooltip.
  window.reportThisHTML = (report, icon = false) => icon
    ? `<button type="button" class="report-this report-this-icon" data-report-this="${escape(JSON.stringify(report))}" aria-label="Report this" title="Report this"><span class="nav-icon" aria-hidden="true">bug_report</span></button>`
    : `<button type="button" class="linkish report-this" data-report-this="${escape(JSON.stringify(report))}">Report this</button>`;
  document.addEventListener('click', e => {
    const report = e.target.closest?.('[data-report-this]');
    if (!report) return;
    e.preventDefault(); e.stopPropagation();
    let value = null;
    try { value = JSON.parse(report.dataset.reportThis); } catch (_) {}
    window.talkToUs(value);
  });
  // "Want help setting up?" and #/support/manage put words in the box for the person to read, edit and send.
  let starting = null;
  function startPending() {
    if (starting && busy()) return;
    if (!starting || !isOpen() || !state.enabled) { starting = null; return; }
    const {kind, message: words} = starting, d = draft, unsent = d.message.trim();
    // Words the person had typed stay, with the new ones after them, once.
    d.message = unsent.includes(words.trim()) ? unsent : unsent ? unsent + '\n\n' + words : words;
    if (d.report?.kind === 'setup_check') d.kindBefore = kind; else d.kind = kind;
    d.key = uid(); starting = null;
    renderComposer(); focusBox();
  }
  window.supportState = () => ({enabled: state.enabled, unread: state.unread});
  window.supportStart = (message, kind = 'support') => {
    starting = {message, kind};
    void open();
  };
  document.addEventListener('click', async e => {
    const button = e.target.closest('[data-setup-help]');
    if (!button || button.disabled) return;
    e.preventDefault(); button.disabled = true;
    try {
      const snapshot = await call('GET', '/support/setup-snapshot?asked_from=' + encodeURIComponent(button.dataset.setupHelp));
      window.supportStart(snapshot.message, snapshot.kind);
    } catch (error) { say(error.message, true); }
    finally { button.disabled = false; }
  });

  // The chat: a panel beside the sidebar on a computer, a full page above the tab bar on a phone. Focus moves into it,
  // Tab stays inside, and Esc, ✕ or a click outside closes it and gives focus back to whatever opened it.
  function build() {
    if (pop) return pop;
    pop = document.createElement('dialog');
    pop.id = 'support-pop'; pop.className = 'support-pop'; pop.setAttribute('aria-labelledby', 'support-pop-h');
    pop.innerHTML = `<div class="support-pop-in">
      <header class="support-head">${AVATAR}<h2 id="support-pop-h">${NAME}</h2><span class="spacer"></span>
        <button type="button" class="support-close support-trash" data-delete aria-label="Delete chat history" title="Delete chat history" hidden><span class="nav-icon" aria-hidden="true">delete</span></button>
        <button type="button" class="support-close" data-close aria-label="Close" title="Close (Esc)"><span class="nav-icon" aria-hidden="true">close</span></button></header>
      <p class="support-connection" data-connection role="status"></p>
      <p class="support-note support-loading" data-loading hidden>Loading…</p>
      <p class="support-off" data-off hidden>Messages to the ${NAME} are off on this Tico server.</p>
      <div class="support-confirm" data-confirm role="alertdialog" aria-labelledby="support-confirm-h" hidden>
        <p id="support-confirm-h"><strong>Delete chat history?</strong></p>
        <p class="support-note">Every message and its diagnostics, here and at the ${NAME}. This cannot be undone.</p>
        <p class="err" data-confirm-error role="alert" hidden></p>
        <div class="support-row"><button type="button" class="ghost" data-confirm-no>Cancel</button><button type="button" class="danger" data-confirm-yes>Delete</button></div></div>
      <div class="support-view" data-view hidden>
        <div class="support-thread" data-thread role="log" aria-label="Conversation with the ${NAME}"></div>
        <div class="support-access" data-access hidden></div>
        <form class="support-write" data-compose novalidate></form>
      </div></div>`;
    document.body.appendChild(pop);
    pop.querySelector('[data-close]').onclick = () => pop.close();
    // Delete chat history: asked inside the chat, then every one of the person's tickets is deleted at HQ.
    const ask = pop.querySelector('[data-confirm]'), askErr = pop.querySelector('[data-confirm-error]');
    const asking = on => { ask.hidden = !on; askErr.hidden = true; pop.querySelector('[data-view]').hidden = on || state.enabled !== true; if (!on) focusBox(); };
    pop.querySelector('[data-delete]').onclick = () => { asking(true); pop.querySelector('[data-confirm-no]').focus(); };
    pop.querySelector('[data-confirm-no]').onclick = () => asking(false);
    pop.querySelector('[data-confirm-yes]').onclick = async e => {
      const yes = e.currentTarget;
      if (sending || deleting) return;
      deleting = true; yes.disabled = true; lockSending();
      try {
        // The list holds at most 50, newest first: list and delete again until it comes back empty. A send still on
        // its way is waited for, so the ticket it lands in goes too.
        let left = state.tickets;
        for (let round = 0; round < 20; round++) {
          if (inFlight) await inFlight;
          for (const t of left) {
            await call('DELETE', `/support/tickets/${encodeURIComponent(t.id)}`).catch(error => { if (error.status !== 404) throw error; });
            stateRevision++;
            state.tickets = state.tickets.filter(x => x.id !== t.id);
          }
          if (inFlight) await inFlight;
          const data = await call('GET', '/support/tickets');
          left = data.tickets || [];
          if (!left.length) break;
        }
        stateRevision++;
        if (left.length) {
          state.tickets = left;
          throw new Error(`Could not delete all of your chat history; ${left.length} conversation${left.length === 1 ? '' : 's'} left. Try again.`);
        }
        state.tickets = []; state.unread = 0; lastUnread = 0;
        deleting = false; mark(); asking(false); render(); renderComposer(); focusBox();
      } catch (error) { askErr.textContent = error.message; askErr.hidden = false; mark(); }
      finally { deleting = false; yes.disabled = !!sending; lockSending(false); }
    };
    // A click that starts and ends on the backdrop (the dialog itself, outside its box) closes it.
    let downOutside = false;
    pop.addEventListener('pointerdown', e => { downOutside = e.target === pop; });
    pop.addEventListener('click', e => { if (e.target === pop && downOutside) pop.close(); downOutside = false; });
    pop.addEventListener('keydown', e => {
      if (e.key !== 'Tab') return;
      const all = [...pop.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
        .filter(el => !el.disabled && el.checkVisibility({visibilityProperty: true}));
      if (!all.length) return;
      const first = all[0], end = all.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); end.focus(); }
      else if (!e.shiftKey && document.activeElement === end) { e.preventDefault(); first.focus(); }
    });
    pop.addEventListener('close', () => {
      editor?.close();
      const back = opener?.isConnected && opener.getClientRects().length ? opener : null;
      opener = null; back?.focus();
      schedule();
    });
    return pop;
  }
  // On a phone the chat ends at the bottom of what is visible: with the keyboard up, the space above it. The visual
  // viewport's height shrinks for the keyboard and for pinch zoom, so height × scale is the visible height in page pixels.
  function fit() {
    if (!pop) return;
    const vv = window.visualViewport, page = document.documentElement.clientHeight || window.innerHeight;
    const height = Math.min(page, (vv?.height || window.innerHeight) * (vv?.scale || 1));
    if (pop.style.getPropertyValue('--support-visible') !== height + 'px') { pop.style.setProperty('--support-visible', height + 'px'); fitVisibleBox(); }
  }
  window.visualViewport?.addEventListener('resize', fit);
  window.addEventListener('resize', fit);
  function focusBox() {
    if (!isOpen()) return;
    const box = pop.querySelector('[data-view]:not([hidden]) [name=message]');
    if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    else if (!pop.contains(document.activeElement) || document.activeElement === pop) pop.querySelector('[data-close]').focus();
  }
  // Loads this person's conversation and the reply email a new ticket carries; a failure keeps what was loaded before.
  async function load() {
    const actor = typeof S === 'undefined' ? '' : S.me?.id;
    if (owner !== actor) { stateRevision++; if (owner) browserFailures.length = 0; owner = actor; draft = newDraft(); compose = null; state = {enabled: null, tickets: [], unread: 0}; lastUnread = 0; announceWaiting = false; }
    try {
      const revision = stateRevision;
      const data = await call('GET', '/support/tickets');
      if (revision === stateRevision) accept(data);
      if (state.enabled) compose = await call('GET', '/support/compose');
      note('');
    } catch (e) { if (state.enabled === null) note(e.message, true); else note(POLL_FAILED, true); }
  }
  async function open() {
    build();
    announceWaiting = false;      // the person is looking now: nothing left to announce
    if (!isOpen()) {
      opener = document.activeElement !== document.body ? document.activeElement : null;
      pop.showModal();
    }
    fit();
    const run = loading = load();
    render(true); renderComposer(); focusBox();
    await run;
    if (loading !== run || !isOpen()) return;
    loading = null;
    render(true);
    if (state.enabled) renderComposer();
    openPending(); startPending();
    focusBox();
    void readVisible();
    if (state.enabled) { await refresh(); schedule(); }
  }
  window.supportOpen = open;
  window.supportPoll = refresh;
  // Replies are checked when the app comes back into view or focus, and on the poll above; there is no Refresh button.
  // Only with a ticket still open (as the poll above), and once for a focus and a visibilitychange that come together.
  let wokeAt = 0;
  const wake = () => {
    if (!state.enabled || Date.now() - wokeAt < 30000) return;
    wokeAt = Date.now();
    if (state.tickets.some(t => !ended(t))) void refresh();
    schedule();
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { wake(); void pollAccess(); announceReply(); } });
  window.addEventListener('focus', wake);
  // A reply waiting for the person to be free (announceReply) opens when a field loses focus or a dialog closes.
  document.addEventListener('focusout', () => { if (announceWaiting) setTimeout(announceReply, 0); });
  document.addEventListener('close', () => { if (announceWaiting) setTimeout(announceReply, 0); }, true);
  window.addEventListener('load', () => setTimeout(async () => {
    if (typeof S === 'undefined' || !S.me || state.enabled !== null) return;
    const revision = stateRevision;
    try { const data = await call('GET', '/support/tickets'); if (revision === stateRevision) accept(data); schedule(); } catch (_) { /* opening the chat tries again */ }
  }, 3000));
  window.addEventListener('load', () => setTimeout(pollAccess, 1000));
})();
