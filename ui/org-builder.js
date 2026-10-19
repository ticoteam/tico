/* First run, the org chart (docs/onboarding.md): pick departments, answer one short question per department, recruit
   bots into it, and watch the chart grow beside the conversation. The server has the departments and cards
   (GET /api/v2/setup/groups) and the suggestions (POST /api/v2/setup/recruit, from Tico HQ or its local
   recommender); this page shows them and keeps what is checked in the wizard's catalog state (`state.catalog.picked`),
   from which ui/first-run.js builds `selected`. Nothing exists until "Create my team". Built-in and message bots
   are not on the chart: the built-ins are always created, and a `kind: helper` card is
   one switch under Message bots on the finished chart, off until someone turns it on. */
'use strict';
const OB_ICONS = {goal: 'flag', go: 'arrow_forward', why: 'auto_awesome', edit: 'edit', chart: 'account_tree',
                  bot: 'smart_toy', expand: 'expand_more'};
// One hue per department; a department this page does not know gets one from its id.
const OB_COLORS = {sales: '#2f855a', marketing: '#c2417a', support: '#d97706', finance: '#0f766e', operations: '#4f46e5',
                   legal: '#64748b', hr: '#9333ea', product: '#0284c7', engineering: '#2563eb'};
// Suggested before anyone picks; a department marked software_only is added only when software is the product.
const OB_CORE = ['sales', 'marketing', 'support', 'finance', 'operations'];
// Long enough to see the chart take the bots in, short enough that nobody waits.
const OB_MIN_RECRUIT_MS = 900;

const obBlank = () => ({loaded: false, loading: null, error: '', departments: [], cards: [], version: '',
  hq: {available: false, off_by: ''}, phase: 'pick', chosen: [], at: 0, step: 'ask', briefings: {}, results: {},
  asked: {}, skipped: new Set(), decided: new Set(), share: null, editing: '', seen: new Set(), open: false,
  more: new Set(), touched: false});
const obColor = id => OB_COLORS[id] || `hsl(${avHue(id)} 45% 45%)`;
const obIcon = (name, cls = '') => `<span class="ob-ms ${cls}" aria-hidden="true">${esc(name || OB_ICONS.bot)}</span>`;
const obDept = (org, id) => org.departments.find(row => row.id === id);
const obDeptCards = (org, id) => org.cards.filter(card => card.department === id);
const obCatalogCard = (state, template) => state.catalog.cards.find(card => card.template === template);
const obCardOf = (state, slug) => state.org.cards.find(card => obCatalogCard(state, card.template)?.slug === slug);
const obPlural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
// A bot on the chart wears the avatar it will have once created: its blob, with its template's icon.
const obAvatar = (slug, card, size) => botAvatar({slug, icon: card.icon}, size);
const obHelpers = state => state.catalog.cards.filter(card => card.kind === 'helper');
const obCurrent = org => org.chosen[org.at];
function obHead(org, id) {
  const dept = obDept(org, id), cards = obDeptCards(org, id);
  return cards.some(card => card.template === dept?.head) ? dept.head : (cards.find(card => card.lead)?.template || '');
}
// The bots checked in one department: its head first, then in the order they were suggested, then by name.
function obPicked(state, id) {
  const org = state.org, head = obHead(org, id), order = (org.results[id]?.bots || []).map(row => row.template_id);
  const rank = card => (card.template === head ? -1 : (order.indexOf(card.template) + 1 || 999));
  return obDeptCards(org, id).filter(card => state.catalog.picked.has(obCatalogCard(state, card.template)?.slug))
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}
// A department head reports to the owner; everyone else to their head while the head is on the team.
function obDefaultReports(state, slug) {
  const card = obCardOf(state, slug), home = frHome(state);
  if (!card) return home;
  const head = obHead(state.org, card.department), headSlug = head && obCatalogCard(state, head)?.slug;
  return !head || card.template === head || !state.catalog.picked.has(headSlug) ? home : headSlug;
}
// While building, every department still in play counts; the finished chart counts the ones with bots, as it shows.
function obCounts(state, finished) {
  const org = state.org, sizes = org.chosen.filter(id => !org.skipped.has(id)).map(id => obPicked(state, id).length);
  return {depts: finished ? sizes.filter(Boolean).length : sizes.length, bots: sizes.reduce((a, b) => a + b, 0)};
}
const obStats = (state, finished) => { const {depts, bots} = obCounts(state, finished); return `${obPlural(depts, 'group')} · ${obPlural(bots, 'bot')}`; };
// A node animates in the first time it is drawn, never again.
function obFresh(state, key) {
  if (state.org.seen.has(key)) return '';
  state.org.seen.add(key);
  return ' ob-new';
}

// ----------------------------------------------------------------- loading and the saved draft
function obEnsure(state) {
  const org = state.org;
  if (org.loaded || org.loading) return;
  org.loading = get('/v2/setup/groups').then(data => {
    org.departments = data.departments || []; org.cards = data.cards || []; org.version = data.version || '';
    org.hq = {available: false, off_by: '', ...(data.hq || {})};
    if (org.share === null) org.share = !!org.hq.available;
    org.loaded = true;
    obRestore(state);
  }).catch(error => { org.error = error.message; })
    .finally(() => { org.loading = null; if (ONB === state && state.steps[state.step] === 'org') onbRender(state); });
}
function obSuggested(state) {
  const software = state.record.answers.software_product === 'yes';
  return state.org.departments.filter(d => (d.software_only ? software : OB_CORE.includes(d.id))).map(d => d.id);
}
// A returning draft keeps its departments, answers and bots, and opens on its chart; a first visit starts with the pick.
function obRestore(state) {
  const org = state.org, answers = state.record.answers, known = new Set(org.departments.map(d => d.id));
  const saved = (answers.departments || []).filter(id => known.has(id));
  const wanted = new Set(saved.length ? saved : obSuggested(state));
  org.briefings = {...(answers.briefings || {})};
  const picked = [...state.catalog.picked].filter(slug => !catalogCard(state.catalog, slug)?.required);
  // A saved bot brings its department onto the chart, so nothing picked is ever out of sight.
  for (const slug of picked) { const card = obCardOf(state, slug); if (card) { org.decided.add(card.department); wanted.add(card.department); } }
  org.chosen = org.departments.map(d => d.id).filter(id => wanted.has(id));
  org.phase = picked.length ? 'finish' : 'pick';
}
function obCollect(state) {
  const org = state.org, r = state.record;
  if (!org.loaded) return;
  const chosen = org.chosen.filter(id => !org.skipped.has(id));
  r.answers = {...r.answers, departments: chosen,
    briefings: Object.fromEntries(chosen.filter(id => (org.briefings[id] || '').trim()).map(id => [id, org.briefings[id].trim().slice(0, 500)]))};
  r.selected = frSelection(state);
}
// Every department answered saves the draft, so a closed tab keeps the chart.
function obSaveDraft(state) {
  obCollect(state);
  void onbSave(state).catch(() => {});
}

// ----------------------------------------------------------------- the chart
function obBotNodeHTML(state, card, head, big) {
  const cat = state.catalog, row = obCatalogCard(state, card.template);
  if (!row) return '';
  const slug = row.slug, editing = big && state.org.editing === slug, reports = frReports(state, slug);
  const moved = reports !== obDefaultReports(state, slug);
  const sub = moved ? `→ ${esc(frParentName(state, reports))}` : head ? 'Head' : '', name = catalogName(cat, row);
  // A long name wraps to two lines (ui/styles/first-run.css); the whole of it is the tooltip.
  return `<li class="oc-bot${head ? ' oc-head' : ''}${obFresh(state, 'b:' + slug)}" data-oc-bot="${esc(slug)}">
      <button type="button" class="oc-node oc-bnode" data-oc-edit="${esc(slug)}" title="${esc(name)}"${big ? ` aria-expanded="${editing}"` : ' tabindex="-1"'}>
        ${obAvatar(slug, card, 24)}<span class="oc-text"><strong data-oc-label="${esc(slug)}">${esc(name)}</strong>${sub ? `<small>${sub}</small>` : ''}</span></button>
      ${editing ? obEditHTML(state, slug) : ''}</li>`;
}
function obDeptNodeHTML(state, id, big) {
  const org = state.org, dept = obDept(org, id);
  if (!dept) return '';
  const bots = obPicked(state, id), cur = org.phase === 'dept' && obCurrent(org) === id;
  const status = org.skipped.has(id) ? 'skipped' : cur ? 'cur' : org.decided.has(id) ? 'done' : 'pending';
  const head = obHead(org, id);
  const sub = status === 'skipped' ? 'Skipped' : cur && org.step === 'recruiting' ? 'Recruiting…' : status === 'done' && !bots.length ? 'No bots' : '';
  const live = cur && org.step === 'recruiting' ? ' oc-recruiting' : '';
  // While its bots are being found, the group holds their places, so the chart never shows lines with nothing on them.
  const waiting = live && !bots.length ? `<ul class="oc-bots oc-wait" aria-hidden="true">${'<li class="oc-bot oc-ph"><span class="oc-node oc-bnode"><span class="sk-i"></span><span class="sk-l"></span></span></li>'.repeat(3)}</ul>` : '';
  return `<section class="oc-dept oc-${status}${live}${obFresh(state, 'd:' + id)}" data-oc-dept="${esc(id)}" style="--dc:${obColor(id)}">
      <button type="button" class="oc-node oc-dnode" data-oc-open="${esc(id)}">
        ${obIcon(dept.icon, 'oc-icon')}<span class="oc-text"><strong>${esc(dept.name)}</strong>${sub ? `<small${cur ? ' class="oc-live"' : ''}>${sub}</small>` : ''}</span>
        ${bots.length ? `<span class="oc-count">${bots.length}</span>` : ''}</button>
      ${bots.length ? `<ul class="oc-bots">${bots.map(card => obBotNodeHTML(state, card, card.template === head, big)).join('')}</ul>` : waiting}
    </section>`;
}
function obCols(state, count) {
  // Measured on whatever is on the page now: the chart itself on a redraw, else the wizard around it. The rows are
  // balanced, so five departments are three and two, not four and one.
  const width = ($('#ob-chart-big') || $('#ob-main') || $('#onb'))?.clientWidth || 900;
  const most = Math.max(1, Math.min(count || 1, 4, Math.floor((width - 40) / 220)));
  return Math.ceil((count || 1) / Math.ceil((count || 1) / most));
}
// The human at the top and each group hanging off one line with its bots below; built-in and message bots are not on it.
// Beside the conversation it is one column; the finished chart spreads the departments into rows.
function obChartHTML(state, big) {
  const org = state.org, me = S.me || {};
  const depts = big ? org.chosen.filter(id => obPicked(state, id).length) : org.chosen;
  const cols = big ? obCols(state, depts.length) : 1, rows = [];
  for (let i = 0; i < depts.length; i += cols) rows.push(depts.slice(i, i + cols));
  return `<div class="oc${big ? ' oc-big' : ''}" style="--cols:${cols}">
      <div class="oc-top"><div class="oc-node oc-ceo" data-oc-ceo>${personAvatar(me, 28)}<span class="oc-text"><strong>${esc(me.name || 'You')}</strong><small>CEO</small></span></div></div>
      ${rows.length ? `<div class="oc-rows">${rows.map(row => `<div class="oc-row" style="--k:${row.length}">${row.map(id => obDeptNodeHTML(state, id, big)).join('')}</div>`).join('')}</div>`
        : '<p class="oc-empty">Groups appear here.</p>'}
    </div>`;
}
function obEditHTML(state, slug) {
  const cat = state.catalog, card = catalogCard(cat, slug), current = frReports(state, slug);
  const groups = ['Humans', 'Bots on your team'].map(group => {
    const rows = frParentOptions(state, slug).filter(option => option.group === group);
    return rows.length ? `<optgroup label="${esc(group)}">${rows.map(option =>
      `<option value="${esc(option.value)}" ${option.value === current ? 'selected' : ''}>${esc(option.label)}</option>`).join('')}</optgroup>` : '';
  }).join('');
  return `<div class="oc-edit" data-oc-editor="${esc(slug)}">
      <label>Name<input type="text" data-oc-name="${esc(slug)}" value="${esc(catalogName(cat, card))}" maxlength="100"></label>
      <label>Reports to<select data-oc-reports="${esc(slug)}">${groups}</select></label>
      <div class="oc-edit-actions"><button class="ghost" type="button" data-oc-remove="${esc(slug)}">Remove</button>
        <span class="spacer"></span><button class="primary" type="button" data-oc-done>Done</button></div></div>`;
}
function obMailboxHTML(state, slug) {
  const people = catalogPeople(), chosen = state.catalog.edits[slug]?.person || '';
  return people.length ? `<label class="ob-mailbox">Whose mailbox<select data-cat-mailbox="${esc(slug)}" aria-label="Human whose mailbox this bot reads">
      <option value="">Choose a human…</option>${people.map(person => `<option value="${esc(person.id)}" ${person.id === chosen ? 'selected' : ''}>${esc(person.name || person.id)}</option>`).join('')}
    </select></label>` : '';
}
function obStripHTML(state) {
  const org = state.org;
  return `${obIcon(OB_ICONS.chart, 'ob-strip-icon')}<span class="ob-strip-text">${esc(obStats(state))}</span>
    <span class="ob-strip-mini">${org.chosen.filter(id => !org.skipped.has(id)).map(id =>
      `<span class="ob-mini" style="--dc:${obColor(id)}">${obIcon(obDept(org, id)?.icon)}${obPicked(state, id).length ? `<b>${obPicked(state, id).length}</b>` : ''}</span>`).join('')}</span>
    ${obIcon(OB_ICONS.expand, 'ob-strip-caret')}`;
}

// ----------------------------------------------------------------- the conversation
function obPickHTML(state) {
  const org = state.org, chosen = new Set(org.chosen), first = org.departments.find(d => chosen.has(d.id));
  return `<div class="ob-card ob-pick">
      <h2 class="ob-title">What groups do you want?</h2>
      <div class="ob-tiles" role="group" aria-label="Groups">${org.departments.map(d => `<button type="button" class="ob-tile${chosen.has(d.id) ? ' on' : ''}"
          data-ob-tile="${esc(d.id)}" aria-pressed="${chosen.has(d.id)}" style="--dc:${obColor(d.id)}">
          ${obIcon(d.icon, 'ob-tile-icon')}<span class="ob-tile-name">${esc(d.name)}</span><span class="ob-ms ob-tile-check" aria-hidden="true">check</span></button>`).join('')}</div>
      <div class="onb-actions"><button class="ghost" type="button" id="onb-back">Back</button><span class="spacer"></span>
        <button class="primary ob-cta" type="button" id="ob-start" ${first ? '' : 'disabled'}>${first ? `Start with ${esc(first.name)}` : 'Pick a group'}${obIcon(OB_ICONS.go)}</button></div>
    </div>`;
}
const obOffText = reason => ({demo: 'Off in the demo', DO_NOT_TRACK: 'Off: DO_NOT_TRACK is set', TICO_TELEMETRY: 'Off: TICO_TELEMETRY=off',
                              setting: 'Off: the usage count is off in Settings'})[reason] || 'Off';
function obHqHTML(state) {
  const org = state.org, off = !org.hq.available;
  return `<label class="ob-hq${off ? ' off' : ''}"><input type="checkbox" role="switch" id="ob-hq" aria-describedby="ob-hq-payload" ${org.share && !off ? 'checked' : ''} ${off ? 'disabled' : ''}>
      <span>Suggestions from Tico HQ</span></label><small id="ob-hq-payload">When on, sends your group, answer, team description, customer type, software choice, catalog version and install ID (when counting is on and its notice has been shown). <a href="https://github.com/ticoteam/tico/blob/main/PRIVACY.md#suggestions-while-you-build-your-team-chart" target="_blank" rel="noopener">Privacy</a></small>${off ? `<span class="ob-hq-off" data-ob-hq-off>${esc(obOffText(org.hq.off_by))}</span>` : ''}`;
}
function obBotCardHTML(state, card, why, head) {
  const row = obCatalogCard(state, card.template);
  if (!row) return '';
  const on = state.catalog.picked.has(row.slug);
  return `<div class="ob-bot-wrap"><label class="ob-bot${on ? ' on' : ''}" data-ob-bot="${esc(row.slug)}">
      <input type="checkbox" data-ob-pick="${esc(row.slug)}" ${on ? 'checked' : ''} aria-label="Add ${esc(catalogName(state.catalog, row))}">
      ${obAvatar(row.slug, card, 38)}
      <span class="ob-bot-body"><span class="ob-bot-name">${esc(catalogName(state.catalog, row))}${head ? '<span class="ob-head">Head</span>' : ''}</span>
        <span class="ob-bot-sum">${esc(card.summary)}</span>
        ${why ? `<span class="ob-bot-why" data-ob-why="${esc(row.slug)}">${obIcon(OB_ICONS.why)}${esc(why)}</span>` : ''}</span>
      <span class="ob-ms ob-bot-check" aria-hidden="true">check</span></label></div>`;
}
function obDeptHTML(state) {
  const org = state.org, id = obCurrent(org), dept = obDept(org, id), step = org.step;
  const briefing = org.briefings[id] || '', last = org.at === org.chosen.length - 1;
  const next = last ? null : obDept(org, org.chosen[org.at + 1]);
  const said = briefing ? `<div class="ob-said"><button type="button" class="ob-said-edit" id="ob-edit-answer" aria-label="Change the answer">${obIcon(OB_ICONS.edit)}</button>
      <span class="ob-bubble" data-ob-said>${esc(briefing)}</span></div>` : '';
  let body = '';
  if (step === 'ask') body = `<form class="ob-ask" id="ob-ask" autocomplete="off">
      <label class="ob-question" for="ob-brief">${esc(dept.question)}</label>
      <div class="ob-compose"><input id="ob-brief" type="text" maxlength="500" placeholder="${esc(dept.placeholder)}" value="${esc(briefing)}">
        <button class="primary ob-cta" type="submit" id="ob-go">Recruit bots${obIcon(OB_ICONS.go)}</button></div>
      <div class="ob-hq-row">${obHqHTML(state)}</div></form>`;
  else if (step === 'recruiting') body = `${said}
      <div class="ob-recruiting" role="status" aria-live="polite" id="ob-recruiting"><span class="ob-radar" aria-hidden="true"></span>Recruiting bots…</div>
      <div class="ob-bots ob-skeleton" aria-hidden="true">${'<div class="ob-bot sk"><span class="sk-i"></span><span class="sk-l"><i></i><i></i><i></i></span></div>'.repeat(3)}</div>`;
  else {
    const result = org.results[id] || {bots: []}, head = obHead(org, id);
    const shown = result.bots.map(row => [org.cards.find(card => card.template === row.template_id && card.department === id), row.why]).filter(([card]) => card);
    const listed = new Set(shown.map(([card]) => card.template));
    const more = obDeptCards(org, id).filter(card => !listed.has(card.template)).sort((a, b) => a.name.localeCompare(b.name));
    // The list under More stays as the person left it through a redraw; it starts open when a bot in it is already checked.
    const moreOpen = org.more.has(id) || more.some(card => state.catalog.picked.has(obCatalogCard(state, card.template)?.slug));
    body = `${said}
      <h3 class="ob-found" data-ob-source="${esc(result.source || '')}">Who joins ${esc(dept.name)}?</h3>
      <div class="ob-bots" id="ob-suggested">${shown.map(([card, why]) => obBotCardHTML(state, card, why, card.template === head)).join('')
        || '<p class="muted">Nothing to suggest yet. Look under More.</p>'}</div>
      ${more.length ? `<div class="ob-more${moreOpen ? ' open' : ''}" id="ob-more">
        <button type="button" class="ob-more-row" id="ob-more-toggle" aria-expanded="${moreOpen}" aria-controls="ob-more-list">More in ${esc(dept.name)} <span class="muted">${more.length}</span></button>
        <div class="ob-bots" id="ob-more-list"${moreOpen ? '' : ' hidden'}>${more.map(card => obBotCardHTML(state, card, '', card.template === head)).join('')}</div></div>` : ''}`;
  }
  const forward = step === 'suggest' ? `<button class="primary ob-cta" type="button" id="ob-next">${next ? `Next: ${esc(next.name)}` : 'See team chart'}${obIcon(OB_ICONS.go)}</button>` : '';
  return `<article class="ob-card ob-dept" data-ob-dept="${esc(id)}" data-ob-step="${esc(step)}" style="--dc:${obColor(id)}">
      <header class="ob-dept-head">${obIcon(dept.icon, 'ob-dept-icon')}<div><div class="ob-kicker">${org.at + 1} of ${org.chosen.length}</div><h2>${esc(dept.name)}</h2></div></header>
      <p class="ob-desc">${esc(dept.description)}</p>
      <p class="ob-goal">${obIcon(OB_ICONS.goal)}<span>${esc(dept.goal)}</span></p>
      ${body}
      <div class="onb-actions ob-nav"><button class="ghost" type="button" id="ob-back">Back</button>
        <button class="ghost" type="button" id="ob-skip">Skip group</button><span class="spacer"></span>${forward}</div>
    </article>`;
}
// Message bots sit apart from the chart: one switch each, off by default, and the Inbox Manager asks whose mailbox here.
function obHelpersHTML(state) {
  const cat = state.catalog, cards = obHelpers(state);
  if (!cards.length) return '';
  return `<section class="ob-helpers" id="ob-helpers" aria-labelledby="ob-helpers-h"><h3 id="ob-helpers-h">Message bots</h3>${cards.map(card => {
    const on = cat.picked.has(card.slug);
    return `<div class="ob-helper${on ? ' on' : ''}" data-ob-helper-row="${esc(card.slug)}"><label class="ob-helper-toggle">${obAvatar(card.slug, card, 28)}
        <span class="ob-helper-name">${esc(catalogName(cat, card))}</span><input type="checkbox" role="switch" data-ob-helper="${esc(card.slug)}" ${on ? 'checked' : ''}></label>
      ${on && card.template === 'inbox' ? obMailboxHTML(state, card.slug) : ''}</div>`;
  }).join('')}</section>`;
}
function obFinishHTML(state) {
  return `<div class="ob-card ob-finish">
      <h2 class="ob-title" id="ob-summary">${esc(obStats(state, true))}</h2>
      <div class="ob-canvas" id="ob-chart-big">${obChartHTML(state, true)}</div>
      ${obHelpersHTML(state)}
      <p class="err" id="team-problem" role="alert" hidden></p>
      <div class="onb-actions"><button class="ghost" type="button" id="ob-back">Back</button>
        <button class="ghost" type="button" id="ob-departments">Groups</button><span class="spacer"></span>
        <span class="muted" id="onb-status"></span><button class="primary" type="button" id="onb-next">Next</button></div>
    </div>`;
}
function obMainHTML(state) {
  const org = state.org;
  if (org.error) return `<div class="ob-card"><p class="err">${esc(org.error)}</p><div class="onb-actions"><button class="ghost" type="button" id="onb-back">Back</button></div></div>`;
  if (!org.loaded) return '<div class="ob-card"><div class="empty">Loading…</div></div>';
  return org.phase === 'finish' ? obFinishHTML(state) : org.phase === 'dept' ? obDeptHTML(state) : obPickHTML(state);
}
// The wizard's org step: the conversation, and the chart beside it (a strip above it on a phone).
function obStepHTML(state) {
  const org = state.org;
  obEnsure(state);
  return `<div class="ob ob-phase-${org.phase}" id="ob">
      <aside class="ob-side${org.open ? ' open' : ''}" id="ob-side" aria-label="Team chart">
        <button type="button" class="ob-strip" id="ob-strip" aria-expanded="${org.open}">${obStripHTML(state)}</button>
        <div class="ob-chart" id="ob-chart"><div class="ob-chart-head">${obIcon(OB_ICONS.chart)}<span>Team chart</span><b id="ob-chart-stats">${esc(obStats(state))}</b></div>
          <div id="ob-chart-body">${org.loaded && org.phase !== 'finish' ? obChartHTML(state, false) : ''}</div></div>
      </aside>
      <div class="ob-main" id="ob-main">${obMainHTML(state)}</div>
    </div>`;
}
function obRenderChart(state, side) {
  const org = state.org;
  if (org.phase === 'finish' && !side) {
    const big = $('#ob-chart-big');
    if (big) big.innerHTML = obChartHTML(state, true);
    const summary = $('#ob-summary');
    if (summary) summary.textContent = obStats(state, true);
  } else if (org.phase !== 'finish') {
    const body = $('#ob-chart-body');
    if (body) body.innerHTML = obChartHTML(state, false);
  }
  const stats = $('#ob-chart-stats'); if (stats) stats.textContent = obStats(state);
  const strip = $('#ob-strip'); if (strip) strip.innerHTML = obStripHTML(state);
}
function obRender(state) {
  const root = $('#ob');
  if (!root) { onbRender(state); return; }
  root.className = `ob ob-phase-${state.org.phase}`;
  $('#ob-main').innerHTML = obMainHTML(state);
  obRenderChart(state, true);          // the finished chart was just drawn with the main pane
  const back = $('#onb-back');       // the pick's Back is the wizard's own
  if (back) back.onclick = () => { onbCollect(state, 'org'); state.step = Math.max(0, state.step - 1); onbRender(state); };
  const next = $('#onb-next');
  if (next) next.onclick = () => void onbAdvance(state, 'org');
  const focus = state.org.phase === 'dept' && state.org.step === 'ask' ? $('#ob-brief') : null;
  if (focus && window.matchMedia?.('(min-width: 761px)').matches) focus.focus({preventScroll: true});
}

// ----------------------------------------------------------------- moving through it
function obGo(state, at) {
  const org = state.org;
  org.editing = '';
  if (at < 0) org.phase = 'pick';
  else if (at >= org.chosen.length) org.phase = 'finish';
  else { org.phase = 'dept'; org.at = at; org.step = org.results[org.chosen[at]] ? 'suggest' : 'ask'; }
  obRender(state);
  // The new card starts in view, below the chart's strip on a phone.
  const main = $('#ob-main'), strip = $('#ob-strip');
  if (main && main.getBoundingClientRect().top < (strip?.offsetHeight || 0)) main.scrollIntoView({block: 'start'});
}
// The head, the department's `default` cards and whatever the answer's source said to add start checked; the rest wait.
function obApplyDefaults(state, id, result) {
  const org = state.org, cat = state.catalog, head = obHead(org, id);
  const shown = new Set(result.bots.map(row => row.template_id)), wanted = new Set(result.suggested_default || []);
  for (const card of obDeptCards(org, id)) {
    const slug = obCatalogCard(state, card.template)?.slug;
    if (!slug) continue;
    const on = card.template === head || wanted.has(card.template) || (card.suggest === 'default' && shown.has(card.template));
    if (on) cat.picked.add(slug); else cat.picked.delete(slug);
  }
}
async function obRecruit(state) {
  const org = state.org, id = obCurrent(org), briefing = ($('#ob-brief')?.value || '').trim().slice(0, 500);
  const share = !!(org.share && org.hq.available), key = JSON.stringify([briefing, share]);
  org.briefings[id] = briefing;
  org.touched = true;
  if (org.results[id] && org.asked[id] === key) { org.step = 'suggest'; obRender(state); return; }
  org.step = 'recruiting';
  obRender(state);
  const started = Date.now();
  let result;
  try { result = await post('/v2/setup/recruit', {department: id, briefing, share}); }
  catch { result = null; }
  await new Promise(resolve => setTimeout(resolve, Math.max(0, OB_MIN_RECRUIT_MS - (Date.now() - started))));
  if (ONB !== state || obCurrent(org) !== id || org.step !== 'recruiting' || org.phase !== 'dept') return;
  if (!result?.bots?.length) {
    // Nothing came back at all: the head and the department's usual cards, so the person still has a choice.
    const head = obHead(org, id), usual = obDeptCards(org, id).filter(card => card.template === head || card.suggest !== 'niche');
    result = {bots: usual.sort((a, b) => (b.template === head) - (a.template === head)).map(card => ({template_id: card.template, why: ''})),
              suggested_default: head ? [head] : [], source: 'local'};
  }
  org.results[id] = result; org.asked[id] = key;
  obApplyDefaults(state, id, result);
  org.decided.add(id); org.skipped.delete(id);
  org.step = 'suggest';
  obRender(state);
}
function obBack(state) {
  const org = state.org;
  if (org.phase === 'finish') { obGo(state, org.chosen.length - 1); return; }
  if (org.step === 'suggest' || org.step === 'recruiting') { org.step = 'ask'; obRender(state); return; }
  obGo(state, org.at - 1);
}
function obSkip(state) {
  const org = state.org, id = obCurrent(org);
  for (const card of obDeptCards(org, id)) { const slug = obCatalogCard(state, card.template)?.slug; if (slug) state.catalog.picked.delete(slug); }
  org.skipped.add(id); org.decided.delete(id); org.touched = true;
  obSaveDraft(state);
  obGo(state, org.at + 1);
}
function obNext(state) {
  const org = state.org;
  org.decided.add(obCurrent(org)); org.skipped.delete(obCurrent(org));
  obSaveDraft(state);
  obGo(state, org.at + 1);
}
function obWire(state) {
  const root = $('#ob');
  if (!root) return;
  const org = state.org;
  root.onclick = event => {
    const target = event.target;
    const tile = target.closest('[data-ob-tile]');
    if (tile) {
      const id = tile.dataset.obTile, on = !org.chosen.includes(id);
      const wanted = new Set(org.chosen); if (on) wanted.add(id); else wanted.delete(id);
      org.chosen = org.departments.map(d => d.id).filter(d => wanted.has(d));
      if (on) org.skipped.delete(id);
      else {                                    // a department taken off takes its bots with it
        for (const card of obDeptCards(org, id)) { const slug = obCatalogCard(state, card.template)?.slug; if (slug) state.catalog.picked.delete(slug); }
        org.decided.delete(id);
      }
      org.touched = true;
      obRender(state);
      return;
    }
    if (target.closest('#ob-start')) { obGo(state, 0); return; }
    if (target.closest('#ob-back')) { obBack(state); return; }
    if (target.closest('#ob-skip')) { obSkip(state); return; }
    if (target.closest('#ob-next')) { obNext(state); return; }
    if (target.closest('#ob-edit-answer')) { org.step = 'ask'; obRender(state); return; }
    if (target.closest('#ob-departments')) { obGo(state, -1); return; }
    if (target.closest('#ob-more-toggle')) {
      const id = org.chosen[org.at], open = !$('#ob-more').classList.contains('open');
      if (open) org.more.add(id); else org.more.delete(id);
      $('#ob-more').classList.toggle('open', open);
      $('#ob-more-toggle').setAttribute('aria-expanded', String(open));
      $('#ob-more-list').hidden = !open;
      return;
    }
    if (target.closest('#ob-strip')) { org.open = !org.open; $('#ob-side')?.classList.toggle('open', org.open); $('#ob-strip')?.setAttribute('aria-expanded', String(org.open)); return; }
    const open = target.closest('[data-oc-open]');
    if (open) {
      const at = org.chosen.indexOf(open.dataset.ocOpen);
      if (at >= 0 && (org.phase === 'finish' || org.decided.has(open.dataset.ocOpen) || org.skipped.has(open.dataset.ocOpen))) obGo(state, at);
      return;
    }
    const remove = target.closest('[data-oc-remove]');
    if (remove) { state.catalog.picked.delete(remove.dataset.ocRemove); org.editing = ''; org.touched = true; obRenderChart(state); return; }
    if (target.closest('[data-oc-done]')) { org.editing = ''; obRenderChart(state); return; }
    const edit = target.closest('[data-oc-edit]');
    if (edit && org.phase === 'finish') { org.editing = org.editing === edit.dataset.ocEdit ? '' : edit.dataset.ocEdit; obRenderChart(state); $(`[data-oc-name="${cssSelectorValue(org.editing)}"]`)?.focus(); }
  };
  root.onsubmit = event => {
    if (event.target.id !== 'ob-ask') return;
    event.preventDefault();
    void obRecruit(state);
  };
  root.onchange = event => {
    const target = event.target;
    if (target.id === 'ob-hq') { org.share = target.checked; return; }
    const helper = target.closest('[data-ob-helper]');
    if (helper) {
      if (helper.checked) state.catalog.picked.add(helper.dataset.obHelper); else state.catalog.picked.delete(helper.dataset.obHelper);
      org.touched = true;
      obRender(state);                                     // the Inbox Manager asks whose mailbox
      return;
    }
    const pick = target.closest('[data-ob-pick]');
    if (pick) {
      const slug = pick.dataset.obPick;
      if (pick.checked) state.catalog.picked.add(slug); else state.catalog.picked.delete(slug);
      org.touched = true;
      pick.closest('.ob-bot')?.classList.toggle('on', pick.checked);
      obRenderChart(state);
      return;
    }
    const mailbox = target.closest('[data-cat-mailbox]');
    if (mailbox) { (state.catalog.edits[mailbox.dataset.catMailbox] ||= {}).person = mailbox.value; return; }
    const reports = target.closest('[data-oc-reports]');
    if (reports) { (state.catalog.edits[reports.dataset.ocReports] ||= {}).reports_to = reports.value; org.touched = true; obRenderChart(state); }
  };
  root.oninput = event => {
    const name = event.target.closest('[data-oc-name]');
    if (!name) return;
    const slug = name.dataset.ocName;
    (state.catalog.edits[slug] ||= {}).display_name = name.value;
    state.catalog.touched.add(slug);
    const label = document.querySelector(`[data-oc-label="${cssSelectorValue(slug)}"]`);
    if (label) { label.textContent = name.value; label.closest('[data-oc-edit]')?.setAttribute('title', name.value); }
  };
  if (!window.obResizeWired) {
    window.obResizeWired = true;
    let timer = null;
    window.addEventListener('resize', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (ONB?.org?.phase === 'finish' && $('#ob-chart-big')) obRenderChart(ONB); }, 150);
    });
  }
}
// The advance from the chart: a cycle or a missing mailbox is said here, not after Create.
function obCanAdvance(state) {
  const problem = frProblem(state), box = $('#team-problem');
  if (box) { box.textContent = problem; box.hidden = !problem; }
  return !problem;
}
