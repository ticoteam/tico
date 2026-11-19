import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { Input } from '@base-ui/react/input';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Popover } from '@/kit/Popover';

export type DatePickProps = {
  label: string;
  labelHidden?: boolean;
  value: Date | null;
  onValueChange: (value: Date) => void;
  /** A day and a time (the default), or a day alone. */
  withTime?: boolean;
  placeholder?: string;
  disabled?: boolean;
  error?: boolean;
};

const WEEK = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
const STEP: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };

/** A date field that opens a month to pick a day from, with a time under it. */
export function DatePick({ label, labelHidden = false, value, onValueChange, withTime = true, placeholder, disabled = false, error = false }: DatePickProps) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => startOfMonth(value ?? new Date()));
  const [draft, setDraft] = useState<Date | null>(value);
  const [focusDay, setFocusDay] = useState<Date>(value ?? new Date());
  const [time, setTime] = useState(() => formatTime(value));
  const [timeBad, setTimeBad] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const moved = useRef(false);
  const ids = useId();

  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    grid.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus();
  });

  const onOpenChange = (next: boolean) => {
    if (next) {
      const start = value ?? new Date();
      setMonth(startOfMonth(start));
      setDraft(value);
      setFocusDay(start);
      setTime(formatTime(value));
      setTimeBad(false);
    }
    setOpen(next);
  };

  const commit = (day: Date, at: string) => {
    const hm = withTime ? parseTime(at) : { h: 0, m: 0 };
    if (!hm) {
      setTimeBad(true);
      return;
    }
    const out = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hm.h, hm.m);
    onValueChange(out);
    setOpen(false);
  };

  const pick = (day: Date) => {
    setDraft(day);
    setFocusDay(day);
    if (!sameMonth(day, month)) setMonth(startOfMonth(day));
    if (!withTime) commit(day, '');
  };

  const onGridKey = (e: KeyboardEvent) => {
    const step = STEP[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const next = addDays(focusDay, step);
    setFocusDay(next);
    if (!sameMonth(next, month)) setMonth(startOfMonth(next));
    moved.current = true;
  };

  const today = new Date();
  const first = addDays(month, -month.getDay());
  const days = Array.from({ length: 42 }, (_, i) => addDays(first, i));
  const tabDay = days.some((d) => sameDay(d, focusDay)) ? focusDay : month;
  const shown = value ? formatValue(value, withTime) : null;

  const trigger = (
    <button
      type="button"
      className={['field is-date', error && 'is-error', disabled && 'is-disabled'].filter(Boolean).join(' ')}
      disabled={disabled}
      aria-labelledby={`${ids}l ${ids}v`}
    >
      <Icon name="calendar" />
      <span id={`${ids}v`} className={shown ? undefined : 'ph'}>
        {shown ?? placeholder}
      </span>
    </button>
  );

  return (
    <div className={labelHidden ? undefined : 'f'}>
      <span id={`${ids}l`} className="f-label" hidden={labelHidden}>
        {label}
      </span>
      <Popover trigger={trigger} className="date-pick" label={label} open={open} onOpenChange={onOpenChange}>
        <div className="date-pick-head">
          <span>{monthName(month)}</span>
          <span className="grow" />
          <IconButton icon="chevron" label="Previous month" size="sm" className="flip" onClick={() => setMonth(addMonths(month, -1))} />
          <IconButton icon="chevron" label="Next month" size="sm" onClick={() => setMonth(addMonths(month, 1))} />
        </div>
        <div className="date-pick-grid" ref={grid} onKeyDown={onGridKey}>
          {WEEK.map((w, i) => (
            <span key={i} aria-hidden="true">
              {w}
            </span>
          ))}
          {days.map((d) => (
            <a
              key={d.getTime()}
              role="button"
              tabIndex={sameDay(d, tabDay) ? 0 : -1}
              aria-label={dayName(d)}
              aria-pressed={draft ? sameDay(d, draft) : false}
              className={[!sameMonth(d, month) && 'is-out', sameDay(d, today) && 'is-today', draft && sameDay(d, draft) && 'is-on'].filter(Boolean).join(' ') || undefined}
              onClick={() => pick(d)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  pick(d);
                }
              }}
            >
              {d.getDate()}
            </a>
          ))}
        </div>
        {withTime && (
          <div className="date-pick-foot">
            <label className={timeBad ? 'field is-error' : 'field'}>
              <Input
                value={time}
                aria-label="Time"
                aria-invalid={timeBad || undefined}
                onValueChange={(v) => {
                  setTime(v);
                  setTimeBad(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && draft) commit(draft, time);
                }}
              />
              {timeBad && <Icon name="alert" />}
            </label>
            <Button variant="primary" disabled={!draft} onClick={() => draft && commit(draft, time)}>
              Set
            </Button>
          </div>
        )}
      </Popover>
    </div>
  );
}

function startOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function addDays(d: Date, n: number) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}
function addMonths(d: Date, n: number) {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function sameMonth(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth();
}

const monthFmt = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' });
const dayFmt = new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
const timeFmt = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });

function monthName(d: Date) {
  return monthFmt.format(d);
}
function dayName(d: Date) {
  return dayFmt.format(d);
}
/** "Tue, Nov 24, 10:15 AM", with the year only when it is not this year. */
function formatValue(d: Date, withTime: boolean) {
  const year = d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric';
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year,
    ...(withTime ? { hour: 'numeric', minute: '2-digit' } : {}),
  } as const).format(d);
}
function formatTime(d: Date | null) {
  return timeFmt.format(d ?? new Date(2000, 0, 1, 9, 0)).replace(/\u202f/g, ' ');
}
/** "10:15 AM", "10am", "22:15" → hours and minutes. */
function parseTime(text: string): { h: number; m: number } | null {
  const t = /^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$/i.exec(text);
  if (!t) return null;
  let h = Number(t[1]);
  const m = Number(t[2] ?? 0);
  const half = t[3]?.toLowerCase();
  if (m > 59) return null;
  if (half) {
    if (h < 1 || h > 12) return null;
    h = (h % 12) + (half === 'p' ? 12 : 0);
  } else if (h > 23) return null;
  return { h, m };
}
