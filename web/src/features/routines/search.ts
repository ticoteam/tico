import { queryClient } from '@/data';
import { defineSearch, type SearchHit } from '@/search/define';
import { HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { orgRulesQuery, routinesQuery, stageRulesQuery } from './queries';

const none = () => undefined;

/* Cmd+K: routines by name, then rules (a company rule by its words, a stage rule by its name), each opening in its
   page's panel. */
export const routinesSearch = defineSearch({
  group: 'Routines',
  order: 75,
  find: async (q) => {
    if (!q.trim()) return [];
    const [routines, org, stage] = await Promise.all([
      queryClient.ensureQueryData(routinesQuery).catch(none),
      queryClient.ensureQueryData(orgRulesQuery('active')).catch(none),
      queryClient.ensureQueryData(stageRulesQuery('active')).catch(none),
    ]);
    const hits: SearchHit[] = [
      ...(routines ?? [])
        .filter((r) => r.name && matches(r.name, q))
        .map((r) => ({ key: `routine:${r.id}`, title: r.name ?? '', meta: 'Routine', lead: { icon: 'refresh' as const }, run: { id: 'routines.open', input: { routine: r.id } } })),
      ...(org ?? [])
        .filter((r) => matches(r.text, q))
        .map((r) => ({
          key: `rule:org:${r.id}`,
          title: r.text,
          meta: 'Rule',
          lead: { icon: 'rules' as const },
          run: { id: 'rules.open', input: r.kind === 'external' ? { rule: r.id, kind: 'org', tab: 'outside' } : { rule: r.id, kind: 'org' } },
        })),
      ...(stage?.items ?? [])
        .filter((r) => matches(r.name, q))
        .map((r) => ({ key: `rule:stage:${r.id}`, title: r.name, meta: 'Rule', lead: { icon: 'rules' as const }, run: { id: 'rules.open', input: { rule: r.id, kind: 'stage' } } })),
    ];
    return hits.slice(0, HITS);
  },
});
