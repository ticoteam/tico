import { queryClient } from '@/data';
import { defineSearch, type SearchHit } from '@/search/define';
import { HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { goalsTreeQuery, productsQuery, strategyQuery } from './queries';

const none = () => undefined;
const lead = { icon: 'company' as const };

/* Cmd+K: the Company's goals, bets and products by name, each going to its page. Pipelines are Tasks' own group. */
export const companySearch = defineSearch({
  group: 'Company',
  order: 70,
  find: async (q) => {
    if (!q.trim()) return [];
    const [tree, strategy, products] = await Promise.all([
      queryClient.ensureQueryData(goalsTreeQuery).catch(none),
      queryClient.ensureQueryData(strategyQuery).catch(none),
      queryClient.ensureQueryData(productsQuery).catch(none),
    ]);
    const hits: SearchHit[] = [
      ...(tree?.goals ?? [])
        .filter((g) => matches(g.title, q))
        .map((g) => ({ key: `goal:${g.id}`, title: g.title, meta: 'Goal', lead, run: { id: 'goals.open', input: { goal: g.id } } })),
      ...(strategy?.bets ?? [])
        .filter((b) => b.status !== 'retired' && matches(b.statement, q))
        .map((b) => ({ key: `bet:${b.id}`, title: b.statement, meta: 'Bet', lead, run: { id: 'shell.goTo', input: { to: '/company/strategy' } } })),
      ...(products?.products ?? [])
        .filter((p) => matches(p.name, q))
        .map((p) => ({ key: `product:${p.key}`, title: p.name, meta: 'Product', lead, run: { id: 'products.open', input: { product: p.key } } })),
    ];
    return hits.slice(0, HITS);
  },
});
