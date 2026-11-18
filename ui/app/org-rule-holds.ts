/* ui/app/org-rule-holds.ts — What org rule checks show (backend/org_rule_check.py, docs/dev/rules-plan.md §3, board Held):
   on a review task (tagged rule-review) each held output with its rule and the reviewer's three actions, Keep blocked,
   Send anyway and Edit rule; the one-line note a person sees in a chat when a bot's reply was held; a person's own held
   message in their chat (the rule, Edit and resend, or Send anyway for an editor of the rule); and the checks line in
   the Rules page header ("Checks on · 12 today · 0 unchecked · $48 left · $3 on people").
   GET /api/v2/rule-holds?task=<id> lists a task's holds a page at a time, as summaries; each card's text (and the
   reviewer's nonce) is read from GET /api/v2/rule-holds/<hold>. Both answer only the reviewer and the author bot.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const RH_LABEL = 'rule-review';
// The rule's If broken, in the words the Rules page uses for it (ui/app/org-rules.ts RU_BROKEN).
const RH_OUTCOME: Record<'blocked' | 'escalated', readonly [string, string]> = {blocked: ['Stop', 'rh-blk'], escalated: ['Ask first', 'rh-esc']};

/** One rule a hold was checked against: the card's Rule block. `part`: the part of the held text that breaks it
    (backend `_parts`), null when no single part does; only an opened hold carries it. */
interface RuleHoldRule { id: string; kind: string; text: string; if_broken?: string; part?: string | null; }
/** A rule hold: a list gives summaries, GET /v2/rule-holds/<id> the card (its text, rules and the actions the reader may take). */
interface RuleHold {
  id: string;
  operation?: string;
  created_at?: WhenInput;
  outcome: 'blocked' | 'escalated';
  state?: string;
  author?: string;
  author_name?: string | null;
  to?: string[];
  text?: string | null;
  text_cut?: boolean;
  rules?: RuleHoldRule[];
  why?: string;
  decided_by?: string;
  can_send?: boolean;
  can_withdraw?: boolean;
  nonce?: string;
  /** The part call still runs (backend `_fill_parts`): an open card reads the hold again until this goes false. */
  part_pending?: boolean;
}
interface RuleHoldPage { holds: RuleHold[]; next_cursor?: string; }
/** GET /v2/rules/stats: the Rules page header's checks line. */
interface RuleStats { on: boolean; checks_today: number; unchecked_today: number; usd_left?: number | null; people_usd: number; }
/** A task dialog; the hold section it keeps between redraws is rhEl. */
type HoldDialog = HTMLElement & {rhEl?: HTMLElement};
/** The task a held comment belongs to (ruleTaskHeld): its id and its dialog. */
interface HoldTask { id: string | number; dialog: HoldDialog; }
/** Called for each card an action was taken on: the card, its hold id and the action's path. */
type HoldAfter = (card: HTMLElement, hid: string, path: string) => unknown;

// ----------------------------------------------------------------- a review task's holds
// The section moves into each redraw of the task, as its pages do (taskPagesAdopt), so a redraw reads nothing again.
function ruleHoldsAdopt(d: HoldDialog, task: {id: string | number; labels?: string[]}) {
  const slot: HTMLElement | null = $('[data-rule-holds]', d);
  if (!slot) return;
  const id = String(task.id);
  if (d.rhEl && d.rhEl.dataset.task === id) { slot.replaceWith(d.rhEl); return; }
  if (!(task.labels || []).includes(RH_LABEL)) { slot.hidden = true; return; }
  d.rhEl = slot; slot.dataset.task = id;
  void ruleHoldsLoad(slot, id);
}
// One page of summaries, then each hold opened for its text. `cursor` continues the list below the cards drawn.
async function ruleHoldsLoad(slot: HTMLElement, id: string, cursor = '') {
  let page: RuleHoldPage = {holds: []};
  try {
    page = await get<RuleHoldPage>('/v2/rule-holds?task=' + encodeURIComponent(id) + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''),
      {fresh: true});
  } catch { page = {holds: []}; }
  const holds = await Promise.all((page.holds || []).map(h =>
    get<RuleHold>('/v2/rule-holds/' + encodeURIComponent(h.id), {fresh: true}).catch(() => h)));
  // Two or more still held: the rule's group first, read from every page (not the cards' page alone), whose Keep all
  // and Send all decide every hold it counts. When every page can't be read there is no group: each card keeps its own.
  const held = cursor ? [] : await ruleReviewRead(id).catch(() => [] as RuleHold[]);
  if (slot.dataset.task !== id) return;
  $('.rh-more', slot)?.remove();
  if (!cursor) slot.innerHTML = '';
  const group = held.length > 1 ? ruleReviewHTML(held, {brief: false}) : '';
  slot.insertAdjacentHTML('beforeend', group + holds.map(ruleHoldHTML).join('') +
    (page.next_cursor ? `<button class="ghost small rh-more" type="button">Show more</button>` : ''));
  slot.hidden = !slot.querySelector('[data-hold]');
  ruleHoldsWire(slot, id);
  if (group) ruleReviewWire(slot, held, () => ruleHoldsLoad(slot, id));
  const more: HTMLButtonElement | null = $('.rh-more', slot);
  more?.addEventListener('click', () => {
    more!.disabled = true;
    void ruleHoldsLoad(slot, id, page.next_cursor);
  }, {once: true});
}
// A decided card is drawn again from its own hold: the cards around it keep their place.
async function ruleHoldRedraw(slot: HTMLElement, id: string | undefined, card: HTMLElement, hid: string) {
  const h = await get<RuleHold>('/v2/rule-holds/' + encodeURIComponent(hid), {fresh: true});
  if (slot.dataset.task !== id) return;
  card.outerHTML = ruleHoldHTML(h);
  ruleHoldsWire(slot, id);
}
function ruleHoldHTML(h: RuleHold): string {
  const [word, cls] = RH_OUTCOME[h.outcome] || RH_OUTCOME.blocked;
  const rules = (h.rules || []).map(r => `<div class="rh-rule">${ruTextHTML(r.text)}</div>`).join('');
  // The reviewer releases or keeps a hold. Its author takes it back to edit and resend; the person a bot's hold is held
  // for may only withdraw it (it stays blocked).
  const reviewer = !!h.nonce || h.can_send;
  const decided = h.state === 'sent' ? `Sent anyway${h.decided_by ? ' by ' + esc(actorLabel(h.decided_by)) : ''}`
    : h.state === 'kept' ? `Kept blocked${h.decided_by ? ' by ' + esc(actorLabel(h.decided_by)) : ''}` : '';
  const rule = rhRule(h);
  const to = h.to || [], text = rhText(h), cut = text.length > RH_TEXT_MAX;
  const shown = cut ? text.slice(0, RH_TEXT_MAX).trimEnd() : text, part = rhPart(h);
  return `<article class="rh-card" data-hold="${esc(h.id)}">
    <header><span class="rh-pill ${cls}">${word}</span><span class="rh-to muted">${esc(h.author_name || actorLabel(h.author))}${
      to.length ? ' → ' + esc(to.join(', ')) : ''}</span></header>
    <div><div class="rh-lbl">Tried to send</div><div class="rh-msg">${rhMarkedHTML(shown, shown.includes(part) ? part : '')}${cut || h.text_cut ? '…' : ''}</div>${
      h.rules?.length && !part ? '<p class="rh-whole muted">The whole text breaks it.</p>' : ''}</div>
    <div><div class="rh-lbl">Rule</div>${rules || '<div class="rh-rule muted">A rule that no longer exists</div>'}</div>
    ${RH_FAILED.has(h.id) ? `<p class="rh-err" role="alert">${esc(RH_FAILED.get(h.id) || '')}</p>` : ''}
    ${h.why ? `<div><div class="rh-lbl">Appeal</div><div class="rh-why">${esc(h.why)}</div></div>` : ''}
    ${decided ? `<p class="muted rh-done">${decided}</p>` : reviewer ? `<div class="rh-acts">
      ${rule ? `<a class="ghost small rh-btn" href="${esc(ruHref(rule.kind, {rule: rule.id}))}">Edit rule</a>` : ''}
      ${h.can_send ? `<button class="ghost small" type="button" data-hold-send="${esc(h.nonce || '')}">Send anyway</button>` : ''}
      <span class="spacer"></span><button class="primary small" type="button" data-hold-keep>Keep blocked</button></div>`
      : h.can_withdraw ? `<div class="rh-acts"><span class="spacer"></span><button class="primary small" type="button" data-hold-edit>${
        String(h.author || '').startsWith('human:') ? 'Edit and resend' : 'Withdraw'}</button></div>` : ''}
    <p class="rh-err" role="status" hidden></p>
  </article>`;
}
// ----------------------------------------------------------------- a rule's holds, as one review
// Holds of one rule for one reviewer share one review task (backend `open_review`). Its group heads them: the rule, how
// many wait, the part of each that broke the rule, and one decision for them all. Needs you shows the first RH_BRIEF
// with the part and a line around it, then "+N more" and Show each (the full cards); the task window shows every card.
const RH_BRIEF = 2;
// A hold's text on its card: the first RH_TEXT_MAX characters, then "…".
const RH_TEXT_MAX = 600;
const RH_ASKS = ['question', 'chat_question', 'task_ask'];
/** A hold's text as its reviewer reads it: an ask held before its copy dropped its labels reads without them too. */
const rhText = (h: RuleHold): string => {
  const text = h.text || '';
  return RH_ASKS.includes(h.operation || '') ? text.replace(/^(?:Ask|Shows): /gm, '') : text;
};
/** The rule a hold is on: the first that is not Warn (the server lists it first; an older hold may not). */
const rhRule = (h: RuleHold): RuleHoldRule | undefined => (h.rules || []).find(r => r.if_broken !== 'warn') || h.rules?.[0];
// Why a hold Send all could not send, by hold id: said under that hold until it is decided.
const RH_FAILED = new Map<string, string>();
/** The part of a hold's text the first rule that names one says breaks it, '' when none does. */
const rhPart = (h: RuleHold): string => (h.rules || []).map(r => r.part || '').find(p => !!p && rhText(h).includes(p)) || '';
/** `text` with `part` marked (the fail tint and a 2px bar: not colour alone), escaped. */
function rhMarkedHTML(text: string, part: string): string {
  const at = part ? text.indexOf(part) : -1;
  if (at < 0) return esc(text);
  return `${esc(text.slice(0, at))}<mark class="rh-part">${esc(part)}</mark>${esc(text.slice(at + part.length))}`;
}
/** A hold in brief: who wrote it, to whom and when, then the line with its part and at most one line around it. */
function rhBriefHTML(h: RuleHold): string {
  const lines = rhText(h).split('\n').map(l => l.trim()).filter(Boolean), part = rhPart(h);
  const at = part ? lines.findIndex(l => l.includes(part)) : -1;
  const from = at < 0 ? 0 : Math.max(0, at - 1), shown = at < 0 ? lines.slice(0, 2) : lines.slice(from, at + 2);
  const asks = RH_ASKS.includes(h.operation || '');
  const to = (h.to || []).join(', ');
  const failed = RH_FAILED.get(h.id);
  return `<div class="rh-brief" data-rh-brief="${esc(h.id)}">
    <p class="rh-who muted"><b>${esc(h.author_name || actorLabel(h.author))}</b> → ${asks ? '❓ ' : ''}${esc(to || 'a message')}${
      h.created_at ? ` · ${esc(ago(h.created_at))}` : ''}</p>
    <div class="rh-lines">${shown.map((l, i) => from + i === at ? `<div class="rh-line rh-hit">${rhMarkedHTML(l, part)}</div>`
      : `<div class="rh-line">${esc(l)}</div>`).join('')}</div>
    ${at < 0 ? '<p class="rh-whole muted">The whole text breaks it.</p>' : ''}
    ${failed ? `<p class="rh-err" role="alert">${esc(failed)}</p>` : ''}
  </div>`;
}
/** The group of a rule's holds. `brief`: Needs you's (the first RH_BRIEF in brief, "+N more", Show each, which `open`
    draws as the full cards); else the task window's head over its cards. The holds Keep all and Send all decide are
    the ones drawn (data-rh-ids): one that joins later stays held until the group is drawn again. */
function ruleReviewHTML(holds: RuleHold[], {brief, open = false}: {brief: boolean; open?: boolean}): string {
  const held = holds.filter(h => (h.state || 'held') === 'held');
  if (!held.length) return holds.length ? '<p class="muted rh-done">Every hold here is decided.</p>' : '';
  const reviewer = held.some(h => !!h.nonce || h.can_send), n = held.length;
  const rule = held.map(rhRule).find(Boolean);
  const title = rule ? `Held by: ${rule.text}` : 'Held by a rule that no longer exists';
  const shown = brief && !open ? held.slice(0, RH_BRIEF) : [], rest = brief && !open ? n - shown.length : 0;
  const sendable = held.filter(h => h.can_send && h.nonce);
  const one = n === 1;
  // One filled button, on the right: Keep blocked. Send anyway is the risky one, so plain; Edit rule sits on the left.
  const acts = reviewer ? `<div class="rh-acts rh-gacts">
      ${rule ? `<a class="ghost small rh-btn" href="${esc(ruHref(rule.kind, {rule: rule.id}))}">Edit rule</a>` : ''}<span class="spacer"></span>
      ${sendable.length ? `<button class="ghost small" type="button" data-rh-send-all>${one ? 'Send anyway' : `Send all ${sendable.length} anyway`}</button>` : ''}
      <button class="primary small" type="button" data-rh-keep-all>${one ? 'Keep blocked' : `Keep all ${n} blocked`}</button></div>` : '';
  return `<section class="rh-group" data-rh-group data-rh-ids="${esc(held.map(h => h.id).join(' '))}">
    <header class="rh-ghead"><span class="rh-gtitle" title="${esc(title)}">${esc(title)}</span><span class="rh-gcount muted tnum">· ${n} ${one ? 'hold' : 'holds'}</span></header>
    ${shown.map(rhBriefHTML).join('')}
    ${rest > 0 || (brief && !open && n > 0) ? `<div class="rh-rest">${rest > 0 ? `<span class="muted">+${rest} more</span>` : ''}<button class="ghost small" type="button" data-rh-each>Show each</button></div>` : ''}
    ${brief && open ? `<div class="rh-each">${held.map(ruleHoldHTML).join('')}</div>` : ''}
    ${acts}
    <p class="rh-err" role="status" hidden></p>
  </section>`;
}
/** Keep all and Send all on a group: each hold drawn, one at a time, through its own Keep blocked or Send anyway (Send
    anyway checks each again). A hold that could not be sent keeps its reason under it; `after` draws the group again. */
function ruleReviewWire(root: HTMLElement, holds: RuleHold[], after: () => unknown, each?: () => unknown) {
  const group: HTMLElement | null = root.querySelector('[data-rh-group]:not([data-wired])');
  if (!group) return;
  group.dataset.wired = '1';
  const ids = (group.dataset.rhIds || '').split(' ').filter(Boolean), err: HTMLElement | null = group.querySelector(':scope > .rh-err');
  const byId = new Map(holds.map(h => [h.id, h]));
  const run = async (button: HTMLButtonElement, act: 'keep' | 'send') => {
    group.querySelectorAll('button').forEach(b => { b.disabled = true; });
    if (err) err.hidden = true;
    let failed = 0;
    for (const hid of ids) {
      const h = byId.get(hid);
      if (!h || (act === 'send' && !(h.can_send && h.nonce))) continue;
      try {
        await post('/v2/rule-holds/' + encodeURIComponent(hid) + '/' + act, act === 'send' ? {nonce: h.nonce} : {});
        RH_FAILED.delete(hid);
      } catch (e) { failed++; RH_FAILED.set(hid, errorMessage(e, act === 'send' ? 'It could not be sent.' : 'It could not be kept.')); }
    }
    if (failed && err) { err.textContent = `${failed} could not be ${act === 'send' ? 'sent' : 'kept'}: the reason is under each.`; err.hidden = false; }
    await after();
    button.blur();
  };
  const keep: HTMLButtonElement | null = group.querySelector('[data-rh-keep-all]');
  keep?.addEventListener('click', () => void run(keep, 'keep'));
  const send: HTMLButtonElement | null = group.querySelector('[data-rh-send-all]');
  send?.addEventListener('click', () => void run(send, 'send'));
  group.querySelector('[data-rh-each]')?.addEventListener('click', () => void each?.());
}

// ----------------------------------------------------------------- a rule's review in Needs you
// The review's item in Needs you (ui/app/decide-about.ts) holds a slot, data-rh-dc, under the task's text: its group
// is read once per task while the screen is open and moves into each redraw; each redraw reads the list again (a page
// of summaries, cheap) and draws the group again only when the holds waiting changed, so the count keeps up.
interface RuleReviewDc { el: HTMLElement; holds: RuleHold[]; open: boolean; reading: boolean; timer?: number; }
const RH_DC = new Map<string, RuleReviewDc>();
// How often a review shown in Needs you reads its list again, so a hold that joins is counted while it is open.
const RH_TICK_MS = 10000;
/** Every hold of a review still held, each opened for its text; `known` are holds read before, opened again only when
    their state changed or their failing part was still coming when they were read. Every page is read, to the list's
    end: a group's count and Keep all / Send all cover all its holds. A list that cannot be read to its end (an error,
    or a cursor that comes back) throws, so no group is drawn as if it were complete. */
async function ruleReviewRead(taskId: string, known: RuleHold[] = []): Promise<RuleHold[]> {
  const listed: RuleHold[] = [];
  const seen = new Set<string>();
  let cursor = '';
  do {
    const page = await get<RuleHoldPage>('/v2/rule-holds?task=' + encodeURIComponent(taskId) +
      (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {fresh: true});
    listed.push(...(page.holds || []));
    seen.add(cursor);
    cursor = page.next_cursor || '';
    if (cursor && seen.has(cursor)) throw new Error('The list of holds came back to a page it had read.');
  } while (cursor);
  const had = new Map(known.map(h => [h.id, h]));
  return Promise.all(listed.filter(h => (h.state || 'held') === 'held').map(h => {
    const old = had.get(h.id);
    return old && (old.state || 'held') === 'held' && !old.part_pending && !h.part_pending ? old
      : get<RuleHold>('/v2/rule-holds/' + encodeURIComponent(h.id), {fresh: true}).catch(() => h);
  }));
}
/** The list was read again (decide.ts decideRefresh): each review on screen counts its holds again. */
function ruleReviewTick() {
  RH_DC.forEach((v, taskId) => { if (v.el.isConnected) void ruleReviewRefresh(taskId, v); });
}
function ruleReviewDraw(taskId: string, v: RuleReviewDc) {
  v.el.innerHTML = v.holds.length ? ruleReviewHTML(v.holds, {brief: true, open: v.open}) : '<p class="muted rh-done">Every hold here is decided.</p>';
  ruleReviewWire(v.el, v.holds, () => ruleReviewRefresh(taskId, v, true), () => { v.open = !v.open; ruleReviewDraw(taskId, v); });
  ruleHoldsWire(v.el, taskId, () => ruleReviewRefresh(taskId, v, true));
}
async function ruleReviewRefresh(taskId: string, v: RuleReviewDc, always = false) {
  if (v.reading) return;
  v.reading = true;
  try {
    const holds = await ruleReviewRead(taskId, always ? [] : v.holds);
    const key = (hs: RuleHold[]) => hs.map(h => `${h.id}:${h.state || 'held'}:${h.part_pending ? 'p' : ''}`).join(' ');
    if (always || key(holds) !== key(v.holds)) { v.holds = holds; ruleReviewDraw(taskId, v); }
  } catch { if (!v.holds.length) v.el.innerHTML = '<p class="muted">The held items could not be read.</p>'; }
  finally { v.reading = false; }
}
/** Called after Needs you draws an item's left side: a review's slot gets its group (read once, then kept). */
function ruleReviewMount(host: HTMLElement) {
  const slot = host.querySelector<HTMLElement>('[data-rh-dc]');
  const taskId = slot?.dataset.rhDc || '';
  if (!slot || !taskId) return;
  const v = RH_DC.get(taskId);
  if (v) {
    if (slot !== v.el) slot.replaceWith(v.el);
    void ruleReviewRefresh(taskId, v);
    return;
  }
  slot.innerHTML = skelRows(3);
  const made: RuleReviewDc = {el: slot, holds: [], open: false, reading: false};
  RH_DC.set(taskId, made);
  void ruleReviewRefresh(taskId, made, true);
  // While its review is shown, a hold that joins is counted; once it leaves the screen the timer stops.
  made.timer = window.setInterval(() => {
    if (!made.el.isConnected) { window.clearInterval(made.timer); if (RH_DC.get(taskId) === made) RH_DC.delete(taskId); return; }
    void ruleReviewRefresh(taskId, made);
  }, RH_TICK_MS);
}

// `after(card, hold, path)` runs once an action is taken; a task's cards redraw in place, a chat's leave the thread.
function ruleHoldsWire(slot: HTMLElement, id: string | undefined,
  after: HoldAfter = (card, hid) => ruleHoldRedraw(slot, id, card, hid)) {
  slot.querySelectorAll<HTMLElement>('[data-hold]:not([data-wired])').forEach(card => {
    card.dataset.wired = '1';
    const hid = card.dataset.hold!, err: HTMLElement = $('.rh-err', card);
    const act = async (path: string, body: {nonce?: string | undefined}, button: HTMLElement | null) => {
      card.querySelectorAll('button').forEach(b => { b.disabled = true; });
      err.hidden = true;
      try {
        await post('/v2/rule-holds/' + encodeURIComponent(hid) + '/' + path, body);
        await after(card, hid, path);
      } catch (e) {
        err.textContent = errorMessage(e, ''); err.hidden = false;
        card.querySelectorAll('button').forEach(b => { b.disabled = false; });
        button?.focus();
      }
    };
    const keep: HTMLButtonElement | null = $('[data-hold-keep]', card);
    keep?.addEventListener('click', () => act('keep', {}, keep));
    const edit: HTMLButtonElement | null = $('[data-hold-edit]', card);
    edit?.addEventListener('click', () => act('withdraw', {}, edit));
    const send: HTMLButtonElement | null = $('[data-hold-send]', card);
    send?.addEventListener('click', () => act('send', {nonce: send.dataset.holdSend}, send));
  });
}

// ----------------------------------------------------------------- a held reply in a chat
// The keeper's one line where a bot's reply would have been: the rule, never the text (execution.complete).
function ruleHeldNoteHTML(m: {id?: string | null | undefined; body?: string | null | undefined; created_at?: WhenInput}) {
  return `<div class="chat-system rh-note" data-message="${esc(m.id || '')}"><span class="rh-note-dot" aria-hidden="true"></span>${
    esc(plainActors(m.body))}<div class="chat-meta"><time class="chat-time" title="${esc(fmt(m.created_at))}">${esc(ago(m.created_at))}</time></div></div>`;
}

// ----------------------------------------------------------------- a person's own held message, in their chat
// The holds a chat shows under its messages: the person's own held writes (with the rule, and Edit and resend), and those
// an editor of the rule may send or keep. A held send is shown at once from its answer; the list is read when the chat
// is drawn (ui/app/chat.ts v2ChatRender), so a reopened chat still shows what is held.
async function ruleChatHoldsLoad(state: ChatState) {
  const thread = state.conv?.id;
  if (!thread) return;
  let page: RuleHoldPage = {holds: []};
  try { page = await get<RuleHoldPage>('/v2/rule-holds?thread=' + encodeURIComponent(thread), {fresh: true}); } catch { return; }
  const holds = (await Promise.all((page.holds || []).map(h =>
    get<RuleHold>('/v2/rule-holds/' + encodeURIComponent(h.id), {fresh: true}).catch((): RuleHold | null => null)))).filter((h): h is RuleHold => h !== null);
  if (V2C !== state || state.conv?.id !== thread) return;
  // A hold shown since the list was asked for stays until the list is read again.
  state.holds = [...(state.holds || []).filter(h => !holds.some(x => x.id === h.id)), ...holds];
  v2ChatRender(state);
}
// A held send, as its answer gives it: its card under the chat, and the text back in the box once Edit and resend is taken.
function ruleChatHeld(state: ChatState, hold: RuleHold) {
  state.holds = [hold, ...(state.holds || []).filter(h => h.id !== hold.id)];
  if (V2C === state) v2ChatRender(state);
}
// A held task comment: its card in the task's hold list, with its actions. Nothing was written, so the box keeps the text.
function ruleTaskHeld(state: HoldTask, hold: RuleHold) {
  const slot: HTMLElement | null = $('[data-rule-holds]', state.dialog);   // beside the chat, not in it
  if (!slot) return;
  const id = String(state.id);
  slot.dataset.task = id;
  state.dialog.rhEl = slot;   // a redraw of the task keeps the card (ruleHoldsAdopt)
  slot.querySelectorAll<HTMLElement>('[data-hold]').forEach(card => { if (card.dataset.hold === hold.id) card.remove(); });
  slot.hidden = false;
  slot.insertAdjacentHTML('afterbegin', ruleHoldHTML(hold));
  ruleHoldsWire(slot, id);
}
// A chat's cards: an action takes its card out of the thread; Edit and resend also puts the text back in this bot's box.
function ruleChatWire(state: ChatState, thread: HTMLElement) {
  ruleHoldsWire(thread, state.conv?.id, (card, hid, path) => {
    const hold = (state.holds || []).find(h => h.id === hid);
    state.holds = (state.holds || []).filter(h => h.id !== hid);
    if (path === 'withdraw' && String(hold?.author || '').startsWith('human:') && hold?.text && !hold.text_cut)
      ruleRestoreDraft(state.slug, hold.text);
    if (V2C === state) v2ChatRender(state);
  });
}
function ruleRestoreDraft(slug: string | undefined, text: string) {
  if (!BOT_PILL || BOT_PILL.slug !== slug) return;
  const box = pq(BOT_PILL, '.p-text');
  if (!box) return;
  box.value = text;
  box.dispatchEvent(new Event('input', {bubbles: true}));
  pillButtons(BOT_PILL);
  box.focus();
}

// ----------------------------------------------------------------- the Rules page header
// "Checks on · N today · N unchecked · $N left", or "Checks off". Mounted into the Rules page's header (ui/app/org-rules.ts)
// whenever that page is drawn; a person who may not read the stats sees nothing.
function ruleChecksLineText(s: RuleStats) {
  if (!s) return '';
  if (!s.on) return 'Checks off';
  const parts = ['Checks on', `${s.checks_today} today`, `${s.unchecked_today} unchecked`];
  if (s.usd_left != null) parts.push(`$${Math.round(s.usd_left)} left`);
  if (s.people_usd > 0) parts.push(`$${Number(s.people_usd.toFixed(2))} on people`);
  return parts.join(' · ');
}
// Whether checks are on, for a composer's "Checking…": read at most once a minute, and off when the stats are not read.
let RH_ON = {at: 0, on: false};
async function ruleChecksOn() {
  if (Date.now() - RH_ON.at < 60000) return RH_ON.on;
  let on = false;
  try { on = !!(await get<RuleStats>('/v2/rules/stats', {fresh: true})).on; } catch { on = false; }
  RH_ON = {at: Date.now(), on};
  return on;
}
// Called by pageRules once it has drawn its header. The line is a status, read rarely, so it heads the header's ⋯ menu
// (style guide, "Top bars: one line": what is used less than daily goes in ⋯); the header keeps the read on itself
// (data-checks) so each tab's new ⋯ shows it again (ruleChecksPaint, from ruDrawHead).
async function ruleChecksMount(head: HTMLElement | null) {
  if (!head || head.dataset.checks !== undefined) return;
  head.dataset.checks = '';
  try {
    const stats = await get<RuleStats>('/v2/rules/stats', {fresh: true});
    head.dataset.checks = ruleChecksLineText(stats);
    // A quarter or more of today's outputs went unchecked: that needs someone, so it is amber in ⋯ and ⋯ carries a dot.
    head.dataset.checksWarn = String(!!stats.on && stats.unchecked_today > 0 && stats.unchecked_today >= stats.checks_today / 4);
  } catch { head.dataset.checks = ''; head.dataset.checksWarn = 'false'; }
  ruleChecksPaint(head);
}
// The checks line at the top of ⋯, or none while it is unread or not readable.
function ruleChecksPaint(head: HTMLElement) {
  const menu = head.querySelector<HTMLElement>(':scope > .ph-menu'), text = head.dataset.checks || '', warn = !!text && head.dataset.checksWarn === 'true';
  // The dot on ⋯: rule checks need attention.
  const more = head.querySelector<HTMLElement>(':scope > .ph-more');
  more?.classList.toggle('ru-checks-warn', warn);
  if (warn) more?.setAttribute('aria-description', 'Rule checks need attention'); else more?.removeAttribute('aria-description');
  if (!menu) return;
  let line = menu.querySelector<HTMLElement>(':scope > .ru-checks');
  if (!text) { line?.remove(); return; }
  if (!line) {
    line = document.createElement('p');
    line.className = 'ru-checks muted tnum';
    const after = menu.querySelector(':scope > :is(.ph-mtabs, .ph-tools):last-of-type');
    if (after) after.after(line); else menu.prepend(line);
  }
  line.textContent = text;
  line.classList.toggle('warn', warn);
}
