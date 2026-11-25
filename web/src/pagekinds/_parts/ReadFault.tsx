import { ApiError } from '@/api/client';
import { Button, type ButtonVariant } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { passing, useReadFault, type FailedRead } from '@/live';

export type { FailedRead };

// One way every read that failed with nothing to show says so (live/fault.ts): "<what> didn't load · <why>", read
// again by itself once the stream is back when that could help, and Try again only when it could not (a refusal, a
// stopped company, the app's own error).

/** A read paused offline fails with no error: it got no answer. */
const NO_ANSWER = new ApiError(0, 'network', 'No answer', true);

/** The reads a fault is about: those that failed, or, when none has an error (paused offline), each one as unanswered. */
export function failedOf(reads: readonly FailedRead[]): FailedRead[] {
  const failed = reads.filter((r) => r.error != null);
  return failed.length ? failed : reads.map((r) => ({ error: NO_ANSWER, refetch: r.refetch }));
}

/** Whether the fault needs Try again: a read in it is not read again by itself. */
export const needsRetry = (reads: readonly FailedRead[]) => failedOf(reads).some((r) => !passing(r.error));

export interface ReadFaultProps {
  /** "Tasks": the line is "Tasks didn't load · server restarting". */
  what: string;
  /** The reads behind it (a query's own result fits); the first that failed says why, one that reading again would not
      fix before any. */
  reads: readonly FailedRead[];
  /** Try again's own way, when reading again takes more than the reads (the router's reset). */
  onRetry?: () => void;
  /** One line and a quiet Try again, inside a row or a panel's text, instead of the centered empty state. */
  inline?: boolean;
  /** Try again's look in the empty state: a page's one filled button. */
  variant?: ButtonVariant;
}

/** A read that failed with nothing to show: its one line and, only when reading again by itself cannot help, Try again. */
export function ReadFault({ what, reads, onRetry, inline, variant }: ReadFaultProps) {
  const failed = failedOf(reads);
  const line = useReadFault(what, failed, false) ?? `${what} didn't load`;
  const retry = onRetry ?? (() => Promise.all(failed.map((r) => r.refetch({ cancelRefetch: true }))));
  const again = needsRetry(failed) && (
    <Button variant={inline ? 'quiet' : variant} onClick={retry}>
      Try again
    </Button>
  );
  if (inline)
    return (
      <>
        <span>{line}</span>
        {again && ' '}
        {again}
      </>
    );
  return (
    <Empty icon="alert" title={line}>
      {again}
    </Empty>
  );
}
