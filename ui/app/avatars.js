/* ui/app/avatars.js — Bot identity: symbols, blob avatars, person avatars, runtime labels, bot order
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- bot identity: one filled Google symbol per role, with colour reserved for department.
// The symbols stay distinct when colour is unavailable; new/custom bots fall back to initials.
const BOT_AVATARS = {
  // Operations / Tico
  coo: {tico: true, color: '#0f6e56'},
  botops: {icon: 'robot_2', color: '#0f6e56'},
  'doc-updater': {icon: 'edit_document', color: '#0f6e56'},
  'inbox': {icon: 'inbox', color: '#0f6e56'},
  inbox: {icon: 'inbox', color: '#0f6e56'},
  // Marketing
  cmo: {icon: 'strategy', color: '#a63d65'},
  game: {icon: 'sports_esports', color: '#a63d65'},
  'model-releaser': {icon: 'deployed_code_update', color: '#a63d65'},
  'content-social': {icon: 'post', color: '#a63d65'},
  social: {icon: 'post', color: '#a63d65'},
  influencer: {icon: 'record_voice_over', color: '#a63d65'},
  'email-marketing': {icon: 'stacked_email', color: '#a63d65'},
  analytics: {icon: 'analytics', color: '#a63d65'},
  listening: {icon: 'radar', color: '#a63d65'},
  reputation: {icon: 'reviews', color: '#a63d65'},
  cro: {icon: 'conversion_path', color: '#a63d65'},
  launch: {icon: 'rocket_launch', color: '#a63d65'},
  recruiting: {icon: 'group_add', color: '#a63d65'},
  seo: {icon: 'travel_explore', color: '#a63d65'},
  // Creative
  designer: {icon: 'design_services', color: '#7a3e9d'},
  'video-producer': {icon: 'movie_edit', color: '#7a3e9d'},
  // Sales / Revenue
  'business-development': {icon: 'partner_exchange', color: '#1769a6'},
  sales: {icon: 'target', color: '#1769a6'},
  'sales-enablement': {icon: 'mail_shield', color: '#1769a6'},
  'sales-ops': {icon: 'account_tree', color: '#1769a6'},
  sdr: {icon: 'person_add', color: '#1769a6'},
  // Finance
  finance: {icon: 'finance_mode', color: '#2f6b3c'},
  bookkeeper: {icon: 'finance_mode', color: '#2f6b3c'},
  // Product
  'product-manager': {icon: 'route', color: '#5946b2'},
  'data-analyst': {icon: 'database_search', color: '#5946b2'},
  'backend-dev': {icon: 'dns', color: '#5946b2'},
  'bug-triage': {icon: 'bug_report', color: '#5946b2'},
  // Service
  success: {icon: 'flag_check', color: '#9a4f19'},
  support: {icon: 'support_agent', color: '#9a4f19'},
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
const avInitials = slug => (String(slug || '').split(/[-_ ]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();
const avHue = slug => { let h = 7; for (let i = 0; i < String(slug).length; i++) h = (h * 31 + slug.charCodeAt(i)) % 360; return h; };
// Bots are soft blobs and people are circles, so the shape says which is which before the name does.
// A bot's outline comes from its slug through a seeded PRNG, the same on every page and every load.
// The seed picks one of ten shape families (a few low cosine harmonics around the circle, so every
// family stays soft), then turns it, varies its proportions and adds a little noise: two bots rarely
// look alike. While the bot works, the outline eases to a second outline from the same seed and back.
const BLOB_WOBBLE = 0.14;           // how far the outline may sit from the circle, as a share of the radius
const BLOB_RADIUS = 46;             // the circle the outline wobbles around, in a 100-unit box
const BLOB_POINTS = 8;              // eight points carry up to four lobes
const BLOB_MORPH_S = 3.6;           // one full breath while working, in seconds
const BLOB_CSS_MORPH = !!window.CSS?.supports?.('d', 'path("M0 0")');   // WebKit has no CSS `d`: it gets SMIL
// Each family is a list of [harmonic, weight, phase]: harmonic 2 stretches, 3 makes a rounded triangle,
// 4 four lobes; mixing them gives beans, eggs and peanuts. `upright` keeps flat shapes flat; `gain`
// lifts families led by harmonic 1, which otherwise reads as a circle pushed off centre.
const BLOB_FAMILIES = [
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
const blobCache = new Map();
const blobSeed = text => { let h = 2166136261; for (const ch of String(text)) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
function blobRandom(seed) {                  // mulberry32: small, fast and the same in every browser
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
function blobPath(radii, angles, slopes) {
  const n = radii.length, f = v => v.toFixed(1), wrap = i => (i + n) % n;
  const theta = i => angles[wrap(i)] + 2 * Math.PI * Math.floor(i / n);   // unwrapped, so gaps stay positive
  const pt = i => [50 + radii[wrap(i)] * Math.cos(theta(i)), 50 + radii[wrap(i)] * Math.sin(theta(i))];
  const slope = i => slopes ? slopes[wrap(i)] : (radii[wrap(i + 1)] - radii[wrap(i - 1)]) / (theta(i + 1) - theta(i - 1));
  const tangent = i => { const a = theta(i), r = radii[wrap(i)], dr = slope(i);
    return [dr * Math.cos(a) - r * Math.sin(a), dr * Math.sin(a) + r * Math.cos(a)]; };
  let d = `M${f(pt(0)[0])} ${f(pt(0)[1])}`;
  for (let i = 0; i < n; i++) {
    const k = 4 / 3 * Math.tan((theta(i + 1) - theta(i)) / 4), [p1, p2, t1, t2] = [pt(i), pt(i + 1), tangent(i), tangent(i + 1)];
    d += `C${f(p1[0] + k * t1[0])} ${f(p1[1] + k * t1[1])} ${f(p2[0] - k * t2[0])} ${f(p2[1] - k * t2[1])} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + 'Z';
}
// Small avatars wobble less, so the outline never pulls in under the symbol (the org tree is 16 px).
const blobWobble = size => BLOB_WOBBLE * (size < 16 ? 0.65 : size < 22 ? 0.8 : 1);
function blobShape(slug, size) {
  const wobble = blobWobble(size), key = `${slug}|${wobble}`;
  if (blobCache.has(key)) return blobCache.get(key);
  const rnd = blobRandom(blobSeed(slug)), n = BLOB_POINTS;
  const fam = BLOB_FAMILIES[Math.floor(rnd() * BLOB_FAMILIES.length)];
  const turn = fam.upright ? (rnd() - 0.5) * 0.3 : rnd() * 2 * Math.PI;
  const harmonics = fam.h.map(([k, w, p]) => [k, w * (0.8 + rnd() * 0.4), p + (rnd() - 0.5) * 0.5]);
  const noise = Array.from({length: n}, () => rnd() * 2 - 1);
  const angles = Array.from({length: n}, (_, i) => turn + i * 2 * Math.PI / n);
  // The family's profile, scaled so its furthest point sits exactly `wobble` from the circle.
  const outline = (hs, jitter) => {
    const at = a => hs.reduce((s, [k, w, p]) => s + w * Math.cos(k * (a - turn) + p), 0);
    const dat = a => hs.reduce((s, [k, w, p]) => s - w * k * Math.sin(k * (a - turn) + p), 0);
    let peak = 0;
    for (let i = 0; i < 72; i++) peak = Math.max(peak, Math.abs(at(i * Math.PI / 36)));
    const g = BLOB_RADIUS * wobble * (fam.gain || 1) / (peak || 1);
    return {radii: angles.map((a, i) => BLOB_RADIUS + g * (at(a) + jitter[i] * 0.18)), slopes: angles.map(a => g * dat(a))};
  };
  const still = outline(harmonics, noise);
  // The working outline shifts each lobe a little and redistributes the noise: it breathes, slowly
  // and shallowly, rather than turning into another bot.
  const moving = outline(harmonics.map(([k, w, p]) => [k, w * (0.85 + rnd() * 0.3), p + (rnd() < 0.5 ? -1 : 1) * (0.35 + rnd() * 0.25)]),
    noise.map(v => v * 0.4 + (rnd() * 2 - 1) * 0.6));
  const shape = {family: fam.name, d: blobPath(still.radii, angles, still.slopes),
    morph: blobPath(moving.radii, angles, moving.slopes),
    delay: -Math.round(rnd() * BLOB_MORPH_S * 100) / 100};   // bots working at once do not breathe in step
  blobCache.set(key, shape);
  return shape;
}
const reducedMotion = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const blobMorphStyle = shape => `--d0:path('${shape.d}');--d1:path('${shape.morph}');animation-delay:${shape.delay}s`;
const blobSmil = shape => `<animate attributeName="d" values="${shape.d};${shape.morph};${shape.d}" dur="${BLOB_MORPH_S}s" begin="${shape.delay}s" repeatCount="indefinite" calcMode="spline" keyTimes="0;.5;1" keySplines=".42 0 .58 1;.42 0 .58 1"/>`;
// Working: running a turn by the status poll, or answering in the chat that is open (the dots are moving).
function botWorking(slug) {
  if (S.v2?.status?.[slug]?.state === 'running' || S.status?.active?.some(a => a.employee === slug)) return true;
  return !!(V2C?.slug === slug && V2C.live && (V2C.live.text || v2PendingText(V2C).mode === 'run'));
}
// One avatar for every bot: a slug, or a row or card with {slug|name, icon}. The symbol inside is the
// template's Material Symbol when it names one, else the built-in art, else initials; the Assistant
// keeps its mark. `state` is stateOf's word, drawn as the corner dot or ring.
function botAvatar(bot, size = 18, state) {
  const slug = typeof bot === 'string' ? bot : bot?.slug || bot?.name || '';
  const row = typeof bot === 'object' && bot ? bot : S.emps?.find(e => e.name === slug);
  const meta = BOT_AVATARS[slug], hue = avHue(slug);
  const icon = !meta?.tico && TEMPLATE_ICON.test(row?.icon || '') ? row.icon : '';
  const kind = meta?.tico ? 'bot-tico' : icon ? 'bot-glyph' : meta?.icon ? 'bot-symbol' : 'av-initials';
  const fill = meta?.tico ? '#172221' : meta?.color || `hsl(${hue} 38% 45%)`;
  const vars = kind === 'bot-symbol' ? `;--bot-color:${meta.color};--bot-icon:url('/assets/bot-symbols/${meta.icon}.svg')`
    : kind === 'av-initials' ? `;--hue:${hue};font-size:${Math.round(size * 0.42)}px` : '';
  const inner = kind === 'bot-glyph' ? `<span class="av-glyph" style="font-size:${Math.round(size * 0.7)}px">${esc(icon)}</span>`
    : kind === 'av-initials' ? esc(avInitials(slug)) : '';
  const shape = blobShape(slug, size), working = state === 'running' || state === 'working' || botWorking(slug);
  const path = working
    ? `<path d="${shape.d}" style="${blobMorphStyle(shape)}">${BLOB_CSS_MORPH || reducedMotion() ? '' : blobSmil(shape)}</path>`
    : `<path d="${shape.d}"/>`;
  return `<span class="av blob ${kind} ${state || ''}${working ? ' morph' : ''}" data-av="${esc(slug)}" style="width:${size}px;height:${size}px;--av-fill:${fill}${vars}" aria-hidden="true">`
    + `<svg class="av-shape" viewBox="0 0 100 100" focusable="false">${path}</svg>${inner}</span>`;
}
const avatar = botAvatar;
// Polls and the open chat change who is working without redrawing every avatar: this follows them.
function botAvatarsSync() {
  for (const el of document.querySelectorAll('.av.blob[data-av]')) {
    const on = botWorking(el.dataset.av), path = el.querySelector('.av-shape path');
    if (on === el.classList.contains('morph') || !path) continue;
    const shape = blobShape(el.dataset.av, parseFloat(el.style.width) || 18);
    el.classList.toggle('morph', on);
    path.replaceChildren();
    if (!on) { path.removeAttribute('style'); continue; }
    path.setAttribute('style', blobMorphStyle(shape));
    if (!BLOB_CSS_MORPH && !reducedMotion()) path.innerHTML = blobSmil(shape);
  }
}

// People: Workspace/Slack photo when we have one, otherwise the same initials circle as a bot with no art.
const personInitials = name => avInitials(String(name || '?').replace(/[^A-Za-z0-9 ]+/g, ' '));
const personCircle = (name, size = 22) => `<span class="av av-initials" style="width:${size}px;height:${size}px;--hue:${avHue(String(name || ''))};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${esc(personInitials(name))}</span>`;
const personPhotoUrl = p => p?.photo_url || p?.photo || '';
function personAvatar(person, size = 22) {
  const name = person?.name || person?.id || '';
  const url = personPhotoUrl(person);
  if (!url) return personCircle(name, size);
  return `<img class="av av-photo" src="${esc(url)}" alt="" data-name="${esc(name)}" style="width:${size}px;height:${size}px" decoding="async" aria-hidden="true">`;
}
document.addEventListener('error', ev => {
  const el = ev.target;
  if (!el || !el.classList?.contains('av-photo')) return;
  const size = parseFloat(el.style.width) || 22;
  el.outerHTML = personCircle(el.dataset.name || '?', size);
}, true);
const firstName = name => String(name || '').trim().split(/\s+/)[0] || '';
const personTitle = u => `${u.name || u.id || ''}${u.email ? ` · ${u.email}` : ''}${u.title ? ` · ${u.title}` : ''}`.trim();
// the people who own a bot, from the employees payload; nothing at all when the backend has none
const userChips = e => (e?.users || []).map(u =>
  `<span class="pchip" title="${esc(personTitle(u))}">${personAvatar(u, 15)}<span>${esc(firstName(u.name) || u.id)}</span></span>`).join('');

// A temp bot is work expected to end: its definition's `temp` flag, or the
// older "Temp X" name. Either way the name shows with a flag instead of the word.
const TEMP_RE = /^Temp\s+/;
const isTempBot = e => !!e?.temp || TEMP_RE.test(e?.display_name || '');
const shownName = e => isTempBot(e)
  ? `<span class="proj" title="temporary: expected to end">\u2691</span>${esc(e.display_name.replace(TEMP_RE, ''))}`
  : esc(e.display_name || '');
// The harness a bot runs on, as a small muted mark beside its name (ui/tool-icons.js). One rule: the
// harness (runtime), never the provider or model; the model rides in the tooltip. No runtime, nothing shown.
const RUNTIME_LABELS = {codex: 'Codex', claude: 'Claude Code', gemini: 'Gemini', antigravity: 'Antigravity', grok: 'Grok',
  cursor: 'Cursor', pi: 'OpenRouter', openrouter: 'OpenRouter', hermes: 'Hermes', openclaw: 'OpenClaw', grokbot: 'Grok Bot', 'grok-bot': 'Grok Bot', dots: 'Dots', muse: 'Muse'};
const RUNTIME_MARKS = {codex: 'openai', claude: 'anthropic', gemini: 'google', antigravity: 'google', grok: 'xai',
  grokbot: 'xai', 'grok-bot': 'xai', cursor: 'cursor', pi: 'openrouter', openrouter: 'openrouter'};
function runtimeTag(e) {
  const raw = String(e?.harness || e?.resolved_runtime || e?.runtime || '').trim().toLowerCase();
  if (!raw) return '';
  const label = RUNTIME_LABELS[raw] || raw.replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const model = e.resolved_model || e.model || '';
  const source = !model ? '' : e.model_source === 'company' ? ' (team default)' : e.model_source === 'bot' ? " (this bot's own choice)" : '';
  const mark = window.toolIcons?.markup({logo_key: RUNTIME_MARKS[raw], name: label}) || '';
  return `<span class="rt" role="img" aria-label="Runs on ${esc(label)}" title="${esc('Runs on ' + label + (model ? ' \u00b7 ' + model : '') + source)}">${mark}</span>`;
}
// A bot's `order` (its definition, Settings or registry) puts a pipeline in sequence without
// numbering the names: siblings with an order come first, lowest first; the rest keep A-Z.
// Renaming a bot never changes where it sits: the order field decides, the name does not.
const botOrder = e => { const n = Number(e?.order); return e?.order == null || e.order === '' || !Number.isFinite(n) ? Infinity : n; };
const byBotOrder = (a, b) => { const x = botOrder(a), y = botOrder(b); return x === y ? 0 : x < y ? -1 : 1; };
