/* ui/app/overview-factories.ts — Company › Factories (docs/pipelines-v3.md S9, S12). One block per factory: its name and
   how many of its tasks reached Done in the period (Closed never counts). Inside, one box per pipeline: its name, how
   many tasks are open, and a mark when it has one (red: a bot in it can't run; amber "!": it needs a person). An arrow
   joins a pipeline to the one it files into. Pipelines in no factory sit in "Other", with no count. A box opens the
   pipeline on the Tasks page. Day, Week and Month choose the period, kept in this browser.
   Reads: GET /api/v2/factories?period= and GET /api/v2/pipelines (pipeline-api.ts, task-api.ts).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A pipeline's box. */
interface OfBox { id: string; name: string; goal: string; manager_id: string | null; open: number; mark: 'cant' | 'person' | null; into: string[] }
/** A factory with its pipelines in the order work moves through them, or the pipelines in no factory (id ''). */
interface OfGroup { id: string; name: string; manager: string | null; done: number | null; pipelines: OfBox[] }

const OF_PERIODS: [string, string][] = [['1d', 'Day'], ['7d', 'Week'], ['30d', 'Month']];
const OF_PERIOD_KEY = 'tico.factories.period';
let OF_RUN = 0;

// The period this viewer last chose. Browser storage may be blocked, so a miss is the week.
function ofPeriodRead(): string {
  try {
    const kept = localStorage.getItem(OF_PERIOD_KEY);
    if (kept && OF_PERIODS.some(([p]) => p === kept)) return kept;
  } catch { /* storage blocked: the default */ }
  return '7d';
}
function ofPeriodKeep(period: string): void {
  try { localStorage.setItem(OF_PERIOD_KEY, period); } catch { /* storage blocked: not kept */ }
}

/** Orders boxes so a pipeline comes before the ones it files into, keeping the given order otherwise. */
function ofFlowOrder(boxes: OfBox[]): OfBox[] {
  const fed = new Set(boxes.flatMap(b => b.into));
  const out: OfBox[] = [], seen = new Set<string>();
  const walk = (b: OfBox): void => {
    if (seen.has(b.id)) return;
    seen.add(b.id); out.push(b);
    for (const to of b.into) { const next = boxes.find(x => x.id === to); if (next) walk(next); }
  };
  for (const b of boxes) if (!fed.has(b.id)) walk(b);
  for (const b of boxes) walk(b);      // a loop with no start
  return out;
}

/** The factories and their pipelines, then "Other": the live pipelines in no factory. */
function ofGroups(map: FactoryMap, recs: PipelineRec[]): OfGroup[] {
  const counts = new Map(map.pipelines.map(p => [p.id, p]));
  const box = (rec: PipelineRec): OfBox => ({id: rec.id, name: rec.name, goal: rec.goal, manager_id: rec.manager_id,
    open: counts.get(rec.id)?.open ?? 0, mark: counts.get(rec.id)?.mark ?? null, into: counts.get(rec.id)?.files_into ?? pipelineFilesInto(rec)});
  const live = recs.filter(r => !r.retired_at);
  const factoryOf = (rec: PipelineRec): string => rec.factory_id ?? counts.get(rec.id)?.factory_id ?? '';
  const ids = new Set(map.factories.map(f => f.id));
  const groups: OfGroup[] = map.factories.map(f => ({id: f.id, name: f.name, manager: f.manager_id, done: f.done,
    pipelines: ofFlowOrder(live.filter(r => factoryOf(r) === f.id).map(box))}));
  const loose = live.filter(r => !ids.has(factoryOf(r))).map(box);
  if (loose.length) groups.push({id: '', name: 'Other', manager: null, done: null, pipelines: ofFlowOrder(loose)});
  return groups;
}

/** The factories and their pipelines over a period, as the map and Strategy › Org draw them. */
async function ofRead(period: string): Promise<OfGroup[]> {
  const [map, recs] = await Promise.all([factoriesRead(period), pipelinesAll()]);
  return ofGroups(map, recs);
}

const OF_MARK: Record<string, string> = {cant: "Can't run", person: 'Needs a person'};
function ofBoxHTML(b: OfBox): string {
  const mark = b.mark ? `<span class="tk-mark tk-mark-${b.mark}" role="img" aria-label="${OF_MARK[b.mark]}">${b.mark === 'person' ? '!' : ''}</span>` : '';
  return `<a class="of-box" href="${esc(pipelineHref(b.id))}"${b.goal ? ` title="${esc(b.goal)}"` : ''}>`
    + `<span class="of-box-name">${esc(b.name)}</span><span class="of-box-n tnum">${b.open}</span>${mark}</a>`;
}

function ofGroupHTML(g: OfGroup): string {
  const boxes = g.pipelines.map((b, i) => {
    const next = g.pipelines[i + 1];
    const arrow = next && b.into.includes(next.id) ? '<span class="of-arrow" aria-label="files into">→</span>' : '';
    return ofBoxHTML(b) + arrow;
  }).join('');
  const done = g.done == null ? '' : `<span class="of-done tnum">${g.done} done</span>`;
  return `<section class="of-factory" aria-label="${esc(g.name)}"><header><h2>${esc(g.name)}</h2>${done}</header>`
    + `<div class="of-boxes">${boxes}</div></section>`;
}

async function ofLoad(period: string): Promise<void> {
  const body = document.getElementById('of-body');
  if (!body) return;
  const run = ++OF_RUN;
  try {
    const groups = await ofRead(period);
    if (run !== OF_RUN || !document.getElementById('of-body')) return;
    body.innerHTML = groups.length ? groups.map(ofGroupHTML).join('') : '<p class="of-empty muted">No pipelines yet</p>';
  } catch {
    if (run === OF_RUN) body.innerHTML = '<p class="of-empty muted">Could not load</p>';
  }
}

function pageOverviewFactories(): void {
  const main = $('#main'); main.classList.add('overview-layout');
  let period = ofPeriodRead();
  const may = ofMayMakeFactory();
  main.innerHTML = `<section class="overview-page of-page" aria-label="Factories">
    <div class="overview-tabs"><h1>Company</h1>${dashTabsHTML('factories')}<span class="spacer"></span>
      <div class="og-seg" role="group" aria-label="Period" id="of-period">${OF_PERIODS.map(([p, l]) =>
        `<button type="button" data-of-period="${p}" aria-pressed="${p === period}">${l}</button>`).join('')}</div>
      ${may ? '<button type="button" class="ghost of-new-b" id="of-new-b" aria-expanded="false" aria-controls="of-new">+ Factory</button>' : ''}</div>
    ${may ? `<form class="of-new" id="of-new" hidden>
      <label>Name <input name="name" required maxlength="80" autocomplete="off"></label>
      <label>Goal <input name="goal" maxlength="500" autocomplete="off"></label>
      <div class="of-new-acts"><button type="button" class="ghost" data-of-new-cancel>Cancel</button><button type="submit">Save</button></div>
      <p class="of-new-err muted" role="alert" hidden></p></form>` : ''}
    <div class="of-body" id="of-body">${skelRows(3, {dot: false})}</div></section>`;
  document.getElementById('of-period')?.addEventListener('click', ev => {
    const pick = eventElement(ev)?.closest<HTMLElement>('[data-of-period]');
    if (!pick?.dataset['ofPeriod'] || pick.dataset['ofPeriod'] === period) return;
    period = pick.dataset['ofPeriod'];
    ofPeriodKeep(period);
    for (const b of document.querySelectorAll<HTMLElement>('[data-of-period]')) b.setAttribute('aria-pressed', String(b.dataset['ofPeriod'] === period));
    void ofLoad(period);
  });
  document.getElementById('of-new-b')?.addEventListener('click', () => ofNewToggle(true));
  document.querySelector('[data-of-new-cancel]')?.addEventListener('click', () => ofNewToggle(false));
  document.getElementById('of-new')?.addEventListener('submit', ev => {
    ev.preventDefault();
    void ofNewSave(ev.currentTarget as HTMLFormElement, () => period);
  });
  void ofLoad(period);
}

// ---- + Factory: the company's owner and Admins make a factory. The map reads again after a save.
function ofMayMakeFactory(): boolean {
  return S.me?.role === 'owner' || S.me?.bot_admin === true;
}

function ofNewToggle(open: boolean): void {
  const form = document.getElementById('of-new') as HTMLFormElement | null;
  const button = document.getElementById('of-new-b');
  if (!form) return;
  form.hidden = !open;
  button?.setAttribute('aria-expanded', String(open));
  if (open) (form.elements.namedItem('name') as HTMLInputElement | null)?.focus();
  else form.reset();
}

async function ofNewSave(form: HTMLFormElement, period: () => string): Promise<void> {
  const name = String((form.elements.namedItem('name') as HTMLInputElement).value).trim();
  const goal = String((form.elements.namedItem('goal') as HTMLInputElement).value).trim();
  const err = form.querySelector<HTMLElement>('.of-new-err');
  if (!name) return;
  try {
    await factoryCreate(name, goal);
    ofNewToggle(false);
    void ofLoad(period());
  } catch (error) {
    // The server's own line: a taken name (409) says so.
    const detail = errorBody(error)['detail'];
    if (err) { err.textContent = typeof detail === 'string' && detail ? detail : errorMessage(error, 'Could not save the factory.'); err.hidden = false; }
  }
}
