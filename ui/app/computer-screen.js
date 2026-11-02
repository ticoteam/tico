/* ui/app/computer-screen.js — a computer's screen (#/computers/{id}/screen): watch its bots work, or take control.
   POST /api/v2/computers/{id}/screen/sessions opens a session; the browser socket at its ws_path is relayed by the hub
   to the runner's VNC server (view-only or control, enforced by the runner). noVNC (ui/vendor/novnc, MPL-2.0) is an
   ES module, so it loads with import() when the page opens, outside the classic bundle.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const SCREEN_RFB = '/vendor/novnc/core/rfb.js', SCREEN_KEYSYMDEF = '/vendor/novnc/core/input/keysymdef.js';
const SCREEN_ZOOM_MAX = 5;
let SCREEN = null;

// ----------------------------------------------------------------- who sees it
// The server's rule (the sessions route): the computer's operator or a bot admin.
const screenMayOpen = machine => !!machine && !machine.revoked_at && (settingsIsAdmin() || (!!S.me?.id && machine.owner_id === S.me.id));
const screenOn = machine => !!machine && machine.screen && machine.screen !== 'off' && machine.screen_available !== false;
// A computer's row from what is already loaded: the bot rows' own computer first (every refresh reloads them), then
// Settings' operations listing, which is read only when Settings opens, for a computer with no bots and for revoked_at.
function screenMachine(id) {
  if (!id) return null;
  const row = (SETTINGS_DATA.machines || []).find(m => m.id === id);
  const bot = (S.emps || []).find(e => e.machine?.runner_id === id)?.machine;
  if (!row && !bot) return null;
  return {id, label: bot?.label || row?.label, owner_id: bot?.owner_id ?? row?.owner_id, revoked_at: row?.revoked_at,
          screen: bot?.screen ?? row?.screen, screen_available: bot?.screen_available ?? row?.screen_available};
}
// A mode just saved, written into both loaded copies so neither shows the old one until the next refresh.
function screenSaved(id, saved) {
  for (const m of [(SETTINGS_DATA.machines || []).find(x => x.id === id), ...(S.emps || []).filter(x => x.machine?.runner_id === id).map(x => x.machine)])
    if (m) Object.assign(m, {screen: saved.screen}, 'screen_available' in saved ? {screen_available: saved.screen_available} : {});
}
// The bot's computer when its screen is on and this person may open it; '' otherwise.
function botScreenComputer(slug) {
  const id = botRow(slug)?.machine?.runner_id;
  const machine = screenMachine(id);
  return screenOn(machine) && screenMayOpen(machine) ? id : '';
}
const screenHref = id => `#/computers/${encodeURIComponent(id)}/screen`;
// The bot header's button: only when its Screen row in More says ready (so not while the computer is offline).
const botScreenLinkHTML = slug => {
  const s = botScreenState(slug), id = s.state === 'ready' ? s.id : '';
  return id ? `<a class="bot-learn bot-screen" id="bot-screen" href="${screenHref(id)}" aria-label="Screen" title="Screen"><span class="nav-icon" aria-hidden="true">computer</span><span class="bl-label">Screen</span></a>` : '';
};
// A task's owner bot's screen, beside the owner in the task's head.
const taskScreenLinkHTML = owner => {
  const id = botScreenComputer(actorSlug(owner));
  return id ? `<a class="ghost tmodal-act" href="${screenHref(id)}">Screen</a>` : '';
};

// ----------------------------------------------------------------- the Screen row in a bot's More
// Always there, so the feature stays findable when it is not working: the status, and when it is not ready, why and
// what to do. Read from the bot row (online, its computer's platform) and the computer's row; no extra request.
function botScreenState(slug) {
  const e = botRow(slug);
  // Someone who may see the bot but not read it gets a row without its computer (backend/views.py): say so, name nobody.
  if (e && !('machine' in e)) return {state: 'hidden'};
  const id = e?.machine?.runner_id, machine = screenMachine(id);
  if (!machine) return {state: e?.agent ? 'external' : 'none'};
  const base = {id, machine, owner_id: machine.owner_id ? botPersonName(machine.owner_id) : '', may: screenMayOpen(machine)};
  if (!e.online) return {...base, state: 'offline'};
  if (!machine.screen_available) return {...base, state: 'not_offered'};
  if (!machine.screen || machine.screen === 'off') return {...base, state: 'off'};
  return {...base, state: base.may ? 'ready' : 'not_allowed'};
}
const SCREEN_DOCS = `${GH}/blob/main/docs/install.md#screen`;
// Docker: `screen on` inside the runner's container turns it on live and saves it in runner.json on the runner-home
// volume, so it survives updates. Installation troubleshooting lives in the screen documentation.
// A named or --server-network runner has runner.override.yaml beside the stock file, used on every compose call
// (install.sh, the updater); without it the command reaches the default runner's project.
const SCREEN_DOCKER_CMD = 'sudo docker compose -f runner.compose.yaml $([ -f runner.override.yaml ] && echo -f runner.override.yaml) exec runner tico-runner --config /home/runner/runner.json screen on';
const SCREEN_CHECKOUT_CMD = 'scripts/tico screen on';
const screenCmdHTML = (cmd, label) => `<div class="bot-screen-cmd"><code>${esc(cmd)}</code><button class="ghost" type="button" data-screen-copy="${esc(cmd)}" aria-label="Copy the ${label} command">Copy</button></div>`;
function botScreenRowHTML(slug) {
  const s = botScreenState(slug);
  const status = text => `<span class="bot-screen-status">${text}</span>`;
  const body = {
    ready: () => `<a class="bot-screen-open" href="${screenHref(s.id)}"><span class="nav-icon" aria-hidden="true">computer</span>Open</a>${status(` · ${s.machine.screen === 'control' ? 'Control' : 'View'}`)}`,
    off: () => status('Off'),
    not_offered: () => status('Off'),
    offline: () => status("This computer is offline. Screen works when it's back."),
    not_allowed: () => status(s.owner_id ? `Only ${esc(s.owner_id)} and admins can open this computer's screen.` : "Only admins can open this computer's screen."),
    none: () => status('This bot has no computer yet.'),
    external: () => status("This bot runs outside Tico's computers, so there is no screen to show."),
    hidden: () => status("You can see this bot but not its activity, so its computer and screen aren't shown to you."),
  }[s.state]();
  return `<dt>Screen${betaPill('computer_screen')}</dt><dd class="bot-screen-row" id="bot-screen-row" data-screen-state="${s.state}" data-screen-key="${esc(botScreenKey(s))}">${body}</dd>`;
}
// What the row and the header button are drawn from; a refresh redraws them only when it changes.
const botScreenKey = s => [s.state, s.id, s.machine?.screen, s.may, s.owner_id].map(v => v ?? '').join('|');
function botScreenRefresh(slug) {
  const row = $('#bot-screen-row');
  if (row && row.dataset.screenKey !== botScreenKey(botScreenState(slug))) botScreenPaint(slug);
  botScreenSettingsPaint(slug);
  const state = botScreenState(slug);
  if (state.machine?.screen_available && SCREEN_SETUP_PENDING.has(state.id) && !SCREEN_SAVING.has(state.id)) void screenSetMode(state.id, 'control');
}
// Draws the row again, and the header's Screen button, which shows only when the row is ready.
function botScreenPaint(slug) {
  const row = $('#bot-screen-row');
  if (row) {
    row.previousElementSibling?.remove();
    row.outerHTML = botScreenRowHTML(slug);
  }
  const strip = $('#bot-tool-strip');
  if (strip && !BOT?.limited) {
    $('#bot-screen')?.remove();
    strip.insertAdjacentHTML('beforebegin', botScreenLinkHTML(slug));
    placeBotHead();
  }
  botScreenWire(slug);
}
const SCREEN_SETUP_PENDING = new Set(), SCREEN_SAVING = new Set();
function screenControlHTML(machine, online = true) {
  if (!machine) return '<span class="muted">No computer assigned.</span>';
  if (!screenMayOpen(machine)) return '<span class="muted">Only the computer’s operator and admins can change its screen.</span>';
  const id = machine.id || machine.runner_id;
  const on = !!machine.screen_available && machine.screen !== 'off' && !!machine.screen;
  const pending = SCREEN_SETUP_PENDING.has(id) && !on;
  const key = [id, machine.screen, machine.screen_available, online, pending, SCREEN_SAVING.has(id)].join('|');
  return `<div class="screen-control" data-screen-control="${esc(id)}" data-screen-control-key="${esc(key)}">
    <label class="screen-toggle">${peopleSwitch(`data-screen-toggle="${esc(id)}"`, on || pending, SCREEN_SAVING.has(id), 'Enable screen')}<span>${pending ? 'Waiting for computer setup' : on ? 'On' : 'Off'}${!online ? ' · Computer offline' : ''}</span></label>
    ${on ? `<select class="settings-inline-select" data-screen-mode="${esc(id)}" aria-label="Screen access" ${SCREEN_SAVING.has(id) ? 'disabled' : ''}><option value="view" ${machine.screen === 'view' ? 'selected' : ''}>View</option><option value="control" ${machine.screen === 'control' ? 'selected' : ''}>Control</option></select>${online ? `<a class="bot-screen-open" href="${screenHref(id)}">Open screen</a>` : ''}` : ''}
    ${pending ? `<div class="bot-screen-steps"><p>Run this on ${esc(machine.label || 'the computer')}, in its runner folder (usually <code>/opt/tico-runner</code>):</p>${screenCmdHTML(SCREEN_DOCKER_CMD, 'Docker')}
      <details><summary>Running from a checkout?</summary>${screenCmdHTML(SCREEN_CHECKOUT_CMD, 'checkout')}</details>
      <p>The screen becomes available when the computer reports it is ready. <a href="${SCREEN_DOCS}" target="_blank" rel="noopener noreferrer">Setup help</a></p></div>` : ''}
  </div>`;
}
function botScreenSettingsPaint(slug) {
  const host = $('#bot-screen-settings'); if (!host || BOT?.slug !== slug) return;
  const state = botScreenState(slug);
  const html = `<dt>Screen</dt><dd>${screenControlHTML(state.machine, !!botRow(slug)?.online)}</dd>`;
  const box = document.createElement('div'); box.innerHTML = html;
  const key = box.querySelector('[data-screen-control]')?.dataset.screenControlKey;
  if (key && host.querySelector('[data-screen-control]')?.dataset.screenControlKey === key) return;
  if (host.innerHTML !== html) host.innerHTML = html;
}
function botScreenWire(slug) { botScreenSettingsPaint(slug); }
function screenControlsPaint(id) {
  if (BOT) botScreenPaint(BOT.slug);
  const machine = (SETTINGS_DATA.machines || []).find(m => m.id === id);
  for (const box of document.querySelectorAll('[data-screen-control]')) {
    if (box.dataset.screenControl === id && !box.closest('#bot-screen-settings') && machine)
      box.outerHTML = screenControlHTML(machine, machine.online !== false);
  }
}
async function screenSetMode(id, mode) {
  if (SCREEN_SAVING.has(id)) return;
  SCREEN_SAVING.add(id); screenControlsPaint(id);
  try {
    const saved = await post(`/v2/computers/${encodeURIComponent(id)}/screen`, {mode});
    screenSaved(id, saved); SCREEN_SETUP_PENDING.delete(id);
  } catch (error) {
    SCREEN_SETUP_PENDING.delete(id); toast(error.message, true);
  } finally { SCREEN_SAVING.delete(id); screenControlsPaint(id); }
}
document.addEventListener('change', event => {
  const toggle = event.target.closest('[data-screen-toggle]'), mode = event.target.closest('[data-screen-mode]');
  if (mode) { void screenSetMode(mode.dataset.screenMode, mode.value); return; }
  if (!toggle) return;
  const id = toggle.dataset.screenToggle, machine = screenMachine(id);
  if (!machine || !screenMayOpen(machine)) return;
  if (toggle.checked && !machine.screen_available) {
    SCREEN_SETUP_PENDING.add(id); screenControlsPaint(id);
  } else if (!toggle.checked && SCREEN_SETUP_PENDING.has(id) && (!machine.screen || machine.screen === 'off')) {
    SCREEN_SETUP_PENDING.delete(id); screenControlsPaint(id);
  } else void screenSetMode(id, toggle.checked ? 'control' : 'off');
});
document.addEventListener('click', event => {
  const copy = event.target.closest('[data-screen-copy]');
  if (copy) void copyText(copy.dataset.screenCopy).then(() => toast('Copied'), () => toast('Could not copy', true));
});

// ----------------------------------------------------------------- the page
function screenStop() {
  if (!SCREEN) return;
  const state = SCREEN; SCREEN = null;
  state.gen++;
  try { state.rfb?.disconnect(); } catch {}
  removeEventListener('resize', state.onResize);
  document.body.classList.remove('screen-open');
}
async function pageComputerScreen(id, from) {
  screenStop();
  const state = SCREEN = {id, from, gen: 0, rfb: null, control: false, mode: '', zoom: 1, onResize: () => screenFit(state)};
  document.body.classList.add('screen-open');
  $('#main').innerHTML = `<div class="scr" id="scr">
    <div class="scr-bar">
      <button class="ghost scr-btn" type="button" id="scr-back" aria-label="Back" title="Back"><span class="nav-icon" aria-hidden="true">chevron_left</span></button>
      <strong class="scr-name" id="scr-name">${esc(screenMachine(id)?.label || 'Computer')}</strong>${betaPill('computer_screen')}
      <span class="scr-state" id="scr-state" role="status"></span>
      <span class="spacer"></span>
      <button class="ghost scr-btn" type="button" id="scr-fit" hidden>Fit</button>
      <button class="ghost scr-btn" type="button" id="scr-keys-btn" hidden>Keyboard</button>
      <div class="theme-seg scr-seg" id="scr-mode" role="radiogroup" aria-label="Mode" hidden>
        <button type="button" role="radio" data-scr-control="0">View</button><button type="button" role="radio" data-scr-control="1">Control</button></div>
    </div>
    <div class="scr-stage" id="scr-stage"><div class="scr-target" id="scr-target"></div>
      <div class="scr-msg" id="scr-msg" hidden><span id="scr-msg-text"></span><button class="ghost" type="button" id="scr-retry" hidden>Reconnect</button></div></div>
    <input class="scr-keys" id="scr-keys" type="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="send" aria-label="Type on the computer">
  </div>`;
  $('#scr-back').onclick = () => { location.hash = state.from && !state.from.startsWith('#/computers/') ? state.from : SETTINGS; };
  $('#scr-retry').onclick = () => void screenConnect(state, state.control);
  $('#scr-fit').onclick = () => screenZoom(state, 1);
  $('#scr-mode').onclick = event => {
    const button = event.target.closest('[data-scr-control]'); if (!button) return;
    const control = button.dataset.scrControl === '1';
    if (control !== state.control) void screenConnect(state, control);
  };
  // The soft keyboard opens only for a focus inside the tap itself.
  $('#scr-keys-btn').onclick = () => { const input = $('#scr-keys'); input.value = SCREEN_KEYS_IDLE; input.focus(); };
  screenKeys(state);
  screenGestures(state);
  addEventListener('resize', state.onResize);
  // The first session and the View | Control switch follow the computer's mode; Settings' listing has it, else one
  // read of it. At Control the person drives at once, and View stays one tap away.
  if (!screenMachine(id)?.screen && S.me?.cloud) {
    try { await settingsMachinesRead(); } catch {}
    if (SCREEN !== state) return;
  }
  const machine = screenMachine(id);
  if (machine?.label) $('#scr-name').textContent = machine.label;
  state.mode = machine?.screen || '';
  await screenConnect(state, state.mode === 'control');
}
function screenShow(state, text, retry) {
  if (SCREEN !== state) return;
  $('#scr-msg').hidden = !text;
  $('#scr-msg-text').textContent = text || '';
  $('#scr-retry').hidden = !retry;
}
function screenBar(state, live) {
  if (SCREEN !== state) return;
  $('#scr-mode').hidden = state.mode !== 'control';
  $('#scr-mode').querySelectorAll('[data-scr-control]').forEach(b => b.setAttribute('aria-checked', String((b.dataset.scrControl === '1') === state.control)));
  $('#scr-keys-btn').hidden = !(live && state.control);
  $('#scr-state').textContent = live ? (state.control ? 'Control' : 'View') : '';
  $('#scr-state').classList.toggle('scr-hide', state.mode === 'control');   // the switch says it already
  $('#scr-fit').hidden = state.zoom <= 1;
}
// Why the hub closed the socket: 4404 no runner, 4403 screen off, view only now, or computer removed; anything else
// (4401 a used ticket, 4408 the hour's limit, 1000 the other side closed) ends the session. Reconnect opens a new one.
const SCREEN_CLOSED = {4404: 'Computer did not answer', 4403: 'Screen not available'};
async function screenConnect(state, control) {
  if (SCREEN !== state) return;
  const gen = ++state.gen;
  try { state.rfb?.disconnect(); } catch {}
  state.rfb = null; state.control = control;
  screenShow(state, 'Connecting…'); screenBar(state, false);
  let session, RFB;
  try {
    let keysyms;
    [session, {default: RFB}, {default: keysyms}] = await Promise.all([
      post(`/v2/computers/${encodeURIComponent(state.id)}/screen/sessions`, {control}),
      import(SCREEN_RFB), import(SCREEN_KEYSYMDEF)]);
    SCREEN_KEYSYMS = keysyms;
  } catch (error) {
    if (gen === state.gen) screenShow(state, error.message || 'Could not open the screen', true);
    return;
  }
  if (gen !== state.gen || SCREEN !== state) return;
  state.control = !!session.control;
  const path = String(session.ws_path || '');
  if (!path.startsWith('/')) { screenShow(state, 'Could not open the screen', true); return; }
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${path}`, ['binary']);
  ws.binaryType = 'arraybuffer';
  let code = 0;
  ws.addEventListener('close', event => { code = event.code; });   // added before noVNC's own handler, so it is known first
  const target = $('#scr-target');
  target.textContent = '';
  const rfb = state.rfb = new RFB(target, ws, {shared: true});
  rfb.viewOnly = !state.control;
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.background = 'transparent';
  rfb.focusOnClick = state.control;
  rfb.addEventListener('connect', () => {
    if (gen !== state.gen) return;
    screenShow(state, ''); screenBar(state, true);
    if (state.control && matchMedia('(hover: hover)').matches) rfb.focus();
  });
  rfb.addEventListener('disconnect', () => {
    if (gen !== state.gen || SCREEN !== state) return;
    state.rfb = null;
    screenShow(state, SCREEN_CLOSED[code] || 'Session ended', true); screenBar(state, false);
  });
}

// ----------------------------------------------------------------- zoom
// noVNC scales the picture to its box (scaleViewport), so zooming makes the box bigger than the stage and the stage
// scrolls: noVNC's own pointer mapping stays right at every zoom. Pinch and two-finger drag (noVNC's gesture events,
// taken before the viewer gets them), Ctrl + wheel and trackpad pinch on a desktop; one finger pans while viewing.
function screenFit(state) { screenZoom(state, state.zoom); }
function screenZoom(state, zoom, cx, cy) {
  if (SCREEN !== state) return;
  const stage = $('#scr-stage'), target = $('#scr-target'); if (!stage || !target) return;
  const z = Math.max(1, Math.min(SCREEN_ZOOM_MAX, zoom));
  const rect = stage.getBoundingClientRect();
  const ox = (cx ?? rect.left + rect.width / 2) - rect.left, oy = (cy ?? rect.top + rect.height / 2) - rect.top;
  const fx = (stage.scrollLeft + ox) / Math.max(1, target.offsetWidth), fy = (stage.scrollTop + oy) / Math.max(1, target.offsetHeight);
  state.zoom = z;
  target.style.width = z > 1 ? `${Math.round(stage.clientWidth * z)}px` : '';
  target.style.height = z > 1 ? `${Math.round(stage.clientHeight * z)}px` : '';
  stage.scrollLeft = fx * target.offsetWidth - ox;
  stage.scrollTop = fy * target.offsetHeight - oy;
  $('#scr-fit').hidden = z <= 1;
}
function screenGestures(state) {
  const stage = $('#scr-stage');
  let pinch = null, drag = null;
  const take = event => { event.stopPropagation(); event.preventDefault?.(); };
  const onGesture = event => {
    const d = event.detail || {};
    if (d.type === 'pinch') {
      take(event);
      const dist = Math.hypot(d.magnitudeX, d.magnitudeY);
      if (event.type === 'gesturestart') pinch = {dist: Math.max(1, dist), zoom: state.zoom, x: d.clientX, y: d.clientY};
      else if (pinch && event.type === 'gesturemove') screenZoom(state, pinch.zoom * dist / pinch.dist, pinch.x, pinch.y);
      if (event.type === 'gestureend') pinch = null;
    } else if (d.type === 'twodrag' || (d.type === 'drag' && !state.control)) {
      take(event);
      // twodrag reports movement since its start; drag only a position, so it is tracked here.
      if (event.type === 'gesturestart') drag = {x: d.clientX, y: d.clientY, left: stage.scrollLeft, top: stage.scrollTop, mx: 0, my: 0};
      else if (drag && event.type === 'gesturemove') {
        const mx = d.type === 'twodrag' ? d.magnitudeX : d.clientX - drag.x, my = d.type === 'twodrag' ? d.magnitudeY : d.clientY - drag.y;
        stage.scrollLeft = drag.left - mx; stage.scrollTop = drag.top - my;
      }
      if (event.type === 'gestureend') drag = null;
    }
  };
  for (const type of ['gesturestart', 'gesturemove', 'gestureend']) stage.addEventListener(type, onGesture, true);
  stage.addEventListener('wheel', event => {
    if (!event.ctrlKey) return;
    take(event);
    screenZoom(state, state.zoom * Math.exp(-event.deltaY / 200), event.clientX, event.clientY);
  }, {capture: true, passive: false});
}

// ----------------------------------------------------------------- keyboard
// A phone types into a hidden input; each change is sent as key presses. The input keeps two characters, so a
// backspace on a soft keyboard that sends no key code still shows as a shorter value.
const SCREEN_KEYS_IDLE = '  ';
const SCREEN_NAMED = {Enter: 0xff0d, Backspace: 0xff08, Tab: 0xff09, Escape: 0xff1b, Delete: 0xffff, ArrowLeft: 0xff51,
                      ArrowUp: 0xff52, ArrowRight: 0xff53, ArrowDown: 0xff54, Home: 0xff50, End: 0xff57};
let SCREEN_KEYSYMS = null;
function screenKeys(state) {
  const input = $('#scr-keys');
  const press = (keysym, code) => state.rfb?.sendKey(keysym, code);
  const reset = () => { input.value = SCREEN_KEYS_IDLE; input.setSelectionRange(2, 2); };
  input.addEventListener('keydown', event => {
    const keysym = SCREEN_NAMED[event.key];
    if (!keysym || !state.control) return;
    event.preventDefault(); press(keysym, event.code || event.key); reset();
  });
  input.addEventListener('input', () => {
    const value = input.value;
    if (!state.control || !state.rfb) { reset(); return; }
    if (value.length < SCREEN_KEYS_IDLE.length) for (let i = value.length; i < SCREEN_KEYS_IDLE.length; i++) press(0xff08, 'Backspace');
    else for (const ch of value.slice(SCREEN_KEYS_IDLE.length)) {
      if (ch === '\n') press(0xff0d, 'Enter');
      else if (SCREEN_KEYSYMS) press(SCREEN_KEYSYMS.lookup(ch.codePointAt(0)), null);
    }
    reset();
  });
  input.addEventListener('focus', reset);
}
