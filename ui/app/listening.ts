/* ui/app/listening.ts — Listening: the automations whose trigger is `listening`, on their own page (#/listening)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A listening is an automation: this page is the Automations page (automations.ts, AU.lst) with its own list, and the
   trigger's three parts in the flow and its editor: Sources (what to read), Rules (remove copies, then keep and tag,
   or drop) and Rate limits (how many go out, how often). Its steps, versions, runs and spend limit are an
   automation's. A run carries what was sent and the counts since the send before (lstRunHTML); a Paths step splits
   what was sent by tag or by item (lstPathsFields).
   Reads: GET /api/v2/automations (the rows whose trigger.type is listening), GET /api/v2/listening/sources (each
   source's health). Writes: POST /api/v2/automations, PATCH /api/v2/automations/{id} {name, trigger},
   POST /api/v2/listening/sources/{id}/address (a push source's new address). */
'use strict';

/** One thing to read: a search or a community on a site, one page, or an address an outside service posts to. */
interface LstSource { id: string; kind: string; site?: string; target?: string }
/** A rule: keep (a tag and a sentence) or drop (a sentence). Remove duplicates is always first and is not in the list. */
interface LstRule { do: string; tag?: string; text?: string }
/** "Up to `count` every `per`"; one per unit. */
interface LstLimit { count?: number | string; per: string }
/** A source as the hub keeps it (GET /api/v2/listening/sources): its place in a listening and how its last read went. */
interface LstSourceRow {
  id: string; automation_id: string; key?: string | null; kind?: string | null; site?: string | null; target?: string | null;
  status?: string | null; last_read_at?: string | null; last_ok_at?: string | null; last_error?: string | null;
}
/** What a send's run carries (run.listening): the text that went out and the counts since the send before. */
interface LstRun {
  text?: string; count?: number; waiting?: number; sent_at?: string;
  counts?: { read?: number; duplicates?: number; dropped?: number; kept?: number; sent?: number; waiting?: number; failed?: number };
}

const LST_UNITS = ['hour', 'day', 'week', 'month'];
const LST_PER: Record<string, string> = {hour: 'an hour', day: 'a day', week: 'a week', month: 'a month'};
const LST_START: Record<string, number> = {hour: 3, day: 10, week: 50, month: 100};
const LST_KINDS: Array<[string, string]> = [['search', 'Search'], ['page', 'Page'], ['push', 'Push']];
const LST_TAG = /^[a-z0-9][a-z0-9-]{0,23}$/;
const LST_STALE = 2 * 864e5;   // a source not read for two days is overdue
const LST_STATUS: Record<string, string> = {never: 'Not read yet', blocked: 'Blocked', rate_limited: 'Rate limited', error: 'Failed'};

/** True while the open automation is a listening. */
const lstIs = (): boolean => AU?.cur?.trigger?.type === 'listening';
const lstNewId = (): string => 'src_' + Math.random().toString(36).slice(2, 10);
const lstSorted = (limits: LstLimit[] | undefined): LstLimit[] => [...(limits || [])].sort((x, y) => LST_UNITS.indexOf(x.per) - LST_UNITS.indexOf(y.per));
/** The limits in a few words: "3 an hour · 10 a day". */
function lstLimitsWords(limits: LstLimit[] | undefined): string {
  return lstSorted(limits).map(l => `${l.count ?? ''} ${LST_PER[l.per] || 'a ' + l.per}`).join(' · ');
}
const lstKeepTags = (t: Partial<AuFlowTrigger> | undefined): string[] => [...new Set((t?.rules || []).filter(r => r.do === 'keep' && r.tag).map(r => String(r.tag)))];
/** The next limit a "+ Limit" adds: the first unit not used yet, at its usual amount. */
function lstNextLimit(limits: LstLimit[] | undefined): LstLimit {
  const per = ['day', 'hour', 'week', 'month'].find(u => !(limits || []).some(l => l.per === u)) || 'day';
  return {count: LST_START[per] ?? 10, per};
}

// ----------------------------------------------------------------------------------------- source health
async function lstSourcesLoad(state: AutomationsState): Promise<void> {
  let rows: LstSourceRow[] = [];
  try { rows = (await get<{sources?: LstSourceRow[] | null}>('/v2/listening/sources')).sources || []; }
  catch { /* no health: every dot is hollow */ }
  const by = new Map<string, LstSourceRow[]>();
  for (const r of rows) by.set(r.automation_id, [...(by.get(r.automation_id) || []), r]);
  state.lstSources = by;
  if (AU !== state || !state.root || state.list === null) return;
  if (!state.id) auDrawTable(); else if (state.view === 'flow' && state.cur) auDrawPane();
}
const lstRowOf = (rows: LstSourceRow[], s: LstSource): LstSourceRow | undefined => rows.find(r => r.key === s.id || r.id === s.id);
/** A source's last read worked and it is not overdue (a push source is never overdue). */
function lstRowOk(r: LstSourceRow | undefined): boolean {
  if (!r || r.status !== 'ok') return false;
  if (r.kind === 'push') return true;
  const at = Date.parse(r.last_ok_at || r.last_read_at || '');
  return !Number.isNaN(at) && Date.now() - at < LST_STALE;
}
function lstRowWords(r: LstSourceRow | undefined): string {
  if (lstRowOk(r)) return 'Read';
  if (r?.status === 'ok') return 'Not read for two days';
  return (r?.status === 'error' && r.last_error) || LST_STATUS[r?.status || 'never'] || 'Not read yet';
}
const lstDot = (ok: boolean, words: string): string => `<span class="au-dot ${ok ? 'ok' : 'lst-no'}" role="img" aria-label="${esc(words)}" title="${esc(words)}"></span>`;
/** A listening's one dot, as the server gives it: hollow when a source did not read well, when posts wait unscored,
 *  and with no source. */
const lstOk = (a: AuRow): boolean => (a as AuRow & {listening?: {dot?: string | null} | null}).listening?.dot === 'filled';

// ----------------------------------------------------------------------------------------- the list
function lstListHTML(state: AutomationsState): string {
  const q = state.f.q, all = state.list || [];
  const rows = all.filter(a => !q || [a.name, lstLimitsWords(a.trigger?.limits), ...(a.trigger?.sources || []).flatMap(s => [s.site, s.target])]
    .join(' ').toLowerCase().includes(q));
  if (!rows.length) return `<p class="muted au-pad">${all.length ? 'No matches' : 'No listening yet'}</p>`;
  return `<div class="lst-list" role="list">${rows.map(a => {
    const ok = lstOk(a), last = a.last_run?.started_at;
    return `<a class="lst-row${a.enabled === false ? ' off' : ''}" role="listitem" href="${auHref(a.id)}" data-lst="${esc(a.id)}">
      ${lstDot(ok, ok ? 'Reading' : 'Not reading')}<span class="lst-nm">${esc(a.name || 'Untitled')}</span>
      <span class="lst-lim">${esc(lstLimitsWords(a.trigger?.limits))}</span>
      <span class="lst-last"${last ? ` title="${esc(fmt(last))}"` : ''}>${esc(auAge(last)) || '—'}</span></a>`;
  }).join('')}</div>`;
}
// +: the viewer's own, off, 10 a day, one step that posts what was sent; then open it.
async function lstNew(): Promise<void> {
  const state = AU;
  auDrawn(state);
  const button = auEl<HTMLButtonElement>(state.root, '.au-new');
  button.disabled = true;
  try {
    const out = auNormal(await post('/v2/automations', {name: 'New listening', owner: 'person:' + S.me!.id, enabled: false, builtin: false,
      trigger: {type: 'listening', sources: [], rules: [], limits: [{count: 10, per: 'day'}], at: '08:00', tz: auZone()},
      steps: [{id: auNewId(), type: 'action', enabled: true, do: 'post_update', args: {text: '{{listening.text}}'}}]}));
    if (AU !== state) return;
    state.list = [...(state.list || []), out];
    location.hash = auHref(out.id);
  } catch (e) { toast(errorMessage(e, ''), true); }
  finally { if (button.isConnected) button.disabled = false; }
}

// ----------------------------------------------------------------------------------------- the flow's block
const lstCount = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;
function lstSummary(t: Partial<AuFlowTrigger>): string {
  return [lstCount((t.sources || []).length, 'source'), lstCount((t.rules || []).length, 'rule'), lstLimitsWords(t.limits)].filter(Boolean).join(' · ');
}
/** The trigger's place in the flow: Sources, Rules and Rate limits; one line while a step is being edited. */
function lstBlockHTML(a: AuAutomation): string {
  const state = auPage(), t = a.trigger, ed = state.ed, edit = !!a.can_edit;
  const head = `<div class="au-cr"><span class="nav-icon" aria-hidden="true">${AU_TRIGGER_ICONS.listening}</span><span class="au-ct">Listening</span></div>`;
  if (ed?.kind === 'step') return `<div class="au-card au-trig" role="button" tabindex="0" data-open="trigger">${head}
    <div class="au-chips"><span class="au-c1">${esc(lstSummary(t))}</span></div></div>`;
  const rows = state.lstSources?.get(a.id) || [], limits = lstSorted(t.limits), first = limits[0];
  const add = (what: string, label: string) => edit ? `<button class="linkish au-plus" type="button" data-lst-add="${what}">+ ${label}</button>` : '';
  const sources = (t.sources || []).map(s => {
    const r = lstRowOf(rows, s), addr = state.lstAddr?.[s.id];
    return `<div class="lst-r">${lstDot(lstRowOk(r), lstRowWords(r))}<span class="lst-site">${esc(s.site || (s.kind === 'push' ? 'Push' : 'Page'))}</span>
      <span class="lst-tg">${esc(s.kind === 'push' ? 'Push' : s.target || '')}</span><span class="lst-age">${esc(auAge(r?.last_read_at))}</span></div>`
      + (addr ? `<div class="lst-addr"><code>${esc(addr)}</code><button class="au-icon" type="button" data-lst-copy="${esc(addr)}" aria-label="Copy address">
        <span class="nav-icon" aria-hidden="true">content_copy</span></button></div>` : '');
  }).join('');
  const rules = `<div class="lst-r"><span class="nav-icon lst-lk" aria-hidden="true">lock</span><span class="lst-tx">Remove duplicates</span></div>`
    + (t.rules || []).map(r => `<div class="lst-r"><span class="lst-do">${r.do === 'drop' ? 'Drop' : 'Keep'}</span>
      ${r.do === 'drop' ? '' : `<span class="au-c1">${esc(r.tag || '')}</span>`}<span class="lst-tx">${esc(r.text || '')}</span></div>`).join('');
  const lims = limits.map(l => `<div class="lst-r"><span class="lst-tx">Up to ${esc(l.count ?? '')} every ${esc(l.per)}`
    + `${l === first && l.per !== 'hour' && t.at ? ` · ${esc(t.at)}` : ''}</span></div>`).join('');
  return `<div class="au-card au-trig lst-blk${ed?.kind === 'trigger' ? ' sel' : ''}" role="button" tabindex="0" data-open="trigger">${head}
    <div class="lst-sec"><div class="au-lb">Sources</div>${sources}${add('lsrc', 'Source')}</div>
    <div class="lst-sec"><div class="au-lb">Rules</div>${rules}${add('lrule', 'Rule')}</div>
    <div class="lst-sec"><div class="au-lb">Rate limits</div>${lims}${limits.length < LST_UNITS.length ? add('llimit', 'Limit') : ''}</div></div>`;
}
/** Clicks on the page's listening parts; true when one was handled. */
function lstClick(t: Element): boolean {
  let m: HTMLElement | null;
  m = t.closest<HTMLElement>('[data-lst-copy]');
  if (m) {
    void copyText(m.dataset.lstCopy || '').then(() => toast('Copied'), () => toast('Could not copy', true));
    return true;
  }
  m = t.closest<HTMLElement>('[data-lst-add]');
  if (m) { lstEdAdd(m.dataset.lstAdd || ''); return true; }
  return false;
}
// "+ Source", "+ Rule", "+ Limit" on the block: the editor opens with the new row in it.
function lstEdAdd(what: string): void {
  auHave(AU);
  if (!AU.cur) return;
  if (AU.ed?.kind !== 'trigger') auEdOpenStep('trigger');
  const ed = AU.ed;
  if (ed?.kind !== 'trigger') return;
  const t = ed.trigger, at: Record<string, string> = {lsrc: 'sources', lrule: 'rules', llimit: 'limits'};
  if (what === 'lsrc') { t.sources ||= []; t.sources.push({id: lstNewId(), kind: 'search', site: '', target: ''}); }
  if (what === 'lrule') { t.rules ||= []; t.rules.push({do: 'keep', tag: '', text: ''}); }
  if (what === 'llimit' && (t.limits || []).length < LST_UNITS.length) { t.limits ||= []; t.limits.push(lstNextLimit(t.limits)); }
  auDrawSide();
  const fields = auNeed(AU.root, '#au-root').querySelectorAll<HTMLElement>(`.au-ed [data-b^="trigger.${at[what]}."]:not([data-b$=".text"]):not([data-b$=".per"])`);
  const row = fields[fields.length - 1]?.closest('.au-rb, .lst-lr');
  row?.scrollIntoView({block: 'nearest'});
  if (!auPhone()) row?.querySelector<HTMLElement>('input')?.focus({preventScroll: true});
}

// ----------------------------------------------------------------------------------------- the editor
/** The trigger editor's fields for a listening: its name and its three parts. */
function lstFields(t: AuFlowTrigger, ed?: AuTriggerEd): string {
  const saved = new Set((auCur().trigger.sources || []).map(s => s.id)), limits = t.limits || [], dis = auDis();
  const sources = (t.sources || []).map((s, i) => `<div class="au-rb"><div class="au-ir">
      ${auSel(`trigger.sources.${i}.kind`, auWithCur(LST_KINDS, s.kind), s.kind, 'data-redraw aria-label="Kind"')}
      ${auIn(`trigger.sources.${i}.site`, s.site, s.kind === 'push' ? 'Name' : 'Site', 'aria-label="Site"')}${auX('trigger.sources', i, 'Remove source')}</div>
    ${s.kind === 'push' ? (saved.has(s.id) && !auRO() ? `<button class="linkish au-plus" type="button" data-op="lst-addr" data-v="${esc(s.id)}">New address</button>` : '')
      : auIn(`trigger.sources.${i}.target`, s.target, s.kind === 'page' ? 'https://' : 'Search or community', 'aria-label="What to read"')}</div>`).join('');
  const rules = `<div class="au-rb lst-dup"><span class="nav-icon" aria-hidden="true">lock</span>Remove duplicates</div>`
    + (t.rules || []).map((r, i) => `<div class="au-rb"><div class="au-ir">
      ${auSel(`trigger.rules.${i}.do`, [['keep', 'Keep'], ['drop', 'Drop']], r.do, 'data-redraw aria-label="Rule"')}
      ${r.do === 'drop' ? '' : auIn(`trigger.rules.${i}.tag`, r.tag, 'tag', 'aria-label="Tag" maxlength="24" autocapitalize="off" spellcheck="false"')}${auX('trigger.rules', i, 'Remove rule')}</div>
    <textarea data-b="trigger.rules.${i}.text" rows="2" maxlength="300" spellcheck="true" aria-label="Sentence"${dis}>${esc(r.text ?? '')}</textarea></div>`).join('');
  const lims = limits.map((l, i) => {
    const units = LST_UNITS.filter(u => u === l.per || !limits.some(x => x.per === u)).map((u): [string, string] => [u, u]);
    return `<div class="au-ir lst-lr"><span class="muted">Up to</span>
      <input type="number" data-b="trigger.limits.${i}.count" data-num min="1" max="100" step="1" value="${esc(l.count ?? '')}" aria-label="How many"${dis}>
      <span class="muted">every</span>${auSel(`trigger.limits.${i}.per`, auWithCur(units, l.per), l.per, 'data-redraw aria-label="How often"')}
      ${limits.length > 1 ? auX('trigger.limits', i, 'Remove limit') : ''}</div>`;
  }).join('');
  const hourly = limits.some(l => l.per === 'hour');
  return auFld('Name', auIn('name', ed?.name))
    + auBox('Sources', sources + ((t.sources || []).length < 50 ? auPlus('trigger.sources', 'lsrc', 'Source') : ''))
    + auBox('Rules', rules + ((t.rules || []).length < 20 ? auPlus('trigger.rules', 'lrule', 'Rule') : ''))
    + auBox('Rate limits', lims + (limits.length < LST_UNITS.length ? auPlus('trigger.limits', 'llimit', 'Limit') : ''))
    + (hourly ? '' : auFld('At', `<input type="time" data-b="trigger.at" value="${esc(t.at || '08:00')}"${dis}>`));
}
/** What Save writes: the name and the trigger, or the first thing wrong with them in a few words. */
function lstBody(ed: AuTriggerEd): {name: string; trigger: AuFlowTrigger} | string {
  const t = ed.trigger, name = String(ed.name || '').trim();
  if (!name) return 'Name it';
  const sources: LstSource[] = [];
  for (const s of t.sources || []) {
    const site = String(s.site || '').trim(), target = String(s.target || '').trim();
    if (s.kind === 'push') { if (!site) return 'Name the push source'; sources.push({id: s.id, kind: 'push', site}); continue; }
    if (!target || (s.kind === 'search' && !site)) return 'Each source needs a site and what to read';
    if (s.kind === 'page' && !/^https?:\/\/\S+$/.test(target)) return 'A page is a web address';
    sources.push({id: s.id, kind: s.kind, site: site || target.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] || '', target});
  }
  const rules: LstRule[] = [], tags = new Set<string>();
  for (const r of t.rules || []) {
    const text = String(r.text || '').trim(), tag = String(r.tag || '').trim();
    if (!text) return 'Each rule needs a sentence';
    if (r.do === 'drop') { rules.push({do: 'drop', text}); continue; }
    if (!LST_TAG.test(tag)) return 'A tag is lowercase letters, numbers and dashes';
    if (tags.has(tag)) return 'Each tag is used once';
    tags.add(tag);
    rules.push({do: 'keep', tag, text});
  }
  const limits: LstLimit[] = [];
  for (const l of t.limits || []) {
    const count = Number(l.count);
    if (!Number.isInteger(count) || count < 1 || count > 100) return 'A limit is 1 to 100';
    if (limits.some(x => x.per === l.per)) return 'One limit for each of hour, day, week and month';
    limits.push({count, per: l.per});
  }
  if (!limits.length) return 'Add a limit';
  const at = /^\d{2}:\d{2}$/.test(String(t.at || '')) ? String(t.at) : '08:00';
  return {name, trigger: {...t, type: 'listening', sources, rules, limits: lstSorted(limits), at, tz: String(t.tz || '').trim() || auZone()}};
}
/** A save that was refused says why; the next save that goes through takes that away. */
let lstRefusal: Element | null = null;
function lstRefused(why: string): void {
  lstRefusal?.remove();
  toast(why, true);
  lstRefusal = [...document.querySelectorAll('.toast.err')].at(-1) || null;
}
/** After a save: a new push source's address is shown once, and the sources' health is read again. */
function lstSaved(out: AuRow): void {
  const state = AU;
  lstRefusal?.remove(); lstRefusal = null;
  if (!state) return;
  const made = (out as AuRow & {push_addresses?: Record<string, string> | null}).push_addresses;
  if (made && Object.keys(made).length) { state.lstAddr = {...state.lstAddr, ...made}; auDrawPane(); }
  void lstSourcesLoad(state);
}
/** The editor's listening buttons: a push source's new address; a Paths step's split and its tags. */
function lstEdOp(op: string, m: HTMLElement, ed: AuEd): void {
  const v = m.dataset.v || '';
  if (op === 'lst-addr') { void lstNewAddress(v, m); return; }
  if (ed.kind !== 'step') return;
  if (op === 'lst-by') ed.step.by = v;
  if (op === 'lst-tag' && (ed.step.paths || []).length < AU_MAX_PATHS) { ed.step.paths ||= []; ed.step.paths.push(lstTagPath(v)); }
  auDrawSide();
}
async function lstNewAddress(sourceId: string, button: HTMLElement): Promise<void> {
  const state = AU;
  if (!state?.cur || !(await askConfirm('The old address stops working.'))) return;
  const row = (state.lstSources?.get(state.cur.id) || []).find(r => r.key === sourceId || r.id === sourceId);
  button.setAttribute('disabled', '');
  try {
    const out: {address?: string} = await post(`/v2/listening/sources/${encodeURIComponent(row?.id || sourceId)}/address`, {});
    if (AU !== state) return;
    if (out.address) state.lstAddr = {...state.lstAddr, [sourceId]: out.address};
    state.ed = null; auDrawPane(); auDrawSide();
  } catch (e) { toast(errorMessage(e, ''), true); if (button.isConnected) button.removeAttribute('disabled'); }
}

// ----------------------------------------------------------------------------------------- paths
const lstByWords = (by: string | undefined): string => by === 'item' ? 'Each item' : 'By tag';
/** A path condition that is just "this tag": the tag, else ''. */
const lstTagOf = (c: AuCond): string => c.field === 'item.tag' && (c.op || 'is') === 'is' && typeof c.value === 'string' ? c.value : '';
/** A path that is just one tag: its only condition is "item.tag is <its name>". Its editor is one tag field. */
function lstIsTagPath(p: AuPath): boolean {
  const w = p.when || [], c = w[0];
  return w.length === 1 && !!c && c.field === 'item.tag' && (c.op || 'is') === 'is' && (c.min == null || c.min === '') && c.value === (p.name || '');
}
/** A tag path's one field: the tag, which is the path's name and its condition's value. */
function lstTagField(i: number, p: AuPath, many: boolean): string {
  return `<div class="au-ir">${auIn(`step.paths.${i}.name`, p.name, 'tag', 'data-lst-tagpath aria-label="Tag" maxlength="24" autocapitalize="off" spellcheck="false"')}${many ? auX('step.paths', i, 'Remove path') : ''}</div>`;
}
const lstTagPath = (tag: string): AuPath => ({name: tag, when: [{field: 'item.tag', op: 'is', value: tag}], steps: []});
/** A new Paths step in a listening: by tag, one path for each Keep rule's tag. */
function lstNewPaths(): Pick<AuStep, 'by' | 'paths' | 'otherwise'> {
  const tags = lstKeepTags(AU?.cur?.trigger).slice(0, AU_MAX_PATHS);
  return {by: 'tag', paths: tags.length ? tags.map(lstTagPath) : [{name: 'Path 1', when: [], steps: []}], otherwise: {steps: []}};
}
/** The Paths editor's listening part: By tag or Each item, and the Keep tags no path takes yet. */
function lstPathsFields(s: AuStep): string {
  const by = s.by === 'item' ? 'item' : 'tag', ro = auRO();
  const taken = new Set((s.paths || []).flatMap(p => (p.when || []).map(lstTagOf)).filter(Boolean));
  const free = ro || (s.paths || []).length >= AU_MAX_PATHS ? [] : lstKeepTags(auCur().trigger).filter(t => !taken.has(t));
  return `<div class="au-f"><span class="au-bseg" role="group" aria-label="Split">${(['tag', 'item'] as const).map(k =>
      `<button type="button" data-op="lst-by" data-v="${k}" aria-pressed="${by === k}"${ro ? ' disabled' : ''}>${lstByWords(k)}</button>`).join('')}</span>
    ${free.length ? `<div class="au-chips lst-tags">${free.map(t => `<button class="au-c1" type="button" data-op="lst-tag" data-v="${esc(t)}">+ ${esc(t)}</button>`).join('')}</div>` : ''}</div>`;
}

// ----------------------------------------------------------------------------------------- a run
function lstRunOf(run: object): LstRun | null {
  const l: unknown = (run as {listening?: unknown}).listening;
  return typeof l === 'object' && l !== null ? l as LstRun : null;
}
/** A send's run, opened: the counts since the send before and the text that went out; the steps' lines follow. */
function lstRunHTML(run: object): string {
  const l = lstRunOf(run);
  if (!l) return '';
  const c = l.counts || {};
  const rows: Array<[string, number | undefined, string]> = [['Read', c.read, ''], ['Duplicates removed', c.duplicates, ''], ['Dropped by rules', c.dropped, ''],
    ['Kept', c.kept, ''], ['Sent', c.sent ?? l.count, ' lst-b'], ['Waiting', c.waiting ?? l.waiting, ' muted']];
  if (Number(c.failed) > 0) rows.splice(3, 0, ['Could not score', c.failed, '']);
  return `<div class="lst-run"><div class="lst-cnt"><div class="au-lb">Since the last one</div>
      ${rows.map(([label, n, cls]) => `<div class="lst-cr${cls}"><span>${label}</span><span class="au-n">${n == null ? '—' : Number(n).toLocaleString()}</span></div>`).join('')}</div>
    <div class="lst-sent"><div class="au-lb">Sent</div><pre>${esc(l.text || '')}</pre></div></div>
    <div class="au-lb lst-then">Then</div>`;
}
/** A test run's sample: one send of one item, as the steps get it. */
function lstSample(t: Partial<AuFlowTrigger>): Record<string, unknown> {
  const tag = lstKeepTags(t)[0] || '';
  const item = {id: 'item_1', tag, title: '', url: '', site: '', source: '', author: '', copies: 1, score: 0.9, text: ''};
  return {name: AU?.cur?.name || '', sent_at: new Date().toISOString().slice(0, 19) + 'Z', count: 1, waiting: 0, text: '', items: [item],
    groups: [{tag, count: 1, text: '', items: [item]}], counts: {read: 1, duplicates: 0, dropped: 0, kept: 1, sent: 1, waiting: 0, failed: 0}};
}
