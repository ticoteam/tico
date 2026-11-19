import { queryOptions } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import { QUIET_STALE_MS, meQuery, queryClient, quietRoot, write } from '@/data';
import { browserFailures } from './failures';

/* Tico support (backend/support.py): the person's tickets, drawn as one conversation. A message goes to the newest
   ticket still open, or starts a new one; the person never sees a ticket. Replies come from Tico support's own server,
   which only a refresh asks, so nothing on the stream carries them: the chat asks while it is open (refreshQuery). */
quietRoot('support');

const enc = encodeURIComponent;

export interface SupportMessage {
  id: number | string;
  from: 'person' | 'staff';
  body: string;
  created_at: string;
  has_diagnostics?: boolean;
  /** Local only: a send not answered yet, or refused. */
  pending?: 'sending' | 'failed';
  /** Local only: what a Retry sends again with. */
  retry?: Retry;
}

export interface SupportTicket {
  id: string;
  /** open, answered, closed, gone (deleted at Tico support). */
  status: string;
  /** The ticket's first message, the person's. */
  message: string;
  created_at: string;
  unread?: number;
  sent?: { diagnostics?: unknown };
  messages?: SupportMessage[];
  pending?: 'sending' | 'failed';
  retry?: Retry;
}

export interface SupportList {
  /** false: this server lets no messages out (demo, rehearsal, support turned off). */
  enabled: boolean;
  tickets: SupportTicket[];
  unread: number;
  refresh_failed?: boolean;
}

/** GET /api/v2/support/compose: the reply email a new ticket carries, and this install's version. */
export interface SupportCompose {
  email: string;
  version: string;
  install_id: string;
}

/** A diagnostics preview: kept by the server under `id` for a few minutes; `text` is exactly what goes. */
export interface DiagnosticsBundle {
  id: string;
  text: string;
  bytes: number;
}

/** Where a message goes: a ticket, the newest open one, or a new one. */
export type To = string | 'latest' | 'new';

export interface Retry {
  to: To;
  key: string;
  diagnostics?: string;
  edited?: string;
}

// ---------------------------------------------------------------- reads

const TICKETS = ['support', 'tickets'] as const;
const readTickets = async ({ signal }: { signal: AbortSignal }) => {
  const next = await api.get<SupportList>('/api/v2/support/tickets', { signal });
  return keepPending(next, queryClient.getQueryData<SupportList>(TICKETS));
};

export const ticketsQuery = queryOptions({ queryKey: TICKETS, queryFn: readTickets, staleTime: QUIET_STALE_MS });

/** The replies from Tico support the person has not read: the menu's "?" can carry a mark. */
export const supportUnreadQuery = queryOptions({
  queryKey: TICKETS,
  queryFn: readTickets,
  staleTime: QUIET_STALE_MS,
  select: (d: SupportList) => (d.enabled ? d.unread : 0),
});

const ended = (t: SupportTicket) => t.status === 'closed' || t.status === 'gone';
const lastAt = (t: SupportTicket) => Date.parse(t.messages?.at(-1)?.created_at ?? t.created_at) || 0;

/** The ticket written on most recently that is still open (none: the next message starts one). */
export function latestOpen(list: SupportList | undefined): SupportTicket | undefined {
  return [...(list?.tickets ?? [])].filter((t) => !t.pending && !ended(t)).sort((a, b) => lastAt(b) - lastAt(a))[0];
}

/* A read that lands while a send is on its way must not wipe the row it shows. */
function keepPending(next: SupportList, held: SupportList | undefined): SupportList {
  if (!held) return next;
  const tmpTickets = held.tickets.filter((t) => t.pending);
  const tickets = next.tickets.map((t) => {
    const was = held.tickets.find((h) => h.id === t.id);
    const mine = was?.messages?.filter((m) => m.pending) ?? [];
    return mine.length ? { ...t, messages: [...(t.messages ?? []), ...mine] } : t;
  });
  return { ...next, tickets: [...tmpTickets, ...tickets] };
}

/** While the chat is open: ask Tico support for replies (the ticket in view first) and keep the answer as the list. */
export const refreshQuery = queryOptions({
  queryKey: ['support', 'refresh'] as const,
  queryFn: async () => {
    const shown = latestOpen(queryClient.getQueryData(ticketsQuery.queryKey))?.id;
    const next = await api.post<SupportList>('/api/v2/support/tickets/refresh', shown ? { ticket: shown } : undefined);
    queryClient.setQueryData(ticketsQuery.queryKey, (held: SupportList | undefined) => keepPending(next, held));
    return { failed: next.refresh_failed === true };
  },
  refetchInterval: 60_000,
  refetchIntervalInBackground: false,
  retry: false,
});

export const composeQuery = queryOptions({
  queryKey: ['support', 'compose'] as const,
  queryFn: ({ signal }) => api.get<SupportCompose>('/api/v2/support/compose', { signal }),
  staleTime: Infinity,
});

/** `hours` of each computer's performance go with it (0: none). Built fresh each time it is opened. */
export const diagnosticsQuery = (hours: number) =>
  queryOptions({
    queryKey: ['support', 'diagnostics', hours] as const,
    // What failed in this browser goes with it when anything did (POST capture); otherwise the plain read.
    queryFn: ({ signal }) => {
      const browser = browserFailures();
      return browser.length
        ? api.post<DiagnosticsBundle>('/api/v2/support/diagnostics/capture', { browser, performance_hours: hours }, { signal })
        : api.get<DiagnosticsBundle>(`/api/v2/support/diagnostics?performance_hours=${hours}`, { signal });
    },
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });

// ---------------------------------------------------------------- writes

const sum = (tickets: SupportTicket[]) => tickets.reduce((n, t) => n + (t.unread ?? 0), 0);

/** Without the local row a send left (a retry puts it back fresh). */
function dropRow(v: SupportList, rowId: string): SupportList {
  return {
    ...v,
    tickets: v.tickets
      .filter((t) => t.id !== rowId)
      .map((t) => (t.messages?.some((m) => m.id === rowId) ? { ...t, messages: t.messages.filter((m) => m.id !== rowId) } : t)),
  };
}

function markRow(v: SupportList, rowId: string, pending: 'failed'): SupportList {
  return {
    ...v,
    tickets: v.tickets.map((t) =>
      t.id === rowId ? { ...t, pending } : { ...t, messages: t.messages?.map((m) => (m.id === rowId ? { ...m, pending } : m)) },
    ),
  };
}

/* Tico support refuses a follow-up to a ticket that is full, closed or gone: the same message starts a new one, once. */
const refused = (e: unknown) =>
  e instanceof ApiError && (e.status === 404 || (e.status === 409 && (e.code === 'support_closed' || e.code === 'http_409')));

export interface Send {
  message: string;
  to: To;
  /** The send's own key: a retry after a lost answer repeats it, so nothing lands twice. */
  key: string;
  /** A reviewed diagnostics preview's id. */
  diagnostics?: string;
  /** The preview's text as the person edited it: checked and redacted again by the server before it goes. */
  edited?: string;
}

interface Sent {
  ticket: SupportTicket;
  /** The ticket a refused follow-up was meant for: closed now. */
  closed?: string;
}

/* What the person reads under the box: a refusal goes on as the server's own error (the action layer reads its words,
   filtered), a failed server as "could not reach". */
function said(e: unknown): unknown {
  if (e instanceof ApiError && e.status >= 400 && e.status < 500) return e;
  if (e instanceof ApiError) return Object.assign(new Error('Could not reach Tico support'), { code: 'unreachable: Could not reach Tico support' });
  return e;
}

/** A message to Tico support: shown at once, sent after; refused, it stays in the list as not sent, with Retry. */
export function sendToSupport(o: Send): Promise<Sent> {
  return sendOnce(o).catch((e: unknown) => Promise.reject(said(e)));
}

function sendOnce({ message, to, key, diagnostics, edited }: Send): Promise<Sent> {
  const held = queryClient.getQueryData(ticketsQuery.queryKey);
  const target = to === 'new' ? '' : to === 'latest' ? (latestOpen(held)?.id ?? '') : to;
  const row = `tmp:${key}`;
  const at = new Date().toISOString();
  const retry: Retry = { to: target || 'new', key, ...(diagnostics && { diagnostics }), ...(edited !== undefined && { edited }) };
  return write<Sent>({
    key: ticketsQuery.queryKey,
    optimistic: (v: SupportList | undefined) => {
      const base = dropRow(v ?? { enabled: true, tickets: [], unread: 0 }, row);
      if (!target) {
        const t: SupportTicket = { id: row, status: 'open', message, created_at: at, messages: [], pending: 'sending', retry, ...(diagnostics && { sent: { diagnostics: true } }) };
        return { ...base, tickets: [t, ...base.tickets] };
      }
      const m: SupportMessage = { id: row, from: 'person', body: message, created_at: at, pending: 'sending', retry, has_diagnostics: !!diagnostics };
      return { ...base, tickets: base.tickets.map((t) => (t.id === target ? { ...t, messages: [...(t.messages ?? []), m] } : t)) };
    },
    send: async () => {
      let attach = diagnostics ?? '';
      if (attach && edited !== undefined) attach = (await api.post<DiagnosticsBundle>('/api/v2/support/diagnostics', { id: attach, text: edited })).id;
      const fresh = (k: string) => {
        const email = queryClient.getQueryData(composeQuery.queryKey)?.email || queryClient.getQueryData(meQuery.queryKey)?.email || '';
        return api.post<SupportTicket>('/api/v2/support/tickets', { message, email, include_ids: true, diagnostics: attach }, { idempotencyKey: k });
      };
      if (!target) return { ticket: await fresh(key) };
      try {
        return { ticket: await api.post<SupportTicket>(`/api/v2/support/tickets/${enc(target)}/messages`, { message, diagnostics: attach }, { idempotencyKey: key }) };
      } catch (e) {
        if (!refused(e)) throw e;
        return { ticket: await fresh(`${key}-new`.slice(0, 200)), closed: target };
      }
    },
    settle: (v: SupportList | undefined, { ticket, closed }: Sent) => {
      if (!v) return v;
      const rest = dropRow(v, row).tickets.filter((t) => t.id !== ticket.id).map((t) => (t.id === closed ? { ...t, status: 'closed' } : t));
      const tickets = [ticket, ...rest];
      return { ...v, tickets, unread: sum(tickets) };
    },
    revert: (v: SupportList | undefined) => v && markRow(v, row, 'failed'),
  });
}

/** Every reply on a ticket read: the chat shows them all. */
export function markTicketRead(ticket: string) {
  return write<SupportTicket>({
    key: ticketsQuery.queryKey,
    optimistic: (v: SupportList | undefined) => {
      if (!v) return v;
      const tickets = v.tickets.map((t) => (t.id === ticket ? { ...t, unread: 0 } : t));
      return { ...v, tickets, unread: sum(tickets) };
    },
    send: (idempotencyKey) => api.post<SupportTicket>(`/api/v2/support/tickets/${enc(ticket)}/read`, undefined, { idempotencyKey }),
    settle: (v: SupportList | undefined, answer: SupportTicket) => {
      if (!v) return v;
      const tickets = v.tickets.map((t) => (t.id === answer.id ? { ...answer, messages: [...(answer.messages ?? []), ...(t.messages?.filter((m) => m.pending) ?? [])] } : t));
      return { ...v, tickets, unread: sum(tickets) };
    },
  });
}

/** Delete every one of the person's tickets, here and at Tico support. The list holds at most 50: delete and list
    again until it comes back empty. */
export function deleteHistory() {
  return write<void>({
    key: ticketsQuery.queryKey,
    optimistic: (v: SupportList | undefined) => v && { ...v, tickets: [], unread: 0 },
    send: async () => {
      let left = (await api.get<SupportList>('/api/v2/support/tickets')).tickets;
      for (let round = 0; round < 20 && left.length; round++) {
        for (const t of left) {
          await api.delete(`/api/v2/support/tickets/${enc(t.id)}`).catch((e: unknown) => {
            if (!(e instanceof ApiError && e.status === 404)) throw e;
          });
        }
        left = (await api.get<SupportList>('/api/v2/support/tickets')).tickets;
      }
      if (left.length) throw new Error(`${left.length} ${left.length === 1 ? 'conversation' : 'conversations'} could not be deleted. Try again.`);
    },
  });
}
