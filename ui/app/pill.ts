/* ui/app/pill.ts — The composer pill at the foot of a bot's Chat tab
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- the pill (talk to a bot)
// The composer at the bottom of a bot's Chat tab: Send is a chat message, the caret offers task or reply.
// Each instance owns its own state.
const ICON_CLIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"/></svg>';
const ICON_STOP = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>';
const ICON_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>';
const clock = (ms: number | null | undefined) => { const t = Math.max(0, Math.round((ms || 0) / 1000)), h = Math.floor(t / 3600);
  return (h ? `${h}:` : '') + String(Math.floor(t % 3600 / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
// The composer's state. makePill builds it; the chat (chat.ts) and the other composers read and set these fields.
// The chat (chat.ts) and the thread composers pass their own composer objects here too: those carry the fields the
// pill helpers read, so the pill's own fields that only makePill sets are optional.
interface Pill {
  mode?: string | undefined;
  slug: string | null;
  dest?: string | null | undefined;
  action?: string | undefined;
  el?: Element | null | undefined;
  files: File[];
  send?: PillSend | null | undefined;
  sending?: boolean | undefined;
  sendRetrying?: boolean | undefined;
  sendNotice?: string | undefined;
  unconfirmed?: ChatUnconfirmed | null | undefined;
  goalMode?: boolean | undefined;
  // running/stopping are set by chatStopSync (chat.ts) while the bot runs.
  running?: { run_id?: string | null | undefined } | null | undefined;
  stopping?: boolean | undefined;
  releasePress?: (() => void) | undefined;
  pressTimer?: ReturnType<typeof setTimeout> | undefined;
  buttons?: (() => void) | undefined;
}
// What Send calls. Declared as a method so a page's own pill type (with its extra fields) can take it.
interface PillSender { send(P: Pill, text: string): unknown }
type PillSend = PillSender['send'];
interface PillConfig {
  mode: string;
  slug?: string | null | undefined;
  label?: string | undefined;
  placeholder?: string | undefined;
  send?: Pill['send'];
  files?: boolean | undefined;
}
const PILLS = new Set<Pill>();
let PILL_WHY_N = 0;
let BOT_PILL: Pill | null = null;
// What was typed to one bot is still there after visiting another and coming
// back. The text is kept per bot in this browser, so a reload keeps it too; attached files are
// kept for the session only. Sending clears it.
// Drafts and pending sends are the signed-in person's in this org (session.ts sessionIdentity): each record names
// whose it is, is read only once /me has said who is signed in (sessionReturn calls chatIdentityRestore), and a record
// of another person or org, or an older one that names no one, is dropped unread, so it never shows to them.
const CHAT_DRAFTS = 'hub.chat.drafts', CHAT_FILES = new Map<string, File[]>();
const CHAT_PENDING_KEY = 'hub.chat.pending';
// One saved record as it is stored: whose it is, and what.
interface ChatOwned { identity?: unknown; value?: unknown; }
function chatOwnedRead(storage: Storage, key: string): unknown {
  const identity = sessionIdentity();
  if (!identity) return null;                       // not known yet: nothing is read, and nothing is dropped
  try {
    const saved = JSON.parse(storage.getItem(key) || 'null') as ChatOwned | null;
    if (saved && saved.identity === identity) return saved.value;
    if (saved !== null) storage.removeItem(key);
  } catch { try { storage.removeItem(key); } catch {} }
  return null;
}
function chatOwnedWrite(storage: Storage, key: string, value: unknown, empty: boolean) {
  const identity = sessionIdentity();
  if (!identity) return;                            // no one signed in to keep it for
  try {
    if (empty) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify({identity, value}));
  } catch {}                                        // memory still works when storage is unavailable
}
const chatPendingRead = (): Map<string, ChatUnconfirmed> => {
  const saved = chatOwnedRead(sessionStorage, CHAT_PENDING_KEY);
  return new Map(Array.isArray(saved) ? saved.filter(([slug, u]: [string, ChatUnconfirmed]) =>
    typeof slug === 'string' && u && typeof u.text === 'string' && typeof u.signature === 'string'
    && typeof u.key === 'string' && u.key.length > 0 && u.key.length <= 200 && Number.isFinite(u.at)) : []);
};
const CHAT_PENDING = new Map<string, ChatUnconfirmed>();   // per tab, including reloads; no in-flight promises are persisted
function chatPendingSave() {
  chatOwnedWrite(sessionStorage, CHAT_PENDING_KEY, [...CHAT_PENDING].map(([slug, u]) =>
    [slug, {text: u.text, signature: u.signature, key: u.key, at: u.at}]), !CHAT_PENDING.size);
}
const chatDrafts = (): Record<string, string> => {
  const saved = chatOwnedRead(localStorage, CHAT_DRAFTS);
  return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved as Record<string, string> : {};
};
// Once /me has said who is signed in: this tab's pending sends are theirs alone, and another person's or org's saved
// drafts are dropped now, before any box is drawn.
function chatIdentityRestore() {
  CHAT_FILES.clear();
  CHAT_PENDING.clear();
  for (const [slug, u] of chatPendingRead()) CHAT_PENDING.set(slug, u);
  chatDrafts();
  if (typeof thvDraftTexts === 'function') thvDraftTexts();   // a bot's Home, DM or thread words (thread-view.ts)
}
function chatDraftSave(P: Pill) {
  if (P?.mode !== 'chat' || !P.slug) return;
  const text = pq(P, '.p-text')?.value || '', all = chatDrafts();
  if (text.trim()) all[P.slug] = text; else delete all[P.slug];
  if (P.files.length) CHAT_FILES.set(P.slug, P.files.slice()); else CHAT_FILES.delete(P.slug);
  chatOwnedWrite(localStorage, CHAT_DRAFTS, all, !Object.keys(all).length);
}
// The draft (and files) last left in this bot's box come back.
function pillRestore<T extends Pill>(P: T): T {
  // A pill with no slug never has a draft, files or pending send under a key, so '' reads the same as null.
  const slug = P.slug || '', draft = chatDrafts()[slug], files = CHAT_FILES.get(slug);
  if (typeof draft === 'string' && draft) pq(P, '.p-text').value = draft;
  if (files?.length) { P.files.push(...files); pillChips(P); }
  P.unconfirmed = CHAT_PENDING.get(slug) || null;
  P.unconfirmed?.inFlight?.then(() => pillButtons(P));
  if (draft || files?.length) pillButtons(P);
  return P;
}
const activeEmps = () => shownEmps().filter(e => e.status === 'active');
function toastSent(r: {pending?: boolean | undefined; bot?: string | undefined; slug?: string | undefined}) {
  const t = document.createElement('div'); t.className = 'toast';
  t.textContent = r.pending ? `Saved. Sending to ${botDisplayName(r.bot || r.slug)} in the background—you can close this page.` : `Sent to ${botDisplayName(r.bot || r.slug)}`;

  document.body.appendChild(t); setTimeout(() => t.remove(), 9000);
}

// ----------------------------------------------------------------- how a message reaches its bot
// One vocabulary for the bot chat and task comments (docs/tasks.md "Comments"): Steer goes into the bot's running turn
// now, Queue right after its current turn (now when idle), Defer waits for its next turn there, at the latest after a
// while. A way is kept as one string: 'steer', 'queue', 'goal' or 'defer:<for>'. Set as goal is an action, not a way.
const DELIVERY_FOR = ['30m', '1h', '4h', '24h', '2d', '7d', 'never'];
const deliveryWay = (mode: string | null | undefined): string => String(mode || '').split(':')[0] ?? '';
const deliveryFor = (mode: string | null | undefined): string => String(mode || '').split(':')[1] || '';
const deliveryForWord = (f: string) => f === 'never' ? 'Never' : f;
// A way's word from a table of words keyed by way (a way the table lacks has no word).
const wordOf = (table: Record<string, string>, way: string) => table[way];
const deliveryWord = (mode: string | null | undefined) => {
  const way = deliveryWay(mode), f = deliveryFor(mode);
  if (way === 'defer') return f && f !== 'never' ? `Defer ${f}` : 'Defer';
  return wordOf({steer: 'Steer', queue: 'Queue', goal: 'Set as goal'}, way) || 'Send';
};
// The request fields for a way: none for no pick; defer carries its duration.
const deliveryBody = (mode: string | null | undefined): {delivery?: string; defer_for?: string} => {
  const way = deliveryWay(mode);
  if (!way) return {};
  return way === 'defer' ? {delivery: way, defer_for: deliveryFor(mode) || 'never'} : {delivery: way};
};
// The menu: Steer now, Queue, Defer with its durations, then Set as goal apart. `current` is checked; `deferFor` is
// the duration the Defer row itself picks. Without `deadlines` (a reader who may not wake the bot) Defer has none.
function deliveryMenuHTML({steer = true, queue = true, goal = false, deadlines = true, current = 'queue', deferFor = '24h'}:
  {steer?: boolean; queue?: boolean; goal?: boolean; deadlines?: boolean; current?: string; deferFor?: string}) {
  const row = (val: string, label: string, on: boolean) => `<button type="button" role="menuitemradio" aria-checked="${on}" data-deliver="${esc(val)}"><span class="p-way-i">${SEND_WAY_ICONS[deliveryWay(val)] || ''}</span><span>${esc(label)}</span>${
    on ? '<span class="tick" aria-hidden="true">✓</span>' : ''}</button>`;
  const way = deliveryWay(current), f = way === 'defer' ? deliveryFor(current) : '';
  const chips = DELIVERY_FOR.map(d => `<button type="button" role="menuitemradio" aria-checked="${f === d}" aria-label="Defer ${esc(deliveryForWord(d))}" data-deliver="defer:${d}">${esc(deliveryForWord(d))}</button>`).join('');
  return (steer ? row('steer', 'Steer now', way === 'steer') : '') + (queue ? row('queue', 'Queue', way === 'queue') : '')
    + row('defer:' + (deadlines ? f || deferFor : 'never'), 'Defer', way === 'defer')
    + (deadlines ? `<div class="p-for" role="group" aria-label="Defer for">${chips}</div>` : '')
    + (goal ? `<div class="sep" role="separator"></div>${row('goal', 'Set as goal', way === 'goal')}` : '');
}
// Arrows move through the menu's buttons; the menu's own handlers do the rest.
function deliveryMenuKeys(menu: HTMLElement, ev: KeyboardEvent) {
  if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(ev.key)) return;
  const all = [...menu.querySelectorAll<HTMLButtonElement>('button')], focused = document.activeElement;
  const i = focused instanceof HTMLButtonElement ? all.indexOf(focused) : -1;
  if (i < 0) return;
  ev.preventDefault();
  all[(i + (ev.key === 'ArrowDown' || ev.key === 'ArrowRight' ? 1 : -1) + all.length) % all.length]?.focus();
}

// ----------------------------------------------------------------- the remembered way to send
// One Send whose icon is the way it sends: Steer the arrow, Queue the arrow over stacked lines, Defer a clock with its
// deadline, Set as goal the target. The way is the person's, kept on the server (preference compose.send_way: 'steer',
// 'queue' or 'defer:<for>'), so every composer and device starts from it. Set as goal is one send and never kept.
// The web sends the shown way every time; a caller that names none (API, CLI, MCP, Slack) gets the server's default.
const SEND_WAY_PREF = 'compose.send_way';
const sendWayOk = (mode: string | null | undefined) => mode === 'steer' || mode === 'queue'
  || typeof mode === 'string' && /^defer:[^:]+$/.test(mode) && DELIVERY_FOR.includes(deliveryFor(mode));
let SEND_WAY = 'steer', SEND_WAY_TIMER: ReturnType<typeof setTimeout> | null = null, SEND_WAY_PICKS = 0;     // picks made on this page, so a late read loses
const SEND_WAY_PAINT = new Set<() => void>();          // repaint hooks of the composers on screen that show the way
const svgIcon = (body: string) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const SEND_WAY_ICONS: Record<string, string> = {
  steer: ICON_SEND,
  queue: svgIcon('<path d="M4 6h8M4 18h8M4 12h14M14 8l4 4-4 4"/>'),
  defer: svgIcon('<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>'),
  goal: svgIcon('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".9" fill="currentColor"/>'),
};
// What Send says it will do, to a screen reader and on hover.
function sendWayLabel(mode: string | null | undefined) {
  const way = deliveryWay(mode), f = deliveryFor(mode);
  if (way === 'defer') return f && f !== 'never' ? `Send, defer ${f}` : 'Send, defer';
  return wordOf({queue: 'Send, queue', goal: 'Set as goal'}, way) || 'Send';
}
// Send's icon for `mode`, and a badge with Defer's deadline. The contents change only when the way does: a redraw
// between press and release loses the click.
function sendWayPaint(btn: UntypedElement, mode: string | null | undefined) {
  if (!btn) return;
  const way = deliveryWay(mode) || 'steer', f = way === 'defer' ? deliveryFor(mode) : '';
  const key = way + ':' + f;
  if (btn.dataset.shows !== key) {
    btn.innerHTML = (SEND_WAY_ICONS[way] || ICON_SEND) + (f && f !== 'never' ? `<span class="p-way-badge">${esc(f)}</span>` : '');
    btn.dataset.shows = key;
  }
  const label = sendWayLabel(mode);
  btn.dataset.word = label;
  if (!btn.disabled) { btn.setAttribute('aria-label', label); btn.title = label; }      // a busy Send says why instead
}
// A pick from a menu: kept for every composer, written to the server a moment later (several picks in a row are one
// write), and every composer on screen repaints.
function sendWayPick(mode: string) {
  if (!sendWayOk(mode)) return;
  const changed = mode !== SEND_WAY;
  SEND_WAY = mode; SEND_WAY_PICKS++;
  if (changed) for (const paint of SEND_WAY_PAINT) paint();
  if (typeof S === 'undefined' || !S.me?.cloud) return;
  clearTimeout(SEND_WAY_TIMER ?? undefined);
  SEND_WAY_TIMER = setTimeout(() => {
    SEND_WAY_TIMER = null;
    void post('/v2/preferences/' + SEND_WAY_PREF, {value: SEND_WAY}).catch(() => {});
  }, 600);
}
// The kept way, read once the page knows who is signed in. A pick made while the read was out wins, written or not.
async function sendWaySync() {
  if (typeof S === 'undefined' || !S.me?.cloud) return;
  const picks = SEND_WAY_PICKS;
  const v = (await v2Get('/v2/preferences/' + SEND_WAY_PREF))?.value;
  if (!sendWayOk(v) || SEND_WAY_PICKS !== picks || v === SEND_WAY) return;
  SEND_WAY = v;
  for (const paint of SEND_WAY_PAINT) paint();
}
// The menu also opens from Send itself: a right-click, or a long press on a touch screen. The click a long press ends
// in does not send. `can` says whether this composer has ways right now.
function sendWayHold(btn: HTMLButtonElement, open: () => void, can: () => boolean = () => true) {
  let timer = 0, held = 0;
  const cancel = () => { clearTimeout(timer); timer = 0; };
  btn.addEventListener('contextmenu', ev => { if (!can()) return; ev.preventDefault(); cancel(); open(); });
  btn.addEventListener('pointerdown', ev => {
    if (ev.pointerType === 'mouse' || btn.disabled || !can()) return;
    cancel();
    timer = setTimeout(() => { timer = 0; held = Date.now(); open(); }, 450);
  });
  for (const type of ['pointerup', 'pointercancel', 'pointerleave']) btn.addEventListener(type, cancel);
  btn.addEventListener('click', ev => {
    if (!held || Date.now() - held > 1500) return;
    held = 0;
    ev.preventDefault(); ev.stopImmediatePropagation();
  }, true);
}

// ----------------------------------------------------------------- "Who it reaches"
// The line under a thread's or a task's composer: who the message would reach and how (GET /v2/threads/{id}/reach).
// Posting never follows (D4); following lives on the task's Followers row and the Threads page, not under the box.
const COMPOSE_REACHES = new Set<ComposeReach>();       // the reach lines on screen
const composeFootHTML = () => `<div class="p-foot" hidden>
    <span class="p-reach-wrap"><button type="button" class="p-reach" aria-haspopup="dialog" aria-expanded="false" title="Who it reaches" hidden></button>
      <div class="p-menu p-reach-pop" role="dialog" aria-label="Who it reaches" hidden></div></span></div>`;
// The line shows only with something on it.
const composeFootShow = (foot: UntypedElement) => { foot.hidden = foot.querySelector('.p-reach').hidden; };

// What each one gets, in a word or two: a bot by the way, a person notified (threads.plan).
function reachWord(r: import('../types/api').ThreadReach['reaches'][number]) {
  if (r.mode === 'digest') return 'Digest';
  if (!String(r.actor).startsWith('bot:')) return 'Notified';
  if (r.mode === 'steer') return 'Now';
  if (r.mode === 'queue') return 'After its turn';
  if (r.mode === 'defer') return r.defer_for && r.defer_for !== 'never' ? `Next turn, within ${r.defer_for}` : 'Next turn';
  return String(r.mode || '');
}
// The server reads only the @mentions in the words, so only the bit after each @ goes: typing elsewhere asks nothing,
// and a long message never makes a long URL.
function reachMentions(text: string | null | undefined) {
  const t = String(text || ''), out: string[] = [];
  for (let i = t.indexOf('@'); i >= 0 && out.length < 20; i = t.indexOf('@', i + 1)) out.push(t.slice(i, i + 64));
  return out.join('\n');
}
// One composer's reach line. `src` gives the thread id, the way (a send_way mode, '' for the server's Steer, 'goal'
// for no message), the words and the message replied to; `update()` after any of them changes: a short wait, then one
// read at a time.
// The reach read as the server sends it: each line may also name its actor (actor_name), which the generated type leaves out.
type PillReach = Omit<import('../types/api').ThreadReach, 'reaches'> & {reaches: (import('../types/api').ThreadReach['reaches'][number] & {actor_name?: string | null | undefined})[]};
interface ComposeReach {
  foot: UntypedElement; src: {thread(): string | null | undefined; mode(): string | null | undefined; text(): string | null | undefined; reply?(): string | null | undefined};
  want: string; got: string; busy: boolean; timer: number; data: PillReach | null; html: string;
  update(): void;
}
function composeReach(foot: UntypedElement, src: ComposeReach['src']): ComposeReach {
  const R = {foot, src, want: '', got: '', busy: false, timer: 0, data: null, html: ''} as ComposeReach;
  const btn = foot.querySelector('.p-reach'), pop = foot.querySelector('.p-reach-pop');
  btn.onclick = () => composeReachOpen(R, pop.hidden);
  foot.querySelector('.p-reach-wrap').addEventListener('keydown', (ev: KeyboardEvent) => {
    if (ev.key !== 'Escape' || pop.hidden) return;
    ev.preventDefault(); ev.stopPropagation();
    composeReachOpen(R, false); btn.focus();
  }, true);
  R.update = () => composeReachUpdate(R);
  COMPOSE_REACHES.add(R);
  return R;
}
// A press outside an open list closes it.
document.addEventListener('pointerdown', ev => {
  for (const R of COMPOSE_REACHES) {
    if (!R.foot.isConnected) { COMPOSE_REACHES.delete(R); continue; }
    if (!R.foot.querySelector('.p-reach-pop').hidden && !R.foot.querySelector('.p-reach-wrap').contains(ev.target)) composeReachOpen(R, false);
  }
});
function composeReachKey(R: ComposeReach) {
  const id = R.src.thread(), mode = R.src.mode() || '';
  if (!id || mode === 'goal') return '';
  return JSON.stringify([id, mode, reachMentions(R.src.text()), R.src.reply?.() || '']);
}
function composeReachUpdate(R: ComposeReach) {
  const key = composeReachKey(R);
  if (key === R.want) return;
  R.want = key;
  clearTimeout(R.timer);
  if (!key) { R.data = null; R.got = ''; composeReachPaint(R); return; }
  R.timer = setTimeout(() => void composeReachRead(R), R.got ? 350 : 0);
}
async function composeReachRead(R: ComposeReach) {
  if (R.busy || !R.want || R.want === R.got) return;
  R.busy = true;
  const key = R.want, [id, mode, text, reply] = JSON.parse(key), way = deliveryBody(mode);
  const q = new URLSearchParams();
  if (text) q.set('text', text);
  if (way.delivery) q.set('delivery', way.delivery);
  if (way.defer_for) q.set('defer_for', way.defer_for);
  if (reply) q.set('in_reply_to', reply);
  let data: PillReach | null = null;
  try { data = await get<PillReach>(`/v2/threads/${encodeURIComponent(id)}/reach${q.size ? '?' + q : ''}`, {fresh: true}); } catch {}
  R.busy = false;
  if (!R.foot.isConnected) { COMPOSE_REACHES.delete(R); return; }
  R.got = key;
  if (key !== R.want) { void composeReachRead(R); return; }     // the words or the way changed while it was out
  R.data = Array.isArray(data?.reaches) ? data : null;
  composeReachPaint(R);
}
// The reach line names who gets the message (spec: "lists the followers and addressed by name"): "To Omar and Builder 2",
// "To Omar, Ana +3". The list opened from it adds each one's way.
function composeReachSays(d: PillReach) {
  const names = d.reaches.map(r => r.actor_name || actorName(r.actor));
  if (!names.length) return 'No one gets this';
  if (names.length <= 2) return 'To ' + names.join(' and ');
  return `To ${names.slice(0, 2).join(', ')} +${names.length - 2}`;
}
function composeReachRowHTML(actor: string, word: string) {
  return `<div class="p-reach-row" role="listitem">${actorAvatar(actor, 18)}<span class="p-reach-name">${esc(actorName(actor))}</span><span class="p-reach-mode">${esc(word)}</span></div>`;
}
function composeReachPaint(R: ComposeReach) {
  const btn = R.foot.querySelector('.p-reach'), pop = R.foot.querySelector('.p-reach-pop'), d = R.data;
  // A bot's Home reaching only that bot says what the composer's "Message <bot>" already says: nothing to show.
  const onlyHome = !!d && d.reaches.length === 1 && d.reaches[0]?.reason === 'home' && !(d.outside || []).length;
  btn.hidden = !d || onlyHome;
  composeFootShow(R.foot);
  if (!d || onlyHome) { composeReachOpen(R, false); return; }
  const says = composeReachSays(d);
  if (btn.textContent !== says) btn.textContent = says;
  const rows = [...d.reaches.map(r => composeReachRowHTML(r.actor, reachWord(r) + (r.adds_reader ? ', can now see it' : ''))),
    ...(d.outside || []).map(a => composeReachRowHTML(a, 'Not reached'))];
  const html = rows.length ? `<div role="list">${rows.join('')}</div>` : '<p class="p-reach-none">No one gets this</p>';
  if (R.html !== html) { pop.innerHTML = html; R.html = html; }
}
function composeReachOpen(R: ComposeReach, open: boolean) {
  const btn = R.foot.querySelector('.p-reach'), pop = R.foot.querySelector('.p-reach-pop');
  if (!open && pop.hidden) return;
  pop.hidden = !open || btn.hidden;
  btn.setAttribute('aria-expanded', String(!pop.hidden));
}

// A phone or tablet typing on its screen keyboard: a coarse pointer, or touch on a narrow screen.
const touchKeyboard = () => matchMedia('(pointer: coarse)').matches || (navigator.maxTouchPoints > 0 && innerWidth <= 760);
// A chat pill is the chat's composer, which chat.ts types as ChatComposer: the chat's own helpers take it that way.
// A pill with no slug (the Assistant's, the ask panel's) never reaches the chat's send or its replay check.
const asChat = (P: Pill): ChatComposer => P as ChatComposer;
// cfg.send replaces where Send goes (the Assistant page posts to /v2/assistant/messages); cfg.files === false
// leaves out attaching, for a chat whose server route takes text only.
// Fit a composer's box to its words, up to its CSS max-height (meetings.css .p-pill .p-text: eight lines), the one cap
// every composer reads (#298); past it the box scrolls inside, the caret's line kept in view. `keep` skips the collapse
// to height:auto, which re-measures a box that only grew (see makePill's grow for why iOS needs that).
function pillGrow(box: HTMLTextAreaElement, keep = false) {
  if (!keep) box.style.height = 'auto';
  const cap = parseFloat(getComputedStyle(box).maxHeight) || Infinity;
  box.style.height = Math.min(box.scrollHeight, cap) + 'px';
  if (box.scrollHeight > box.clientHeight && box.selectionEnd === box.value.length) box.scrollTop = box.scrollHeight;
}
function makePill(cfg: PillConfig): Pill {
  const P: Pill = {
    mode: cfg.mode, slug: cfg.slug || null, dest: cfg.slug,
    action: 'chat',
    el: null, files: [], send: cfg.send || null,
  };
  const attach = cfg.files !== false;
  const el = P.el = document.createElement('div');
  el.className = 'ask pill-' + P.mode;
  el.innerHTML = `
    <div class="p-pill">
      ${P.mode === 'chat' && P.slug ? pillBotHTML(P.slug) : ''}
      <textarea class="p-text" rows="1" autocorrect="on" autocapitalize="sentences" spellcheck="true" aria-label="${esc(cfg.label || 'Message this bot')}" placeholder="${esc(cfg.placeholder || 'Type a message…')}"></textarea>
      ${attach ? `<button class="p-attach p-icon" type="button" title="Attach files" aria-label="Attach files">${ICON_CLIP}</button>
      <input type="file" multiple hidden class="p-file">` : ''}
      <button class="p-send p-send-icon" type="button" aria-label="Send" title="Send">${ICON_SEND}</button>
    </div>
    <div class="p-slow muted" role="status" hidden></div>
    <div class="p-why muted" id="p-why-${++PILL_WHY_N}" role="status"></div>
    <div class="p-chips"></div>
    <div class="p-cmd" hidden></div>`;

  const q = (sel: string): UntypedElement => el.querySelector(sel);
  const box = q('.p-text');
  // IPhone autocorrect never landed in the composer. Collapsing the box to
  // height:auto on every keystroke re-lays it out mid-word, which drops iOS's pending correction.
  // Grow only when the text overflows; collapse and re-measure only when text was removed.
  let lastLength = 0;
  const grow = () => {
    const shrank = box.value.length < lastLength; lastLength = box.value.length;
    if (shrank || box.scrollHeight > box.clientHeight) pillGrow(box, !shrank);
  };
  box.addEventListener('input', () => { P.sendNotice = ''; grow(); pillButtons(P); chatDraftSave(P); if (P.unconfirmed) chatForgetUnconfirmed(asChat(P)); });
  // "lets do the triple return behavior on mobile send, but on desktop, lets
  // make it enter send, and shift enter new line." On a computer Return sends and Shift+Return is
  // a new line; on a phone Return is a line and three in a row send (below). Cmd/Ctrl+Return and
  // the arrow always send.
  box.addEventListener('keydown', (ev: KeyboardEvent) => {
    if (ev.key !== 'Enter' || ev.isComposing) return;
    const send = ev.metaKey || ev.ctrlKey || (!ev.shiftKey && !touchKeyboard());
    if (send) { ev.preventDefault(); pillSend(P); }
  });
  // "if i press enter 3 times, then send it. if i press 1 or twice, page break."
  // Counted on beforeinput, not keydown, so a phone keyboard's Return counts too; the two line
  // breaks the first presses made come off before it sends.
  let breaks = 0;
  box.addEventListener('beforeinput', (ev: InputEvent) => {
    if (P.mode !== 'chat' || !touchKeyboard()) return;
    if (ev.inputType !== 'insertLineBreak' && ev.inputType !== 'insertParagraph') { breaks = 0; return; }
    const at = box.selectionStart;
    if (++breaks < 3 || at !== box.selectionEnd || box.value.slice(at - 2, at) !== '\n\n') return;
    ev.preventDefault(); breaks = 0;
    box.value = box.value.slice(0, at - 2) + box.value.slice(at);
    box.setSelectionRange(at - 2, at - 2);
    pillSend(P);
  });
  box.addEventListener('pointerdown', () => { breaks = 0; });
  if (attach) q('.p-attach').onclick = () => q('.p-file').click();
  if (P.slug && q('.p-bot')) pillBotWire(q('.p-bot'), P.slug);
  if (attach) q('.p-file').onchange = (ev: Event & {target: HTMLInputElement}) => { P.files.push(...(ev.target.files ?? [])); pillChips(P); chatDraftSave(P); ev.target.value = ''; };
  if (attach) pillPasteWire(box, files => { P.files.push(...files); pillChips(P); chatDraftSave(P); });
  q('.p-chips').onclick = (ev: MouseEvent & {target: HTMLElement}) => {
    const thumb = ev.target.closest<HTMLElement>('.p-thumb'); if (thumb) { ev.preventDefault(); chatImageOpen(thumb); return; }
    const i = ev.target.dataset.rm;
    if (i != null) {
      ev.preventDefault();
      pillPreviewForget(P.files.splice(+i, 1)[0]);
      pillChips(P);
    }
  };
  q('.p-send').onclick = () => pillSend(P);
  // Acknowledge the physical press before validation or any request. Keep the
  // real button as the target, including its transparent border, and retain focus.
  el.querySelectorAll<HTMLButtonElement>('.p-send, .p-stop').forEach(btn => btn.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const started = performance.now();
    P.releasePress?.();
    btn.classList.add('p-pressed');
    const release = (event?: Event) => {
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', release);
      window.removeEventListener('blur', release);
      clearTimeout(P.pressTimer);
      const clear = () => btn.classList.remove('p-pressed');
      if (event?.type === 'pointerup') P.pressTimer = setTimeout(clear, Math.max(0, 120 - (performance.now() - started)));
      else clear();
    };
    P.releasePress = release;
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    window.addEventListener('blur', release);
  }));
  // Safari does not focus buttons on tap. Keep the composer in place until its
  // control click runs; restoring navigation on blur can swallow that tap.
  // Delegate so attachment-removal controls added after selection work too.
  el.addEventListener('pointerdown', event => {
    if (document.activeElement === box && event.target instanceof Element && event.target.closest('button,[data-rm]'))
      event.preventDefault();
  });
  PILLS.add(P);
  pillLabel(P); pillButtons(P);
  return P;
}
const pq = (P: Pill, sel: string): UntypedElement => P.el?.querySelector(sel);
// The bot being talked to, left of the bar on a phone (ui/styles/chat.css): a tap opens the switcher over the bar
// (ui/app/org-fan.ts), which keeps this button's aria-expanded in step. Used by this pill and a bot's Home and DM
// composer (ui/app/thread-view.ts).
const pillBotHTML = (slug: string) => `<button class="p-bot" type="button" aria-label="Switch bot" aria-haspopup="menu" aria-expanded="false" aria-controls="org-fan" title="Switch bot">${avatar(slug, 30)}</button>`;
function pillBotWire(btn: UntypedElement, slug: string) {
  btn.onclick = (ev: MouseEvent) => { ev.preventDefault(); $('#org-fan').hidden ? orgFanOpen({chat: slug, anchor: btn}) : orgFanClose(); };
}
// An empty box only dims Send: text that arrived without an input event (autofill, dictation, a tool) must still
// send on a click, and pillSend puts the cursor back in an empty box.
function pillButtons(P: Pill) {
  const box = pq(P, '.p-text'); if (!box) return;
  chatForgetUnconfirmed(asChat(P));
  const fileChat = P.files.length && P.action === 'chat';
  const btn = pq(P, '.p-send');
  btn.classList.toggle('p-off', !box.value.trim() && !fileChat);
  btn.disabled = !!P.sending || !!P.unconfirmed?.inFlight || P.action === 'chat' && !P.send && !pillGoalMode(P) && chatReplayExpired(asChat(P));
  // While the bot runs (chatStopSync sets P.running), Stop takes Send's place until something is typed: a message
  // typed meanwhile still queues for after the run.
  const stop = pq(P, '.p-stop');
  if (stop) {
    const show = !!P.running && !box.value.trim() && !P.files.length;
    stop.hidden = !show; btn.hidden = show; stop.disabled = !!P.stopping;
  }
  pillWhy(P);
}
// Ask the bot's running turn to stop; the button stays pressed until the run ends. `P` is this pill or a composer that
// redraws its own buttons (P.buttons: a bot's Home or DM, ui/app/thread-view.ts).
async function pillStop(P: Pill) {
  if (!P.running || P.stopping) return;
  const redraw = () => P.buttons ? P.buttons() : pillButtons(P);
  P.stopping = true; redraw();
  try { await post(`/v2/bots/${encodeURIComponent(String(P.slug))}/stop`, P.running.run_id ? {run_id: P.running.run_id} : {}); }
  catch (e) { P.stopping = false; redraw(); toast(`Not stopped: ${errorMessage(e, String(e))}`, true); }
}
// "when i paste an image into a box, can we show a clickable preview of it?"
// An image waiting to be sent shows as a small thumbnail; a tap opens it full size. Shared by this pill and a bot's
// Home and DM composer (ui/app/thread-view.ts thvChips), which is where the bot chat lives now (#138).
const PILL_PREVIEWS = new WeakMap<File, string>();
const pillPreview = (f: File) => {
  if (!/^image\//.test(f.type || '')) return '';
  if (!PILL_PREVIEWS.has(f)) PILL_PREVIEWS.set(f, URL.createObjectURL(f));
  return PILL_PREVIEWS.get(f);
};
function pillPreviewForget(f: File | undefined) {
  if (f && PILL_PREVIEWS.has(f)) { URL.revokeObjectURL(PILL_PREVIEWS.get(f)!); PILL_PREVIEWS.delete(f); }
}
// One chip per waiting file: an image as its thumbnail (a button chatImageOpen opens), any other file as a file icon
// and its name; × carries `rm` (the composer's own key for the file) in data-rm.
function pillChipHTML(f: File, rm: string) {
  const src = pillPreview(f), name = f.name || 'file';
  const lead = src ? `<button class="p-thumb" type="button" data-name="${esc(name)}" title="Open ${esc(name)}" aria-label="Open ${esc(name)}"><img src="${esc(src)}" alt=""></button>`
    : '<span class="p-fileicon nav-icon" aria-hidden="true">description</span>';
  return `<span class="${src ? 'has-thumb' : 'has-icon'}">${lead}<span class="p-chipname">${esc(name)}</span> <a href="#" data-rm="${esc(rm)}" aria-label="Remove ${esc(name)}">×</a></span>`;
}
// A file the clipboard or a restored draft hands over with no type takes one from its name, so an image still shows.
const PILL_TYPES: Record<string, string> = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
  bmp: 'image/bmp', svg: 'image/svg+xml', heic: 'image/heic', tif: 'image/tiff', tiff: 'image/tiff', pdf: 'application/pdf'};
function pillTyped(f: File): File {
  if (f.type) return f;
  const type = PILL_TYPES[(f.name.split('.').pop() || '').toLowerCase()];
  return type ? new File([f], f.name, {type, lastModified: f.lastModified}) : f;
}
// The files one paste carries, once each (#138): the clipboard's file list, or else its file items. A browser can list
// the same screenshot in both, or twice among the items, and one paste must still give one chip.
function pastedFiles(clipboard: DataTransfer | null | undefined): File[] {
  if (!clipboard) return [];
  let files = Array.from(clipboard.files || []);
  if (!files.length) files = Array.from(clipboard.items || []).filter(item => item.kind === 'file')
    .map(item => item.getAsFile()).filter((f): f is File => f !== null);
  // Two different files copied together with the same name, size and type would also merge; rare enough to accept.
  const seen = new Set<string>();
  return files.map(pillTyped).filter(f => {
    const key = `${f.name}\n${f.size}\n${f.type}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
// A paste into a composer's box: files become chips (`take`), and the clipboard's text, if any, goes in at the cursor
// once. A paste with no file is the browser's own, untouched. A file copied in a file manager also brings its name as
// text; that name is not typed into the message.
function pillPasteWire(box: HTMLTextAreaElement, take: (files: File[]) => void) {
  box.addEventListener('paste', (ev: ClipboardEvent) => {
    const files = pastedFiles(ev.clipboardData);
    if (!files.length) return;
    ev.preventDefault();
    const text = ev.clipboardData?.getData('text/plain') || '';
    const names = new Set(files.map(f => f.name));
    const lines = text.split(/\r?\n/).map(line => line.trim().split(/[\\/]/).pop()).filter(Boolean);
    if (text && !(lines.length && lines.every(line => names.has(line!)))) {
      box.setRangeText(text, box.selectionStart, box.selectionEnd, 'end');
      box.dispatchEvent(new Event('input', {bubbles: true}));
    }
    take(files);
  });
}
function pillChips(P: Pill) {
  P.sendNotice = '';
  const el = pq(P, '.p-chips'); if (!el) return;
  el.innerHTML = P.files.map((f, i) => pillChipHTML(f, String(i))).join('');
  pillLabel(P); pillButtons(P);
}
// The chat composer's send is an arrow: a busy word goes to its label, never over the icon. A busy word said while
// the button is disabled stays until it is enabled again, so pillLabel's action word never covers it.
const pillBtnSay = (btn: UntypedElement, text?: string | null) => {
  if (!btn) return;
  if (btn.disabled && text) btn.dataset.busy = text; else delete btn.dataset.busy;
  if (btn.classList.contains('p-send-icon')) { btn.setAttribute('aria-label', text || 'Send'); btn.title = (text && PILL_BUSY[text]) || text || 'Send'; }
  else btn.textContent = text;
  // Every busy start (a send, a goal save, a task) goes through here, so the reason and the phone line come at once.
  for (const P of PILLS) if (P.el?.contains(btn)) pillWhy(P);
};
// What a disabled (or, for an empty box, dimmed) Send or Stop says on hover and to a screen reader, in plain words.
// On a phone, with no hover, the busy reasons show as a line under the box (ui/styles/chat.css).
const PILL_BUSY: Record<string, string> = {'Sending…': 'Sending… waiting for Tico to answer'};
function pillWhy(P: Pill) {
  const btn = pq(P, '.p-send'), why = pq(P, '.p-why'); if (!btn || !why) return;
  const busy = btn.disabled && (btn.dataset.busy || (P.sending || P.unconfirmed?.inFlight ? 'Sending…' : ''));
  const stop = pq(P, '.p-stop'), stopping = stop && !stop.hidden && stop.disabled;
  let text = '';
  if (stopping) text = 'Stopping…';
  else if (busy) text = busy === 'Sending…' && P.sendRetrying ? 'Sending… trying again' : PILL_BUSY[busy] || busy;
  else if (btn.disabled && chatReplayExpired(asChat(P))) text = CHAT_REPLAY_EXPIRED;
  else if (btn.classList.contains('p-off'))
    text = pillGoalMode(P) ? 'Type a goal to set' : P.action === 'chat' ? 'Type a message or attach a file to send' : 'Type a message to send';
  if (P.sendNotice && !busy && !stopping) text = P.sendNotice;
  why.textContent = text;
  why.classList.toggle('p-why-notice', !!P.sendNotice && !busy && !stopping);
  why.classList.toggle('p-why-busy', !!(stopping || busy || btn.disabled && chatReplayExpired(asChat(P))));
  if (stop) {
    stop.title = stopping ? text : 'Stop';
    if (stopping) stop.setAttribute('aria-describedby', why.id); else stop.removeAttribute('aria-describedby');
  }
  if (text && !stopping) { btn.title = text; btn.setAttribute('aria-describedby', why.id); }
  else { btn.title = btn.dataset.word || 'Send'; btn.removeAttribute('aria-describedby'); }
}
// Goal mode (ui/app/chat-goal.ts) turns Send into Set goal. Attached files always go as a message, so they are never lost.
const pillGoalMode = (P: Pill) => !!P.goalMode && P.action === 'chat' && !P.send && !P.files.length;
// The label always says what Send will do; in goal mode it says so in words, and so does the empty box.
function pillLabel(P: Pill) {
  const btn = pq(P, '.p-send'); if (!btn) return;
  const goal = pillGoalMode(P);
  const word = goal ? 'Set goal' : P.action === 'task' ? 'Send as a task' : 'Send';
  // A redraw that replaces the arrow between press and release loses the click in every engine, so the
  // contents change only when goal mode swaps the arrow for words (and ui/styles/chat.css makes the button itself
  // the press target).
  const shows = goal ? word : 'icon';
  if (btn.dataset.shows !== shows) { btn.innerHTML = goal ? esc(word) : ICON_SEND; btn.dataset.shows = shows; }
  btn.dataset.word = word;
  btn.classList.toggle('p-send-goal', goal);
  btn.setAttribute('aria-label', btn.disabled && btn.dataset.busy || word);
  pillWhy(P);
  const box = pq(P, '.p-text');
  if (box) {
    box.dataset.placeholder ??= box.placeholder;
    box.placeholder = goal ? `Goal for ${botDisplayName(P.slug)}…` : box.dataset.placeholder;
    // The placeholder names where the message goes; the accessible name says the same, so the two never disagree.
    if (box.placeholder) box.setAttribute('aria-label', box.placeholder.replace(/…$/, ''));
  }
}
function pillMenu(P: Pill, open: boolean) {
  const menu = pq(P, '.p-menu'); if (!menu) return;
  pq(P, '.p-caret')?.setAttribute('aria-expanded', String(!!open));
  menu.hidden = !open;
  if (!open) return;
  const row = (val: string, label: string, art: string, on: boolean) => `<button type="button" role="menuitemradio" data-val="${esc(val)}" aria-checked="${on}">${art}<span>${label}</span>${on ? '<span class="tick" aria-hidden="true">✓</span>' : ''}</button>`;
  menu.innerHTML = row('chat', 'Send <span class="muted">(chat)</span>', '<span class="auto" aria-hidden="true">✦</span>', P.action === 'chat')
      + row('task', 'Send as task', '<span class="auto" aria-hidden="true">＋</span>', P.action === 'task');
  menu.querySelectorAll('[data-val]').forEach((b: HTMLElement) => b.onclick = () => {
    P.action = b.dataset.val ?? '';
    pillLabel(P); pillButtons(P); pillMenu(P, false); pq(P, '.p-text').focus();
  });
}
function pillAcknowledge(P: Pill, text: string, files: File[], scope?: string) {
  sessionAcknowledge(pq(P, '.p-text'), scope);
  // A slow upload must not erase the person's next draft or newly selected files.
  const nextText = pq(P, '.p-text').value;
  const remaining = P.files.filter(file => !files.includes(file));
  pillClear(P);
  if (nextText.trim() !== text) {
    const box = pq(P, '.p-text');
    box.value = nextText; box.dispatchEvent(new Event('input', {bubbles: true}));
  }
  P.files.push(...remaining); pillChips(P); pillButtons(P); chatDraftSave(P);
}
function pillClear(P: Pill, keepText?: boolean) {
  const box = pq(P, '.p-text');
  box.value = ''; box.style.height = 'auto';
  P.files.length = 0; pillChips(P); chatDraftSave(P);
  if (keepText) pillButtons(P);
}
async function pillSend(P: Pill) {
  if (!P.el) return;
  const box = pq(P, '.p-text'), text = box.value.trim();
  if (!text && !(P.action === 'chat' && P.files.length)) {
    P.sendNotice = pillGoalMode(P) ? 'Type a goal to set' : P.action === 'chat' ? 'Type a message or attach a file to send' : 'Type a message to send';
    pillWhy(P); box.focus(); return;
  }
  P.sendNotice = ''; pillWhy(P);
  // Goal mode keeps the familiar Send button and Enter shortcut; only the destination changes.
  // The Assistant's custom sender and explicit task/reply actions retain their own contracts.
  if (pillGoalMode(P)) return chatGoalSend(P, text);
  if (P.send) return P.send(P, text);
  const slash = slashRun(P, text);
  if (slash === true) return;
  if (slash !== 'plain' && runCommand(text, pq(P, '.p-cmd'), P.slug)) {
    sessionAcknowledge(box); pillClear(P, true); return;
  }
  return P.action === 'task' ? v2PillTask(P, text) : v2ChatSend(asChat(P), text);
}
