import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';
import { api, type ApiPath } from '@/api/client';
import type { AccessAudience, EmailSendList, Group, InboundInboxes, MailboxDetail, SlackLink, SlackLinkList, WebsiteAccountAccessSet } from '@/api/schema';
import { QUIET_STALE_MS, queryClient } from '@/data';
import { apiToolQuery, inboxesQuery, mailboxQuery, toolsQuery, websiteToolQuery, type Inbox } from './queries';

/* The Tools calls the gap rows need (s787 s788 s866 s865 s926 s867 s908 s909): stored keys no tool holds, a declared
   tool's own page, a mailbox's access and mail, who reads a Tico address, a website's accounts, who sees a tool, the
   teams Tools narrows by, and Slack's linked threads. A secret is only ever sent from here: nothing below caches one. */

const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS } as const;
/** These keys and the Tools list, read again once the server agrees. */
const reread = (...keys: (readonly unknown[])[]) =>
  Promise.all([...keys, toolsQuery.queryKey].map((queryKey) => queryClient.invalidateQueries({ queryKey })));

// ---------------------------------------------------------------- stored keys (the vault)

export interface KeyGrant {
  id: string;
  /** 'human:<id>', 'bot:<id>', 'computers' or 'computer:<id>'. */
  subject: string;
  granted_by?: string;
  parent_id?: string | null;
  created_at?: string;
  can_revoke?: boolean;
}
/** A stored credential as GET /api/v2/credentials lists it (backend/credentials.py): never its value. */
export interface StoredKey {
  id: string;
  name: string;
  username: string;
  kind: string;
  env: string;
  preview?: string;
  source: string;
  revision: number;
  created_at?: string;
  updated_at?: string;
  stored: boolean;
  can_reveal?: boolean;
  can_remove?: boolean;
  /** The API tool that holds it as a key; null for a key no tool holds. */
  tool_id?: string | null;
  last_used_at?: string | null;
  last_used_by?: string;
  remove_only?: boolean;
  grants: KeyGrant[];
}
export interface Vault {
  credentials: StoredKey[];
  can_manage: boolean;
  people: { id: string; name: string; email?: string }[];
  bots: { id: string; slug?: string; name: string }[];
}

/** Whoever may open the vault; anyone else gets a 403 (the page then says so, and Tools lists no keys). */
export const vaultQuery = queryOptions({
  queryKey: ['tools', 'credentials'],
  queryFn: () => api.get<Vault>('/api/v2/credentials'),
  retry: false,
  ...quiet,
});
const vaultChanged = () => reread(vaultQuery.queryKey);

/** A new value for a stored key. The value goes once and never comes back; the rest of the key is sent as it was. */
export function rotateKey(key: StoredKey, value: string) {
  const { name, username, kind, env, source, revision } = key;
  return api.post<unknown>(`/api/v2/credentials/${enc(key.id)}`, { name, username, kind, env, source, secret: value, expected_revision: revision }).then(vaultChanged);
}
/** A key a declared tool names but the vault does not hold yet. */
export const storeKey = (body: { name: string; kind: string; env: string; source: string; secret: string }) =>
  api.post<{ id: string }>('/api/v2/credentials', { ...body, username: '' }).then(async (r) => {
    await vaultChanged();
    return r;
  });
export const grantKey = (key: string, subject: string) => api.post<unknown>(`/api/v2/credentials/${enc(key)}/grants`, { subject }).then(vaultChanged);
export const revokeKey = (key: string, grant: string) => api.post<unknown>(`/api/v2/credentials/${enc(key)}/grants/${enc(grant)}/revoke`, {}).then(vaultChanged);
export const deleteKey = (key: string) => api.delete<unknown>(`/api/v2/credentials/${enc(key)}`).then(vaultChanged);

// ---------------------------------------------------------------- a declared tool (GET /api/v2/tools)

export interface ServiceConnection {
  bot: string;
  status?: string;
  write?: 'never' | 'approval' | 'allowed';
  problem?: string;
  env?: string;
}
export interface Service {
  service: string;
  title: string;
  kind: string;
  summary: string;
  credentials: string[];
  aliases?: string[];
  writes: 'never' | 'approval' | 'allowed';
  status?: string;
  bots?: string[];
  connections?: ServiceConnection[];
}
export const servicesQuery = queryOptions({
  queryKey: ['tools', 'services'],
  queryFn: () => api.get<{ integrations?: Service[] }>('/api/v2/tools').then((r) => r.integrations ?? []),
  ...quiet,
});
/** What its page says (`body`, markdown). */
export const servicePageQuery = (service: string) =>
  queryOptions({
    queryKey: ['tools', 'service', service],
    queryFn: () => api.get<{ title?: string; summary?: string; body?: string }>(`/api/v2/tools/${enc(service)}`),
    ...quiet,
  });

/** Variables a tool names (ACME_API_KEY), from its page and from the entries agents declare. */
export function envsOf(s: Service): string[] {
  const out = new Set<string>();
  for (const t of s.credentials) for (const m of t.match(/\b[A-Z](?:[A-Z0-9_]|<[A-Z]+>){2,}/g) ?? []) out.add(m);
  for (const c of s.connections ?? []) if (c.env) out.add(c.env);
  return [...out].filter((e) => e.includes('_') && !e.includes('<'));
}
/** The stored keys a declared tool uses: by variable, by name, or by where they came from (ui/app/integrations.ts). */
export function keysOf(s: Service, keys: StoredKey[]): StoredKey[] {
  const envs = new Set(envsOf(s));
  const names = new Set([s.service, s.title, ...(s.aliases ?? [])].map((x) => x.toLowerCase()));
  return keys.filter((k) => {
    if (k.env && envs.has(k.env)) return true;
    const source = k.source.toLowerCase();
    return names.has(k.name.toLowerCase()) || names.has(source) || source.startsWith(`${s.service}:`) || source.startsWith(`${s.service}/`);
  });
}

// ---------------------------------------------------------------- teams (Tools' Who has access)

export const teamsQuery = queryOptions({
  queryKey: ['tools', 'groups'],
  queryFn: () => api.get<Group[] | { groups?: Group[] }>('/api/v2/groups').then((r) => (Array.isArray(r) ? r : (r.groups ?? []))),
  ...quiet,
});

// ---------------------------------------------------------------- a mailbox

/** Each mailbox write answers the mailbox as it is now. */
const mailboxIs = (mailbox: string) => (d: MailboxDetail) => {
  queryClient.setQueryData(mailboxQuery(mailbox).queryKey, d);
  return reread();
};
const mb = (mailbox: string): ApiPath => `/api/v2/mailboxes/${enc(mailbox)}`;

export const addMailboxAccess = (mailbox: string, grantee_type: 'person' | 'bot', grantee_id: string, level: 'read' | 'read_write') =>
  api.post<MailboxDetail>(`${mb(mailbox)}/accesses`, { grantee_type, grantee_id, level }).then(mailboxIs(mailbox));
export const setMailboxLevel = (mailbox: string, access: string, level: 'read' | 'read_write') =>
  api.patch<MailboxDetail>(`${mb(mailbox)}/accesses/${enc(access)}`, { level }).then(mailboxIs(mailbox));
export const removeMailboxAccess = (mailbox: string, access: string) =>
  api.delete<MailboxDetail>(`${mb(mailbox)}/accesses/${enc(access)}`).then(mailboxIs(mailbox));
export const shareMailbox = (mailbox: string) => api.post<MailboxDetail>(`${mb(mailbox)}/share`, {}).then(mailboxIs(mailbox));
export const stopMailbox = (mailbox: string) => api.delete<unknown>(mb(mailbox)).then(() => reread(mailboxQuery(mailbox).queryKey));

/** Google asked first when the company's key reads it (an admin's check); then the mailbox is read again. */
export async function checkMailbox(mailbox: string): Promise<{ ok: boolean; text: string }> {
  const had = queryClient.getQueryData(mailboxQuery(mailbox).queryKey) ?? (await queryClient.fetchQuery(mailboxQuery(mailbox)));
  let said: { ok?: boolean; message?: string } | null = null;
  if (had.can_manage && had.mailbox.connection === 'workspace')
    said = await api.post<{ ok?: boolean; message?: string }>('/api/v2/google/workspace/check', { mailbox: had.mailbox.address });
  const d = await queryClient.fetchQuery({ ...mailboxQuery(mailbox), staleTime: 0 });
  if (said) return { ok: !!said.ok, text: said.message || (said.ok ? 'Connected' : 'Not connected') };
  const failing = !!d.mailbox.sync && !!d.mailbox.sync_error;
  return { ok: !failing, text: failing ? d.mailbox.sync_error : 'Connected' };
}

export interface MailLine {
  id?: string;
  thread_id?: string | null;
  subject?: string | null;
  from_header?: string | null;
  from_addr?: string | null;
  epoch?: number | null;
}
/** Its newest mail, from Gmail, for whoever may read it. */
export const mailboxMailQuery = (address: string) =>
  queryOptions({
    queryKey: ['tools', 'mailbox-mail', address],
    queryFn: () => api.get<{ messages?: MailLine[] }>(`/api/v2/mail/messages?mailbox=${enc(address)}&limit=8`).then((r) => r.messages ?? []),
    ...quiet,
  });
/** What was sent from it, newest first, a page at a time. */
export const mailboxSendsQuery = (address: string) =>
  infiniteQueryOptions({
    queryKey: ['tools', 'mailbox-sends', address],
    queryFn: ({ pageParam }) => api.get<EmailSendList>(`/api/v2/email/sends?mailbox=${enc(address)}${pageParam ? `&cursor=${enc(pageParam)}` : ''}`),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor || undefined,
    ...quiet,
  });

// ---------------------------------------------------------------- a Tico address or number

/** Who reads it: the company, or one team (and the teams under it). */
export const setInboxReaders = (inbox: string, audience: 'company' | 'team', group: string | null) =>
  api
    .put<unknown>(`/api/v2/inbound/inboxes/${enc(inbox)}/audience`, audience === 'team' ? { audience, group_id: group } : { audience })
    .then(() => reread(inboxesQuery.queryKey));

/** The inbound connection read again: whether it is set up, its last sync and any error. */
export async function checkInboxes(): Promise<{ ok: boolean; text: string }> {
  const r = await api.get<InboundInboxes>('/api/v2/inbound/inboxes');
  queryClient.setQueryData(inboxesQuery.queryKey, r.inboxes as unknown as Inbox[]);
  if (!r.configured) return { ok: false, text: 'Not set up' };
  return r.error ? { ok: false, text: r.error } : { ok: true, text: 'Connected' };
}

// ---------------------------------------------------------------- a website

const site = (tool: string): ApiPath => `/api/v2/website-tools/${enc(tool)}`;
export const removeAccount = (tool: string, account: string) =>
  api.delete<unknown>(`${site(tool)}/accounts/${enc(account)}`).then(() => reread(websiteToolQuery(tool).queryKey));
export const deleteWebsite = (tool: string) => api.delete<unknown>(site(tool)).then(() => reread());
/** One shared account given to a person, an agent, a team, or every agent (`bots`), or taken away. */
export const setAccountAccess = (tool: string, account: string, subject: string, on: boolean) =>
  api.put<WebsiteAccountAccessSet>(`${site(tool)}/accounts/${enc(account)}/access/${enc(subject)}`, { on }).then(() => reread(websiteToolQuery(tool).queryKey));

// ---------------------------------------------------------------- who sees an API tool or a website

/** Every agent reads the tool with no grant while it is on. */
export const setEveryoneReads = (tool: string, on: boolean) =>
  api.patch<unknown>(`/api/v2/api-tools/${enc(tool)}`, { everyone_reads: on }).then(() => reread(apiToolQuery(tool).queryKey));

export type SeenList = Required<Pick<AccessAudience, 'people' | 'teams' | 'bots'>>;
/** null: Everyone. A list: Only listed (the owner, the list and whoever can use it). */
export const setSeenBy = (kind: 'api' | 'website', tool: string, audience: SeenList | null) =>
  api
    .patch<unknown>(kind === 'api' ? `/api/v2/api-tools/${enc(tool)}` : site(tool), { audience })
    .then(() => reread(kind === 'api' ? apiToolQuery(tool).queryKey : websiteToolQuery(tool).queryKey));

// ---------------------------------------------------------------- Slack's linked threads

/** Admins only: everyone else gets a 403, and the section is left out. */
export const slackLinksQuery = queryOptions({
  queryKey: ['tools', 'slack-links'],
  queryFn: () => api.get<SlackLinkList>('/api/v2/slack/links?limit=50'),
  retry: false,
  ...quiet,
});
export interface ThreadPick {
  id: string;
  title?: string | null;
  name?: string | null;
  archived_at?: string | null;
}
/** The threads a channel can be linked to. */
export const threadPicksQuery = queryOptions({
  queryKey: ['tools', 'slack-threads'],
  queryFn: () =>
    api.get<{ threads?: ThreadPick[]; attention?: ThreadPick[] }>('/api/v2/threads?limit=50').then((r) => {
      const seen = new Set<string>();
      return [...(r.attention ?? []), ...(r.threads ?? [])].filter((t) => !t.archived_at && !seen.has(t.id) && !!seen.add(t.id));
    }),
  ...quiet,
});
const linksChanged = () => queryClient.invalidateQueries({ queryKey: slackLinksQuery.queryKey });
export const linkSlack = (thread_id: string, channel: string, history: boolean) =>
  api.post<SlackLink>('/api/v2/slack/links', { thread_id, channel, history }).then(linksChanged);
export const unlinkSlack = (link: string) => api.delete<SlackLink>(`/api/v2/slack/links/${enc(link)}`).then(linksChanged);
export const resumeSlack = (link: string) => api.post<SlackLink>(`/api/v2/slack/links/${enc(link)}/resume`, {}).then(linksChanged);

// ---------------------------------------------------------------- what the screen holds, for an action's `can`

type Client = typeof queryClient;
/** From the cache only (the server still decides): what a tighten-only check compares with. */
export const was = {
  vault: (qc: Client) => qc.getQueryData(vaultQuery.queryKey),
  mailbox: (qc: Client, id: string) => qc.getQueryData(mailboxQuery(id).queryKey),
  inbox: (qc: Client, id: string) => qc.getQueryData<Inbox[]>(inboxesQuery.queryKey)?.find((b) => b.inbox_id === id) as (Inbox & { group_id?: string | null }) | undefined,
  api: (qc: Client, id: string) => qc.getQueryData(apiToolQuery(id).queryKey),
  website: (qc: Client, id: string) => qc.getQueryData(websiteToolQuery(id).queryKey),
};
