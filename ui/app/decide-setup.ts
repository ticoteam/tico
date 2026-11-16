/* ui/app/decide-setup.ts — Strategy setup questions in Flow (docs/strategy.md#flow, backend/flow_questions.py): for the
   owner, after their asks in "All mine" (never in a pipeline's or a stage's walk), and first when Flow is opened from
   the Strategy page (its Flow mark, or #/decide?setup=strategy). With no asks waiting Flow opens on them, not All done.
   Needs you's count, the Flow tab and its mark are not changed by them.
   The questions are read from the draft (GET /v2/flow/questions): who we serve, what we sell, one active bet, what we
   won't do, each while it is empty or still Unconfirmed; Keep going asks the guide's other sections. A card is the
   question, the suggested answer as its first choice (the draft's own unconfirmed text, or a proposed bet), a box, and
   Skip, kept per browser. The mic focuses the box, so the phone keyboard's dictation types into it. An answer over
   DEC_Q_RAMBLE characters goes to POST /v2/flow/ramble, which splits it into lines per section in the owner's words; ✕
   drops a line, then one Save. With no model, or on any failure, the answer is saved whole for the section asked.
   Every save goes to the DRAFT through the Strategy routes the owner already has, with the draft's revision (a 409 is
   read again and made again, once): a line is added after what is there, never over confirmed text; an answer takes
   the place of drafted text still marked Unconfirmed (which removes the marker); a bet is made active. Flow never writes
   the summary or the vision. The end screen lists what was saved, by section (✕ takes a line out again, as a draft
   save, until Publish), what Publish would change in all, and Publish, or what still stops it. No Undo after Publish.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type DecQuestion = import('../types/api').FlowQuestion;
type DecQKey = DecQuestion['key'];
/** A line to save: a section's, or a bet's (with the rest of a long answer as its why). `off`: dropped with ✕. */
interface DecQLine {section: DecQKey; text: string; why?: string | undefined; off?: boolean}
/** A line saved this visit, as saved: a bet keeps its id, and whether Flow made it or only made it active. */
interface DecQSaved {section: DecQKey; text: string; bet?: string; made?: boolean}
/** GET /v2/strategy, the parts read here. Its own types: the Strategy page's are in a part this file cannot use. */
interface DecQBet {id: string; statement?: string; why?: string; status?: string}
interface DecQVersion {summary?: string; vision?: string; guide?: string; sections?: Record<string, unknown>; bets?: DecQBet[]; revision?: number | null}
interface DecQStrat {draft?: DecQVersion | null; current?: DecQVersion | null; bets?: DecQBet[]; label?: string; label_one?: string}

const DEC_Q_SKIP = 'hub.flow.strategy-skipped';   // the keys Skip was pressed on, in this browser
const DEC_Q_RAMBLE = 200;     // a longer answer is split (backend/flow_questions.py RAMBLE_MIN)
const DEC_Q_MAX = 6000;       // one answer (RAMBLE_MAX)
const DEC_Q_BET_MAX = 300;    // a bet, one sentence (backend/strategy.py STATEMENT_MAX)
const DEC_Q_WHY_MAX = 2000;   // its why (BET_TEXT_MAX)
const DEC_Q_SPLIT_MS = 45_000;
// strategy_setup.UNCONFIRMED at the head of a section, the line after it included.
const DEC_Q_MARK = /^\s*_Unconfirmed: drafted from [^\n]*?\._[ \t]*(?:\n+|$)/;

/** This visit's questions: read once (`state`), the basics and the rest, the part being asked and what is left of it,
    the screen (a question, the lines of a long answer, the end, or `off` once the walk goes on), whether Flow was
    opened for them (`first`), what is typed, what was saved, the strategy as the end screen read it, the version a
    Publish made, and `visit`, which closing moves on so an answer from an earlier visit lands nowhere. */
interface DecQState {
  visit: number; state: 'idle' | 'loading' | 'ready' | 'failed'; basics: DecQuestion[]; more: DecQuestion[];
  phase: 'basics' | 'more'; queue: DecQuestion[]; view: 'ask' | 'strike' | 'end' | 'off'; first: boolean; typed: string;
  lines: DecQLine[]; saved: DecQSaved[]; data: DecQStrat | null; published: number; busy: boolean; error: string;
  titles: Record<string, string>;
}
const decideQFresh = (visit: number): DecQState => ({visit, state: 'idle', basics: [], more: [], phase: 'basics', queue: [],
  view: 'ask', first: false, typed: '', lines: [], saved: [], data: null, published: 0, busy: false, error: '', titles: {}});
const DEC_Q: DecQState = decideQFresh(0);
let DECIDE_SETUP = false;                     // #/decide?setup=strategy: Flow on the questions once the Strategy page has read
function decideQEnd() {
  const saved = DEC_Q.saved.length > 0 || DEC_Q.published > 0;
  Object.assign(DEC_Q, decideQFresh(DEC_Q.visit + 1));
  // The Strategy page under Flow shows the draft as it is now.
  if (saved && S.route.startsWith('#/strategy') && !S.route.startsWith('#/strategy/')) void pageStrategy();
}
/** The questions are on screen: the owner, walking All mine, past their asks (or Flow was opened for them), while a
    question, the lines or the end is to show. While they are read, a skeleton. */
function decideQOn(st: DecState): boolean {
  if (S.me?.role !== 'owner' || st.stage || st.pipeline || DEC_Q.view === 'off' || DEC_Q.state === 'failed') return false;
  if (!DEC_Q.first && decideCurrent(st)) return false;
  if (DEC_Q.state !== 'ready') return true;
  return DEC_Q.view !== 'ask' || DEC_Q.queue.length > 0;
}
/** Flow from the Strategy page: its questions first, then the asks. */
function decideQOpen(from: HTMLElement | null = null) {
  if (decideNoFlow()) return;
  if (DEC) decideStop();
  DEC_Q.first = true;
  decideOpen('', decideSource(), '', '', true);
  if (DEC && from) DEC.opener = from;
}

// ---- Skip, per browser
function decideQSkipped(): Set<string> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(DEC_Q_SKIP) || '[]');
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
  } catch { return new Set(); }
}
function decideQSkip(key: string) {
  try { localStorage.setItem(DEC_Q_SKIP, JSON.stringify([...decideQSkipped().add(key)])); }
  catch { /* no storage (a private window): skipped for this visit only */ }
}
/** A part's questions to ask: all of them when Flow was opened for them, else those not skipped in this browser. */
const decideQPick = (list: DecQuestion[]) => { const skip = decideQSkipped(); return DEC_Q.first ? list.slice() : list.filter(q => !skip.has(q.key)); };

// ---- reading
async function decideQLoad(st: DecState) {
  if (DEC_Q.state !== 'idle') return;
  DEC_Q.state = 'loading';
  const visit = DEC_Q.visit, got = await v2Get<import('../types/api').FlowQuestions>('/v2/flow/questions', {fresh: true});
  if (visit !== DEC_Q.visit || DEC !== st) return;
  if (!got) { DEC_Q.state = 'failed'; decideRender(st); return; }
  // An answer without the lists (an older server) asks nothing.
  DEC_Q.basics = Array.isArray(got.questions) ? got.questions : []; DEC_Q.more = Array.isArray(got.more) ? got.more : [];
  DEC_Q.titles = got.titles && typeof got.titles === 'object' ? got.titles : {}; DEC_Q.state = 'ready';
  DEC_Q.queue = decideQPick(DEC_Q.basics);
  if (DEC_Q.first && !DEC_Q.queue.length) { DEC_Q.phase = 'more'; DEC_Q.queue = decideQPick(DEC_Q.more); }
  decideRender(st);
}
/** The strategy as it is now: the end screen's, and every save's starting point. */
const decideQRead = () => get<DecQStrat>('/v2/strategy', {fresh: true});
const decideQText = (s: unknown): string => typeof s === 'string' ? s.trim() : '';
const decideQBody = (data: DecQStrat, key: string): string => { const v = data.draft?.sections?.[key]; return typeof v === 'string' ? v : ''; };
const decideQTitle = (key: string): string => DEC_Q.titles[key] || key;

// ---- saving
/** One write with the draft's revision as just read: on a 409 the strategy is read again and the write made from it
    again, once. */
async function decideQWrite(write: (data: DecQStrat, revision: number) => Promise<UntypedJson>): Promise<UntypedJson> {
  for (let attempt = 0; ; attempt++) {
    const data = await decideQRead();
    try { return await write(data, data.draft?.revision ?? 0); }
    catch (e) { if (attempt || errorStatus(e) !== 409) throw e; }
  }
}
/** A section with a line added: after what is there, or in place of drafted text still marked Unconfirmed. */
const decideQMerge = (body: string, line: string) => !body.trim() || DEC_Q_MARK.test(body) ? line : `${body.trimEnd()}\n\n${line}`;
/** A section with a line taken out again. */
function decideQCut(body: string, line: string): string {
  const i = body.lastIndexOf(line);
  return i < 0 ? body : (body.slice(0, i) + body.slice(i + line.length)).replace(/\n{3,}/g, '\n\n').trim();
}
/** The whole answer as one line for the section asked (no model, or it failed): a bet keeps the rest as its why. */
function decideQWhole(text: string, key: DecQKey): DecQLine[] {
  if (key !== 'bet' || text.length <= DEC_Q_BET_MAX) return [{section: key, text: key === 'bet' ? text : text.slice(0, 4000)}];
  const head = text.slice(0, DEC_Q_BET_MAX), cut = head.lastIndexOf(' ') > DEC_Q_BET_MAX * 0.6 ? head.lastIndexOf(' ') : DEC_Q_BET_MAX;
  return [{section: 'bet', text: text.slice(0, cut).trim(), why: text.slice(cut).trim().slice(0, DEC_Q_WHY_MAX)}];
}
/** Saves `lines` (taking each out of the list once it is saved, so a failure leaves only the rest): the sections in one
    draft write, then each bet, made active. */
async function decideQSave(lines: DecQLine[]) {
  const sections = lines.filter(l => l.section !== 'bet' && !l.off);
  if (sections.length) {
    await decideQWrite((data, revision) => {
      const now: Record<string, string> = {};
      for (const l of sections) now[l.section] = decideQMerge(now[l.section] ?? decideQBody(data, l.section), l.text);
      return put('/v2/strategy/draft', {revision, sections: now});
    });
    for (const l of sections) { DEC_Q.saved.push({section: l.section, text: l.text}); lines.splice(lines.indexOf(l), 1); }
  }
  for (const l of lines.filter(x => x.section === 'bet' && !x.off)) {
    const r = await decideQWrite((_data, revision) => post('/v2/strategy/bets',
      {statement: l.text, ...(l.why ? {why: l.why} : {}), status: 'active', revision}));
    DEC_Q.saved.push({section: 'bet', text: l.text, bet: String(r.bet?.id || ''), made: true});
    lines.splice(lines.indexOf(l), 1);
  }
}
/** The suggested answer, tapped: the drafted text kept as the owner's (its marker gone), or the proposed bet made active. */
async function decideQTake(st: DecState, q: DecQuestion) {
  await decideQRun(st, q, async () => {
    if (q.key === 'bet' && q.bet_id) {
      await decideQWrite((_data, revision) => post(`/v2/strategy/bets/${encodeURIComponent(q.bet_id || '')}`, {status: 'active', revision}));
      DEC_Q.saved.push({section: 'bet', text: q.suggest, bet: q.bet_id, made: false});
    } else await decideQSave([{section: q.key, text: q.suggest}]);
    return [q.key];
  });
}
/** Send: a short answer saved as typed; a long one split into lines first (the lines screen), or saved whole when it
    cannot be. The split is one request, never retried (a plain fetch, not post()). */
async function decideQSend(st: DecState, q: DecQuestion) {
  const text = DEC_Q.typed.trim().slice(0, DEC_Q_MAX);
  if (!text) return;
  if (text.length <= DEC_Q_RAMBLE) {
    await decideQRun(st, q, async () => { await decideQSave(decideQWhole(text, q.key)); return [q.key]; });
    return;
  }
  DEC_Q.busy = true; DEC_Q.error = ''; decideQPaint(st);
  const visit = DEC_Q.visit, lines = await decideQSplit(text, q.key);
  if (visit !== DEC_Q.visit) return;
  DEC_Q.busy = false;
  if (lines) { DEC_Q.lines = lines; DEC_Q.view = 'strike'; decideQPaint(st); return; }
  await decideQRun(st, q, async () => { await decideQSave(decideQWhole(text, q.key)); return [q.key]; });
}
/** The ramble's lines, or null when they were not split (no model, the cap, the day's limit, a failure). */
async function decideQSplit(text: string, key: DecQKey): Promise<DecQLine[] | null> {
  const abort = new AbortController(), late = window.setTimeout(() => abort.abort(), DEC_Q_SPLIT_MS);
  try {
    const r = await fetch(`${API}/v2/flow/ramble`, {method: 'POST', redirect: 'error', signal: abort.signal,
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': newRequestId()}, body: JSON.stringify({text, section: key})});
    if (!r.ok) return null;
    const body: unknown = await r.json();
    const o = body && typeof body === 'object' ? body as {split?: unknown; lines?: unknown} : {};
    if (o.split !== true || !Array.isArray(o.lines)) return null;
    const keys = new Set<string>(Object.keys(DEC_Q.titles));
    const lines = (o.lines as unknown[]).flatMap((x): DecQLine[] => {
      const l = x && typeof x === 'object' ? x as {section?: unknown; text?: unknown} : {};
      return typeof l.section === 'string' && keys.has(l.section) && typeof l.text === 'string' && l.text.trim()
        ? [{section: l.section as DecQKey, text: l.text.trim()}] : [];
    });
    return lines.length ? lines : null;
  } catch { return null; } finally { clearTimeout(late); }
}
/** A save from a card: busy while it runs, then the next question (those the save answered are dropped); on a failure
    its words, and whatever is left of a long answer's lines stays on the lines screen. */
async function decideQRun(st: DecState, q: DecQuestion | null, run: () => Promise<string[]>) {
  const visit = DEC_Q.visit;
  DEC_Q.busy = true; DEC_Q.error = ''; decideQPaint(st);
  try {
    const answered = new Set(await run());
    if (visit !== DEC_Q.visit) return;
    DEC_Q.typed = ''; DEC_Q.lines = [];
    DEC_Q.queue = DEC_Q.queue.filter(x => x !== q && !answered.has(x.key));
    decideQNext(st);
  } catch (e) {
    if (visit !== DEC_Q.visit) return;
    DEC_Q.busy = false; DEC_Q.error = errorMessage(e, 'Not saved.');
    if (DEC_Q.lines.length) DEC_Q.view = 'strike';
    decideQPaint(st);
  }
}
/** The next question of the part, else its end. */
function decideQNext(st: DecState) {
  DEC_Q.busy = false; DEC_Q.error = ''; DEC_Q.typed = ''; DEC_Q.lines = [];
  DEC_Q.view = DEC_Q.queue.length ? 'ask' : 'end';
  if (DEC_Q.view === 'end') { DEC_Q.data = null; void decideQEndRead(st); }
  decideQPaint(st);
}
async function decideQEndRead(st: DecState) {
  const visit = DEC_Q.visit;
  try { const data = await decideQRead(); if (visit === DEC_Q.visit) DEC_Q.data = data; }
  catch (e) { if (visit === DEC_Q.visit) DEC_Q.error = errorMessage(e, 'Could not read the strategy.'); }
  if (visit === DEC_Q.visit && DEC === st) decideQPaint(st);
}
/** ✕ on a saved line, before Publish: taken out of the draft again (a revision-checked save); a bet Flow made is retired,
    one it only made active goes back to proposed. */
async function decideQUnsave(st: DecState, i: number) {
  const s = DEC_Q.saved[i];
  if (!s || DEC_Q.published) return;
  await decideQRun(st, null, async () => {
    if (s.section === 'bet' && s.bet) {
      await decideQWrite((_data, revision) => post(`/v2/strategy/bets/${encodeURIComponent(s.bet || '')}`, {status: s.made ? 'retired' : 'proposed', revision}));
    } else {
      await decideQWrite((data, revision) => put('/v2/strategy/draft', {revision, sections: {[s.section]: decideQCut(decideQBody(data, s.section), s.text)}}));
    }
    DEC_Q.saved.splice(DEC_Q.saved.indexOf(s), 1);
    return [];
  });
}
/** Publish: the whole draft, as the Strategy page's Publish does (no note). */
async function decideQPublish(st: DecState) {
  await decideQRun(st, null, async () => {
    const r = await decideQWrite((_data, revision) => post('/v2/strategy/publish', {note: '', revision}));
    DEC_Q.published = Number(r.n) || 1;
    return [];
  });
}

// ---- drawing
/** Everything Publish would change, the owner's earlier edits included: each part's name (as the Strategy page's
    strategyDiffers compares them). */
function decideQChanges(data: DecQStrat): string[] {
  const a = data.draft, b = data.current, t = decideQText, out: string[] = [];
  if (!a) return out;
  if (t(a.summary) !== t(b?.summary)) out.push('Summary');
  if (t(a.vision) !== t(b?.vision)) out.push('Vision');
  for (const key of Object.keys(DEC_Q.titles)) if (key !== 'bet' && t(a.sections?.[key]) !== t(b?.sections?.[key])) out.push(decideQTitle(key));
  const extras = (v: DecQVersion | null | undefined): Record<string, {title?: unknown; body?: unknown}> => {
    const x = v?.sections?.['extra'];
    return x && typeof x === 'object' ? x as Record<string, {title?: unknown; body?: unknown}> : {};
  };
  const ea = extras(a), eb = extras(b);
  for (const k of new Set([...Object.keys(ea), ...Object.keys(eb)]))
    if (t(ea[k]?.body) !== t(eb[k]?.body) || t(ea[k]?.title) !== t(eb[k]?.title)) out.push(t(ea[k]?.title) || t(eb[k]?.title) || k);
  const sig = (list: DecQBet[] | undefined) => (list || []).filter(x => x.status !== 'retired')
    .map(x => [x.id, t(x.statement), t(x.why), x.status].join('\u0001')).sort().join('\u0002');
  if (sig(data.bets) !== sig(b?.bets)) out.push(data.label || 'Bets');
  return out;
}
function decideQAskHTML(q: DecQuestion): string {
  const busy = DEC_Q.busy ? ' disabled' : '';
  const take = q.suggest ? `<div class="dc-choice-list"><button type="button" class="dc-choice dc-rec" data-dc="q-take"${busy}><span class="dc-choice-name dc-q-sug">${esc(q.suggest)}</span></button></div>` : '';
  return `<div class="dc-qs" data-q="${esc(q.key)}"><h3 class="dc-q">${esc(q.question)}</h3>${take}`
    + `<div class="dc-note-row"><textarea id="dc-q-say" data-dc-grow rows="${decideNoteRows()}" maxlength="${DEC_Q_MAX}" aria-label="Answer" placeholder="Answer"${busy}></textarea></div>`
    + (DEC_Q.error ? `<p class="err" role="alert">${esc(DEC_Q.error)}</p>` : '')
    + `<div class="dc-acts">${decideBtn('q-send', 'Send', true, ` data-dc-needs="#dc-q-say" disabled`)}<button type="button" class="linkish dc-skip" data-dc="q-skip"${busy}>Skip</button></div></div>`;
}
/** A long answer's lines, each under its section's name; ✕ drops one (↩ keeps it after all), then Save. */
function decideQStrikeHTML(): string {
  const busy = DEC_Q.busy ? ' disabled' : '', on = DEC_Q.lines.some(l => !l.off);
  const rows = DEC_Q.lines.map((l, i) => `<li class="dc-ql${l.off ? ' off' : ''}"><div class="tx"><span>${esc(decideQTitle(l.section))}</span><b>${esc(l.text)}</b></div>`
    + `<button type="button" class="dc-icon" data-dc="q-drop" data-q-i="${i}" aria-label="${l.off ? 'Keep' : 'Drop'}" title="${l.off ? 'Keep' : 'Drop'}"${busy}>${l.off ? DEC_UNDO_ICON : '✕'}</button></li>`).join('');
  return `<div class="dc-qs" data-q-strike><ul class="dc-qls">${rows}</ul>`
    + (DEC_Q.error ? `<p class="err" role="alert">${esc(DEC_Q.error)}</p>` : '')
    + `<div class="dc-acts">${decideBtn('q-save', 'Save', true, on && !DEC_Q.busy ? '' : ' disabled')}<button type="button" class="linkish dc-skip" data-dc="q-skip"${busy}>Skip</button></div></div>`;
}
/** The end of a part: what was saved, by section; what Publish would change, and Publish or what stops it; Keep going, Done. */
function decideQEndHTML(): string {
  const busy = DEC_Q.busy ? ' disabled' : '', data = DEC_Q.data, done = DEC_Q.published > 0;
  const groups = new Map<string, {s: DecQSaved; i: number}[]>();
  DEC_Q.saved.forEach((s, i) => { groups.set(s.section, [...groups.get(s.section) || [], {s, i}]); });
  const list = [...groups].map(([key, rows]) => `<section class="dc-qg"><h4>${esc(decideQTitle(key))}</h4><ul>${rows.map(({s, i}) =>
    `<li><span>${esc(s.text)}</span>${done ? '' : `<button type="button" class="dc-icon" data-dc="q-unsave" data-q-i="${i}" aria-label="Take out" title="Take out"${busy}>✕</button>`}</li>`).join('')}</ul></section>`).join('');
  const said = done ? `<p class="dc-q-said">Published v${DEC_Q.published}</p>`
    : DEC_Q.saved.length ? '<p class="dc-q-said">Saved to your draft. Bots use it after you publish.</p>' : '';
  let publish = '';
  if (!done && !data) publish = skelRows(1);
  else if (!done && data) {
    const noBet = !(data.bets || []).some(b => b.status === 'active'), noNot = !decideQText(data.draft?.sections?.['not']);
    const changes = decideQChanges(data);
    const fix = (key: string, words: string) => `<button type="button" class="ghost dc-q-fix" data-dc="q-fix" data-q-key="${key}"${busy}>${esc(words)}</button>`;
    publish = noBet || noNot
      ? `<div class="dc-q-pub">${noBet ? fix('bet', `Needs an active ${(data.label_one || 'Bet').toLowerCase()}`) : ''}${noNot ? fix('not', "Needs What we won't do") : ''}</div>`
      : changes.length ? `<div class="dc-q-pub"><p class="dc-sub" aria-label="Publish changes">${changes.map(esc).join(' · ')}</p>${decideBtn('q-publish', 'Publish', true, busy)}</div>` : '';
  }
  return `<div class="dc-qs dc-qend" data-q-end>${flowMarkHTML(32)}${said}${list}${publish}`
    + (DEC_Q.error ? `<p class="err" role="alert">${esc(DEC_Q.error)}</p>` : '')
    + `<div class="dc-acts">${decideBtn('q-more', 'Keep going', false, busy)}${decideBtn('q-done', 'Done', false, busy)}</div></div>`;
}
/** The questions' screen: the right side alone, the thing side empty (decide.css .dc-empty). */
function decideQPaint(st: DecState) {
  const root = $('.decide'), side: HTMLElement | null = $('.decide .dc-side'), thing: HTMLElement | null = $('.decide .dc-thing');
  if (!root || !side || !thing || DEC !== st || !decideQOn(st)) return;
  if (DEC_Q.state === 'idle') void decideQLoad(st);
  root.classList.add('dc-empty');
  thing.parentElement?.classList.remove('dc-qmain');
  if (thing.innerHTML) { browserView.stopIn(thing); thing.innerHTML = ''; delete thing.dataset.html; delete thing.dataset.key; }
  const q = DEC_Q.queue[0], typing = document.activeElement?.id === 'dc-q-say';
  side.innerHTML = DEC_Q.state !== 'ready' ? `<div class="dc-qs">${skelRows(3)}</div>`
    : DEC_Q.view === 'strike' ? decideQStrikeHTML() : DEC_Q.view === 'end' || !q ? decideQEndHTML() : decideQAskHTML(q);
  const box = side.querySelector<HTMLTextAreaElement>('#dc-q-say');
  if (box) { box.value = DEC_Q.typed; decideGrow(box); if (typing && !box.disabled) box.focus({preventScroll: true}); }
  decideNeedsText(root);
  decideBarRoom();
}
/** A press on the questions' screen (data-dc="q-…"). */
async function decideQClick(st: DecState, what: string, b: HTMLElement) {
  const q = DEC_Q.queue[0], i = Number(b.dataset.qI);
  switch (what) {
    case 'q-take': if (q && DEC_Q.view === 'ask') await decideQTake(st, q); return;
    case 'q-send': if (q && DEC_Q.view === 'ask') await decideQSend(st, q); return;
    case 'q-skip':
      if (q && DEC_Q.view === 'ask') decideQSkip(q.key);
      DEC_Q.queue.shift();
      decideQNext(st);
      return;
    case 'q-drop': { const l = DEC_Q.lines[i]; if (l) { l.off = !l.off; decideQPaint(st); } return; }
    case 'q-save': await decideQRun(st, q || null, async () => { const keys = DEC_Q.lines.filter(l => !l.off).map(l => l.section); await decideQSave(DEC_Q.lines); return keys; }); return;
    case 'q-unsave': await decideQUnsave(st, i); return;
    case 'q-publish': await decideQPublish(st); return;
    case 'q-fix': {
      // What stops Publish: its question again, here.
      const key = b.dataset.qKey || '', again = DEC_Q.basics.find(x => x.key === key);
      if (again) { DEC_Q.queue = [again]; decideQNext(st); }
      return;
    }
    case 'q-more': {
      const more = DEC_Q.phase === 'basics' ? decideQPick(DEC_Q.more) : [];
      if (more.length) { DEC_Q.phase = 'more'; DEC_Q.queue = more; decideQNext(st); return; }
      // Nothing more to ask: the walk goes on, to the asks or All done.
      DEC_Q.view = 'off'; DEC_Q.first = false;
      const live = decideLive(st);
      if (!decideCurrent(st) && live[0]) decideGo(st, live[0]); else decideRender(st);
      return;
    }
    case 'q-done': decideStop(); return;
  }
}
/** The mic on a question: the box, so the keyboard's own dictation types the answer (no speech is sent anywhere). */
function decideQVoice(st: DecState): boolean {
  if (!decideQOn(st) || DEC_Q.view !== 'ask') return false;
  $('.decide #dc-q-say')?.focus();
  return true;
}
