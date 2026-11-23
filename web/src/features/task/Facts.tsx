import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import { meQuery, useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { DateText } from '@/kit/DateText';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { goalLink } from '@/features/company/exports';
import { pipelinesQuery, roadmapPipeline } from '@/features/tasks/routing';
import { EditKv, Inline, Kv, PickKv, saved } from './Fact';
import { ActorItems } from './Head';
import { failedToast, reasonOf } from './model';
import { Face, Go, isActor, NameOf } from './parts';
import { fixHref } from './StatusLine';
import { taskGoalQuery, taskMeetingsQuery, visibleOf, type TaskData, type TaskFacts, type Visible } from './queries';

/* The facts the old task window had (ui/app/task-props.ts) that Details draws as rows (more.js dKv): Size, Human,
   Developer, Goal, Roadmap, People, Visible to, Followers, the public text; and the Meetings section. Each value edits
   in place or through a small pick, and saves at once. */

const act = async (id: string, input: object) => {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast(failedToast(r));
};

/** Names for labels that must be words (a face's button). */
function useNames() {
  const actors = useActors();
  return (id: string) => actors.find((a) => a.id === id)?.name ?? 'Someone';
}

/** People only, for the Human. */
function PeopleItems({ current, onPick }: { current: string | null | undefined; onPick: (id: string) => void }) {
  const people = useActors().filter((a) => a.kind === 'person');
  return (
    <>
      {people.map((a) => (
        <MenuItem key={a.id} label={a.name} lead={<Face id={a.id} size={16} decorative />} on={a.id === current} onSelect={() => onPick(a.id)} />
      ))}
    </>
  );
}

/** A face that opens a short menu (take them off). */
function FaceMenu({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <Menu
      align="start"
      trigger={
        <button type="button" className="dt-face" aria-label={label} title={label}>
          <Face id={id} decorative />
        </button>
      }
    >
      {children}
    </Menu>
  );
}

const none = <small>None</small>;

type Turn = NonNullable<TaskData['turn']>;

/** The wait states whose `who` is a teammate; the rest name a task, a stage or a source, which the line says. */
const ON_SOMEONE = new Set(['person', 'bot', 'paused']);

/** The teammate a wait is on, or null: a task's, a stage's or a snoozer's `who` is never named as what it waits on. */
const onWhom = (turn: Turn) => (ON_SOMEONE.has(turn.state) && isActor(turn.who) ? turn.who : null);

/** Whether the wait only says again who has the task: its agent at work, or its person's own work. Paused always says
    more (the agent cannot run). */
export const repeatsOwner = (turn: Turn, owner: string | null | undefined) => !turn.ask && turn.state !== 'paused' && !!owner && onWhom(turn) === owner;

/** The waiting question as a line: a leading "❓" goes, since the line already says it waits (and the emoji draws in red). */
const question = (line: string | null | undefined) => (line ?? '').replace(/^\s*❓️?\s*/u, '');

/** What the open task waits on, in words, for Details' Waiting on and Facts' Waiting: a teammate by name (`short`: only
    that; else with their face, what is asked of them, and a Paused agent's Fix). Anything else is the server's own
    line: a task it waits on links to it when the reader may know which, and a snooze says why and until when, or "On
    ice". Never an id. */
export function WaitOn({ turn, short = false }: { turn: Turn; short?: boolean }) {
  const who = onWhom(turn);
  const line = question(turn.line);
  if (who && short)
    return (
      <>
        on <NameOf id={who} />
      </>
    );
  if (who) {
    const fix = turn.state === 'paused' ? fixHref(turn.href) : null;
    return (
      <>
        <Face id={who} />
        <span>
          <NameOf id={who} />
        </span>
        {line && <small>{line}</small>}
        {fix && (
          <Link className="status-fix" href={fix}>
            Fix
          </Link>
        )}
      </>
    );
  }
  if (turn.state === 'snoozed') {
    if (!line && !turn.until) return <>On ice</>;
    return (
      <>
        {line}
        {turn.until && (
          <>
            {line ? ' · until ' : 'Until '}
            <DateText at={turn.until} />
          </>
        )}
      </>
    );
  }
  if (turn.state === 'tasks' && turn.who) return <Go to={`/task/${turn.who}`}>{line}</Go>;
  return <>{line}</>;
}

/** One quiet line: who or what the open task waits on, and for how long when its time is the wait's own: an ask
    (when it was asked) or a snooze (when it was set). Another wait's time is its stage's or requirement's, which a
    reassignment in the same stage leaves as it was. */
export function WaitFact({ t }: { t: TaskData }) {
  const turn = t.status === 'open' ? t.turn : null;
  if (!turn) return null;
  const since = turn.ask || turn.state === 'snoozed' ? turn.since : null;
  return (
    <Kv k="Waiting">
      <small>
        {since && (
          <>
            <Ago at={since} />{' '}
          </>
        )}
        <WaitOn turn={turn} short />
      </small>
    </Kv>
  );
}

/** Size, before Tags (more.js facts order). */
export function SizeFact({ t }: { t: TaskData }) {
  return (
    <EditKv
      k="Size"
      label="Size"
      target={`task-size:${t.id}`}
      value={t.size != null ? <span>{t.size}</span> : none}
      edit={(done) => (
        <Inline
          label="Size"
          numeric
          initial={t.size != null ? String(t.size) : ''}
          placeholder="3"
          onSave={(text) => {
            const size = Number(text.trim());
            if (!text.trim() || !Number.isFinite(size) || size < 0) return Promise.resolve('Use a number');
            return saved('task.setSize', { task: t.id, size });
          }}
          onEnd={done}
        />
      )}
    />
  );
}

/** Human, Developer, Goal, Roadmap, People, Visible to, Followers: after Tags. */
export function MoreFacts({ t }: { t: TaskFacts }) {
  return (
    <>
      <HumanFact t={t} />
      {t.assignee_id && t.assignee_id !== t.owner_id && (
        <Kv k="Developer">
          <Face id={t.assignee_id} />
          <span>
            <NameOf id={t.assignee_id} />
          </span>
        </Kv>
      )}
      {t.goal_id && <GoalFact goal={t.goal_id} />}
      <RoadmapFact t={t} />
      <PeopleFact t={t} />
      <VisibleFact t={t} />
      {Array.isArray(t.followers) && <FollowersFact t={t} followers={t.followers} />}
    </>
  );
}

function HumanFact({ t }: { t: TaskData }) {
  const human = t.human_id ?? null;
  const set = (to: string | null) => act('task.setHuman', { task: t.id, human: to });
  return (
    <PickKv
      k="Human"
      label="Human"
      target={`task-human:${t.id}`}
      value={
        human ? (
          <>
            <Face id={human} />
            <span>
              <NameOf id={human} />
            </span>
          </>
        ) : (
          none
        )
      }
    >
      <PeopleItems current={human} onPick={set} />
      {human && (
        <>
          <MenuRule />
          <MenuItem label="None" icon="close" onSelect={() => set(null)} />
        </>
      )}
    </PickKv>
  );
}

function GoalFact({ goal }: { goal: string }) {
  const title = useQuery(taskGoalQuery(goal));
  const to = goalLink(goal);
  return (
    <Kv k="Goal">
      <Go to={to.to} search={to.search}>
        {title.data ?? 'Goal'}
      </Go>
    </Kv>
  );
}

/** A task in the Features pipeline is on the roadmap: who asked for it opens there. */
function RoadmapFact({ t }: { t: TaskData }) {
  const pipelines = useQuery(pipelinesQuery);
  const road = roadmapPipeline(pipelines.data ?? []);
  if (!road || road.id !== t.pipeline_id) return null;
  const n = t.requester_count ?? 0;
  return (
    <Kv k="Roadmap">
      <Go to="/roadmap" search={{ requesters: t.id }}>
        {n ? `${n} asked` : 'On the roadmap'}
      </Go>
      {t.visibility === 'public' && !t.private && <small>Public</small>}
    </Kv>
  );
}

/** "reviewer", "QA lead" to the role the server takes: lower case, words joined by "-". */
const roleOf = (text: string) => text.trim().toLowerCase().split(/\s+/).join('-');

function PeopleFact({ t }: { t: TaskData }) {
  const nameOf = useNames();
  const actors = useActors();
  const roles = Object.entries(t.roles ?? {}).filter(([, people]) => people.length);
  const [adding, setAdding] = useState(false);
  const [role, setRole] = useState('');
  const [who, setWho] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const close = () => {
    setAdding(false);
    setRole('');
    setWho(null);
    setError('');
  };
  const add = async () => {
    const r = roleOf(role);
    if (!who || !r) return;
    setBusy(true);
    const res = await run('task.setRole', { task: t.id, role: r, people: [...new Set([...(t.roles?.[r] ?? []), who])] }, { via: 'click' });
    setBusy(false);
    if (res.ok) close();
    else setError(reasonOf(res.error));
  };
  return (
    <div className="dt-kv" data-target={`task-people:${t.id}`}>
      <span className="dt-k">People</span>
      <span className="dt-v">
        <span className="faces">
          {roles.flatMap(([r, people]) =>
            people.map((p) => (
              <FaceMenu key={`${r}:${p}`} id={p} label={`${nameOf(p)} · ${r}`}>
                <MenuItem label="Take off" icon="close" onSelect={() => void act('task.setRole', { task: t.id, role: r, people: people.filter((x) => x !== p) })} />
              </FaceMenu>
            )),
          )}
        </span>
        {!roles.length && none}
        <IconButton icon="plus" label="Add someone" size="sm" quiet onClick={() => setAdding(true)} />
      </span>
      <Dialog
        open={adding}
        onOpenChange={(o) => !o && close()}
        title="Add someone"
        actions={
          <>
            <Button onClick={close}>Cancel</Button>
            <Button variant="primary" busy={busy} disabled={!who || !roleOf(role) || busy} onClick={() => add()}>
              Add
            </Button>
          </>
        }
      >
        <Field label="Role" autoFocus placeholder="reviewer" value={role} onValueChange={setRole} error={error || undefined} />
        <Select label="Who" options={actors.map((a) => ({ value: a.id, label: a.name }))} value={who} onValueChange={setWho} placeholder="Pick someone" />
      </Dialog>
    </div>
  );
}

const VISIBLE: Record<Visible, string> = { team: 'Team', public: 'Public roadmap', private: 'Private' };

/** Team, Public roadmap (a roadmap task) or Private; a private task's people: who asked and who has it, then readers. */
function VisibleFact({ t }: { t: TaskData }) {
  const nameOf = useNames();
  const pipelines = useQuery(pipelinesQuery);
  const onRoad = roadmapPipeline(pipelines.data ?? [])?.id === t.pipeline_id;
  const now = visibleOf(t);
  const choices: Visible[] = onRoad || now === 'public' ? ['team', 'public', 'private'] : ['team', 'private'];
  const readers = (t.readers ?? []).filter((r) => r !== t.requester_id && r !== t.owner_id);
  const fixed = [...new Set([t.requester_id, t.owner_id].filter((x): x is string => !!x))];
  return (
    <PickKv
      k="Visible to"
      label="Visible to"
      target={`task-visible:${t.id}`}
      value={<span>{VISIBLE[now]}</span>}
      after={
        now === 'private' && (
          <>
            <span className="faces">
              {fixed.map((id) => (
                <Face key={id} id={id} />
              ))}
              {readers.map((id) => (
                <FaceMenu key={id} id={id} label={nameOf(id)}>
                  <MenuItem label="Take off" icon="close" onSelect={() => void act('task.setReaders', { task: t.id, add: [], remove: [id] })} />
                </FaceMenu>
              ))}
            </span>
            <Menu align="start" trigger={<IconButton icon="plus" label="Let someone see" size="sm" quiet />}>
              <ActorItems current={null} onPick={(id) => void act('task.setReaders', { task: t.id, add: [id], remove: [] })} />
            </Menu>
          </>
        )
      }
    >
      {choices.map((v) => (
        <MenuItem key={v} label={VISIBLE[v]} on={v === now} onSelect={() => v !== now && void act('task.setVisible', { task: t.id, to: v })} />
      ))}
    </PickKv>
  );
}

function FollowersFact({ t, followers }: { t: TaskData; followers: { actor: string }[] }) {
  const nameOf = useNames();
  const me = useQuery(meQuery).data?.id;
  const mine = followers.find((f) => f.actor === me);
  const follow = (input: object) => act('task.follow', { task: t.id, ...input });
  return (
    <div className="dt-kv" data-target={`task-followers:${t.id}`}>
      <span className="dt-k">Followers</span>
      <span className="dt-v">
        <span className="faces">
          {followers.map((f) =>
            f.actor === me ? (
              <Face key={f.actor} id={f.actor} />
            ) : (
              <FaceMenu key={f.actor} id={f.actor} label={nameOf(f.actor)}>
                <MenuItem label="Take off" icon="close" onSelect={() => follow({ actor: f.actor, following: false })} />
              </FaceMenu>
            ),
          )}
        </span>
        <button type="button" className="dt-act" onClick={() => follow({ following: !mine })}>
          {mine ? 'Unfollow' : 'Follow'}
        </button>
        <Menu align="start" trigger={<IconButton icon="plus" label="Add a follower" size="sm" quiet />}>
          <ActorItems current={null} onPick={(id) => follow({ actor: id, following: true })} />
        </Menu>
      </span>
    </div>
  );
}

/** Public title and summary: a roadmap task's words for the public, written in place. */
export function PublicFacts({ t }: { t: TaskData }) {
  const pipelines = useQuery(pipelinesQuery);
  const onRoad = roadmapPipeline(pipelines.data ?? [])?.id === t.pipeline_id;
  const fact = (field: 'public_title' | 'public_summary', k: string) => {
    const text = t[field] ?? '';
    if (!text && !onRoad) return null;
    return (
      <EditKv
        k={k}
        label={k}
        target={`task-public:${t.id}`}
        value={text ? <span>{text}</span> : none}
        edit={(done) => <Inline label={k} initial={text} onSave={(v) => saved('task.setPublic', { task: t.id, field, text: v })} onEnd={done} />}
      />
    );
  };
  return (
    <>
      {fact('public_title', 'Public title')}
      {fact('public_summary', 'Public summary')}
    </>
  );
}

/** The meetings the task was in (more.js dMeet), folded with the record. One that would not load says so. */
export function Meetings({ t }: { t: TaskData }) {
  const meetings = useQuery(taskMeetingsQuery(t.id));
  if (meetings.isError)
    return (
      <Section name="Meetings" defaultOpen={false}>
        <div className="dt-line">
          <span className="dt-mark">
            <Icon name="alert" />
          </span>
          <span className="dt-text">Couldn’t load meetings</span>
          <button type="button" className="dt-act" onClick={() => void meetings.refetch()}>
            Try again
          </button>
        </div>
      </Section>
    );
  const rows = meetings.data ?? [];
  if (!rows.length) return null;
  return (
    <Section name="Meetings" count={rows.length} defaultOpen={false}>
      {rows.map((m) => (
        <Go key={m.meeting_id} to="/calendar" search={{ view: 'list', panel: `meeting:${m.meeting_id}` }} className="row dt-meet" title={m.title}>
          <span className="row-mark">
            <Icon name="call" />
          </span>
          <span className="row-title">{m.title}</span>
          <span className="row-stage" />
          <Ago at={m.started_at} className="row-age" />
        </Go>
      ))}
    </Section>
  );
}
