import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  ChangelogList,
  CloudSupportView,
  Health,
  ModelSubscription,
  ModelSubscriptionList,
  ScreeningModes,
  Usage,
  UsageFigures,
  UsageLimits,
} from '@/api/schema';
import { QUIET_STALE_MS, quietRoot, write } from '@/data';

/* Settings and Usage have no live topic: both are read again after the person's own writes, on reconnect, and when a
   section opens with data older than a minute. The one timer is a model sign-in under way (signInQuery). */
quietRoot('settings');
quietRoot('usage');

const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS };

// ---------------------------------------------------------------- people and access (GET /api/v2/access)

export interface AccessPerson {
  id: string;
  name: string;
  email: string;
  title?: string;
  team?: string;
  left: boolean;
  owner: boolean;
  role: string;
  sign_in: boolean;
  removable?: boolean;
}

export interface AccessView {
  owner: { email: string; person: string };
  people: AccessPerson[];
  allowed: string[];
  allowed_domains: string[];
  revision: number;
  rules: Record<string, unknown>;
  you: string;
  invites: Record<string, string>;
  invite_email: boolean;
}

export const accessQuery = queryOptions({
  queryKey: ['settings', 'access'],
  queryFn: ({ signal }) => api.get<AccessView>('/api/v2/access', { signal }),
  ...quiet,
});

const patchPerson = (pid: string, change: Partial<AccessPerson>) => (v: AccessView | undefined) =>
  v && { ...v, people: v.people.map((p) => (p.id === pid ? { ...p, ...change } : p)) };

export const setRole = (pid: string, role: 'admin' | 'member') =>
  write({
    key: accessQuery.queryKey,
    optimistic: patchPerson(pid, { role }),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${enc(pid)}`, { role }, { idempotencyKey }),
  });

export const renamePerson = (pid: string, name: string) =>
  write({
    key: accessQuery.queryKey,
    optimistic: patchPerson(pid, { name }),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${enc(pid)}`, { name }, { idempotencyKey }),
  });

export const removePerson = (pid: string) =>
  write({
    key: accessQuery.queryKey,
    optimistic: patchPerson(pid, { left: true }),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${enc(pid)}`, { left: true }, { idempotencyKey }),
  });

/** Add a person, then send the invite when this server can mail one (the old app's two calls). */
export async function addPerson(email: string, name: string): Promise<{ person: string }> {
  const added = await api.post<{ person: string }>('/api/v2/access/humans', { email, name });
  try {
    await api.post(`/api/v2/access/humans/${enc(added.person)}/invite`, {});
  } catch {
    // The person is on the roster; the invite can be sent again from their row.
  }
  return added;
}

export const sendInvite = (pid: string) => api.post(`/api/v2/access/humans/${enc(pid)}/invite`, {});

export const setRule = (rule: string, value: boolean) =>
  write({
    key: accessQuery.queryKey,
    optimistic: (v: AccessView | undefined) => v && { ...v, rules: { ...v.rules, [rule]: value } },
    send: (idempotencyKey) => api.put('/api/v2/access/rules', { [rule]: value }, { idempotencyKey }),
  });

// ---------------------------------------------------------------- page owners

export interface PageOwnerRow {
  name?: string;
  choice?: string;
  bot?: string | null;
  default?: string | null;
}

export const pageOwnersQuery = queryOptions({
  queryKey: ['settings', 'page-owners'],
  queryFn: async ({ signal }) =>
    (await api.get<{ pages?: Record<string, PageOwnerRow> }>('/api/v2/page-owners', { signal })).pages ?? {},
  ...quiet,
});

/** `choice`: 'default', 'none' or an agent's actor id ('bot:<id>'), as the server's `choice` reads. */
export const setPageOwner = (page: string, choice: string) =>
  write({
    key: pageOwnersQuery.queryKey,
    optimistic: (v: Record<string, PageOwnerRow> | undefined) => v && { ...v, [page]: { ...v[page], choice } },
    send: (idempotencyKey) =>
      api.put(
        `/api/v2/page-owners/${enc(page)}`,
        { bot: choice === 'none' ? null : choice.replace(/^bot:/, '') },
        { idempotencyKey },
      ),
  });

// ---------------------------------------------------------------- your API tokens (the secret is shown once)

export interface PersonalToken {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export const tokensQuery = queryOptions({
  queryKey: ['settings', 'tokens'],
  queryFn: ({ signal }) => api.get<{ tokens: PersonalToken[]; can_create: boolean }>('/api/v2/me/tokens', { signal }),
  ...quiet,
});

export const createToken = (label: string) =>
  api.post<{ id: string; token: string; label: string; expires_at: string | null }>('/api/v2/me/tokens', {
    label,
    expires_in_days: 90,
  });

export const revokeToken = (id: string) =>
  write({
    key: tokensQuery.queryKey,
    optimistic: (v: { tokens: PersonalToken[] } | undefined) =>
      v && { ...v, tokens: v.tokens.map((t) => (t.id === id ? { ...t, revoked_at: new Date().toISOString() } : t)) },
    send: (idempotencyKey) => api.post(`/api/v2/me/tokens/${enc(id)}/revoke`, {}, { idempotencyKey }),
  });

// ---------------------------------------------------------------- notifications (POST /api/v2/humans/{id})

export interface NoticePerson {
  id: string;
  notify_browser?: boolean;
  email_digest?: boolean;
}

export const humansQuery = queryOptions({
  queryKey: ['settings', 'humans'],
  queryFn: async ({ signal }) => (await api.get<{ people?: NoticePerson[] }>('/api/v2/humans', { signal })).people ?? [],
  ...quiet,
});

export const setMyNotice = (pid: string, field: 'notify_browser' | 'email_digest', on: boolean) =>
  write({
    key: humansQuery.queryKey,
    optimistic: (v: NoticePerson[] | undefined) => v?.map((p) => (p.id === pid ? { ...p, [field]: on } : p)),
    send: (idempotencyKey) => api.post(`/api/v2/humans/${enc(pid)}`, { [field]: on }, { idempotencyKey }),
  });

// ---------------------------------------------------------------- security

export type ScreenMode = 'off' | 'warn' | 'hold';

/** GET /api/v2/screening (backend/screening.py view): each door's mode and the two detector switches. */
export interface ScreeningView {
  modes: Record<string, ScreenMode>;
  layers: { patterns: boolean; model: boolean };
  sources: { id: string; name: string }[];
}

export const screeningQuery = queryOptions({
  queryKey: ['settings', 'screening'],
  queryFn: ({ signal }) => api.get<ScreeningView>('/api/v2/screening', { signal }),
  ...quiet,
});

export const setScreening = (change: ScreeningModes) =>
  write({
    key: screeningQuery.queryKey,
    optimistic: (v: ScreeningView | undefined) => {
      if (!v) return v;
      const { patterns, model, ...doors } = change;
      return {
        ...v,
        modes: { ...v.modes, ...doors },
        layers: { patterns: patterns ?? v.layers.patterns, model: model ?? v.layers.model },
      };
    },
    send: (idempotencyKey) => api.put('/api/v2/screening', change, { idempotencyKey }),
  });

export const supportQuery = queryOptions({
  queryKey: ['settings', 'support'],
  queryFn: ({ signal }) => api.get<CloudSupportView>('/api/v2/cloud-machines/support', { signal }),
  ...quiet,
});

export const setSupport = (on: boolean) =>
  write({
    key: supportQuery.queryKey,
    optimistic: (v: CloudSupportView | undefined) => v && { ...v, on },
    send: (idempotencyKey) => api.put('/api/v2/cloud-machines/support', { on }, { idempotencyKey }),
  });

// ---------------------------------------------------------------- billing (owners and admins; money in micro-dollars)

export interface BillingView {
  state: string;
  month: string;
  balance_micros: number;
  min_balance_micros: number;
  cloud?: { access?: string; plan?: string; trial_days_left?: number | null; billing_url?: string | null } | null;
  credits: { label: string; amount_micros: number; left_micros: number; expires?: string | null; current: boolean }[];
  usage: { period: string; total_micros: number }[];
  statement: { opening_micros: number; closing_micros: number; debits_by_product: Record<string, number> };
  months: string[];
}

export const billingQuery = queryOptions({
  queryKey: ['settings', 'billing'],
  queryFn: ({ signal }) => api.get<BillingView>('/api/v2/billing', { signal }),
  ...quiet,
});

// ---------------------------------------------------------------- history, health, changelog

export interface HistoryItem {
  id: string;
  at: string;
  actor: string;
  kind: string;
  subject?: string;
  result: string;
}

/** `who`: an actor id from the directory ('bot:<slug>' or 'human:<id>'), as the server's `bot` or `person` filter. */
export const historyFilter = (who?: string) => {
  const m = who ? /^(bot|human):(.+)$/.exec(who) : null;
  return m ? `?${m[1] === 'bot' ? 'bot' : 'person'}=${enc(m[2] ?? '')}` : '';
};

export const historyFor = (who?: string) =>
  queryOptions({
    queryKey: ['settings', 'history', who ?? ''],
    queryFn: ({ signal }) =>
      api.get<{ items: HistoryItem[]; names: Record<string, string>; next?: string | null }>(`/api/v2/history${historyFilter(who)}`, { signal }),
    ...quiet,
  });

export const historyQuery = historyFor();

export interface HealthCheck {
  id: string;
  label: string;
  status: 'ok' | 'info' | 'warn' | 'bad' | string;
  summary: string;
  /** What to do about it: the old app's address (`#/settings` + `tab`), an outside page, or only a label. */
  fixes?: HealthFix[];
}

export interface HealthFix {
  label: string;
  href?: string;
  tab?: string;
  click?: string;
}

export const healthQuery = queryOptions({
  queryKey: ['settings', 'health'],
  queryFn: async ({ signal }) => {
    const h = await api.get<Health>('/api/v2/health', { signal });
    return { checked: h.checked, attention: h.attention, checks: h.checks as HealthCheck[] };
  },
  ...quiet,
});

export const changelogQuery = queryOptions({
  queryKey: ['settings', 'changelog'],
  queryFn: ({ signal }) => api.get<ChangelogList>('/api/v2/changelog?kind=product&limit=60', { signal }),
  ...quiet,
});

// ---------------------------------------------------------------- model subscriptions (Settings, Agents)

/** The agents on a subscription: the server adds `agents` (S2); until it does there are none to draw. */
export interface SubscriptionAgent {
  id: string;
  name: string;
  fallback?: boolean;
}

export const subscriptionsQuery = queryOptions({
  queryKey: ['settings', 'subscriptions'],
  queryFn: ({ signal }) => api.get<ModelSubscriptionList>('/api/v2/model-subscriptions', { signal }),
  ...quiet,
});

export type Subscription = ModelSubscription & { agents?: SubscriptionAgent[] };

export const removeSubscription = (id: string) =>
  write({
    key: subscriptionsQuery.queryKey,
    optimistic: (v: ModelSubscriptionList | undefined) => v && { ...v, subscriptions: v.subscriptions.filter((x) => x.id !== id) },
    // The person has said yes already, so runs on it stop and start fresh without a second question.
    send: (idempotencyKey) => api.delete(`/api/v2/model-subscriptions/${enc(id)}?confirm=1`, { idempotencyKey }),
  });

export const refreshSubscription = (id: string) => api.post(`/api/v2/model-subscriptions/${enc(id)}/usage-refresh`, {});

/** A key or a token: the secret goes to the server once and is never read back. */
export const addSubscription = (kind: string, secret: string, name?: string) =>
  api.post<{ id: string; name?: string }>('/api/v2/model-subscriptions', { kind, secret, ...(name ? { name } : {}) });

/** A sign-in on a computer (backend/model_login.py). No `id` and `waking`: its computer is starting; ask again. */
export interface SignIn {
  id?: string;
  kind: string;
  runner_id?: string;
  state?: 'requested' | 'starting' | 'waiting' | 'finishing' | 'done' | 'failed' | 'expired' | 'canceled';
  url?: string | null;
  code?: string | null;
  accepts_code?: boolean;
  message?: string | null;
  computer?: string | null;
  name?: string | null;
  waking?: boolean;
}

export const LIVE_SIGN_IN = ['requested', 'starting', 'waiting', 'finishing'];

export const startSignIn = (kind: 'claude_login' | 'codex_login') =>
  api.post<SignIn | null>('/api/v2/model-subscriptions/connect', { kind });

/** The one timer in Settings: a sign-in under way is read every 2 s until it ends. */
export const signInQuery = (runner: string, id: string) =>
  queryOptions({
    queryKey: ['settings', 'sign-in', runner, id],
    queryFn: ({ signal }) => api.get<SignIn>(`/api/v2/computers/${enc(runner)}/logins/${enc(id)}`, { signal }),
    refetchInterval: (q) => (LIVE_SIGN_IN.includes(q.state.data?.state ?? 'requested') ? 2000 : false),
    staleTime: 0,
  });

export const sendSignInCode = (runner: string, id: string, code: string) =>
  api.post<SignIn>(`/api/v2/computers/${enc(runner)}/logins/${enc(id)}/code`, { code });

export const cancelSignIn = (runner: string, id: string) => api.post(`/api/v2/computers/${enc(runner)}/logins/${enc(id)}/cancel`, {});

/** One computer as GET /api/v2/operations draws it (ui/app/settings.ts SettingsMachine). */
export interface Computer {
  id: string;
  label?: string;
  owner_id?: string;
  revoked_at?: string | null;
  gateway_fallback?: boolean;
  online?: boolean;
  cloud_machine?: { shared?: boolean; bot?: string; mode?: string } | null;
  readiness?: { bots?: Record<string, { ready?: boolean }> } | null;
}

export const computersQuery = queryOptions({
  queryKey: ['settings', 'computers'],
  queryFn: async ({ signal }) => (await api.get<{ machines?: Computer[] }>('/api/v2/operations', { signal })).machines ?? [],
  ...quiet,
});

const patchComputer = (rid: string, change: Partial<Computer>) => (v: Computer[] | undefined) =>
  v?.map((c) => (c.id === rid ? { ...c, ...change } : c));

export const labelComputer = (rid: string, label: string) =>
  write({
    key: computersQuery.queryKey,
    optimistic: patchComputer(rid, { label }),
    send: (idempotencyKey) => api.post(`/api/v2/computers/${enc(rid)}/label`, { label }, { idempotencyKey }),
  });

export const removeComputer = (rid: string) =>
  write({
    key: computersQuery.queryKey,
    optimistic: patchComputer(rid, { revoked_at: new Date().toISOString() }),
    send: (idempotencyKey) => api.post(`/api/v2/computers/${enc(rid)}/revoke`, {}, { idempotencyKey }),
  });

export const setGatewayFallback = (rid: string, on: boolean) =>
  write({
    key: computersQuery.queryKey,
    optimistic: patchComputer(rid, { gateway_fallback: on }),
    send: (idempotencyKey) => api.post(`/api/v2/computers/${enc(rid)}/gateway-fallback`, { on }, { idempotencyKey }),
  });

/** A one-time code a new computer enrolls with: shown once. */
export const enrollComputer = (operator: string) => api.post<{ code: string }>('/api/v2/enrollments', { operator });

/** GET /api/v2/models: the catalogue (backend/providers.py MODEL_CATALOG) and the providers turned on. */
export interface CatalogModel {
  id: string;
  provider: string;
  label: string;
  deprecated?: boolean;
  runtime?: string;
  context_tokens?: number | null;
}

export const modelsQuery = queryOptions({
  queryKey: ['settings', 'models'],
  queryFn: ({ signal }) =>
    api.get<{ models: CatalogModel[]; enabled_providers?: string[]; default?: { model?: string } }>('/api/v2/models', { signal }),
  ...quiet,
});

// ---- usage (anchor: the Usage builder adds its calls below) ----

export type UsageRange = 'today' | '7d' | '30d' | 'month';

/** Days are UTC, as the server counts them. 7 days is today and six back. */
export function rangeDays(range: UsageRange, now = new Date()): { from: string; to: string } {
  const day = (d: Date) => d.toISOString().slice(0, 10);
  const to = day(now);
  const back = (n: number) => day(new Date(now.getTime() - n * 86_400_000));
  if (range === 'today') return { from: to, to };
  if (range === '7d') return { from: back(6), to };
  if (range === '30d') return { from: back(29), to };
  return { from: `${to.slice(0, 8)}01`, to };
}

export interface UsageRow extends UsageFigures {
  bot?: string;
  name?: string;
  day?: string;
  value?: string;
  share?: number;
}

export interface UsageView {
  from: string;
  to: string;
  totals: UsageFigures;
  rows: UsageRow[];
}

export const usageQuery = (range: UsageRange, group: 'bot' | 'day' | 'model') =>
  queryOptions({
    queryKey: ['usage', range, group],
    queryFn: async ({ signal }) => {
      const { from, to } = rangeDays(range);
      const u = await api.get<Usage>(`/api/v2/usage?from=${from}&to=${to}&group=${group}`, { signal });
      return { from: u.from, to: u.to, totals: u.totals, rows: (u.rows ?? []) as unknown as UsageRow[] } satisfies UsageView;
    },
    ...quiet,
  });

export interface AgentUsage {
  totals: UsageFigures;
  tasks: UsageRow[];
  models: UsageRow[];
}

export const agentUsageQuery = (range: UsageRange, bot: string) =>
  queryOptions({
    queryKey: ['usage', range, 'agent', bot],
    queryFn: async ({ signal }) => {
      const { from, to } = rangeDays(range);
      const u = await api.get<Usage>(`/api/v2/usage?from=${from}&to=${to}&group=bot&bot=${enc(bot)}`, { signal });
      return { totals: u.totals, tasks: (u.tasks ?? []) as unknown as UsageRow[], models: (u.models ?? []) as unknown as UsageRow[] } satisfies AgentUsage;
    },
    ...quiet,
  });

export const limitsQuery = queryOptions({
  queryKey: ['usage', 'limits'],
  queryFn: ({ signal }) => api.get<UsageLimits>('/api/v2/usage/limits', { signal }),
  ...quiet,
});

/** An agent's monthly limit in dollars; null takes it off (the company's default then holds). */
export const setLimit = (bot: string, monthly: number | null) =>
  write({
    key: limitsQuery.queryKey,
    optimistic: (v: UsageLimits | undefined) => {
      const cur = v?.bots[bot];
      if (!v || !cur) return v;
      const shown = monthly ?? v.default.monthly_usd;
      const percent = shown ? Math.round(((cur.month_spent ?? 0) / shown) * 100) : 0;
      return { ...v, bots: { ...v.bots, [bot]: { ...cur, own_monthly_usd: monthly, monthly_usd: shown, percent } } };
    },
    send: (idempotencyKey) => api.put(`/api/v2/usage/limits/${enc(bot)}`, { monthly_usd: monthly }, { idempotencyKey }),
  });

export const setDefaultLimit = (monthly: number | null) =>
  write({
    key: limitsQuery.queryKey,
    optimistic: (v: UsageLimits | undefined) => v && { ...v, default: { ...v.default, monthly_usd: monthly } },
    send: (idempotencyKey) => api.put('/api/v2/usage/limits', { monthly_usd: monthly }, { idempotencyKey }),
  });

// ---------------------------------------------------------------- tasks: the public roadmap (GET /api/v2/roadmaps/settings)

export interface RoadmapView {
  /** The labels (tag keys) whose tasks show on a public page at /public/roadmaps/<label>. */
  labels: string[];
  cors_origins: string[];
  action_limit: number;
  suggestion_limit: number;
  /** An agent's public words wait for a person's OK. */
  bot_public_approval: boolean;
  suggestions_bot: string | null;
  suggestions_receiver?: { bot: string | null; name: string | null; source: string };
}

export const roadmapQuery = queryOptions({
  queryKey: ['settings', 'roadmap'],
  queryFn: ({ signal }) => api.get<RoadmapView>('/api/v2/roadmaps/settings', { signal }),
  ...quiet,
});

export const setRoadmap = (change: Partial<Pick<RoadmapView, 'labels' | 'bot_public_approval'>>) =>
  write({
    key: roadmapQuery.queryKey,
    optimistic: (v: RoadmapView | undefined) => v && { ...v, ...change },
    send: (idempotencyKey) => api.post<RoadmapView>('/api/v2/roadmaps/settings', change, { idempotencyKey }),
  });

export interface TagRow {
  id: string;
  key: string;
  label: string;
  is_template: boolean;
}

export const labelsQuery = queryOptions({
  queryKey: ['settings', 'labels'],
  queryFn: async ({ signal }) =>
    (await api.get<{ tags: TagRow[] }>('/api/v2/tags?is_template=false', { signal })).tags.filter((t) => !t.is_template),
  ...quiet,
});

// ---------------------------------------------------------------- contacts: types and their fields (/api/v2/entities)

export interface ContactType {
  key: string;
  name: string;
  plural_name?: string | null;
  description?: string | null;
  built_in: boolean | number;
}

export interface ContactField {
  key: string;
  name: string;
  description?: string | null;
  value_type: string;
  /** 'tico', or 'connection:<id>' for a field a connection fills. */
  owner: string;
  required: boolean;
}

export const contactTypesQuery = queryOptions({
  queryKey: ['settings', 'contact-types'],
  queryFn: async ({ signal }) => {
    const [t, n] = await Promise.all([
      api.get<{ types: ContactType[] }>('/api/v2/entities/types', { signal }),
      api.get<{ counts: { type: string; count: number; approximate: boolean }[] }>('/api/v2/entities/counts', { signal }),
    ]);
    const count = new Map(n.counts.map((c) => [c.type, c]));
    return t.types.map((type) => ({ ...type, count: count.get(type.key)?.count ?? 0, approximate: !!count.get(type.key)?.approximate }));
  },
  ...quiet,
});

export const contactTypeQuery = (type: string) =>
  queryOptions({
    queryKey: ['settings', 'contact-types', type],
    queryFn: ({ signal }) => api.get<{ attributes: ContactField[] }>(`/api/v2/entities/types/${enc(type)}`, { signal }),
    ...quiet,
  });

/** The kinds a field can be without a list of options. */
export const FIELD_KINDS = ['text', 'number', 'currency', 'date', 'checkbox', 'email', 'phone', 'url', 'rating'] as const;
export type FieldKind = (typeof FIELD_KINDS)[number];

export const addContactField = (type: string, name: string, kind: FieldKind, description: string) =>
  api.post<{ key: string; type: string }>(
    `/api/v2/entities/types/${enc(type)}/attributes`,
    {
      key: name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 60),
      name,
      value_type: kind,
      description,
    },
  );
