import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';

/** Which of an agent's chats a head shows: a task's own conversation (its title), or the agent's main chat. */
export type Session = 'main' | { task: string; title: string };

/** The quiet line under a chat head's name that says which chat this is; a task's goes to the task. */
export function SessionLine({ session }: { session: Session }) {
  if (session === 'main') return <span className="head-sub">Main chat</span>;
  return (
    <Link className="head-sub" href={`/task/${encodeURIComponent(session.task)}`} title={session.title}>
      <Icon name="tasks" />
      <span>{session.title}</span>
    </Link>
  );
}
