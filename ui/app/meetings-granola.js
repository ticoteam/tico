/* ui/app/meetings-granola.js — The viewer's own Granola account on the Meetings page (docs/meetings.md, Granola)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Granola's row lives in Sources (ui/app/meetings-sources.js). Connecting is a device-code sign-in in
// a dialog: the server hands out a short code, the person enters it in Granola, and this page polls
// GET .../connect/status every `interval` seconds until it connects, expires or is denied. When the page opens and the
// account is connected it asks for one sync (fire and forget) and refreshes the list once the sync lands. The API-key
// importer (Business/Enterprise) is a link in the dialog, for the owner.
// state.granola is the last GET /v2/meetings/granola; null when the server has no such route, and then the
// Granola tile keeps opening the API-key importer as before.
const GRANOLA = '/v2/meetings/granola';
const GRANOLA_POLL_FIRST_MS = 3000, GRANOLA_POLL_MAX_MS = 15000, GRANOLA_LEGACY_POLLS = 5;

function granolaStop(state) {
  if (!state) return;
  if (state.gflow) { state.gflow = null; $('#mg-dialog')?.remove(); }
  clearTimeout(state.gTimer); clearTimeout(state.gSyncTimer);
  state.gTimer = state.gSyncTimer = 0;
}
// A sentence for screen readers; the Sources row re-renders, so announcements go through one steady live region.
function granolaSay(text) {
  const live = $('#mg-live'); if (!live) return;
  live.textContent = '';
  setTimeout(() => { live.textContent = text; }, 30);
}
async function granolaStatus(state) {
  let s;
  try { s = await get(GRANOLA); } catch { s = null; }
  if (MEET !== state) return null;
  state.granola = s && typeof s.mode === 'string' ? s : null;
  granolaPaint(state);
  meetPaintTiles(state);
  return state.granola;
}
async function granolaInit(state) {
  const s = await granolaStatus(state);
  // Tools > Granola links here with ?connect=granola: open the sign-in straight away.
  if (state.connectGranola && s && !granolaReady(s)) {
    state.connectGranola = false;
    // Signed out: Sources shows why first, with Sign in again beside it.
    return s.needs_signin ? meetSourcesOpen(state) : granolaConnect(state);
  }
  if (s?.syncing) granolaWatch(state);           // a sync already running (the schedule, another tab): follow it
  else if (granolaReady(s)) granolaSync(state);
}
// Signed out is `needs_signin`, whatever `connected` says (the server sends connected:false then).
const granolaReady = s => !!(s?.connected && !s.needs_signin);

// Ask for one sync, then follow it.
async function granolaSync(state, now = false) {
  if (state.gSyncing) return;
  state.gSyncing = true; state.gmsg = '';
  granolaPaint(state);
  let r;
  try { r = await post(GRANOLA + '/sync' + (now ? '?now=true' : ''), {}); }
  catch (e) {
    if (MEET !== state) return;
    state.gSyncing = false; state.gmsg = e.message;
    granolaPaint(state);
    return;
  }
  if (MEET !== state) return;
  // "recent", "off" and "needs_signin" start nothing: say so now instead of showing a sync that is not running.
  if (r?.state && r.state !== 'syncing') { state.gSyncing = false; granolaStatus(state); return; }
  granolaWatch(state, true);
}
// Re-read the status while the server says `syncing`: every 3 s, backing off to 15 s, until it stops (or the page
// closes: granolaStop clears the timer). Then reload the meeting list if anything came in. A server that does not
// send `syncing` is followed a few times until last_synced_at moves.
function granolaWatch(state, asked) {
  clearTimeout(state.gSyncTimer);
  const before = {last: state.granola?.last_synced_at || '', count: Number(state.granola?.imported_count) || 0};
  state.gSyncing = true;
  granolaPaint(state);
  let wait = GRANOLA_POLL_FIRST_MS, tries = 0;
  const done = changed => {
    state.gSyncing = false; state.gSyncTimer = 0;
    granolaPaint(state); meetPaintTiles(state);
    if (changed && !state.editing && !state.open) meetLoad(state, false);
  };
  const check = async () => {
    state.gSyncTimer = 0;
    const now = await granolaStatus(state);
    if (MEET !== state) return;
    tries++;
    const changed = !!now && ((now.last_synced_at || '') !== before.last || (Number(now.imported_count) || 0) !== before.count);
    if (!now || !granolaReady(now)) return done(changed);
    if (typeof now.syncing === 'boolean') {
      if (!now.syncing && (tries > 1 || !asked || changed)) return done(changed);
    } else if (changed || tries >= GRANOLA_LEGACY_POLLS) return done(changed);
    state.gSyncing = true;
    granolaPaint(state);
    wait = Math.min(GRANOLA_POLL_MAX_MS, Math.round(wait * 1.5));
    state.gSyncTimer = setTimeout(check, wait);
  };
  state.gSyncTimer = setTimeout(check, wait);
}

async function granolaConnect(state) {
  granolaStop(state);
  state.gmsg = ''; state.gflow = {phase: 'starting'};
  granolaPaint(state);
  let r;
  try { r = await post(GRANOLA + '/connect', {}); }
  catch (e) { if (MEET !== state) return; state.gflow = null; state.gmsg = e.message; granolaPaint(state, 'connect'); return; }
  if (MEET !== state) return;
  if (!r?.user_code) { state.gflow = null; state.gmsg = 'Granola did not send a code. Try again.'; granolaPaint(state, 'connect'); return; }
  const every = Math.max(0.1, Number(r.interval) || 5) * 1000;
  state.gflow = {phase: 'code', code: String(r.user_code), uri: granolaSafeUri(r.verification_uri_complete) || granolaSafeUri(r.verification_uri),
    host: granolaHost(r.verification_uri) || granolaHost(r.verification_uri_complete),
    until: Date.now() + Math.max(1, Number(r.expires_in) || 600) * 1000, every};
  granolaPaint(state, 'open');
  granolaSay(`Your code is ${r.user_code.split('').join(' ')}. Enter it in Granola.`);
  state.gTimer = setTimeout(() => granolaPoll(state), every);
}
// Only a https link on Granola's own host becomes "Open Granola"; anything else is shown as plain host text.
function granolaSafeUri(value) {
  try {
    const u = new URL(String(value));
    return u.protocol === 'https:' && (u.hostname === 'granola.ai' || u.hostname.endsWith('.granola.ai')) ? u.href : '';
  } catch { return ''; }
}
function granolaHost(value) { try { const u = new URL(String(value)); return /^https?:$/.test(u.protocol) ? u.host : ''; } catch { return ''; } }
async function granolaPoll(state) {
  const flow = state.gflow;
  if (MEET !== state || flow?.phase !== 'code') return;
  let r;
  try { r = await get(GRANOLA + '/connect/status'); } catch { r = {state: 'pending'}; }
  if (MEET !== state || state.gflow !== flow) return;
  if (r.state === 'connected') {
    state.gflow = null;
    if (state.granola) Object.assign(state.granola, {connected: true, needs_signin: false, email: r.email || state.granola.email, mode: 'account'});
    granolaPaint(state, 'more');
    granolaSay('Granola connected.');
    await granolaStatus(state);
    if (MEET === state && granolaReady(state.granola)) granolaSync(state);
    return;
  }
  if (r.state === 'needs_signin') {
    state.gflow = null;
    state.gmsg = 'Granola sign-in failed. Try again.';
    if (state.granola) state.granola.needs_signin = true;
    granolaPaint(state, 'connect'); meetPaintTiles(state);
    return;
  }
  if (r.state === 'denied' || r.state === 'expired' || Date.now() > flow.until) {
    state.gflow = null;
    state.gmsg = r.state === 'denied' ? 'Granola access was denied.' : 'The code expired. Try again.';
    granolaPaint(state, 'connect');
    return;
  }
  state.gTimer = setTimeout(() => granolaPoll(state), flow.every);
}
function granolaCancel(state) {
  granolaStop(state);
  state.gflow = null; state.gmsg = '';
  granolaPaint(state, 'connect');
}
async function granolaDisconnect(state) {
  granolaStop(state);
  state.gSyncing = false; state.gmsg = '';
  try { await writeRequest('DELETE', GRANOLA + '/connect'); }
  catch (e) { if (MEET === state) { state.gmsg = e.message; granolaPaint(state, 'more'); } return; }
  if (MEET !== state) return;
  if (state.granola) Object.assign(state.granola, {connected: false, needs_signin: false, syncing: false, email: '', mode: 'off'});
  granolaPaint(state, 'connect');
  granolaSay('Granola disconnected.');
  granolaStatus(state);
}
function granolaTileState(state) {
  const s = state.granola;
  if (!s) return null;
  if (s.needs_signin) return {key: 'error', word: 'Sign in', when: ''};
  if (s.connected) return {key: 'on', word: 'Connected', when: s.last_synced_at || ''};
  return null;
}

function granolaFacts(s, syncing) {
  const n = Number(s.imported_count) || 0, skipped = Math.max(0, Number(s.skipped) || 0);
  const free = s.plan_hint === 'free' ? ' title="Free plan: notes from the last 30 days"' : '';
  return [s.email ? `<span class="mg-email" title="${esc(s.email)}">${esc(s.email)}</span>` : '',
    syncing ? '<span class="mg-syncing"><i class="mg-spin" aria-hidden="true"></i>Syncing…</span>'
      : `<span>${s.last_synced_at ? 'Synced ' + esc(ago(s.last_synced_at)) : 'Not synced yet'}</span>`,
    `<span class="mg-count"${free}>${n} ${n === 1 ? 'note' : 'notes'}</span>`,
    skipped ? `<span class="mg-skip" title="${skipped} ${skipped === 1 ? 'note' : 'notes'} from Granola could not be imported">${skipped} skipped</span>` : ''].filter(Boolean).join('');
}
// The Sources row says how the account is doing; signing in happens in a dialog (granolaDialog) on top of it.
function granolaPaint(state, focus) {
  const busy = $('#meet-syncing'); if (busy) busy.hidden = !(state.gSyncing && granolaReady(state.granola));
  meetSourcesDot(state);
  // The sign-in dialog first: Sources under it can take focus only once it has closed.
  granolaDialog(state, focus);
  // A sign-in that failed says why on Granola's row, so Sources opens if it is not open yet.
  if (focus === 'connect' && state.gmsg && !state.gflow && !$('#meet-src-dialog')) meetSourcesOpen(state, focus);
  else meetSourcesPaint(state, focus);
}
// Connect Granola: three short steps around the code, open while a code is being fetched or waited on.
function granolaDialog(state, focus) {
  const flow = state.gflow;
  let dialog = $('#mg-dialog');
  if (!flow) { dialog?.remove(); return; }
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'tmodal mg-dialog'; dialog.id = 'mg-dialog'; dialog.setAttribute('aria-label', 'Connect Granola');
    dialog.oncancel = e => { e.preventDefault(); granolaCancel(state); };
    dialog.onclick = e => { if (e.target === dialog) granolaCancel(state); };
    document.body.append(dialog);
  }
  const code = flow.phase === 'code';
  const open = flow.uri ? `<a class="primary small mg-open" href="${esc(flow.uri)}" target="_blank" rel="noopener" data-g="open">Open Granola</a>`
    : flow.host ? `<span class="mg-host">${esc(flow.host)}</span>` : '';
  const key = S.me?.role === 'owner' ? '<button type="button" class="linkish mg-key" data-g="key">Use an API key</button>' : '';
  dialog.innerHTML = `<header class="mg-dhead"><h2>Connect Granola</h2><button type="button" class="ghost" data-g="cancel" aria-label="Close">✕</button></header>
    ${code ? `<ol class="mg-steps">
      <li><span>Open Granola and sign in</span>${open}</li>
      <li><span>Enter this code if asked</span><span class="mg-code-row"><output class="mg-code" id="mg-code" aria-label="Granola code">${esc(flow.code)}</output><button type="button" class="ghost small" data-g="copy" aria-label="Copy code">Copy</button></span></li>
      <li><span>Allow access</span></li></ol>
      <p class="mg-wait muted"><i class="mg-spin" aria-hidden="true"></i>Waiting for Granola</p>`
      : '<p class="mg-wait muted"><i class="mg-spin" aria-hidden="true"></i>Getting a code…</p>'}
    <dl class="mg-facts"><dt>Sync</dt><dd>Every 25 min and when you open Meetings</dd><dt>New notes</dt><dd>Land in Pending, only you see them</dd><dt>Free plan</dt><dd>Summaries from the last 30 days</dd></dl>
    <footer class="mg-dfoot">${key}<span class="spacer"></span><button type="button" class="ghost" data-g="cancel">Cancel</button></footer>`;
  granolaWire(state, dialog);
  if (!dialog.open) dialog.showModal();
  if (focus === 'open') (dialog.querySelector('[data-g=open]') || dialog.querySelector('[data-g=copy]'))?.focus();
}
function granolaWire(state, row) {
  row.querySelectorAll('[data-g]').forEach(b => {
    const act = b.dataset.g;
    if (act === 'open') return;
    b.onclick = () => {
      if (act === 'connect') granolaConnect(state);
      else if (act === 'cancel') granolaCancel(state);
      else if (act === 'copy') void copyText(state.gflow?.code || '').then(() => toast('Code copied'), () => toast('Could not copy', true));
      else if (act === 'key') { granolaCancel(state); window.openMeetingImporter?.('granola', 'Granola', () => meetSources(state)); }
    };
  });
}

// Tools > Granola: the viewer's own account, any plan (free included). Connecting happens on Meetings, where the
// notes land, so the button links there with the sign-in open.
window.mountGranolaTool = async function (host) {
  if (!host) return;
  let s;
  try { s = await get(GRANOLA); } catch { s = null; }
  if (!host.isConnected) return;
  if (!s || typeof s.mode !== 'string') { host.closest('section')?.remove(); return; }
  const go = MEETINGS + '?connect=granola';
  const line = s.needs_signin ? '<span class="mg-bad">Signed out</span>'
    : s.connected ? granolaFacts(s, s.syncing) : '<span>Any Granola plan, free included</span>';
  const [word, cls, to] = s.needs_signin ? ['Sign in again', 'primary', go] : s.connected ? ['Open Meetings', 'ghost', MEETINGS] : ['Connect', 'primary', go];
  host.innerHTML = `<div class="mg-row mg-flat">${meetLogo('granola', 22)}<div class="mg-body"><div class="mg-line">${line}</div></div>
    <div class="mg-actions"><button type="button" class="${cls} small">${word}</button></div></div>`;
  host.querySelector('button').onclick = () => { location.hash = to; };
};
