import { queryOptions } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import type {
  BotTaskChoice,
  BotTaskModel,
  BotTaskModelSaved,
  ChangelogList,
  CloudSupportView,
  DirectoryCredentials,
  DirectoryPlan,
  DirectorySaved,
  DirectoryScimToken,
  DirectorySyncResult,
  DirectoryUpdate,
  DirectoryView as DirectoryViewWire,
  Health,
  ModelSubscription,
  ModelSubscriptionList,
  ScreeningModes,
  SyncRequest,
  TaskModelSetting,
  Usage,
  UsageFigures,
  UsageLimits,
} from '@/api/schema';
import { configQuery, pageOwnersQuery, queryClient, quietRoot, write, type PageOwnerRow, type PageOwners } from '@/data';
import type { TaskCaps } from '@/api/schema';
import type { ProductRepositoryPreview, ProductRepositoryResult, Repository, RepositoryList } from '@/api/schema';

/* Settings and Usage have no live topic: both are read again after the person's own writes, on reconnect, and when a
   section opens with data older than a minute. The one timer is a model sign-in under way (readSignIn). */
quietRoot('settings');
quietRoot('usage');

const enc = encodeURIComponent;

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
  can_sign_in?: boolean;
  removable?: boolean;
}

export interface AccessView {
  /** `revision` is the owner record's, which a transfer or a new sign-in email is sent against. */
  owner: { email: string; person: string; revision?: number };
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
    // The access route refuses `left: true` (422); the owner marks someone as left on their profile.
    send: (idempotencyKey) => api.post(`/api/v2/humans/${enc(pid)}`, { left: true }, { idempotencyKey }),
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

const ownerRevision = () => queryClient.getQueryData<AccessView>(accessQuery.queryKey)?.owner.revision ?? 0;

/** Hand the company to another person, against the owner record on screen (POST /api/v2/access/owner). */
export const transferOwner = (pid: string, keepAdmin: boolean) =>
  api.post<{ owner: string; revision: number }>('/api/v2/access/owner', {
    person: pid,
    previous_owner_bot_admin: keepAdmin,
    expected_revision: ownerRevision(),
    confirm: true,
  });

/** The owner's own sign-in address, typed twice; the old one stops signing in at once. */
export const changeOwnerEmail = (email: string, again: string) =>
  api.post<{ owner: string; revision: number }>('/api/v2/access/owner/email', {
    email,
    confirm_email: again,
    expected_revision: ownerRevision(),
    confirm: true,
  });

export const setRule = (rule: string, value: boolean) =>
  write({
    key: accessQuery.queryKey,
    optimistic: (v: AccessView | undefined) => v && { ...v, rules: { ...v.rules, [rule]: value } },
    send: (idempotencyKey) => api.put('/api/v2/access/rules', { [rule]: value }, { idempotencyKey }),
  });

// ---------------------------------------------------------------- directory sync (owner; /api/v2/directory)

export type { DirectoryCredentials };

/** The schema leaves the last sync an open object; these are the fields backend/directory.py writes into it. A saved
    key is never read back: `credentials` says only whether there is one and its last few characters. */
export type DirectoryView = Omit<DirectoryViewWire, 'last'> & { last: { at?: string; ok?: boolean; error?: string; held?: boolean } };

export type DirectorySource = DirectoryView['source'];

/** One person in a plan (backend/directory.py `plan`); the schema leaves the rows open. */
export interface DirectoryPlanRow {
  email: string;
  name?: string;
  reason?: string;
}

type WithRows<T extends { plan: object }> = Omit<T, 'plan'> & {
  plan: { hash: string } & Record<'adds' | 'updates' | 'restores' | 'leaves' | 'protected' | 'skipped', DirectoryPlanRow[]>;
};

/** POST /api/v2/directory/preview: what a sync would do. Nothing changes until it is applied. */
export type DirectoryPreview = WithRows<DirectoryPlan>;
/** POST /api/v2/directory/sync: the plan again, and whether it was applied (`stale`: the directory changed since). */
export type DirectorySynced = WithRows<DirectorySyncResult>;

export const directorySyncQuery = queryOptions({
  queryKey: ['settings', 'directory-sync'],
  queryFn: async ({ signal }) => {
    const v = await api.get<Partial<DirectoryView>>('/api/v2/directory', { signal });
    return {
      source: '',
      interval_minutes: 360,
      mass_leave_limit: 10,
      revision: 0,
      ...v,
      last: { ...v.last },
      filter: { groups: [], org_units: [], domains: [], ...v.filter },
      credentials: { ...v.credentials },
      scim: { ...v.scim },
    } as DirectoryView;
  },
});

/** `credentials` only when the person typed new ones; they are never read back. */
export type DirectorySave = Omit<DirectoryUpdate, 'expected_revision'>;

export const saveDirectory = async (body: DirectorySave) => {
  const expected_revision = queryClient.getQueryData(directorySyncQuery.queryKey)?.revision ?? 0;
  const r = await api.put<DirectorySaved>('/api/v2/directory', { ...body, expected_revision } satisfies DirectoryUpdate);
  await queryClient.invalidateQueries({ queryKey: directorySyncQuery.queryKey });
  return r;
};

/** A new SCIM token. It goes back to the press that asked for it and nowhere else: not the cache, not a toast. */
export const newScimToken = async () => {
  const r = await api.post<DirectoryScimToken>('/api/v2/directory/scim-token', {});
  await queryClient.invalidateQueries({ queryKey: directorySyncQuery.queryKey });
  return r.token;
};

export const previewDirectory = () => api.post<DirectoryPreview>('/api/v2/directory/preview', {});

export const syncDirectory = async (planHash: string, confirm: boolean) => {
  const r = await api.post<DirectorySynced>('/api/v2/directory/sync', { confirm, plan_hash: planHash } satisfies SyncRequest);
  await queryClient.invalidateQueries({ queryKey: directorySyncQuery.queryKey });
  await queryClient.invalidateQueries({ queryKey: accessQuery.queryKey });
  return r;
};

// ---------------------------------------------------------------- page owners

export { pageOwnersQuery, type PageOwnerRow };

/** `choice`: 'default', 'none' or an agent's actor id ('bot:<id>'), as the server's `choice` reads. */
export const setPageOwner = (page: string, choice: string) =>
  write({
    key: pageOwnersQuery.queryKey,
    optimistic: (v: PageOwners | undefined) => v && { ...v, pages: { ...v.pages, [page]: { ...v.pages[page], choice } } },
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
});

export const createToken = (label: string, days = 90) =>
  api.post<{ id: string; token: string; label: string; expires_at: string | null }>('/api/v2/me/tokens', {
    label,
    expires_in_days: days,
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
  // The list has no v2 address: GET /api/humans (backend/views.py people), each row people.profile with its switches.
  queryFn: async ({ signal }) => (await api.get<{ people?: NoticePerson[] }>('/api/humans', { signal })).people ?? [],
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

type HistoryPage = { items: HistoryItem[]; names: Record<string, string>; next?: string | null };

/** One page of the log, newest first. `before`: the page before's `next` cursor; the first page is the section's read. */
export const historyPage = (before = '') =>
  queryOptions({
    queryKey: ['settings', 'history', before],
    queryFn: ({ signal }) => api.get<HistoryPage>(`/api/v2/history${before ? `?before=${enc(before)}` : ''}`, { signal }),
  });

export const historyQuery = historyPage();

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
    return { checked: h.checked, attention: h.attention, checks: h.checks as HealthCheck[], stuck: stuckOf(h) };
  },
});

export const changelogQuery = queryOptions({
  queryKey: ['settings', 'changelog'],
  queryFn: ({ signal }) => api.get<ChangelogList>('/api/v2/changelog?kind=product&limit=60', { signal }),
});

/** A product update by hand (owner): a title and its bullets. */
export const addProductUpdate = async (title: string, bullets: string[]) => {
  await api.post('/api/changelog', { title, bullets });
  await queryClient.invalidateQueries({ queryKey: changelogQuery.queryKey });
};

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
});

export type Subscription = ModelSubscription & { agents?: SubscriptionAgent[] };

export const removeSubscription = (id: string) =>
  write({
    key: subscriptionsQuery.queryKey,
    wait: true,
    optimistic: (v: ModelSubscriptionList | undefined) => v && { ...v, subscriptions: v.subscriptions.filter((x) => x.id !== id) },
    // The person has said yes already, so runs on it stop and start fresh without a second question.
    send: (idempotencyKey) => api.delete(`/api/v2/model-subscriptions/${enc(id)}?confirm=1`, { idempotencyKey }),
  });

export const refreshSubscription = (id: string) => api.post(`/api/v2/model-subscriptions/${enc(id)}/usage-refresh`, {});

/** A new name, and for a plan a new person who holds it (`owner`: a person id), sent only when it changed. */
export const editSubscription = (id: string, name: string, owner?: string) =>
  write({
    key: subscriptionsQuery.queryKey,
    optimistic: (v: ModelSubscriptionList | undefined) =>
      v && { ...v, subscriptions: v.subscriptions.map((x) => (x.id === id ? { ...x, name, ...(owner ? { owner_id: `human:${owner}` } : {}) } : x)) },
    send: (idempotencyKey) =>
      api.post<ModelSubscription>(
        `/api/v2/model-subscriptions/${enc(id)}`,
        { name, ...(owner ? { owner_id: `human:${owner}` } : {}) },
        { idempotencyKey },
      ),
  });

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

/** One read of a sign-in under way. Not a query: its answer holds the provider's one-time code and page, which stay
    in the dialog's state (polled with `poll` from @/lib), never the query cache. */
export const readSignIn = (runner: string, id: string, signal?: AbortSignal) =>
  api.get<SignIn>(`/api/v2/computers/${enc(runner)}/logins/${enc(id)}`, { signal });

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
});

const patchComputer = (rid: string, change: Partial<Computer>) => <C extends { id: string }>(v: C[] | undefined) =>
  v?.map((c) => (c.id === rid ? { ...c, ...change } : c));
/** Settings and the teammate pages read the computers apart: a change shows in both at once. The team key is written out:
    importing the team's queries here would load the action registry before this file is done. */
const computerKeys = [computersQuery.queryKey, ['team', 'computers']];

export const labelComputer = (rid: string, label: string) =>
  write({
    key: computerKeys,
    optimistic: patchComputer(rid, { label }),
    send: (idempotencyKey) => api.post(`/api/v2/computers/${enc(rid)}/label`, { label }, { idempotencyKey }),
  });

export const removeComputer = (rid: string) =>
  write({
    key: computerKeys,
    optimistic: patchComputer(rid, { revoked_at: new Date().toISOString() }),
    send: (idempotencyKey) => api.post(`/api/v2/computers/${enc(rid)}/revoke`, {}, { idempotencyKey }),
  });

export const setGatewayFallback = (rid: string, on: boolean) =>
  write({
    key: computerKeys,
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
  /** The harnesses that run it; none listed means any. */
  harnesses?: string[];
  efforts?: string[];
}

export const modelsQuery = queryOptions({
  queryKey: ['settings', 'models'],
  queryFn: ({ signal }) =>
    api.get<{ models: CatalogModel[]; enabled_providers?: string[]; harnesses?: { id: string; name: string }[]; default?: { model?: string } }>(
      '/api/v2/models',
      { signal },
    ),
});

/** GET /api/v2/providers: the model a new agent uses and the one meetings reply on ('' for each agent's own). */
export interface ProvidersView {
  enabled: string[];
  default: { runtime?: string; model?: string };
  revision: number;
  meeting_model?: string;
}

export const providersQuery = queryOptions({
  queryKey: ['settings', 'providers'],
  queryFn: ({ signal }) => api.get<ProvidersView>('/api/v2/providers', { signal }),
});

/** The owner's defaults, against the revision on screen; the providers turned on are sent back unchanged. */
export const setDefaults = async (change: { model?: string; meeting?: string }) => {
  const before = queryClient.getQueryData<ProvidersView>(providersQuery.queryKey);
  await write({
    key: providersQuery.queryKey,
    optimistic: (v: ProvidersView | undefined) =>
      v && {
        ...v,
        default: change.model === undefined ? v.default : { ...v.default, model: change.model },
        meeting_model: change.meeting ?? v.meeting_model,
      },
    send: (idempotencyKey) =>
      api.put<ProvidersView>(
        '/api/v2/providers',
        {
          enabled: before?.enabled ?? [],
          model: change.model ?? before?.default.model ?? '',
          expected_revision: before?.revision ?? 0,
          ...(change.meeting === undefined ? {} : { meeting_model: change.meeting }),
        },
        { idempotencyKey },
      ),
  });
  // The catalog's own `default` follows the saved one.
  void queryClient.invalidateQueries({ queryKey: modelsQuery.queryKey });
};

// ---------------------------------------------------------------- agents: retired ones, owners, access, mail, stopped runs

/** An agent as GET /api/v2/bots?include_archived=1 lists it: who may bring a retired one back. */
export interface BotListed {
  id?: string;
  slug?: string;
  display_name?: string;
  state?: string;
  owner_id?: string | null;
  bot_owners?: { id: string; name?: string; email?: string }[];
}

export const botsQuery = queryOptions({
  queryKey: ['settings', 'bots'],
  queryFn: ({ signal }) => api.get<BotListed[]>('/api/v2/bots?include_archived=1', { signal }),
});

export const restoreAgent = (bot: string) =>
  write({
    key: botsQuery.queryKey,
    optimistic: (v: BotListed[] | undefined) => v?.map((b) => ((b.id ?? b.slug) === bot ? { ...b, state: 'active' } : b)),
    send: (idempotencyKey) => api.post(`/api/v2/bots/${enc(bot)}/restore`, {}, { idempotencyKey }),
  });

/** Add and take away co-owners (person ids); the agent's creator always stays. */
export const setCoOwners = (bot: string, add: string[], remove: string[]) =>
  write({
    key: botsQuery.queryKey,
    optimistic: (v: BotListed[] | undefined) =>
      v?.map((b) => {
        if ((b.id ?? b.slug) !== bot) return b;
        const kept = (b.bot_owners ?? []).filter((o) => !remove.includes(o.id));
        return { ...b, bot_owners: [...kept, ...add.filter((id) => !kept.some((o) => o.id === id)).map((id) => ({ id }))] };
      }),
    send: (idempotencyKey) => api.post(`/api/v2/bots/${enc(bot)}/co-owners`, { add, remove }, { idempotencyKey }),
  });

/** Who sees, reads and writes to an agent: everyone, or the people, teams and agents named. */
export interface Audience {
  everyone: boolean;
  people: string[];
  teams: string[];
  bots: string[];
}

export interface BotAccess {
  bot: string;
  see: Audience;
  read: Audience;
  write: Audience;
  teams?: { id: string; name: string }[];
  revision: number;
}

export const botAccessQuery = (bot: string) =>
  queryOptions({
    queryKey: ['settings', 'bot-access', bot],
    queryFn: ({ signal }) => api.get<BotAccess>(`/api/v2/bots/${enc(bot)}/access`, { signal }),
  });

export const setBotAccess = (bot: string, change: Pick<BotAccess, 'see' | 'read' | 'write'>) => {
  const key = botAccessQuery(bot).queryKey;
  const revision = queryClient.getQueryData<BotAccess>(key)?.revision ?? 0;
  return write({
    key,
    optimistic: (v: BotAccess | undefined) => v && { ...v, ...change },
    send: (idempotencyKey) => api.put(`/api/v2/bots/${enc(bot)}/access`, { ...change, revision }, { idempotencyKey }),
  });
};

export interface BotMail {
  set?: boolean;
  outbound_send: boolean;
  sending_policy?: string;
  company_send?: boolean;
}

export const botMailQuery = (bot: string) =>
  queryOptions({
    queryKey: ['settings', 'bot-mail', bot],
    queryFn: ({ signal }) => api.get<BotMail>(`/api/v2/bots/${enc(bot)}/mail-settings`, { signal }),
  });

export const setBotMail = (bot: string, on: boolean) =>
  write({
    key: botMailQuery(bot).queryKey,
    optimistic: (v: BotMail | undefined) => v && { ...v, outbound_send: on },
    send: (idempotencyKey) => api.post(`/api/v2/bots/${enc(bot)}/mail-settings`, { outbound_send: on }, { idempotencyKey }),
  });

/** A run that stopped part way (GET /api/v2/bots/{bot}/execution-review): try it again or let it go. */
export interface StoppedRun {
  id: string;
  run_id: string;
  stopped: 'read' | 'restarted' | 'timeout' | 'changed' | 'twice' | 'unknown' | string;
  reason?: string;
  request?: string;
  run?: { final_text?: string | null; started_at?: string | null; created_at?: string | null } | null;
  task?: { title?: string; status?: string; owner_name?: string } | null;
  pending_decision?: { decision?: string } | null;
  /** What the run streamed before it stopped, when it saved no final answer. */
  output?: { text?: string }[];
  /** The tools that may have changed something. */
  acted?: string[];
  tools?: { tool: string; summary?: string; status?: string; nested?: boolean }[];
}

export const stoppedRunsQuery = (bot: string) =>
  queryOptions({
    queryKey: ['settings', 'stopped-runs', bot],
    queryFn: async ({ signal }) => (await api.get<{ jobs?: StoppedRun[] }>(`/api/v2/bots/${enc(bot)}/execution-review`, { signal })).jobs ?? [],
  });

/** Retrying is safe only when the run stopped before it could have acted. */
export const SAFE_RETRY = ['read', 'restarted', 'timeout'];

const RECONCILE_NOTE = {
  resume: 'The human chose to retry this request. Check existing work and external effects before repeating any step.',
  dismiss: 'The human chose not to retry this delivery. Inspect the task separately.',
};

export const reconcileRun = (bot: string, job: string, run: string, decision: 'resume' | 'dismiss', detail = '') =>
  write({
    key: stoppedRunsQuery(bot).queryKey,
    wait: true,
    optimistic: (v: StoppedRun[] | undefined) => v?.filter((j) => j.id !== job),
    send: (idempotencyKey) =>
      api.post<{ deferred?: boolean }>(
        `/api/v2/jobs/${enc(job)}/reconcile`,
        {
          run_id: run,
          decision,
          note: `${RECONCILE_NOTE[decision]}${detail.trim() ? ` Details: ${detail.trim()}` : ''}`.slice(0, 4000),
          acknowledge_uncertain_effects: true,
        },
        { idempotencyKey },
      ),
  });

// ---------------------------------------------------------------- a destination that changes (409 destination_change)

/** The server's 409 when a saved address changes and credentials were saved for the old one. */
export interface DestinationChange {
  name: string;
  old: string;
  new: string;
  /** The saved credentials, by label. */
  credentials: string[];
}

export type DestinationChoice = 'keep' | 'clear';

const addressOf = (v: unknown) => (typeof v === 'object' && v ? String((v as { address?: unknown }).address ?? '') : String(v ?? ''));

/** The change the server asks about, or null when the error is anything else. */
export function destinationChange(e: unknown): DestinationChange | null {
  if (!(e instanceof ApiError) || e.code !== 'destination_change') return null;
  const x = e.extra;
  return {
    name: String(x.name || x.integration || 'it'),
    old: addressOf(x.old),
    new: addressOf(x.new),
    credentials: Array.isArray(x.credentials) ? x.credentials.map(String) : [],
  };
}

/** Send a change of address; when the server asks what to do with the old address's credentials, ask the person
    (`ask` resolves null for Cancel) and send again with their answer. Cancel saves nothing. */
export async function withDestination<T>(
  send: (credentials?: DestinationChoice) => Promise<T>,
  ask: (change: DestinationChange) => Promise<DestinationChoice | null>,
): Promise<T> {
  try {
    return await send();
  } catch (e) {
    const change = destinationChange(e);
    if (!change) throw e;
    const choice = await ask(change);
    if (!choice) throw new Error('Nothing was saved', { cause: e });
    return send(choice);
  }
}

// ---------------------------------------------------------------- Tico Gateway: its key and address (owner)

/** GET /api/v2/providers/gateway (backend gateway_connection.view): never the key, only its preview, to the owner. */
export interface GatewayView {
  connected: boolean;
  url: string;
  default_url: string;
  /** false: set by the server's environment, not here. */
  editable: boolean;
  preview?: string;
  revision?: number;
}

export const gatewayQuery = queryOptions({
  queryKey: ['settings', 'gateway'],
  queryFn: ({ signal }) => api.get<GatewayView>('/api/v2/providers/gateway', { signal }),
});

/** A new key (left out to keep the saved one) and the address ('' for Tico's), against the revision on screen. The
    route keeps no receipt (the key must not be stored), so no idempotency key. `credentials` answers a held move. */
export async function saveGateway(change: { url: string; key?: string; credentials?: DestinationChoice }): Promise<GatewayView> {
  const before = queryClient.getQueryData<GatewayView>(gatewayQuery.queryKey);
  const saved = await api.put<GatewayView>('/api/v2/providers/gateway', { ...change, expected_revision: before?.revision ?? 0 });
  queryClient.setQueryData(gatewayQuery.queryKey, saved);
  return saved;
}

/** What an agent's tasks run on, as the agent row carries it (`reasoning_effort`; the write takes `effort`). */
export type TaskModel = TaskModelSetting;

/** The model an agent's tasks run on; null runs them on the agent's own model. `warning`: a cheaper pick on an agent
    that sends, spends or publishes, in plain words; it still saves. */
export const setTaskModel = (bot: string, pick: BotTaskChoice | null, revision: number) =>
  api.post<BotTaskModelSaved>(`/api/v2/bots/${enc(bot)}/task-model`, { task_model: pick, expected_revision: revision } satisfies BotTaskModel);

// ---------------------------------------------------------------- computers: sleep when idle, hosted servers

/** GET /api/v2/sleep: one setting for the company's hosted server (and, with `runners`, its agents' computers). */
export interface SleepView {
  hosted: boolean;
  managed: boolean;
  hours: number | null;
  runners: boolean;
  blocker?: string | null;
  active?: boolean;
}

export const sleepQuery = queryOptions({
  queryKey: ['settings', 'sleep'],
  queryFn: async ({ signal }) => (await api.get<{ sleep: SleepView }>('/api/v2/sleep', { signal })).sleep,
});

export const setSleep = (change: { hours: number } | { runners: boolean }) =>
  write({
    key: sleepQuery.queryKey,
    optimistic: (v: SleepView | undefined) => v && { ...v, ...change },
    send: (idempotencyKey) => api.post('/api/v2/sleep', change, { idempotencyKey }),
  });

export interface HostedServer {
  server_id: string;
  kind: string;
  power: 'on' | 'stopped' | string;
}

/** `ok` with the servers HQ hosts; `deactivated` once hosting ended; anything else draws nothing. */
export const hostedQuery = queryOptions({
  queryKey: ['settings', 'hosted'],
  queryFn: ({ signal }) => api.get<{ state: string; servers?: HostedServer[] }>('/api/v2/hosted-servers', { signal }),
});

export const startServer = (id: string) =>
  api.post<{ state: string; message?: string; credit_url?: string }>(`/api/v2/hosted-servers/${enc(id)}/start`, {});

// ---------------------------------------------------------------- settings search (GET /api/v2/settings/catalog)

export interface SettingHit {
  key: string;
  name: string;
  where?: string;
  who?: string;
  /** The old app's address, '#/settings/<tab>/<card>' or '#/usage'. */
  card?: string;
}

export const settingsCatalogQuery = (q: string) =>
  queryOptions({
    queryKey: ['settings', 'catalog', q],
    queryFn: async ({ signal }) =>
      (await api.get<{ settings?: SettingHit[] }>(`/api/v2/settings/catalog?limit=40&q=${enc(q.slice(0, 200))}`, { signal })).settings ?? [],
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
  choices: UsageChoices;
}

function usageView(u: Usage): UsageView {
  return {
    from: u.from,
    to: u.to,
    totals: u.totals,
    rows: (u.rows ?? []) as unknown as UsageRow[],
    choices: {
      departments: u.departments ?? [],
      dimensions: (u.dimensions ?? {}) as Record<string, string[]>,
      labels: (u.dimension_labels ?? {}) as Record<string, Record<string, string>>,
    },
  };
}

export const usageQuery = (range: UsageRange, group: 'bot' | 'day' | 'model') =>
  queryOptions({
    queryKey: ['usage', range, group],
    queryFn: async ({ signal }) => {
      const { from, to } = rangeDays(range);
      const u = await api.get<Usage>(`/api/v2/usage?from=${from}&to=${to}&group=${group}`, { signal });
      return usageView(u);
    },
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
  });

export const limitsQuery = queryOptions({
  queryKey: ['usage', 'limits'],
  queryFn: ({ signal }) => api.get<UsageLimits>('/api/v2/usage/limits', { signal }),
});

/** An agent's monthly limit in dollars; null takes it off (the company's default then holds). */
export const setLimit = (bot: string, monthly: number | null) =>
  write({
    key: limitsQuery.queryKey,
    optimistic: (v: UsageLimits | undefined) => {
      const cur = v?.bots[bot];
      if (!v || !cur) return v;
      const shown = monthly ?? v.default.monthly_usd;
      const percent = shown ? Math.round((monthSpent(cur) / shown) * 100) : 0;
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

/** Whether plan runs count against the limits (the company default's switch); the amounts keep their values. */
export const setCountPlanRuns = (on: boolean) =>
  write({
    key: limitsQuery.queryKey,
    optimistic: (v: UsageLimits | undefined) => v && { ...v, default: { ...v.default, count_subscription: on } },
    send: (idempotencyKey) => api.put('/api/v2/usage/limits', { count_subscription: on }, { idempotencyKey }),
  });

/** Cancel queued automatic wakes and waits; tasks, people's messages and running work stay. */
export const purgeQueue = (scope: string) =>
  api.post<{ cancelled: number; kept_people: number; still_running: number }>('/api/v2/usage/queues/purge', { scope });

// ---------------------------------------------------------------- tasks: the public roadmap (GET /api/v2/roadmaps/settings)

export interface RoadmapView {
  /** The labels (tag keys) whose tasks show on a public page at /public/roadmaps/<label>. */
  labels: string[];
  /** An agent's public words wait for a person's OK. */
  bot_public_approval: boolean;
}

export const roadmapQuery = queryOptions({
  queryKey: ['settings', 'roadmap'],
  queryFn: ({ signal }) => api.get<RoadmapView>('/api/v2/roadmaps/settings', { signal }),
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
});

export const contactTypeQuery = (type: string) =>
  queryOptions({
    queryKey: ['settings', 'contact-types', type],
    queryFn: ({ signal }) => api.get<{ attributes: ContactField[] }>(`/api/v2/entities/types/${enc(type)}`, { signal }),
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

// ---- company gaps (anchor: General, People, tokens, Security and Tico support add their calls below) ----

// People: who can join, the agents a person may have, and the people who left.

/** What GET /api/v2/access also says (backend/access.py view) that AccessView leaves out. */
export interface AccessJoin {
  /** The owner's own company domain, when it is not public mail. */
  home_domain?: string;
  /** "domain": anyone at home_domain may sign in; "admin": only people added here. */
  join?: 'domain' | 'admin';
  member_bot_limit?: number;
}
export type AccessFull = AccessView & AccessJoin;

/** Who may join by signing in (owner): the whole lists, against the revision on screen (PUT /api/v2/access/allow). */
export const setAllowed = (allowed: string[], domains: string[]) => {
  const revision = queryClient.getQueryData<AccessView>(accessQuery.queryKey)?.revision ?? 0;
  return write({
    key: accessQuery.queryKey,
    optimistic: (v: AccessFull | undefined) =>
      v && {
        ...v,
        allowed,
        allowed_domains: domains,
        join: v.home_domain && domains.includes(v.home_domain) ? ('domain' as const) : ('admin' as const),
      },
    send: (idempotencyKey) =>
      api.put('/api/v2/access/allow', { allowed, allowed_domains: domains, expected_revision: revision }, { idempotencyKey }),
  });
};

/** How many active agents one member may have (owners and admins). */
export const setAgentLimit = (limit: number) =>
  write({
    key: accessQuery.queryKey,
    optimistic: (v: AccessFull | undefined) => v && { ...v, member_bot_limit: limit },
    send: (idempotencyKey) => api.put('/api/v2/access/limits', { member_bot_limit: limit }, { idempotencyKey }),
  });

export const restorePerson = (pid: string) =>
  write({
    key: accessQuery.queryKey,
    optimistic: patchPerson(pid, { left: false }),
    send: (idempotencyKey) => api.post(`/api/v2/access/humans/${enc(pid)}`, { left: false }, { idempotencyKey }),
  });

/** Remove a left person with no history, for good (owner; backend/people_remove.py). */
export const forgetPerson = (pid: string) =>
  write({
    key: accessQuery.queryKey,
    wait: true,
    optimistic: (v: AccessView | undefined) => v && { ...v, people: v.people.filter((p) => p.id !== pid) },
    send: (idempotencyKey) => api.delete(`/api/v2/humans/${enc(pid)}`, { idempotencyKey }),
  });

/** Add a person and send no email: the old app's plain Add. */

// Tokens: everyone's, for owners and admins (GET /api/v2/access/tokens), revoked with the same call as your own.

export interface CompanyToken extends PersonalToken {
  human?: string;
  name?: string;
  email?: string;
}

export const allTokensQuery = queryOptions({
  queryKey: ['settings', 'all-tokens'],
  queryFn: ({ signal }) => api.get<{ tokens: CompanyToken[] }>('/api/v2/access/tokens', { signal }),
});

export const revokeAnyToken = (id: string) => {
  const gone = (v: { tokens: PersonalToken[] } | undefined) =>
    v && { ...v, tokens: v.tokens.map((t) => (t.id === id ? { ...t, revoked_at: new Date().toISOString() } : t)) };
  return write({
    key: [allTokensQuery.queryKey, tokensQuery.queryKey],
    optimistic: gone,
    send: (idempotencyKey) => api.post(`/api/v2/me/tokens/${enc(id)}/revoke`, {}, { idempotencyKey }),
  });
};

// Screening: what it caught lately (on GET /api/v2/screening).

export interface ScreeningCaught {
  at?: string;
  action: 'held' | 'warned';
  source?: string | null;
  score?: number | null;
  task?: string | null;
}
export type ScreeningFull = ScreeningView & { recent?: ScreeningCaught[] };

// Tico support signs in for a while, from one of the person's own open requests (backend/support_access.py).

export interface SupportSession {
  id: string;
  ticket_id: string;
  tier: 'app' | 'full';
  started_at: string;
  expires_at: string;
  seconds_left?: number;
  ended_at?: string | null;
  end_reason?: string | null;
}

export interface SupportAccessView {
  available: boolean;
  off_reason?: string;
  active: SupportSession | null;
  last: SupportSession | null;
  sessions: SupportSession[];
  can_start: boolean;
  can_full: boolean;
}

export const supportAccessQuery = queryOptions({
  queryKey: ['settings', 'support-access'],
  queryFn: ({ signal }) => api.get<SupportAccessView>('/api/v2/support/access', { signal }),
});

/** The person's own requests to Tico support: a sign-in is let in against an open one. */
export const supportTicketsQuery = queryOptions({
  queryKey: ['settings', 'support-tickets'],
  queryFn: async ({ signal }) =>
    (await api.get<{ tickets?: { id: string; status: string; created_at: string; pending?: boolean }[] }>('/api/v2/support/tickets', { signal }))
      .tickets ?? [],
});

export interface SupportLogLine {
  created_at: string;
  by: string;
  method: string;
  path: string;
  status: number | string;
}

export const supportLogQuery = (sid: string) =>
  queryOptions({
    queryKey: ['settings', 'support-log', sid],
    queryFn: async ({ signal }) => (await api.get<{ log?: SupportLogLine[] }>(`/api/v2/support/access/${enc(sid)}/log`, { signal })).log ?? [],
  });

export const startSupportAccess = (ticket: string, tier: 'app' | 'full', hours: number) =>
  write({
    key: supportAccessQuery.queryKey,
    optimistic: (v: SupportAccessView | undefined) => v,
    send: (idempotencyKey) => api.post('/api/v2/support/access', { ticket_id: ticket, tier, hours }, { idempotencyKey }),
  });

export const stopSupportAccess = (sid: string) =>
  write({
    key: supportAccessQuery.queryKey,
    optimistic: (v: SupportAccessView | undefined) => v && { ...v, active: null },
    send: (idempotencyKey) => api.post(`/api/v2/support/access/${enc(sid)}/stop`, {}, { idempotencyKey }),
  });

// General: the team icon and repositories.

/** PNG, JPEG or WebP up to 1 MB, sent as the file itself (owner; backend/team_icon.py). */
export const setTeamIcon = (file: Blob) => api.post<{ url: string }>('/api/v2/team/icon', file);

export type Repo = Repository & { archived?: boolean };
export type ProductRepoPreview = ProductRepositoryPreview;
export type RepoList = Omit<RepositoryList, 'repositories'> & { repositories: Repo[] };

export const repositoriesQuery = queryOptions({
  queryKey: ['settings', 'repositories'],
  queryFn: ({ signal }) => api.get<RepoList>('/api/v2/repositories', { signal }),
});

const repoPath = (name: string) => name.split('/').map(enc).join('/');

/** Put a repository in use or take it out, or change its setup command (blank hands it back to tico.json). */
export const setRepo = (name: string, change: { enabled?: boolean; setup_command?: string }) =>
  write({
    key: repositoriesQuery.queryKey,
    optimistic: (v: RepoList | undefined) =>
      v && { ...v, repositories: v.repositories.map((r) => (r.full_name === name ? { ...r, ...change } : r)) },
    send: (idempotencyKey) => api.put(`/api/v2/repositories/${repoPath(name)}`, change, { idempotencyKey }),
  });

export const setNewAgentRepos = (value: 'own' | 'read' | 'all') =>
  write({
    key: repositoriesQuery.queryKey,
    optimistic: (v: RepoList | undefined) => v && { ...v, new_bot_default: value },
    send: (idempotencyKey) => api.put('/api/v2/repositories/settings', { new_bot_default: value }, { idempotencyKey }),
  });

export const refreshRepos = () =>
  write({
    key: repositoriesQuery.queryKey,
    optimistic: (v: RepoList | undefined) => v,
    send: (idempotencyKey) => api.post<RepoList>('/api/v2/repositories/refresh', {}, { idempotencyKey }),
  });

/** What a new private repository would be, before it is made (owner; spends nothing). */
export const previewRepo = (name: string) =>
  api.get<ProductRepositoryPreview>(`/api/v2/github/product-repos/preview?name=${enc(name)}`);

export const createRepo = (p: ProductRepositoryPreview) =>
  api.post<ProductRepositoryResult>('/api/v2/github/product-repos', {
    org: p.org,
    name: p.name,
    visibility: p.visibility,
    auto_init: p.auto_init,
    confirmed: true,
  });

// ---- gap wave: usage filters, pools and task caps; history pages; billing months; Gateway checks ----

/** Spend over a range, narrowed by team (`department`) or model. Not narrowed, it is the range's own read. */
export interface UsageWhere {
  range: UsageRange;
  filter?: Record<string, string>;
}

export const USAGE_FILTERS = ['department', 'model'] as const;

const narrowed = (w: UsageWhere) => Object.values(w.filter ?? {}).some(Boolean);

function whereParams(w: UsageWhere): URLSearchParams {
  const q = new URLSearchParams(rangeDays(w.range));
  for (const k of USAGE_FILTERS) if (w.filter?.[k]) q.set(k, w.filter[k]);
  return q;
}

export const usageWhereQuery = (w: UsageWhere, group: 'bot' | 'day' | 'model') =>
  narrowed(w)
    ? queryOptions({
        queryKey: ['usage', 'where', whereParams(w).toString(), group],
        queryFn: async ({ signal }) => {
          const q = whereParams(w);
          q.set('group', group);
          const u = await api.get<Usage>(`/api/v2/usage?${q}`, { signal });
          return usageView(u);
        },
      })
    : usageQuery(w.range, group);

export const agentUsageWhereQuery = (w: UsageWhere, bot: string) =>
  narrowed(w)
    ? queryOptions({
        queryKey: ['usage', 'where', whereParams(w).toString(), 'agent', bot],
        queryFn: async ({ signal }) => {
          const q = whereParams(w);
          q.set('bot', bot);
          const u = await api.get<Usage>(`/api/v2/usage?${q}`, { signal });
          return { totals: u.totals, tasks: (u.tasks ?? []) as unknown as UsageRow[], models: (u.models ?? []) as unknown as UsageRow[] } satisfies AgentUsage;
        },
      })
    : agentUsageQuery(w.range, bot);

/** What the filter can pick over the span: the teams and each dimension's values (`__unknown__`: not recorded). */
interface UsageChoices {
  departments: string[];
  dimensions: Record<string, string[]>;
  labels: Record<string, Record<string, string>>;
}

/** A combined limit: the whole company's, or a team's (its spend counts every agent under it). */
export interface UsagePool {
  name?: string;
  parent?: string | null;
  monthly_usd: number | null;
  month_spent: number | null;
  /** What it has spent in each window, capped or not: {month: usd, ...}. */
  spent?: Record<string, number>;
  percent: number;
  blocked: string | null;
  may_edit?: boolean;
}

/** An agent's or pool's spend this month, capped or not: `month_spent` is set only under a monthly limit. */
export const monthSpent = (x: { spent?: Record<string, number> | null; month_spent: number | null }) => x.spent?.month ?? x.month_spent ?? 0;

export interface UsagePools {
  company: UsagePool | null;
  groups: Record<string, UsagePool>;
}

/** The limits read's `pools`: null for someone who leads nothing and is no administrator. */
export function poolsOf(v: UsageLimits | undefined): UsagePools | null {
  const p = v?.pools as Partial<UsagePools> | null | undefined;
  return p ? { company: p.company ?? null, groups: p.groups ?? {} } : null;
}

/** A pool's monthly limit in dollars; null takes it off. `scope`: 'company' or a team's group id. */
export const setPoolLimit = (scope: string, monthly: number | null) =>
  write({
    key: limitsQuery.queryKey,
    optimistic: (v: UsageLimits | undefined) => {
      const p = poolsOf(v);
      if (!v || !p) return v;
      const fix = (pool: UsagePool): UsagePool => ({ ...pool, monthly_usd: monthly, percent: monthly ? Math.round((monthSpent(pool) / monthly) * 100) : 0 });
      const group = p.groups[scope];
      const next: UsagePools =
        scope === 'company' ? { ...p, company: p.company && fix(p.company) } : group ? { ...p, groups: { ...p.groups, [scope]: fix(group) } } : p;
      return { ...v, pools: next as unknown as Record<string, unknown> };
    },
    send: (idempotencyKey) =>
      api.put(scope === 'company' ? '/api/v2/usage/pools/company' : `/api/v2/usage/pools/groups/${enc(scope)}`, { monthly_usd: monthly }, { idempotencyKey }),
  });

/** Caps on the tasks one teammate sends another a day (`bot:<slug>` / `person:<id>`), with today's count. */
export const taskCapsQuery = queryOptions({
  queryKey: ['usage', 'task-caps'],
  queryFn: ({ signal }) => api.get<TaskCaps>('/api/v2/task-caps', { signal }),
});

/** null removes the cap; 0 blocks the pair. An agent's own change comes back as an approval a person decides. */
export const setTaskCap = (sender: string, receiver: string, perDay: number | null) =>
  write({
    key: taskCapsQuery.queryKey,
    optimistic: (v: TaskCaps | undefined) => {
      if (!v) return v;
      const same = (c: { sender: string; receiver: string }) => c.sender === sender && c.receiver === receiver;
      if (perDay == null) return { ...v, caps: v.caps.filter((c) => !same(c)) };
      return v.caps.some(same)
        ? { ...v, caps: v.caps.map((c) => (same(c) ? { ...c, per_day: perDay } : c)) }
        : { ...v, caps: [...v.caps, { sender, receiver, per_day: perDay, count: 0, closed: 0, updated_at: '', updated_by: '' }] };
    },
    send: (idempotencyKey) =>
      perDay == null
        ? api.delete(`/api/v2/task-caps?sender=${enc(sender)}&receiver=${enc(receiver)}`, { idempotencyKey })
        : api.put('/api/v2/task-caps', { sender, receiver, per_day: perDay }, { idempotencyKey }),
  });

/** One day's charges. */
export interface BillingBucket {
  period: string;
  total_micros: number;
}

/** The billing read in full: a month's days, its statement, and where credit is added. */
export interface BillingMonth extends BillingView {
  grace_until?: string | null;
  booking_url?: string | null;
  record_only?: boolean;
  usage: BillingBucket[];
  statement: BillingView['statement'] & { credits_by_source?: Record<string, number>; expired_micros?: number | null };
}

/** The month picked on the page ('' is the server's current one). */
export const billingFor = (month = '') =>
  month
    ? queryOptions({
        queryKey: ['settings', 'billing', month],
        queryFn: ({ signal }) => api.get<BillingView>(`/api/v2/billing?month=${enc(month)}`, { signal }),
      })
    : billingQuery;

/** What the owner sees of the Gateway connection besides the key's preview: the last check's word. */
export interface GatewayStatus extends GatewayView {
  source?: string;
  automatic?: boolean;
  state?: string;
  working?: boolean;
  message?: string;
}

// ---- tier 2 gaps (Tasks, Health, Changelog, Contacts, stopped runs, search): this builder's calls below ----

/** One set of rules for what agents may put on a person (backend/ask_ingest.py): Off or On, the open asks before the
    check tightens, and up to ten lines the person wants to be asked about. `own`: theirs, not the team default. */
export interface AskRules {
  mode: 'off' | 'on' | string;
  budget: number;
  criteria: string[];
  own?: boolean;
}

/** GET /api/v2/ask-ingest/settings: the person's own rules, the team default, and whether they may change the default.
    A person's own setting: an agent's read is refused (403). */
export interface AskView {
  settings: AskRules;
  team: AskRules;
  may_edit_team?: boolean;
}

export const askQuery = queryOptions({
  queryKey: ['settings', 'ask'],
  queryFn: ({ signal }) => api.get<AskView>('/api/v2/ask-ingest/settings', { signal }),
});

/** Save the person's own rules, or the team default (`team`). The next ask uses them; nothing on a plate moves. */
export const saveAsk = (rules: { mode: string; budget: number; criteria: string[] }, team: boolean) =>
  write({
    key: askQuery.queryKey,
    optimistic: (v: AskView | undefined) => v && (team ? { ...v, team: { ...v.team, ...rules, own: true } } : { ...v, settings: { ...rules, own: true } }),
    send: (idempotencyKey) => api.put('/api/v2/ask-ingest/settings', { ...rules, team }, { idempotencyKey }),
  });

/** Back to the team default: the person's own rules are removed. */
export const resetAsk = () =>
  write({
    key: askQuery.queryKey,
    optimistic: (v: AskView | undefined) => v && { ...v, settings: { ...v.team, own: false } },
    send: (idempotencyKey) => api.delete('/api/v2/ask-ingest/settings', { idempotencyKey }),
  });

/** An agent with queued work that is not starting, or left in setup (backend/health.py `_stuck`). */
export interface StuckAgent {
  bot: string;
  name: string;
  reason: string;
  why: string;
  oldest?: string | null;
}

export const stuckOf = (h: { stuck?: unknown } | undefined): StuckAgent[] => (Array.isArray(h?.stuck) ? (h.stuck as StuckAgent[]) : []);

/** The changelog a page at a time (60 entries), from `offset`. */
export const changelogPage = (offset: number) =>
  offset === 0
    ? changelogQuery
    : queryOptions({
        queryKey: ['settings', 'changelog', 'page', offset],
        queryFn: ({ signal }) => api.get<ChangelogList>(`/api/v2/changelog?kind=product&limit=60&offset=${offset}`, { signal }),
      });

/** Acknowledge product entries for this person. The list is not read again: what was New stays marked for this visit. */
export const markChangelogRead = (ids: string[]) => api.post<{ unread_count: number }>('/api/v2/changelog/read', { ids });

// ---------------------------------------------------------------- company name (GET/PUT /api/v2/setup, owner)

/** What GET /api/v2/setup answers that PUT takes back (backend/onboarding.py view/save; the rest is read-only). */
interface SetupRecord {
  names: Record<string, string>;
  answers: Record<string, unknown>;
  selected: Record<string, unknown>;
}

/** Rename the company. The server has no narrower route: PUT /api/v2/setup rewrites the whole setup record (names,
    answers, chosen agents), so the record is read first and sent back with only the name changed. `owner_name` goes
    blank: on that PUT a name there renames the owner on the roster (blank leaves it), and the record's copy is from
    setup, older than any rename since. GET /api/v2/config then reads the new name. */
export const renameCompany = (name: string) =>
  write({
    key: configQuery.queryKey,
    optimistic: (v) => v && { ...v, company_name: name },
    send: async (idempotencyKey) => {
      const r = await api.get<SetupRecord>('/api/v2/setup');
      const names = { ...r.names, company_name: name, owner_name: '' };
      return api.put('/api/v2/setup', { names, answers: r.answers, selected: r.selected }, { idempotencyKey });
    },
  });
