import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { AutomationOverviewRuns, AutomationTemplate, AutomationTemplates, EventTypeList, Webhook, WebhookCreate, WebhookCreated, WebhookList, WebhookUpdate } from '@/api/schema';
import type { Dest } from './board';
import { QUIET_STALE_MS, quietRoot, write } from '@/data';
import type {
  Delivery,
  DocFile,
  DocRead,
  DueDate,
  DueDetail,
  Group,
  Heartbeat,
  Hook,
  Maintenance,
  MaintenanceRun,
  Match,
  OrgRule,
  Payload,
  Reach,
  ReachValues,
  Routine,
  RuleGroups,
  RuleHold,
  Repeats,
  RuleSource,
  Run,
  ShownOnce,
  StageRule,
  Step,
  SystemJob,
  Trigger,
  Version,
  WatchSource,
} from './types';

/* Routines, Rules and Listening. No live topic carries automations, rules, triggers or listening (backend/events.py
   TOPICS), so each root is read again after the person's own writes, on reconnect and on open when older than 60 s.
   Company also reads Listening's stats under `listening`; this feature keeps to its own sub-keys there. */

for (const root of ['routines', 'rules', 'listening']) quietRoot(root);

const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS } as const;
const tmpId = () => `tmp:${Math.random().toString(36).slice(2, 10)}`;
const now = () => new Date().toISOString();

// ---------------------------------------------------------------- routines

/** Every routine a person sees on Routines: not Tico's own jobs (kind 'system') and not listening watches. */
export const routinesQuery = queryOptions({
  queryKey: ['routines', 'list'],
  queryFn: async ({ signal }) => {
    const { automations } = await api.get<{ automations: Routine[] }>('/api/v2/automations?limit=500', { signal });
    return automations.filter((a) => a.kind !== 'system' && a.trigger?.type !== 'listening');
  },
  ...quiet,
});

export const routineQuery = (id: string) =>
  queryOptions({
    queryKey: ['routines', 'one', id],
    queryFn: ({ signal }) => api.get<Routine>(`/api/v2/automations/${enc(id)}`, { signal }),
    ...quiet,
  });

export const runsQuery = (id: string) =>
  queryOptions({
    queryKey: ['routines', 'runs', id],
    queryFn: async ({ signal }) => (await api.get<{ runs: Run[] }>(`/api/v2/automations/${enc(id)}/runs?limit=20`, { signal })).runs,
    ...quiet,
  });

/** What a new routine or an edit sends: the old app's one-step routine, an agent taking a turn on the instruction. */
export interface RoutineFields {
  name: string;
  /** `bot:<id>`: the agent that runs it. */
  agent: string;
  trigger: Trigger;
  instruction: string;
}

const brief = (f: RoutineFields): Step[] => [{ id: 'brief', type: 'bot_turn', bot: f.agent, prompt: f.instruction }];

const mapList = (list: Routine[] | undefined, id: string, f: (r: Routine) => Routine | null) =>
  list?.flatMap((r) => {
    if (r.id !== id) return [r];
    const next = f(r);
    return next ? [next] : [];
  });

export function createRoutine(f: RoutineFields) {
  const id = tmpId();
  const shown: Routine = {
    id,
    name: f.name,
    owner_id: f.agent,
    enabled: true,
    can_edit: true,
    trigger: f.trigger,
    steps: brief(f),
    last_run: null,
    updated_at: now(),
  };
  return write<Routine>({
    key: routinesQuery.queryKey,
    optimistic: (list: Routine[] | undefined) => list && [...list, shown],
    send: (idempotencyKey) =>
      api.post<Routine>('/api/v2/automations', { name: f.name, owner: f.agent, trigger: f.trigger, steps: brief(f), enabled: true }, { idempotencyKey }),
    settle: (list: Routine[] | undefined, made) => mapList(list, id, () => made),
    revert: (list: Routine[] | undefined) => mapList(list, id, () => null),
  });
}

/** Its steps with the agent's turn changed in place: every other step (a filter, an action, a second turn) is kept as
   it is. A routine with no agent turn gets one only when an instruction was written. */
export function stepsWith(was: Step[] | undefined, f: RoutineFields): Step[] {
  const list = was ?? [];
  const at = list.findIndex((x) => x.type === 'bot_turn');
  if (at < 0) return !list.length || f.instruction ? [...list, ...brief(f)] : list;
  return list.map((x, i) => (i === at ? { ...x, bot: f.agent, prompt: f.instruction } : x));
}

/** Name, agent, trigger and instruction; `was` is its steps as loaded, so the steps the editor does not draw stay. */
export function updateRoutine(id: string, f: RoutineFields, was?: Step[]) {
  const steps = stepsWith(was, f);
  const patch = { name: f.name, owner: f.agent, trigger: f.trigger, steps };
  const change = (r: Routine): Routine => ({ ...r, name: f.name, owner_id: f.agent, trigger: f.trigger, steps });
  return write<Routine>({
    key: [routinesQuery.queryKey, routineQuery(id).queryKey],
    optimistic: (cur: Routine[] | Routine | undefined) => (Array.isArray(cur) ? mapList(cur, id, change) : cur && change(cur)),
    send: (idempotencyKey) => api.patch<Routine>(`/api/v2/automations/${enc(id)}`, patch, { idempotencyKey }),
  });
}

/** On or off. Off is the person's pause; `paused` stays the server's own (the guard turned it off). */
export function setRoutineOn(id: string, on: boolean, key: readonly unknown[] = routinesQuery.queryKey, one: readonly unknown[] = routineQuery(id).queryKey) {
  const change = (r: Routine): Routine => ({ ...r, enabled: on, ...(on ? { paused: false } : {}) });
  return write<Routine>({
    key: [key as unknown[], one as unknown[]],
    optimistic: (cur: Routine[] | Routine | undefined) => (Array.isArray(cur) ? mapList(cur, id, change) : cur && change(cur)),
    send: (idempotencyKey) => api.patch<Routine>(`/api/v2/automations/${enc(id)}`, { enabled: on }, { idempotencyKey }),
  });
}

/** Its spend limit: dollars each 30 minutes, or null for the agent's own limit (PATCH spend_cap). */
export function setRoutineLimit(id: string, per30m: number | null) {
  const cap = per30m === null ? null : { per_30m: Math.round(per30m * 100) / 100 };
  const change = (r: Routine): Routine => ({ ...r, spend_cap: cap });
  return write<Routine>({
    key: [routinesQuery.queryKey, routineQuery(id).queryKey],
    optimistic: (cur: Routine[] | Routine | undefined) => (Array.isArray(cur) ? mapList(cur, id, change) : cur && change(cur)),
    send: (idempotencyKey) => api.patch<Routine>(`/api/v2/automations/${enc(id)}`, { spend_cap: cap }, { idempotencyKey }),
  });
}

/** Run it now; `input` stands in for the trigger's output (a test run). A sample its filters drop makes no run. */
export function runRoutine(id: string, input?: Record<string, unknown>) {
  const shown: Run = { id: tmpId(), automation_id: id, status: 'queued', started_at: now(), steps: null, version: 0 };
  return write<{ run: Run | null; dropped?: boolean }>({
    key: runsQuery(id).queryKey,
    optimistic: (runs: Run[] | undefined) => [shown, ...(runs ?? [])],
    send: (idempotencyKey) =>
      api.post<{ run: Run | null; dropped?: boolean }>(`/api/v2/automations/${enc(id)}/run`, input ? { input } : {}, { idempotencyKey }),
    settle: (runs: Run[] | undefined, r) => runs?.flatMap((x) => (x.id !== shown.id ? [x] : r.run ? [r.run] : [])),
    revert: (runs: Run[] | undefined) => runs?.filter((x) => x.id !== shown.id),
  });
}

/** Its versions, newest first; an agent's proposal is a `pending` one. */
export const versionsQuery = (id: string) =>
  queryOptions({
    queryKey: ['routines', 'versions', id],
    queryFn: async ({ signal }) => {
      const r = await api.get<{ versions: Version[] }>(`/api/v2/automations/${enc(id)}/versions`, { signal });
      return [...(r.versions ?? [])].sort((a, b) => b.n - a.n);
    },
    ...quiet,
  });

/** Restore a version as a new one, or turn a proposal on (it keeps its number). */
export function restoreVersion(id: string, n: number) {
  return write<Routine>({
    key: [versionsQuery(id).queryKey, routinesQuery.queryKey, routineQuery(id).queryKey],
    optimistic: (cur: unknown) => cur,
    send: (idempotencyKey) => api.post<Routine>(`/api/v2/automations/${enc(id)}/versions/${n}/restore`, {}, { idempotencyKey }),
    // a version has no id, so only the routine's own row takes the answer
    settle: (cur: (Version | Routine)[] | Routine | undefined, made) =>
      Array.isArray(cur) ? cur.map((x) => ('id' in x && x.id === id ? made : x)) : cur && made,
  });
}

/** Set an agent's proposal aside. */
export function discardVersion(id: string, n: number) {
  return write<unknown>({
    key: versionsQuery(id).queryKey,
    optimistic: (list: Version[] | undefined) => list?.filter((v) => v.n !== n),
    send: (idempotencyKey) => api.delete(`/api/v2/automations/${enc(id)}/versions/${n}`, { idempotencyKey }),
  });
}

export function removeRoutine(id: string, key: readonly unknown[] = routinesQuery.queryKey) {
  return write<unknown>({
    key: key as unknown[],
    optimistic: (list: Routine[] | undefined) => mapList(list, id, () => null),
    send: (idempotencyKey) => api.delete(`/api/v2/automations/${enc(id)}`, { idempotencyKey }),
  });
}

/** Its steps and destinations as one new version (the board, a template applied). */
export function setFlow(id: string, flow: { steps: Step[]; destinations: Dest[] }, summary?: string) {
  const change = (r: Routine): Routine => ({ ...r, steps: flow.steps, destinations: flow.destinations });
  return write<Routine>({
    key: [routinesQuery.queryKey, routineQuery(id).queryKey],
    optimistic: (cur: Routine[] | Routine | undefined) => (Array.isArray(cur) ? mapList(cur, id, change) : cur && change(cur)),
    send: (idempotencyKey) => api.patch<Routine>(`/api/v2/automations/${enc(id)}`, { ...flow, ...(summary ? { summary } : {}) }, { idempotencyKey }),
    settle: (cur: Routine[] | Routine | undefined, made) => (Array.isArray(cur) ? mapList(cur, id, () => made) : cur && made),
  });
}

// ---------------------------------------------------------------- every run, across routines

export const RUN_RANGES = ['5m', '1h', '24h', '7d'] as const;
export type RunRange = (typeof RUN_RANGES)[number];
export const RUN_STATUSES = ['failed', 'waiting', 'running', 'done'] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];
export type AnyRun = AutomationOverviewRuns['runs'][number];

/** Runs across every routine the person may see, newest first, fifty a page; `next` is the next page's `before`. */
export const allRunsQuery = (range: RunRange, status?: RunStatus) =>
  infiniteQueryOptions({
    queryKey: ['routines', 'all-runs', range, status ?? ''],
    queryFn: ({ signal, pageParam }) => {
      const q = new URLSearchParams({ range, limit: '50', ...(status ? { status } : {}), ...(pageParam ? { before: pageParam } : {}) });
      return api.get<AutomationOverviewRuns>(`/api/v2/automations/runs?${q.toString()}`, { signal });
    },
    initialPageParam: '',
    getNextPageParam: (last) => last.next || undefined,
    ...quiet,
  });

// ---------------------------------------------------------------- inbox templates

export const templatesQuery = queryOptions({
  queryKey: ['routines', 'templates'],
  queryFn: async ({ signal }) => (await api.get<AutomationTemplates>('/api/v2/automations/templates', { signal })).templates,
  ...quiet,
});

/** A template filled in for an owner (`person:`, `bot:` or `group:<id>`) and an inbox: the flow the server would save.
   Nothing is saved; the server refuses an owner the person may not make a routine for, or one that can't hear it. */
export const fillTemplate = (tid: string, owner: string, inbox: string, signal?: AbortSignal) =>
  api.get<AutomationTemplate>(`/api/v2/automations/templates/${enc(tid)}?${new URLSearchParams({ owner, inbox }).toString()}`, { signal });

export const filledQuery = (tid: string, owner: string, inbox: string) =>
  queryOptions({
    queryKey: ['routines', 'template', tid, owner, inbox],
    queryFn: ({ signal }) => fillTemplate(tid, owner, inbox, signal),
    staleTime: 0,
    gcTime: 0,
  });

/** A new inbox routine from a filled template, on or off. */
export function createInbox(f: AutomationTemplate, on: boolean) {
  return write<Routine>({
    key: routinesQuery.queryKey,
    optimistic: (list: Routine[] | undefined) => list,
    send: (idempotencyKey) =>
      api.post<Routine>(
        '/api/v2/automations',
        { name: f.name, owner: f.owner, enabled: on, builtin: false, trigger: f.trigger, steps: f.steps, destinations: f.destinations },
        { idempotencyKey },
      ),
    settle: (list: Routine[] | undefined, made) => list && [...list.filter((r) => r.id !== made.id), made],
  });
}

// ---------------------------------------------------------------- built in: Maintenance, Heartbeat, System jobs

export const maintenanceQuery = queryOptions({
  queryKey: ['routines', 'maintenance'],
  queryFn: ({ signal }) => api.get<Maintenance>('/api/v2/maintenance', { signal }),
  ...quiet,
});
export const maintenanceRunsQuery = queryOptions({
  queryKey: ['routines', 'maintenance', 'runs'],
  queryFn: async ({ signal }) => (await api.get<{ runs: MaintenanceRun[] }>('/api/v2/maintenance/runs?limit=10', { signal })).runs,
  ...quiet,
});
export const heartbeatQuery = queryOptions({
  queryKey: ['routines', 'heartbeat'],
  queryFn: ({ signal }) => api.get<Heartbeat>('/api/v2/heartbeat?limit=10', { signal }),
  ...quiet,
});
export const systemJobsQuery = queryOptions({
  queryKey: ['routines', 'system'],
  queryFn: async ({ signal }) => (await api.get<{ jobs: SystemJob[] }>('/api/v2/system-routines', { signal })).jobs,
  ...quiet,
});

export type MaintenanceChange = { schedule?: string; paused?: boolean; step?: { key: string; enabled: boolean } };

export function setMaintenance(c: MaintenanceChange) {
  const body = { schedule: c.schedule, paused: c.paused, steps: c.step ? [c.step] : undefined };
  return write<Maintenance>({
    key: maintenanceQuery.queryKey,
    optimistic: (m: Maintenance | undefined) =>
      m && {
        ...m,
        settings: { ...m.settings, ...(c.schedule ? { schedule: c.schedule } : {}), ...(c.paused !== undefined ? { paused: c.paused } : {}) },
        steps: c.step ? m.steps.map((s) => (s.key === c.step?.key ? { ...s, enabled: c.step.enabled } : s)) : m.steps,
      },
    send: (idempotencyKey) => api.patch<Maintenance>('/api/v2/maintenance', body, { idempotencyKey }),
  });
}

export function runMaintenance() {
  return write<unknown>({
    key: maintenanceRunsQuery.queryKey,
    optimistic: (runs: MaintenanceRun[] | undefined) => [{ id: tmpId(), started_at: now(), finished_at: null, ok: null }, ...(runs ?? [])],
    send: (idempotencyKey) => api.post('/api/v2/maintenance/run', {}, { idempotencyKey }),
  });
}

export function setHeartbeat(interval_s: number) {
  return write<Heartbeat>({
    key: heartbeatQuery.queryKey,
    optimistic: (h: Heartbeat | undefined) => h && { ...h, interval_s },
    send: (idempotencyKey) => api.put<Heartbeat>('/api/v2/heartbeat', { interval_s }, { idempotencyKey }),
  });
}

/** A system job's switch; a Heartbeat step with its own switch saves through the same call. */
export function setJob(key: string, on: boolean) {
  return write<unknown>({
    key: [systemJobsQuery.queryKey, heartbeatQuery.queryKey],
    optimistic: (cur: SystemJob[] | Heartbeat | undefined) =>
      Array.isArray(cur) ? cur.map((j) => (j.key === key ? { ...j, on } : j)) : cur && { ...cur, steps: cur.steps.map((s) => (s.key === key ? { ...s, on } : s)) },
    send: (idempotencyKey) => api.put(`/api/v2/system-routines/${enc(key)}`, { on }, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- incoming webhooks

export const hooksQuery = queryOptions({
  queryKey: ['routines', 'hooks'],
  queryFn: async ({ signal }) => (await api.get<{ triggers: Hook[] }>('/api/v2/triggers', { signal })).triggers,
  ...quiet,
});

export const deliveriesQuery = (id: string) =>
  queryOptions({
    queryKey: ['routines', 'hooks', id, 'deliveries'],
    queryFn: async ({ signal }) => {
      const r = await api.get<{ deliveries: Delivery[] } | Delivery[]>(`/api/v2/triggers/${enc(id)}/deliveries`, { signal });
      return Array.isArray(r) ? r : r.deliveries;
    },
    ...quiet,
  });

/** One kept call's headers and body. Read when opened, and not kept after. */
export const payloadQuery = (hook: string, delivery: string) =>
  queryOptions({
    queryKey: ['routines', 'hooks', hook, 'payload', delivery],
    queryFn: async ({ signal }) => (await api.get<{ delivery: Payload }>(`/api/v2/triggers/${enc(hook)}/deliveries/${enc(delivery)}`, { signal })).delivery,
    staleTime: 0,
    gcTime: 0,
  });

/* The full address of a webhook, secret included, is given once: at creation and on a new secret. It goes back to
   the caller only (the panel's state): never the cache, a toast, a log or the address bar. */
export async function createHook(name: string): Promise<{ id: string; url: string }> {
  const id = tmpId();
  const shown: Hook = { id, name, url: '', received_24h: 0, automations: [] };
  const r = await write<{ trigger: ShownOnce }>({
    key: hooksQuery.queryKey,
    optimistic: (list: Hook[] | undefined) => list && [...list, shown],
    send: (idempotencyKey) => api.post<{ trigger: ShownOnce }>('/api/v2/triggers', { name }, { idempotencyKey }),
    settle: (list: Hook[] | undefined, made) => list?.map((h) => (h.id === id ? { ...h, id: made.trigger.id, url: stripSecret(made.trigger.url) } : h)),
    revert: (list: Hook[] | undefined) => list?.filter((h) => h.id !== id),
  });
  return { id: r.trigger.id, url: r.trigger.url };
}

export async function rotateHook(id: string): Promise<string> {
  return (await api.post<{ trigger: ShownOnce }>(`/api/v2/triggers/${enc(id)}/rotate`, {})).trigger.url;
}

export function removeHook(id: string) {
  return write<unknown>({
    key: hooksQuery.queryKey,
    optimistic: (list: Hook[] | undefined) => list?.filter((h) => h.id !== id),
    send: (idempotencyKey) => api.post(`/api/v2/triggers/${enc(id)}/delete`, {}, { idempotencyKey }),
  });
}

/** Check each call with a preset; with no secret given, the server makes one. The answer's secret goes back to the
   caller only: no cache, no log. `replayed` means it was lost on the way (a resend), so ask again for a new one. */
export async function signHook(id: string, preset: string, secret?: string): Promise<{ secret?: string; replayed?: boolean }> {
  const r = await write<{ trigger: { signing_secret?: string; replayed?: boolean } }>({
    key: hooksQuery.queryKey,
    optimistic: (list: Hook[] | undefined) => list?.map((h) => (h.id === id ? { ...h, signed: true, preset } : h)),
    send: (idempotencyKey) => api.post(`/api/v2/triggers/${enc(id)}/signing`, { preset, ...(secret ? { secret } : {}) }, { idempotencyKey }),
  });
  return { secret: secret ? undefined : r.trigger.signing_secret, replayed: r.trigger.replayed };
}

export function signHookOff(id: string) {
  return write<unknown>({
    key: hooksQuery.queryKey,
    optimistic: (list: Hook[] | undefined) => list?.map((h) => (h.id === id ? { ...h, signed: false, preset: 'none' } : h)),
    send: (idempotencyKey) => api.post(`/api/v2/triggers/${enc(id)}/signing`, { off: true }, { idempotencyKey }),
  });
}

/** The address without its secret: what a row may copy. The secret is the last path part (backend/triggers.py). */
export function stripSecret(url: string): string {
  return url.replace(/\/[^/]+$/, '');
}

// ---------------------------------------------------------------- outgoing endpoints (/v2/webhooks)

export type Endpoint = Webhook;

export const endpointsQuery = queryOptions({
  queryKey: ['routines', 'endpoints'],
  queryFn: async ({ signal }) => (await api.get<WebhookList>('/api/v2/webhooks?limit=200', { signal })).items,
  ...quiet,
});

/** The events an endpoint may send: the catalog less the ones only rules see. */
export const eventTypesQuery = queryOptions({
  queryKey: ['routines', 'event-types'],
  queryFn: async ({ signal }) => (await api.get<EventTypeList>('/api/v2/event-types', { signal })).items.filter((t) => t.outbound !== false),
  staleTime: 60 * 60_000,
});

type Shown = Webhook & { secret?: string; replayed?: boolean };
const unsecret = ({ secret: _s, replayed: _r, ...h }: Shown): Webhook => h;

/* A new endpoint's signing secret is in the answer once. It goes back to the caller only (the dialog's state): the
   list keeps the endpoint without it. An agent's call files an approval instead. */
export async function createEndpoint(body: WebhookCreate): Promise<{ id?: string; secret?: string; replayed?: boolean; approval?: boolean }> {
  const r = await write<WebhookCreated & { webhook?: Shown }>({
    key: endpointsQuery.queryKey,
    optimistic: (list: Webhook[] | undefined) => list,
    send: (idempotencyKey) => api.post<WebhookCreated & { webhook?: Shown }>('/api/v2/webhooks', body, { idempotencyKey }),
    settle: (list: Webhook[] | undefined, made) => (made.webhook && list ? [...list.filter((h) => h.id !== made.webhook?.id), unsecret(made.webhook)] : list),
  });
  if (!r.webhook) return { approval: !!r.approval };
  return { id: r.webhook.id, secret: r.webhook.secret, replayed: !!r.webhook.replayed };
}

export function updateEndpoint(id: string, body: WebhookUpdate) {
  return write<{ webhook: Webhook }>({
    key: endpointsQuery.queryKey,
    optimistic: (list: Webhook[] | undefined) =>
      list?.map((h) => (h.id === id ? { ...h, ...(body.name ? { name: body.name } : {}), ...(body.events ? { events: body.events } : {}) } : h)),
    send: (idempotencyKey) => api.post<{ webhook: Webhook }>(`/api/v2/webhooks/${enc(id)}/update`, body, { idempotencyKey }),
    settle: (list: Webhook[] | undefined, made) => list?.map((h) => (h.id === id ? made.webhook : h)),
  });
}

// ---------------------------------------------------------------- rules

export const orgRulesQuery = (status: 'active' | 'suggested') =>
  queryOptions({
    queryKey: ['rules', 'org', status],
    queryFn: async ({ signal }) => (await api.get<{ rules: OrgRule[] }>(`/api/v2/rules?status=${status}&limit=100`, { signal })).rules,
    ...quiet,
  });

export interface StageRules {
  items: StageRule[];
  built_in: { name: string; what?: string }[];
  may_add: boolean;
}
export const stageRulesQuery = (status: 'active' | 'suggested') =>
  queryOptions({
    queryKey: ['rules', 'stage', status],
    queryFn: ({ signal }) => api.get<StageRules>(`/api/v2/stage-rules${status === 'suggested' ? '?status=suggested' : ''}`, { signal }),
    ...quiet,
  });

export const ruleGroupsQuery = queryOptions({
  queryKey: ['rules', 'groups'],
  queryFn: ({ signal }) => api.get<RuleGroups>('/api/v2/rule-groups', { signal }),
  ...quiet,
});

export const dueQuery = queryOptions({
  queryKey: ['rules', 'due'],
  queryFn: async ({ signal }) => (await api.get<{ due: DueDate[] }>('/api/v2/rules/due?next=3660d', { signal })).due,
  ...quiet,
});

/** One due date in full: its history and what the viewer may do. */
export const dueOneQuery = (id: string) =>
  queryOptions({
    queryKey: ['rules', 'due', id],
    queryFn: ({ signal }) => api.get<DueDetail>(`/api/v2/rule-due/${enc(id)}`, { signal }),
    ...quiet,
  });

export interface DueFields {
  first_due?: string;
  repeats?: Repeats;
  /** `bot:<id>` or `human:<id>`. */
  owner?: string;
  /** Open a task this many days before each due day; null for none. */
  task_days?: number | null;
}

/** A due date on a rule. Its row shows when the list is read again. */
export function addDue(rule: string, f: Required<Pick<DueFields, 'first_due' | 'repeats' | 'owner'>> & Pick<DueFields, 'task_days'>) {
  return write<DueDetail>({
    key: dueQuery.queryKey,
    optimistic: (list: DueDate[] | undefined) => list,
    send: (idempotencyKey) => api.post<DueDetail>(`/api/v2/rules/${enc(rule)}/due`, f, { idempotencyKey }),
  });
}

/** A new date or repeat starts a new row from it (the old one keeps its marks); the answer is the live row. */
export function updateDue(id: string, f: DueFields) {
  return write<DueDetail>({
    key: [dueQuery.queryKey, dueOneQuery(id).queryKey],
    optimistic: (cur: DueDate[] | DueDetail | undefined) =>
      Array.isArray(cur) ? cur.map((d) => (d.id === id ? { ...d, ...(f.first_due ? { due_on: f.first_due } : {}), ...(f.owner ? { owner: f.owner } : {}) } : d)) : cur && { ...cur, ...f, due_on: f.first_due ?? cur.due_on },
    send: (idempotencyKey) => api.post<DueDetail>(`/api/v2/rule-due/${enc(id)}`, f, { idempotencyKey }),
  });
}

/** Mark an occurrence satisfied: `due_on` names which when more than one is open. */
export function satisfyDue(id: string, dueOn?: string) {
  return write<unknown>({
    key: [dueQuery.queryKey, dueOneQuery(id).queryKey],
    optimistic: (cur: DueDate[] | DueDetail | undefined) => (Array.isArray(cur) || !cur ? cur : { ...cur, status: 'satisfied' }),
    send: (idempotencyKey) => api.post(`/api/v2/rule-due/${enc(id)}/satisfied`, dueOn ? { due_on: dueOn } : {}, { idempotencyKey }),
  });
}

export function removeDue(id: string) {
  return write<unknown>({
    key: dueQuery.queryKey,
    optimistic: (list: DueDate[] | undefined) => list?.filter((d) => d.id !== id),
    send: (idempotencyKey) => api.post(`/api/v2/rule-due/${enc(id)}/remove`, {}, { idempotencyKey }),
  });
}

/** Where outside rules come from (From). */
export const ruleSourcesQuery = queryOptions({
  queryKey: ['rules', 'sources'],
  queryFn: async ({ signal }) => (await api.get<{ sources: RuleSource[] }>('/api/v2/rule-sources?limit=200', { signal })).sources,
  ...quiet,
});

/** Files kinds no text is read from. */
const UNREAD = new Set(['image', 'media']);
/** The recent files a document can be picked from: ones with text to read. */
export const docFilesQuery = queryOptions({
  queryKey: ['rules', 'files'],
  queryFn: async ({ signal }) => {
    const r = await api.get<{ files?: DocFile[] }>('/api/v2/files?limit=50', { signal });
    return (r.files ?? []).filter((f) => f.locator !== 'remote_link' && !f.url && !UNREAD.has(f.library_kind ?? '') && !f.archived);
  },
  ...quiet,
});

/** A file or a link for an agent to read and suggest outside rules from. `source` is a From's id, or a new one's name
   with its kind. */
export function readDocument(b: { file_id?: string; url?: string; note: string; source_id: string; kind?: string }) {
  return write<DocRead>({
    key: [ruleSourcesQuery.queryKey],
    optimistic: (cur: unknown) => cur,
    send: (idempotencyKey) => api.post<DocRead>('/api/v2/rules/read', b, { idempotencyKey }),
  });
}

export const holdsQuery = queryOptions({
  queryKey: ['rules', 'holds'],
  queryFn: async ({ signal }) => {
    const r = await api.get<{ holds: RuleHold[] } | RuleHold[]>('/api/v2/rule-holds', { signal });
    return Array.isArray(r) ? r : r.holds;
  },
  ...quiet,
});

export const reachQuery = queryOptions({
  queryKey: ['rules', 'reach'],
  queryFn: ({ signal }) => api.get<Reach>('/api/v2/reach', { signal }),
  ...quiet,
});

export const teamsQuery = queryOptions({
  queryKey: ['rules', 'teams'],
  queryFn: async ({ signal }) => {
    const r = await api.get<Group[] | { groups: Group[] }>('/api/v2/groups', { signal });
    return Array.isArray(r) ? r : r.groups;
  },
  ...quiet,
});

/** A company rule as the editor writes it. */
export interface OrgRuleFields {
  kind: 'internal' | 'external';
  text: string;
  if_broken: 'block' | 'escalate' | 'warn';
  reviewer?: string | null;
  /** Everyone, or one team, agent or person: `*`, `group:<id>`, `bot:<id>`, `human:<id>`. */
  who: string;
}
const scopeOf = (who: string) =>
  who === '*'
    ? { everyone: true }
    : who.startsWith('group:')
      ? { groups: [who.slice(6)] }
      : who.startsWith('bot:')
        ? { bots: [who.slice(4)] }
        : { people: [who.replace(/^(human|person):/, '')] };

const orgBody = (f: OrgRuleFields) => ({
  kind: f.kind,
  text: f.text,
  if_broken: f.if_broken,
  reviewer: f.if_broken === 'escalate' ? (f.reviewer ?? null) : null,
  who: scopeOf(f.who),
});

export function addOrgRule(f: OrgRuleFields) {
  const id = tmpId();
  const shown: OrgRule = { id, kind: f.kind, text: f.text, status: 'active', version: 1, can_edit: true, if_broken: f.if_broken, updated_at: now() };
  return write<OrgRule>({
    key: orgRulesQuery('active').queryKey,
    optimistic: (list: OrgRule[] | undefined) => list && [...list, shown],
    send: (idempotencyKey) => api.post<OrgRule>('/api/v2/rules', orgBody(f), { idempotencyKey }),
    settle: (list: OrgRule[] | undefined, made) => list?.map((r) => (r.id === id ? made : r)),
    revert: (list: OrgRule[] | undefined) => list?.filter((r) => r.id !== id),
  });
}

export function updateOrgRule(rule: Pick<OrgRule, 'id' | 'version'>, f: OrgRuleFields) {
  return write<OrgRule>({
    key: orgRulesQuery('active').queryKey,
    optimistic: (list: OrgRule[] | undefined) => list?.map((r) => (r.id === rule.id ? { ...r, text: f.text, if_broken: f.if_broken } : r)),
    send: (idempotencyKey) => api.post<OrgRule>(`/api/v2/rules/${enc(rule.id)}`, { version: rule.version, ...orgBody(f), kind: undefined }, { idempotencyKey }),
    settle: (list: OrgRule[] | undefined, made) => list?.map((r) => (r.id === rule.id ? made : r)),
  });
}

/** Retire (in force), accept or skip (a suggestion): each takes the rule out of the list it was in. */
export function decideOrgRule(rule: Pick<OrgRule, 'id' | 'version' | 'status'>, verb: 'retire' | 'accept' | 'skip') {
  const from = orgRulesQuery(rule.status === 'suggested' ? 'suggested' : 'active').queryKey;
  return write<unknown>({
    key: verb === 'accept' ? [from, orgRulesQuery('active').queryKey] : from,
    optimistic: (list: OrgRule[] | undefined) => list?.filter((r) => r.id !== rule.id),
    send: (idempotencyKey) => api.post(`/api/v2/rules/${enc(rule.id)}/${verb}`, { version: rule.version }, { idempotencyKey }),
  });
}

/** A stage rule as the editor writes it. */
export interface StageRuleFields {
  name: string;
  pipeline: string;
  stages: string[];
  event: string;
  timer: { kind: string; hours: number } | null;
  cond: Record<string, unknown>;
  jev: string;
  then: string;
  reason: string;
}

export function addStageRule(f: StageRuleFields) {
  const id = tmpId();
  const shown: StageRule = { id, ...f, pipeline_id: f.pipeline, scope: f.stages.length ? 'stage' : 'pipeline', status: 'active', revision: 1, can_edit: true };
  const key = stageRulesQuery('active').queryKey;
  return write<StageRule>({
    key,
    optimistic: (cur: StageRules | undefined) => cur && { ...cur, items: [...cur.items, shown] },
    send: (idempotencyKey) => api.post<StageRule>('/api/v2/stage-rules', f, { idempotencyKey }),
    settle: (cur: StageRules | undefined, made) => cur && { ...cur, items: cur.items.map((r) => (r.id === id ? made : r)) },
    revert: (cur: StageRules | undefined) => cur && { ...cur, items: cur.items.filter((r) => r.id !== id) },
  });
}

export function updateStageRule(rule: Pick<StageRule, 'id' | 'revision'>, f: StageRuleFields) {
  return write<StageRule>({
    key: stageRulesQuery('active').queryKey,
    optimistic: (cur: StageRules | undefined) => cur && { ...cur, items: cur.items.map((r) => (r.id === rule.id ? { ...r, ...f, pipeline_id: f.pipeline } : r)) },
    send: (idempotencyKey) => api.post<StageRule>(`/api/v2/stage-rules/${enc(rule.id)}`, { revision: rule.revision, ...f }, { idempotencyKey }),
    settle: (cur: StageRules | undefined, made) => cur && { ...cur, items: cur.items.map((r) => (r.id === rule.id ? made : r)) },
  });
}

export function decideStageRule(rule: Pick<StageRule, 'id' | 'revision' | 'status'>, verb: 'retire' | 'accept' | 'skip') {
  const from = stageRulesQuery(rule.status === 'suggested' ? 'suggested' : 'active').queryKey;
  return write<unknown>({
    key: verb === 'accept' ? [from, stageRulesQuery('active').queryKey] : from,
    optimistic: (cur: StageRules | undefined) => cur && { ...cur, items: cur.items.filter((r) => r.id !== rule.id) },
    send: (idempotencyKey) => api.post(`/api/v2/stage-rules/${enc(rule.id)}/${verb}`, { revision: rule.revision }, { idempotencyKey }),
  });
}

/** One party's limits: `*`, `group:<id>`, `bot:<id>`, `human:<id>`. A null value goes back to what it inherits. */
export function setReach(party: string, values: ReachValues) {
  return write<unknown>({
    key: reachQuery.queryKey,
    optimistic: (r: Reach | undefined) => {
      if (!r) return r;
      const had = r.settings.some((s) => s.party === party);
      const settings = had ? r.settings.map((s) => (s.party === party ? { ...s, ...values } : s)) : [...r.settings, { party, ...values }];
      const effective = { ...r.effective, [party]: { ...r.effective[party], ...values } };
      return { ...r, settings, effective };
    },
    send: (idempotencyKey) => api.put(`/api/v2/reach/settings/${enc(party)}`, values, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- listening

export const watchesQuery = queryOptions({
  queryKey: ['listening', 'watches'],
  queryFn: async ({ signal }) => (await api.get<{ automations: Routine[] }>('/api/v2/automations?trigger=listening&limit=500', { signal })).automations,
  ...quiet,
});

export const watchQuery = (id: string) =>
  queryOptions({
    queryKey: ['listening', 'watch', id],
    queryFn: ({ signal }) => api.get<Routine>(`/api/v2/automations/${enc(id)}`, { signal }),
    ...quiet,
  });

export const matchesQuery = (id: string) =>
  queryOptions({
    queryKey: ['listening', 'items', id],
    queryFn: async ({ signal }) => (await api.get<{ items: Match[] }>(`/api/v2/listening/items?automation=${enc(id)}&limit=100`, { signal })).items,
    ...quiet,
  });

export const sourcesQuery = (id: string) =>
  queryOptions({
    queryKey: ['listening', 'sources', id],
    queryFn: async ({ signal }) => (await api.get<{ sources: WatchSource[] }>(`/api/v2/listening/sources?automation=${enc(id)}`, { signal })).sources,
    ...quiet,
  });

/** A new watch, as the old app made one: off, no sources yet, ten a day, posting each kept match as an update. */
export async function createWatch(name: string, me: string) {
  const tz = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
      return 'UTC';
    }
  })();
  const body = {
    name,
    owner: me.replace(/^human:/, 'person:'),
    enabled: false,
    builtin: false,
    trigger: { type: 'listening', sources: [], rules: [], limits: [{ count: 10, per: 'day' }], at: '08:00', tz },
    steps: [{ id: 'post', type: 'action', enabled: true, do: 'post_update', args: { text: '{{listening.text}}' } }],
  };
  const made = await write<Routine>({
    key: watchesQuery.queryKey,
    optimistic: (list: Routine[] | undefined) => list,
    send: (idempotencyKey) => api.post<Routine>('/api/v2/automations', body, { idempotencyKey }),
    settle: (list: Routine[] | undefined, r) => list && [...list.filter((w) => w.id !== r.id), r],
  });
  return made.id;
}

/** A watch's sources and keep or drop rules live in its trigger; a change sends the trigger whole. */
export function setWatchTrigger(watch: Routine, trigger: Trigger) {
  return write<Routine>({
    key: watchQuery(watch.id).queryKey,
    optimistic: (w: Routine | undefined) => w && { ...w, trigger },
    send: (idempotencyKey) => api.patch<Routine>(`/api/v2/automations/${enc(watch.id)}`, { trigger }, { idempotencyKey }),
  });
}
