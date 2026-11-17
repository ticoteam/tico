/* ui/app/org-factories.ts — Strategy › Org: the Factories section under the people chart (redesign-org.js mounts it). Each
   factory is a locked box, named on its tab. Its Factory Manager sits on top when it has two or more pipelines (D59), and
   under it one Pipeline Manager card per pipeline. A card's name opens its bot; its chip opens the pipeline on the Tasks
   page. Pipelines outside every factory share one box after them. The cards come from the pipeline and factory setup, so
   none of them moves, folds or opens an edit menu. Reads: GET /api/v2/factories and GET /api/v2/pipelines
   (pipeline-api.ts, task-api.ts). A pipeline the reader may not see is not in either answer.
   The company's owner and Admins get a quiet + beside the title that makes a factory (POST /api/v2/factories), so the
   section shows for them even before there is one.
   A pipeline's manager is its team's Manager, so the bots this section draws stay on the people chart, with their
   reports under them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A pipeline's card: its manager, and the pipelines it files into (which order a factory's cards). */
interface OrgFacBox { id: string; name: string; goal: string; manager_id: string | null; into: string[] }
/** A factory with its pipelines in the order work moves through them, or the pipelines in no factory (id ''). */
interface OrgFacGroup { id: string; name: string; manager: string | null; pipelines: OrgFacBox[] }

const ORG_FAC_LOCK = '<svg aria-hidden="true" width="9" height="11" viewBox="0 0 10 12" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="5" width="8" height="6" rx="1"></rect><path d="M3 5V3.5a2 2 0 014 0V5"></path></svg>';

/** Orders boxes so a pipeline comes before the ones it files into, keeping the given order otherwise. */
function orgFacOrder(boxes: OrgFacBox[]): OrgFacBox[] {
  const fed = new Set(boxes.flatMap(b => b.into));
  const out: OrgFacBox[] = [], seen = new Set<string>();
  const walk = (b: OrgFacBox): void => {
    if (seen.has(b.id)) return;
    seen.add(b.id); out.push(b);
    for (const to of b.into) { const next = boxes.find(x => x.id === to); if (next) walk(next); }
  };
  for (const b of boxes) if (!fed.has(b.id)) walk(b);
  for (const b of boxes) walk(b);      // a loop with no start
  return out;
}

/** The factories and their live pipelines, then one group of the live pipelines in no factory. */
function orgFacGroups(map: FactoryMap, recs: PipelineRec[]): OrgFacGroup[] {
  const counts = new Map(map.pipelines.map(p => [p.id, p]));
  const box = (rec: PipelineRec): OrgFacBox => ({id: rec.id, name: rec.name, goal: rec.goal, manager_id: rec.manager_id,
    into: counts.get(rec.id)?.files_into ?? pipelineFilesInto(rec)});
  const live = recs.filter(r => !r.retired_at);
  const factoryOf = (rec: PipelineRec): string => rec.factory_id ?? counts.get(rec.id)?.factory_id ?? '';
  const ids = new Set(map.factories.map(f => f.id));
  const groups: OrgFacGroup[] = map.factories.map(f => ({id: f.id, name: f.name, manager: f.manager_id,
    pipelines: orgFacOrder(live.filter(r => factoryOf(r) === f.id).map(box))}));
  const loose = live.filter(r => !ids.has(factoryOf(r))).map(box);
  if (loose.length) groups.push({id: '', name: 'Other', manager: null, pipelines: orgFacOrder(loose)});
  return groups;
}

async function orgFacRead(): Promise<OrgFacGroup[]> {
  const [map, recs] = await Promise.all([factoriesRead('7d'), pipelinesAll()]);
  return orgFacGroups(map, recs);
}

/** A manager reference without its bot: prefix (task_types.manager and factories.manager_id are stored either way). */
const orgBotId = (ref: string): string => ref.replace(/^bot:/, '');
/** The roster id a manager reference names. Setup can store the slug instead of the id, and botByRef reads either. */
const orgBotOf = (ref: string): string => botByRef(ref)?.id || orgBotId(ref);
/** A factory's Factory Manager is drawn only when it has two or more pipelines (D59). */
const orgShowsFm = (g: OrgFacGroup): boolean => !!g.manager && g.pipelines.length >= 2;
/** The company's owner and Admins make factories (the server refuses anyone else). */
const orgFacMayMake = (): boolean => S.me?.role === 'owner' || S.me?.bot_admin === true;

/** One locked card. With a manager, its name opens the bot; `chip` (a pipeline) opens that pipeline. */
function orgCardHtml(kind: 'fm' | 'pm', manager: string | null, chip: OrgFacBox | null): string {
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
const orgPipelineItems = (pipelines: OrgFacBox[]): string =>
  pipelines.map(p => `<li>${orgCardHtml('pm', p.manager_id, p)}</li>`).join('');

/** A factory's box: its Factory Manager on top when it has two or more pipelines, then a card per pipeline. */
function orgFactoryBoxHtml(g: OrgFacGroup): string {
  const body = orgShowsFm(g)
    ? `<div class="rdo-br"><div class="rdo-top has-row">${orgCardHtml('fm', g.manager, null)}</div><ul class="rdo-row">${orgPipelineItems(g.pipelines)}</ul></div>`
    : `<ul class="org-fac-row">${orgPipelineItems(g.pipelines)}</ul>`;
  return `<section class="rdo-g org-fac-box" data-org-fac="${esc(g.id)}" aria-label="${esc(g.name)}">`
    + `<span class="rdo-tab"><span class="rdo-tn">${esc(g.name)} <span class="org-fac-kind">· factory</span></span></span>`
    + `<div class="rdo-gb">${body}</div></section>`;
}

/** The pipelines in no factory: one box after the factories, with no manager on top. */
const orgLooseBoxHtml = (g: OrgFacGroup): string =>
  `<section class="rdo-g org-fac-box" data-org-loose aria-label="Not in a factory">`
  + `<span class="rdo-tab"><span class="rdo-tn">Not in a factory</span></span>`
  + `<div class="rdo-gb"><ul class="org-fac-row">${orgPipelineItems(g.pipelines)}</ul></div></section>`;

const orgHasFactory = (groups: OrgFacGroup[]): boolean => groups.some(g => g.id);

/** + New factory's form, under the title until it is saved or cancelled. */
const ORG_FAC_NEW = `<form class="org-fac-new" id="org-fac-new" hidden>
    <label>Name <input name="name" required maxlength="80" autocomplete="off"></label>
    <label>Goal <input name="goal" maxlength="500" autocomplete="off"></label>
    <div class="org-fac-new-acts"><button type="button" class="ghost" data-org-fac-cancel>Cancel</button><button type="submit" class="primary">Save</button></div>
    <p class="org-fac-new-err muted" role="alert" hidden></p></form>`;

/** The section for the groups. With no factory there is nothing to draw ('') unless the reader may make one: then the
    title and its + alone. */
function orgFactoriesHtml(groups: OrgFacGroup[]): string {
  const may = orgFacMayMake();
  if (!orgHasFactory(groups) && !may) return '';
  const boxes = orgHasFactory(groups) ? groups.map(g => g.id ? orgFactoryBoxHtml(g) : orgLooseBoxHtml(g)).join('') : '';
  return `<section class="org-fac" aria-labelledby="org-fac-title">`
    + `<div class="org-fac-head"><h2 id="org-fac-title">Factories</h2>`
    + (may ? pageHeadAdd('New factory', 'data-org-fac-add aria-expanded="false" aria-controls="org-fac-new"') : '')
    + (boxes ? `<span class="org-fac-key"><i class="org-fac-ring fm" aria-hidden="true"></i>Factory bot<i class="org-fac-ring pm" aria-hidden="true"></i>Pipeline bot</span>` : '')
    + `</div>${may ? ORG_FAC_NEW : ''}`
    + (boxes ? `<div class="rdo-cv org-fac-cv"><div class="org-fac-boxes">${boxes}</div></div>` : '') + `</section>`;
}

function orgFacNewToggle(host: HTMLElement, open: boolean): void {
  const form = host.querySelector<HTMLFormElement>('#org-fac-new');
  if (!form) return;
  form.hidden = !open;
  host.querySelector('[data-org-fac-add]')?.setAttribute('aria-expanded', String(open));
  if (open) form.querySelector<HTMLInputElement>('[name=name]')?.focus();
  else form.reset();
}

async function orgFacNewSave(host: HTMLElement, form: HTMLFormElement): Promise<void> {
  const name = (form.querySelector<HTMLInputElement>('[name=name]')?.value || '').trim();
  const goal = (form.querySelector<HTMLInputElement>('[name=goal]')?.value || '').trim();
  const err = form.querySelector<HTMLElement>('.org-fac-new-err');
  if (!name) return;
  try {
    await factoryCreate(name, goal);
    await orgFactoriesMount(host);
  } catch (error) {
    // The server's own line: a taken name (409) says so.
    const detail = errorBody(error)['detail'];
    if (err) { err.textContent = typeof detail === 'string' && detail ? detail : errorMessage(error, 'Could not save the factory.'); err.hidden = false; }
  }
}

/** Draws the section into host, below the chart. A failed read draws nothing; so does a host the page has left. */
async function orgFactoriesMount(host: HTMLElement): Promise<void> {
  const groups = await orgFacRead().catch(() => null);
  if (!groups || !host.isConnected) return;
  host.innerHTML = orgFactoriesHtml(groups);
  host.onclick = ev => {
    const t = eventElement(ev);
    if (t?.closest('[data-org-fac-add]')) orgFacNewToggle(host, !!host.querySelector<HTMLFormElement>('#org-fac-new')?.hidden);
    else if (t?.closest('[data-org-fac-cancel]')) orgFacNewToggle(host, false);
  };
  host.onsubmit = ev => {
    ev.preventDefault();
    if (ev.target instanceof HTMLFormElement) void orgFacNewSave(host, ev.target);
  };
}
