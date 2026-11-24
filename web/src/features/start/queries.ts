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
import { ticoRoomQuery } from '@/conversation';
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
export interface SetupTeam {
  team: string;
  on: boolean;
  locked?: boolean;
}
/** One person the setup file names (backend/company_import.py `apply`): `email` only when the visitor typed it on the
    setup page (format 0.4), `invited_at` once Invite sent theirs. */
export interface FilePerson {
  id: string;
  name: string;
  title?: string;
  email?: string;
  person?: string;
  invited_at?: string | null;
}
/** What a setup file left (backend/company_import.py `apply`). `drafts` (format 0.4): how many of the file's drafts
    wait on the owner in Needs you, were approved, skipped, or had no agent. */
export interface FileImport {
  fingerprint: string;
  people?: FilePerson[];
  drafts?: { waiting?: number; approved?: number; skipped?: number; unowned?: number };
}
export interface SetupRecord {
  names: SetupNames;
  answers: Record<string, unknown> & { first_job?: string; teams_off?: string[] };
  selected: Record<string, unknown>;
  completed: string | null;
  needed: boolean;
  /** A setup file made the company; its owner still goes live here (backend/onboarding.py `needed`). */
  live_owed?: boolean;
  company_import?: FileImport | null;
  default_chart?: SetupTeam[];
}

const SETUP = ['setup'] as const;

/** The setup record. Refetched on the `setup` signal. */
export const setupQuery = queryOptions({
  queryKey: SETUP,
  queryFn: ({ signal }) => api.get<SetupRecord>('/api/v2/setup', { signal }),
});

/** A PUT replaces names, answers and choices whole, so each save sends the record as held with its change. */
const draftOf = (r: SetupRecord) => ({ names: r.names, answers: r.answers, selected: r.selected });

/** The people from the setup file who have an email and no invite yet: Tico offers to invite them. */
export const invitees = (record: SetupRecord | undefined): FilePerson[] =>
  (record?.company_import?.people ?? []).filter((p) => !!p.email?.trim() && !p.invited_at);

/** Invite one person the setup file names, with the email they gave (backend/invites.py sends Tico's sign-in mail). */
export async function inviteFromFile(person: FilePerson): Promise<void> {
  const record = await api.post<SetupRecord>(
    `/api/v2/setup/company/people/${encodeURIComponent(person.id)}/invite`,
    { email: person.email ?? '' },
  );
  queryClient.setQueryData(SETUP, record);
}

/**
 * The owner's finish. A company a setup file made already has its team: the server only marks it live, and its bots
 * are pinged for the setup questions they still have. A company set up here gets the chart setup offers first (every
 * seat Recruiting) and the owner's "Set up my company." to Tico in the same write; Tico then sets the rest up with
 * the owner in its chat, so no bot is pinged. Names, setup state and Tico change for the whole app, so they are read again.
 */
export async function finishSetup(): Promise<{ fromFile: boolean }> {
  const record = await queryClient.fetchQuery(setupQuery);
  const fromFile = !!record.company_import;
  // A finish whose answer was lost already completed it: the starting team is saved once.
  if (!record.completed) {
    // No seat starts on a guess: Tico asks what winning looks like first, then hires for it (its set-up-the-company skill).
    const teams_off = (record.default_chart ?? []).filter((t) => !t.on && !t.locked).map((t) => t.team);
    const next: SetupRecord = { ...record, answers: { ...record.answers, departments: [], briefings: {}, first_job: 'none', teams_off }, selected: {} };
    await write<SetupRecord>({
      key: SETUP,
      optimistic: () => next,
      send: (idem) => api.put<SetupRecord>('/api/v2/setup', draftOf(next), { idempotencyKey: idem }),
    });
  }
  // One set up here hands the rest to Tico in the same write (backend/onboarding.py `complete`, hand_off): a reload or a
  // lost answer cannot leave it live with no one asked.
  await api.post('/api/v2/setup/complete', fromFile ? {} : { hand_off: true });
  // A ping that fails leaves the bots to ask on their own.
  if (fromFile) await api.post('/api/v2/flow/ping', { scope: 'setup' }).catch(() => undefined);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: SETUP }),
    queryClient.invalidateQueries({ queryKey: configQuery.queryKey }),
    queryClient.invalidateQueries({ queryKey: keys.directory() }),
    // Finishing turns Tico on (backend/onboarding.py `_activate_bootstrap`): its power and room are read again.
    queryClient.invalidateQueries({ queryKey: ['tico'] }),
    queryClient.invalidateQueries({ queryKey: ticoRoomQuery.queryKey }),
  ]);
  return { fromFile };
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
