/* Support is a pop-up over the current screen, opened from Ask's + (Tico support, ui/app/ask-panel.js), by a #/support link, by
   Report this and Want help setting up?, and by old #/help links. It uses the existing ticket transport.
   Drafts stay on this page's account in memory; an attachment is an immutable server preview, never silently replaced
   when Send is pressed. */
'use strict';
(function () {
  'use strict';
  const API_BASE = () => (typeof API === 'string' ? API : '/api') + '/v2';
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const say = (msg, error, action) => typeof toast === 'function' && toast(msg, error, action);
  const uid = () => crypto.randomUUID();
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
  // Report this pressed anywhere: the pop-up opens on a new request, the report attached as diagnostics.
  let pending = null;
  const PERF_WINDOWS = [[0, 'None'], [24, '1 day'], [168, '7 days'], [336, '14 days']];
  const statuses = {open: 'Open', answered: 'Answered', closed: 'Closed', gone: 'Removed'};
  // The reply promise (Support & Managed Owner's spec, reply-promise.md): the same words before and after sending, and who
  // answers first. Shown on an open request only, never on an answered, closed or failed one.
  const PROMISE = 'We usually reply within 1 business day (Monday to Friday).';
  const WHO_ANSWERS = "Tico's support assistant answers first and brings in a person when it needs one.";
  const ICON = {
    send: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 12h15M13 6l6 6-6 6"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2.5 4h11M6.5 4V2.5h3V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v4.5M9.2 6.5v4.5"/></svg>',
    back: '<svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7.5 3l-3 3 3 3"/></svg>',
    plus: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M8 3v10M3 8h10"/></svg>',
  };
  // `selected`: the request in the chat ('' is a new one); `view`: the chat, or the list of Your requests.
  let state = {enabled: null, tickets: [], unread: 0}, selected = '', view = 'chat', pop = null, timer, busyRefresh = null, stateRevision = 0;
  let owner = '', toasted = 0, compose = null, drafts = new Map(), editor = null, opener = null, loading = null, sending = null;
  const when = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}); };
  const short = iso => {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : d.toLocaleDateString([], {month: 'short', day: 'numeric'});
  };
  // A request has no subject: its first line is the title and the lines after it are the preview.
  const lines = t => (t.message || '').split(/\r?\n/).map(l => l.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const title = t => lines(t)[0]?.slice(0, 90) || 'Request';
  const preview = t => lines(t).slice(1).join(' ').slice(0, 140);
  const last = t => (t.messages || []).at(-1)?.created_at || t.created_at;
  const newestFirst = list => [...list].sort((a, b) => (Date.parse(last(b)) || 0) - (Date.parse(last(a)) || 0));
  const ended = t => ['closed', 'gone'].includes(t.status);
  // The request with the most recent message that isn't Closed or Removed.
  const latest = () => newestFirst(state.tickets).find(t => !ended(t));
  // The pop-up opens on the newest of those with an unread reply or a message in the last day. An older one (say a
  // week-old alert) is not opened on its own: Your requests is shown instead (a new request when none is open).
  const RECENT_MS = 24 * 3600 * 1000;
  const landing = () => newestFirst(state.tickets).find(t => !ended(t) && (t.unread || Date.now() - (Date.parse(last(t)) || 0) < RECENT_MS));
  const isOpen = () => !!pop?.open;
  const draft = () => {
    if (!drafts.has(selected)) drafts.set(selected, {message: '', email: compose?.email || '', ids: true, diag: !selected, perf: 24, bundle: null, key: uid(), kind: 'support', report: null, open: false});
    return drafts.get(selected);
  };
  const current = () => state.tickets.find(t => t.id === selected);
  async function call(method, path, body, key) {
    const r = await fetch(API_BASE() + path, {method, cache: 'no-store',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'Idempotency-Key': key || uid()},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.error?.detail || data.error?.message || (typeof data.detail === 'string' ? data.detail : '') || 'Could not reach support. Try again.'), {status: r.status});
    return data;
  }
  // Unread replies show only inside Ask's panel, as support's messages in the chat (ui/app/ask-panel.js hears
  // `tico:support`); an #ask-support item, where a page has one, carries the count. Ask's button and the phone's Ask
  // tab carry no count.
  function mark() {
    const n = state.unread, item = document.getElementById('ask-support');
    if (item) {
      if (n) item.setAttribute('data-support-count', n > 9 ? '9+' : String(n));
      else item.removeAttribute('data-support-count');
      item.setAttribute('aria-label', n ? `Tico support, ${n} new ${n === 1 ? 'reply' : 'replies'}` : 'Tico support');
    }
    document.dispatchEvent(new CustomEvent('tico:support'));
  }
  function accept(data) {
    state = {enabled: data.enabled !== false, tickets: data.tickets || [], unread: data.unread || 0};
    if (state.unread > toasted) {
      const replied = newestFirst(state.tickets).find(t => t.unread);
      say('Tico support replied.', false, replied ? {label: 'Open', run: () => open({ticket: replied.id})} : undefined);
    }
    toasted = state.unread;
    mark();
    if (!isOpen()) return;
    if (selected && !current()) { selected = latest()?.id || ''; renderComposer(); }
    render();
    // A reply that lands in the conversation someone has open in front of them is read; while the pop-up is still
    // opening, open() reads what it settles on instead.
    if (!loading && view === 'chat' && current()?.unread && document.hasFocus()) void readVisible();
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
        const shown = isOpen() && view === 'chat' ? current()?.id : '';
        const data = await call('POST', '/support/tickets/refresh', shown ? {ticket: shown} : undefined);
        // A refresh started before a send/read/delete settled can return an older ticket list. Keep the local change;
        // the next refresh will reconcile it with the server.
        if (revision === stateRevision) accept(data);
        note(data.refresh_failed ? POLL_FAILED : '', !!data.refresh_failed);
      } catch (_) { note(POLL_FAILED, true); }      // what was loaded before stays on screen
      finally { busyRefresh = null; }
      return state;
    })();
    return busyRefresh;
  }
  // Every 30 seconds while the pop-up is open, otherwise every 5 minutes, plus whenever the app comes back into focus.
  function schedule() {
    clearTimeout(timer);
    if (!state.enabled) return;
    timer = setTimeout(async () => {
      if (!document.hidden && state.tickets.some(t => !ended(t))) await refresh();
      schedule();
    }, isOpen() ? 30000 : 300000);
  }
  async function readVisible() {
    const t = current();
    if (document.hidden || !isOpen() || view !== 'chat' || !t?.unread) return;
    // Reading follows opening a conversation (or having it open), not a background fetch.
    const revision = stateRevision;
    try {
      const next = await call('POST', `/support/tickets/${encodeURIComponent(t.id)}/read`);
      if (revision !== stateRevision) return;
      stateRevision++;
      state.tickets = state.tickets.map(x => x.id === next.id ? next : x);
      state.unread = state.tickets.reduce((n, x) => n + (x.unread || 0), 0);
      toasted = state.unread; mark(); render();
    } catch (_) { /* unread remains until the conversation is opened again */ }
  }
  const badge = status => `<span class="support-badge s-${statuses[status] ? status : 'other'}">${escape(statuses[status] || status)}</span>`;
  // While a message is on its way nothing that could send it again, change it or move the chat can be pressed, however
  // often the pop-up is closed and reopened: every render locks what it draws until the send settles.
  const LOCKED = '[data-compose] :is(input, textarea, button, select), [data-requests] button, [data-new], [data-mine], [data-delete], [data-view-last]';
  const lockSending = (on = true) => {
    if (!pop || (on && !sending)) return;
    pop.querySelectorAll(LOCKED).forEach(el => { el.disabled = on; });
    const submit = pop.querySelector('[data-compose] [type=submit]');
    if (on) submit?.setAttribute('aria-busy', 'true'); else submit?.removeAttribute('aria-busy');
  };
  const settled = () => { sending = null; lockSending(false); };
  function render() {
    if (!pop) return;
    const off = state.enabled === false, known = state.enabled !== null;
    pop.querySelector('[data-loading]').hidden = known || !loading;
    pop.querySelector('[data-off]').hidden = !off;
    pop.querySelector('[data-new]').hidden = !known || off;
    pop.querySelector('[data-list]').hidden = !known || off || view !== 'list';
    pop.querySelector('[data-view]').hidden = !known || off || view !== 'chat';
    renderLinks();
    if (!known || off) return;
    renderTickets(); renderThread(); lockSending();
  }
  // View docs · What's new (N) · Your requests (N)
  function renderLinks() {
    const news = Number(typeof S === 'undefined' ? 0 : S.config?.changelog?.unread_count) || 0;
    const total = state.tickets.length;
    pop.querySelector('[data-whats-new]').textContent = news ? `What's new (${news})` : "What's new";
    const mine = pop.querySelector('[data-mine]');
    // The number of requests the list shows; the unread replies are the dots in it and the count on Ask.
    mine.textContent = total ? `Your requests (${total})` : 'Your requests';
    mine.setAttribute('aria-pressed', String(view === 'list'));
  }
  function renderTickets() {
    const list = pop.querySelector('[data-requests]'), items = newestFirst(state.tickets);
    list.innerHTML = items.map(t => `<li><button type="button" class="support-item${t.unread ? ' unread' : ''}" data-id="${escape(t.id)}">
      <span class="support-item-top"><span class="support-item-title">${escape(title(t))}</span>${t.unread ? `<span class="support-dot" role="img" aria-label="${t.unread} new ${t.unread === 1 ? 'reply' : 'replies'}"></span>` : ''}</span>${preview(t) ? `
      <span class="support-item-body">${escape(preview(t))}</span>` : ''}
      <span class="support-item-meta">${badge(t.status)}<time datetime="${escape(last(t))}" title="${escape(when(last(t)))}">${escape(short(last(t)))}</time></span></button></li>`).join('');
    pop.querySelector('[data-none]').hidden = !!items.length;
  }
  function renderThread(pin = false) {
    const host = pop.querySelector('[data-thread]'), t = current();
    const bubble = (body, staff, at, diag) => `<div class="support-msg ${staff ? 'staff' : 'you'}"><span class="support-who">${staff ? 'Tico support' : 'You'}${at ? ` · <time datetime="${escape(at)}">${escape(when(at))}</time>` : ''}</span><div class="support-body">${escape(body)}</div>${diag ? '<small class="support-sent">Diagnostics attached</small>' : ''}</div>`;
    const after = {open: `Delivered to Tico support. ${PROMISE} The reply will appear here.`, gone: 'Tico support removed this request.'};
    // No request in the chat: an empty chat, and a way back to the last request when it is closed.
    const recent = newestFirst(state.tickets)[0], closedLast = !latest() && recent?.status === 'closed' ? recent : null;
    const html = t ? bubble(t.message, false, t.created_at, t.sent?.diagnostics) + (t.messages || []).map(m => bubble(m.body, m.from === 'staff', m.created_at, m.has_diagnostics)).join('')
        + (after[t.status] ? `<p class="support-delivered">${after[t.status]}</p>` : '')
      : `<div class="support-empty"><p>Ask us anything. ${PROMISE}</p><p>${WHO_ANSWERS}</p>${closedLast
        ? `<p>Your last request, ${escape(title(closedLast))}, is closed. <button type="button" class="linkish" data-view-last="${escape(closedLast.id)}">View it</button></p>` : ''}</div>`;
    // stays on the newest message only while the reader is there, so a poll never moves someone reading back
    // (ui/app/stick-scroll.js); a reply that lands while they are scrolled up shows the "New reply" pill instead
    const stick = stickScroll(host), id = t ? t.id : '';
    // a conversation just opened, a message just sent, or another one (or the empty state) now in view: start at its newest
    if (pin || host.dataset.ticket !== id) stick.toBottom();
    if (host.dataset.shown !== html) {
      const fresh = !pin && !!host.dataset.shown && host.dataset.ticket === id;
      stick.update(() => { host.innerHTML = html; host.dataset.shown = html; host.dataset.ticket = id; }, {fresh});
    }
    // The request's title and status in a thin line above the bubbles; "New request" once there are others to tell apart.
    pop.querySelector('[data-head]').hidden = !t && !latest();
    pop.querySelector('[data-title]').textContent = t ? title(t) : 'New request';
    pop.querySelector('[data-status]').innerHTML = t ? badge(t.status) : '';
    pop.querySelector('[data-delete]').hidden = !t;
    const closed = t && ended(t);
    pop.querySelector('[data-compose]').hidden = !!closed;
    pop.querySelector('[data-closed]').hidden = !closed;
    renderAccess();
    lockSending();
  }
  // Letting Tico support in (docs/support.md): a banner on every screen while a session lasts; under an open request,
  // the button that starts one from it, for an owner or admin; and under each request, every request support made in
  // the sessions started from it.
  let access = {available: false, active: null, sessions: [], at: 0}, accessTimer = 0, accessRevision = 0;
  const accessLogs = new Map();
  const TIERS = {app: 'App only', full: 'Full access'};
  // Why the backend says support access is off (backend/support_access.py off_reason).
  const ACCESS_OFF = {TICO_SUPPORT_ACCESS: 'Support access is turned off on this server.', team_rule: 'Support access is turned off for this team.',
    other: 'Support access is not available here.'};
  const remaining = () => access.active ? access.active.seconds_left - (Date.now() - access.at) / 1000 : 0;
  const timeLeft = () => {
    const m = Math.max(1, Math.ceil(remaining() / 60)), h = Math.floor(m / 60);
    return h ? `${h} h ${m % 60} min` : `${m} min`;
  };
  const canStop = () => !!access.active && (!!access.can_start || access.active.started_by === 'human:' + (typeof S === 'undefined' ? '' : S.me?.id || ''));
  // The same words on the banner and under the request: the tier and the time left.
  const accessWords = live => `Tico support has access (${TIERS[live.tier] || live.tier}), ${timeLeft()} left`;
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
  // Under the request in the chat: its live session with Stop, else the start button (an open request, an owner or
  // admin, no session live), or why it is off; then the log of every session started from it, newest first. Log times
  // read as the app's event times do: how long ago, the full time on hover (ui/app/format.js).
  function renderAccess() {
    const host = pop?.querySelector('[data-access]'), t = current();
    if (!host) return;
    const live = liveAccess(), here = live && t && live.ticket_id === t.id ? live : null;
    let html = '';
    if (here) html = `<p class="support-access-on">${escape(accessWords(here))}${canStop() ? ' · <button type="button" class="ghost" data-access-stop>Stop</button>' : ''}</p>`;
    else if (t && !ended(t) && access.available && access.can_start && !access.active)
      html = '<button type="button" class="ghost" data-access-start>Let Tico support in</button>';
    else if (t && !ended(t) && !access.available && access.off_reason) html = `<p class="support-sent">${escape(ACCESS_OFF[access.off_reason] || ACCESS_OFF.other)}</p>`;
    const sessions = t ? (access.sessions || []).filter(x => x.ticket_id === t.id) : [];
    if (sessions.length) {
      for (const x of sessions) if (!accessLogs.has(x.id) || x.id === here?.id) void loadLog(x.id);
      const items = sessions.flatMap(x => accessLogs.get(x.id)?.items || []).sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
      html += `<details class="support-access-log"${here ? ' open' : ''}><summary>Requests Tico support made</summary>${items.length
        ? `<ol>${items.map(x => `<li><time datetime="${escape(x.created_at)}" data-ago="${escape(x.created_at)}" title="${escape(fmt(x.created_at))}">${escape(ago(x.created_at))}</time> ${escape(x.by === 'support-agent' ? 'Support Agent' : 'Staff')} · ${escape(x.method)} ${escape(x.path)} · ${escape(x.status)}</li>`).join('')}</ol>`
        : '<p class="support-sent">None yet.</p>'}</details>`;
    }
    host.hidden = !html;
    if (host.dataset.shown !== html) {
      // Opened or closed by hand, the log stays so while the same request is in view.
      const open = host.dataset.ticket === (t?.id || '') ? host.querySelector('details')?.open : undefined;
      host.innerHTML = html; host.dataset.shown = html; host.dataset.ticket = t?.id || '';
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
    // Every 30 s while support is in, for the time left; otherwise every 5 minutes (access is given from this app, and a
    // tab that gives it asks again at once).
    accessTimer = setTimeout(pollAccess, typeof S !== 'undefined' && S.me ? (access.active ? 30000 : 300000) : 2000);
  }
  async function stopAccess() {
    if (!access.active) return;
    try {
      const stopped = await call('POST', `/support/access/${encodeURIComponent(access.active.id)}/stop`, {});
      // Show it ended at once, on the banner and under the request, whatever a poll still in flight answers.
      accessRevision++;
      access = {...access, active: null, sessions: (access.sessions || []).map(x => x.id === stopped?.id ? stopped : x)};
      renderBanner(); renderAccess();
      say('Tico support no longer has access.');
    } catch (e) { say(e.message, true); }
    await pollAccess();
  }
  function openAccess(t) {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal support-modal'; dialog.setAttribute('aria-label', 'Let Tico support in');
    dialog.innerHTML = `<div class="tmodal-head"><strong>Let Tico support in</strong><span class="spacer"></span><button type="button" class="ghost" data-close aria-label="Close">✕</button></div>
      <form class="tmodal-body" data-access-form>
      <label class="support-check"><input type="radio" name="tier" value="app" checked><span><strong>App only.</strong> Support sees settings, Health, computers, bots, tasks and runs, and can change setup (a bot's model, a computer's runner restart). Support cannot see credential values, approve for you, send, spend, publish or run bots.</span></label>
      <label class="support-check"><input type="radio" name="tier" value="full"${access.can_full ? '' : ' disabled'}><span><strong>Full access.</strong> What the owner can do in Tico, except tokens, credential values and ownership. Its changes can outlast the session.${access.can_full ? '' : ' Only the owner can give it.'}</span></label>
      <label>How long <select name="hours"><option value="1">1 hour</option><option value="4">4 hours</option><option value="24" selected>24 hours</option><option value="168">7 days</option><option value="custom">Other…</option></select></label>
      <label data-custom hidden>Hours (1 to 720) <input name="custom" type="number" min="1" max="720" step="1" value="24"></label>
      <p class="support-sent">A banner shows on every screen while it lasts, and Stop there ends it at once. Every request support makes is listed under this request.</p>
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
        dialog.close(); say('Tico support has access.'); await pollAccess();
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
    else if (e.target.closest('[data-access] [data-access-start]') && current() && !ended(current())) openAccess(current());
    // The banner itself opens the request the session started from, where Stop and the log are; someone else's
    // request is not theirs to open, so Your requests.
    else if (e.target.closest('#support-access-banner [data-access-left]')) {
      const from = state.tickets.find(t => t.id === access.active?.ticket_id);
      void open(from ? {ticket: from.id} : {list: true});
    }
  });
  // The message box grows to what it holds, so a "Want help setting up?" snapshot reads whole, up to the CSS cap and
  // never past the room the sheet has left: the thread keeps its minimum and Send stays on screen, which matters on a
  // phone, where the keyboard shortens the sheet. What does not fit scrolls inside the box.
  function fitBox(box) {
    if (!box || !box.isConnected) return;
    box.style.height = 'auto';
    const want = box.scrollHeight + box.offsetHeight - box.clientHeight;
    const thread = box.closest('[data-view]')?.querySelector('[data-thread]'), sheet = pop?.querySelector('.support-pop-in');
    let room = want;
    if (thread?.getClientRects().length && sheet) {
      const spare = Math.max(0, thread.offsetHeight - (parseFloat(getComputedStyle(thread).minHeight) || 0));
      const over = Math.max(0, box.form.getBoundingClientRect().bottom - sheet.getBoundingClientRect().bottom);
      room = Math.max(parseFloat(getComputedStyle(box).minHeight) || 0, box.offsetHeight + spare - over);
    }
    box.style.height = Math.min(want, room) + 'px';
  }
  const fitVisibleBox = () => { if (isOpen()) fitBox(pop.querySelector('[data-view]:not([hidden]) [data-compose]:not([hidden]) [name=message]')); };
  function renderComposer() {
    const d = draft(), form = pop.querySelector('[data-compose]');
    form.innerHTML = `<label class="sr-only" for="support-message">Message to Tico support</label>
      <div class="support-input"><textarea id="support-message" name="message" maxlength="4000" rows="2" placeholder="Message Tico support…" required aria-describedby="support-keys">${escape(d.message)}</textarea>
      <button class="support-send" type="submit" aria-label="Send" title="Send (Enter)">${ICON.send}</button></div>
      <p class="err support-error" data-error role="alert"${d.error ? '' : ' hidden'}>${escape(d.error || '')}</p>
      <small class="sr-only" id="support-keys">Enter sends. Shift+Enter adds a new line.</small>
      <details class="support-attach" data-attach${d.open ? ' open' : ''}><summary data-attach-label></summary>
      <div class="support-diag"><label><input name="diag" type="checkbox" ${d.diag ? 'checked' : ''}> @diagnostics</label><label>Computer performance <select name="perf">${PERF_WINDOWS.map(([h, label]) => `<option value="${h}"${h === d.perf ? ' selected' : ''}>${label}</option>`).join('')}</select></label><button class="ghost" type="button" data-preview>Review and edit</button></div>
      ${!selected ? `<details class="support-options"><summary>Reply email and install details</summary><label>Email for a reply<input name="email" type="email" value="${escape(d.email)}" autocomplete="email"></label><label class="support-check"><input name="ids" type="checkbox" ${d.ids ? 'checked' : ''}> Include ${appVersion() ? 'versions' : 'version'} and install ID</label></details>` : ''}
      ${d.report ? `<p class="support-report" data-report>Attached: <strong>${escape(REPORTED[d.report.kind])}</strong> ${escape(d.report.title)} <button class="linkish" type="button" data-unreport>Remove</button></p>` : ''}
      <p class="support-sent" data-sent></p></details>`;
    fitBox(form.elements.message);
    form.querySelector('[data-attach]').ontoggle = e => { if (e.target === form.querySelector('[data-attach]')) { d.open = e.target.open; fitBox(form.elements.message); } };
    const unreport = form.querySelector('[data-unreport]');
    if (unreport) unreport.onclick = () => {
      // An untouched prefill would say "details attached" with nothing attached.
      d.message = unprefilled(d.message);
      if (d.report.kind === 'setup_check') d.kind = d.kindBefore || 'support';
      d.report = null; d.bundle = null; d.key = uid(); renderComposer(); form.elements.message?.focus();
    };
    form.oninput = () => {
      d.message = form.elements.message.value;
      fitBox(form.elements.message);
      if (!selected) { d.email = form.elements.email.value; d.ids = form.elements.ids.checked; }
      d.diag = form.elements.diag.checked;
      const perf = Number(form.elements.perf.value);
      if (perf !== d.perf) { d.perf = perf; d.bundle = null; }
      d.key = uid(); updateSent();
    };
    form.elements.message.onkeydown = e => {
      if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return;
      e.preventDefault(); form.requestSubmit();
    };
    form.querySelector('[data-preview]').onclick = () => openEditor(d);
    // The outcome is kept on the draft and shown in whichever composer is on screen when it settles: the pop-up may
    // have been closed and reopened (a new form) since Send was pressed.
    const shown = text => {
      d.error = text;
      const err = pop.querySelector('[data-compose] [data-error]');
      if (err && draft() === d) { err.textContent = text; err.hidden = !text; }
    };
    form.onsubmit = async e => {
      e.preventDefault();
      if (sending) return;
      shown('');
      if (!d.message.trim()) { shown('Write a message first.'); return; }
      const thread = selected, key = d.key;
      // What goes is what was in the box when Send was pressed, with its own attachment: nothing that changes the draft
      // while it is on its way (a Report this, a setup snapshot) ends up in it.
      const sent = {message: d.message, diag: d.diag, perf: d.perf, report: d.report, bundle: d.bundle, email: d.email.trim(), ids: d.ids, kind: d.kind};
      sending = d; lockSending();
      try {
        let bundle = sent.bundle;
        if (sent.diag && !bundle) {
          bundle = await capture(sent.perf, sent.report);
          // Kept for a retry only while the draft is still the one sent.
          if (d.key === key) { d.bundle = bundle; updateSent(); }
        }
        const body = {message: sent.message, diagnostics: sent.diag ? bundle.id : ''};
        if (!thread) Object.assign(body, {email: sent.email, include_ids: sent.ids}, sent.ids && appVersion() ? {app_version: appVersion()} : {},
          sent.kind !== 'support' ? {kind: sent.kind} : {});
        const next = await call('POST', thread ? `/support/tickets/${encodeURIComponent(thread)}/messages` : '/support/tickets', body, key);
        stateRevision++;
        state.tickets = [next, ...state.tickets.filter(t => t.id !== next.id)];
        // A draft that changed while this was on its way holds newer words: it stays, and stays on screen.
        if (drafts.get(thread) === d && d.key === key) { drafts.delete(thread); selected = next.id; view = 'chat'; }
        settled();
        if (isOpen()) { render(); renderThread(true); renderComposer(); pop.querySelector('[name=message]')?.focus(); }
        say(sent.diag ? 'Sent with diagnostics.' : 'Sent.'); schedule();
      } catch (error) {
        settled();
        // Said where the person is looking: the message did not arrive and is still in the box.
        shown(!error.status || error.status >= 500 ? 'Not sent. Could not reach Tico support. Try again in a moment.' : 'Not sent. ' + error.message);
        if (isOpen() && draft() === d) pop.querySelector('[data-compose] [name=message]')?.focus();
      }
      // A Report this or setup snapshot that came while a new request was on its way starts from what is left of it now.
      if (isOpen()) { openPending(); startPending(); }
    };
    updateSent(); lockSending();
  }
  function updateSent() {
    if (!pop) return;
    const d = draft(), parts = [!selected && d.kind !== 'support' ? 'message marked ' + d.kind : 'message'];
    if (!selected && d.email.trim()) parts.push('email ' + d.email.trim());
    if (!selected && d.ids) parts.push('version ' + (compose?.version || 'unknown'), ...(appVersion() ? ['app version ' + appVersion()] : []), 'install ID ' + compose?.install_id);
    if (d.diag) parts.push('redacted diagnostics' + (d.report ? ' with what you reported' : '') + (d.bundle ? ' (' + Math.max(1, Math.ceil(d.bundle.bytes / 1024)) + ' KB)' : ''));
    pop.querySelector('[data-sent]').textContent = 'Sends to ' + (compose?.to || 'Tico support') + ': ' + parts.join(', ') + '.';
    pop.querySelector('[data-attach-label]').textContent = d.diag ? 'Details attached' : 'Attach details';
  }
  async function openEditor(d) {
    if (editor) return;
    const dialog = document.createElement('dialog'); editor = dialog;
    dialog.className = 'tmodal support-modal'; dialog.setAttribute('aria-label', 'Review and edit diagnostics');
    dialog.innerHTML = `<div class="tmodal-head"><strong>Review and edit diagnostics</strong><span class="spacer"></span><button type="button" class="ghost" data-close aria-label="Close">✕</button></div>
      <div class="tmodal-body"><p>Remove sections or edit the JSON below. Secrets are redacted again before you attach it. Nothing is sent to support here.</p>
      <div data-sections class="support-sections"></div><label for="support-json">Exact attachment</label><textarea id="support-json" class="support-json" spellcheck="false" aria-label="Diagnostics JSON"></textarea>
      <p class="support-sent" data-result role="status">Loading diagnostics…</p><p class="err" data-error role="alert" hidden></p>
      <div class="support-row"><button class="ghost" type="button" data-refresh>Refresh capture</button><button class="ghost" type="button" data-save>Check edits</button><button class="primary" type="button" data-use disabled>Use diagnostics</button></div></div>`;
    document.body.appendChild(dialog); dialog.showModal();
    const area = dialog.querySelector('textarea'), err = dialog.querySelector('[data-error]'), result = dialog.querySelector('[data-result]'), use = dialog.querySelector('[data-use]');
    let base = d.bundle, checked = null, loading = false;
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
      try { show(await capture(d.perf, d.report)); }
      catch (e) { fail(e); }
      finally { loading = false; }
    };
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
      d.bundle = checked; d.diag = true; d.key = uid();
      if (isOpen()) { pop.querySelector('[name=diag]').checked = true; updateSent(); }
      dialog.close();
    };
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.onclose = () => { editor = null; dialog.remove(); };
    if (base) show(base); else await load();
  }
  // A new request still on its way is not changed under it: what came meanwhile waits until that send settles.
  const busyNew = () => !!sending && sending === drafts.get('');
  function openPending() {
    if (pending && busyNew()) return;
    const p = pending; pending = null;
    if (!p || !isOpen() || !state.enabled) return;
    selected = ''; view = 'chat';
    const d = draft();
    if (p.report) {
      // A Setup check marks the request setup (HQ's kind for help with setting up); replacing or removing it gives back
      // the kind the request had before, which may be setup already ("Want help setting up?").
      if (d.report?.kind === 'setup_check') d.kind = d.kindBefore || 'support';
      if (p.report.kind === 'setup_check') { d.kindBefore = d.kind; d.kind = 'setup'; }
      d.report = p.report; d.diag = true; d.open = true; d.bundle = null; d.key = uid();
      // The words on screen go only in the attachment, which is redacted; the message is sent as typed, so it names
      // the kind and nothing else.
      const rest = unprefilled(d.message);
      if (!d.message.trim() || rest !== d.message) d.message = prefill(p.report) + rest;
    }
    render(); renderThread(true); renderComposer(); focusBox();
  }
  window.talkToUs = report => {
    pending = {report: asReport(report)};
    void open({fresh: true});
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
  // "Want help setting up?" starts a new request from text the person reads, edits and sends themselves.
  let starting = null;
  function startPending() {
    if (starting && busyNew()) return;
    if (!starting || !isOpen() || !state.enabled) { starting = null; return; }
    const {kind} = starting, draft = drafts.get(''), unsent = draft?.message.trim() || '';
    // A new request the person had started keeps their words and choices, with the snapshot after them, once.
    const message = unsent.includes(starting.message.trim()) ? unsent
      : unsent ? unsent + '\n\n' + starting.message : starting.message;
    starting = null; selected = ''; view = 'chat';
    // With a Setup check attached, the kind it gives back on Remove is now this one.
    drafts.set('', draft ? {...draft, message, kind, ...(draft.report?.kind === 'setup_check' ? {kindBefore: kind} : {})} :
      {message, email: compose?.email || '', ids: true, diag: true, perf: 24, bundle: null, key: uid(), kind, open: false});
    render(); renderThread(true); renderComposer(); focusBox();
  }
  window.supportState = () => ({enabled: state.enabled, unread: state.unread,
                                 open: state.tickets.filter(t => !ended(t)).map(t => ({id: t.id, unread: !!t.unread}))});
  // Ask's panel (ui/app/ask-panel.js) shows an open request's messages in its chat, writes to it and reads its replies.
  window.supportTicket = id => state.tickets.find(t => t.id === id) || null;
  window.supportReply = async (id, message) => {
    const next = await call('POST', `/support/tickets/${encodeURIComponent(id)}/messages`, {message, diagnostics: ''});
    stateRevision++;
    state.tickets = [next, ...state.tickets.filter(t => t.id !== next.id)];
    mark(); if (isOpen()) render(); schedule();
    return next;
  };
  // Ask's "Loop in Tico Support" switch with no open request: a new request, sent at once, holding the chat it shares
  // (the switch is the consent; ui/app/ask-rules.js askShareText scrubs it). The reply email and install details are
  // the same defaults the form starts with.
  window.supportFile = async message => {
    const body = {message, email: compose?.email || '', include_ids: true, ...(appVersion() ? {app_version: appVersion()} : {})};
    const next = await call('POST', '/support/tickets', body, uid());
    stateRevision++;
    state.tickets = [next, ...state.tickets.filter(t => t.id !== next.id)];
    mark(); if (isOpen()) render(); schedule();
    return next;
  };
  window.supportRead = async id => {
    const revision = stateRevision;
    try {
      const next = await call('POST', `/support/tickets/${encodeURIComponent(id)}/read`);
      if (revision !== stateRevision) return;
      stateRevision++;
      state.tickets = state.tickets.map(x => x.id === next.id ? next : x);
      state.unread = state.tickets.reduce((n, x) => n + (x.unread || 0), 0);
      toasted = state.unread; mark();
    } catch (_) { /* unread until it is seen again */ }
  };
  window.supportStart = (message, kind = 'support') => {
    starting = {message, kind};
    void open({fresh: true});
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

  // The pop-up: a modal dialog beside the sidebar on a computer, a bottom sheet on a phone. Focus moves into the box,
  // Tab stays inside, and Esc, ✕ or a click outside closes it and gives focus back to whatever opened it (Ask).
  function build() {
    if (pop) return pop;
    pop = document.createElement('dialog');
    pop.id = 'support-pop'; pop.className = 'support-pop'; pop.setAttribute('aria-labelledby', 'support-pop-h');
    pop.innerHTML = `<div class="support-pop-in">
      <div class="support-grab" aria-hidden="true"></div>
      <header class="support-head"><h2 id="support-pop-h">Support</h2><span class="spacer"></span>
        <button type="button" class="ghost support-icon" data-new aria-label="New request" title="New request">${ICON.plus}</button>
        <button type="button" class="ghost support-icon" data-close aria-label="Close" title="Close (Esc)">✕</button></header>
      <nav class="support-links" aria-label="Support links"><a href="${typeof GH === 'string' ? GH : 'https://github.com/ticoteam/tico'}/blob/main/docs/README.md" target="_blank" rel="noopener noreferrer" data-docs>View docs</a><span aria-hidden="true">·</span><a href="#/changelog" data-whats-new>What's new</a><span aria-hidden="true">·</span><button type="button" class="linkish" data-mine aria-pressed="false">Your requests</button></nav>
      <p class="support-connection" data-connection role="status"></p>
      <p class="support-sent support-loading" data-loading hidden>Loading…</p>
      <p class="support-off" data-off hidden>Contact support is turned off on this Tico server, so requests cannot be sent from here.</p>
      <div class="support-listview" data-list hidden>
        <div class="support-list-head"><button type="button" class="ghost support-icon" data-back aria-label="Back to the chat" title="Back to the chat">${ICON.back}</button><strong>Your requests</strong></div>
        <ul class="support-list" data-requests aria-label="Your requests"></ul>
        <p class="support-sent support-none" data-none hidden>No requests yet.</p>
      </div>
      <div class="support-view" data-view hidden>
        <div class="support-thread-head" data-head hidden><div class="support-thread-title"><strong data-title></strong><span data-status></span></div>
          <button type="button" class="ghost support-icon support-trash" data-delete aria-label="Delete request" title="Delete request">${ICON.trash}</button></div>
        <div class="support-thread" data-thread aria-label="Support conversation"></div>
        <div class="support-access" data-access hidden></div>
        <p class="support-sent support-closed" data-closed hidden>This request is closed. Start a new request if you need more help.</p>
        <form class="support-write" data-compose novalidate></form>
      </div></div>`;
    document.body.appendChild(pop);
    const choose = id => { selected = id; view = 'chat'; render(); renderThread(true); renderComposer(); void readVisible(); };
    pop.querySelector('[data-close]').onclick = () => pop.close();
    pop.querySelector('[data-new]').onclick = () => { choose(''); focusBox(); };
    pop.querySelector('[data-mine]').onclick = () => { view = view === 'list' ? 'chat' : 'list'; render(); pop.querySelector(view === 'list' ? '[data-back]' : '[data-mine]')?.focus(); };
    pop.querySelector('[data-back]').onclick = () => { view = 'chat'; render(); void readVisible(); focusBox(); };
    pop.querySelector('[data-whats-new]').onclick = () => pop.close();      // the link goes on to What's new
    pop.querySelector('[data-requests]').onclick = e => { const item = e.target.closest('[data-id]'); if (item) { choose(item.dataset.id); focusBox(); } };
    pop.querySelector('[data-thread]').onclick = e => { const v = e.target.closest('[data-view-last]'); if (v) choose(v.dataset.viewLast); };
    pop.querySelector('[data-delete]').onclick = async () => {
      const id = selected;
      if (!id || !confirm('Delete this request and its diagnostics? Tico support will no longer see it. This cannot be undone.')) return;
      try {
        await call('DELETE', `/support/tickets/${encodeURIComponent(id)}`);
        stateRevision++;
        state.tickets = state.tickets.filter(t => t.id !== id); drafts.delete(id);
        selected = latest()?.id || ''; render(); renderComposer(); say('Request deleted.');
      } catch (e) { say(e.message, true); }
    };
    // A click that starts and ends on the backdrop (the dialog itself, outside its box) closes it.
    let downOutside = false;
    pop.addEventListener('pointerdown', e => { downOutside = e.target === pop; });
    pop.addEventListener('click', e => { if (e.target === pop && downOutside) pop.close(); downOutside = false; });
    pop.addEventListener('keydown', e => {
      if (e.key !== 'Tab') return;
      const all = [...pop.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
        .filter(el => !el.disabled && el.checkVisibility({visibilityProperty: true}));   // not inside a closed Attach details
      if (!all.length) return;
      const first = all[0], end = all.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); end.focus(); }
      else if (!e.shiftKey && document.activeElement === end) { e.preventDefault(); first.focus(); }
    });
    // On a phone the sheet can be swiped down from its top to close it.
    const head = pop.querySelector('.support-pop-in');
    let swipe = null;
    head.addEventListener('touchstart', e => {
      const top = e.target.closest('.support-grab, .support-head, .support-links');
      swipe = top && e.touches.length === 1 && matchMedia('(max-width: 760px)').matches ? {y: e.touches[0].clientY, dy: 0} : null;
    }, {passive: true});
    head.addEventListener('touchmove', e => {
      if (!swipe) return;
      swipe.dy = Math.max(0, e.touches[0].clientY - swipe.y);
      head.style.transform = swipe.dy ? `translateY(${swipe.dy}px)` : '';
    }, {passive: true});
    const settle = () => { const dy = swipe?.dy || 0; swipe = null; head.style.transform = ''; if (dy > 80) pop.close(); };
    head.addEventListener('touchend', settle);
    head.addEventListener('touchcancel', () => { swipe = null; head.style.transform = ''; });
    pop.addEventListener('close', () => {
      editor?.close();
      const back = opener?.isConnected && opener.getClientRects().length ? opener : visibleQuestion();
      opener = null; back?.focus();
      schedule();
    });
    return pop;
  }
  const visibleQuestion = () => ['ask-row', 'mobile-ask'].map(id => document.getElementById(id)).find(el => el?.getClientRects().length);
  // On a phone the sheet sits on the bottom of what is visible: with the keyboard up, the space above it. The browser
  // reports that space in the visual viewport, whose height shrinks both for the keyboard and for pinch zoom, so
  // height × scale is the visible height in page pixels with zoom taken out. At normal scale the sheet follows the
  // visual viewport where the browser has panned it; zoomed in, it stands on the bottom of the page, where panning
  // reaches it, so a keyboard opened or closed while zoomed still resizes it and nothing from before the zoom lingers.
  function fit() {
    if (!pop) return;
    const vv = window.visualViewport, page = document.documentElement.clientHeight || window.innerHeight;
    const scale = vv?.scale || 1, height = Math.min(page, (vv?.height || window.innerHeight) * scale);
    const bottom = Math.abs(scale - 1) < 0.01 ? (vv?.offsetTop || 0) + height : page;
    let moved = false;
    for (const [name, px] of [['--support-visible', height], ['--support-bottom', bottom]])
      if (pop.style.getPropertyValue(name) !== px + 'px') { pop.style.setProperty(name, px + 'px'); moved = true; }   // only on a change: typing fires these
    if (moved) fitVisibleBox();   // a shorter sheet leaves the box less room
  }
  window.visualViewport?.addEventListener('resize', fit);
  window.visualViewport?.addEventListener('scroll', fit);
  window.addEventListener('resize', fit);
  function focusBox() {
    if (!isOpen()) return;
    const box = pop.querySelector('[data-view]:not([hidden]) [data-compose]:not([hidden]) [name=message]');
    if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    else if (!pop.contains(document.activeElement) || document.activeElement === pop) pop.querySelector('[data-close]').focus();
  }
  // Loads this person's requests and what a new one sends; a failure keeps what was loaded before.
  async function load() {
    const actor = typeof S === 'undefined' ? '' : S.me?.id;
    if (owner !== actor) { stateRevision++; if (owner) browserFailures.length = 0; owner = actor; drafts = new Map(); selected = ''; view = 'chat'; compose = null; state = {enabled: null, tickets: [], unread: 0}; }
    try {
      const revision = stateRevision;
      const data = await call('GET', '/support/tickets');
      if (revision === stateRevision) accept(data);
      if (state.enabled) compose = await call('GET', '/support/compose');
      note('');
    } catch (e) { if (state.enabled === null) note(e.message, true); else note(POLL_FAILED, true); }
  }
  // how: {ticket} opens that request, {fresh} a new one, {list} Your requests; nothing opens the latest recent request
  // (or Your requests, see `landing`), unless the person left words unsent in the one they had open.
  async function open(how = {}) {
    build();
    if (!isOpen()) {
      opener = document.activeElement !== document.body ? document.activeElement : null;
      pop.showModal();
    }
    fit();
    // Where it is going is set before anything loads, so the chat left open last time is not shown or read on the way.
    if (how.list) view = 'list';
    else if (how.fresh || how.ticket) { view = 'chat'; selected = how.fresh ? '' : how.ticket; }
    const run = loading = load();
    render(); focusBox();
    await run;
    if (loading !== run || !isOpen()) return;
    loading = null;
    const kept = drafts.get(selected)?.message.trim() && (!selected || current() && !ended(current()));
    if (how.list) view = 'list';
    else {
      view = 'chat';
      if (how.ticket && state.tickets.some(t => t.id === how.ticket)) selected = how.ticket;
      else if (how.fresh) selected = '';
      else if (!kept) { selected = landing()?.id || ''; if (!selected && latest()) view = 'list'; }
    }
    if (selected && !current()) selected = latest()?.id || '';
    render(); renderThread(true);
    if (state.enabled) renderComposer();
    openPending(); startPending();
    if (view === 'list') pop.querySelector('[data-back]')?.focus(); else focusBox();
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
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { wake(); void pollAccess(); } });
  window.addEventListener('focus', wake);
  window.addEventListener('load', () => setTimeout(async () => {
    if (typeof S === 'undefined' || !S.me || state.enabled !== null) return;
    const revision = stateRevision;
    try { const data = await call('GET', '/support/tickets'); if (revision === stateRevision) accept(data); schedule(); } catch (_) { /* opening Support tries again */ }
  }, 3000));
  window.addEventListener('load', () => setTimeout(pollAccess, 1000));
})();
