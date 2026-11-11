/* ui/app/plan-signin.ts — adding a subscription, as one sheet (Settings > Models and the setup pop-up): tap the plan,
   the server starts its sign-in on a computer (starting the company's cloud computer first when none is online), one
   button opens the provider, and the sheet closes itself once the server has saved the result. Reconnect is the same
   sheet from the second step. Pasting a value is a link behind the sign-in, never the first thing shown; an API key
   has a small form of its own. The page never sees the saved value, and keeps no link, code or token in storage.
   Routes: POST /v2/model-subscriptions/connect, then the sign-in's own (backend/model_subscriptions.py `connect`,
   backend/model_login.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The sign-in the server started, or `waking`: no sign-in yet, the cloud computer is starting, ask again.
interface PlanLogin {id: string; state: string; runner_id: string; runtime: string; url?: string; code?: string; accepts_code?: boolean;
  code_sent?: boolean; message?: string; name?: string; waking?: boolean; computer?: string; not_yours?: boolean}
type PlanStep = 'pick' | 'starting' | 'signin' | 'done' | 'failed' | 'none' | 'paste' | 'key';
interface PlanSheetOptions {sub?: SubsRow | null; kind?: string; onDone?: () => void}

const PLAN_POLL_MS = 2000, PLAN_ASK_MS = 3000, PLAN_DONE_MS = 1400;
// What the provider's page is called on its button.
const PLAN_PROVIDER: Record<string, string> = {claude: 'Claude', codex: 'ChatGPT'};
// A code as the provider's page hands it back: long, one word. Shorter ones are typed and sent with Sign in.
const PLAN_CODE = /^[A-Za-z0-9_\-#.~%+=/]{20,600}$/;
const PLAN_PASTE: Record<string, {label: string; help: string}> = {
  claude_token: {label: 'Token', help: 'Run claude setup-token on a computer, then paste what it prints.'},
  codex_login: {label: 'auth.json', help: 'From ~/.codex/auth.json on a computer signed in with codex login.'}};
const planClock = (since: number) => { const s = Math.max(0, Math.floor((Date.now() - since) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const planLive = (state: string) => ['requested', 'starting', 'waiting', 'finishing'].includes(state);

function planSignIn({sub = null, kind = '', onDone}: PlanSheetOptions = {}): HTMLDialogElement {
  const dialog = document.createElement('dialog');
  dialog.className = 'tmodal tmodal-shell plan-sheet';
  dialog.setAttribute('aria-labelledby', 'plan-sheet-title');
  dialog.innerHTML = `<header><h2 id="plan-sheet-title"></h2><button class="ghost tmodal-x" type="button" data-plan-close aria-label="Close">✕</button></header>
    <div class="plan-body" data-plan-body></div>`;
  const heading = dialog.querySelector<HTMLElement>('h2')!, body = dialog.querySelector<HTMLElement>('[data-plan-body]')!;
  let step: PlanStep = 'pick', chosen = sub?.kind || kind, login: PlanLogin | null = null, name = sub?.name || '';
  let since = Date.now(), waking = false, message = '', closed = false, sending = false, drawn = '';
  let timer: ReturnType<typeof setTimeout> | undefined, clock: ReturnType<typeof setInterval> | undefined;
  // Each start has a number: an answer that comes back after Cancel, Close or Try again belongs to nobody.
  let run = 0;
  // Said yes to restarting the runs once: the next write of this sheet carries it, and nobody is asked twice.
  let agreed = false;
  // The server's reference for a refusal, when it sends one: small, after its sentence, for whoever is asked to help.
  let ref = '';

  const row = () => subsKind(chosen);
  const provider = () => PLAN_PROVIDER[row().runtime] || row().label;
  const logins = () => `/v2/computers/${encodeURIComponent(login?.runner_id || '')}/logins/${encodeURIComponent(login?.id || '')}`;
  const drop = (found: PlanLogin | null) => { if (found?.id && planLive(found.state)) void post(`/v2/computers/${encodeURIComponent(found.runner_id)}/logins/${encodeURIComponent(found.id)}/cancel`, {}).catch(() => {}); };
  const halt = () => { run += 1; clearTimeout(timer); const was = login; login = null; drop(was); };
  const close = () => { if (closed) return; closed = true; halt(); clearInterval(clock); if (dialog.open) dialog.close(); };
  dialog.addEventListener('close', () => { close(); dialog.remove(); if (typeof subsRefresh === 'function') void subsRefresh(); });
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });

  const pasteLink = () => row().connect === false ? '' : '<button type="button" class="linkish plan-alt" data-plan-paste>Paste instead</button>';
  const waitHTML = (words: string, what: string) => `<p class="plan-wait" role="status" data-plan-wait="${what}"><span class="plan-spin" aria-hidden="true"></span><span>${esc(words)}</span><span class="tnum muted" data-plan-clock>${planClock(since)}</span></p>`;
  const html = (): string => {
    if (step === 'pick') {
      const rows = SUBS.kinds.length ? SUBS.kinds : SUBS_KINDS;
      const plans = rows.filter(k => subsOwned(k.kind)), keys = rows.filter(k => !subsOwned(k.kind));
      return `<div class="plan-list">${plans.map(k => `<button type="button" class="plan-pick" data-plan-kind="${esc(k.kind)}">${esc(k.label)}<span class="nav-icon" aria-hidden="true">chevron_right</span></button>`).join('')}</div>
        <div class="plan-keys">${keys.map(k => `<button type="button" class="linkish" data-plan-kind="${esc(k.kind)}">${esc(k.label)}</button>`).join('')}</div>`;
    }
    if (step === 'starting') return `${waking ? waitHTML('Starting your computer', 'computer') : waitHTML('Starting', 'start')}
      <div class="plan-foot"><button type="button" class="ghost" data-plan-cancel>Cancel</button></div>`;
    if (step === 'signin' && login) {
      const open = `<a class="plan-open" href="${esc(login.url || '')}" target="_blank" rel="noopener noreferrer" data-plan-open>Open ${esc(provider())}</a>`;
      const code = login.code ? `<div class="plan-code"><code data-plan-code>${esc(login.code)}</code><button type="button" class="ghost" data-plan-copy>Copy</button></div>` : '';
      const field = login.accepts_code && !login.code_sent ? `<form class="plan-entry" data-plan-entry novalidate>
          <div class="plan-entry-row"><input name="code" type="text" inputmode="text" autocomplete="one-time-code" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="700" aria-label="Code" placeholder="Code">
            <button type="button" class="ghost" data-plan-clip>Paste</button></div>
          <p class="err plan-err" data-plan-err hidden></p>
          <button type="submit" class="ghost">Sign in</button></form>` : '';
      const finishing = login.state === 'finishing' || !!login.code_sent;
      return `${code}${open}${field}${finishing ? waitHTML('Finishing', 'finish') : waitHTML('Waiting for you to finish in the other tab', 'tab')}
        ${login.not_yours && login.computer ? `<p class="muted plan-note" data-plan-elsewhere>Signs in through ${esc(login.computer)}.</p>` : ''}
        <p class="muted plan-note">Only use your subscription on your own bots, not shared ones.</p>
        <div class="plan-foot">${pasteLink()}<span class="spacer"></span><button type="button" class="ghost" data-plan-cancel>Cancel</button></div>`;
    }
    if (step === 'done') return `<div class="plan-done" role="status"><span class="plan-check" aria-hidden="true">✓</span><b>${esc(name)}</b></div>`;
    if (step === 'failed') return `<p class="plan-said" role="alert">${esc(message)}${ref ? ` <small class="muted" data-plan-ref>${esc(ref)}</small>` : ''}</p>
      <button type="button" class="primary plan-wide" data-plan-retry>Try again</button>
      <div class="plan-foot">${pasteLink()}</div>`;
    if (step === 'none') return `<p class="plan-said" role="alert">No computer online</p>
      <a class="plan-open" href="#/settings" data-gs-tab="devices" data-plan-computer>Add a computer</a>
      <div class="plan-foot">${pasteLink()}</div>`;
    if (step === 'paste') {
      const words = PLAN_PASTE[chosen] || {label: row().secret_label || 'Value', help: ''};
      const file = chosen === 'codex_login';
      return `<form class="plan-form" data-plan-save novalidate>
        <label class="plan-label" for="plan-secret">${esc(words.label)}</label>
        ${file ? `<textarea id="plan-secret" name="secret" rows="4" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>
          <button type="button" class="ghost" data-plan-file>Choose auth.json</button><input type="file" accept=".json,application/json" data-plan-file-input hidden>`
          : '<input id="plan-secret" name="secret" type="password" spellcheck="false" autocomplete="off" autocapitalize="off">'}
        ${words.help ? `<p class="muted plan-note">${esc(words.help)}</p>` : ''}
        <p class="err plan-err" data-plan-err hidden></p>
        <div class="plan-foot"><button type="button" class="ghost" data-plan-back>Back</button><span class="spacer"></span><button type="submit" class="primary">Save</button></div></form>`;
    }
    return `<form class="plan-form" data-plan-save novalidate>
      <label class="plan-label" for="plan-secret">Key</label>
      <input id="plan-secret" name="secret" type="password" spellcheck="false" autocomplete="off" autocapitalize="off">
      ${sub ? '' : `<label class="plan-label" for="plan-name">Name</label><input id="plan-name" name="name" type="text" maxlength="80" autocomplete="off" placeholder="${esc(row().label.replace(/ key$/, ''))}">`}
      <p class="err plan-err" data-plan-err hidden></p>
      <div class="plan-foot">${sub ? '' : '<button type="button" class="ghost" data-plan-back>Back</button>'}<span class="spacer"></span><button type="submit" class="primary">Save</button></div></form>`;
  };
  // Redrawn only when what it shows changed: a poll never takes a half-typed code, and the clock ticks in place.
  const draw = () => {
    if (closed) return;
    heading.textContent = step === 'pick' ? 'Add subscription' : sub ? `Reconnect ${sub.name}` : row().label;
    dialog.dataset.planStep = step;
    const key = [step, waking, message, ref, login?.id, login?.state === 'finishing', login?.url, login?.code, login?.accepts_code, login?.code_sent].join('|');
    if (key === drawn) return;
    drawn = key;
    body.innerHTML = html();
    body.querySelector<HTMLElement>(step === 'signin' ? '[data-plan-open]' : '#plan-secret, [data-plan-retry], [data-plan-kind]')?.focus({preventScroll: true});
  };
  const show = (next: PlanStep, why = '') => { step = next; message = why; draw(); };
  const failed = (why: string, mark = '') => { halt(); ref = mark; show('failed', why); };

  // Follow the sign-in until the server has saved its result.
  const poll = async (mine: number) => {
    if (mine !== run || !login) return;
    try {
      const next: PlanLogin = await get<PlanLogin>(logins());
      if (mine !== run || !login) return;
      login = {...login, ...next};
    } catch { /* the next look asks again */ }
    const state = login.state;
    if (state === 'done') {
      login = null;
      show('done');
      if (!sub) toast(`${name} added`); else toast(`${name} reconnected`);
      onDone?.();
      timer = setTimeout(close, PLAN_DONE_MS);
      return;
    }
    // The server's own sentence when it sent one; what it adds beside it (a category, a trace) is not read here.
    if (state === 'failed') return failed(login.message || 'The sign-in did not finish');
    if (state === 'expired') return failed('The sign-in ran out of time');
    if (state === 'canceled') return close();
    // A link first: with none yet there is nothing to open, so it is still starting.
    show(state === 'waiting' && login.url || state === 'finishing' ? 'signin' : 'starting');
    timer = setTimeout(() => void poll(mine), PLAN_POLL_MS);
  };
  // Ask the server to start it. `waking`: its cloud computer is on its way, so ask again until the sign-in exists.
  const ask = async (mine: number) => {
    const start = (confirm: boolean): Promise<PlanLogin> => {
      agreed = agreed || confirm;
      return post('/v2/model-subscriptions/connect', sub ? (agreed ? {subscription: sub.id, confirm: true} : {subscription: sub.id}) : {kind: chosen});
    };
    let found: PlanLogin | null;
    try {
      // A new value restarts the runs on each computer given it: with runs going, the page asks before the sign-in.
      found = sub ? await subsConfirmed(start, running => ({title: `Reconnect ${sub.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Reconnect', danger: false})) : await start(false);
    } catch (error) {
      if (mine !== run) return;
      // A server from before the sign-in route has only the value to take.
      if (errorCode(error) === 'offline') { halt(); show('none'); }
      else if (errorStatus(error) === 404) { halt(); show('paste'); }
      else { const mark = (errorBody(error) as {ref?: unknown}).ref; failed(errorMessage(error, 'The sign-in did not start'), typeof mark === 'string' ? mark : ''); }
      return;
    }
    // Closed, canceled or started again meanwhile: nobody is left to finish this one.
    if (mine !== run) { drop(found); return; }
    if (!found) { if (sub) close(); else show('pick'); return; }
    name = found.name || name || row().label;
    if (!found.id) {
      waking = true;
      show('starting');
      timer = setTimeout(() => void ask(mine), PLAN_ASK_MS);
      return;
    }
    login = found;
    show(found.state === 'waiting' && found.url ? 'signin' : 'starting');
    timer = setTimeout(() => void poll(mine), PLAN_POLL_MS);
  };
  const begin = () => {
    halt();
    since = Date.now(); waking = false;
    show('starting');
    void ask(run);
  };
  const choose = (next: string) => {
    chosen = next;
    if (subsOwned(next) && subsKind(next).connect !== false) begin(); else show(subsOwned(next) ? 'paste' : 'key');
  };
  const sendCode = async (value: string) => {
    const code = value.trim(), said = body.querySelector<HTMLElement>('[data-plan-err]');
    if (!code || sending || !login) return;
    sending = true;
    try {
      login = {...login, ...(await post(`${logins()}/code`, {code}) as PlanLogin)};
      draw();
    } catch (error) { if (said) { said.textContent = errorMessage(error, 'That code did not go through'); said.hidden = false; } }
    finally { sending = false; }
  };
  const save = async (form: HTMLFormElement) => {
    const field = form.querySelector<HTMLInputElement | HTMLTextAreaElement>('[name=secret]')!, said = form.querySelector<HTMLElement>('[data-plan-err]')!;
    const secret = field.value.trim(), given = form.querySelector<HTMLInputElement>('[name=name]')?.value.trim();
    said.hidden = true;
    if (!secret) { field.focus(); return; }
    const button = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
    button.disabled = true;
    try {
      let saved: {name?: string} | null;
      if (sub) saved = await subsConfirmed((confirm: boolean): Promise<{name?: string}> => post(`/v2/model-subscriptions/${encodeURIComponent(sub.id)}`, confirm || agreed ? {secret, confirm: true} : {secret}),
        running => ({title: `Reconnect ${sub.name}?`, text: `${subsStopWords(running)}, and their sessions start fresh.`, ok: 'Reconnect', danger: false}));
      else saved = await post('/v2/model-subscriptions', {kind: chosen, secret, ...(given ? {name: given} : {})}) as {name?: string};
      if (!saved) { button.disabled = false; return; }
      field.value = '';   // nothing on the page keeps the value once sent
      name = saved.name || name || given || row().label;
      toast(sub ? `${name} reconnected` : `${name} added`);
      onDone?.();
      show('done');
      timer = setTimeout(close, PLAN_DONE_MS);
    } catch (error) {
      said.textContent = errorMessage(error, 'That did not save'); said.hidden = false;
      button.disabled = false;
    }
  };

  dialog.addEventListener('click', event => {
    const target = eventElement(event);
    if (event.target === dialog || target?.closest('[data-plan-close], [data-plan-cancel], [data-plan-computer]')) { close(); return; }
    const pick = target?.closest<HTMLElement>('[data-plan-kind]');
    if (pick) { choose(pick.dataset.planKind || ''); return; }
    if (target?.closest('[data-plan-retry]')) { begin(); return; }
    if (target?.closest('[data-plan-paste]')) { halt(); show('paste'); return; }
    if (target?.closest('[data-plan-back]')) { if (sub || kind) begin(); else show('pick'); return; }
    if (target?.closest('[data-plan-copy]') && login?.code) { void copyText(login.code).then(() => toast('Copied'), () => toast('Could not copy', true)); return; }
    if (target?.closest('[data-plan-file]')) { body.querySelector<HTMLInputElement>('[data-plan-file-input]')?.click(); return; }
    if (target?.closest('[data-plan-clip]')) {
      // The clipboard is read inside the tap; where the browser will not hand it over, the field takes the paste.
      const field = body.querySelector<HTMLInputElement>('[name=code]');
      const read = navigator.clipboard?.readText ? navigator.clipboard.readText() : Promise.reject(new Error('no clipboard'));
      void read.then(text => {
        const code = text.trim();
        if (!field?.isConnected) return;
        if (!code) { field.focus(); return; }
        field.value = code;
        if (PLAN_CODE.test(code)) void sendCode(code);
      }, () => field?.focus());
    }
  });
  // A pasted code goes at once; a typed one waits for Sign in.
  body.addEventListener('paste', event => {
    const field = eventElement(event)?.closest<HTMLInputElement>('[name=code]');
    if (field) setTimeout(() => { if (PLAN_CODE.test(field.value.trim())) void sendCode(field.value); }, 0);
  });
  body.addEventListener('input', () => { const said = body.querySelector<HTMLElement>('[data-plan-err]'); if (said) said.hidden = true; });
  body.addEventListener('change', event => {
    const input = eventElement(event)?.closest<HTMLInputElement>('[data-plan-file-input]'), file = input?.files?.[0];
    const area = body.querySelector<HTMLTextAreaElement>('#plan-secret');
    if (file && area) void file.text().then(text => { if (area.isConnected) area.value = text.slice(0, 65536); });
  });
  body.addEventListener('submit', event => {
    event.preventDefault();
    const form = eventElement(event)?.closest<HTMLFormElement>('form');
    if (form?.matches('[data-plan-entry]')) void sendCode(form.querySelector<HTMLInputElement>('[name=code]')?.value || '');
    else if (form) void save(form);
  });
  clock = setInterval(() => { const el = body.querySelector('[data-plan-clock]'); if (el) el.textContent = planClock(since); }, 1000);

  document.body.appendChild(dialog);
  dialog.showModal();
  if (chosen) choose(chosen); else draw();
  // Opened from the setup pop-up, the kinds may not be read yet: the list redraws with the server's own.
  if (!SUBS.loaded) void subsLoad().then(() => { if (step === 'pick') { drawn = ''; draw(); } });
  return dialog;
}
