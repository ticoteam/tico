import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  CompanyFloor,
  FactoryCreated,
  FactoryMap,
  FinanceMetric,
  Goal,
  GoalCheckinView,
  GoalCreate,
  GoalProposalView,
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
  LibrarianMarketSetup,
  LibrarianSetup,
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
import { keys, QUIET_STALE_MS, queryClient, quietRoot, write } from '@/data';
import type {
  FileSearch,
  GetGoalsNeedsYouResponse,
  LibrarianResearchFiled,
  Matter,
  MatterResult,
  MatterWrite,
  NeedsYou,
  RepositoryList,
  RiskObligation,
  TaskFileList,
  TaskFileResult,
  TaskFileView,
  TaskResult,
} from '@/api/schema';
import { newId } from '@/lib';

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

/** One row of the history: every save that changed something is a version (GET /api/v2/strategy/versions). */
export interface VersionRow {
  n: number;
  note?: string | null;
  published_at?: string | null;
  published_by?: string | null;
}
export const strategyVersionsQuery = queryOptions({
  queryKey: ['strategy', 'versions'],
  queryFn: ({ signal }) => api.get<VersionRow[]>('/api/v2/strategy/versions?limit=50', { signal }),
  ...quiet,
});
/** One version whole, with its bets as they were. A published version never changes. */
export type StrategySnapshot = StrategyVersion & { bets: Bet[] };
export const strategyVersionQuery = (n: number) =>
  queryOptions({
    queryKey: ['strategy', 'version', n],
    queryFn: ({ signal }) => api.get<StrategySnapshot>(`/api/v2/strategy/versions/${n}`, { signal }),
    staleTime: Infinity,
  });
/** What changed between two versions (backend/strategy.py diff): field keys are `summary`, `sections.<key>`, `sections.extra.<key>`. */
export interface StrategyDiff {
  fields: { key: string; from: unknown; to: unknown }[];
  bets: { added: Bet[]; retired: Bet[]; changed: { id: string; from: Bet; to: Bet }[] };
}
export const strategyDiffQuery = (from: number, to: number) =>
  queryOptions({
    queryKey: ['strategy', 'diff', from, to],
    queryFn: ({ signal }) => api.get<StrategyDiff>(`/api/v2/strategy/diff?from=${from}&to=${to}`, { signal }),
    staleTime: Infinity,
  });

// Market: the latest briefing, the market's spend and players, its value chain, and Listening's posts a day. None of
// the market reads is in the schema; these are their shapes as backend/market_spend.py, market.py and
// market_briefing.py send them (the fields the pages read).
export interface MarketItem {
  text: string;
  kind?: string;
  entity_ids?: string[];
}
export interface MarketFigure {
  metric?: string;
  value?: number | string | null;
  text?: string | null;
  kind?: string | null;
  as_of?: string | null;
  source_url?: string | null;
}
export interface MarketPlayer {
  entity_id?: string;
  name: string;
  figures?: MarketFigure[] | null;
}
interface MarketRange {
  spend_usd?: number | null;
  low_usd?: number | null;
  high_usd?: number | null;
}
/** A shared parameter of an estimate: a bare value, or the value with whether it is a judgement and its source. */
export type MarketParam = number | string | null | { value?: unknown; judgement?: boolean; source?: string | null };
export interface MarketEstimate extends MarketRange {
  id: string;
  method: string;
  status?: string | null;
  error?: string | null;
  arithmetic?: string | null;
  formula?: string | null;
  params?: Record<string, MarketParam> | null;
  evidence?: { source_url?: string | null; source_kind?: string | null; quote?: string | null }[] | null;
  overlaps?: string | null;
  spread?: string | null;
}
export interface MarketSegment {
  id: string;
  name: string;
  parent?: string | null;
  depth?: number;
  summary?: string | null;
  size?: (MarketRange & { id?: string; growth_pct?: number | null; confidence?: 'high' | 'medium' | 'low' | null; accepted?: boolean; error?: string | null; overlaps?: string | null; spread?: string | null }) | null;
  rolled_usd?: { spend_usd?: number | null } | null;
  estimates?: MarketEstimate[] | null;
  disagree?: boolean;
  players?: MarketPlayer[] | null;
  self?: boolean;
}
export interface MarketDefinition {
  description?: string | null;
  year?: string | number | null;
  geography?: string | null;
  currency?: string | null;
  includes?: string[] | null;
  excludes?: string[] | null;
  counting_rules?: string[] | null;
  params?: Record<string, MarketParam> | null;
}
/** The open "Size the market" task: whose turn it is, and the reader's question when it waits on them. */
export interface MarketSizing {
  id: string;
  turn?: string | null;
  ask?: { line?: string | null } | null;
}
export interface MarketSpend {
  name?: string | null;
  definition?: MarketDefinition | null;
  year?: string | number | null;
  total?: MarketRange | null;
  segments?: MarketSegment[] | null;
  sizing?: MarketSizing | null;
}
export interface MarketBriefing {
  briefing?: { week?: string; title: string; body?: string; created_at?: string; items?: MarketItem[]; touched?: { id: string; n?: number; statement: string }[] } | null;
}
/** GET /api/v2/market/value-chain: who supplies, sells and buys around the company, layer by layer. */
export interface ValueChain {
  layers?: { key: string; title: string; nodes?: { id: string; name: string; type?: string; self?: boolean }[] | null }[] | null;
}
/** GET /api/v2/market/entities/{id}?names=true: one organization's note, its relations named. */
export interface MarketNote {
  entity: { id: string; name: string; type?: string | null; tier?: string | null; summary?: string | null; aliases?: string[] | null };
  edges?: Record<string, { out?: { src: string; dst: string }[]; in?: { src: string; dst: string }[] }>;
  evidence?: { our_read?: string | null; quote?: string | null }[];
  neighbors?: { id: string; name: string }[];
}
/** GET /api/v2/listening/stats (backend/listening.py stats; the schema leaves it untyped): posts sent on each day by
    site, and the newest posts sent on, each one's text clipped. */
export interface ListeningStats {
  daily?: { day: string; sources?: Record<string, number> }[];
  recent?: { item_id: string; source: string; text: string; url?: string | null; at?: string | null }[];
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
export const valueChainQuery = queryOptions({
  queryKey: ['market', 'chain'],
  queryFn: ({ signal }) => api.get<ValueChain>('/api/v2/market/value-chain', { signal }),
  ...quiet,
});
/** How many organizations the market holds: none means it was never set up. */
export const marketCountQuery = queryOptions({
  queryKey: ['market', 'count'],
  queryFn: async ({ signal }) => (await api.get<{ total?: number | null; entities?: unknown[] }>('/api/v2/market/entities?status=active&limit=1', { signal })).total ?? 0,
  ...quiet,
});
export const marketNoteQuery = (id: string) =>
  queryOptions({
    queryKey: ['market', 'note', id],
    queryFn: ({ signal }) => api.get<MarketNote>(`/api/v2/market/entities/${enc(id)}?names=true`, { signal }),
    ...quiet,
  });
/** The owner's answers to Market's four set-up questions so far, and the research task they filed. */
export const marketSetupQuery = queryOptions({
  queryKey: ['market', 'setup'],
  queryFn: async ({ signal }) => (await api.get<LibrarianSetup>('/api/v2/librarian/setup', { signal })).market,
  ...quiet,
});
export type MarketSetupState = LibrarianMarketSetup;
export type MarketAnswer = keyof LibrarianMarketSetup['answers'];

/** Keep the answers (`save`, `skip`), or `finish`: file the research to the Librarian. Owner only. */
export function setUpMarket(answers: LibrarianMarketSetup['answers'], step: number, action: 'save' | 'skip' | 'finish') {
  return write<LibrarianMarketSetup>({
    key: marketSetupQuery.queryKey,
    optimistic: (m: LibrarianMarketSetup | undefined) => m && { ...m, answers, step, skipped: action === 'skip' },
    send: (idempotencyKey) => api.post<LibrarianMarketSetup>('/api/v2/librarian/setup/market', { answers, step, action }, { idempotencyKey }),
    settle: (_m: LibrarianMarketSetup | undefined, r) => r,
  });
}

/** The owner asks the Librarian to define and size the market from a sentence. */
export function sizeMarket(description: string) {
  return write<{ task_id?: string; sizing?: MarketSizing | null }>({
    key: marketSpendQuery.queryKey,
    optimistic: (m: MarketSpend | undefined) => m && { ...m, definition: { ...m.definition, description } },
    send: (idempotencyKey) => api.post<{ task_id?: string; sizing?: MarketSizing | null }>('/api/v2/market/spend/research', { description }, { idempotencyKey }),
    settle: (m: MarketSpend | undefined, r) => m && { ...m, sizing: r.sizing ?? m.sizing },
  });
}

const mapEstimates = (m: MarketSpend | undefined, f: (e: MarketEstimate, s: MarketSegment) => MarketEstimate) =>
  m && { ...m, segments: m.segments?.map((s) => ({ ...s, estimates: s.estimates?.map((e) => f(e, s)) })) };

/** An estimate becomes its segment's size. */
export function acceptSize(size: string) {
  return write<unknown>({
    key: marketSpendQuery.queryKey,
    optimistic: (m: MarketSpend | undefined) => mapEstimates(m, (e) => (e.id === size ? { ...e, status: 'accepted' } : e)),
    send: (idempotencyKey) => api.post<unknown>(`/api/v2/market/sizes/${enc(size)}/accept`, {}, { idempotencyKey }),
  });
}

/** One parameter of an estimate, changed by the owner: the server works the estimate out again. */
export function setSizeParam(size: string, name: string, value: number) {
  const put = (p: MarketParam | undefined): MarketParam => (p && typeof p === 'object' ? { ...p, value } : value);
  return write<unknown>({
    key: marketSpendQuery.queryKey,
    optimistic: (m: MarketSpend | undefined) => mapEstimates(m, (e) => (e.id === size ? { ...e, params: { ...e.params, [name]: put(e.params?.[name]) } } : e)),
    send: (idempotencyKey) => api.post<unknown>(`/api/v2/market/sizes/${enc(size)}/params`, { params: { [name]: value } }, { idempotencyKey }),
  });
}

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

// ---------------------------------------------------------------- Org: the factories and who may make one

/** The live factories and the live pipelines with the factory each sits in; null when the read fails (the section is
    then left out, as the old Org page did). */
export const factoriesQuery = queryOptions({
  queryKey: ['company', 'factories'],
  queryFn: async ({ signal }) => {
    try {
      return await api.get<FactoryMap>('/api/v2/factories?period=7d', { signal });
    } catch {
      return null;
    }
  },
  ...quiet,
});

/** The reader's company role as Access gives it ('owner', 'admin'); null for a member, whom Access refuses. */
export const companyRoleQuery = queryOptions({
  queryKey: ['company', 'role'],
  queryFn: async ({ signal }) => {
    try {
      return (await api.get<{ you?: string }>('/api/v2/access', { signal })).you ?? null;
    } catch {
      return null;
    }
  },
  ...quiet,
});

/** A new factory, shown at once at the end of the list. Only the company's owner and Admins (403 for anyone else; 409
    when a live factory has the name). */
export function createFactory(name: string, goal: string) {
  const id = tmpId();
  return write<FactoryCreated>({
    key: factoriesQuery.queryKey,
    optimistic: (m: FactoryMap | null | undefined) => m && { ...m, factories: [...m.factories, { id, name, goal, manager_id: null, done: 0 }] },
    send: (idempotencyKey) => api.post<FactoryCreated>('/api/v2/factories', goal ? { name, goal } : { name }, { idempotencyKey }),
    settle: (m: FactoryMap | null | undefined, r) => m && { ...m, factories: m.factories.map((f) => (f.id === id ? { ...f, id: r.factory.id } : f)) },
    revert: (m: FactoryMap | null | undefined) => m && { ...m, factories: m.factories.filter((f) => f.id !== id) },
  });
}

/** A group with its lead (sent when the caller may read leads; not in the schema). */
export type Team = Group & { lead?: string };
export const groupsQuery = queryOptions({
  queryKey: ['groups'],
  queryFn: ({ signal }) => api.get<Team[]>('/api/v2/groups', { signal }),
  ...quiet,
});

/** A teammate into another group (it leaves the one it was in), or out of its group when `to` is empty. People go by
    id, agents by slug (backend/groups.py update). */
export function moveTeammate(who: string, from: string, to: string) {
  const agent = who.startsWith('bot:');
  const bare = who.slice(who.indexOf(':') + 1);
  const kind = agent ? 'bots' : 'people';
  const strip = (g: Team): Team => (agent ? { ...g, bots: g.bots.filter((b) => b !== bare), bot_slugs: g.bot_slugs.filter((b) => b !== bare) } : { ...g, people: g.people.filter((p) => p !== bare && p !== who) });
  const add = (g: Team): Team => (agent ? { ...g, bots: [...g.bots, bare], bot_slugs: [...g.bot_slugs, bare] } : { ...g, people: [...g.people, bare] });
  return write<Team>({
    key: groupsQuery.queryKey,
    optimistic: (gs: Team[] | undefined) => gs?.map((g) => (g.id === to ? add(strip(g)) : strip(g))),
    send: (idempotencyKey) =>
      to ? api.patch<Team>(`/api/v2/groups/${enc(to)}`, { add: { [kind]: [bare] } }, { idempotencyKey }) : api.patch<Team>(`/api/v2/groups/${enc(from)}`, { remove: { [kind]: [bare] } }, { idempotencyKey }),
  });
}

// Brand: the published book, its versions, and for an owner or admin the draft (backend/brand.py view).
export interface BrandBody {
  name?: string | null;
  positioning?: string | null;
  voice?: { trait: string; do?: string | null; dont?: string | null }[] | null;
  tone?: { situation: string; guide: string }[] | null;
  words?: { use?: { word: string; not?: string | null }[] | null; avoid?: { word: string; why?: string | null; level?: string }[] | null } | null;
  naming?: { name: string; rule?: string | null; wrong?: string[] }[] | null;
  visual?: { colors?: { name?: string | null; hex?: string | null }[] | null; fonts?: string[] | null; logo_files?: string[]; imagery?: string | null } | null;
  claims?: { allowed?: { claim: string; proof?: string | null; check?: string; status?: string }[] | null; never?: string[] | null } | null;
  examples?: { text: string; verdict: 'on' | 'off'; why?: string }[] | null;
}
export interface BrandVersion {
  n: number;
  body: BrandBody;
  note?: string | null;
  published_at?: string | null;
  published_by?: string | null;
}
export interface Brand {
  published: BrandVersion | null;
  draft?: { body: BrandBody; revision: number; changed: string[] } | null;
  versions?: VersionRow[];
  may_edit?: boolean;
}
export const brandQuery = queryOptions({
  queryKey: ['brand'],
  queryFn: ({ signal }) => api.get<Brand>('/api/v2/brand', { signal }),
  ...quiet,
});
export const brandVersionQuery = (n: number) =>
  queryOptions({
    queryKey: ['brand', 'version', n],
    queryFn: ({ signal }) => api.get<BrandVersion>(`/api/v2/brand/versions/${n}`, { signal }),
    staleTime: Infinity,
  });

/** Sections onto the brand draft (null takes one out). `revision` is the draft's as read; a draft changed meanwhile
    is refused (409) and read again. */
export function saveBrandDraft(sections: Partial<Record<keyof BrandBody, unknown>>, revision?: number) {
  return write<Brand>({
    key: brandQuery.queryKey,
    optimistic: (b: Brand | undefined) => b && { ...b, draft: { revision: revision ?? 0, changed: [], ...b.draft, body: { ...(b.draft?.body ?? b.published?.body), ...sections } as BrandBody } },
    send: (idempotencyKey) => api.put<Brand>('/api/v2/brand/draft', { sections, ...(revision != null && { revision }) }, { idempotencyKey }),
    settle: (_b: Brand | undefined, r) => r,
  });
}

/** The draft becomes the next version. */
export function publishBrand(note: string) {
  return write<Brand>({
    key: brandQuery.queryKey,
    optimistic: (b: Brand | undefined) => b,
    send: (idempotencyKey) => api.post<Brand>('/api/v2/brand/publish', { note }, { idempotencyKey }),
    settle: (_b: Brand | undefined, r) => r,
  });
}

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

/** A proposal on a goal or a KPI, as the tree sends it: what its card says, and whether this reader decides it. */
export type Proposal = GoalProposalView;
export interface ProposalCard {
  target_label?: string;
  now_label?: string;
  measure?: { name: string };
  changes?: { label: string; now: string; proposed: string }[];
}

/** The owner's yes or no on a proposal: the card goes at once and comes back if refused. A yes changes the goal or
    its KPIs, so both are read again. */
export function decideProposal(id: string, decision: 'confirm' | 'reject') {
  return write<{ proposal: Proposal }>({
    key: treeKey,
    optimistic: (t: GoalTree | undefined) => t && { ...t, proposals: t.proposals.filter((p) => p.id !== id) },
    send: async (idempotencyKey) => {
      const out = await api.post<{ proposal: Proposal }>(`/api/v2/proposals/${enc(id)}/decide`, { decision }, { idempotencyKey });
      void queryClient.invalidateQueries({ queryKey: ['kpis'] });
      void queryClient.invalidateQueries({ queryKey: ['goals', 'detail'] });
      return out;
    },
  });
}
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

/** A KPI's place in the KPI tree (GET /api/v2/growth/kpis; not in the schema): what it feeds and its role. */
export type GrowthRole = NonNullable<KpiUpdate['growth_role']>;
export interface PlacedKpi {
  id: string;
  name: string;
  parent_kpi_id: string | null;
  growth_role: GrowthRole | null;
  may_edit?: boolean;
}
export const placedKpisQuery = queryOptions({
  queryKey: ['kpis', 'placed'],
  queryFn: async ({ signal }) => (await api.get<{ kpis?: PlacedKpi[] }>('/api/v2/growth/kpis', { signal })).kpis ?? [],
  ...quiet,
});

/** What a KPI feeds and its role; one North Star at a time, so naming one moves the role from the old. */
export function placeKpi(kid: string, feeds: string | null, role: GrowthRole | null) {
  const body: KpiUpdate = { parent_kpi_id: feeds, growth_role: role };
  return write<unknown>({
    key: placedKpisQuery.queryKey,
    optimistic: (ks: PlacedKpi[] | undefined) =>
      ks?.map((k) => (k.id === kid ? { ...k, parent_kpi_id: feeds, growth_role: role } : role === 'north_star' && k.growth_role === 'north_star' ? { ...k, growth_role: null } : k)),
    send: (idempotencyKey) => api.post<unknown>(`/api/v2/kpis/${enc(kid)}`, body, { idempotencyKey }),
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

// ---------------------------------------------------------------- tier 2 gaps: what waits on you in Goals, records' lists

/** One item of GET /api/v2/goals/needs-you (backend/goal_routes.py goals_needs_you, goal_asks.py for_person). */
export type GoalNeed = GetGoalsNeedsYouResponse['items'][number];
/** A recovery's option: what it would do, the Strategy Owner's pick marked. */
export interface RecoveryOption {
  label: string;
  pick?: boolean;
  actions?: { text: string; note?: string | null }[];
}

export const goalNeedsQuery = queryOptions({
  queryKey: ['goals', 'needs'],
  queryFn: async ({ signal }) => (await api.get<GetGoalsNeedsYouResponse>('/api/v2/goals/needs-you', { signal })).items,
  ...quiet,
});

/** Yes or no on a suggestion that waits on you (a recovery's chosen `option`): it leaves the list at once and comes
    back if refused. A yes may change goals, KPIs, the strategy or the brand draft, so those are read again. */
export function decideNeed(proposal: string, decision: 'confirm' | 'reject', option?: number) {
  return write<{ proposal: Proposal }>({
    key: goalNeedsQuery.queryKey,
    optimistic: (items: GoalNeed[] | undefined) => items?.filter((i) => (i.proposal as { id?: string } | undefined)?.id !== proposal),
    send: async (idempotencyKey) => {
      const out = await api.post<{ proposal: Proposal }>(`/api/v2/proposals/${enc(proposal)}/decide`, { decision, ...(option != null && { option }) }, { idempotencyKey });
      for (const root of ['strategy', 'brand', 'kpis']) void queryClient.invalidateQueries({ queryKey: [root] });
      void queryClient.invalidateQueries({ queryKey: treeKey });
      return out;
    },
  });
}

/** The tasks that wait on the person (GET /api/v2/needs-you): a matter among them gets the waits-on-you mark. */
export const waitingTasksQuery = queryOptions({
  queryKey: ['needs', 'tasks'],
  queryFn: async ({ signal }) => {
    try {
      const r = await api.get<NeedsYou>('/api/v2/needs-you?limit=200', { signal });
      return 'items' in r ? r.items.map((i) => i.task_id ?? i.task?.id).filter((x): x is string => !!x) : [];
    } catch {
      return [];
    }
  },
  ...quiet,
});

/** A risk's name and page text (the old Edit dialog). */
export function editRisk(id: string, label: string, markdown: string, version: number) {
  return updateRisk(id, { label, markdown }, version);
}

/** A new matter under a risk: a task carrying the risk's tag, then its kind (backend/risk.py: a matter is a task tagged
    with a risk). The risk's page is read again when the server has both. */
export function addMatter(risk: { id: string; key: string }, title: string, kind: Matter['kind']) {
  return write<TaskResult>({
    key: riskQuery(risk.id).queryKey,
    optimistic: (d: RiskDetail | undefined) => d,
    send: async (idempotencyKey) => {
      const made = await api.post<TaskResult>('/api/v2/tasks', { title, labels: [risk.key] }, { idempotencyKey });
      await api.post<unknown>(`/api/v2/tasks/${enc(made.task.id)}/matter`, { kind }, { idempotencyKey: `${idempotencyKey}:matter` });
      return made;
    },
  });
}

export type RiskDate = RiskObligation;
/** How a date is marked: met with proof (a link or a file), waived (with why) or missed. */
export type DateMark = { proof: { kind: 'link' | 'file'; ref: string } } | { outcome: 'waived' | 'missed'; note?: string };

/** One occurrence of a date marked (POST /api/v2/rule-due/{id}/satisfied), shown at once on the risk's page. */
export function markDate(risk: string, due: string, on: string, mark: DateMark) {
  const state = 'proof' in mark ? 'met' : mark.outcome;
  return write<unknown>({
    key: riskQuery(risk).queryKey,
    optimistic: (d: RiskDetail | undefined) =>
      d && { ...d, obligations: d.obligations.map((o) => (o.id === due && o.due_on === on ? { ...o, state, mark: o.mark ?? ({ outcome: state, proof: null, note: '', satisfied_at: new Date().toISOString(), satisfied_by: '' } as unknown as RiskDate['mark']) } : o)) },
    send: (idempotencyKey) => api.post<unknown>(`/api/v2/rule-due/${enc(due)}/satisfied`, { due_on: on, ...mark }, { idempotencyKey }),
  });
}

/** Files the person may read, by title: the proof for a date. */
export const fileSearchQuery = (text: string) =>
  queryOptions({
    queryKey: ['risks', 'file-search', text],
    queryFn: async ({ signal }) => (await api.get<FileSearch>(`/api/v2/files/search?q=${enc(text)}&limit=6`, { signal })).results.map((r) => ({ id: r.id, title: r.title })),
    enabled: !!text.trim(),
    staleTime: 30_000,
  });

/** One matter's dates: its risk's dates that belong to its task (GET /api/v2/risk/obligations). */
export const matterDatesQuery = (risk: string) =>
  queryOptions({
    queryKey: ['risks', 'dates', risk],
    queryFn: async ({ signal }) => (await api.get<{ obligations: RiskDate[] }>(`/api/v2/risk/obligations?risk=${enc(risk)}`, { signal })).obligations,
    ...quiet,
  });

/** The files on a matter's task. */
export const matterFilesQuery = (task: string) =>
  queryOptions({
    queryKey: ['risks', 'files', task],
    queryFn: async ({ signal }) => (await api.get<TaskFileList>(`/api/v2/tasks/${enc(task)}/files`, { signal })).files.filter((f) => !f.archived),
    ...quiet,
  });

/** A file from the person's disk onto the matter's task (POST /api/v2/tasks/{tid}/files). */
export function addMatterFile(task: string, file: File) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  return write<TaskFileResult>({
    key: matterFilesQuery(task).queryKey,
    optimistic: (fs: TaskFileView[] | undefined) => fs,
    send: (idempotencyKey) => api.post<TaskFileResult>(`/api/v2/tasks/${enc(task)}/files`, fd, { idempotencyKey }),
  });
}

/** A product's matters (GET /api/v2/matters?product=<key>). */
export const productMattersQuery = (key: string) =>
  queryOptions({
    queryKey: ['risks', 'matters', 'product', key],
    queryFn: async ({ signal }) => (await api.get<MatterList>(`/api/v2/matters?product=${enc(key)}&limit=200`, { signal })).matters,
    ...quiet,
  });

/** The team's repositories, for a product's Code (null when they cannot be read: a name is typed instead). */
export const repositoriesQuery = queryOptions({
  queryKey: ['products', 'repositories'],
  queryFn: async ({ signal }) => {
    try {
      return (await api.get<RepositoryList>('/api/v2/repositories', { signal })).repositories.map((r) => r.full_name);
    } catch {
      return null;
    }
  },
  ...quiet,
});

export type ProductListField = 'agreements' | 'repositories' | 'depends_on';
/** The values a list holds as the server takes them: a repository's name, an agreement's or a vendor's id. */
export const listValues = (p: Product, field: ProductListField): string[] => (field === 'repositories' ? [...p.repositories] : p[field].map((x) => x.id));

/** A product's Agreement, Code or Depends on, the whole set sent (one added by name, made if new; one removed). */
export function setProductList(key: string, field: ProductListField, values: string[]) {
  const shown = (p: Product): Product => {
    if (field === 'repositories') return { ...p, repositories: values };
    const have = p[field];
    return { ...p, [field]: values.map((v) => have.find((x) => x.id === v) ?? { id: v, name: v, link_id: tmpId() }) };
  };
  return write<ProductResult>({
    key: productsKey,
    optimistic: (l: ProductList | undefined) => mapProducts(l, (p) => (p.key === key ? shown(p) : p)),
    send: (idempotencyKey) => api.post<ProductResult>(`/api/v2/products/${enc(key)}`, { [field]: values }, { idempotencyKey }),
    settle: (l: ProductList | undefined, r) => mapProducts(l, (p) => (p.key === key ? r.product : p)),
  });
}

// Brand: the suggested changes to the book that wait on an editor, and checking copy against the published book.
export interface BrandProposal {
  id: string;
  kind: string;
  reason?: string;
  proposed_by: string;
  proposed_at: string;
  may_decide?: boolean;
  payload?: { sections?: Record<string, unknown> };
  card?: { title?: string };
}
export const brandProposalsQuery = queryOptions({
  queryKey: ['brand', 'proposals'],
  queryFn: async ({ signal }) => (await api.get<{ proposals?: BrandProposal[] }>('/api/v2/proposals', { signal })).proposals?.filter((p) => p.kind === 'brand_draft' && p.may_decide !== false) ?? [],
  ...quiet,
});

/** Add a suggested change to the draft, or decline it; the draft is read again. */
export function decideBrandProposal(id: string, decision: 'confirm' | 'reject') {
  return write<unknown>({
    key: brandProposalsQuery.queryKey,
    optimistic: (ps: BrandProposal[] | undefined) => ps?.filter((p) => p.id !== id),
    send: async (idempotencyKey) => {
      const out = await api.post<unknown>(`/api/v2/proposals/${enc(id)}/decide`, { decision }, { idempotencyKey });
      void queryClient.invalidateQueries({ queryKey: brandQuery.queryKey, exact: true });
      return out;
    },
  });
}

/** Every rule of the published book some copy breaks (POST /api/v2/brand/check). */
export interface BrandHit {
  kind: string;
  match: string;
  rule?: string;
  level?: 'warn' | 'block';
}
export const checkCopy = (text: string) => api.post<{ hits?: BrandHit[] }>('/api/v2/brand/check', { text }).then((r) => r.hits ?? []);

// Market: the owner asks the Librarian to research a segment, or cites it in a bet.
export type CitableBet = Bet & { cites?: string[] };
export type CitableStrategy = Omit<Strategy, 'bets'> & { bets: CitableBet[]; draft?: { revision?: number | null } | null };

export const researchSegment = (target: string, instructions: string) =>
  api.post<LibrarianResearchFiled>('/api/v2/librarian/research', { target, section: '', instructions }, { idempotencyKey: newId() });

/** A segment into a bet's cites, shown at once. */
export function citeInBet(bet: string, segment: string) {
  const s = queryClient.getQueryData<CitableStrategy>(strategyQuery.queryKey);
  const cites = [...(s?.bets.find((b) => b.id === bet)?.cites ?? []), segment];
  const revision = s?.draft?.revision;
  return write<unknown>({
    key: strategyQuery.queryKey,
    optimistic: (cur: CitableStrategy | undefined) => cur && { ...cur, bets: cur.bets.map((b) => (b.id === bet ? { ...b, cites } : b)) },
    send: (idempotencyKey) => api.post<unknown>(`/api/v2/strategy/bets/${enc(bet)}`, { cites, ...(revision != null && { revision }) }, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- a matter's own facts, edited in place

/** The facts a change sets (null clears one), as POST /api/v2/tasks/{tid}/matter takes them. */
export type MatterFacts = Pick<MatterWrite, 'kind' | 'side' | 'party_entity_id' | 'amount' | 'currency' | 'claim' | 'forum' | 'product_id'>;

/** Contacts a matter's party may be: people, then organizations, by name (GET /api/v2/entities/find/{type}). */
export const partySearchQuery = (text: string) =>
  queryOptions({
    queryKey: ['risks', 'party-search', text],
    queryFn: async ({ signal }) => {
      const one = (type: string) =>
        api
          .get<{ rows?: { id: string; name: string }[] }>(`/api/v2/entities/find/${type}?name=${enc(text)}&limit=5`, { signal })
          .then((r) => r.rows ?? [])
          .catch(() => []);
      return [...(await one('person')), ...(await one('organization'))];
    },
    enabled: !!text.trim(),
    staleTime: 30_000,
  });

/** One matter's facts, shown at once wherever the matter is listed (the open list, Disputes, its risk's page, a
    product's matters) and taken back if refused. `names` carries the party's or product's name for the first paint. */
export function setMatter(task: string, facts: MatterFacts, version: number | undefined, names: { party?: string; product?: string } = {}) {
  const patch = (m: Matter | null): Matter | null => {
    if (!m) return m;
    const next = { ...m, ...facts } as Matter;
    if ('party_entity_id' in facts) next.party = facts.party_entity_id ? { id: facts.party_entity_id, name: names.party ?? m.party?.name ?? '' } : null;
    if ('product_id' in facts) next.product = facts.product_id ? { id: facts.product_id, name: names.product ?? m.product?.name ?? '' } : null;
    return next;
  };
  const row = (r: MatterRow, m: (x: Matter | null) => Matter | null): MatterRow => (r.task.id === task ? { ...r, matter: m(r.matter) } : r);
  const each = (cur: unknown, m: (x: Matter | null) => Matter | null): unknown => {
    if (Array.isArray(cur)) return (cur as MatterRow[]).map((r) => (r && typeof r === 'object' && 'task' in r ? row(r, m) : r));
    if (cur && typeof cur === 'object' && Array.isArray((cur as { matters?: unknown }).matters)) return { ...cur, matters: (cur as { matters: MatterRow[] }).matters.map((r) => row(r, m)) };
    return cur;
  };
  const keys = queryClient
    .getQueryCache()
    .findAll({ queryKey: ['risks'] })
    .map((q) => q.queryKey)
    .filter((k) => k[1] === 'matters' || k[1] === 'detail');
  return write<MatterResult>({
    key: keys.length ? keys : [['risks', 'matters', 'open']],
    optimistic: (cur: unknown) => each(cur, patch),
    send: (idempotencyKey) => api.post<MatterResult>(`/api/v2/tasks/${enc(task)}/matter`, { ...facts, ...(version != null && { version }) }, { idempotencyKey }),
    settle: (cur: unknown, r) => (r.matter ? each(cur, () => r.matter) : cur),
  });
}
