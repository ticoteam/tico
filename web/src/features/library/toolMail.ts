import { infiniteQueryOptions, keepPreviousData, queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { LetterOutPage, PostalMailGrants } from '@/api/schema';
import { QUIET_STALE_MS, queryClient } from '@/data';
import { mailQuery, postalAddressesQuery, toolsQuery, type MailItem } from './queries';

/* The Tools calls the tier-2 gap rows need (s515 s868 s869 s870 s932): an outgoing webhook's delete and new secret; a
   mailing address's Read grants, letters in and their retry; every Tico address's messages; a message's code. A secret (a signing secret, a code) is
   only ever returned to the caller here, never put in the cache. */

const enc = encodeURIComponent;
const quiet = { staleTime: QUIET_STALE_MS } as const;
const qs = (o: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};
const reread = (...keys: (readonly unknown[])[]) => Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));

// ---------------------------------------------------------------- an outgoing webhook

const hookKey = (id: string) => ['tools', 'webhook', id] as const;

/** For good: nothing more is sent, and what waits is canceled. */
export async function deleteWebhook(id: string) {
  await api.post<unknown>(`/api/v2/webhooks/${enc(id)}/delete`, {});
  queryClient.removeQueries({ queryKey: hookKey(id) });
  await reread(toolsQuery.queryKey);
}

/** A new signing secret, shown once (the old one signs beside it for a day). A replay answers without one: refused. */
export async function rotateWebhookSecret(id: string): Promise<string> {
  const r = await api.post<{ webhook?: { secret?: string; replayed?: boolean } }>(`/api/v2/webhooks/${enc(id)}/rotate`, {});
  await reread(hookKey(id));
  const secret = r.webhook?.secret;
  if (!secret) throw new Error('The new secret was lost on the way');
  return secret;
}

export async function retryWebhookSend(id: string, send: string) {
  await api.post<unknown>(`/api/v2/webhooks/${enc(id)}/sends/${enc(send)}/retry`, {});
  await reread(hookKey(id));
}

// ---------------------------------------------------------------- a mailing address

/** A letter that came in, as GET /api/v2/postal/items gives it (a redacted one: only when and its status). */
export interface LetterIn extends MailItem {
  provider?: string | null;
  address?: string | null;
  address_id?: string | null;
  file_url?: string | null;
  task_id?: string | null;
  redacted?: boolean;
}

/** Every letter the reader may see, for one address's page (the old page read 100). */
export const lettersInQuery = queryOptions({
  queryKey: ['tools', 'postal', 'items', 100],
  queryFn: () => api.get<{ items: LetterIn[] }>('/api/v2/postal/items?limit=100').then((r) => r.items),
  ...quiet,
});

/** A failed letter tried again now (owner or admin). */
export async function retryLetterIn(item: string) {
  await api.post<unknown>(`/api/v2/postal/items/${enc(item)}/retry`, {});
  await reread(mailQuery.queryKey);
}

/** Who may read every letter and scan at one address (a Gateway assignment, or an older address by its text). */
export const addressGrantsQuery = (scope: string) =>
  queryOptions({
    queryKey: ['tools', 'postal', 'grants', 'one', scope],
    queryFn: () => api.get<PostalMailGrants>(`/api/v2/postal/addresses/${enc(scope)}/grants?limit=50`).then((r) => r.grants),
    ...quiet,
  });

export async function setAddressGrant(scope: string, type: 'person' | 'bot', who: string, on: boolean) {
  const path = `/api/v2/postal/addresses/${enc(scope)}/grants/${type}/${enc(who)}` as const;
  await (on ? api.put<unknown>(path, { level: 'read' }) : api.delete<unknown>(path));
  await reread(addressGrantsQuery(scope).queryKey);
}

/** The addresses read again now, as the Gateway has them. */
export const checkAddresses = () => queryClient.fetchQuery({ ...postalAddressesQuery, staleTime: 0 });

// ---------------------------------------------------------------- letters out

/** Letters older than the first page (`lettersQuery`), a page at a time. The `letters` topic marks them stale too. */
export const olderLettersQuery = (before: string) =>
  infiniteQueryOptions({
    queryKey: ['letters', 'older', before],
    queryFn: ({ pageParam }) => api.get<LetterOutPage>(`/api/v2/letters${qs({ limit: 20, before: pageParam })}`),
    initialPageParam: before,
    getNextPageParam: (p) => p.next_before ?? undefined,
    staleTime: QUIET_STALE_MS,
  });

// ---------------------------------------------------------------- messages to every Tico address and number

/** What Messages narrows by. No inbox: every one the reader may read. */
export interface MessageFilter {
  inboxes?: string[];
  kind?: 'email' | 'sms';
  from?: string;
  q?: string;
  has_code?: boolean;
  spam?: boolean;
}
/** One message as the list gives it: never its whole text, never its code. */
export interface InMessage {
  message_id: string;
  inbox_id: string;
  kind?: string;
  from?: string;
  subject?: string;
  preview?: string;
  received_at: number;
  has_code?: boolean;
  spam?: boolean;
  retention_removed?: boolean;
}

export const allMessagesQuery = (f: MessageFilter) =>
  infiniteQueryOptions({
    queryKey: ['tools', 'messages', 'all', f],
    queryFn: ({ pageParam }) =>
      api.get<{ messages: InMessage[]; next: number | null }>(
        `/api/v2/inbound/messages${qs({
          limit: 50,
          inbox_ids: f.inboxes?.length ? f.inboxes.join(',') : undefined,
          kind: f.kind,
          from: f.from?.trim(),
          q: f.q?.trim(),
          has_code: f.has_code ? 'true' : undefined,
          spam: f.spam ? 'true' : undefined,
          before: pageParam || undefined,
        })}`,
      ),
    initialPageParam: 0,
    getNextPageParam: (p) => p.next ?? undefined,
    // a new filter keeps the last list on screen until its own read lands
    placeholderData: keepPreviousData,
    ...quiet,
  });

/**
 * The code a message carries, read for the person who asked. Deliberately not a query: a code is a secret, never
 * cached; the caller keeps it in its own state while it shows.
 */
export const readCode = (message: string) =>
  api.get<{ code?: string | null }>(`/api/v2/inbound/messages/${enc(message)}/code`).then((r) => r.code ?? '');
