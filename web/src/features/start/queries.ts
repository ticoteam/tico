import { queryOptions, type QueryClient } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import type {
  GetConfigResponse,
  GitHubApp,
  GitHubAppDisconnected,
  GitHubAppFinished,
  GitHubAppSetup,
  GitHubAuthorizeLink,
  GitHubLostKeyReplaced,
} from '@/api/schema';
import { configQuery, keys, meQuery, queryClient, quietRoot, write } from '@/data';
import { addSubscription, subscriptionsQuery } from '@/features/settings/routing';

/* ---------------------------------------------------------------- where a person lands */

/** GET /api/v2/config also says whether the owner still owes the first run (backend/onboarding.py config_view). */
export type StartConfig = GetConfigResponse & { onboarding_needed?: boolean };

export const needsSetup = (config: GetConfigResponse | undefined | null): boolean => !!(config as StartConfig | null | undefined)?.onboarding_needed;

/** Signed in: Tasks, with the first run open over it while setup is owed. Not signed in: null (the root's gate answers). */
export async function landing(qc: QueryClient): Promise<{ setup: boolean } | null> {
  try {
    await qc.ensureQueryData(meQuery);
  } catch {
    return null;
  }
  const config = await qc.ensureQueryData(configQuery).catch(() => null);
  return { setup: needsSetup(config) };
}

/** Why the app cannot open, from what GET /api/v2/me answered. */
export type StartScreenKind = 'signin' | 'signed-out' | 'refused' | 'down' | 'timed-out';

export function screenFor(error: unknown): StartScreenKind | null {
  if (!error) return null;
  if (!(error instanceof ApiError)) return 'down';
  if (error.status === 401) return error.signIn ? 'signin' : 'signed-out';
  if (error.status === 403) return 'refused';
  return 'down';
}

/** Read the person and the directory again (Try again on a boot failure); what failed stays shown while it reads. */
export function retryStart(): void {
  void queryClient.refetchQueries({ queryKey: keys.me() });
  void queryClient.refetchQueries({ queryKey: keys.directory() });
}

/* ---------------------------------------------------------------- first run (GET/PUT /api/v2/setup) */

export interface SetupNames {
  company_name: string;
  app_name: string;
  assistant_name: string;
  owner_name: string;
  team_domain: string;
  website: string;
}
export interface SetupJob {
  key: string;
  label: string;
  seat: string;
  team: string;
}
export interface SetupTeam {
  team: string;
  on: boolean;
  locked?: boolean;
}
export interface SetupRecord {
  names: SetupNames;
  answers: Record<string, unknown> & { first_job?: string; teams_off?: string[] };
  selected: Record<string, unknown>;
  completed: string | null;
  needed: boolean;
  default_chart?: SetupTeam[];
  first_jobs?: { choices: SetupJob[]; picked: string };
}

const SETUP = ['setup'] as const;

/** The setup record, kept as it was left: closing and reopening the first run resumes it. Refetched on the `setup` signal. */
export const setupQuery = queryOptions({
  queryKey: SETUP,
  queryFn: ({ signal }) => api.get<SetupRecord>('/api/v2/setup', { signal }),
});

/** A PUT replaces names, answers and choices whole, so each save sends the record as held with its change. */
const draftOf = (r: SetupRecord) => ({ names: r.names, answers: r.answers, selected: r.selected });

async function held(): Promise<SetupRecord> {
  return queryClient.getQueryData<SetupRecord>(SETUP) ?? queryClient.fetchQuery(setupQuery);
}

export async function saveCompanyName(name: string): Promise<SetupRecord> {
  const record = await held();
  const next: SetupRecord = { ...record, names: { ...record.names, company_name: name } };
  return write<SetupRecord>({
    key: SETUP,
    optimistic: () => next,
    send: (idem) => api.put<SetupRecord>('/api/v2/setup', draftOf(next), { idempotencyKey: idem }),
    settle: (current, answer) => (answer && typeof answer === 'object' && 'names' in answer ? answer : current),
  });
}

/**
 * The default chart with only the first job's seat live (welcome.ts onbCollect), then Create. `job` '' keeps every
 * live seat of the chart. Names and setup state change for the whole app, so config and the directory are read again.
 */
export async function finishSetup(job: string): Promise<void> {
  const record = await held();
  const team = record.first_jobs?.choices.find((c) => c.key === job)?.team;
  const teams_off = (record.default_chart ?? []).filter((t) => !t.on && !t.locked && t.team !== team).map((t) => t.team);
  const next: SetupRecord = {
    ...record,
    answers: { ...record.answers, departments: [], briefings: {}, first_job: job, teams_off },
    selected: {},
  };
  await write<SetupRecord>({
    key: SETUP,
    optimistic: () => next,
    send: (idem) => api.put<SetupRecord>('/api/v2/setup', draftOf(next), { idempotencyKey: idem }),
  });
  await api.post('/api/v2/setup/complete', {});
  // The new team's agents start on their first work; a ping that fails leaves them to start on their own.
  await api.post('/api/v2/flow/ping', { scope: 'setup' }).catch(() => undefined);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: SETUP }),
    queryClient.invalidateQueries({ queryKey: configQuery.queryKey }),
    queryClient.invalidateQueries({ queryKey: keys.directory() }),
  ]);
}

export type KeyKind = 'anthropic_key' | 'openai_key';

/** A pasted API key goes to the same list as a connected plan. */
export async function saveKey(kind: KeyKind, secret: string): Promise<void> {
  await addSubscription(kind, secret);
  await queryClient.invalidateQueries({ queryKey: subscriptionsQuery.queryKey });
}

/* ---------------------------------------------------------------- GitHub (backend/github_app.py, docs/github-app.md) */

// No topic carries the App's state: read again after the owner's own changes and on a reconnect.
quietRoot('github');

/** The company's GitHub App: connected or not, which account, what it may do. The owner only. */
export const githubQuery = queryOptions({
  queryKey: ['github', 'app'] as const,
  queryFn: ({ signal }) => api.get<GitHubApp>('/api/v2/github/app', { signal }),
});

const rereadGithub = () =>
  Promise.all([queryClient.invalidateQueries({ queryKey: ['github'] }), queryClient.invalidateQueries({ queryKey: ['tools'] })]);

/** What GitHub makes the App from, with its single-use state. Never cached: the state stays with the press that asked. */
export function githubSetup(org: string, administration: boolean): Promise<GitHubAppSetup> {
  const q = new URLSearchParams({ administration: String(administration) });
  if (org) q.set('org', org);
  return api.get<GitHubAppSetup>(`/api/v2/github/app/manifest?${q}`);
}

/** The key that seals the App's secrets is lost: drop what it sealed (the App and directory sync keys). */
export const replaceGithubKey = () => api.post<GitHubLostKeyReplaced>('/api/v2/github/app/replace-lost-key', {});

/** GitHub came back with no session and its code waits on the server: exchange it. Answers GitHub's install page. */
export async function finishGithub(state: string): Promise<GitHubAppFinished> {
  const done = await api.post<GitHubAppFinished>('/api/v2/github/app/finish', { state });
  await rereadGithub();
  return done;
}

/** After accepting a permission on GitHub: GitHub is asked again, and the answer replaces the state held. */
export async function checkGithub(): Promise<GitHubApp> {
  const app = await api.post<GitHubApp>('/api/v2/github/app/check-permissions', {});
  queryClient.setQueryData(githubQuery.queryKey, app);
  return app;
}

/** GitHub's page where the owner lets Tico create repositories in a personal account. */
export const githubAuthorizeLink = () => api.get<GitHubAuthorizeLink>('/api/v2/github/app/authorize-link');

/** Forget the App here; it stays on GitHub until deleted there. */
export async function disconnectGithub(): Promise<GitHubAppDisconnected> {
  const done = await api.post<GitHubAppDisconnected>('/api/v2/github/app/disconnect', {});
  await rereadGithub();
  return done;
}
