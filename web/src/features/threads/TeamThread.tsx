import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Conversation, MessageBones, type ChatMessage } from '@/conversation';
import { useDirectory } from '@/data';
import { callCard } from '@/features/calls/exports';
import { groupsQuery, teamSlug } from '@/features/team/routing';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { updateTitle, type Update } from './model';
import { teamThreadQuery, updateListQuery } from './queries';

/** `bot` is an agent's slug or id, or human:<id>. */
const authorOf = (u: Update) => (u.bot.includes(':') ? u.bot : `bot:${u.bot}`);

/** An update as a row of the team's thread: its author's (`by`, as the directory knows them), at its time, drawn as a
    card. */
const asMessage = (u: Update, by: { id: string; name: string }): ChatMessage => ({
  id: `update:${u.id}`,
  sender_id: by.id,
  sender_name: by.name,
  created_at: u.created_at,
  kind: 'update',
  body: updateTitle(u),
  refs: { update: u },
});

/** An update's card: what kind, its headline, and Replies when it has some. It opens the update. */
function UpdateCard({ u }: { u: Update }) {
  return (
    <Link className="kcard" to="/updates" search={{ open: u.id }} data-target={`update:${u.id}`}>
      <span className="kh">
        <Icon name="updates" />
        {u.kind === 'weekly' ? 'Weekly update' : 'Daily update'}
      </span>
      <p>{u.headline || updateTitle(u)}</p>
      {u.replies ? <span className="kf">Replies</span> : null}
    </Link>
  );
}

/** What a team thread draws in place of a message's text: an update's card or a call's. */
export function teamCard(m: ChatMessage) {
  const u = m.kind === 'update' ? (m.refs?.update as Update | undefined) : undefined;
  return u ? <UpdateCard u={u} /> : callCard(m);
}

export interface TeamThreadProps {
  /** The team: its group id, or its address key `team:<name>` (features/team teamSlug). */
  who: string;
}

/**
 * A team's thread (plan/threads-team.html), a row in Threads: its own conversation, with its teammates' updates and its meetings as
 * cards in the timeline. The meetings are the thread's own call messages; the updates are read from Updates and set
 * among them by time.
 */
export function TeamThread({ who }: TeamThreadProps) {
  const groups = useQuery(groupsQuery);
  const group = groups.data?.find((g) => teamSlug(g.name) === who || g.id === who);
  const thread = useQuery({ ...teamThreadQuery(group?.id ?? ''), enabled: !!group });
  const updates = useInfiniteQuery(updateListQuery('all'));
  const all = (updates.data?.pages.flatMap((p) => p.updates) ?? []).filter((u) => !u.archived);
  // the ones its teammates wrote, each with its author as the directory names them (an update names an agent by its
  // bare id); as ids, so the directory's status lines do not draw the thread again
  const ours = new Map(
    useDirectory((d) =>
      all.flatMap((u) => {
        const a = d.byId[u.bot] ?? d.byId[authorOf(u)];
        return group && a?.team === group.name ? [[u.id, { id: a.id, name: a.name }] as const] : [];
      }),
    ),
  );
  const extra = all.flatMap((u) => (ours.has(u.id) ? [asMessage(u, ours.get(u.id)!)] : []));

  if (groups.isError || thread.isError)
    return (
      <Empty icon="alert" title="This thread did not load">
        <Button onClick={() => void (groups.isError ? groups.refetch() : thread.refetch())}>Try again</Button>
      </Empty>
    );
  if (groups.data && !group) return <Empty icon="people" title="No such team" />;
  if (!thread.data) return <MessageBones />;
  const t = thread.data;
  return <Conversation key={t.id} id={t.id} variant="page" title={group?.name} card={teamCard} canPost={t.me?.can_post !== false} extra={extra} />;
}
