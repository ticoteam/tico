/* Sign a model in on a computer from the browser (backend/model_login.py, runner-rs/src/login.rs).
   The dialog shows what the model's own CLI prints: a link and a one-time code, or a link and a
   field for the code the sign-in page hands back. This page never sees the resulting credential;
   the CLI keeps it on the computer, and the chip flips when the computer reports it is ready.
   The same module signs a login out (after a confirmation naming the bots that stop), switches it to
   another account (a sign-in on the side: cancelling leaves the old account working), and lets the
   company's shared key sign a signed-out computer login in again. `actionsHTML` draws those buttons for
   any page that lists a login: Settings > Computers and Manage subscription today. */
'use strict';
(function () {
  const RUNTIME_NAMES = {codex: 'Codex (ChatGPT)', claude: 'Claude Code'};
  const POLL_MS = 2000, STALLED_S = 30;
  const text = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  // `finishing`: a switch is putting the new account in place, past the point where it can be cancelled.
  const active = state => ['requested', 'starting', 'waiting', 'finishing'].includes(state);

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
      // The bot editor's subscription list, for a named subscription.
      if (profile) try { if (typeof subsLoad === 'function') await subsLoad(); } catch { /* not on Settings */ }
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
  // `switchAccount` signs in to another account in place of the one signed in now.
  function open({runnerId, runtime, machine, profile = '', switchAccount = false}) {
    const base = `/v2/computers/${encodeURIComponent(runnerId)}/logins`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const kept = switchAccount ? ' The account signed in before still works.' : '';
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal model-login';
    dialog.setAttribute('aria-labelledby', 'model-login-title');
    dialog.innerHTML = `<header><h2 id="model-login-title">${switchAccount ? 'Switch account for' : 'Sign in to'} ${text(name)}${machine ? ` on ${text(machine)}` : ''}</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close">✕</button></header>
      <div class="model-login-body" data-body></div>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions></div>`;
    document.body.appendChild(dialog);
    const body = dialog.querySelector('[data-body]'), status = dialog.querySelector('[data-status]'),
          actions = dialog.querySelector('[data-actions]');
    let login = null, timer = null, closed = false, sending = false;

    const stop = () => { closed = true; clearTimeout(timer); };
    const close = async () => {
      stop();
      if (login && active(login.state)) {
        try { await post(`${base}/${encodeURIComponent(login.id)}/cancel`, {}); } catch { /* it expires on its own */ }
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
        label = seconds > STALLED_S
          ? 'Still waiting for the computer to start the sign-in. It may need a newer Tico runner; you can also sign in from its terminal.'
          : `Starting the sign-in on ${machine || 'the computer'}…`;
      } else if (state === 'waiting') {
        label = 'Waiting for you to finish signing in…';
      } else if (state === 'finishing') {
        label = `Putting the new account in place on ${machine || 'the computer'}…`;
      } else if (state === 'signed_in') {
        label = switchAccount ? `Switched. ${name} on ${machine || 'the computer'} now uses the new account; its bots keep it.`
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
          <li>${login.url ? `Open <a href="${text(login.url)}" target="_blank" rel="noopener noreferrer" data-link>${text(login.url.replace(/^https:\/\//, '').slice(0, 60))}</a> and sign in with the team's account.`
                          : 'Open the sign-in page the computer printed (see below) and sign in with the team\'s account.'}</li>
          ${login.code ? `<li>Enter this one-time code:<div class="model-login-code"><code data-code>${text(login.code)}</code>
              <button class="ghost" type="button" data-copy>Copy</button></div></li>` : ''}
          ${login.accepts_code ? `<li>${login.code_sent ? 'Code sent. Waiting for the computer to finish…' : 'The page then shows a code. Paste it here:'}
              ${login.code_sent ? '' : `<form class="model-login-paste" data-paste><input name="code" type="text" autocomplete="off" spellcheck="false" required maxlength="700" aria-label="Code from the sign-in page" placeholder="Paste the code">
              <button class="primary" type="submit">Send code</button></form>`}</li>` : ''}
        </ol>`;
      }
      const lines = (login.lines || []).length && (state === 'waiting' && !login.url && !login.code || state === 'failed')
        ? `<details open><summary>What the computer printed</summary><pre data-lines>${text(login.lines.join('\n'))}</pre></details>` : '';
      // Keep what the person is typing when a poll redraws around it.
      const typed = body.querySelector('[name=code]')?.value || '';
      body.innerHTML = head + lines;
      const field = body.querySelector('[name=code]');
      if (field && typed) field.value = typed;
      status.textContent = label;
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
        login = await post(`${base}/${encodeURIComponent(login.id)}/code`, {code: field.value.trim()});
        field.value = '';
        draw();
      } catch (error) { status.textContent = error.message; }
      finally { sending = false; }
    });
    actions.addEventListener('click', async event => {
      if (event.target.closest('[data-cancel]')) {
        try { login = await post(`${base}/${encodeURIComponent(login.id)}/cancel`, {}); draw(); }
        catch (error) { status.textContent = error.message; }
      } else if (event.target.closest('[data-retry]')) {
        void begin();
      }
    });

    const tick = async () => {
      if (closed) return;
      try {
        const next = await get(`${base}/${encodeURIComponent(login.id)}`);
        const changed = next.state !== login.state;
        login = next;
        if (!closed) draw();
        if (changed && next.state === 'signed_in') refreshUntil(runnerId, runtime, profile, 'ready', next.updated);
      } catch (error) { status.textContent = error.message; }
      if (!closed && active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    const begin = async () => {
      clearTimeout(timer);
      closed = false;
      status.textContent = 'Starting…';
      try { login = await post(base, {runtime, ...(profile ? {profile} : {}), ...(switchAccount ? {switch: true} : {})}); }
      catch (error) { status.textContent = error.message; actions.innerHTML = '<button class="ghost" type="button" data-close>Close</button>'; return; }
      draw();
      if (active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    dialog.showModal();
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
    dialog.className = 'tmodal model-login model-logout';
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

  // The Sign out / Switch account buttons for a signed-in login, and Use the shared key for a computer login a person
  // signed out. Only for a CLI whose manifest has a logout (`sign_out`), on a computer that is online, to whoever may
  // sign it in (the caller checks: an admin or the computer's operator).
  // No Switch account where the CLI keeps the login in the system keychain (`switch_unavailable_on`, by the computer's
  // `platform`): there is no side to sign in on, so a cancel could not keep the old account.
  function actionsHTML({runnerId, runtime, machine = '', profile = '', signedIn = false, signedOut = false, online = true, platform = ''}) {
    const caps = typeof harnessCaps === 'function' ? harnessCaps(runtime) : {};
    if (!online || !caps.sign_out) return '';
    const data = `data-runner="${text(runnerId)}" data-runtime="${text(runtime)}" data-machine="${text(machine)}"${profile ? ` data-profile="${text(profile)}"` : ''}`;
    if (signedIn) {
      const switches = !(caps.switch_unavailable_on || []).includes(platform);
      return (switches ? ` <button class="ghost machine-signin" type="button" data-model-switch ${data}>Switch account</button>` : '')
        + ` <button class="ghost machine-signin" type="button" data-model-sign-out ${data}>Sign out</button>`;
    }
    return signedOut && !profile ? ` <button class="ghost machine-signin" type="button" data-model-shared-key ${data}>Use the shared key</button>` : '';
  }

  window.TicoModelLogin = {open, signOut, useSharedKey, actionsHTML};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-model-login], [data-model-switch], [data-model-sign-out], [data-model-shared-key]');
    if (!button) return;
    event.preventDefault();
    const target = {runnerId: button.dataset.runner, runtime: button.dataset.runtime, machine: button.dataset.machine || '', profile: button.dataset.profile || ''};
    if (button.hasAttribute('data-model-sign-out')) signOut(target);
    else if (button.hasAttribute('data-model-shared-key')) void useSharedKey(target);
    else open({...target, switchAccount: button.hasAttribute('data-model-switch')});
  });
})();
