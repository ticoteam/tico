import { fmtAge, fmtDate } from '@/lib';

export type AgoProps = {
  at: Date | string;
  /** `row-age` in a row; the bare `time` style elsewhere. */
  className?: string;
};

/** How long ago ("4m", "2h", "3d"), with the date on hover. The clock is the one `@/lib` holds, so tests can set it. */
export function Ago({ at, className }: AgoProps) {
  const d = typeof at === 'string' ? new Date(at) : at;
  return (
    <time className={className} dateTime={d.toISOString()} title={fmtDate(d)}>
      {fmtAge(d)}
    </time>
  );
}
