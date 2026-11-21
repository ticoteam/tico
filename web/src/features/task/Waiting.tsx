import { useQueries, useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { dateLine } from '@/features/company/exports';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Markdown } from '@/kit/Markdown';
import { answeringOf, changesAskedOf, decisionName, decisionOf, emailOf, personOf, sendTitle, tabAddress } from './model';
import { AskCard } from './AskCard';
import { Go } from './parts';
import { emailSendQuery, taskDetailQuery, type TaskData } from './queries';
import { isSendTask, sendTasksOf, sendToGo } from './waits';

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
export function useWaiting(t: TaskData | undefined): { items: Waiting[]; settled: boolean } {
  const me = useQuery(meQuery).data?.id;
  const reads = useQueries({ queries: (t ? sendTasksOf(t) : []).map((id) => ({ ...taskDetailQuery(id), retry: false })) });
  const subs = reads
    .flatMap((q) => (q.data ? [q.data] : []))
    .flatMap((sub) => {
      const r = decisionOf(sub) ?? changesAskedOf(sub, me) ?? answeringOf(sub);
      return r ? [{ sub, send: emailOf(r) }] : [];
    });
  // Still to go out, as the OK task's row says (sendToGo): its preview is read only when its tab opens.
  const items = subs.filter(({ sub, send }) => !send || sendToGo(sub, send));
  const settled = reads.every((q) => !q.isPending);
  return { items, settled };
}

/** An email that waits on the person, as a pointer on the matter's other pages: the email is not on screen there, so
    there is no Approve; Open goes to the Email tab, where the decision sits over the email itself. */
function Pointer({ sub, to, search }: { sub: TaskData; send: string; to: string; search?: object }) {
  const navigate = useNavigate();
  const r = decisionOf(sub);
  if (!r) return null;
  // Named from the OK task's title ("Send: <subject> → <to>"): reading the email's preview counts as showing it.
  const who = sendTitle(sub.title)?.to;
  return <AskCard name={decisionName(sub, r, who ? personOf(who) : undefined)} label="Waits on you" end={<Button onClick={() => navigate({ to: to as never, search: search as never })}>Open</Button>} />;
}

/** At the top of a task's other pages: each email that waits on the person, pointing to its Email tab. */
export function WaitingOnYou({ items }: { items: { sub: TaskData; send: string; to: string; search?: object }[] }) {
  if (!items.some((x) => decisionOf(x.sub))) return null;
  return (
    <div className="tv-waits">
      {items.map((x) => (
        <Pointer key={x.sub.id} {...x} />
      ))}
    </div>
  );
}

/** At the top of a task's other pages: each email whose OK waits on the person on this task itself, pointing to its
    Email tab. Named without reading the email: a read of its preview counts as showing it to them. */
export function EmailPointers({ t, items }: { t: TaskData; items: { send: string; to: string; search?: object }[] }) {
  const navigate = useNavigate();
  if (!items.length) return null;
  return (
    <div className="tv-waits">
      {items.map((x) => {
        const r = decisionOf(t, x.send);
        return r ? <AskCard key={x.send} name={decisionName(t, r)} label="Waits on you" end={<Button onClick={() => navigate({ to: x.to as never, search: x.search as never })}>Open</Button>} /> : null;
      })}
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
  const self = matter?.id === t.id;
  const read = useQuery({ ...taskDetailQuery(self ? undefined : matter?.id), retry: false });
  // An OK on the case itself: the case's own brief, with no link to the page it is on.
  if (self)
    return (
      <>
        <Brief note={t.note} />
        <MatterDates matter={t.matter} />
      </>
    );
  const m = read.data?.id === matter?.id ? read.data : undefined;
  if (!matter || !m) return null;
  return (
    <>
      <Brief note={m.note} title={m.title} to={tabAddress(m, undefined, false).to} />
      <MatterDates matter={m.matter} />
    </>
  );
}

/** The matter's open key dates, one line above its email: each its label and dates ("Hearing: Not before Nov 25 · Due
    Dec 8"); a done date leaves the line, and nothing shows when none is open. */
export function MatterDates({ matter }: { matter: TaskData['matter'] }) {
  const open = (matter?.dates ?? []).filter((d) => !d.done_at);
  if (!open.length) return null;
  return (
    <p className="tv-dates">
      {open.map((d) => (
        <span key={d.id}>{d.label ? `${d.label}: ${dateLine(d)}` : dateLine(d)}</span>
      ))}
    </p>
  );
}

/** The matter an email's OK task came from, from the OK task alone: its own parent, else a task that waits on it.
    Null when there is none to name. An OK on a case itself (no "Send:" task) is the case's own. */
export function matterOf(t: TaskData): { id: string; title: string; to: string } | null {
  if (!isSendTask(t.title)) return { id: t.id, title: t.title, to: `/task/${encodeURIComponent(t.id)}` };
  const own = t.parent && t.parent.id !== t.id && !isSendTask(t.parent.title) ? t.parent : null;
  const waits = own
    ? null
    : Object.entries(t.relations ?? {})
        .flatMap(([kind, list]) => (kind === 'parent' ? [] : list))
        .find((r) => r.direction === 'in' && r.id !== t.id && !isSendTask(r.title));
  const found = own ?? waits;
  return found ? { id: found.id, title: found.title, to: `/task/${encodeURIComponent(found.id)}` } : null;
}

/** The matter an email's OK task came from: the OK's own parent; else the send's task (`task_id`), or that task's
    parent when it is an earlier OK itself; else a task that waits on this OK. Null when there is none to name. */
export function useMatterOf(t: TaskData, send: string): { id: string; title: string; to: string } | null {
  // An OK on a case itself (no "Send:" task) is the case's own: no reading the send to find another.
  const sendTask = isSendTask(t.title);
  const mail = useQuery({ ...emailSendQuery(t.id, send), enabled: sendTask });
  const own = t.parent && t.parent.id !== t.id && !isSendTask(t.parent.title) ? t.parent : null;
  const id = mail.data?.send.task_id;
  const other = !own && id && id !== t.id ? id : undefined;
  const src = useQuery({ ...taskDetailQuery(other), enabled: !!other, retry: false });
  const byId = (x: { id: string; title: string }, at?: TaskData) => ({ id: x.id, title: x.title, to: at ? tabAddress(at, undefined, false).to : `/task/${encodeURIComponent(x.id)}` });
  if (!sendTask) return byId(t, t);
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
