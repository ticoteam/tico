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
  const visible = (S.emps || []).filter(e => e.name && !['archived', 'retired'].includes(e.status || e.state)
    && e.my_access?.see !== false && orgBranchVisible(e));
  const byBot = new Map(visible.map(e => [e.name, e]));
  for (const e of visible) {
    const readable = e.my_access?.read !== false;
    const live = readable && fresh ? S.v2.status[e.name] : null;
    const state = !readable ? 'restricted' : !fresh ? 'unknown' : ['paused', 'planned'].includes(e.status) ? e.status : live?.state || 'unknown';
    const stuck = readable ? overviewStuck().find(row => row.bot === e.name) : null;
    const word = state === 'restricted' ? 'Activity requires Read access.' : stuck ? 'Stuck: ' + stuck.why
      : state === 'unknown' ? 'No current status available.' : V2_WORD[state] || statusWord(state);
    // Never infer a restricted bot's machine from inventory, even if the response happens to name it.
    const location = readable ? e.machine : null;
    const group = location?.runner_id ? computer(String(location.runner_id), location.label) : commons;
    group.members.push({id: 'bot:' + e.name, name: botDisplayName(e.name) + (e.shared_from || e.is_branch ? ' · Your branch' : ''),
      type: 'bot', state, detail: word, href: readable || e.can_chat ? '#/bot/' + encodeURIComponent(e.name) : '', face: overviewFace(e)});
    if (group === commons) continue;
    const collaborators = new Set([actor(e.operator), ...(e.users || []).map(actor), ...(e.owners || []).map(actor), ...(e.bot_owners || []).map(actor)]);
    // Follow visible reporting chains too: a human may direct a bot through another bot.
    let parent = e.reports_to; const seen = new Set([e.name]);
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
  const meta = BOT_AVATARS[e.name], glyph = !meta?.tico && TEMPLATE_ICON.test(e.icon || '') ? e.icon : '';
  return {glyph, initials: avInitials(e.name), color: meta?.tico ? '#3fd6a8' : meta?.color || `hsl(${avHue(e.name)} 60% 55%)`,
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
    <a class="overview-needs" id="overview-needs" href="#/tasks" hidden></a>
    <div class="overview-stuck" id="overview-stuck" role="status" hidden></div>
    <iframe id="overview-frame" class="overview-frame" title="Interactive campus of your computers, bots and human teammates" src="/tico/ui/overview/index.html" allow="fullscreen"></iframe>
  </section>`;
  const frame = $('#overview-frame');
  const state = OVERVIEW_PAGE = {frame, abort: new AbortController(), ready: false, computers: [], loadingComputers: false};
  window.addEventListener('message', ev => {
    if (OVERVIEW_PAGE !== state || ev.source !== frame.contentWindow || ev.origin !== location.origin) return;
    if (ev.data?.type === 'tico-overview-ready') { state.ready = true; overviewRefresh(); }
    if (ev.data?.type === 'tico-overview-open') {
      const person = overviewModel().groups.flatMap(g => g.members).find(m => m.id === ev.data.id);
      if (person?.href) location.hash = person.href;
    }
    if (ev.data?.type === 'tico-overview-replay-load') void overviewReplayLoad(state);
  }, {signal: state.abort.signal});
  overviewStuckDraw();
  overviewNeedsDraw();
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
  const actors = (data.items || []).flatMap(it => [it.who, it.to, it.owner, it.kind === 'task.owner' ? it.new : null, it.bot ? 'bot:' + it.bot : null])
    .concat(Object.keys(data.start?.bots || {}).map(slug => 'bot:' + slug));
  for (const a of actors) if (a && !(a in names)) names[a] = nameOf(a);
  const slim = it => ({at: it.at, kind: it.kind, who: it.who, to: it.to, owner: it.owner, bot: it.bot, state: it.state, exit: it.exit,
    new: it.new, task: it.task ? {title: it.task.title} : undefined, goal: it.goal ? {title: it.goal.title} : undefined,
    text: it.kind === 'chat.message' || it.kind === 'task.comment' ? String(it.text || '').slice(0, 160) : undefined});
  const start = {bots: Object.fromEntries(Object.entries(data.start?.bots || {}).map(([slug, b]) => [slug, {state: b.state}]))};
  post({type: 'tico-overview-replay-data', names, words: V2_WORD,
    data: {from: data.from, to: data.to, truncated: !!data.truncated, next: data.next || null, items: (data.items || []).map(slim), start}});
}
function overviewRefresh() {
  const state = OVERVIEW_PAGE; if (!state) return;
  state.model = overviewModel();
  if (state.ready) state.frame.contentWindow?.postMessage({type: 'tico-overview-data', model: state.model}, location.origin);
}
function overviewStop() {
  const state = OVERVIEW_PAGE; if (!state) return;
  state.frame.contentWindow?.postMessage({type: 'tico-overview-dispose'}, location.origin);
  state.abort.abort(); state.frame.remove(); OVERVIEW_PAGE = null;
}
// Bots with queued work that is not starting, from the Health answer the app already polls (ui/health.js).
const overviewStuck = () => (typeof HL !== 'undefined' && HL?.stuck) || [];
function overviewStuckDraw() {
  const el = OVERVIEW_PAGE && $('#overview-stuck'); if (!el) return;
  const rows = overviewStuck();
  el.hidden = !rows.length;
  el.innerHTML = rows.length ? `<strong>Stuck · ${rows.length}</strong><ul class="hl-bots">${hlStuckHtml(rows.slice(0, 5))}</ul>${rows.length > 5 ? `<a href="#/health">${rows.length - 5} more</a>` : ''}` : '';
  overviewRefresh();
}
// The person's Needs you count, with how many items left it this week without them (expired, withdrawn, or answered by
// their Assistant), so the number is read against what is already going away on its own.
function overviewNeedsDraw() {
  const el = OVERVIEW_PAGE && $('#overview-needs'); if (!el) return;
  if (S.me?.role === 'bot') { el.hidden = true; return; }
  const n = (S.v2?.needs || []).length, w = S.v2?.wentAway, parts = [];
  if (w?.expired) parts.push(`${w.expired} expired`);
  if (w?.withdrawn) parts.push(`${w.withdrawn} withdrawn`);
  if (w?.assistant) parts.push(`${w.assistant} answered by your Assistant`);
  el.hidden = false;
  el.innerHTML = `<strong>Needs you <span class="tnum">${n}</span></strong>${w?.total
    ? `<span class="muted">${w.total} went away on their own this week (${esc(parts.join(', '))})</span>` : ''}`;
  el.setAttribute('aria-label', `Needs you: ${n}${w?.total ? `. ${w.total} went away on their own this week` : ''}`);
}
