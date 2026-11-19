import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import type { CalendarOccurrence } from '@/api/schema';
import { useActor } from '@/data';
import { useFollow } from '@/live/follow';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { SidePanel } from '@/pagekinds';
import { EventDialog } from './EventDialog';
import { clock, dl, isLive, joinOf, kindWords, lengthWords, minutes, parseDay } from './model';
import { joinNow, LiveMark, press, refsOf } from './parts';
import { itemsQuery, meetingQuery, type MeetingItem } from './queries';

/* ---------------------------------------------------------------- an event */

export function EventPanel({
  id,
  list,
  pending,
  now,
  onClose,
}: {
  id: string;
  list: readonly CalendarOccurrence[];
  pending: boolean;
  now: Date;
  onClose: () => void;
}) {
  const o = list.find((x) => x.occurrence_id === id);
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  if (!o)
    return (
      <SidePanel title={pending ? '' : 'Not on this calendar'} target={`event:${id}`} onClose={onClose}>
        {!pending && <Empty title="Not on this calendar" />}
      </SidePanel>
    );
  const s = new Date(o.start);
  const live = isLive(o, now);
  const way = joinOf(o);
  const refs = refsOf(o.attendees);
  const editable = o.can.edit && !o.occurrence_id.startsWith('tmp:');
  const day = o.all_day ? (parseDay(o.start.slice(0, 10)) ?? s) : s;
  return (
    <SidePanel
      title={o.title}
      target={`event:${o.occurrence_id}`}
      onClose={onClose}
      actions={
        editable ? (
          <>
            <IconButton icon="edit" label="Change" size="sm" onClick={() => setEditing(true)} />
            <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>
              <MenuItem label="Remove" onSelect={() => setRemoving(true)} />
            </Menu>
          </>
        ) : undefined
      }
    >
      {live && way && (
        <section className="rail-sec ev-now">
          <div className="ev-live">
            <LiveMark />
            <b>On now</b>
            <span className="grow" />
            <span className="row-age">{Math.max(0, Math.round((now.getTime() - s.getTime()) / 60_000))} min in</span>
          </div>
          <Button className="btn-wide" icon="call" onClick={(e) => joinNow(o, e)}>
            Join
          </Button>
        </section>
      )}
      <section className="rail-sec">
        <h3>When</h3>
        <Fact k="Day" v={dl(day)} />
        {!o.all_day && <Fact k="Time" v={`${clock(minutes(s))}${o.end ? ` to ${clock(minutes(new Date(o.end)))}` : ''}`} />}
        {!o.all_day && o.end && <Fact k="Length" v={lengthWords((new Date(o.end).getTime() - s.getTime()) / 60_000)} />}
        <Fact k="Repeats" v={o.recurring ? 'Yes' : 'No'} />
        <Fact k="Kind" v={kindWords(o)} />
      </section>
      {refs.length > 0 && (
        <section className="rail-sec">
          <h3>Who</h3>
          {refs.map((r) => (
            <Guest key={r} actor={r} />
          ))}
        </section>
      )}
      {(o.links.thread || o.links.meeting || o.links.task || o.open_url) && (
        <section className="rail-sec">
          <h3>About</h3>
          {o.links.thread && (
            <Link to="/threads/$thread" params={{ thread: o.links.thread }} className="rail-row">
              <Icon name="threads" />
              <span>Thread</span>
            </Link>
          )}
          {o.links.meeting && (
            <Link to="/calendar" search={{ view: 'list', panel: `meeting:${o.links.meeting}` }} className="rail-row">
              <Icon name="file" />
              <span>Notes</span>
            </Link>
          )}
          {o.links.task && (
            <Link to="/task/$task" params={{ task: o.links.task }} className="rail-row">
              <Icon name="tasks" />
              <span>Task</span>
            </Link>
          )}
          {o.open_url && (
            <a href={o.open_url} target="_blank" rel="noopener noreferrer" className="rail-row">
              <Icon name="popout" />
              <span>{o.home === 'google' ? 'Open in Google Calendar' : 'Open'}</span>
            </a>
          )}
        </section>
      )}
      <EventDialog open={editing} onOpenChange={setEditing} event={o} />
      <Dialog
        open={removing}
        onOpenChange={setRemoving}
        alert
        title={`Remove ${o.title}?`}
        description={o.recurring ? 'Only this one goes; the rest stay.' : undefined}
        actions={
          <>
            <Button onClick={() => setRemoving(false)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => {
                setRemoving(false);
                void press('calls.removeEvent', { id: o.occurrence_id });
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </SidePanel>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="fact">
      <span>{k}</span>
      <span className="fact-v">{v}</span>
    </div>
  );
}

function Guest({ actor }: { actor: string }) {
  const a = useActor(actor);
  const name = a?.name ?? actor.slice(actor.indexOf(':') + 1);
  return (
    <span className="rail-row">
      <Avatar kind={a?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={a?.hue} decorative />
      <span>{name}</span>
    </span>
  );
}

/* ---------------------------------------------------------------- a meeting: its agenda, notes, summary and tasks */

export function MeetingPanel({ id, onClose }: { id: string; onClose: () => void }) {
  useFollow({ meeting: id });
  const m = useQuery(meetingQuery(id));
  const items = useQuery(itemsQuery(id));
  const [text, setText] = useState('');
  const summary = m.data?.outcome?.summary;
  const lines = Array.isArray(summary) ? summary : summary ? summary.split('\n').filter((l) => l.trim()) : [];
  const made = m.data?.outcome?.tasks ?? [];
  const doc = items.data?.sections.doc ?? [];
  const todo = items.data?.sections.task ?? [];
  const add = () => {
    const t = text.trim();
    if (!t) return;
    setText('');
    void press('calls.addItem', { meeting: id, section: 'doc', text: t });
  };
  if (m.isError)
    return (
      <SidePanel title="Meeting" target={`meeting:${id}`} onClose={onClose}>
        <Empty title="Meeting did not load" icon="alert">
          <Button onClick={() => void m.refetch()}>Try again</Button>
        </Empty>
      </SidePanel>
    );
  return (
    <SidePanel title={m.data?.title ?? ''} target={`meeting:${id}`} onClose={onClose}>
      {m.data?.started && (
        <section className="rail-sec">
          <Fact k="Day" v={dl(new Date(m.data.started))} />
          <Fact k="Time" v={clock(minutes(new Date(m.data.started)))} />
          {m.data.duration_ms ? <Fact k="Length" v={lengthWords(m.data.duration_ms / 60_000)} /> : null}
        </section>
      )}
      {lines.length > 0 && (
        <section className="rail-sec">
          <h3>Summary</h3>
          <ul className="mv-list">
            {lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}
      <section className="rail-sec">
        <h3>Agenda</h3>
        {doc.length > 0 && (
          <ul className="acts">
            {doc.map((it) => (
              <Item key={it.id} meeting={id} it={it} />
            ))}
          </ul>
        )}
        <form
          className="mv-add"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <Field label="Add to the agenda" labelHidden placeholder="Add to the agenda" value={text} onValueChange={setText} lead="plus" />
        </form>
      </section>
      {todo.length > 0 && (
        <section className="rail-sec">
          <h3>Action items</h3>
          <ul className="acts">
            {todo.map((it) => (
              <Item key={it.id} meeting={id} it={it} task canPush={!!items.data?.can_push} />
            ))}
          </ul>
        </section>
      )}
      {made.length > 0 && (
        <section className="rail-sec">
          <h3>Tasks made</h3>
          {made.map((t) => (
            <Link key={t.id} to="/task/$task" params={{ task: t.id }} className="rail-row">
              <Icon name="tasks" />
              <span>{t.title}</span>
            </Link>
          ))}
        </section>
      )}
      {m.data?.notes && (
        <section className="rail-sec">
          <h3>Notes</h3>
          <div className="mv-notes">
            {m.data.notes
              .split('\n')
              .filter((l) => l.trim())
              .map((l, i) => (
                <p key={i}>{l}</p>
              ))}
          </div>
        </section>
      )}
    </SidePanel>
  );
}

const taskOf = (it: MeetingItem) => (it.result_ref?.startsWith('task:') ? it.result_ref.slice(5) : (it.proposed_task_id ?? null));

function Item({ meeting, it, task, canPush }: { meeting: string; it: MeetingItem; task?: boolean; canPush?: boolean }) {
  const made = task && it.status === 'pushed' ? taskOf(it) : null;
  const tmp = it.id.startsWith('tmp:');
  return (
    <li className="act">
      <span />
      <span className="act-text">{it.text}</span>
      {made ? (
        <Link to="/task/$task" params={{ task: made }} className="btn btn-sm btn-quiet">
          <Icon name="tasks" />
          Open task
        </Link>
      ) : task && canPush && it.status !== 'pushed' ? (
        <Button className="btn-sm" disabled={tmp} onClick={() => void press('calls.makeTask', { meeting, item: it.id })}>
          Create task
        </Button>
      ) : (
        <span />
      )}
      <IconButton icon="close" label="Hide" size="sm" quiet disabled={tmp} onClick={() => void press('calls.dismissItem', { meeting, item: it.id })} />
    </li>
  );
}
