/* ui/app/ask-panel.ts — Ask: the person's own chat bot, a tap away on every page
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Ask (docs/assistant.md). The one way in to the Assistant ("Tico Assistant" here), to the helpers behind it (BotOps,
// the Strategy Bot, the Librarian). The Tico team has its own chat (ui/support.ts), opened by the ? beside Search. On a computer the "Tico Assistant" row at the bottom of the sidebar
// (or ⌘J) opens a panel beside the rail, rising from the bottom left; on a phone the Liquid Dot in the middle of the tab bar opens it as a sheet over
// the page, and closes it again. It is the same conversation as the Assistant page (#/assistant): GET /v2/assistant
// reads it and POST /v2/assistant/messages sends with the page the person is on (`page`), so the turn knows what "this"
// means. It keeps its own small state, never the bot pages' (chat.js V2C), and drops the
// Assistant page's cached copy when it sends.
// What waits on the person from anyone else sits in the thread by when it was asked, like every other line: a Confirm
// card a bot such as BotOps left in its own chat (GET /v2/assistant `pending`), and bots' questions (Needs you),
// answered here or by telling the Assistant.

// The state of the panel's conversation (ASK), its thread and composer, as the functions below read and write them.
interface AskChat {
  open?: boolean;
  mounted?: boolean;
  dotState?: string;
  draw?: () => void;
  refresh?: () => Promise<void>;
  send?: (text: string) => Promise<void>;
  stop?: () => void;
  draft?: string;
  composer?: Pill;
  live?: boolean;
  drawn?: boolean;
}
// What GET /v2/assistant answers for the panel: the conversation, its run and the Confirm cards waiting elsewhere.
interface AskPending {
  id: string;
  created_at?: string | null;
}
interface AskInfo {
  room_id?: string | null;
  bot?: string;
  name?: string;
  available?: boolean;
  can_turn_on?: boolean;
  messages?: ChatMessage[];
  execution?: AskExecution | null;
  pending?: AskPending[];
}
// A turn's run as the panel reads it: `text` is the reply so far, while it streams.
interface AskExecution {
  text?: string;
}
// A bot's question to this person (Needs you), and the body of its ask.
interface AskBody {
  sender_id: string;
  recipient_id: string;
  body?: string;
  created_at?: string;
  refs?: { questions?: unknown[] | null; [key: string]: unknown } | null;
}
// Needs you: a bot's question carries its ask here; the panel reads these fields of each item.
interface NeedsItem {
  ask?: AskBody;
  created_at?: string;
}
// A question as the question card reads it (qcardQuestion), with its options.
interface AskQuestion {
  id?: unknown;
  question: string;
  options: { label: string; recommended?: boolean }[];
  multi?: boolean;
}
// The reply forms: a choice of options has a named select, a free answer a named text box.
interface AskChoiceForm extends HTMLFormElement {
  readonly elements: HTMLFormControlsCollection & { pick: HTMLSelectElement };
}
interface AskReplyForm extends HTMLFormElement {
  readonly elements: HTMLFormControlsCollection & { text: HTMLInputElement };
}
// One line of the thread: when it was asked (or sent), and its markup.
interface AskRow {
  at?: string | null | undefined;
  html: string;
}
interface Element {
  _ldotFly?: { cancel(): void };
}

const ASK_MANAGE = "We'd like Tico to fully manage our company.";
let ASK: AskChat | null = null;
const ASK_CARDS_MAX = 3;
// The panel's name: "Tico Assistant", or the name a team gave its assistant.
const askName = () => myChatName() === CONFIG_DEFAULTS.assistant_name ? 'Tico Assistant' : myChatName();
const askPhone = () => matchMedia('(max-width:760px)').matches;
// The page the person is looking at: its heading, else the address.
function askPage() {
  const path = S.route || location.hash || '';
  const heading = $('#main h1, #main h2')?.textContent.trim().replace(/\s+/g, ' ') || '';
  return {path: path.slice(0, 300), title: heading.slice(0, 200)};
}
// The Assistant page is this chat already, and setup has no room for it.
const askAway = () => !S.me || S.route === ASSISTANT || document.documentElement.classList.contains('in-setup');
// Open or closed is a class, never `hidden`: the app's `:has(… :not([hidden]))` rules make any change of a `hidden`
// attribute restyle the whole page (6-30 ms on a real hub), and a panel shown from display:none lays itself out cold
// (11-45 ms). Closed, the panel keeps its box but draws nothing, is inert and is out of the accessibility tree
// (ask.css). Nothing else on the way in or out writes a `hidden` (or text) it does not change (askSet); what shows
// and goes with it (the page it sees) does so by a class, `ask-on` (askOn).
const askShown = () => !!$('#ask-panel')?.classList.contains('ask-shown');
const askSet = <T extends object, K extends keyof T>(el: T | null | undefined, key: K, value: T[K]) => { if (el && el[key] !== value) el[key] = value; };
const askOn = (el: Element | null | undefined, on: unknown) => { on = !!on; if (el && el.classList.contains('ask-on') !== on) el.classList.toggle('ask-on', !!on); };
// The row is there from the first paint (index.html); a click before /me answers opens Ask once the first page is
// drawn (router.js calls askSync), and boot that never gets a person hides the row (boot.js).
let ASK_EARLY = false;
function askSync() {
  const row = $('#ask-row'), tab = $('#mobile-ask'), panel = $('#ask-panel');
  if (!row || !panel) return;
  if (ASK_EARLY && S.me) { ASK_EARLY = false; if (!askAway()) { askToggle(true); return; } }
  const name = askName(), away = askAway(), open = !!ASK?.open && !away;
  askSet(row, 'hidden', away);
  document.documentElement.classList.toggle('ask-away', away);   // ui/theme.ts set it for the first paint
  if (askShown() !== open) { panel.classList.toggle('ask-shown', open); panel.inert = !open; }
  document.body.classList.toggle('ask-open', open);
  askSet(row, 'title', name + ' (⌘J)');
  // While the panel is open a close mark holds the row's (and the phone tab's) dot place (ask.css).
  for (const el of [row, tab].filter(Boolean)) {
    el.setAttribute('aria-expanded', String(open));
    if (open) el.setAttribute('aria-label', 'Close Tico Assistant'); else el.removeAttribute('aria-label');
  }
  for (const el of document.querySelectorAll('[data-ask-dot]')) ldotWatch(el);
  askDot();
  if (panel.getAttribute('aria-label') !== name) panel.setAttribute('aria-label', name);
  askSet(panel.querySelector('#ask-title'), 'textContent', name);
  if (!open) return;
  const page = askPage(), sees = panel.querySelector('[data-ask-sees]');
  askOn(sees, page.title);
  askSet(sees.querySelector('[data-ask-sees-name]'), 'textContent', page.title);
  askSet(sees, 'title', 'Sees ' + page.title);
}
// The Liquid Dot (app/liquid-dot.js) is Ask's mark. One live dot per screen: the row's (or the phone tab's) while the
// panel is closed, the panel's own beside its name while it is open (Tico's turns carry none); the rest hold still.
// With reduced motion the dot is still and the closed row says the state in a word (shown while it has one, ask.css);
// the open panel says it in the thread already ("Thinking…", the reply itself).
function askDot() {
  const state = ASK?.dotState || 'idle', open = askShown();
  document.querySelectorAll('#ask-row [data-ask-dot], #mobile-ask [data-ask-dot]').forEach(el => ldotSet(el, open ? 'still' : state));
  ldotSet($('#ask-panel [data-ask-dot]'), open ? state : 'still');
  askSet($('#ask-row [data-ask-word]'), 'textContent', open ? '' : ldotWord(state));
}
// Where the dot is now: the open panel's, else the button's (the row on a computer, the tab on a phone).
const askHomeDot = (): Element | null => [...document.querySelectorAll('#ask-row [data-ask-dot], #mobile-ask [data-ask-dot]')].find(el => el.getClientRects().length) || null;
const askLiveDot = (): Element | null => askShown() ? $('#ask-panel [data-ask-dot]') : askHomeDot();
// Where `el` rests once the panel has finished sliding in: its box less the panel's translate right now (read in the
// same style pass as the box, so a slide just begun, or one reversed mid-close, cancels out).
function askSettled(el: Element) {
  const t = getComputedStyle($('#ask-panel')).translate, r = el.getBoundingClientRect();
  const [, y = '0'] = t && t !== 'none' ? t.split(' ') : [];
  const dy = parseFloat(y) || 0;
  return dy ? {left: r.left, top: r.top - dy, width: r.width, height: r.height} : r;
}
// Open: the row's dot flies up to its place beside the panel's name; close: back down to the row. Both ends are
// measured once, before anything moves. On a computer the panel stands on the row (ask.css --ask-foot), measured
// with them. The flight is quick (a third of a free dot's): the panel is a click away and waits on nothing.
function askToggle(open = !ASK?.open, focus = true) {
  ASK ||= {open: false, mounted: false};
  document.querySelectorAll('[data-ask-dot]').forEach(d => d._ldotFly?.cancel());   // a flight the other way lands at once
  const from = askLiveDot()?.getBoundingClientRect(), back = open ? null : askHomeDot()?.getBoundingClientRect();
  const foot = open ? askFootNow() : null;
  const inside = !!$('#ask-panel')?.contains(document.activeElement);   // read before the closed panel goes inert
  askFoot(foot);
  ASK.open = open;
  askSync();
  const to = askLiveDot();
  ldotFly(to, from, {to: open ? to && askSettled(to) : back, pace: 3});
  if (open && !ASK.mounted) { ASK.mounted = true; void askLoad(ASK); }
  else if (open) void ASK.refresh?.();       // cards answered or confirmed elsewhere meanwhile
  // A phone keeps its keyboard down until the person taps the box.
  if (open && focus && !askPhone()) setTimeout(() => $('#ask-panel .ask-form .p-text')?.focus(), 0);
  else if (!open && focus && inside) (askPhone() ? $('#mobile-ask') : $('#ask-row'))?.focus();
}
// Where the panel's foot goes on a computer: just over the Ask row (ask.css --ask-foot). Read with the other boxes before
// anything moves, then written; a phone's sheet has its own place.
const askFootNow = (): number | null => { const r = askPhone() ? null : $('#ask-row')?.getBoundingClientRect(); return r?.height ? Math.round(innerHeight - r.top + 6) : null; };
const askFoot = (px: number | null) => { if (px != null) $('#ask-panel')?.style.setProperty('--ask-foot', px + 'px'); };
// Opens the panel with words already in its box (Add bot's "connecting an agent"); words already typed stay.
function askDraft(text: string) {
  ASK ||= {open: false, mounted: false};
  ASK.draft = text;
  askToggle(true);
  askFillDraft(ASK);
}
function askFillDraft(chat: AskChat) {
  const box = chat.draft && chat.composer ? pq(chat.composer, '.p-text') : null;
  if (!box) return;
  if (!box.value.trim()) { box.value = chat.draft; box.dispatchEvent(new Event('input', {bubbles: true})); }
  chat.draft = '';
}

function askClick(ev: MouseEvent) {
  if (!(ev.target instanceof Element)) return;   // a click always lands on an element
  const t = ev.target;
  if (t.closest('[data-ask-close]')) { askToggle(false); return; }
  const pick = t.closest<HTMLElement>('[data-ask-pick]');
  if (pick) { void askAnswer(pick.closest<HTMLElement>('[data-ask-q]'), {pick: Number(pick.dataset.askPick)}); return; }
  const more = t.closest<HTMLElement>('[data-ask-more]');
  if (more) { more.setAttribute('aria-expanded', String(more.getAttribute('aria-expanded') !== 'true')); return; }
  const hint = t.closest<HTMLElement>('[data-ask-hint]');
  if (hint) { ev.preventDefault(); void ASK?.send?.(hint.dataset.askHint || ''); return; }
  // A link followed from the panel: on a phone the sheet gets out of the way of the page it opens.
  const a = t.closest<HTMLAnchorElement>('a[href^="#/"]');
  if (a && askPhone() && !a.getAttribute('href')?.startsWith('#/support')) askToggle(false, false);
}

// ---- Bots' questions
// Bots' questions to this person (Needs you), the first few: one pick of two to six answered with a tap, any other
// question with a line of words; several picks at once, or several questions, open the task.
function askQuestions(): NeedsItem[] {
  const me = myActor();
  return (S.v2?.needs || []).filter(it => it.kind === 'question' && it.ask?.recipient_id === me
    && it.ask.sender_id !== me).slice(0, ASK_CARDS_MAX);
}
// Who asks: the panel's own bot by the panel's name, anyone else as the question card names them.
const askAsker = (actor: string | undefined): string => actor === 'bot:' + assistantBot() ? askName() : qcardAsker(actor);
// A card is who asks, the question and at most two buttons. A long question shows three lines until it is tapped; three
// or more picks are one list and Send.
const ASK_Q_LONG = 160;
// A pick chosen in a list and not yet sent, by the question's wording ("task:question id", a reworded ask gets a new
// id) and kept as its label: the thread is drawn again whenever the chat changes. Questions no longer shown drop out.
const askPicks = new Map<string, string>(), askPickKey = (it: NeedsItem, q: {id?: unknown}) => `${it.id}:${q.id}`;
function askQuestionHTML(it: NeedsItem) {
  const q: AskQuestion | null = it.kind === 'question' ? qcardQuestion(it) : null, who = askAsker(it.ask?.sender_id);
  const text = plainActors(q ? q.question : it.ask?.body);
  const body = `<div class="ask-card-who">${esc(who)} asks</div>${text.length > ASK_Q_LONG
    ? `<button type="button" class="ask-card-q" data-ask-more aria-expanded="false">${esc(text)}</button>` : `<div class="ask-card-q">${esc(text)}</div>`}`;
  const structured = it.kind === 'question' && (it.ask?.refs?.questions || []).length;
  const answer = q?.multi || (structured && !q) ? `<a href="#/task/${encodeURIComponent(String(it.id))}">Answer</a>`
    : q && q.options.length > 2 ? `<form class="ask-reply" data-ask-choose="${esc(askPickKey(it, q))}"><select name="pick" aria-label="Answer ${esc(who)}">${q.options.map((o, i, all) => {
      const kept = askPicks.get(askPickKey(it, q)), on = all.some(x => x.label === kept) ? o.label === kept : o.recommended;
      return `<option value="${i}"${on ? ' selected' : ''}>${esc(o.label)}</option>`;
    }).join('')}</select><button type="submit" class="ghost">Send</button></form>`
    : q ? `<div class="ask-picks">${q.options.map((o, i) => `<button type="button" class="ghost" data-ask-pick="${i}">${esc(o.label)}</button>`).join('')}</div>`
    : `<form class="ask-reply" data-ask-reply><input name="text" maxlength="4000" autocomplete="off" placeholder="Answer ${esc(who)}" aria-label="Answer ${esc(who)}"><button type="submit" class="ghost">Send</button></form>`;
  return `<div class="ask-card" data-ask-q="${esc(it.id)}" data-ask-row="q:${esc(it.id)}">${body}${answer}</div>`;
}
// The answer goes where Decide sends it (decide.js): a pick to the task's answers, words to a loose question's answer
// or as a comment on the task, which wakes its bot.
async function askAnswer(card: HTMLElement | null, {pick, text}: {pick?: number; text?: string}) {
  const it = (S.v2?.needs || []).find(x => String(x.id) === card?.dataset.askQ);
  if (!it || !card || card.dataset.busy) return;
  const q: AskQuestion | null = it.kind === 'question' ? qcardQuestion(it) : null, who = askAsker(it.ask?.sender_id);
  const writes = pick !== undefined && q ? qcardWrites(it, q, [q.options[pick]?.label], undefined)
    : [{path: `/v2/tasks/${encodeURIComponent(String(it.id))}/comments`, body: {text}}];
  card.dataset.busy = '1';
  card.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('button, input, select').forEach(el => { el.disabled = true; });
  try {
    for (const w of writes) await post(w.path, w.body);
    card.innerHTML = `<div class="ask-card-who">${esc(who)}</div><div class="muted">Answered</div>`;
    toast('Sent to ' + who);
    await v2Refresh();                              // Needs you without it: the card leaves the chat
    ASK?.draw?.();
  } catch (e) {
    toast(errorMessage(e, ''), true);
    delete card.dataset.busy;
    card.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('button, input, select').forEach(el => { el.disabled = false; });
  }
}
async function askLoad(chat: AskChat) {
  const el = $('#ask-panel'), thread = el.querySelector('[data-ask-thread]'), error = el.querySelector('[data-ask-error]');
  const current = () => ASK === chat && chat.mounted;
  const fail = (text: string) => { if (!current()) return; askSet(error, 'textContent', text || ''); askSet(error, 'hidden', !text); };
  let info: AskInfo | null = null, messages: ChatMessage[] = [], execution: AskExecution | null = null, rendered = '', es: (() => void) | null = null, poll: ReturnType<typeof setInterval> | null = null, tick: ReturnType<typeof setInterval> | null = null;
  const stopStream = () => { try { es?.(); } catch { /* released */ } es = null; if (poll) clearInterval(poll); poll = null; };
  chat.stop = () => { stopStream(); if (tick) clearInterval(tick); tick = null; };
  const isReply = (m: ChatMessage | undefined) => m?.sender_id === 'bot:' + info?.bot;
  let pending: AskPending[] = [];
  const seenRows = new Set<string>();
  const draw = () => {
    if (!current() || !info?.available) { if (tick) clearInterval(tick); tick = null; return; }
    const at = (x: string | null | undefined) => Date.parse(x || '') || 0;
    // While the Assistant is replying, Ask's dot works, then talks as the words stream in, the panel open or not.
    const streaming = !!(execution && execution.state !== 'completed' && execution.text);
    const replying = streaming || (messages.length > 0 && !isReply(messages.at(-1)));
    chat.dotState = replying ? ldotChatState({replying, streaming}) : 'idle';
    askDot();
    // What waits on the person sits where it was asked, among the rest: Confirm cards left in other chats (BotOps'),
    // and bots' questions. One with no time comes last.
    const here = new Set(messages.map(m => m.refs?.action).filter(Boolean)), late = new Date().toISOString(), asks = askQuestions();
    const cards: AskRow[] = pending.filter(a => !here.has(a.id)).map(a => ({at: a.created_at || late,
      html: `<div class="conv-run chat"><div data-action-host="${esc(a.id)}"><div class="asst-state">Loading the card…</div></div></div>`}))
      .concat(asks.map(it => ({at: it.ask?.created_at || it.created_at || late, html: askQuestionHTML(it)})));
    const listed = new Set(asks.map(it => { const q: AskQuestion | null | false = it.kind === 'question' && qcardQuestion(it); return q ? askPickKey(it, q) : ''; }));
    for (const k of askPicks.keys()) if (!listed.has(k)) askPicks.delete(k);
    const rows: string[] = messages.slice(-30).map((m): AskRow => ({at: m.created_at, html: v2MessageHTML({...m, run: null} as ChatMessage)}))
      .concat(cards)
      .map((r, i) => ({...r, i})).sort((a, b) => at(a.at) - at(b.at) || a.i - b.i).map(r => r.html);
    const live = execution && execution.state !== 'completed' ? execution.text : '';
    // No words yet: what the turn is doing and for how long (ui/app/ask-rules.ts), its time kept current by a tick.
    const working = !live && messages.length && !isReply(messages.at(-1)) ? askWorkingLine(execution) : '';
    if (live) rows.push(`<div data-ask-live>${v2MessageHTML({from_actor: 'bot:' + info?.bot, body: live} as ChatMessage)}</div>`);
    // The thread holds only the line's label (its time and step are set after the draw), so a redraw of the same chat leaves the thread (and typed replies) be.
    else if (messages.length && !isReply(messages.at(-1))) rows.push(`<p class="muted" role="status" data-ask-working>${esc(execution?.label || (working ? 'Working' : 'Thinking…'))}</p>`);
    // The tick moves only the line's time, never the thread (its cards would load again).
    if (working && !tick) tick = setInterval(() => { const el = current() && thread.querySelector('[data-ask-working]'), t = askWorkingLine(execution);
      if (el && t) el.textContent = t; }, ASK_TICK_MS);
    else if (!working && tick) { clearInterval(tick); tick = null; }
    const html = rows.join('') || `<div class="ask-empty">${ASSISTANT_HINTS.map(h =>
      `<button class="ghost" type="button" data-ask-hint="${esc(h)}">${esc(h)}</button>`).join('')}</div>`;
    // Compared without the messages' "2m ago" (format.ts agoStill): those move in place.
    if (agoStill(html) === rendered) agoTick(thread);
    else {
      stickScroll(thread).update(() => {
        thread.innerHTML = html;
        if (thread.querySelector('[data-action-host]')) void window.assistantChat?.cards(thread, {get, post, esc, toast, reload: snapshot, assistantName: askName()});
      }, {fresh: !!rendered});
      askRowsIn(thread, seenRows, !!rendered);
      rendered = agoStill(html);
      fbSync();                         // the Assistant's replies' 👍 / 👎, read for this panel too (ui/app/feedback.ts)
    }
    const wl = working && thread.querySelector('[data-ask-working]');
    if (wl) wl.textContent = working;
    chat.drawn = true;
  };
  chat.draw = draw;
  // The whole chat again, with the Confirm cards waiting in other chats.
  const snapshot = async () => {
    if (!info?.room_id || !current()) return;
    try {
      const d = await get<AskInfo>('/v2/assistant');
      if (!current()) return;
      messages = d.messages || []; execution = d.execution || null; pending = d.pending || []; draw(); fail('');
    } catch (e) { fail(errorMessage(e, '')); }
  };
  chat.refresh = snapshot;
  const watch = () => {
    stopStream();
    if (!info?.room_id || !current()) return;
    const fallback = liveFallback(snapshot, 5000);
    if (fallback) { poll = fallback; return; }
    const cid = info.room_id, mine = (d: {conversation_id?: string}) => d.conversation_id === cid && current();
    const soon = () => liveSoon('ask:' + cid, () => void snapshot(), 150);
    const offs = [liveFollow(cid), liveOn('messages', (d: {conversation_id?: string}) => { if (mine(d)) soon(); }),
                  liveOn('runs', (d: {conversation_id?: string}) => { if (mine(d)) soon(); }), liveOn('reset', () => { if (current()) soon(); })];
    es = () => offs.forEach(off => off());
  };
  const send = async (text: string) => {
    text = String(text || '').trim();
    const P = chat.composer, files = P?.files.slice() || [];
    if ((!text && !files.length) || !P || P.sending) return;
    P.sending = true; pillButtons(P); fail('');
    try {
      const r = await cloudCompose('/v2/assistant/messages', {text: text || 'Attached files.', page: askPage()}, files);
      // An org rule held it: nothing was written, so the box keeps the text and there is no message to show.
      if (r?.held) { if (current()) fail('An org rule held this message. Nothing was sent.'); return; }
      if (!r?.message) throw new Error('Not sent. Try again.');
      CHAT_CACHE.delete(info?.bot || '');                    // the Assistant page reads the conversation again when it opens
      if (!current()) return;
      if (!messages.some(x => x.id === r.message.id)) messages.push(r.message);
      if (r.message?.conversation_id && r.message.conversation_id !== info?.room_id) { if (info) info.room_id = r.message.conversation_id; watch(); }
      pillAcknowledge(P, text, files, undefined); stickScroll(thread).toBottom(); draw();
      if (!es && !poll) watch();
      await snapshot();
    } catch (e) { fail(errorMessage(e, '')); }
    finally { P.sending = false; if (current()) pillButtons(P); }
  };
  chat.send = send;
  try { info = await get<AskInfo>('/v2/assistant'); }
  catch (e) { chat.mounted = false; if (ASK === chat) thread.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  if (!current()) return;
  info.name ||= myChatName();
  thread.setAttribute('aria-label', askName() + ' conversation');
  if (!info.available) {
    if (!info.can_turn_on) { thread.innerHTML = `<p class="muted">${esc(askName())} is off. Ask the team owner to turn it on.</p>`; return; }
    window.assistantChat?.off(thread, info, {post, esc, after: () => { if (current()) void askLoad(chat); }});
    return;
  }
  messages = info.messages || []; execution = info.execution || null; pending = info.pending || [];
  if (!el._dropZone) { el._dropZone = true; chatDropZone(el, () => ASK?.live ? ASK.composer : null); }   // a dropped file attaches
  chat.composer = makePill({mode: 'chat', label: 'Message ' + askName(), placeholder: 'Message ' + askName(),
                            send: (P: unknown, text: string) => send(text)});
  pq(chat.composer, '.p-text').maxLength = 4000;
  el.querySelector('.ask-form').replaceChildren(chat.composer.el);
  chat.live = true;
  askFillDraft(chat);
  draw();
  watch();
  if (chat.open && !askPhone()) pq(chat.composer, '.p-text').focus();
}
// Lines new since the thread last drew come in: the person's and a new card rise. Tico's own turns arrive by the
// streaming words instead. Transform and opacity only.
function askRowsIn(thread: Element, seen: Set<string>, animate: boolean) {
  const rows = thread.querySelectorAll<HTMLElement>(':scope > [data-ask-row], :scope > .conv-run.from-me[data-message]');
  for (const row of rows) {
    const key = row.dataset.askRow || 'm:' + row.dataset.message;
    if (seen.has(key)) continue;
    seen.add(key);
    if (!animate || ldotCalm() || typeof row.animate !== 'function') continue;
    row.animate([{transform: 'translateY(8px)', opacity: 0}, {transform: 'none', opacity: 1}],
      {duration: 260, easing: 'cubic-bezier(.2,0,0,1)', fill: 'backwards'});
  }
}
$('#ask-row')?.addEventListener('click', () => { if (S.me) askToggle(); else ASK_EARLY = true; });
$('#mobile-ask')?.addEventListener('click', () => { if (S.me) askToggle(); else ASK_EARLY = true; });
$('#ask-panel')?.addEventListener('click', askClick);
$('#ask-panel')?.addEventListener('change', (ev: Event) => {
  if (!(ev.target instanceof Element)) return;
  const choose = ev.target.closest<AskChoiceForm>('[data-ask-choose]');
  if (choose) askPicks.set(choose.dataset.askChoose || '', choose.elements.pick.selectedOptions[0]?.textContent || '');
});
$('#ask-panel')?.addEventListener('submit', (ev: Event) => {
  if (!(ev.target instanceof Element)) return;
  const choose = ev.target.closest<AskChoiceForm>('[data-ask-choose]');
  if (choose) { ev.preventDefault(); void askAnswer(choose.closest<HTMLElement>('[data-ask-q]'), {pick: Number(choose.elements.pick.value)}); return; }
  const form = ev.target.closest<AskReplyForm>('[data-ask-reply]');
  if (!form) return;
  ev.preventDefault();
  const text = form.elements.text.value.trim();
  if (text) void askAnswer(form.closest<HTMLElement>('[data-ask-q]'), {text});
});
document.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'j' && !askAway()) { ev.preventDefault(); askToggle(); return; }
  if (ev.key !== 'Escape' || !ASK?.open || document.querySelector('dialog[open]')) return;
  if ($('#ask-panel').contains(document.activeElement)) askToggle(false);
});
// "Ask to add or edit your bots" (getting started) and any other [data-ask-open] opens the panel.
document.addEventListener('click', ev => {
  if (!(ev.target instanceof Element) || !ev.target.closest('[data-ask-open]') || askAway()) return;
  ev.preventDefault(); askToggle(true);
});
// Any link to #/support (a reply's "Talk to the Tico team") opens the Tico team chat over the page; #/support/manage
// opens it with words asking for full management in the box.
document.addEventListener('click', ev => {
  if (!(ev.target instanceof Element)) return;
  const a = ev.target.closest<HTMLAnchorElement>('a[href^="#/support"]');
  if (!a) return;
  ev.preventDefault();
  if (a.getAttribute('href')?.includes('manage')) window.supportStart?.(ASK_MANAGE); else window.supportOpen?.();
}, true);
liveOn('needs-list', () => { ASK?.draw?.(); });
addEventListener('hashchange', () => setTimeout(askSync, 0));
// On a phone Ask is a page of its own: another tab, even the one already under it, leaves it.
document.addEventListener('click', ev => {
  if (!(ev.target instanceof Element)) return;
  if (ASK?.open && askPhone() && ev.target.closest('#mobile-nav .mobile-nav-item:not(#mobile-ask)')) askToggle(false, false);
}, true);
addEventListener('resize', () => { if (ASK?.open) askFoot(askFootNow()); askSync(); });
