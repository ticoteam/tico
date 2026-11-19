import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  ApiTool,
  BotFile,
  DocSourceList,
  DocSourceResult,
  EmailSendingPolicy,
  ExternalDocResult,
  FileLibrary,
  GitHubApp,
  InboundCompanyConnection,
  InboundInboxes,
  InboundPhoneRequests,
  InboundMessages,
  InternalDoc,
  InternalDocList,
  InternalDocResult,
  InternalDocRow,
  InternalDocVersionResult,
  InternalDocVersions,
  LetterOutPage,
  LettersSettings,
  LibraryFile,
  MailboxDetail,
  PolicyUpdate,
  PostalMailGrants,
  PostalMailSettings,
  SetLettersSettingsBody,
  SkillPropose,
  SourceCreate,
  SourceDocumentList,
  SourceEdit,
  ToolIndex,
  ToolIndexRow,
  ToolVendor,
  WebsiteAccountCreate,
  WebsiteAccountUpdate,
  WebsiteTool,
  WebsiteToolCreate,
  WebsiteToolCreated,
  WebhookSends,
  WebhookShown,
} from '@/api/schema';
import { QUIET_STALE_MS, queryClient, quietRoot, write } from '@/data';
import type { DocsSearchResults, Group, LibrarianResearch, LibrarianResearchFiled } from '@/api/schema';

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
export function saveDoc(id: string, change: { version: number; body?: string; title?: string; path?: string }) {
  const at = new Date().toISOString();
  // The open doc and its row in the list both change at once; a list holds `docs`, a doc does not.
  const show = (cur: Doc | InternalDocList | undefined) => {
    if (!cur) return cur;
    if ('docs' in cur) return { ...cur, docs: cur.docs.map((d) => (d.id === id ? { ...d, title: change.title ?? d.title, path: change.path ?? d.path, updated_at: at } : d)) };
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

/** Archived docs only: the server lists them apart (`archived=true`). */
export const archivedDocsQuery = queryOptions({
  queryKey: ['docs', 'archived'],
  queryFn: () => api.get<InternalDocList>('/api/v2/docs?archived=true&limit=200'),
  ...quiet,
});

/** Archive a doc, or bring one back. `version` is the one shown: the server refuses a stale one. */
export function archiveDoc(id: string, version: number, archived: boolean) {
  const from = archived ? docsListQuery.queryKey : archivedDocsQuery.queryKey;
  return write<InternalDocResult>({
    key: from,
    optimistic: (cur: InternalDocList | undefined) => cur && { ...cur, docs: cur.docs.filter((d) => d.id !== id) },
    send: (idempotencyKey) => api.patch<InternalDocResult>(`/api/v2/docs/${enc(id)}`, { version, archived }, { idempotencyKey }),
  }).then(async (r) => {
    // the list it moved to shows it on its next read
    await queryClient.invalidateQueries({ queryKey: archived ? archivedDocsQuery.queryKey : docsListQuery.queryKey });
    return r;
  });
}

/** Lock a doc (only owners and agent administrators may change it then), or unlock it. */
export function lockDoc(id: string, version: number, locked: boolean) {
  return write<InternalDocResult>({
    key: ['docs', 'detail', id],
    optimistic: (cur: Doc | undefined) => cur && { ...cur, locked },
    send: (idempotencyKey) => api.patch<InternalDocResult>(`/api/v2/docs/${enc(id)}`, { version, locked }, { idempotencyKey }),
    settle: (_cur: Doc | undefined, answer) => answer.doc,
  }).then(async (r) => {
    // the list marks a locked doc
    await queryClient.invalidateQueries({ queryKey: docsListQuery.queryKey });
    return r;
  });
}

/** The types the import reads (backend/docs_import.py), up to 20 MB. */
export const IMPORT_TYPES = '.md,.markdown,.txt,.html,.htm,.docx,.pdf';
export const IMPORT_MAX = 20_000_000;

/** A file becomes a new doc: converted to Markdown by the server. */
export function importDoc(file: File, fields: { title?: string; path?: string } = {}) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (fields.title) fd.append('title', fields.title);
  if (fields.path) fd.append('path', fields.path);
  return api.post<InternalDocResult>('/api/v2/docs/import', fd, { idempotencyKey: crypto.randomUUID() }).then(async (r) => {
    await queryClient.invalidateQueries({ queryKey: docsListQuery.queryKey });
    return r.doc;
  });
}

/** Link an outside source: an address, and what to call it. The server reads it in the background. */
export const addSource = (body: SourceCreate) =>
  api.post<DocSourceResult>('/api/v2/doc-sources', body, { idempotencyKey: crypto.randomUUID() }).then(async (r) => {
    await queryClient.invalidateQueries({ queryKey: sourcesQuery.queryKey });
    return r.source;
  });

/** Change a source's address, name, description or branch; `archived` takes it off the list. */
export function editSource(id: string, change: SourceEdit) {
  const keep = Object.fromEntries(Object.entries(change).filter(([k, v]) => v != null && k !== 'archived'));
  return write<unknown>({
    key: sourcesQuery.queryKey,
    optimistic: (cur: DocSourceList | undefined) =>
      cur && {
        ...cur,
        sources: change.archived ? cur.sources.filter((x) => x.id !== id) : cur.sources.map((x) => (x.id === id ? { ...x, ...keep } : x)),
      },
    send: (idempotencyKey) => api.patch(`/api/v2/doc-sources/${enc(id)}`, change, { idempotencyKey }),
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
    queryFn: ({ signal }) => api.get<{ results: Array<{ id: string; title: string }> }>(`/api/v2/files/search${q({ q: text, limit: 50 })}`, { signal }),
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
/** One row per name (backend/skills.py skills_list). A skill only Tico ships has no id (`source: 'tico'`) and is on
   the agents made from its roles: `on_count` counts them, `installs_count` stays 0. */
export interface SkillRow {
  id: string | null;
  name: string;
  description: string;
  source?: 'directory' | 'tico';
  latest: SkillVersion | null;
  installs_count: number;
  on_count?: number;
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

/** The reader's company role as Access gives it ('owner', 'admin'); null for anyone Access refuses (a member). */
export const accessRoleQuery = queryOptions({
  queryKey: ['skills', 'access-role'],
  queryFn: async ({ signal }) => {
    try {
      return (await api.get<{ you?: string }>('/api/v2/access', { signal })).you ?? null;
    } catch (e) {
      if ((e as { status?: number }).status === 403) return null;
      throw e;
    }
  },
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

/** Who a skill may go to: the reader, agents, people, and teams (each team as its current members). */
export interface InstallTargets {
  me?: { type?: string; id: string } | null;
  bots: Array<{ id: string; name: string }>;
  people: Array<{ id: string; name: string }>;
  teams: Array<{ id: string; name: string; targets: Array<{ target_type: 'Bot' | 'Human'; target_id: string }> }>;
}

export const installTargetsQuery = queryOptions({
  queryKey: ['skills', 'install-targets'],
  queryFn: () => api.get<InstallTargets>('/api/v2/skills/install-targets'),
  ...quiet,
});

export type InstallFor = Array<{ target_type: 'Bot' | 'Human'; target_id: string }>;

/** Install for one teammate, or for several at once (a team). `version` pins one; none follows the latest. */
export function installSkill(name: string, targets: InstallFor, version?: number) {
  const policy = version ? { version_policy: 'pinned', pinned_version: version } : { version_policy: 'latest' };
  const one = targets.length === 1 && targets[0];
  return api
    .post<unknown>(`/api/v2/skills/${enc(name)}/installs${one ? '' : '/bulk'}`, one ? { ...one, ...policy } : { targets, ...policy }, {
      idempotencyKey: crypto.randomUUID(),
    })
    .then(() => queryClient.invalidateQueries({ queryKey: ['skills'] }));
}

/** Connect Claude Code: the computer waiting on the code its skills helper shows (backend/skill_devices.py). Not a
    query: the code is a one-time approval, held in the dialog's state only, never a query key or the cache. */
export const previewDevice = (code: string) =>
  api.get<{ user_code: string; label: string | null; expires_at: string }>(`/api/v2/skills/devices/codes/${enc(code)}`);

export const decideDevice = (code: string, approve: boolean, share: boolean) =>
  api.post(`/api/v2/skills/devices/codes/${enc(code)}/${approve ? 'approve' : 'deny'}`, { reporting_enabled: approve && share });

/** How a skill is named (clients/skills.py NAME, the rule the server checks). */
export const SKILL_NAME = /^[a-z0-9][a-z0-9._-]{0,79}$/;

/** A new skill as its SKILL.md, waiting for an admin's OK after screening (backend/skills.py propose: no
    `base_version`, so nothing it replaces). It waits at the top of the list once the answer is in. */
export function proposeSkill(input: { name: string; text: string; note?: string; task?: string }) {
  const body: SkillPropose = {
    name: input.name,
    files: [{ path: 'SKILL.md', content: input.text }],
    ...(input.note && { note: input.note }),
    ...(input.task && { task_id: input.task }),
  };
  return write<{ proposal: Proposal }>({
    key: proposalsQuery.queryKey,
    // nothing to show before the server names it
    optimistic: (cur: Proposal[] | undefined) => cur,
    send: (idempotencyKey) => api.post<{ proposal: Proposal }>('/api/v2/skills/proposals', body, { idempotencyKey }),
    // the server withdraws the proposer's earlier pending one for the name
    settle: (cur: Proposal[] | undefined, answer) => cur && [answer.proposal, ...cur.filter((p) => p.name !== answer.proposal.name)],
  }).then((r) => r.proposal);
}

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
  used_for?: string | null;
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

/** After GitHub's return: Tools read again, and the index as the server has it now. */
export async function rereadTools(): Promise<ToolIndex> {
  await queryClient.invalidateQueries({ queryKey: ['tools'] });
  return queryClient.fetchQuery(toolsQuery);
}

/** After GitHub's sign-in return: the App as the server reads it now, whose `authorized` says the sign-in held. */
export const readGithubApp = () => api.get<GitHubApp>('/api/v2/github/app');

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

/* Changing a tool. Each reads the tool (and the list, whose name and status it shows) again once the server agrees. */

const toolChanged = (key: readonly unknown[]) =>
  Promise.all([queryClient.invalidateQueries({ queryKey: key }), queryClient.invalidateQueries({ queryKey: toolsQuery.queryKey })]);
/** After a change the list shows too (a name, a state): the list reads again. */
const andList = async <T,>(sent: Promise<T>) => {
  const r = await sent;
  await queryClient.invalidateQueries({ queryKey: toolsQuery.queryKey });
  return r;
};

export interface ApiToolChange {
  name?: string;
  service?: string;
  base_url?: string;
  auth_scheme?: string;
  check_path?: string;
}
export function editApiTool(tool: string, change: ApiToolChange) {
  return andList(
    write<unknown>({
      key: ['tools', 'api', tool],
      optimistic: (cur: ApiTool | undefined) => cur && { ...cur, tool: { ...cur.tool, ...change } },
      send: (idempotencyKey) => api.patch(`/api/v2/api-tools/${enc(tool)}`, change, { idempotencyKey }),
    }),
  );
}

/** A key's own end date (`''` clears it). A vendor's date is the vendor's: the server refuses a change to it. */
export function setKeyExpiry(tool: string, key: string, expiresOn: string) {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withKeys((keys) => keys.map((k) => (k.id === key ? { ...k, expires_on: expiresOn || null } : k))),
    send: (idempotencyKey) => api.patch(`/api/v2/api-tools/${enc(tool)}/keys/${enc(key)}`, { expires_on: expiresOn }, { idempotencyKey }),
  });
}

type Setting = NonNullable<ApiTool['settings']>[number];
const withSettings = (f: (s: Setting[]) => Setting[]) => (cur: ApiTool | undefined) => cur && { ...cur, settings: f(cur.settings ?? []) };

/** A run setting: a plain value a call carries (an account id, a region), never a secret. */
export function saveSetting(tool: string, setting: { id?: string; name: string; value: string }) {
  const { id, name, value } = setting;
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withSettings((list) =>
      id ? list.map((s) => (s.id === id ? { ...s, name, value } : s)) : [...list, { id: `tmp:${Date.now()}`, name, value, updated_at: new Date().toISOString() }],
    ),
    send: (idempotencyKey) =>
      id
        ? api.patch(`/api/v2/api-tools/${enc(tool)}/settings/${enc(id)}`, { name, value }, { idempotencyKey })
        : api.post(`/api/v2/api-tools/${enc(tool)}/settings`, { name, value }, { idempotencyKey }),
  });
}

export function removeSetting(tool: string, id: string) {
  return write<unknown>({
    key: ['tools', 'api', tool],
    optimistic: withSettings((list) => list.filter((s) => s.id !== id)),
    send: (idempotencyKey) => api.delete(`/api/v2/api-tools/${enc(tool)}/settings/${enc(id)}`, { idempotencyKey }),
  });
}

/**
 * A stored secret, read once for the person who asked (the server logs it). Deliberately not a query: the value is
 * never cached, and the caller keeps it in its own state only while it is shown.
 */
export const revealSecret = (credential: string) =>
  api.post<{ value?: string; password?: string }>(`/api/v2/credentials/${enc(credential)}/reveal`, {}).then((r) => r.value ?? r.password ?? '');

/** What a paste holds, item by item: names and masked previews, never the values. Nothing is stored. */
export interface PasteItem {
  role: string;
  name: string;
  preview: string;
}
export const classifyPaste = (text: string) => api.post<{ template?: string | null; items: PasteItem[] }>('/api/v2/tool-templates/classify', { text });
/** Stores a paste on a tool in one step: each secret as a key, each plain value as a run setting. */
export const pasteKeys = (tool: string, text: string) =>
  api.post<{ keys: number; settings: number }>(`/api/v2/api-tools/${enc(tool)}/paste`, { text }).then(async (r) => {
    await toolChanged(['tools', 'api', tool]);
    return r;
  });

export interface WebsiteChange {
  name?: string;
  sites?: string[];
  central_only?: boolean;
}
export function editWebsite(tool: string, change: WebsiteChange) {
  return andList(
    write<unknown>({
      key: ['tools', 'website', tool],
      optimistic: (cur: WebsiteTool | undefined) => cur && { ...cur, tool: { ...cur.tool, ...change } },
      send: (idempotencyKey) => api.patch(`/api/v2/website-tools/${enc(tool)}`, change, { idempotencyKey }),
    }),
  );
}

/** The sign-in code the account's authenticator shows now. Not cached: it changes every half minute. */
export const signInCode = (tool: string, account: string) =>
  api.get<{ code: string; expires_in?: number }>(`/api/v2/website-tools/${enc(tool)}/accounts/${enc(account)}/code`);

/** One message that came in, a page of its text at a time (it is untrusted: shown as text, never as a page). */
export const readMessage = (message: string, offset = 0) =>
  api.get<{ text: string; next_offset?: number | null }>(`/api/v2/inbound/messages/${enc(message)}${q({ offset: offset || undefined })}`);

const inboxesChanged = () => toolChanged(inboxesQuery.queryKey);

export const addTicoAddress = (name: string) =>
  api.post<{ inbox_id?: string; inbox?: { inbox_id: string } }>('/api/v2/inbound/inboxes', { kind: 'email', name }).then(async (r) => {
    await inboxesChanged();
    return r.inbox_id ?? r.inbox?.inbox_id ?? null;
  });

/** Asks for a monthly US mobile number. No purchase: a quote comes back for a person to accept. */
export const requestNumber = (name: string) => api.post<unknown>('/api/v2/inbound/phone-requests', { name }).then(inboxesChanged);

export function editInbox(inbox: string, change: { name: string; used_for?: string | null }) {
  return andList(
    write<unknown>({
      key: inboxesQuery.queryKey,
      optimistic: (cur: Inbox[] | undefined) => cur?.map((b) => (b.inbox_id === inbox ? { ...b, ...change } : b)),
      send: (idempotencyKey) => api.patch(`/api/v2/inbound/inboxes/${enc(inbox)}`, change, { idempotencyKey }),
    }),
  );
}

/** Retires an address, or stops renewing a number. `confirm`: the address typed back when something depends on it. */
export const releaseInbox = (inbox: string, confirm: string) =>
  api.post<unknown>(`/api/v2/inbound/inboxes/${enc(inbox)}/release`, { confirm }).then(inboxesChanged);

export const connectMailbox = (address: string, shared: boolean) =>
  made(api.post<{ mailbox: { id: string } }>('/api/v2/mailboxes', { address, shared }).then((r) => ({ id: r.mailbox.id })));

/** A mailing address, shared or private, in one state. Paid monthly: the person's own order. */
export const orderAddress = (body: { kind: 'shared' | 'private'; state: string; company_name: string }) =>
  andList(api.post<unknown>('/api/v2/postal/orders', body, { idempotencyKey: crypto.randomUUID() }));

/* An outgoing webhook: where it sends, what it sends, how its sends went. */

export const webhookQuery = (id: string) =>
  queryOptions({
    queryKey: ['tools', 'webhook', id],
    queryFn: () => api.get<WebhookShown>(`/api/v2/webhooks/${enc(id)}`),
    ...quiet,
  });

export const webhookSendsQuery = (id: string) =>
  queryOptions({
    queryKey: ['tools', 'webhook', id, 'sends'],
    queryFn: () => api.get<WebhookSends>(`/api/v2/webhooks/${enc(id)}/sends${q({ limit: 20 })}`).then((r) => r.items),
    ...quiet,
  });

/** Pause or send again. */
export function setWebhookOn(id: string, on: boolean) {
  return andList(
    write<unknown>({
      key: ['tools', 'webhook', id],
      optimistic: (cur: WebhookShown | undefined) => cur && { ...cur, webhook: { ...cur.webhook, paused_reason: on ? '' : 'Paused' } },
      send: (idempotencyKey) => api.post(`/api/v2/webhooks/${enc(id)}/${on ? 'resume' : 'pause'}`, {}, { idempotencyKey }),
    }),
  );
}

/** A made-up event to the endpoint, to see that it answers. */
export const sendTestEvent = (id: string) =>
  api.post<unknown>(`/api/v2/webhooks/${enc(id)}/send`, { type: 'custom.test', subject: 'Test' }).then(() =>
    queryClient.invalidateQueries({ queryKey: ['tools', 'webhook', id] }),
  );

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
  /** Its links to us (customer_of, vendor_to...): what it is to the company. */
  links_to_us?: string[];
  organization?: { id: string; name: string; links_to_us?: string[] } | null;
  /** `human:<id>` or `bot:<slug>` (store._owner). */
  owner?: string | null;
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

/** One type's contacts by name, and `link`: only those with that link to us (customer_of...). */
export const contactsQuery = (type: string, name: string, link = '') =>
  infiniteQueryOptions({
    queryKey: ['contacts', 'find', type, name, link],
    queryFn: ({ pageParam }) =>
      api.get<{ rows: ContactRow[]; next: string | null }>(`/api/v2/entities/find/${enc(type)}${q({ name, link, after: pageParam, limit: 100, detail: true })}`),
    initialPageParam: '',
    getNextPageParam: (last) => last.next ?? undefined,
    ...quiet,
  });

/** Search's contacts: one type's, by name, a few rows without their details. */
export const contactSearchQuery = (type: string, name: string, limit: number) =>
  queryOptions({
    queryKey: ['contacts', 'search', type, name, limit],
    queryFn: ({ signal }) => api.get<{ rows: ContactRow[] }>(`/api/v2/entities/find/${enc(type)}${q({ name, limit })}`, { signal }),
    ...quiet,
  });

export const cardQuery = (ref: string) =>
  queryOptions({
    queryKey: ['contacts', 'card', ref],
    queryFn: () => api.get<Card>(`/api/v2/entities/card${q({ ref })}`),
    ...quiet,
  });

/** A field's past value (backend/entities/store.py history): where it came from, and when it held. */
export interface FactPast {
  id: string;
  value: unknown;
  source_type: string;
  source_id: string | null;
  confidence: number | null;
  active_from: string | null;
  active_until: string | null;
}

export const factHistoryQuery = (ref: string, attribute: string) =>
  queryOptions({
    queryKey: ['contacts', 'history', ref, attribute],
    queryFn: () => api.get<{ rows: FactPast[]; next: string | null }>(`/api/v2/entities/history${q({ ref, attribute })}`).then((r) => r.rows),
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

/* ---------------------------------------------------------------- company settings for Email, Texts and Letters */

/* The old Tools › Settings (ui/app/inboxes-*.ts). Google's routes, the postal intake and the postal address list are not
   in the generated schema: their shapes are typed here from backend/google_workspace.py, google_connect.py and
   postal_mail.py. A secret sent here (a key, a connection code) is never kept: not in the cache, not in a key. */

/** What the company lets Tico do in Google beyond reading (backend/google_workspace.py permissions). */
export interface MailMay {
  organize_mail: boolean;
  send_mail: boolean;
  change_calendar: boolean;
}
/** A Workspace key as the server shows it: never the key itself. */
export interface GoogleKey {
  status: string;
  domain: string;
  client_id: string;
  delegation_url: string;
  checked_at: string | null;
  last_error: string | null;
}
/** GET /api/v2/google */
export interface GoogleState {
  can_admin: boolean;
  workspace: GoogleKey | null;
  pending: GoogleKey | null;
  me: { mailbox: string; covered_by_workspace: boolean } | null;
  permissions: MailMay;
}
/** GET /api/v2/mail/google: the person's own Google sign-in. */
export interface MyGoogle {
  configured: boolean;
  mailbox: string;
  status: string;
  message: string | null;
}

export const googleQuery = queryOptions({ queryKey: ['tools', 'google'], queryFn: () => api.get<GoogleState>('/api/v2/google'), ...quiet });
export const myGoogleQuery = queryOptions({ queryKey: ['tools', 'google', 'me'], queryFn: () => api.get<MyGoogle>('/api/v2/mail/google'), ...quiet });
const googleChanged = () => toolChanged(googleQuery.queryKey);

export const setMailMay = (may: MailMay) =>
  write<unknown>({
    key: googleQuery.queryKey,
    optimistic: (cur: GoogleState | undefined) => cur && { ...cur, permissions: may },
    send: (idempotencyKey) => api.put('/api/v2/google/permissions', may, { idempotencyKey }),
  });

/** Asks Google whether the key works, for a mailbox the server picks. */
export const checkGoogle = () =>
  api.post<{ ok: boolean; message: string }>('/api/v2/google/workspace/check', {}).then(async (r) => {
    await googleChanged();
    return r;
  });

/** A one-time setup command for a Workspace domain. Its code is a secret: shown once in the dialog, never cached. */
export const googleSetupCommand = (domain: string) => api.post<{ command: string; expires_at: string }>('/api/v2/google/workspace/setup-code', { domain });

/** A service-account key, sealed on the server and kept pending until its check passes. */
export const uploadGoogleKey = (domain: string, key: Record<string, unknown>) =>
  api.post<{ delegation_url: string }>('/api/v2/google/workspace/key', { domain, key }).then(async (r) => {
    await googleChanged();
    return r;
  });

export const disconnectGoogle = () => api.delete<unknown>('/api/v2/google/workspace').then(googleChanged);

/** The person's own Google sign-in: Google's page to go to. */
export const connectMyGoogle = () => api.post<{ auth_url: string }>('/api/v2/mail/google/connect', {});
export const disconnectMyGoogle = () => api.post<unknown>('/api/v2/mail/google/disconnect', {}).then(() => toolChanged(myGoogleQuery.queryKey));

/** A sending policy, effective or as stored: what agents may send and when they ask first (backend/email_sending.py). */
export interface SendPolicy {
  mode?: string | null;
  what?: string;
  to?: string;
  daily_cap?: number;
  mailbox_daily_cap?: number;
  company_daily_cap?: number;
  undo_seconds?: number;
  max_outside_recipients?: number;
  blocked_recipients?: string[];
  ask_new_address?: boolean;
  ask_legal_money?: boolean;
  ask_attachments?: boolean;
  ask_many_recipients?: boolean;
  ask_outside_cc?: boolean;
}
export interface SendPolicies {
  effective: SendPolicy;
  company: SendPolicy;
}

export const sendingQuery = queryOptions({
  queryKey: ['tools', 'sending'],
  queryFn: () => api.get<EmailSendingPolicy>('/api/v2/email/sending-policy').then((r) => ({ effective: r.effective as SendPolicy, company: r.company as SendPolicy })),
  ...quiet,
});
export const agentSendingQuery = (bot: string) =>
  queryOptions({
    queryKey: ['tools', 'sending', 'agent', bot],
    queryFn: () => api.get<EmailSendingPolicy>(`/api/v2/bots/${enc(bot)}/email-sending-policy`).then((r) => ({ mode: (r.bot.mode as string | null | undefined) ?? null })),
    ...quiet,
  });

export const setSending = (change: PolicyUpdate) =>
  write<unknown>({
    key: sendingQuery.queryKey,
    optimistic: (cur: SendPolicies | undefined) => cur && { effective: { ...cur.effective, ...change } as SendPolicy, company: { ...cur.company, ...change } as SendPolicy },
    send: (idempotencyKey) => api.put('/api/v2/email/sending-policy', change, { idempotencyKey }),
  });

/** One agent's own mode, or `null`: the company's. */
export const setAgentSending = (bot: string, mode: string | null) =>
  write<unknown>({
    key: agentSendingQuery(bot).queryKey,
    optimistic: () => ({ mode }),
    send: (idempotencyKey) => api.put(`/api/v2/bots/${enc(bot)}/email-sending-policy`, mode ? { mode } : { inherit: true }, { idempotencyKey }),
  });

/** Joining the company's shared Inbox with a connection code from the customer portal. `preview` names the company. */
export type SharedInbox = InboundCompanyConnection & { company: { name?: string } };
export const sharedInbox = (action: 'preview' | 'connect', code: string) =>
  api.post<SharedInbox>('/api/v2/inbound/company-connection', { action, code }).then(async (r) => {
    if (action === 'connect') await toolChanged(inboxesQuery.queryKey);
    return r;
  });

/** A phone number asked for: priced by Tico, then accepted by a person. */
export interface NumberRequest {
  request_id: string;
  name: string;
  state: string;
  price_micros?: number | null;
  quoted_until?: number | string | null;
}
export const numberRequestsQuery = queryOptions({
  queryKey: ['tools', 'phone-requests'],
  queryFn: () => api.get<InboundPhoneRequests>('/api/v2/inbound/phone-requests').then((r) => r.phone_requests as unknown as NumberRequest[]),
  ...quiet,
});
const requestsChanged = () => Promise.all([toolChanged(numberRequestsQuery.queryKey), queryClient.invalidateQueries({ queryKey: inboxesQuery.queryKey })]);
export const acceptQuote = (request: string, price_micros: number) =>
  api.post<unknown>(`/api/v2/inbound/phone-requests/${enc(request)}/accept`, { price_micros }, { idempotencyKey: crypto.randomUUID() }).then(requestsChanged);
export const cancelNumberRequest = (request: string) => api.post<unknown>(`/api/v2/inbound/phone-requests/${enc(request)}/cancel`, {}).then(requestsChanged);

/** A mailing address or an open order for one (GET /api/v2/postal/addresses). 409 `gateway` while the Gateway is off. */
export interface PostalAddress {
  id?: string | null;
  assignment_id?: string | null;
  address?: string | null;
  state: string;
  kind: string;
  status: string;
}
export interface PostalOrder {
  order_id?: string | null;
  kind: string;
  state: string;
  status?: string;
}
export const postalAddressesQuery = queryOptions({
  queryKey: ['tools', 'postal', 'addresses'],
  queryFn: () => api.get<{ addresses?: PostalAddress[]; orders?: PostalOrder[] }>('/api/v2/postal/addresses'),
  ...quiet,
});

/** Letters emailed in: PDFs and images sent to this inbox by these senders become letters. */
export interface LetterIntake {
  inbox_id?: string | null;
  address?: string | null;
  hidden?: boolean;
  senders?: string[] | null;
}
export const intakeQuery = queryOptions({
  queryKey: ['tools', 'postal', 'intake'],
  queryFn: () => api.get<{ intake?: LetterIntake }>('/api/v2/postal/intake').then((r) => r.intake ?? {}),
  ...quiet,
});
export const setIntake = (inbox_id: string | null, senders: string[]) =>
  write<unknown>({
    key: intakeQuery.queryKey,
    optimistic: (cur: LetterIntake | undefined) => ({ ...cur, inbox_id, senders }),
    send: (idempotencyKey) => api.put('/api/v2/postal/intake', { inbox_id, senders }, { idempotencyKey }),
  });

/** Read on every address: who may read every letter and scan, private ones included. */
export const letterGrantsQuery = queryOptions({
  queryKey: ['tools', 'postal', 'grants'],
  queryFn: () => api.get<PostalMailGrants>('/api/v2/postal/addresses/all/grants?limit=50').then((r) => r.grants),
  ...quiet,
});
export const setLetterGrant = (type: 'person' | 'bot', who: string, on: boolean) => {
  const path = `/api/v2/postal/addresses/all/grants/${type}/${enc(who)}` as const;
  return (on ? api.put<unknown>(path, { level: 'read' }) : api.delete<unknown>(path)).then(() => toolChanged(letterGrantsQuery.queryKey));
};

export const legalMailQuery = queryOptions({
  queryKey: ['tools', 'postal', 'settings'],
  queryFn: () => api.get<PostalMailSettings>('/api/v2/postal/settings').then((r) => r.settings.legal_bot_id),
  ...quiet,
});
export const setLegalMail = (legal_bot_id: string | null) =>
  write<unknown>({
    key: legalMailQuery.queryKey,
    optimistic: () => legal_bot_id,
    send: (idempotencyKey) => api.put('/api/v2/postal/settings', { legal_bot_id }, { idempotencyKey }),
  });

export const lettersSettingsQuery = queryOptions({
  queryKey: ['tools', 'letters', 'settings'],
  queryFn: () => api.get<LettersSettings>('/api/v2/letters/settings'),
  ...quiet,
});
export const setLettersOut = (change: SetLettersSettingsBody) =>
  api.put<LettersSettings>('/api/v2/letters/settings', change).then((r) => queryClient.setQueryData(lettersSettingsQuery.queryKey, r));
/** Asks the Gateway whether letters can go out now; the answer is the settings, checked. */
export const checkLetters = () =>
  api.get<LettersSettings>('/api/v2/letters/settings?check=true').then((r) => {
    queryClient.setQueryData(lettersSettingsQuery.queryKey, r);
    return r;
  });

/* ---------------------------------------------------------------- gap wave: Docs search and Research, Files filters, a
   contact's rules and a place */

/** One hit of the server's docs search: a team doc (by its words, with an excerpt), an outside doc, or a linked page. */
export type DocHit = DocsSearchResults['results'][number];

/** The team's docs by their words, best first (GET /api/v2/docs/search; the old Docs page's search box). */
export const docsSearchQuery = (words: string) =>
  queryOptions({
    queryKey: ['docs', 'search', words],
    queryFn: ({ signal }) => api.get<DocsSearchResults>(`/api/v2/docs/search${q({ q: words, limit: 30 })}`, { signal }).then((r) => r.results),
    enabled: /\w/.test(words),
    ...quiet,
  });

/** The Librarian researches the web and adds what it finds, cited, to a doc or one section of it: one task to it. */
export const startResearch = (body: LibrarianResearch) =>
  api.post<LibrarianResearchFiled>('/api/v2/librarian/research', body, { idempotencyKey: crypto.randomUUID() });

/** What Files may be narrowed by, as the address carries it (`agent` is a bot id, `writer` and `linked` actor or target
    ids, `days` back from now). The server's names: backend/file_library.py listing. */
export interface FileFilters {
  kind?: string;
  role?: string;
  agent?: string;
  writer?: string;
  linked?: string;
  team?: string;
  task?: string;
  days?: number;
  pinned?: 1;
  archived?: 1;
}
const MORE_FILTERS = ['role', 'agent', 'writer', 'linked', 'team', 'task', 'days', 'pinned', 'archived'] as const;

/** Files by every filter. With the kind alone it is `filesQuery(kind)`'s key, so the route's first read is the page's. */
export const filesListQuery = (f: FileFilters) => {
  const rest = Object.fromEntries(MORE_FILTERS.filter((k) => f[k] !== undefined).map((k) => [k, f[k]]));
  const kind = f.kind ?? '';
  return infiniteQueryOptions({
    queryKey: Object.keys(rest).length ? ['files', 'list', kind, rest] : ['files', 'list', kind],
    queryFn: ({ pageParam }) =>
      api.get<FileLibrary>(
        `/api/v2/files${q({
          limit: 100,
          kind,
          role: f.role,
          bot: f.agent,
          writer: f.writer,
          linked: f.linked,
          team: f.team,
          task: f.task,
          since: f.days ? new Date(Date.now() - f.days * 86_400_000).toISOString() : undefined,
          pinned: f.pinned ? true : undefined,
          archived: f.archived ? true : undefined,
          cursor: pageParam,
        })}`,
      ),
    initialPageParam: '',
    getNextPageParam: (last) => (last.has_more && last.next_cursor ? last.next_cursor : undefined),
    ...quiet,
  });
};

/** The file search with the filters it takes (kind, role, linked, writer); none is `fileSearchQuery`'s key. */
export const filesFindQuery = (text: string, f: FileFilters) => {
  const by = { kind: f.kind, role: f.role, linked: f.linked, writer: f.writer };
  const on = Object.values(by).some(Boolean);
  return queryOptions({
    queryKey: on ? ['files', 'search', text, by] : ['files', 'search', text],
    queryFn: ({ signal }) => api.get<{ results: Array<{ id: string; title: string }> }>(`/api/v2/files/search${q({ q: text, limit: 50, ...by })}`, { signal }),
    enabled: text.trim().length > 1,
    ...quiet,
  });
};

/** The teams, for the Files filter. */
export const filesTeamsQuery = queryOptions({
  queryKey: ['files', 'teams'],
  queryFn: ({ signal }) => api.get<Group[] | { groups: Group[] }>('/api/v2/groups', { signal }).then((r) => (Array.isArray(r) ? r : r.groups)),
  ...quiet,
});

/** A rule that applies to a contact, with the path it comes by (backend/entities/routes.py contacts_rules). */
export interface ContactRule {
  id?: string;
  text: string;
  cite?: string | null;
  effect?: string | null;
  path?: string[] | null;
  reason?: string | null;
}
export const contactRulesQuery = (ref: string) =>
  queryOptions({
    queryKey: ['contacts', 'rules', ref],
    queryFn: () => api.get<{ rules?: ContactRule[] }>(`/api/v2/entities/rules${q({ ref })}`).then((r) => r.rules ?? []),
    ...quiet,
  });

/** Our own company in Contacts and the places it operates in; null while it is not in Contacts (a 404). */
export interface Us {
  id: string;
  name: string;
  operates_in: Array<{ id: string; name: string }>;
}
export const usQuery = queryOptions({
  queryKey: ['contacts', 'self'],
  queryFn: () =>
    api.get<Us>('/api/v2/entities/self').catch((e: unknown) => {
      if ((e as { status?: number } | null)?.status === 404) return null;
      throw e;
    }),
  ...quiet,
});

/** A place's own words on its card (backend/entities/store.py card), and a link as the card sends it in full. */
export interface PlaceInfo {
  kind: string;
  governing_body: string;
  shared: boolean;
}
export type CardLink = Card['links'][number] & { link_key?: string; from_entity_id?: string; inverse_name?: string | null };
