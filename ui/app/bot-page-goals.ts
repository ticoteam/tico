/* ui/app/bot-page-goals.ts — The bot page's goal on its top line and the Goals card under More (split out of ui/app/bot-page.ts).
   Classic script, loaded right after bot-page.ts: its globals are shared with the other files under ui/app/. */
'use strict';

// The bot's goal, at the top of its page (#514): its first goal in the owner's order, as words.
// Tapping it opens it on the Goals page. No goal is one quiet link: "Set goal".
async function botGoalLoad(slug: string) {
  if (!$('#bot-goal')) return;
  const load = ++BOT_GOAL_LOAD;
  // A deploy restarts the hub for a few seconds and a phone drops a request now and then: try
  // again quietly before giving up, and a miss shows nothing and tries again in 30 s.
  let r = null;
  for (const wait of [0, 600, 2000]) {
    if (wait) await new Promise(done => setTimeout(done, wait));
    if (load !== BOT_GOAL_LOAD || BOT?.slug !== slug) return;
    r = await v2Get(`/v2/goals?owner=${encodeURIComponent('bot:' + slug)}`);
    if (r) break;
  }
  const mine = (r?.goals || []).filter(goalLive).sort(goalRank);
  if (load !== BOT_GOAL_LOAD || BOT?.slug !== slug || !$('#bot-goal')) return;
  botGoalRender(slug, r ? mine : undefined);
}
// The Goals card under More is the bot's goals as plain lines, each edited in
// place with the link icon for the goal it supports; the same goals the top line shows and the
// bot reads.
async function botGoalsCardLoad(slug: string) {
  /** @type {[any, any]} */
  const [r, goals] = await Promise.all([v2Get(`/v2/goals?owner=${encodeURIComponent('bot:' + slug)}`), goalOptions()]);
  const host = $('#bot-goals-list');
  if (!host || BOT?.slug !== slug) return;
  if (!r) { host.innerHTML = '<div class="empty">Could not load the goals.</div>'; return; }
  const live: GkGoal[] = (r.goals || []).filter(goalLive).sort(goalRank);
  const all = goals.some(g => g.id === live[0]?.id) ? goals : [...goals, ...live];
  host.innerHTML = live.map(g => `<div class="bot-goal-row">${goalEditorHtml(g, all)}</div>`).join('')
    + (live.length ? '' : '<div class="empty" id="bot-goals-none">No goal yet.</div>')
    + '<div id="bot-goals-finished"></div>';
  for (const el of host.querySelectorAll('[data-goal-ed]')) {
    const g = live.find(x => x.id === el.dataset.goalEd)!;   // each editor was drawn from one of live
    bindGoalEditor(el, g, () => botGoalsCardLoad(slug));
  }
  for (const a of host.querySelectorAll('[data-goal-go]'))
    a.onclick = (ev: MouseEvent) => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); openGoal(a.dataset.goalGo); };
  // Its done and dropped goals, under the live ones: "Finished (N)" (ui/app/goals.js).
  const finished = await v2Get('/v2/goals?all=true&status=done,dropped&owner=' + encodeURIComponent('bot:' + slug));
  if (!finished || BOT?.slug !== slug || !host.isConnected) return;
  goalFinishedMount(host.querySelector('#bot-goals-finished'), 'bot:' + slug, [...all, ...(finished.goals || [])], () => goalsRefresh());
}
// "Set goal" and the Goals card's add open the Goals page's goal sheet, the owner filled in (ui/app/goals.js).
async function botGoalAdd(slug: string) {
  const goals = await goalOptions();
  if (BOT?.slug !== slug) return;
  goalSheetOpen({goals}, {owner: 'bot:' + slug});
}
// The top line's goal: a target, the first goal cut to fit, and "+N" for the rest. A tap lists them all with
// Add goal under them; no goal is the target and "Set goal", which opens the goal sheet.
function botGoalRender(slug: string, goals: GkGoal[] | undefined) {
  const el = $('#bot-goal');
  botGoalFitWatch();
  if (goals === undefined) {
    el.removeAttribute('data-status'); el.innerHTML = '';
    setTimeout(() => { if (BOT?.slug === slug && $('#bot-goal') && !$('#bot-goal').innerHTML) botGoalLoad(slug); }, 30000);
    return;
  }
  const icon = '<span class="nav-icon bot-goal-i" aria-hidden="true">target</span>';
  if (!goals.length) {
    el.dataset.status = 'none';
    el.innerHTML = `<button type="button" class="bot-goal-main bot-goal-set" id="bot-goal-set">${icon}<span class="bot-goal-title">Set goal</span></button>`;
    $('#bot-goal-set').onclick = () => void botGoalAdd(slug);
    return;
  }
  const [g, ...rest] = goals as [GkGoal, ...GkGoal[]];   // not empty here (the return above)
  el.dataset.status = 'set';
  el.innerHTML = `<button type="button" class="bot-goal-main" id="bot-goal-btn" aria-haspopup="menu" aria-expanded="false"
      aria-label="Goal: ${esc(g.title)}${rest.length ? `, ${rest.length} more` : ''}" title="${esc(goals.map(x => x.title).join('\n'))}">
      ${icon}<span class="bot-goal-title">${esc(g.title)}</span>${rest.length ? `<span class="bot-goal-n">+${rest.length}</span>` : ''}</button>
    <div class="tl-pop bot-goal-pop" id="bot-goal-pop" popover><div class="tl-menu" role="menu" aria-label="Goals">
      ${goals.map(x => `<a class="tl-mi" role="menuitem" href="${GOALS}/${encodeURIComponent(x.id)}" data-goal-open="${esc(x.id)}" title="${esc(x.title)}"><span class="bot-goal-mi">${esc(x.title)}</span></a>`).join('')}
      <button type="button" class="tl-mi" role="menuitem" data-goal-add><span class="nav-icon" aria-hidden="true">add</span>Add goal</button></div></div>`;
  const btn = $('#bot-goal-btn'), pop = $('#bot-goal-pop');
  btn.onclick = () => {
    if (pop.matches(':popover-open')) { pop.hidePopover(); return; }
    pop.showPopover(); tasksMenuPlace(pop, btn); pop.querySelector('.tl-mi')?.focus();
  };
  pop.ontoggle = (ev: ToggleEvent) => btn.setAttribute('aria-expanded', String(ev.newState === 'open'));
  pop.onkeydown = tasksMenuKeys;
  for (const a of pop.querySelectorAll('[data-goal-open]'))
    a.onclick = (ev: MouseEvent) => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; ev.preventDefault(); pop.hidePopover(); openGoal(a.dataset.goalOpen); };
  pop.querySelector('[data-goal-add]').onclick = () => { pop.hidePopover(); void botGoalAdd(slug); };
}

// The goal in the header never draws narrower than BOT_GOAL_MIN (#302): its pill shrinks first, down to where
// its title still reads, and with less room than that the header drops it (.bot-top.no-goal) and the Goals card
// leads More instead. Room is the header's width less what the bot's name and the actions need; the alert line
// beside the name yields first, so it is not counted. It comes back only with BOT_GOAL_BACK px to spare, so a
// header resized around the switch point does not flicker. Below 1100px the goal has its own line and needs only
// that line's width; on a phone it lives under More anyway (bot-page.css).
const BOT_GOAL_MIN = 140, BOT_GOAL_BACK = 16;
let BOT_GOAL_FIT: ResizeObserver | null = null;
function botGoalFitWatch() {
  BOT_GOAL_FIT?.disconnect();
  BOT_GOAL_FIT = null;
  const top = $('#bot-top'), name = top?.querySelector('.bot-nameline');
  if (!top || !top.querySelector('#bot-goal')) return;
  const watch = new ResizeObserver(() => {
    // A redrawn or left page detaches the header: stop watching it.
    if (!top.isConnected) { watch.disconnect(); if (BOT_GOAL_FIT === watch) BOT_GOAL_FIT = null; return; }
    botGoalFit(top);
  });
  watch.observe(top);
  if (name) watch.observe(name);
  BOT_GOAL_FIT = watch;
  botGoalFit(top);
}
function botGoalFit(top: HTMLElement) {
  const phone = window.matchMedia('(max-width: 760px)').matches, wide = window.matchMedia('(min-width: 1100px)').matches;
  const need = phone ? 0 : wide ? botGoalNeed(top) : BOT_GOAL_MIN, room = top.clientWidth;
  const off = top.classList.contains('no-goal');
  const hide = off ? need + BOT_GOAL_BACK > room : need > room;
  if (hide === off) return;
  top.classList.toggle('no-goal', hide);
  botGoalsCardPlace(hide);
}
// What one line needs to hold the bot's whole name, every other shown item and the goal at its narrowest.
function botGoalNeed(top: HTMLElement) {
  const gap = parseFloat(getComputedStyle(top).columnGap) || 0;
  let need = BOT_GOAL_MIN, items = 1;
  for (const el of top.children as HTMLCollectionOf<HTMLElement>) {
    if (el.id === 'bot-goal' || !el.getClientRects().length) continue;   // the goal itself, and what is not drawn
    items++;
    need += el.classList.contains('bot-ident') ? botIdentNeed(el) : el.getBoundingClientRect().width;
  }
  return need + gap * (items - 1);
}
// The avatar and the name line at full length (the name's scrollWidth, not its cut width), without the alert.
function botIdentNeed(ident: HTMLElement) {
  const id = ident.querySelector('.botid') as HTMLElement | null, line = ident.querySelector('.bot-nameline') as HTMLElement | null;
  if (!id || !line) return ident.getBoundingClientRect().width;
  let name = 0;
  for (const el of line.children as HTMLCollectionOf<HTMLElement>) {
    if (!el.getClientRects().length) continue;
    const w = el.getBoundingClientRect().width;
    name += el.tagName === 'H1' ? Math.max(w, el.scrollWidth) : w;
  }
  return ident.getBoundingClientRect().width - id.getBoundingClientRect().width + Math.ceil(name);
}
// With no goal in the header the Goals card is the first card under More, as on a phone; back home after the
// alert's quarantine card when the goal returns.
function botGoalsCardPlace(first: boolean) {
  const card = $('#bot-goals-card'), lead = $('#bot-more-top'), home = $('#bot-quarantine-host');
  if (!card || !lead) return;
  if (first) lead.after(card);
  else if (home) home.after(card);
}
