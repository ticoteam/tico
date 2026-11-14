/* ui/app/replay.ts — Replay: play back what happened over a range (GET /api/v2/replay, backend/replay.py), and present it
   without the app around it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The replay on the page (REPLAY): its range and span, who it is for, the speed, and the data and player once loaded.
interface ReplayState {
  range: string;
  from: string;
  to: string;
  scope: string;
  speed: number;
  data: ReplayAnswer | null;
  player: ReplayPlayer | null;
  load: number;
  presenting: boolean;
  onKey?: ((ev: KeyboardEvent) => void) | undefined;
}
// GET /api/v2/replay (ReplayData, ui/replay-player.ts) plus the page's own flags: when the answer was cut short, `next` is where it stopped.
interface ReplayAnswer extends ReplayData {
  truncated?: boolean | undefined;
  next?: string | undefined;
}

// ----------------------------------------------------------------- replay
// The query rides in the hash (#/replay?from=..&to=..&speed=60&bot=..), so the link reopens the same replay. Nothing
// is stored: whoever opens a link sees only what they may see.
const REPLAY_RANGES = [['today', 'Today'], ['yesterday', 'Yesterday'], ['7d', '7 days'], ['custom', 'Custom']];
let REPLAY: ReplayState | null = null;
const replayDay = (offset = 0) => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + offset); return d; };
function replaySpan(range: string) {
  if (range === 'yesterday') return {from: replayDay(-1), to: replayDay(0)};
  if (range === '7d') return {from: new Date(Date.now() - 7 * 864e5), to: new Date()};
  return {from: replayDay(0), to: new Date()};
}
// <input type=datetime-local> speaks local wall time without a zone.
const replayLocal = (iso: string) => { const d = new Date(iso); return isNaN(+d) ? '' : new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); };
const replayIso = (local: string) => { const d = new Date(local); return isNaN(+d) ? '' : d.toISOString(); };
function replayStop() {
  if (!REPLAY) return;
  REPLAY.player?.destroy();
  // onKey is set in the same call that makes REPLAY (pageReplay), before anything else can stop it.
  document.removeEventListener('keydown', REPLAY.onKey!);
  document.body.classList.remove('rp-presenting');
  REPLAY = null;
}
const replayQuery = (state: ReplayState) => {
  const q = new URLSearchParams({from: state.from, to: state.to});
  if (state.scope) { const [kind, id] = state.scope.split(/:(.*)/); q.set(kind === 'human' ? 'person' : String(kind), String(id)); }
  return q;
};
function replaySync(state: ReplayState) {
  const q = replayQuery(state); q.set('speed', String(state.speed));
  const hash = '#/replay?' + q.toString();
  if (location.hash !== hash) history.replaceState(null, '', hash);
  S.route = LAST_ROUTE = hash;
}

// The shared popover's host for the Who filter (task-list-menus.ts tasksMenu keeps its anchor here).
const REPLAY_MENU: TasksMenuHost = {};
// Who a replay can be narrowed to: everyone, a team, a bot or a person ([value, name] in the scope's form, kind:id).
function replayScopes(): {label: string; rows: [string, string][]}[] {
  const groups = (S.orgGroups || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  const bots = (S.emps || []).filter(e => e.status !== 'planned' && e.status !== 'archived').sort((a, b) => botDisplayName(a.id).localeCompare(botDisplayName(b.id)));
  const people = (S.people || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
  return [{label: 'Teams', rows: groups.map((g): [string, string] => ['team:' + g.id, g.name || g.id])},
          {label: 'Bots', rows: bots.map((e): [string, string] => ['bot:' + e.id, botDisplayName(e.id)])},
          {label: 'People', rows: people.map((p): [string, string] => ['human:' + p.id, p.name || p.id])}].filter(g => g.rows.length);
}
// The name of the scope in force, for its chip and the filter's label ('' for everyone).
const replayScopeName = (scope: string): string => !scope ? '' : replayScopes().flatMap(g => g.rows).find(([v]) => v === scope)?.[1] || scope.replace(/^[a-z]+:/, '');

function pageReplay() {
  replayStop();
  const q = new URLSearchParams(S.route.split('?')[1] || '');
  const scope = q.get('bot') ? 'bot:' + q.get('bot') : q.get('person') ? 'human:' + q.get('person') : q.get('team') ? 'team:' + q.get('team') : '';
  const given = q.get('from') && q.get('to'), span = replaySpan('today');
  const state: ReplayState = REPLAY = {range: given ? 'custom' : 'today', from: given ? String(q.get('from')) : span.from.toISOString(),
                          to: given ? String(q.get('to')) : span.to.toISOString(), scope, speed: +(q.get('speed') ?? '') || 60,
                          data: null, player: null, load: 0, presenting: false};
  // The one page header (ui/app/page-head.ts): the range as a segment, who to show behind the shared filter icon, and
  // Present (it rides in the player's bar while presenting); Copy link in ⋯. A custom range's two fields and the chip
  // for who is shown sit under the header.
  const head = pageHead({title: 'Replay', hook: 'rp-head',
    tools: `<div class="use-seg ph-seg rp-seg" role="tablist" aria-label="Range">${REPLAY_RANGES.map(([k, label]) =>
        `<button type="button" role="tab" data-rp-range="${k}" aria-selected="${k === state.range}">${label}</button>`).join('')}</div>`
      + `<button type="button" class="tl-ib" id="rp-filter" aria-haspopup="true" aria-expanded="false" aria-label="Who" title="Who">${filterIcon(13)}<span class="ph-fold-word" aria-hidden="true">Who</span><span class="tl-fbadge tnum" aria-hidden="true" hidden>1</span></button>`
      + '<span class="rp-actions"><button type="button" class="ghost" id="rp-present" aria-pressed="false">Present</button></span>',
    more: '<button type="button" id="rp-link">Copy link</button>'});
  $('#main').innerHTML = `<div class="rp-page">${head}
    <div class="rp-under">
      <span class="rp-custom" hidden><input type="datetime-local" id="rp-from" aria-label="From"><input type="datetime-local" id="rp-to" aria-label="To"></span>
      <span class="tl-chipset" id="rp-chips"></span>
    </div>
    <div id="rp-note" class="rp-note" hidden></div>
    <div id="rp-player">${skelRows(4, {card: true})}</div>
  </div><div id="task-filter-pop" class="tl-pop" popover></div>`;
  const main = $('#main');
  main.classList.add('replay-layout');
  main.querySelector('.rp-seg').onclick = (ev: MouseEvent) => {
    const b = eventElement(ev)?.closest<HTMLElement>('[data-rp-range]'); if (!b) return;
    state.range = b.dataset.rpRange!;
    if (state.range !== 'custom') { const s = replaySpan(state.range); state.from = s.from.toISOString(); state.to = s.to.toISOString(); }
    replayLoad(state);
  };
  $('#rp-from').onchange = $('#rp-to').onchange = () => {
    const f = replayIso($('#rp-from').value), t = replayIso($('#rp-to').value);
    if (f && t && f < t) { state.from = f; state.to = t; replayLoad(state); }
  };
  const filterBtn = $('#rp-filter') as HTMLElement, pop = $('#task-filter-pop') as HTMLElement;
  filterBtn.onclick = () => replayWhoMenu(state);
  $('#rp-chips').onclick = (ev: MouseEvent) => {
    if (eventElement(ev)?.closest('[data-rp-drop]')) { state.scope = ''; replayLoad(state); if (!filterBtn.closest('.ph-menu')) filterBtn.focus(); return; }
    if (eventElement(ev)?.closest('[data-rp-edit]')) replayWhoMenu(state);
  };
  pop.addEventListener('keydown', tasksMenuKeys);
  pop.addEventListener('toggle', (ev: Event & {newState?: string}) => {
    filterBtn.setAttribute('aria-expanded', String(ev.newState === 'open'));
    if (ev.newState === 'closed' && (pop.contains(document.activeElement) || document.activeElement === document.body) && !filterBtn.closest('.ph-menu')) filterBtn.focus();
  });
  $('#rp-link').onclick = async () => {
    replaySync(state);
    try { await copyText(location.href); toast('Link copied'); } catch (e) { toast(errorMessage(e, 'Could not copy the link.'), true); }
  };
  $('#rp-present').onclick = () => replayPresent(state, !state.presenting);
  state.onKey = ev => {
    if (ev.key === 'Escape' && state.presenting) replayPresent(state, false);
    else if ((ev.key === 'p' || ev.key === 'P') && !ev.metaKey && !ev.ctrlKey && !/^(INPUT|SELECT|TEXTAREA)$/.test(eventElement(ev)?.tagName || '')) replayPresent(state, !state.presenting);
  };
  document.addEventListener('keydown', state.onKey);
  replayLoad(state);
}

// Who to show: everyone, or one team, bot or person, picked from a list with a search (the shared popover). A pick
// loads the replay for them.
function replayWhoMenu(state: ReplayState): void {
  const btn = $('#rp-filter') as HTMLElement | null;
  if (!btn) return;
  // Opening the menu closes the header's ⋯; a filter icon folded into it is hidden then, so the menu goes under ⋯.
  const anchor = btn.closest('.ph-menu') ? $('#main header.rp-head > .ph-more') || btn : btn;
  const opt = (v: string, label: string) => `<label class="tl-opt" data-label="${esc(label.toLowerCase())}"><input type="radio" name="rp-who" value="${esc(v)}"${v === state.scope ? ' checked' : ''}><span>${esc(label)}</span></label>`;
  tasksMenu(REPLAY_MENU, anchor, `<div class="tl-menu rp-who" role="group" aria-label="Who">
    <input type="search" class="tl-menu-q" placeholder="Search" aria-label="Search teams, bots and people" autocomplete="off" spellcheck="false">
    <div class="tl-menu-list">${opt('', 'Everyone')}${replayScopes().map(g => `<div class="tl-menu-h" aria-hidden="true">${esc(g.label)}</div>${g.rows.map(([v, label]) => opt(v, label)).join('')}`).join('')}</div>
  </div>`, p => {
    const find = p.querySelector<HTMLInputElement>('.tl-menu-q');
    // A search hides what does not match, and a heading with nothing left under it.
    if (find) find.oninput = () => {
      const want = find.value.trim().toLowerCase();
      for (const o of p.querySelectorAll<HTMLElement>('.tl-opt')) o.hidden = !!want && !(o.dataset.label || '').includes(want);
      for (const h of p.querySelectorAll<HTMLElement>('.tl-menu-h')) {
        let n = h.nextElementSibling, any = false;
        while (n && !n.matches('.tl-menu-h')) { if (!(n as HTMLElement).hidden) any = true; n = n.nextElementSibling; }
        h.hidden = !any;
      }
    };
    p.onchange = ev => {
      const t = eventElement(ev);
      if (!(t instanceof HTMLInputElement) || t.name !== 'rp-who') return;
      tasksMenuClose();
      state.scope = t.value;
      replayLoad(state);
    };
  });
}
// The filter icon's badge and label, and a chip under the header naming who is shown (its ✕ shows everyone again).
function replayWhoPaint(state: ReplayState): void {
  const name = replayScopeName(state.scope), btn = $('#rp-filter') as HTMLElement | null, chips = $('#rp-chips') as HTMLElement | null;
  const badge = btn?.querySelector<HTMLElement>('.tl-fbadge');
  if (badge) badge.hidden = !name;
  btn?.classList.toggle('active', !!name);
  btn?.setAttribute('aria-label', name ? `Who, ${name}` : 'Who');
  if (chips) chips.innerHTML = name ? `<span class="tl-chip"><button type="button" class="tl-chip-main" data-rp-edit aria-haspopup="true" title="${esc('Who: ' + name)}"><span class="k">Who</span><span class="v">${esc(name)}</span></button><button type="button" class="tl-chip-x" data-rp-drop aria-label="Show everyone">${TL_ICON.x}</button></span>` : '';
  $('#main .rp-under')?.classList.toggle('on', !!name || state.range === 'custom');
}

// Presenting hides the page's bar, so its button moves into the player's transport bar and fades with it.
function replayPresent(state: ReplayState, on: boolean) {
  state.presenting = on;
  document.body.classList.toggle('rp-presenting', on);
  const button = $('#rp-present'); if (!button) return;
  button.setAttribute('aria-pressed', String(on));
  $('#rp-player')?.classList.toggle('rp-fade', on);
  const bar = $('#rp-player .rp-bar');
  if (on && bar) bar.appendChild(button); else $('#main .rp-actions')?.appendChild(button);
}

async function replayLoad(state: ReplayState) {
  const mine = ++state.load;
  for (const b of $('#main').querySelectorAll('[data-rp-range]')) b.setAttribute('aria-selected', String(b.dataset.rpRange === state.range));
  $('#main').querySelector('.rp-custom').hidden = state.range !== 'custom';
  $('#rp-from').value = replayLocal(state.from); $('#rp-to').value = replayLocal(state.to);
  replayWhoPaint(state);
  replaySync(state);
  state.player?.destroy(); state.player = null; state.data = null;
  $('#rp-player').innerHTML = skelRows(4, {card: true});
  let data: ReplayAnswer;
  try { data = await get('/v2/replay?' + replayQuery(state).toString()); }
  catch (e) {
    if (REPLAY !== state || mine !== state.load) return;
    $('#rp-player').innerHTML = `<div class="empty-state" role="alert"><p>${esc(errorMessage(e, ''))}</p><button type="button" class="ghost" data-rp-retry>Retry</button></div>`;
    $('[data-rp-retry]').onclick = () => replayLoad(state);
    return;
  }
  if (REPLAY !== state || mine !== state.load) return;
  state.data = data;
  // A range cut short says where it stops; an empty one says nothing here (its feed says "Nothing yet", once).
  const note = $('#rp-note');
  note.hidden = !data.truncated;
  note.textContent = data.truncated ? `First ${data.items.length.toLocaleString()} events, up to ${timeSaid(data.next)}` : '';
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


// The app's own faces: a bot's blob in the replayed state (never the live one), a person's photo or initials.
function replayFace(actor: ReplayActor, size: number, state?: string) {
  const slug = actorSlug(actor), pid = actorPerson(actor);
  if (slug) {
    const bot = botRow(slug) ? slug : {id: slug, display_name: replayName(REPLAY?.data || {})(actor)};
    const html = botAvatar(bot, size, state === 'running' ? 'running' : '');
    return state === 'running' ? html : html.replace(/ morph"/, '"').replace(/ data-breathe/, '').replace(/ style="animation-delay:[^"]*"/, '');
  }
  if (pid) return personAvatar((S.people || []).find(p => p.id === pid) || {id: pid, name: personDisplay(pid)}, size);
  return personCircle(replayName({})(actor) || '?', size);
}
