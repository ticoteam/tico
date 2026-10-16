/* Support uses the existing ticket transport and the app's right rail. Drafts stay on this page's account in memory;
   an attachment is an immutable server preview, never silently replaced when Send is pressed. */
'use strict';
(function () {
  'use strict';
  const API_BASE = () => (typeof API === 'string' ? API : '/api') + '/v2';
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const say = (msg, error) => typeof toast === 'function' && toast(msg, error);
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
  // `hours` of each computer's performance history go with the attachment (0: none).
  async function capture(hours) {
    const app = await appShell();
    return browserFailures.length || app
      ? call('POST', '/support/diagnostics/capture', {browser: browserFailures, performance_hours: hours, ...(app ? {app} : {})})
      : call('GET', '/support/diagnostics?performance_hours=' + hours);
  }
  const PERF_WINDOWS = [[0, 'None'], [24, '1 day'], [168, '7 days'], [336, '14 days']];
  const statuses = {open: 'Open', answered: 'Answered', closed: 'Closed', gone: 'Removed'};
  const ICON = {
    send: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 12h15M13 6l6 6-6 6"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2.5 4h11M6.5 4V2.5h3V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v4.5M9.2 6.5v4.5"/></svg>',
    back: '<svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7.5 3l-3 3 3 3"/></svg>',
  };
  // `viewing`: a conversation (or a new request) is open in place of the list of requests.
  let state = {enabled: null, tickets: [], unread: 0}, selected = '', viewing = false, rail = null, timer, busyRefresh = null;
  let owner = '', toasted = 0, compose = null, drafts = new Map(), editor = null;
  const when = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}); };
  const short = iso => {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : d.toLocaleDateString([], {month: 'short', day: 'numeric'});
  };
  const title = t => (t.message || '').replace(/\s+/g, ' ').trim().slice(0, 90) || 'Request';
  const last = t => (t.messages || []).at(-1)?.created || t.created;
  const inThread = () => viewing || !state.tickets.length;
  const draft = () => {
    if (!drafts.has(selected)) drafts.set(selected, {message: '', email: compose?.email || '', ids: true, diag: !selected, perf: 24, bundle: null, key: uid(), kind: 'support'});
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
  function mark() {
    const link = document.getElementById('help-open'), n = state.unread;
    if (!link) return;
    if (n) {
      link.setAttribute('data-support-unread', n);
      link.setAttribute('data-support-count', n > 9 ? '9+' : String(n));
      link.setAttribute('aria-label', `Overview and support, ${n} new support ${n === 1 ? 'reply' : 'replies'}`);
    } else {
      link.removeAttribute('data-support-unread'); link.removeAttribute('data-support-count');
      link.setAttribute('aria-label', 'Overview and support');
    }
  }
  function accept(data) {
    state = {enabled: data.enabled !== false, tickets: data.tickets || [], unread: data.unread || 0};
    if (state.unread > toasted) say('Tico support replied.');
    toasted = state.unread;
    mark();
    if (!rail?.isConnected) return;
    if (selected && !current()) { selected = ''; viewing = false; renderComposer(); }
    renderTickets(); renderThread();
    // A reply that lands in the conversation someone has open in front of them is read.
    if (inThread() && current()?.unread && document.hasFocus()) void readVisible();
  }
  function note(message, error = false) {
    const el = rail?.querySelector('[data-connection]');
    if (el) { el.textContent = message; el.classList.toggle('err', error); }
  }
  async function refresh() {
    if (busyRefresh) return busyRefresh;
    busyRefresh = (async () => {
      try {
        const data = await call('POST', '/support/tickets/refresh');
        accept(data);
        note(data.refresh_failed ? 'Could not check for replies just now. Tico will try again.' : '', !!data.refresh_failed);
      } catch (e) { note(e.message, true); }
      finally { busyRefresh = null; }
      return state;
    })();
    return busyRefresh;
  }
  function schedule() {
    clearTimeout(timer);
    if (!state.enabled) return;
    timer = setTimeout(async () => {
      if (!document.hidden && state.tickets.some(t => !['closed', 'gone'].includes(t.status))) await refresh();
      schedule();
    }, rail?.isConnected ? 30000 : 300000);
  }
  async function readVisible() {
    const t = current();
    if (document.hidden || !rail?.isConnected || !inThread() || !t?.unread) return;
    // Reading follows opening a conversation (or having it open), not a background fetch.
    try {
      const next = await call('POST', `/support/tickets/${encodeURIComponent(t.id)}/read`);
      state.tickets = state.tickets.map(x => x.id === next.id ? next : x);
      state.unread = state.tickets.reduce((n, x) => n + (x.unread || 0), 0);
      toasted = state.unread; mark(); renderTickets(); renderThread();
    } catch (_) { /* unread remains until the conversation is opened again */ }
  }
  const badge = status => `<span class="support-badge s-${statuses[status] ? status : 'other'}">${escape(statuses[status] || status)}</span>`;
  function renderTickets() {
    const list = rail.querySelector('[data-requests]');
    const items = [...state.tickets].sort((a, b) => (Date.parse(last(b)) || 0) - (Date.parse(last(a)) || 0));
    list.innerHTML = items.map(t => `<li><button type="button" class="support-item${t.unread ? ' unread' : ''}" data-id="${escape(t.id)}">
      <span class="support-item-top"><span class="support-item-title">${escape(title(t))}</span>${t.unread ? `<span class="support-dot" role="img" aria-label="${t.unread} new ${t.unread === 1 ? 'reply' : 'replies'}"></span>` : ''}</span>
      <span class="support-item-meta">${badge(t.status)}<time datetime="${escape(last(t))}" title="${escape(when(last(t)))}">${escape(short(last(t)))}</time></span></button></li>`).join('');
    rail.querySelector('[data-list]').hidden = inThread();
    rail.querySelector('[data-view]').hidden = !inThread();
    rail.querySelector('[data-new]').hidden = !state.tickets.length || (inThread() && !selected);
  }
  function renderThread(pin = false) {
    const host = rail.querySelector('[data-thread]'), t = current();
    const bubble = (body, staff, at, diag) => `<div class="support-msg ${staff ? 'staff' : 'you'}"><span class="support-who">${staff ? 'Tico support' : 'You'}${at ? ` · <time datetime="${escape(at)}">${escape(when(at))}</time>` : ''}</span><div class="support-body">${escape(body)}</div>${diag ? '<small class="support-sent">Diagnostics attached</small>' : ''}</div>`;
    const after = {open: 'Delivered to Tico support. Their reply will appear here.', gone: 'Tico support removed this request.'};
    const html = t ? bubble(t.message, false, t.created, t.sent?.diagnostics) + (t.messages || []).map(m => bubble(m.body, m.from === 'staff', m.created, m.has_diagnostics)).join('')
        + (after[t.status] ? `<p class="support-delivered">${after[t.status]}</p>` : '')
      : `<div class="support-empty"><h3>Ask Tico support</h3><p>Tell us what happened and what you expected. A person on the Tico team replies here, and a number on the ? button tells you when they have.</p></div>`;
    // stays on the newest message only while the reader is there, so a poll never moves someone reading back
    // (ui/app/stick-scroll.js); a reply that lands while they are scrolled up shows the "New reply" pill instead
    const stick = stickScroll(host), id = t ? t.id : '';
    // a conversation just opened, a message just sent, or another one (or the empty state) now in view: start at its newest
    if (pin || host.dataset.ticket !== id) stick.toBottom();
    if (host.dataset.shown !== html) {
      const fresh = !pin && !!host.dataset.shown && host.dataset.ticket === id;
      stick.update(() => { host.innerHTML = html; host.dataset.shown = html; host.dataset.ticket = id; }, {fresh});
    }
    rail.querySelector('[data-back]').hidden = !state.tickets.length;
    rail.querySelector('[data-title]').textContent = t ? title(t) : 'New request';
    rail.querySelector('[data-status]').innerHTML = t ? badge(t.status) : '';
    rail.querySelector('[data-delete]').hidden = !t;
    const closed = t && ['closed', 'gone'].includes(t.status);
    rail.querySelector('[data-compose]').hidden = !!closed;
    rail.querySelector('[data-closed]').hidden = !closed;
  }
  function renderComposer() {
    const d = draft(), form = rail.querySelector('[data-compose]');
    form.innerHTML = `<label class="sr-only" for="support-message">Message to support</label>
      <div class="support-input"><textarea id="support-message" name="message" maxlength="4000" rows="3" placeholder="${selected ? 'Write a reply…' : 'How can we help?'}" required aria-describedby="support-keys">${escape(d.message)}</textarea>
      <button class="support-send" type="submit" aria-label="Send" title="Send (Enter)">${ICON.send}</button></div>
      <p class="err support-error" data-error role="alert" hidden></p>
      <small class="sr-only" id="support-keys">Enter sends. Shift+Enter adds a new line.</small>
      <div class="support-diag"><label><input name="diag" type="checkbox" ${d.diag ? 'checked' : ''}> @diagnostics</label><label>Computer performance <select name="perf">${PERF_WINDOWS.map(([h, label]) => `<option value="${h}"${h === d.perf ? ' selected' : ''}>${label}</option>`).join('')}</select></label><button class="ghost" type="button" data-preview>Review and edit</button></div>
      ${!selected ? `<details class="support-options"><summary>Reply email and install details</summary><label>Email for a reply<input name="email" type="email" value="${escape(d.email)}" autocomplete="email"></label><label class="support-check"><input name="ids" type="checkbox" ${d.ids ? 'checked' : ''}> Include ${appVersion() ? 'versions' : 'version'} and install ID</label></details>` : ''}
      <p class="support-sent" data-sent></p>`;
    form.oninput = () => {
      d.message = form.elements.message.value;
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
    form.onsubmit = async e => {
      e.preventDefault();
      const err = form.querySelector('[data-error]'), submit = form.querySelector('[type=submit]');
      if (submit.disabled) return;
      err.hidden = true;
      if (!d.message.trim()) { err.textContent = 'Write a message first.'; err.hidden = false; return; }
      const locked = [...form.querySelectorAll('input, textarea, button'), ...rail.querySelectorAll('[data-requests] button, [data-new], [data-back], [data-delete]')];
      locked.forEach(el => { el.disabled = true; });
      submit.setAttribute('aria-busy', 'true');
      const thread = selected;
      try {
        if (d.diag && !d.bundle) { d.bundle = await capture(d.perf); updateSent(); }
        const body = {message: d.message, diagnostics: d.diag ? d.bundle.id : ''};
        if (!thread) Object.assign(body, {email: d.email.trim(), include_ids: d.ids}, d.ids && appVersion() ? {app_version: appVersion()} : {},
          d.kind !== 'support' ? {kind: d.kind} : {});
        const next = await call('POST', thread ? `/support/tickets/${encodeURIComponent(thread)}/messages` : '/support/tickets', body, d.key);
        state.tickets = [next, ...state.tickets.filter(t => t.id !== next.id)];
        drafts.delete(thread); selected = next.id; viewing = true;
        locked.forEach(el => { el.disabled = false; });
        if (rail?.isConnected) { renderTickets(); renderThread(true); renderComposer(); rail.querySelector('[name=message]')?.focus(); }
        say(d.diag ? 'Sent with diagnostics.' : 'Sent.'); schedule();
      } catch (error) {
        // Said where the person is looking: the message did not arrive and is still in the box.
        err.textContent = 'Not sent. ' + error.message + ' Your message is still here.'; err.hidden = false;
        locked.forEach(el => { el.disabled = false; });
        submit.removeAttribute('aria-busy');
        form.elements.message?.focus();
      }
    };
    updateSent();
  }
  function updateSent() {
    if (!rail?.isConnected) return;
    const d = draft(), parts = [!selected && d.kind !== 'support' ? 'message marked ' + d.kind : 'message'];
    if (!selected && d.email.trim()) parts.push('email ' + d.email.trim());
    if (!selected && d.ids) parts.push('version ' + (compose?.version || 'unknown'), ...(appVersion() ? ['app version ' + appVersion()] : []), 'install ID ' + compose?.install_id);
    if (d.diag) parts.push('redacted diagnostics' + (d.bundle ? ' (' + Math.max(1, Math.ceil(d.bundle.bytes / 1024)) + ' KB)' : ''));
    rail.querySelector('[data-sent]').textContent = 'Sends to ' + compose?.to + ': ' + parts.join(', ') + '.';
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
      try { show(await capture(d.perf)); }
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
      if (rail?.isConnected) { rail.querySelector('[name=diag]').checked = true; updateSent(); }
      dialog.close();
    };
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.onclose = () => { editor = null; dialog.remove(); };
    if (base) show(base); else await load();
  }
  // "Want help setting up?" starts a new request from text the person reads, edits and sends themselves.
  let pending = null;
  function startPending() {
    if (!pending || !rail?.isConnected) return;
    const {kind} = pending, draft = drafts.get(''), unsent = draft?.message.trim() || '';
    // A new request the person had started keeps their words and choices, with the snapshot after them, once.
    const message = unsent.includes(pending.message.trim()) ? unsent
      : unsent ? unsent + '\n\n' + pending.message : pending.message;
    pending = null; selected = ''; viewing = true;
    drafts.set('', draft ? {...draft, message, kind} :
      {message, email: compose?.email || '', ids: true, diag: true, perf: 24, bundle: null, key: uid(), kind});
    renderTickets(); renderThread(true); renderComposer();
    rail.querySelector('[name=message]')?.focus();
  }
  window.supportStart = (message, kind = 'support') => {
    pending = {message, kind};
    if (rail?.isConnected) startPending(); else location.hash = '#/help';
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
  window.supportLeave = () => { editor?.close(); rail = null; schedule(); };
  window.supportHelp = async page => {
    const actor = typeof S === 'undefined' ? '' : S.me?.id;
    if (owner !== actor) { if (owner) browserFailures.length = 0; owner = actor; drafts = new Map(); selected = ''; viewing = false; compose = null; state = {enabled: null, tickets: [], unread: 0}; }
    try {
      accept(await call('GET', '/support/tickets'));
      if (!page.isConnected) return;
      if (!state.enabled) {
        page.querySelector('[data-help-support]')?.remove();
        const off = document.createElement('p'); off.className = 'support-off'; off.setAttribute('data-support-off', '');
        off.textContent = 'Contact support is turned off on this Tico server, so requests cannot be sent from here.';
        const actions = page.querySelector('.help-actions');
        if (actions) actions.after(off); else page.appendChild(off);
        return;
      }
      compose = await call('GET', '/support/compose');
      if (!page.isConnected) return;
      const main = page.parentElement; main.classList.add('help-layout');
      rail = document.createElement('aside'); rail.id = 'support-rail'; rail.className = 'support-rail'; rail.setAttribute('aria-label', 'Support');
      rail.innerHTML = `<div class="rail-sec support-head"><h2>Support</h2><span class="spacer"></span><button type="button" class="ghost" data-new>New request</button></div>
        <p class="support-connection" data-connection role="status"></p>
        <div class="support-listview" data-list><ul class="support-list" data-requests aria-label="Your requests"></ul></div>
        <div class="support-view" data-view>
          <div class="support-thread-head"><button type="button" class="ghost support-icon" data-back aria-label="All requests" title="All requests">${ICON.back}</button>
            <div class="support-thread-title"><strong data-title></strong><span data-status></span></div>
            <button type="button" class="ghost support-icon support-trash" data-delete aria-label="Delete request" title="Delete request">${ICON.trash}</button></div>
          <div class="support-thread" data-thread aria-label="Support conversation"></div>
          <p class="support-sent support-closed" data-closed hidden>This request is closed. Start a new request if you need more help.</p>
          <form class="support-write rail-sec" data-compose novalidate></form>
        </div>`;
      main.appendChild(rail);
      main.insertAdjacentHTML('beforeend', railEdgeHTML('right', 'Resize support panel', 'support-rail'));
      const choose = id => { selected = id; viewing = true; renderTickets(); renderThread(true); renderComposer(); void readVisible(); };
      rail.querySelector('[data-requests]').onclick = e => { const item = e.target.closest('[data-id]'); if (item) choose(item.dataset.id); };
      rail.querySelector('[data-new]').onclick = () => { choose(''); rail.querySelector('[name=message]')?.focus(); };
      rail.querySelector('[data-back]').onclick = () => { viewing = false; selected = ''; renderTickets(); renderThread(); };
      rail.querySelector('[data-delete]').onclick = async () => {
        const id = selected;
        if (!id || !confirm('Delete this request and its diagnostics? Tico support will no longer see it. This cannot be undone.')) return;
        try {
          await call('DELETE', `/support/tickets/${encodeURIComponent(id)}`);
          state.tickets = state.tickets.filter(t => t.id !== id); drafts.delete(id);
          selected = ''; viewing = false; renderTickets(); renderThread(); renderComposer(); say('Request deleted.');
        } catch (e) { say(e.message, true); }
      };
      if (selected && !current()) { selected = ''; viewing = false; }
      renderTickets(); renderThread(true); renderComposer(); void readVisible(); startPending();
      await refresh(); schedule();
    } catch (e) { if (page.isConnected) { const p = document.createElement('p'); p.className = 'err'; p.textContent = e.message; page.appendChild(p); } }
  };
  window.supportPoll = refresh;
  // Replies are checked when the app comes back into view or focus, and on the poll above; there is no Refresh button.
  const wake = () => { if (state.enabled) { void refresh(); schedule(); } };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) wake(); });
  window.addEventListener('focus', wake);
  window.addEventListener('load', () => setTimeout(async () => {
    if (typeof S === 'undefined' || !S.me || state.enabled !== null) return;
    try { accept(await call('GET', '/support/tickets')); schedule(); } catch (_) { /* Help offers a retry */ }
  }, 3000));
})();
