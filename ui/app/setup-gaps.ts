/* ui/app/setup-gaps.ts — Setup in Flow: the marks on the setup pages, and a tap on one
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.

   GET /v2/setup/gaps (backend/setup_gaps.py) says which areas (Market, Strategy, Goals, Org, Tools, Budget) still wait on
   the person and the bot that asks for each. The Strategy tabs carry Flow's mark for theirs (redesign.ts
   strategyTabNeeds), and a page draws a holder for its own area (`data-setup-mark="<area>"`). A write that may open or
   close a gap rings the `setup` topic (backend/events.py), which reads the gaps again, so a mark goes the moment its
   area is filled, by whoever fills it. A tap on a mark opens Flow on that area's bot and pings it with the area: the
   bot asks one ❓ at a time and fills the area in (skills/setup-in-flow). */
'use strict';

interface SetupGap {area: string; needs: boolean; bot: {id: string; slug: string; name: string} | null; href: string}
const SETUP_GAPS: {list: SetupGap[]; live: boolean} = {list: [], live: false};
const SETUP_STRAT_TABS = ['market', 'strategy', 'org', 'goals', 'budget'];

async function setupGapsRead(): Promise<void> {
  try {
    const r = await get<{areas?: SetupGap[]}>('/v2/setup/gaps', {fresh: true});
    SETUP_GAPS.list = Array.isArray(r?.areas) ? r.areas : [];
  } catch {
    return;      // offline, or a server without setup gaps: the marks stay as they are
  }
  setupGapsPaint();
}

function setupGapsPaint(): void {
  const open = new Set(SETUP_GAPS.list.filter(g => g.needs).map(g => g.area));
  for (const tab of SETUP_STRAT_TABS) strategyTabNeeds(tab, open.has(tab));
  document.querySelectorAll<HTMLElement>('[data-setup-mark]').forEach(el => {
    const area = el.dataset.setupMark || '';
    el.innerHTML = open.has(area) ? needsMarkHTML(Number(el.dataset.size) || 12, area) : '';
  });
}

// Called as a setup page draws its header: the first call reads and joins the `setup` topic; later ones paint what is
// known once the page is on screen (the tabs already drew from it).
function setupGapsWatch(): void {
  if (SETUP_GAPS.live) { setTimeout(setupGapsPaint); return; }
  SETUP_GAPS.live = true;
  const soon = () => liveSoon('setup-gaps', () => void setupGapsRead(), 300);
  liveOn('setup', soon);
  liveOn('reset', soon);
  liveTopicAdd('setup');
  void setupGapsRead();
}

// A tap on a mark: Flow on the area's bot, which is asked now (decideBotsFlow `ask`). The mark sits in a tab or a
// heading link, which it does not follow.
async function setupGapsOpen(el: HTMLElement): Promise<void> {
  const area = el.dataset.setupArea || '';
  if (!SETUP_GAPS.list.length) await setupGapsRead();
  const bot = SETUP_GAPS.list.find(g => g.area === area)?.bot;
  if (!bot) { toast('No bot to ask'); return; }
  await decideBotsFlow(el, [bot.id], bot.name, {scope: 'setup', id: area}, true);
}
for (const type of ['click', 'keydown'] as const) {
  document.addEventListener(type, ev => {
    const el = eventElement(ev)?.closest<HTMLElement>('[data-setup-area]');
    if (!el || ev instanceof KeyboardEvent && ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    ev.stopPropagation();
    void setupGapsOpen(el);
  }, true);
}
