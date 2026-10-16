/* Market is its own room: a note, the graph of who connects to whom, and Ask the Librarian beside it
   (ui/docs-ask.js), which reads the graph at the moment you ask. It is not the Docs library. */
'use strict';
let MARKET_VIEW = null;

const MARKET_TYPES = [
  ['company', 'Organizations'],
  ['segment', 'Segments'],
  ['channel', 'Channels'],
  ['person', 'People'],
  ['product', 'Products'],
  ['regulation', 'Regulation'],
  ['geography', 'Places'],
  ['event', 'Events'],
];

const MARKET_BRIEF = 'brief';

// Nothing about the market is written here: the Overview is the page the Librarian writes
// (playbooks/market-setup.md). Until the market has content the page is its own empty state, drawn in
// the app's theme rather than the graph's dark room: the owner's one box, or the notice while it works.
const marketStyle = document.createElement('style');
marketStyle.textContent = `.market-shell.market-shell-blank{display:flex;align-items:center;justify-content:center;overflow:auto;padding:32px 16px 12vh;background:var(--bg);color:var(--ink)}
.market-start{width:min(560px,100%);display:grid;gap:10px;margin:0}
.market-start h1{margin:0 0 6px;font-size:22px;line-height:1.25;font-weight:650;color:var(--ink)}
.market-start label{font-size:13px;color:var(--muted)}
.market-start textarea{min-height:112px;font:inherit;font-size:14px}
.market-start .err{margin:0;font-size:13px}
.market-start-actions{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:4px}
.market-start-actions .primary{padding:9px 18px}
.market-start-actions a{font-size:13.5px}
.market-researching{width:min(560px,100%);display:flex;align-items:flex-start;gap:14px}
.market-researching strong{display:block;font-size:16px;font-weight:600;color:var(--ink)}
.market-researching p{margin:4px 0 0;color:var(--muted);font-size:13.5px}
.market-spin{flex:none;width:18px;height:18px;margin-top:2px;border-radius:50%;border:2px solid var(--line);border-top-color:var(--accent);animation:marketspin .9s linear infinite}
@keyframes marketspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.market-spin{animation:none;border-color:var(--accent)}}
.market-read-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.market-research{flex:none;padding:3px 10px;border-radius:6px;background:none;border:1px solid var(--line);color:var(--ink);font-size:12.5px;cursor:pointer}
.market-research:hover{background:var(--surface2)}
.market-research:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.market-none{margin:0;color:var(--muted);font-size:14px}
.market-blank{margin:0;color:var(--muted)}
.market-ask-open{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;margin:0 0 10px;padding:7px 10px;border-radius:7px;background:none;border:1px solid var(--line);color:var(--ink);font-size:13px;cursor:pointer}
.market-ask-open .nav-icon{font-size:17px;color:var(--accent)}
.market-ask-open:hover{background:var(--surface2)}
.market-ask-open:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
@media (max-width:600px){.market-shell.market-shell-blank{align-items:flex-start;padding:28px 16px}.market-start-actions .primary{flex:1}}`;
document.head.appendChild(marketStyle);

// The node for your own team sits at the centre of the graph. Tico may name it in its
// status (company_entity); otherwise the convention is company/self.
const marketSelfId = () => (typeof S !== 'undefined' && S.status && S.status.company_entity) || 'company/self';

function marketNoteId() {
  if (S.route.startsWith('#/market/')) return decodeURIComponent(S.route.slice('#/market/'.length).split('?')[0]);
  return new URLSearchParams((S.route.split('?')[1] || '')).get('note') || MARKET_BRIEF;
}

function marketHref(id) {
  return '#/market?note=' + encodeURIComponent(id);
}

window.marketStop = function marketStop() {
  if (MARKET_VIEW) MARKET_VIEW.stop();
};

// Draws the page again from the server, when the research notice sees the market's first content.
window.marketReload = function marketReload() {
  if (!MARKET_VIEW || !(S.route === '#/market' || S.route.startsWith('#/market?') || S.route.startsWith('#/market/'))) return;
  MARKET_VIEW.stop();
  MARKET_VIEW = null;
  void pageMarket();
};

window.pageMarket = async function pageMarket() {
  $('#main').classList.add('market-layout');
  const note = marketNoteId();
  if (!MARKET_VIEW || !document.body.contains(MARKET_VIEW.shell)) {
    $('#main').innerHTML = '<div id="market-shell" class="market-shell"><p class="market-loading">Opening the market…</p></div>';
    const shell = $('#market-shell');
    try {
      MARKET_VIEW = await mountMarket(shell);
    } catch (error) {
      shell.innerHTML = `<p class="market-loading">${esc(error.message || 'The market graph did not load.')}</p>`;
      window.syncLibrarianRail?.(true);
      return;
    }
  }
  await MARKET_VIEW.open(note);
  if (S.route.startsWith('#/market')) window.syncLibrarianRail?.(true);
};
// What Ask the Librarian needs from the graph (ui/docs-ask.js): a name for each row it cites, and those rows lit up.
window.marketName = id => MARKET_VIEW?.name?.(id) || '';
window.marketCite = ids => MARKET_VIEW?.cite?.(ids);

async function mountMarket(shell) {
  const today = new Date().toISOString().slice(0, 10);
  const graphUrl = (id, fill = false, include = []) => {
    const params = new URLSearchParams({focus: id, fill, limit: 250, as_of: today});
    include.slice(0, 20).forEach(id => params.append('include', id));
    return '/v2/market/graph?' + params;
  };
  const [initial, listing, library] = await Promise.all([
    get(graphUrl(marketSelfId(), true)),
    get('/v2/market/entities?status=active&limit=50'),
    get('/company-docs?collection=market'),
  ]);
  const nodes = initial.entities || [];
  const byId = new Map(nodes.map(row => [row.id, row]));
  const links = initial.edges || [];
  (listing.entities || []).forEach(row => byId.set(row.id, row));
  // A fresh install starts with pages the seed wrote ("None in the seed."): they are not shown until the graph has rows.
  const pages = (library.documents || []).filter(doc => nodes.length || !doc.seeded).sort((a, b) => a.title.localeCompare(b.title));
  let researching = marketResearchGet();
  const blank = !nodes.length && !pages.length;
  if (researching && !blank && Date.now() - Number(researching.at) >= MARKET_RESEARCH_MIN) { marketResearchSet(null); researching = null; }
  if (blank || researching) return mountBlank(shell);
  shell.innerHTML = `<aside class="market-list" aria-label="Market notes">
      <a id="market-overview" class="market-overview-btn" href="${marketHref(MARKET_BRIEF)}">Overview</a>
      <input id="market-filter" type="search" placeholder="Search the market" aria-label="Search the market" autocomplete="off">
      <button class="market-ask-open" type="button" data-librarian-open><span class="nav-icon" aria-hidden="true">auto_awesome</span>Ask the Librarian</button>
      <div id="market-index"></div>
    </aside>
    <section class="market-note">
      <div id="market-read" class="market-read"></div>
    </section>
    <div class="market-graph">
      <button id="market-reset" class="market-research market-reset" type="button">Reset</button>
      <canvas id="market-canvas" aria-label="Graph of the market"></canvas>
    </div>`;
  const graph = mountGraph($('#market-canvas'), nodes, links);
  const index = $('#market-index');
  const filter = $('#market-filter');

  let listVersion = 0, listTimer = 0, stopped = false, graphVersion = 0, openVersion = 0;
  let graphFocus = marketSelfId(), graphDefault = true;

  async function loadGraph(id, fill = false, include = []) {
    const version = ++graphVersion;
    if (!include.length && graphFocus === id && graphDefault === fill) { graph.focus(fill ? null : id); return; }
    const data = await get(graphUrl(id, fill, include));
    if (stopped || version !== graphVersion) return;
    (data.entities || []).forEach(row => byId.set(row.id, row));
    graph.update(data.entities || [], data.edges || [], fill ? null : id);
    graphFocus = id; graphDefault = fill;
  }
  $('#market-reset').onclick = () => void loadGraph(marketSelfId(), true).catch(error => toast(error.message));

  function renderIndex(data, query, offset = 0) {
    const q = query.trim().toLowerCase();
    const match = row => !q || (row.name || row.title || '').toLowerCase().includes(q) || (row.id || '').toLowerCase().includes(q);
    const rows = data.entities || [];
    rows.forEach(row => byId.set(row.id, row));
    const groups = [['Pages', pages.filter(doc => doc.id !== 'market/overview' && match(doc)).map(doc => ({id: doc.id, name: doc.title}))]];
    for (const [type, label] of MARKET_TYPES) {
      const typed = rows.filter(row => row.type === type);
      if (typed.length) groups.push([label, typed]);
    }
    index.innerHTML = groups.filter(([, rows]) => rows.length).map(([label, rows]) =>
      `<section><h2>${esc(label)}</h2><ul>${rows.map(row =>
        `<li><a href="${marketHref(row.id)}" data-market-note="${esc(row.id)}"${row.id === marketNoteId() ? ' class="cur" aria-current="page"' : ''}>${esc(row.name || row.title)}</a></li>`).join('')}</ul></section>`
    ).join('') || (q ? '<p class="market-empty">Nothing matches.</p>' : '');
    index.insertAdjacentHTML('beforeend', `<div class="market-paging">
      ${offset ? '<button type="button" data-market-prev>Previous</button>' : ''}
      ${data.next_offset != null ? '<button type="button" data-market-next>Next</button>' : ''}</div>`);
    const prev = index.querySelector('[data-market-prev]'), next = index.querySelector('[data-market-next]');
    if (prev) prev.onclick = () => void fetchIndex(query, Math.max(0, offset - 50));
    if (next) next.onclick = () => void fetchIndex(query, data.next_offset);
  }
  async function fetchIndex(query, offset = 0) {
    const version = ++listVersion;
    try {
      const data = await get('/v2/market/entities?' + new URLSearchParams({status: 'active', limit: 50, offset, q: query}));
      if (!stopped && version === listVersion) renderIndex(data, query, offset);
    } catch (error) { if (!stopped && version === listVersion) index.textContent = error.message; }
  }
  renderIndex(listing, '');
  filter.oninput = () => {
    clearTimeout(listTimer); ++listVersion;
    listTimer = setTimeout(() => void fetchIndex(filter.value), 180);
  };
  // Research with the Librarian on the page or entry being read (ui/docs-editor.js).
  let shownBody = '';
  $('#market-read').addEventListener('click', event => {
    const button = event.target.closest('[data-market-research]');
    if (button) DocsEditor.researchDialog({target: button.dataset.marketResearch, body: shownBody});
  });

  async function open(id) {
    const version = ++openVersion;
    const overview = $('#market-overview');
    const onBrief = id === MARKET_BRIEF;
    overview.classList.toggle('cur', onBrief);
    if (onBrief) overview.setAttribute('aria-current', 'page'); else overview.removeAttribute('aria-current');
    index.querySelectorAll('a').forEach(link => {
      const on = link.dataset.marketNote === id;
      link.classList.toggle('cur', on);
      if (on) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    const read = $('#market-read');
    const showDoc = async doc => {
      const full = await get('/company-docs/' + doc.id.split('/').map(encodeURIComponent).join('/'));
      if (stopped || version !== openVersion) return;
      shownBody = full.content || '';
      read.innerHTML = `${marketHead('Market', doc.id === 'market/weekly-delta' ? '' : doc.id)}<div class="md market-md">${safeMd(shownBody)}</div>`;
      if (onBrief) await loadGraph(marketSelfId(), true); else { ++graphVersion; graph.focus(null); }
    };
    if (onBrief) {
      const overview = pages.find(row => row.id === 'market/overview');
      if (overview) { await showDoc(overview); return; }
      read.innerHTML = '<p class="market-blank">Nothing here yet.</p>';
      await loadGraph(marketSelfId(), true);
      return;
    }
    const doc = pages.find(row => row.id === id);
    if (!doc) {
      try {
        const [shown] = await Promise.all([
          get('/v2/market/entities/' + id.split('/').map(encodeURIComponent).join('/') + '?names=true'), loadGraph(id),
        ]);
        if (stopped || version !== openVersion) return;
        byId.set(id, shown.entity);
        (shown.neighbors || []).forEach(row => byId.set(row.id, row));
        shownBody = '';
        read.innerHTML = renderEntity(shown, byId);
        graph.focus(id);
        const current = index.querySelector('a.cur');
        if (current) current.scrollIntoView({block: 'nearest'});
      } catch (error) {
        if (!stopped && version === openVersion) {
          read.innerHTML = `<p class="market-empty">${esc(error.message || 'That note is not in the market.')}</p>`;
          graph.focus(null);
        }
      }
      return;
    }
    await showDoc(doc);
  }

  return {shell, open, stop() { stopped = true; clearTimeout(listTimer); graph.stop(); },
    name: id => byId.get(id)?.name || '',
    async cite(ids) {
      if (ids[0]) await loadGraph(ids[0], false, ids).catch(error => toast(error.message));
      if (!stopped) graph.cite(ids);
    }};
}

// ---------------------------------------------------------------- an empty market
// The owner's one box: whatever they have about their market. The Librarian reads it and builds the map
// (templates/catalog/librarian/playbooks/market-setup.md); "Attach files" is the Docs import. Then a notice,
// kept in this browser with no server involved: it shows for at least two minutes and until the market has
// content, and goes for good after thirty. While it shows, the market is read every thirty seconds (two
// small GETs), and the page is drawn again once the notice is done.
const MARKET_RESEARCH_KEY = 'tico.market.researching';
const MARKET_RESEARCH_MIN = 2 * 60 * 1000, MARKET_RESEARCH_MAX = 30 * 60 * 1000, MARKET_RESEARCH_POLL = 30 * 1000;
let MARKET_RESEARCH = null;        // the same record in memory, for a browser that will not store it

function marketResearchGet() {
  let row = MARKET_RESEARCH;
  try { row = JSON.parse(localStorage.getItem(MARKET_RESEARCH_KEY) || 'null') || row; } catch { /* the memory copy stands */ }
  const age = row ? Date.now() - Number(row.at) : NaN;
  if (row && age >= -60000 && age < MARKET_RESEARCH_MAX) return row;
  if (row) marketResearchSet(null);
  return null;
}

function marketResearchSet(row) {
  MARKET_RESEARCH = row;
  try { if (row) localStorage.setItem(MARKET_RESEARCH_KEY, JSON.stringify(row)); else localStorage.removeItem(MARKET_RESEARCH_KEY); } catch { /* memory only */ }
}

// What the market holds, in one string: how many entities, and when its newest page was written. The seed's
// pages are already there when the owner asks, so "has content" means "differs from what it was when asked".
async function marketSig() {
  const [entities, pages] = await Promise.all([get('/v2/market/entities?limit=1'), get('/company-docs?collection=market')]);
  const written = (pages.documents || []).filter(doc => doc.id !== 'market/weekly-delta').map(doc => String(doc.fetched || ''));
  return `${(entities.total ?? (entities.entities || []).length)}|${written.reduce((a, b) => a > b ? a : b, '')}`;
}

function marketBlankHtml(row) {
  if (row) return `<div class="market-researching" data-market-researching role="status">
      <span class="market-spin" aria-hidden="true"></span>
      <div><strong>The Librarian is researching your market.</strong>
        <p>This usually takes 5–10 minutes.${row.task ? ` <a href="#/task/${esc(row.task)}">View task</a>` : ''}</p></div></div>`;
  if (!(S.me?.cloud && S.me.role === 'owner')) return '<p class="market-none">Nothing here yet.</p>';
  return `<form class="market-start" data-market-research>
      <h1>Market${marketBeta()}</h1>
      <label for="market-sources">Your website, a description, or links to anything about your market</label>
      <textarea id="market-sources" name="text" rows="4" maxlength="8000" required autocomplete="off" placeholder="https://example.com"></textarea>
      <p class="err" data-market-error hidden></p>
      <div class="market-start-actions"><button class="primary" type="submit">Start research</button>
        <a href="#/docs?import=1">Attach files</a></div>
    </form>`;
}

function mountBlank(shell) {
  let poll = 0;
  const watch = on => {
    if (!on) { clearInterval(poll); poll = 0; } else if (!poll) poll = setInterval(() => void tick(), MARKET_RESEARCH_POLL);
  };
  const draw = () => {
    const row = marketResearchGet();
    shell.classList.add('market-shell-blank');
    shell.innerHTML = marketBlankHtml(row);
    watch(!!row);
  };
  async function tick() {
    const row = marketResearchGet();
    if (!row) { watch(false); window.marketReload(); return; }          // thirty minutes passed: the box is back
    let sig;
    try { sig = await marketSig(); } catch { return; }                   // the next tick tries again
    const arrived = row.base == null ? !sig.startsWith('0|') : sig !== row.base;
    if (arrived && Date.now() - Number(row.at) >= MARKET_RESEARCH_MIN) {
      marketResearchSet(null);
      watch(false);
      window.marketReload();
    }
  }
  shell.onsubmit = async event => {
    const form = event.target.closest('[data-market-research]');
    if (!form) return;
    event.preventDefault();
    const line = form.querySelector('[data-market-error]'), button = form.querySelector('[type=submit]');
    const fail = error => { line.textContent = error.message || 'That did not go through.'; line.hidden = false; button.disabled = false; };
    const text = form.elements.text.value.trim();
    if (!text) return fail(new Error('Add your website, a description or a link.'));
    button.disabled = true;
    try {
      const base = await marketSig().catch(() => null);
      const result = await post('/v2/setup/getting-started/market', {text});
      marketResearchSet({at: Date.now(), task: result.task_id || '', base});
      draw();
    } catch (error) { fail(error); }
  };
  draw();
  return {shell, open: async () => {}, stop: () => watch(false)};
}

// Market is a Beta feature (backend/beta.py): its pill sits on the empty form's title and on the Overview's kicker.
const marketBeta = () => typeof betaPill === 'function' ? betaPill('market') : '';

// The note's kicker, and Research for a page or entry the Librarian can add to.
function marketHead(kicker, target) {
  return `<div class="market-read-head"><p class="market-kicker">${esc(kicker)}${kicker === 'Market' ? marketBeta() : ''}</p>${target && S.me?.role === 'owner'
    ? `<button class="market-research" type="button" data-market-research="${esc(target)}">Research</button>` : ''}</div>`;
}

function renderEntity(shown, names) {
  const entity = shown.entity;
  const bits = [entity.type, entity.tier].filter(Boolean).join(' · ');
  const aliases = (entity.aliases || []).filter(name => name && name !== entity.name);
  const nameOf = id => names.get(id)?.name || id;
  const lines = [];
  for (const [rel, bucket] of Object.entries(shown.edges || {})) {
    for (const edge of [...(bucket.out || []), ...(bucket.in || [])]) {
      const other = edge.src === entity.id ? edge.dst : edge.src;
      lines.push(`<li>${esc(rel.replaceAll('_', ' '))} <a href="${marketHref(other)}" data-market-note="${esc(other)}">${esc(nameOf(other))}</a></li>`);
    }
  }
  const evidence = (shown.evidence || []).map(row =>
    `<blockquote class="market-quote">${row.our_read ? `<p>${esc(row.our_read)}</p>` : ''}${row.quote ? `<p>${esc(row.quote)}</p>` : ''}</blockquote>`
  ).join('');
  return `${marketHead(bits || 'Entity', entity.id)}<h1>${esc(entity.name)}</h1>
    ${entity.summary ? `<p class="market-summary">${esc(entity.summary)}</p>` : ''}
    ${aliases.length ? `<p class="market-aliases">Also called ${aliases.map(esc).join(', ')}</p>` : ''}
    ${lines.length ? `<h2>Relations</h2><ul class="market-rels">${lines.join('')}</ul>` : '<p class="market-empty">No current relations.</p>'}
    ${evidence ? `<h2>Evidence</h2>${evidence}` : ''}`;
}

function mountGraph(canvas, rows, edges) {
  let nodes = [], byId = new Map(), links = [];
  function replace(rows, edges, parentId = null) {
    const old = byId, parent = old.get(parentId);
    nodes = rows.slice(0, 300).map((row, index) => {
      const kept = old.get(row.id);
      const angle = index * 2.399963;
      const radius = (parent ? 12 : 18) * Math.sqrt(index + 1);
      return {id: row.id, name: row.name, type: row.type, tier: row.tier,
        r: 2.4 + Math.min(5.5, Math.sqrt(row.degree || 0) * 1.7),
        x: kept?.x ?? (row.id === parentId ? 0 : (parent?.x || 0) + Math.cos(angle) * radius),
        y: kept?.y ?? (row.id === parentId ? 0 : (parent?.y || 0) + Math.sin(angle) * radius * 0.72),
        vx: 0, vy: 0, pinned: !!kept || row.id === parentId};
    });
    byId = new Map(nodes.map(node => [node.id, node]));
    links = edges.map(edge => ({a: byId.get(edge.src), b: byId.get(edge.dst), rel: edge.rel})).filter(link => link.a && link.b);
    const self = byId.get(marketSelfId());
    if (self && !old.has(self.id)) { self.x = 0; self.y = 0; }
  }
  replace(rows, edges);
  let focus = null;
  let cited = new Set();
  let hover = null;
  let ticks = 0, settled = 0, raf = 0;
  let running = true;
  const view = {x: 0, y: 0, k: 1};
  let drag = null;

  function step() {
    // Beyond nearby cells repulsion is negligible; the grid avoids all-pairs work.
    const grid = new Map(), cellSize = 100;
    for (const node of nodes) {
      const key = Math.floor(node.x / cellSize) + ',' + Math.floor(node.y / cellSize);
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(node);
    }
    for (const node of nodes) {
      if (node.pinned) continue;
      const cx = Math.floor(node.x / cellSize), cy = Math.floor(node.y / cellSize);
      for (let x = cx - 1; x <= cx + 1; x++) for (let y = cy - 1; y <= cy + 1; y++) {
        for (const other of grid.get(x + ',' + y) || []) {
          if (other === node) continue;
          const dx = node.x - other.x, dy = node.y - other.y;
          const dist = Math.max(2, Math.hypot(dx, dy));
          const force = 420 / (dist * dist * dist);
          node.vx += dx * force; node.vy += dy * force;
        }
      }
    }
    for (const link of links) {
      let dx = link.b.x - link.a.x;
      let dy = link.b.y - link.a.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const force = (dist - 96) * 0.012;
      dx /= dist; dy /= dist;
      link.a.vx += dx * force; link.a.vy += dy * force;
      link.b.vx -= dx * force; link.b.vy -= dy * force;
    }
    let speed = 0;
    for (const node of nodes) {
      if (node.pinned) { node.vx = 0; node.vy = 0; continue; }
      if (node.id === marketSelfId()) { node.x = 0; node.y = 0; node.vx = 0; node.vy = 0; continue; }
      node.vx -= node.x * 0.008;
      node.vy -= node.y * 0.008;
      node.vx *= 0.72; node.vy *= 0.72;
      node.x += Math.max(-12, Math.min(12, node.vx));
      node.y += Math.max(-12, Math.min(12, node.vy));
      speed = Math.max(speed, Math.abs(node.vx), Math.abs(node.vy));
    }
    ticks++;
    settled = speed < 0.08 ? settled + 1 : 0;
  }
  function invalidate() {
    if (running && !document.hidden && !raf) raf = requestAnimationFrame(draw);
  }

  function neighbors() {
    const near = new Set();
    if (!focus) return near;
    near.add(focus);
    for (const link of links) {
      if (link.a.id === focus) near.add(link.b.id);
      if (link.b.id === focus) near.add(link.a.id);
    }
    return near;
  }

  // The theme's colours (ui/styles/tokens.css), read once a frame so a theme switch repaints at once.
  let pal = {};
  function palette() {
    const cs = getComputedStyle(canvas), v = name => cs.getPropertyValue(name).trim();
    pal = {bg: v('--bg') || '#121417', ink: v('--ink') || '#e6e9ee', soft: v('--g7') || '#b6bec9', muted: v('--muted') || '#8a93a0',
      line: v('--g5') || '#5b6470', ok: v('--ok') || '#6cc48f', accent: v('--accent') || '#45c09c'};
  }
  function color(node, near) {
    if (cited.has(node.id)) return pal.accent;
    if (node.id === focus || near.has(node.id)) return pal.ink;
    if (node.type === 'segment') return pal.ok;
    if (node.tier === 'core') return pal.ink;
    if (node.type === 'channel') return pal.muted;
    return pal.soft;
  }

  function draw() {
    raf = 0;
    if (!running || document.hidden) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width * ratio));
    const height = Math.max(1, Math.floor(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width; canvas.height = height;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    palette();
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, rect.width, rect.height);
    if (ticks < 180 && settled < 8) step();
    const near = neighbors();
    ctx.save();
    ctx.translate(rect.width / 2 + view.x, rect.height / 2 + view.y);
    ctx.scale(view.k, view.k);
    for (const link of links) {
      const hot = focus && (link.a.id === focus || link.b.id === focus);
      ctx.beginPath();
      ctx.moveTo(link.a.x, link.a.y);
      ctx.lineTo(link.b.x, link.b.y);
      ctx.strokeStyle = hot ? pal.ink : pal.line;
      ctx.globalAlpha = hot ? 0.55 : 0.35;
      ctx.lineWidth = hot ? 1.15 : 0.8;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    for (const node of nodes) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.id === focus ? node.r + 1.6 : node.r, 0, Math.PI * 2);
      ctx.fillStyle = color(node, near);
      ctx.fill();
    }
    ctx.font = '12px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    // A small graph names every node; a big one its core, the focus and its neighbours. Each label sits on a halo of
    // the background so a line under it never runs through the words.
    // Labels go in order of importance, each to the right, left, above or below its node, wherever it covers no
    // label already drawn and no other node; a minor one with no free spot is left out.
    const few = nodes.length <= 40;
    const rank = node => node.id === focus || node.id === hover ? 0 : near.has(node.id) ? 1 : node.tier === 'core' || node.id === marketSelfId() ? 2 : 3;
    const placed = [], h = 14;
    const hits = (box, self) => placed.some(o => box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t)
      || nodes.some(n => n !== self && n.x + n.r > box.l && n.x - n.r < box.r && n.y + n.r > box.t && n.y - n.r < box.b);
    ctx.lineJoin = 'round';
    for (const node of [...nodes].sort((a, b) => rank(a) - rank(b)).slice(0, 60)) {
      if (!few && rank(node) === 3) continue;
      const w = ctx.measureText(node.name).width, gap = node.r + 5;
      const spots = [[node.x + gap, node.y], [node.x - gap - w, node.y], [node.x - w / 2, node.y - gap - 4], [node.x - w / 2, node.y + gap + 4]]
        .map(([x, y]) => ({x, y, box: {l: x - 2, r: x + w + 2, t: y - h / 2, b: y + h / 2}}));
      const spot = spots.find(o => !hits(o.box, node)) || (rank(node) < 3 ? spots[0] : null);
      if (!spot) continue;
      placed.push(spot.box);
      ctx.strokeStyle = pal.bg; ctx.lineWidth = 3.5; ctx.globalAlpha = 0.9;
      ctx.strokeText(node.name, spot.x, spot.y);
      ctx.globalAlpha = 1;
      ctx.fillStyle = node.id === focus || node.id === hover ? pal.ink : pal.soft;
      ctx.fillText(node.name, spot.x, spot.y);
    }
    ctx.restore();
    if (ticks < 180 && settled < 8) invalidate();
  }
  const resize = new ResizeObserver(invalidate);
  resize.observe(canvas);
  const theme = new MutationObserver(invalidate);
  theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme', 'class', 'style']});
  const visibility = () => { if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else invalidate(); };
  const media = matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener('change', invalidate);
  document.addEventListener('visibilitychange', visibility);
  invalidate();

  function world(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - rect.width / 2 - view.x) / view.k,
      y: (event.clientY - rect.top - rect.height / 2 - view.y) / view.k,
    };
  }
  function hit(point) {
    let found = null;
    let best = 14 / view.k;
    for (const node of nodes) {
      const dist = Math.hypot(node.x - point.x, node.y - point.y);
      if (dist < best) { best = dist; found = node; }
    }
    return found;
  }
  canvas.onmousemove = event => {
    if (drag && drag.moved) {
      view.x = drag.vx + (event.clientX - drag.x);
      view.y = drag.vy + (event.clientY - drag.y);
      invalidate();
      return;
    }
    if (drag) drag.moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3;
    const node = hit(world(event));
    hover = node ? node.id : null;
    canvas.style.cursor = node ? 'pointer' : 'grab';
    invalidate();
  };
  canvas.onmousedown = event => {
    drag = {x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false, node: hit(world(event))};
  };
  const release = () => {
    if (!drag) return;
    const picked = drag.node;
    const moved = drag.moved;
    drag = null;
    if (!moved && picked) location.hash = marketHref(picked.id);
  };
  window.addEventListener('mouseup', release);
  const wheel = event => {
    event.preventDefault();
    const next = Math.min(2.4, Math.max(0.45, view.k * (event.deltaY < 0 ? 1.08 : 0.92)));
    view.k = next;
    invalidate();
  };
  canvas.addEventListener('wheel', wheel, {passive: false});

  return {
    update(rows, edges, id) {
      replace(rows, edges, id);
      focus = byId.has(id) ? id : null; cited = new Set();
      const parent = byId.get(id);
      if (parent) { view.x = -parent.x * view.k; view.y = -parent.y * view.k; }
      else { view.x = 0; view.y = 0; view.k = 1; }
      ticks = 0; settled = 0; invalidate();
    },
    focus(id) { focus = byId.has(id) ? id : null; cited = new Set(); invalidate(); },
    cite(ids) { cited = new Set(ids); if (ids[0] && byId.has(ids[0])) focus = ids[0]; invalidate(); },
    stop() {
      running = false; cancelAnimationFrame(raf);
      resize.disconnect(); theme.disconnect();
      media.removeEventListener('change', invalidate);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('mouseup', release);
      canvas.removeEventListener('wheel', wheel);
      canvas.onmousemove = canvas.onmousedown = null;
    },
  };
}
