/** The one cap: 99 and under show as they are, anything more is "99+". */
export function capCount(n: number): string {
  return n > 99 ? '99+' : String(n);
}

export type CountProps = {
  n: number;
  /** What the number counts, read aloud ("3 unread"). */
  label?: string;
  className?: string;
};

/** The one count style everywhere: muted, small, tabular, no box (aaa-audit-a: `.count` wins, `.count-pill` is gone). */
export function Count({ n, label, className }: CountProps) {
  return (
    <span className={className ? `count ${className}` : 'count'} aria-label={label}>
      {capCount(n)}
    </span>
  );
}

export type BadgeProps = {
  n: number;
  label: string;
  /** The Dot holding the count, on the Flow slot only. */
  flow?: boolean;
};

/** A count on an icon. Only the 56px rail and the phone's bar use it; it sits inside a `.slot-ico`. */
export function Badge({ n, label, flow }: BadgeProps) {
  if (n <= 0) return null;
  return (
    <i className={flow ? 'slot-n is-flow' : 'slot-n'} aria-label={label}>
      {capCount(n)}
    </i>
  );
}
