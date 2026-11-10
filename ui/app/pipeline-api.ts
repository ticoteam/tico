/* ui/app/pipeline-api.ts — What the pipeline screens read beyond the task client (task-api.ts): the factories and the
   way to a pipeline on the Tasks page. Tasks and pipeline records go through task-api.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A factory, with how many of its tasks reached Done in the period asked for (Closed never counts). */
interface FactoryRow { id: string; name: string; goal: string | null; manager_id: string | null; done: number }
/** A pipeline's counts on Company › Factories: its open tasks, and its mark: a bot in it can't run, or it needs a person. */
interface FactoryPipelineRow { id: string; factory_id: string | null; open: number; mark: 'cant' | 'person' | null }
/** GET /api/v2/factories?period=1d|7d|30d. */
interface FactoryMap { factories: FactoryRow[]; pipelines: FactoryPipelineRow[] }

/** The factories and each pipeline's open count and mark over a period (1d, 7d or 30d). */
async function factoriesRead(period: string): Promise<FactoryMap> {
  const map = await get<Partial<FactoryMap>>(`/v2/factories?period=${encodeURIComponent(period)}`, {fresh: true});
  return {factories: map.factories ?? [], pipelines: map.pipelines ?? []};
}

/** Make a factory: the company's owner and Admins. */
async function factoryCreate(name: string, goal: string): Promise<void> {
  await post('/v2/factories', {name, goal: goal || null});
}

/** The Tasks page showing one pipeline (tasks-page.ts reads `pipeline`). */
const pipelineHref = (id: string): string => `${TASKS}?pipeline=${encodeURIComponent(id)}`;

/** The pipelines a pipeline's stages file tasks into (on enter: file into). */
const pipelineFilesInto = (rec: PipelineRec): string[] => [...new Set(rec.stages
  .map(stage => stage.on_enter['file_into'])
  .filter((to): to is string => typeof to === 'string' && !!to))];
