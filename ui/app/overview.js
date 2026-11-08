/* The campus receives only visible identities, readable assignments and read-permitted status. */
'use strict';
let OVERVIEW_PAGE = null;
function overviewModel() {
  const groups = [], machines = new Map(), humans = new Map(), placed = new Set();
  const fresh = S.overviewRosterFresh !== false && S.status !== null && S.v2.on;
  const actor = value => String(typeof value === 'object' ? value?.id || '' : value || '').replace(/^human:/, '');
  for (const p of S.people || []) {
    if (p.hidden || !p.id) continue;
    humans.set(p.id, {id: 'human:' + p.id, name: p.name || p.id, type: 'human', state: 'human',
      detail: 'Human teammate. Presence is not tracked.', href: '#/person/' + encodeURIComponent(p.id)});
  }
  const computer = (id, label) => {
    if (!machines.has(id)) {
      const group = {id: 'computer:' + id, name: label || 'Computer', kind: 'computer', members: []};
      machines.set(id, group); groups.push(group);
    }
    return machines.get(id);
  };
  for (const c of OVERVIEW_PAGE?.computers || []) {
    if (c.id && !c.revoked_at) computer(String(c.id), c.label);
  }
  const commons = {id: '@commons', name: 'Shared pavilion', kind: 'commons', members: []};
  const visible = (S.emps || []).filter(e => e.id && !['archived', 'retired'].includes(e.status || e.state)
    && e.my_access?.see !== false && orgBranchVisible(e));
  const byBot = new Map(visible.map(e => [e.id, e]));
  for (const e of visible) {
    const readable = e.my_access?.read !== false;
    const live = readable && fresh ? S.v2.status[e.id] : null;
    const state = !readable ? 'restricted' : !fresh ? 'unknown' : ['paused', 'planned'].includes(e.status) ? e.status : live?.state || 'unknown';
    const stuck = readable ? overviewStuck().find(row => row.bot === e.id) : null;
    const word = state === 'restricted' ? 'Activity requires Read access.' : stuck ? 'Stuck: ' + stuck.why
      : state === 'unknown' ? 'No current status available.' : V2_WORD[state] || statusWord(state);
    // Never infer a restricted bot's machine from inventory, even if the response happens to name it.
    const location = readable ? e.machine : null;
    const group = location?.runner_id ? computer(String(location.runner_id), location.label) : commons;
    group.members.push({id: 'bot:' + e.id, name: botDisplayName(e.id) + (e.shared_from || e.is_branch ? ' · Your branch' : ''),
      type: 'bot', state, detail: word, href: readable || e.can_chat ? '#/bot/' + encodeURIComponent(e.id) : '', face: overviewFace(e)});
    if (group === commons) continue;
    const collaborators = new Set([actor(e.owner_id), ...(e.users || []).map(actor), ...(e.owners || []).map(actor), ...(e.bot_owners || []).map(actor)]);
    // Follow visible reporting chains too: a human may direct a bot through another bot.
    let parent = e.reports_to; const seen = new Set([e.id]);
    while (parent && !seen.has(parent)) {
      if (String(parent).startsWith('human:')) { collaborators.add(actor(parent)); break; }
      seen.add(parent); parent = byBot.get(parent)?.reports_to;
    }
    for (const id of collaborators) {
      const person = humans.get(id);
      if (person && !group.members.some(m => m.id === person.id)) { group.members.push({...person}); placed.add(id); }
    }
  }
  for (const [id, person] of humans) if (!placed.has(id)) commons.members.push(person);
  if (commons.members.length) groups.push(commons);
  groups.sort((a, b) => (a.kind === 'commons') - (b.kind === 'commons') || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  for (const group of groups) group.members.sort((a, b) => (a.type === 'bot') - (b.type === 'bot') || a.id.localeCompare(b.id));
  return {company: companyName(), groups, fresh: !!fresh};
}
// A robot's screen face is the same symbol as its avatar (avatars.js botAvatar), glowing in its colour.
function overviewFace(e) {
  const meta = BOT_AVATARS[e.id], glyph = !meta?.tico && TEMPLATE_ICON.test(e.icon || '') ? e.icon : '';
  return {glyph, initials: avInitials(e.slug || e.id), color: meta?.tico ? '#3fd6a8' : meta?.color || `hsl(${avHue(e.id)} 60% 55%)`,
    svg: meta?.tico ? '/assets/tico/tico-glyph.svg' : !glyph && meta?.icon ? '/assets/bot-symbols/' + meta.icon + '.svg' : ''};
}
async function overviewLoadComputers() {
  const state = OVERVIEW_PAGE;
  if (!state || state.loadingComputers) return;
  state.loadingComputers = true;
  try {
    const result = await get('/v2/computers');
    if (OVERVIEW_PAGE === state && Array.isArray(result.computers)) state.computers = result.computers.map(c => ({id: c.id, label: c.label, revoked_at: c.revoked_at}));
  } catch {} // Readable bot assignments still provide their buildings when inventory is unavailable.
  finally { state.loadingComputers = false; }
}
function pageOverview() {
  const main = $('#main'); main.classList.add('overview-layout');
  main.innerHTML = `<section class="overview-page" aria-label="Company overview">
    <div class="overview-tabs"><h1>Overview</h1>${dashTabsHTML('campus')}</div>
    <a class="overview-needs" id="overview-needs" href="#/tasks" hidden></a>
    <div class="overview-stuck" id="overview-stuck" role="status" hidden></div>
    <div class="overview-hours" id="overview-hours" role="button" tabindex="0" aria-haspopup="dialog"></div>
  </section>`;
  // The campus left a moment ago is put back as it was, already drawn (overviewStop parks it); otherwise a new one loads.
  const kept = OVERVIEW_KEPT, page = main.querySelector('.overview-page');
  let frame = null;
  if (kept) {
    clearTimeout(kept.timer); OVERVIEW_KEPT = null;
    try { page.moveBefore(kept.frame, null); frame = kept.frame; frame.contentWindow?.postMessage({type: 'tico-overview-resume'}, location.origin); }
    catch { overviewDrop(kept); }
  }
  if (!frame) {
    page.insertAdjacentHTML('beforeend', '<iframe id="overview-frame" class="overview-frame" title="Interactive campus of your computers, bots and human teammates" src="/tico/ui/overview/index.html" allow="fullscreen"></iframe>');
    frame = page.lastElementChild;
  }
  const state = OVERVIEW_PAGE = {frame, abort: new AbortController(), ready: !!kept && frame === kept.frame, computers: kept?.computers || [], loadingComputers: false};
  window.addEventListener('message', ev => {
    if (OVERVIEW_PAGE !== state || ev.source !== frame.contentWindow || ev.origin !== location.origin) return;
    if (ev.data?.type === 'tico-overview-ready') { state.ready = true; state.sent = ''; overviewRefresh(); }
    if (ev.data?.type === 'tico-overview-open') {
      const person = overviewModel().groups.flatMap(g => g.members).find(m => m.id === ev.data.id);
      if (person?.href) location.hash = person.href;
    }
    if (ev.data?.type === 'tico-overview-replay-load') void overviewReplayLoad(state);
  }, {signal: state.abort.signal});
  const hours = $('#overview-hours');
  hours.addEventListener('click', ev => { if (!ev.target.closest('a')) overviewHoursOpen(); }, {signal: state.abort.signal});
  hours.addEventListener('keydown', ev => {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target === hours) { ev.preventDefault(); overviewHoursOpen(); }
  }, {signal: state.abort.signal});
  overviewStuckDraw();
  overviewNeedsDraw();
  overviewHoursDraw();
  void overviewHoursLoad();
  void overviewLoadComputers().then(() => { if (OVERVIEW_PAGE === state) overviewRefresh(); });
}
// The campus replay reads the same history as the Replay page (GET /api/v2/replay), with the viewer's own rights,
// and hands the frame only what the campus shows: who, to whom, task titles, states and a short line of each
// message. Bot focus text and run summaries stay on this side, as they do for the live campus.
async function overviewReplayLoad(state) {
  const to = new Date(), from = new Date(to.getTime() - 864e5);
  const post = message => { if (OVERVIEW_PAGE === state) state.frame.contentWindow?.postMessage(message, location.origin); };
  let data;
  try { data = await get('/v2/replay?' + new URLSearchParams({from: from.toISOString(), to: to.toISOString()})); }
  catch { post({type: 'tico-overview-replay-error'}); return; }
  const nameOf = replayName(data), names = {};
  const actors = (data.items || []).flatMap(it => [it.who, it.to, it.owner_id, it.kind === 'task.owner' ? it.new : null, it.bot ? 'bot:' + it.bot : null])
    .concat(Object.keys(data.start?.bots || {}).map(slug => 'bot:' + slug));
  for (const a of actors) if (a && !(a in names)) names[a] = nameOf(a);
  const slim = it => ({at: it.at, kind: it.kind, who: it.who, to: it.to, owner: it.owner_id, bot: it.bot, state: it.state, exit: it.exit,
    new: it.new, task: it.task ? {title: it.task.title} : undefined, goal: it.goal ? {title: it.goal.title} : undefined,
    text: it.kind === 'chat.message' || it.kind === 'task.comment' ? String(it.text || '').slice(0, 160) : undefined});
  const start = {bots: Object.fromEntries(Object.entries(data.start?.bots || {}).map(([slug, b]) => [slug, {state: b.state}]))};
  post({type: 'tico-overview-replay-data', names, words: V2_WORD,
    data: {from: data.from, to: data.to, truncated: !!data.truncated, next: data.next || null, items: (data.items || []).map(slim), start}});
}
function overviewRefresh() {
  const state = OVERVIEW_PAGE; if (!state) return;
  state.model = overviewModel();
  overviewHoursMaybe();
  if (!state.ready) return;
  // Every bot event lands here; the frame redraws only for a model it has not had.
  const sent = JSON.stringify(state.model);
  if (sent === state.sent) return;
  state.sent = sent;
  state.frame.contentWindow?.postMessage({type: 'tico-overview-data', model: state.model}, location.origin);
}
// Leaving the campus parks its frame for a few minutes: paused (no 3D frames drawn), out of sight and out of #main,
// so coming back shows the scene at once instead of loading and building it again. Element.moveBefore keeps the
// frame's document through both moves; a browser without it would reload a moved frame, so there it is let go.
const OVERVIEW_KEEP_MS = 5 * 60e3;
let OVERVIEW_KEPT = null;          // {frame, computers, timer}
function overviewStop() {
  const state = OVERVIEW_PAGE; if (!state) return;
  state.abort.abort(); OVERVIEW_PAGE = null;
  const frame = state.frame;
  if (state.ready && frame.isConnected && Element.prototype.moveBefore) {
    let hold = $('#overview-hold');
    if (!hold) {
      hold = document.createElement('div'); hold.id = 'overview-hold'; hold.setAttribute('aria-hidden', 'true'); hold.inert = true;
      document.body.append(hold);
    }
    // The same size as on the page, so the scene does not resize twice for a visit elsewhere.
    const box = frame.getBoundingClientRect();
    hold.style.width = box.width + 'px'; hold.style.height = box.height + 'px';
    try {
      hold.moveBefore(frame, null);
      frame.contentWindow?.postMessage({type: 'tico-overview-pause'}, location.origin);
      OVERVIEW_KEPT = {frame, computers: state.computers, timer: setTimeout(() => overviewDrop(OVERVIEW_KEPT), OVERVIEW_KEEP_MS)};
      return;
    } catch { /* not movable here: let it go below */ }
  }
  overviewDrop({frame});
}
function overviewDrop(kept) {
  if (!kept) return;
  if (OVERVIEW_KEPT === kept) { clearTimeout(kept.timer); OVERVIEW_KEPT = null; }
  kept.frame.contentWindow?.postMessage({type: 'tico-overview-dispose'}, location.origin);
  kept.frame.remove();
}
// Bots with queued work that is not starting, or left in setup for a day, from the Health answer the app already
// polls (ui/health.js).
const overviewStuck = () => (typeof HL !== 'undefined' && HL?.stuck) || [];
// "3 bots running on fallback since 10:04, 2 limited": bots whose plan ran out, from the same answer.
function overviewFallbackLine() {
  const on = (typeof HL !== 'undefined' && HL?.on_fallback) || [], limited = (typeof HL !== 'undefined' && HL?.limited) || [];
  if (!on.length && !limited.length) return '';
  const first = on.map(row => row.changed_at).filter(Boolean).sort()[0];
  const clock = first ? new Date(first).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
  const parts = [on.length ? `${on.length} ${on.length === 1 ? 'bot' : 'bots'} running on fallback${clock ? ' since ' + clock : ''}` : '',
    limited.length ? `${limited.length} limited` : ''].filter(Boolean);
  return `<p class="overview-fallback"><a href="#/settings" data-gs-tab="runs">${esc(parts.join(', '))}</a></p>`;
}
function overviewStuckDraw() {
  const el = OVERVIEW_PAGE && $('#overview-stuck'); if (!el) return;
  const rows = overviewStuck(), fallback = overviewFallbackLine();
  el.hidden = !rows.length && !fallback;
  el.innerHTML = (rows.length ? `<strong>Stuck · ${rows.length}</strong><ul class="hl-bots">${hlStuckHtml(rows.slice(0, 5))}</ul>${rows.length > 5 ? `<a href="#/health">${rows.length - 5} more</a>` : ''}` : '') + fallback;
  overviewRefresh();
}
// The person's Needs you count, with how many items left it this week without them (expired, withdrawn, or answered by
// their Assistant), so the number is read against what is already going away on its own.
function overviewNeedsDraw() {
  const el = OVERVIEW_PAGE && $('#overview-needs'); if (!el) return;
  if (S.me?.role === 'bot') { el.hidden = true; return; }
  const n = S.v2?.needsCount ?? (S.v2?.needs || []).length, w = S.v2?.wentAway, parts = [];
  if (w?.expired) parts.push(`${w.expired} expired`);
  if (w?.withdrawn) parts.push(`${w.withdrawn} withdrawn`);
  if (w?.assistant) parts.push(`${w.assistant} answered by your Assistant`);
  el.hidden = false;
  el.innerHTML = `<strong>Needs you <span class="tnum">${n}</span></strong>${w?.total
    ? `<span class="muted">${w.total} went away on their own this week (${esc(parts.join(', '))})</span>` : ''}`;
  el.setAttribute('aria-label', `Needs you: ${n}${w?.total ? `. ${w.total} went away on their own this week` : ''}`);
}
