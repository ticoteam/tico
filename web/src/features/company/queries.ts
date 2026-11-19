import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  CompanyFloor,
  FinanceMetric,
  Goal,
  GoalCheckinView,
  GoalCreate,
  GoalResult,
  GoalTree,
  GoalUpdate,
  Group,
  Kpi,
  KpiCreate,
  KpiDetail,
  KpiReadingResult,
  KpiReadingView,
  KpiTarget,
  KpiUpdate,
  MatterList,
  MatterRow,
  Product,
  ProductList,
  ProductResult,
  ProductUpdate,
  RiskDetail,
  RiskList,
  RiskResult,
  RiskUpdate,
  TaskBrief,
  Usage,
} from '@/api/schema';
import { keys, QUIET_STALE_MS, quietRoot, write } from '@/data';

/* Company, Strategy, Products and Risk. None of these has a live topic: each root is read again after the person's
   own writes, on reconnect and on open when older than 60 s. Three widgets on Company read other areas' endpoints
   under those areas' roots (finance, usage, needs) and Market reads Listening's stats under `listening`. */

for (const root of ['company', 'goals', 'kpis', 'strategy', 'brand', 'market', 'products', 'risks', 'groups', 'finance', 'usage', 'needs', 'listening']) quietRoot(root);

const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS } as const;
const tmpId = () => `tmp:${Math.random().toString(36).slice(2, 10)}`;

// ---------------------------------------------------------------- Company: the Floor and its figures

export type Pipeline = CompanyFloor['factories'][number]['pipelines'][number];
export type Stage = Pipeline['stages'][number];

export const floorQuery = queryOptions({
  queryKey: ['company', 'floor'],
  queryFn: ({ signal }) => api.get<CompanyFloor>('/api/v2/company/floor', { signal }),
  ...quiet,
});

/** The open tasks in one stage, as the stage panel lists them. A task-list key, so the stream keeps it current. */
export const stageParams = (pipeline: string, stage: string) => ({ brief: true, pipeline, stage, status: 'open', snoozed: 'all', limit: 20 });
export const stageTasksQuery = (pipeline: string, stage: string) => {
  const params = stageParams(pipeline, stage);
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) =>
      api.get<{ tasks: TaskBrief[]; cursor?: string }>(`/api/v2/tasks?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`, {
        signal,
      }),
  });
};

/** Revenue over the last seven days, in dollars; null when the person may not read finance. */
export const revenueQuery = queryOptions({
  queryKey: ['finance', 'metric', 'revenue', { days: 7 }],
  queryFn: async ({ signal }) => {
    try {
      return await api.get<FinanceMetric>('/api/v2/finance/metrics/revenue?days=7', { signal });
    } catch {
      return null;
    }
  },
  ...quiet,
});

export const needsQuery = queryOptions({
  queryKey: ['needs', 'summary'],
  queryFn: ({ signal }) => api.get<{ total: number; this_week: number }>('/api/v2/needs-you/summary', { signal }),
  ...quiet,
});

const day = (d: Date) => d.toISOString().slice(0, 10);
/** Agent spend over the last 30 days, by day. */
export const spendQuery = queryOptions({
  queryKey: ['usage', 'company', { days: 30 }],
  queryFn: async ({ signal }) => {
    const to = new Date();
    const from = new Date(to.getTime() - 29 * 86_400_000);
    try {
      return await api.get<Usage>(`/api/v2/usage?group=day&from=${day(from)}&to=${day(to)}`, { signal });
    } catch {
      return null;
    }
  },
  ...quiet,
});

// ---------------------------------------------------------------- Strategy

export interface Bet {
  id: string;
  statement: string;
  why?: string;
  status: 'proposed' | 'active' | 'retired';
  rank?: number | null;
}
export interface StrategyVersion {
  n: number;
  summary?: string;
  vision?: string;
  sections: Record<string, string> & { extra?: Record<string, { title: string; body: string }> };
  published_at?: string | null;
  published_by?: string | null;
}
/** GET /api/v2/strategy (backend/strategy.py view); the schema leaves it untyped. */
export interface Strategy {
  current: StrategyVersion | null;
  bets: Bet[];
  label?: string;
  may_edit?: boolean;
  goals_by_bet?: Record<string, { id: string; title: string; owner_id: string; status: string | null }[]>;
  health_by_bet?: Record<string, string | null>;
}

export const strategyQuery = queryOptions({
  queryKey: ['strategy'],
  queryFn: ({ signal }) => api.get<Strategy>('/api/v2/strategy', { signal }),
  ...quiet,
});

// Market: the latest briefing, the market's spend and players, and Listening's posts a day.
export interface MarketItem {
  text: string;
  kind?: string;
  entity_ids?: string[];
}
export interface MarketPlayer {
  entity_id?: string;
  name: string;
  figures?: { metric?: string; value?: number | string | null }[] | null;
}
export interface MarketSpend {
  name?: string | null;
  total?: { spend_usd?: number | null } | null;
  segments?: { id: string; name: string; parent?: string | null; players?: MarketPlayer[] | null; self?: boolean }[] | null;
}
export interface MarketBriefing {
  briefing?: { title: string; created_at?: string; items?: MarketItem[] } | null;
}
export interface ListeningStats {
  daily?: { day: string; sources?: Record<string, number> }[];
}

export const marketSpendQuery = queryOptions({
  queryKey: ['market', 'spend'],
  queryFn: ({ signal }) => api.get<MarketSpend>('/api/v2/market/spend?dimension=offering', { signal }),
  ...quiet,
});
export const marketBriefingQuery = queryOptions({
  queryKey: ['market', 'briefing'],
  queryFn: ({ signal }) => api.get<MarketBriefing>('/api/v2/market/briefings/latest', { signal }),
  ...quiet,
});
export const listeningStatsQuery = queryOptions({
  queryKey: ['listening', 'stats', { days: 14 }],
  queryFn: async ({ signal }) => {
    try {
      const since = new Date(Date.now() - 14 * 86_400_000).toISOString();
      return await api.get<ListeningStats>(`/api/v2/listening/stats?since=${enc(since)}`, { signal });
    } catch {
      return null;
    }
  },
  ...quiet,
});

/** A group with its lead (sent when the caller may read leads; not in the schema). */
export type Team = Group & { lead?: string };
export const groupsQuery = queryOptions({
  queryKey: ['groups'],
  queryFn: ({ signal }) => api.get<Team[]>('/api/v2/groups', { signal }),
  ...quiet,
});

// Brand: the published book (backend/brand.py view).
export interface BrandBody {
  name?: string | null;
  positioning?: string | null;
  voice?: { trait: string; do?: string | null; dont?: string | null }[] | null;
  tone?: { situation: string; guide: string }[] | null;
  words?: { use?: { word: string; not?: string | null }[] | null; avoid?: { word: string; why?: string | null }[] | null } | null;
  naming?: { name: string; rule?: string | null }[] | null;
  visual?: { colors?: { name?: string | null; hex?: string | null }[] | null; fonts?: string[] | null; imagery?: string | null } | null;
  claims?: { allowed?: { claim: string; proof?: string | null }[] | null; never?: string[] | null } | null;
}
export interface Brand {
  published: { n: number; body: BrandBody; published_at?: string | null; published_by?: string | null } | null;
  may_edit?: boolean;
}
export const brandQuery = queryOptions({
  queryKey: ['brand'],
  queryFn: ({ signal }) => api.get<Brand>('/api/v2/brand', { signal }),
  ...quiet,
});

// ---------------------------------------------------------------- Goals and KPIs

export type TreeGoal = GoalTree['goals'][number];

export const goalsTreeQuery = queryOptions({
  queryKey: ['goals', 'tree'],
  queryFn: ({ signal }) => api.get<GoalTree>('/api/v2/goals/tree', { signal }),
  ...quiet,
});

export const goalQuery = (gid: string) =>
  queryOptions({
    queryKey: ['goals', 'detail', gid],
    queryFn: ({ signal }) => api.get<GoalResult>(`/api/v2/goals/${enc(gid)}`, { signal }),
    ...quiet,
  });

export const kpiQuery = (kid: string) =>
  queryOptions({
    queryKey: ['kpis', 'detail', kid],
    queryFn: ({ signal }) => api.get<KpiDetail>(`/api/v2/kpis/${enc(kid)}`, { signal }),
    ...quiet,
  });

const treeKey = goalsTreeQuery.queryKey;
const mapGoals = (tree: GoalTree | undefined, f: (g: TreeGoal) => TreeGoal | null): GoalTree | undefined =>
  tree && { ...tree, goals: tree.goals.map(f).filter((g): g is TreeGoal => g !== null) };
const mapKpis = (tree: GoalTree | undefined, f: (k: Kpi) => Kpi | null): GoalTree | undefined =>
  mapGoals(tree, (g) => ({ ...g, kpis: g.kpis.map(f).filter((k): k is Kpi => k !== null) }));

export function createGoal(input: GoalCreate) {
  const id = tmpId();
  const now = new Date().toISOString();
  const shown: TreeGoal = {
    id,
    title: input.title,
    body: input.body ?? '',
    owner_id: input.owner,
    parent_id: input.parent_id ?? null,
    rank: null,
    status: null,
    status_at: null,
    status_by_id: null,
    status_note: '',
    status_source: null,
    created_at: now,
    created_by_id: '',
    updated_at: now,
    kpis: [],
    open_tasks: 0,
  };
  return write<GoalResult>({
    key: treeKey,
    optimistic: (t: GoalTree | undefined) => t && { ...t, goals: [...t.goals, shown] },
    send: (idempotencyKey) => api.post<GoalResult>('/api/v2/goals', input, { idempotencyKey }),
    settle: (t: GoalTree | undefined, r) => mapGoals(t, (g) => (g.id === id ? { ...g, ...r.goal, kpis: r.goal.kpis } : g)),
    revert: (t: GoalTree | undefined) => mapGoals(t, (g) => (g.id === id ? null : g)),
  });
}

export function updateGoal(gid: string, patch: GoalUpdate) {
  const fields = Object.fromEntries(Object.entries(patch).filter(([, v]) => v != null)) as Partial<Goal>;
  return write<GoalResult>({
    key: [treeKey, goalQuery(gid).queryKey],
    optimistic: (cur: GoalTree | GoalResult | undefined) =>
      cur && ('goals' in cur ? mapGoals(cur, (g) => (g.id === gid ? { ...g, ...fields } : g)) : { goal: { ...cur.goal, ...fields } }),
    send: (idempotencyKey) => api.post<GoalResult>(`/api/v2/goals/${enc(gid)}`, patch, { idempotencyKey }),
  });
}

/** Done, or dropped (Archive): either way it leaves the tree. */
export function closeGoal(gid: string, status: 'done' | 'dropped') {
  return write<GoalResult>({
    key: treeKey,
    optimistic: (t: GoalTree | undefined) => mapGoals(t, (g) => (g.id === gid ? null : g)),
    send: (idempotencyKey) => api.post<GoalResult>(`/api/v2/goals/${enc(gid)}/status`, { status, note: '' }, { idempotencyKey }),
  });
}

export function addReading(kid: string, value: number, by: string | null) {
  const now = new Date().toISOString();
  const shown: KpiReadingView = { id: tmpId(), value, period_end: now, created_at: now, actor_id: by, definition_version: 0, quality: 'measured' };
  return write<KpiReadingResult>({
    key: [kpiQuery(kid).queryKey, treeKey],
    optimistic: (cur: KpiDetail | GoalTree | undefined) =>
      cur &&
      ('readings' in cur
        ? { ...cur, readings: [shown, ...cur.readings], kpi: { ...cur.kpi, latest: shown } }
        : mapKpis(cur, (k) => (k.id === kid ? { ...k, latest: shown, spark: [...k.spark, value] } : k))),
    send: (idempotencyKey) => api.post<KpiReadingResult>(`/api/v2/kpis/${enc(kid)}/readings`, { value }, { idempotencyKey }),
  });
}

export interface KpiFields {
  name: string;
  unit: string;
  direction: 'up' | 'down';
  target: number | null;
  deadline: string | null;
  owner: string | null;
}

export function createKpi(goal: string, f: KpiFields) {
  const id = tmpId();
  const body: KpiCreate = {
    goal_id: goal,
    name: f.name,
    unit: f.unit,
    direction: f.direction,
    kind: f.target == null ? 'none' : 'improve',
    target: f.target,
    deadline: f.deadline,
    owner: f.owner,
  };
  const shown: Kpi = {
    id,
    name: f.name,
    unit: f.unit,
    direction: f.direction,
    cadence: 'weekly',
    auto: false,
    definition_version: 1,
    freshness: 'missing',
    latest: null,
    owner_id: f.owner ?? '',
    readings: 0,
    reason: '',
    spark: [],
    status: 'none',
    link: { goal_id: goal, kind: body.kind ?? 'none', target: f.target, deadline: f.deadline },
  };
  return write<KpiDetail>({
    key: treeKey,
    optimistic: (t: GoalTree | undefined) => mapGoals(t, (g) => (g.id === goal ? { ...g, kpis: [...g.kpis, shown] } : g)),
    send: (idempotencyKey) => api.post<KpiDetail>('/api/v2/kpis', body, { idempotencyKey }),
    settle: (t: GoalTree | undefined, r) => mapKpis(t, (k) => (k.id === id ? r.kpi : k)),
    revert: (t: GoalTree | undefined) => mapKpis(t, (k) => (k.id === id ? null : k)),
  });
}

export function updateKpi(kid: string, goal: string, f: KpiFields) {
  const update: KpiUpdate = { name: f.name, unit: f.unit, direction: f.direction, owner: f.owner };
  const target: KpiTarget = { kind: f.target == null ? 'none' : 'improve', target: f.target, deadline: f.deadline };
  const link = (k: Kpi): Kpi => ({ ...k, name: f.name, unit: f.unit, direction: f.direction, link: { goal_id: goal, ...k.link, kind: target.kind ?? 'none', target: f.target, deadline: f.deadline } });
  return write<unknown>({
    key: [treeKey, kpiQuery(kid).queryKey],
    optimistic: (cur: GoalTree | KpiDetail | undefined) => cur && ('goals' in cur ? mapKpis(cur, (k) => (k.id === kid ? link(k) : k)) : { ...cur, kpi: link(cur.kpi) }),
    send: async (idempotencyKey) => {
      await api.post<KpiDetail>(`/api/v2/kpis/${enc(kid)}`, update, { idempotencyKey });
      return api.post<unknown>(`/api/v2/goals/${enc(goal)}/kpis/${enc(kid)}`, target, { idempotencyKey: `${idempotencyKey}:target` });
    },
  });
}

export function archiveKpi(kid: string) {
  return write<KpiDetail>({
    key: treeKey,
    optimistic: (t: GoalTree | undefined) => mapKpis(t, (k) => (k.id === kid ? null : k)),
    send: (idempotencyKey) => api.post<KpiDetail>(`/api/v2/kpis/${enc(kid)}/archive`, {}, { idempotencyKey }),
  });
}

export type Checkin = GoalCheckinView;

// ---------------------------------------------------------------- Products

export const productsQuery = queryOptions({
  queryKey: ['products', 'list'],
  queryFn: ({ signal }) => api.get<ProductList>('/api/v2/products', { signal }),
  ...quiet,
});

const productsKey = productsQuery.queryKey;
const mapProducts = (l: ProductList | undefined, f: (p: Product) => Product | null): ProductList | undefined =>
  l && { ...l, products: l.products.map(f).filter((p): p is Product => p !== null) };

/** One fact of a product, saved at once ("" clears it). */
export function updateProduct(key: string, patch: ProductUpdate) {
  return write<ProductResult>({
    key: productsKey,
    optimistic: (l: ProductList | undefined) => mapProducts(l, (p) => (p.key === key ? ({ ...p, ...patch } as Product) : p)),
    send: (idempotencyKey) => api.post<ProductResult>(`/api/v2/products/${enc(key)}`, patch, { idempotencyKey }),
    settle: (l: ProductList | undefined, r) => mapProducts(l, (p) => (p.key === key ? r.product : p)),
  });
}

export function createProduct(name: string) {
  const id = tmpId();
  const shown: Product = { id, key: id, name, kind: null, status: 'planned', tax_kind: null, owner_teammate: null, agreements: [], depends_on: [], repositories: [], risks: [], open_matters: 0 };
  return write<ProductResult>({
    key: productsKey,
    optimistic: (l: ProductList | undefined) => l && { ...l, products: [...l.products, shown] },
    send: (idempotencyKey) => api.post<ProductResult>('/api/v2/products', { name, status: 'planned' }, { idempotencyKey }),
    settle: (l: ProductList | undefined, r) => mapProducts(l, (p) => (p.id === id ? r.product : p)),
    revert: (l: ProductList | undefined) => mapProducts(l, (p) => (p.id === id ? null : p)),
  });
}

// ---------------------------------------------------------------- Risk

export const risksQuery = queryOptions({
  queryKey: ['risks', 'list'],
  queryFn: ({ signal }) => api.get<RiskList>('/api/v2/risks', { signal }),
  ...quiet,
});

export const riskQuery = (id: string) =>
  queryOptions({
    queryKey: ['risks', 'detail', id],
    queryFn: ({ signal }) => api.get<RiskDetail>(`/api/v2/risks/${enc(id)}`, { signal }),
    ...quiet,
  });

/** Open matters (under live risks), or every dispute (the Disputes tab). */
export const mattersQuery = (which: 'open' | 'disputes') =>
  queryOptions({
    queryKey: ['risks', 'matters', which],
    queryFn: ({ signal }) => api.get<MatterList>(`/api/v2/matters?${which === 'open' ? 'open=true' : 'kind=dispute'}&limit=200`, { signal }),
    ...quiet,
  });

export type { MatterRow };

export function updateRisk(id: string, patch: Omit<RiskUpdate, 'version'>, version: number) {
  return write<RiskResult>({
    key: [risksQuery.queryKey, riskQuery(id).queryKey],
    optimistic: (cur: RiskList | RiskDetail | undefined) =>
      cur && ('risks' in cur ? { ...cur, risks: cur.risks.map((r) => (r.id === id ? { ...r, ...patch } : r)) } : { ...cur, risk: { ...cur.risk, ...patch } }),
    send: (idempotencyKey) => api.post<RiskResult>(`/api/v2/risks/${enc(id)}`, { ...patch, version }, { idempotencyKey }),
  });
}

export function createRisk(label: string) {
  return write<RiskResult>({
    key: risksQuery.queryKey,
    optimistic: (l: RiskList | undefined) => l,
    send: (idempotencyKey) => api.post<RiskResult>('/api/v2/risks', { label }, { idempotencyKey }),
    settle: (l: RiskList | undefined, r) => l && { ...l, risks: [...l.risks, r.risk] },
  });
}
