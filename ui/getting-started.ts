/* After the wizard: the tour, and one line under the org list while there are no bots of your own
   (docs/onboarding.md). Whether that line shows comes from GET /api/v2/setup/getting-started; the only thing
   kept per person is whether they saw the tour. Strategy's Market tab, when empty (ui/market-page.ts), is
   where the market is asked for. */
'use strict';
interface GsItem { id: string; done: boolean; skipped?: boolean; optional?: boolean }
interface GsAnswer { items: GsItem[]; can_build?: boolean; tour_seen?: boolean; dismissed?: boolean }
interface GsTour {
  root: HTMLDivElement; steps: [string, string, string][]; at: number; phone: boolean; opener: HTMLElement | null;
  off?: () => void;
}
let GS: GsAnswer | null = null;                     // the last answer, or null when the server has none to give
let GS_LOAD = 0;

async function gsRefresh(): Promise<GsAnswer | null> {
  if (!S.me?.cloud) return null;
  const seq = ++GS_LOAD;
  const data: GsAnswer | null = await v2Get('/v2/setup/getting-started');
  if (seq !== GS_LOAD) return GS;
  GS = data && Array.isArray(data.items) ? data : null;
  gsOrgHint();
  renderOnboardingNav();
  return GS;
}

async function gsState(change: {tour?: boolean; checklist?: boolean}): Promise<void> {
  try {
    const next = await post('/v2/setup/getting-started/state', change);
    if (GS && next) GS = {...GS, tour_seen: next.tour, dismissed: next.checklist};
  } catch { /* a choice that did not save is asked again next time */ }
}


// "Ask to add or edit your bots" (it opens Ask, ui/app/ask-panel.ts), for whoever may add bots, until there is one of your own.
function gsOrgHint(): void {
  const line = $('#gs-org-hint');
  if (!line) return;
  const bot = GS?.items.find(item => item.id === 'first_bot');
  line.hidden = !(bot && !bot.done && GS?.can_build);
}

// ---------------------------------------------------------------- the tour
const GS_STEPS: [string, string, string][] = [
  ['[data-nav="updates"]', 'Updates', 'Every bot posts a short update each day, and a fuller one on Fridays.'],
  ['[data-nav="tasks"]', 'Tasks', 'Work for bots and humans. Give a task an owner and it gets done, or comes back with a question.'],
  ['#nav-organisation', 'Team', 'Your team is listed here. Open a bot to chat, see its work and change its settings.'],
  ['[data-nav="docs"]', 'Docs', 'Your docs, searchable, with questions answered from them.'],
  ['[data-nav="strategy"]', 'Strategy', 'How your team means to win, and a map of the market that a bot keeps current.'],
  ['[data-nav="meetings"]', 'Meetings', 'Connect a source or add notes, and bots pull out the tasks and follow-ups.'],
];
let GS_TOUR: GsTour | null = null;

function gsTourStart(): void {
  if (GS_TOUR) return;
  const phone = drawerMedia.matches;
  if (phone) setDrawer(true, false, $('#mobile-more'));
  // Docs, Strategy and Meetings sit in Company and Library, which start folded: the tour shows them open while it runs
  // and leaves the person's own fold as it was (getting-started.css html.gs-touring).
  document.documentElement.classList.add('gs-touring');
  const steps = GS_STEPS.filter(([selector]) => {
    const el = $('#side ' + selector);
    return el && el.getClientRects().length;
  });
  if (!steps.length) { document.documentElement.classList.remove('gs-touring'); if (phone) setDrawer(false); return; }
  const root = document.createElement('div');
  root.className = 'gs-tour';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-labelledby', 'gs-tour-title');
  root.innerHTML = `<div class="gs-tour-hole"></div><div class="gs-tour-card">
    <p class="gs-tour-count muted"></p><h2 id="gs-tour-title"></h2><p class="gs-tour-text"></p>
    <div class="gs-tour-actions"><button class="ghost" type="button" data-tour-skip>Skip</button>
      <button class="primary" type="button" data-tour-next>Next</button></div></div>`;
  document.body.appendChild(root);
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const tour: GsTour = {root, steps, at: 0, phone, opener};
  GS_TOUR = tour;
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); gsTourEnd(); }
    else if (event.key === 'Tab') {
      const buttons: (HTMLButtonElement | null)[] = [...root.querySelectorAll<HTMLButtonElement>('button')];
      const focused = document.activeElement;
      const i = buttons.indexOf(focused instanceof HTMLButtonElement ? focused : null);
      event.preventDefault();
      buttons[(i + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length]?.focus();
    }
  };
  const onFocus = (event: FocusEvent) => {
    if (!(event.target instanceof Node && root.contains(event.target))) root.querySelector<HTMLButtonElement>('[data-tour-next]')?.focus();
  };
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('focusin', onFocus);
  window.addEventListener('resize', gsTourPlace);
  tour.off = () => {
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('focusin', onFocus);
    window.removeEventListener('resize', gsTourPlace);
  };
  const skip = root.querySelector<HTMLButtonElement>('[data-tour-skip]');
  if (skip) skip.onclick = () => gsTourEnd();
  const next = root.querySelector<HTMLButtonElement>('[data-tour-next]');
  if (next) next.onclick = () => {
    if (tour.at === steps.length - 1) gsTourEnd(); else { tour.at++; gsTourShow(); }
  };
  gsTourShow();
  next?.focus();
}

function gsTourShow(): void {
  if (!GS_TOUR) return;
  const {root, steps, at} = GS_TOUR;
  const step = steps[at];
  if (!step) return;
  const [selector, title, text] = step;
  const count = root.querySelector<HTMLElement>('.gs-tour-count'), heading = root.querySelector<HTMLElement>('#gs-tour-title');
  const body = root.querySelector<HTMLElement>('.gs-tour-text'), next = root.querySelector<HTMLButtonElement>('[data-tour-next]');
  if (!count || !heading || !body || !next) return;
  count.textContent = `${at + 1} of ${steps.length}`;
  heading.textContent = title;
  body.textContent = text;
  next.textContent = at === steps.length - 1 ? 'Done' : 'Next';
  $('#side ' + selector)?.scrollIntoView({block: 'nearest'});
  gsTourPlace();
}

function gsTourPlace(): void {
  if (!GS_TOUR) return;
  const {root, steps, at} = GS_TOUR;
  const target = $('#side ' + steps[at]?.[0]);
  if (!target) return;
  const box = target.getBoundingClientRect(), pad = 4;
  const hole = root.querySelector<HTMLElement>('.gs-tour-hole'), card = root.querySelector<HTMLElement>('.gs-tour-card');
  if (!hole || !card) return;
  Object.assign(hole.style, {left: box.left - pad + 'px', top: box.top - pad + 'px',
    width: box.width + pad * 2 + 'px', height: Math.min(box.height, innerHeight - box.top) + pad * 2 + 'px'});
  const width = card.offsetWidth, height = card.offsetHeight;
  if (GS_TOUR.phone) {
    // The list is in the drawer, so the card takes whichever end of the screen the row is not at.
    const low = box.top + box.height / 2 > innerHeight / 2;
    Object.assign(card.style, {left: '12px', right: '12px', width: 'auto', top: low ? '12px' : 'auto', bottom: low ? 'auto' : '12px'});
    return;
  }
  Object.assign(card.style, {right: 'auto', bottom: 'auto', width: '',
    left: Math.min(box.right + 16, innerWidth - width - 12) + 'px',
    top: Math.max(12, Math.min(box.top, innerHeight - height - 12)) + 'px'});
}

function gsTourEnd(): void {
  if (!GS_TOUR) return;
  const {root, phone, opener} = GS_TOUR;
  GS_TOUR.off?.();
  root.remove();
  document.documentElement.classList.remove('gs-touring');
  GS_TOUR = null;
  if (phone) setDrawer(false, true);
  // Started from the account menu, which closed as the tour took focus: back to the button that opens it.
  else if (opener?.closest?.('#account-menu')) $('#account').focus();
  else if (opener && opener.isConnected) opener.focus();
  void gsState({tour: true});
}

window.gsStartTour = gsTourStart;
window.gsBoot = function () {
  // The first look around, once per person, never over setup: the owner who set the team up has it marked seen
  // (ui/app/welcome.ts) and finds it in the account menu; someone who joins later gets it on their first visit. The
  // owner of a team that came in from the company file gets the welcome (onbWelcome) in its place.
  void gsRefresh().then(async seen => {
    if (!seen || seen.tour_seen || S.config?.onboarding_needed || S.route === '#/welcome') return;
    // Only the owner of a team that came in from the company file gets the welcome: its code (a page part,
    // ui/parts.json, with Settings') loads only then, so every other first visit stays the shell alone.
    if (S.me?.role !== 'owner') { gsTourStart(); return; }
    const setup = await get<Partial<OnbRecord> | null>('/v2/setup').catch(() => null);
    if (!(setup?.completed && setup.company_import && await onbWelcome(setup))) gsTourStart();
  });
  // What is set up changes seldom, and this tab's own steps write it at once (gsState): every 5 minutes is enough.
  setInterval(() => { if (!document.hidden && S.me?.cloud) void gsRefresh(); }, 300000);
};

// ---------------------------------------------------------------- one listener for all of it
document.addEventListener('click', (event: MouseEvent) => {
  const t = event.target;
  if (!(t instanceof Element)) return;
  if (t.closest('[data-gs-tour]')) { gsTourStart(); return; }
  const link = t.closest<HTMLElement>('[data-gs-tab]');
  if (link) {
    event.preventDefault();
    SETTINGS_TAB = String(link.dataset.gsTab);
    if (location.hash === link.getAttribute('href')) route(); else location.hash = String(link.getAttribute('href'));
  }
});
