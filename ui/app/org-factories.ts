/* ui/app/org-factories.ts — Strategy › Org: the Factories section under the people chart (redesign-org.js mounts it). Each
   factory is a locked box, named on its tab. Its Factory Manager sits on top when it has two or more pipelines (D59), and
   under it one Pipeline Manager card per pipeline. A card's name opens its bot; its chip opens the pipeline, by the route
   the Factories map uses. Pipelines outside every factory share one box after them. The cards come from the pipeline and
   factory setup, so none of them moves, folds or opens an edit menu. The read is the one Company › Factories reads
   (overview-factories.ts ofRead: GET /api/v2/factories and GET /api/v2/pipelines). A pipeline can't be private yet
   (pipeline_settings refuses it): everyone on the team reads every pipeline.
   A pipeline's manager is its team's Manager, so the bots this section draws stay on the people chart, with their
   reports under them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const ORG_FAC_LOCK = '<svg aria-hidden="true" width="9" height="11" viewBox="0 0 10 12" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="5" width="8" height="6" rx="1"></rect><path d="M3 5V3.5a2 2 0 014 0V5"></path></svg>';

/** A manager reference without its bot: prefix (task_types.manager and factories.manager_id are stored either way). */
const orgBotId = (ref: string): string => ref.replace(/^bot:/, '');
/** The roster id a manager reference names. Setup can store the slug instead of the id, and botByRef reads either. */
const orgBotOf = (ref: string): string => botByRef(ref)?.id || orgBotId(ref);
/** A factory's Factory Manager is drawn only when it has two or more pipelines (D59). */
const orgShowsFm = (g: OfGroup): boolean => !!g.manager && g.pipelines.length >= 2;

/** One locked card. With a manager, its name opens the bot; `chip` (a pipeline) opens that pipeline. */
function orgCardHtml(kind: 'fm' | 'pm', manager: string | null, chip: OfBox | null): string {
  const id = manager ? orgBotOf(manager) : '';
  // With no manager the card is the pipeline's: its name is the title and opens it, over an empty manager slot.
  const head = manager
    ? `<a class="org-fac-open" href="#/bot/${encodeURIComponent(id)}"><b>${esc(botDisplayName(id))}</b></a><span class="rdo-r">${avatar(id, 16)}</span>`
    : chip ? `<a class="org-fac-open" href="${esc(pipelineHref(chip.id))}"${chip.goal ? ` title="${esc(chip.goal)}"` : ''}><b>${esc(chip.name)}</b></a>`
      + '<span class="rdo-r"><i class="org-fac-empty" role="img" aria-label="No manager" title="No manager"></i></span>'
    : '<span class="rdo-r"><i class="org-fac-empty" role="img" aria-label="No manager" title="No manager"></i></span>';
  const link = chip && manager
    ? `<a class="org-fac-chip" href="${esc(pipelineHref(chip.id))}"${chip.goal ? ` title="${esc(chip.goal)}"` : ''}>${esc(chip.name)} <span aria-hidden="true">→</span></a>`
    : '';
  return `<div class="rdo-card org-fac-card ${kind}" data-org-card="${kind}">${head}${link}`
    + `<span class="org-fac-lock" role="img" aria-label="Set by Factories" title="Set by Factories">${ORG_FAC_LOCK}</span></div>`;
}

/** The Pipeline Manager cards of a box, one list item each. */
const orgPipelineItems = (pipelines: OfBox[]): string =>
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

const orgHasFactory = (groups: OfGroup[]): boolean => groups.some(g => g.id);

/** The section for the groups, or '' when there are no factories: nothing to draw, so no empty box. */
function orgFactoriesHtml(groups: OfGroup[]): string {
  if (!orgHasFactory(groups)) return '';
  const boxes = groups.map(g => g.id ? orgFactoryBoxHtml(g) : orgLooseBoxHtml(g)).join('');
  return `<section class="org-fac" aria-labelledby="org-fac-title">`
    + `<div class="org-fac-head"><h2 id="org-fac-title">Factories</h2>`
    + `<span class="org-fac-key"><i class="org-fac-ring fm" aria-hidden="true"></i>Factory bot<i class="org-fac-ring pm" aria-hidden="true"></i>Pipeline bot</span></div>`
    + `<div class="rdo-cv org-fac-cv"><div class="org-fac-boxes">${boxes}</div></div></section>`;
}

/** Draws the section into host, below the chart. A failed read draws nothing; so does a host the page has left. */
async function orgFactoriesMount(host: HTMLElement): Promise<void> {
  const groups = await ofRead('7d').catch(() => null);
  if (!groups || !host.isConnected) return;
  host.innerHTML = orgFactoriesHtml(groups);
}
