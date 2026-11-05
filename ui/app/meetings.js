/* ui/app/meetings.js — The Meetings page: list, a meeting (tasks, notes, transcript), Add notes
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- meetings page (docs/meetings.md)
// Meeting transcripts and notes imported from other tools: a searchable table, a detail dialog
// with its tasks, notes and transcript, and an Import action.
let MEET = null;
function meetStop() {
  if (MEET) {
    clearInterval(MEET.poll); clearInterval(MEET.sourcesPoll);
    meetSharedStop(MEET);
    granolaStop(MEET);   // ui/app/meetings-granola.js
    $('#meet-src-dialog')?.remove();
    MEET = null;
  }
  window.liveMeetingsStop?.();
}
async function pageNotes() {
  meetStop();
  const linked = new URLSearchParams(S.route.split('?')[1] || '');
  if (linked.get('live')) return pageLiveMeetings(linked.get('live'));
  // New imports wait in Pending, so the page opens there when it has any; an empty Pending falls back to Shared.
  const state = MEET = {connectGranola: linked.get('connect') === 'granola', review: linked.get('meeting') || linked.get('recording') ? 'live' : 'pending', landing: true, checked: new Set(), pendingCount: 0, loadSeq: 0, filter: 'all', when: 'all', person: '', source: '', people: new Map(), selected: '', list: [], poll: 0,
    sourcesPoll: 0, items: {}, itemAdd: '', confirmDelete: '', open: false};
  $('#main').innerHTML = `<div class="notes-head"><h1>Meetings</h1>
      <input type="search" id="notes-search" autocomplete="off" aria-label="Search meetings" placeholder="Search meetings">
      <div class="notes-head-actions"><button class="docs-ask" id="meet-ask" type="button" data-ask-open aria-label="Ask" title="Ask">${ldotHTML({state: 'still', size: 16})}<span class="docs-ask-word">Ask</span></button><button class="ghost" type="button" id="meet-live-now">Live now${betaPill('live_meetings')}</button>
      <button class="primary" type="button" id="notes-manual">Add notes</button></div></div>
    <div class="meet-review-bar"><div class="meet-review-tabs" role="tablist" aria-label="Meeting views">
      <button type="button" role="tab" data-review="live" aria-selected="false">Shared</button>
      <button type="button" role="tab" data-review="pending" aria-selected="false">Pending <span class="meet-pending-n" id="meet-pending-count"></span></button>
      <button type="button" role="tab" data-review="dismissed" aria-selected="false">Dismissed</button></div>
      <div class="meet-sources" id="meet-sources" hidden></div>
      <a class="linkish" id="meet-recorder" href="${MEET_RECORDER_DMG}" aria-label="Get Tico Recorder for Mac" title="For Mac">Get Tico Recorder</a>
      <button class="linkish" type="button" id="meet-settings">Settings</button></div>
    <p class="sr-only" id="mg-live" aria-live="polite"></p>
    <div class="meet-review-help muted" id="meet-review-help" hidden></div>
    <div class="meet-review-bulk" id="meet-review-bulk" hidden></div>
    <div class="notes" id="notes">
      <div class="meet-syncing" id="meet-syncing" role="status" hidden>${meetLogo('granola', 30)}<div class="meet-main"><b>Checking Granola</b><div class="meet-meta"><span>New notes arrive once Granola has a summary</span></div></div><div class="meet-side"><span class="meet-state"><i class="mg-spin" aria-hidden="true"></i>Syncing</span></div></div>
      <section class="notes-list"><div class="notes-filters" id="notes-filters" hidden><label class="meet-select-all" id="meet-select-all" hidden><input type="checkbox" aria-label="Select all shown meetings"></label><select id="notes-when" aria-label="When"><option value="all">Any time</option><option value="today">Today</option><option value="7">Past 7 days</option><option value="30">Past 30 days</option><option value="older">Older than 30 days</option></select><select id="notes-person" aria-label="Participant"><option value="">All participants</option></select><select id="notes-source" aria-label="Source"><option value="">All sources</option></select><select id="notes-status" aria-label="Status"><option value="all">All statuses</option><option value="unsent">Not sent</option><option value="sent">Sent</option><option value="sending">Sending</option><option value="failed">Failed</option></select><span class="notes-count muted" id="notes-count"></span></div><div class="notes-rows" id="notes-rows"><div class="notes-empty">Loading…</div></div></section>
    </div>
    <dialog class="tmodal notes-modal" id="notes-modal" aria-label="Meeting details"><div class="notes-modal-close"><span class="mp-tools" id="notes-modal-tools"></span><button class="mp-icon" type="button" id="notes-modal-window" aria-label="Open in new tab" title="Open in new tab"><span class="nav-icon" aria-hidden="true">open_in_new</span></button><button class="mp-icon" type="button" id="notes-modal-close" aria-label="Close meeting" title="Close (Esc)"><span class="nav-icon" aria-hidden="true">close</span></button></div><section class="notes-detail" id="notes-detail"></section></dialog>
    <dialog class="tmodal import-modal" id="manual-modal" aria-label="Add notes"></dialog>`;
  document.querySelectorAll('[data-review]').forEach(button => button.onclick = () => {
    if (state.review === button.dataset.review) return;
    state.review = button.dataset.review; state.landing = false; state.checked.clear(); state.selected = ''; state.people.clear();
    state.person = ''; state.source = ''; state.filter = 'all'; state.when = 'all';
    $('#notes-rows').innerHTML = '<div class="notes-empty">Loading…</div>';
    $('#meet-review-bulk').hidden = true; meetReviewTabs(state); meetLoad(state, true);
  });
  $('#meet-settings').onclick = () => meetSettingsOpen(state);
  meetRecorderLink();
  $('#meet-live-now').onclick = () => pageLiveMeetings();
  const dialog = $('#notes-modal');
  const closeNote = () => meetShow(state, false);
  $('#notes-modal-close').onclick = closeNote;
  // A meeting opens here; its own window is a choice, because a window that fails to open would look like a dead click.
  const own = $('#notes-modal-window');
  own.hidden = MEET_WINDOW;
  own.onclick = () => { if (meetWindow(state.selected)) meetShow(state, false); };
  // Escape and a click elsewhere close an open menu first.
  dialog.oncancel = e => {
    e.preventDefault();
    const menu = dialog.querySelector('details.mp-pop[open]');
    if (menu) { menu.open = false; menu.querySelector('summary').focus(); } else closeNote();
  };
  dialog.onclick = e => {
    dialog.querySelectorAll('details.mp-pop[open]').forEach(menu => { if (!menu.contains(e.target)) menu.open = false; });
    if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeNote(); } };
  $('#notes-manual').onclick = () => meetNotesOpen(state);
  const applyFilters = () => {
    state.person = $('#notes-person').value; state.source = $('#notes-source').value; state.filter = $('#notes-status').value; state.when = $('#notes-when').value;
    state.selected = meetVisible(state)[0]?.id || ''; meetList(state);
  };
  $('#notes-person').onchange = applyFilters;
  $('#notes-source').onchange = applyFilters;
  $('#notes-status').onchange = applyFilters;
  $('#notes-when').onchange = applyFilters;
  let searchTimer;
  $('#notes-search').oninput = e => {
    state.query = e.target.value.trim(); state.selected = '';
    clearTimeout(searchTimer); searchTimer = setTimeout(() => meetLoad(state, true), 250);
  };
  meetSources(state);
  meetReviewTabs(state);
  granolaInit(state);   // the viewer's own Granola account: status, then one sync (ui/app/meetings-granola.js)
  await meetLoad(state, true);
  if (MEET !== state) return;
  if (state.landing && state.review === 'pending' && !state.pendingCount) {
    state.review = 'live'; meetReviewTabs(state); await meetLoad(state, true);
    if (MEET !== state) return;
  }
  state.landing = false;
  const open = linked.get('meeting') || linked.get('recording');
  if (open) { state.selected = open; meetShow(state, true); meetDetail(state, open); }
  state.poll = setInterval(() => { if (!document.hidden && !state.open) meetLoad(state, false); }, 30000);
  state.sourcesPoll = setInterval(() => { if (!document.hidden) meetSources(state); }, 60000);
}

// Review stays personal until approval; counts are quiet and never enter Needs you.
function meetPendingBadge(count) {
  for (const el of document.querySelectorAll('[data-meet-pending]')) {
    el.hidden = !count; countSet(el, count > 99 ? '99+' : String(count || ''));   // a new number ticks in (ui/app/motion.js)
    el.setAttribute('aria-label', `${count} pending meetings`);
  }
}
function meetReviewTabs(state) {
  document.querySelectorAll('[data-review]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.review === state.review)));
  const count = $('#meet-pending-count'); if (count) count.textContent = state.pendingCount || '';
  $('[data-review=pending]')?.classList.toggle('has-pending', !!state.pendingCount);
  const help = $('#meet-review-help');
  if (help) { help.hidden = state.review === 'live'; help.textContent = state.review === 'pending'
    ? 'Only you see these until you share them.' : 'Only you see dismissed meetings.'; }
}
// Bulk actions appear only once two or more shown rows are ticked; a hidden row is never part of them.
function meetReviewBulk(state) {
  const el = $('#meet-review-bulk'); if (!el) return;
  const visible = meetVisible(state), selected = visible.filter(r => state.checked.has(r.id)).length;
  const all = $('#meet-select-all');
  if (all) {
    all.hidden = state.review !== 'pending' || visible.length < 2;
    const box = all.querySelector('input');
    box.checked = visible.length > 0 && visible.every(r => state.checked.has(r.id));
    box.onchange = () => { for (const r of visible) box.checked ? state.checked.add(r.id) : state.checked.delete(r.id); meetList(state); };
  }
  el.hidden = state.review !== 'pending' || selected < 2;
  el.innerHTML = `<span>${selected} selected</span><span class="spacer"></span>
    <button class="primary small" type="button" data-review-bulk="approve_all">Share ${selected}</button>
    <button class="ghost small" type="button" data-review-bulk="dismiss_all">Dismiss ${selected}</button>
    <button class="linkish" type="button" data-review-clear>Clear</button>`;
  el.querySelectorAll('[data-review-bulk]').forEach(b => b.onclick = () => meetReviewAct(state, b, b.dataset.reviewBulk));
  el.querySelector('[data-review-clear]').onclick = () => { state.checked.clear(); meetList(state); };
}
function meetReviewWire(state, host) {
  host.querySelectorAll('[data-review-action]').forEach(b => b.onclick = () => meetReviewAct(state, b, b.dataset.reviewAction, b.dataset.id));
  host.querySelectorAll('[data-review-check]').forEach(b => b.onchange = () => {
    b.checked ? state.checked.add(b.dataset.reviewCheck) : state.checked.delete(b.dataset.reviewCheck); meetReviewBulk(state);
  });
}
async function meetReviewAct(state, button, action, id) {
  if (state.reviewBusy) return;
  const visible = id ? [] : meetVisible(state).map(r => r.id);
  const selected = visible.filter(id => state.checked.has(id));
  if (!id && !selected.length) return;
  state.reviewBusy = true; button.disabled = true;
  const body = {action};
  if (!id) body.ids = selected;
  const detail = id && button.closest('#notes-detail');
  const privacy = detail?.querySelector('#meet-review-private');
  if (action === 'approve' && privacy) body.private = privacy.value === 'private';
  // Share & send hands the meeting to a bot once it is shared; the bot files the tasks it finds.
  const dest = detail ? detail.querySelector('#meet-review-send')?.value : button.dataset.send;
  if (action === 'approve' && dest) body.send_to = dest;
  try {
    await post(id ? `/v2/meetings/${encodeURIComponent(id)}/review` : '/v2/meetings/review', body);
    if (MEET !== state) return;
    state.checked.clear();
    if (state.open && (!id || state.selected === id)) meetShow(state, false);
    await meetLoad(state, true);
    toast(body.send_to ? 'Shared and sent' : action.startsWith('approve') ? 'Shared' : action === 'restore' ? 'Restored to Pending' : 'Dismissed');
  } catch (e) { toast(e.message, true); }
  finally { state.reviewBusy = false; button.disabled = false; }
}
function meetReviewDetail(rec) {
  const sendTo = `<option value="auto">Send to ${esc(assistantName())}</option>` + activeEmps().filter(e => e.id !== 'coo').map(e => `<option value="${esc(e.id)}">Send to ${esc(e.display_name)}</option>`).join('') + '<option value="">Don\'t send</option>';
  return `<div class="meet-review-detail"><span class="meet-review-tag">${rec.review_state === 'pending' ? 'Pending' : 'Dismissed'}</span><span class="muted meet-review-who">Only you see this</span><span class="spacer"></span>
    ${rec.review_state === 'pending' ? `<select id="meet-review-private" aria-label="Who sees it once shared"><option value="team"${!rec.private ? ' selected' : ''}>Team</option><option value="private"${rec.private ? ' selected' : ''}>Private</option></select>
    <select id="meet-review-send" aria-label="Bot that files its tasks">${sendTo}</select>
    <button class="primary" type="button" data-review-action="approve" data-id="${esc(rec.id)}" id="meet-review-go">Share & send</button><button class="ghost" type="button" data-review-action="dismiss" data-id="${esc(rec.id)}">Dismiss</button>`
    : `<button class="primary" type="button" data-review-action="restore" data-id="${esc(rec.id)}">Restore to Pending</button>`}</div>`;
}
async function meetSettingsOpen(state) {
  const dialog = document.createElement('dialog'); dialog.className = 'tmodal meet-settings-modal'; dialog.setAttribute('aria-label', 'Meeting settings');
  dialog.innerHTML = '<div class="empty">Loading…</div>'; document.body.append(dialog); dialog.showModal();
  dialog.addEventListener('close', () => dialog.remove());
  try {
    const settings = await get('/v2/meetings/settings'); if (!dialog.isConnected) return;
    const personal = settings.auto_share === null ? 'default' : settings.auto_share ? 'auto' : 'review';
    dialog.innerHTML = `<form id="meet-settings-form"><h2>Meeting settings</h2>
      <label>Your imports<select name="personal" aria-label="Your imports"><option value="default">Use Team default (${settings.review_default === 'auto' ? 'auto-share' : 'review'})</option><option value="review">Review before sharing</option><option value="auto">Auto-share</option></select></label>
      <p class="hint">Auto-share applies to new imports. Existing Pending meetings stay pending.</p>
      ${S.me?.role === 'owner' ? '<label>Team default<select name="team" aria-label="Team default"><option value="review">Review before sharing</option><option value="auto">Auto-share</option></select></label><p class="hint">Each person can choose their own setting.</p>' : ''}
      <div class="row"><button class="primary" type="submit">Save</button><button class="ghost" type="button" data-close>Close</button><span class="err" role="status" id="meet-settings-error"></span></div></form>`;
    const form = dialog.querySelector('form'); form.elements.personal.value = personal;
    if (form.elements.team) form.elements.team.value = settings.review_default;
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    form.onsubmit = async e => {
      e.preventDefault(); const submit = form.querySelector('[type=submit]'); submit.disabled = true;
      const body = {auto_share: form.elements.personal.value === 'default' ? null : form.elements.personal.value === 'auto'};
      if (form.elements.team) body.review_default = form.elements.team.value;
      try { await post('/v2/meetings/settings', body); dialog.close(); toast('Saved'); }
      catch (error) { $('#meet-settings-error').textContent = error.message; submit.disabled = false; }
    };
  } catch (e) { dialog.close(); toast(e.message, true); }
}

// The meeting sources Tico takes from, in the order they are offered. `logo` is a key in ui/tool-icons.js;
// a source without one is drawn as its first two letters. Close is set up in its tool doc, the
// rest in a dialog (ui/meeting-importers.js).
// Tico Recorder for Mac: the newest release's disk image.
const MEET_RECORDER_DMG = 'https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg';
// In the Mac app with Tico Recorder installed, Record opens the Recorder on this org instead (app/src/recorder.rs).
// A browser can't tell, so it keeps the download.
async function meetRecorderLink() {
  if (!nativeHas('openRecorder')) return;
  let installed = false;
  try { installed = await nativeHandler('hasRecorder')?.postMessage(null) === true; } catch {}
  const link = $('#meet-recorder');
  if (!installed || !link) return;
  const button = document.createElement('button');
  Object.assign(button, {type: 'button', className: 'linkish', id: 'meet-recorder', textContent: 'Record'});
  button.onclick = () => { try { Promise.resolve(nativeHandler('openRecorder')?.postMessage(null)).catch(() => {}); } catch {} };
  link.replaceWith(button);
}
const MEET_SOURCES = [
  {id: 'granola', name: 'Granola'},
  {id: 'zoom', name: 'Zoom', logo: 'zoom'},
  {id: 'google-meet', name: 'Google Meet', logo: 'google'},
  {id: 'close', name: 'Close'},
];
const MEET_GLYPHS = {
  manual: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
  upload: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5',
};
MEET_GLYPHS.tico = MEET_GLYPHS.manual;
for (const k of ['api', 'submitted', 'other']) MEET_GLYPHS[k] = MEET_GLYPHS.upload;
// The round mark for a source: its logo, a pencil or page for notes and uploads, else two letters.
function meetLogo(id, size) {
  const known = MEET_SOURCES.find(s => s.id === id), name = known?.name || meetSourceLabel(id);
  const glyph = MEET_GLYPHS[id || 'tico'];
  const logo = known?.logo && window.toolIcons?.has(known.logo);
  const inner = glyph ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="${glyph}"/></svg>`
    : window.toolIcons ? window.toolIcons.markup({logo_key: known?.logo, name})
    : `<span class="tool-initials">${esc(name.slice(0, 2))}</span>`;
  const hue = window.toolIcons ? window.toolIcons.hue(name) : 0;
  return `<span class="msrc-logo${logo || glyph ? '' : ' tint'}" style="--h:${hue};--s:${size || 32}px" aria-hidden="true">${inner}</span>`;
}
// One word for how a source is doing, from a row of GET /meetings/sources (missing row: never set up).
function meetSourceState(row, id) {
  if (!row || (row.status === 'needs_setup' && id === 'close')) return {key: 'off', word: 'Connect'};
  if (row.status === 'needs_setup') return {key: 'wait', word: 'Waiting'};
  if (row.status === 'error') return {key: 'error', word: 'Error'};
  if (row.status === 'delayed') return {key: 'delayed', word: 'Delayed'};
  return {key: 'on', word: 'Connected'};
}
// A large tile on the empty page: a source and how it is doing.
function meetTile(state, source) {
  const row = (state.sourceRows || []).find(r => r.id === source.id);
  const known = Array.isArray(state.sourceRows);
  // Granola with account sign-in is per person: the tile shows the viewer's own connection and anyone can use it.
  const account = source.id === 'granola' && !!state.granola;
  const mine = account ? granolaTileState(state) : null;
  const st = mine || (known ? meetSourceState(row, source.id) : {key: '', word: ''});
  const when = mine ? (mine.when ? ago(mine.when) : '') : st.key === 'on' ? ago(row.last_import || row.last_succeeded_at) : '';
  const label = `${source.name}: ${st.word || 'Connect'}${when ? (mine ? ', synced ' : ', last import ') + when : ''}`;
  const attrs = source.id === 'close' ? `href="${INTEGRATIONS}/close-crm"` : `type="button" data-msrc="${esc(source.id)}"${S.me?.role === 'owner' || account ? '' : ' disabled title="The owner connects sources"'}`;
  return `<${source.id === 'close' ? 'a' : 'button'} class="meet-tile big" data-state="${st.key}" ${attrs} aria-label="${esc(label)}">
      ${meetLogo(source.id, 40)}<span class="mt-text"><b>${esc(source.name)}</b><span class="mt-status">${st.key && st.key !== 'off' ? '<i class="dot" aria-hidden="true"></i>' : ''}${esc(st.word)}${when ? ` <span class="mt-when">${esc(when)}</span>` : ''}</span></span></${source.id === 'close' ? 'a' : 'button'}>`;
}
const meetTilesHTML = state => MEET_SOURCES.map(s => meetTile(state, s)).join('');
function meetWireTiles(state, root) {
  root.querySelectorAll('[data-msrc]').forEach(b => b.onclick = () => {
    const source = MEET_SOURCES.find(s => s.id === b.dataset.msrc);
    if (source.id === 'granola' && state.granola) return granolaConnect(state);
    if (window.openMeetingImporter) window.openMeetingImporter(source.id, source.name, () => meetSources(state));
  });
}
// Whether anything already brings meetings in: the viewer's Granola or any importer that has been set up.
const meetConnected = state => granolaReady(state.granola) || (state.sourceRows || []).some(r => MEET_SOURCES.some(m => m.id === r.id) && r.status && r.status !== 'needs_setup');
// A quiet "Sources" link in the tab bar (ui/app/meetings-sources.js), and the large tiles only on a page with no
// meetings and nothing connected.
function meetPaintTiles(state) {
  // Granola's status can land after the list: swap the empty state's tiles for a plain line once something is connected.
  if (state.empty && !!$('#notes-rows .meet-blank') === meetConnected(state)) { meetList(state); return; }
  const strip = $('#meet-sources');
  if (strip) {
    strip.hidden = !state.loaded || (!!state.empty && !meetConnected(state));
    strip.innerHTML = '<button type="button" class="linkish meet-src-btn" aria-haspopup="dialog">Sources</button>';
    strip.querySelector('.meet-src-btn').onclick = () => meetSourcesOpen(state);
    meetSourcesDot(state);
  }
  const big = document.querySelector('#notes-rows [data-meet-tiles]');
  if (big) { big.innerHTML = meetTilesHTML(state); meetWireTiles(state, big); }
  meetSourcesPaint(state);
}
async function meetSources(state) {
  let rows;
  try { rows = (await get('/meetings/sources')).sources; }
  catch { rows = null; }
  if (MEET !== state) return;
  state.sourceRows = Array.isArray(rows) ? rows : null;
  meetPaintTiles(state);
}

const noteTitle = r => r.title || (r.kind === 'note' ? 'Meeting log' : 'Meeting');
const notePreview = r => plainMd(r.note || r.voice_text || r.preview || '');
const meetPersonKey = value => String(value || '').trim().toLowerCase();
const meetPersonNames = r => [...(r.participants || []).map(p => p.name || p.email), ...(r.confirmed_attendees || []), ...(r.calendar?.attendees || []), ...(r.speakers || []).filter(v => !/^(?:[A-Z]|speaker[\s_-]*[A-Z0-9]+)$/i.test(String(v).trim())), r.source_context?.lead_name, r.source_context?.contact_name, r.recorded_by, r.owner !== 'owner' ? r.owner : ''].filter(v => typeof v === 'string' && v.trim());
const meetStatus = r => r.delivery?.status === 'pending' ? 'sending'
  : r.delivery?.status === 'error' || r.status === 'error' ? 'failed'
  : r.sent ? 'sent' : 'unsent';
// Age of a note in whole days, measured from local midnight so "Today" means the calendar day.
function meetAgeDays(r) {
  const t = Date.parse(r.started || r.created || ''); if (!Number.isFinite(t)) return Infinity;
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  return Math.round((day(new Date()) - day(new Date(t))) / 864e5);
}
const meetInWindow = (r, when) => when === 'all' ? true
  : when === 'today' ? meetAgeDays(r) <= 0
  : when === 'older' ? meetAgeDays(r) > 30
  : meetAgeDays(r) < Number(when);
const meetVisible = state => state.list.filter(r =>
  meetInWindow(r, state.when || 'all')
  && (!state.person || meetPersonNames(r).some(p => meetPersonKey(p) === state.person))
  && (!state.source || (r.source || 'tico') === state.source)
  && (state.filter === 'all' || meetStatus(r) === state.filter));
function meetFilterOptions(state) {
  for (const r of state.list) for (const name of meetPersonNames(r)) state.people.set(meetPersonKey(name), name.trim());
  const el = $('#notes-person'); if (!el) return;
  el.innerHTML = '<option value="">All participants</option>' + [...state.people].sort((a, b) => a[1].localeCompare(b[1])).map(([key, name]) => `<option value="${esc(key)}">${esc(name)}</option>`).join('');
  el.value = state.person;
  const src = $('#notes-source');
  if (src) {
    const kinds = [...new Set(state.list.map(r => r.source || 'tico'))].sort((a, b) => meetSourceLabel(a).localeCompare(meetSourceLabel(b)));
    src.innerHTML = '<option value="">All sources</option>' + kinds.map(k => `<option value="${esc(k)}">${esc(meetSourceLabel(k))}</option>`).join('');
    src.value = kinds.includes(state.source) ? state.source : '';
  }
}
// A meeting opens in its own resizable window showing only that meeting (`&window=1`): the
// desktop app makes a real window (app/src/main.rs, openWindow), a browser a pop-up. Phones, a
// blocked pop-up and the meeting window itself keep the full-page dialog.
const MEET_WINDOW = new URLSearchParams(location.hash.split('?')[1] || '').get('window') === '1';
if (MEET_WINDOW) document.body.classList.add('meet-window');
function meetWindow(id) {
  if (MEET_WINDOW || !id || matchMedia('(max-width: 760px)').matches) return false;
  const native = (window.ticoNativeFeatures || []).includes('openWindow') && nativeHandler('openWindow');
  if (native) { native.postMessage({meeting: id}); return true; }
  const url = `${location.pathname}${location.search}${MEETINGS}?meeting=${encodeURIComponent(id)}&window=1`;
  return !!window.open(url, 'tico-meeting-' + id, 'popup,width=1100,height=820');
}
function meetShow(state, open) {
  if (!open && MEET_WINDOW) { window.close(); return; }   // closing the meeting closes its window
  state.open = open;
  const dialog = $('#notes-modal'); if (!dialog) return;
  if (open && !dialog.open) dialog.showModal();
  if (!open) {
    meetSharedStop(state);
    state.noteSave?.(); state.noteSave = null;
    state.itemAdd = ''; state.linkOpen = false; state.showDismissed = false; state.confirmDelete = '';
    state.tsearch = false; state.tq = ''; state.sideTab = '';
    const tools = $('#notes-modal-tools'); if (tools) tools.innerHTML = '';
    dialog.close();
  }
}
async function meetLoad(state, first) {
  let list;
  const seq = ++state.loadSeq, review = state.review;
  const query = state.query || '';
  try { list = await get('/v2/meetings?review=' + review + '&q=' + encodeURIComponent(query)); }
  catch (e) {
    if (MEET !== state || seq !== state.loadSeq || !first) return;
    $('#notes-rows').innerHTML = `<div class="err" style="padding:14px">${esc(e.message)}</div>`;
    $('#notes-detail').innerHTML = `<div class="empty">Meetings are not available yet.</div>`;
    state.loaded = true; state.empty = false; meetPaintTiles(state);
    return;
  }
  if (MEET !== state || seq !== state.loadSeq) return;
  state.list = Array.isArray(list?.meetings) ? list.meetings : [];
  const grew = (list.pending_count || 0) - (state.pendingCount || 0);
  if (!first && grew > 0 && review !== 'pending') toast(`${grew} new in Pending`);
  state.pendingCount = list.pending_count || 0; meetPendingBadge(state.pendingCount);
  for (const id of state.checked) if (!state.list.some(r => r.id === id)) state.checked.delete(id);
  meetReviewTabs(state);
  meetFilterOptions(state);
  if (!state.selected || !meetVisible(state).some(r => r.id === state.selected)) state.selected = meetVisible(state)[0]?.id || '';
  meetList(state);
  if (state.selected && state.open) meetDetail(state, state.selected);
  else if (!state.selected) $('#notes-detail').innerHTML = '';
}
const meetFiltersOn = state => !!(state.person || state.source || state.filter !== 'all' || (state.when || 'all') !== 'all');
// No meetings and nothing narrowing the list: the sources, since the Sources link is hidden until one is connected
// (the header has Add notes).
const meetEmptyHTML = state => meetConnected(state) ? '<div class="notes-empty">No shared meetings yet.</div>'
  : `<div class="meet-blank"><div class="meet-tiles big" data-meet-tiles>${meetTilesHTML(state)}</div></div>`;
const meetPill = r => r.delivery?.status === 'pending' ? '<span class="pill in-progress">sending</span>' : r.delivery?.status === 'error' ? '<span class="pill fail">delivery failed</span>' : r.status === 'error' ? '<span class="pill fail">failed</span>'
  : r.sent ? '<span class="pill ok">sent</span>' : '';
function meetPersonFor(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;
  const email = raw.toLowerCase();
  const local = email.split('@')[0];
  const first = (raw.split(/\s+/)[0] || '').toLowerCase();
  const people = S.people || [];
  return people.find(p => (p.email || '').toLowerCase() === email)
      || people.find(p => p.id === local)
      || people.find(p => (firstName(p.name) || '').toLowerCase() === first)
      || null;
}
function meetPeopleList(r) {
  const seen = new Set(), out = [];
  const add = p => { if (p && !seen.has(p.id)) { seen.add(p.id); out.push(p); } };
  // A participant the roster knows is that human; anyone else is shown as they were named.
  for (const p of r.participants || []) {
    const known = p.person_id ? (S.people || []).find(x => x.id === p.person_id) : meetPersonFor(p.email || p.name);
    add(known || {id: p.email || p.name, name: p.name || p.email});
  }
  if (out.length) return out;
  const emails = (r.confirmed_attendees && r.confirmed_attendees.length ? r.confirmed_attendees
    : r.calendar?.attendees && r.calendar.attendees.length ? r.calendar.attendees : []);
  for (const e of emails) add(meetPersonFor(e));
  if (!out.length) for (const s of r.speakers || []) add(meetPersonFor(s));
  return out;
}
// Who was in a meeting, for the list: the first person's avatar and up to two names.
function meetWhoHTML(r) {
  if (r.source === 'close') {
    const lead = r.source_context?.lead_name || r.source_context?.contact_name || '';
    return `<span class="meet-who">${esc(lead || (r.source_type === 'meeting' ? 'Close meeting' : 'Close call'))}</span>`;
  }
  const people = meetPeopleList(r);
  if (!people.length) return '';
  const names = people.map(p => firstName(p.name) || p.id);
  const text = names.slice(0, 2).join(', ') + (names.length > 2 ? ` +${names.length - 2}` : '');
  return `<span class="meet-who" title="${esc(people.map(p => p.name || p.id).join(', '))}"><span class="meet-faces">${personAvatar(people[0], 18)}</span>${esc(text)}</span>`;
}
// What a meeting turned into: items a person pushed, and the follow-ups the bot it was sent to filed.
const MEET_KINDS = {task: ['task', 'tasks', 'Task'], feature: ['feature request', 'feature requests', 'Feature request'], doc: ['doc update', 'doc updates', 'Doc update']};
function meetMade(r) {
  const out = [];
  for (const key of ['task', 'feature', 'doc']) for (const it of (r.outbox || {})[key] || [])
    if (it.status === 'pushed') out.push({kind: key, id: /^https?:/.test(it.result_ref || '') ? '' : it.result_ref || '', url: /^https?:\/\//.test(it.result_ref || '') ? it.result_ref : '', title: it.text});
  for (const t of r.outcome?.tasks || []) out.push({kind: 'task', id: t.id, title: t.title, status: t.status});
  return out;
}
const meetProposed = r => ['task', 'feature', 'doc'].reduce((n, k) => n + ((r.outbox || {})[k] || []).filter(it => it.status === 'proposed').length, 0);
// The bot's progress on a sent meeting, in one word, or '' once it is done (or never sent).
function meetWorking(r) {
  const bot = r.outcome?.bot || r.delivery?.destination;
  const name = bot ? botDisplayName(bot) : '';
  if (r.delivery?.status === 'pending') return {spin: true, word: 'Sending', bot: name};
  const st = r.outcome?.status;
  if (st === 'open' || st === 'doing' || st === 'review' || st === 'ready') return {spin: true, word: 'Processing', bot: name};
  if (st === 'waiting') return {spin: false, word: 'Waiting', bot: name};
  return null;
}
function meetMadeChips(r) {
  const made = meetMade(r), chips = [];
  for (const key of ['task', 'feature', 'doc']) {
    const of = made.filter(m => m.kind === key); if (!of.length) continue;
    const word = `${of.length} ${MEET_KINDS[key][of.length === 1 ? 0 : 1]}`;
    chips.push(of.length === 1 && of[0].id ? `<a class="meet-made-chip" href="#/task/${encodeURIComponent(of[0].id)}" title="${esc(of[0].title)}">${esc(word)}</a>`
      : `<span class="meet-made-chip" title="${esc(of.map(m => m.title).join('\n'))}">${esc(word)}</span>`);
  }
  return chips.join('');
}
// "Today, 2:37 PM" / "Yesterday, 9:12 AM" / "Sep 4, 2:37 PM"
function meetWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 864e5);
  const t = d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  if (diff === 0) return `Today, ${t}`;
  if (diff === 1) return `Yesterday, ${t}`;
  return `${d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', ...(d.getFullYear() === now.getFullYear() ? {} : {year: 'numeric'})})}, ${t}`;
}
// The first sentence of an outcome, for the list; the full text stays in the detail view.
function meetOneLine(text) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  const m = s.match(/^.*?[.!?](?=\s|$)/);
  const line = m ? m[0] : s;
  return line.length > 140 ? line.slice(0, 137).replace(/\s+\S*$/, '') + '…' : line;
}
// The top of an open meeting: every task it turned into, each a link. The bot's progress is the Outcome below.
function meetList(state) {
  const el = $('#notes-rows'); if (!el) return;
  const list = meetVisible(state);
  const filtered = meetFiltersOn(state);
  state.loaded = true;
  state.empty = state.review === 'live' && !state.list.length && !filtered && !state.query;
  const bar = $('#notes-filters'); if (bar) bar.hidden = !(state.list.length || filtered);
  const count = $('#notes-count');
  if (count) count.textContent = state.list.length ? (list.length === state.list.length ? `${list.length} total` : `${list.length} of ${state.list.length}`) : '';
  patchLiveHTML(el, list.length ? `<ul class="meet-list">${list.map(r => {
      const when = meetWhen(r.started || r.created), working = meetWorking(r), proposed = meetProposed(r);
      const meta = [when ? `<span>${esc(when)}</span>` : '', r.duration_ms ? `<span>${esc(mmss(r.duration_ms))}</span>` : '', meetWhoHTML(r)].filter(Boolean).join('');
      const fail = /fail/.test(meetPill(r)) ? meetPill(r) : '';
      return `<li class="meet-row review-${state.review}" data-note-row="${esc(r.id)}" title="${esc(meetSourceLabel(r.source))}">${state.review === 'pending' ? `<input type="checkbox" data-review-check="${esc(r.id)}" aria-label="Select ${esc(noteTitle(r))}"${state.checked.has(r.id) ? ' checked' : ''}>` : ''}${meetLogo(r.source || 'tico', 30)}
        <div class="meet-main"><button class="note-title" type="button" data-rec="${esc(r.id)}">${esc(noteTitle(r))}</button><div class="meet-meta">${meta}</div></div>
        <div class="meet-side">${state.review === 'pending' ? `<button class="primary small" type="button" data-review-action="approve" data-send="auto" data-id="${esc(r.id)}" title="Share, then send to ${esc(assistantName())} to file its tasks">Share & send</button><button class="ghost small" type="button" data-review-action="dismiss" data-id="${esc(r.id)}">Dismiss</button>` : state.review === 'dismissed' ? `<button class="ghost small" type="button" data-review-action="restore" data-id="${esc(r.id)}">Restore</button>` : ''}${fail}${working ? `<span class="meet-state" title="${esc(working.bot)}">${working.spin ? '<i class="mg-spin" aria-hidden="true"></i>' : ''}${esc(working.word)}${working.bot ? ` <span class="muted">· ${esc(working.bot)}</span>` : ''}</span>` : ''}${meetMadeChips(r)}${proposed && state.review === 'live' && !working && !meetMade(r).length ? `<span class="meet-proposed muted">${proposed} proposed</span>` : ''}</div></li>`;
    }).join('')}</ul>`
    : state.empty ? meetEmptyHTML(state) : `<div class="notes-empty">${state.review === 'pending' && !state.query && !filtered ? 'No pending meetings.' : state.review === 'dismissed' && !state.query && !filtered ? 'No dismissed meetings.' : 'No meetings match.'}</div>`);
  el.querySelectorAll('[data-note-row]').forEach(b => b.onclick = event => {
    if (event.target.closest('[data-review-check], [data-review-action], a')) return;
    state.selected = b.dataset.noteRow; state.confirmDelete = ''; state.editing = false;
    meetShow(state, true); meetDetail(state, state.selected);
  });
  meetReviewWire(state, el); meetReviewBulk(state);
  meetPaintTiles(state);
}
async function meetDetail(state, id) {
  const el = $('#notes-detail'); if (!el) return;
  if (el.dataset.id !== id) { el.dataset.id = id; el.innerHTML = '<div class="empty">Loading…</div>'; }
  let d;
  try { d = await get(`/meetings/${encodeURIComponent(id)}`); }
  catch (e) { if (MEET === state && state.selected === id && $('#notes-detail')) $('#notes-detail').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (MEET !== state || state.selected !== id || !$('#notes-detail')) return;
  meetPaint(state, {...(state.list.find(r => r.id === id) || {}), ...d});
}
const IMPORT_SOURCES = {upload: 'Upload', zoom: 'Zoom', 'google-meet': 'Google Meet', granola: 'Granola', otter: 'Otter', fireflies: 'Fireflies', other: 'Other'};
const meetSourceLabel = s => ({...IMPORT_SOURCES, manual: 'Typed', close: 'Close', submitted: 'Imported', api: 'API', tico: 'Tico'})[s || 'tico']
  || String(s).replace(/[-_]+/g, ' ').replace(/^./, c => c.toUpperCase());
// The source in words; a meeting the Recorder filed says so.
const meetSourceName = r => r.client === 'tico-recorder' ? 'Tico Recorder' : r.source === 'tico-live' ? 'Tico live' : meetSourceLabel(r.source);
const meetIcon = (name, label = '') => `<span class="nav-icon" aria-hidden="true">${name}</span>${label}`;
const meetStatusWord = s => (typeof STATUS_WORD === 'object' && STATUS_WORD[s]) || s || '';
// Who was there: people, then the bots attached to its live meeting and the bot it was shared with. Each is an
// avatar; its name shows on hover, or on tap (focus) on a phone.
function meetFaces(state, rec) {
  const faces = meetPeopleList(rec).map(p => ({name: p.name || p.id, html: personAvatar(p, 26)}));
  const bots = new Map((state.live?.[rec.id]?.bots || []).map(b => [b.bot, b.name]));
  const sent = rec.sent?.bot || rec.sent?.slug || rec.delivery?.destination;
  if (sent && !bots.has(sent)) bots.set(sent, '');
  for (const [bot, name] of bots) faces.push({name: name || botDisplayName(bot), html: botAvatar(bot, 26), bot});
  return faces.map(f => `<span class="mp-face" tabindex="0" role="img" aria-label="${esc(f.name)}" data-name="${esc(f.name)}"${f.bot ? ` data-face-bot="${esc(f.bot)}"` : ''}>${f.html}</span>`).join('');
}
// Team: everyone signed in can open it. Private: its participants, its owner and the team owner.
function meetVisibilityHTML(rec, mayEdit) {
  const priv = !!rec.private;
  if (!mayEdit) return `<span class="mp-vis-ro">${meetIcon(priv ? 'lock' : 'groups', priv ? 'Private' : 'Team')}</span>`;
  return `<div class="mp-vis" role="group" aria-label="Who can see it">${[['team', 'groups', 'Team'], ['private', 'lock', 'Private']].map(([v, icon, label]) =>
    `<button type="button" data-vis="${v}" aria-pressed="${(v === 'private') === priv}">${meetIcon(icon, label)}</button>`).join('')}</div>`;
}
// Share: a link for a person, and the bot this meeting goes to. Sending is once, so a sent meeting shows its bot.
function meetShareHTML(rec, mayEdit) {
  const sent = rec.sent?.bot || rec.sent?.slug || rec.delivery?.destination || '';
  const bots = !mayEdit ? [] : sent ? [{id: sent}] : activeEmps();
  return `<details class="mp-pop" id="meet-share"><summary class="mp-icon" role="button" aria-haspopup="menu" aria-label="Share" title="Share">${meetIcon('person_add')}</summary>
    <div class="mp-pop-list" role="menu">
      <button type="button" role="menuitem" data-share-copy>${meetIcon('link', 'Copy link')}</button>
      ${bots.length ? `<div class="mp-pop-h">Bots</div>${bots.length > 8 ? '<input type="search" class="mp-pop-find" data-share-find aria-label="Find a bot" placeholder="Find">' : ''}
      ${bots.map(b => `<button type="button" role="menuitemcheckbox" aria-checked="${sent === b.id}" data-share-bot="${esc(b.id)}"${sent ? ' disabled' : ''}>${botAvatar(b.id, 18)}<span>${esc(botDisplayName(b.id))}</span>${sent ? meetIcon('check') : ''}</button>`).join('')}` : ''}
    </div></details>`;
}
const meetMoreHTML = (state, rec) => `<details class="mp-pop" id="meet-more-menu"><summary class="mp-icon" role="button" aria-haspopup="menu" aria-label="More" title="More">${meetIcon('more_horiz')}</summary>
  <div class="mp-pop-list" role="menu"><button type="button" role="menuitem" class="danger" id="meet-del">${meetIcon('delete', state.confirmDelete === rec.id ? 'Really delete?' : 'Delete')}</button></div></details>`;
function meetPaint(state, rec) {
  const el = $('#notes-detail'); if (!el) return;
  const id = rec.id, typed = rec.kind === 'note';
  state.rec = rec;
  // The server decides who may change a meeting; `can_edit: false` is somebody else's, open here to read.
  const mayEdit = rec.can_edit !== false;
  const reviewing = rec.review_state === 'pending' || rec.review_state === 'dismissed';
  const meta = [meetWhen(rec.started || rec.created), rec.duration_ms ? mmss(rec.duration_ms) : '', meetSourceName(rec)].filter(Boolean).map(esc);
  if (rec.source === 'close') {
    const lead = rec.source_context?.lead_name || rec.source_context?.contact_name;
    if (lead) meta.push(esc(lead));
    if (/^https:\/\//i.test(rec.source_context?.lead_url || '')) meta.push(`<a href="${esc(rec.source_context.lead_url)}" target="_blank" rel="noopener">Open in Close</a>`);
  }
  // Only an https link is drawn as one; the server refuses anything else, and this keeps a bad row inert.
  if (/^https:\/\//i.test(rec.media_url || '')) meta.push(`<a href="${esc(rec.media_url)}" target="_blank" rel="noopener noreferrer">Recording</a>`);
  const draft = state.noteDraft?.id === id ? state.noteDraft.value : null;
  const notes = mayEdit ? `<section class="mp-sec"><div class="mp-label">Notes</div><textarea id="meet-note" class="mp-notes" aria-label="Notes" placeholder="Write notes…">${esc(draft ?? rec.note ?? '')}</textarea></section>`
    : rec.note ? `<section class="mp-sec"><div class="mp-label">Notes</div><div class="mp-notes mp-notes-ro">${esc(rec.note)}</div></section>` : '';
  const files = rec.attachments?.length ? `<div class="mp-files">${rec.attachments.map(f => S.me?.cloud && f.id
    ? `<a class="pill" href="${API}/v2/files/${encodeURIComponent(f.id)}" download>${esc(f.name)}</a>`
    : `<span class="pill">${esc(typeof f === 'string' ? f : f.name || 'Attachment')}</span>`).join('')}</div>` : '';
  const summary = rec.notes ? `<section class="mp-sec"><div class="mp-label">Summary</div><div class="md">${safeMd(rec.notes)}</div></section>` : '';
  const side = !typed || (rec.turns || []).length || rec.voice_text;
  el.innerHTML = `<div class="mp${side ? '' : ' mp-one'}">
    <header class="mp-head">
      <h2 id="meet-heading">${esc(noteTitle(rec))}</h2>
      <div class="mp-meta">${meta.join('<span class="sep" aria-hidden="true"> · </span>')}</div>
      ${reviewing && typed ? '' : `<div class="mp-who">${typed ? '' : `<div class="mp-faces" id="meet-faces">${meetFaces(state, rec)}</div>`}${reviewing ? '' : meetVisibilityHTML(rec, mayEdit)}</div>`}
      ${rec.error ? `<div class="err">${esc(rec.error)}</div>` : ''}
      ${rec.warning ? `<div class="muted mp-warn">${esc(rec.warning)}</div>` : ''}
    </header>
    <div class="mp-cols">
      <div class="mp-main">
        ${typed || reviewing ? '' : '<section class="mp-sec" id="meet-sections"></section>'}
        ${notes}${files}${summary}
      </div>
      ${side ? '<aside class="mp-side" id="meet-side" aria-label="Transcript"></aside>' : ''}
    </div>
    ${reviewing ? meetReviewDetail(rec) : ''}</div>`;
  const tools = $('#notes-modal-tools');
  if (tools) tools.innerHTML = reviewing ? '' : meetShareHTML(rec, mayEdit) + (mayEdit ? meetMoreHTML(state, rec) : '');
  if (mayEdit) meetRenameable(state, rec, $('#meet-heading'), () => meetPaint(state, rec));
  if (reviewing) meetReviewWire(state, el);
  const reviewSend = $('#meet-review-send');
  if (reviewSend) reviewSend.onchange = () => { $('#meet-review-go').textContent = reviewSend.value ? 'Share & send' : 'Share'; };
  meetSideRender(state, rec);
  meetNoteWire(state, rec);
  meetToolsWire(state, rec);
  el.querySelectorAll('[data-vis]').forEach(b => b.onclick = async () => {
    const wanted = b.dataset.vis === 'private';
    if (wanted === !!rec.private) return;
    el.querySelectorAll('[data-vis]').forEach(x => x.disabled = true);
    try {
      const d = await post(`/meetings/${encodeURIComponent(id)}/edit`, {private: wanted, ...(S.me?.cloud ? {version: rec.version} : {})});
      toast(wanted ? 'Private' : 'Visible to the team');
      await meetLoad(state, false);
      if (MEET === state && state.selected === id) meetPaint(state, {...rec, ...(d || {})});
    } catch (e) { toast(e.message, true); el.querySelectorAll('[data-vis]').forEach(x => x.disabled = false); }
  });
  if (!typed && !reviewing) meetItemsWire(state, rec);
  meetLiveLoad(state, rec);
}
// Notes are edited in place and saved a moment after typing stops, on blur, and when the meeting closes.
function meetNoteWire(state, rec) {
  const area = $('#meet-note'); if (!area) return;
  const id = rec.id;
  const grow = () => { area.style.height = 'auto'; area.style.height = Math.max(72, area.scrollHeight + 2) + 'px'; };
  grow();
  const save = async () => {
    clearTimeout(state.noteTimer); state.noteTimer = 0;
    if (state.noteDraft?.id !== id) return;
    const value = state.noteDraft.value.trim();
    if (value === String(rec.note || '').trim()) { state.noteDraft = null; return; }
    if (state.noteSaving) { state.noteAgain = true; return; }
    state.noteSaving = true;
    try {
      const d = await post(`/meetings/${encodeURIComponent(id)}/edit`, {note: value, ...(S.me?.cloud ? {version: rec.version} : {})});
      rec.note = value; if (d?.version !== undefined) rec.version = d.version;
      const row = state.list.find(r => r.id === id); if (row) row.note = value;
      if (state.noteDraft?.id === id && state.noteDraft.value.trim() === value) state.noteDraft = null;
    } catch (e) { toast(e.message, true); }
    finally { state.noteSaving = false; if (state.noteAgain) { state.noteAgain = false; save(); } }
  };
  area.oninput = () => {
    grow(); state.noteDraft = {id, value: area.value};
    clearTimeout(state.noteTimer); state.noteTimer = setTimeout(save, 900);
  };
  area.onblur = save;
  state.noteSave = save;
}
function meetToolsWire(state, rec) {
  const id = rec.id, root = $('#notes-modal');
  const copy = root?.querySelector('[data-share-copy]');
  if (copy) copy.onclick = async () => {
    try { await copyText(`${location.origin}${location.pathname}${location.search}${MEETINGS}?meeting=${encodeURIComponent(id)}`); toast('Link copied'); }
    catch (e) { toast(e.message, true); }
    copy.closest('details').open = false;
  };
  const find = root?.querySelector('[data-share-find]');
  if (find) find.oninput = () => {
    const q = find.value.trim().toLowerCase();
    root.querySelectorAll('[data-share-bot]').forEach(b => { b.hidden = !!q && !b.textContent.toLowerCase().includes(q); });
  };
  root?.querySelectorAll('[data-share-bot]').forEach(b => b.onclick = async () => {
    root.querySelectorAll('[data-share-bot]').forEach(x => x.disabled = true);
    try {
      toastSent(await post(`/meetings/${encodeURIComponent(id)}/send`, {bot_id: b.dataset.shareBot, instructions: ''}));
      await meetLoad(state, false);
    } catch (e) { toast(e.message, true); root.querySelectorAll('[data-share-bot]').forEach(x => x.disabled = false); }
  });
  const del = $('#meet-del');
  if (del) del.onclick = async () => {
    if (state.confirmDelete !== id) { state.confirmDelete = id; del.innerHTML = meetIcon('delete', 'Really delete?'); return; }
    del.disabled = true;
    try {
      await post(`/meetings/${encodeURIComponent(id)}/delete`);
      toast(S.me?.cloud ? 'Removed from the list; recoverable server history is retained.' : 'Deleted');
      state.confirmDelete = ''; state.selected = ''; meetShow(state, false); await meetLoad(state, true);
    } catch (e) { toast(e.message, true); del.disabled = false; del.innerHTML = meetIcon('delete', 'Delete'); }
  };
}
// The live meeting a saved one was filed from holds its chat and the bots that sat in; they are read with the
// live meeting's own rules, so a viewer who cannot open it sees neither.
async function meetLiveLoad(state, rec) {
  const live = rec.live_meeting_id;
  if (!live || state.live?.[rec.id]) return;
  (state.live ||= {})[rec.id] = {chat: [], bots: []};
  let d;
  try { d = await get(`/v2/live-meetings/${encodeURIComponent(live)}`); } catch { return; }
  if (MEET !== state || state.selected !== rec.id) return;
  state.live[rec.id] = {chat: d?.chat || [], bots: d?.bots || []};
  const faces = $('#meet-faces');
  if (faces) faces.innerHTML = meetFaces(state, rec);
  meetSideRender(state, rec);
}
// ---- the transcript (and the live meeting's chat) on the side
const meetMarked = (text, q) => {
  const safe = esc(text);
  if (!q) return safe;
  const needle = esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return safe.replace(new RegExp(needle, 'gi'), m => `<mark>${m}</mark>`);
};
const meetTurnHTML = (who, at, text, q) => `<div class="mp-turn"><div class="who">${who ? `<b>${esc(who)}</b>` : ''}${at ? `<span class="time">${esc(at)}</span>` : ''}</div><p class="said">${meetMarked(text, q)}</p></div>`;
function meetTranscriptHTML(rec, q) {
  const turns = rec.turns || [];
  const main = turns.length ? turns.map(t => meetTurnHTML(t.speaker, mmss(t.start_ms), t.text, q)).join('')
    : rec.voice_text ? meetTurnHTML('', '', rec.voice_text, q)
    : rec.transcript_readable ? String(rec.transcript_readable).split(/\n+/).filter(l => l.trim()).map(l => `<p class="said">${meetMarked(plainMd(l), q)}</p>`).join('')
    : '<div class="mp-empty">No transcript</div>';
  const others = (rec.transcript_sources || []).filter(t => !t.primary).map(t =>
    `<details class="mp-alt"><summary>${esc(meetSourceLabel(t.source))} ${esc(t.resource_type)}${t.transcript_index ? ` ${t.transcript_index + 1}` : ''}</summary>
      ${(t.turns || []).map(turn => meetTurnHTML(turn.speaker, mmss(turn.start_ms), turn.text, q)).join('')}</details>`).join('');
  return main + others;
}
const meetTranscriptText = rec => (rec.turns || []).length
  ? rec.turns.map(t => `${t.speaker ? t.speaker + ' ' : ''}[${mmss(t.start_ms)}] ${t.text}`).join('\n')
  : rec.voice_text || plainMd(rec.transcript_readable || '');
const meetChatName = m => m.actor ? actorLabel(m.actor) : m.role || '';
const meetChatHTML = chat => chat.map(m => meetTurnHTML(meetChatName(m), m.at_ms === null || m.at_ms === undefined ? '' : mmss(m.at_ms), m.text, '')).join('');
function meetSideRender(state, rec) {
  const side = $('#meet-side'); if (!side) return;
  const chat = state.live?.[rec.id]?.chat || [];
  const tab = chat.length && state.sideTab === 'chat' ? 'chat' : 'transcript';
  const searching = tab === 'transcript' && state.tsearch;
  side.innerHTML = `<div class="mp-side-head">${chat.length ? `<div class="mp-tabs" role="tablist" aria-label="Transcript and chat">${[['transcript', 'Transcript'], ['chat', 'Chat']].map(([k, label]) =>
      `<button type="button" role="tab" data-side-tab="${k}" aria-selected="${tab === k}">${label}</button>`).join('')}</div>` : '<h3>Transcript</h3>'}<span class="grow"></span>
    ${tab === 'transcript' ? `<button class="mp-icon" type="button" data-tsearch aria-pressed="${!!searching}" aria-label="Search transcript" title="Search">${meetIcon('search')}</button>` : ''}
    <button class="mp-icon" type="button" data-tcopy aria-label="${tab === 'chat' ? 'Copy chat' : 'Copy transcript'}" title="Copy">${meetIcon('content_copy')}</button></div>
    ${searching ? `<label class="mp-search">${meetIcon('search')}<input type="search" data-tq aria-label="Find in transcript" placeholder="Find" value="${esc(state.tq || '')}"><span class="mp-hits" data-thits></span></label>` : ''}
    <div class="mp-side-body" data-tbody>${tab === 'chat' ? meetChatHTML(chat) : meetTranscriptHTML(rec, searching ? state.tq : '')}</div>`;
  const body = side.querySelector('[data-tbody]');
  const hits = () => {
    const marks = [...body.querySelectorAll('mark')], out = side.querySelector('[data-thits]');
    if (out) out.textContent = state.tq ? `${marks.length ? Math.min(state.thit || 0, marks.length - 1) + 1 : 0}/${marks.length}` : '';
    marks.forEach((m, i) => m.classList.toggle('now', i === (state.thit || 0)));
    marks[state.thit || 0]?.scrollIntoView({block: 'center'});
  };
  side.querySelectorAll('[data-side-tab]').forEach(b => b.onclick = () => { state.sideTab = b.dataset.sideTab; meetSideRender(state, rec); });
  const search = side.querySelector('[data-tsearch]');
  if (search) search.onclick = () => { state.tsearch = !state.tsearch; if (!state.tsearch) state.tq = ''; meetSideRender(state, rec); side.querySelector('[data-tq]')?.focus(); };
  side.querySelector('[data-tcopy]').onclick = async () => {
    try { await copyText(tab === 'chat' ? chat.map(m => `${meetChatName(m)}: ${m.text}`).join('\n') : meetTranscriptText(rec)); toast('Copied'); }
    catch (e) { toast(e.message, true); }
  };
  const input = side.querySelector('[data-tq]');
  if (input) {
    input.oninput = () => { state.tq = input.value.trim(); state.thit = 0; body.innerHTML = meetTranscriptHTML(rec, state.tq); hits(); };
    input.onkeydown = ev => {
      if (ev.key === 'Escape' && input.value) { ev.preventDefault(); ev.stopPropagation(); input.value = ''; input.oninput(); }
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      const count = body.querySelectorAll('mark').length; if (!count) return;
      state.thit = ((state.thit || 0) + (ev.shiftKey ? count - 1 : 1)) % count; hits();
    };
    if (state.tq) hits();
  }
}

// ---- Add notes: typed notes, an uploaded or pasted transcript, or both, filed through POST /api/v2/meetings/import ----
// A local date-time as ISO-8601 with this browser's offset; the server wants a timezone.
function meetLocalIso(value) {
  const d = new Date(value); if (Number.isNaN(d.getTime())) return '';
  const p = n => String(Math.trunc(Math.abs(n))).padStart(2, '0'), off = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${off >= 0 ? '+' : '-'}${p(off / 60)}:${p(off % 60)}`;
}
const meetSplitList = text => String(text || '').split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
// A human's own meeting: notes typed by hand (source "manual") and/or a transcript from a file or a
// paste (a source of their choosing, "upload" by default). The importer takes participants, a start time
// and send_to, and files under one reference per meeting, which /api/notes (a bare note) does not.
const TRANSCRIPT_TYPES = '.txt,.vtt,.srt,.json,.md,text/plain,text/vtt,application/json';
function meetNotesOpen(state) {
  const dialog = $('#manual-modal'); if (!dialog) return;
  const people = (S.people || []).filter(p => !p.hidden && !p.left && (p.name || p.email));
  const now = new Date(), pad = n => String(n).padStart(2, '0');
  const local = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
  dialog.innerHTML = `<form class="import-form" id="manual-form">
      <header><h2>Add notes</h2><button class="ghost" type="button" id="manual-close" aria-label="Close">✕</button></header>
      <div class="import-grid">
        <label>Title <input type="text" id="manual-title" maxlength="300" autocomplete="off"></label>
        <label>Date and time <input type="datetime-local" id="manual-when" value="${local}"></label>
      </div>
      <fieldset class="import-people"><legend>Participants</legend>
        <div class="bot-editor-owners">${people.map(p => `<label><input type="checkbox" name="manual-person" value="${esc(p.email || p.name)}">${esc(p.name || p.email)}</label>`).join('')}</div>
        <label>Others <input type="text" id="manual-others" autocomplete="off" spellcheck="false" placeholder="Emails or names, separated by commas"></label>
      </fieldset>
      <label>Notes (markdown) <textarea id="manual-notes" rows="6" placeholder="What was discussed and decided"></textarea></label>
      <div class="meet-drop" id="manual-drop"><span>Transcript</span>
        <button class="linkish" type="button" id="manual-upload">Upload a file</button><span aria-hidden="true">·</span>
        <button class="linkish" type="button" id="manual-paste">Paste</button>
        <input type="file" id="manual-file" accept="${TRANSCRIPT_TYPES}" hidden aria-label="Transcript file"></div>
      <div id="manual-transcript" hidden>
        <label>Transcript <textarea id="manual-text" rows="6" placeholder="Name: text, WebVTT, SRT or JSON segments"></textarea></label>
        <div class="import-grid">
          <label>Source <select id="manual-source">${Object.entries(IMPORT_SOURCES).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>
          <label>Recording link (optional) <input type="url" id="manual-media" placeholder="https://…"></label>
        </div>
      </div>
      <label>Send to a bot (optional) <select id="manual-send"><option value="">Don't send</option><option value="auto">Auto — ${esc(assistantName())} routes it</option>${activeEmps().map(e => `<option value="${esc(e.id)}">${esc(e.display_name)}</option>`).join('')}</select></label>
      <label class="import-private"><input type="checkbox" id="manual-private"> Private: participants, its owner and the team owner only</label>
      <div class="row"><button class="primary" type="submit" id="manual-go">Add notes</button>
        <button class="ghost" type="button" id="manual-cancel">Cancel</button><span class="muted" id="manual-msg" role="status"></span></div>
    </form>`;
  const q = sel => dialog.querySelector(sel), close = () => dialog.close();
  q('#manual-close').onclick = close; q('#manual-cancel').onclick = close;
  const showTranscript = () => { q('#manual-transcript').hidden = false; };
  q('#manual-paste').onclick = () => { showTranscript(); q('#manual-text').focus(); };
  q('#manual-upload').onclick = () => q('#manual-file').click();
  const readFile = file => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      showTranscript(); q('#manual-text').value = String(reader.result || '');
      if (!q('#manual-title').value.trim()) q('#manual-title').value = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
      q('#manual-msg').textContent = file.name;
    };
    reader.onerror = () => { q('#manual-msg').innerHTML = '<span class="err">That file could not be read.</span>'; };
    reader.readAsText(file);
  };
  q('#manual-file').onchange = ev => readFile(ev.target.files?.[0]);
  const drop = q('#manual-drop');
  dialog.ondragover = ev => { ev.preventDefault(); drop.classList.add('over'); };
  dialog.ondragleave = () => drop.classList.remove('over');
  dialog.ondrop = ev => { ev.preventDefault(); drop.classList.remove('over'); readFile(ev.dataTransfer?.files?.[0]); };
  q('#manual-form').onsubmit = async ev => {
    ev.preventDefault();
    const notes = q('#manual-notes').value.trim(), title = q('#manual-title').value.trim(), transcript = q('#manual-text').value;
    const withTranscript = !!transcript.trim();
    const msg = q('#manual-msg'), go = q('#manual-go');
    if (!withTranscript && !notes) { msg.innerHTML = '<span class="err">Add notes or a transcript.</span>'; return; }
    if (!withTranscript && !title) { msg.innerHTML = '<span class="err">Add a title.</span>'; q('#manual-title').focus(); return; }
    const participants = [...dialog.querySelectorAll('input[name=manual-person]:checked')].map(i => i.value);
    for (const other of meetSplitList(q('#manual-others').value)) if (!participants.includes(other)) participants.push(other);
    const body = withTranscript ? {source: q('#manual-source').value, transcript} : {source: 'manual'};
    body.review = 'live';
    if (title) body.title = title;
    if (notes) body.notes = notes;
    const at = meetLocalIso(q('#manual-when').value);
    if (at) body.started_at = at;
    if (participants.length) body.participants = participants;
    if (withTranscript && q('#manual-media').value.trim()) body.media_url = q('#manual-media').value.trim();
    if (q('#manual-private').checked) body.private = true;
    if (q('#manual-send').value) body.send_to = q('#manual-send').value;
    go.disabled = true; msg.textContent = 'Saving…';
    try {
      const r = await post('/v2/meetings/import', body);
      close(); toast(r?.sent ? 'Added and sent' : 'Added');
      if (MEET !== state) return;
      state.filter = 'all'; state.person = ''; state.source = ''; state.when = 'all'; state.query = '';
      const search = $('#notes-search'); if (search) search.value = '';
      ['#notes-status', '#notes-when', '#notes-person', '#notes-source'].forEach(id => { const el = $(id); if (el) el.selectedIndex = 0; });
      state.selected = r?.id || '';
      await meetLoad(state, true);
      if (MEET === state && state.selected) { meetShow(state, true); meetDetail(state, state.selected); }
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; go.disabled = false; }
  };
  dialog.showModal();
  q('#manual-title').focus();
}

// ----------------------------------------------------------------- Tasks: one list
// What the meeting proposes (Create / Dismiss), what it already made, and the tasks linked to it, each once.
const meetAuthor = a => a === 'brain' ? 'Tico' : actorLabel(a);
const MEET_SEP = '<span class="sep" aria-hidden="true"> · </span>';
// The owner picker's bot options are bare ids; a task owner is the actor, `bot:<id>`.
const meetFieldValue = f => { const v = f.value.trim(); return f.dataset.field === 'owner' && !v.includes(':') ? 'bot:' + v : v; };
const meetTaskId = ref => /^https?:/i.test(ref || '') ? '' : ref || '';
function meetTaskRowHTML(t) {
  const done = t.status === 'done' || t.status === 'closed';
  const href = t.id ? `#/task/${encodeURIComponent(t.id)}` : /^https:\/\//i.test(t.url || '') ? t.url : '';
  const title = esc(t.title || 'Task');
  const parts = [t.number ? '#' + esc(t.number) : '', esc(meetStatusWord(t.status)), t.who ? esc(t.who) : ''].filter(Boolean);
  return `<span class="mp-check${done ? ' done' : ''}${t.spin ? ' spin' : ''}" aria-hidden="true"></span>
    <span class="mp-task-main">${href ? `<a class="t" href="${esc(href)}"${t.url && !t.id ? ' target="_blank" rel="noopener"' : ''}>${title}</a>` : `<span class="t">${title}</span>`}
      ${parts.length ? `<span class="mp-sub">${parts.join(MEET_SEP)}</span>` : ''}
      ${t.line ? `<span class="mp-sub mp-line">${esc(t.line)}</span>` : ''}</span>
    ${t.unlink ? `<button class="mp-icon mp-unlink" type="button" data-unlink="${esc(t.id)}" aria-label="Unlink" title="Unlink">${meetIcon('close')}</button>` : ''}`;
}
// Tasks made from the meeting: items a person created, the task it was shared to a bot as, and that task's follow-ups.
function meetMadeTasks(state, rec) {
  const out = new Map(), add = t => { const key = t.id || t.url || t.title; if (key && !out.has(key)) out.set(key, t); };
  const items = state.items?.[rec.id]?.sections?.task || rec.outbox?.task || [];
  for (const it of items) if (it.status === 'pushed')
    add({id: meetTaskId(it.result_ref), url: /^https:\/\//i.test(it.result_ref || '') ? it.result_ref : '', title: it.text});
  const bot = rec.sent?.bot || rec.sent?.slug || rec.delivery?.destination || rec.outcome?.bot;
  const work = rec.delivery?.status === 'pending' ? 'Sending' : rec.delivery?.status === 'error' ? 'Delivery failed' : '';
  if (rec.sent?.task || work) {
    const st = rec.outcome?.status;
    add({id: rec.sent?.task || '', title: bot ? `Review by ${botDisplayName(bot)}` : 'Review', status: work ? '' : st,
         who: work, spin: work === 'Sending' || ['open', 'doing', 'review', 'ready'].includes(st),
         line: rec.outcome?.task_id ? meetOneLine(plainMd(rec.outcome.summary || '')) : ''});
  }
  for (const t of rec.outcome?.tasks || []) add({id: t.id, title: t.title, status: t.status});
  return [...out.values()];
}
const meetRecFor = (state, id) => state.rec?.id === id ? state.rec : {...(state.list.find(r => r.id === id) || {}), id};
function meetTasksShell(box) {
  if (box.querySelector('[data-pending-panel]')) return;
  box.innerHTML = `<div class="mp-label-row"><span class="mp-label">Tasks</span><span data-meet-presence class="muted"></span><span class="grow"></span>
      <button class="mp-text" type="button" data-link-open>Link</button><button class="mp-text" type="button" data-add="task">${meetIcon('add', 'Add')}</button></div>
    <div data-link-slot></div>
    <div data-pending-panel><div data-pending-rows></div></div>
    <ul class="mp-tasks" data-made-tasks></ul>
    <ul class="mp-tasks" data-linked-tasks></ul>
    <div data-add-slot></div>
    <div class="mp-empty" data-tasks-empty hidden>No tasks</div>
    <div data-dismissed></div>`;
}
function meetItemsRender(state, rec) {
  const box = $('#meet-sections');
  if (!box) return;
  meetTasksShell(box);
  const data = state.items?.[rec.id];
  if (data) meetPendingRender(state, rec, data);
  // The add and link forms are built when they open and kept across repaints, with their text, focus and caret.
  const slot = box.querySelector('[data-add-slot]'), form = slot.querySelector('form[data-new]');
  if (state.itemAdd === 'task' && !form) {
    slot.innerHTML = `<form class="mp-form" data-new="task">
        <textarea data-text aria-label="What to add" placeholder="Add a task" rows="2"></textarea>
        <div class="mp-form-row"><select data-field="owner" aria-label="Owner">${taskOwnerOptions('')}</select><span class="grow"></span>
          <button class="ghost" type="button" data-cancel>Cancel</button><button class="primary" type="submit">Add</button></div></form>`;
  } else if (state.itemAdd !== 'task' && form) slot.innerHTML = '';
  const linkSlot = box.querySelector('[data-link-slot]'), linkForm = linkSlot.querySelector('form[data-link-form]');
  if (state.linkOpen && !linkForm) {
    linkSlot.innerHTML = `<form class="mp-form mp-form-row" data-link-form><input type="text" data-link-ref aria-label="Task to link" placeholder="#123 or task link">
        <button class="ghost" type="button" data-cancel>Cancel</button><button class="primary" type="submit">Link</button></form>`;
  } else if (!state.linkOpen && linkForm) linkSlot.innerHTML = '';
  meetDismissedRender(state, rec, data);
  meetItemsHandlers(state, rec);
  meetLinkedTasksRender(state, rec.id);
}
// Dismissed proposals stay out of the way, behind one toggle.
function meetDismissedRender(state, rec, data) {
  const box = $('#meet-sections [data-dismissed]'); if (!box) return;
  const gone = (data?.sections?.task || []).filter(it => it.status === 'dismissed' && !$(`#meet-sections [data-pending="${it.id}"]`));
  const open = !!state.showDismissed;
  const html = gone.length ? `<button class="mp-text mp-dismissed-toggle" type="button" data-show-dismissed aria-expanded="${open}">Dismissed (${gone.length})</button>
    ${open ? `<ul class="mp-tasks mp-gone">${gone.map(it => `<li>${meetTaskRowHTML({title: it.text, who: it.updated_by ? meetAuthor(it.updated_by) : ''})}</li>`).join('')}</ul>` : ''}` : '';
  if (box.innerHTML !== html) box.innerHTML = html;
  const toggle = box.querySelector('[data-show-dismissed]');
  if (toggle) toggle.onclick = () => { state.showDismissed = !state.showDismissed; meetDismissedRender(state, rec, state.items?.[rec.id]); };
}
function meetMadeRender(state, id) {
  const list = $('#meet-sections [data-made-tasks]'); if (!list) return;
  const linked = new Set((state.linkedTasks?.[id]?.tasks || []).map(t => t.task_id));
  const made = meetMadeTasks(state, meetRecFor(state, id)).filter(t => !t.id || !linked.has(t.id));
  const html = made.map(t => `<li${t.id ? ` data-made-task="${esc(t.id)}"` : ''}>${meetTaskRowHTML(t)}</li>`).join('');
  if (list.innerHTML !== html) list.innerHTML = html;
  const box = $('#meet-sections'), empty = box.querySelector('[data-tasks-empty]');
  if (empty) empty.hidden = !!(made.length || linked.size || box.querySelector('[data-pending-rows]')?.children.length || box.querySelector('form[data-new]'));
}
function meetItemsHandlers(state, rec) {
  const box = $('#meet-sections'), id = rec.id;
  if (!box) return;
  const repaint = () => meetItemsRender(state, rec);
  box.querySelector('[data-add]').onclick = () => {
    state.itemAdd = state.itemAdd === 'task' ? '' : 'task'; state.linkOpen = false; repaint();
    box.querySelector('form[data-new] [data-text]')?.focus();
  };
  box.querySelector('[data-link-open]').onclick = () => {
    state.linkOpen = !state.linkOpen; state.itemAdd = ''; repaint();
    box.querySelector('[data-link-ref]')?.focus();
  };
  const form = box.querySelector('form[data-new]');
  if (form) {
    form.querySelector('[data-cancel]').onclick = () => { state.itemAdd = ''; repaint(); };
    form.onsubmit = async ev => {
      ev.preventDefault();
      const text = form.querySelector('[data-text]').value.trim();
      if (!text) return;
      const detail = {};
      form.querySelectorAll('[data-field]').forEach(f => { if (f.value.trim()) detail[f.dataset.field] = meetFieldValue(f); });
      form.querySelectorAll('button').forEach(b => b.disabled = true);
      try {
        await post(`/meetings/${encodeURIComponent(id)}/items`, {section: 'task', text, detail});
        state.itemAdd = ''; repaint(); toast('Added');
        await meetItemsLoad(state, id);
      } catch (e) { toast(e.message, true); form.querySelectorAll('button').forEach(b => b.disabled = false); }
    };
  }
  const link = box.querySelector('form[data-link-form]');
  if (link) {
    link.querySelector('[data-cancel]').onclick = () => { state.linkOpen = false; repaint(); };
    link.onsubmit = async ev => {
      ev.preventDefault();
      const raw = link.querySelector('[data-link-ref]').value.trim();
      const ref = (raw.match(/#\/task\/([^/?#]+)/) || [])[1] || raw.replace(/^#/, '');
      if (!ref) return;
      link.querySelectorAll('button').forEach(b => b.disabled = true);
      try {
        const reply = await post(`/v2/meetings/${encodeURIComponent(id)}/tasks`, {task_ids: [decodeURIComponent(ref)]});
        if (reply?.results?.[0]?.status === 404) throw new Error('Task not found');
        state.linkOpen = false; repaint(); toast('Linked');
        meetLinkedTasksLoad(state, id);
      } catch (e) { toast(e.message, true); link.querySelectorAll('button').forEach(b => b.disabled = false); }
    };
  }
}
async function meetItemsLoad(state, id) {
  const sequence = state.itemLoadSequence = (state.itemLoadSequence || 0) + 1;
  getForget();
  let data;
  try { data = await get(`/meetings/${encodeURIComponent(id)}/items`); }
  catch { return null; }                        // a meeting that went away takes its items with it
  if (MEET !== state || sequence !== state.itemLoadSequence) return null;
  state.items = {...(state.items || {}), [id]: data};
  if (state.open && state.selected === id) meetItemsRender(state, meetRecFor(state, id));
  return data;
}
function meetLinkedTasksRender(state, id) {
  if (MEET !== state || !state.open || state.selected !== id) return;
  const slot = $('[data-linked-tasks]');
  if (!slot) return;
  const linked = state.linkedTasks?.[id];
  const mayEdit = meetRecFor(state, id).can_edit !== false;
  const held = new Map([...slot.querySelectorAll('[data-linked-task]')].map(row => [row.dataset.linkedTask, row]));
  for (const task of linked?.tasks || []) {
    let row = held.get(task.task_id);
    held.delete(task.task_id);
    if (!row) { row = document.createElement('li'); row.dataset.linkedTask = task.task_id; }
    const html = meetTaskRowHTML({id: task.task_id, number: task.number, title: task.title, status: task.status, unlink: mayEdit});
    if (row.innerHTML !== html) row.innerHTML = html;
    const unlink = row.querySelector('[data-unlink]');
    if (unlink) unlink.onclick = async () => {
      unlink.disabled = true;
      try { await writeRequest('DELETE', `/v2/meetings/${encodeURIComponent(id)}/tasks/${encodeURIComponent(task.task_id)}`); meetLinkedTasksLoad(state, id); }
      catch (e) { toast(e.message, true); unlink.disabled = false; }
    };
    slot.append(row);
  }
  for (const row of held.values()) row.remove();
  let more = $('[data-linked-more]', slot.parentElement);
  if (!linked?.next_cursor) { more?.remove(); more = null; }
  else {
    if (!more) { more = document.createElement('button'); more.type = 'button'; more.className = 'mp-text'; more.dataset.linkedMore = ''; more.textContent = 'More'; }
    slot.after(more);
  }
  if (more) more.onclick = () => { more.disabled = true; meetLinkedTasksLoad(state, id, linked.next_cursor); };
  meetMadeRender(state, id);
}
async function meetLinkedTasksLoad(state, id, cursor) {
  const changes = new Map();
  (state.linkedTaskChanges ||= {})[id] = changes;
  try {
    const target = cursor ? 0 : Math.max(20, state.linkedTasks?.[id]?.tasks?.length || 0);
    const previous = cursor ? state.linkedTasks?.[id]?.tasks || [] : [];
    const rows = new Map(previous.map(task => [task.task_id, task]));
    let next = cursor, result;
    do {
      const query = new URLSearchParams({limit: '20'});
      if (next) query.set('cursor', next);
      result = await get(`/v2/meetings/${encodeURIComponent(id)}/tasks?${query}`);
      if (MEET !== state || state.linkedTaskChanges[id] !== changes) return;
      for (const task of result.tasks || []) rows.set(task.task_id, task);
      next = result.next_cursor;
    } while (!cursor && next && rows.size < target);
    for (const [tid, task] of changes) {
      if (task) rows.set(tid, task);
      else rows.delete(tid);
    }
    state.linkedTasks = {...state.linkedTasks, [id]: {...result, tasks: [...rows.values()]}};
    meetLinkedTasksRender(state, id);
  } catch (e) {
    if (MEET === state && state.selected === id) {
      const more = $('[data-linked-more]');
      if (more) more.disabled = false;
      if (cursor) toast(e.message, true);
    }
  }
}
// The task event carries only links whose meeting and task this viewer can both read.
liveOn('tasks', event => {
  const state = MEET, id = state?.selected;
  if (!state?.open || !id) return;
  const linked = state.linkedTasks?.[id];
  const change = event.meeting_links?.find(link => link.meeting_id === id);
  const existing = linked?.tasks?.find(task => task.task_id === event.id);
  const changes = (state.linkedTaskChanges ||= {});
  const pending = (changes[id] ||= new Map());
  const known = existing || pending.get(event.id);
  if (event.bulk || ((event.kind === 'meetings' || event.kinds?.includes('meetings')) && !event.meeting_links)) {
    liveSoon('meeting-tasks:' + id, () => { if (MEET === state && state.open && state.selected === id) meetLinkedTasksLoad(state, id); }, 200);
    return;
  }
  if (!change && !known) return;
  if (event.gone || (change && !change.link)) pending.set(event.id, null);
  else if (event.task) {
    pending.set(event.id, {...(change?.link || known), task_id: event.id, number: event.task.number,
      title: String(event.task.title || '').slice(0, 200), status: event.task.status});
  } else return;
  const rows = new Map((linked?.tasks || []).map(task => [task.task_id, task]));
  for (const [tid, task] of pending) { if (task) rows.set(tid, task); else rows.delete(tid); }
  state.linkedTasks = {...state.linkedTasks, [id]: {...linked, tasks: [...rows.values()]}};
  meetLinkedTasksRender(state, id);
});
liveOn('reset', () => {
  if (!MEET?.open || !MEET.selected) return;
  MEET.linkedTaskChanges = {};
  meetLinkedTasksLoad(MEET, MEET.selected);
});
function meetItemsWire(state, rec) {
  if (!$('#meet-sections')) return;
  meetItemsRender(state, rec);
  meetItemsLoad(state, rec.id);
  meetSharedStart(state, rec.id);
  meetLinkedTasksLoad(state, rec.id);
}

// Click the title to rename it, Enter or blur saves, Escape keeps the old name (owner only).
function meetRenameable(state, rec, title, repaint) {
  if (!title) return;
  const id = rec.id;
  const rename = () => {
    if (!title.isConnected) return;
    const input = document.createElement('input');
    input.type = 'text'; input.className = 'meet-title'; input.value = rec.title || ''; input.setAttribute('aria-label', 'Title');
    title.replaceWith(input); input.focus(); input.select();
    let done = false;
    const finish = async save => {
      if (done) return; done = true;
      const value = input.value.trim();
      if (save && value && value !== (rec.title || '')) {
        try {
          const d = await post(`/meetings/${encodeURIComponent(id)}/edit`, {title: value, ...(S.me?.cloud ? {version: rec.version} : {})});
          rec.title = value; if (d?.version !== undefined) rec.version = d.version; toast('Renamed');
          const row = state.list.find(r => r.id === id); if (row) row.title = value;
        } catch (e) { toast(e.message, true); }
      }
      if (MEET === state && state.selected === id) repaint();
    };
    input.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); finish(true); } if (ev.key === 'Escape') finish(false); };
    input.onblur = () => finish(true);
  };
  title.setAttribute('data-editable', ''); title.setAttribute('tabindex', '0'); title.title = 'Click to rename';
  title.onclick = rename;
  title.onkeydown = ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); rename(); } };
}

// Pending items keep text at the root and their editable details under detail.
function meetPendingDraft(state, id) {
  state.pendingDrafts ||= new Map();
  if (!state.pendingDrafts.has(id)) state.pendingDrafts.set(id, sharedDraft());
  return state.pendingDrafts.get(id);
}
const meetPendingPatch = (draft, key, value) => sharedDraftPatch(draft, key, value, key === 'text' ? '' : 'detail');
const meetPendingMerge = sharedDraftMerge;
async function meetPendingSave(state, rid, iid) {
  return sharedDraftSave(meetPendingDraft(state, iid), {
    write: (patch, version) => post(`/meetings/${encodeURIComponent(rid)}/items/${encodeURIComponent(iid)}`, {...patch, version}).then(r => r.item),
    current: error => error.body?.error?.item,
    settled: () => meetItemsLoad(state, rid)
  });
}
function meetPendingRender(state, rec, data) {
  const panel = $('#meet-sections [data-pending-panel]');
  if (!panel) return;
  if (!panel.firstElementChild) panel.innerHTML = '<div data-pending-rows></div>';
  const list = panel.querySelector('[data-pending-rows]');
  const pending = (data.sections?.task || []).filter(it => {
    const draft = meetPendingDraft(state, it.id);
    if (it.status === 'proposed') return true;
    if (draft.sending && it.updated_by === 'human:' + S.me?.id) return false;
    if (!draft.closedAcknowledged && (draft.closed || draft.flight || Object.keys(draft.dirty).length)) {
      clearTimeout(draft.timer);
      // Capture before a late save reply can replace the draft's base or dirty fields.
      draft.closed ||= meetPendingMerge(meetPendingMerge(draft.base || it, draft.inflightPatch || {}), draft.patch);
      return true;
    }
    return false;
  });
  for (const node of [...list.children]) if (!pending.some(it => it.id === node.dataset.pending)) node.remove();
  panel.hidden = !pending.length;
  for (const item of pending) {
    const draft = meetPendingDraft(state, item.id);
    const server = draft.base?.version > item.version ? draft.base : item;
    if (!draft.flight && !draft.sending && !Object.keys(draft.dirty).length) { draft.version = server.version; draft.base = server; }
    let node = [...list.children].find(row => row.dataset.pending === item.id);
    if (item.status !== 'proposed') {
      if (!node) {
        node = document.createElement('section'); node.className = 'mp-prop mp-closed'; node.dataset.pending = item.id; list.append(node);
      }
      const text = [draft.closed.text, ...Object.values(draft.closed.detail || {})].filter(Boolean).join('\n\n');
      node.innerHTML = `<div class="mp-sub">${esc(item.status === 'pushed' ? 'Sent by ' : 'Dismissed by ')}${esc(meetAuthor(item.updated_by))}</div><pre class="selectable"></pre><div class="mp-form-row"><span class="grow"></span><button type="button" class="ghost" data-copy>Copy</button><button type="button" class="ghost" data-close>Close</button></div>`;
      node.querySelector('pre').textContent = text;
      const close = () => { draft.closedAcknowledged = true; draft.closed = null; draft.patch = {}; draft.dirty = {}; node.remove(); panel.hidden = !list.children.length; };
      node.querySelector('[data-copy]').onclick = async () => { try { await copyText(text); close(); } catch (e) { toast(e.message, true); } };
      node.querySelector('[data-close]').onclick = close;
      continue;
    }
    if (!node) {
      node = document.createElement('section'); node.className = 'mp-prop'; node.dataset.pending = item.id;
      node.innerHTML = `<span class="mp-check mp-check-new" aria-hidden="true"></span><div class="mp-prop-body">
        <textarea class="mp-prop-title" data-pending-field="text" aria-label="Title" rows="1"></textarea>
        <textarea class="mp-prop-detail" data-pending-field="body" aria-label="Details" rows="1" placeholder="Details"></textarea>
        <div class="mp-form-row"><select data-pending-field="owner" aria-label="Owner"></select><input data-pending-field="due" type="date" aria-label="Due">
          <span class="grow"></span><button type="button" class="ghost" data-dismiss>Dismiss</button><button type="button" class="primary" data-send>Create</button></div>
        <div class="mp-sub"><span data-edited></span><span class="err" data-save-error hidden>Not saved · <button type="button" class="linkish" data-retry>Retry</button></span></div>
        <div class="mp-sub err" data-owner-error role="alert" hidden></div>
        <div class="mp-sub" data-pending-duplicate hidden></div></div>`;
      list.append(node);
      for (const field of node.querySelectorAll('[data-pending-field]')) {
        const changed = () => {
          const key = field.dataset.pendingField;
          const value = key === 'due' && field.value ? new Date(field.value + 'T12:00:00').toISOString() : field.value;
          meetPendingPatch(draft, key, value);
          if (key === 'owner' && draft.errorCode === 'owner') { draft.error = ''; draft.errorCode = ''; }
          clearTimeout(draft.timer);
          if (!draft.error) draft.timer = setTimeout(() => meetPendingSave(state, rec.id, item.id), 700);
        };
        field.oninput = changed;
        field.onchange = () => { if (!draft.error) meetPendingSave(state, rec.id, item.id); };
        field.onblur = () => { if (!draft.error) meetPendingSave(state, rec.id, item.id); };
      }
      node.querySelector('[data-retry]').onclick = () => meetPendingSave(state, rec.id, item.id);
      const finish = async (send, force = false) => {
        if (draft.sending) return;
        draft.sending = true; clearTimeout(draft.timer);
        node.querySelectorAll('button, input, textarea, select').forEach(field => field.disabled = true);
        try {
          if (draft.flight) { try { await draft.flight; } catch {} if (draft.error) throw new Error(draft.error); }
          const proposal = node._item?.section === 'task' && node._item.proposed_task_id;
          if (send && proposal && Object.keys(draft.patch).length) {
            const patch = draft.patch;
            const saved = await post(`/meetings/${encodeURIComponent(rec.id)}/items/${encodeURIComponent(item.id)}`, {...patch, version: draft.version});
            draft.patch = {};
            sharedDraftAccepted(draft, patch, saved.item);
          }
          const route = send ? (proposal ? '/link' : '/push') : '';
          const reply = await post(`/meetings/${encodeURIComponent(rec.id)}/items/${encodeURIComponent(item.id)}${route}`,
                     send ? (proposal ? {} : {...draft.patch, force, version: draft.version}) : {status: 'dismissed', version: draft.version});
          if (send && reply.pushed === false && Object.keys(draft.dirty).length) {
            draft.closed ||= meetPendingMerge(draft.base, draft.patch);
          } else {
            draft.patch = {}; draft.dirty = {}; draft.error = ''; draft.current = null; draft.closed = null;
          }
          await meetItemsLoad(state, rec.id);
        } catch (e) {
          draft.error = e.message;
          draft.errorCode = e.body?.error?.code || '';
          if (e.body?.error?.item) {
            const current = e.body.error.item;
            draft.current = current; draft.version = current.version; draft.base = current;
          }
          const duplicate = e.body?.error?.duplicate;
          if (duplicate) {
            const box = node.querySelector('[data-pending-duplicate]');
            box.hidden = false;
            box.innerHTML = `<a href="#/task/${encodeURIComponent(duplicate.id)}">${esc(duplicate.name)}</a> <button type="button" class="linkish">Send anyway</button>`;
            box.querySelector('button').onclick = () => finish(true, true);
          } else if (draft.errorCode !== 'owner') toast(e.message, true);
        }
        finally { draft.sending = false; node.querySelectorAll('button, input, textarea, select').forEach(field => field.disabled = false); }
        await meetItemsLoad(state, rec.id);
        const error = node.querySelector('[data-save-error]');
        if (error) error.hidden = !draft.error || draft.errorCode === 'owner';
      };
      node.querySelector('[data-send]').onclick = () => finish(true);
      node.querySelector('[data-dismiss]').onclick = () => finish(false);
    }
    node._item = item;
    const displayed = meetPendingMerge(meetPendingMerge(server, draft.inflightPatch || {}), draft.patch);
    for (const field of node.querySelectorAll('[data-pending-field]')) {
      const key = field.dataset.pendingField;
      if (draft.sending) continue;
      const value = key === 'text' ? displayed.text : displayed.detail[key] || '';
      if (key === 'owner') {
        const options = [...(S.people || []).map(p => ['human:' + p.id, p.name || p.id]),
                         ...activeEmps().map(b => ['bot:' + b.id, b.display_name])]
          .filter(([actor]) => !data.audience || data.audience.includes(actor));
        const html = '<option value="">Choose who this is for</option>' + options.map(([actor, name]) => `<option value="${esc(actor)}">${esc(name)}</option>`).join('');
        if (field.innerHTML !== html) field.innerHTML = html;
      }
      const next = key === 'due' ? String(value).slice(0, 10) : value;
      if (field.value !== next) {
        const selection = field === document.activeElement && typeof field.selectionStart === 'number' ? [field.selectionStart, field.selectionEnd] : null;
        field.value = next;
        if (selection) field.setSelectionRange(...selection);
      }
    }
    node.querySelector('[data-edited]').textContent = item.updated_by ? sharedEdited(item.updated_by, meetAuthor) : '';
    node.querySelector('[data-save-error]').hidden = !draft.error || draft.errorCode === 'owner';
    const ownerError = node.querySelector('[data-owner-error]');
    ownerError.hidden = draft.errorCode !== 'owner';
    ownerError.textContent = draft.errorCode === 'owner' ? draft.error : '';
    let conflict = node.querySelector('[data-current]');
    if (!conflict) { conflict = document.createElement('div'); conflict.className = 'mp-sub'; conflict.dataset.current = ''; node.querySelector('.mp-prop-body').append(conflict); }
    conflict.hidden = !draft.current;
    conflict.textContent = draft.current ? 'Current: ' + draft.current.text + ' · ' + Object.values(draft.current.detail || {}).join(' · ') : '';
    const owner = displayed.detail.owner;
    const proposal = item.section === 'task' && item.proposed_task_id;
    node.querySelector('[data-send]').textContent = proposal ? 'Link' : 'Create';
    node.querySelector('[data-send]').disabled = draft.sending || !(data.can_send ?? data.can_push) || (!proposal && !owner);
  }
}
function meetSharedStop(state) { sharedPlaceStop(state); }
function meetSharedStart(state, id) {
  sharedPlaceStart(state, {
    id, place: 'meeting:' + id, topic: 'meeting_items', filter: 'meeting',
    changed: () => meetItemsLoad(state, id),
    gone: () => { meetSharedStop(state); delete state.items[id]; meetShow(state, false); },
    present: words => { const el = $('#meet-sections [data-meet-presence]'); if (el) el.textContent = words; }
  });
}
