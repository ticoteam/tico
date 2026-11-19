import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  ApiTool,
  BotFile,
  DocSourceList,
  ExternalDocResult,
  FileLibrary,
  InboundInboxes,
  InboundMessages,
  InternalDoc,
  InternalDocList,
  InternalDocResult,
  InternalDocRow,
  InternalDocVersionResult,
  InternalDocVersions,
  LetterOutPage,
  LibraryFile,
  MailboxDetail,
  SourceDocumentList,
  ToolIndex,
  ToolIndexRow,
  ToolVendor,
  WebsiteAccountCreate,
  WebsiteAccountUpdate,
  WebsiteTool,
  WebsiteToolCreate,
  WebsiteToolCreated,
} from '@/api/schema';
import { QUIET_STALE_MS, queryClient, quietRoot, write } from '@/data';

/* Every Library call. Docs, files, skills, tools and contacts have no live topic (backend/events.py): they are read
   again after the person's own writes, on reconnect, and when a page opens on data older than a minute. Letters have
   the `letters` topic; the Tools page follows it while a mailing address is open. */
quietRoot('docs');
quietRoot('files');
quietRoot('skills');
quietRoot('tools');
quietRoot('contacts');

/** Each page's owner (the agent its chat opens), as an actor id. Same key and shape the settings feature reads. */
export const pageOwnersQuery = queryOptions({
  queryKey: ['settings', 'page-owners'],
  queryFn: async ({ signal }) => (await api.get<{ pages?: Record<string, { bot: string | null }> }>('/api/v2/page-owners', { signal })).pages ?? {},
  staleTime: QUIET_STALE_MS,
});

const q = (o: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};
const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS } as const;

// ---------------------------------------------------------------- docs

export type DocRow = InternalDocRow;
export type Doc = InternalDoc;

export const docsListQuery = queryOptions({
  queryKey: ['docs', 'list'],
  queryFn: () => api.get<InternalDocList>('/api/v2/docs?limit=200'),
  ...quiet,
});

export const docQuery = (id: string) =>
  queryOptions({
    queryKey: ['docs', 'detail', id],
    queryFn: () => api.get<InternalDocResult>(`/api/v2/docs/${enc(id)}`).then((r) => r.doc),
    ...quiet,
  });

export const docVersionsQuery = (id: string) =>
  queryOptions({
    queryKey: ['docs', 'versions', id],
    queryFn: () => api.get<InternalDocVersions>(`/api/v2/docs/${enc(id)}/versions`).then((r) => r.versions),
    ...quiet,
  });

export const docVersionQuery = (id: string, n: number) =>
  queryOptions({
    queryKey: ['docs', 'version', id, n],
    queryFn: () => api.get<InternalDocVersionResult>(`/api/v2/docs/${enc(id)}/versions/${n}`).then((r) => r.version),
    enabled: n > 0,
    // a past version never changes
    staleTime: Infinity,
  });

export const sourcesQuery = queryOptions({
  queryKey: ['docs', 'sources'],
  queryFn: () => api.get<DocSourceList>('/api/v2/doc-sources?limit=100'),
  ...quiet,
});

export const sourceDocsQuery = (id: string) =>
  queryOptions({
    queryKey: ['docs', 'source', id],
    queryFn: () => api.get<SourceDocumentList>(`/api/v2/doc-sources/${enc(id)}/documents?limit=200`),
    ...quiet,
  });

export const externalDocQuery = (id: string) =>
  queryOptions({
    queryKey: ['docs', 'external', id],
    queryFn: () => api.get<ExternalDocResult>(`/api/v2/docs/external/${enc(id)}`).then((r) => r.doc),
    ...quiet,
  });

export function createDoc(input: { title: string; body: string; path?: string }) {
  const tmp = `tmp:${Date.now()}`;
  return write<InternalDocResult>({
    key: docsListQuery.queryKey,
    optimistic: (cur: InternalDocList | undefined) =>
      cur && {
        ...cur,
        docs: [
          { id: tmp, locked: false, path: input.path ?? '', title: input.title, updated_at: new Date().toISOString(), updated_by_id: '', updated_by_name: '', version: 1 },
          ...cur.docs,
        ],
      },
    send: (idempotencyKey) => api.post<InternalDocResult>('/api/v2/docs', { title: input.title, body: input.body, path: input.path || null }, { idempotencyKey }),
    settle: (cur: InternalDocList | undefined, answer) =>
      cur && { ...cur, docs: cur.docs.map((d) => (d.id === tmp ? { ...d, id: answer.doc.id, version: answer.doc.version } : d)) },
  });
}

/** Save a doc's text or title. `version` is the one the person was editing: the server refuses a stale one (409). */
export function saveDoc(id: string, change: { version: number; body?: string; title?: string }) {
  const at = new Date().toISOString();
  // The open doc and its row in the list both change at once; a list holds `docs`, a doc does not.
  const show = (cur: Doc | InternalDocList | undefined) => {
    if (!cur) return cur;
    if ('docs' in cur) return { ...cur, docs: cur.docs.map((d) => (d.id === id ? { ...d, title: change.title ?? d.title, updated_at: at } : d)) };
    return { ...cur, ...change, version: change.version + 1, updated_at: at };
  };
  return write<InternalDocResult>({
    key: [['docs', 'detail', id], docsListQuery.queryKey],
    optimistic: show,
    send: (idempotencyKey) => api.patch<InternalDocResult>(`/api/v2/docs/${enc(id)}`, change, { idempotencyKey }),
    settle: (cur: Doc | InternalDocList | undefined, answer) => (cur && 'docs' in cur ? cur : answer.doc),
  });
}

export function restoreDoc(id: string, version: number) {
  return write<InternalDocResult>({
    key: ['docs', 'detail', id],
    optimistic: (cur: Doc | undefined) => cur,
    send: (idempotencyKey) => api.post<InternalDocResult>(`/api/v2/docs/${enc(id)}/restore`, { version }, { idempotencyKey }),
    settle: (_cur: Doc | undefined, answer) => answer.doc,
  });
}

export function refreshSource(id: string) {
  return write<unknown>({
    key: sourcesQuery.queryKey,
    optimistic: (cur: DocSourceList | undefined) =>
      cur && { ...cur, sources: cur.sources.map((s) => (s.id === id ? { ...s, status: 'updating' as const } : s)) },
    send: (idempotencyKey) => api.post(`/api/v2/doc-sources/${enc(id)}/refresh`, {}, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- files

export type FileRow = LibraryFile;
export interface FileVersion {
  version: number;
  current: boolean;
  name: string | null;
  size: number | null;
  mime: string | null;
  actor: string | null;
  created_at: string;
  url: string;
}

export const filesQuery = (kind: string) =>
  infiniteQueryOptions({
    queryKey: ['files', 'list', kind],
    queryFn: ({ pageParam }) => api.get<FileLibrary>(`/api/v2/files${q({ limit: 100, kind, cursor: pageParam })}`),
    initialPageParam: '',
    getNextPageParam: (last) => (last.has_more && last.next_cursor ? last.next_cursor : undefined),
    ...quiet,
  });

export const fileSearchQuery = (text: string) =>
  queryOptions({
    queryKey: ['files', 'search', text],
    queryFn: () => api.get<{ results: Array<{ id: string; title: string }> }>(`/api/v2/files/search${q({ q: text, limit: 50 })}`),
    enabled: text.trim().length > 1,
    ...quiet,
  });

export const fileQuery = (id: string) =>
  queryOptions({
    queryKey: ['files', 'info', id],
    queryFn: () => api.get<{ file: BotFile }>(`/api/v2/files/${enc(id)}/info`).then((r) => r.file),
    ...quiet,
  });

export const fileVersionsQuery = (id: string) =>
  queryOptions({
    queryKey: ['files', 'versions', id],
    queryFn: () => api.get<{ versions: FileVersion[] }>(`/api/v2/files/${enc(id)}/versions`).then((r) => r.versions),
    ...quiet,
  });

/** A text file's words, for the preview. */
export const fileTextQuery = (id: string, version: number) =>
  queryOptions({
    queryKey: ['files', 'text', id, version],
    queryFn: () => api.get<string>(`/api/v2/files/${enc(id)}/versions/${version}`).then((t) => (typeof t === 'string' ? t : JSON.stringify(t, null, 2))),
    staleTime: Infinity,
  });

export const fileUrl = (id: string, version?: number) => (version ? `/api/v2/files/${enc(id)}/versions/${version}` : `/api/v2/files/${enc(id)}`);

/** A person adds a file to a task (the server takes a person's upload only there). */
export function uploadFile(task: string, file: File) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  return write<unknown>({
    key: ['files', 'list'],
    optimistic: (cur: unknown) => cur,
    send: (idempotencyKey) => api.post(`/api/v2/tasks/${enc(task)}/files`, fd, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- skills

export interface SkillActor {
  type: 'Bot' | 'Human';
  id: string;
  name: string | null;
}
export interface SkillVersion {
  number: number;
  sha256: string;
  description?: string;
  approved_at: string | null;
  approved_by_id?: string | null;
}
export interface SkillRow {
  id: string;
  name: string;
  description: string;
  latest: SkillVersion | null;
  installs_count: number;
  on: SkillActor[];
}
export interface Proposal {
  id: string;
  name: string;
  status: string;
  screen_status?: string;
  description: string;
  note: string | null;
  base_version: number | null;
  version: number | null;
  proposed_by: { type: string; id: string };
  task_id: string | null;
  decline_reason?: string | null;
  created_at: string;
  updated_at: string;
}
export interface SkillUse {
  name: string;
  uses: number;
  last_used_at: string | null;
}
export interface SkillInstall {
  id: string;
  target: SkillActor;
}
export interface SkillDetail {
  skill: { id: string; name: string; description: string; latest: SkillVersion | null };
  versions: SkillVersion[];
  installs: SkillInstall[];
}
export interface SkillFile {
  path: string;
  content: string;
}
export interface ProposalDetail {
  proposal: Proposal;
  files: SkillFile[];
  diff: string;
  stale: boolean;
  screening: { verdict: string; risk: string; summary: string } | null;
}
export interface SkillRecent {
  actor: SkillActor;
  trigger: string;
  used_at: string;
  task_id: string | null;
  thread_id: string | null;
}

export const skillsQuery = queryOptions({
  queryKey: ['skills', 'list'],
  queryFn: () => api.get<{ skills: SkillRow[] }>('/api/v2/skills').then((r) => r.skills),
  ...quiet,
});

export const proposalsQuery = queryOptions({
  queryKey: ['skills', 'proposals'],
  queryFn: () => api.get<{ proposals: Proposal[] }>('/api/v2/skills/proposals?status=pending').then((r) => r.proposals),
  ...quiet,
});

export const skillUsageQuery = queryOptions({
  queryKey: ['skills', 'usage'],
  queryFn: () => api.get<{ skills: SkillUse[] }>('/api/v2/skills/usage?days=7').then((r) => r.skills),
  ...quiet,
});

export const skillQuery = (name: string) =>
  queryOptions({
    queryKey: ['skills', 'detail', name],
    queryFn: () => api.get<SkillDetail>(`/api/v2/skills/${enc(name)}`),
    ...quiet,
  });

export const skillTextQuery = (name: string, n: number) =>
  queryOptions({
    queryKey: ['skills', 'version', name, n],
    queryFn: () => api.get<{ version: SkillVersion & { files: SkillFile[] } }>(`/api/v2/skills/${enc(name)}/versions/${n}`).then((r) => r.version),
    enabled: n > 0,
    staleTime: Infinity,
  });

export const skillRecentQuery = (name: string) =>
  queryOptions({
    queryKey: ['skills', 'recent', name],
    queryFn: () => api.get<{ recent: SkillRecent[] }>(`/api/v2/skills/${enc(name)}/usage?days=30`).then((r) => r.recent),
    ...quiet,
  });

export const proposalQuery = (id: string) =>
  queryOptions({
    queryKey: ['skills', 'proposal', id],
    queryFn: () => api.get<ProposalDetail>(`/api/v2/skills/proposals/${enc(id)}`),
    ...quiet,
  });

const dropProposal = (id: string) => (cur: Proposal[] | undefined) => cur?.filter((p) => p.id !== id);

export function approveProposal(id: string, acknowledged = false) {
  return write<unknown>({
    key: proposalsQuery.queryKey,
    optimistic: dropProposal(id),
    send: (idempotencyKey) => api.post(`/api/v2/skills/proposals/${enc(id)}/approve`, acknowledged ? { acknowledged: true } : {}, { idempotencyKey }),
  });
}

/** Connect Claude Code: the code the skills helper on a person's computer shows (backend/skill_devices.py). */
export const deviceCodeQuery = (code: string) =>
  queryOptions({
    queryKey: ['skills', 'device', code],
    queryFn: () => api.get<{ user_code: string; label: string | null; expires_at: string }>(`/api/v2/skills/devices/codes/${enc(code)}`),
    retry: false,
    staleTime: 0,
  });

export const decideDevice = (code: string, approve: boolean, share: boolean) =>
  api.post(`/api/v2/skills/devices/codes/${enc(code)}/${approve ? 'approve' : 'deny'}`, { reporting_enabled: approve && share });

export function declineProposal(id: string, reason: string) {
  return write<unknown>({
    key: proposalsQuery.queryKey,
    optimistic: dropProposal(id),
    send: (idempotencyKey) => api.post(`/api/v2/skills/proposals/${enc(id)}/decline`, { reason }, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- tools

export type ToolRow = ToolIndexRow;
export interface Inbox {
  inbox_id: string;
  kind: string;
  name: string;
  address: string;
  state: string;
  audience?: string;
}
export interface MailItem {
  id: string;
  kind: string;
  summary: string;
  sender: string;
  status: string;
  received_at: string;
}

export const toolsQuery = queryOptions({
  queryKey: ['tools', 'index'],
  queryFn: () => api.get<ToolIndex>('/api/v2/tool-index?limit=200'),
  ...quiet,
});

export const apiToolQuery = (id: string) =>
  queryOptions({
    queryKey: ['tools', 'api', id],
    queryFn: () => api.get<ApiTool>(`/api/v2/api-tools/${enc(id)}`),
    ...quiet,
  });

export const websiteToolQuery = (id: string) =>
  queryOptions({
    queryKey: ['tools', 'website', id],
    queryFn: () => api.get<WebsiteTool>(`/api/v2/website-tools/${enc(id)}`),
    ...quiet,
  });

export const mailboxQuery = (id: string) =>
  queryOptions({
    queryKey: ['tools', 'mailbox', id],
    queryFn: () => api.get<MailboxDetail>(`/api/v2/mailboxes/${enc(id)}`),
    ...quiet,
  });

export const vendorQuery = (vendor: string) =>
  queryOptions({
    queryKey: ['tools', 'vendor', vendor],
    queryFn: () => api.get<ToolVendor>(`/api/v2/tool-vendors/${enc(vendor)}`),
    ...quiet,
  });

export const inboxesQuery = queryOptions({
  queryKey: ['tools', 'inboxes'],
  queryFn: () => api.get<InboundInboxes>('/api/v2/inbound/inboxes').then((r) => r.inboxes as unknown as Inbox[]),
  ...quiet,
});

export const inboxMessagesQuery = (inbox: string) =>
  queryOptions({
    queryKey: ['tools', 'messages', inbox],
    queryFn: () => api.get<InboundMessages>(`/api/v2/inbound/messages${q({ inbox_ids: inbox, limit: 20 })}`).then((r) => r.messages),
    ...quiet,
  });

export const mailQuery = queryOptions({
  queryKey: ['tools', 'postal'],
  queryFn: () => api.get<{ items: MailItem[] }>('/api/v2/postal/items?limit=20').then((r) => r.items),
  ...quiet,
});

/** Letters out: the `letters` topic feeds this key while the page follows it. */
export const lettersQuery = queryOptions({
  queryKey: ['letters'],
  queryFn: () => api.get<LetterOutPage>('/api/v2/letters?limit=20').then((r) => r.items),
  staleTime: QUIET_STALE_MS,
});

type Key = ApiTool['keys'][number];
const withKeys = (f: (keys: Key[]) => Key[]) => (cur: ApiTool | undefined) => cur && { ...cur, keys: f(cur.keys) };

/** A new key. The value goes to the server once and never comes back: the row shows only a mask. */
export function addKey(tool: string, name: string, value: string) {
  const tmp = `tmp:${Date.now()}`;
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withKeys((keys) => [...keys, { id: tmp, name, preview: '••••', access_level: 'read_write' }]),
    send: (idempotencyKey) => api.post(`/api/v2/api-tools/${enc(tool)}/keys`, { name, value }, { idempotencyKey }),
  });
}

export function replaceKey(tool: string, key: string, value: string) {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withKeys((keys) => keys.map((k) => (k.id === key ? { ...k, preview: '••••' } : k))),
    send: (idempotencyKey) => api.patch(`/api/v2/api-tools/${enc(tool)}/keys/${enc(key)}`, { value }, { idempotencyKey }),
  });
}

export function removeKey(tool: string, key: string) {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withKeys((keys) => keys.filter((k) => k.id !== key)),
    send: (idempotencyKey) => api.delete(`/api/v2/api-tools/${enc(tool)}/keys/${enc(key)}`, { idempotencyKey }),
  });
}

export function checkTool(tool: string) {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: (cur: ApiTool | undefined) => cur && { ...cur, status: { text: 'Checking', tone: 'run' as const } },
    send: (idempotencyKey) => api.post(`/api/v2/api-tools/${enc(tool)}/check`, {}, { idempotencyKey }),
  });
}

/** One-tap connect: the server gives the provider's sign-in address, and its callback brings the person back. */
export const startSignIn = (tool: string) => api.post<{ authorize_url: string }>(`/api/v2/api-tools/${enc(tool)}/sign-in`, {});

/* Adding a tool: the server makes it and answers its id; the list reads again. */

const made = async (send: Promise<{ id: string }>, keys: readonly unknown[][] = []) => {
  const r = await send;
  await Promise.all([toolsQuery.queryKey, ...keys].map((queryKey) => queryClient.invalidateQueries({ queryKey })));
  return r;
};

/** What an MCP server asks for: a token, its own sign-in (`dcr`: it registers us itself), or nothing. */
export interface McpProbe {
  url: string;
  token: boolean;
  oauth: boolean;
  dcr: boolean;
  open: boolean;
  server?: string | null;
}
export const probeMcp = (url: string) => api.post<McpProbe>('/api/v2/mcp-tools/probe', { url });

export interface McpAdd {
  name: string;
  url: string;
  auth: 'token' | 'oauth';
  token?: string;
  header?: string;
  client_id?: string;
  client_secret?: string;
}
export const addMcp = (body: McpAdd) => made(api.post<{ id: string }>('/api/v2/mcp-tools', body));

export interface ApiAdd {
  name: string;
  service?: string;
  base_url?: string;
  auth_scheme: string;
  check_path?: string;
}
export const addApiTool = (body: ApiAdd) => made(api.post<{ id: string }>('/api/v2/api-tools', body));

/** A login: a shared one for every agent, or one person's or agent's own (`holder`). No password: signed in by hand. */
export interface LoginAdd {
  username?: string;
  password?: string;
  totp?: string;
  holder?: string;
  all_bots?: boolean;
}
export const addWebsite = (site: string, centralOnly: boolean) =>
  made(api.post<WebsiteToolCreated>('/api/v2/website-tools', { sites: [site], central_only: centralOnly } satisfies WebsiteToolCreate));
export const addAccount = (tool: string, login: LoginAdd) =>
  made(api.post<{ id: string }>(`/api/v2/website-tools/${enc(tool)}/accounts`, login satisfies WebsiteAccountCreate), [['tools', 'website', tool]]);
/** Only what changed is sent; `totp: null` takes the authenticator off. */
export const replaceLogin = (tool: string, account: string, change: WebsiteAccountUpdate) =>
  made(
    api.patch<unknown>(`/api/v2/website-tools/${enc(tool)}/accounts/${enc(account)}`, change).then(() => ({ id: account })),
    [['tools', 'website', tool]],
  );

export function setAccess(tool: string, subject: string, level: 'read' | 'read_write' | 'none') {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: (cur: ApiTool | undefined) =>
      cur && {
        ...cur,
        access: level === 'none' ? cur.access.filter((a) => a.subject !== subject) : cur.access.map((a) => (a.subject === subject ? { ...a, level } : a)),
      },
    send: (idempotencyKey) => api.put(`/api/v2/api-tools/${enc(tool)}/access/${enc(subject)}`, { level }, { idempotencyKey }),
  });
}

export function cancelLetter(id: string) {
  return write<unknown>({
    key: lettersQuery.queryKey,
    optimistic: (cur: LetterOutPage['items'] | undefined) => cur?.map((l) => (l.id === id ? { ...l, status: 'canceled', can_cancel: false } : l)),
    send: (idempotencyKey) => api.post(`/api/v2/letters/${enc(id)}/cancel`, {}, { idempotencyKey }),
  });
}

// ---------------------------------------------------------------- contacts

export interface EntityType {
  key: string;
  name: string;
  plural_name: string;
}
export interface Attribute {
  key: string;
  name: string;
  value_type: string;
  multiple: boolean;
  sensitive: boolean;
}
export interface ContactRow {
  id: string;
  type: string;
  name: string;
  status: string;
  primary_email: string | null;
  primary_domain: string | null;
  last_activity_at: string | null;
  organization?: { id: string; name: string } | null;
  owner?: { type?: string; id?: string; name?: string | null } | null;
  sources?: string[];
}
export interface FactValue {
  value: unknown;
  source_type: string;
  source_id: string | null;
  updated_at: string;
}
export interface Card extends ContactRow {
  summary: string | null;
  values: Record<string, FactValue | FactValue[]>;
  links: Array<{ id: string; link_name: string; other_id: string; other_name: string; other_type: string }>;
  notes: Array<{ id: string; author_type: string; author_id: string; body: string; audience: string; created_at: string }>;
}

export const typesQuery = queryOptions({
  queryKey: ['contacts', 'types'],
  queryFn: () => api.get<{ types: EntityType[] }>('/api/v2/entities/types').then((r) => r.types),
  ...quiet,
});

export const countsQuery = queryOptions({
  queryKey: ['contacts', 'counts'],
  queryFn: () => api.get<{ counts: Array<{ type: string; count: number }> }>('/api/v2/entities/counts').then((r) => r.counts),
  ...quiet,
});

export const attributesQuery = (type: string) =>
  queryOptions({
    queryKey: ['contacts', 'type', type],
    queryFn: () => api.get<{ attributes: Attribute[] }>(`/api/v2/entities/types/${enc(type)}`).then((r) => r.attributes),
    ...quiet,
  });

export const contactsQuery = (type: string, name: string) =>
  infiniteQueryOptions({
    queryKey: ['contacts', 'find', type, name],
    queryFn: ({ pageParam }) =>
      api.get<{ rows: ContactRow[]; next: string | null }>(`/api/v2/entities/find/${enc(type)}${q({ name, after: pageParam, limit: 100, detail: true })}`),
    initialPageParam: '',
    getNextPageParam: (last) => last.next ?? undefined,
    ...quiet,
  });

export const cardQuery = (ref: string) =>
  queryOptions({
    queryKey: ['contacts', 'card', ref],
    queryFn: () => api.get<Card>(`/api/v2/entities/card${q({ ref })}`),
    ...quiet,
  });

export interface ContactThread {
  thread_id: string;
  title?: string;
  excerpt: string;
  created_at?: string;
}

/* No address lists a contact's threads, so these are the threads whose messages name them (their email when known),
   one line a thread, from the threads search the viewer may read. */
export const contactThreadsQuery = (words: string) =>
  queryOptions({
    queryKey: ['contacts', 'threads', words],
    queryFn: async () => {
      const r = await api.get<{ hits: ContactThread[] }>(`/api/v2/threads/search${q({ q: words, limit: 20 })}`);
      const seen = new Set<string>();
      return r.hits.filter((h) => !seen.has(h.thread_id) && seen.add(h.thread_id));
    },
    enabled: words.trim().length > 0,
    ...quiet,
  });

/** A fact saves at once: shown first, taken back if the server refuses it. */
export function saveFact(ref: string, type: string, key: string, value: string) {
  return write<unknown>({
    key: ['contacts', 'card', ref],
    optimistic: (cur: Card | undefined) =>
      cur && {
        ...cur,
        ...(key === 'name' ? { name: value } : {}),
        values: { ...cur.values, [key]: { value, source_type: 'hub', source_id: null, updated_at: new Date().toISOString() } },
      },
    send: (idempotencyKey) =>
      api.post('/api/v2/entities', { type, match: { id: ref }, ...(key === 'name' ? { name: value } : { values: { [key]: value } }) }, { idempotencyKey }),
  });
}

export function addNote(ref: string, body: string, audience: 'company' | 'me') {
  const tmp = `tmp:${Date.now()}`;
  return write<unknown>({
    key: ['contacts', 'card', ref],
    optimistic: (cur: Card | undefined) =>
      cur && { ...cur, notes: [{ id: tmp, author_type: 'human', author_id: '', body, audience, created_at: new Date().toISOString() }, ...cur.notes] },
    send: (idempotencyKey) => api.post('/api/v2/entities/notes', { ref, body, audience }, { idempotencyKey }),
  });
}
