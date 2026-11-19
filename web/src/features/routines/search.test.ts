import { afterEach, describe, expect, it } from 'vitest';
import { registry } from '@/actions';
import { queryClient } from '@/data';
import { orgRulesQuery, routinesQuery, stageRulesQuery } from './queries';
import { routinesSearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;

afterEach(() => queryClient.clear());

describe('routines search', () => {
  it('finds routines and rules by name and opens each in its panel', async () => {
    queryClient.setQueryData(routinesQuery.queryKey, [{ id: 'r1', name: 'Frost check' }, { id: 'r2', name: 'Weekly report' }, { id: 'r3' }] as never);
    queryClient.setQueryData(orgRulesQuery('active').queryKey, [
      { id: 'o1', kind: 'internal', text: 'Warn before a frost', status: 'active', version: 1, can_edit: true },
      { id: 'o2', kind: 'external', text: 'Frost notes go to buyers', status: 'active', version: 1, can_edit: true },
    ]);
    queryClient.setQueryData(stageRulesQuery('active').queryKey, { items: [{ id: 's1', name: 'Frost gate' }], built_in: [], may_add: true } as never);
    const hits = await routinesSearch.find('frost', sig());
    expect(hits.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['Frost check', 'Routine', { id: 'routines.open', input: { routine: 'r1' } }],
      ['Warn before a frost', 'Rule', { id: 'rules.open', input: { rule: 'o1', kind: 'org' } }],
      ['Frost notes go to buyers', 'Rule', { id: 'rules.open', input: { rule: 'o2', kind: 'org', tab: 'outside' } }],
      ['Frost gate', 'Rule', { id: 'rules.open', input: { rule: 's1', kind: 'stage' } }],
    ]);
    expect(hits.every((h) => registry.get(h.run.id)?.input.parse(h.run.input).ok)).toBe(true);
    expect(await routinesSearch.find('', sig())).toEqual([]);
  });
});
