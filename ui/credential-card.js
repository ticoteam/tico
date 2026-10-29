/* The credential card: a bot needs a secret, so it asks for it inside the chat. The person types it here and
   it goes straight to Credentials (POST /api/v2/credential-requests/{id}/save); the bot and the model never see
   the value. A message with `refs.credential_request` has a host element (`data-credential-host`) that
   window.credentialCards.mount(root, {get, post, esc, toast, reload}) fills from the request.
   A request of kind `login` asks for a website login instead: username, password and an optional 2FA setup key
   (a base32 key or an otpauth:// URI), saved as {username, password, totp?}. A host element `data-login-host`
   (a site reconnect card with no request behind it) is the same form saved by a credential administrator straight
   to the vault: the login (POST /api/v2/credentials), its grant to the bot, then the reconnect's Done.
   The value lives only in the input (and in `typed` while the chat repaints around it): never in storage, a URL,
   a toast, the console or an error message. */
'use strict';
(function () {
  const css = `
.cc-card{align-self:flex-start;width:min(560px,100%);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0;background:var(--surface)}
.cc-card b{display:block;margin-bottom:6px}
.cc-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cc-row input,.cc-login input{flex:1 1 220px;min-width:0;min-height:44px;font-size:16px}
.cc-row button{min-height:44px}
.cc-login{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.cc-login [name=totp]{grid-column:1/-1}
@media (max-width:480px){.cc-login{grid-template-columns:minmax(0,1fr)}}
.cc-login .cc-row{grid-column:1/-1}
.cc-help{font-size:12.5px;text-decoration:underline;text-underline-offset:2px;white-space:nowrap}
.cc-note{font-size:12.5px;color:var(--muted);margin:6px 0 0}
.cc-err{font-size:12.5px;color:var(--fail);margin:6px 0 0}
.cc-done{font-size:12.5px;color:var(--muted);padding:6px 0}`;

  const typed = new Map();      // request id -> what was typed so far (a string, or a login's fields), kept across repaints
  const settled = new Map();    // request id -> its final view
  const SECRET_ATTRS = 'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" data-lpignore="true" data-1p-ignore data-form-type="other"';
  const LOGIN_FIELDS = ['username', 'password', 'totp'];

  function style() {
    if (document.getElementById('cc-style')) return;
    const el = document.createElement('style');
    el.id = 'cc-style';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // A local check that never echoes the value: what is wrong, or ''.
  function problem(raw, format) {
    const value = String(raw || '').trim();
    if (!value) return 'Enter the value.';
    if (/\s/.test(value)) return 'No spaces or line breaks.';
    if (String(format || '').includes(':')) {
      const at = value.indexOf(':');
      if (at < 1 || at === value.length - 1) return 'Use this format: ' + format;
    }
    return '';
  }
  // A login needs its password; a password may hold spaces, so it is sent as typed.
  const loginProblem = values => values.password ? '' : 'Enter the password.';
  const sitesOf = v => (Array.isArray(v.sites) ? v.sites : []).filter(Boolean);

  // The login's three inputs, the same in the chat card, a site's reconnect card and the vault's dialog.
  const loginInputs = (esc, username = '') => `
        <input type="text" name="username" ${SECRET_ATTRS} placeholder="Username" aria-label="Username" value="${esc(username)}">
        <input type="password" name="password" ${SECRET_ATTRS} placeholder="Password" aria-label="Password">
        <input type="password" name="totp" ${SECRET_ATTRS} placeholder="2FA setup key (optional)" aria-label="2FA setup key">`;

  function view(v, esc) {
    const login = v.kind === 'login', sites = sitesOf(v);
    const what = v.label || (login ? 'the login for ' + (sites[0] || 'the site') : v.env);
    if (v.status === 'saved') return `<div class="cc-done" data-status="saved">Saved. ${esc(what)} is set for ${esc(v.bot_name || v.bot)}.</div>`;
    if (v.status === 'cancelled') return '<div class="cc-done" data-status="cancelled">Not now.</div>';
    const help = /^https:\/\//i.test(v.help_url || '')
      ? `<a class="cc-help" href="${esc(v.help_url)}" target="_blank" rel="noopener noreferrer">Get one</a>` : '';
    const title = `<b>${esc(v.title || (v.bot_name || v.bot) + ' needs ' + what)}</b>`;
    if (!v.can_save) return `<div class="cc-card" data-status="pending">${title}<p class="cc-note">${esc(v.note || '')}</p></div>`;
    const buttons = `<button class="primary" type="submit">Save</button>
        <button class="ghost" type="button" data-cc-cancel>Not now</button>`;
    const form = login
      ? `<form class="cc-login" data-cc-form data-kind="login" autocomplete="off">${loginInputs(esc, v.username || '')}
        <div class="cc-row">${help}${buttons}</div></form>`
      : `<form class="cc-row" data-cc-form autocomplete="off">
        <input type="password" name="value" ${SECRET_ATTRS} placeholder="${esc(v.format || '')}" aria-label="${esc(v.env)}">
        ${help}${buttons}</form>`;
    return `<div class="cc-card" data-status="pending"${login ? ' data-kind="login"' : ''}>${title}${form}
      <p class="cc-err" data-cc-error role="alert" hidden></p>
      <p class="cc-note">Goes straight to Credentials. The bot never sees it.</p></div>`;
  }

  // `ops.save(body)` stores what was typed and returns the card's next view; `ops.cancel()` returns it for Not now.
  function paint(host, v, deps, ops) {
    const {esc, toast, reload} = deps;
    const id = v.id || host.dataset.credentialHost || host.dataset.loginHost;
    if (v.status !== 'pending') { settled.set(id, v); typed.delete(id); }
    host.innerHTML = view(v, esc);
    const form = host.querySelector('[data-cc-form]');
    if (!form) return;
    const login = form.dataset.kind === 'login', error = host.querySelector('[data-cc-error]');
    const show = text => { error.textContent = text; error.hidden = !text; };
    const inputs = login ? LOGIN_FIELDS.map(n => form.elements[n]) : [form.elements.value];
    const kept = typed.get(id);
    if (kept != null) {
      if (login) inputs.forEach(input => { if (kept[input.name] != null) input.value = kept[input.name]; });
      else inputs[0].value = kept;
    }
    for (const input of inputs) input.oninput = () => {
      typed.set(id, login ? Object.fromEntries(inputs.map(i => [i.name, i.value])) : input.value);
      show('');
    };
    form.onsubmit = async event => {
      event.preventDefault();
      let body;
      if (login) {
        const values = {username: inputs[0].value.trim(), password: inputs[1].value, totp: inputs[2].value.trim()};
        const bad = loginProblem(values);
        if (bad) { show(bad); inputs[1].focus(); return; }
        body = {username: values.username, password: values.password, ...(values.totp ? {totp: values.totp} : {})};
      } else {
        const bad = problem(inputs[0].value, v.format);
        if (bad) { show(bad); inputs[0].focus(); return; }
        body = {value: inputs[0].value.trim()};
      }
      const buttons = form.querySelectorAll('button');
      buttons.forEach(b => { b.disabled = true; });
      try {
        const saved = await ops.save(body);
        inputs.forEach(input => { input.value = ''; });
        paint(host, saved, deps, ops);
        reload?.();
      } catch (e) {
        buttons.forEach(b => { b.disabled = false; });
        // The server's words about the format, never the value.
        const code = e?.body?.error?.code || e?.code;
        if (code === 'format' || (login && e?.status === 422)) show(e.message || 'That does not look right.');
        else toast?.(e?.message || 'Could not save', true);
        inputs[login ? 1 : 0].focus();
      }
    };
    host.querySelector('[data-cc-cancel]').onclick = async () => {
      try { const next = await ops.cancel(); if (next) { paint(host, next, deps, ops); reload?.(); } }
      catch (e) { toast?.(e?.message || 'Could not cancel', true); }
    };
  }

  // A site's login typed by a credential administrator: the login, its grant to the bot, then the card's Done. The
  // reconnect card folds the form away itself (its Enter login), so there is nothing to cancel.
  function directOps(host, v, deps) {
    const {post} = deps, d = host.dataset;
    return {
      async save(body) {
        const made = await post('/v2/credentials', {kind: 'login', name: d.host, sites: [d.host], source: 'Login card', ...body});
        await post(`/v2/credentials/${encodeURIComponent(made.id)}/grants`, {subject: 'bot:' + d.bot});
        if (d.done) await post(d.done, {task: d.task || '', key: d.key || ''});
        return {...v, status: 'saved'};
      },
      async cancel() { typed.delete(v.id); return null; },
    };
  }

  async function mount(root, deps) {
    if (!root) return;
    style();
    const {get, post} = deps;
    const requestOps = id => ({
      save: body => post(`/v2/credential-requests/${encodeURIComponent(id)}/save`, body),
      cancel: () => post(`/v2/credential-requests/${encodeURIComponent(id)}/cancel`, {}),
    });
    for (const host of root.querySelectorAll('[data-login-host]')) {
      const id = host.dataset.loginHost, d = host.dataset;
      const v = settled.get(id) || {id, status: 'pending', kind: 'login', can_save: true, sites: [d.host], bot: d.bot,
        bot_name: d.botName || d.bot, title: 'Log in to ' + d.host};
      paint(host, v, deps, directOps(host, v, deps));
    }
    for (const host of root.querySelectorAll('[data-credential-host]')) {
      const id = host.dataset.credentialHost;
      if (settled.has(id)) { paint(host, settled.get(id), deps, requestOps(id)); continue; }
      try { paint(host, await get(`/v2/credential-requests/${encodeURIComponent(id)}`), deps, requestOps(id)); }
      catch { host.innerHTML = '<div class="cc-done">This card is not yours to see.</div>'; }
    }
  }

  window.credentialCards = {mount, problem, loginInputs, SECRET_ATTRS};
})();
