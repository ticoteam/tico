/* Sign a model in on a computer from the browser (backend/model_login.py, runner-rs/src/login.rs).
   The dialog shows what the model's own CLI prints: a link and a one-time code, or a link and a
   field for the code the sign-in page hands back. This page never sees the resulting credential;
   the CLI keeps it on the computer, and the chip flips when the computer reports it is ready.
   The same module signs a login out (after a confirmation naming the bots that stop), switches it to
   another account (a sign-in on the side: cancelling leaves the old account working), and lets the
   company's shared key sign a signed-out computer login in again. `actionsHTML` draws those buttons for
   any page that lists a login: Settings > Computers and Manage subscription today.
   Clear key / Replace key / Add key (backend/computer_keys.py) are here too, for a login a model key signs in: the key
   is pasted once, stored in the vault for that computer and shown only by its preview. A key the company shares with
   every computer first asks "this computer only" or "all of them", listing them (`scopeHTML`, `chosenScope`).
   Switch to API key (`toKey`) is Replace key for the computer's own login on a subscription: the key is checked while the
   subscription stays signed in and in use, and only once it is in place is the subscription signed out.
   On a hosted Tico (config `hosted`, TICO_HOSTED) the person has no terminal on the computer, so the sign-in shows only
   the provider's link and code (never what else the CLI printed; a failure gives its message), never points to a
   terminal, and closes by itself once it has signed in or its cancel is confirmed.
   Connect (`connect`, Settings > Models, ui/app/subscriptions.js) follows a sign-in the server started for a company
   subscription on a computer it chose. It ends when the server has saved the result, which this page never sees. */
'use strict';
(function () {
  const RUNTIME_NAMES = {codex: 'Codex (ChatGPT)', claude: 'Claude Code'};
  const POLL_MS = 2000, STALLED_S = 30;
  const text = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  // `finishing`: a switch is putting the new account in place, past the point where it can be cancelled.
  const active = state => ['requested', 'starting', 'waiting', 'finishing'].includes(state);
  const hosted = () => typeof S !== 'undefined' && !!S.config?.hosted;

  // The computer shows a sign-in, switch or sign-out only in the readiness it reports next. Look again until it has
  // reported since the change finished (`since`, the server's time on the finished row; `readiness_at` is when the
  // computer last reported) and that report shows `want` ('ready' or 'signed_out') for this login, redrawing
  // Settings > Computers. About a minute at most; until then its row and pop-up say "Waiting for <computer>"
  // (`machineLoginPending`, ui/app/computer-detail.js).
  const pending = window.machineLoginPending = window.machineLoginPending || new Map();
  function refreshUntil(runnerId, runtime, profile, want, since) {
    const after = Date.parse(since) || Date.now();
    let tries = 0;
    pending.set(runnerId, Date.now() + 65000);
    const look = async () => {
      tries += 1;
      try { if (typeof loadSettings === 'function') await loadSettings(); } catch { /* the next try asks again */ }
      try { if (typeof gsRefresh === 'function') await gsRefresh(); } catch { /* same */ }
      let shown = false;
      try {
        const machine = SETTINGS_DATA.machines.find(row => row.id === runnerId);
        const ready = profile ? machine?.profiles?.find(p => p.name === profile)?.runtimes?.[runtime]?.signed_in === true
          : machine?.readiness?.runtimes?.[runtime]?.authenticated === 'ready';
        shown = Date.parse(machine?.readiness_at) > after && (want === 'ready' ? ready : !ready);
      } catch { /* not on Settings */ }
      if (shown || tries >= 20) {
        pending.delete(runnerId);
        try { if (typeof renderSettingsMachines === 'function') renderSettingsMachines(); } catch { /* not on Settings */ }
      }
      if (!shown && tries < 20) setTimeout(look, 3000);
    };
    setTimeout(look, 1500);
  }

  // `profile` signs in one of the computer's subscriptions (runner-rs/src/profiles.rs) instead of its default login.
  // `switchAccount` signs in to another account in place of the one signed in now. `toSubscription` does the same for
  // the computer's login running on an API key: the key stays in use until the subscription works, then goes.
  // `into` is a pop-up already open (Add connection, ui/app/computer-detail.js): the sign-in carries on inside it.
  // `connect` {title, first, start, done}: `first` is the sign-in already started, `start` starts another for Try again,
  // `done` is what the toast says once the server has saved the result.
  function open({runnerId, runtime, machine, profile = '', switchAccount = false, toSubscription = false, into = null, connect = null}) {
    const base = () => `/v2/computers/${encodeURIComponent(runnerId)}/logins`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const kept = toSubscription ? ' The API key is still in use.' : switchAccount ? ' The account signed in before still works.' : '';
    const where = machine || 'the computer';
    const dialog = into || document.createElement('dialog');
    dialog.className = 'tmodal tmodal-shell model-login';
    dialog.setAttribute('aria-labelledby', 'model-login-title');
    const title = connect ? text(connect.title) : toSubscription ? `Switch ${text(name)}${machine ? ` on ${text(machine)}` : ''} to a subscription`
      : `${switchAccount ? 'Switch account for' : 'Sign in to'} ${text(name)}${machine ? ` on ${text(machine)}` : ''}`;
    dialog.innerHTML = `<header><h2 id="model-login-title">${title}</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="model-login-body" data-body></div>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions></div>`;
    if (!into) document.body.appendChild(dialog);
    const body = dialog.querySelector('[data-body]'), status = dialog.querySelector('[data-status]'),
          actions = dialog.querySelector('[data-actions]');
    // `note`: why a cancel did not go through, kept on screen while the sign-in it left running goes on.
    let login = null, timer = null, closed = false, sending = false, note = '', first = connect?.first || null;

    const stop = () => { closed = true; clearTimeout(timer); };
    const close = async () => {
      stop();
      if (login && active(login.state)) {
        try { await post(`${base()}/${encodeURIComponent(login.id)}/cancel`, {}); } catch { /* it expires on its own */ }
      }
      if (dialog.open) dialog.close();
    };
    dialog.addEventListener('close', () => { stop(); dialog.remove(); });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target.closest('[data-close]')) void close();
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); void close(); });

    const draw = () => {
      const state = login.state;
      const seconds = (Date.now() - new Date(login.created)) / 1000;
      let head = '', label = '';
      if (state === 'requested' || state === 'starting') {
        label = seconds <= STALLED_S ? `Starting the sign-in on ${machine || 'the computer'}…`
          : hosted() || connect ? 'Still waiting for the computer to start the sign-in. Check that it shows as online in Settings > Computers.'
          : 'Still waiting for the computer to start the sign-in. It may need a newer Tico runner; you can also sign in from its terminal.';
      } else if (state === 'waiting') {
        label = 'Waiting for you to finish signing in…';
      } else if (state === 'finishing') {
        label = `Putting the new account in place on ${machine || 'the computer'}…`;
      } else if (state === 'done') {
        label = connect?.done || 'Done.';
      } else if (state === 'signed_in') {
        label = toSubscription ? `Switched. ${name} on ${where} now runs on the subscription; its bots keep it, and the API key is off.`
          : switchAccount ? `Switched. ${name} on ${where} now uses the new account; its bots keep it.`
          : `Signed in. ${name} is ready on ${machine || 'the computer'}.`;
      } else if (state === 'failed') {
        label = (login.message || 'The sign-in failed.') + kept;
      } else if (state === 'expired') {
        label = 'The sign-in ran out of time. Start it again.' + kept;
      } else if (state === 'cancelled') {
        label = 'The sign-in was cancelled.' + kept;
      }
      if (state === 'waiting') {
        head = `<ol class="model-login-steps">
          <li>${login.url ? `Open <a href="${text(login.url)}" target="_blank" rel="noopener noreferrer" data-link>${text(login.url.replace(/^https:\/\//, '').slice(0, 60))}</a> and sign in with ${connect ? 'the account that has the plan' : "the team's account"}.`
                          : hosted() || connect ? 'Waiting for the sign-in link from the computer…'
                          : 'Open the sign-in page the computer printed (see below) and sign in with the team\'s account.'}</li>
          ${login.code ? `<li>Enter this one-time code:<div class="model-login-code"><code data-code>${text(login.code)}</code>
              <button class="ghost" type="button" data-copy>Copy</button></div></li>` : ''}
          ${login.accepts_code ? `<li>${login.code_sent ? 'Code sent. Waiting for the computer to finish…' : 'The page then shows a code. Paste it here:'}
              ${login.code_sent ? '' : `<form class="model-login-paste" data-paste><input name="code" type="text" autocomplete="off" spellcheck="false" required maxlength="700" aria-label="Code from the sign-in page" placeholder="Paste the code">
              <button class="primary" type="submit">Send code</button></form>`}</li>` : ''}
        </ol>`;
      }
      const lines = (login.lines || []).length && !hosted() && !connect && (state === 'waiting' && !login.url && !login.code || state === 'failed')
        ? `<details open><summary>What the computer printed</summary><pre data-lines>${text(login.lines.join('\n'))}</pre></details>` : '';
      // Keep what the person is typing when a poll redraws around it.
      const typed = body.querySelector('[name=code]')?.value || '';
      body.innerHTML = head + lines;
      const field = body.querySelector('[name=code]');
      if (field && typed) field.value = typed;
      status.textContent = note && active(state) ? `${label} ${note}` : label;
      status.dataset.state = state;
      actions.innerHTML = state === 'finishing' ? ''
        : active(state) ? '<button class="ghost" type="button" data-cancel>Cancel sign-in</button>'
        : `${state === 'signed_in' ? '' : '<button class="primary" type="button" data-retry>Try again</button>'}
           <button class="ghost" type="button" data-close>${state === 'signed_in' ? 'Done' : 'Close'}</button>`;
    };

    body.addEventListener('click', event => {
      const copy = event.target.closest('[data-copy]');
      if (copy) void copyText(login.code).then(() => toast('Code copied'), () => toast('Could not copy the code', true));
    });
    body.addEventListener('submit', async event => {
      event.preventDefault();
      const field = event.target.querySelector('[name=code]');
      if (sending || !field.value.trim()) return;
      sending = true;
      try {
        login = await post(`${base()}/${encodeURIComponent(login.id)}/code`, {code: field.value.trim()});
        field.value = '';
        draw();
      } catch (error) { status.textContent = error.message; }
      finally { sending = false; }
    });
    actions.addEventListener('click', async event => {
      if (event.target.closest('[data-cancel]')) {
        // On a hosted Tico the pane closes once the computer's sign-in is cancelled. A refused cancel (a switch
        // already putting the new account in place) or a failed one leaves it open, saying why and following it on.
        try {
          login = await post(`${base()}/${encodeURIComponent(login.id)}/cancel`, {});
          note = '';
          draw();
          if (hosted() && login.state === 'cancelled') { toast(status.textContent); stop(); if (dialog.open) dialog.close(); }
        } catch (error) { note = error.message; status.textContent = error.message; }
      } else if (event.target.closest('[data-retry]')) {
        void begin();
      }
    });

    const tick = async () => {
      if (closed) return;
      try {
        const next = await get(`${base()}/${encodeURIComponent(login.id)}`);
        const changed = next.state !== login.state;
        login = next;
        if (!closed) draw();
        if (changed && connect && next.state === 'done') { toast(connect.done); stop(); if (dialog.open) dialog.close(); }
        if (changed && next.state === 'signed_in') {
          refreshUntil(runnerId, runtime, profile, 'ready', next.updated);
          if (hosted()) { toast(status.textContent); stop(); if (dialog.open) dialog.close(); }
        }
      } catch (error) { status.textContent = error.message; }
      if (!closed && active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    const begin = async () => {
      clearTimeout(timer);
      closed = false;
      note = '';
      status.textContent = 'Starting…';
      try {
        if (connect) {
          // Try again may run on another computer: the server chooses.
          login = first || await connect.start();
          first = null;
          runnerId = login.runner_id; machine = login.computer || machine;
        } else login = await post(base(), {runtime, ...(profile ? {profile} : {}), ...(toSubscription ? {to: 'subscription'} : switchAccount ? {switch: true} : {})});
      } catch (error) { status.textContent = error.message; actions.innerHTML = '<button class="ghost" type="button" data-close>Close</button>'; return; }
      draw();
      if (active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    if (!dialog.open) dialog.showModal();
    void begin();
    return dialog;
  }

  // "Reviewer 1", "Reviewer 1 and QA", "Reviewer 1, QA and Scout", "Reviewer 1, QA and 2 more".
  function botsWords(names) {
    if (names.length <= 1) return names.join('');
    if (names.length <= 3) return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
    return `${names.slice(0, 2).join(', ')} and ${names.length - 2} more`;
  }

  // Sign a login out, after a confirmation naming the bots on it that stop. Bots and assignments stay; the login reads
  // Signed out until someone signs in again, and the company's shared key does not sign it back in by itself.
  function signOut({runnerId, runtime, machine, profile = ''}) {
    const computer = `/v2/computers/${encodeURIComponent(runnerId)}`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const where = machine || 'the computer';
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal tmodal-shell model-login model-logout';
    dialog.setAttribute('aria-labelledby', 'model-logout-title');
    dialog.innerHTML = `<header><h2 id="model-logout-title">Sign out of ${text(name)} on ${text(where)}?</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="model-login-body"><p data-bots>Checking which bots use it…</p>
        <p class="muted">Bots keep this login, so nothing needs reassigning: they start again once someone signs in.${profile ? '' : ' The company\'s shared key won\'t sign it back in by itself.'}</p></div>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions><button class="ghost" type="button" data-close>Cancel</button>
        <button class="primary people-danger" type="button" data-sign-out disabled>Sign out</button></div>`;
    document.body.appendChild(dialog);
    const status = dialog.querySelector('[data-status]'), actions = dialog.querySelector('[data-actions]');
    let timer = null, closed = false;
    dialog.addEventListener('close', () => { closed = true; clearTimeout(timer); dialog.remove(); });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target.closest('[data-close]')) dialog.close();
    });
    // Sign out stays off until the bots that stop are named: a quick click or a failed check never stops them unnamed.
    const query = `runtime=${encodeURIComponent(runtime)}${profile ? `&profile=${encodeURIComponent(profile)}` : ''}`;
    const said = dialog.querySelector('[data-bots]');
    const loadBots = () => {
      said.textContent = 'Checking which bots use it…';
      get(`${computer}/logouts/bots?${query}`).then(found => {
        const bots = (found.bots || []).map(bot => bot.name || bot.bot);
        said.textContent = bots.length ? `${botsWords(bots)} will stop running until this is signed in again.`
          : 'No bot uses this login right now.';
        if (bots.length > 3) said.title = bots.join(', ');
        const button = actions.querySelector('[data-sign-out]');
        if (button) button.disabled = false;
      }, error => {
        said.textContent = `Couldn't check which bots use it (${error.message}), so it can't be signed out yet. `;
        const retry = document.createElement('button');
        retry.className = 'ghost';
        retry.type = 'button';
        retry.textContent = 'Try again';
        retry.addEventListener('click', loadBots);
        said.appendChild(retry);
      });
    };
    loadBots();
    const finish = row => {
      status.dataset.state = row.state;
      status.textContent = row.state === 'signed_out' ? `Signed out. ${name} on ${where} stays signed out until someone signs in.`
        : row.state === 'failed' ? (row.message || 'The sign-out did not finish; the login is as it was.')
        : row.state === 'expired' ? 'The computer did not sign out in time; the login is as it was.' : '';
      actions.innerHTML = '<button class="ghost" type="button" data-close>Close</button>';
      refreshUntil(runnerId, runtime, profile, row.state === 'signed_out' ? 'signed_out' : 'ready', row.updated);
    };
    const tick = async id => {
      if (closed) return;
      try {
        const row = await get(`${computer}/logins/${encodeURIComponent(id)}`);
        if (active(row.state)) { timer = setTimeout(() => tick(id), POLL_MS); return; }
        finish(row);
      } catch (error) { status.textContent = error.message; timer = setTimeout(() => tick(id), POLL_MS); }
    };
    actions.addEventListener('click', async event => {
      if (!event.target.closest('[data-sign-out]') || event.target.closest('[data-sign-out]').disabled) return;
      actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
      status.textContent = `Signing out on ${where}…`;
      try {
        const row = await post(`${computer}/logouts`, {runtime, ...(profile ? {profile} : {})});
        if (active(row.state)) timer = setTimeout(() => tick(row.id), POLL_MS); else finish(row);
      } catch (error) {
        status.textContent = error.message;
        actions.innerHTML = '<button class="ghost" type="button" data-close>Close</button>';
      }
    });
    dialog.showModal();
    return dialog;
  }

  // A computer login a person signed out takes the company's shared key again (when there is one).
  async function useSharedKey({runnerId, runtime}) {
    try {
      const row = await post(`/v2/computers/${encodeURIComponent(runnerId)}/shared-key`, {runtime});
      toast('The shared key signs it in at the computer\'s next check, if the company has one for it');
      refreshUntil(runnerId, runtime, '', 'ready', row.created);
    } catch (error) { toast(error.message, true); }
  }

  // Every action here waits for the computer: offline, its button stays, disabled, and says why (nothing queues).
  const offline = online => online ? '' : ' disabled title="Computer is offline"';
  // Switch account and Sign out sit on every signed-in line, so they are icons; the name is the label and the tooltip
  // (an offline title in `attrs` comes first and wins).
  // (A *_ICONS map, so scripts/build-icon-font.py keeps their glyphs.)
  const LOGIN_ICONS = {switch: 'manage_accounts', signOut: 'logout'};
  const iconButton = (icon, label, attrs) => `<button class="ghost machine-signin machine-icon-btn" type="button" ${attrs} aria-label="${label}" title="${label}"><span class="nav-icon" aria-hidden="true">${icon}</span></button>`;

  // The Sign out / Switch account buttons for a signed-in login, and Use the shared key for a computer login a person
  // signed out. Only for a CLI whose manifest has a logout (`sign_out`), to whoever may sign it in (the caller checks:
  // an admin or the computer's operator), disabled while the computer is offline. The computer's login running on an API key
  // (`onKey`) offers Switch to subscription instead of Switch account; on a subscription, a runtime Tico keeps a key for
  // (`api_keys = "computer"`) and can check one (`api_key_login` or `api_key_check`) also offers Switch to API key, except
  // where its login is in the system keychain (a logout there could not be undone).
  // No switch where the CLI keeps the login in the system keychain (`switch_unavailable_on`, by the computer's
  // `platform`): there is no side to sign in on, so a cancel could not keep the old login.
  function actionsHTML({runnerId, runtime, machine = '', profile = '', signedIn = false, signedOut = false, online = true, platform = '', onKey = false}) {
    const caps = typeof harnessCaps === 'function' ? harnessCaps(runtime) : {};
    if (!caps.sign_out) return '';
    const data = `data-runner="${text(runnerId)}" data-runtime="${text(runtime)}" data-machine="${text(machine)}"${profile ? ` data-profile="${text(profile)}"` : ''}${offline(online)}`;
    if (signedIn) {
      const switches = !(caps.switch_unavailable_on || []).includes(platform);
      const toKey = switches && !onKey && !profile && caps.api_keys === 'computer' && (caps.api_key_login || caps.api_key_check);
      return (onKey && !profile ? toSubscriptionHTML({runnerId, runtime, machine, online, platform})
        : switches ? ` ${iconButton(LOGIN_ICONS.switch, 'Switch account', `data-model-switch ${data}`)}` : '')
        + (toKey ? ` <button class="ghost machine-signin" type="button" data-model-to-key ${data}>Switch to API key</button>` : '')
        + ` ${iconButton(LOGIN_ICONS.signOut, 'Sign out', `data-model-sign-out ${data}`)}`;
    }
    return signedOut && !profile ? ` <button class="ghost machine-signin" type="button" data-model-shared-key ${data}>Use the shared key</button>` : '';
  }

  // Switch to subscription for the computer's own login while an API key signs it in: next to Sign out, or to Replace
  // key / Clear key where the key is one Tico keeps. Same rights and conditions as actionsHTML, and none where the
  // login lives in the system keychain.
  function toSubscriptionHTML({runnerId, runtime, machine = '', online = true, platform = ''}) {
    const caps = typeof harnessCaps === 'function' ? harnessCaps(runtime) : {};
    if (!caps.sign_out || !caps.browser_sign_in || (caps.switch_unavailable_on || []).includes(platform)) return '';
    return ` <button class="ghost machine-signin" type="button" data-model-to-subscription data-runner="${text(runnerId)}" data-runtime="${text(runtime)}" data-machine="${text(machine)}"${offline(online)}>Switch to subscription</button>`;
  }

  const KEY_SOURCES = {computer: 'added from Tico', terminal: 'set on the computer', shared: 'the company\'s shared key'};
  const keyName = runtime => RUNTIME_NAMES[runtime] || (typeof harnessRow === 'function' && harnessRow(runtime)?.name) || runtime;
  const keyBase = runnerId => `/v2/computers/${encodeURIComponent(runnerId)}`;

  // The "this computer only / all listed computers" ask for a shared key: the company's "Every computer" one, or one
  // shared with some computers by name (`info` is GET /v2/computers/{id}/keys, `computers` every one it serves). `verb` is what happens to it ('clear', 'replace'). Reused by every page that changes
  // a shared key; `chosenScope` reads the answer back.
  function scopeHTML(info, verb, here) {
    if (info.source !== 'shared') return '';
    const later = verb === 'clear' ? 'clears its copy when it is next online' : 'gets the new key when it is next online';
    // An older runner cannot clear its copy, online or not (`updated` false), so All of them waits for its update.
    const stale = verb === 'clear' ? (info.computers || []).filter(c => c.updated === false) : [];
    const rows = (info.computers || []).map(c => `<li data-scope-computer="${text(c.id)}">${text(c.label)}${stale.includes(c) ? ' <span class="muted">(needs a runner update to clear it from here)</span>' : c.online ? '' : ` <span class="muted">(offline: ${later})</span>`}${c.may ? '' : ' <span class="muted">(not yours to change)</span>'}</li>`).join('');
    const all = verb === 'replace' && !info.can_replace_all ? ' disabled' : verb === 'clear' && ((info.computers || []).some(c => !c.may) || stale.length) ? ' disabled' : '';
    const why = !all ? '' : verb === 'clear' && stale.length && !(info.computers || []).some(c => !c.may)
      ? `<span class="muted"> Update ${text(stale.map(c => c.label).join(', '))} first, or delete the shared key in Credentials.</span>`
      : `<span class="muted"> Only ${verb === 'replace' ? 'a credential administrator' : 'someone who may change every one of them'} can do this.</span>`;
    return `<fieldset class="model-key-scope" data-key-scope><legend>This key is shared with ${(info.computers || []).length} computers:</legend>
      <ul>${rows}</ul>
      <label><input type="radio" name="key-scope" value="this" checked> This computer only${here ? ` (${text(here)})` : ''}: ${verb === 'clear' ? 'it stops using the shared key; the others keep it' : 'it gets its own key; the others keep the shared one'}</label>
      <label><input type="radio" name="key-scope" value="all"${all}> All listed computers: ${verb === 'clear' ? 'every computer listed stops using it' : 'the shared key is replaced for every computer, in Credentials too'}${why}</label>
      <p class="muted" data-key-scope-warn hidden>This changes every listed computer: ${text((info.computers || []).map(c => c.label).join(', '))}.</p></fieldset>`;
  }
  // The warning under the ask shows while "All listed computers" is chosen.
  const scopeWarn = root => {
    const warn = root.querySelector('[data-key-scope-warn]');
    if (warn) warn.hidden = chosenScope(root) !== 'all';
  };
  const chosenScope = root => root.querySelector('[name=key-scope]:checked')?.value || 'this';

  // A small dialog shell shared by Clear key and Replace key.
  function keyDialog(title, inner, action, danger) {
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal tmodal-shell model-login model-key';
    dialog.setAttribute('aria-labelledby', 'model-key-title');
    dialog.innerHTML = `<header><h2 id="model-key-title">${text(title)}</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <form class="model-login-body" data-key-form>${inner}</form>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions><button class="ghost" type="button" data-close>Cancel</button>
        <button class="primary${danger ? ' people-danger' : ''}" type="button" data-key-go disabled>${text(action)}</button></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener('close', () => { dialog.closed = true; clearTimeout(dialog.timer); dialog.remove(); });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target.closest('[data-close]')) dialog.close();
    });
    dialog.showModal();
    return dialog;
  }

  // Follow a key job (`clear_key`, `use_key`) on one computer to its end, then redraw.
  function followKeyJob(dialog, runnerId, runtime, row, done) {
    const tick = async () => {
      if (dialog.closed) return;
      try {
        row = await get(`${keyBase(runnerId)}/logins/${encodeURIComponent(row.id)}`);
      } catch (error) { dialog.querySelector('[data-status]').textContent = error.message; }
      if (active(row.state)) { dialog.timer = setTimeout(tick, POLL_MS); return; }
      done(row);
      refreshUntil(runnerId, runtime, '', row.state === 'signed_out' ? 'signed_out' : 'ready', row.updated);
    };
    if (active(row.state)) dialog.timer = setTimeout(tick, POLL_MS); else done(row);
  }

  // Clear key: confirm first, naming the bots that stop (on every computer, for "all of them"); bots and assignments
  // stay, and the shared key does not sign it back in until someone adds a key or chooses the shared key.
  async function clearKey({runnerId, runtime, machine}) {
    const name = keyName(runtime), where = machine || 'the computer';
    const dialog = keyDialog(`Clear the ${name} key on ${where}?`, '<p data-key-info>Checking which key it uses…</p>', 'Clear key', true);
    const status = dialog.querySelector('[data-status]'), form = dialog.querySelector('[data-key-form]');
    const go = dialog.querySelector('[data-key-go]');
    let info;
    try { info = await get(`${keyBase(runnerId)}/keys?runtime=${encodeURIComponent(runtime)}`); }
    catch (error) { status.textContent = error.message; return dialog; }
    const bots = async computers => {
      const named = [];
      for (const c of computers) {
        const found = await get(`${keyBase(c.id)}/logouts/bots?runtime=${encodeURIComponent(runtime)}`);
        named.push(...(found.bots || []).map(bot => (bot.name || bot.bot) + (computers.length > 1 ? ` (${c.label})` : '')));
      }
      return named;
    };
    const say = async () => {
      go.disabled = true;
      const scope = chosenScope(form);
      const computers = scope === 'all' ? (info.computers || []).filter(c => c.may) : [{id: runnerId, label: where}];
      const said = form.querySelector('[data-key-bots]');
      said.textContent = 'Checking which bots use it…';
      try {
        const named = await bots(computers);
        said.textContent = named.length
          ? `${named.length === 1 ? 'This bot stops' : `These ${named.length} bots stop`} until a key is added again: ${named.join(', ')}.`
          : 'No bot uses this key right now.';
        go.disabled = false;
      } catch (error) { said.textContent = `Couldn't check which bots use it (${error.message}), so it can't be cleared yet.`; }
    };
    form.innerHTML = `<p>${info.preview ? `Key <code>${text(info.preview)}</code>, ` : ''}${text(KEY_SOURCES[info.source] || 'a key')}.</p>
      ${scopeHTML(info, 'clear', where)}
      <p data-key-bots></p>
      <p class="muted">The key is deleted from the computer. Bots keep this login, so nothing needs reassigning; the company's shared key won't sign it back in by itself. Add a key or choose Use the shared key to bring it back.</p>`;
    form.addEventListener('change', () => { scopeWarn(form); void say(); });
    void say();
    go.addEventListener('click', async () => {
      if (go.disabled) return;
      dialog.querySelectorAll('[data-actions] button').forEach(button => { button.disabled = true; });
      status.textContent = 'Clearing…';
      try {
        const out = await post(`${keyBase(runnerId)}/keys/clear`, {runtime, scope: chosenScope(form)});
        const waiting = out.computers.filter(c => c.waiting).map(c => c.label);
        const later = waiting.length ? ` ${waiting.join(', ')} ${waiting.length === 1 ? 'is' : 'are'} offline and clear${waiting.length === 1 ? 's its copy' : ' their copies'} when next online.` : '';
        const here = out.computers.find(c => c.id === runnerId)?.login;
        const end = row => {
          status.dataset.state = row.state;
          status.textContent = (row.state === 'signed_out' ? `Cleared. ${name} on ${where} has no key until one is added.`
            : row.message || 'The key could not be cleared on the computer.') + later;
          dialog.querySelector('[data-actions]').innerHTML = '<button class="ghost" type="button" data-close>Close</button>';
        };
        if (here) followKeyJob(dialog, runnerId, runtime, here, end); else end({state: 'signed_out'});
        for (const c of out.computers) if (c.login && c.id !== runnerId) refreshUntil(c.id, runtime, '', 'signed_out', c.login.created);
      } catch (error) {
        status.textContent = error.message;
        dialog.querySelector('[data-actions]').innerHTML = '<button class="ghost" type="button" data-close>Close</button>';
      }
    });
    return dialog;
  }

  // Replace key, or Add key where none signs it in: the key is pasted once and goes to the vault; the computer checks
  // it before the old one is deleted, so a key that doesn't work leaves the old one in use. `toKey`: Switch to API key
  // from a subscription, which stays in use until the key is in place (Cancel switch, or closing, cancels the check).
  async function replaceKey({runnerId, runtime, machine, adding = false, toKey = false, others = null}) {
    // The other computers it can be shared with: the connected ones Settings > Computers lists.
    others = others || (typeof SETTINGS_DATA !== 'undefined' ? SETTINGS_DATA.machines || [] : [])
      .filter(m => !m.revoked_at && m.id !== runnerId).map(m => ({id: m.id, label: m.label}));
    const name = keyName(runtime), where = machine || 'the computer';
    adding = adding || toKey;
    const dialog = keyDialog(toKey ? `Switch ${name} on ${where} to an API key` : `${adding ? 'Add a' : 'Replace the'} ${name} key on ${where}`,
      '<p>Checking which key it uses…</p>', toKey ? 'Switch to API key' : adding ? 'Add key' : 'Replace key');
    const status = dialog.querySelector('[data-status]'), form = dialog.querySelector('[data-key-form]');
    const go = dialog.querySelector('[data-key-go]');
    let info;
    try { info = await get(`${keyBase(runnerId)}/keys?runtime=${encodeURIComponent(runtime)}`); }
    catch (error) { status.textContent = error.message; return dialog; }
    const names = info.names || [];
    // Add connection > API key, for a credential administrator: share the pasted key with other computers by name, or
    // use a key already shared ("Every computer" in Credentials, or one shared from another computer).
    // Switch to API key moves this computer's own login onto a key of its own, so it is not offered there.
    const share = adding && !toKey && info.can_share;
    const choices = share ? info.shared_choices || [] : [];
    const shareHTML = !share ? '' : `<fieldset class="model-key-share" data-key-share><legend>Use it on</legend>
        <label><input type="radio" name="key-share" value="this" checked> This computer</label>
        ${others.length ? `<label><input type="radio" name="key-share" value="shared"> Shared with:</label>
        <div class="model-key-share-list">${others.map(c => `<label><input type="checkbox" name="key-share-computer" value="${text(c.id)}" disabled> ${text(c.label)}</label>`).join('')}</div>` : ''}
        ${choices.length ? `<label><input type="radio" name="key-share" value="existing"> A shared key: <select name="key-existing" disabled aria-label="Shared key">${choices.map(k => `<option value="${text(k.id)}">${text(k.name)} · ${text(k.preview)}${k.every ? ' · every computer' : ''}</option>`).join('')}</select></label>` : ''}
      </fieldset>`;
    form.innerHTML = `${info.preview && !adding ? `<p>Now: key <code>${text(info.preview)}</code>, ${text(KEY_SOURCES[info.source] || '')}.</p>` : ''}
      ${adding ? shareHTML : scopeHTML(info, 'replace', where)}
      <label>New key <input name="secret" type="password" autocomplete="off" spellcheck="false" required maxlength="4000" aria-label="New ${text(name)} key"></label>
      ${names.length > 1 ? `<label>Kind <select name="env">${names.map(n => `<option value="${text(n)}"${n === (info.name || info.shared?.env) ? ' selected' : ''}>${text(n)}</option>`).join('')}</select></label>` : ''}
      <p class="muted">It is kept in Tico's credential vault and never shown again in full. ${toKey
        ? `The subscription stays signed in and in use while ${text(where)} checks the key; once it works there the subscription is signed out and the next turn uses the key. Bots keep this login, so nothing needs reassigning.`
        : 'The next turn uses it once the computer has checked it; the old key is then deleted.'}</p>`;
    const field = form.querySelector('[name=secret]');
    const shareMode = () => form.querySelector('[name=key-share]:checked')?.value || 'this';
    const picked = () => [...form.querySelectorAll('[name=key-share-computer]:checked')].map(box => box.value);
    const ready = () => {
      const mode = shareMode();
      field.closest('label').hidden = mode === 'existing';
      form.querySelectorAll('[name=key-share-computer]').forEach(box => { box.disabled = mode !== 'shared'; });
      const existing = form.querySelector('[name=key-existing]');
      if (existing) existing.disabled = mode !== 'existing';
      go.disabled = mode === 'existing' ? false : !field.value.trim() || (mode === 'shared' && !picked().length);
    };
    field.addEventListener('input', ready);
    form.addEventListener('change', () => { scopeWarn(form); ready(); });
    field.focus();
    form.addEventListener('submit', event => { event.preventDefault(); go.click(); });
    go.addEventListener('click', async () => {
      if (go.disabled) return;
      const mode = shareMode();
      const close = () => { dialog.querySelector('[data-actions]').innerHTML = '<button class="ghost" type="button" data-close>Close</button>'; };
      const sharedDone = out => {
        const listed = (out.computers || []).map(c => c.label);
        status.dataset.state = 'done';
        status.textContent = `Added. ${name} on ${listed.join(', ')} uses the shared key ${out.shared.preview} from each computer's next key check.`;
        close();
        for (const c of out.computers || []) refreshUntil(c.id, runtime, '', 'ready', new Date().toISOString());
      };
      if (mode === 'existing') {
        dialog.querySelectorAll('[data-actions] button, [data-key-form] input, [data-key-form] select').forEach(el => { el.disabled = true; });
        status.textContent = 'Adding the shared key…';
        try { sharedDone(await post(`${keyBase(runnerId)}/shared-key`, {runtime, credential: form.querySelector('[name=key-existing]').value})); }
        catch (error) { status.textContent = error.message; close(); }
        return;
      }
      if (!field.value.trim()) return;
      const scope = adding ? 'this' : chosenScope(form);
      const body = {runtime, secret: field.value.trim(), scope, ...(form.querySelector('[name=env]') ? {env: form.querySelector('[name=env]').value} : {}),
        ...(mode === 'shared' ? {share: picked()} : {}), ...(toKey ? {switch: true} : {})};
      // The key leaves the page with this request and is not kept in it.
      field.value = '';
      dialog.querySelectorAll('[data-actions] button, [data-key-form] input, [data-key-form] select').forEach(el => { el.disabled = true; });
      status.textContent = scope === 'all' ? 'Replacing the shared key…' : body.share ? 'Sharing the key…' : `Checking the new key on ${where}…`;
      // Switch to API key: closing the dialog cancels the switch, also while it is being asked for (`job` not back yet).
      let job = null;
      const cancel = async () => {
        try { await post(`${keyBase(runnerId)}/logins/${encodeURIComponent(job.id)}/cancel`, {}); }
        catch (error) { status.textContent = error.message; }
      };
      if (toKey) dialog.addEventListener('close', () => { if (job && !dialog.ended) void cancel(); });
      try {
        const out = await post(`${keyBase(runnerId)}/keys`, body);
        if (body.share) { sharedDone(out); return; }
        job = out;
        if (toKey && dialog.closed) { void cancel(); return; }
        if (scope === 'all') {
          const later = (out.computers || []).filter(c => !c.online).map(c => c.label);
          status.textContent = `Replaced. The shared key is now ${out.shared.preview}; every computer it serves uses it from its next check.${later.length ? ` ${later.join(', ')} ${later.length === 1 ? 'gets' : 'get'} it when next online.` : ''}`;
          close();
          for (const c of out.computers || []) refreshUntil(c.id, runtime, '', 'ready', new Date().toISOString());
          return;
        }
        if (toKey) {
          dialog.querySelector('[data-actions]').innerHTML = '<button class="ghost" type="button" data-key-cancel>Cancel switch</button>';
          dialog.querySelector('[data-key-cancel]').addEventListener('click', () => void cancel());
        }
        followKeyJob(dialog, runnerId, runtime, out, row => {
          dialog.ended = true;
          status.dataset.state = row.state;
          // A failure says what the computer reported, which says whether the subscription is still in use: when putting
          // things back did not work either, it says that instead.
          const said = String(row.message || '').trim();
          status.textContent = toKey
            ? (row.state === 'done' ? `Switched. ${name} on ${where} now uses ${out.key?.preview || 'the API key'}; the subscription is signed out.`
              : row.state === 'expired' ? `${where} did not finish the switch in time. Check this connection once it reports again.`
              : row.state === 'cancelled' ? 'Cancelled. The subscription is still in use.'
              : said ? said[0].toUpperCase() + said.slice(1) + (/[.!?]$/.test(said) ? '' : '.')
              : 'The switch did not finish.')
            : row.state === 'done' ? `${adding ? 'Added' : 'Replaced'}. ${name} on ${where} now uses ${out.key?.preview || 'the new key'}.`
            : row.state === 'expired' ? 'The computer did not check the key in time; nothing changed.'
            : (row.message || 'The new key did not work') + (adding ? '' : '. The old key is still in use.');
          close();
        });
      } catch (error) { status.textContent = error.message; close(); }
    });
    return dialog;
  }

  // Replace key / Clear key for a login a key signs in (`key`: its readiness `key`, {name, source}), and Add key where
  // none does: a key runtime (`api_keys = "computer"`) signed out, cleared or never signed in. Same rights and
  // conditions as actionsHTML: an admin or the computer's operator (the caller checks), disabled while it is offline.
  function keyActionsHTML({runnerId, runtime, machine = '', key = null, signedIn = false, online = true}) {
    const caps = typeof harnessCaps === 'function' ? harnessCaps(runtime) : {};
    if (caps.api_keys !== 'computer') return '';
    const data = `data-runner="${text(runnerId)}" data-runtime="${text(runtime)}" data-machine="${text(machine)}"${offline(online)}`;
    if (signedIn && key) {
      return ` <button class="ghost machine-signin" type="button" data-model-key-replace ${data}>Replace key</button>`
        + ` <button class="ghost machine-signin" type="button" data-model-key-clear ${data}>Clear key</button>`;
    }
    return signedIn ? '' : ` <button class="ghost machine-signin" type="button" data-model-key-add ${data}>Add key</button>`;
  }

  window.TicoModelLogin = {open, signOut, useSharedKey, actionsHTML, toSubscriptionHTML, keyActionsHTML, clearKey, replaceKey, scopeHTML, chosenScope};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-model-login], [data-model-switch], [data-model-to-subscription], [data-model-to-key], [data-model-sign-out], [data-model-shared-key], [data-model-key-replace], [data-model-key-clear], [data-model-key-add]');
    if (!button) return;
    event.preventDefault();
    const target = {runnerId: button.dataset.runner, runtime: button.dataset.runtime, machine: button.dataset.machine || '', profile: button.dataset.profile || ''};
    if (button.hasAttribute('data-model-key-clear')) void clearKey(target);
    else if (button.hasAttribute('data-model-key-replace') || button.hasAttribute('data-model-key-add')) void replaceKey({...target, adding: button.hasAttribute('data-model-key-add')});
    else if (button.hasAttribute('data-model-to-key')) void replaceKey({...target, toKey: true});
    else if (button.hasAttribute('data-model-sign-out')) signOut(target);
    else if (button.hasAttribute('data-model-shared-key')) void useSharedKey(target);
    else open({...target, switchAccount: button.hasAttribute('data-model-switch'), toSubscription: button.hasAttribute('data-model-to-subscription')});
  });
})();
