import type { QueryClient } from '@tanstack/react-query';
import { EDIT, ROUTINE_STATES, RULE_TABS, RUNS_RANGE } from './actions';
import type { AutomationSearch } from './AutomationPage';
import type { ListeningSearch } from './ListeningPage';
import { allRunsQuery, dueQuery, holdsQuery, hooksQuery, matchesQuery, orgRulesQuery, reachQuery, routineQuery, routinesQuery, ruleGroupsQuery, RUN_RANGES, RUN_STATUSES, sourcesQuery, stageRulesQuery, statsQuery, teamsQuery, watchesQuery, watchQuery } from './queries';
import type { RoutinesSearch } from './AutomationsPage';
import type { RulesSearch } from './RulesPage';

/* What the Routines, Rules and Listening addresses hold, and what each page reads before it draws. The router parses
   search values as JSON, so an id that looks like a number arrives as one: each is read back as text. */

const text = (v: unknown, max = 200): string | undefined => {
  const s = typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : '';
  return s ? s.slice(0, max) : undefined;
};
const PANEL = /^(routine|builtin|hook|org|stage|due):\S+$/;
const PARTY = /^(group|bot|human):\S+$/;

export function validateRoutinesSearch(raw: Record<string, unknown>): RoutinesSearch {
  const out: RoutinesSearch = {};
  if (raw.tab === 'webhooks' || raw.tab === 'runs' || raw.tab === 'all') out.tab = raw.tab;
  if (out.tab === 'runs') {
    const status = RUN_STATUSES.find((x) => x === raw.status);
    if (status) out.status = status;
  }
  const range = out.tab === 'webhooks' ? undefined : RUN_RANGES.find((x) => x === raw.range);
  if (range) out.range = range;
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
  {
    // the sidebar's search and state hold beside every view
    const q = text(raw.q);
    if (q) out.q = q;
    const state = ROUTINE_STATES.find((x) => x === raw.state);
    if (state) out.state = state;
  }
  return out;
}

export function validateAutomationSearch(raw: Record<string, unknown>): AutomationSearch {
  const out: AutomationSearch = {};
  if (raw.view === 'runs') out.view = 'runs';
  const edit = text(raw.edit);
  if (edit && EDIT.test(edit) && !out.view) out.edit = edit;
  return out;
}

export function validateRulesSearch(raw: Record<string, unknown>): RulesSearch {
  const out: RulesSearch = {};
  const tab = RULE_TABS.find((t) => t && t === raw.tab);
  if (tab) out.tab = tab;
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
  const who = text(raw.who);
  if (who && PARTY.test(who)) out.who = who;
  const q = text(raw.q);
  if (q) out.q = q;
  return out;
}

export function validateListeningSearch(raw: Record<string, unknown>): ListeningSearch {
  const out: ListeningSearch = {};
  const watch = text(raw.watch);
  if (watch) out.watch = watch;
  const q = text(raw.q);
  if (!watch && q) out.q = q;
  if (watch && raw.tab === 'sources') out.tab = 'sources';
  const match = text(raw.match);
  if (watch && match) out.match = match;
  return out;
}

/* Each page's first read is awaited: one that fails is the router's fault, with Try again. What only fills in a part
   of the page is started, not awaited. */
export function loadRoutines(qc: QueryClient, search: RoutinesSearch): Promise<unknown> {
  if (search.tab === 'runs') {
    void qc.prefetchQuery(routinesQuery);
    void qc.prefetchQuery(statsQuery(search.range ?? RUNS_RANGE));
    return qc.ensureInfiniteQueryData(allRunsQuery(search.range ?? RUNS_RANGE, search.status));
  }
  if (search.tab === 'webhooks') return qc.ensureQueryData(hooksQuery);
  void qc.prefetchQuery(statsQuery(search.range ?? RUNS_RANGE));
  return qc.ensureQueryData(routinesQuery);
}

/** One automation: drawn from the list when it is there, its own read behind it. */
export function loadAutomation(qc: QueryClient, id: string): Promise<unknown> {
  void qc.prefetchQuery(routinesQuery);
  return qc.prefetchQuery(routineQuery(id));
}

export function loadRules(qc: QueryClient, search: RulesSearch): Promise<unknown> {
  const t = search.tab;
  if (!t) {
    void qc.prefetchQuery(teamsQuery);
    void qc.prefetchQuery(reachQuery);
    return qc.ensureQueryData(orgRulesQuery('active'));
  }
  if (t === 'stages') {
    const rules = qc.ensureQueryData(stageRulesQuery('active'));
    void qc.prefetchQuery(teamsQuery);
    return rules;
  }
  if (t === 'outside') {
    void qc.prefetchQuery(dueQuery);
    const rules = qc.ensureQueryData(orgRulesQuery('active'));
    void qc.prefetchQuery(holdsQuery);
    return rules;
  }
  if (t === 'due') return qc.ensureQueryData(dueQuery);
  // Suggested reads two lists and shows what it has: neither alone faults the page.
  if (t === 'suggested') return Promise.all([qc.prefetchQuery(orgRulesQuery('suggested')), qc.prefetchQuery(stageRulesQuery('suggested'))]);
  return qc.ensureQueryData(ruleGroupsQuery);
}

export function loadListening(qc: QueryClient, search: ListeningSearch): Promise<unknown> {
  if (!search.watch) return qc.ensureQueryData(watchesQuery);
  // One watch draws from the list while its own read comes, and its matches have their own states.
  const watch = qc.prefetchQuery(watchQuery(search.watch));
  if (search.tab === 'sources') {
    void qc.prefetchQuery(sourcesQuery(search.watch));
    return watch;
  }
  return Promise.all([watch, qc.prefetchQuery(matchesQuery(search.watch))]);
}
