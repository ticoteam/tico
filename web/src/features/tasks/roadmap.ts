import type { Pipeline, Stage, TaskItem } from './queries';

/** The Roadmap's sections, in the order they show. Horizons are not stored: each comes from the task's stage. */
export type Horizon = 'now' | 'next' | 'later' | 'shipped';

export const HORIZONS: readonly (readonly [Horizon, string])[] = [
  ['now', 'Now'],
  ['next', 'Next'],
  ['later', 'Later'],
  ['shipped', 'Shipped'],
];

/** The presets the server's Roadmap reads (backend/roadmaps.py): the Product pipeline, under either name. */
const PRODUCT_PRESETS = new Set(['product', 'features']);

/** The company's Features pipeline: the first one made from a Product preset, else the one with id `product`. */
export function roadmapPipeline(pipelines: readonly Pipeline[]): Pipeline | null {
  return pipelines.find((p) => p.preset && PRODUCT_PRESETS.has(p.preset)) ?? pipelines.find((p) => p.id === 'product') ?? null;
}

const SPEC = /\bspec/i;
const BUILD = /\b(build|development)\b/i;

/**
 * Where the pipeline turns: the first work stage named for the spec, and the first named for building. A pipeline
 * without those names splits in half: its first stage is Later, the rest of the first half Next, the second half Now.
 */
function turns(stages: readonly Stage[]): { spec: number; build: number } {
  const named = (re: RegExp) => stages.findIndex((s) => !s.side && re.test(s.name));
  const build = named(BUILD) >= 0 ? named(BUILD) : Math.ceil(stages.length / 2);
  const spec = named(SPEC) >= 0 ? named(SPEC) : 1;
  return { spec: Math.min(spec, build), build };
}

/**
 * A task's horizon, from where it stands in the pipeline's stage order (a side stage counts where it sits):
 * done is Shipped; from the build stage on is Now; from the spec stage on is Next (being specified); before that, or
 * in a stage the pipeline no longer has, is Later (not started). A closed task is on no horizon.
 */
export function horizonOf(p: Pick<Pipeline, 'stages'>, t: Pick<TaskItem, 'status' | 'stage_id'>): Horizon | null {
  if (t.status === 'done') return 'shipped';
  if (t.status !== 'open') return null;
  const at = p.stages.findIndex((s) => s.id === t.stage_id);
  if (at < 0) return 'later';
  const { spec, build } = turns(p.stages);
  if (at >= build) return 'now';
  return at >= spec ? 'next' : 'later';
}

/** The track's most pips: a long pipeline's stages share them. */
export const TRACK_STEPS = 6;

/**
 * How far a task is through its pipeline, as the row's track draws it: `steps` pips, the first `at` done and the one
 * at `at` on. A side stage stands where the work stage before it does; Shipped fills every pip.
 */
export function trackOf(p: Pick<Pipeline, 'stages'>, t: Pick<TaskItem, 'status' | 'stage_id'>): { steps: number; at: number } {
  const work = p.stages.filter((s) => !s.side);
  const steps = Math.max(1, Math.min(TRACK_STEPS, work.length));
  if (t.status === 'done') return { steps, at: steps };
  const i = p.stages.findIndex((s) => s.id === t.stage_id);
  const before = i < 0 ? 0 : p.stages.slice(0, i).filter((s) => !s.side).length;
  const pos = i >= 0 && p.stages[i]?.side ? Math.max(0, before - 1) : before;
  return { steps, at: Math.min(steps - 1, Math.floor((pos * steps) / Math.max(1, work.length))) };
}

/** The name the track reads out: the stage, or the pipeline's Done stage for a shipped task. */
export function stageName(p: Pick<Pipeline, 'stages' | 'done'>, t: Pick<TaskItem, 'status' | 'stage_id'>): string {
  if (t.status === 'done') return p.done?.name ?? 'Done';
  return p.stages.find((s) => s.id === t.stage_id)?.name ?? '';
}

/** One horizon's rows, the furthest along first, then the soonest due. */
export function byHorizon(p: Pick<Pipeline, 'stages'>, rows: readonly TaskItem[]): Record<Exclude<Horizon, 'shipped'>, TaskItem[]> {
  const out: Record<Exclude<Horizon, 'shipped'>, TaskItem[]> = { now: [], next: [], later: [] };
  const order = new Map(p.stages.map((s, i) => [s.id, i]));
  for (const t of rows) {
    const h = horizonOf(p, t);
    if (h && h !== 'shipped') out[h].push(t);
  }
  const due = (t: TaskItem) => (t.due_at ? Date.parse(t.due_at) : Infinity);
  for (const list of Object.values(out))
    list.sort((a, b) => (order.get(b.stage_id ?? '') ?? -1) - (order.get(a.stage_id ?? '') ?? -1) || due(a) - due(b));
  return out;
}
