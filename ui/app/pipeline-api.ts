/* ui/app/pipeline-api.ts — What the pipeline screens read beyond the task client (task-api.ts): the factories and the
   way to a pipeline on the Tasks page. Tasks and pipeline records go through task-api.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A factory, with how many of its tasks reached Done in the period asked for (Closed never counts). */
interface FactoryRow { id: string; name: string; goal: string | null; manager_id: string | null; done: number }
/** A pipeline as the factories read places it: its factory and the pipelines it files into (Strategy › Org's Factories
    section orders a factory's cards by them, org-factories.ts). */
interface FactoryPipelineRow { id: string; name: string; factory_id: string | null; files_into: string[] }
/** GET /api/v2/factories?period=1d|7d|30d. */
interface FactoryMap { factories: FactoryRow[]; pipelines: FactoryPipelineRow[] }
interface FactoryPipelineWire { id: string; name?: string; factory_id?: string | null; files_into?: string[] | null }

/** The factories and where each pipeline files, with each factory's Done count over a period (1d, 7d or 30d). */
async function factoriesRead(period: string): Promise<FactoryMap> {
  const map = await get<{factories?: FactoryRow[]; pipelines?: FactoryPipelineWire[]}>(`/v2/factories?period=${encodeURIComponent(period)}`, {fresh: true});
  return {
    factories: map.factories ?? [],
    pipelines: (map.pipelines ?? []).map(p => ({id: p.id, name: p.name ?? '', factory_id: p.factory_id ?? null, files_into: p.files_into ?? []})),
  };
}

/** Make a factory: the company's owner and Admins (403 for anyone else; 409 when the name is taken). */
async function factoryCreate(name: string, goal: string): Promise<void> {
  await post('/v2/factories', goal ? {name, goal} : {name});
}

/** The Tasks page showing one pipeline (tasks-page.ts reads `pipeline`). */
const pipelineHref = (id: string): string => `${TASKS}?pipeline=${encodeURIComponent(id)}`;

/** The pipelines a pipeline's stages file tasks into (on enter: file into). */
const pipelineFilesInto = (rec: PipelineRec): string[] => [...new Set(rec.stages
  .map(stage => stage.on_enter['file_into'])
  .filter((to): to is string => typeof to === 'string' && !!to))];
