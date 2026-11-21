import { useQueries, useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Markdown } from '@/kit/Markdown';
import { answeringOf, changesAskedOf, decisionName, decisionOf, emailOf, personOf, sendTitle, tabAddress } from './model';
import { AskCard } from './AskCard';
import { Go } from './parts';
import { addressesOf, emailSendQuery, taskDetailQuery, type TaskData } from './queries';
import { isSendTask, sendTasksOf, stillToGo } from './waits';

/* An email's OK hung on a matter (waits.ts): the matter opens on the email, its brief first, then the decision over the
   email itself; its other pages point to it (no Approve where the email is not on screen). The email's OK names the
   matter it came from, with the matter's brief. */

export interface Waiting {
  /** The OK task. */
  sub: TaskData;
  /** The email it shows: its send's id. */
  send: string | null;
}

/** Each email's OK hung on a task that waits on the person (or that they asked changes on, or whose answer is being
    sent), once its send is read, so one already sent or canceled never flashes. `settled` once every read is in. An
    answer in flight keeps it: its tab, and the decision on it, stay until the server says, so a refusal shows there. */
export function useWaiting(t: TaskData): { items: Waiting[]; settled: boolean } {
  const me = useQuery(meQuery).data?.id;
  const reads = useQueries({ queries: sendTasksOf(t).map((id) => ({ ...taskDetailQuery(id), retry: false })) });
  const subs = reads
    .flatMap((q) => (q.data ? [q.data] : []))
    .flatMap((sub) => {
      const r = decisionOf(sub) ?? changesAskedOf(sub, me) ?? answeringOf(sub);
      return r ? [{ sub, send: emailOf(r) }] : [];
    });
  const mails = useQueries({ queries: subs.map(({ sub, send }) => ({ ...emailSendQuery(sub.id, send ?? ''), enabled: !!send })) });
  const items = subs.filter(({ send }, i) => {
    if (!send) return true;
    const m = mails[i];
    if (!m || m.isPending) return false;
    return stillToGo(m.data?.send.state);
  });
  const settled = reads.every((q) => !q.isPending) && subs.every(({ send }, i) => !send || !mails[i]?.isPending);
  return { items, settled };
}

/** An email that waits on the person, as a pointer on the matter's other pages: the email is not on screen there, so
    there is no Approve; Open goes to the Email tab, where the decision sits over the email itself. */
function Pointer({ sub, send, to }: { sub: TaskData; send: string; to: string }) {
  const navigate = useNavigate();
  const mail = useQuery(emailSendQuery(sub.id, send));
  const r = decisionOf(sub);
  if (!r) return null;
  const who = (mail.data && addressesOf(mail.data).to[0]) ?? sendTitle(sub.title)?.to;
  return <AskCard name={decisionName(sub, r, who ? personOf(who) : undefined)} label="Waits on you" end={<Button onClick={() => navigate({ to: to as never })}>Open</Button>} />;
}

/** At the top of a task's other pages: each email that waits on the person, pointing to its Email tab. */
export function WaitingOnYou({ items }: { items: { sub: TaskData; send: string; to: string }[] }) {
  if (!items.some((x) => decisionOf(x.sub))) return null;
  return (
    <div className="tv-waits">
      {items.map((x) => (
        <Pointer key={x.sub.id} {...x} />
      ))}
    </div>
  );
}

/** A task's note, the case in brief, read before what it asks: nothing when it has none. */
export function Brief({ note, title, to }: { note: string | null | undefined; title?: string; to?: string }) {
  const text = (note ?? '').trim();
  if (!text) return null;
  return (
    <section className="tv-brief" aria-label="Brief">
      {title && to && (
        <Go to={to} className="tv-brief-from">
          {title}
        </Go>
      )}
      <div className="tv-brief-text">
        <Markdown text={text} compact />
      </div>
    </section>
  );
}

/** On an email's OK: the matter it answers, by name, and its brief, above the email; nothing when it has no note. */
export function MatterBrief({ t, send }: { t: TaskData; send: string }) {
  const matter = useMatterOf(t, send);
  const read = useQuery({ ...taskDetailQuery(matter?.id), retry: false });
  const m = read.data?.id === matter?.id ? read.data : undefined;
  if (!matter || !m) return null;
  return <Brief note={m.note} title={m.title} to={tabAddress(m, undefined, false).to} />;
}

/** The matter an email's OK task came from: the OK's own parent; else the send's task (`task_id`), or that task's
    parent when it is an earlier OK itself; else a task that waits on this OK. Null when there is none to name. */
export function useMatterOf(t: TaskData, send: string): { id: string; title: string; to: string } | null {
  const mail = useQuery(emailSendQuery(t.id, send));
  const own = t.parent && t.parent.id !== t.id && !isSendTask(t.parent.title) ? t.parent : null;
  const id = mail.data?.send.task_id;
  const other = !own && id && id !== t.id ? id : undefined;
  const src = useQuery({ ...taskDetailQuery(other), enabled: !!other, retry: false });
  const byId = (x: { id: string; title: string }, at?: TaskData) => ({ id: x.id, title: x.title, to: at ? tabAddress(at, undefined, false).to : `/task/${encodeURIComponent(x.id)}` });
  if (own) return byId(own);
  const s = src.data?.id === other ? src.data : undefined;
  if (s && !isSendTask(s.title)) return byId(s, s);
  if (s?.parent && s.parent.id !== t.id && !isSendTask(s.parent.title)) return byId(s.parent);
  if (other && !src.isFetched) return null;
  const waits = Object.entries(t.relations ?? {})
    .flatMap(([kind, list]) => (kind === 'parent' ? [] : list))
    .find((r) => r.direction === 'in' && r.id !== t.id && !isSendTask(r.title));
  return waits ? byId(waits) : null;
}
