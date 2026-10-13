/* ui/app/replay.js — Replay: play back what happened over a range (GET /api/v2/replay, backend/replay.py), and present it
   without the app around it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- replay
// The query rides in the hash (#/replay?from=..&to=..&speed=60&bot=..), so the link reopens the same replay. Nothing
// is stored: whoever opens a link sees only what they may see.
const REPLAY_RANGES = [['today', 'Today'], ['yesterday', 'Yesterday'], ['7d', '7 days'], ['custom', 'Custom']];
let REPLAY = null;
const replayDay = (offset = 0) => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + offset); return d; };
function replaySpan(range) {
  if (range === 'yesterday') return {from: replayDay(-1), to: replayDay(0)};
  if (range === '7d') return {from: new Date(Date.now() - 7 * 864e5), to: new Date()};
  return {from: replayDay(0), to: new Date()};
}
// <input type=datetime-local> speaks local wall time without a zone.
const replayLocal = iso => { const d = new Date(iso); return isNaN(d) ? '' : new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); };
const replayIso = local => { const d = new Date(local); return isNaN(d) ? '' : d.toISOString(); };
function replayScopeOptions(selected) {
  const opt = (value, label) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`;
  const groups = (S.orgGroups || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'planned' && e.status !== 'archived').sort((a, b) => botDisplayName(a.name).localeCompare(botDisplayName(b.name)));
  const people = (S.people || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  return opt('', 'Everyone')
    + (groups.length ? `<optgroup label="Teams">${groups.map(g => opt('team:' + g.id, g.name || g.id)).join('')}</optgroup>` : '')
    + (bots.length ? `<optgroup label="Bots">${bots.map(e => opt('bot:' + e.name, botDisplayName(e.name))).join('')}</optgroup>` : '')
    + (people.length ? `<optgroup label="People">${people.map(p => opt('human:' + p.id, p.name || p.id)).join('')}</optgroup>` : '');
}
function replayStop() {
  if (!REPLAY) return;
  REPLAY.player?.destroy();
  document.removeEventListener('keydown', REPLAY.onKey);
  document.body.classList.remove('rp-presenting');
  REPLAY = null;
}
const replayQuery = state => {
  const q = new URLSearchParams({from: state.from, to: state.to});
  if (state.scope) { const [kind, id] = state.scope.split(/:(.*)/); q.set(kind === 'human' ? 'person' : kind, id); }
  return q;
};
function replaySync(state) {
  const q = replayQuery(state); q.set('speed', String(state.speed));
  const hash = '#/replay?' + q.toString();
  if (location.hash !== hash) history.replaceState(null, '', hash);
  S.route = LAST_ROUTE = hash;
}

function pageReplay() {
  replayStop();
  const q = new URLSearchParams(S.route.split('?')[1] || '');
  const scope = q.get('bot') ? 'bot:' + q.get('bot') : q.get('person') ? 'human:' + q.get('person') : q.get('team') ? 'team:' + q.get('team') : '';
  const given = q.get('from') && q.get('to'), span = replaySpan('today');
  const state = REPLAY = {range: given ? 'custom' : 'today', from: given ? q.get('from') : span.from.toISOString(),
                          to: given ? q.get('to') : span.to.toISOString(), scope, speed: +q.get('speed') || 60,
                          data: null, player: null, load: 0, presenting: false};
  $('#main').innerHTML = `<div class="rp-page">
    <div class="rp-head">
      <h1>Replay</h1>
      <div class="use-seg" role="tablist" aria-label="Range">${REPLAY_RANGES.map(([k, label]) =>
        `<button type="button" role="tab" data-rp-range="${k}" aria-selected="${k === state.range}">${label}</button>`).join('')}</div>
      <span class="rp-custom" hidden><input type="datetime-local" id="rp-from" aria-label="From"><input type="datetime-local" id="rp-to" aria-label="To"></span>
      <select id="rp-scope" aria-label="Who">${replayScopeOptions(state.scope)}</select>
      <span class="spacer"></span>
      <div class="rp-actions">
        <button type="button" class="ghost rp-icon" id="rp-link" title="Copy link" aria-label="Copy link"><span class="nav-icon" aria-hidden="true">link</span></button>
        <button type="button" class="ghost" id="rp-present" aria-pressed="false">Present</button>
      </div>
    </div>
    <div id="rp-note" class="rp-note" hidden></div>
    <div id="rp-player">${skelRows(4, {card: true})}</div>
  </div>`;
  const main = $('#main');
  main.classList.add('replay-layout');
  main.querySelector('.use-seg').onclick = ev => {
    const b = ev.target.closest('[data-rp-range]'); if (!b) return;
    state.range = b.dataset.rpRange;
    if (state.range !== 'custom') { const s = replaySpan(state.range); state.from = s.from.toISOString(); state.to = s.to.toISOString(); }
    replayLoad(state);
  };
  $('#rp-from').onchange = $('#rp-to').onchange = () => {
    const f = replayIso($('#rp-from').value), t = replayIso($('#rp-to').value);
    if (f && t && f < t) { state.from = f; state.to = t; replayLoad(state); }
  };
  $('#rp-scope').onchange = ev => { state.scope = ev.target.value; replayLoad(state); };
  $('#rp-link').onclick = async () => {
    replaySync(state);
    try { await copyText(location.href); toast('Link copied'); } catch (e) { toast(e.message, true); }
  };
  $('#rp-present').onclick = () => replayPresent(state, !state.presenting);
  state.onKey = ev => {
    if (ev.key === 'Escape' && state.presenting) replayPresent(state, false);
    else if ((ev.key === 'p' || ev.key === 'P') && !ev.metaKey && !ev.ctrlKey && !/^(INPUT|SELECT|TEXTAREA)$/.test(ev.target?.tagName || '')) replayPresent(state, !state.presenting);
  };
  document.addEventListener('keydown', state.onKey);
  replayLoad(state);
}

// Presenting hides the page's bar, so its button moves into the player's transport bar and fades with it.
function replayPresent(state, on) {
  state.presenting = on;
  document.body.classList.toggle('rp-presenting', on);
  const button = $('#rp-present'); if (!button) return;
  button.setAttribute('aria-pressed', String(on));
  $('#rp-player')?.classList.toggle('rp-fade', on);
  const bar = $('#rp-player .rp-bar');
  if (on && bar) bar.appendChild(button); else $('#main .rp-actions')?.appendChild(button);
}

async function replayLoad(state) {
  const mine = ++state.load;
  for (const b of $('#main').querySelectorAll('[data-rp-range]')) b.setAttribute('aria-selected', String(b.dataset.rpRange === state.range));
  $('#main').querySelector('.rp-custom').hidden = state.range !== 'custom';
  $('#rp-from').value = replayLocal(state.from); $('#rp-to').value = replayLocal(state.to);
  $('#rp-scope').value = state.scope;
  replaySync(state);
  state.player?.destroy(); state.player = null; state.data = null;
  $('#rp-player').innerHTML = skelRows(4, {card: true});
  let data;
  try { data = await get('/v2/replay?' + replayQuery(state).toString()); }
  catch (e) { if (REPLAY === state && mine === state.load) $('#rp-player').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (REPLAY !== state || mine !== state.load) return;
  state.data = data;
  const note = $('#rp-note');
  note.hidden = !data.truncated && !!data.items.length;
  note.textContent = !data.items.length ? 'Nothing happened in this range' : data.truncated ? `First ${data.items.length.toLocaleString()} events, up to ${fmt(data.next)}` : '';
  const root = $('#rp-player');
  $('#main .rp-actions').appendChild($('#rp-present'));    // it may sit in the old player's bar
  root.innerHTML = '';
  state.player = window.TicoReplay.mount(root, data, {
    speed: state.speed, face: replayFace,
    name: replayName(data),
    onChange: s => { if (s.speed !== state.speed) { state.speed = s.speed; replaySync(state); } },
  });
  replayPresent(state, state.presenting);
}

// Real names throughout, the viewer's own too: a replay is watched and recorded, so "You" would read wrong.
const replayName = data => a => {
  const slug = actorSlug(a), pid = actorPerson(a);
  if (slug) return botDisplayName(slug);
  if (pid) return data.names?.[a] || (S.people || []).find(p => p.id === pid)?.name || personDisplay(pid);
  return a === 'keeper' ? assistantName() : String(a || '');
};

// The app's own faces: a bot's blob in the replayed state (never the live one), a person's photo or initials.
function replayFace(actor, size, state) {
  const slug = actorSlug(actor), pid = actorPerson(actor);
  if (slug) {
    const html = botAvatar(slug, size, state === 'running' ? 'running' : '');
    return state === 'running' ? html : html.replace(/ morph"/, '"').replace(/<animate [^>]*\/>/, '').replace(/ style="--d0:[^"]*"/, '');
  }
  if (pid) return personAvatar((S.people || []).find(p => p.id === pid) || {id: pid, name: personDisplay(pid)}, size);
  return personCircle(replayName({})(actor) || '?', size);
}
