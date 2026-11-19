import { useState } from 'react';
import type { CalendarOccurrence } from '@/api/schema';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { lengthWords } from './model';
import { PeoplePick } from './PeoplePick';
import { press, refsOf } from './parts';

const LENGTHS = [15, 30, 45, 60, 90, 120].map((m) => ({ value: String(m), label: lengthWords(m) }));

export interface EventDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Change this one; without it the dialog adds a new event. */
  event?: CalendarOccurrence;
  /** A new event's start. */
  start?: Date;
}

/** Add or change an event: what, when, how long, who, and a Google Meet link. */
export function EventDialog({ open, onOpenChange, event, start }: EventDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={event ? 'Change event' : 'New event'}>
      {open && <EventForm event={event} start={start} done={() => onOpenChange(false)} />}
    </Dialog>
  );
}

function nextHalfHour(): Date {
  const d = new Date();
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d;
}

function EventForm({ event, start, done }: { event?: CalendarOccurrence; start?: Date; done: () => void }) {
  const [title, setTitle] = useState(event?.title ?? '');
  const [when, setWhen] = useState<Date>(() => (event ? new Date(event.start) : (start ?? nextHalfHour())));
  const was = event ? Math.round((new Date(event.end).getTime() - new Date(event.start).getTime()) / 60_000) : 30;
  const [mins, setMins] = useState(String(was > 0 ? was : 30));
  const [guests, setGuests] = useState<string[]>(() => (event ? refsOf(event.attendees) : []));
  const [meet, setMeet] = useState(!!event?.meeting_url);
  const [busy, setBusy] = useState(false);
  const lengths = LENGTHS.some((l) => l.value === mins) ? LENGTHS : [...LENGTHS, { value: mins, label: lengthWords(Number(mins)) }];

  const save = async () => {
    if (!title.trim()) return;
    setBusy(true);
    const input = { title: title.trim(), start: when.toISOString(), minutes: Number(mins), with: guests, meet };
    const ok = await (event ? press('calls.changeEvent', { id: event.occurrence_id, ...input }) : press('calls.createEvent', input));
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
      <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
      <div className="call-f2">
        <DatePick label="When" value={when} onValueChange={setWhen} />
        <Select label="Length" options={lengths} value={mins} onValueChange={setMins} />
      </div>
      <div className="f">
        <span className="f-label">Who</span>
        <PeoplePick value={guests} onChange={setGuests} quiet />
      </div>
      <Switch label="Add Google Meet" checked={meet} onCheckedChange={setMeet} />
      <div className="modal-row">
        <Button onClick={done}>Cancel</Button>
        <Button variant="primary" type="submit" busy={busy} disabled={!title.trim()}>
          {event ? 'Save' : 'Add event'}
        </Button>
      </div>
    </form>
  );
}
