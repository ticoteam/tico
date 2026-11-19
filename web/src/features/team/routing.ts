import { ADD_STEPS, TABS, type AddStep, type Tab } from './actions';

// What the routes, the shell and other features use before a teammate's page loads: the address check, the reads and
// a teammate's address. No components here: the first load imports this file, and a page comes through exports.ts.
export { agentsQuery, groupsQuery, computersQuery, type AgentRow } from './queries';
export { teamSlug, whoHref } from './model';

export interface TeamSearch {
  tab?: Tab;
  add?: AddStep;
}

/** `?tab=settings|instructions|model|computer|screen|slack&add=pick|new|template|person`; anything else is dropped. */
export function validateTeamSearch(raw: Record<string, unknown>): TeamSearch {
  const out: TeamSearch = {};
  if (TABS.includes(raw.tab as Tab)) out.tab = raw.tab as Tab;
  if (ADD_STEPS.includes(raw.add as AddStep)) out.add = raw.add as AddStep;
  return out;
}
