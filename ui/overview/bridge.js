const $ = selector => document.querySelector(selector);
const origin = location.origin;
let model = null, scene = null, topology = '', revision = 0, disposed = false, failed = false, replay = null, replaying = false;
// The renderer starts downloading before the team arrives; the page preloads it too.
const renderer = import('./building.js'); renderer.catch(() => {});
const preferences = {layout: 'campus'};
try { if (localStorage.getItem('tico-overview-layout') === 'tower') preferences.layout = 'tower'; } catch {}
const send = data => parent.postMessage(data, origin);
function openMember(id) {
  if (model.groups.some(g => g.members.some(m => m.id === id && m.href))) send({type: 'tico-overview-open', id});
}
function fallback(message) {
  $('#loading').hidden = true; $('#fallback').hidden = false;
  $('#fallback h1').textContent = model?.company || 'Overview';
  $('#fallback p').textContent = message;
  const list = $('#fallback-team'); list.replaceChildren();
  for (const group of model?.groups || []) {
    const section = document.createElement('section'), heading = document.createElement('h2');
    heading.textContent = group.name; section.append(heading);
    for (const member of group.members) {
      const button = document.createElement('button');
      button.textContent = member.name + ' · ' + member.detail;
      button.disabled = !member.href; button.onclick = () => openMember(member.id); section.append(button);
    }
    if (!group.members.length) { const p = document.createElement('p'); p.textContent = 'No teammates assigned yet.'; section.append(p); }
    list.append(section);
  }
}
// While a replay plays, each readable bot's desk shows its state as of the replay time; restricted bots stay
// restricted and people stay people.
function currentData() {
  const live = {...model, offset: 0, groups: model.groups.map(g => ({...g, key: g.id}))};
  if (!replay) return live;
  const desk = m => {
    if (m.type !== 'bot' || m.state === 'restricted') return m;
    const shown = replay.desk(m.id.slice(4)) || {state: 'idle', detail: 'Idle'};
    return {...m, ...shown};
  };
  return {...live, fresh: true, groups: live.groups.map(g => ({...g, members: g.members.map(desk)}))};
}
function controls() {
  $('#campus-company').textContent = model.company;
  const count = model.groups.filter(g => g.kind === 'computer').length;
  $('#campus-summary').textContent = count + (count === 1 ? ' computer' : ' computers') + (replaying ? ' · Replaying the last 24 hours' : preferences.layout === 'tower' ? ' · One tower' : ' · One connected campus');
  $('#connection-status').hidden = model.fresh || replaying;
  $('#replay-toggle').setAttribute('aria-pressed', String(replaying));
  document.body.classList.toggle('replaying', replaying);
  document.querySelectorAll('[data-layout]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.layout === preferences.layout)));
}
async function mount(quiet = false) {
  const seq = ++revision; scene?.dispose(); scene = null; failed = false; $('#world').replaceChildren();
  controls(); $('#fallback').hidden = true;
  if (!quiet) { $('#loading').hidden = false; $('#loading').classList.remove('gone'); }
  delete document.body.dataset.sceneReady;
  if (!model.groups.length) { fallback('Your campus will come to life when you add computers and teammates.'); return; }
  try {
    const {createBuilding} = await renderer;
    if (disposed || seq !== revision) return;
    scene = createBuilding(currentData(), preferences, openMember);
  } catch (error) {
    if (disposed || seq !== revision) return;
    failed = true; fallback('The 3D view is unavailable. Explore your team below.');
    console.warn('Overview renderer unavailable', error);
  }
}
function update(next) {
  if (!Array.isArray(next?.groups)) return;
  const shape = JSON.stringify([next.company, next.groups.map(g => [g.id, g.name, g.kind, g.members.map(m => [m.id, m.name, m.type])])]);
  model = next;
  if (shape !== topology) { topology = shape; mount(); }
  else { controls(); scene?.update(currentData()); if (failed) fallback('The 3D view is unavailable. Explore your team below.'); }
}
for (const button of document.querySelectorAll('[data-layout]')) button.onclick = () => {
  if (!model || preferences.layout === button.dataset.layout) return;
  preferences.layout = button.dataset.layout; preferences.floorId = null;
  try { localStorage.setItem('tico-overview-layout', preferences.layout); } catch {}
  mount(true);
};
// Replay: the page fetches the last 24 hours with the viewer's own rights and posts them in.
const replayModule = () => import('./replay.js');
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function startReplay() {
  if (!model || replaying) return;
  replaying = true; controls();
  $('#replay').hidden = false; $('#replay-note').hidden = false; $('#replay-note').textContent = 'Loading the last 24 hours…';
  $('#replay-captions').replaceChildren(); $('#replay-bar').hidden = true;
  send({type: 'tico-overview-replay-load'});
}
async function showReplay(message) {
  if (!replaying || disposed) return;
  const {createReplay} = await replayModule();
  if (!replaying || disposed) return;
  replay?.destroy();
  $('#replay-bar').hidden = false;
  replay = createReplay($('#replay'), message.data, {names: message.names || {}, words: message.words || {}, reduced: reducedMotion,
    scene: () => scene, onState: () => scene?.update(currentData()), onEnd: stopReplay});
  scene?.update(currentData());
  replay.play();
}
function stopReplay() {
  replaying = false; replay?.destroy(); replay = null;
  $('#replay').hidden = true;
  if (model) { controls(); scene?.update(currentData()); }
}
$('#replay-toggle').onclick = () => replaying ? stopReplay() : startReplay();
// Back to live works while loading and after an error too, before any replay exists. A replay that plays to its
// end goes back to live the same way; one scrubbed to the end stays open for scrubbing back.
$('#replay-live').onclick = stopReplay;
function dispose() { disposed = true; revision++; replay?.destroy(); replay = null; scene?.dispose(); scene = null; }
window.addEventListener('message', event => {
  if (event.source !== parent || event.origin !== origin || disposed) return;
  if (event.data?.type === 'tico-overview-data') update(event.data.model);
  if (event.data?.type === 'tico-overview-dispose') dispose();
  if (event.data?.type === 'tico-overview-replay-data') showReplay(event.data);
  if (event.data?.type === 'tico-overview-replay-error' && replaying) $('#replay-note').textContent = 'The replay could not be loaded.';
});
window.addEventListener('pagehide', dispose);
window.addEventListener('overview-context-lost', () => { failed = true; fallback('The 3D view was interrupted. Your team is still accessible below.'); });
send({type: 'tico-overview-ready'});
