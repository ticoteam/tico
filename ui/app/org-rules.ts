/* ui/app/org-rules.ts — Rules (docs/rules-plan.md, Screens 1, 2, 4–8; docs/reach.md): the Rules page with a tab for every
   kind of rule, each with its count (GET /rule-groups): Company (internal; Reach: the org chart and a party's panel),
   Outside (external), Pipelines and stages (stage rules by pipeline, then stage; changed from a stage's Rules button,
   stage-rules.ts), Due, Wakes and Automations on tasks (listed, edited on the Automations page), Built in (read-only)
   and Suggested (a bot's org rules and stage rules, for a person to accept or skip); the rule and due-date side panels, the
   Add rule and Add document dialogs, and a bot's Rules tab.
   Everything is read from and written to the org rules API (backend/org_rules.py): GET /api/v2/rules (paged; every
   page is read, a team has dozens of rules), /rules/due, /rule-sources and /bots/<slug>/rules. A rule
   says whether the caller may change it (`can_edit`), so the panels show its controls only then; the server checks
   again, and a refusal shows as one line. Saving sends the rule's `version`: a 409 reloads it and says so. Add document
   starts a Rules Owner review (POST /rules/read, backend/counsel.py); its suggestions wait in Suggested, and each one's
   quote shows in its side panel, never on its row.
   Addresses: #/rules[/external|/stages|/due|/wakes|/automations|/builtin|/suggested][?rule=<id> | ?due=<id> | ?due=new&rule=<id>], and on Internal
   ?party=<party>[&rule=<id>] (a party as docs/reach.md writes it: *, group:<id>, human:<id>, bot:<id>). The filters stay on
   the page, not in the address. On a phone the lists are stacked rows and a panel is a full-screen sheet.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RULES_ROUTE = '#/rules';
// Every kind of rule, in the page's order (Stage rules spec, The Rules page): each tab's count comes from /rule-groups.
const RU_TABS: [RuleTab, string][] = [['internal', 'Company'], ['external', 'Outside'], ['stages', 'Pipelines and stages'], ['due', 'Due'],
  ['wakes', 'Wakes'], ['automations', 'Automations on tasks'], ['builtin', 'Built in'], ['suggested', 'Suggested']];
const RU_REPEATS: [string, string][] = [['once', 'Once'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['quarterly', 'Quarterly'], ['yearly', 'Yearly']];
const RU_STATUS: Record<string, [string, string]> = {overdue: ['Overdue', 'fail'], due_soon: ['Due soon', 'ready'], upcoming: ['Upcoming', ''], satisfied: ['Satisfied', 'ok']};
const RU_KINDS: [string, string][] = [['vendor', 'Vendor'], ['customer', 'Customer'], ['government', 'Government'], ['other', 'Other']];
// If broken: the stored values stay block / escalate / warn; each label says what happens (org-rule-holds.js and
// overview-graph.js use the same words).
const RU_BROKEN: [IfBroken, string][] = [['block', 'Stop'], ['escalate', 'Ask first'], ['warn', 'Warn']];
const RU_BROKEN_HINT: Record<string, string> = {block: 'The output is refused.', escalate: 'Held until the reviewer OKs it.', warn: 'It goes through with a note.'};
const RU_PAGES = 50;        // pages read for one list at most (a page stops at 100 rows or at its byte budget)

type RuleTab = 'internal' | 'external' | 'stages' | 'due' | 'wakes' | 'automations' | 'builtin' | 'suggested';
/** An automation the Rules page lists and the Automations page edits: a What wakes one, or one a task starts. */
interface RuleAuto { id: string; name: string; enabled: boolean }
/** GET /rule-groups: each tab's count, and the rows of the read-only tabs. Optional: an older hub answers without them. */
interface RuleGroups {
  groups?: {key: RuleTab; label: string; count: number}[];
  wakes?: RuleAuto[]; automations?: RuleAuto[]; built_in?: StageBuiltIn[];
}
type RuleKind = 'internal' | 'external';
type IfBroken = 'block' | 'escalate' | 'warn';
type ScopeKind = 'groups' | 'bots' | 'people';
type RrKind = 'all' | 'group' | 'bot' | 'human';
/** A team, bot or person a rule names, as the API sends it: its id, and its name beside it. */
interface RuleRef { id: string; name?: string; }
/** Who a rule covers, or its With, as the API sends it. */
interface RuleScope {
  everyone?: boolean;
  all_bots?: boolean;
  outside_editions?: boolean;
  groups?: RuleRef[];
  bots?: RuleRef[];
  people?: RuleRef[];
}
/** A Who or With as the scope editor and the Add rule dialog keep and send it: ids only. */
interface ScopeIds {
  everyone?: boolean;
  all_bots?: boolean;
  outside_editions?: boolean;
  groups?: string[];
  bots?: string[];
  people?: string[];
}
/** A rule's source (its From): a vendor, customer, government or other document owner. */
interface RuleSource { id: string; name: string; kind: string; }
/** One due date of a rule, as the rules list, the due list and a due date's own page show it. */
interface RuleDue {
  id: string;
  due_on: string;
  repeats?: string;
  status: string;
  task_days?: number | null;
  owner?: string;
  rule: { text: string; id?: string };
  routine_id?: string | null;
  can_edit?: boolean;
  can_satisfy?: boolean;
  open_days?: string[];
  chain?: { satisfied?: { due_on: string; via?: string; at?: string; satisfied_by?: string }[] }[];
  retired_at?: string | null;
  ended_on?: string | null;
  replaced_by?: string | null;
}
/** A rule as the rules API lists or reads it. `who` and `with` are the scope editor's; `cite` is the Rules Owner's quote. */
interface RuleRow {
  id: string;
  kind: RuleKind;
  text: string;
  description?: string;
  status?: string;
  version: number;
  can_edit?: boolean;
  who?: RuleScope | null;
  with?: RuleScope | null;
  if_broken?: IfBroken | null;
  reviewer?: RuleRef | null;
  source?: RuleSource | null;
  due?: RuleDue[];
  due_more?: number;
  cite?: { file_id?: string | null; version?: number | null; quote: string; section?: string; hidden?: boolean } | null;
}
/** One page of a list the rules API reads (rules, due dates or sources), and where the next one starts. */
interface RulesPage<T> { rules?: T[]; sources?: T[]; due?: T[]; next_cursor?: string | null; }
/** A bot's Rules tab answer: a page of each kind, and its settings as sentences. */
interface BotRulesPage { rules: RuleRow[]; next_cursor?: string | null; }
interface BotRules { internal?: BotRulesPage; external?: BotRulesPage; settings?: BotSetting[]; }
/** One of a bot's settings as a rule sentence, with where it is changed. */
interface BotSetting {
  text: string;
  source?: string;
  edit?: { page?: string; path?: string; anchor?: string } | null;
}
/** A party's line, as Reach lists it: a sender, a receiver, a daily count or a rule. */
interface ReachLine {
  id: string;
  sender: string;
  receiver: string;
  per_day?: number | null;
  rule?: { id?: string; text: string } | null;
  may_edit?: boolean;
}
interface ReachCaps { mode: string; book: string; chats_per_day: number; tasks_per_day: number; }
/** One setting of a party in Reach (its own value, or the server's effective one). */
interface ReachSetting { party: string; mode?: string; book?: string | null; chats_per_day?: number | null; tasks_per_day?: number | null; }
/** Reach as GET /v2/reach answers it. */
interface ReachData {
  defaults?: Partial<ReachCaps & { person_mode: string }>;
  settings?: ReachSetting[];
  effective?: Record<string, ReachCaps>;
  lines?: ReachLine[];
  may_edit?: string[];
}
/** What a party's value of a setting is, and the party that sets each of them (null: the default). */
type ReachEff = ReachCaps & { from?: Record<string, string | null> };
/** Reach after rrNormal has filled the defaults. */
interface ReachNormal {
  defaults: ReachCaps & { person_mode: string };
  settings: ReachSetting[];
  effective: Record<string, ReachCaps>;
  lines: ReachLine[];
  mayEdit: Set<string>;
}
/** A line being made or changed in the Reach panel. */
interface RrEdit { id: string | null; other: string; dir: string; per_day: number | string | null | undefined; rule: string; }
/** The Reach panel's state: the normalised answer, the selected party, its search and its open line editor. */
interface ReachState extends ReachNormal {
  groups: OrgGroup[] | null;
  error: string;
  party: string | null;
  q: string;
  edit: RrEdit | null;
  adding: boolean;
  shut: Set<string>;
  chart: string;
  shown?: string | null;
}
/** The Rules page's state (RU): the open tab, every list it has read, its filters and the side panel's note. */
interface RuState {
  root: HTMLElement;
  tab: RuleTab | null;
  rules: { internal: RuleRow[]; external: RuleRow[] };
  reach: ReachState;
  suggested: RuleRow[];
  /** Stage rules: those in force (Pipelines and stages), and a bot's waiting in Suggested. */
  stageRules: StageRule[];
  stageSuggested: StageRule[];
  groups: RuleGroups | null;
  due: RuleDue[];
  sources: RuleSource[];
  filter: { q: string; who: string; from: string; due: boolean; status: string; routine: string; moreFrom: boolean };
  seq: number;
  loaded: Set<string>;
  error: string;
  note?: { key: string; text: string } | null;
}
/** A box in the chart's own pixels. */
interface RrBox { x: number; y: number; w: number; h: number; }
/** A party in a search or a list: Everyone, a team, a person or a bot. */
interface RrParty { party: string; name: string; kind: string; }
/** A person on the roster as the Rules page reads them. */
type RuPerson = NonNullable<AppState['people'][number]> & { archived?: boolean };
/** A scope editor's segment: Everyone, Anyone, or the kind of id it adds (Departments, Bots, People). */
type ScopeSeg = 'everyone' | 'anyone' | ScopeKind;
/** A segment as a button or a chip names it: a segment, or Outside editions. */
type SegKey = ScopeSeg | 'outside';
/** A scope editor's state: the ids it holds and the segment it adds to. */
type ScopeEdit = Required<ScopeIds> & { seg: ScopeSeg };
/** The Rule panel's named controls (`form.elements`). */
interface RuleEditControls { text: HTMLTextAreaElement; description: HTMLTextAreaElement; source: HTMLSelectElement; reviewer: HTMLSelectElement; }
/** The Add rule dialog's named controls. */
interface AddRuleControls { text: HTMLTextAreaElement; source: HTMLSelectElement; source_name: HTMLInputElement; source_kind: HTMLSelectElement; reviewer: HTMLSelectElement; }
/** The Due date panel's named controls. */
interface DueControls { rule: HTMLSelectElement; due: HTMLInputElement; repeats: HTMLSelectElement; owner: HTMLSelectElement; routine: HTMLSelectElement; task_days: HTMLInputElement; due_on: HTMLSelectElement; }
/** The Add document dialog's named controls. */
interface DocControls { find: HTMLInputElement; note: HTMLTextAreaElement; source: HTMLSelectElement; source_name: HTMLInputElement; }
/** A file from Files as the Add document picker lists it. */
interface DocFile { id: string; title?: string; name?: string; locator?: string; url?: string | null; library_kind?: string; archived?: boolean; [key: string]: unknown; }
/** What POST /rules/read answers. */
interface DocReadAnswer { task_id: string; screening?: string; existing?: boolean; truncated?: boolean; file?: { title?: string }; }
let RU: RuState | null = null;   // the open Rules page; null off it
/** The open Rules page's state. Off the page it throws, as reading a field of null did. */
function ruOpen(): RuState {
  if (!RU) throw new Error('The Rules page is not open');
  return RU;
}
/** The open tab. Off a tab it throws, as drawing it from null did. */
function ruTab(): RuleTab {
  const tab = ruOpen().tab;
  if (!tab) throw new Error('The Rules page has no open tab');
  return tab;
}
/** A part of the Rules page (or of `within`); throws, as reading a field of the missing element did, when it is not there. */
function ruFind<E extends Element = HTMLElement>(selector: string, within: ParentNode = ruOpen().root): E {
  const el = within.querySelector<E>(selector);
  if (!el) throw new Error(`The Rules page has no ${selector}`);
  return el;
}

/** The nearest ancestor that matches; throws, as reading a field of the missing element did, when there is none. */
function ruClosest<E extends HTMLElement>(el: Element, selector: string): E {
  const found = el.closest<E>(selector);
  if (!found) throw new Error(`The Rules page has no ${selector} above it`);
  return found;
}
/** A form's named controls, typed by the caller (a form's elements are read by their names). */
function ruControls<T>(form: HTMLFormElement): T { return form.elements as unknown as T; }
const ruPhone = () => matchMedia('(max-width:760px)').matches;
const ruQuery = () => new URLSearchParams((S.route || '').split('?')[1] || '');
const ruTabOf = (r: string): RuleTab => { const t = ((r || '').split('?')[0] ?? '').slice(RULES_ROUTE.length + 1); return RU_TABS.some(([k]) => k === t) ? t as RuleTab : 'internal'; };
const ruHref = (tab: string | null, q: Record<string, string> = {}) => {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v)).toString();
  return RULES_ROUTE + (tab && tab !== 'internal' ? '/' + tab : '') + (qs ? '?' + qs : '');
};

// ----------------------------------------------------------------- names
const ruGroups = () => (S.orgGroups || []).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
// The Assistant and its helpers (the Rules Owner among them) are in no bot picker (ui/app/sidebar.ts isHiddenBot).
const ruBots = () => (S.emps || []).filter(e => e.status !== 'archived' && !isHiddenBot(e.id)).map(e => ({id: e.id, name: botDisplayName(e.id)}))
  .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
const ruPeople = () => (S.people || []).filter((p: RuPerson) => !p.hidden && !p.archived).map(p => ({id: p.id, name: p.name || p.email || p.id}))
  .sort((a, b) => a.name.localeCompare(b.name));
const ruGroupName = (id: string) => (S.orgGroups || []).find(g => g.id === id)?.name || id;
const ruPersonName = (id: string) => (S.people || []).find(p => p.id === id)?.name || id;
function ruActorName(actor: string | null | undefined) {
  const [kind, id] = String(actor || '').split(/:(.*)/);
  if (kind === 'bot') return botDisplayName(id);
  if (kind === 'human' || kind === 'person') return ruPersonName(id ?? '');
  return actor ? 'Tico' : '';
}
// A Who or With as the API shows it ({everyone, all_bots, groups: [{id, name}], ...}) in words.
function ruScopeWords(scope: RuleScope | null | undefined, empty = '—') {
  if (!scope) return empty;
  const outside = scope.outside_editions ? ['Outside editions'] : [];
  if (scope.everyone) return ['Everyone', ...outside].join(', ');
  const words = [...(scope.all_bots ? ['All bots'] : []), ...outside, ...(['groups', 'bots', 'people'] as const).flatMap(k => (scope[k] || []).map(x => x.name || x.id))];
  return words.length ? words.join(', ') : empty;
}
// Who follows a rule and with whom, in one sentence: "All bots follow this with Ben Okafor." Takes the API's view
// ({id, name} entries) or the scope editor's ids.
function ruFollowSentence(who: RuleScope | ScopeIds | null | undefined, withScope: RuleScope | ScopeIds | null | undefined) {
  const name = (kind: ScopeKind, x: RuleRef | string): string => {
    const id = typeof x === 'string' ? x : x.id;
    return (typeof x === 'string' ? '' : x.name) || (kind === 'groups' ? ruGroupName(id) : kind === 'bots' ? botDisplayName(id) : ruPersonName(id));
  };
  const names = (s: RuleScope | ScopeIds | null | undefined) => (['groups', 'bots', 'people'] as const).flatMap(k => (s?.[k] || []).map(x => name(k, x)));
  const list = (a: string[]) => a.length < 3 ? a.join(' and ') : a.slice(0, -1).join(', ') + ' and ' + a.at(-1);
  const parts = who?.everyone ? ['Everyone'] : [...(who?.all_bots ? ['All bots'] : []), ...names(who)];
  if (who?.outside_editions) parts.push('Outside editions');
  if (!parts.length) return '';
  const subject = list(parts.map((p, i) => i && (p === 'All bots' || p === 'Outside editions') ? p.toLowerCase() : p));
  const one = parts.length === 1 && !['All bots', 'Outside editions'].includes(parts[0] ?? '');
  const other = names(withScope);
  return `${subject} ${one ? 'follows' : 'follow'} this${other.length ? ' with ' + list(other) : ''}.`;
}
// Its ids only, as the API takes it and as the scope editor keeps it.
const ruScopeIds = (scope: RuleScope | ScopeIds | null | undefined): Required<ScopeIds> => ({everyone: !!scope?.everyone, all_bots: !!scope?.all_bots, outside_editions: !!scope?.outside_editions,
  groups: (scope?.groups || []).map(x => typeof x === 'string' ? x : x.id), bots: (scope?.bots || []).map(x => typeof x === 'string' ? x : x.id),
  people: (scope?.people || []).map(x => typeof x === 'string' ? x : x.id)});
const ruSameScope = (a: RuleScope | ScopeIds | null | undefined, b: RuleScope | ScopeIds | null | undefined) => JSON.stringify(ruScopeIds(a)) === JSON.stringify(ruScopeIds(b));

// ----------------------------------------------------------------- mentions
// A rule names a teammate the way a thread's composer writes a mention: @slug for a bot, @handle for a person (their
// id, the part of their email before the @, or a first name no one else shares). Each shows as a mention chip.
function ruMentionOf(word: string) {
  const w = word.toLowerCase();
  const bot = (S.emps || []).find(e => e.status !== 'archived' && (String(e.id).toLowerCase() === w || String(e.name || '').toLowerCase() === w));
  if (bot) return {href: '#/bot/' + encodeURIComponent(bot.id), name: botDisplayName(bot.id)};
  const people = (S.people || []).filter((p: RuPerson) => !p.archived), first = (p: RuPerson) => String(p.name || '').split(' ')[0]?.toLowerCase();
  const firsts = people.filter(p => first(p) === w);
  const p = people.find(p => String(p.id).toLowerCase() === w || (p.email && personHandle(p.email) === w)) || (firsts.length === 1 ? firsts[0] : null);
  return p ? {href: '#/person/' + encodeURIComponent(p.id), name: p.name || p.id} : null;
}
// A rule's text as HTML, its @mentions as chips. `as`: 'link' (an <a>), 'inline' (inside a row that is itself a link:
// a span that opens the teammate on its own click) or 'plain' (inside a button: the chip's look only).
function ruTextHTML(text: string, as: 'link' | 'inline' | 'plain' = 'link') {
  return esc(text).replace(/(^|[^\w@])@(\w[\w.-]*)((?:&#39;s\b|[,;:!?)])*)/g, (all: string, pre: string, word: string, after: string) => {
    const handle = word.replace(/[.-]+$/, ''), hit = handle && ruMentionOf(handle);
    if (!hit) return all;
    const label = '@' + esc(hit.name), tail = word.slice(handle.length) + after;
    const chip = as === 'link' ? `<a class="ru-at" href="${esc(hit.href)}">${label}</a>`
      : as === 'inline' ? `<span class="ru-at" data-href="${esc(hit.href)}">${label}</span>` : `<span class="ru-at">${label}</span>`;
    return pre + (tail ? `<span class="ru-at-w">${chip}${tail}</span>` : chip);
  });
}
// An inline chip opens its teammate, not the row around it.
document.addEventListener('click', ev => {
  const at = eventElement(ev)?.closest<HTMLElement>('.ru-at[data-href]');
  if (!at) return;
  ev.preventDefault(); ev.stopPropagation();
  location.hash = at.dataset.href ?? '';
}, true);

// "Jan 31", or "Jan 31, 2027" outside this year.
function ruDay(iso: string | null | undefined, year = false) {
  if (!iso) return '';
  const d = new Date(String(iso).slice(0, 10) + 'T12:00:00');
  if (isNaN(Number(d))) return String(iso);
  const opts: Intl.DateTimeFormatOptions = {month: 'short', day: 'numeric'};
  if (year || d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString(undefined, opts);
}
const ruRepeatWord = (r: string | undefined) => (RU_REPEATS.find(([k]) => k === r) || [, r || ''])[1] ?? '';
const ruPill = (status: string) => { const [label, tone] = RU_STATUS[status] || [status, '']; return `<span class="pill ru-pill ${tone}">${esc(label)}</span>`; };
// A rule's due dates, the most urgent first: overdue, then due soon, upcoming, satisfied; the nearest within each.
const RU_URGENCY: Record<string, number> = {overdue: 0, due_soon: 1, upcoming: 2, satisfied: 3};
const ruDueSorted = (rule: RuleRow) => (rule.due || []).slice().sort((a, b) => (RU_URGENCY[a.status] ?? 9) - (RU_URGENCY[b.status] ?? 9) || a.due_on.localeCompare(b.due_on));
// The most urgent one, and "+N" for the rest (those the view counts but leaves out too).
// A routine's lead time: "14 days before"; null with no routine.
const ruLead = (days: number | null | undefined) => days == null ? null : `${days} day${days === 1 ? '' : 's'} before`;
const ruDueCell = (rule: RuleRow) => {
  const [d, ...rest] = ruDueSorted(rule), more = rest.length + (rule.due_more || 0);
  return d ? `<span class="ru-due">${esc(ruDay(d.due_on))}${d.repeats && d.repeats !== 'once' ? ` <span class="muted">· ${esc(ruRepeatWord(d.repeats).toLowerCase())}</span>` : ''}${more ? ` <span class="ru-plus">+${more}</span>` : ''}</span>` : '<span class="ru-none">—</span>';
};
// A refusal in one line: a 403 is about permission, the rest say what the server said.
const ruError = (e: unknown) => errorStatus(e) === 403 ? "You can't change this." : errorMessage(e, 'Something went wrong.');

// ----------------------------------------------------------------- reads
async function ruAll<T = RuleRow>(path: string): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | null | undefined = '';
  for (let i = 0; i < RU_PAGES; i++) {
    const page: RulesPage<T> = await get<RulesPage<T>>(path + (path.includes('?') ? '&' : '?') + 'limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
    out.push(...(page.rules || page.sources || page.due || []));
    cursor = page.next_cursor;
    if (!cursor) break;
  }
  return out;
}
async function ruLoad(tab: RuleTab): Promise<boolean> {
  const state = ruOpen(), seq = ++state.seq;
  try {
    if (tab === 'internal') {
      // A hub without Reach still lists the rules; the panel says what went wrong.
      const [rules, reach, groups] = await Promise.all([ruAll('/v2/rules?kind=internal'),
        get<ReachData>('/v2/reach', {fresh: true}).then((d): {d?: ReachData; e?: unknown} => ({d}), (e: unknown): {d?: ReachData; e?: unknown} => ({e})),
        get<OrgGroup[] | {groups?: OrgGroup[]} | null>('/v2/groups').catch(() => null)]);
      state.rules.internal = rules;
      Object.assign(state.reach, rrNormal(reach.d), {error: reach.e ? rrError(reach.e) : '',
        groups: Array.isArray(groups) ? groups : groups?.groups || null});
    } else if (tab === 'external') {
      [state.rules.external, state.sources] = await Promise.all([ruAll('/v2/rules?kind=external'), ruAll<RuleSource>('/v2/rule-sources')]);
    } else if (tab === 'due') {
      state.due = await ruAll<RuleDue>('/v2/rules/due?next=3660d');
    } else if (tab === 'stages') {
      state.stageRules = (await get<StageRulesList>('/v2/stage-rules', {fresh: true})).items || [];
    } else if (tab === 'suggested') {
      [state.suggested, state.stageSuggested] = await Promise.all([ruAll('/v2/rules?status=suggested'),
        get<StageRulesList>('/v2/stage-rules?status=suggested', {fresh: true}).then(d => d.items || [])]);
    }
    // Every tab's count, and the rows of Wakes, Automations on tasks and Built in. Read on its own: a tab still lists
    // its rules when the counts can't be read, and shows none.
    state.groups = await get<RuleGroups>('/v2/rule-groups', {fresh: true}).catch((): RuleGroups => ({}));
    state.error = '';
  } catch (e) { state.error = errorMessage(e, ''); }
  if (RU !== state || seq !== state.seq) return false;
  state.loaded.add(tab);
  return true;
}
// ----------------------------------------------------------------- the page
async function pageRules() {
  const tab = ruTabOf(S.route);
  const main: HTMLElement | null = $('#main');
  if (!main) throw new Error('The page has no #main');
  main.classList.add('ru-layout');
  if (!RU || !document.body.contains(RU.root)) {
    main.innerHTML = `<div class="ru" id="ru-root">
      ${pageHead({title: 'Rules', hook: 'ru-ph', pad: true, tabsHook: 'ru-tabs', tabsAttrs: 'aria-label="Rules"'})}
      <div class="ru-body"><aside class="ru-filters" aria-label="Filters"></aside><section class="ru-list" aria-live="polite">${skelRows(6)}</section>
        <aside class="ru-side" id="ru-side" hidden></aside></div></div>`;
    const root: HTMLElement | null = $('#ru-root');
    if (!root) throw new Error('The Rules page did not draw');
    RU = {root, tab: null, rules: {internal: [], external: []}, suggested: [], stageRules: [], stageSuggested: [], groups: null, due: [], sources: [],
          reach: {...rrNormal(null), error: '', groups: null, party: null, q: '', edit: null, adding: false, shut: new Set(), chart: ''},
          filter: {q: '', who: '', from: '', due: false, status: '', routine: '', moreFrom: false},
          seq: 0, loaded: new Set(), error: ''};
    ruWire();
    void ruleChecksMount(RU.root.querySelector('.ru-ph'));
  }
  const state = RU;
  if (state.tab !== tab) {
    state.tab = tab;
    state.filter = {...state.filter, who: '', from: '', due: false, status: '', routine: '', q: ''};
    ruDrawHead();
    if (!state.loaded.has(tab)) ruFind('.ru-list', state.root).innerHTML = skelRows(6);
    else ruDraw();
    if (!await ruLoad(tab) || RU !== state || state.tab !== tab) return;
  } else if (!state.loaded.has(tab)) {   // a write elsewhere on the page marked it stale
    if (!await ruLoad(tab) || RU !== state || state.tab !== tab) return;
  }
  ruDraw();
  ruDrawPanel();
}
function rulesStop() { RU = null; }

// The header (pageHead, drawn once by pageRules) keeps its title; each tab change fills its tabs, tools and button.
function ruDrawHead() {
  const ru = ruOpen(), head = ruFind('.ru-ph', ru.root);
  const count = (k: RuleTab) => ru.groups?.groups?.find(g => g.key === k)?.count;
  pageHeadSet(head, 'tabs', pageHeadTabs(RU_TABS.map(([k, label]) => ({label, href: ruHref(k), count: count(k), cur: ru.tab === k, attrs: `data-ru-tab="${k}"`}))));
  const human = S.me?.role !== 'bot';
  // Internal's search stays put while the page draws again, so it keeps what is typed and its focus.
  if (ru.tab === 'internal' && head.querySelector('.ph-tools .rr-q')) return;
  type ByTab = Partial<Record<RuleTab, string>>;
  const tools = ({
    internal: `<div class="rr-find"><input class="rr-q" type="search" placeholder="Search people, bots and teams" aria-label="Search people, bots and teams" autocomplete="off">
      <div class="rr-results" role="listbox" aria-label="Matches" hidden></div></div>`,
    external: human ? '<button class="ghost" type="button" data-ru-add>Add rule</button>' : '',
  } as ByTab)[ruTab()] || '';
  const primary = ({
    external: human ? '<button class="primary ph-go" type="button" data-ru-add-doc>Add document</button>' : '',
    due: human ? '<button class="primary ph-go" type="button" data-ru-add-due>Add due date</button>' : '',
  } as ByTab)[ruTab()] || '';
  pageHeadSet(head, 'tools', tools);
  pageHeadSet(head, 'primary', primary);
}
function ruDraw() {
  ruDrawHead();
  const root = ruOpen().root, list = ruFind('.ru-list', root), filters = ruFind('.ru-filters', root);
  root.dataset.tab = ruTab();
  if (ruOpen().error) { filters.innerHTML = ''; list.innerHTML = `<p class="err ru-pad">${esc(ruOpen().error)}</p>`; return; }
  const draw = {internal: ruDrawInternal, external: ruDrawExternal, due: ruDrawDue, suggested: ruDrawSuggested, stages: ruDrawStages,
    wakes: ruDrawWakes, automations: ruDrawAutomations, builtin: ruDrawBuiltIn}[ruTab()];
  draw(filters, list);
}

// Draws again, the search box keeping its focus and caret when it had them.
function ruDrawKeepSearch() {
  const box = ruOpen().root.querySelector<HTMLInputElement>('.ru-q'), had = document.activeElement === box, at = box?.selectionStart ?? null;
  ruDraw();
  if (!had) return;
  const q = ruOpen().root.querySelector<HTMLInputElement>('.ru-q'); q?.focus(); q?.setSelectionRange(at, at);
}
// One facet row: a label, its count, pressed when it is the filter.
const ruFacet = (key: string, value: string, label: string | undefined, n: number, on: boolean) => `<button type="button" class="ru-f" data-ru-f="${esc(key)}" data-v="${esc(value)}" aria-pressed="${on}"><span>${esc(label)}</span><span class="ru-n">${n}</span></button>`;
// The Who (or With) facets of a list of rules, by how many rules name each.
function ruScopeFacets(rules: RuleRow[], key: 'who' | 'from', field: 'who' | 'with') {
  const counts = new Map<string, number>(), label = new Map<string, string>();
  const add = (k: string, l: string) => { counts.set(k, (counts.get(k) || 0) + 1); label.set(k, l); };
  for (const r of rules) {
    const s = r[field]; if (!s) continue;
    if (s.everyone) add('everyone', 'Everyone');
    if (s.all_bots) add('all_bots', 'All bots');
    if (s.outside_editions) add('outside_editions', 'Outside editions');
    for (const kind of ['groups', 'bots', 'people'] as const) for (const x of s[kind] || []) add(kind + ':' + x.id, x.name || x.id);
  }
  const cur = ruOpen().filter[key];
  return [...counts].sort((a, b) => b[1] - a[1] || (label.get(a[0]) ?? '').localeCompare(label.get(b[0]) ?? ''))
    .map(([k, n]) => ruFacet(key, k, label.get(k), n, cur === k)).join('');
}
const ruScopeHas = (scope: RuleScope | null | undefined, key: string) => {
  if (!key) return true;
  if (!scope) return false;
  if (key === 'everyone') return !!scope.everyone;
  if (key === 'all_bots') return !!scope.all_bots;
  if (key === 'outside_editions') return !!scope.outside_editions;
  const [kind, id] = key.split(/:(.*)/);
  return (scope[kind as ScopeKind] || []).some(x => x.id === id);
};
const ruMatches = (text: string, q: string) => !q || q.toLowerCase().split(/\s+/).filter(Boolean).every(w => text.toLowerCase().includes(w));
const ruSearch = () => `<input class="ru-q" type="search" placeholder="Search" aria-label="Search rules" autocomplete="off" value="${esc(ruOpen().filter.q)}">`;
const ruSel = (id: string) => ruQuery().get('rule') === id;

// ----------------------------------------------------------------- Internal: Reach (docs/reach.md)
// The map is the Org tab's chart (rdoChartHtml over orgChartShape), every group open and its tab a button: tapping a
// teammate, a team's tab or Everyone selects that party. From the selected party a line runs to each party it has a line
// with, coloured by its count (0 red, N amber, a rule alone blue and dashed), with its chip. The panel beside it reads
// and changes the party's settings, lists the rules it follows and its lines. On a phone the map gives way to a list.
// GET /v2/reach holds it all; each change writes one setting or one line, then reads it again.
// Bots, teams and Everyone start nearby; a person's own mode starts open (people are never refused).
const RR_DEFAULTS = {mode: 'nearby', book: 'anyone', chats_per_day: 999, tasks_per_day: 999, person_mode: 'open'};
const RR_MAX = 100000;
const RR_MATCHES = 8;      // the header search's list
const RR_ADD_MATCHES = 7;  // + Add exception's list
const RR_MODES: [string, string][] = [['open', 'Can reach anyone'], ['nearby', 'Nearby'], ['closed', "Only who's listed"]];
const RR_BOOKS: [string, string][] = [['anyone', 'Anyone'], ['nearby', 'Nearby'], ['none', 'No one']];

function rrNormal(d: ReachData | null | undefined): ReachNormal {
  return {defaults: {...RR_DEFAULTS, ...(d?.defaults || {})}, settings: d?.settings || [], effective: d?.effective || {},
    lines: d?.lines || [], mayEdit: new Set(d?.may_edit || [])};
}
// The selected party: the address's, else Everyone on a computer and none (the list) on a phone.
const rrSel = () => ruQuery().get('party') || (ruPhone() ? '' : '*');
const rrKind = (p: string | null) => p === '*' ? 'all' : String(p).split(':')[0] as RrKind;
const rrId = (p: string | null) => String(p).slice(String(p).indexOf(':') + 1);
function rrName(p: string | null): string {
  const kind = rrKind(p), id = rrId(p);
  return kind === 'all' ? 'Everyone' : kind === 'group' ? ruGroupName(id) : kind === 'bot' ? botDisplayName(id) : ruPersonName(id);
}
// The teams above a party, nearest first: a person's or bot's home team and the teams that hold it; a team's parents.
function rrTeams(p: string): string[] {
  const kind = rrKind(p), id = rrId(p), groups = new Map<string | undefined, OrgGroup>((S.orgGroups || []).map((g): [string, OrgGroup] => [g.id, g]));
  const home = kind === 'group' ? groups.get(id)?.parent : kind === 'bot' ? botRow(id)?.team
    : kind === 'human' ? (S.people || []).find(x => x.id === id)?.team : '';
  const out: string[] = [];
  for (let g = groups.get(home), hops = 0; g && hops < 20 && !out.includes(g.id); g = groups.get(g.parent), hops++) out.push(g.id);
  return out;
}
const rrOwn = (p: string, R: ReachNormal = ruOpen().reach): Partial<ReachSetting> => R.settings.find(s => s.party === p) || {};
// What applies to a party: the server's answer, else the same walk worked out here (own, teams nearest first,
// Everyone, the default). `from` names the party that sets each value, null for the default, as the server's does.
function rrEff(p: string, R: ReachNormal = ruOpen().reach): ReachEff {
  if (R.effective[p]) return {...R.defaults, ...R.effective[p]};
  const chain = [p, ...rrTeams(p).map(id => 'group:' + id), ...(p === '*' ? [] : ['*'])], out: Record<string, string | number | null | undefined> = {}, from: Record<string, string | null> = {};
  for (const f of ['mode', 'book', 'chats_per_day', 'tasks_per_day'] as const) {
    const at = chain.find(q => rrOwn(q, R)[f] != null);
    out[f] = at ? rrOwn(at, R)[f] : f === 'mode' && rrKind(p) === 'human' ? R.defaults.person_mode : R.defaults[f];
    from[f] = at ?? null;
  }
  return {...out, from} as ReachEff;
}
// Where a party's value of a setting comes from, as the panel words it: '' when the party sets it itself, else
// "from Everyone" or "from Sales" (the party above that does), or "default".
function rrFrom(p: string, eff: ReachEff, f: string) {
  const at = eff.from?.[f];
  return at === p ? '' : at ? 'from ' + rrName(at) : 'default';
}
// The panel's source tag reads as a state on its label line: "Default", "From Sales".
const rrFromTag = (p: string, eff: ReachEff, f: string) => { const w = rrFrom(p, eff, f); return w ? `<span class="rr-from">${esc(w[0]?.toUpperCase() + w.slice(1))}</span>` : ''; };
const rrMay = (p: string) => ruOpen().reach.mayEdit.has(p);
// A hub from before Reach has no /v2/reach: its rules still list.
const rrError = (e: unknown) => errorStatus(e) === 404 ? 'Reach needs a newer hub.' : errorMessage(e, 'Something went wrong.');
// A party's lines as its panel lists them: those it sends first ("To"), then those it receives ("From").
const rrLinesOf = (p: string | null, R: ReachNormal = ruOpen().reach) => R.lines.filter(l => (l.sender === p) !== (l.receiver === p))
  .map(l => ({l, dir: l.sender === p ? 'to' : 'from', other: l.sender === p ? l.receiver : l.sender}))
  .sort((a, b) => (a.dir === 'to' ? 0 : 1) - (b.dir === 'to' ? 0 : 1));
const rrTone = (l: ReachLine) => l.per_day === 0 ? 'zero' : l.per_day != null ? 'cap' : l.rule?.text ? 'rule' : 'open';
const rrCount = (l: ReachLine) => l.per_day != null ? `${l.per_day} a day` : l.rule?.text ? '' : 'Allowed';
// A line's chip says its count ("3 a day", "Allowed"), or "Rule" for a rule alone; the rule's words are in its title and
// in the panel's row, never cut on the line.
const rrChip = (l: ReachLine) => rrCount(l) || 'Rule';
const rrWords = (l: ReachLine) => [rrCount(l), l.rule?.text || ''].filter(Boolean).join(' · ');
// Who a rule names, as the Add rule dialog takes it.
const rrScope = (p: string) => {
  const kind = rrKind(p), id = rrId(p);
  return kind === 'all' ? {everyone: true} : {[({group: 'groups', bot: 'bots', human: 'people'} as const)[kind]]: [id]};
};
// Everyone, then every team, person and bot, as the searches offer them.
const rrParties = (): RrParty[] => [...ruGroups().map(g => ({party: 'group:' + g.id, name: g.name || g.id, kind: 'Team'})),
  ...ruPeople().map(x => ({party: 'human:' + x.id, name: x.name, kind: 'Person'})), ...ruBots().map(b => ({party: 'bot:' + b.id, name: b.name, kind: 'Bot'}))];
const rrFind = (q: string, {everyone = true}: {everyone?: boolean} = {}): RrParty[] => [...(everyone ? [{party: '*', name: 'Everyone', kind: ''}] : []), ...rrParties()].filter(x => ruMatches(x.name, q));
const rrOption = (x: RrParty) => `<button type="button" class="rr-opt" role="option" data-rr-party="${esc(x.party)}"><span class="rr-on">${esc(x.name)}</span><span class="rr-k">${esc(x.kind)}</span></button>`;

// The rules a party follows: the company's (Who Everyone), each team's above it, and for a bot those for all bots, then
// the ones naming the party itself. A line's rule is listed with its line, not here.
function rrRules(p: string) {
  const lineRules = new Set(ruOpen().reach.lines.map(l => l.rule?.id).filter(Boolean));
  const rules = ruOpen().rules.internal.filter(r => !lineRules.has(r.id) && (!r.status || r.status === 'active'));
  const names = (r: RuleRow, kind: ScopeKind, id: string) => (r.who?.[kind] || []).some(x => (x.id ?? x) === id);
  const kind = rrKind(p), id = rrId(p), inherited: [string, string, RuleRow[]][] = [];
  if (kind !== 'all') inherited.push(['company', 'Company rules', rules.filter(r => r.who?.everyone)]);
  for (const t of rrTeams(p)) inherited.push(['group:' + t, ruGroupName(t) + ' rules', rules.filter(r => names(r, 'groups', t))]);
  if (kind === 'bot') inherited.push(['all_bots', 'Rules for all bots', rules.filter(r => r.who?.all_bots)]);
  const own = kind === 'all' ? rules.filter(r => r.who?.everyone) : rules.filter(r => names(r, ({group: 'groups', bot: 'bots', human: 'people'} as const)[kind], id));
  return {inherited: inherited.filter(g => g[2].length), own};
}

// ----------------------------------------------------------------- the map
const rrLead = (id: string) => ((ruOpen().reach.groups || S.orgGroups || []).find(g => g.id === id)?.lead) || '';
// A group always open, its tab selecting the team.
function rrGroupBlock(by: Parameters<typeof rdoNode>[0], g: OrgGroup, ctx: Parameters<typeof rdoNode>[2], depth: number) {
  const kids = rdoSorted(by['g:' + g.id] || [], g.id, rrLead(g.id));
  return `<section class="rdo-g" data-rr-team="group:${esc(g.id)}"><button type="button" class="rdo-tab" data-rr-party="group:${esc(g.id)}" title="${esc(g.name)}"><span class="rdo-tn">${esc(g.name)}</span></button>
    <div class="rdo-gb">${kids.map((k: OrgNode) => `<div class="rdo-gi${k.kind === 'group' ? ' is-g' : ''}">${rdoNode(by, k, ctx, depth + 1)}</div>`).join('')}</div></section>`;
}
const rrChartHtml = () => rdoChartHtml(rdoTree(rrLead), {seen: new Set(), group: rrGroupBlock});
// A card's ref (rdoCard: person:<id> or bot:<id>) as a party.
const rrOfRef = (ref: string) => ref.startsWith('person:') ? 'human:' + ref.slice(7) : ref;
// A box in the chart's own pixels.
function rrBox(el: HTMLElement, cv: HTMLElement): RrBox {
  let x = 0, y = 0;
  for (let e: HTMLElement | null = el; e && e !== cv; e = e.offsetParent as HTMLElement | null) { x += e.offsetLeft; y += e.offsetTop; }
  return {x, y, w: el.offsetWidth, h: el.offsetHeight};
}
// Where the segment from a box's middle toward (tx, ty) leaves the box, a few pixels out.
function rrEdge(b: RrBox, tx: number, ty: number): [number, number] {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2, dx = tx - cx, dy = ty - cy;
  const k = Math.min(dx ? (b.w / 2 + 3) / Math.abs(dx) : Infinity, dy ? (b.h / 2 + 3) / Math.abs(dy) : Infinity, 1);
  return [cx + dx * k, cy + dy * k];
}
// The chart is drawn at its own size, never shrunk: past the pane it scrolls and a mouse drags it (dragPan). The
// selected party, the parties it has lines with, and the search's matches are marked, and the lines and chips drawn.
// The rest are muted only while a team, person or bot is selected or a search runs (Everyone dims nothing).
function rrMapPaint() {
  const map = RU?.root.querySelector<HTMLElement>('.rr-map');
  if (!map || !map.offsetParent) return;
  const pane = ruFind('.rr-pane', map), sz = ruFind('.rr-sz', map), cv = ruFind('.rr-cv', map);
  sz.style.width = cv.offsetWidth + 'px'; sz.style.height = cv.offsetHeight + 'px';
  const R = ruOpen().reach, p = R.party, q = R.q;
  const nodes = [...cv.querySelectorAll<HTMLElement>('[data-rr-party]')], at = (party: string | null) => nodes.find(n => n.dataset.rrParty === party);
  const lines = rrLinesOf(p);
  const tone = new Map(lines.map((x): [string, string] => [x.other, rrTone(x.l)]));
  const team = rrKind(p) === 'group' ? cv.querySelector(`[data-rr-team="${CSS.escape(p ?? '')}"]`) : null;
  const hits = q ? new Set(rrFind(q).map(x => x.party)) : null;
  map.classList.toggle('has-sel', !!p && p !== '*');
  map.classList.toggle('searching', !!q);
  for (const n of nodes) {
    const party = n.dataset.rrParty ?? '';
    n.classList.toggle('on', party === p);
    n.setAttribute('aria-pressed', String(party === p));
    if (tone.has(party)) n.dataset.rrTone = tone.get(party) ?? ''; else delete n.dataset.rrTone;
    n.classList.toggle('in-team', !!team && team.contains(n));
    n.classList.toggle('hit', !!hits?.has(party));
  }
  const svg = ruFind<SVGElement>('.rr-lines', cv), chips = ruFind('.rr-chips', cv), from = at(p);
  svg.setAttribute('width', String(cv.offsetWidth)); svg.setAttribute('height', String(cv.offsetHeight));
  const marks = ['zero', 'cap', 'rule', 'open'].map(k => `<marker id="rr-mk-${k}" class="rr-mk ${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"/></marker>`).join('');
  let paths = '', labels = '';
  const spans: [number, number, number, number][] = [];   // each chip's line (from the selected party's end), for placing it below
  // A card not wholly in the pane's view: its chip leads with its name, so the line says where it goes.
  const view = pane.getBoundingClientRect(), vx = view.left + pane.clientLeft, vy = view.top + pane.clientTop;
  const unseen = (el: HTMLElement) => { const r = el.getBoundingClientRect(); return r.left < vx - 1 || r.top < vy - 1 || r.right > vx + pane.clientWidth + 1 || r.bottom > vy + pane.clientHeight + 1; };
  if (from) {
    const a = rrBox(from, cv), both = new Set(lines.filter(x => lines.some(y => y !== x && y.other === x.other)).map(x => x.other));
    for (const {l, dir, other} of lines) {
      const to = at(other);
      if (!to) continue;
      const b = rrBox(to, cv), k = rrTone(l);
      const [ax, ay] = rrEdge(a, b.x + b.w / 2, b.y + b.h / 2), [bx, by] = rrEdge(b, a.x + a.w / 2, a.y + a.h / 2);
      // A pair with lines both ways: each a little to its own side, so both show.
      const len = Math.hypot(bx - ax, by - ay) || 1, off = both.has(other) ? (dir === 'to' ? 5 : -5) : 0;
      const ox = -(by - ay) / len * off, oy = (bx - ax) / len * off;
      const [x1, y1, x2, y2]: [number, number, number, number] = dir === 'to' ? [ax + ox, ay + oy, bx + ox, by + oy] : [bx + ox, by + oy, ax + ox, ay + oy];
      paths += `<line class="rr-ln ${k}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" marker-end="url(#rr-mk-${k})"/>`;
      const cx = ax + (bx - ax) * 0.55 + ox * 3, cy = ay + (by - ay) * 0.55 + oy * 3;
      spans.push([ax + ox * 3, ay + oy * 3, bx + ox * 3, by + oy * 3]);
      const words = (dir === 'to' ? rrName(p) + ' to ' + rrName(other) : rrName(other) + ' to ' + rrName(p)) + ': ' + rrWords(l);
      const name = unseen(to) ? `<span class="rr-cn">${esc(rrName(other))}</span> · ` : '';
      labels += `<button type="button" class="rr-chip ${k}" data-rr-line="${esc(l.id)}" style="left:${cx.toFixed(1)}px;top:${cy.toFixed(1)}px" aria-label="${esc(words)}" title="${esc(words)}">${name}${esc(rrChip(l))}</button>`;
    }
  }
  // A party picked from the search or a link, off screen in a chart wider than the pane (a phone): bring it into view.
  if (R.shown !== p && (R.shown = p) && from) {
    const b = rrBox(from, cv), l = sz.offsetLeft + b.x, t = b.y, r = l + b.w, btm = b.y + b.h;
    if (l < pane.scrollLeft || r > pane.scrollLeft + pane.clientWidth || t < pane.scrollTop || btm > pane.scrollTop + pane.clientHeight)
      pane.scrollTo({left: (l + r - pane.clientWidth) / 2, top: (t + btm - pane.clientHeight) / 2});
  }
  svg.innerHTML = `<defs>${marks}</defs>${paths}`;
  // The chips are drawn again on every paint (a scroll coming to rest among them): a chip that had focus keeps it.
  const focused = document.activeElement instanceof HTMLElement && chips.contains(document.activeElement) ? document.activeElement.dataset.rrLine : '';
  chips.innerHTML = labels;
  if (focused) chips.querySelector<HTMLElement>(`[data-rr-line="${CSS.escape(focused)}"]`)?.focus({preventScroll: true});
  // A chip goes where it covers no card and no chip already placed: along its line first (90px past the selected card,
  // then the middle outward), then above, below or beside it; where nothing is clear it keeps the first spot that
  // misses the chips. Near the selected card it stays on screen however far its line runs.
  const cards = nodes.filter(n => n.offsetParent).map(n => rrBox(n, cv)), placed: RrBox[] = [];
  const over = (r: RrBox, x: number, y: number, w: number, h: number, pad: number) => Math.abs(r.x + r.w / 2 - x) < (r.w + w) / 2 + pad && Math.abs(r.y + r.h / 2 - y) < (r.h + h) / 2 + pad;
  chips.querySelectorAll<HTMLElement>(':scope > *').forEach((c, i) => {
    const w = c.offsetWidth, h = c.offsetHeight, [x1, y1, x2, y2] = spans[i] ?? [0, 0, 0, 0];
    const tries: [number, number][] = [], near = Math.min(0.55, (90 + w / 2) / (Math.hypot(x2 - x1, y2 - y1) || 1));
    for (const t of [near, 0.55, 0.45, 0.65, 0.35, 0.75, 0.5]) for (const dy of [0, h + 4, -(h + 4), 2 * (h + 4), -2 * (h + 4)])
      for (const dx of [0, w * 0.6, -w * 0.6]) tries.push([x1 + (x2 - x1) * t + dx, y1 + (y2 - y1) * t + dy]);
    const freeOfChips = ([x, y]: [number, number]) => !placed.some(r => over(r, x, y, w, h, 3));
    const at = tries.find(q => freeOfChips(q) && !cards.some(r => over(r, q[0], q[1], w, h, 2))) || tries.find(freeOfChips) || tries[0];
    if (!at) return;
    c.style.left = at[0].toFixed(1) + 'px'; c.style.top = at[1].toFixed(1) + 'px';
    placed.push({x: at[0] - w / 2, y: at[1] - h / 2, w, h});
  });
}
// The search's matches under the header box (a computer) and the list of parties (a phone).
function rrSearchPaint() {
  const R = ruOpen().reach, root = ruOpen().root, box = root.querySelector<HTMLElement>('.ru-ph .rr-results');
  if (box) {
    const found = R.q ? rrFind(R.q).slice(0, RR_MATCHES) : [];
    box.hidden = !R.q;
    box.innerHTML = found.map(rrOption).join('') || (R.q ? '<p class="rr-none">Nothing matches.</p>' : '');
  }
  const pick = root.querySelector<HTMLElement>('.rr-pick');
  if (pick) {
    const found = rrFind(R.q);
    pick.innerHTML = found.map(rrOption).join('') || '<p class="rr-none">Nothing matches.</p>';
  }
  root.querySelector('.rr')?.classList.toggle('picked', !!R.party && !R.q);
  root.querySelector('.rr')?.classList.toggle('searching', !!R.q);
  rrMapPaint();
}

// ----------------------------------------------------------------- the panel
// True while the panel's HTML is being replaced: a box removed then may fire change (rrChange).
let RR_DRAWING = false;
function rrPanelDraw(note = '') {
  const side = ruOpen().root.querySelector<HTMLElement>('.rr-side'), R = ruOpen().reach, p = R.party;
  if (!side) return;
  if (!p) { RR_DRAWING = true; try { side.innerHTML = ''; } finally { RR_DRAWING = false; } return; }
  const may = rrMay(p), dis = may ? '' : ' disabled', eff = rrEff(p), own = rrOwn(p);
  const human = S.me?.role !== 'bot';
  const {inherited, own: mine} = rrRules(p);
  const rule = (r: RuleRow, cls = 'rr-rule') => `<a class="${cls}" href="${ruHref('internal', {party: p, rule: r.id})}" data-rule="${esc(r.id)}">${ruTextHTML(r.text, 'inline')}</a>`;
  // A cap shows the value that applies: typed over it, it is the party's own; cleared, it is taken from above again.
  const cap = (f: 'chats_per_day' | 'tasks_per_day', word: string) => `<label class="rr-cap"><span>Up to</span><input type="text" inputmode="numeric" data-rr-cap="${f}" value="${esc(own[f] ?? eff[f])}"
    aria-label="${esc((word[0] ?? '').toUpperCase() + word.slice(1))}"${dis}><span>${esc(word)}</span>${rrFromTag(p, eff, f)}</label>`;
  const groups = inherited.map(([k, label, rules]) => { const open = !R.shut.has(k); return `<div class="rr-inh">
      <button type="button" class="rr-inh-h" data-rr-inh="${esc(k)}" aria-expanded="${open}"><span class="rr-cx" aria-hidden="true">›</span><span class="rr-inh-t">${esc(label)}</span><span class="ru-n">${rules.length}</span></button>
      ${open ? `<div class="rr-inh-l">${rules.map(r => rule(r, 'rr-rule rr-sub')).join('')}</div>` : ''}</div>`; }).join('');
  // Who bots may book is a bot's calendar rule: a person's own has nothing to limit, so none is shown for one.
  // Each control under its label line, the source of its value at the line's right; the control on one row.
  const head = (label: string, f: string) => `<div class="rr-row-h"><span class="rr-row-l">${esc(label)}</span>${rrFromTag(p, eff, f)}</div>`;
  const book = rrKind(p) === 'human' ? '' : `<div class="rr-row rr-book">${head('Who bots may book', 'book')}${ruSegHTML('rr-book', RR_BOOKS, eff.book, !may)}</div>`;
  const lines = rrLinesOf(p), E = R.edit;
  const row = ({l, dir, other}: ReturnType<typeof rrLinesOf>[number]) => {
    const body = `<span class="rr-lt"><span class="rr-dir">${dir === 'to' ? 'To' : 'From'}</span><span class="rr-who">${esc(rrName(other))}</span>
      <span class="rr-n ${rrTone(l)}">${esc(rrCount(l))}</span></span>${l.rule?.text ? `<span class="rr-lr">${ruTextHTML(l.rule.text, 'plain')}</span>` : ''}`;
    return l.may_edit ? `<button type="button" class="rr-line" data-rr-line="${esc(l.id)}">${body}</button>` : `<div class="rr-line ro" data-rr-line="${esc(l.id)}">${body}</div>`;
  };
  const list = R.adding ? rrAddHTML(p) : E ? rrEditorHTML(p, E)
    : lines.map(row).join('') || '<p class="rr-none">None.</p>';
  RR_DRAWING = true;
  try {
  side.innerHTML = `<div class="rr-panel" data-party="${esc(p)}">
    <div class="rr-h"><a class="ru-x rr-back" href="${ruHref('internal')}" aria-label="Back">‹</a><h2>${esc(rrName(p))}</h2></div>
    ${R.error ? `<p class="err">${esc(R.error)}</p>` : ''}
    <div class="rr-set"><div class="rr-row rr-reach">${head('Reach', 'mode')}${ruSegHTML('rr-mode', RR_MODES, eff.mode, !may)}</div>${book}${cap('chats_per_day', 'chats a day')}${cap('tasks_per_day', 'tasks a day')}</div>
    <section class="rr-sec" aria-label="Rules"><header><h3 class="ru-lbl">Rules</h3>${human ? '<button class="ghost small" type="button" data-rr-add-rule>+ Add rule</button>' : ''}</header>
      ${groups}${mine.map(r => rule(r)).join('')}${groups || mine.length ? '' : '<p class="rr-none">None.</p>'}</section>
    <section class="rr-sec" aria-label="Exceptions"><header><h3 class="ru-lbl">Exceptions</h3>${R.mayEdit.size && !R.adding && !E ? '<button class="ghost small" type="button" data-rr-add-line>+ Add exception</button>' : ''}</header>
      ${list}</section>
    <p class="ru-status" role="status">${esc(note)}</p></div>`;
  } finally { RR_DRAWING = false; }
}
// + Add exception: search a team, person or bot; picking one opens the editor.
function rrAddHTML(p: string) {
  return `<div class="rr-box rr-add"><input type="search" class="rr-xq" placeholder="Team, person or bot" aria-label="Team, person or bot" autocomplete="off">
    <div class="rr-xres" role="listbox" aria-label="Matches">${rrAddMatches(p, '')}</div>
    <div class="rr-foot"><span class="spacer"></span><button class="ghost small" type="button" data-rr-cancel>Cancel</button></div></div>`;
}
function rrAddMatches(p: string | null, q: string) {
  const taken = new Set(rrLinesOf(p).map(x => x.other));
  return rrFind(q, {everyone: false}).filter(x => x.party !== p && !taken.has(x.party)).slice(0, RR_ADD_MATCHES)
    .map(x => rrOption(x).replace('data-rr-party=', 'data-rr-pick=')).join('') || '<p class="rr-none">Nothing matches.</p>';
}
// The line editor: which way (only a sender the viewer may change), how many a day (empty: no count), and its rule.
function rrEditorHTML(p: string, E: RrEdit) {
  const me = esc(rrName(p)), them = esc(rrName(E.other));
  const way = (v: string, label: string, ok: boolean) => `<button type="button" data-rr-dir="${v}" aria-pressed="${E.dir === v}"${ok ? '' : ' disabled'} title="${label}">${label}</button>`;
  return `<div class="rr-box rr-ed" data-rr-ed>
    <div class="ru-seg rr-dirs" role="group" aria-label="Direction">${way('to', `${me} → ${them}`, rrMay(p))}${way('from', `${them} → ${me}`, rrMay(E.other))}</div>
    <label class="rr-cap"><span>Up to</span><input type="text" inputmode="numeric" name="per_day" value="${esc(E.per_day ?? '')}" aria-label="Up to a day"><span>a day</span></label>
    <textarea name="rule" rows="2" maxlength="1000" placeholder="Rule" aria-label="Rule">${esc(E.rule || '')}</textarea>
    <div class="rr-foot"><button class="primary small" type="button" data-rr-save>${E.id ? 'Save' : 'Add'}</button><button class="ghost small" type="button" data-rr-cancel>Cancel</button>
      <span class="spacer"></span>${E.id ? '<button class="ghost small rr-rm" type="button" data-rr-remove>Remove</button>' : ''}</div></div>`;
}

function ruDrawInternal(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  const R = ruOpen().reach, p = rrSel();
  if (R.party !== p) Object.assign(R, {party: p, edit: null, adding: false});
  let rr = list.querySelector<HTMLElement>(':scope > .rr');
  const chart = rrChartHtml();
  if (!rr || R.chart !== chart) {
    R.chart = chart;
    list.innerHTML = `<div class="rr">
      <div class="rr-map"><div class="rr-pane"><div class="rr-sz"><div class="rdo-cv rr-cv">
        <div class="rr-every"><button type="button" class="rr-all" data-rr-party="*">Everyone</button></div>${chart}
        <svg class="rr-lines" aria-hidden="true"></svg><div class="rr-chips"></div></div></div></div></div>
      <div class="rr-pick" role="listbox" aria-label="People, bots and teams"></div>
      <aside class="rr-side" aria-label="Reach"></aside></div>`;
    rr = ruFind<HTMLElement>(':scope > .rr', list);
    rr.querySelectorAll<HTMLElement>('.rr-cv [data-rdo-ref]').forEach(n => { n.dataset.rrParty = rrOfRef(n.dataset.rdoRef ?? ''); });
    const pane = ruFind('.rr-pane', rr);
    let queued = false;
    new ResizeObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; if (pane.isConnected) rrMapPaint(); });
    }).observe(pane);
    document.fonts?.ready.then(() => { if (pane.isConnected) rrMapPaint(); });
    // A mouse drags the map about; once it comes to rest the chips are drawn again, naming the cards now out of view.
    dragPan(pane);
    let still = 0;
    pane.addEventListener('scroll', () => { clearTimeout(still); still = window.setTimeout(() => { if (pane.isConnected) rrMapPaint(); }, 120); }, {passive: true});
  }
  rrSearchPaint();
  rrPanelDraw();
}
// Select a party: the address names it, so Back returns to the one before.
function rrPick(p: string) {
  const R = ruOpen().reach, box = ruOpen().root.querySelector<HTMLInputElement>('.rr-q');
  R.q = '';
  if (box) box.value = '';
  const href = ruHref('internal', {party: p});
  if (location.hash !== href) { location.hash = href; return; }
  Object.assign(R, {party: p, edit: null, adding: false});
  rrSearchPaint(); rrPanelDraw();
}
async function rrReload(note = '') {
  const state = ruOpen();
  try {
    const [d, rules] = await Promise.all([get<ReachData>('/v2/reach', {fresh: true}), ruAll('/v2/rules?kind=internal')]);
    if (RU !== state) return;
    Object.assign(state.reach, rrNormal(d), {error: ''});
    state.rules.internal = rules;
  } catch (e) { if (RU === state) state.reach.error = rrError(e); }
  if (RU !== state || state.tab !== 'internal') return;
  rrMapPaint(); rrPanelDraw(note);
}
const rrStatus = (text: string) => { const el = ruOpen().root.querySelector('.rr-panel .ru-status'); if (el) el.textContent = text; };
// One setting of the selected party: a mode, a book, or a cap (empty clears it, so it is taken from above).
async function rrPut(p: string, field: string, value: number | string | null) {
  if ((field === 'chats_per_day' || field === 'tasks_per_day') && value != null && !(Number.isInteger(value) && Number(value) >= 0 && Number(value) <= RR_MAX)) {
    rrPanelDraw(`A whole number from 0 to ${RR_MAX}.`); return;
  }
  rrStatus('Saving…');
  try { await put('/v2/reach/settings/' + encodeURIComponent(p), {[field]: value}); }
  catch (e) { rrPanelDraw(ruError(e)); return; }
  await rrReload('Saved');
}
async function rrSaveLine(button: HTMLButtonElement) {
  const R = ruOpen().reach, E = R.edit, p = R.party;
  if (!E || !p) throw new Error('No exception is open');
  const n = String(E.per_day ?? '').trim(), perDay = n === '' ? null : Number(n);
  if (perDay != null && !(Number.isInteger(perDay) && perDay >= 0 && perDay <= RR_MAX)) { rrStatus(`A whole number from 0 to ${RR_MAX}.`); return; }
  const [sender, receiver] = E.dir === 'to' ? [p, E.other] : [E.other, p];
  const body = {sender, receiver, per_day: perDay, rule: (E.rule || '').trim() || null};
  button.disabled = true; rrStatus('Saving…');
  try {
    if (E.id) await post('/v2/reach/lines/' + encodeURIComponent(E.id), body);
    else await post('/v2/reach/lines', body);
  } catch (e) { button.disabled = false; rrStatus(ruError(e)); return; }
  R.edit = null;
  await rrReload('Saved');
}
async function rrRemoveLine(button: HTMLButtonElement) {
  const R = ruOpen().reach, E = R.edit;
  if (!E) throw new Error('No exception is open');
  button.disabled = true; rrStatus('Saving…');
  try { await writeRequest('DELETE', '/v2/reach/lines/' + encodeURIComponent(String(E.id))); }
  catch (e) { button.disabled = false; rrStatus(ruError(e)); return; }
  R.edit = null;
  await rrReload('Removed');
}
function rrEdit(id: string) {
  const R = ruOpen().reach, x = rrLinesOf(R.party).find(y => y.l.id === id);
  if (!x?.l.may_edit) return;
  R.adding = false;
  R.edit = {id, other: x.other, dir: x.dir, per_day: x.l.per_day, rule: x.l.rule?.text || ''};
  rrMapPaint(); rrPanelDraw();
  ruOpen().root.querySelector<HTMLInputElement>('.rr-ed input[name=per_day]')?.focus();
}
// A chip on the map: a line the viewer may change opens its editor; any other closes an open editor and shows the
// line's row in the panel, lit for a moment.
function rrChipGo(id: string) {
  const R = ruOpen().reach, x = rrLinesOf(R.party).find(y => y.l.id === id);
  if (!x) return;
  if (x.l.may_edit) { rrEdit(id); return; }
  if (R.edit || R.adding) { Object.assign(R, {adding: false, edit: null}); rrMapPaint(); rrPanelDraw(); }
  const row = ruOpen().root.querySelector<HTMLElement>(`.rr-panel [data-rr-line="${CSS.escape(id)}"]`);
  if (!row) return;
  row.scrollIntoView({block: 'nearest'});
  row.classList.add('lit');
  setTimeout(() => row.classList.remove('lit'), 1200);
}
/** The selected party of the open panel; every control that uses it lives inside that panel. */
function rrParty(): string {
  const p = ruOpen().reach.party;
  if (!p) throw new Error('No party is selected');
  return p;
}
// Clicks on Internal; true when one was handled.
function rrClick(ev: Event) {
  const t = eventElement(ev), R = ruOpen().reach;
  if (!t) return false;
  const party = t.closest<HTMLElement>('[data-rr-party]');
  if (party) { rrPick(party.dataset.rrParty ?? ''); return true; }
  const inh = t.closest<HTMLElement>('[data-rr-inh]');
  if (inh) { const k = inh.dataset.rrInh ?? ''; R.shut.has(k) ? R.shut.delete(k) : R.shut.add(k); rrPanelDraw(); return true; }
  if (t.closest('[data-rr-add-rule]')) {
    const p = rrParty();
    void ruAddOpen({kind: 'internal', who: rrScope(p), after: async made => {
      ruOpen().loaded.clear();
      if (made.status !== 'active') { location.hash = ruHref('suggested'); return; }
      if (made.kind !== 'internal') { location.hash = ruHref(made.kind, {rule: made.id}); return; }
      await ruReload();
      location.hash = ruHref('internal', {party: p, rule: made.id});
    }});
    return true;
  }
  if (t.closest('[data-rr-add-line]')) {
    Object.assign(R, {adding: true, edit: null}); rrPanelDraw();
    ruOpen().root.querySelector<HTMLInputElement>('.rr-xq')?.focus();
    return true;
  }
  const pick = t.closest<HTMLElement>('[data-rr-pick]');
  if (pick) {
    const other = pick.dataset.rrPick ?? '', p = rrParty();
    Object.assign(R, {adding: false, edit: {id: null, other, dir: rrMay(p) || !rrMay(other) ? 'to' : 'from',
      per_day: rrEff(p).mode === 'open' ? 0 : null, rule: ''}});
    rrPanelDraw();
    ruOpen().root.querySelector<HTMLInputElement>('.rr-ed input[name=per_day]')?.focus();
    return true;
  }
  const chip = t.closest<HTMLElement>('.rr-chip[data-rr-line]');
  if (chip) { rrChipGo(chip.dataset.rrLine ?? ''); return true; }
  const line = t.closest<HTMLElement>('[data-rr-line]');
  if (line) { rrEdit(line.dataset.rrLine ?? ''); return true; }
  const way = t.closest<HTMLButtonElement>('[data-rr-dir]');
  if (way) {
    if (way.disabled) return true;
    if (!R.edit) throw new Error('No exception is open');
    R.edit.dir = way.dataset.rrDir ?? '';
    way.parentElement?.querySelectorAll('[data-rr-dir]').forEach(b => b.setAttribute('aria-pressed', String(b === way)));
    return true;
  }
  if (t.closest('[data-rr-cancel]')) { Object.assign(R, {adding: false, edit: null}); rrMapPaint(); rrPanelDraw(); return true; }
  const save = t.closest<HTMLButtonElement>('[data-rr-save]');
  if (save) { void rrSaveLine(save); return true; }
  const remove = t.closest<HTMLButtonElement>('[data-rr-remove]');
  if (remove) { void rrRemoveLine(remove); return true; }
  const mode = t.closest<HTMLButtonElement>('[data-ru-seg="rr-mode"] [data-v]');
  if (mode) {
    if (!mode.disabled && mode.getAttribute('aria-pressed') !== 'true') void rrPut(rrParty(), 'mode', mode.dataset.v ?? '');
    return true;
  }
  const book = t.closest<HTMLButtonElement>('[data-ru-seg="rr-book"] [data-v]');
  if (book) {
    if (!book.disabled && book.getAttribute('aria-pressed') !== 'true') void rrPut(rrParty(), 'book', book.dataset.v ?? '');
    return true;
  }
  return false;
}
function rrInput(ev: Event) {
  const t = eventElement(ev), R = ruOpen().reach;
  if (!t) return false;
  if (t instanceof HTMLInputElement && t.matches('.rr-q')) { R.q = t.value.trim(); rrSearchPaint(); return true; }
  if (t instanceof HTMLInputElement && t.matches('.rr-xq')) { ruFind('.rr-xres').innerHTML = rrAddMatches(R.party, t.value.trim()); return true; }
  if (R.edit && t.closest('[data-rr-ed]') && (t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement)) { if (t.name === 'per_day') R.edit.per_day = t.value; if (t.name === 'rule') R.edit.rule = t.value; return true; }
  return false;
}
function rrChange(ev: Event) {
  const cap = eventElement(ev)?.closest<HTMLInputElement>('[data-rr-cap]');
  if (!cap) return false;
  const v = cap.value.trim(), field = cap.dataset.rrCap ?? '';
  // A box with an unsaved number fires change when a redraw removes it (a save finishing, another party picked).
  // Save it for the panel it was in, and after that redraw: drawing the panel again inside it throws.
  const party = cap.closest<HTMLElement>('.rr-panel[data-party]')?.dataset.party || ruOpen().reach.party || '';
  const save = () => { if (RU?.reach) void rrPut(party, field, v === '' ? null : Number(v)); };
  if (RR_DRAWING) setTimeout(save, 0); else save();
  return true;
}
function rrKey(ev: KeyboardEvent) {
  const t = eventElement(ev);
  if (!(t instanceof HTMLInputElement) || !t.matches('.rr-q')) return;
  if (ev.key === 'Enter') { const first = rrFind(ruOpen().reach.q)[0]; if (ruOpen().reach.q && first) { ev.preventDefault(); rrPick(first.party); } }
  else if (ev.key === 'Escape' && t.value) { ev.preventDefault(); ev.stopPropagation(); t.value = ''; ruOpen().reach.q = ''; rrSearchPaint(); }
}
// A setting opens where it is changed; a fixed limit has no editor and no link. Spend limits and task caps are edited on
// Strategy › Budget (a bot's node, or the company's or a group's pool, named by the descriptor's path); access and run
// budgets on the bot's Settings tab, scrolled to their row; mail sending in its own dialog (ruSettingGo).
function ruSettingTarget(s: BotSetting, bot: string) {
  const e = s.edit, slug = encodeURIComponent(bot), botSettings = '#/bot/' + slug + '/settings';
  if (!e) return null;
  if (e.page === 'strategy') {
    const pool = /\/usage\/pools\/groups\/(.+)$/.exec(e.path || '');
    return {href: RD_BUDGET + '/' + (pool ? 'group:' + pool[1] : 'company'), kind: 'budget'};
  }
  if (e.anchor === 'spend' || e.anchor === 'tasks') return {href: RD_BUDGET + '/bot:' + slug, kind: 'budget'};
  if (s.source === 'mail_settings') return {href: botSettings, kind: 'mail'};
  if (e.anchor === 'access') return {href: botSettings, kind: 'access'};
  if (e.anchor === 'turns') return {href: botSettings, kind: 'turns'};
  return {href: e.page === 'settings' ? '#/settings/' + encodeURIComponent(e.anchor || '') : botSettings, kind: ''};
}
function ruSettingLink(s: BotSetting, bot: string) {
  const to = ruSettingTarget(s, bot);
  if (!to) return '<span class="ru-none">—</span>';
  return `<a class="ru-set" href="${esc(to.href)}" data-ru-setting="${to.kind}" data-bot="${esc(bot)}">Setting ›</a>`;
}

function ruDrawExternal(filters: HTMLElement, list: HTMLElement) {
  const all = ruOpen().rules.external, f = ruOpen().filter;
  const from = new Map<string, {name: string; n: number}>();
  for (const r of all) if (r.source) { const was = from.get(r.source.id); from.set(r.source.id, {name: r.source.name, n: (was?.n || 0) + 1}); }
  const sorted = [...from].sort((a, b) => b[1].n - a[1].n || a[1].name.localeCompare(b[1].name));
  const shown = f.moreFrom ? sorted : sorted.slice(0, 5);
  const withDue = all.filter(r => (r.due || []).length).length;
  filters.innerHTML = `${ruSearch()}
    <div class="ru-fh">From</div>${shown.map(([id, s]) => ruFacet('from', id, s.name, s.n, f.from === id)).join('') || '<div class="ru-fnone">—</div>'}
    ${sorted.length > 5 && !f.moreFrom ? `<button type="button" class="ru-f ru-more" data-ru-more>${sorted.length - 5} more</button>` : ''}
    <div class="ru-fh">Followed by</div>${ruScopeFacets(all, 'who', 'who') || '<div class="ru-fnone">—</div>'}
    <div class="ru-fh">Due</div>${ruFacet('due', '1', 'Has a due date', withDue, !!f.due)}`;
  const rows = all.filter(r => (!f.from || r.source?.id === f.from) && ruScopeHas(r.who, f.who) && (!f.due || (r.due || []).length)
    && ruMatches(r.text + ' ' + r.description + ' ' + (r.source?.name || ''), f.q));
  const head = '<div class="ru-row ru-head" role="row"><span>Rule</span><span>From</span><span>Followed by</span><span>Due</span></div>';
  const body = rows.map(r => `<a class="ru-row${ruSel(r.id) ? ' sel' : ''}" role="row" href="${ruHref('external', {rule: r.id})}" data-rule="${esc(r.id)}">
      <span class="ru-txt">${ruTextHTML(r.text, 'inline')}</span><span class="ru-c" data-l="From">${r.source ? `<span class="ru-src ${esc(r.source.kind)}">${esc(r.source.name)}</span>` : '—'}</span>
      <span class="ru-c" data-l="Followed by">${esc(ruScopeWords(r.who))}</span><span class="ru-c" data-l="Due">${ruDueCell(r)}</span></a>`).join('');
  list.innerHTML = `<div class="ru-table ru-ext" role="table">${head}${body}</div>${body ? '' : `<p class="muted ru-pad">${all.length ? 'Nothing matches.' : 'No external rules yet.'}</p>`}`;
}

function ruDrawDue(filters: HTMLElement, list: HTMLElement) {
  const all = ruOpen().due, f = ruOpen().filter;
  const by = (k: string) => all.filter(d => d.status === k).length;
  filters.innerHTML = `<div class="ru-fh">Status</div>${Object.entries(RU_STATUS).map(([k, [label]]) => ruFacet('status', k, label, by(k), f.status === k)).join('')}
    <div class="ru-fh">Routine</div>${ruFacet('routine', 'on', 'Task', all.filter(d => d.task_days != null).length, f.routine === 'on')}${ruFacet('routine', 'none', 'None', all.filter(d => d.task_days == null).length, f.routine === 'none')}`;
  const rows = all.filter(d => (!f.status || d.status === f.status) && (!f.routine || (f.routine === 'on') === (d.task_days != null)));
  const open = ruQuery().get('due');
  const head = '<div class="ru-row ru-head" role="row"><span>Due</span><span>Rule</span><span>Repeats</span><span>Routine</span><span>Owner</span><span>Status</span></div>';
  const body = rows.map(d => `<a class="ru-row${open === d.id ? ' sel' : ''}" role="row" href="${ruHref('due', {due: d.id})}" data-due="${esc(d.id)}">
      <span class="ru-c ru-day">${esc(ruDay(d.due_on))}</span><span class="ru-txt">${ruTextHTML(d.rule.text, 'inline')}</span>
      <span class="ru-c" data-l="Repeats">${esc(ruRepeatWord(d.repeats))}</span>
      <span class="ru-c${d.task_days != null ? '' : ' ru-none'}" data-l="Routine">${esc(ruLead(d.task_days) || '—')}</span>
      <span class="ru-c ru-owner" data-l="Owner">${ruOwnerHTML(d.owner)}</span><span class="ru-c">${ruPill(d.status)}</span></a>`).join('');
  list.innerHTML = `<div class="ru-table ru-duet" role="table">${head}${body}</div>${body ? '' : `<p class="muted ru-pad">${all.length ? 'Nothing matches.' : 'No due dates yet.'}</p>`}`;
}
function ruOwnerHTML(actor: string | undefined) {
  const [kind, id] = String(actor || '').split(/:(.*)/);
  const face = kind === 'bot' ? avatar(id ?? '', 18) : personAvatar((S.people || []).find(p => p.id === id) || {id, name: id}, 18);
  return `${face}<span>${esc(ruActorName(actor))}</span>`;
}

// Suggested: grouped by From, each row the sentence, a Who picker, and Skip / Add. The sentence opens the rule's side
// panel, where its quote is; the row names no document and shows no quote.
function ruDrawSuggested(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  const groups = new Map<string, {name: string; rows: RuleRow[]}>();
  for (const r of ruOpen().suggested) {
    const key = r.source?.id || '', name = r.source?.name || 'Internal';
    let group = groups.get(key);
    if (!group) { group = {name, rows: []}; groups.set(key, group); }
    group.rows.push(r);
  }
  // A suggestion with no Who yet (the Rules Owner may leave it open) is added only once its picker names one; Add all adds the
  // rows that have one and leaves the rest waiting.
  list.innerHTML = [...groups].map(([key, g]) => `<section class="ru-sg" data-group="${esc(key)}"><header><h2>${esc(g.name)}</h2><span class="spacer"></span>
        <button class="primary small" type="button" data-ru-add-all="${esc(key)}" hidden></button></header>
      ${g.rows.map(r => { const who = ruScopeWords(r.who, ''); return `<div class="ru-prop${ruSel(r.id) ? ' sel' : ''}" data-rule="${esc(r.id)}"${who ? ' data-has-who' : ''}><a class="ru-pt" href="${ruHref('suggested', {rule: r.id})}">${ruTextHTML(r.text, 'inline')}</a>
        <select class="ru-pwho" aria-label="Followed by" data-ru-who${r.can_edit ? '' : ' disabled'}><option value="">${esc(who || 'Pick who')}</option>${ruWhoOptions()}</select>
        <span class="ru-pacts">${r.can_edit ? `<button class="ghost small" type="button" data-ru-skip>Skip</button><button class="primary small" type="button" data-ru-accept${who ? '' : ' disabled'}>Add</button>` : ''}</span></div>`; }).join('')}</section>`)
    .join('');
  list.innerHTML += ruStageSuggestedHTML(ruOpen().stageSuggested);
  if (!list.innerHTML) list.innerHTML = '<p class="muted ru-pad">No suggestions.</p>';
  // Add all is the org rules' sections' alone; the stage rules' section has none.
  list.querySelectorAll<HTMLElement>('.ru-sg:not([data-group="stage-rules"])').forEach(ruAddAllSync);
}

// ----------------------------------------------------------------- Pipelines and stages, Wakes, Automations, Built in
// Stage rules are read here and changed from a stage's Rules button (stage-rules.ts), where On starts as that stage.
const ruStageWhere = (r: StageRuleCore) => r.scope === 'company' ? 'Every pipeline'
  : r.scope === 'pipeline' ? `${r.pipeline_name || r.pipeline_id}: every stage` : `${r.pipeline_name || r.pipeline_id}: ${(r.stage_names || r.stages).join(', ')}`;
function ruStageRowHTML(r: StageRule, acts = ''): string {
  return `<div class="ru-row sr-row-ru" data-stage-rule="${esc(r.id)}"><div class="sr-line"><span class="sr-name">${esc(r.name)}</span>`
    + `<span class="sr-cost${r.cost === SR_FREE ? '' : ' sr-cost-jev'}">${esc(r.cost === SR_FREE ? 'Free' : r.cost)}</span>${acts}</div>`
    + `<div class="sr-what muted">${esc(ruStageWhere(r))} · ${esc(srSentence(r))}</div></div>`;
}
/** What a suggested change alters in the rule it replaces: "When: A task enters → The owner changes", one per part. */
function ruStageDiff(old: StageRuleCore, r: StageRuleCore): [string, string, string][] {
  const a = srParts(old), b = srParts(r);
  const parts: [string, string, string][] = [['Name', old.name, r.name], ['On', ruStageWhere(old), ruStageWhere(r)],
    ['When', a.when, b.when], ['If', a.ifs || 'always', b.ifs || 'always'], ['Then', a.act, b.act], ['Says', old.reason || '', r.reason || '']];
  return parts.filter(([, x, y]) => x !== y);
}
// A bot's stage rule, change or retire, for a person to accept or skip. Each says what accepting does: a new rule
// comes into force, a change rewrites the rule it names (what differs is listed), a retire takes that rule out of force.
function ruStageSuggestedHTML(rows: StageRule[]): string {
  if (!rows.length) return '';
  const human = S.me?.role !== 'bot';
  return `<section class="ru-sg" data-group="stage-rules"><header><h2>Stage rules</h2></header>${rows.map(r => {
    const old = r.replaces, kind = !r.suggests ? 'new' : r.retires ? 'retire' : 'change';
    const accept = kind === 'retire' ? 'Retire' : kind === 'change' ? 'Accept change' : 'Accept';
    const acts = human && r.can_edit ? `<span class="ru-pacts"><button class="ghost small" type="button" data-sr-decide="skip">Skip</button>`
      + `<button class="primary small" type="button" data-sr-decide="accept">${accept}</button></span>` : '';
    const title = kind === 'retire' ? `Retire: ${old?.name ?? r.name}` : kind === 'change' ? `Change to ${old?.name ?? r.name}` : `New: ${r.name}`;
    let more = '';
    if (kind === 'retire') more = `<div class="sr-says muted" data-sr-why>${r.retire_why ? `Why: ${esc(r.retire_why)}` : 'No reason given.'}</div>`;
    else if (kind === 'change' && old) {
      const diff = ruStageDiff(old, r);
      more = `<ul class="sr-diff" data-sr-diff>${diff.map(([k, x, y]) => `<li><span class="sr-diff-k">${esc(k)}</span> <s class="muted">${esc(x || '(none)')}</s> → ${esc(y || '(none)')}</li>`).join('')
        || '<li class="muted">Nothing differs from the rule as it is now.</li>'}</ul>`;
    } else if (kind !== 'new') more = '<div class="sr-says muted">The rule it changes is gone; skip it.</div>';
    return `<div class="ru-row sr-row-ru" data-stage-rule="${esc(r.id)}" data-sr-kind="${kind}"><div class="sr-line"><span class="sr-name">${esc(title)}</span>`
      + `<span class="sr-cost${r.cost === SR_FREE ? '' : ' sr-cost-jev'}">${esc(r.cost === SR_FREE ? 'Free' : r.cost)}</span>${acts}</div>`
      + `<div class="sr-what muted">${esc(ruStageWhere(old ?? r))} · ${esc(srSentence(old ?? r))}</div>${more}</div>`;
  }).join('')}</section>`;
}
// By pipeline, then stage: Company first, then each pipeline's whole-pipeline rules and its stages' rules.
function ruDrawStages(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  const rows = ruOpen().stageRules, groups = new Map<string, StageRule[]>();
  for (const r of rows) {
    const key = r.scope === 'company' ? 'Company: every pipeline' : r.scope === 'pipeline' ? `${r.pipeline_name || r.pipeline_id} · Whole pipeline`
      : `${r.pipeline_name || r.pipeline_id} · ${(r.stage_names || r.stages).join(', ')}`;
    groups.set(key, [...(groups.get(key) || []), r]);
  }
  list.innerHTML = [...groups].map(([name, rs]) => `<section class="ru-sg sr-ru-group"><header><h2>${esc(name)} <span class="ru-n tnum">${rs.length}</span></h2></header>`
    + rs.map(r => ruStageRowHTML(r, r.can_edit && r.scope === 'stage'
      ? `<span class="ru-pacts"><button class="ghost small" type="button" data-sr-open-ru="${esc(r.stages[0] ?? '')}" data-sr-pipeline="${esc(r.pipeline_id)}" data-sr-name="${esc(r.stage_names?.[0] ?? '')}">Edit</button></span>` : '')).join('')
    + '</section>').join('') || '<p class="muted ru-pad">No stage rules. Add one from a stage\'s Rules button on its pipeline.</p>';
}
function ruAutosHTML(rows: RuleAuto[], empty: string): string {
  return rows.length ? `<p class="muted ru-pad">Listed here; edited on the Automations page.</p>${rows.map(a => `<div class="ru-row sr-row-ru"><div class="sr-line">`
    + `<a class="sr-name" href="${AUTOMATION_HREF(a.id)}">${esc(a.name)}</a><span class="sr-cost">${a.enabled ? 'On' : 'Off'}</span></div></div>`).join('')}`
    : `<p class="muted ru-pad">${empty}</p>`;
}
function ruDrawWakes(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  list.innerHTML = ruAutosHTML(ruOpen().groups?.wakes || [], 'No What wakes automations.');
}
function ruDrawAutomations(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  list.innerHTML = ruAutosHTML(ruOpen().groups?.automations || [], 'No automations start from a task.');
}
// What Tico does in code: read-only.
function ruDrawBuiltIn(filters: HTMLElement, list: HTMLElement) {
  filters.innerHTML = '';
  const rows = ruOpen().groups?.built_in || [];
  list.innerHTML = `<p class="muted ru-pad">In code, read-only.</p>` + rows.map(b => `<div class="ru-row sr-row-ru sr-builtin" data-built-in><div class="sr-line"><span class="sr-name">${esc(b.name)}</span></div>`
    + `${b.what ? `<div class="sr-what muted">${esc(b.what)}${b.where ? ` · ${esc(b.where)}` : ''}</div>` : ''}</div>`).join('');
}
// Accept or Skip on a suggested stage rule; Edit opens the stage's Rules panel.
async function ruStageClick(ev: MouseEvent): Promise<boolean> {
  const el = eventElement(ev)?.closest<HTMLElement>('[data-sr-decide],[data-sr-open-ru]');
  if (!el) return false;
  if (el.dataset['srOpenRu'] !== undefined) {
    await stageRulesOpen(el.dataset['srPipeline'] ?? '', el.dataset['srOpenRu'] ?? '', el.dataset['srName'] ?? '');
    return true;
  }
  const id = el.closest<HTMLElement>('[data-stage-rule]')?.dataset['stageRule'] ?? '', action = el.dataset['srDecide'] ?? '';
  const r = ruOpen().stageSuggested.find(x => x.id === id);
  if (!r) return true;
  el.setAttribute('disabled', '');
  try {
    await post(`/v2/stage-rules/${encodeURIComponent(id)}/${action}`, {revision: r.revision});
    toast(action === 'skip' ? 'Skipped' : r.retires ? 'Retired' : 'Accepted');
    ruOpen().loaded.clear();
    SR_BOARD.clear();  // the board's Rules counts read again the next time it draws
    if (await ruLoad('suggested')) ruDraw();
  } catch (e) { el.removeAttribute('disabled'); toast(ruError(e)); }
  return true;
}
// The rows of a group that can be added now: the viewer may, and a Who is set.
const ruReady = (group: HTMLElement) => [...group.querySelectorAll<HTMLElement>('.ru-prop')].filter(p => {
  const add = p.querySelector('[data-ru-accept]');
  return add && (p.hasAttribute('data-has-who') || ruFind<HTMLSelectElement>('[data-ru-who]', p).value);
});
function ruAddAllSync(group: HTMLElement) {
  const n = ruReady(group).length, button = ruFind<HTMLButtonElement>('[data-ru-add-all]', group);
  button.hidden = n < 2;
  button.textContent = `Add all ${n}`;
}
// A single Who, as the Suggested picker offers it.
function ruWhoOptions() {
  const opt = (v: string, l: string) => `<option value="${esc(v)}">${esc(l)}</option>`;
  const groups = ruGroups(), bots = ruBots(), people = ruPeople();
  return (settingsIsAdmin() ? opt('everyone', 'Everyone') + opt('outside_editions', 'Outside editions') : '') + opt('all_bots', 'All bots')
    + (groups.length ? `<optgroup label="Departments">${groups.map(g => opt('groups:' + g.id, g.name || g.id)).join('')}</optgroup>` : '')
    + (bots.length ? `<optgroup label="Bots">${bots.map(b => opt('bots:' + b.id, b.name)).join('')}</optgroup>` : '')
    + (people.length ? `<optgroup label="People">${people.map(p => opt('people:' + p.id, p.name)).join('')}</optgroup>` : '');
}
function ruWhoFromOption(v: string) {
  if (!v) return null;
  if (v === 'everyone') return {everyone: true};
  if (v === 'all_bots') return {all_bots: true};
  if (v === 'outside_editions') return {outside_editions: true};
  const [kind, id] = v.split(/:(.*)/);
  return {[kind ?? '']: [id]};
}

// ----------------------------------------------------------------- events
function ruWire() {
  const root = ruOpen().root;
  root.addEventListener('input', ev => {
    if (ruOpen().tab === 'internal' && rrInput(ev)) return;
    const search = eventElement(ev);
    if (search instanceof HTMLInputElement && search.matches('.ru-q')) {
      ruOpen().filter.q = search.value.trim();
      ruDrawKeepSearch();
    }
  });
  root.addEventListener('keydown', ev => { if (ruOpen().tab === 'internal') rrKey(ev); });
  root.addEventListener('change', ev => {
    if (ruOpen().tab === 'internal' && rrChange(ev)) return;
    const pick = eventElement(ev)?.closest<HTMLSelectElement>('.ru-prop [data-ru-who]');
    if (pick) {
      const prop = ruClosest<HTMLElement>(pick, '.ru-prop'), add = prop.querySelector<HTMLButtonElement>('[data-ru-accept]');
      if (add) add.disabled = !prop.hasAttribute('data-has-who') && !pick.value;
      ruAddAllSync(ruClosest<HTMLElement>(prop, '.ru-sg'));
    }
  });
  root.addEventListener('click', ev => {
    const t = eventElement(ev);
    if (!t) return;
    if (ruOpen().tab === 'internal' && !t.closest('#ru-side') && rrClick(ev)) return;
    const facet = t.closest<HTMLElement>('[data-ru-f]');
    if (facet) {
      const key = facet.dataset.ruF as 'who' | 'from' | 'status' | 'routine' | 'due', v = facet.dataset.v ?? '';
      const f = ruOpen().filter;
      if (key === 'due') f.due = !f.due;
      else f[key] = f[key] === v ? '' : v;
      ruDraw(); return;
    }
    if (t.closest('[data-sr-decide],[data-sr-open-ru]')) { void ruStageClick(ev); return; }
    if (t.closest('[data-ru-more]')) { ruOpen().filter.moreFrom = true; ruDraw(); return; }
    if (t.closest('[data-ru-add]')) { ruAddOpen({kind: ruOpen().tab === 'external' ? 'external' : 'internal'}); return; }
    if (t.closest('[data-ru-add-due]')) { location.hash = ruHref('due', {due: 'new'}); return; }
    if (t.closest('[data-ru-add-doc]')) { void ruDocOpen(); return; }
    const prop = t.closest<HTMLElement>('.ru-prop');
    if (prop && t.closest('[data-ru-accept]')) { void ruAccept([prop]); return; }
    if (prop && t.closest('[data-ru-skip]')) { void ruSkip(prop); return; }
    const all = t.closest('[data-ru-add-all]');
    if (all) { void ruAccept(ruReady(ruClosest<HTMLElement>(all, '.ru-sg'))); return; }
    const set = t.closest<HTMLElement>('[data-ru-setting]');
    if (set) ruSettingGo(set, ev);
  });
}
// After a bot setting's link lands on the bot's Settings tab: its row in view, or (mail) its dialog open.
function ruSettingGo(link: HTMLElement, ev: MouseEvent) {
  const kind = link.dataset.ruSetting ?? '', slug = link.dataset.bot ?? '';
  if (ev.metaKey || ev.ctrlKey || !['access', 'turns', 'mail'].includes(kind)) return;
  const sel = kind === 'access' ? '#bot-access-card' : kind === 'turns' ? `[data-bot-budget="${CSS.escape(slug)}"]` : '';
  const started = Date.now();
  // The Settings tab draws after the route; look for the row until it shows (or give up after a few seconds).
  const look = () => {
    if (!location.hash.startsWith('#/bot/' + encodeURIComponent(slug) + '/settings')) return;
    if (kind === 'mail') { if (typeof settingsEditMail === 'function' && $('#bot-editor .sb-row')) { void settingsEditMail(slug); return; } }
    else { const el = $(sel) as HTMLElement | null; if (el) { (el.closest('.sb-row,section') || el).scrollIntoView({block: 'center'}); return; } }
    if (Date.now() - started < 5000) setTimeout(look, 150);
  };
  setTimeout(look, 150);
}
async function ruAccept(props: HTMLElement[]) {
  const written: string[] = [];
  for (const prop of props) {
    const r = ruOpen().suggested.find(x => x.id === prop.dataset.rule);
    if (!r) continue;
    const who = ruWhoFromOption(ruFind<HTMLSelectElement>('[data-ru-who]', prop).value);
    prop.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = true; });
    written.push(r.id);
    try { await post('/v2/rules/' + encodeURIComponent(r.id) + '/accept', {version: r.version, ...(who ? {who} : {})}); }
    catch (e) { toast(ruError(e), true); break; }
  }
  await ruReload(written);
}
async function ruSkip(prop: HTMLElement) {
  const r = ruOpen().suggested.find(x => x.id === prop.dataset.rule);
  if (!r) return;
  try { await post('/v2/rules/' + encodeURIComponent(r.id) + '/skip', {version: r.version}); }
  catch (e) { toast(ruError(e), true); }
  await ruReload([r.id]);
}
// After a write: the open tab reads again; the other tabs read again when shown. `written` names rules
// a list row wrote: when the open panel shows one of them, it reads that rule again, and on Suggested it closes once
// the rule is no longer a suggestion (a row's Add, Skip or Add all leaves the panel's copy stale otherwise).
async function ruReload(written: string[] = []) {
  if (!RU) return;
  const tab = ruTab();
  RU.loaded = new Set([tab]);
  if (!await ruLoad(tab)) return;
  const open = RU.tab === 'due' ? '' : ruQuery().get('rule'), side = ruSide();
  if (open && written.includes(open)) {
    if (side) side.dataset.open = '';
    if (RU.tab === 'suggested' && !RU.suggested.some(r => r.id === open)) { location.hash = ruBack(); return; }
  }
  ruDraw(); ruDrawPanel();
}
/** The Rules side panel, or null when it is not drawn. */
function ruSide(): HTMLElement | null { return $('#ru-side') as HTMLElement | null; }
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape' || !RU || !document.body.contains(RU.root) || $('dialog[open]')) return;
  const side = ruSide();
  if (side && !side.hidden) { ev.preventDefault(); location.hash = ruBack(); }
});

// ----------------------------------------------------------------- the scope editor (Who, With)
// Segments pick what "+ add" offers; the chips are everything chosen. Everyone (Who) is the owner's and admins' alone;
// Anyone (With) clears it. Outside editions (Who, the owner's and admins' alone) stands apart from who else follows the
// rule: it adds the Company page's editions, and Everyone doesn't include them. The state is
// {everyone, all_bots, outside_editions, groups, bots, people, seg}, ids only.
function ruScopeEditor(host: HTMLElement, scope: RuleScope | ScopeIds | null | undefined, {flags, disabled = false, onChange = () => {}}: {flags?: boolean; disabled?: boolean; onChange?: () => void}) {
  const ids = ruScopeIds(scope);
  // The ids held by the segment being added to; none for Everyone or Anyone, whose list is never read.
  const listOf = (seg: ScopeSeg): string[] => seg === 'groups' || seg === 'bots' || seg === 'people' ? s[seg] : [];
  const s: ScopeEdit = {...ids, seg: ids.everyone ? 'everyone' : ids.all_bots || ids.bots.length ? 'bots' : ids.groups.length ? 'groups' : ids.people.length ? 'people' : flags ? 'groups' : 'anyone'};
  const draw = () => {
    const segs = flags ? [['everyone', 'Everyone'], ['groups', 'Departments'], ['bots', 'Bots'], ['people', 'People'],
      ...(settingsIsAdmin() || s.outside_editions ? [['outside', 'Outside editions']] : [])]
      : [['anyone', 'Anyone'], ['groups', 'Departments'], ['bots', 'Bots'], ['people', 'People']];
    const chips = [
      ...(s.all_bots ? [['all_bots', '', 'All bots']] : []),
      ...(s.outside_editions ? [['outside_editions', '', 'Outside editions']] : []),
      ...s.groups.map(id => ['groups', id, ruGroupName(id)]), ...s.bots.map(id => ['bots', id, botDisplayName(id)]),
      ...s.people.map(id => ['people', id, ruPersonName(id)])];
    const pools: Partial<Record<ScopeSeg, {id: string; name: string}[]>> = {groups: ruGroups().map(g => ({id: g.id, name: g.name || g.id})), bots: ruBots(), people: ruPeople()};
    const pool = pools[s.seg] || [];
    const options = [...(s.seg === 'bots' && flags && !s.all_bots ? [['all_bots', 'All bots']] : []),
      ...pool.filter(x => !listOf(s.seg).includes(x.id)).map(x => [x.id, x.name])];
    const adding = !disabled && !s.everyone && s.seg !== 'anyone' && options.length;
    host.innerHTML = `<div class="ru-seg" role="group">${segs.map(([k, label]) => {
        const on = k === 'everyone' ? s.everyone : k === 'outside' ? s.outside_editions : k === 'anyone' ? s.seg === 'anyone' : !s.everyone && s.seg === k;
        const off = disabled || ((k === 'everyone' || k === 'outside') && !settingsIsAdmin());
        return `<button type="button" data-seg="${k}" aria-pressed="${on}"${off ? ' disabled' : ''}>${label}</button>`; }).join('')}</div>
      ${(s.everyone && !s.outside_editions) || (s.seg === 'anyone' && !chips.length) ? '' : `<div class="ru-chips">${chips.map(([k, id, label]) =>
        `<span class="ru-chip">${esc(label)}${disabled ? '' : `<button type="button" data-unchip="${k}" data-id="${esc(id)}" aria-label="Remove ${esc(label)}">✕</button>`}</span>`).join('')}
        ${adding ? `<select class="ru-addsel" aria-label="Add"><option value="">+ add</option>${options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select>` : ''}</div>`}`;
  };
  host.onclick = ev => {
    const seg = eventElement(ev)?.closest<HTMLButtonElement>('[data-seg]');
    if (seg && !seg.disabled) {
      const k = seg.dataset.seg as SegKey;
      if (k === 'outside') { s.outside_editions = !s.outside_editions; draw(); onChange(); return; }
      if (k === 'everyone') Object.assign(s, {everyone: true, all_bots: false, groups: [], bots: [], people: []});
      else if (k === 'anyone') Object.assign(s, {groups: [], bots: [], people: []});
      else s.everyone = false;
      s.seg = k; draw(); onChange(); return;
    }
    const un = eventElement(ev)?.closest<HTMLElement>('[data-unchip]');
    if (un) {
      const k = un.dataset.unchip as 'all_bots' | 'outside_editions' | ScopeKind;
      if (k === 'all_bots') s.all_bots = false;
      else if (k === 'outside_editions') s.outside_editions = false;
      else s[k] = s[k].filter(x => x !== un.dataset.id);
      draw(); onChange();
    }
  };
  host.onchange = ev => {
    const sel = eventElement(ev);
    if (!(sel instanceof HTMLSelectElement) || !sel.matches('.ru-addsel') || !sel.value) return;
    if (sel.value === 'all_bots') s.all_bots = true;
    else listOf(s.seg).push(sel.value);
    draw(); onChange();
    host.querySelector<HTMLSelectElement>('.ru-addsel')?.focus();
  };
  draw();
  return {value: () => flags ? {everyone: s.everyone, all_bots: s.all_bots, groups: s.groups, bots: s.bots, people: s.people, ...(s.outside_editions ? {outside_editions: true} : {})}
    : {groups: s.groups, bots: s.bots, people: s.people}};
}
const ruSegHTML = (name: string, options: [string, string][], value: string | null | undefined, disabled?: boolean) => `<div class="ru-seg" role="group" data-ru-seg="${name}">${options.map(([k, label]) =>
  `<button type="button" data-v="${k}" aria-pressed="${k === value}"${disabled ? ' disabled' : ''}>${label}</button>`).join('')}</div>`;
function ruSegWire(host: HTMLElement, onChange: (name: string, value: string) => void = () => {}) {
  host.addEventListener('click', ev => {
    const b = eventElement(ev)?.closest<HTMLButtonElement>('[data-ru-seg] [data-v]');
    const group = b?.parentElement;
    if (!b || !group || b.disabled) return;
    group.querySelectorAll<HTMLElement>('[data-v]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    onChange(group.dataset.ruSeg ?? '', b.dataset.v ?? '');
  });
}
const ruSegValue = (host: HTMLElement, name: string) => host.querySelector<HTMLElement>(`[data-ru-seg="${name}"] [aria-pressed="true"]`)?.dataset.v || '';
const ruSourceOptions = (sources: {id: string; name: string}[], value: string | null | undefined) => sources.map(s => `<option value="${esc(s.id)}"${s.id === value ? ' selected' : ''}>${esc(s.name)}</option>`).join('');

// ----------------------------------------------------------------- the side panels
function ruDrawPanel() {
  const side = ruSide(), q = ruQuery();
  if (!side) return;
  const ruleId = ruOpen().tab === 'due' ? '' : q.get('rule'), dueId = ruOpen().tab === 'due' ? q.get('due') : '';
  const want = ruleId ? 'rule:' + ruleId : dueId ? 'due:' + dueId + ':' + (q.get('rule') || '') : '';
  ruOpen().root.classList.toggle('ru-open', !!want);
  if (!want) { side.hidden = true; side.innerHTML = ''; side.dataset.open = ''; return; }
  side.hidden = false;
  if (side.dataset.open === want) return;
  side.dataset.open = want;
  // A note ("Saved", "Changed elsewhere") left for this panel shows in its status line, inside the sheet on a phone.
  const key = ruleId ? 'rule:' + ruleId : 'due:' + dueId, pending = ruOpen().note, note = pending?.key === key ? pending.text : '';
  ruOpen().note = null;
  if (ruleId) void ruRulePanel(side, ruleId, note);
  else void ruDuePanel(side, String(dueId), q.get('rule') || '', note);
}
// Reload, then draw `key`'s panel again with `text` only if the address still names it.
async function ruRedraw(side: HTMLElement, key: string, text: string) {
  ruOpen().note = {key, text};
  side.dataset.open = '';
  await ruReload();
}
// The cited section and quote, where the viewer may read its file (`hidden` otherwise: nothing shows). The section
// opens that exact file version in the viewer (ui/app/viewer.ts reads /api/v2/files/<id>?v=<n> links).
function ruQuoteHTML(cite: RuleRow['cite']) {
  if (!cite || cite.hidden || !cite.quote) return '';
  const label = esc(cite.section || 'Document');
  const src = cite.file_id
    ? `<a class="ru-qsrc" href="${API}/v2/files/${encodeURIComponent(cite.file_id)}${cite.version ? '?v=' + encodeURIComponent(cite.version) : ''}" data-name="${label}">${label}</a>`
    : `<b>${label}</b>`;
  return `<blockquote class="ru-quote">${src} “${esc(cite.quote)}”</blockquote>`;
}
// Where closing a rule's panel goes: the tab, and on Internal the party it was opened from.
const ruBack = () => ruHref(ruOpen().tab, ruOpen().tab === 'internal' ? {party: ruQuery().get('party') || ''} : {});
const ruPanelHead = (title: string, close: string) => `<div class="ru-side-h"><h2>${esc(title)}</h2><span class="spacer"></span><a class="ru-x" href="${close}" aria-label="Close">✕</a></div>`;


// If broken: the three choices, one line on what the chosen one does, and under Ask first the reviewer (no pick: the
// manager above the one who broke it).
function ruBrokenHTML(value: IfBroken | null | undefined, reviewer: string | null | undefined, disabled: boolean) {
  const dis = disabled ? ' disabled' : '';
  return `${ruSegHTML('if_broken', RU_BROKEN, value, disabled)}<p class="ru-hint" data-ru-hint>${esc(RU_BROKEN_HINT[value ?? ''] || '')}</p>
    <label class="ru-reviewer"${value === 'escalate' ? '' : ' hidden'}><span class="ru-lbl">Reviewer <span class="ru-opt">(optional)</span></span>
      <select name="reviewer"${dis}><option value="">Manager</option>${ruPeople().map(p => `<option value="${esc(p.id)}"${reviewer === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>`;
}
function ruBrokenSync(form: HTMLElement, value: string) {
  ruFind('.ru-reviewer', form).hidden = value !== 'escalate';
  ruFind('[data-ru-hint]', form).textContent = RU_BROKEN_HINT[value] || '';
}

async function ruRulePanel(side: HTMLElement, id: string, note = '') {
  const want = side.dataset.open;
  side.innerHTML = ruPanelHead('Rule', ruBack()) + skelRows(4);
  let r: RuleRow;
  try { r = await get<RuleRow>('/v2/rules/' + encodeURIComponent(id), {fresh: true}); }
  catch (e) { if (side.dataset.open === want) side.innerHTML = ruPanelHead('Rule', ruBack()) + `<p class="err ru-pad">${esc(errorMessage(e, ''))}</p>`; return; }
  if (side.dataset.open !== want) return;
  const edit = !!r.can_edit, dis = edit ? '' : ' disabled', internal = r.kind === 'internal';
  if (!internal && !ruOpen().sources.length) ruOpen().sources = await ruAll<RuleSource>('/v2/rule-sources').catch(() => []);
  const dues = ruDueSorted(r);
  const quote = ruQuoteHTML(r.cite);
  const suggested = r.status === 'suggested';
  side.innerHTML = `${ruPanelHead('Rule', ruBack())}
    <form class="ru-form" data-rule="${esc(r.id)}">
      <div class="ru-fl"><span class="ru-lbl">Rule</span>
        <div class="ru-read"${edit ? ' tabindex="0" title="Edit"' : ''}>${ruTextHTML(r.text)}</div>
        <textarea name="text" rows="2" maxlength="1000" required aria-label="Rule" hidden${dis}>${esc(r.text)}</textarea>
        <p class="ru-follow">${esc(ruFollowSentence(r.who, internal ? r.with : null))}</p></div>
      <label class="ru-fl"><span class="ru-lbl">Description</span><textarea name="description" rows="2" maxlength="2000"${dis}>${esc(r.description || '')}</textarea></label>
      <div class="ru-fl"><span class="ru-lbl">Followed by</span><div data-scope="who"></div></div>
      ${internal ? '<div class="ru-fl"><span class="ru-lbl">With <span class="ru-opt">(optional)</span></span><div data-scope="with"></div></div>'
        : `<label class="ru-fl"><span class="ru-lbl">From</span><select name="source"${dis}>${ruSourceOptions(ruOpen().sources, r.source?.id)}</select></label>`}
      <div class="ru-fl"><span class="ru-lbl">If broken</span>${ruBrokenHTML(r.if_broken, r.reviewer?.id, !edit)}</div>
      <div class="ru-fl"><span class="ru-lbl">Due</span>${dues.map(due =>
          `<a class="ru-duebox" href="${ruHref('due', {due: due.id})}" data-due="${esc(due.id)}"><span>${esc(ruDay(due.due_on, true))}${due.repeats && due.repeats !== 'once' ? ' · ' + esc(ruRepeatWord(due.repeats).toLowerCase()) : ''}${due.task_days != null ? ' · task ' + esc(ruLead(due.task_days)) : ''}</span>${ruPill(due.status)}<span class="spacer"></span><span class="muted">›</span></a>`).join('')}
        ${r.due_more ? `<a class="ru-adddue" href="${ruHref('due')}">${r.due_more} more</a>` : ''}
        ${edit ? `<a class="ru-adddue" href="${ruHref('due', {due: 'new', rule: r.id})}">+ Add due date</a>` : dues.length ? '' : '<span class="ru-none">—</span>'}</div>
      ${quote}
      <p class="ru-status" role="status">${esc(note)}</p>
      ${!edit ? '' : suggested
        ? `<div class="ru-foot"><button class="ghost" type="button" data-ru-skip-one>Skip</button><span class="spacer"></span><button class="ghost" type="submit">Save</button>
            <button class="primary" type="button" data-ru-accept-one>Add</button></div>`
        : '<div class="ru-foot"><button class="ghost" type="button" data-ru-retire>Retire</button><span class="spacer"></span><button class="primary" type="submit">Save</button></div>'}
    </form>`;
  const form = ruFind<HTMLFormElement>('form', side), status = ruFind('.ru-status', form), controls = ruControls<RuleEditControls>(form);
  // A suggestion is added only with a Who: Add waits until the editor names one.
  const addOne = form.querySelector<HTMLButtonElement>('[data-ru-accept-one]');
  const whoSet = (scope: ScopeIds) => scope.everyone || scope.all_bots || scope.outside_editions || [scope.groups, scope.bots, scope.people].some(list => list?.length);
  // The sentence under the rule follows the Who and With being edited.
  const follow = () => { ruFind('.ru-follow', form).textContent = ruFollowSentence(who.value(), withEd?.value()); };
  const who = ruScopeEditor(ruFind('[data-scope="who"]', form), r.who, {flags: true, disabled: !edit,
    onChange: () => { if (addOne) addOne.disabled = !whoSet(who.value()); follow(); }});
  if (addOne) addOne.disabled = !whoSet(who.value());
  const withEd = internal ? ruScopeEditor(ruFind('[data-scope="with"]', form), r.with, {flags: false, disabled: !edit, onChange: follow}) : null;
  ruSegWire(form, (name, v) => { if (name === 'if_broken') ruBrokenSync(form, v); });
  // The rule reads with its mentions as chips; an editor clicks it (or presses Enter on it) to edit the words.
  const read = ruFind('.ru-read', form);
  const write = () => {
    read.hidden = true;
    const box = controls.text;
    box.hidden = false; box.focus(); box.setSelectionRange(box.value.length, box.value.length);
  };
  if (edit) {
    read.addEventListener('click', ev => { const t = eventElement(ev); if (t && !t.closest('a')) write(); });
    read.addEventListener('keydown', ev => { const t = eventElement(ev); if (ev.key === 'Enter' && t && !t.closest('a')) { ev.preventDefault(); write(); } });
  }
  // The fields changed in the panel, as an edit sends them (`version` alone when nothing changed).
  const changes = () => {
    const body: Record<string, unknown> = {version: r.version};
    const text = controls.text.value.trim(), description = controls.description.value.trim();
    if (text !== r.text) body.text = text;
    if (description !== (r.description || '')) body.description = description;
    if (!ruSameScope(who.value(), r.who)) body.who = who.value();
    if (withEd && !ruSameScope(withEd.value(), r.with)) body.with = withEd.value();
    if (!internal && controls.source.value !== r.source?.id) body.source_id = controls.source.value;
    const broken = ruSegValue(form, 'if_broken');
    if (broken !== r.if_broken) body.if_broken = broken;
    // The reviewer shows only under Ask first; a stored one stays unless the person picks another.
    const reviewer = controls.reviewer.value;
    if (broken === 'escalate' && reviewer !== (r.reviewer?.id || '')) body.reviewer = reviewer;
    return body;
  };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = changes();
    if (Object.keys(body).length === 1) { status.textContent = 'Saved'; return; }
    const button = ruFind<HTMLButtonElement>('[type=submit]', form); button.disabled = true; status.textContent = 'Saving…';
    try {
      await post('/v2/rules/' + encodeURIComponent(r.id), body);
      if (side.dataset.open === want) await ruRedraw(side, 'rule:' + r.id, 'Saved');
      else await ruReload();
    } catch (e) {
      button.disabled = false;
      if (errorStatus(e) === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  };
  // A suggestion: Add saves what was changed here, then accepts it; Skip sets it aside.
  addOne?.addEventListener('click', async () => {
    addOne.disabled = true; status.textContent = 'Saving…';
    try {
      const body = changes();
      let version = r.version;
      if (Object.keys(body).length > 1) version = (await post('/v2/rules/' + encodeURIComponent(r.id), body)).version;
      await post('/v2/rules/' + encodeURIComponent(r.id) + '/accept', {version});
      location.hash = ruBack();
      await ruReload();
      toast('Added');
    } catch (e) {
      addOne.disabled = false;
      if (errorStatus(e) === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  });
  form.querySelector('[data-ru-skip-one]')?.addEventListener('click', async () => {
    try {
      await post('/v2/rules/' + encodeURIComponent(r.id) + '/skip', {version: r.version});
      location.hash = ruBack();
      await ruReload();
    } catch (e) { status.textContent = ruError(e); }
  });
  form.querySelector('[data-ru-retire]')?.addEventListener('click', async () => {
    if (!(await askConfirm('Retire this rule?'))) return;
    try {
      const out: {due?: RuleDue[]} = await post('/v2/rules/' + encodeURIComponent(r.id) + '/retire', {version: r.version});
      const dues = (out.due || []).filter(d => !d.retired_at);
      if (dues.length && (await askConfirm(dues.length === 1 ? 'Remove its due date too?' : `Remove its ${dues.length} due dates too?`)))
        for (const d of dues) await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/remove', {});
      location.hash = ruBack();
      await ruReload();
      toast('Retired');
    } catch (e) {
      if (errorStatus(e) === 409 && side.dataset.open === want) { await ruRedraw(side, 'rule:' + r.id, 'Changed elsewhere'); return; }
      status.textContent = ruError(e);
    }
  });
}

// One due date, or a new one (`id` "new"; `ruleId` when it comes from a rule's panel, else a rule is picked).
async function ruDuePanel(side: HTMLElement, id: string, ruleId: string, note = '') {
  const want = side.dataset.open, close = ruHref('due');
  const fresh = id === 'new';
  // An existing one: GET /rule-due/<id> holds its history (edits included) and what the viewer may do.
  let d: RuleDue | null = null;
  if (!fresh) {
    const gone = () => { side.innerHTML = ruPanelHead('Due date', close) + '<p class="muted ru-pad">This due date is gone.</p>'; };
    side.innerHTML = ruPanelHead('Due date', close) + skelRows(3);
    try { d = await get<RuleDue>('/v2/rule-due/' + encodeURIComponent(id), {fresh: true}); }
    catch (e) {
      if (side.dataset.open !== want) return;
      if (errorStatus(e) === 404) gone(); else side.innerHTML = ruPanelHead('Due date', close) + `<p class="err ru-pad">${esc(errorMessage(e, ''))}</p>`;
      return;
    }
    if (side.dataset.open !== want) return;
    // An edit ended this row and started another: open the live one.
    if (d.ended_on && d.replaced_by) { location.replace(ruHref('due', {due: d.replaced_by})); return; }
    if (d.retired_at || d.ended_on) { gone(); return; }
  }
  let rules: RuleRow[] = [];
  if (fresh && !ruleId) {
    side.innerHTML = ruPanelHead('Add due date', close) + skelRows(3);
    rules = (await ruAll('/v2/rules?status=active').catch(() => [])).filter(r => r.can_edit);
    if (side.dataset.open !== want) return;
  }
  let rule: RuleRow | null = null;
  if (fresh && ruleId) rule = await get<RuleRow>('/v2/rules/' + encodeURIComponent(ruleId)).catch(() => null);
  if (side.dataset.open !== want) return;
  const edit = fresh || !!d?.can_edit, dis = edit ? '' : ' disabled';
  const opt = (v: string, l: string, sel: string | null | undefined) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  const owner = d?.owner || '';
  const owners = `<optgroup label="Bots">${ruBots().map(b => opt('bot:' + b.id, b.name, owner)).join('')}</optgroup>
    <optgroup label="People">${ruPeople().map(p => opt('human:' + p.id, p.name, owner || myActor())).join('')}</optgroup>`;
  // Routine: none, or a task opened for the owner N days before each due day (an automation, linked by routine_id).
  const lead = d ? d.task_days ?? null : null;
  const marks = d ? (d.chain || []).flatMap(link => link.satisfied || []).sort((a, b) => b.due_on.localeCompare(a.due_on)) : [];
  const history = d ? [
    ...(d.status !== 'satisfied' ? [`<div class="ru-h"><span>${esc(ruDay(d.due_on, true))}</span><span class="spacer"></span>${ruPill(d.status)}</div>`] : []),
    ...marks.map(m => `<div class="ru-h ru-mark"><span>${esc(ruDay(m.due_on, true))}</span><span class="muted">${m.via === 'routine'
      ? 'task closed ' + esc(ruDay(m.at)) : 'marked by ' + esc(ruActorName(m.satisfied_by))}</span><span class="spacer"></span>${ruPill('satisfied')}</div>`)].join('') : '';
  const openDays = d?.open_days || [];
  const title = fresh ? 'Add due date' : d?.rule.text ?? '';
  side.innerHTML = `${ruPanelHead(title, close)}
    <form class="ru-form">
      ${fresh ? `<label class="ru-fl"><span class="ru-lbl">Rule</span>${rule ? `<span class="ru-ruletext">${esc(rule.text)}</span>`
        : `<select name="rule" required><option value="">Pick a rule</option>${rules.map(r => opt(r.id, r.text, '')).join('')}</select>`}</label>` : ''}
      <div class="ru-two">
        <label class="ru-fl"><span class="ru-lbl">Due</span><input type="date" name="due" required value="${esc(d?.due_on || '')}"${dis}></label>
        <label class="ru-fl"><span class="ru-lbl">Repeats</span><select name="repeats"${dis}>${RU_REPEATS.map(([k, l]) => opt(k, l, d?.repeats || 'yearly')).join('')}</select></label>
        <label class="ru-fl"><span class="ru-lbl">Owner</span><select name="owner" required${dis}>${owners}</select></label>
        <div class="ru-fl"><span class="ru-lbl">Routine</span>
          <select name="routine" aria-label="Routine"${dis}>${opt('', 'None', lead == null ? '' : 'task')}${opt('task', 'Task', lead == null ? '' : 'task')}</select>
          <span class="ru-lead"${lead == null ? ' hidden' : ''}><input type="number" name="task_days" min="0" max="366" step="1" required value="${lead ?? 14}" aria-label="Days before"${lead == null || !edit ? ' disabled' : ''}><span>days before</span></span></div>
      </div>
      ${d?.routine_id && lead != null ? `<a class="ru-routine" href="#/automations/${encodeURIComponent(d.routine_id)}">Open routine ›</a>` : ''}
      ${d ? `<div class="ru-stat"><span class="ru-lbl">Status</span>${ruPill(d.status)}<span class="spacer"></span>
        ${d.can_satisfy && d.status !== 'satisfied' ? '<button class="ghost small" type="button" data-ru-satisfy>Mark satisfied</button>' : ''}</div>
        <div class="ru-satisfy-on" hidden><select name="due_on" aria-label="Which due date">${openDays.map(day => opt(day, ruDay(day, true), d?.due_on)).join('')}</select><button class="ghost small" type="button" data-ru-satisfy-on>Mark</button></div>
        <div class="ru-fl"><span class="ru-lbl">History</span><div class="ru-hist">${history || '<span class="ru-none">—</span>'}</div></div>` : ''}
      <p class="ru-status" role="status">${esc(note)}</p>
      ${edit ? `<div class="ru-foot">${fresh ? '' : '<button class="ghost" type="button" data-ru-remove>Remove</button>'}<span class="spacer"></span><button class="primary" type="submit">Save</button></div>` : ''}
    </form>`;
  const form = ruFind<HTMLFormElement>('form', side), status = ruFind('.ru-status', form), fields = ruControls<DueControls>(form);
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = fields, button = ruFind<HTMLButtonElement>('[type=submit]', form);
    button.disabled = true; status.textContent = 'Saving…';
    try {
      let saved: RuleDue;
      if (fresh) {
        const target = rule?.id || f.rule.value;
        const body: Record<string, unknown> = {first_due: f.due.value, repeats: f.repeats.value, owner: f.owner.value};
        if (f.routine.value === 'task') body.task_days = Number(f.task_days.value);
        saved = await post('/v2/rules/' + encodeURIComponent(target) + '/due', body);
      } else {
        // A new date or repeat starts again from the date shown (the next one due), not the first date ever set, so
        // occurrences already satisfied stay behind. An owner change alone keeps the anchor.
        if (!d) throw new Error('This due date is gone.');
        const body: Record<string, unknown> = {};
        if (f.due.value !== d.due_on || f.repeats.value !== d.repeats) { body.first_due = f.due.value; body.repeats = f.repeats.value; }
        if (f.owner.value !== d.owner) body.owner = f.owner.value;
        const days = f.routine.value === 'task' ? Number(f.task_days.value) : null;
        if (days !== lead) body.task_days = days;
        saved = Object.keys(body).length ? await post('/v2/rule-due/' + encodeURIComponent(d.id), body) : d;
      }
      ruOpen().loaded.delete('due');
      if (ruQuery().get('due') !== saved.id) { ruOpen().note = {key: 'due:' + saved.id, text: 'Saved'}; location.hash = ruHref('due', {due: saved.id}); }
      else await ruRedraw(side, 'due:' + saved.id, 'Saved');
    } catch (e) { button.disabled = false; status.textContent = ruError(e); }
  };
  fields.routine.onchange = () => {
    const on = fields.routine.value === 'task', box = ruFind('.ru-lead', form);
    box.hidden = !on; fields.task_days.disabled = !on;
  };
  const satisfy = async (dueOn: string) => {
    status.textContent = 'Saving…';
    try {
      if (!d) throw new Error('This due date is gone.');
      await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/satisfied', dueOn ? {due_on: dueOn} : {});
      await ruRedraw(side, 'due:' + d.id, 'Saved');
    } catch (e) { status.textContent = ruError(e); }
  };
  // More than one open occurrence: the server needs to know which one.
  form.querySelector('[data-ru-satisfy]')?.addEventListener('click', () => {
    if (openDays.length > 1) { ruFind('.ru-satisfy-on', form).hidden = false; return; }
    void satisfy('');
  });
  form.querySelector('[data-ru-satisfy-on]')?.addEventListener('click', () => void satisfy(fields.due_on.value));
  form.querySelector('[data-ru-remove]')?.addEventListener('click', async () => {
    if (!(await askConfirm('Remove this due date?'))) return;
    try {
      if (!d) throw new Error('This due date is gone.');
      await post('/v2/rule-due/' + encodeURIComponent(d.id) + '/remove', {});
      location.hash = ruHref('due');
      await ruReload();
    } catch (e) { status.textContent = ruError(e); }
  });
}

// ----------------------------------------------------------------- Add rule
// Internal or External first; Internal asks Rule, Who (Followed by) and With, External asks Rule, Who and From (an
// existing one, or a new name with its kind); then If broken. A person who can't manage what they wrote adds a suggestion, which the server decides.
async function ruAddOpen({kind = 'internal', who = null, after = null}: {kind?: RuleKind; who?: RuleScope | ScopeIds | null; after?: ((made: RuleRow) => unknown) | null} = {}) {
  let dlg = document.getElementById('ru-add') as HTMLDialogElement | null;
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'ru-add'; dlg.className = 'ru-modal'; dlg.setAttribute('aria-label', 'Add rule');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg?.close(); });
    document.body.appendChild(dlg);
  }
  const sources = await ruAll<RuleSource>('/v2/rule-sources').catch(() => []);
  // Opened for one party (a bot's page, a Reach panel), the rule is for that party: just the text, the rest behind More.
  const quick = !!who && Object.values(who).some(v => Array.isArray(v) ? v.length : v);
  dlg.innerHTML =`<form class="ru-form" method="dialog">
      <div class="ru-side-h"><h2>Add rule</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-ru-close aria-label="Close">✕</button></div>
      ${quick ? '' : ruSegHTML('kind', [['internal', 'Internal'], ['external', 'External']], kind)}
      <label class="ru-fl"><span class="ru-lbl">Rule</span><textarea name="text" rows="2" maxlength="1000" required></textarea></label>
      ${quick ? '<button type="button" class="ru-adv-t" data-ru-adv aria-expanded="false">More ›</button>' : ''}
      <div class="ru-adv"${quick ? ' hidden' : ''}>
      ${quick ? ruSegHTML('kind', [['internal', 'Internal'], ['external', 'External']], kind) : ''}
      <div class="ru-fl"><span class="ru-lbl">Followed by</span><div data-scope="who"></div></div>
      <div class="ru-fl" data-for="internal"><span class="ru-lbl">With <span class="ru-opt">(optional)</span></span><div data-scope="with"></div></div>
      <div class="ru-fl" data-for="external"><span class="ru-lbl">From</span>
        <select name="source"><option value="">Pick one</option>${ruSourceOptions(sources, '')}<option value="new">New…</option></select>
        <div class="ru-newsrc" hidden><input name="source_name" maxlength="200" placeholder="Name" aria-label="Name">
          <select name="source_kind" aria-label="Kind">${RU_KINDS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></div></div>
      <div class="ru-fl"><span class="ru-lbl">If broken</span>${ruBrokenHTML('block', '', false)}</div>
      </div>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-ru-close>Cancel</button><button class="primary" type="submit">Save</button></div>
    </form>`;
  const form = ruFind<HTMLFormElement>('form', dlg), status = ruFind('.ru-status', form);
  const adv = form.querySelector<HTMLButtonElement>('[data-ru-adv]');
  if (adv) adv.onclick = () => { adv.hidden = true; ruFind('.ru-adv', form).hidden = false; };
  const whoEd = ruScopeEditor(ruFind('[data-scope="who"]', form), who || {}, {flags: true});
  const withEd = ruScopeEditor(ruFind('[data-scope="with"]', form), {}, {flags: false});
  const show = (k: string) => form.querySelectorAll<HTMLElement>('[data-for]').forEach(el => { el.hidden = el.dataset.for !== k; });
  show(kind);
  ruSegWire(form, (name, v) => { if (name === 'kind') show(v); if (name === 'if_broken') ruBrokenSync(form, v); });
  const controls = ruControls<AddRuleControls>(form);
  controls.source.onchange = () => { ruFind('.ru-newsrc', form).hidden = controls.source.value !== 'new'; };
  form.querySelectorAll<HTMLButtonElement>('[data-ru-close]').forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const k = ruSegValue(form, 'kind'), f = controls, button = ruFind<HTMLButtonElement>('[type=submit]', form);
    const body: Record<string, unknown> = {kind: k, text: f.text.value.trim(), who: whoEd.value(), if_broken: ruSegValue(form, 'if_broken')};
    if (k === 'internal') body.with = withEd.value();
    if (body.if_broken === 'escalate' && f.reviewer.value) body.reviewer = f.reviewer.value;
    button.disabled = true; status.textContent = 'Saving…';
    try {
      if (k === 'external') {
        if (f.source.value === 'new') {
          if (!f.source_name.value.trim()) throw new Error('Name the source.');
          body.source_id = (await post('/v2/rule-sources', {name: f.source_name.value.trim(), kind: f.source_kind.value})).source.id;
        } else if (f.source.value) body.source_id = f.source.value;
        else throw new Error('Pick who it is from.');
      }
      const made = await post('/v2/rules', body);
      dlg.close();
      toast(made.status === 'suggested' ? 'Added to Suggested' : 'Added');
      if (after) { after(made); return; }
      if (RU && document.body.contains(RU.root)) {
        RU.loaded.clear();
        const tab = made.status === 'suggested' ? 'suggested' : made.kind;
        if (RU.tab === tab) { await ruReload(); if (made.status === 'active') location.hash = ruHref(tab, {rule: made.id}); }
        else location.hash = ruHref(tab, made.status === 'active' ? {rule: made.id} : {});
      }
    } catch (e) { button.disabled = false; status.textContent = ruError(e); }
  };
  dlg.showModal();
  controls.text.focus();
}

// ----------------------------------------------------------------- Add document
// One file from Files (one the viewer can read) or a public link, From (an existing one, or a new name with its kind),
// and an optional description. POST /rules/read opens the Rules Owner's task; the dialog then says the Rules Owner is reading it and
// links the task. Asked again while that task is open, the server returns it (`existing`), and the dialog says so. When
// screening holds the document (`screening: "hold"`), the Rules Owner is given none of its text, and the dialog says that instead.
const RU_DOC_SKIP = new Set(['image', 'media']);   // Files kinds the Rules Owner reads no text from
const RU_DOC_PICKS = 6;
const ruIsLink = (v: string) => /^https?:\/\/\S+$/i.test(v);
// A refusal as one line: The Rules Owner off, or the server's own reason (a file the Rules Owner can't read and why, a link that didn't
// open and why, a file or From that isn't there), which is already one line for the person.
function ruDocError(e: unknown) {
  if (errorCode(e) === 'counsel_off') return 'The Rules Owner is off.';
  return ruError(e);
}
async function ruDocOpen() {
  let dlg = document.getElementById('ru-doc') as HTMLDialogElement | null;
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'ru-doc'; dlg.className = 'ru-modal'; dlg.setAttribute('aria-label', 'Add document');
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg?.close(); });
    document.body.appendChild(dlg);
  }
  const [sources, recent] = await Promise.all([ruAll<RuleSource>('/v2/rule-sources').catch(() => []),
    get<{files?: DocFile[]}>('/v2/files?limit=50', {fresh: true}).then(r => r.files || []).catch(() => [])]);
  const readable = (f: DocFile) => f.locator !== 'remote_link' && !f.url && !RU_DOC_SKIP.has(f.library_kind ?? '') && !f.archived;
  const files = recent.filter(readable);
  const head = '<div class="ru-side-h"><h2>Add document</h2><span class="spacer"></span><button class="ghost tmodal-x" type="button" data-ru-close aria-label="Close">✕</button></div>';
  dlg.innerHTML = `<form class="ru-form" method="dialog">${head}
      <div class="ru-fl"><span class="ru-lbl">Document</span>
        <div class="ru-doc" hidden><span class="nav-icon" aria-hidden="true">description</span><span class="ru-doc-t"></span>
          <button type="button" class="ru-doc-x" data-ru-unpick aria-label="Remove">✕</button></div>
        <input name="find" type="search" placeholder="Pick from Files or paste a link" aria-label="Pick from Files or paste a link" autocomplete="off">
        <div class="ru-picks" role="listbox" aria-label="Files"></div></div>
      <label class="ru-fl"><span class="ru-lbl">Description <span class="ru-opt">(optional)</span></span><textarea name="note" rows="2" maxlength="2000"></textarea></label>
      <div class="ru-fl"><span class="ru-lbl">From</span>
        <select name="source"><option value="">Pick one</option>${ruSourceOptions(sources, '')}<option value="new">New…</option></select>
        <div class="ru-newdoc" hidden><input name="source_name" maxlength="200" placeholder="Name" aria-label="Name">${ruSegHTML('source_kind', RU_KINDS, 'vendor')}</div></div>
      <p class="ru-status" role="status"></p>
      <div class="ru-foot"><span class="spacer"></span><button class="ghost" type="button" data-ru-close>Cancel</button><button class="primary" type="submit">Read and suggest rules</button></div>
    </form>`;
  const form = ruFind<HTMLFormElement>('form', dlg), status = ruFind('.ru-status', form), controls = ruControls<DocControls>(form), find = controls.find;
  const picks = ruFind('.ru-picks', form), chosen = ruFind('.ru-doc', form);
  let pick: DocFile | null = null, found: DocFile[] = [], seq = 0, soon = 0;
  const pickRow = (f: DocFile) => `<button type="button" class="ru-pick" role="option" data-id="${esc(f.id)}"><span class="nav-icon" aria-hidden="true">description</span>
      <span class="ru-pick-t">${esc(f.title || f.name || f.id)}</span><span class="ru-pick-m">${esc(pageBotName(f))}</span></button>`;
  // The list under the box: recent readable files whose title has every word typed, then search hits by title and text.
  const drawPicks = () => {
    const q = find.value.trim();
    if (pick || ruIsLink(q)) { picks.innerHTML = ''; picks.hidden = true; return; }
    const local = files.filter(f => ruMatches(f.title || f.name || '', q));
    const rows = [...local, ...found.filter(f => !local.some(x => x.id === f.id))].slice(0, RU_DOC_PICKS);
    picks.hidden = false;
    picks.innerHTML = rows.map(pickRow).join('') || `<p class="ru-pick-none">${q ? 'No files match.' : 'No files yet.'}</p>`;
  };
  const choose = (f: DocFile | null) => {
    pick = f;
    chosen.hidden = !f;
    find.hidden = !!f;
    if (f) ruFind('.ru-doc-t', chosen).textContent = f.title || f.name || f.id;
    else { find.value = ''; find.focus(); }
    drawPicks();
  };
  find.oninput = () => {
    status.textContent = '';
    found = [];
    drawPicks();
    clearTimeout(soon);
    const q = find.value.trim(), mine = ++seq;
    if (!q || ruIsLink(q)) return;
    soon = setTimeout(async () => {
      const r = await get<{results?: DocFile[]}>('/v2/files/search?q=' + encodeURIComponent(q) + '&limit=' + RU_DOC_PICKS).catch(() => null);
      if (mine !== seq || !dlg.open) return;
      found = (r?.results || []).filter(readable);
      drawPicks();
    }, 250);
  };
  picks.onclick = ev => {
    const b = eventElement(ev)?.closest<HTMLElement>('.ru-pick');
    if (b) choose([...files, ...found].find(f => f.id === b.dataset.id) || null);
  };
  ruFind<HTMLButtonElement>('[data-ru-unpick]', chosen).onclick = () => choose(null);
  controls.source.onchange = () => { ruFind('.ru-newdoc', form).hidden = controls.source.value !== 'new'; };
  ruSegWire(form);
  form.querySelectorAll<HTMLButtonElement>('[data-ru-close]').forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async ev => {
    ev.preventDefault();
    const f = controls, button = ruFind<HTMLButtonElement>('[type=submit]', form), link = find.value.trim();
    const body: Record<string, unknown> = {note: f.note.value.trim()};
    if (pick) body.file_id = pick.id;
    else if (ruIsLink(link)) body.url = link;
    else { status.textContent = 'Pick a file or paste a link.'; return; }
    if (f.source.value === 'new') {
      if (!f.source_name.value.trim()) { status.textContent = 'Name the source.'; return; }
      body.source_id = f.source_name.value.trim();
      body.kind = ruSegValue(form, 'source_kind');
    } else if (f.source.value) body.source_id = f.source.value;
    else { status.textContent = 'Pick who it is from.'; return; }
    button.disabled = true; status.textContent = 'Reading…';
    let out: DocReadAnswer;
    try { out = await post('/v2/rules/read', body); }
    catch (e) { button.disabled = false; status.textContent = ruDocError(e); return; }
    if (RU) RU.sources = [];
    const title = esc(out.file?.title || pick?.title || link);
    form.innerHTML = `${head}
      ${out.screening === 'hold'
        ? `<p class="ru-held" data-ru-read="${esc(out.task_id)}">Screening held <b>${title}</b>. The Rules Owner won't suggest rules from it.</p>`
        : `<p class="ru-done" data-ru-read="${esc(out.task_id)}">${out.existing ? `The Rules Owner is already reading <b>${title}</b>.` : `The Rules Owner is reading <b>${title}</b>.`}</p>
      ${out.truncated ? '<p class="ru-warn">Long document: The Rules Owner reads only the start.</p>' : ''}`}
      <div class="ru-foot"><a href="#/task/${encodeURIComponent(out.task_id)}" data-ru-task>Open task ›</a><span class="spacer"></span><button class="primary" type="button" data-ru-close>Done</button></div>`;
    form.querySelectorAll('[data-ru-close], [data-ru-task]').forEach(b => { b.addEventListener('click', () => dlg.close()); });
  };
  drawPicks();
  dlg.showModal();
  find.focus();
}

// ----------------------------------------------------------------- the bot's Rules tab
// Internal first (the written rules whose Who or With names the bot, then its settings as sentences, each opening the
// setting), then External. Every teammate reads it. Each kind is paged on its own: a More row reads its next page.
const ruBotRow = (r: RuleRow) => `<a class="ru-row" role="row" href="${ruHref(r.kind, {rule: r.id})}" data-rule="${esc(r.id)}"><span class="ru-txt">${ruTextHTML(r.text, 'inline')}</span>${r.kind === 'internal'
  ? `<span class="ru-c${ruScopeWords(r.with, 'Anyone') === 'Anyone' ? ' ru-none' : ''}" data-l="With">${esc(ruScopeWords(r.with, 'Anyone'))}</span><span class="ru-c" data-l="Followed by">${esc(ruScopeWords(r.who))}</span>`
  : `<span class="ru-c" data-l="From">${r.source ? `<span class="ru-src ${esc(r.source.kind)}">${esc(r.source.name)}</span>` : '—'}</span><span class="ru-c" data-l="Due">${ruDueCell(r)}</span>`}</a>`;
const ruBotMore = (kind: string, cursor: string | null | undefined) => cursor ? `<button type="button" class="ru-row ru-more-row" data-ru-bot-more="${kind}" data-cursor="${esc(cursor)}">More</button>` : '';
let RU_BOT_GEN = 0;   // each read of the tab; an older response (or its More) never paints over a newer one
// A bot's reach as the Rules panel has it: its mode and counts, each with where it comes from, and the exceptions it
// sends or receives. Each row's link opens the panel, where it is changed.
const RR_MODE_WORDS: Record<string, string> = {open: 'anyone', nearby: 'nearby', closed: "only who's listed"};
function botReachRows(slug: string, data: ReachData | null | undefined) {
  if (!data) return [];   // a hub from before Reach
  const p = 'bot:' + slug, R = rrNormal(data), eff = rrEff(p, R), href = ruHref('internal', {party: p});
  const row = (text: string, from = '') => `<div class="ru-row ru-setting" role="row"><span class="ru-txt">${esc(text)}${from ? ` <span class="rr-from">${esc(from)}</span>` : ''}</span>`
    + `<span class="ru-c ru-none">—</span><span class="ru-c"><a class="ru-set" href="${esc(href)}">Setting ›</a></span></div>`;
  const rows = [row('Reach: ' + RR_MODE_WORDS[eff.mode], rrFrom(p, eff, 'mode')),
    row(`Up to ${eff.chats_per_day} chats a day`, rrFrom(p, eff, 'chats_per_day')),
    row(`Up to ${eff.tasks_per_day} tasks a day`, rrFrom(p, eff, 'tasks_per_day'))];
  for (const {l, dir, other} of rrLinesOf(p, R)) rows.push(row(`${dir === 'to' ? 'To' : 'From'} ${rrName(other)} · ${rrCount(l) || 'by rule'}`));
  return rows;
}
async function botRulesLoad(slug: string) {
  const host = $('#bot-rules') as HTMLElement | null;
  if (!host) return;
  const gen = ++RU_BOT_GEN, current = () => gen === RU_BOT_GEN && BOT?.slug === slug && host.isConnected;
  if (!host.dataset.loaded) host.innerHTML = skelRows(4, {card: true});
  let data: BotRules, reach: ReachData | null;
  try {
    [data, reach] = await Promise.all([get<BotRules>('/v2/bots/' + encodeURIComponent(slug) + '/rules', {fresh: true}),
      get<ReachData>('/v2/reach', {fresh: true}).catch(() => null)]);
  }
  catch (e) { if (current()) host.innerHTML = `<p class="err">${esc(errorMessage(e, ''))}</p>`; return; }
  if (!current()) return;
  host.dataset.loaded = '1';
  const internal: BotRulesPage = data.internal || {rules: []}, external: BotRulesPage = data.external || {rules: []}, settings: BotSetting[] = data.settings || [];
  const reachRows = botReachRows(slug, reach);
  const count = (page: BotRulesPage, extra = 0) => `${page.rules.length + extra}${page.next_cursor ? '+' : ''}`;
  host.innerHTML = `<section class="card ru-bot-card"><header><h2>Internal</h2><span class="muted" data-ru-n="internal">${count(internal, settings.length + reachRows.length)}</span><span class="spacer"></span>
        ${S.me?.role !== 'bot' ? '<button class="ghost small" type="button" data-ru-bot-add>+ Add rule</button>' : ''}</header>
      <div class="ru-table ru-bot-int" role="table"><div class="ru-row ru-head" role="row"><span>Rule</span><span>With</span><span>Followed by</span></div>
        ${internal.rules.map(ruBotRow).join('')}${ruBotMore('internal', internal.next_cursor)}
        ${/* the settings follow every written rule: More adds its page above itself, so above these */ ''}
        ${reachRows.join('')}
        ${settings.map(s => `<div class="ru-row ru-setting" role="row"><span class="ru-txt">${esc(s.text)}</span><span class="ru-c ru-none">—</span><span class="ru-c">${ruSettingLink(s, slug)}</span></div>`).join('')}
      </div></section>
    <section class="card ru-bot-card"><header><h2>External</h2><span class="muted">${count(external)}</span></header>
      ${external.rules.length ? `<div class="ru-table ru-bot-ext" role="table"><div class="ru-row ru-head" role="row"><span>Rule</span><span>From</span><span>Due</span></div>
        ${external.rules.map(ruBotRow).join('')}${ruBotMore('external', external.next_cursor)}</div>`
        : '<p class="empty">None.</p>'}</section>`;
  host.querySelector('[data-ru-bot-add]')?.addEventListener('click', () =>
    ruAddOpen({kind: 'internal', who: {bots: [slug]}, after: () => botRulesLoad(slug)}));
  host.onclick = async ev => {
    const set = eventElement(ev)?.closest<HTMLElement>('[data-ru-setting]');
    if (set) { ruSettingGo(set, ev); return; }
    const more = eventElement(ev)?.closest<HTMLButtonElement>('[data-ru-bot-more]');
    if (!more || more.disabled) return;
    more.disabled = true;
    const kind = more.dataset.ruBotMore as 'internal' | 'external';
    try {
      const page = (await get<BotRules>(`/v2/bots/${encodeURIComponent(slug)}/rules?${kind}_cursor=${encodeURIComponent(more.dataset.cursor ?? '')}`, {fresh: true}))[kind];
      if (!page) throw new Error('This bot has no more rules.');
      if (!current() || !more.isConnected) return;
      more.insertAdjacentHTML('beforebegin', page.rules.map(ruBotRow).join(''));
      if (page.next_cursor) { more.dataset.cursor = page.next_cursor; more.disabled = false; } else more.remove();
    } catch (e) { more.disabled = false; toast(errorMessage(e, ''), true); }
  };
}
