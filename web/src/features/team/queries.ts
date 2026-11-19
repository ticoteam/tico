import { queryOptions } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type { Task } from '@/api/schema';
import { keys, queryClient, quietRoot, QUIET_STALE_MS, write } from '@/data';

// Teammates, teams and computers have no live topic of their own (status lines come through the directory): read
// again after the person's own writes, on reconnect, and when a page opens on a read older than a minute.
quietRoot('team');

const q = (params: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();
const at = (s: string) => encodeURIComponent(s);

/* ---------------------------------------------------------------- agents */

export interface Machine {
  runner_id?: string | null;
  label?: string | null;
  generation?: number | null;
}

/** An agent as GET /api/employees lists it: the roster row with its model, computer and revision. */
export interface AgentRow {
  id: string;
  slug: string;
  display_name?: string;
  description?: string;
  /** The team's group id. */
  team?: string;
  reports_to?: string;
  state?: string;
  status?: string;
  harness?: string;
  model?: string;
  reasoning_effort?: string;
  resolved_model?: string;
  resolved_model_label?: string;
  resolved_effort?: string;
  /** 'bot' when the agent picked its own model; otherwise it follows the company's default for its plan. */
  model_source?: string;
  revision?: number;
  machine?: Machine | null;
  cloud_machine?: { mode?: string; placement?: string; shared?: boolean; size?: string; state?: string } | null;
  can_manage?: boolean;
  online?: boolean;
}

export const agentsQuery = queryOptions({
  queryKey: ['team', 'agents'] as const,
  queryFn: ({ signal }) => api.get<AgentRow[]>('/api/employees', { signal }),
  staleTime: QUIET_STALE_MS,
});

/** One agent (GET /api/v2/bots/{slug}): what it is on, why it cannot run, who it reports to. */
export interface AgentDetail {
  id: string;
  slug: string;
  display_name: string;
  description?: string;
  team?: string;
  reports_to?: string;
  reports_to_name?: string;
  state?: string;
  revision?: number;
  status?: { state?: string; focus?: string; task_id?: string | null } | null;
  cant_run?: { cause?: string; fix?: string } | null;
}

export const agentQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'agent', slug] as const,
    queryFn: ({ signal }) => api.get<AgentDetail>(`/api/v2/bots/${at(slug)}`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

export interface PauseState {
  state: string;
  revision: number;
  can_control: boolean;
}

export const pauseQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'pause', slug] as const,
    queryFn: ({ signal }) => api.get<PauseState>(`/api/v2/bots/${at(slug)}/pause-state`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

export interface Instructions {
  content: string;
  source?: string;
  updated_at?: string;
}

export const instructionsQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'instructions', slug] as const,
    queryFn: ({ signal }) => api.get<Instructions>(`/api/v2/bots/${at(slug)}/instructions`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

export interface Rule {
  id: string;
  kind: 'internal' | 'external';
  text: string;
  status?: string;
  source?: { name?: string } | null;
  due?: { due_on?: string }[];
}
interface RulesAnswer {
  settings?: { text: string }[];
  internal?: { rules: Rule[] };
  external?: { rules: Rule[] };
}
export interface RuleLine {
  key: string;
  text: string;
  how: 'set' | 'own' | 'outside';
  from?: string;
  due?: string;
  off?: boolean;
}

/** Its rules as one flat list: its settings' rules, then the company's, then the outside ones (GET .../rules). */
export const rulesQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'rules', slug] as const,
    queryFn: async ({ signal }): Promise<RuleLine[]> => {
      const r = await api.get<RulesAnswer>(`/api/v2/bots/${at(slug)}/rules`, { signal });
      const line = (x: Rule, how: RuleLine['how']): RuleLine => ({
        key: x.id,
        text: x.text,
        how,
        from: x.source?.name || undefined,
        due: x.due?.[0]?.due_on,
        off: x.status !== undefined && x.status !== 'active',
      });
      return [
        ...(r.settings ?? []).map((x, i): RuleLine => ({ key: `set:${i}`, text: x.text, how: 'set' })),
        ...(r.internal?.rules ?? []).map((x) => line(x, 'own')),
        ...(r.external?.rules ?? []).map((x) => line(x, 'outside')),
      ];
    },
    staleTime: QUIET_STALE_MS,
  });

/** A run: GET /api/runs?bot= answers the agent's newest 200. */
export interface Run {
  id: string;
  issue?: string | null;
  state?: string;
  outcome?: string;
  failure_reason?: string;
  finished_at?: string | null;
  last_event_at?: string | null;
  duration_s?: number | null;
  cost_usd?: number | null;
}

export const runsQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'runs', slug] as const,
    queryFn: ({ signal }) => api.get<Run[]>(`/api/runs?${q({ bot: slug })}`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

/** What it learned: its memory's commits (GET .../memory), newest first. */
export interface Learned {
  sha: string;
  subject: string;
  when: string;
}

export const learnedQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'learned', slug] as const,
    queryFn: async ({ signal }) =>
      (await api.get<{ updates?: Learned[] }>(`/api/v2/bots/${at(slug)}/memory?include_documents=false`, { signal })).updates ?? [],
    staleTime: QUIET_STALE_MS,
  });

/** Changes to its settings (GET /api/v2/settings/history?bot=). */
export interface Change {
  id: string;
  field: string;
  created_at: string;
}

export const changesQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'changes', slug] as const,
    queryFn: async ({ signal }) =>
      (await api.get<{ changes?: Change[] }>(`/api/v2/settings/history?${q({ bot: slug })}`, { signal })).changes ?? [],
    staleTime: QUIET_STALE_MS,
  });

/** Its latest update (GET /api/v2/updates?bot=&limit=1), as the old page's rail showed it. */
export interface Update {
  id: string;
  body: string;
  created_at: string;
  updated_at?: string | null;
}

export const latestUpdateQuery = (slug: string) =>
  queryOptions({
    queryKey: ['team', 'update', slug] as const,
    queryFn: async ({ signal }) =>
      (await api.get<{ updates?: Update[] }>(`/api/v2/updates?${q({ bot: slug, limit: 1 })}`, { signal })).updates?.[0] ?? null,
    staleTime: QUIET_STALE_MS,
  });

/* ---------------------------------------------------------------- models */

export interface Model {
  id: string;
  label: string;
  provider?: string;
  harnesses?: string[];
  efforts?: string[];
  default_effort?: string;
  deprecated?: boolean;
}
export interface Harness {
  id: string;
  name: string;
}
export interface Models {
  models: Model[];
  harnesses: Harness[];
  enabled_providers?: string[];
  /** The company's default model id: what an agent with no model of its own runs. */
  default?: { runtime?: string; model?: string };
}

export const modelsQuery = queryOptions({
  queryKey: ['team', 'models'] as const,
  queryFn: ({ signal }) => api.get<Models>('/api/v2/models', { signal }),
  staleTime: QUIET_STALE_MS,
});

/* ---------------------------------------------------------------- people, teams, access */

export interface PersonRow {
  id: string;
  name: string;
  email?: string;
  title?: string;
  /** The team's group id. */
  team?: string;
  reports_to?: string;
  slack?: string;
  github?: string;
}

export const peopleQuery = queryOptions({
  queryKey: ['team', 'people'] as const,
  queryFn: async ({ signal }) => (await api.get<{ people?: PersonRow[] }>('/api/humans', { signal })).people ?? [],
  staleTime: QUIET_STALE_MS,
});

/** A team (GET /api/v2/groups): its members, the team it sits in, and its lead when one is set. */
export interface Group {
  id: string;
  name: string;
  parent: string;
  /** `bot:<bot id>` or `person:<id>`; "" when none is set. */
  lead?: string;
  people: string[];
  /** Bot ids (a uuid for newer agents; `bot_slugs` has the slugs). */
  bots: string[];
  order?: number;
}

export const groupsQuery = queryOptions({
  queryKey: ['team', 'groups'] as const,
  queryFn: ({ signal }) => api.get<Group[]>('/api/v2/groups', { signal }),
  staleTime: QUIET_STALE_MS,
});

export interface AccessPerson {
  id: string;
  role: 'owner' | 'admin' | 'member';
  removable?: boolean;
  left?: boolean;
}
export interface Access {
  you: 'owner' | 'admin' | 'member';
  people: AccessPerson[];
}

/** Roles (GET /api/v2/access). A member is refused it: they then see no admin controls. */
export const accessQuery = queryOptions({
  queryKey: ['team', 'access'] as const,
  queryFn: async ({ signal }): Promise<Access | null> => {
    try {
      return await api.get<Access>('/api/v2/access', { signal });
    } catch (e) {
      if (e instanceof ApiError && (e.status === 403 || e.status === 401)) return null;
      throw e;
    }
  },
  staleTime: QUIET_STALE_MS,
});

export interface Template {
  slug: string;
  name: string;
  summary?: string;
  retired?: boolean;
}

export const templatesQuery = queryOptions({
  queryKey: ['team', 'templates'] as const,
  queryFn: async ({ signal }) =>
    ((await api.get<{ cards?: Template[] }>('/api/v2/templates', { signal })).cards ?? []).filter((t) => !t.retired),
  staleTime: QUIET_STALE_MS,
});

/* ---------------------------------------------------------------- computers */

export interface Computer {
  id: string;
  label: string;
  online: boolean;
  last_seen_at?: string | null;
  revoked_at?: string | null;
  version?: string;
  platform?: string;
  /** Bot ids. */
  bots?: string[];
  restarting?: unknown;
  screen_available?: boolean;
  performance?: string;
  may_manage?: boolean;
  cloud_machine?: { size?: string; placement?: string; shared?: boolean } | null;
  readiness?: { disk?: { free_bytes?: number; total_bytes?: number } } | null;
}

/** Every computer still in use (GET /api/v2/operations). */
export const computersQuery = queryOptions({
  queryKey: ['team', 'computers'] as const,
  queryFn: async ({ signal }) =>
    ((await api.get<{ computers?: Computer[] }>('/api/v2/operations', { signal })).computers ?? []).filter((c) => !c.revoked_at),
  staleTime: QUIET_STALE_MS,
});

/** [t, cpu, cpuPeak, mem, memPeak, load, swap, diskFree, activeTurns] every 30 s while performance is on. */
export type PerfPoint = [number, number, number, number, number, number, number, number, number];
export interface Performance {
  points: PerfPoint[];
  summary?: string;
  performance?: string;
}

export const performanceQuery = (id: string) =>
  queryOptions({
    queryKey: ['team', 'performance', id] as const,
    queryFn: ({ signal }) => api.get<Performance>(`/api/v2/computers/${at(id)}/performance?hours=24`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

/* ---------------------------------------------------------------- chats and tasks */

/** The chat with a teammate: their direct thread (GET /api/v2/threads/dm, which makes it the first time). */
export const dmQuery = (actor: string) =>
  queryOptions({
    queryKey: ['team', 'dm', actor] as const,
    queryFn: async ({ signal }) => (await api.get<{ id: string }>(`/api/v2/threads/dm?${q({ with: actor })}`, { signal })).id,
    staleTime: Infinity,
  });

/** The fields a teammate's task rows read. */
export type OwnTask = Pick<Task, 'id' | 'number' | 'title' | 'status' | 'pipeline_id' | 'stage_id' | 'owner_id' | 'late' | 'turn' | 'updated_at'>;
export interface OwnTasks {
  tasks: OwnTask[];
  cursor?: string;
}

/** A teammate's open tasks, under the tasks root so the stream patches them (`owner` is an actor id). */
export const openTasksQuery = (actor: string) => {
  const params = { owner: actor, status: 'open', brief: 'true', limit: 200 };
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<OwnTasks>(`/api/v2/tasks?${q(params)}`, { signal }),
  });
};

/** What they finished since `since` (an ISO day): the figures and the Done fold. */
export const doneTasksQuery = (actor: string, since: string) => {
  const params = { owner: actor, status: 'done', brief: 'true', updated_since: since, limit: 500 };
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<OwnTasks>(`/api/v2/tasks?${q(params)}`, { signal }),
  });
};

/** Every open task, or every task done since `since`: a team page counts them by owner. */
export const allTasksQuery = (since?: string) => {
  const params: Record<string, string | number> = since ? { status: 'done', brief: 'true', updated_since: since, limit: 500 } : { status: 'open', brief: 'true', limit: 500 };
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<OwnTasks>(`/api/v2/tasks?${q(params)}`, { signal }),
  });
};

/* ---------------------------------------------------------------- changes */

type AgentPatch = Partial<Pick<AgentRow, 'display_name' | 'description' | 'reports_to'>>;

const patchRows = (slug: string, patch: Partial<AgentRow>) => (rows: AgentRow[] | undefined) =>
  rows?.map((r) => (r.slug === slug ? { ...r, ...patch } : r));
const patchDetail = (patch: Partial<AgentDetail>) => (d: AgentDetail | undefined) => (d ? { ...d, ...patch } : d);
const revisionOf = (slug: string) =>
  queryClient.getQueryData(agentsQuery.queryKey)?.find((r) => r.slug === slug)?.revision ??
  queryClient.getQueryData(agentQuery(slug).queryKey)?.revision ??
  1;
const directoryAgain = () => void queryClient.invalidateQueries({ queryKey: keys.directory() });

/** Name, role or manager: one definition change, sent against the revision on screen (409 when someone else changed it). */
export async function changeAgent(slug: string, patch: AgentPatch) {
  const out = await write({
    key: [agentsQuery.queryKey, agentQuery(slug).queryKey],
    optimistic: (cur: AgentRow[] | AgentDetail | undefined) =>
      Array.isArray(cur) ? patchRows(slug, patch)(cur) : patchDetail(patch as Partial<AgentDetail>)(cur),
    send: (idempotencyKey) =>
      api.post<{ revision?: number }>(`/api/v2/bots/${at(slug)}/definition`, { ...patch, expected_revision: revisionOf(slug) }, { idempotencyKey }),
  });
  directoryAgain();
  return out;
}

export interface ModelPick {
  model: string;
  effort?: string;
  harness?: string;
}

/** A new model: a transition, applied at once, against the agent's computer generation and revision. */
export function setAgentModel(slug: string, pick: ModelPick) {
  const row = queryClient.getQueryData(agentsQuery.queryKey)?.find((r) => r.slug === slug);
  const effort = pick.effort ?? '';
  return write({
    key: agentsQuery.queryKey,
    optimistic: patchRows(slug, { model: pick.model, resolved_model: pick.model, reasoning_effort: effort, model_source: 'bot', ...(pick.harness ? { harness: pick.harness } : {}) }),
    send: (idempotencyKey) =>
      api.post<{ id: string }>(
        `/api/v2/bots/${at(slug)}/transitions`,
        {
          kind: 'model',
          model: pick.model,
          effort,
          harness: pick.harness ?? row?.harness,
          expected_generation: row?.machine?.generation ?? 0,
          expected_revision: revisionOf(slug),
        },
        { idempotencyKey },
      ),
  });
}

/** Pause or resume: against the pause state's revision. */
export function controlAgent(slug: string, action: 'pause' | 'resume') {
  const key = pauseQuery(slug).queryKey;
  const revision = queryClient.getQueryData(key)?.revision ?? revisionOf(slug);
  return write({
    key,
    optimistic: (cur: PauseState | undefined) => (cur ? { ...cur, state: action === 'pause' ? 'paused' : 'active' } : cur),
    send: (idempotencyKey) => api.post(`/api/v2/bots/${at(slug)}/control`, { action, expected_revision: revision }, { idempotencyKey }),
  });
}

/** Archive: its open tasks stay where they are (no successor). */
export async function archiveAgent(slug: string) {
  const out = await write({
    key: agentsQuery.queryKey,
    optimistic: (rows: AgentRow[] | undefined) => rows?.filter((r) => r.slug !== slug),
    send: (idempotencyKey) =>
      api.post(`/api/v2/bots/${at(slug)}/archive`, { successor: null, expected_revision: revisionOf(slug) }, { idempotencyKey }),
  });
  directoryAgain();
  return out;
}

/** Into a team: a teammate added to a group leaves the one it was in (PATCH /api/v2/groups/{id}). */
export async function moveToTeam(kind: 'agent' | 'person', id: string, team: string) {
  const field = kind === 'agent' ? 'bots' : 'people';
  const out = await write({
    key: groupsQuery.queryKey,
    optimistic: (groups: Group[] | undefined) =>
      groups?.map((g) => {
        const rest = g[field].filter((x) => x !== id);
        return { ...g, [field]: g.id === team ? [...rest, id] : rest };
      }),
    send: (idempotencyKey) => api.patch(`/api/v2/groups/${at(team)}`, { add: { [field]: [id] } }, { idempotencyKey }),
  });
  directoryAgain();
  void queryClient.invalidateQueries({ queryKey: agentsQuery.queryKey });
  void queryClient.invalidateQueries({ queryKey: peopleQuery.queryKey });
  return out;
}

export interface NewAgent {
  name: string;
  role?: string;
  team?: string;
  template?: string;
  computer?: string;
}

/** A new agent. Its model is left to the server, so it follows the connected plan's default. */
export async function addAgent(a: NewAgent): Promise<{ slug: string }> {
  const made = await api.post<{ slug: string }>('/api/v2/bots', {
    display_name: a.name,
    description: a.role ?? '',
    status: 'active',
    ...(a.template ? { template: a.template } : {}),
    ...(a.computer ? { runner_id: a.computer } : {}),
  });
  if (a.team && made.slug) await api.patch(`/api/v2/groups/${at(a.team)}`, { add: { bots: [made.slug] } });
  directoryAgain();
  void queryClient.invalidateQueries({ queryKey: ['team'] });
  return made;
}

export interface Invite {
  email: string;
  name?: string;
  team?: string;
  role?: 'member' | 'admin';
}

/** Add them, then send the invite; an admin role is set after. */
export async function invitePerson(p: Invite): Promise<{ person: string }> {
  const added = await api.post<{ person: string }>('/api/v2/access/humans', { email: p.email, name: p.name ?? '', team: p.team ?? '' });
  await api.post(`/api/v2/access/humans/${at(added.person)}/invite`, {});
  if (p.role === 'admin') await api.post(`/api/v2/access/humans/${at(added.person)}`, { role: 'admin' });
  directoryAgain();
  void queryClient.invalidateQueries({ queryKey: ['team'] });
  return added;
}

export function setPersonRole(person: string, role: 'member' | 'admin') {
  return write({
    key: accessQuery.queryKey,
    optimistic: (a: Access | null | undefined) => (a ? { ...a, people: a.people.map((p) => (p.id === person ? { ...p, role } : p)) } : a),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${at(person)}`, { role }, { idempotencyKey }),
  });
}

export async function setPersonTitle(person: string, title: string) {
  const out = await write({
    key: peopleQuery.queryKey,
    optimistic: (rows: PersonRow[] | undefined) => rows?.map((p) => (p.id === person ? { ...p, title } : p)),
    send: (idempotencyKey) => api.post(`/api/v2/humans/${at(person)}`, { title }, { idempotencyKey }),
  });
  directoryAgain();
  return out;
}

/** Remove them: their sign-in and tokens end; their history stays. */
/** A person's name: an owner's or admin's edit (POST /api/v2/access/humans/{id}). */
export async function renamePerson(person: string, name: string) {
  const out = await write({
    key: peopleQuery.queryKey,
    optimistic: (rows: PersonRow[] | undefined) => rows?.map((p) => (p.id === person ? { ...p, name } : p)),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${at(person)}`, { name }, { idempotencyKey }),
  });
  directoryAgain();
  return out;
}

export async function removePerson(person: string) {
  const out = await write({
    key: peopleQuery.queryKey,
    optimistic: (rows: PersonRow[] | undefined) => rows?.filter((p) => p.id !== person),
    send: (idempotencyKey) => api.post(`/api/v2/humans/${at(person)}`, { left: true }, { idempotencyKey }),
  });
  directoryAgain();
  return out;
}

export function restartComputer(id: string) {
  return write({
    key: computersQuery.queryKey,
    optimistic: (rows: Computer[] | undefined) => rows?.map((c) => (c.id === id ? { ...c, restarting: true } : c)),
    send: (idempotencyKey) => api.post<{ requested?: boolean; running?: number }>(`/api/v2/computers/${at(id)}/restart`, {}, { idempotencyKey }),
  });
}
