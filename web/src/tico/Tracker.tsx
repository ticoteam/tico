import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { meQuery, useActor } from '@/data';
import { flowQuery } from '@/features/tasks/routing';
import { Avatar } from '@/kit/Avatar';
import { FlowMark } from '@/kit/FlowMark';
import { Icon } from '@/kit/Icon';
import { TaskRow } from '@/kit/Row';
import { Section } from '@/kit/SectionHead';
import { useFollow } from '@/live/follow';
import { backQuery, markTrackerSeen, trackedQuery, trackerSeenQuery, type TrackedTask } from './queries';
import { backSince, nextAgeOut, trackerGroups } from './tracked';
import './tracker.css';

/* The tracker (plan: the assistant as a delegator): one row at the top of Tico's chat counting every open task the
   person asked for that someone else holds, how many wait on them, and how many came back since they last looked.
   Opening it shows three groups and starts Back again from now. One stream filter keeps it live: party=<their actor
   id>, which sends the tasks they own or asked for, however many (task=<ids> stops narrowing past 20). */

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
  const open = useQuery({ ...trackedQuery(me ?? ''), enabled: !!me, select: rows });
  const back = useQuery({ ...backQuery(me ?? ''), enabled: !!me, select: rows });
  const { data: waiting } = useQuery({ ...flowQuery, select: (f) => new Set(f.task_ids ?? f.items.map((i) => i.id)) });
  const seen = useQuery(trackerSeenQuery);
  // Open: when, and the last opening before it, which Back counts from while it stays open.
  const [opened, setOpened] = useState<{ at: number; seen: string | null } | null>(null);
  // Back moves on to now once the groups it counted are on screen (after this opening's render), not before.
  const marked = useRef<number | null>(null);
  useEffect(() => {
    if (!opened || marked.current === opened.at) return;
    marked.current = opened.at;
    void markTrackerSeen(new Date(opened.at).toISOString()).catch(() => undefined);
  }, [opened]);
  // The time Back's 7 days count back from: now when drawn, then again whenever its next result ages out.
  const [now, setNow] = useState(() => Date.now());
  const at = Math.max(now, back.dataUpdatedAt, opened?.at ?? 0);
  // Nothing shows until the lists and the last opening are read: a count from half the reads would be wrong. One
  // cutoff serves the count and the open group (which counts from the opening before this one).
  const groups =
    me && open.isSuccess && back.isSuccess && seen.isSuccess
      ? trackerGroups(open.data, back.data, waiting ?? new Set(), me, backSince(opened ? opened.seen : seen.data, at))
      : null;
  const ageOut = groups ? nextAgeOut(groups.back) : null;
  useEffect(() => {
    if (ageOut == null) return;
    // One timer for the next result to leave, not a poll: redrawing then sets the one after.
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, ageOut - Date.now()));
    return () => clearTimeout(timer);
  }, [ageOut]);
  if (!groups) return null;
  const tracking = groups.needs.length + groups.open.length;
  const backCount = opened ? 0 : groups.back.length;
  if (!opened && !tracking && !backCount) return null;
  const toggle = () => setOpened(opened ? null : { at: Date.now(), seen: seen.data ?? null });
  return (
    <div className="tracker">
      <button type="button" className="tracker-row" aria-expanded={!!opened} onClick={toggle}>
        <span className="tracker-n">Tracking {tracking}</span>
        {groups.needs.length > 0 && <span className="tracker-tag">{groups.needs.length} needs you</span>}
        {backCount > 0 && <span className="tracker-tag">{backCount} back</span>}
        <Icon name="chevdown" />
      </button>
      {opened && (
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
