/* ui/app/org-factories.ts — Strategy › Org: the Factories section under the people chart (redesign-org.js mounts it). Each
   factory is a locked box, named on its tab. Its Factory Manager sits on top when it has two or more pipelines (D59), and
   under it one Pipeline Manager card per pipeline. A card's name opens its bot; its chip opens the pipeline, by the route
   the Factories map uses. Pipelines outside every factory share one box after them. The cards come from the pipeline and
   factory setup, so none of them moves, folds or opens an edit menu. The read is the overview the map reads
   (GET /api/v2/pipelines/overview, backend/pipeline_overview.py), which already leaves out a members-only pipeline for
   anyone who is not on it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const ORG_FAC_LOCK = '<svg aria-hidden="true" width="9" height="11" viewBox="0 0 10 12" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="5" width="8" height="6" rx="1"></rect><path d="M3 5V3.5a2 2 0 014 0V5"></path></svg>';

/** A manager reference without its bot: prefix (task_types.manager and factories.manager_id are stored either way). */
const orgBotId = (ref: string): string => ref.replace(/^bot:/, '');

/** One locked card. With a manager, its name opens the bot; `chip` (a pipeline) opens that pipeline. */
function orgCardHtml(kind: 'fm' | 'pm', manager: string | null, chip: OfPipeline | null): string {
  const id = manager ? orgBotId(manager) : '';
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
  const body = g.manager && g.pipelines.length >= 2
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

/** Draws the section into host, below the chart. A failed read, or one without the overview's lists, leaves the chart alone;
 *  so does a host the page has left. */
async function orgFactoriesMount(host: HTMLElement): Promise<void> {
  const map: OfMap | null = await get('/v2/pipelines/overview?period=7d', {fresh: true}).catch(() => null);
  if (!Array.isArray(map?.factories) || !Array.isArray(map?.pipelines) || !host.isConnected) return;
  host.innerHTML = orgFactoriesHtml(map);
}
