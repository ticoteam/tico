/* ui/app/org-rule-holds.ts — What org rule checks show (backend/org_rule_check.py, docs/rules-plan.md §3, board Held):
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

/** One rule a hold was checked against: the card's Rule block. */
interface RuleHoldRule { id: string; kind: string; text: string; }
/** A rule hold: a list gives summaries, GET /v2/rule-holds/<id> the card (its text, rules and the actions the reader may take). */
interface RuleHold {
  id: string;
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
  if (slot.dataset.task !== id) return;
  $('.rh-more', slot)?.remove();
  if (!cursor) slot.innerHTML = '';
  slot.insertAdjacentHTML('beforeend', holds.map(ruleHoldHTML).join('') +
    (page.next_cursor ? `<button class="ghost small rh-more" type="button">Show more</button>` : ''));
  slot.hidden = !slot.querySelector('[data-hold]');
  ruleHoldsWire(slot, id);
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
  const rule = h.rules?.[0];
  const to = h.to || [];
  return `<article class="rh-card" data-hold="${esc(h.id)}">
    <header><span class="rh-pill ${cls}">${word}</span><span class="rh-to muted">${esc(h.author_name || actorLabel(h.author))}${
      to.length ? ' → ' + esc(to.join(', ')) : ''}</span></header>
    <div><div class="rh-lbl">Tried to send</div><div class="rh-msg">${esc(h.text || '')}${h.text_cut ? '…' : ''}</div></div>
    <div><div class="rh-lbl">Rule</div>${rules || '<div class="rh-rule muted">A rule that no longer exists</div>'}</div>
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
function ruleHeldNoteHTML(m: {id?: string | null; body?: string | null; created_at?: WhenInput}) {
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
  if (BOT_PILL?.slug !== slug) return;
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
// Called by pageRules once it has drawn its header.
async function ruleChecksMount(head: Element | null) {
  if (!head || head.querySelector('.ru-checks')) return;
  const line = document.createElement('span');
  line.className = 'ru-checks muted tnum';
  head.querySelector('h1')?.after(line);
  try {
    const stats = await get<RuleStats>('/v2/rules/stats', {fresh: true});
    line.textContent = ruleChecksLineText(stats);
    line.classList.toggle('warn', !!stats.on && stats.unchecked_today > 0 && stats.unchecked_today >= stats.checks_today / 4);
  } catch { line.remove(); }
}
