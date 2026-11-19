import type { QueryClient } from '@tanstack/react-query';
import { RULE_TABS } from './actions';
import type { ListeningSearch } from './ListeningPage';
import { dueQuery, hooksQuery, matchesQuery, orgRulesQuery, reachQuery, routinesQuery, ruleGroupsQuery, stageRulesQuery, teamsQuery, watchesQuery, watchQuery } from './queries';
import type { RoutinesSearch } from './RoutinesPage';
import type { RulesSearch } from './RulesPage';

/* What the Routines, Rules and Listening addresses hold, and what each page reads before it draws. The router parses
   search values as JSON, so an id that looks like a number arrives as one: each is read back as text. */

const text = (v: unknown, max = 200): string | undefined => {
  const s = typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : '';
  return s ? s.slice(0, max) : undefined;
};
const PANEL = /^(routine|builtin|hook|org|stage):\S+$/;
const PARTY = /^(group|bot|human):\S+$/;

export function validateRoutinesSearch(raw: Record<string, unknown>): RoutinesSearch {
  const out: RoutinesSearch = {};
  if (raw.tab === 'webhooks') out.tab = 'webhooks';
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
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
  return out;
}

export function validateListeningSearch(raw: Record<string, unknown>): ListeningSearch {
  const out: ListeningSearch = {};
  const watch = text(raw.watch);
  if (watch) out.watch = watch;
  if (watch && raw.tab === 'sources') out.tab = 'sources';
  const match = text(raw.match);
  if (watch && match) out.match = match;
  return out;
}

/** A failed read is the page's own fault state, not the router's. */
const warm = (reads: Promise<unknown>[]) => Promise.all(reads).catch(() => undefined);

export function loadRoutines(qc: QueryClient, search: RoutinesSearch): Promise<unknown> {
  return warm([search.tab === 'webhooks' ? qc.ensureQueryData(hooksQuery) : qc.ensureQueryData(routinesQuery)]);
}

export function loadRules(qc: QueryClient, search: RulesSearch): Promise<unknown> {
  const t = search.tab;
  if (!t) return warm([qc.ensureQueryData(orgRulesQuery('active')), qc.ensureQueryData(teamsQuery), qc.ensureQueryData(reachQuery)]);
  if (t === 'stages') return warm([qc.ensureQueryData(stageRulesQuery('active'))]);
  if (t === 'outside') return warm([qc.ensureQueryData(orgRulesQuery('active')), qc.ensureQueryData(dueQuery)]);
  if (t === 'due') return warm([qc.ensureQueryData(dueQuery)]);
  if (t === 'suggested') return warm([qc.ensureQueryData(orgRulesQuery('suggested')), qc.ensureQueryData(stageRulesQuery('suggested'))]);
  return warm([qc.ensureQueryData(ruleGroupsQuery)]);
}

export function loadListening(qc: QueryClient, search: ListeningSearch): Promise<unknown> {
  if (!search.watch) return warm([qc.ensureQueryData(watchesQuery)]);
  return warm([qc.ensureQueryData(watchQuery(search.watch)), ...(search.tab ? [] : [qc.ensureQueryData(matchesQuery(search.watch))])]);
}
