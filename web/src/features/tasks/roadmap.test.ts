import { describe, expect, it } from 'vitest';
import type { Pipeline, TaskItem } from './queries';
import { byHorizon, horizonOf, roadmapPipeline, trackOf } from './roadmap';

const pipe = (id: string, names: (string | [string, 'side'])[], preset: string | null = null): Pipeline => ({
  id,
  name: id,
  stages: names.map((n) => (Array.isArray(n) ? { id: n[0], name: n[0], side: true } : { id: n, name: n })),
  owner: null,
  done: { id: 'done', name: 'Done' },
  closed: { id: 'closed', name: 'Closed' },
  mayChange: true,
  preset,
});

// The mock's Features pipeline: a pitch, a side stage, three spec stages, then the build and everything after it.
const FEAT = pipe('feat', ['Pitch', ['Parked', 'side'], 'Spec', 'Related input', 'OK spec', 'Build', 'Review', 'QA', 'Release', 'Validate'], 'features');
const at = (stage: string, status: TaskItem['status'] = 'open') => ({ status, stage_id: stage });

describe('roadmap horizons', () => {
  it('reads the Product pipeline by preset, else by id, never another', () => {
    const eng = pipe('eng', ['Build'], 'engineering');
    expect(roadmapPipeline([eng, FEAT])?.id).toBe('feat');
    expect(roadmapPipeline([eng, pipe('product', ['Ideas'])])?.id).toBe('product');
    expect(roadmapPipeline([eng])).toBeNull();
  });

  it('not started is Later, being specified is Next, from Build on is Now, done is Shipped', () => {
    expect(horizonOf(FEAT, at('Pitch'))).toBe('later');
    expect(horizonOf(FEAT, at('Parked'))).toBe('later');
    expect(horizonOf(FEAT, at('Spec'))).toBe('next');
    expect(horizonOf(FEAT, at('OK spec'))).toBe('next');
    expect(horizonOf(FEAT, at('Build'))).toBe('now');
    expect(horizonOf(FEAT, at('Validate'))).toBe('now');
    expect(horizonOf(FEAT, at('done', 'done'))).toBe('shipped');
    expect(horizonOf(FEAT, at('closed', 'closed'))).toBeNull();
    expect(horizonOf(FEAT, at('gone'))).toBe('later');
  });

  it('a pipeline that starts at Spec has nothing Later; "In development" is a build stage', () => {
    const product = pipe('product', ['Spec', 'In development', 'Validate'], 'product');
    expect(horizonOf(product, at('Spec'))).toBe('next');
    expect(horizonOf(product, at('In development'))).toBe('now');
  });

  it('without those names the stages split: first Later, then Next, the second half Now', () => {
    const plain = pipe('p', ['One', 'Two', 'Three', 'Four']);
    expect(['One', 'Two', 'Three', 'Four'].map((s) => horizonOf(plain, at(s)))).toEqual(['later', 'next', 'now', 'now']);
  });

  it('the track shares six pips over the work stages; Shipped fills them', () => {
    expect(trackOf(FEAT, at('Pitch'))).toEqual({ steps: 6, at: 0 });
    expect(trackOf(FEAT, at('Parked'))).toEqual({ steps: 6, at: 0 });
    expect(trackOf(FEAT, at('Validate'))).toEqual({ steps: 6, at: 5 });
    expect(trackOf(FEAT, at('done', 'done'))).toEqual({ steps: 6, at: 6 });
    expect(trackOf(pipe('p', ['A', 'B', 'C']), at('B'))).toEqual({ steps: 3, at: 1 });
  });

  it('a horizon lists the furthest along first, then the soonest due', () => {
    const row = (id: string, stage: string, due: string | null = null) => ({ id, status: 'open', stage_id: stage, due_at: due }) as TaskItem;
    const h = byHorizon(FEAT, [row('a', 'Build', '2030-01-09'), row('b', 'QA'), row('c', 'Build', '2030-01-02'), row('d', 'Spec')]);
    expect(h.now.map((t) => t.id)).toEqual(['b', 'c', 'a']);
    expect(h.next.map((t) => t.id)).toEqual(['d']);
    expect(h.later).toEqual([]);
  });
});
