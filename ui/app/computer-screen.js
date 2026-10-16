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
const screenMayOpen = machine => !!machine && !machine.revoked_at && (settingsIsAdmin() || (!!S.me?.id && machine.operator === S.me.id));
const screenOn = machine => !!machine && machine.screen && machine.screen !== 'off' && machine.screen_available !== false;
// A computer's row from what is already loaded: Settings' operations listing, or the bot rows' own computer.
function screenMachine(id) {
  if (!id) return null;
  const row = (SETTINGS_DATA.machines || []).find(m => m.id === id);
  const bot = (S.emps || []).find(e => e.machine?.runner_id === id)?.machine;
  if (!row && !bot) return null;
  return {id, label: row?.label || bot?.label, operator: row?.operator ?? bot?.operator, revoked_at: row?.revoked_at,
          screen: row?.screen ?? bot?.screen, screen_available: row?.screen_available ?? bot?.screen_available};
}
// The bot's computer when its screen is on and this person may open it; '' otherwise.
function botScreenComputer(slug) {
  const id = (S.emps || []).find(e => e.name === slug)?.machine?.runner_id;
  const machine = screenMachine(id);
  return screenOn(machine) && screenMayOpen(machine) ? id : '';
}
const screenHref = id => `#/computers/${encodeURIComponent(id)}/screen`;
const botScreenLinkHTML = slug => {
  const id = botScreenComputer(slug);
  return id ? `<a class="bot-learn bot-screen" id="bot-screen" href="${screenHref(id)}" aria-label="Screen" title="Screen"><span class="nav-icon" aria-hidden="true">computer</span><span class="bl-label">Screen</span></a>` : '';
};
// A task's owner bot's screen, beside the owner in the task's head.
const taskScreenLinkHTML = owner => {
  const id = botScreenComputer(actorSlug(owner));
  return id ? `<a class="ghost tmodal-act" href="${screenHref(id)}">Screen</a>` : '';
};

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
  // The View | Control switch needs the computer's mode; Settings' listing has it, else one read of it.
  if (!screenMachine(id)?.screen && S.me?.cloud) {
    try { SETTINGS_DATA.machines = (await get('/v2/operations')).machines || SETTINGS_DATA.machines; } catch {}
    if (SCREEN !== state) return;
  }
  const machine = screenMachine(id);
  if (machine?.label) $('#scr-name').textContent = machine.label;
  state.mode = machine?.screen || '';
  await screenConnect(state, false);
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
