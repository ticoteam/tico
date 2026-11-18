/* ui/app/org-rule-review.ts — A rule's holds as one review (backend/org_rule_check.py open_review): the group on its
   Needs you item (ui/app/decide.ts) and over the task window's cards (org-rule-holds.ts ruleHoldsLoad). The rule-review
   page part (ui/parts.json): a cold open does not download it. The cards and their helpers stay in org-rule-holds.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// ----------------------------------------------------------------- a rule's holds, as one review
// Holds of one rule for one reviewer share one review task (backend `open_review`). Its group heads them: the rule, how
// many wait, the part of each that broke the rule, and one decision for them all. Needs you shows the first RH_BRIEF
// with the part and a line around it, then "+N more" and Show each (the full cards); the task window shows every card.
const RH_BRIEF = 2;
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
/** The task window's group over its cards (ruleHoldsLoad): every page read; drawn only when two or more are still held
    and the whole list could be read, so Keep all and Send all never leave a hold behind. */
async function ruleReviewGroupInto(slot: HTMLElement, id: string) {
  const held = await ruleReviewRead(id).catch(() => [] as RuleHold[]);
  if (slot.dataset.task !== id || held.length < 2) return;
  slot.querySelector(':scope > [data-rh-group]')?.remove();
  slot.insertAdjacentHTML('afterbegin', ruleReviewHTML(held, {brief: false}));
  slot.hidden = false;
  ruleReviewWire(slot, held, () => ruleHoldsLoad(slot, id));
}
/** Needs you closed (decide.ts): the reviews it kept are read afresh next time. */
function ruleReviewReset() { RH_DC.clear(); }
