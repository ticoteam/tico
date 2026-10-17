/* Sign a model in on a computer from the browser (backend/model_login.py, runner-rs/src/login.rs).
   The dialog shows what the model's own CLI prints: a link and a one-time code, or a link and a
   field for the code the sign-in page hands back. This page never sees the resulting credential;
   the CLI keeps it on the computer, and the chip flips when the computer reports it is ready. */
'use strict';
(function () {
  const RUNTIME_NAMES = {codex: 'Codex (ChatGPT)', claude: 'Claude Code'};
  const POLL_MS = 2000, STALLED_S = 30;
  const text = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const active = state => ['requested', 'starting', 'waiting'].includes(state);

  // The computer only reports a sign-in on its next heartbeat, so look again for a little while; until then its row
  // and pop-up say "Waiting for <computer>" (ui/app/computer-detail.js). `profile`: a named subscription's sign-in.
  const pending = window.machineLoginPending = window.machineLoginPending || new Map();
  function refreshUntilReady(runnerId, runtime, profile = '') {
    let tries = 0;
    pending.set(runnerId, Date.now() + 50000);
    const look = async () => {
      tries += 1;
      let ready = false;
      try {
        const machine = SETTINGS_DATA.machines.find(row => row.id === runnerId);
        ready = profile ? machine?.profiles?.find(p => p.name === profile)?.runtimes?.[runtime]?.signed_in === true
          : machine?.readiness?.runtimes?.[runtime]?.authenticated === 'ready';
      } catch { /* not on Settings */ }
      if (ready || tries >= 15) pending.delete(runnerId);
      try { if (typeof loadSettings === 'function') await loadSettings(); } catch { /* the next try asks again */ }
      try { if (typeof gsRefresh === 'function') await gsRefresh(); } catch { /* same */ }
      if (!ready && tries < 15) setTimeout(look, 3000);
    };
    setTimeout(look, 1500);
  }

  // `profile` signs in one of the computer's subscriptions (runner-rs/src/profiles.rs) instead of its default login.
  function open({runnerId, runtime, machine, profile = ''}) {
    const base = `/v2/computers/${encodeURIComponent(runnerId)}/logins`;
    const name = (RUNTIME_NAMES[runtime] || runtime) + (profile ? ` · ${profile}` : '');
    const dialog = document.createElement('dialog');
    dialog.className = 'tmodal model-login';
    dialog.setAttribute('aria-labelledby', 'model-login-title');
    dialog.innerHTML = `<header><h2 id="model-login-title">Sign in to ${text(name)}${machine ? ` on ${text(machine)}` : ''}</h2>
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
      } else if (state === 'signed_in') {
        label = `Signed in. ${name} is ready on ${machine || 'the computer'}.`;
      } else if (state === 'failed') {
        label = login.message || 'The sign-in failed.';
      } else if (state === 'expired') {
        label = 'The sign-in ran out of time. Start it again.';
      } else if (state === 'cancelled') {
        label = 'The sign-in was cancelled.';
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
      actions.innerHTML = active(state)
        ? '<button class="ghost" type="button" data-cancel>Cancel sign-in</button>'
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
        if (changed && next.state === 'signed_in') {
          refreshUntilReady(runnerId, runtime, profile);
        }
      } catch (error) { status.textContent = error.message; }
      if (!closed && active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    const begin = async () => {
      clearTimeout(timer);
      closed = false;
      status.textContent = 'Starting…';
      try { login = await post(base, profile ? {runtime, profile} : {runtime}); }
      catch (error) { status.textContent = error.message; actions.innerHTML = '<button class="ghost" type="button" data-close>Close</button>'; return; }
      draw();
      if (active(login.state)) timer = setTimeout(tick, POLL_MS);
    };
    dialog.showModal();
    void begin();
    return dialog;
  }

  window.TicoModelLogin = {open};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-model-login]');
    if (!button) return;
    event.preventDefault();
    open({runnerId: button.dataset.runner, runtime: button.dataset.runtime, machine: button.dataset.machine || '', profile: button.dataset.profile || ''});
  });
})();
