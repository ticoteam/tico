import { useQueries, useQuery } from '@tanstack/react-query';
import type { ChatMessage } from '@/conversation';
import { useActor } from '@/data';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { flowQuery } from '@/features/tasks/routing';
import { decisionOf, personOf, sendTitle, tabAddress } from './model';
import { addressesOf, emailSendQuery, taskDetailQuery, type EmailFile, type TaskData } from './queries';
import { useMatterOf } from './Waiting';
import { sendTasksOf, stillToGo } from './waits';

/* An approval an agent asked for that waits on the person, as a card in the agent's thread at the time it was asked
   (plan/threads-team.html: an approval waiting, as a link in a thread). The card only points: Approve stays where the
   email itself is on screen, the case's Email tab or the Send task's page. Answered, sent or canceled, it drops off. */

/** Chips a card shows before it counts the rest. */
const CHIPS = 3;

interface Approval {
  /** The OK task. */
  task: string;
  /** The email it shows: its send's id. */
  send: string | null;
}

const approvalOf = (m: ChatMessage): Approval | null => (m.kind === 'approval' ? ((m.refs?.approval as Approval | undefined) ?? null) : null);
const emailIn = (shows: readonly string[]) => shows.find((x) => x.startsWith('email:'))?.slice(6) || null;

/** Which approvals a thread shows: those `agent` asked for (the person's DM with it, or its Home; its id or slug), or
    those hung on `task` (a case's chat: its "Send:" OKs). */
export type ApprovalScope = { agent: string | undefined } | { task: TaskData | undefined };

/** Each open approval in scope that waits on the viewer, as a row for the conversation's `extra`, once what it shows is
    read (so a card never draws half, and an email already sent or canceled never flashes). */
export function useApprovalCards(scope: ApprovalScope): ChatMessage[] {
  // An agent by its slug (`bot:<slug>`, a chat address) is the same agent as by its id, which an ask names.
  const named = 'agent' in scope ? scope.agent : undefined;
  const agent = useActor(named)?.id ?? named;
  const task = 'task' in scope ? scope.task : undefined;
  const hung = task ? new Set(sendTasksOf(task)) : null;
  const on = !!agent?.startsWith('bot:') || !!hung?.size;
  const { data } = useQuery({ ...flowQuery, enabled: on });
  const asks = (on ? (data?.items ?? []) : []).filter(
    (it) => it.ask?.kind === 'ok' && !!it.ask.by?.startsWith('bot:') && (hung ? hung.has(it.id) : it.ask.by === agent),
  );
  const subs = useQueries({ queries: asks.map((it) => ({ ...taskDetailQuery(it.id), retry: false })) });
  const mails = useQueries({
    queries: asks.map((it) => {
      const send = emailIn(it.ask?.shows ?? []);
      return { ...emailSendQuery(it.id, send ?? ''), enabled: !!send, retry: false };
    }),
  });
  // The case an older send names by its task (useMatterOf): read before the card draws, so its title does not change.
  const froms = useQueries({
    queries: asks.map((it, i) => {
      const from = mails[i]?.data?.send.task_id;
      return { ...taskDetailQuery(from && from !== it.id ? from : undefined), retry: false };
    }),
  });
  return asks.flatMap((it, i) => {
    const sub = subs[i]?.data;
    if (!sub || !decisionOf(sub)) return [];
    const send = emailIn(it.ask?.shows ?? []);
    if (send) {
      const mail = mails[i];
      if (!mail?.data || !stillToGo(mail.data.send.state)) return [];
      const from = mail.data.send.task_id;
      if (from && from !== it.id && froms[i]?.isPending && froms[i]?.fetchStatus !== 'idle') return [];
    }
    // The words stand for the card where words are read (a row with none is not drawn).
    const row: ChatMessage = {
      id: `approval:${it.id}`,
      sender_id: it.ask?.by ?? undefined,
      created_at: it.since,
      kind: 'approval',
      body: `Approve: ${it.title}`,
      refs: { approval: { task: it.id, send } },
    };
    return [row];
  });
}

/** What a conversation draws for an approval's row (its `card`); nothing for any other message. */
export function approvalCard(m: ChatMessage) {
  const a = approvalOf(m);
  return a ? <ApprovalCard {...a} /> : null;
}

function ApprovalCard({ task, send }: Approval) {
  const sub = useQuery(taskDetailQuery(task)).data;
  if (!sub) return null;
  return send ? <EmailCard sub={sub} send={send} /> : <Card to={tabAddress(sub, undefined, false).to} what={sub.title} />;
}

/** An email's OK: the case it answers (else the subject) and who it goes to, its files as chips. It opens on the case's
    Email tab, or on the Send task's own page when the case has more than one email (the tab would be a guess). */
function EmailCard({ sub, send }: { sub: TaskData; send: string }) {
  const mail = useQuery(emailSendQuery(sub.id, send)).data;
  const matter = useMatterOf(sub, send);
  const held = useQuery({ ...taskDetailQuery(matter?.id), retry: false }).data;
  const one = !!matter && (held?.id !== matter.id || sendTasksOf(held).length <= 1);
  const to = one ? matter.to : tabAddress(sub, undefined, false).to;
  const title = sendTitle(sub.title);
  const address = (mail && addressesOf(mail).to[0]) ?? title?.to;
  const what = matter?.title ?? title?.subject ?? sub.title;
  return <Card to={to} what={address ? `${what}, to ${personOf(address)}` : what} files={mail?.files ?? []} />;
}

const typeOf = (f: EmailFile) => (f.type?.split('/')[1] ?? f.name.split('.').pop() ?? '').toUpperCase();

function Card({ to, what, files = [] }: { to: string; what: string; files?: EmailFile[] }) {
  const more = files.length - CHIPS;
  return (
    <Link className="kcard is-link" href={to} data-target={`approval:${to}`}>
      <span className="kh">
        <FlowMark size={10} />
        Approve
        <span className="grow" />
        <Icon name="chevron" className="end" />
      </span>
      <p>{what}</p>
      {files.length > 0 && (
        <span className="kfiles">
          {files.slice(0, more > 1 ? CHIPS : files.length).map((f) => (
            <span key={`${f.part}:${f.name}`} className="file">
              <Icon name="file" />
              <span>{f.name}</span>
              {typeOf(f) && <em>{typeOf(f)}</em>}
            </span>
          ))}
          {more > 1 && <span className="file is-more">+{more}</span>}
        </span>
      )}
    </Link>
  );
}
