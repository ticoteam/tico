/* ui/app/avatars.ts — Bot identity: symbols, blob avatars, person avatars, runtime labels, bot order
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A bot's built-in art: a symbol from /assets/bot-symbols/ in its department's colour, or the Tico mark. */
interface BotArt {
  icon?: string;
  color: string;
  tico?: boolean;
}
/** A bot as an avatar reads it: a roster row, a card or a template ({id, slug, icon}). */
interface AvatarBot {
  id?: string | null | undefined;
  slug?: string | null | undefined;
  name?: string | null | undefined;
  icon?: string | null | undefined;
  display_name?: string | null | undefined;
}
/** A person as an avatar or a chip reads them: a roster person, a bot's owner or the signed-in person. */
interface AvatarPerson {
  id?: string | null | undefined;
  name?: string | null | undefined;
  email?: string | null | undefined;
  title?: string | null | undefined;
  photo?: string | null | undefined;
  photo_url?: string | null | undefined;
}
/** A bot row as its name, its temp flag and its order read it. */
interface NamedBot {
  display_name?: string | null | undefined;
  temp?: unknown;
  order?: unknown;
}
/** A node of the team chart as its order reads it: `{kind: 'person', person}` or a bot row. */
interface ChartNode extends NamedBot {
  kind?: unknown;
  id?: unknown;
  slug?: unknown;
  name?: unknown;
  reports_to?: unknown;
  report_position?: number | null | undefined;
  person?: {id?: unknown; name?: unknown; reports_to?: unknown; report_position?: number | null | undefined; [field: string]: unknown} | null | undefined;
}
/** One shape family: [harmonic, weight, phase] terms, and whether it stays upright or needs lifting. */
interface BlobFamily {
  name: string;
  h: [number, number, number][];
  upright?: boolean;
  gain?: number;
}
/** A bot's outline: its family, the path, and where in the breath it starts. */
interface BlobShape {
  family: string;
  d: string;
  delay: number;
}

// ----------------------------------------------------------------- bot identity: one filled Google symbol per role, with colour reserved for department.
// The symbols stay distinct when colour is unavailable; new/custom bots fall back to initials.
const BOT_AVATARS: Record<string, BotArt> = {
  // Operations / Tico
  assistant: {tico: true, color: '#0f6e56'},
  botops: {icon: 'robot_2', color: '#0f6e56'},
  'doc-updater': {icon: 'edit_document', color: '#0f6e56'},
  'inbox-manager': {icon: 'inbox', color: '#0f6e56'},
  // Marketing
  cmo: {icon: 'strategy', color: '#a63d65'},
  game: {icon: 'sports_esports', color: '#a63d65'},
  'model-releaser': {icon: 'deployed_code_update', color: '#a63d65'},
  'content-social': {icon: 'post', color: '#a63d65'},
  social: {icon: 'post', color: '#a63d65'},
  influencer: {icon: 'record_voice_over', color: '#a63d65'},
  'email-owner': {icon: 'stacked_email', color: '#a63d65'},
  analytics: {icon: 'analytics', color: '#a63d65'},
  'social-media-manager': {icon: 'radar', color: '#a63d65'},
  'reputation-manager': {icon: 'reviews', color: '#a63d65'},
  'cro-owner': {icon: 'conversion_path', color: '#a63d65'},
  launch: {icon: 'rocket_launch', color: '#a63d65'},
  recruiter: {icon: 'group_add', color: '#a63d65'},
  seo: {icon: 'travel_explore', color: '#a63d65'},
  // Creative
  designer: {icon: 'design_services', color: '#7a3e9d'},
  'video-owner': {icon: 'movie_edit', color: '#7a3e9d'},
  // Sales / Revenue
  'business-development': {icon: 'partner_exchange', color: '#1769a6'},
  'inbound-sales-owner': {icon: 'target', color: '#1769a6'},
  'sales-enablement-manager': {icon: 'mail_shield', color: '#1769a6'},
  'sales-operations-manager': {icon: 'account_tree', color: '#1769a6'},
  sdr: {icon: 'person_add', color: '#1769a6'},
  // Finance
  finance: {icon: 'finance_mode', color: '#2f6b3c'},
  'finance-owner': {icon: 'finance_mode', color: '#2f6b3c'},
  // Product
  'product-manager': {icon: 'route', color: '#5946b2'},
  'data-analyst': {icon: 'database_search', color: '#5946b2'},
  'backend-dev': {icon: 'dns', color: '#5946b2'},
  'bug-triage': {icon: 'bug_report', color: '#5946b2'},
  // Service
  success: {icon: 'flag_check', color: '#9a4f19'},
  'support-owner': {icon: 'support_agent', color: '#9a4f19'},
  // Legal and Engineering
  legal: {icon: 'balance', color: '#4b5563'},
  cto: {icon: 'lan', color: '#3f566b'},
  'engineering-manager': {icon: 'engineering', color: '#3f566b'},
  'backend-reviewer': {icon: 'rate_review', color: '#3f566b'},
  'frontend-reviewer': {icon: 'preview', color: '#3f566b'},
  'tico-open-source': {icon: 'code', color: '#3f566b'},
  // Operations notes (every bot has an icon)
  scribe: {icon: 'history_edu', color: '#0f6e56'},
};
const avInitials = (slug: string | null | undefined) => (String(slug || '').split(/[-_ ]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();
const avHue = (slug: string) => { let h = 7; for (let i = 0; i < String(slug).length; i++) h = (h * 31 + slug.charCodeAt(i)) % 360; return h; };
// Bots are soft blobs and people are circles, so the shape says which is which before the name does.
// A bot's outline comes from its slug through a seeded PRNG, the same on every page and every load.
// The seed picks one of ten shape families (a few low cosine harmonics around the circle, so every
// family stays soft), then turns it, varies its proportions and adds a little noise: two bots rarely
// look alike. While the bot works, it breathes: the shape swells and turns a little and back.
const BLOB_WOBBLE = 0.14;           // how far the outline may sit from the circle, as a share of the radius
const BLOB_RADIUS = 46;             // the circle the outline wobbles around, in a 100-unit box
const BLOB_POINTS = 8;              // eight points carry up to four lobes
const BLOB_MORPH_S = 3.6;           // one full breath while working, in seconds (ui/styles/avatars.css)
// Only a page's own bot breathes (its header, the chat pill, 30 px and up). Rows, the sidebar and lists keep still
// outlines. The breath is a transform the compositor draws: the outline morph it replaced (SMIL in WebKit, CSS `d`
// elsewhere) repainted every frame, and on every row a working bot appeared in it cost the desktop app a quarter of a core.
const BLOB_MORPH_MIN = 30;
// Each family is a list of [harmonic, weight, phase]: harmonic 2 stretches, 3 makes a rounded triangle,
// 4 four lobes; mixing them gives beans, eggs and peanuts. `upright` keeps flat shapes flat; `gain`
// lifts families led by harmonic 1, which otherwise reads as a circle pushed off centre.
const BLOB_FAMILIES: BlobFamily[] = [
  {name: 'pebble', h: [[2, 1, 0], [3, 0.22, 0.6]]},
  {name: 'triangle', h: [[3, 1, 0], [2, 0.15, 0]]},
  {name: 'clover', h: [[4, 1, 0], [2, 0.12, 0.4]]},
  {name: 'bean', h: [[2, 1, 0], [3, 0.55, Math.PI / 2], [1, 0.3, Math.PI / 2]]},
  {name: 'squat', h: [[2, 1, 0], [4, -0.45, 0]], upright: true},
  {name: 'egg', h: [[1, 0.6, 0], [2, 1, 0]], gain: 1.2},
  {name: 'peanut', h: [[2, 0.85, 0], [4, -0.7, 0]]},
  {name: 'shell', h: [[2, 0.7, 0], [3, 0.8, 0.5]]},
  {name: 'drop', h: [[1, 1, 0], [2, 0.5, 0], [3, 0.25, 0]], gain: 1.35},
  {name: 'cushion', h: [[4, 0.7, 0], [3, 0.45, 0.3], [2, 0.3, 1.1]]},
];
const TEMPLATE_ICON = /^[a-z0-9_]{1,48}$/;
const blobCache = new Map<string, BlobShape>();
const blobSeed = (text: string) => { let h = 2166136261; for (const ch of String(text)) { h ^= ch.codePointAt(0)!; h = Math.imul(h, 16777619); } return h >>> 0; };
function blobRandom(seed: number) {          // mulberry32: small, fast and the same in every browser
  return () => {
    seed = (seed + 0x6D2B79F5) >>> 0;
    let t = Math.imul(seed ^ (seed >>> 15), seed | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
// A closed curve through the points as cubic Béziers, each handle along the polar tangent with the
// length that draws an exact circle when every radius is equal: a wobble of 0 is a circle. `slopes`
// (dr/dθ at each point) default to finite differences. Every outline is M, n Cs and Z, so any two
// outlines of one bot interpolate point for point.
function blobPath(radii: number[], angles: number[], slopes?: number[]) {
  const n = radii.length, f = (v: number) => v.toFixed(1), wrap = (i: number) => (i + n) % n;
  const radius = (i: number) => radii[wrap(i)]!;                           // wrap keeps every index in range
  const theta = (i: number) => angles[wrap(i)]! + 2 * Math.PI * Math.floor(i / n);   // unwrapped, so gaps stay positive
  const pt = (i: number): [number, number] => [50 + radius(i) * Math.cos(theta(i)), 50 + radius(i) * Math.sin(theta(i))];
  const slope = (i: number) => slopes ? slopes[wrap(i)]! : (radius(i + 1) - radius(i - 1)) / (theta(i + 1) - theta(i - 1));
  const tangent = (i: number): [number, number] => { const a = theta(i), r = radius(i), dr = slope(i);
    return [dr * Math.cos(a) - r * Math.sin(a), dr * Math.sin(a) + r * Math.cos(a)]; };
  let d = `M${f(pt(0)[0])} ${f(pt(0)[1])}`;
  for (let i = 0; i < n; i++) {
    const k = 4 / 3 * Math.tan((theta(i + 1) - theta(i)) / 4), [p1, p2, t1, t2] = [pt(i), pt(i + 1), tangent(i), tangent(i + 1)];
    d += `C${f(p1[0] + k * t1[0])} ${f(p1[1] + k * t1[1])} ${f(p2[0] - k * t2[0])} ${f(p2[1] - k * t2[1])} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + 'Z';
}
// Small avatars wobble less, so the outline never pulls in under the symbol (the org tree is 16 px).
const blobWobble = (size: number) => BLOB_WOBBLE * (size < 16 ? 0.65 : size < 22 ? 0.8 : 1);
function blobShape(slug: string, size: number): BlobShape {
  const wobble = blobWobble(size), key = `${slug}|${wobble}`;
  if (blobCache.has(key)) return blobCache.get(key)!;
  const rnd = blobRandom(blobSeed(slug)), n = BLOB_POINTS;
  const fam = BLOB_FAMILIES[Math.floor(rnd() * BLOB_FAMILIES.length)]!;
  const turn = fam.upright ? (rnd() - 0.5) * 0.3 : rnd() * 2 * Math.PI;
  const harmonics = fam.h.map(([k, w, p]): [number, number, number] => [k, w * (0.8 + rnd() * 0.4), p + (rnd() - 0.5) * 0.5]);
  const noise = Array.from({length: n}, () => rnd() * 2 - 1);
  const angles = Array.from({length: n}, (_, i) => turn + i * 2 * Math.PI / n);
  // The family's profile, scaled so its furthest point sits exactly `wobble` from the circle.
  const outline = (hs: [number, number, number][], jitter: number[]) => {
    const at = (a: number) => hs.reduce((s, [k, w, p]) => s + w * Math.cos(k * (a - turn) + p), 0);
    const dat = (a: number) => hs.reduce((s, [k, w, p]) => s - w * k * Math.sin(k * (a - turn) + p), 0);
    let peak = 0;
    for (let i = 0; i < 72; i++) peak = Math.max(peak, Math.abs(at(i * Math.PI / 36)));
    const g = BLOB_RADIUS * wobble * (fam.gain || 1) / (peak || 1);
    return {radii: angles.map((a, i) => BLOB_RADIUS + g * (at(a) + jitter[i]! * 0.18)), slopes: angles.map(a => g * dat(a))};
  };
  const still = outline(harmonics, noise);
  const shape = {family: fam.name, d: blobPath(still.radii, angles, still.slopes),
    delay: -Math.round(rnd() * BLOB_MORPH_S * 100) / 100};   // bots working at once do not breathe in step
  blobCache.set(key, shape);
  return shape;
}
const blobBreath = (shape: BlobShape) => `animation-delay:${shape.delay}s`;
// Working: running a turn by the status poll, or answering in the chat that is open (the dots are moving).
function botWorking(slug: string) {
  if (S.v2?.status?.[slug]?.state === 'running' || S.status?.active?.some(a => a.employee === slug)) return true;
  return !!(V2C?.slug === slug && V2C.live && (V2C.live.text || v2PendingText(V2C).mode === 'run'));
}
// One avatar for every bot: its id, or a row or card with {id, slug, icon}. The symbol inside is the
// template's Material Symbol when it names one, else the built-in art, else the initials of its name; the Assistant
// keeps its mark. `state` is stateOf's word, drawn as the corner dot or ring. Its shape and colour follow the id, so a
// renamed bot keeps its face.
function botAvatar(bot: string | AvatarBot | null | undefined, size = 18, state?: string) {
  const slug = typeof bot === 'string' ? bot : bot?.id || bot?.slug || bot?.name || '';
  const row: AvatarBot | undefined = typeof bot === 'object' && bot ? bot : S.emps?.find(e => e.id === slug);
  const meta = BOT_AVATARS[slug], hue = avHue(slug);
  const rowIcon = row?.icon || '';
  const icon = !meta?.tico && TEMPLATE_ICON.test(rowIcon) ? rowIcon : '';
  const kind = meta?.tico ? 'bot-tico' : icon ? 'bot-glyph' : meta?.icon ? 'bot-symbol' : 'av-initials';
  const fill = meta?.tico ? '#172221' : meta?.color || `hsl(${hue} 38% 33%)`;
  const vars = kind === 'bot-symbol' && meta ? `;--bot-color:${meta.color};--bot-icon:url('/assets/bot-symbols/${meta.icon}.svg')`
    : kind === 'av-initials' ? `;--hue:${hue};font-size:${Math.round(size * 0.42)}px` : '';
  const inner = kind === 'bot-glyph' ? `<span class="av-glyph" style="font-size:${Math.round(size * 0.7)}px">${esc(icon)}</span>`
    : kind === 'av-initials' ? esc(avInitials(row?.display_name || row?.slug || slug)) : '';
  const breathes = size >= BLOB_MORPH_MIN, shape = blobShape(slug, size);
  const working = breathes && (state === 'running' || state === 'working' || botWorking(slug));
  return `<span class="av blob ${kind} ${state || ''}${working ? ' morph' : ''}" data-av="${esc(slug)}"${breathes ? ' data-breathe' : ''} style="width:${size}px;height:${size}px;--av-fill:${fill}${vars}" aria-hidden="true">`
    + `<svg class="av-shape" viewBox="0 0 100 100" focusable="false"${working ? ` style="${blobBreath(shape)}"` : ''}><path d="${shape.d}"/></svg>${inner}</span>`;
}
const avatar = botAvatar;
// Polls and the open chat change who is working without redrawing every avatar: this follows them.
function botAvatarsSync() {
  for (const el of document.querySelectorAll<HTMLElement>('.av.blob[data-breathe]')) {
    const slug = el.dataset.av;
    if (slug === undefined) continue;                  // botAvatar writes data-av beside data-breathe
    const on = botWorking(slug), svg = el.querySelector('.av-shape');
    if (on === el.classList.contains('morph') || !svg) continue;
    el.classList.toggle('morph', on);
    if (on) svg.setAttribute('style', blobBreath(blobShape(slug, parseFloat(el.style.width) || 18)));
    else svg.removeAttribute('style');
  }
}

// A hidden or minimized window draws nothing, so nothing in it moves: its animations hold (ui/styles/base.css) until it
// is shown again.
document.addEventListener('visibilitychange', () => document.documentElement.classList.toggle('page-hidden', document.hidden));

// People: Workspace/Slack photo when we have one, otherwise the same initials circle as a bot with no art.
const personInitials = (name: string | null | undefined) => avInitials(String(name || '?').replace(/[^A-Za-z0-9 ]+/g, ' '));
const personCircle = (name: string | null | undefined, size = 22) => `<span class="av av-initials" style="width:${size}px;height:${size}px;--hue:${avHue(String(name || ''))};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${esc(personInitials(name))}</span>`;
const personPhotoUrl = (p: AvatarPerson | null | undefined) => p?.photo_url || p?.photo || '';
function personAvatar(person: AvatarPerson | null | undefined, size = 22) {
  const name = person?.name || person?.id || '';
  const url = personPhotoUrl(person);
  if (!url) return personCircle(name, size);
  return `<img class="av av-photo" src="${esc(url)}" alt="" data-name="${esc(name)}" style="width:${size}px;height:${size}px" decoding="async" aria-hidden="true">`;
}
document.addEventListener('error', ev => {
  const el = ev.target;
  if (!(el instanceof HTMLElement) || !el.classList?.contains('av-photo')) return;
  const size = parseFloat(el.style.width) || 22;
  el.outerHTML = personCircle(el.dataset.name || '?', size);
}, true);
const firstName = (name: string | null | undefined) => String(name || '').trim().split(/\s+/)[0] || '';
const personTitle = (u: AvatarPerson) => `${u.name || u.id || ''}${u.email ? ` · ${u.email}` : ''}${u.title ? ` · ${u.title}` : ''}`.trim();
// the people who own a bot, from the employees payload; nothing at all when the backend has none
const userChips = (e: {users?: AvatarPerson[] | undefined} | null | undefined) => (e?.users || []).map(u =>
  `<span class="pchip" title="${esc(personTitle(u))}">${personAvatar(u, 15)}<span>${esc(firstName(u.name) || u.id)}</span></span>`).join('');

// A temp bot is work expected to end: its definition's `temp` flag, or the
// older "Temp X" name. Either way the name shows with a flag instead of the word.
const TEMP_RE = /^Temp\s+/;
const isTempBot = (e: NamedBot | null | undefined) => !!e?.temp || TEMP_RE.test(e?.display_name || '');
const TEMP_FLAG = '<span class="proj" title="temporary: expected to end">\u2691</span>';
const shownName = (e: NamedBot) => isTempBot(e)
  ? `${TEMP_FLAG}${esc((e.display_name || '').replace(TEMP_RE, ''))}`
  : esc(e.display_name || '');
// A bot's `order` (its definition, Settings or registry) puts a pipeline in sequence without
// numbering the names: siblings with an order come first, lowest first; the rest keep A-Z.
// Renaming a bot never changes where it sits: the order field decides, the name does not.
const botOrder = (e: NamedBot | null | undefined) => { const n = Number(e?.order); return e?.order == null || e.order === '' || !Number.isFinite(n) ? Infinity : n; };
const byBotOrder = (a: NamedBot, b: NamedBot) => { const x = botOrder(a), y = botOrder(b); return x === y ? 0 : x < y ? -1 : 1; };
// Whom a chart node reports to, as a tree key (`p:<id>` or `b:<bot id>`); '' at the top.
const orgReportsTo = (n: ChartNode) => {
  const to = String((n.kind === 'person' ? n.person?.reports_to : n.reports_to) || '');
  return !to ? '' : n.kind === 'person' ? 'p:' + to : to.startsWith('human:') ? 'p:' + to.slice(6) : 'b:' + to;
};
// A manager's reports in the order someone put them on the chart (backend/report_order.py): the placed ones first, by
// place, before the chart's own order. Only a direct report of `parent` has a place there; in a group (`g:<id>`), each
// member has its place among its own manager's reports.
const reportPlace = (n: ChartNode, parent: string) => {
  const at = n.kind === 'person' ? n.person?.report_position : n.report_position;
  const manager = String(parent || '').startsWith('g:') ? orgReportsTo(n) : parent;
  return at == null || !manager || orgReportsTo(n) !== manager ? Infinity : at;
};
const byReportOrder = (parent: string) => (a: ChartNode, b: ChartNode) => { const x = reportPlace(a, parent), y = reportPlace(b, parent); return x === y ? 0 : x < y ? -1 : 1; };
// The chart's own order for reports with no place, the same everywhere they are listed and on the server
// (backend/report_order.py fallback): humans first; then bots, temporary ones last and a bot's `order` lowest first;
// then the name, accents and case aside; the id breaks a tie. Nodes are `{kind: 'person', person}` or bot rows.
const chartNameKey = (s: unknown) => String(s || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
const chartFallback = (a: ChartNode, b: ChartNode) => {
  const human = (n: ChartNode) => n.kind === 'person';
  if (human(a) !== human(b)) return human(a) ? -1 : 1;
  if (!human(a) && isTempBot(a) !== isTempBot(b)) return Number(isTempBot(a)) - Number(isTempBot(b));
  if (!human(a) && byBotOrder(a, b)) return byBotOrder(a, b);
  const name = (n: ChartNode) => chartNameKey(human(n) ? n.person?.name || n.person?.id || n.id : String(n.display_name || n.slug || n.name || '').replace(TEMP_RE, ''));
  const id = (n: ChartNode) => String(human(n) ? n.person?.id || n.id : n.id || n.name || '');
  const x = name(a), y = name(b);
  if (x !== y) return x < y ? -1 : 1;
  return id(a) < id(b) ? -1 : id(a) > id(b) ? 1 : 0;
};
