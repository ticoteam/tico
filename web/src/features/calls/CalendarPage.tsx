import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { CalendarOccurrence, Call } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Dialog } from '@/kit/Dialog';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Popover } from '@/kit/Popover';
import { FoldButton } from '@/kit/SectionHead';
import { Segmented } from '@/kit/Segmented';
import { Calendar } from '@/pagekinds';
import { EventDialog } from './EventDialog';
import { MeetingPanel, EventPanel } from './EventPanel';
import { MeetingMenu } from './MeetingMenu';
import {
  alsoOf,
  calendarSearch,
  clock,
  dayKey,
  dayName,
  dl,
  DOW,
  HAS,
  ALSO,
  hasOf,
  hourWord,
  hoursFor,
  isLive,
  isPast,
  joinOf,
  lengthWords,
  markOf,
  minutes,
  onDay,
  panelOf,
  parseDay,
  place,
  rangeOf,
  rangeWords,
  shortRange,
  step,
  tip,
  toggled,
  type Block,
  type CalendarSearch,
  type CalView,
  type Placed,
} from './model';
import { PeoplePick } from './PeoplePick';
import { joinNow, LiveMark, press, refsOf, useCards, useNow, Who } from './parts';
import { eventsQuery, findEventsQuery, liveCallsQuery, meetingsQuery, type Meeting, type Review } from './queries';
import './calls.css';

const HOUR = 56;
const VIEW_WORDS: { value: CalView; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: '3day', label: '3 days' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'list', label: 'List' },
];
const UNIT: Record<CalView, string> = { day: 'day', '3day': '3 days', week: 'week', month: 'month', list: 'week' };

/** The calendar's address with some of it changed: what every link on the page points at. */
type At = (patch: CalendarSearch, drop?: 'panel') => CalendarSearch;

/** Calendar: Day, 3 days, Week, Month and List. Meetings, due dates, routines' runs and time away; an event opens on the right. */
export function CalendarPage() {
  const search = calendarSearch(useSearch({ strict: false }) as Record<string, unknown>);
  const navigate = useNavigate();
  const now = useNow(30_000);
  const today = parseDay(dayKey(now)) as Date;
  const view = search.view ?? 'week';
  const day = parseDay(search.date) ?? today;
  const range = rangeOf(view, view === 'list' ? today : day);
  const events = useQuery(eventsQuery(range.from, range.to));
  // List's own read (ListView reads the same): a refusal there is the page's fault, not "No meetings yet".
  const past = useQuery({ ...meetingsQuery(search.review ?? 'live', search.q ?? ''), enabled: view === 'list' });
  const [adding, setAdding] = useState(false);
  const [calling, setCalling] = useState(false);
  const cards = useCards();
  const at: At = (patch, drop) => {
    const next = { ...search, ...patch };
    if (drop) delete next[drop];
    return next;
  };
  const list = events.data?.occurrences ?? [];
  const open = panelOf(search.panel);
  const close = () => void navigate({ to: '/calendar', search: at({}, 'panel') });

  const panel =
    open?.kind === 'event' ? (
      <EventPanel id={open.id} list={list} pending={events.isPending} now={now} onClose={close} />
    ) : open?.kind === 'meeting' ? (
      <MeetingPanel id={open.id} onClose={close} />
    ) : undefined;

  const find = <ListFind q={search.q ?? ''} onFind={(q) => void navigate({ to: '/calendar', search: at({ q: q || undefined }), replace: true })} />;
  const grid =
    view === 'month' ? (
      <MonthGrid days={range.days} weeks={range.weeks} month={day.getMonth()} list={list} now={now} today={today} at={at} />
    ) : view === 'list' ? (
      <>
        {/* a phone's head keeps two controls: the search sits under it */}
        {cards && <div className="cl-find">{find}</div>}
        <ListView list={list} now={now} today={today} at={at} search={search} />
      </>
    ) : (
      <TimeGrid view={view} days={range.days} list={list} now={now} today={today} at={at} open={open?.kind === 'event' ? open.id : null} />
    );

  return (
    <>
      <Calendar
        title="Calendar"
        path="/calendar"
        className="cal-page"
        after={
          view === 'list' ? undefined : (
            <>
              <span className="cv-range">{rangeWords(view, day)}</span>
              <span className="cv-range-s">{shortRange(view, day)}</span>
              <span className="cv-step">
                <Link
                  to="/calendar"
                  search={at({ date: dayKey(step(view, day, -1)) })}
                  className="ib"
                  aria-label={`Previous ${UNIT[view]}`}
                  title={`Previous ${UNIT[view]}`}
                >
                  <Icon name="chevron" className="flip" />
                </Link>
                <Link
                  to="/calendar"
                  search={at({ date: dayKey(step(view, day, 1)) })}
                  className="ib"
                  aria-label={`Next ${UNIT[view]}`}
                  title={`Next ${UNIT[view]}`}
                >
                  <Icon name="chevron" />
                </Link>
              </span>
            </>
          )
        }
        actions={
          <>
            {view === 'list' && !cards && find}
            {view === 'list' && <ListFilter search={search} at={at} />}
            <Segmented
              label="View"
              className="cal-seg"
              options={VIEW_WORDS}
              value={view}
              onValueChange={(v) => void navigate({ to: '/calendar', search: at({ view: v }) })}
            />
            {(!cards || view === 'list') && <IconButton icon="plus" label="New event" onClick={() => setAdding(true)} data-phone />}
            {!cards && <StartCall />}
          </>
        }
        // A phone's head keeps two controls and the ⋯: Previous and Next where the view steps through dates (New event
        // then moves into the ⋯), New event in List. The view switch, Filter and Start call are always in the ⋯.
        more={
          view === 'list' || cards ? (
            <MeetingMenu>
              {cards && (
                <>
                  <MenuSub label="View" icon="calendar">
                    {VIEW_WORDS.map((o) => (
                      <MenuItem key={o.value} label={o.label} on={o.value === view} onSelect={() => void navigate({ to: '/calendar', search: at({ view: o.value }) })} />
                    ))}
                  </MenuSub>
                  {view === 'list' && (
                    <MenuSub label="Filter" icon="filter">
                      <FilterItems search={search} at={at} />
                    </MenuSub>
                  )}
                  {view !== 'list' && <MenuItem label="New event" icon="plus" onSelect={() => setAdding(true)} />}
                  <MenuItem label="Start call" icon="call" onSelect={() => setCalling(true)} />
                  <MenuRule />
                </>
              )}
            </MeetingMenu>
          ) : undefined
        }
        // Until the range is read the grid would say "Nothing this week": the kind's bars instead.
        state={view === 'list' ? (past.isError ? 'fault' : undefined) : events.isError ? 'fault' : events.isPending ? 'loading' : undefined}
        onRetry={() => void (view === 'list' ? past.refetch() : events.refetch())}
        grid={grid}
        agenda={view === 'week' || view === 'month' ? <Agenda days={range.days} list={list} now={now} today={today} at={at} /> : undefined}
        panel={panel}
      />
      <EventDialog open={adding} onOpenChange={setAdding} />
      {cards && <StartCallDialog open={calling} onOpenChange={setCalling} />}
    </>
  );
}

/* ---------------------------------------------------------------- List's search and filter */

/** Find a meeting: the words go in the address a moment after the last key. */
function ListFind({ q, onFind }: { q: string; onFind: (q: string) => void }) {
  const [text, setText] = useState(q);
  const [seen, setSeen] = useState(q);
  const wait = useRef<ReturnType<typeof setTimeout>>(undefined);
  // The address changed from elsewhere (Back, Tico): show its words.
  if (q !== seen) {
    setSeen(q);
    if (q !== text.trim()) setText(q);
  }
  useEffect(() => () => clearTimeout(wait.current), []);
  return (
    <label className="search">
      <span>
        <Icon name="search" />
      </span>
      <input
        type="search"
        placeholder="Find a meeting"
        aria-label="Find a meeting"
        value={text}
        onChange={(e) => {
          const v = e.target.value;
          setText(v);
          clearTimeout(wait.current);
          wait.current = setTimeout(() => onFind(v.trim()), 300);
        }}
      />
    </label>
  );
}

const HAS_WORDS: Record<(typeof HAS)[number], string> = { summary: 'Has summary', recording: 'Has recording', tasks: 'Has tasks' };
const ALSO_WORDS: Record<(typeof ALSO)[number], string> = { due: 'Due dates', run: 'Routines' };

/** The old Meetings tabs, what a meeting kept, and more kinds than meetings: one filter (cal-merge.js filterMenu). */
function ListFilter({ search, at }: { search: CalendarSearch; at: At }) {
  const set = !!search.review || hasOf(search.has).length > 0 || alsoOf(search.also).length > 0;
  return (
    <Menu trigger={<IconButton icon="filter" label="Filter" on={set || undefined} />}>
      <FilterItems search={search} at={at} />
    </Menu>
  );
}

/** The filter's rows: in the head's Filter menu, and on a phone under the ⋯. */
function FilterItems({ search, at }: { search: CalendarSearch; at: At }) {
  const navigate = useNavigate();
  const pending = useQuery(meetingsQuery('pending')).data?.count;
  const go = (patch: CalendarSearch) => void navigate({ to: '/calendar', search: at(patch) });
  const has = hasOf(search.has);
  const also = alsoOf(search.also);
  return (
    <>
      <MenuItem label="All meetings" on={!search.review} onSelect={() => go({ review: undefined })} />
      <MenuItem label="Pending" meta={pending ? String(pending) : undefined} on={search.review === 'pending'} onSelect={() => go({ review: 'pending' })} />
      <MenuItem label="Shared with me" on={search.review === 'shared'} onSelect={() => go({ review: 'shared' })} />
      <MenuItem label="Dismissed" on={search.review === 'dismissed'} onSelect={() => go({ review: 'dismissed' })} />
      <MenuRule />
      {HAS.map((h) => (
        <MenuItem key={h} label={HAS_WORDS[h]} on={has.includes(h)} onSelect={() => go({ has: toggled(HAS, search.has, h) })} />
      ))}
      <MenuRule />
      {ALSO.map((k) => (
        <MenuItem key={k} label={ALSO_WORDS[k]} on={also.includes(k)} onSelect={() => go({ also: toggled(ALSO, search.also, k) })} />
      ))}
    </>
  );
}

/* ---------------------------------------------------------------- the head's Start call */

function StartCall() {
  const [open, setOpen] = useState(false);
  const [who, setWho] = useState<string[]>([]);
  return (
    <Popover
      className="pop call-pick"
      label="Start call"
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setWho([]);
      }}
      align="end"
      trigger={
        <Button variant="primary" icon="call" aria-haspopup="dialog" data-phone>
          Start call
        </Button>
      }
    >
      <PeoplePick value={who} onChange={setWho} tico autoFocus />
      <Button
        variant="primary"
        className="pop-go"
        onClick={() => {
          setOpen(false);
          void press('calls.start', { with: who });
        }}
      >
        {who.length ? 'Start call' : 'Call Tico'}
      </Button>
    </Popover>
  );
}

/** Start call on a phone: the head has no room for the button, so the ⋯ opens the same pick as a dialog. */
function StartCallDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [who, setWho] = useState<string[]>([]);
  const close = () => {
    onOpenChange(false);
    setWho([]);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => (o ? onOpenChange(true) : close())}
      title="Start call"
      actions={
        <Button
          variant="primary"
          onClick={() => {
            close();
            void press('calls.start', { with: who });
          }}
        >
          {who.length ? 'Start call' : 'Call Tico'}
        </Button>
      }
    >
      {open && <PeoplePick value={who} onChange={setWho} tico autoFocus />}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- Week and Day */

interface GridProps {
  list: readonly CalendarOccurrence[];
  now: Date;
  today: Date;
  at: At;
}

/** Day, or 3 days and Week: the same columns, as many as the view's days (3 days is the phone's grid; Week there is an agenda). */
function TimeGrid({ view, days, list, now, today, at, open }: GridProps & { view: 'day' | '3day' | 'week'; days: Date[]; open: string | null }) {
  const placed = days.map((d) => onDay(list, d));
  const [h0, h1] = hoursFor(placed.flat(), view);
  const y = (m: number) => ((m - h0 * 60) * HOUR) / 60;
  const nowM = minutes(now);
  const hasNow = days.some((d) => d.getTime() === today.getTime()) && nowM >= h0 * 60 && nowM <= h1 * 60;
  const isDay = view === 'day';
  const empty = !placed.some((p) => p.length);
  return (
    <div className={isDay ? 'cv is-day' : 'cv'} style={{ '--days': days.length } as CSSProperties}>
      {!isDay && (
        <div className="cv-heads">
          <span />
          {days.map((d) => (
            <Link
              key={d.getTime()}
              to="/calendar"
              search={at({ view: 'day', date: dayKey(d) })}
              className={['cv-dh', d.getTime() === today.getTime() && 'is-today', d < today && 'is-past'].filter(Boolean).join(' ')}
              aria-label={dayName(d, today)}
            >
              <span>{DOW[d.getDay()]}</span>
              <b>{d.getDate()}</b>
            </Link>
          ))}
        </div>
      )}
      <div className="cv-allday">
        <span />
        {days.map((d, i) => {
          const all = (placed[i] ?? []).filter((p) => p.s == null);
          const max = isDay ? 4 : 2;
          const cut = all.length > max ? max - 1 : max;
          return (
            <div key={d.getTime()} className="cv-ad">
              {all.slice(0, cut).map((p) => (
                <Chip key={p.o.occurrence_id} o={p.o} now={now} at={at} />
              ))}
              {all.length > cut && (
                <Link to="/calendar" search={at({ view: 'day', date: dayKey(d) })} className="mi-more">
                  {all.length - cut} more
                </Link>
              )}
            </div>
          );
        })}
      </div>
      <div className="cv-scroll">
        <div className="cv-grid" style={{ height: (h1 - h0) * HOUR }}>
          <div className="cv-hours">
            {Array.from({ length: h1 - h0 }, (_, i) => h0 + i).map((h) =>
              hasNow && Math.abs(h * 60 - nowM) < 14 ? null : (
                <span key={h} style={{ top: y(h * 60) }}>
                  {hourWord(h)}
                </span>
              ),
            )}
            {hasNow && <b style={{ top: y(nowM) }}>{clock(nowM)}</b>}
          </div>
          {days.map((d, i) => (
            <div key={d.getTime()} className="cv-col">
              {place(placed[i] ?? [], isDay ? 5 : 2, !isDay).map((b, j) => (
                <EvBlock key={b.p?.o.occurrence_id ?? `more:${j}`} b={b} day={d} isDay={isDay} y={y} now={now} at={at} open={open} />
              ))}
              {hasNow && d.getTime() === today.getTime() && <i className="cv-now" style={{ top: y(nowM) }} />}
            </div>
          ))}
        </div>
        {empty && <p className="cv-none">{isDay ? 'Nothing this day' : view === '3day' ? 'Nothing these 3 days' : 'Nothing this week'}</p>}
      </div>
    </div>
  );
}

function EvBlock({
  b,
  day,
  isDay,
  y,
  now,
  at,
  open,
}: {
  b: Block;
  day: Date;
  isDay: boolean;
  y: (m: number) => number;
  now: Date;
  at: At;
  open: string | null;
}) {
  const pos = { '--c': b.col, '--n': b.cols, top: y(b.s) + 1 } as Record<string, number>;
  if (b.more || !b.p)
    return (
      <Link
        to="/calendar"
        search={at({ view: 'day', date: dayKey(day) })}
        className="ev ev-more"
        style={{ ...pos, height: Math.max(24, ((b.e2 - b.s) * HOUR) / 60 - 2) } as CSSProperties}
      >
        {b.more} more
      </Link>
    );
  const p: Placed = b.p;
  const o = p.o;
  const k = markOf(o);
  const e = p.e ?? (p.s as number) + 30;
  const h = Math.max(24, ((e - b.s) * HOUR) / 60 - 2);
  const lines = Math.floor((Math.min(h, b.clear ? (b.clear * HOUR) / 60 : h) - 6) / 15);
  const live = isLive(o, now);
  const wide = isDay && b.cols <= 2;
  const cls = [
    'ev',
    `ev-${k}`,
    live && 'is-due',
    h < 40 && 'is-short',
    !live && isPast(o, now) && 'is-past',
    !wide && 'is-narrow',
    !(wide || lines >= 3) && 'no-time',
    b.over && 'is-over',
    open === o.occurrence_id && 'is-on',
  ];
  const style = { ...pos, height: live && !wide ? Math.max(h, 60) : h, '--l': lines >= 3 ? lines - 1 : Math.max(1, lines) } as CSSProperties;
  const join = live && joinOf(o);
  return (
    <Link
      to="/calendar"
      search={at({ panel: `event:${o.occurrence_id}` })}
      className={cls.filter(Boolean).join(' ')}
      style={style}
      title={tip(o)}
      data-target={`event:${o.occurrence_id}`}
    >
      <span className="ev-top">
        {live ? <LiveMark /> : k === 'm' ? null : <i className={`km km-${k}`} />}
        <b>{o.title}</b>
      </span>
      <time dateTime={o.start}>
        {clock(minutes(new Date(o.start)))}
        {isDay && o.end ? ` to ${clock(minutes(new Date(o.end)))}` : ''}
      </time>
      {wide && <Who refs={refsOf(o.attendees)} max={5} />}
      {join && (
        // The block is the link that opens the event; Join inside it acts on its own press (the panel's Join is the
        // one a keyboard reaches).
        <span className="btn btn-sm ev-join" aria-hidden="true" onClick={(ev) => joinNow(o, ev)}>
          Join
        </span>
      )}
    </Link>
  );
}

function Chip({ o, now, at }: { o: CalendarOccurrence; now: Date; at: At }) {
  const k = markOf(o);
  const live = isLive(o, now);
  return (
    <Link
      to="/calendar"
      search={at({ panel: `event:${o.occurrence_id}` })}
      className={['mi', `mi-${k}`, !live && isPast(o, now) && 'is-past'].filter(Boolean).join(' ')}
      title={tip(o)}
      data-target={`event:${o.occurrence_id}`}
    >
      {live ? <LiveMark /> : k === 'm' ? null : <i className={`km km-${k}`} />}
      <span>{o.title}</span>
    </Link>
  );
}

/* ---------------------------------------------------------------- Month */

function MonthGrid({ days, weeks, month, list, now, today, at }: GridProps & { days: Date[]; weeks: number; month: number }) {
  return (
    <div className="cv-month" style={{ gridTemplateRows: `26px repeat(${weeks},minmax(0,1fr))` }}>
      {DOW.map((d) => (
        <span key={d} className="mh">
          {d}
        </span>
      ))}
      {days.map((d) => {
        // a routine gives its line to a meeting or a due date
        const evs = onDay(list, d).sort((a, b) => Number(a.o.kind === 'run') - Number(b.o.kind === 'run'));
        const cls = ['mc', d.getTime() === today.getTime() && 'is-today', d < today && 'is-past', d.getMonth() !== month && 'is-out'];
        return (
          <div key={d.getTime()} className={cls.filter(Boolean).join(' ')}>
            <Link to="/calendar" search={at({ view: 'day', date: dayKey(d) })} className="mc-n" aria-label={dayName(d, today)}>
              {d.getDate()}
            </Link>
            {evs.slice(0, 3).map((p) => (
              <Chip key={p.o.occurrence_id} o={p.o} now={now} at={at} />
            ))}
            {evs.length > 3 && (
              <Link to="/calendar" search={at({ view: 'day', date: dayKey(d) })} className="mi-more">
                {evs.length - 3} more
              </Link>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- the phone's agenda (Week and Month) */

function Agenda({ days, list, now, today, at }: GridProps & { days: Date[] }) {
  const groups = days.map((d) => ({ d, evs: onDay(list, d) })).filter((g) => g.evs.length);
  if (!groups.length) return <Empty title="Nothing on the calendar" />;
  return (
    <div className="page-body cl-body">
      {groups.map((g) => (
        <section key={g.d.getTime()} className="cl-sec">
          <DayHead d={g.d} today={today} />
          {g.evs.map((p) => (
            <EventRow key={p.o.occurrence_id} o={p.o} now={now} at={at} />
          ))}
        </section>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- List: what Meetings was */

const kept = (m: Meeting, h: (typeof HAS)[number]) =>
  h === 'summary'
    ? Array.isArray(m.outcome?.summary)
      ? m.outcome.summary.length > 0
      : !!m.outcome?.summary
    : h === 'recording'
      ? !!m.media_url
      : (m.outcome?.tasks?.length ?? 0) > 0;

const REVIEW_WORDS: Record<Review, string> = { live: 'Past', pending: 'Pending', dismissed: 'Dismissed', shared: 'Shared with me' };

function ListView({ list, now, today, at, search }: GridProps & { search: CalendarSearch }) {
  const q = search.q ?? '';
  const has = hasOf(search.has);
  // Only past meetings are shared, pending or dismissed, and only they keep a summary: those filters show the Past alone.
  const pastOnly = !!search.review || has.length > 0;
  const kinds = new Set<string>(['event', ...alsoOf(search.also)]);
  const live = useQuery(liveCallsQuery);
  const past = useQuery(meetingsQuery(search.review ?? 'live', q));
  const found = useQuery({ ...findEventsQuery(q), enabled: !!q });
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const fold = (k: string) => {
    const next = new Set(folded);
    if (!next.delete(k)) next.add(k);
    setFolded(next);
  };
  const meetings = pastOnly ? [] : (q ? (found.data?.occurrences ?? []) : list).filter((o) => kinds.has(o.kind));
  const onNow = meetings.filter((o) => isLive(o, now));
  const coming = meetings.filter((o) => !isLive(o, now) && !isPast(o, now));
  const booked = new Set(onNow.map(callOf));
  const words = q.toLowerCase();
  const calls = pastOnly
    ? []
    : (live.data?.calls ?? []).filter((c) => c.state === 'live' && !booked.has(c.id) && (c.title || 'Call').toLowerCase().includes(words));
  const done = (past.data?.meetings ?? []).filter((m) => has.every((h) => kept(m, h)));
  const comingDays = groupBy(coming, (o) => dayKey(new Date(o.start)));
  const pastDays = groupBy(done, (m) => dayKey(new Date(m.started ?? m.created ?? 0)));

  if (!onNow.length && !calls.length && !coming.length && !done.length)
    return past.isPending || (q && found.isPending) ? null : (
      <div className="page-body cl-body is-empty">
        <Empty
          title={search.review === 'shared' && !q && !has.length ? 'Nothing shared with you' : q || pastOnly || kinds.size > 1 ? 'Nothing found' : 'No meetings or calls yet'}
          icon="calendar"
        />
      </div>
    );

  return (
    <div className="page-body cl-body">
      {(onNow.length > 0 || calls.length > 0) && (
        <div className="cl-pin">
          {calls.map((c) => (
            <LiveCallRow key={c.id} call={c} now={now} />
          ))}
          {onNow.map((o) => (
            <EventRow key={o.occurrence_id} o={o} now={now} at={at} />
          ))}
        </div>
      )}
      {coming.length > 0 && (
        <ListSec name="Coming" count={coming.length} open={!folded.has('coming')} onToggle={() => fold('coming')}>
          {comingDays.map(([key, evs]) => (
            <div key={key}>
              <DayHead d={parseDay(key) as Date} today={today} />
              {evs.map((o) => (
                <EventRow key={o.occurrence_id} o={o} now={now} at={at} />
              ))}
            </div>
          ))}
        </ListSec>
      )}
      {done.length > 0 && (
        <ListSec name={REVIEW_WORDS[search.review ?? 'live']} count={done.length} open={!folded.has('past')} onToggle={() => fold('past')}>
          {pastDays.map(([key, ms]) => (
            <div key={key}>
              <DayHead d={parseDay(key) as Date} today={today} />
              {ms.map((m) => (
                <PastRow key={m.id} m={m} at={at} />
              ))}
            </div>
          ))}
        </ListSec>
      )}
    </div>
  );
}

function callOf(o: CalendarOccurrence): string | null {
  const way = joinOf(o);
  return way && 'call' in way ? way.call : null;
}

function groupBy<T>(xs: readonly T[], key: (x: T) => string): [string, T[]][] {
  const m = new Map<string, T[]>();
  for (const x of xs) m.set(key(x), [...(m.get(key(x)) ?? []), x]);
  return [...m];
}

function ListSec({ name, count, open, onToggle, children }: { name: string; count: number; open: boolean; onToggle: () => void; children: ReactNode }) {
  return (
    <section className={open ? 'cl-sec' : 'cl-sec is-folded'}>
      <header className="cl-sec-head">
        <h2 className="sec-name">
          <FoldButton name={name} count={count} open={open} onToggle={onToggle} />
        </h2>
      </header>
      {open && children}
    </section>
  );
}

function DayHead({ d, today }: { d: Date; today: Date }) {
  const name = dayName(d, today);
  const near = name === 'Today' || name === 'Tomorrow' || name === 'Yesterday';
  return (
    <div className="cl-day">
      <h3 className="sec-name is-2">{dl(d)}</h3>
      {near && <span className="stage-n">{name}</span>}
    </div>
  );
}

function EventRow({ o, now, at }: { o: CalendarOccurrence; now: Date; at: At }) {
  const live = isLive(o, now);
  const s = new Date(o.start);
  const len = o.end ? (new Date(o.end).getTime() - s.getTime()) / 60_000 : 0;
  const way = joinOf(o);
  const cls = ['row', 'cl-row', live && 'is-live', !live && isPast(o, now) && 'is-past'];
  return (
    <div className={cls.filter(Boolean).join(' ')} title={tip(o)}>
      <span className="er-time">{o.all_day ? 'All day' : clock(minutes(s))}</span>
      <span className="cl-mark">
        {live ? <LiveMark /> : callOf(o) ? <Icon name="call" /> : o.kind !== 'event' ? <i className={`km km-${markOf(o)}`} /> : null}
      </span>
      <Link to="/calendar" search={at({ panel: `event:${o.occurrence_id}` })} className="row-title" data-target={`event:${o.occurrence_id}`}>
        {o.title}
      </Link>
      <span className="row-pills">{way && 'url' in way && /meet\.google\.com/.test(way.url) ? <span className="pill">Google Meet</span> : null}</span>
      <span className="cl-kept" />
      <span className="row-age cl-len">
        {live ? `${Math.max(0, Math.round((now.getTime() - s.getTime()) / 60_000))} min in` : len > 0 ? lengthWords(len) : ''}
      </span>
      <span className="er-who">
        <Who refs={refsOf(o.attendees)} />
      </span>
      <span className="cl-act">
        {live && way && (
          <Button className="btn-sm cl-join" onClick={(e) => joinNow(o, e)}>
            Join
          </Button>
        )}
      </span>
    </div>
  );
}

/** An unbooked call that is on (no event): "Call with Maya". */
function LiveCallRow({ call, now }: { call: Call; now: Date }) {
  const s = new Date(call.started_at);
  const others = call.participants.filter((p) => p.in_call).map((p) => p.actor);
  return (
    <div className="row cl-row is-live" title={call.title}>
      <span className="er-time">{clock(minutes(s))}</span>
      <span className="cl-mark">
        <LiveMark />
      </span>
      <Link to="/call/$room" params={{ room: call.id }} className="row-title" data-target={`call:${call.id}`}>
        {call.title || 'Call'}
      </Link>
      <span className="row-pills" />
      <span className="cl-kept" />
      <span className="row-age cl-len">{`${Math.max(0, Math.round((now.getTime() - s.getTime()) / 60_000))} min in`}</span>
      <span className="er-who">
        <Who refs={others} />
      </span>
      <span className="cl-act">
        <Button className="btn-sm cl-join" onClick={() => void press('calls.join', { id: call.id })}>
          Join
        </Button>
      </span>
    </div>
  );
}

/** A meeting that is over. In Pending it has a ⋯ to share or dismiss it. */
function PastRow({ m, at }: { m: Meeting; at: At }) {
  const s = new Date(m.started ?? m.created ?? 0);
  const summary = Array.isArray(m.outcome?.summary) ? m.outcome.summary.length > 0 : !!m.outcome?.summary;
  const tasks = m.outcome?.tasks?.length ?? 0;
  const who = (m.participants ?? []).map((p) => p.person_id ?? p.email ?? p.name ?? '').filter(Boolean);
  return (
    <div className={m.review_state === 'pending' ? 'row cl-row is-past is-unread' : 'row cl-row is-past'} title={m.title}>
      <span className="er-time">{clock(minutes(s))}</span>
      <span className="cl-mark">
        <Icon name="call" />
      </span>
      <Link to="/calendar" search={at({ view: 'list', panel: `meeting:${m.id}` })} className="row-title" data-target={`meeting:${m.id}`}>
        {m.title}
      </Link>
      <span className="row-pills" />
      <span className="cl-kept">
        <span>{summary && <Icon name="file" label="Summary" />}</span>
        <span>{m.media_url && <Icon name="play" label="Recording" />}</span>
        {tasks > 0 ? (
          <span className="cl-k" aria-label={`${tasks} tasks made`}>
            <Icon name="tasks" />
            <i>{tasks}</i>
          </span>
        ) : (
          <span />
        )}
      </span>
      <span className="row-age cl-len">{m.duration_ms ? lengthWords(m.duration_ms / 60_000) : ''}</span>
      <span className="er-who">
        <Who refs={who} />
      </span>
      <span className="cl-act">
        {m.review_state === 'pending' && (
          <Menu trigger={<IconButton icon="more" label={`More for ${m.title}`} size="sm" quiet />}>
            <MenuItem label="Share & send" onSelect={() => void press('calls.review', { meeting: m.id, action: 'approve', send: 'auto' })} />
            <MenuItem label="Dismiss" onSelect={() => void press('calls.review', { meeting: m.id, action: 'dismiss' })} />
          </Menu>
        )}
      </span>
    </div>
  );
}
