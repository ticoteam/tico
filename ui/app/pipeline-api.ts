/* ui/app/pipeline-api.ts — What the pipeline screens read beyond the task client (task-api.ts): the factories and the
   way to a pipeline on the Tasks page. Tasks and pipeline records go through task-api.ts.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

/** A factory, with how many of its tasks reached Done in the period asked for (Closed never counts). */
interface FactoryRow { id: string; name: string; goal: string | null; manager_id: string | null; done: number }
/** A pipeline's counts on Company › Factories: its open tasks, the pipelines it files into, and its mark: a bot in it
    can't run, or it needs a person. The mark is read into the task mark's own names (task-row.ts). */
interface FactoryPipelineRow { id: string; name: string; factory_id: string | null; open: number; files_into: string[]; mark: 'cant' | 'person' | null }
/** GET /api/v2/factories?period=1d|7d|30d. */
interface FactoryMap { factories: FactoryRow[]; pipelines: FactoryPipelineRow[] }
interface FactoryPipelineWire { id: string; name?: string; factory_id?: string | null; open?: number; files_into?: string[] | null; mark?: string | null }
const FACTORY_MARKS: Record<string, 'cant' | 'person'> = {cant_run: 'cant', needs_person: 'person'};

/** The factories and each pipeline's open count, arrows and mark over a period (1d, 7d or 30d). */
async function factoriesRead(period: string): Promise<FactoryMap> {
  const map = await get<{factories?: FactoryRow[]; pipelines?: FactoryPipelineWire[]}>(`/v2/factories?period=${encodeURIComponent(period)}`, {fresh: true});
  return {
    factories: map.factories ?? [],
    pipelines: (map.pipelines ?? []).map(p => ({id: p.id, name: p.name ?? '', factory_id: p.factory_id ?? null, open: p.open ?? 0,
      files_into: p.files_into ?? [], mark: FACTORY_MARKS[p.mark ?? ''] ?? null})),
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
