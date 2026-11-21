import { useQueries, useQuery } from '@tanstack/react-query';
import type { ChatMessage } from '@/conversation';
import { useActor, useDirectory } from '@/data';
import { Icon } from '@/kit/Icon';
import { besideThread } from '@/lib';
import { flowQuery, type FlowItem } from '@/features/tasks/routing';
import { AskCard } from './AskCard';
import { decisionOf, emailOf, personOf, sendTitle, tabAddress, thingsOf } from './model';
import { taskDetailQuery, taskFilesQuery, type TaskData } from './queries';
import { matterOf } from './Waiting';
import { sendTasksOf, sendToGo } from './waits';

/* An approval an agent asked for that waits on the person, as a card in the agent's thread at the time it was asked
   (plan/threads-team.html: an approval waiting, as a link in a thread). An OK the agent asked for (an email, a finance
   item, a file) or a page its task's stage needs approved (a page OK, asked by no one: the task's owner holds it). The
   card only points: Approve stays where the thing itself is on screen, the case's Email tab, the Send task's page, the
   task's page that opens on what waits. Answered, sent or canceled, it drops off. */

/** Chips a card shows before it counts the rest. */
const CHIPS = 3;

interface Approval {
  /** The OK task. */
  task: string;
  /** The email it shows: its send's id. */
  send: string | null;
  /** The thread the card sits in, as the shell's `?chat=`: what it opens comes beside that thread (plan/layers.html). */
  chat?: string;
}

const approvalOf = (m: ChatMessage): Approval | null => (m.kind === 'approval' ? ((m.refs?.approval as Approval | undefined) ?? null) : null);
const emailIn = (shows: readonly string[]) => shows.find((x) => x.startsWith('email:'))?.slice(6) || null;

/** Which approvals a thread shows: those `agent` asked for (the person's DM with it, or its Home; its id or slug), or
    those hung on `task` (a case's chat: its "Send:" OKs). */
export type ApprovalScope = { agent: string | undefined } | { task: TaskData | undefined };

/** Each open approval in scope that waits on the viewer, as a row for the conversation's `extra`, once what it shows is
    read (so a card never draws half, and an email already sent or canceled never flashes). `chat`: the thread the
    cards sit in (a `?chat=` address), so what one opens comes beside it; left out, it opens as a page of its own. */
export function useApprovalCards(scope: ApprovalScope, chat?: string): ChatMessage[] {
  // An agent by its slug (`bot:<slug>`, a chat address) is the same agent as by its id, which an ask names.
  const named = 'agent' in scope ? scope.agent : undefined;
  const agent = useActor(named)?.id ?? named;
  const task = 'task' in scope ? scope.task : undefined;
  // A case's chat: its "Send:" OKs, and the email OKs on the case itself (no "Send:" task of their own).
  const own = !!task && !!task.to_leave?.some((r) => !!emailOf(r) && !r.met);
  const hung = task ? new Set([...sendTasksOf(task), ...(own ? [task.id] : [])]) : null;
  const on = !!agent?.startsWith('bot:') || !!hung?.size;
  const { data } = useQuery({ ...flowQuery, enabled: on });
  // An ask names its agent by id or by slug (`bot:finance-owner`): both read as the agent's id here.
  const byId = useDirectory((d) => d.byId);
  const whose = (it: FlowItem) => {
    // no asker named (a page its stage needs approved): the task's owner holds it, as Needs you groups it (views.needs_actor)
    const by = it.ask?.by ?? (it.ask?.kind === 'page' ? it.owner_id : null);
    return by?.startsWith('bot:') ? (byId?.[by]?.id ?? by) : null;
  };
  const asks = (on ? (data?.items ?? []) : []).filter((it) =>
    hung
      ? it.ask?.kind === 'ok' && !!whose(it) && hung.has(it.id) && (it.id !== task?.id || !!emailIn(it.ask?.shows ?? []))
      : (it.ask?.kind === 'ok' || it.ask?.kind === 'page') && !!agent && whose(it) === agent,
  );
  const subs = useQueries({ queries: asks.map((it) => ({ ...taskDetailQuery(it.id), retry: false })) });
  // The files any other OK shows (its chips, ThingCard), read before the card draws.
  const files = useQueries({
    queries: asks.map((it, i) => {
      const sub = subs[i]?.data;
      return { ...taskFilesQuery(it.id), enabled: !!sub && !emailIn(it.ask?.shows ?? []) && thingsOf(decisionOf(sub)).size > 0, retry: false };
    }),
  });
  return asks.flatMap((it, i) => {
    const sub = subs[i]?.data;
    const send = emailIn(it.ask?.shows ?? []);
    // An email's card stands for that email's own open OK on the task, never another ask there.
    if (!sub || !decisionOf(sub, send ?? undefined)) return [];
    // An email's: still to go out, as its OK task's row says (sendToGo); its preview is read only on its Email tab.
    if (send) {
      if (!sendToGo(sub, send)) return [];
    } else if (files[i]?.isPending && files[i]?.fetchStatus !== 'idle') return [];
    // The words stand for the card where words are read (a row with none is not drawn).
    const row: ChatMessage = {
      id: `approval:${it.id}`,
      sender_id: whose(it) ?? undefined,
      created_at: it.since,
      kind: 'approval',
      body: `Approve: ${it.title}`,
      refs: { approval: { task: it.id, send, chat } },
    };
    return [row];
  });
}

/** What a conversation draws for an approval's row (its `card`); nothing for any other message. */
export function approvalCard(m: ChatMessage) {
  const a = approvalOf(m);
  return a ? <ApprovalCard {...a} /> : null;
}

function ApprovalCard({ task, send, chat }: Approval) {
  const sub = useQuery(taskDetailQuery(task)).data;
  if (!sub) return null;
  return send ? <EmailCard sub={sub} chat={chat} /> : <ThingCard sub={sub} chat={chat} />;
}

/** Any other OK (a page, a file, a finance item): the task's title, and the files it shows as chips. It opens the task's
    page, which opens on what waits on the person. */
function ThingCard({ sub, chat }: { sub: TaskData; chat?: string }) {
  const shown = thingsOf(decisionOf(sub));
  const files = useQuery({ ...taskFilesQuery(sub.id), enabled: shown.size > 0, retry: false }).data ?? [];
  const chips = files.filter((f) => shown.has(f.id)).map((f) => ({ key: f.id, name: f.title || f.name, type: typeOf(f.name, undefined) }));
  return <Card to={tabAddress(sub, undefined, false).to} chat={chat} what={sub.title} files={chips} />;
}

/** An email's OK: the case it answers (else the subject) and who it goes to, from the OK task itself (its title and its
    parent or the case that waits on it, `matterOf`): the email's preview is read only on its Email tab, since that
    read counts as showing it. It opens on the case's Email tab, or on the Send task's own page when the case has more
    than one email (the tab would be a guess). */
function EmailCard({ sub, chat }: { sub: TaskData; chat?: string }) {
  const matter = matterOf(sub);
  const held = useQuery({ ...taskDetailQuery(matter?.id), retry: false }).data;
  const one = !!matter && (held?.id !== matter.id || sendTasksOf(held).length <= 1);
  const to = one ? matter.to : tabAddress(sub, undefined, false).to;
  const title = sendTitle(sub.title);
  const what = matter?.title ?? title?.subject ?? sub.title;
  return <Card to={to} chat={chat} what={title?.to ? `${what}, to ${personOf(title.to)}` : what} />;
}

/** A file's kind as a chip names it: from its media type, else its name's ending ("PDF"). */
const typeOf = (name: string, mime: string | undefined) => (mime?.split('/')[1] ?? (name.includes('.') ? name.split('.').pop() : '') ?? '').toUpperCase();

interface Chip {
  key: string;
  name: string;
  type: string;
}

function Card({ to, chat, what, files = [] }: { to: string; chat?: string; what: string; files?: Chip[] }) {
  const more = files.length - CHIPS;
  return (
    <AskCard name="Approve" href={besideThread(to, chat)} target={`approval:${to}`} end={<Icon name="chevron" />}>
      <p className="askcard-what">{what}</p>
      {files.length > 0 && (
        <span className="askcard-files">
          {files.slice(0, more > 1 ? CHIPS : files.length).map((f) => (
            <span key={f.key} className="file">
              <Icon name="file" />
              <span>{f.name}</span>
              {f.type && <em>{f.type}</em>}
            </span>
          ))}
          {more > 1 && <span className="file is-more">+{more}</span>}
        </span>
      )}
    </AskCard>
  );
}
