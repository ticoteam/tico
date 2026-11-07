/* ui/app/org-factories.ts — Strategy › Org: the Factories section under the people chart (redesign-org.js mounts it). Each
   factory is a locked box, named on its tab. Its Factory Manager sits on top when it has two or more pipelines (D59), and
   under it one Pipeline Manager card per pipeline. A card's name opens its bot; its chip opens the pipeline, by the route
   the Factories map uses. Pipelines outside every factory share one box after them. The cards come from the pipeline and
   factory setup, so none of them moves, folds or opens an edit menu. The read is the overview the map reads
   (GET /api/v2/pipelines/overview, backend/pipeline_overview.py), which already leaves out a members-only pipeline for
   anyone who is not on it.
   The bots this section draws are not on the people chart too: orgSetupLift takes them out of it, and their reports
   hang where they stood.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const ORG_FAC_LOCK = '<svg aria-hidden="true" width="9" height="11" viewBox="0 0 10 12" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="5" width="8" height="6" rx="1"></rect><path d="M3 5V3.5a2 2 0 014 0V5"></path></svg>';

/** The roster ids of the bots the section draws, set when it is drawn: the people chart leaves them out. */
let ORG_FAC_BOTS: Set<string> = new Set();

/** A manager reference without its bot: prefix (task_types.manager and factories.manager_id are stored either way). */
const orgBotId = (ref: string): string => ref.replace(/^bot:/, '');
/** The roster id a manager reference names. Setup can store the slug instead of the id, and botByRef reads either. */
const orgBotOf = (ref: string): string => botByRef(ref)?.id || orgBotId(ref);
/** A factory's Factory Manager is drawn only when it has two or more pipelines (D59). */
const orgShowsFm = (g: OfGroup): boolean => !!g.manager && g.pipelines.length >= 2;

/** One locked card. With a manager, its name opens the bot; `chip` (a pipeline) opens that pipeline. */
function orgCardHtml(kind: 'fm' | 'pm', manager: string | null, chip: OfPipeline | null): string {
  const id = manager ? orgBotOf(manager) : '';
  const head = manager
    ? `<a class="org-fac-open" href="#/bot/${encodeURIComponent(id)}"><b>${esc(botDisplayName(id))}</b></a><span class="rdo-r">${avatar(id, 16)}</span>`
    : '<b>No manager</b>';
  const link = chip
    ? `<a class="org-fac-chip" href="${esc(ofPipelineHref(chip.id))}"${chip.goal ? ` title="${esc(chip.goal)}"` : ''}>${esc(chip.name)} <span aria-hidden="true">→</span></a>`
    : '';
  return `<div class="rdo-card org-fac-card ${kind}" data-org-card="${kind}">${head}${link}`
    + `<span class="org-fac-lock" role="img" aria-label="Set by Factories" title="Set by Factories">${ORG_FAC_LOCK}</span></div>`;
}

/** The Pipeline Manager cards of a box, one list item each. */
const orgPipelineItems = (pipelines: OfPipeline[]): string =>
  pipelines.map(p => `<li>${orgCardHtml('pm', p.manager_id, p)}</li>`).join('');

/** A factory's box: its Factory Manager on top when it has two or more pipelines, then a card per pipeline. */
function orgFactoryBoxHtml(g: OfGroup): string {
  const body = orgShowsFm(g)
    ? `<div class="rdo-br"><div class="rdo-top has-row">${orgCardHtml('fm', g.manager, null)}</div><ul class="rdo-row">${orgPipelineItems(g.pipelines)}</ul></div>`
    : `<ul class="org-fac-row">${orgPipelineItems(g.pipelines)}</ul>`;
  return `<section class="rdo-g org-fac-box" data-org-fac="${esc(g.id)}" aria-label="${esc(g.name)}">`
    + `<span class="rdo-tab"><span class="rdo-tn">${esc(g.name)} <span class="org-fac-kind">· factory</span></span></span>`
    + `<div class="rdo-gb">${body}</div></section>`;
}

/** The pipelines in no factory: one box after the factories, with no manager on top. */
const orgLooseBoxHtml = (g: OfGroup): string =>
  `<section class="rdo-g org-fac-box" data-org-loose aria-label="Not in a factory">`
  + `<span class="rdo-tab"><span class="rdo-tn">Not in a factory</span></span>`
  + `<div class="rdo-gb"><ul class="org-fac-row">${orgPipelineItems(g.pipelines)}</ul></div></section>`;

/** The section for an overview, or '' when there are no factories: nothing to draw, so no empty box. */
function orgFactoriesHtml(map: OfMap): string {
  if (!map.factories.length) return '';
  const boxes = ofGroups(map).map(g => g.id ? orgFactoryBoxHtml(g) : orgLooseBoxHtml(g)).join('');
  return `<section class="org-fac" aria-labelledby="org-fac-title">`
    + `<div class="org-fac-head"><h2 id="org-fac-title">Factories</h2>`
    + `<span class="org-fac-key"><i class="org-fac-ring fm" aria-hidden="true"></i>Factory bot<i class="org-fac-ring pm" aria-hidden="true"></i>Pipeline bot</span></div>`
    + `<div class="rdo-cv org-fac-cv"><div class="org-fac-boxes">${boxes}</div></div></section>`;
}

/** The roster ids of the bots the section draws as cards: each Pipeline Manager, and each Factory Manager shown. Nothing
 *  when there are no factories, since the section then draws nothing and the people chart keeps every bot. */
function orgDrawnBots(map: OfMap): Set<string> {
  const bots = new Set<string>();
  if (!map.factories.length) return bots;
  for (const g of ofGroups(map)) {
    if (g.manager && orgShowsFm(g)) bots.add(orgBotOf(g.manager));
    for (const p of g.pipelines) if (p.manager_id) bots.add(orgBotOf(p.manager_id));
  }
  return bots;
}

/** Takes the bots in `bots` out of the people chart's map, `by` (changed in place). Each one's reports take its place
 *  under the group or manager above, so nobody under a manager goes missing. The map keys are the chart's (`g:`, `p:`, `b:`). */
type OrgChartNode = {kind: string; id: string};
function orgSetupLift(by: Record<string, OrgChartNode[]>, bots: Set<string>): void {
  if (!bots.size) return;
  const seen = new Set<string>();
  const lift = (nodes: OrgChartNode[]): OrgChartNode[] => nodes.flatMap((n): OrgChartNode[] => {
    if (n.kind !== 'bot' || !bots.has(n.id)) return [n];
    if (seen.has(n.id)) return [];
    seen.add(n.id);
    return lift(by['b:' + n.id] || []);
  });
  // A removed bot's own list is read through its place above, so it is not lifted twice.
  for (const key of Object.keys(by)) if (!(key.startsWith('b:') && bots.has(key.slice(2)))) by[key] = lift(by[key] || []);
}

/** Draws the section into host, below the chart, and records its bots for the people chart. Resolves true when the chart
 *  must be drawn again (it now leaves bots out). A failed read, or one without the overview's lists, leaves the chart
 *  alone; so does a host the page has left. */
async function orgFactoriesMount(host: HTMLElement): Promise<boolean> {
  const map: OfMap | null = await get('/v2/pipelines/overview?period=7d', {fresh: true}).catch(() => null);
  if (!Array.isArray(map?.factories) || !Array.isArray(map?.pipelines) || !host.isConnected) return false;
  ORG_FAC_BOTS = orgDrawnBots(map);
  host.innerHTML = orgFactoriesHtml(map);
  return ORG_FAC_BOTS.size > 0;
}
