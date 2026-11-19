import { fmtDate } from '@/lib';

export type DateTextProps = {
  at: Date | string;
  className?: string;
};

/** A date as words ("Nov 19"), with the exact moment in `dateTime`. */
export function DateText({ at, className }: DateTextProps) {
  const d = typeof at === 'string' ? new Date(at) : at;
  return (
    <time className={className} dateTime={d.toISOString()}>
      {fmtDate(d)}
    </time>
  );
}
