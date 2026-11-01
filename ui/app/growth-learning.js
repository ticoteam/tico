/* ui/app/growth-learning.js — Growth › Learning (#/growth/learning, the brain icon in Growth's head; growth spec §4.5):
   per bet, its ideas as a tree (each follow-up under the idea it follows, coloured by verdict) and the bet's evidence
   tally from GET /api/v2/growth/evidence; Log is every verdict, newest first. Below either, the go-to-market's
   published versions with their notes (GET /api/v2/gtm), each opening read-only.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

function pageGrowthLearning(state) {
  state.view = new URLSearchParams((S.route || '').split('?')[1] || '').get('v') === 'log' ? 'log' : 'tree';
  void glRead(state);
}
async function glRead(state) {
  let ideas, ev, refs, gtm;
  try {
    [ideas, ev, refs, gtm] = await Promise.all([get('/v2/growth/ideas', {fresh: true}).then(r => r.ideas || []),
      get('/v2/growth/evidence', {fresh: true}).then(r => r.bets || []).catch(() => []), grRefs(), get('/v2/gtm', {fresh: true}).catch(() => null)]);
  } catch (e) { if (GR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (GR !== state) return;
  state.data = {ideas, ev, gtm, ...refs};
  glDraw(state);
}
const glTally = t => {
  const n = o => (o?.low || 0) + (o?.medium || 0) + (o?.high || 0);
  const s = n(t?.supports), w = n(t?.weakens);
  return `<span class="gl-tally">${s ? `<span class="pill ok" title="${esc(['high', 'medium', 'low'].map(c => `${c} ${t.supports[c] || 0}`).join(' · '))}">Supports ${s}</span>` : ''}
    ${w ? `<span class="pill fail" title="${esc(['high', 'medium', 'low'].map(c => `${c} ${t.weakens[c] || 0}`).join(' · '))}">Weakens ${w}</span>` : ''}
    ${t?.none ? `<span class="pill">No effect ${esc(t.none)}</span>` : ''}</span>`;
};
function glNode(i, kids, d) {
  const sub = kids.get(i.task_id) || [];
  const waits = (i.depends_on || []).map(id => d.ideas.find(x => x.task_id === id)?.title).filter(Boolean);
  return `<li><a class="gl-node gr-is-${esc(i.status || 'idea')}" href="${grTaskHref(i.task_id)}"><span class="gl-t">${esc(i.title)}</span>${grStatus(i.status)}
    ${i.result ? `<span class="muted gl-r">${esc(i.result)}</span>` : ''}${waits.length ? `<span class="muted gl-r">Waits on ${esc(waits.join(', '))}</span>` : ''}</a>
    ${sub.length ? `<ul>${sub.map(k => glNode(k, kids, d)).join('')}</ul>` : ''}</li>`;
}
function glDraw(state) {
  const body = $('#gr-body'), d = state.data;
  if (!body || !d) return;
  const tools = grSeg('gl', 'View', [['tree', 'Tree'], ['log', 'Log']], state.view);
  let html = '';
  if (state.view === 'log') {
    const done = d.ideas.filter(i => i.verdict_at || GR_RESULTS.includes(i.status)).sort((a, b) => String(b.verdict_at || '').localeCompare(String(a.verdict_at || '')));
    const betOf = id => d.bets.find(b => b.id === id);
    html = done.length ? `<section class="card gr-sec gl-log">${done.map(i => {
      const bet = betOf(i.bet_id), effect = i.bet_effect && i.bet_effect !== 'none' ? (i.bet_effect === 'supports' ? 'Supports' : 'Weakens') : '';
      return `<a class="gr-row" href="${grTaskHref(i.task_id)}"><span class="gr-row-t">${esc(i.title)}</span>${grStatus(i.status)}<span class="muted gr-num">${esc(grDay(i.verdict_at))}</span>
        ${i.result || i.learning || bet ? `<span class="gr-row-l">${esc([i.result, i.learning, bet ? [`Bet ${bet.n}`, effect].filter(Boolean).join(' · ') : ''].filter(Boolean).join(' · '))}</span>` : ''}</a>`;
    }).join('')}</section>` : '';
  } else {
    const ids = new Set(d.ideas.map(i => i.task_id)), kids = new Map();
    for (const i of d.ideas) if (i.follow_up_of && ids.has(i.follow_up_of)) { if (!kids.has(i.follow_up_of)) kids.set(i.follow_up_of, []); kids.get(i.follow_up_of).push(i); }
    const roots = d.ideas.filter(i => !i.follow_up_of || !ids.has(i.follow_up_of));
    const groups = [...d.bets.map(b => ({id: b.id, label: `${b.n} · ${b.statement}`})), {id: '', label: 'No bet'}];
    html = groups.map(g => {
      const mine = roots.filter(i => g.id ? i.bet_id === g.id : !i.bet_id || !d.bets.some(b => b.id === i.bet_id));
      const t = d.ev.find(e => e.bet_id === g.id);
      if (!mine.length && !t) return '';
      return `<section class="card gl-bet"><header class="gl-bh"><h3>${esc(g.label)}</h3>${g.id ? glTally(t) : ''}</header>
        ${mine.length ? `<ul class="gl-tree">${mine.map(i => glNode(i, kids, d)).join('')}</ul>` : ''}</section>`;
    }).join('');
  }
  const vers = (d.gtm?.versions || []).slice().sort((a, b) => b.n - a.n);
  const gtm = vers.length ? `<section class="card gr-sec gl-vers" aria-label="Go-to-market versions"><h3 class="gr-k">Go-to-market versions</h3><ul class="gr-vers">${vers.map(x =>
    `<li><button type="button" class="gr-ver" data-gtm-ver="${esc(x.n)}"><b>v${esc(x.n)}</b><span>${esc(x.note || '')}</span><span class="muted">${esc(grWho(x.published_by))} · ${esc(grDay(x.published_at))}</span></button></li>`).join('')}</ul></section>` : '';
  body.innerHTML = grTools(tools) + `<div class="gl-body">${html}${gtm}</div>`;
  body.querySelectorAll('[data-gtm-ver]').forEach(b => { b.onclick = () => void grDocVersions('/v2/gtm', d.gtm, gtmBookHtml, b.dataset.gtmVer); });
  body.querySelector('[data-gr-seg="gl"]').onclick = ev => {
    const b = ev.target.closest('button[data-v]'); if (!b || b.dataset.v === state.view) return;
    state.view = b.dataset.v;
    history.replaceState(history.state, '', state.view === 'log' ? `${GR_LEARNING}?v=log` : GR_LEARNING);
    glDraw(state);
  };
}
