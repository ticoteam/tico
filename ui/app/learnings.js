/* ui/app/learnings.js — The Learnings page (docs/learnings.md, Nightly learning run): a rail of nights, then one
   night read top to bottom. Sections Bots, Docs and Market, one row per subject; a row opens to each change with
   its source and diff (learnDiff, ui/app/bot-learnings.js). GET /api/v2/learnings and /api/v2/learnings/{id}. */
'use strict';

const LEARN_SECTIONS = [['bots', 'Bots', 'smart_toy'], ['docs', 'Docs', 'description'], ['market', 'Market', 'monitoring']];
const LEARN_SRC_ICONS = {chat: 'forum', task: 'task_alt', slack: 'tag', mail: 'mail', meeting: 'groups', doc: 'description', commit: 'history'};
let LEARN_LOAD = 0;

const learnNight = night => new Date(night + 'T12:00:00').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});

async function pageLearnings() {
  const load = ++LEARN_LOAD;
  const want = new URLSearchParams(S.route.split('?')[1] || '').get('night') || '';
  $('#main').innerHTML = '<div class="lrn"><aside class="lrn-rail" aria-label="Nights"><h3>Learnings</h3><div id="lrn-nights"></div></aside><section class="lrn-night" id="lrn-night" aria-live="polite"></section></div>';
  let data;
  try { data = await get('/v2/learnings'); } catch (e) { $('#lrn-night').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (load !== LEARN_LOAD) return;
  const nights = data.nights || [];
  if (!nights.length) { $('#lrn-night').innerHTML = '<div class="empty">The first nightly learning run is tonight at 3:00.</div>'; return; }
  const cur = nights.find(n => n.id === want) || nights[0];
  $('#lrn-nights').innerHTML = nights.map(n => `<a class="lrn-n${n.id === cur.id ? ' on' : ''}" href="${LEARNINGS}?night=${encodeURIComponent(n.id)}"><span>${esc(learnNight(n.night))}</span><span class="c">${n.status === 'done' ? n.updates : '—'}</span></a>`).join('');
  await learnShowNight(cur, load);
}

async function learnShowNight(n, load) {
  const box = $('#lrn-night');
  const head = text => `<div class="lrn-head"><h2>${esc(learnNight(n.night))}</h2>${text ? `<p>${esc(text)}</p>` : ''}</div>`;
  if (n.status === 'running') { box.innerHTML = head('Running now.'); return; }
  if (n.status === 'quiet') { box.innerHTML = head('No new activity.'); return; }
  if (n.status === 'unconfigured') { box.innerHTML = head('Learnings need a decision model: add a TypeSafe key or an AI provider key.'); return; }
  if (n.status === 'failed') { box.innerHTML = head('The run failed.'); return; }
  let night;
  try { night = await get(`/v2/learnings/${encodeURIComponent(n.id)}`); } catch (e) { box.innerHTML = head('') + `<div class="err">${esc(e.message)}</div>`; return; }
  if (load !== LEARN_LOAD) return;
  const s = night.sections || {};
  const groups = LEARN_SECTIONS.filter(([k]) => (s[k] || []).length);
  const total = groups.reduce((a, [k]) => a + s[k].reduce((b, x) => b + x.entries.length, 0), 0);
  const parts = groups.map(([k, label]) => `${s[k].length} ${label.toLowerCase()}`);
  box.innerHTML = head(total ? `${total} update${total === 1 ? '' : 's'} across ${parts.join(', ')}` : 'Nothing changed.')
    + groups.map(([k, label, icon]) => `<section class="lrn-grp"><h3><span class="nav-icon" aria-hidden="true">${icon}</span>${label}</h3>${s[k].map(x => learnSubject(k, x)).join('')}</section>`).join('');
}

function learnSubject(kind, x) {
  const more = x.entries.length - 1;
  const av = kind === 'bots' ? botAvatar(x.id, 22) : `<span class="lrn-sq" aria-hidden="true">${esc((x.name || '?')[0])}</span>`;
  const name = kind === 'bots' ? `<a href="#/bot/${encodeURIComponent(x.id)}">${esc(x.name)}</a>` : esc(x.name);
  return `<details class="lrn-subj"><summary>${av}<span class="nm">${name}</span><span class="first">${esc(x.entries[0]?.title || '')}</span><span class="more">${more ? `+${more} more` : ''}<span class="nav-icon" aria-hidden="true">expand_more</span></span></summary>
    <div class="lrn-body">${x.saw ? `<div class="saw">${esc(x.saw)}</div>` : ''}${x.entries.map(learnEntry).join('')}</div></details>`;
}

function learnEntry(e) {
  const src = (e.sources || []).map(s => {
    const label = `<span class="nav-icon" aria-hidden="true">${LEARN_SRC_ICONS[s.kind] || 'description'}</span>${esc(s.label)}`;
    const ext = /^https?:/.test(s.link || '');
    return `<div class="src">${s.link ? `<a href="${esc(s.link)}"${ext ? ' target="_blank" rel="noopener"' : ''}>${label}</a>` : `<span>${label}</span>`}${s.quote ? ` <q>${esc(s.quote)}</q>` : ''}</div>`;
  }).join('');
  const c = e.change || {};
  return `<div class="lrn-entry"><div class="t">${esc(e.title)}</div><div class="kv">
    ${src ? `<span class="k">Source</span><span class="v">${src}</span>` : ''}
    <span class="k">Change</span><span class="v"><span class="where">${esc(c.where || '')}</span>${c.diff ? `<pre class="lrn-diff">${learnDiff(c.diff)}</pre>` : ''}</span></div></div>`;
}

document.addEventListener('click', ev => {             // a bot's name opens its page, not the row
  if (ev.target.closest('.lrn-subj summary a')) ev.stopPropagation();
}, true);
