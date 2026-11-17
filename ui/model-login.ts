/* Sign a model in on a computer from the browser (backend/model_login.py, runner-rs/src/login.rs).
   The dialog shows what the model's own CLI prints: a link and a one-time code, or a link and a
   field for the code the sign-in page hands back. This page never sees the resulting credential;
   the CLI keeps it on the computer, and the chip flips when the computer reports it is ready.
   The same module signs a login out (after a confirmation naming the bots that stop), switches it to
   another account (a sign-in on the side: canceling leaves the old account working), and lets the
   key the computer is given sign a signed-out computer login in again. `actionsHTML` draws those buttons for
   any page that lists a login: Settings > Computers and Manage subscription today.
   Model keys are added, replaced and given to computers in Settings > Models (ui/app/subscriptions.ts), not here.
   On a hosted Tico (config `hosted`, TICO_HOSTED) the person has no terminal on the computer, so the sign-in shows only
   the provider's link and code (never what else the CLI printed; a failure gives its message), never points to a
   terminal, and closes by itself once it has signed in or its cancel is confirmed.
   Connect (`connect`, Settings > Models, ui/app/subscriptions.ts) follows a sign-in the server started for a company
   subscription on a computer it chose. It ends when the server has saved the result, which this page never sees. */
'use strict';
(function () {
  // One sign-in, switch or sign-out job as the server's logins routes answer it (backend/model_login.py).
  interface LoginRow {
    id: string;
    state: string;
    created_at: string;
    updated_at?: string;
    message?: string;
    url?: string;
    code?: string;
    accepts_code?: boolean;
    code_sent?: boolean;
    lines?: string[];
    runner_id: string;
    computer?: string;
    not_yours?: boolean;
  }
  interface LoginConnect {
    title: string;
    first?: LoginRow | null;
    start: () => Promise<LoginRow>;
    done: string;
  }
  interface LoginTarget {
    runnerId: string;
    runtime: string;
    machine?: string;
    profile?: string;
  }
  // The part of a Settings > Computers row the refresh reads.
  interface LoginMachine {
    id: string;
    label?: string;
    revoked_at?: string | null;
    readiness_at?: string;
    profiles?: {name: string; runtimes?: Record<string, {signed_in?: boolean} | undefined>}[];
    readiness?: {runtimes?: Record<string, {authenticated?: string} | undefined>};
  }
  interface BotRow {name?: string; bot: string}
  interface ActionOptions {
    runnerId: string;
    runtime: string;
    machine?: string;
    profile?: string;
    signedIn?: boolean;
    signedOut?: boolean;
    online?: boolean;
    platform?: string;
  }

  // An element the markup this file just wrote must hold; missing means the markup and this code disagree.
  function part<E extends Element = HTMLElement>(root: ParentNode, selector: string): E {
    const found = root.querySelector<E>(selector);
    if (!found) throw new Error(`model-login: ${selector} is missing`);
    return found;
  }

  const RUNTIME_NAMES: Record<string, string> = {codex: 'Codex (ChatGPT)', claude: 'Claude Code'};
  const POLL_MS = 2000, STALLED_S = 30;
  const text = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c] ?? c));
  // `finishing`: a switch is putting the new account in place, past the point where it can be canceled.
  const active = (state: string) => ['requested', 'starting', 'waiting', 'finishing'].includes(state);
  const hosted = () => typeof S !== 'undefined' && !!S.config?.hosted;

  // The computer shows a sign-in, switch or sign-out only in the readiness it reports next. Look again until it has
  // reported since the change finished (`since`, the server's time on the finished row; `readiness_at` is when the
  // computer last reported) and that report shows `want` ('ready' or 'signed_out') for this login, redrawing
  // Settings > Computers. About a minute at most; until then its row and pop-up say "Waiting for <computer>"
  // (`machineLoginPending`, ui/app/computer-detail.ts).
  const pending = window.machineLoginPending = window.machineLoginPending || new Map();
  function refreshUntil(runnerId: string, runtime: string, profile: string, want: string, since: string | undefined) {
    const after = Date.parse(since ?? '') || Date.now();
    let tries = 0;
    pending.set(runnerId, Date.now() + 65000);
    const look = async () => {
      tries += 1;
      try { if (typeof loadSettings === 'function') await loadSettings(); } catch { /* the next try asks again */ }
      try { if (typeof gsRefresh === 'function') await gsRefresh(); } catch { /* same */ }
      let shown = false;
      try {
        const machines: LoginMachine[] = SETTINGS_DATA.machines;
        const machine = machines.find(row => row.id === runnerId);
        const ready = profile ? machine?.profiles?.find(p => p.name === profile)?.runtimes?.[runtime]?.signed_in === true
          : machine?.readiness?.runtimes?.[runtime]?.authenticated === 'ready';
        shown = Date.parse(machine?.readiness_at ?? '') > after && (want === 'ready' ? ready : !ready);
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
  // `into` is a pop-up already open (Add connection, ui/app/computer-detail.ts): the sign-in carries on inside it.
  // `connect` {title, first, start, done}: `first` is the sign-in already started, `start` starts another for Try again,
  // `done` is what the toast says once the server has saved the result.
  function open({runnerId, runtime, machine, profile = '', switchAccount = false, into = null, connect = null}: LoginTarget & {switchAccount?: boolean; into?: HTMLDialogElement | null; connect?: LoginConnect | null}) {
    const base = () => `/v2/computers/${encodeURIComponent(runnerId)}/logins`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const kept = switchAccount ? ' The account signed in before still works.' : '';
    const where = machine || 'the computer';
    const dialog = into || document.createElement('dialog');
    dialog.className = 'tmodal tmodal-shell model-login';
    dialog.setAttribute('aria-labelledby', 'model-login-title');
    const title = connect ? text(connect.title)
      : `${switchAccount ? 'Switch account for' : 'Sign in to'} ${text(name)}${machine ? ` on ${text(machine)}` : ''}`;
    dialog.innerHTML = `<header><h2 id="model-login-title">${title}</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button></header>
      <div class="model-login-body" data-body></div>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions></div>`;
    if (!into) document.body.appendChild(dialog);
    const body = part(dialog, '[data-body]'), status = part(dialog, '[data-status]'),
          actions = part(dialog, '[data-actions]');
    // `note`: why a cancel did not go through, kept on screen while the sign-in it left running goes on.
    let login: LoginRow | null = null, timer: number | null = null, closed = false, sending = false, note = '', first = connect?.first || null, elsewhere = false;

    const stop = () => { closed = true; clearTimeout(timer ?? undefined); };
    const close = async () => {
      stop();
      if (login && active(login.state)) {
        try { await post(`${base()}/${encodeURIComponent(login.id)}/cancel`, {}); } catch { /* it expires on its own */ }
      }
      if (dialog.open) dialog.close();
    };
    dialog.addEventListener('close', () => { stop(); dialog.remove(); });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || eventElement(event)?.closest('[data-close]')) void close();
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); void close(); });

    const draw = () => {
      if (!login) return;
      const state = login.state;
      const seconds = (Date.now() - new Date(login.created_at).getTime()) / 1000;
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
        label = switchAccount ? `Switched. ${name} on ${where} now uses the new account; its bots keep it.`
          : `Signed in. ${name} is ready on ${machine || 'the computer'}.`;
      } else if (state === 'failed') {
        label = (login.message || 'The sign-in failed.') + kept;
      } else if (state === 'expired') {
        label = 'The sign-in ran out of time. Start it again.' + kept;
      } else if (state === 'canceled') {
        label = 'The sign-in was canceled.' + kept;
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
        </ol>${connect && elsewhere ? `<p class="model-login-where">Runs on ${text(machine)}, not your computer: the token passes through it. To avoid that, cancel and paste.</p>` : ''}`;
      }
      const lines = (login.lines || []).length && !hosted() && !connect && (state === 'waiting' && !login.url && !login.code || state === 'failed')
        ? `<details open><summary>What the computer printed</summary><pre data-lines>${text((login.lines || []).join('\n'))}</pre></details>` : '';
      // Keep what the person is typing when a poll redraws around it.
      const typed = body.querySelector<HTMLInputElement>('[name=code]')?.value || '';
      body.innerHTML = head + lines;
      const field = body.querySelector<HTMLInputElement>('[name=code]');
      if (field && typed) field.value = typed;
      status.textContent = note && active(state) ? `${label} ${note}` : label;
      status.dataset.state = state;
      actions.innerHTML = state === 'finishing' ? ''
        : active(state) ? '<button class="ghost" type="button" data-cancel>Cancel sign-in</button>'
        // Cancel left and the forward action last (style guide, Button order); finished, Done ends it. ✕ always dismisses.
        : state === 'signed_in' ? '<button class="primary" type="button" data-close>Done</button>'
        : '<button class="ghost" type="button" data-close>Cancel</button><button class="primary" type="button" data-retry>Try again</button>';
    };

    body.addEventListener('click', event => {
      const copy = eventElement(event)?.closest('[data-copy]');
      if (copy && login) void copyText(login.code!).then(() => toast('Code copied'), () => toast('Could not copy the code', true));
    });
    body.addEventListener('submit', async event => {
      event.preventDefault();
      const field = eventElement(event)?.querySelector<HTMLInputElement>('[name=code]');
      if (sending || !login || !field?.value.trim()) return;
      sending = true;
      try {
        login = await post(`${base()}/${encodeURIComponent(login.id)}/code`, {code: field.value.trim()});
        field.value = '';
        draw();
      } catch (error) { status.textContent = errorMessage(error, ''); }
      finally { sending = false; }
    });
    actions.addEventListener('click', async event => {
      const target = eventElement(event);
      if (target?.closest('[data-cancel]')) {
        // On a hosted Tico the pane closes once the computer's sign-in is canceled. A refused cancel (a switch
        // already putting the new account in place) or a failed one leaves it open, saying why and following it on.
        try {
          if (!login) throw new Error('No sign-in is open');
          login = await post(`${base()}/${encodeURIComponent(login.id)}/cancel`, {});
          note = '';
          draw();
          if (hosted() && login?.state === 'canceled') { toast(status.textContent); stop(); if (dialog.open) dialog.close(); }
        } catch (error) { note = errorMessage(error, ''); status.textContent = note; }
      } else if (target?.closest('[data-retry]')) {
        void begin();
      }
    });

    const tick = async () => {
      if (closed || !login) return;
      try {
        const next = await get<LoginRow>(`${base()}/${encodeURIComponent(login.id)}`);
        const changed = next.state !== login.state;
        login = next;
        if (!closed) draw();
        if (changed && connect && next.state === 'done') { toast(connect.done); stop(); if (dialog.open) dialog.close(); }
        if (changed && next.state === 'signed_in') {
          refreshUntil(runnerId, runtime, profile, 'ready', next.updated_at);
          if (hosted()) { toast(status.textContent); stop(); if (dialog.open) dialog.close(); }
        }
      } catch (error) { status.textContent = errorMessage(error, ''); }
      if (!closed && login && active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    const begin = async () => {
      clearTimeout(timer ?? undefined);
      closed = false;
      note = '';
      status.textContent = 'Starting…';
      try {
        if (connect) {
          // Try again may run on another computer: the server chooses.
          login = first || await connect.start();
          first = null;
          runnerId = login.runner_id; machine = login.computer || machine; elsewhere = Boolean(login.not_yours);
          // Closed while Try again was on its way: nobody is left to finish the sign-in it started.
          if (closed) { await cancel(login); return; }
        } else login = await post(base(), {runtime, ...(profile ? {profile} : {}), ...(switchAccount ? {switch: true} : {})});
      } catch (error) { status.textContent = errorMessage(error, ''); actions.innerHTML = '<button class="ghost" type="button" data-close>Done</button>'; return; }
      draw();
      if (login && active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    if (!dialog.open) dialog.showModal();
    void begin();
    return dialog;
  }

  // Cancel a sign-in nobody is left to finish. If this does not reach the server, it expires on its own.
  const cancel = (login: LoginRow) => post(`/v2/computers/${encodeURIComponent(login.runner_id)}/logins/${encodeURIComponent(login.id)}/cancel`, {}).catch(() => {});

  // "Reviewer 1", "Reviewer 1 and QA", "Reviewer 1, QA and Scout", "Reviewer 1, QA and 2 more".
  function botsWords(names: string[]) {
    if (names.length <= 1) return names.join('');
    if (names.length <= 3) return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
    return `${names.slice(0, 2).join(', ')} and ${names.length - 2} more`;
  }

  // Sign a login out, after a confirmation naming the bots on it that stop. Bots and assignments stay; the login reads
  // Signed out until someone signs in again, and the company's shared key does not sign it back in by itself.
  function signOut({runnerId, runtime, machine, profile = ''}: LoginTarget) {
    const computer = `/v2/computers/${encodeURIComponent(runnerId)}`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const where = machine || 'the computer';
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal tmodal-shell model-login model-logout';
    dialog.setAttribute('aria-labelledby', 'model-logout-title');
    dialog.innerHTML = `<header><h2 id="model-logout-title">Sign out of ${text(name)} on ${text(where)}?</h2>
        <button class="ghost tmodal-x" type="button" data-close aria-label="Close"><span class="nav-icon" aria-hidden="true">close</span></button></header>
      <div class="model-login-body"><p data-bots>Checking which bots use it…</p>
        <p class="muted">Bots keep this login, so nothing needs reassigning: they start again once someone signs in.${profile ? '' : ' The company\'s shared key won\'t sign it back in by itself.'}</p></div>
      <p class="model-login-status" role="status" aria-live="polite" data-status></p>
      <div class="model-login-actions" data-actions><button class="ghost" type="button" data-close>Cancel</button>
        <button class="primary people-danger" type="button" data-sign-out disabled>Sign out</button></div>`;
    document.body.appendChild(dialog);
    const status = part(dialog, '[data-status]'), actions = part(dialog, '[data-actions]');
    let timer: number | null = null, closed = false;
    dialog.addEventListener('close', () => { closed = true; clearTimeout(timer ?? undefined); dialog.remove(); });
    dialog.addEventListener('click', event => {
      if (event.target === dialog || eventElement(event)?.closest('[data-close]')) dialog.close();
    });
    // Sign out stays off until the bots that stop are named: a quick click or a failed check never stops them unnamed.
    const query = `runtime=${encodeURIComponent(runtime)}${profile ? `&profile=${encodeURIComponent(profile)}` : ''}`;
    const said = part(dialog, '[data-bots]');
    const loadBots = () => {
      said.textContent = 'Checking which bots use it…';
      get<{bots?: BotRow[]}>(`${computer}/logouts/bots?${query}`).then(found => {
        const bots = (found.bots || []).map(bot => bot.name || bot.bot);
        said.textContent = bots.length ? `${botsWords(bots)} will stop running until this is signed in again.`
          : 'No bot uses this login right now.';
        if (bots.length > 3) said.title = bots.join(', ');
        const button = actions.querySelector<HTMLButtonElement>('[data-sign-out]');
        if (button) button.disabled = false;
      }, error => {
        said.textContent = `Couldn't check which bots use it (${errorMessage(error, '')}), so it can't be signed out yet. `;
        const retry = document.createElement('button');
        retry.className = 'ghost';
        retry.type = 'button';
        retry.textContent = 'Try again';
        retry.addEventListener('click', loadBots);
        said.appendChild(retry);
      });
    };
    loadBots();
    const finish = (row: LoginRow) => {
      status.dataset.state = row.state;
      status.textContent = row.state === 'signed_out' ? `Signed out. ${name} on ${where} stays signed out until someone signs in.`
        : row.state === 'failed' ? (row.message || 'The sign-out did not finish; the login is as it was.')
        : row.state === 'expired' ? 'The computer did not sign out in time; the login is as it was.' : '';
      actions.innerHTML = '<button class="ghost" type="button" data-close>Done</button>';
      refreshUntil(runnerId, runtime, profile, row.state === 'signed_out' ? 'signed_out' : 'ready', row.updated_at);
    };
    const tick = async (id: string) => {
      if (closed) return;
      try {
        const row = await get<LoginRow>(`${computer}/logins/${encodeURIComponent(id)}`);
        if (active(row.state)) { timer = setTimeout(() => tick(id), POLL_MS); return; }
        finish(row);
      } catch (error) { status.textContent = errorMessage(error, ''); timer = setTimeout(() => tick(id), POLL_MS); }
    };
    actions.addEventListener('click', async event => {
      const signOutButton = eventElement(event)?.closest<HTMLButtonElement>('[data-sign-out]');
      if (!signOutButton || signOutButton.disabled) return;
      actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
      status.textContent = `Signing out on ${where}…`;
      try {
        const row: LoginRow = await post(`${computer}/logouts`, {runtime, ...(profile ? {profile} : {})});
        if (active(row.state)) timer = setTimeout(() => tick(row.id), POLL_MS); else finish(row);
      } catch (error) {
        status.textContent = errorMessage(error, '');
        actions.innerHTML = '<button class="ghost" type="button" data-close>Done</button>';
      }
    });
    dialog.showModal();
    return dialog;
  }

  // A computer login a person signed out takes the key it is given in Settings > Models again (when there is one).
  async function useSharedKey({runnerId, runtime}: LoginTarget) {
    try {
      const row: LoginRow = await post(`/v2/computers/${encodeURIComponent(runnerId)}/shared-key`, {runtime});
      toast('The key it is given signs it in at the computer\'s next check, if it has one');
      refreshUntil(runnerId, runtime, '', 'ready', row.created_at);
    } catch (error) { toast(errorMessage(error, ''), true); }
  }

  // Every action here waits for the computer: offline, its button stays, disabled, and says why (nothing queues).
  const offline = (online: boolean) => online ? '' : ' disabled title="Computer is offline"';
  // Switch account and Sign out sit on every signed-in line, so they are icons; the name is the label and the tooltip
  // (an offline title in `attrs` comes first and wins).
  // (A *_ICONS map, so scripts/build-icon-font.py keeps their glyphs.)
  const LOGIN_ICONS = {switch: 'manage_accounts', signOut: 'logout'};
  const iconButton = (icon: string, label: string, attrs: string) => `<button class="ghost machine-signin machine-icon-btn" type="button" ${attrs} aria-label="${label}" title="${label}"><span class="nav-icon" aria-hidden="true">${icon}</span></button>`;

  // The Sign out / Switch account buttons for a signed-in login, and Use the shared key for a computer login a person
  // signed out. Only for a CLI whose manifest has a logout (`sign_out`), to whoever may sign it in (the caller checks:
  // an admin or the computer's operator), disabled while the computer is offline. Moving a login between a key and a
  // subscription is done in Settings > Models: give the key to the computer there, or take it off and sign in.
  // No switch where the CLI keeps the login in the system keychain (`switch_unavailable_on`, by the computer's
  // `platform`): there is no side to sign in on, so a cancel could not keep the old login.
  function actionsHTML({runnerId, runtime, machine = '', profile = '', signedIn = false, signedOut = false, online = true, platform = ''}: ActionOptions) {
    const caps = typeof harnessCaps === 'function' ? harnessCaps(runtime) : {};
    if (!caps.sign_out) return '';
    const data = `data-runner="${text(runnerId)}" data-runtime="${text(runtime)}" data-machine="${text(machine)}"${profile ? ` data-profile="${text(profile)}"` : ''}${offline(online)}`;
    if (signedIn) {
      const switches = !(caps.switch_unavailable_on || []).includes(platform);
      return (switches ? ` ${iconButton(LOGIN_ICONS.switch, 'Switch account', `data-model-switch ${data}`)}` : '')
        + ` ${iconButton(LOGIN_ICONS.signOut, 'Sign out', `data-model-sign-out ${data}`)}`;
    }
    return signedOut && !profile ? ` <button class="ghost machine-signin" type="button" data-model-shared-key ${data}>Use the shared key</button>` : '';
  }

  window.TicoModelLogin = {open, cancel, signOut, useSharedKey, actionsHTML};
  document.addEventListener('click', event => {
    const button = eventElement(event)?.closest<HTMLElement>('[data-model-login], [data-model-switch], [data-model-sign-out], [data-model-shared-key]');
    if (!button) return;
    event.preventDefault();
    const target = {runnerId: button.dataset.runner ?? '', runtime: button.dataset.runtime ?? '', machine: button.dataset.machine || '', profile: button.dataset.profile || ''};
    if (button.hasAttribute('data-model-sign-out')) signOut(target);
    else if (button.hasAttribute('data-model-shared-key')) void useSharedKey(target);
    else open({...target, switchAccount: button.hasAttribute('data-model-switch')});
  });
})();
