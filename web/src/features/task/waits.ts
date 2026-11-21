import type { QueryClient } from '@tanstack/react-query';
import { decisionOf, emailOf } from './model';
import { taskDetailQuery, type TaskData } from './queries';

/* An email's OK waits on the matter itself now (backend/email_sending.py request_approval), in its own requirements.
   An older send's OK is a task of its own ("Send: …"): a sub-task of the matter, or a task on its own the agent linked
   to it; these rules keep those answerable. Plain rules here, so the route can open the matter on the email before the
   page's code loads. */

/** An email's OK task, as the server titles it ("Send: <subject> → <to>", or an older "Send an email"). */
export const isSendTask = (title: string | null | undefined): boolean => /^send(:|\s+an\s+email\s*$)/i.test((title ?? '').trim());

/** The open OK tasks hung on a task: its open "Send:" sub-tasks, and the ones linked to it. */
export function sendTasksOf(t: Pick<TaskData, 'id' | 'children' | 'relations'>): string[] {
  const ids = new Set<string>();
  for (const c of t.children) if (c.status === 'open' && isSendTask(c.title)) ids.add(c.id);
  for (const list of Object.values(t.relations ?? {})) for (const r of list) if (r.status === 'open' && isSendTask(r.title)) ids.add(r.id);
  ids.delete(t.id);
  return [...ids];
}

/** A send still to go out: one sent, canceled or failed is no longer asked about, whatever its OK task says. */
export const stillToGo = (state: string | null | undefined): boolean => !state || state === 'pending' || state === 'waiting_approval';

/** The slugs of the emails' tabs: "email", or "email-1", "email-2" for more than one; none taken twice. */
export function waitSlugs(count: number, taken: Iterable<string>): string[] {
  const used = new Set(taken);
  return Array.from({ length: count }, (_, i) => {
    let slug = count > 1 ? `email-${i + 1}` : 'email';
    while (used.has(slug)) slug += '-2';
    used.add(slug);
    return slug;
  });
}

/** Whether the email `send` an open OK shows is still to go out: from the task's row (`email_send`, the newest send of
    the task) when that row is this send; another send there (a newer one canceled, say) says nothing of this one, and
    the OK still open says it waits (an OK goes with its send). Its preview is read only when its tab opens, since that
    read counts as showing it to the person. */
export const sendToGo = (sub: Pick<TaskData, 'email_send'>, send: string): boolean => sub.email_send?.id !== send || stillToGo(sub.email_send.state);

/** Whether an email's OK hung on the task waits on the person now: the matter then opens on it. */
export async function waitsOnYou(client: QueryClient, t: TaskData): Promise<boolean> {
  const found = await Promise.all(
    sendTasksOf(t).map(async (id) => {
      try {
        const sub = await client.ensureQueryData(taskDetailQuery(id));
        const send = emailOf(decisionOf(sub));
        return !!send && sendToGo(sub, send);
      } catch {
        return false;
      }
    }),
  );
  return found.some(Boolean);
}

/** Whether anything on a task waits on the person now, from what is already read: its own ask, or an email's OK hung
    on it whose email is still to go out. */
export function waitsNow(client: QueryClient, id: string): boolean {
  const t = client.getQueryData(taskDetailQuery(id).queryKey);
  if (!t) return false;
  if (decisionOf(t)) return true;
  return sendTasksOf(t).some((sid) => {
    const sub = client.getQueryData(taskDetailQuery(sid).queryKey);
    const send = sub && emailOf(decisionOf(sub));
    return !!sub && !!send && sendToGo(sub, send);
  });
}
