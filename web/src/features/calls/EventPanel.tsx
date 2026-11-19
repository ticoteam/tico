import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { CalendarOccurrence } from '@/api/schema';
import { useActor, useActors } from '@/data';
import { useFollow } from '@/live/follow';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { FoldButton } from '@/kit/SectionHead';
import { Select } from '@/kit/Select';
import { SkeletonRows } from '@/kit/Skeleton';
import { Tabs } from '@/kit/Tabs';
import { toast } from '@/kit/Toast';
import { SidePanel } from '@/pagekinds';
import { EventDialog } from './EventDialog';
import { clock, dl, isLive, joinOf, kindWords, lengthWords, minIn, minutes, parseDay, running } from './model';
import { joinNow, LiveMark, press, refsOf, useNameOf } from './parts';
import {
  itemsQuery,
  meetingQuery,
  transcriptQuery,
  upcomingCallsQuery,
  type LiveTranscript,
  type MeetChat,
  type Meeting,
  type MeetingItem,
  type MeetingMore,
} from './queries';

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
  // A booked Tico call: its time is the call's (PATCH /calls/{id}), and it is cancelled rather than removed.
  const way0 = o ? joinOf(o) : null;
  const callId = way0 && 'call' in way0 ? way0.call : undefined;
  const upcoming = useQuery({ ...upcomingCallsQuery, enabled: !!callId });
  const booked = callId ? upcoming.data?.calls.find((c) => c.id.replace(/-/g, '').toLowerCase() === callId.replace(/-/g, '').toLowerCase()) : undefined;
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
        booked ? (
          booked.can_edit ? (
            <>
              <IconButton icon="edit" label="Change time" size="sm" onClick={() => setEditing(true)} />
              <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>
                <MenuItem label="Cancel call" onSelect={() => setRemoving(true)} />
              </Menu>
            </>
          ) : undefined
        ) : editable ? (
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
            <span className="row-age">{minIn(s, now)}</span>
          </div>
          <Button className="btn-wide" icon="call" onClick={(e) => joinNow(o, e)}>
            Join
          </Button>
        </section>
      )}
      <RailSec name="When">
        <Fact k="Day" v={dl(day)} />
        {!o.all_day && <Fact k="Time" v={`${clock(minutes(s))}${o.end ? ` to ${clock(minutes(new Date(o.end)))}` : ''}`} />}
        {!o.all_day && o.end && <Fact k="Length" v={lengthWords((new Date(o.end).getTime() - s.getTime()) / 60_000)} />}
        <Fact k="Repeats" v={o.recurring ? 'Yes' : 'No'} />
        <Fact k="Kind" v={kindWords(o)} />
      </RailSec>
      {refs.length > 0 && (
        <RailSec name="Who">
          {refs.map((r) => (
            <Guest key={r} actor={r} />
          ))}
        </RailSec>
      )}
      {(o.links.thread || o.links.meeting || o.links.task || o.links.automation || o.open_url) && (
        <RailSec name="About">
          {o.links.thread && (
            <Link to="/threads/$thread" params={{ thread: o.links.thread }} className="rail-row call-rail">
              <Icon name="threads" />
              <span>Thread</span>
            </Link>
          )}
          {o.links.meeting && (
            <Link to="/calendar" search={{ view: 'list', panel: `meeting:${o.links.meeting}` }} className="rail-row call-rail">
              <Icon name="file" />
              <span>Notes</span>
            </Link>
          )}
          {o.links.task && (
            <Link to="/task/$task" params={{ task: o.links.task }} className="rail-row call-rail">
              <Icon name="tasks" />
              <span>Task</span>
            </Link>
          )}
          {/* a routine's run: the routine itself, its last runs in its panel (the old card's Automation and Last run) */}
          {o.links.automation && (
            <Link to="/company/routines" search={{ panel: `routine:${o.links.automation}` }} className="rail-row call-rail">
              <Icon name="clock" />
              <span>Routine</span>
            </Link>
          )}
          {o.open_url && (
            <a href={o.open_url} target="_blank" rel="noopener noreferrer" className="rail-row call-rail">
              <Icon name="popout" />
              <span>{o.home === 'google' ? 'Open in Google Calendar' : 'Open'}</span>
            </a>
          )}
        </RailSec>
      )}
      {booked ? <EventDialog open={editing} onOpenChange={setEditing} call={booked} /> : <EventDialog open={editing} onOpenChange={setEditing} event={o} />}
      {booked && (
        <Dialog
          open={removing}
          onOpenChange={setRemoving}
          alert
          title={`Cancel ${o.title}?`}
          description="Everyone invited is told."
          actions={
            <>
              <Button onClick={() => setRemoving(false)}>Keep it</Button>
              <Button
                variant="primary"
                onClick={() => {
                  setRemoving(false);
                  void press('calls.cancel', { id: booked.id });
                }}
              >
                Cancel call
              </Button>
            </>
          }
        />
      )}
      <Dialog
        open={removing && !booked}
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

/** A side panel's section whose name folds it (t-calendar-event). */
function RailSec({ name, children }: { name: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="rail-sec">
      <h3>
        <FoldButton name={name} open={open} onToggle={() => setOpen(!open)} />
      </h3>
      {open && children}
    </section>
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
    <span className="rail-row call-rail">
      <Avatar kind={a?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person')} name={name} hue={a?.hue} decorative />
      <span>{name}</span>
    </span>
  );
}

/* ---------------------------------------------------------------- a meeting: its notes, summary and tasks */

export function MeetingPanel({ id, onClose }: { id: string; onClose: () => void }) {
  useFollow({ meeting: id });
  const m = useQuery(meetingQuery(id));
  // a meeting still to be shared has no tasks yet; the server refuses to list them
  const items = useQuery({ ...itemsQuery(id), enabled: m.data?.review_state === 'live' });
  const liveId = m.data?.live_meeting_id ?? '';
  const chat = useQuery({ ...transcriptQuery(liveId), enabled: !!liveId, select: (d: LiveTranscript) => (d as LiveTranscript & { chat?: MeetChat[] }).chat ?? [] });
  const [tab, setTab] = useState<'summary' | 'transcript' | 'chat'>('summary');
  const [deleting, setDeleting] = useState(false);
  const summary = m.data?.outcome?.summary;
  const lines = Array.isArray(summary) ? summary : summary ? summary.split('\n').filter((l) => l.trim()) : [];
  const todo = items.data?.sections.task ?? [];
  if (m.isError)
    return (
      <SidePanel title="Meeting" target={`meeting:${id}`} onClose={onClose}>
        <Empty title="Meeting did not load" icon="alert">
          <Button onClick={() => void m.refetch()}>Try again</Button>
        </Empty>
      </SidePanel>
    );
  const mm = m.data as MeetingMore | undefined;
  const state = mm?.review_state;
  const canEdit = !!mm?.can_edit;
  const said = mm ? transcriptRows(mm) : [];
  const talk = chat.data ?? [];
  const tabs = [
    { value: 'summary' as const, label: 'Summary' },
    ...(said.length ? [{ value: 'transcript' as const, label: 'Transcript' }] : []),
    ...(talk.length ? [{ value: 'chat' as const, label: 'Chat' }] : []),
  ];
  const on = tabs.some((t) => t.value === tab) ? tab : 'summary';
  return (
    <SidePanel
      title={mm?.title ?? ''}
      target={`meeting:${id}`}
      onClose={onClose}
      actions={mm && state === 'live' ? <MeetingMenu m={mm} canSend={!!items.data?.can_send} onDelete={() => setDeleting(true)} /> : undefined}
    >
      {tabs.length > 1 && (
        <div className="cw-tabs">
          <Tabs label="Pages" tabs={tabs} value={on} onValueChange={setTab} />
        </div>
      )}
      {on === 'transcript' && mm && <Said rows={said} what="transcript" />}
      {on === 'chat' && <Said rows={talk.map((c) => ({ who: c.actor ?? c.role ?? '', actor: !!c.actor, time: c.at_ms == null ? '' : running(c.at_ms / 1000), text: c.text }))} what="chat" />}
      {m.isPending && <SkeletonRows rows={5} />}
      {on === 'summary' && (
        <>
          {(state === 'pending' || state === 'dismissed') && (
            <section className="rail-sec">
              {state === 'dismissed' && <p className="mv-notes">Dismissed</p>}
              {state === 'pending' && mm ? (
                <ShareBox m={mm} />
              ) : (
                <div className="mv-review">
                  <Button onClick={() => void press('calls.review', { meeting: id, action: 'restore' })}>Restore</Button>
                </div>
              )}
            </section>
          )}
          {mm?.started && (
            <section className="rail-sec">
              <Fact k="Day" v={dl(new Date(mm.started))} />
              <Fact k="Time" v={clock(minutes(new Date(mm.started)))} />
              {mm.duration_ms ? <Fact k="Length" v={lengthWords(mm.duration_ms / 60_000)} /> : null}
              {state === 'live' && <Fact k="Seen by" v={mm.private ? 'Private' : 'Team'} />}
              {mm.sent?.bot && <SentTo bot={mm.sent.bot} />}
            </section>
          )}
          {lines.length > 0 && (
            <RailSec name="Summary">
              <ul className="mv-list">
                {lines.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </RailSec>
          )}
          {todo.length > 0 && (
            <RailSec name="Action items">
              <ul className="acts">
                {todo.map((it) => (
                  <Item key={it.id} meeting={id} it={it} task canPush={!!items.data?.can_push} canEdit={canEdit} />
                ))}
              </ul>
            </RailSec>
          )}
          {mm && (mm.can_edit || mm.note || mm.notes) && (
            <RailSec name="Notes">
              {mm.can_edit ? <Notes m={mm} /> : mm.note && <p className="mv-notes">{mm.note}</p>}
              {mm.notes && (
                <div className="mv-notes">
                  {mm.notes
                    .split('\n')
                    .filter((l) => l.trim())
                    .map((l, i) => (
                      <p key={i}>{l}</p>
                    ))}
                </div>
              )}
            </RailSec>
          )}
          {mm?.media_url?.startsWith('https:') && (
            <RailSec name="Recording">
              <Recording src={mm.media_url} />
            </RailSec>
          )}
        </>
      )}
      {mm && (
        <Dialog
          open={deleting}
          onOpenChange={setDeleting}
          alert
          title={`Delete ${mm.title}?`}
          actions={
            <>
              <Button onClick={() => setDeleting(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={() => {
                  setDeleting(false);
                  void press('calls.deleteMeeting', { meeting: id });
                }}
              >
                Delete
              </Button>
            </>
          }
        />
      )}
    </SidePanel>
  );
}

/** A shared meeting's ⋯ (meetings.ts: Team / Private, Share's agents and link, Delete). */
function MeetingMenu({ m, canSend, onDelete }: { m: MeetingMore; canSend: boolean; onDelete: () => void }) {
  const actors = useActors();
  const nameOf = useNameOf();
  const agents = actors.filter((a) => a.kind === 'agent');
  const sent = m.sent?.bot;
  const copy = () =>
    void navigator.clipboard?.writeText(`${location.origin}/calendar?view=list&panel=${encodeURIComponent(`meeting:${m.id}`)}`).then(
      () => toast({ text: 'Link copied' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  return (
    <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>
      {m.can_edit && <MenuItem label={m.private ? 'Show to team' : 'Make private'} onSelect={() => void press('calls.setPrivate', { meeting: m.id, private: !m.private })} />}
      {canSend &&
        (sent ? (
          <MenuItem label="Sent" meta={nameOf(sent.startsWith('bot:') ? sent : `bot:${sent}`)} disabled onSelect={() => undefined} />
        ) : (
          agents.length > 0 && (
            <MenuSub label="Send to agent">
              {agents.map((a) => (
                <MenuItem key={a.id} label={a.name} onSelect={() => void press('calls.sendMeeting', { meeting: m.id, agent: a.id })} />
              ))}
            </MenuSub>
          )
        ))}
      <MenuItem label="Copy link" onSelect={copy} />
      {m.can_edit && (
        <>
          <MenuRule />
          <MenuItem label="Delete" onSelect={onDelete} />
        </>
      )}
    </Menu>
  );
}

/** Sharing a Pending meeting (meetings.ts meetReviewDetail): who sees it once shared, and the agent that files its
    tasks, Tico by default as in the old app; then Share, or Dismiss. */
function ShareBox({ m }: { m: MeetingMore }) {
  const agents = useActors().filter((a) => a.kind === 'agent');
  const [seen, setSeen] = useState<'team' | 'private'>(m.private ? 'private' : 'team');
  const [send, setSend] = useState('auto');
  const sends = [{ value: 'auto', label: 'Send to Tico' }, ...agents.map((a) => ({ value: a.id, label: `Send to ${a.name}` })), { value: '', label: "Don't send" }];
  return (
    <>
      <div className="mv-review">
        <Select
          label="Who sees it once shared"
          labelHidden
          look="quiet"
          options={[
            { value: 'team' as const, label: 'Team' },
            { value: 'private' as const, label: 'Private' },
          ]}
          value={seen}
          onValueChange={setSeen}
        />
        <Select label="Agent that files its tasks" labelHidden look="quiet" options={sends} value={send} onValueChange={setSend} />
      </div>
      <div className="mv-review">
        <Button onClick={() => void press('calls.review', { meeting: m.id, action: 'dismiss' })}>Dismiss</Button>
        <Button
          variant="primary"
          onClick={() => void press('calls.review', { meeting: m.id, action: 'approve', private: seen === 'private', ...(send ? { send } : {}) })}
        >
          Share
        </Button>
      </div>
    </>
  );
}

function SentTo({ bot }: { bot: string }) {
  const nameOf = useNameOf();
  return <Fact k="Sent to" v={nameOf(bot.startsWith('bot:') ? bot : `bot:${bot}`)} />;
}

interface Line {
  who?: string;
  /** `who` is an actor ref to name. */
  actor?: boolean;
  time?: string;
  text: string;
}

/** What was said (meetings.ts meetTranscriptHTML): its turns, with times only when the source kept them; else its text. */
function transcriptRows(m: Meeting): Line[] {
  const turns = m.turns ?? [];
  const timed = turns.some((t) => t.start_ms);
  if (turns.length) return turns.map((t) => ({ who: t.speaker ?? '', time: timed ? running((t.start_ms ?? 0) / 1000) : '', text: t.text }));
  return (m.voice_text || m.transcript_readable || '')
    .split('\n')
    .filter((l) => l.trim())
    .map((text) => ({ text }));
}

const hitsIn = (text: string, q: string) => (q ? text.toLowerCase().split(q).length - 1 : 0);

/** The words with each hit of `needle` in a <mark>; hit number `base + k` is the current one when it equals `now`. */
function markIn(text: string, needle: string, base: number, now: number): { nodes: ReactNode; n: number } {
  if (!needle) return { nodes: text, n: 0 };
  const low = text.toLowerCase();
  const nodes: ReactNode[] = [];
  let from = 0;
  let n = 0;
  for (let i = low.indexOf(needle); i >= 0; i = low.indexOf(needle, i + needle.length)) {
    nodes.push(text.slice(from, i));
    nodes.push(
      <mark key={i} className={base + n === now ? 'is-on' : undefined}>
        {text.slice(i, i + needle.length)}
      </mark>,
    );
    n += 1;
    from = i + needle.length;
  }
  nodes.push(text.slice(from));
  return { nodes, n };
}

/** The transcript or the chat (calls.js .ts-tools): search the words (Enter and Shift+Enter step through), copy them. */
function Said({ rows, what }: { rows: readonly Line[]; what: 'transcript' | 'chat' }) {
  const nameOf = useNameOf();
  const [q, setQ] = useState('');
  const [at, setAt] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const needle = what === 'transcript' ? q.trim().toLowerCase() : '';
  const named = rows.map((r) => ({ ...r, who: r.actor && r.who ? nameOf(r.who) : r.who }));
  const hits = needle ? named.reduce((n, r) => n + hitsIn(r.text, needle), 0) : 0;
  const now = hits ? at % hits : -1;
  useEffect(() => {
    box.current?.querySelector('mark.is-on')?.scrollIntoView?.({ block: 'center' });
  }, [now, needle]);
  const copy = () =>
    void navigator.clipboard?.writeText(named.map((r) => `${r.who ? `${r.who}${r.time ? ` [${r.time}]` : ''}: ` : ''}${r.text}`).join('\n')).then(
      () => toast({ text: 'Copied' }),
      () => toast({ text: "Couldn't copy", kind: 'failed' }),
    );
  // Each row's words with its hits marked; the hits are counted across rows so Enter steps through them all.
  const bodies: ReactNode[] = [];
  let seen = 0;
  for (const r of named) {
    const { nodes, n } = markIn(r.text, needle, seen, now);
    bodies.push(nodes);
    seen += n;
  }
  return (
    <section className="rail-sec">
      <div
        className="ts-tools"
        onKeyDown={(e) => {
          if (e.key !== 'Enter' || !hits) return;
          e.preventDefault();
          setAt((a) => (a + (e.shiftKey ? hits - 1 : 1)) % hits);
        }}
      >
        {what === 'transcript' && (
          <Search
            label="Search the transcript"
            placeholder="Search"
            value={q}
            onValueChange={(v) => {
              setQ(v);
              setAt(0);
            }}
          />
        )}
        {needle && <span className="ts-hits">{`${hits ? now + 1 : 0}/${hits}`}</span>}
        <span className="grow" />
        <IconButton icon="copy" label={what === 'chat' ? 'Copy chat' : 'Copy transcript'} size="sm" onClick={copy} />
      </div>
      <div className="cw-ts" ref={box}>
        {named.map((r, i) =>
          r.who || r.time ? (
            <div key={i} className="msg ts">
              <div className="msg-main">
                <div className="msg-meta">
                  {r.who && <b>{r.who}</b>}
                  {r.time && <time>{r.time}</time>}
                </div>
                <p>{bodies[i]}</p>
              </div>
            </div>
          ) : (
            <p key={i} className="ts">
              {bodies[i]}
            </p>
          ),
        )}
      </div>
    </section>
  );
}

/** The person's own notes: saved a moment after typing stops, and when the panel closes. */
function Notes({ m }: { m: Meeting }) {
  const [text, setText] = useState(m.note ?? '');
  const latest = useRef(m.note ?? '');
  const saved = useRef(m.note ?? '');
  const wait = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    const id = m.id;
    const timer = wait;
    return () => {
      clearTimeout(timer.current);
      saveNote(id, latest, saved);
    };
  }, [m.id]);
  return (
    <Field
      label="Notes"
      labelHidden
      area
      wide
      placeholder="Write notes"
      value={text}
      onValueChange={(v) => {
        setText(v);
        latest.current = v;
        clearTimeout(wait.current);
        wait.current = setTimeout(() => saveNote(m.id, latest, saved), 900);
      }}
    />
  );
}

function saveNote(meeting: string, latest: { current: string }, saved: { current: string }) {
  const v = latest.current.trim();
  if (v === saved.current.trim()) return;
  saved.current = v;
  void press('calls.editNote', { meeting, note: v });
}

/** The meeting's recording (calls.css .call-rec): play, how far, the time, download. */
function Recording({ src }: { src: string }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState(0);
  const [length, setLength] = useState(0);
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  return (
    <div className="call-rec">
      <button
        type="button"
        className="call-rec-play"
        aria-label={playing ? 'Pause' : 'Play'}
        title={playing ? 'Pause' : 'Play'}
        onClick={() => {
          const a = audio.current;
          if (!a) return;
          if (a.paused) void a.play().catch(() => undefined);
          else a.pause();
        }}
      >
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <span className="call-rec-track">
        <i style={{ width: `${length ? (at / length) * 100 : 0}%` }} />
      </span>
      <time>{mmss(length ? length - at : 0)}</time>
      <a className="ib" href={src} download target="_blank" rel="noopener noreferrer" aria-label="Download" title="Download">
        <Icon name="download" />
      </a>
      <audio
        ref={audio}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setAt(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setLength(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
      />
    </div>
  );
}

const taskOf = (it: MeetingItem) => (it.result_ref?.startsWith('task:') ? it.result_ref.slice(5) : (it.proposed_task_id ?? null));

function Item({ meeting, it, task, canPush, canEdit }: { meeting: string; it: MeetingItem; task?: boolean; canPush?: boolean; canEdit?: boolean }) {
  const made = task && it.status === 'pushed' ? taskOf(it) : null;
  const tmp = it.id.startsWith('tmp:');
  const [editing, setEditing] = useState(false);
  const done = (text: string | null) => {
    setEditing(false);
    const t = text?.trim();
    if (t && t !== it.text) void press('calls.editItem', { meeting, item: it.id, text: t });
  };
  return (
    <li className="act">
      <span />
      {editing ? (
        <input
          className="act-in"
          aria-label="Change it"
          defaultValue={it.text}
          autoFocus
          onBlur={(e) => done(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') done(e.currentTarget.value);
            if (e.key === 'Escape') {
              e.stopPropagation();
              done(null);
            }
          }}
        />
      ) : canEdit && !tmp && !made ? (
        <button type="button" className="act-text" title="Change it" onClick={() => setEditing(true)}>
          {it.text}
        </button>
      ) : (
        <span className="act-text">{it.text}</span>
      )}
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
