/* Market is a Strategy tab (ui/strategy-page.ts): one page of five questions (ui/app/redesign-market.ts) and, behind
   Open map, the note and the graph of who connects to whom. It is not the Docs library. */
'use strict';

/** Where an address goes (marketAt): a question on the page, or a note on the map. */
type MarketAt = {kind: 'page'; section: string; dimension?: string} | {kind: 'map'; note: string};

/** What a mount gives back: the page (ui/app/redesign-market.ts), the map, or the empty state. */
interface MarketMounted {
  shell: HTMLElement;
  open(at: string | MarketAt | RdqOpenAt): Promise<unknown>;   // RdqOpenAt: the page's own open (redesign-market.ts)
  stop(): void;
}

interface MarketView extends MarketMounted {
  kind: MarketAt['kind'];
}

/** An entity as the market's lists and graph answer it (GET /v2/market/entities, /v2/market/graph); a page in the
    index list is drawn as one too, with its title as the name. */
interface MarketRow {
  id: string;
  name: string;
  title?: string;
  type?: string;
  tier?: string | null;
  degree?: number;
}

interface MarketEdge {
  src: string;
  dst: string;
  rel: string;
}

interface MarketGraphAnswer {
  entities?: MarketRow[];
  edges?: MarketEdge[];
}

interface MarketEntityList {
  entities?: MarketRow[];
  next_offset?: number | null;
  total?: number;
}

/** A page in the market's collection (GET /company-docs?collection=market). */
interface MarketDoc {
  id: string;
  title: string;
  seeded?: boolean;
  fetched?: string | null;
}

interface MarketDocList {
  documents?: MarketDoc[];
}

/** One entity with its relations and evidence (GET /v2/market/entities/{id}?names=true). */
interface MarketShown {
  entity: MarketRow & {summary?: string; aliases?: string[]};
  neighbors?: MarketRow[];
  edges?: Record<string, {out?: MarketEdge[]; in?: MarketEdge[]}>;
  evidence?: Array<{our_read?: string; quote?: string}>;
}

/** The research notice's record: when it was filed, its task, and the market's signature then (marketSig). */
interface MarketResearchRow {
  at: number;
  task: string;
  base: string | null;
}

/** Market's setup questions as GET /v2/librarian/setup (market) and POST /v2/librarian/setup/market answer them
    (backend/librarian_setup.py). */
interface MarketSetupState {
  answers: Record<string, string>;
  can_answer: boolean;
  skipped: boolean;
  step: number;
  task: {id: string; status: string; created_at: string} | null;
}

let MARKET_VIEW: MarketView | null = null;   // what is mounted: {kind: 'page' | 'map', shell, open(at), stop()}
// Old #/market links land here (ui/app/router.ts), keeping their query or note path.
const MARKET_ROUTE = '#/strategy/market';
const MARKET_MAP = MARKET_ROUTE + '/map';
const isMarketRoute = (r: string) => r === MARKET_ROUTE || r.startsWith(MARKET_ROUTE + '?') || r.startsWith(MARKET_ROUTE + '/');
// The tab's old views each land on the page at their question; Players opens the map. A note id always has a slash
// in it, so a bare key is never a note.
const MARKET_SECTIONS: Record<string, string> = {briefing: 'changed', spend: 'money', buyers: 'money', growth: 'money', 'value-chain': 'reach'};
let MARKET_PAGE = 0;

// Where an address goes: {kind: 'page', section, dimension} or {kind: 'map', note}.
function marketAt(route: string): MarketAt {
  const [path, query] = route.slice(MARKET_ROUTE.length).split('?') as [string, string | undefined];
  const note = new URLSearchParams(query || '').get('note');
  const key = path.replace(/^\//, '');
  if (note) return {kind: 'map', note};
  if (!key) return {kind: 'page', section: ''};
  const section = MARKET_SECTIONS[key];
  if (section) return {kind: 'page', section, dimension: key === 'buyers' ? 'buyer' : ''};
  if (key === 'map' || key === 'players') return {kind: 'map', note: MARKET_BRIEF};
  return {kind: 'map', note: decodeURIComponent(key)};
}

const MARKET_TYPES: Array<[string, string]> = [
  ['organization', 'Organizations'],
  ['segment', 'Segments'],
  ['channel', 'Channels'],
  ['person', 'People'],
  ['product', 'Products'],
  ['document', 'Documents'],
  ['place', 'Places'],
  ['event', 'Events'],
];

const MARKET_BRIEF = 'brief';

// Nothing about the market is written here: the Overview is the page the Librarian, a helper nobody chats with,
// writes (playbooks/market-setup.md). Until the market has content the page is its own empty state, drawn in the app's
// theme rather than the graph's dark room: the setup questions, or the notice while the research runs.
const marketStyle = document.createElement('style');
marketStyle.textContent = `.market-shell.market-shell-blank{display:flex;align-items:center;justify-content:center;overflow:auto;padding:32px 16px 12vh;background:var(--bg);color:var(--ink)}
.market-start{width:min(560px,100%);display:grid;gap:10px;margin:0}
.market-start h1{margin:0 0 6px;font-size:22px;line-height:1.25;font-weight:650;color:var(--ink)}
.market-start label{font-size:13px;color:var(--muted)}
.market-start h1 label{font-size:inherit;color:inherit}
.market-start textarea{min-height:112px;font:inherit;font-size:14px}
.market-start .err{margin:0;font-size:13px}
.market-hint{margin:0;color:var(--muted);font-size:13.5px;line-height:1.5}
.market-count{white-space:nowrap;opacity:.8}
.market-count::before{content:"· "}
.market-start-actions{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:4px}
.market-start-actions .primary{padding:9px 18px}
.market-start-actions a{font-size:13.5px}
.market-researching{width:min(560px,100%);display:flex;align-items:flex-start;gap:14px}
.market-researching strong{display:block;font-size:16px;font-weight:600;color:var(--ink)}
.market-researching p{margin:4px 0 0;color:var(--muted);font-size:13.5px}
.market-spin{flex:none;width:18px;height:18px;margin-top:2px;border-radius:50%;border:2px solid var(--line);border-top-color:var(--ink);animation:marketspin .9s linear infinite}
@keyframes marketspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.market-spin{animation:none;border-color:var(--ink)}}
.market-read-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.market-research{flex:none;padding:3px 10px;border-radius:6px;background:none;border:1px solid var(--line);color:var(--ink);font-size:12.5px;cursor:pointer}
.market-research:hover{background:var(--surface2)}
.market-research:focus-visible{outline:var(--focus-ring);outline-offset:1px}
.market-none{margin:0;color:var(--muted);font-size:14px}
.market-blank{margin:0;color:var(--muted)}
@media (max-width:600px){.market-shell.market-shell-blank{align-items:flex-start;padding:28px 16px}.market-start-actions .primary{flex:1}}`;
document.head.appendChild(marketStyle);

// The node for your own team sits at the centre of the graph. Tico may name it in its
// status (company_entity); otherwise the convention is company/self.
const marketSelfId = () => (typeof S !== 'undefined' && S.status && S.status.company_entity) || 'company/self';

function marketNoteId() {
  const at = marketAt(S.route);
  return at.kind === 'map' ? at.note : MARKET_BRIEF;
}

function marketHref(id: string) {
  return MARKET_ROUTE + '?note=' + encodeURIComponent(id);
}

window.marketStop = function marketStop() {
  MARKET_PAGE++;
  if (MARKET_VIEW) MARKET_VIEW.stop();
};

// Draws the page again from the server, when the research notice sees the market's first content.
window.marketReload = function marketReload() {
  if (!MARKET_VIEW || !isMarketRoute(S.route)) return;
  MARKET_VIEW.stop();
  MARKET_VIEW = null;
  void pageMarket();
};

// The page and the map share one mount: moving within the page only scrolls it, and a note opens inside the map.
window.pageMarket = async function pageMarket() {
  const page = ++MARKET_PAGE, at = marketAt(S.route);
  $('#main').classList.add('market-layout');
  if (!MARKET_VIEW || MARKET_VIEW.kind !== at.kind || !document.body.contains(MARKET_VIEW.shell)) {
    if (MARKET_VIEW) { MARKET_VIEW.stop(); MARKET_VIEW = null; }
    // The section's heading and tabs sit above the shell, and stay while notes open inside it.
    $('#main').innerHTML = (window.strategyHeadHtml?.('market') || '') + `<div id="market-shell" class="${at.kind === 'map' ? 'market-shell' : 'mkt-host mkt-page'}"><p class="market-loading">Opening the market…</p></div>`;
    if (at.kind === 'map') $('.rd-strat-head > .ph-tools').innerHTML = `<a class="ghost" href="${MARKET_ROUTE}" data-market-close>Close map</a>`;
    const shell = $('#market-shell');
    try {
      const mounted = await (at.kind === 'map' ? mountMarket(shell) : mountMarketPage(shell));
      if (page !== MARKET_PAGE) { mounted.stop(); return; }   // another address was asked for meanwhile
      MARKET_VIEW = {...mounted, kind: at.kind};
    } catch (error) {
      shell.innerHTML = `<p class="market-loading">${esc(errorMessage(error, 'The market did not load.'))}</p>`;
      return;
    }
  }
  await MARKET_VIEW.open(at.kind === 'map' ? at.note : at);
};

// The market has content once it has an active entity, or a page the seed did not write ("None in the seed."). Until
// then, and while the research notice shows, Market is its empty state. Returns the pages it may list.
function marketBlank(count: number, docs: MarketDoc[] | undefined) {
  const pages = (docs || []).filter(doc => count || !doc.seeded);
  let researching = marketResearchGet();
  const blank = !count && !pages.length;
  if (researching && !blank && Date.now() - Number(researching.at) >= MARKET_RESEARCH_MIN) { marketResearchSet(null); researching = null; }
  return {blank: blank || !!researching, pages};
}

async function mountMarket(shell: HTMLElement): Promise<MarketMounted> {
  const today = new Date().toISOString().slice(0, 10);
  const graphUrl = (id: string, fill = false) => {
    const params = new URLSearchParams({focus: id, fill: String(fill), limit: '250', as_of: today});
    return '/v2/market/graph?' + params;
  };
  const [initial, listing, library] = await Promise.all([
    get<MarketGraphAnswer>(graphUrl(marketSelfId(), true)),
    get<MarketEntityList>('/v2/market/entities?status=active&limit=50'),
    get<MarketDocList>('/company-docs?collection=market'),
  ]);
  const nodes = initial.entities || [];
  const byId = new Map(nodes.map(row => [row.id, row]));
  const links = initial.edges || [];
  (listing.entities || []).forEach(row => byId.set(row.id, row));
  const {blank, pages} = marketBlank(nodes.length, library.documents);
  if (blank) return await mountBlank(shell);
  pages.sort((a, b) => a.title.localeCompare(b.title));
  shell.innerHTML = `<aside class="market-list" aria-label="Market notes">
      <a id="market-overview" class="market-overview-btn" href="${marketHref(MARKET_BRIEF)}">Overview</a>
      <input id="market-filter" type="search" placeholder="Search the market" aria-label="Search the market" autocomplete="off">
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

  async function loadGraph(id: string, fill = false) {
    const version = ++graphVersion;
    if (graphFocus === id && graphDefault === fill) { graph.focus(fill ? null : id); return; }
    const data = await get<MarketGraphAnswer>(graphUrl(id, fill));
    if (stopped || version !== graphVersion) return;
    (data.entities || []).forEach(row => byId.set(row.id, row));
    graph.update(data.entities || [], data.edges || [], fill ? null : id);
    graphFocus = id; graphDefault = fill;
  }
  $('#market-reset').onclick = () => void loadGraph(marketSelfId(), true).catch(error => toast(errorMessage(error, '')));

  function renderIndex(data: MarketEntityList, query: string, offset = 0) {
    const q = query.trim().toLowerCase();
    const match = (row: {id: string; name?: string; title?: string}) => !q || (row.name || row.title || '').toLowerCase().includes(q) || (row.id || '').toLowerCase().includes(q);
    const rows = data.entities || [];
    rows.forEach(row => byId.set(row.id, row));
    const groups: Array<[string, MarketRow[]]> = [['Pages', pages.filter(doc => doc.id !== 'market/overview' && match(doc)).map(doc => ({id: doc.id, name: doc.title}))]];
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
    if (next) next.onclick = () => void fetchIndex(query, data.next_offset!);   // Next is drawn only with a next_offset
  }
  async function fetchIndex(query: string, offset = 0) {
    const version = ++listVersion;
    try {
      const data = await get<MarketEntityList>('/v2/market/entities?' + new URLSearchParams({status: 'active', limit: '50', offset: String(offset), q: query}));
      if (!stopped && version === listVersion) renderIndex(data, query, offset);
    } catch (error) { if (!stopped && version === listVersion) index.textContent = errorMessage(error, ''); }
  }
  renderIndex(listing, '');
  filter.oninput = () => {
    clearTimeout(listTimer); ++listVersion;
    listTimer = setTimeout(() => void fetchIndex(filter.value), 180);
  };
  // Research on the page or entry being read (ui/docs-editor.js).
  let shownBody = '';
  $('#market-read').addEventListener('click', (event: MouseEvent) => {
    const button = eventElement(event)?.closest<HTMLElement>('[data-market-research]');
    if (button) DocsEditor.researchDialog({target: button.dataset.marketResearch, body: shownBody});
  });

  async function open(id: string) {
    const version = ++openVersion;
    const overview = $('#market-overview');
    const onBrief = id === MARKET_BRIEF;
    overview.classList.toggle('cur', onBrief);
    if (onBrief) overview.setAttribute('aria-current', 'page'); else overview.removeAttribute('aria-current');
    index.querySelectorAll('a').forEach((link: HTMLAnchorElement) => {
      const on = link.dataset.marketNote === id;
      link.classList.toggle('cur', on);
      if (on) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    const read = $('#market-read');
    const showDoc = async (doc: MarketDoc) => {
      const full = await get<{content?: string}>('/company-docs/' + doc.id.split('/').map(encodeURIComponent).join('/'));
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
          get<MarketShown>('/v2/market/entities/' + id.split('/').map(encodeURIComponent).join('/') + '?names=true'), loadGraph(id),
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
          read.innerHTML = `<p class="market-empty">${esc(errorMessage(error, 'That note is not in the market.'))}</p>`;
          graph.focus(null);
        }
      }
      return;
    }
    await showDoc(doc);
  }

  return {shell, open, stop() { stopped = true; clearTimeout(listTimer); graph.stop(); }};
}

// ---------------------------------------------------------------- an empty market
// On a first visit Market asks the owner four questions, one at a time (backend/librarian_setup.py): what you
// sell, who buys it, who you compete with, what to watch. Each answer is kept on the server as it is given, so
// "Skip for now" leaves and Continue picks up at the same question, in any browser. The last one files "Set up the
// market map" (templates/catalog/librarian/playbooks/market-setup.md), which builds the map and sets what Market
// tracks; "Attach files" is the Docs import. Then a notice, kept in this browser with no server involved: it shows for
// at least two minutes and until the market has content, and goes for good after thirty. While it shows, the market
// is read every thirty seconds (two small GETs), and the page is drawn again once the notice is done.
const MARKET_RESEARCH_KEY = 'tico.market.researching';
const MARKET_RESEARCH_MIN = 2 * 60 * 1000, MARKET_RESEARCH_MAX = 30 * 60 * 1000, MARKET_RESEARCH_POLL = 30 * 1000;
let MARKET_RESEARCH: MarketResearchRow | null = null;        // the same record in memory, for a browser that will not store it
// [key, question, hint, placeholder]. Only the first must be answered: the research finds the rest.
const MARKET_QUESTIONS: Array<[string, string, string, string]> = [
  ['sell', 'What do you sell?', 'Your website, or a sentence or two.', 'https://example.com'],
  ['buyers', 'Who buys it?', 'The kinds of companies or people, and where they are. Optional.', 'Property managers in the US Midwest'],
  ['competitors', 'Who do you compete with?', 'Names or websites, one per line. Optional.', 'CleanCo\nhttps://sparkle.example'],
  ['watch', 'What should Market watch?', 'Where your buyers talk, topics, rules or people to follow. Optional.', 'r/PropertyManagement, new licensing rules'],
];

function marketResearchGet() {
  let row = MARKET_RESEARCH;
  try { row = JSON.parse(localStorage.getItem(MARKET_RESEARCH_KEY) || 'null') || row; } catch { /* the memory copy stands */ }
  const age = row ? Date.now() - Number(row.at) : NaN;
  if (row && age >= -60000 && age < MARKET_RESEARCH_MAX) return row;
  if (row) marketResearchSet(null);
  return null;
}

function marketResearchSet(row: MarketResearchRow | null) {
  MARKET_RESEARCH = row;
  try { if (row) localStorage.setItem(MARKET_RESEARCH_KEY, JSON.stringify(row)); else localStorage.removeItem(MARKET_RESEARCH_KEY); } catch { /* memory only */ }
}

// What the market holds, in one string: how many entities, and when its newest page was written. The seed's
// pages are already there when the owner asks, so "has content" means "differs from what it was when asked".
async function marketSig() {
  const [entities, pages] = await Promise.all([get<MarketEntityList>('/v2/market/entities?limit=1'), get<MarketDocList>('/company-docs?collection=market')]);
  const written = (pages.documents || []).filter(doc => doc.id !== 'market/weekly-delta').map(doc => String(doc.fetched || ''));
  return `${(entities.total ?? (entities.entities || []).length)}|${written.reduce((a, b) => a > b ? a : b, '')}`;
}

function marketQuestionHtml(setup: MarketSetupState) {
  const [key, question, hint, placeholder] = MARKET_QUESTIONS[setup.step]!;
  const last = setup.step === MARKET_QUESTIONS.length - 1;
  return `<form class="market-start" data-market-setup data-step="${setup.step}" novalidate>
      <h1><label for="market-answer">${esc(question)}</label></h1>
      <p class="market-hint" id="market-hint">${esc(hint)} <span class="market-count">${setup.step + 1} of ${MARKET_QUESTIONS.length}</span></p>
      <textarea id="market-answer" name="answer" rows="4" maxlength="4000" autocomplete="off" aria-describedby="market-hint" placeholder="${esc(placeholder)}">${esc(setup.answers[key] || '')}</textarea>
      <p class="err" data-market-error role="alert" hidden></p>
      <div class="market-start-actions"><button class="primary" type="submit">${last ? 'Start research' : 'Next'}</button>
        ${setup.step ? '<button class="ghost" type="button" data-market-back>Back</button>' : ''}
        <button class="ghost" type="button" data-market-skip>Skip for now</button>
        <a href="#/docs?import=1">Attach files</a></div>
    </form>`;
}

function marketBlankHtml(row: MarketResearchRow | null, setup: MarketSetupState | null) {
  if (row) return `<div class="market-researching" data-market-researching role="status">
      <span class="market-spin" aria-hidden="true"></span>
      <div><strong>Researching your market.</strong>
        <p>This usually takes 5–10 minutes.${row.task ? ` <a href="#/task/${esc(row.task)}">View task</a>` : ''}</p></div></div>`;
  if (!(S.me?.cloud && S.me.role === 'owner') || !setup) return '<p class="market-none">Nothing here yet.</p>';
  if (!setup.skipped) return marketQuestionHtml(setup);
  const answered = MARKET_QUESTIONS.filter(([key]) => setup.answers[key]).length;
  return `<div class="market-start" data-market-resume>
      <h1>Set up Market</h1>
      <p class="market-hint">${MARKET_QUESTIONS.length} questions about your market set what Market tracks. You've answered ${answered}.</p>
      <div class="market-start-actions"><button class="primary" type="button" data-market-continue>Continue</button>
        <a href="#/docs?import=1">Attach files</a></div>
    </div>`;
}

async function mountBlank(shell: HTMLElement): Promise<MarketMounted> {
  let poll = 0, setup: MarketSetupState | null = null;
  if (S.me?.cloud && S.me.role === 'owner') {
    try { setup = (await get<{market: MarketSetupState | null}>('/v2/librarian/setup')).market; } catch { setup = null; }
    if (setup) setup = {...setup, answers: {...setup.answers}};
    // Filed from another browser and still being worked on: the notice, not the questions. Its thirty minutes
    // count from when the task was filed, so a reload never starts them again.
    const filed = Date.parse(setup?.task?.created_at || '');
    if (setup?.task && !['done', 'closed', 'declined'].includes(setup.task.status) && !marketResearchGet()
        && Date.now() - filed < MARKET_RESEARCH_MAX)
      marketResearchSet({at: filed, task: setup.task.id, base: null});
  }
  const watch = (on: boolean) => {
    if (!on) { clearInterval(poll); poll = 0; }
    else if (!poll) poll = setInterval(() => { if (!document.hidden) void tick(); }, MARKET_RESEARCH_POLL);
  };
  const draw = () => {
    const row = marketResearchGet();
    shell.className = 'market-shell market-shell-blank';
    shell.innerHTML = marketBlankHtml(row, setup);
    watch(!!row);
    if (!row) shell.querySelector<HTMLElement>('#market-answer')?.focus();
  };
  async function tick() {
    const row = marketResearchGet();
    if (!row) { watch(false); window.marketReload(); return; }          // thirty minutes passed: the questions are back
    let sig: string;
    try { sig = await marketSig(); } catch { return; }                   // the next tick tries again
    const arrived = row.base == null ? !sig.startsWith('0|') : sig !== row.base;
    if (arrived && Date.now() - Number(row.at) >= MARKET_RESEARCH_MIN) {
      marketResearchSet(null);
      watch(false);
      window.marketReload();
    }
  }
  // Keeps what is typed in the open question, then tells the server where the owner is.
  const keep = () => {
    if (!setup) return;
    const field = shell.querySelector<HTMLTextAreaElement>('#market-answer');
    const asked = MARKET_QUESTIONS[setup.step];
    if (field && asked) setup.answers[asked[0]] = field.value.trim();
  };
  // One write at a time, in the order they were made: a slow Back save never lands after the Next that followed it.
  let saving: Promise<unknown> = Promise.resolve();
  // Only the open question's buttons send, and they are drawn only once setup is read.
  const send = (step: number, action: string) => {
    const body = {answers: {...setup!.answers}, step, action};
    const sent: Promise<MarketSetupState> = saving.catch(() => {}).then(() => post('/v2/librarian/setup/market', body));
    saving = sent;
    return sent;
  };
  const fail = (form: Element, error: unknown) => {
    const line = form.querySelector<HTMLElement>('[data-market-error]');
    if (!line) return;
    line.textContent = errorMessage(error, 'That did not go through.'); line.hidden = false;
    form.querySelectorAll('button').forEach(button => { button.disabled = false; });
  };
  shell.onclick = async event => {
    const target = eventElement(event);
    if (!setup) return;
    if (target?.closest('[data-market-continue]')) { setup.skipped = false; draw(); return; }
    const form = target?.closest('[data-market-setup]');
    if (!form) return;
    if (target?.closest('[data-market-back]')) {
      keep(); setup.step -= 1; draw();
      send(setup.step, 'save').catch(() => { /* kept here; the next answer saves it */ });
    } else if (target?.closest('[data-market-skip]')) {
      keep();
      form.querySelectorAll('button').forEach(button => { button.disabled = true; });
      try { setup = {...setup, ...(await send(setup.step, 'skip')), answers: setup.answers}; draw(); } catch (error) { fail(form, error); }
    }
  };
  shell.onsubmit = async event => {
    const form = eventElement(event)?.closest('[data-market-setup]');
    if (!form || !setup) return;
    event.preventDefault();
    keep();
    if (setup.step === 0 && !setup.answers.sell) return fail(form, new Error('Add your website, or a sentence about what you sell.'));
    form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    const last = setup.step === MARKET_QUESTIONS.length - 1;
    try {
      if (!last) { await send(setup.step + 1, 'save'); setup.step += 1; draw(); return; }
      if (!setup.answers.sell) { setup.step = 0; draw(); return; }
      const base = await marketSig().catch(() => null);
      const result = await send(setup.step, 'finish');
      marketResearchSet({at: Date.now(), task: result.task?.id || '', base});
      draw();
    } catch (error) { fail(form, error); }
  };
  draw();
  return {shell, open: async () => {}, stop: () => watch(false)};
}

// The note's kicker, and Research for a page or entry the research can add to.
function marketHead(kicker: string, target: string) {
  return `<div class="market-read-head"><p class="market-kicker">${esc(kicker)}</p>${target && S.me?.role === 'owner'
    ? `<button class="market-research" type="button" data-market-research="${esc(target)}">Research</button>` : ''}</div>`;
}

function renderEntity(shown: MarketShown, names: Map<string, MarketRow>) {
  const entity = shown.entity;
  const bits = [entity.type, entity.tier].filter(Boolean).join(' · ');
  const aliases = (entity.aliases || []).filter(name => name && name !== entity.name);
  const nameOf = (id: string) => names.get(id)?.name || id;
  const lines: string[] = [];
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
