import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { meQuery, useActor } from '@/data';
import { flowQuery } from '@/features/tasks/routing';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { Section } from '@/kit/SectionHead';
import { useFollow } from '@/live/follow';
import { backQuery, markTrackerSeen, trackedQuery, trackerSeenQuery, type TrackedTask } from './queries';
import { backSince, trackerGroups } from './tracked';
import './tracker.css';

/* The tracker (plan: the assistant as a delegator): one row at the top of Tico's chat counting every open task the
   person asked for that someone else holds, how many wait on them, and how many came back since they last looked.
   Opening it shows three groups and starts Back again from now. One stream filter keeps it live: party=<their actor
   id>, which sends the tasks they own or asked for, however many (task=<ids> stops narrowing past 20). */

const NONE: TrackedTask[] = [];
const rows = (list: { tasks: TrackedTask[] }) => list.tasks;

const taskHref = (t: TrackedTask) =>
  t.pipeline_id && t.number ? `/tasks/${encodeURIComponent(t.pipeline_id)}/${t.number}` : `/task/${encodeURIComponent(t.id)}`;

function Owner({ id }: { id: string | null }) {
  const a = useActor(id);
  if (!a) return <span className="av-gap" />;
  return <Avatar kind={a.kind} name={a.name} hue={a.hue} live={a.working} />;
}

function Rows({ tasks, waits }: { tasks: TrackedTask[]; waits?: boolean }) {
  return tasks.map((t) => (
    <TaskRow
      key={t.id}
      mark={waits ? <FlowMark /> : undefined}
      title={t.title}
      href={taskHref(t)}
      target={`task:${t.number ?? t.id}`}
      avatar={<Owner id={t.owner_id} />}
      at={t.done_at ?? t.closed_at ?? t.updated_at}
    />
  ));
}

export function Tracker() {
  const me = useQuery(meQuery).data?.id;
  useFollow(me ? { party: me } : null);
  const { data: open = NONE } = useQuery({ ...trackedQuery(me ?? ''), enabled: !!me, select: rows });
  const { data: back = NONE } = useQuery({ ...backQuery(me ?? ''), enabled: !!me, select: rows });
  const { data: waiting } = useQuery({ ...flowQuery, select: (f) => new Set(f.items.map((i) => i.id)) });
  const { data: seenAt } = useQuery(trackerSeenQuery);
  // While open, Back shows what came back since the tracker was last opened; the count is already cleared.
  const [shownSince, setShownSince] = useState<string | null>(null);
  // Back's 7-day floor, from when the sheet drew: a long-open sheet only looks a little further back.
  const [drawn] = useState(() => Date.now());
  if (!me) return null;
  const counted = trackerGroups(open, back, waiting ?? new Set(), me, backSince(seenAt, drawn));
  const groups = shownSince ? trackerGroups(open, back, waiting ?? new Set(), me, shownSince) : counted;
  const tracking = counted.needs.length + counted.open.length;
  if (!shownSince && !tracking && !counted.back.length) return null;
  const toggle = () => {
    if (shownSince) return setShownSince(null);
    const now = Date.now();
    setShownSince(backSince(seenAt, now));
    void markTrackerSeen(new Date(now).toISOString()).catch(() => undefined);
  };
  return (
    <div className="tracker">
      <button type="button" className="tracker-row" aria-expanded={!!shownSince} onClick={toggle}>
        <span className="tracker-n">Tracking {tracking}</span>
        {counted.needs.length > 0 && <span className="tracker-tag">{counted.needs.length} needs you</span>}
        {counted.back.length > 0 && <span className="tracker-tag">{counted.back.length} back</span>}
        <Icon name="chevdown" />
      </button>
      {shownSince && (
        <div className="tracker-groups">
          {groups.needs.length > 0 && (
            <Section name="Needs you" count={groups.needs.length}>
              <Rows tasks={groups.needs} waits />
            </Section>
          )}
          {groups.open.length > 0 && (
            <Section name="Open" count={groups.open.length}>
              <Rows tasks={groups.open} />
            </Section>
          )}
          {groups.back.length > 0 && (
            <Section name="Back" count={groups.back.length}>
              <Rows tasks={groups.back} />
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
