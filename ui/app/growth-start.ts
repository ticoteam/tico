/* ui/app/growth-start.ts — Growth's first run on the web (docs/growth.md "Getting started"): the Get started panel over
   the Growth cards while setup is not done (GET /api/v2/growth/setup, backend/growth_setup.py), and the line each empty
   Growth card shows naming the step that fills it.
   The panel lists the four steps in order: a done step shows what was set, the first one not done is next, with one
   button that opens the form that exists for it (Settings, a new channel, the channel's source picker, the Months form
   on the channel and the last full month; growth-channels.ts). Someone who may not do it sees who may, by name. Once
   all four are done it says so once (POST /api/v2/growth/setup/shown) and points at the channel's numbers; then it is
   gone for good. The words are drafts until the UX copy pass.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type GrStep = 'settings' | 'channel' | 'source' | 'month';
interface GrSetupNext { step: GrStep; number: number; says: string; you_may: boolean; who_may?: Array<{ref: string; name: string}>; bots_may?: boolean; month?: string; sources?: number }
interface GrSetupSet {
  settings: {unit?: string | null; revenue_month: number | null; margin_pct: number | null} | null;
  channel: ChannelRef | null;
  source: {system: string; truth_for: string} | null;
  month: {month: string; spend: number | null; new_customers: number | null} | null;
}
interface GrSetup {
  done: boolean; celebrate: boolean; next: GrSetupNext | null; channel: ChannelRef | null; set: GrSetupSet;
  steps: Array<{step: GrStep; done: boolean; at?: string | null}>; you_may: Record<string, boolean>;
}
const GRS_STEPS: GrStep[] = ['settings', 'channel', 'source', 'month'];
const GRS_ACTION: Record<GrStep, string> = {settings: 'settings', channel: 'channels', source: 'sources', month: 'months'};
// The panel's state: the page it is on and the last answer (null before the first, or once the panel is gone).
let GRS: {state: GrowthState; data: GrSetup | null; seq: number} | null = null;

// "Ana", "Ana and Ben", "Ana, Ben and Cy".
const grsAnd = (names: string[]) => names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
const grsPossessive = (name: string) => name.endsWith('s') ? `${name}’` : `${name}’s`;

// Each step's words: its title, why it matters, what a done step shows, and its button.
function grsStep(step: GrStep, d: GrSetup) {
  const ch = d.channel?.name || 'your channel', set = d.set;
  const month = d.next?.step === 'month' && d.next.month ? grMonth(d.next.month) : 'last month';
  switch (step) {
    case 'settings': {
      const s = set.settings;
      return {title: 'Say what a customer is worth', why: 'Revenue per customer a month, and your margin. Every LTV is worked out from these.',
        done: s ? `${grUsd(s.revenue_month)} per customer a month · ${grPct(s.margin_pct)} margin` : '', button: 'Set the numbers'};
    }
    case 'channel':
      return {title: 'Add your first channel', why: 'Where customers come from: search ads, SEO, partners, events.',
        done: set.channel?.name || '', button: 'Add a channel'};
    case 'source':
      return {title: 'Say where its numbers come from', why: `The system that holds ${grsPossessive(ch)} spend and new customers, so everyone knows which number is true.`,
        done: set.source ? `${set.source.system} · ${set.source.truth_for}` : '', button: d.next?.step === 'source' && !d.next.sources ? 'Add a source' : 'Choose the source'};
    case 'month': {
      const m = set.month;
      return {title: 'Add its first month', why: `Spend and new customers for ${month}. That is enough for ${grsPossessive(ch)} first CAC.`,
        done: m ? `${grMonth(m.month)}: ${grUsd(m.spend)} spent · ${grX(m.new_customers, 0)} new` : '', button: `Add ${month}`};
    }
  }
}

// Who may do the next step, for someone who may not: the owner and admins by name.
function grsWhoMay(n: GrSetupNext) {
  const names = (n.who_may || []).map(p => p.name).filter(Boolean);
  const people = names.length ? grsAnd(names) : 'the owner or an admin';
  return n.bots_may ? `Only ${people} or a bot can do this.` : `Only ${people} can do this.`;
}

function grsStepsHTML(d: GrSetup) {
  const done = d.steps.filter(s => s.done).length, next = d.next;
  const items = GRS_STEPS.map((step, i) => {
    const words = grsStep(step, d), isDone = !!d.steps.find(s => s.step === step)?.done, isNext = next?.step === step;
    const act = isNext ? next?.you_may
      ? `<button type="button" class="primary grs-go" data-gr-step="${step}">${esc(words.button)}</button>`
      : `<p class="grs-who" data-grs-who>${esc(grsWhoMay(next!))}</p>` : '';
    return `<li class="grs-step${isDone ? ' grs-done' : ''}${isNext ? ' grs-next' : ''}" data-grs-step="${step}"${isNext ? ' aria-current="step"' : ''}>
      <span class="grs-n" aria-hidden="true">${isDone ? '<span class="grs-tick">✓</span>' : i + 1}</span>
      <div class="grs-b"><span class="grs-k">${isDone ? 'Done' : isNext ? 'Next' : `Step ${i + 1}`}<span class="sr-only">: step ${i + 1} of 4</span></span>
        <b class="grs-t">${esc(words.title)}</b>
        <span class="grs-l">${esc(isDone ? words.done : words.why)}</span>${act}</div></li>`;
  }).join('');
  return `<div class="grs-head"><div><h2 class="grs-h">Get started</h2><p class="grs-sub">Four steps to your first numbers: what a customer costs you, and what one is worth.</p></div>
      <span class="spacer"></span><div class="grs-prog" role="progressbar" aria-label="Steps done" aria-valuemin="0" aria-valuemax="4" aria-valuenow="${done}">
        <span class="grs-prog-t"><b>${done}</b> of 4 done</span><span class="grs-bar"><i style="--p:${done / 4}"></i></span></div></div>
    <ol class="grs-steps">${items}</ol>`;
}

// All four done: said once, pointing at the channel's numbers.
function grsDoneHTML(d: GrSetup) {
  const ch = d.channel || d.set.channel;
  return `<div class="grs-yay"><span class="grs-burst" aria-hidden="true"><span class="grs-tick">✓</span></span>
    <div class="grs-b"><h2 class="grs-h">You’re set up</h2>
      <p class="grs-sub">${ch ? `${esc(grsPossessive(ch.name))} first numbers are in.` : 'Your first numbers are in.'} LTV and payback fill in once a month has customers kept 30 days.</p></div>
    <span class="spacer"></span>${ch ? `<button type="button" class="primary grs-go" data-grs-see="${esc(ch.slug)}">See ${esc(grsPossessive(ch.name))} numbers</button>` : ''}
    <button type="button" class="ghost tmodal-x" data-grs-close aria-label="Close">✕</button></div>`;
}

// The panel goes just under the head, over the cards; it reads its steps once now and after every save.
function grStartMount(state: GrowthState, root: HTMLElement) {
  if (S.me?.role === 'bot') return;   // a bot reads its next step from every Growth read (backend/growth_setup.py)
  const head = root.querySelector('.dash-head');
  if (!head) return;   // G: the dashboard always draws its head
  const panel = document.createElement('section');
  panel.className = 'grs'; panel.id = 'gr-start'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Get started');
  head.after(panel);
  GRS = {state, data: null, seq: 0};
  // A step's button here and on an empty card, and the done state's buttons.
  root.addEventListener('click', ev => {
    const t = ev.target instanceof Element ? ev.target : null;
    const go = t?.closest<HTMLElement>('[data-gr-step]');
    if (go) { ev.preventDefault(); void grStepGo(go.dataset.grStep as GrStep); return; }
    const see = t?.closest<HTMLElement>('[data-grs-see]');
    if (see) { grsSee(see.dataset.grsSee || ''); return; }
    if (t?.closest('[data-grs-close]')) grsGone();
  });
  void grStartRead(state);
}

async function grStartRead(state: GrowthState) {
  if (!GRS || GRS.state !== state) return;
  const seq = ++GRS.seq;
  let d: GrSetup;
  try { d = await get<GrSetup>('/v2/growth/setup', {fresh: true}); }
  catch { return; }   // no panel rather than a wrong one; the cards still name their steps
  if (!GRS || GRS.state !== state || GRS.seq !== seq || GR !== state) return;
  const panel = document.getElementById('gr-start');
  if (!panel) return;   // G: mounted with the page
  const was = GRS.data;
  GRS.data = d;
  if (d.done && !d.celebrate) { if (!panel.querySelector('.grs-yay')) grsGone(); return; }
  panel.hidden = false;
  if (d.done) {
    panel.innerHTML = grsDoneHTML(d);
    panel.classList.add('grs-won');
    // Said once: the next open has no panel. A refusal (support reading the app) says it again next time.
    post('/v2/growth/setup/shown', {}).catch(() => undefined);
    return;
  }
  panel.innerHTML = grsStepsHTML(d);
  // A step just done: its tick pops and the bar moves up.
  if (was) for (const s of d.steps) if (s.done && !was.steps.find(w => w.step === s.step)?.done) panel.querySelector(`[data-grs-step="${s.step}"]`)?.classList.add('grs-just');
}

function grsGone() {
  const panel = document.getElementById('gr-start');
  if (panel) { panel.hidden = true; panel.innerHTML = ''; }
  if (GRS) GRS.data = null;
}

// The channel's numbers: its row on the Channels card, else the first card with numbers.
function grsSee(slug: string) {
  const row = document.querySelector<HTMLElement>(`#dash-grid [data-channel="${CSS.escape(slug)}"]`) || document.querySelector<HTMLElement>('#dash-grid .dash-card');
  grsGone();
  if (!row) return;
  row.scrollIntoView({block: 'center', behavior: 'smooth'});
  row.classList.remove('grs-flash'); void row.offsetWidth; row.classList.add('grs-flash');
}

// Open a step's form; a step that is not next yet, or one this person may not do, points at the panel instead.
async function grStepGo(step: GrStep) {
  const d = GRS?.data, state = GRS?.state;
  if (!state || GR !== state) return;
  const may = d ? !!d.you_may[GRS_ACTION[step]] : grAdmin();
  const reachable = !d || d.done || d.next?.step === step || !!d.steps.find(s => s.step === step)?.done;
  if (!may || !reachable) { grsPoint(); return; }
  const ch = d?.channel;
  if (step === 'settings') return gcSettings(state);
  if (step === 'channel') return gcChannelDialog(state);
  if (!ch) { grsPoint(); return; }
  if (step === 'month') { gcCohortDialog(state, ch, {month: d?.next?.month || grsLastMonth()}, true); return; }
  // The source: Sources when there is none to pick, else the channel's form at its source picker.
  if (d?.next?.step === 'source' && !d.next.sources) { location.hash = GR_SOURCES; return; }
  const rows = await get<{channels?: GrowthChannelRow[]}>('/v2/growth/channels', {fresh: true}).then(r => r.channels || []).catch((): GrowthChannelRow[] => []);
  const row = rows.find(r => r.slug === ch.slug);
  if (!row || GR !== state) return;
  await gcChannelDialog(state, row);
  document.querySelector<HTMLSelectElement>('dialog.gr-dlg [name=source_id]')?.focus();
}
const grsLastMonth = () => { const d = new Date(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7); };

// Scroll to the panel and pulse its next step (or the panel itself once done).
function grsPoint() {
  const panel = document.getElementById('gr-start');
  if (!panel || panel.hidden) { toast('Only the owner or an admin can do this'); return; }
  const at = panel.querySelector<HTMLElement>('.grs-next') || panel;
  panel.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  at.classList.remove('grs-flash'); void at.offsetWidth; at.classList.add('grs-flash');
}

// ----------------------------------------------------------------- empty cards
// What fills each empty Growth card, with a link to that step (DASH_NONE, ui/app/dashboard.ts). The Funnel is Goals'.
const GRS_LINK: Record<GrStep, string> = {settings: 'Set the numbers', channel: 'Add a channel', source: 'Choose a source', month: 'Add a month'};
const grsFill = (step: GrStep, line: string) => `<p class="muted dash-none grs-fill" data-grs-fill="${step}">${esc(line)} <button type="button" class="linkish" data-gr-step="${step}">${esc(GRS_LINK[step])}</button></p>`;
const GRS_KEPT = ['kept_d30', 'kept_d90', 'kept_d180', 'ltv', 'ltv_cac', 'payback_months'];
Object.assign(DASH_NONE, {
  growth_number: (card: ApiCard) => GRS_KEPT.includes(String(card.settings?.metric))
    ? grsFill('month', 'Fills after step 4, a channel’s first month, once its customers are kept 30 days.')
    : grsFill('month', 'Fills after step 4: a channel’s first month.'),
  growth_channels: () => grsFill('channel', 'Fills after step 2: your first channel.'),
  growth_retention: () => grsFill('month', 'Fills after step 4: a month with customers kept 30, 90 and 180 days.'),
  growth_funnel: () => `<p class="muted dash-none grs-fill" data-grs-fill="goals">Comes from your growth KPIs on Goals. <a href="${GR_GOALS}">Open Goals</a></p>`,
  growth_board: () => `<p class="muted dash-none grs-fill" data-grs-fill="board">Fills when someone files a growth idea. ${grBoardLink('', 'Open the Growth board')}</p>`,
});
