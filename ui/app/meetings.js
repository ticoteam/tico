/* ui/app/meetings.js — The Meetings page: list, detail, Add notes, sections and Push
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- meetings page (docs/meetings.md)
// Meeting transcripts and notes imported from other tools: a searchable table, a detail dialog
// with the transcript, the sections a human pushes to tasks, and an Import action.
let MEET = null;
function meetStop() {
  if (MEET) {
    clearInterval(MEET.poll); clearInterval(MEET.sourcesPoll);
    granolaStop(MEET);   // ui/app/meetings-granola.js
    MEET = null;
  }
  window.liveMeetingsStop?.();
}
async function pageNotes() {
  meetStop();
  const state = MEET = {review: 'live', checked: new Set(), pendingCount: 0, loadSeq: 0, filter: 'all', when: 'all', person: '', source: '', people: new Map(), selected: '', list: [], poll: 0,
    sourcesPoll: 0, items: {}, itemEdit: '', itemAdd: '', itemConfirm: '', itemDup: {},
    confirmDelete: '', editing: false, open: false};
  $('#main').innerHTML = `<div class="notes-head"><h1>Meetings</h1>
      <input type="search" id="notes-search" autocomplete="off" aria-label="Search meetings" placeholder="Search meetings">
      <div class="notes-head-actions"><button class="ghost" type="button" id="meet-live-now">Live now</button>
      <button class="primary" type="button" id="notes-manual">Add notes</button></div></div>
    <div class="meet-review-bar"><div class="meet-review-tabs" role="tablist" aria-label="Meeting views">
      <button type="button" role="tab" data-review="live" aria-selected="true">Shared</button>
      <button type="button" role="tab" data-review="pending" aria-selected="false">Pending <span id="meet-pending-count"></span></button>
      <button type="button" role="tab" data-review="dismissed" aria-selected="false">Dismissed</button></div>
      <button class="linkish" type="button" id="meet-settings">Settings</button></div>
    <div class="meet-review-help muted" id="meet-review-help" hidden></div>
    <div class="meet-review-bulk" id="meet-review-bulk" hidden></div>
    <section class="meet-granola" id="meet-granola" aria-label="Granola" hidden></section>
    <section class="meet-sources" id="meet-sources" aria-label="Sources"></section>
    <div class="notes" id="notes">
      <section class="notes-list"><div class="notes-filters" id="notes-filters" hidden><select id="notes-when" aria-label="When"><option value="all">Any time</option><option value="today">Today</option><option value="7">Past 7 days</option><option value="30">Past 30 days</option><option value="older">Older than 30 days</option></select><select id="notes-person" aria-label="Participant"><option value="">All participants</option></select><select id="notes-source" aria-label="Source"><option value="">All sources</option></select><select id="notes-status" aria-label="Status"><option value="all">All statuses</option><option value="unsent">Not sent</option><option value="sent">Sent</option><option value="sending">Sending</option><option value="failed">Failed</option></select><span class="notes-count muted" id="notes-count"></span></div><div class="notes-rows" id="notes-rows"><div class="notes-empty">Loading…</div></div></section>
    </div>
    <dialog class="tmodal notes-modal" id="notes-modal" aria-label="Meeting details"><div class="notes-modal-close"><button class="ghost" type="button" id="notes-modal-close" aria-label="Close meeting">✕</button></div><section class="notes-detail" id="notes-detail"></section></dialog>
    <dialog class="tmodal import-modal" id="manual-modal" aria-label="Add notes"></dialog>`;
  document.querySelectorAll('[data-review]').forEach(button => button.onclick = () => {
    if (state.review === button.dataset.review) return;
    state.review = button.dataset.review; state.checked.clear(); state.selected = ''; state.people.clear();
    state.person = ''; state.source = ''; state.filter = 'all'; state.when = 'all';
    $('#notes-rows').innerHTML = '<div class="notes-empty">Loading…</div>';
    $('#meet-review-bulk').hidden = true; meetReviewTabs(state); meetLoad(state, true);
  });
  $('#meet-settings').onclick = () => meetSettingsOpen(state);
  $('#meet-live-now').onclick = () => pageLiveMeetings();
  const dialog = $('#notes-modal');
  const closeNote = () => {
    if (state.editing && !confirm('Discard unsaved changes?')) return;
    meetShow(state, false);
  };
  $('#notes-modal-close').onclick = closeNote;
  dialog.oncancel = e => { e.preventDefault(); closeNote(); };
  dialog.onclick = e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeNote(); } };
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
  granolaInit(state);   // the viewer's own Granola account: status, then one sync (ui/app/meetings-granola.js)
  await meetLoad(state, true);
  if (MEET !== state) return;
  const query = new URLSearchParams(S.route.split('?')[1] || '');
  const linked = query.get('meeting') || query.get('recording');
  if (linked) { state.selected = linked; meetShow(state, true); meetDetail(state, linked); }
  state.poll = setInterval(() => { if (!state.editing && !state.open) meetLoad(state, false); }, 30000);
  state.sourcesPoll = setInterval(() => meetSources(state), 60000);
}

// Review stays personal until approval; counts are quiet and never enter Needs you.
function meetPendingBadge(count) {
  for (const el of document.querySelectorAll('[data-meet-pending]')) {
    el.textContent = count > 99 ? '99+' : String(count || ''); el.hidden = !count;
    el.setAttribute('aria-label', `${count} pending meetings`);
  }
}
function meetReviewTabs(state) {
  document.querySelectorAll('[data-review]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.review === state.review)));
  const count = $('#meet-pending-count'); if (count) count.textContent = state.pendingCount || '';
  const help = $('#meet-review-help');
  if (help) { help.hidden = state.review === 'live'; help.textContent = state.review === 'pending'
    ? 'Only you can see these meetings. Review before sharing.' : 'Only you can see dismissed meetings. Restore to review them again.'; }
}
function meetReviewBulk(state) {
  const el = $('#meet-review-bulk'); if (!el) return;
  const visible = meetVisible(state), selected = visible.filter(r => state.checked.has(r.id)).length;
  el.hidden = state.review !== 'pending' || !visible.length;
  el.innerHTML = `<label><input type="checkbox" id="meet-select-all" aria-label="Select visible meetings"${visible.length && visible.every(r => state.checked.has(r.id)) ? ' checked' : ''}> Select all</label>
    <span class="muted">${selected ? `${selected} selected` : `${visible.length} visible`}</span><span class="spacer"></span>
    <button class="primary" type="button" data-review-bulk="approve_all">${selected ? 'Share selected' : 'Share visible'}</button>
    <button class="ghost" type="button" data-review-bulk="dismiss_all">${selected ? 'Dismiss selected' : 'Dismiss visible'}</button>`;
  $('#meet-select-all').onchange = e => { for (const r of visible) e.target.checked ? state.checked.add(r.id) : state.checked.delete(r.id); meetList(state); };
  el.querySelectorAll('[data-review-bulk]').forEach(b => b.onclick = () => meetReviewAct(state, b, b.dataset.reviewBulk));
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
  if (!id && !visible.length) return;
  state.reviewBusy = true; button.disabled = true;
  const body = {action};
  if (!id) body.ids = selected.length ? selected : visible;
  const privacy = id && button.closest('#notes-detail')?.querySelector('#meet-review-private');
  if (action === 'approve' && privacy) body.private = privacy.value === 'private';
  try {
    await post(id ? `/v2/meetings/${encodeURIComponent(id)}/review` : '/v2/meetings/review', body);
    if (MEET !== state) return;
    state.checked.clear();
    if (state.open && (!id || state.selected === id)) meetShow(state, false);
    await meetLoad(state, true);
    toast(action.startsWith('approve') ? 'Shared' : action === 'restore' ? 'Restored to Pending' : 'Dismissed');
  } catch (e) { toast(e.message, true); }
  finally { state.reviewBusy = false; button.disabled = false; }
}
function meetReviewDetail(rec) {
  return `<div class="note-send meet-review-detail"><span class="hint">${rec.review_state === 'pending' ? 'Pending · Only you can see this meeting.' : 'Dismissed · Only you can see this meeting.'}</span>
    ${rec.review_state === 'pending' ? `<select id="meet-review-private" aria-label="Visibility after approval"><option value="team"${!rec.private ? ' selected' : ''}>Team</option><option value="private"${rec.private ? ' selected' : ''}>Private</option></select>
    <button class="primary" type="button" data-review-action="approve" data-id="${esc(rec.id)}">Share</button><button class="ghost" type="button" data-review-action="dismiss" data-id="${esc(rec.id)}">Dismiss</button>`
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
function meetTile(state, source, big) {
  const row = (state.sourceRows || []).find(r => r.id === source.id);
  const known = Array.isArray(state.sourceRows);
  // Granola with account sign-in is per person: the tile shows the viewer's own connection and anyone can use it.
  const account = source.id === 'granola' && !!state.granola;
  const mine = account ? granolaTileState(state) : null;
  const st = mine || (known ? meetSourceState(row, source.id) : {key: '', word: ''});
  const when = mine ? (mine.when ? ago(mine.when) : '') : st.key === 'on' ? ago(row.last_import || row.last_success) : '';
  const label = `${source.name}: ${st.word || 'Connect'}${when ? (mine ? ', synced ' : ', last import ') + when : ''}`;
  const attrs = source.id === 'close' ? `href="${INTEGRATIONS}/close-crm"` : `type="button" data-msrc="${esc(source.id)}"${S.me?.role === 'owner' || account ? '' : ' disabled title="The owner connects sources"'}`;
  return `<${source.id === 'close' ? 'a' : 'button'} class="meet-tile${big ? ' big' : ''}" data-state="${st.key}" ${attrs} aria-label="${esc(label)}">
      ${meetLogo(source.id, big ? 40 : 30)}<span class="mt-text"><b>${esc(source.name)}</b><span class="mt-status">${st.key && st.key !== 'off' ? '<i class="dot" aria-hidden="true"></i>' : ''}${esc(st.word)}${when ? ` <span class="mt-when">${esc(when)}</span>` : ''}</span></span></${source.id === 'close' ? 'a' : 'button'}>`;
}
// With the viewer's own Granola row above the strip (ui/app/meetings-granola.js), the strip leaves Granola out: one place for it.
const meetTilesHTML = (state, big) => MEET_SOURCES.filter(s => big || s.id !== 'granola' || !state.granola).map(s => meetTile(state, s, big)).join('');
function meetWireTiles(state, root) {
  root.querySelectorAll('[data-msrc]').forEach(b => b.onclick = () => {
    const source = MEET_SOURCES.find(s => s.id === b.dataset.msrc);
    if (source.id === 'granola' && state.granola) return granolaFromTile(state);
    if (window.openMeetingImporter) window.openMeetingImporter(source.id, source.name, () => meetSources(state));
  });
}
// The strip under the header, and the large tiles in the empty state, from the same data.
function meetPaintTiles(state) {
  const strip = $('#meet-sources');
  if (strip) {
    strip.hidden = !state.loaded || !!state.empty;
    strip.innerHTML = `<div class="meet-sources-label">Sources</div><div class="meet-tiles">${meetTilesHTML(state, false)}</div>`;
    meetWireTiles(state, strip);
  }
  const big = document.querySelector('#notes-rows [data-meet-tiles]');
  if (big) { big.innerHTML = meetTilesHTML(state, true); meetWireTiles(state, big); }
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
const meetRecorder = r => r.recorded_by || (r.owner === 'owner' ? 'the owner' : r.owner) || 'someone';
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
    state.editing = false;
    state.itemEdit = ''; state.itemAdd = ''; state.itemConfirm = '';
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
// No meetings and nothing narrowing the list: offer both ways in.
const meetEmptyHTML = state => `<div class="meet-blank"><h2>Connect a source or add a note</h2>
    <div class="meet-tiles big" data-meet-tiles>${meetTilesHTML(state, true)}</div>
    <div class="meet-or">or</div>
    <button class="primary" type="button" data-add>Add notes</button></div>`;
const meetPill = r => r.delivery?.status === 'pending' ? '<span class="pill in-progress">sending</span>' : r.delivery?.status === 'error' ? '<span class="pill fail">delivery failed</span>' : r.status === 'error' ? '<span class="pill fail">failed</span>'
  : r.sent ? '<span class="pill ok">sent</span>' : '';
const meetPeople = r => {
  // An imported Close activity has no Tico invitees: the lead is who was on it.
  if (r.source === 'close') {
    const lead = r.source_context?.lead_name || r.source_context?.contact_name || '';
    const kind = r.source_type === 'meeting' ? 'Close meeting' : 'Close call';
    return `${esc(lead || kind)}<span class="note-sub">${kind}${r.source_context?.direction ? ' · ' + esc(r.source_context.direction) : ''}</span>`;
  }
  const names = meetPeopleList(r).map(p => p.name || p.id);
  return names.length ? esc(names.join(', ')) : '<span class="muted">None listed</span>';
};
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
// Who was in a meeting, for the list: a few avatars and up to two names.
function meetWhoHTML(r) {
  if (r.source === 'close') {
    const lead = r.source_context?.lead_name || r.source_context?.contact_name || '';
    return `<span class="meet-who">${esc(lead || (r.source_type === 'meeting' ? 'Close meeting' : 'Close call'))}</span>`;
  }
  const people = meetPeopleList(r);
  if (!people.length) return '';
  const names = people.map(p => firstName(p.name) || p.id);
  const text = names.slice(0, 2).join(', ') + (names.length > 2 ? ` +${names.length - 2}` : '');
  return `<span class="meet-who" title="${esc(people.map(p => p.name || p.id).join(', '))}"><span class="meet-faces">${people.slice(0, 3).map(p => personAvatar(p, 18)).join('')}</span>${esc(text)}</span>`;
}
const meetTasks = r => ((r.outbox || {}).task || []).filter(it => it.status !== 'dismissed');
const sentLink = s => s?.task
  ? `<a href="#/bot/${esc(s.slug)}/tasks">${esc(empName(s.slug))}</a>`
  : `<a href="${esc(s.url || `${GH}/issues/${s.issue}`)}" target="_blank" rel="noopener">${esc(empName(s.slug))}</a>`;
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
function noteOutcomeHTML(record, detail = false) {
  const out = record.outcome;
  if (!out) return '<span class="muted">No outcome reported yet.</span>';
  const labels = {open:'Awaiting review',doing:'In progress',waiting:'Waiting',done:'Reviewed',closed:'Closed',pending:'Sending',error:'Delivery failed',sent:'Sent',not_sent:'Not sent'};
  const counts = out.followups ? `<span class="note-sub">${out.followups} follow-ups · ${out.open_followups ? `${out.open_followups} outstanding` : 'all finished'}</span>` : '';
  const summary = detail
    ? `<div class="md" style="margin:6px 0;overflow-wrap:anywhere">${safeMd(out.summary || '')}</div>`
    : `<p class="note-outcome-line" title="${esc(plainMd(out.summary))}">${esc(meetOneLine(plainMd(out.summary)))}</p>`;
  return `<strong>${out.bot ? esc(empName(out.bot)) + ' · ' : ''}${esc(labels[out.status] || out.status)}</strong>${summary}${counts}${detail && out.task_id && out.bot ? `<a href="#/bot/${encodeURIComponent(out.bot)}/tasks">View bot tasks and follow-ups</a> ${taskConversationButton(out.task_id)}` : ''}`;
}
function meetList(state) {
  const el = $('#notes-rows'); if (!el) return;
  const list = meetVisible(state);
  const filtered = meetFiltersOn(state);
  state.loaded = true;
  state.empty = state.review === 'live' && !state.list.length && !filtered && !state.query;
  const bar = $('#notes-filters'); if (bar) bar.hidden = !(state.list.length || filtered);
  const count = $('#notes-count');
  if (count) count.textContent = state.list.length ? (list.length === state.list.length ? `${list.length} total` : `${list.length} of ${state.list.length}`) : '';
  el.innerHTML = list.length ? `<ul class="meet-list">${list.map(r => {
      const tasks = meetTasks(r), when = meetWhen(r.started || r.created);
      const meta = [when ? `<span>${esc(when)}</span>` : '', r.duration_ms ? `<span>${esc(mmss(r.duration_ms))}</span>` : '', meetWhoHTML(r)].filter(Boolean).join('');
      const fail = /fail/.test(meetPill(r)) ? meetPill(r) : '';
      return `<li class="meet-row review-${state.review}" data-note-row="${esc(r.id)}" title="${esc(meetSourceLabel(r.source))}">${state.review === 'pending' ? `<input type="checkbox" data-review-check="${esc(r.id)}" aria-label="Select ${esc(noteTitle(r))}"${state.checked.has(r.id) ? ' checked' : ''}>` : ''}${meetLogo(r.source || 'tico', 34)}
        <div class="meet-main"><button class="note-title" type="button" data-rec="${esc(r.id)}">${esc(noteTitle(r))}</button><div class="meet-meta">${meta}</div></div>
        <div class="meet-side">${state.review === 'pending' ? `<button class="primary" type="button" data-review-action="approve" data-id="${esc(r.id)}">Share</button><button class="ghost" type="button" data-review-action="dismiss" data-id="${esc(r.id)}">Dismiss</button>` : state.review === 'dismissed' ? `<button class="ghost" type="button" data-review-action="restore" data-id="${esc(r.id)}">Restore</button>` : ''}${fail}${tasks.length ? `<span class="meet-tasks" title="${esc(tasks.map(t => String(t.text || '').trim()).filter(Boolean).join('\n'))}">${tasks.length} ${tasks.length === 1 ? 'task' : 'tasks'}</span>` : ''}</div></li>`;
    }).join('')}</ul>`
    : state.empty ? meetEmptyHTML(state) : `<div class="notes-empty">${state.review === 'pending' && !state.query && !filtered ? 'No pending meetings. New imports wait here for your review.' : state.review === 'dismissed' && !state.query && !filtered ? 'No dismissed meetings.' : 'No meetings match.'}</div>`;
  el.querySelectorAll('[data-note-row]').forEach(b => b.onclick = event => {
    if (event.target.closest('[data-review-check], [data-review-action]')) return;
    if (meetWindow(b.dataset.noteRow)) return;
    state.selected = b.dataset.noteRow; state.confirmDelete = ''; state.editing = false;
    meetShow(state, true); meetDetail(state, state.selected);
  });
  meetReviewWire(state, el); meetReviewBulk(state);
  const add = el.querySelector('[data-add]'); if (add) add.onclick = () => meetNotesOpen(state);
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
function meetPaint(state, rec) {
  const el = $('#notes-detail'); if (!el) return;
  const id = rec.id, turns = rec.turns || [], sent = rec.sent, typed = rec.kind === 'note';
  const outcome = rec.outcome && rec.outcome.status !== 'not_sent' ? rec.outcome : null;   // "Not sent" is said once, by the send bar
  // The server decides who may change a meeting; `can_edit: false` is somebody else's, open here to read.
  const mayEdit = rec.can_edit !== false;
  const reviewing = rec.review_state === 'pending' || rec.review_state === 'dismissed';
  const transcript = turns.length ? `<div class="note-transcript">${turns.map(t => `<p><span class="muted mono">[${mmss(t.start_ms)}]</span> ${t.speaker ? `<strong>${esc(t.speaker)}</strong> ` : ''}${esc(t.text)}</p>`).join('')}</div>`
    : rec.transcript_readable ? `<div class="md">${safeMd(rec.transcript_readable)}</div>`
    : '<div class="empty">No transcript yet.</div>';
  const alternateTranscripts = (rec.transcript_sources || []).filter(t => !t.primary).map(t =>
    `<details data-note-section="source-${t.id}"><summary>${esc(meetSourceLabel(t.source))} ${esc(t.resource_type)} transcript${t.transcript_index ? ` ${t.transcript_index + 1}` : ''}</summary>
      ${t.summary ? `<div class="note-body">${esc(t.summary).replace(/\n/g, '<br>')}</div>` : ''}
      <div class="note-transcript">${(t.turns || []).map(turn => `<p><span class="muted mono">[${mmss(turn.start_ms)}]</span> ${turn.speaker ? `<strong>${esc(turn.speaker)}</strong> ` : ''}${esc(turn.text)}</p>`).join('')}</div></details>`).join('');
  const options = `<option value="auto">Auto — ${esc(assistantName())} routes it</option>` + activeEmps().map(e => `<option value="${esc(e.name)}">${esc(e.display_name)}</option>`).join('');
  const owner = rec.owner === 'owner' ? 'the owner' : (rec.owner || 'the sender');
  const meta = [meetWhen(rec.started), rec.duration_ms ? mmss(rec.duration_ms) : '', owner, meetSourceLabel(rec.source)].filter(Boolean).map(esc).join(' · ');
  const leadLink = rec.source === 'close' && /^https:\/\//i.test(rec.source_context?.lead_url || '')
    ? ` <span class="sep">·</span> <a href="${esc(rec.source_context.lead_url)}" target="_blank" rel="noopener">Open in Close</a>` : '';
  // Only an https link is drawn as one; the server refuses anything else, and this keeps a bad row inert.
  const mediaLink = /^https:\/\//i.test(rec.media_url || '')
    ? ` <span class="sep">·</span> <a href="${esc(rec.media_url)}" target="_blank" rel="noopener noreferrer">Open the recording</a>` : '';
  // the content itself, no headings: a typed note is its text; a meeting is its summary, with any
  // log you added under it. Editing, adding a log and the transcript are quiet links below.
  const writeup = rec.notes ? `<details data-note-section="summary"${state.sections?.[id]?.summary ? ' open' : ''}><summary>Summary</summary><div class="md">${safeMd(rec.notes)}</div></details>`
    : '';
  const voiceText = rec.voice_text ? `<div class="note-body"><span class="lbl">Voice transcript</span>${esc(rec.voice_text)}</div>` : '';
  const noteText = voiceText + (rec.note ? `<div class="note-body${typed ? '' : ' aside'}">${typed ? '' : '<span class="lbl">Your log</span>'}${esc(rec.note)}</div>`
    : typed && !voiceText ? '<div class="muted">Nothing logged yet.</div>' : '');
  // Private is the owner's switch: on, only its participants and the owner can open it;
  // off, everyone signed in can.
  const privateLink = typed || reviewing ? ''
    : mayEdit ? `<button class="linkish" type="button" id="meet-private">${rec.private ? 'Private — make it visible to the team' : 'Make this meeting private'}</button>`
    : rec.private ? '<span class="muted">Private: participants, its owner and the team owner only</span>' : '';
  const content = state.editing
    ? `<div class="note-content note-edit"><textarea id="meet-note" aria-label="Meeting log" placeholder="${typed ? 'What happened in this meeting?' : 'A log to go with this meeting'}">${esc(rec.note || '')}</textarea>
       <div class="row" style="gap:8px;margin-top:8px"><button class="primary" type="button" id="meet-save">Save</button><button class="ghost" type="button" id="meet-cancel">Cancel</button><span class="muted" id="meet-editmsg"></span></div></div>`
    : `<div class="note-content">${typed ? noteText : writeup + noteText}</div>
       <div class="note-links">${mayEdit ? `<button class="linkish" type="button" id="meet-edit">${rec.note ? 'Edit log' : typed ? 'Write the log' : 'Add a log'}</button>` : ''}${typed ? privateLink : ''}
         ${rec.attachments?.length ? rec.attachments.map(f => S.me?.cloud && f.id
           ? `<a class="pill" href="${API}/v2/files/${encodeURIComponent(f.id)}" download>${esc(f.name)}</a>`
           : `<span class="pill">${esc(typeof f === 'string' ? f : f.name || 'Attachment')}</span>`).join('') : ''}
         ${!typed || turns.length ? `<details data-note-section="transcript"${state.sections?.[id]?.transcript ? ' open' : ''}><summary>Transcript · ${esc(meetSourceLabel(rec.source))}</summary>${transcript}</details>${alternateTranscripts}` : ''}
       </div>`;
  const delBtn = mayEdit ? `<button class="ghost danger" type="button" id="meet-del">${state.confirmDelete === id ? 'Really delete?' : 'Delete'}</button>` : '';
  // What was said and written on the left, the sections on the right. The outcome is only a
  // section once there is one; until then the send row says so.
  const body = typed || reviewing ? content
    : `<div class="meet-done-cols"><section class="meet-done-main">${content}<div class="meet-comments" id="live-comments"></div></section>
       <section class="meet-live-items"><section class="meet-sections" id="meet-sections"></section></section></div>`;
  el.innerHTML = `<div class="note-head"><div>
      ${state.editing
        ? `<input type="text" id="meet-title" aria-label="Title" value="${esc(rec.title || '')}" placeholder="Title">`
        : `<h2 id="meet-heading">${esc(noteTitle(rec))}</h2>`}
      <div class="meta">${meta}</div>${!typed ? `<div class="meta">Participants: ${meetPeople(rec)}${leadLink}${mediaLink}${privateLink ? ` <span class="sep">·</span> ${privateLink}` : ''}</div>` : ''}
    </div>${meetPill(rec)}</div>
    ${rec.error ? `<div class="err" style="margin-top:12px">${esc(rec.error)}</div>` : ''}
    ${rec.warning ? `<div class="muted" style="font-size:12.5px;margin-top:8px">${esc(rec.warning)}</div>` : ''}
    ${rec.meeting_context ? `<details class="note-content"><summary>Meeting context</summary><div class="md">${safeMd(rec.meeting_context)}</div></details>` : ''}
    ${outcome || sent ? `<section class="note-content note-outcome"><h3>Outcome</h3>${noteOutcomeHTML(rec, true)}</section>` : ''}
    ${rec.delivery?.status === 'pending' ? '<p class="hint">Saved. Delivery continues in the background.</p>' : ''}
    ${rec.delivery?.error ? `<p class="err">Delivery failed: ${esc(rec.delivery.error)}. Press Send to retry.</p>` : ''}
    ${body}
    ${reviewing ? meetReviewDetail(rec) : sent ? `<div class="note-send"><span>Sent to ${sentLink(sent)}${sent.picked_by === 'auto' ? ' <span class="muted">(the sender\'s own bot)</span>' : sent.picked_by === 'coo' ? ' <span class="muted">(Auto)</span>' : ''}.</span>
        <span class="spacer"></span>${delBtn}
        ${sent.reason ? `<div class="hint">${esc(sent.reason)}</div>` : ''}</div>`
    : !mayEdit ? `<div class="note-send"><span class="hint">${esc(meetRecorder(rec))} owns this meeting; only ${esc(meetRecorder(rec))} sends it on.</span></div>`
    : `<div class="note-send">
        <select id="meet-dest" aria-label="Which bot receives this">${options}</select>
        <button class="primary" type="button" id="meet-send">Send to bot</button>
        <button class="linkish" type="button" id="meet-more" style="font-size:12.5px;text-decoration:none">Add instructions</button>
        <span class="spacer"></span>${delBtn}
        <textarea id="meet-instructions" aria-label="Anything else for the bot" placeholder="Anything else the bot should know" hidden></textarea>
        <span class="hint" id="meet-msg">${outcome || sent ? '' : 'Not sent to a bot yet.'}</span></div>`}`;
  if (mayEdit && !state.editing) meetRenameable(state, rec, $('#meet-heading'), () => meetPaint(state, rec));
  if (!typed && !reviewing) meetComments(state, rec);
  if (reviewing) meetReviewWire(state, el);

  el.querySelectorAll('[data-note-section]').forEach(section => {
    section.ontoggle = () => {
      state.sections ||= {}; state.sections[id] ||= {};
      state.sections[id][section.dataset.noteSection] = section.open;
    };
  });
  state.sendDrafts ||= {};
  const draft = state.sendDrafts[id] ||= {destination:'auto',instructions:'',expanded:false};
  const destination = $('#meet-dest'), instructions = $('#meet-instructions');
  if (destination) {
    destination.value = draft.destination;
    destination.onchange = () => {draft.destination = destination.value;};
  }
  if (instructions) {
    instructions.value = draft.instructions; instructions.hidden = !draft.expanded;
    instructions.oninput = () => {draft.instructions = instructions.value;};
  }
  const more = $('#meet-more');
  if (more) more.hidden = draft.expanded;
  if (more) more.onclick = () => { draft.expanded = true; more.hidden = true; const t = $('#meet-instructions'); t.hidden = false; t.focus(); };
  const priv = $('#meet-private');
  if (priv) priv.onclick = async () => {
    const wanted = !rec.private;
    priv.disabled = true;
    try {
      const d = await post(`/meetings/${encodeURIComponent(id)}/edit`,
        {private: wanted, ...(S.me?.cloud ? {version: rec.version} : {})});
      toast(wanted ? 'Private: participants, its owner and the team owner only' : 'Visible to everyone signed in');
      await meetLoad(state, false);
      if (MEET === state && state.selected === id) meetPaint(state, {...rec, ...(d || {})});
    } catch (e) { toast(e.message, true); priv.disabled = false; }
  };
  const edit = $('#meet-edit');
  if (edit) edit.onclick = () => { state.editing = true; state.confirmDelete = ''; meetPaint(state, rec); const t = $('#meet-note'); if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); } };
  const cancel = $('#meet-cancel');
  if (cancel) cancel.onclick = () => { state.editing = false; meetPaint(state, rec); };
  const save = $('#meet-save');
  if (save) {
    save.onclick = async () => {
      const msg = $('#meet-editmsg'); save.disabled = true; msg.textContent = 'Saving…';
      try {
        const d = await post(`/meetings/${encodeURIComponent(id)}/edit`, {note: $('#meet-note').value.trim(), title: $('#meet-title').value.trim(),
          ...(S.me?.cloud ? {version:rec.version} : {})});
        state.editing = false; toast('Saved');
        await meetLoad(state, false); if (MEET === state && state.selected === id) meetPaint(state, {...rec, ...(d || {})});
      } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; save.disabled = false; }
    };
    const keys = ev => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') { ev.preventDefault(); save.click(); }
      if (ev.key === 'Escape') cancel.click();
    };
    $('#meet-note').onkeydown = keys; $('#meet-title').onkeydown = keys;
  }
  const del = $('#meet-del');
  if (del) del.onclick = async () => {
    if (state.confirmDelete !== id) { state.confirmDelete = id; del.textContent = 'Really delete?'; return; }
    del.disabled = true;
    try { await post(`/meetings/${encodeURIComponent(id)}/delete`); toast(S.me?.cloud ? 'Removed from the list; recoverable server history is retained.' : 'Deleted'); state.confirmDelete = ''; state.selected = ''; meetShow(state, false); await meetLoad(state, true); }
    catch (e) { toast(e.message, true); del.disabled = false; del.textContent = 'Delete'; }
  };
  const send = $('#meet-send');
  if (send) send.onclick = async () => {
    const msg = $('#meet-msg');
    send.disabled = true; msg.textContent = 'Sending…';
    try {
      toastSent(await post(`/meetings/${encodeURIComponent(id)}/send`, {slug: $('#meet-dest').value, instructions: $('#meet-instructions').value.trim()}));
      await refresh(true); await meetLoad(state, true);
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; send.disabled = false; }
  };
  if (!typed && !reviewing) meetItemsWire(state, rec);
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
      <label>Send to a bot (optional) <select id="manual-send"><option value="">Don't send</option><option value="auto">Auto — ${esc(assistantName())} routes it</option>${activeEmps().map(e => `<option value="${esc(e.name)}">${esc(e.display_name)}</option>`).join('')}</select></label>
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

// ----------------------------------------------------------------- the three sections and Push
// A meeting is an outbox a human empties: Doc updates, Tasks and Feature requests, each row with
// its quote and Edit / Delete / Push. Anyone who can read the meeting edits and deletes; Push is
// the meeting's owner only, which is what `can_push` says, and it makes a hub task.
const MEET_SECTIONS = [
  {key: 'doc', title: 'Doc updates', fields: [['document', 'Document, e.g. docs/pricing.md'], ['change', 'What it should say now'], ['why', 'Why']]},
  {key: 'task', title: 'Tasks', fields: [['owner', 'Owner — human:<id> or bot:<slug>'], ['due', 'Due — 2026-09-18T17:00:00-07:00'], ['priority', 'Priority — p0 to p3']]},
  {key: 'feature', title: 'Feature requests', fields: [['side', 'Product area (optional)'], ['app', 'App (optional)'], ['area', 'Label (optional)']]},
];
const meetAuthor = a => a === 'brain' ? 'Tico' : actorLabel(a);
// One line under the text: who owns a task and when, which document, which side of a feature.
const meetItemLine = it => {
  const d = it.detail || {};
  // A bare "ana" or "support" is a person's id or a bot's name: show the name, as everywhere else.
  const who = o => String(o).includes(':') ? actorLabel(o) : (S.people || []).find(p => p.id === o)?.name
    || (S.emps.some(e => e.name === o) ? botDisplayName(o) : o);
  if (it.section === 'task') return [d.owner ? who(d.owner) : 'No owner yet', d.due ? 'due ' + String(d.due).slice(0, 10) : '',
    /^p\d$/i.test(d.priority || '') ? 'priority ' + d.priority.toUpperCase() : d.priority || ''].filter(Boolean).join(' · ');
  if (it.section === 'doc') return [d.document || 'No document named', d.change].filter(Boolean).join(' — ');
  if (it.section === 'feature') return [({B: 'Backend', F: 'Frontend', 'B/F': 'Backend and frontend'})[d.side] || d.side, ({CA: 'Client app', PA: 'Pro app'})[d.app] || d.app, d.area, d.bug ? 'bug' : ''].filter(Boolean).join(' · ');
  return '';
};
// Where a pushed item ended up: the task it became, or a link.
const meetResultLink = it => {
  const ref = it.result_ref || '';
  if (!ref) return '<span class="muted">pushed</span>';
  if (/^https?:\/\//.test(ref)) return `<a href="${esc(ref)}" target="_blank" rel="noopener">the link</a>`;
  return `<a href="#/task/${encodeURIComponent(ref)}">the task</a>`;   // a hub task id opens it in full
};
// Clicking a quote jumps the transcript to the moment it was said, when that line is on the page.
function meetSeek(ms) {
  const box = $('#notes-detail');
  if (box === null || ms === null || ms === undefined) return;
  const stamp = '[' + mmss(ms) + ']';
  const line = [...box.querySelectorAll('.note-transcript p, .md p, .md li')].find(p => p.textContent.includes(stamp));
  if (!line) return;
  line.closest('details')?.setAttribute('open', '');
  line.scrollIntoView({block: 'center', behavior: 'smooth'});
  line.classList.add('meet-seeked');
  setTimeout(() => line.classList.remove('meet-seeked'), 1500);
}
function meetItemFields(section, detail) {
  const fields = (MEET_SECTIONS.find(s => s.key === section) || {fields: []}).fields;
  return fields.map(([name, label]) => {
    if (name === 'owner') {
      const current = (detail || {}).owner || '';
      return `<select data-field="owner" aria-label="${esc(label)}">${taskOwnerOptions(current)}</select>`;
    }
    return `<input type="text" data-field="${name}" placeholder="${esc(label)}" aria-label="${esc(label)}" value="${esc((detail || {})[name] || '')}">`;
  }).join('');
}
function meetItemHTML(state, it, canPush) {
  const id = it.id, dup = state.itemDup?.[id];
  if (state.itemEdit === id) {
    return `<div class="meet-item" data-item="${esc(id)}"><form data-edit="${esc(id)}">
        <textarea data-text aria-label="What this item says">${esc(it.text)}</textarea>
        ${meetItemFields(it.section, it.detail)}
        <div class="row"><button class="primary" type="submit">Save</button>
          <button class="ghost" type="button" data-cancel>Cancel</button></div></form></div>`;
  }
  const pill = it.status === 'pushed' ? `<span class="pill ok">pushed</span> <span class="muted">→ ${meetResultLink(it)}</span>`
    : it.status === 'dismissed' ? '<span class="pill">deleted</span>'
    : '<span class="pill">proposed</span>';
  const line = meetItemLine(it);
  const buttons = it.status === 'proposed' ? [
    `<button class="linkish" type="button" data-act="edit" data-id="${esc(id)}">Edit</button>`,
    `<button class="linkish" type="button" data-act="del" data-id="${esc(id)}">${state.itemConfirm === id ? 'Really delete?' : 'Delete'}</button>`,
    canPush ? `<button class="linkish" type="button" data-act="push" data-id="${esc(id)}">Push</button>` : '',
  ].filter(Boolean).join('') : '';
  const at = it.at_ms === null || it.at_ms === undefined ? '' : `<span class="at" data-seek="${it.at_ms}" title="Jump to this moment">${mmss(it.at_ms)}</span>`;
  return `<div class="meet-item${it.status === 'dismissed' ? ' gone' : ''}" data-item="${esc(id)}">
      <span class="t">${esc(it.text)}</span>${at}
      ${line ? `<span class="sub">${esc(line)}</span>` : ''}
      ${it.quote && it.quote.trim().toLowerCase() !== String(it.text || '').trim().toLowerCase() ? `<blockquote data-seek="${it.at_ms === null || it.at_ms === undefined ? '' : it.at_ms}" title="Jump to this moment in the transcript">${esc(it.quote)}</blockquote>` : ''}
      <div class="row">${pill}${buttons}<span class="sub" style="margin:0">${esc(meetAuthor(it.updated_by || it.created_by))}</span></div>
      ${dup ? `<div class="dup">Already on the board (${esc(STATUS_WORD[dup.list] || dup.list)}): <a href="${esc(dup.url)}" target="_blank" rel="noopener">${esc(dup.name)}</a>
        <button class="linkish" type="button" data-act="force" data-id="${esc(id)}">Push anyway</button></div>` : ''}</div>`;
}
function meetItemsRender(state, rec) {
  const box = $('#meet-sections');
  if (!box) return;
  const data = state.items?.[rec.id];
  if (!data) { box.innerHTML = ''; return; }
  const canPush = !!data.can_push;
  const sections = MEET_SECTIONS.map(({key, title}) => {
    const rows = data.sections?.[key] || [];
    const proposed = (data.sections?.[key] || []).filter(it => it.status === 'proposed').length;
    return `<section class="meet-sec" data-sec="${key}"><header><h4>${esc(title)}</h4>
        ${proposed ? `<span class="count">${proposed}</span>` : ''}
        ${canPush && proposed > 1 ? `<button class="linkish" type="button" data-all="${key}">Push all</button>` : ''}
        <button class="linkish" type="button" data-add="${key}">Add</button></header>
      ${rows.map(it => meetItemHTML(state, it, canPush)).join('') || '<div class="meet-empty">Nothing yet.</div>'}
      ${state.itemAdd === key ? `<div class="meet-item"><form data-new="${key}">
          <textarea data-text aria-label="What to add" placeholder="What should happen?"></textarea>
          ${meetItemFields(key, {})}
          <div class="row"><button class="primary" type="submit">Add</button>
            <button class="ghost" type="button" data-cancel>Cancel</button></div></form></div>` : ''}</section>`;
  }).join('');
  box.innerHTML = `<div class="cols">${sections}</div>`;
  meetItemsHandlers(state, rec);
  box.querySelectorAll('.at[data-seek]').forEach(b => { b.onclick = () => meetSeek(Number(b.dataset.seek)); });
}
function meetItemsHandlers(state, rec) {
  const box = $('#meet-sections'), id = rec.id;
  if (!box) return;
  const repaint = () => meetItemsRender(state, rec);
  const call = async (path, body, item = '') => {
    try {
      const reply = await post(`/meetings/${encodeURIComponent(id)}/items${path}`, body);
      state.itemDup = {...(state.itemDup || {})};
      delete state.itemDup[reply?.item?.id || item];
      await meetItemsLoad(state, id);
      return reply;
    } catch (e) {
      const duplicate = e.body?.error?.duplicate;
      if (duplicate) {
        state.itemDup = {...(state.itemDup || {}), [item]: duplicate};
        repaint();
      } else toast(e.message, true);
      return null;
    }
  };
  box.querySelectorAll('blockquote[data-seek]').forEach(q => q.onclick = () => meetSeek(q.dataset.seek === '' ? null : +q.dataset.seek));
  box.querySelectorAll('[data-add]').forEach(b => b.onclick = () => {
    state.itemAdd = state.itemAdd === b.dataset.add ? '' : b.dataset.add; state.itemEdit = ''; repaint();
    $('#meet-sections form[data-new] [data-text]')?.focus();
  });
  box.querySelectorAll('form[data-new]').forEach(form => {
    form.querySelector('[data-cancel]').onclick = () => { state.itemAdd = ''; repaint(); };
    form.onsubmit = async ev => {
      ev.preventDefault();
      const text = form.querySelector('[data-text]').value.trim();
      if (!text) return;
      const detail = {};
      form.querySelectorAll('[data-field]').forEach(f => { if (f.value.trim()) detail[f.dataset.field] = f.value.trim(); });
      state.itemAdd = '';
      if (await call('', {section: form.dataset.new, text, detail})) toast('Added');
      else repaint();
    };
  });
  box.querySelectorAll('form[data-edit]').forEach(form => {
    form.querySelector('[data-cancel]').onclick = () => { state.itemEdit = ''; repaint(); };
    form.onsubmit = async ev => {
      ev.preventDefault();
      const text = form.querySelector('[data-text]').value.trim();
      if (!text) return;
      const detail = {};
      form.querySelectorAll('[data-field]').forEach(f => { if (f.value.trim()) detail[f.dataset.field] = f.value.trim(); });
      state.itemEdit = '';
      if (await call('/' + encodeURIComponent(form.dataset.edit), {text, detail}, form.dataset.edit)) toast('Saved');
      else repaint();
    };
  });
  box.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const item = b.dataset.id, act = b.dataset.act;
    if (act === 'edit') { state.itemEdit = item; state.itemAdd = ''; state.itemConfirm = ''; repaint(); return; }
    if (act === 'del') {
      if (state.itemConfirm !== item) { state.itemConfirm = item; b.textContent = 'Really delete?'; return; }
      state.itemConfirm = '';
      b.disabled = true;
      if (await call('/' + encodeURIComponent(item), {status: 'dismissed'}, item)) toast('Deleted');
      return;
    }
    b.disabled = true;
    const done = await call('/' + encodeURIComponent(item) + '/push', {force: act === 'force'}, item);
    if (done) toast(done.pushed ? 'Pushed' : 'Already pushed');
    else b.disabled = false;
  });
  box.querySelectorAll('[data-all]').forEach(b => b.onclick = async () => {
    const section = b.dataset.all;
    b.disabled = true;
    for (const it of (state.items?.[id]?.sections?.[section] || []).filter(x => x.status === 'proposed')) {
      if (!await call('/' + encodeURIComponent(it.id) + '/push', {force: false}, it.id)) break;
    }
    b.disabled = false;
  });
}
async function meetItemsLoad(state, id) {
  let data;
  try { data = await get(`/meetings/${encodeURIComponent(id)}/items`); }
  catch { return null; }                        // a meeting that went away takes its sections with it
  if (MEET !== state) return null;
  state.items = {...(state.items || {}), [id]: data};
  if (state.open && state.selected === id) {
    const rec = {...(state.list.find(r => r.id === id) || {}), id};
    meetItemsRender(state, rec);
  }
  return data;
}
function meetItemsWire(state, rec) {
  if (!$('#meet-sections')) return;
  meetItemsRender(state, rec);
  meetItemsLoad(state, rec.id);
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
// The thread beside the meeting: anyone who can open it can add to it.
async function meetComments(state, rec, quiet) {
  const box = $('#live-comments'); if (!box) return;
  const T = (state.thread ||= {});
  T[rec.id] ||= [];
  if (!quiet || !box.querySelector('textarea:focus')) {
    let d = null;
    try { d = await get(`/meetings/${encodeURIComponent(rec.id)}/comments`); } catch { /* an old server: no thread */ }
    if (MEET !== state || state.selected !== rec.id || !$('#live-comments')) return;
    const next = d?.comments || [];
    if (quiet && next.length === T[rec.id].length && box.querySelector('.meet-comment-form')) return;
    T[rec.id] = next;
  }
  const draft = box.querySelector('textarea')?.value || '';
  box.innerHTML = `<h4>Comments</h4>
    ${T[rec.id].map(c => `<div class="meet-comment"><span class="who">${esc(c.author_name || c.author)}</span>${esc(c.text)}${c.at_ms !== null && c.at_ms !== undefined ? `<span class="when">${mmss(c.at_ms)}</span>` : ''}</div>`).join('') || '<div class="muted" style="font-size:12.5px">Nothing yet. Anyone who can open this meeting can add a comment.</div>'}
    <form class="meet-comment-form"><textarea aria-label="Add a comment" placeholder="Add a comment… (⏎ to post)">${esc(draft)}</textarea><button class="primary" type="submit">Post</button></form>`;
  const form = box.querySelector('form'), text = form.querySelector('textarea');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const value = text.value.trim(); if (!value) return;
    form.querySelector('button').disabled = true;
    try {
      await post(`/meetings/${encodeURIComponent(rec.id)}/comments`, {text: value});
      text.value = ''; await meetComments(state, rec, false);
    } catch (e) { toast(e.message, true); form.querySelector('button').disabled = false; }
  };
  text.onkeydown = ev => { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); } };
}
