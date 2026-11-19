import { useState } from 'react';
import type { CalendarOccurrence, CallUpcoming } from '@/api/schema';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { lengthWords } from './model';
import { PeoplePick } from './PeoplePick';
import { press, refsOf } from './parts';

const LENGTHS = [15, 30, 45, 60, 90, 120].map((m) => ({ value: String(m), label: lengthWords(m) }));

/** A booked call as /api/v2/calls/upcoming lists it. */
export type BookedCall = CallUpcoming['calls'][number];

export interface EventDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Change this one; without it the dialog adds a new event. */
  event?: CalendarOccurrence;
  /** Change a booked call's time (its title and people stay). */
  call?: BookedCall;
  /** A new event's start. */
  start?: Date;
}

type Kind = 'event' | 'voice' | 'meet';
const KINDS: { value: Kind; label: string }[] = [
  { value: 'event', label: 'Event' },
  { value: 'voice', label: 'Voice call' },
  { value: 'meet', label: 'Google Meet' },
];

/** Add or change an event (what, when, how long, who, a Google Meet link); book a voice call (calls.js "New call"); or
    move a booked call. */
export function EventDialog({ open, onOpenChange, event, call, start }: EventDialogProps) {
  const [kind, setKind] = useState<Kind>('event');
  const title = call ? 'Change time' : event ? 'Change event' : kind === 'event' ? 'New event' : 'New call';
  const change = (o: boolean) => {
    if (!o) setKind('event');
    onOpenChange(o);
  };
  return (
    <Dialog open={open} onOpenChange={change} title={title}>
      {open &&
        (call ? (
          <CallTimeForm call={call} done={() => change(false)} />
        ) : (
          <EventForm event={event} start={start} kind={kind} onKind={setKind} done={() => change(false)} />
        ))}
    </Dialog>
  );
}

function nextHalfHour(): Date {
  const d = new Date();
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d;
}

const lengthsWith = (mins: string) => (LENGTHS.some((l) => l.value === mins) ? LENGTHS : [...LENGTHS, { value: mins, label: lengthWords(Number(mins)) }]);

function EventForm({ event, start, kind, onKind, done }: { event?: CalendarOccurrence; start?: Date; kind: Kind; onKind: (k: Kind) => void; done: () => void }) {
  const [title, setTitle] = useState(event?.title ?? '');
  const [when, setWhen] = useState<Date>(() => (event ? new Date(event.start) : (start ?? nextHalfHour())));
  const was = event ? Math.round((new Date(event.end).getTime() - new Date(event.start).getTime()) / 60_000) : 30;
  const [mins, setMins] = useState(String(was > 0 ? was : 30));
  const [guests, setGuests] = useState<string[]>(() => (event ? refsOf(event.attendees) : []));
  const [meet, setMeet] = useState(!!event?.meeting_url);
  const [busy, setBusy] = useState<'save' | 'now' | null>(null);
  const voice = !event && kind === 'voice';
  // A voice call is with people and agents only; an event also takes teams and emails.
  const callees = guests.filter((g) => g.startsWith('human:') || g.startsWith('bot:'));

  const save = async () => {
    if (voice) {
      if (!callees.length) return;
      setBusy('save');
      const ok = await press('calls.schedule', { with: callees, title: title.trim() || undefined, start: when.toISOString(), minutes: Number(mins) });
      setBusy(null);
      if (ok) done();
      return;
    }
    if (!title.trim()) return;
    setBusy('save');
    const input = { title: title.trim(), start: when.toISOString(), minutes: Number(mins), with: guests, meet: event ? meet : kind === 'meet' };
    const ok = await (event ? press('calls.changeEvent', { id: event.occurrence_id, ...input }) : press('calls.createEvent', input));
    setBusy(null);
    if (ok) done();
  };
  // Start now opens the microphone: it runs inside this press, and the call page takes over.
  const now = () => {
    done();
    void press('calls.startNow', { with: callees, title: title.trim() || undefined });
  };

  return (
    <form
      className="ev-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
      <div className="f">
        <span className="f-label">Who</span>
        <PeoplePick value={guests} onChange={setGuests} quiet />
      </div>
      <div className="call-f2">
        <DatePick label="When" value={when} onValueChange={setWhen} />
        <Select label="Length" options={lengthsWith(mins)} value={mins} onValueChange={setMins} />
      </div>
      {event ? (
        <Switch label="Add Google Meet" checked={meet} onCheckedChange={setMeet} />
      ) : (
        <div className="f">
          <span className="f-label">Kind</span>
          <Segmented label="Kind" options={KINDS} value={kind} onValueChange={onKind} />
        </div>
      )}
      <div className="modal-row">
        {voice ? (
          <Button busy={busy === 'now'} onClick={now}>
            Start now
          </Button>
        ) : (
          <Button onClick={done}>Cancel</Button>
        )}
        <Button variant="primary" type="submit" busy={busy === 'save'} disabled={voice ? !callees.length : !title.trim()}>
          {voice ? 'Schedule' : event ? 'Save' : 'Add event'}
        </Button>
      </div>
    </form>
  );
}

/** A booked call's new time (meetings.ts meetCallDialog's change): its title stays; Save moves it for everyone invited. */
function CallTimeForm({ call, done }: { call: BookedCall; done: () => void }) {
  const [when, setWhen] = useState(() => new Date(call.scheduled_start));
  const was = Math.round((Date.parse(call.scheduled_end) - Date.parse(call.scheduled_start)) / 60_000);
  const [mins, setMins] = useState(String(was > 0 ? was : 30));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await press('calls.moveCall', { id: call.id, start: when.toISOString(), minutes: Number(mins) });
    setBusy(false);
    if (ok) done();
  };
  return (
    <form
      className="ev-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <Field label="Title" value={call.title || 'Call'} disabled />
      <div className="call-f2">
        <DatePick label="When" value={when} onValueChange={setWhen} />
        <Select label="Length" options={lengthsWith(mins)} value={mins} onValueChange={setMins} />
      </div>
      <div className="modal-row">
        <Button onClick={done}>Cancel</Button>
        <Button variant="primary" type="submit" busy={busy}>
          Save
        </Button>
      </div>
    </form>
  );
}
