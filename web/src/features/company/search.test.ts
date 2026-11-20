import { afterEach, describe, expect, it } from 'vitest';
import { registry } from '@/actions';
import { queryClient } from '@/data';
import { goalsTreeQuery, productsQuery, strategyQuery } from './queries';
import { companySearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;

afterEach(() => queryClient.clear());

describe('company search', () => {
  it('finds goals, bets and products by name, each going to its page (pipelines are Tasks’ group)', async () => {
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [{ id: 'g1', title: 'Beat the first frost' }, { id: 'g2', title: 'Ship seeds' }] } as never);
    queryClient.setQueryData(strategyQuery.queryKey, {
      current: null,
      bets: [
        { id: 'b1', statement: 'Frost guides sell kits', status: 'active' },
        { id: 'b2', statement: 'Frost apps', status: 'retired' },
      ],
    });
    queryClient.setQueryData(productsQuery.queryKey, { products: [{ id: 'x1', key: 'frost-kit', name: 'Frost kit' }] } as never);
    const hits = await companySearch.find('frost', sig());
    expect(hits.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['Beat the first frost', 'Goal', { id: 'goals.open', input: { goal: 'g1' } }],
      ['Frost guides sell kits', 'Bet', { id: 'shell.goTo', input: { to: '/company/strategy' } }],
      ['Frost kit', 'Product', { id: 'products.open', input: { product: 'frost-kit' } }],
    ]);
    expect(hits.every((h) => registry.get(h.run.id)?.input.safeParse(h.run.input).success)).toBe(true);
    expect(await companySearch.find('', sig())).toEqual([]);
  });
});
