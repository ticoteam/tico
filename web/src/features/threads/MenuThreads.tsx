import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { conversationsOf, isDm, othersOf, rowsOf, unreadOf, type Thread } from './model';
import { Face, useMe, useTitle } from './people';
import { threadListQuery } from './queries';

const ACTIVE = { className: 'is-active', 'aria-current': 'page' as const };

export interface MenuThreadsProps {
  /** The threads that ask you something: the Dot on their rows. */
  asks: ReadonlySet<string>;
}

/** The menu's Threads tab (plan/threads-team.html): every conversation you are in, newest first, bold when there is
    something new, the Dot when it asks you. No counts. Mail and Messaging fold at the foot. */
export function MenuThreads({ asks }: MenuThreadsProps) {
  const list = useInfiniteQuery(threadListQuery);
  const rows = conversationsOf(rowsOf(list.data));
  return (
    <>
      <div className="ca-list" role="tabpanel" aria-label="Threads">
        {rows.map((t) => (
          <ThreadLine key={t.id} t={t} ask={asks.has(t.id)} />
        ))}
        {list.hasNextPage ? (
          <button type="button" className="tree-row ca-more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            <span className="tree-ic" />
            <span className="tree-name">Older</span>
          </button>
        ) : null}
      </div>
      <div className="ca-src">
        <Link className="grp" to="/threads/mail" activeProps={ACTIVE}>
          <span className="tree-name">Mail</span>
          <Icon name="chevron" />
        </Link>
        <Link className="grp" to="/threads/messaging" activeProps={ACTIVE}>
          <span className="tree-name">Messaging</span>
          <Icon name="chevron" />
        </Link>
      </div>
    </>
  );
}

function ThreadLine({ t, ask }: { t: Thread; ask: boolean }) {
  const me = useMe();
  const title = useTitle(t);
  const other = isDm(t) ? othersOf(t, me)[0] : undefined;
  const home = t.audience === 'home' && typeof t.home?.actor === 'string' ? t.home.actor : undefined;
  const face = other ?? home;
  const isNew = unreadOf(t) > 0 || !!t.me?.activity;
  return (
    <Link
      className={isNew ? 'tree-row is-new' : 'tree-row'}
      to="/threads/$thread"
      params={{ thread: t.id }}
      activeProps={ACTIVE}
      title={title}
      data-target={`thread:${t.id}`}
    >
      {face ? (
        <Face actor={face} />
      ) : (
        <span className="tree-ic">
          <Icon name="threads" />
        </span>
      )}
      <span className="tree-name">{title}</span>
      {ask ? <FlowMark label="Asks you" /> : null}
    </Link>
  );
}
