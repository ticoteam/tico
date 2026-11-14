/* ui/app/tools.ts — Tools (#/tools): every way bots and people reach the outside world, and the router's pages under
   it. The list is laid out the way Automations is (style guide: "Tools and Automations: one layout"), on Automations'
   own layout classes (ui/styles/automations.css .au-*): search and filters in a rail on the left (Kind, Needs you, Who
   has access, each a plain list with counts; picking one narrows the list), one list on the right grouped In use, then
   Available. A row is the tool's mark and name, who can use it as faces, and a pill only when something is wrong; a
   tool that needs someone sorts to the top of its group. A row opens the tool as its own page, which holds its issues,
   who has it and its keys: Keys is not a page of its own any more. Adding is a quiet + in the header; ⋯ is last.
   On a phone the search moves into the header, the filters fold behind a button (sections that open one at a time)
   and the list is rows.
   The rows: GET /api/v2/tool-index (backend: one row an API tool, website, mailbox, phone number or Tico address,
   mailing address, Slack, GitHub, meeting importer, Listening, outgoing webhook, or a tool a bot declares with no row
   of its own; the hub filters them per reader), then the tools the old Keys page listed that the hub has no row for
   (GET /api/v2/tools: what bots declare and what could be set up; lcModel, ui/app/redesign-library.ts) and, for
   whoever may open the vault, each stored key no tool holds (GET /api/v2/credentials). GET /api/v2/groups lets Who has
   access name teams, a team counting the teams under it.
   Addresses:
     #/tools[?show=api|websites|mail|other|keys][&needs=1][&who=bot:<id>|human:<id>|group:<id>][&q=<words>]   the list
     #/tools/api/<id>[/activity]                      one API tool (ui/app/tools-api.ts)
     #/tools/website/<id>[/activity]                  one website tool (ui/app/tools-website.ts)
     #/tools/mailbox/<address or id>[/activity]       one mailbox  ┐
     #/tools/number/<id>                              one Tico address or phone number │ the inbox kinds' pages
     #/tools/address/<id>, #/tools/address/outbox     one mailing address, letters out  │ (ui/app/inboxes*.js)
     #/tools/settings[/<section>], #/tools/messages   mail and number settings, texts   ┘
     #/tools/webhooks[/<id>]                          outgoing webhooks, and one (ui/app/settings-webhooks.ts)
     #/tools/key/<id>                                 one stored key no tool holds (ui/app/redesign-library.ts)
     #/tools/<service>                                a declared tool's page: issues, access, keys (same file)
   The old homes of all of it redirect here (pages-shell.ts toolsRoute): Keys (#/tools/keys[/<service> |
   /credentials/<id>], with its who, issues and q filters), Library › Tools and › Connections, Settings › Inboxes and
   the tabs it replaced, #/integrations and #/credentials.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// A tool's status: the hub's tone (ok, wait, fail, run, idle) and the text the pill shows.
interface ToolsStatus { tone?: string; text?: string; }
// Someone who can use a tool: a bot, a person or (a website login shared with one) a group, with the name and face the
// list shows; a group carries its member count.
interface ToolsWho { kind: string; id: string; name?: string; members?: number | undefined; }
// One row of the hub's list, as it sends it for this reader.
interface ToolsRow { id: string; kind: string; name: string; detail?: string; way?: string; href?: string; private?: boolean; needs_you?: boolean; status?: ToolsStatus; who?: ToolsWho[]; who_total?: number; service?: string; }
// One row as the list draws it, from the hub's row, a declared tool or a loose key. `facet`: its Kind; `group`: In use
// or Available; `trouble`: the pill's words when something is wrong ('' otherwise), in `tone`; `text`: what search reads.
interface ToolsItem {
  key: string; kind: string; facet: string; group: string; name: string; title: string; href: string; icon: string;
  trouble: string; tone: string; private: boolean; who: ToolsWho[]; whoTotal: number; text: string;
}
// The list's state: its run (a newer pageTools wins), what was read, the rows built from it, the error, and the filters
// (they ride in the address); `fopen` is a phone's open filter sections, `closed` the groups folded.
interface ToolsListState {
  run: number; rows: ToolsRow[] | null; catalog: LcTool[]; groups: LcGroup[]; items: ToolsItem[] | null; error: string;
  q: string; show: string; needs: boolean; who: string; fopen: Set<string>; closed: Set<string>;
}
// Kind: key, label, icon. All, then each kind; a kind with nothing in it is left out unless it is the one picked.
const TOOLS_KINDS: [string, string, string][] = [['', 'All', 'category'], ['api', 'APIs', 'api'], ['websites', 'Websites', 'travel_explore'],
  ['mail', 'Mail', 'mail'], ['other', 'Other', 'build'], ['keys', 'Keys', 'key_vertical']];
// The Kind a hub row files under; Slack, GitHub, meeting importers, Listening and webhooks are Other. A declared tool
// files by what it is (a browser is a website, mail is mail, anything else an API).
const TOOLS_FACET: Record<string, string> = {api: 'api', declared: 'api', website: 'websites', mailbox: 'mail', number: 'mail', address: 'mail', key: 'keys'};
const TOOLS_LC_FACET: Record<string, string> = {browser: 'websites', mail: 'mail'};
// Icons the UI's font holds (ui/vendor/fonts/icons.txt).
const TOOLS_ICONS: Record<string, string> = {api: 'api', mailbox: 'mail', number: 'sms', address: 'local_shipping', postal: 'local_shipping', slack: 'forum', github: 'code',
  meetings: 'co_present', listening: 'campaign', webhook: 'open_in_new', declared: 'build', website: 'travel_explore', key: 'key_vertical'};
// The hub's tones as the house pills (ui/styles/components.css).
const TOOLS_TONE: Record<string, string> = {ok: 'ok', wait: 'waiting', fail: 'fail', run: 'in-progress', idle: ''};
const TOOLS_WHO_SHOWN = 4;
const TOOLS_GROUPS = ['In use', 'Available'];
let TL: ToolsListState | null = null;
const TOOLS_PRIVATE = ' <span class="pill tt-private" data-private>Private</span>';
const toolsPhone = (): boolean => matchMedia('(max-width:760px)').matches;
// A tool's own page shows its status as a pill (tools-api.ts, tools-website.ts).
const toolsPill = (status?: ToolsStatus) => {
  const tone = TOOLS_TONE[status?.tone ?? ''];
  return `<span class="pill${tone ? ' ' + tone : ''} tt-status" data-tone="${esc(status?.tone || 'idle')}"><span class="tt-dot" aria-hidden="true"></span>${esc(status?.text || '')}</span>`;
};
// A group's face: a rounded square with the groups icon (ui/styles/avatars.css .av-group).
const groupAvatar = (size = 22) => `<span class="av av-group" style="width:${size}px;height:${size}px" aria-hidden="true"><span class="nav-icon">groups</span></span>`;
// A group's member count, in words: "No members", "1 member", "5 members".
const toolsMembers = (n: number | undefined) => n === undefined ? '' : n === 0 ? 'No members' : n === 1 ? '1 member' : `${n} members`;
// One face: a group's, a bot's avatar, or a person's photo or initials.
const toolsFace = (w: ToolsWho, size = 22) => w.kind === 'group' ? groupAvatar(size) : w.kind === 'bot' ? avatar(w.id, size)
  : personAvatar((S.people || []).find(p => p.id === w.id) || {id: w.id, name: w.name}, size);
// A face's hover name: a group's says its member count ("Design · 5 members").
const toolsFaceTitle = (w: ToolsWho) => [w.name || w.id, w.kind === 'group' ? toolsMembers(w.members) : ''].filter(Boolean).join(' · ');
// Who can use it: up to four faces, each named on hover, then "+N".
function toolsWhoHTML(item: ToolsItem): string {
  if (!item.whoTotal) return '<span class="sr-only">Nobody yet</span>';
  const shown = item.who.slice(0, TOOLS_WHO_SHOWN), more = item.whoTotal - shown.length;
  return `<span class="tt-faces">${shown.map(w => `<span class="tt-face" title="${esc(toolsFaceTitle(w))}">${toolsFace(w)}</span>`).join('')}</span>${
    more > 0 ? `<span class="muted tt-more" title="${more} more">+${more}<span class="sr-only"> more</span></span>` : ''}`;
}
// A teammate as the Who has access filter names them: bot:<id>, human:<id> or group:<id> (the Keys page's links used
// these too). A bot named by its slug is read as its id, so the hub's rows and the vault's grants count as one.
const toolsBotId = (ref: string): string => botByRef(ref)?.id || ref;
const toolsWhoKey = (w: ToolsWho): string => w.kind === 'bot' ? 'bot:' + toolsBotId(w.id) : w.kind === 'group' ? 'group:' + w.id : 'human:' + w.id;
// The team or teammate Tools is narrowed to: a tool's page opens that team first under Access (redesign-library.ts).
const toolsWhoPicked = (): string => TL?.who || '';

// ----------------------------------------------------------------- the rows
// A hub row: in use when someone can use it; wrong when the hub says it needs someone or its status failed.
function toolsFromRow(r: ToolsRow): ToolsItem {
  const who = r.who || [], total = Math.max(r.who_total || 0, who.length), tone = r.status?.tone || 'idle';
  const wrong = !!r.needs_you || tone === 'fail', facet = TOOLS_FACET[r.kind] || 'other';
  return {key: r.id, kind: r.kind, facet, group: total ? 'In use' : 'Available', name: r.name || r.id, title: [r.name, r.detail].filter(Boolean).join(' · '),
    href: r.href || TOOLS_ROUTE, icon: TOOLS_ICONS[r.kind] || 'link', trouble: wrong ? r.status?.text || 'Needs you' : '', tone: tone === 'fail' ? 'fail' : 'waiting',
    private: !!r.private, who, whoTotal: total,
    // Search reads the words, not the cells: a status or "Nobody yet" the row leaves blank still finds it.
    text: [r.name, r.detail, r.way, r.status?.text, total ? '' : 'Nobody yet', ...who.map(w => w.name)].join(' ').toLowerCase()};
}
// The faces of a declared tool or a key, from who the bots' entries and the vault's grants say has it.
const toolsLcWho = (it: LcItem): ToolsWho[] => [
  ...[...it.people.keys()].map(id => ({kind: 'person', id, name: lcPerson(id).name || id})),
  ...[...it.bots.keys()].map(id => ({kind: 'bot', id, name: lcBotName(id)}))];
// A tool bots declare (or could use) that the hub has no row for: its own page is #/tools/<service>.
function toolsFromLc(it: LcItem, row: LcTool): ToolsItem {
  const who = toolsLcWho(it);
  // A bot's entry reporting a problem is a Problem; a key with no value stored, No key.
  const trouble = !it.issues.length ? '' : it.issues.some(i => i.bot) ? 'Problem' : 'No key';
  return {key: 'service:' + it.key, kind: 'declared', facet: TOOLS_LC_FACET[row.kind] || 'api', group: it.group, name: it.title, title: [it.title, it.kind].filter(Boolean).join(' · '),
    href: TOOLS_ROUTE + '/' + encodeURIComponent(it.key), icon: it.icon, trouble, tone: 'fail', private: false, who, whoTotal: who.length,
    text: [it.text, it.issues.map(i => i.text).join(' '), who.length ? '' : 'Nobody yet', ...who.map(w => w.name)].join(' ').toLowerCase()};
}
// A stored key no tool holds: in use once it is granted to someone or to the computers.
function toolsFromKey(it: LcItem, cred: LcCredential): ToolsItem {
  const who = toolsLcWho(it), computers = (cred.grants || []).some(g => g.subject === 'computers' || g.subject.startsWith('computer:'));
  return {key: it.key, kind: 'key', facet: 'keys', group: who.length || computers ? 'In use' : 'Available', name: cred.name, title: [cred.name, cred.env].filter(Boolean).join(' · '),
    href: TOOLS_ROUTE + '/key/' + encodeURIComponent(cred.id), icon: 'key_vertical', trouble: cred.stored ? '' : 'No value', tone: 'fail', private: false,
    who, whoTotal: who.length, text: [it.text, cred.stored ? '' : 'No value', ...who.map(w => w.name)].join(' ').toLowerCase()};
}
// Every row: the hub's, then the declared tools it has no row for (a service its rows already cover is drawn once), then
// the keys no tool holds (a key on an API tool is on that tool's page).
function toolsItems(state: ToolsListState): ToolsItem[] {
  const rows = state.rows || [], items = rows.map(toolsFromRow), covered = new Set<string>();
  for (const r of rows) {
    if (r.service) covered.add(r.service.toLowerCase());
    const own = (r.href || '').match(/^#\/tools\/([^/?]+)$/)?.[1];
    if (own) { try { covered.add(decodeURIComponent(own).toLowerCase()); } catch { covered.add(own.toLowerCase()); } }
  }
  for (const it of lcModel(state.catalog)) {
    if (it.row) { if (it.group !== 'Removed' && !covered.has(it.row.service.toLowerCase())) items.push(toolsFromLc(it, it.row)); }
    else if (it.cred && !it.cred.tool_id) items.push(toolsFromKey(it, it.cred));
  }
  return items;
}

// ----------------------------------------------------------------- the filters
// The address's filters. The Keys page's own (issues=1, show=credentials) and the old ?show=needs read the same way.
function toolsParams(params: URLSearchParams): Pick<ToolsListState, 'q' | 'show' | 'needs' | 'who'> {
  const show = params.get('show') || '', who = params.get('who') || '';
  return {q: params.get('q') || '', needs: show === 'needs' || params.get('needs') === '1' || params.get('issues') === '1',
    show: show === 'credentials' ? 'keys' : TOOLS_KINDS.some(([k]) => k && k === show) ? show : '',
    who: who.startsWith('bot:') ? 'bot:' + toolsBotId(who.slice(4)) : who};
}
function toolsQuery(state: ToolsListState): string {
  const q = new URLSearchParams();
  if (state.show) q.set('show', state.show);
  if (state.needs) q.set('needs', '1');
  if (state.who) q.set('who', state.who);
  if (state.q.trim()) q.set('q', state.q.trim());
  const text = q.toString();
  return text ? '?' + text : '';
}
// Whether a row is one this teammate or team can use. A team counts everyone in it and in the teams under it.
function toolsWhoTest(state: ToolsListState, ref: string): (i: ToolsItem) => boolean {
  if (!ref.startsWith('group:')) return i => i.who.some(w => toolsWhoKey(w) === ref);
  const gid = ref.slice(6), team = lcGroupMembers(state.groups, gid), bots = new Set([...team.bots].map(toolsBotId));
  return i => i.who.some(w => w.kind === 'group' ? w.id === gid : w.kind === 'bot' ? bots.has(toolsBotId(w.id)) : team.people.has(w.id));
}
function toolsShown(state: ToolsListState): ToolsItem[] {
  const words = state.q.toLowerCase().split(/\s+/).filter(Boolean), who = state.who ? toolsWhoTest(state, state.who) : null;
  return (state.items || []).filter(i => (!state.show || i.facet === state.show) && (!state.needs || i.trouble) && (!who || who(i))
    && words.every(w => i.text.includes(w)));
}
const toolsActive = (state: ToolsListState): number => Number(!!state.show) + Number(state.needs) + Number(!!state.who);
// Who has access: the teams anyone in them can use something, then each teammate, most tools first.
function toolsWhoChoices(state: ToolsListState): {ref: string; name: string; lead: string; n: number}[] {
  const items = state.items || [], seen = new Map<string, {w: ToolsWho; n: number}>();
  for (const i of items) for (const w of new Map(i.who.map(x => [toolsWhoKey(x), x])).values()) {
    const k = toolsWhoKey(w), had = seen.get(k);
    if (had) had.n++; else seen.set(k, {w, n: 1});
  }
  const count = (ref: string): number => items.filter(toolsWhoTest(state, ref)).length;
  const teams = state.groups.map(g => ({ref: 'group:' + g.id, name: g.name, lead: auIconHTML('groups'), n: count('group:' + g.id)}))
    .filter(t => t.n).sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
  const known = new Set(teams.map(t => t.ref));
  const each = [...seen].filter(([k]) => !known.has(k)).map(([ref, {w, n}]) => ({ref, n, name: w.name || (w.kind === 'bot' ? lcBotName(w.id) : w.id),
    lead: w.kind === 'group' ? auIconHTML('groups') : toolsFace(w, 16)}))
    .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name, undefined, {numeric: true}));
  const out = [...teams, ...each];
  // A filter for someone the list does not know (an old link to a bot this person cannot see) still shows as picked.
  if (state.who && !out.some(c => c.ref === state.who)) {
    const [kind = '', id = ''] = [state.who.slice(0, state.who.indexOf(':')), state.who.slice(state.who.indexOf(':') + 1)];
    const name = kind === 'bot' ? lcBotName(id) : kind === 'human' ? lcPerson(id).name || id : state.groups.find(g => g.id === id)?.name || id;
    out.unshift({ref: state.who, name, lead: auIconHTML(kind === 'group' ? 'groups' : 'person'), n: 0});
  }
  return out;
}
const auIconHTML = (name: string): string => `<span class="nav-icon" aria-hidden="true">${name}</span>`;
// The rail: Automations' facets (.au-fg, .au-fb). Every section open on a computer; on a phone each is a header that
// opens its list (TL.fopen), as Automations' are.
function toolsFacetsDraw(): void {
  const state = TL, box = $('#tools-root .au-facets') as HTMLElement | null;
  if (!state || !box) return;
  if (!state.items) { box.innerHTML = ''; return; }
  const items = state.items, phone = toolsPhone(), count = (test: (i: ToolsItem) => boolean): number => items.filter(test).length;
  const btn = (attrs: string, on: boolean, lead: string, label: string, n: number | null): string =>
    `<button type="button" class="au-fb" ${attrs} aria-pressed="${on}">${lead}<span class="au-ft">${esc(label)}</span>${n === null ? '' : `<span class="au-fn">${n}</span>`}</button>`;
  const sec = (key: string, label: string, html: string, picked: number): string => {
    if (!html) return '';
    if (!phone) return `<div class="au-fg" role="group" aria-label="${label}"><div class="au-lb">${label}</div>${html}</div>`;
    const open = state.fopen.has(key);
    return `<div class="au-fg" role="group" aria-label="${label}"><button type="button" class="au-fh" data-tl-fsec="${key}" aria-expanded="${open}">`
      + `<span class="au-ft">${label}</span>${picked ? `<span class="au-fhn">${picked}</span>` : ''}${auIconHTML('chevron_right').replace('nav-icon', 'nav-icon au-chev')}</button>${open ? html : ''}</div>`;
  };
  const kinds = TOOLS_KINDS.map(([k, label, icon]) => {
    const n = k ? count(i => i.facet === k) : items.length;
    return !k || n || state.show === k ? btn(`data-tl-show="${k}"`, state.show === k, auIconHTML(icon), label, n) : '';
  }).join('');
  const needs = count(i => !!i.trouble);
  const needHTML = needs || state.needs ? btn('data-tl-fneeds', state.needs, '<span class="au-dot fail" aria-hidden="true"></span>', needs === 1 ? '1 tool' : `${needs} tools`, null) : '';
  const who = toolsWhoChoices(state).map(c => btn(`data-tl-who="${esc(c.ref)}" title="${esc(c.name)}"`, state.who === c.ref, c.lead, c.name, c.n)).join('');
  box.innerHTML = sec('kind', 'Kind', kinds, Number(!!state.show)) + sec('needs', 'Needs you', needHTML, Number(state.needs)) + sec('who', 'Who has access', who, Number(!!state.who));
  const badge = $('#tools-root [data-tl-filters] .au-fbn') as HTMLElement | null, n = toolsActive(state);
  if (badge) { badge.hidden = !n; badge.textContent = String(n || ''); }
}
// One search, in the rail on a computer and in the header's tools on a phone: moved when the width crosses over, so a
// typed search stays put (Automations' auPlace).
function toolsPlace(root: HTMLElement): void {
  const s = root.querySelector<HTMLElement>('.au-s'), tools = root.querySelector<HTMLElement>('.ph .ph-tools'), rail = root.querySelector<HTMLElement>('.au-rail');
  if (!s || !tools || !rail) return;
  if (toolsPhone()) tools.prepend(s); else rail.prepend(s);
}
matchMedia('(max-width:760px)').addEventListener?.('change', () => {
  const root = $('#tools-root') as HTMLElement | null;
  if (!root || !TL) return;
  toolsPlace(root); toolsFacetsDraw();
});

// ----------------------------------------------------------------- the list
async function pageTools() {
  const params = new URLSearchParams(S.route.split('?')[1] || '');
  const run = (TL?.run || 0) + 1, kept = TL;
  TL = {run, rows: kept?.rows || null, catalog: kept?.catalog || [], groups: kept?.groups || [], items: kept?.items || null, error: '',
    ...toolsParams(params), fopen: kept?.fopen || new Set(), closed: kept?.closed || new Set()};
  const state = TL;
  const add = toolsAddItems().length;
  $('#main').classList.add('au-layout');
  // The one page header (ui/app/page-head.ts): a phone's filter button and the owner's chat in its tools, the quiet +
  // (Automations' .au-plus) for adding, then ⋯ with mail Settings, last.
  $('#main').innerHTML = `<div class="au tt-au" id="tools-root" data-screen="list">
    ${pageHead({title: 'Tools', hook: 'tt-head', pad: true,
      tools: `<button type="button" class="au-fbtn" data-tl-filters aria-label="Filters" aria-expanded="false">${filterIcon(18)}<span class="au-fbn" hidden></span></button>`,
      primary: add ? `<span class="au-newbox"><button class="ghost ph-go au-plus" type="button" data-tl-add aria-label="Add tool" title="Add tool" aria-haspopup="menu" aria-expanded="false"><span class="nav-icon" aria-hidden="true">add</span></button><div class="au-newm" role="menu" aria-label="Add tool" hidden></div></span>` : '',
      more: `<a href="#/tools/settings" data-tl-settings>Settings</a>`})}
    <div class="au-body">
      <aside class="au-rail" aria-label="Filters"><div class="au-s"><input class="au-q" type="search" placeholder="Search" aria-label="Search tools" autocomplete="off" spellcheck="false" data-tl-q></div><div class="au-facets"></div></aside>
      <section class="au-c" aria-label="Tools"><div class="au-tbl tt-tbl" data-tl-table>${state.items ? '' : skelRows(6)}</div></section>
    </div>
    <dialog class="bot-editor" id="credential-dialog" aria-label="Key"></dialog></div>`;
  const root: HTMLElement = $('#tools-root')!;   // drawn just above
  const search = root.querySelector<HTMLInputElement>('[data-tl-q]')!;
  search.value = state.q;
  search.oninput = () => { if (TL !== state) return; state.q = search.value; toolsChanged(); };
  toolsPlace(root);
  toolsWire(root, state);
  // The Tools owner's chat (page-chat.ts, mounted by the router), which takes keys without a model seeing them
  // (tools-key-drop.ts).
  pageChatUse('tools', {empty: toolsChatEmpty, keyDrop: toolsInProblem});
  if (state.items) toolsDraw();
  // The hub's list, the tools bots declare, the teams and (for whoever may open it) the vault, read together so the
  // list draws once, in its final order. Only the hub's list failing is an error: the rest leave their rows out.
  const vault = S.me?.credential_access ? vaultFetch().catch(() => null) : Promise.resolve(null);
  const [index, catalog, groups] = await Promise.allSettled([
    get<{rows?: ToolsRow[]}>('/v2/tool-index', {fresh: true}),
    get<LcToolsResponse>('/v2/tools').then(r => r.integrations || []),
    get<LcGroup[] | {groups?: LcGroup[]}>('/v2/groups'),
    vault,
  ]);
  if (TL !== state) return;
  if (index.status === 'fulfilled') state.rows = index.value.rows || [];
  else { state.error = errorMessage(index.reason, ''); state.rows ||= []; }
  if (catalog.status === 'fulfilled') state.catalog = catalog.value;
  if (groups.status === 'fulfilled') state.groups = Array.isArray(groups.value) ? groups.value : groups.value?.groups || [];
  state.items = toolsItems(state);
  if (root.isConnected) { toolsDraw(); pageChatEmptySync(); }   // the chat's empty line names the tools in Problem
}

// The tools that need someone, by name: the chat's empty state names them and a dropped key's "For" offers them.
const toolsInProblem = (): string[] => (TL?.items || []).filter(i => i.trouble).map(i => i.name).slice(0, 8);
function toolsChatEmpty(): string {
  const names = toolsInProblem();
  const list = names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1] : names[0] || '';
  return (list ? `${list} ${names.length > 1 ? 'need' : 'needs'} keys. ` : '')
    + 'Paste keys here, in any order, with a word about what each is for. They are stored encrypted; the bot only sees their names.';
}
// A vault dialog saved a change (lcVaultChanged): the keys' rows and faces follow.
function toolsVaultChanged(): void {
  if (!TL?.items) return;
  TL.items = toolsItems(TL);   // also while a tool's page is open, so going back shows the change at once
  if ($('#tools-root')) toolsDraw();
}
// A filter or the search changed: into the address (replaced, so Back leaves Tools), then the rail and the list.
function toolsChanged(): void {
  if (!TL) return;
  history.replaceState(null, '', TOOLS_ROUTE + toolsQuery(TL));
  S.route = LAST_ROUTE = location.hash;
  toolsDraw();
}
function toolsDraw(): void { toolsFacetsDraw(); toolsListDraw(); }

function toolsListDraw(): void {
  const state = TL, box = $('#tools-root [data-tl-table]') as HTMLElement | null;
  if (!state || !box || !state.items) return;
  const all = state.items, shown = toolsShown(state);
  const err = state.error ? `<div class="err au-pad tt-err" role="alert" data-tl-error>${esc(state.error)} <button type="button" class="linkish" data-tl-retry>Try again</button></div>` : '';
  if (!shown.length) {
    // Empty is one line and one action: add the first tool, add a key, or clear what narrows the list.
    const canAdd = toolsAddItems().length > 0, keys = state.show === 'keys' && !all.some(i => i.facet === 'keys');
    const empty = state.error && !all.length ? ''
      : !all.length ? `<div class="empty tt-empty" data-tl-empty><p>No tools yet.</p>${canAdd ? '<button type="button" class="ghost" data-tl-add-open>Add a tool</button>' : ''}</div>`
      : keys && !state.q && !state.needs && !state.who ? `<div class="empty tt-empty" data-tl-empty><p>No keys yet.</p>${VAULT?.can_manage ? '<button type="button" class="ghost" data-tl-add-key>Add a key</button>' : ''}</div>`
      : `<div class="empty tt-empty" data-tl-empty><p>Nothing matches.</p><button type="button" class="ghost" data-tl-clear>Clear filters</button></div>`;
    box.innerHTML = err + empty;
    return;
  }
  const by = (a: ToolsItem, b: ToolsItem): number => Number(!a.trouble) - Number(!b.trouble) || a.name.localeCompare(b.name, undefined, {numeric: true});
  const lock = '<span class="nav-icon tt-lock" role="img" aria-label="Private" title="Private" data-private>lock</span>';
  const tr = (i: ToolsItem): string => `<tr class="au-row" data-tl-row="${esc(i.key)}" data-tl-kind="${esc(i.kind)}"${i.trouble ? ' data-tl-needs' : ''}>`
    + `<td class="au-tn"><div><a href="${esc(i.href)}" title="${esc(i.title)}">${auIconHTML(i.icon)}<span class="au-nm">${esc(i.name)}</span>${i.private ? lock : ''}</a>`
    + `${i.trouble ? `<span class="pill ${i.tone} tt-pill">${esc(i.trouble)}</span>` : ''}</div></td><td class="tt-who">${toolsWhoHTML(i)}</td></tr>`;
  const body = TOOLS_GROUPS.map(g => {
    const rows = shown.filter(i => i.group === g).sort(by), closed = state.closed.has(g);
    if (!rows.length) return '';
    return `<tr class="au-grp${closed ? ' closed' : ''}" data-tl-grp="${g}" role="button" tabindex="0" aria-expanded="${!closed}"><td colspan="2">${auIconHTML('expand_more').replace('nav-icon', 'nav-icon au-chev')}${g}<span class="au-fn">${rows.length}</span></td></tr>`
      + (closed ? '' : rows.map(tr).join(''));
  }).join('');
  patchLiveHTML(box, err + `<table class="au-tbl-t tt-tbl-t"><colgroup><col><col class="tt-who-col"></colgroup>
    <thead><tr><th>Name</th><th>Who can use it</th></tr></thead><tbody>${body}</tbody></table>`);
}

// The page's clicks and keys, on its root: the filters, the groups, the rows, the + and its menu, Retry and Clear.
function toolsWire(root: HTMLElement, state: ToolsListState): void {
  const menu = root.querySelector<HTMLElement>('.au-newm'), plus = root.querySelector<HTMLButtonElement>('[data-tl-add]');
  const search = root.querySelector<HTMLInputElement>('[data-tl-q]');
  const menuOpen = (open: boolean, focus = true): void => {
    if (!menu || !plus) return;
    if (open) menu.innerHTML = toolsAddItems().map(([k, label, icon]) => `<button type="button" role="menuitem" data-tl-add-item="${esc(k)}">${auIconHTML(icon)}${esc(label)}</button>`).join('');
    menu.hidden = !open;
    plus.setAttribute('aria-expanded', String(open));
    if (open && focus) menu.querySelector<HTMLElement>('button')?.focus();
  };
  root.addEventListener('click', ev => {
    if (TL !== state) return;
    const t = eventElement(ev);
    if (!t) return;
    let m: HTMLElement | null;
    if (menu && !menu.hidden && !t.closest('.au-newbox')) menuOpen(false, false);
    m = t.closest<HTMLElement>('[data-tl-show]');
    if (m) { state.show = m.dataset.tlShow ?? ''; toolsChanged(); return; }
    if (t.closest('[data-tl-fneeds]')) { state.needs = !state.needs; toolsChanged(); return; }
    m = t.closest<HTMLElement>('[data-tl-who]');
    if (m) { const ref = m.dataset.tlWho ?? ''; state.who = state.who === ref ? '' : ref; toolsChanged(); return; }
    m = t.closest<HTMLElement>('[data-tl-filters]');
    if (m) {
      // Opening starts with the sections that have a filter on open and the rest closed.
      const open = root.classList.toggle('filters-open');
      if (open) state.fopen = new Set([state.show ? 'kind' : '', state.needs ? 'needs' : '', state.who ? 'who' : ''].filter(Boolean));
      m.setAttribute('aria-expanded', String(open));
      toolsFacetsDraw();
      return;
    }
    m = t.closest<HTMLElement>('[data-tl-fsec]');
    if (m) {
      const k = m.dataset.tlFsec ?? '';
      if (state.fopen.has(k)) state.fopen.delete(k); else state.fopen.add(k);
      toolsFacetsDraw();
      root.querySelector<HTMLElement>(`[data-tl-fsec="${k}"]`)?.focus();
      return;
    }
    m = t.closest<HTMLElement>('[data-tl-grp]');
    if (m) { toolsGroupToggle(m.dataset.tlGrp ?? ''); return; }
    if (t.closest('[data-tl-add]')) { menuOpen(!!menu?.hidden); return; }
    if (t.closest('[data-tl-add-open]')) { menuOpen(true); return; }
    m = t.closest<HTMLElement>('[data-tl-add-item]');
    if (m) { menuOpen(false, false); toolsAdd(m.dataset.tlAddItem ?? ''); return; }
    if (t.closest('[data-tl-add-key]')) { vaultEdit(); return; }
    if (t.closest('[data-tl-clear]')) { state.show = ''; state.needs = false; state.who = ''; state.q = ''; if (search) search.value = ''; toolsChanged(); return; }
    if (t.closest('[data-tl-retry]')) { void pageTools(); return; }
    // The whole row opens the tool; its link keeps the browser's own ⌘-click and middle click.
    const row = t.closest<HTMLElement>('tr[data-tl-row]');
    if (row && !t.closest('a')) { const href = row.querySelector('.au-tn a')?.getAttribute('href'); if (href) location.hash = href; }
  });
  root.addEventListener('keydown', (ev: KeyboardEvent) => {
    const t = ev.target instanceof HTMLElement ? ev.target : null;
    if (ev.key === 'Escape' && menu && !menu.hidden) { ev.preventDefault(); menuOpen(false, false); plus?.focus(); return; }
    if ((ev.key === 'Enter' || ev.key === ' ') && t?.matches('[data-tl-grp]')) { ev.preventDefault(); toolsGroupToggle(t.dataset.tlGrp ?? ''); }
  });
}
function toolsGroupToggle(g: string): void {
  if (!TL) return;
  if (TL.closed.has(g)) TL.closed.delete(g); else TL.closed.add(g);
  toolsListDraw();
  ($(`#tools-root [data-tl-grp="${g}"]`) as HTMLElement | null)?.focus();
}

// ----------------------------------------------------------------- Add tool
// What + offers: an API (POST /api-tools) and a website (POST /website-tools) for whoever manages keys, each inbox
// kind's own Add (ui/app/inboxes*.js), an outgoing webhook, and a key no tool holds (the vault's Add, for whoever
// manages the vault). A read-only view offers nothing, so it has no +. [key, label, icon]
function toolsAddItems(): [string, string, string][] {
  if (S.me?.read_only_view) return [];
  const admin = settingsIsAdmin();
  const kinds: InboxKind[] = Object.values(INBOX_KINDS).filter((k: InboxKind) => k.add && (!k.add.admin || admin)).sort((a: InboxKind, b: InboxKind) => a.order - b.order);
  return [
    ...(S.me?.credential_access || admin ? [['api', 'API', 'api'], ['website', 'Website', 'travel_explore']] as [string, string, string][] : []),
    ...kinds.map((k): [string, string, string] => ['kind:' + k.key, k.add?.label || k.label, TOOLS_ICONS[k.key] || 'mail']),
    ['webhook', 'Outgoing webhook', 'open_in_new'],
    ...(VAULT?.can_manage ? [['key', 'Key', 'key_vertical']] as [string, string, string][] : []),
  ];
}
function toolsAdd(item: string): void {
  if (item === 'api') { toolsApiCreate(); return; }
  if (item === 'website') { toolsWebsiteCreate(); return; }
  if (item === 'webhook') { location.hash = WH_HOOKS_ROUTE; void whHookDialog(null); return; }
  if (item === 'key') { vaultEdit(); return; }
  INBOX_KINDS[item.slice(5)]?.add?.open(inboxesCtx(IB.run));
}

// One inbox kind's page, mail settings or Messages, drawn by ui/app/inboxes.ts into a host kept while the address stays
// under the same page, so a redraw keeps what it drew.
function toolsInboxPage(view: string) {
  INBOXES_VIEW = view;
  if (!$('#tools-inbox')) $('#main').innerHTML = '<div class="tt tt-page" id="tools-inbox"></div>';
  void inboxesRender();
}
// Leaving Tools drops the list, so coming back reads the hub again.
function toolsStop() { TL = null; INBOXES_VIEW = ''; rdToolsStop(); toolApiStop(); toolWebsiteStop(); }
