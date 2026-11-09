export {};
import type {createBuilding} from './building.ts';
import type {createReplay} from './replay.ts';

type Data = Parameters<typeof createBuilding>[0];
type Scene = ReturnType<typeof createBuilding>;
type Replay = ReturnType<typeof createReplay>;
interface Face { glyph?: string; initials: string; color: string; svg?: string }
interface Member { id: string; name: string; type: string; state: string; detail: string; href?: string; face?: Face }
interface Group { id: string; name: string; kind: string; members: Member[] }
interface Model { company: string; fresh: boolean; groups: Group[] }
interface Preferences { layout?: string; floorId?: string | null }
interface Incoming { type?: string; model?: Model; data?: unknown; names?: Record<string, string>; words?: Record<string, string> }

const $ = (selector: string) => document.querySelector<HTMLElement>(selector);
const origin = location.origin;
let model: Model | null = null, scene: Scene | null = null, topology = '', revision = 0, disposed = false, failed = false, replay: Replay | null = null, replaying = false, paused = false;
// The renderer starts downloading before the team arrives; the page preloads it too.
const renderer = import('./building.ts'); renderer.catch(() => {});
const preferences: Preferences = {layout: 'campus'};
try { if (localStorage.getItem('tico-overview-layout') === 'tower') preferences.layout = 'tower'; } catch {}
const send = (data: object) => parent.postMessage(data, origin);
function openMember(id: string) {
  if (!model) return;
  if (model.groups.some(g => g.members.some(m => m.id === id && m.href))) send({type: 'tico-overview-open', id});
}
function fallback(message: string) {
  const loading = $('#loading'), pane = $('#fallback'), title = $('#fallback h1'), text = $('#fallback p'), list = $('#fallback-team');
  if (!loading || !pane || !title || !text || !list) return;
  loading.hidden = true; pane.hidden = false;
  title.textContent = model?.company || 'Overview';
  text.textContent = message;
  list.replaceChildren();
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
function currentData(data: Model): Data {
  const live = {...data, offset: 0, groups: data.groups.map(g => ({...g, key: g.id}))};
  const current = replay;
  if (!current) return live;
  const desk = (m: Member): Member => {
    if (m.type !== 'bot' || m.state === 'restricted') return m;
    const shown = current.desk(m.id.slice(4)) || {state: 'idle', detail: 'Idle'};
    return {...m, ...shown};
  };
  return {...live, fresh: true, groups: live.groups.map(g => ({...g, members: g.members.map(desk)}))};
}
function controls() {
  const company = $('#campus-company'), summary = $('#campus-summary'), status = $('#connection-status'), toggle = $('#replay-toggle');
  if (!model || !company || !summary || !status || !toggle) return;
  company.textContent = model.company;
  const count = model.groups.filter(g => g.kind === 'computer').length;
  summary.textContent = count + (count === 1 ? ' computer' : ' computers') + (replaying ? ' · Replaying the last 24 hours' : preferences.layout === 'tower' ? ' · One tower' : ' · One connected campus');
  status.hidden = model.fresh || replaying;
  toggle.setAttribute('aria-pressed', String(replaying));
  document.body.classList.toggle('replaying', replaying);
  document.querySelectorAll<HTMLElement>('[data-layout]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.layout === preferences.layout)));
}
async function mount(quiet = false) {
  const seq = ++revision; scene?.dispose(); scene = null; failed = false;
  const world = $('#world');
  if (!world) return;
  world.replaceChildren();
  controls();
  const pane = $('#fallback'), loading = $('#loading');
  if (!pane || !loading) return;
  pane.hidden = true;
  if (!quiet) { loading.hidden = false; loading.classList.remove('gone'); }
  delete document.body.dataset.sceneReady;
  if (!model) return;
  if (!model.groups.length) { fallback('Your campus will come to life when you add computers and teammates.'); return; }
  try {
    const {createBuilding} = await renderer;
    if (disposed || seq !== revision) return;
    scene = createBuilding(currentData(model), preferences, openMember);
    if (paused) scene.pause?.();
  } catch (error) {
    if (disposed || seq !== revision) return;
    failed = true; fallback('The 3D view is unavailable. Explore your team below.');
    console.warn('Overview renderer unavailable', error);
  }
}
function update(next: Model | null | undefined) {
  if (!next || !Array.isArray(next.groups)) return;
  const shape = JSON.stringify([next.company, next.groups.map(g => [g.id, g.name, g.kind, g.members.map(m => [m.id, m.name, m.type])])]);
  model = next;
  if (shape !== topology) { topology = shape; mount(); }
  else { controls(); scene?.update(currentData(next)); if (failed) fallback('The 3D view is unavailable. Explore your team below.'); }
}
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-layout]')) button.onclick = () => {
  if (!model || preferences.layout === button.dataset.layout) return;
  preferences.layout = String(button.dataset.layout); preferences.floorId = null;
  try { localStorage.setItem('tico-overview-layout', String(preferences.layout)); } catch {}
  mount(true);
};
// Replay: the page fetches the last 24 hours with the viewer's own rights and posts them in.
const replayModule = () => import('./replay.ts');
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function startReplay() {
  if (!model || replaying) return;
  replaying = true; controls();
  const pane = $('#replay'), note = $('#replay-note'), captions = $('#replay-captions'), bar = $('#replay-bar');
  if (!pane || !note || !captions || !bar) return;
  pane.hidden = false; note.hidden = false; note.textContent = 'Loading the last 24 hours…';
  captions.replaceChildren(); bar.hidden = true;
  send({type: 'tico-overview-replay-load'});
}
async function showReplay(message: Incoming) {
  if (!replaying || disposed) return;
  const {createReplay} = await replayModule();
  if (!replaying || disposed) return;
  replay?.destroy();
  const bar = $('#replay-bar');
  if (!bar || !model) return;
  bar.hidden = false;
  replay = createReplay($('#replay'), message.data, {names: message.names || {}, words: message.words || {}, reduced: reducedMotion,
    scene: () => scene, onState: () => { if (model) scene?.update(currentData(model)); }, onEnd: stopReplay});
  scene?.update(currentData(model));
  replay.play();
}
function stopReplay() {
  replaying = false; replay?.destroy(); replay = null;
  const pane = $('#replay');
  if (!pane) return;
  pane.hidden = true;
  if (model) { controls(); scene?.update(currentData(model)); }
}
const replayToggle = $('#replay-toggle'), replayLive = $('#replay-live');
if (replayToggle) replayToggle.onclick = () => replaying ? stopReplay() : startReplay();
// Back to live works while loading and after an error too, before any replay exists. A replay that plays to its
// end goes back to live the same way; one scrubbed to the end stays open for scrubbing back.
if (replayLive) replayLive.onclick = stopReplay;
function dispose() { disposed = true; revision++; replay?.destroy(); replay = null; scene?.dispose(); scene = null; }
window.addEventListener('message', (event: MessageEvent<Incoming | null>) => {
  if (event.source !== parent || event.origin !== origin || disposed) return;
  if (event.data?.type === 'tico-overview-data') update(event.data.model);
  if (event.data?.type === 'tico-overview-dispose') dispose();
  // The app parks this frame while another page shows (ui/app/overview.ts): no frames are drawn until it is back.
  if (event.data?.type === 'tico-overview-pause') { paused = true; if (replaying) stopReplay(); scene?.pause?.(); }
  if (event.data?.type === 'tico-overview-resume') { paused = false; scene?.resume?.(); }
  if (event.data?.type === 'tico-overview-replay-data') showReplay(event.data);
  if (event.data?.type === 'tico-overview-replay-error' && replaying) { const note = $('#replay-note'); if (note) note.textContent = 'The replay could not be loaded.'; }
});
window.addEventListener('pagehide', dispose);
window.addEventListener('overview-context-lost', () => { failed = true; fallback('The 3D view was interrupted. Your team is still accessible below.'); });
send({type: 'tico-overview-ready'});
