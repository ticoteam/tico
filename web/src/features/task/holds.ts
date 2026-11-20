import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import { queryClient, write } from '@/data';
import { flowQuery } from '@/features/tasks/routing';

/* What a company rule held (backend/org_rule_check.py): a message, an email, a task change an agent wrote that broke a
   rule set to Ask first. Its review task ("Held by: <rule>", labelled rule-review) waits on whoever reviews the rule,
   and each hold on it is decided there: Send anyway (once, with the hold's one-shot nonce) or Keep blocked. The server
   decides who may: the rule's owner or editors, the reviewer it names, the company's owner, an Admin. Read under the
   Rules page's quiet root, so a decision here reads that page's holds again too. */

const enc = encodeURIComponent;

/** A review task's label (org_rule_check.REVIEW_LABEL). */
export const REVIEW_LABEL = 'rule-review';

export interface HoldRule {
  id: string;
  kind: 'internal' | 'external' | string;
  text: string;
  if_broken?: 'block' | 'escalate' | 'warn' | string | null;
  /** The rule's Why, said when it acts (an opened hold only). */
  description?: string | null;
  /** The part of the held text that breaks it, when one part does (an opened hold only). */
  part?: string | null;
}

/** A hold as its reader sees it: a list's row (no text, no nonce) or one opened (`GET /api/v2/rule-holds/<id>`). */
export interface Hold {
  id: string;
  /** What was held: `mail`, `message`, `threads`, `task_create`… (org_rule_check.OPERATIONS). */
  operation: string;
  author: string;
  author_name?: string | null;
  /** `held` until decided, then `kept` or `sent`. */
  state: 'held' | 'kept' | 'sent' | string;
  outcome: 'blocked' | 'escalated' | string;
  rules: HoldRule[];
  /** Who it was for, by name. */
  to: string[];
  task_id: string | null;
  created_at: string;
  /** This reader may send it anyway: they review it, it is still held and it can be written again from here. */
  can_send: boolean;
  can_withdraw: boolean;
  /** The held text, cut past 20,000 characters (`text_cut`, the whole length); Send anyway sends it whole. */
  text?: string;
  text_cut?: number;
  /** The author's appeal, to its reviewer. */
  why?: string;
  /** Send anyway's one-shot key, given to a reviewer while it is held. */
  nonce?: string;
  part_pending?: boolean;
  /** This reader's copy leaves out words they may not read (a private task's): someone who can read all of it sends
      it. What was left out, and how much, is never said. */
  partial?: boolean;
}

/** A review task's holds, any state, as summaries (the first page: a review groups one rule's holds for one person). */
export const taskHoldsQuery = (task: string) =>
  queryOptions({
    queryKey: ['rules', 'holds', 'task', task] as const,
    queryFn: async ({ signal }) => (await api.get<{ holds: Hold[] }>(`/api/v2/rule-holds?task=${enc(task)}`, { signal })).holds,
  });

/** One hold opened: its text, each rule's Why and the part that breaks it, and the nonce for its reviewer. */
export const holdQuery = (id: string) =>
  queryOptions({
    queryKey: ['rules', 'holds', 'one', id] as const,
    queryFn: ({ signal }) => api.get<Hold>(`/api/v2/rule-holds/${enc(id)}`, { signal }),
  });

/** Whether another hold on the task still waits, besides `id`, from what is read. */
export const othersHeld = (task: string, id: string): boolean =>
  (queryClient.getQueryData(taskHoldsQuery(task).queryKey) ?? []).some((h) => h.id !== id && h.state === 'held');

/** Keep it blocked, or send it anyway with its nonce. Shown once the server took it (the box stays, busy, until then;
    a refusal is said in it). The last hold decided takes the task out of Flow: the server closes its review. */
export function decideHold(task: string, id: string, how: 'keep' | 'send', nonce?: string): Promise<unknown> {
  const last = !othersHeld(task, id);
  const state = how === 'keep' ? 'kept' : 'sent';
  const decided = (h: Hold): Hold => (h.id === id ? { ...h, state, can_send: false, can_withdraw: false, nonce: undefined } : h);
  return write<unknown>({
    key: [taskHoldsQuery(task).queryKey, holdQuery(id).queryKey, ...(last ? [flowQuery.queryKey] : [])],
    wait: true,
    optimistic: (cur: unknown) => {
      if (Array.isArray(cur)) return (cur as Hold[]).map(decided);
      const f = cur as { items?: { id: string }[]; count?: number } | Hold | undefined;
      if (f && 'items' in f && f.items) {
        const items = f.items.filter((x) => x.id !== task);
        return { ...f, items, count: items.length };
      }
      return f && 'operation' in f ? decided(f) : cur;
    },
    send: (idempotencyKey) => api.post(`/api/v2/rule-holds/${enc(id)}/${how}`, how === 'send' ? { nonce } : {}, { idempotencyKey }),
  });
}
