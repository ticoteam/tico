import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect, useRef } from 'react';
import type { Task } from '@/api/schema';
import { meQuery } from '@/data';
import { FlowMark } from '@/kit/FlowMark';
import { Link } from '@/kit/Link';
import { newAddressFor } from '@/oldAddress/map';
import './status.css';

/* A task's one status line (the server's `status_line`, task_reads.status_line), the same on its board card, its head
   and over its chat's message box; the stream sends it again as it changes. What it is about is the task's turn: whose
   turn Needs you is (said only to that person), and Paused's why and where it is fixed. */

type Line = NonNullable<Task['status_line']>;
type Shown = Pick<Task, 'status_line' | 'turn'>;

const WORDS: Record<Line, string> = {
  starting: 'Starting',
  up_next: 'Up next',
  working: 'Working',
  needs_you: 'Needs you',
  paused: 'Paused',
};

/** The line this reader sees, or null: none, or Needs you for someone else. */
export const lineFor = (t: Shown, me: string | undefined): Line | null => {
  const line = t.status_line ?? null;
  return line === 'needs_you' && (!me || t.turn?.who !== me) ? null : line;
};

/** Where Paused's Fix goes in this app: the server names the old app's page ("#/settings/computers"). */
export const fixHref = (href: string | null | undefined) => (href ? (href.startsWith('#/') ? newAddressFor(href) : href) : null);

/** The line. `inLink`: it sits inside a link (a board card), which may not hold another: Fix there is only its place,
    and `CardFix` beside the card is the link. */
export function StatusLine({ t, me, inLink = false }: { t: Shown; me: string | undefined; inLink?: boolean }) {
  const line = lineFor(t, me);
  if (!line) return null;
  const href = line === 'paused' ? fixHref(t.turn?.href) : null;
  return (
    <span className={`status-line is-${line}`} data-state={line} title={line === 'paused' ? t.turn?.line : undefined}>
      {line === 'needs_you' && (
        <span aria-hidden="true">
          <FlowMark size={8} />
        </span>
      )}
      {WORDS[line]}
      {href && (
        <>
          {' · '}
          {inLink ? (
            <span className="status-fix status-fix-slot" aria-hidden="true">
              Fix
            </span>
          ) : (
            <Link className="status-fix" href={href}>
              Fix
            </Link>
          )}
        </>
      )}
    </span>
  );
}

/** Paused's Fix on a board card: a real link next to the card's own (so the keyboard reaches it after the card), laid
    over the place the card's line keeps for it. */
export function CardFix({ t, me }: { t: Shown; me: string | undefined }) {
  const ref = useRef<HTMLAnchorElement>(null);
  const href = lineFor(t, me) === 'paused' ? fixHref(t.turn?.href) : null;
  useLayoutEffect(() => {
    const a = ref.current;
    const item = a?.parentElement;
    if (!a || !item) return;
    const place = () => {
      const slot = item.querySelector('.status-fix-slot');
      if (!slot) return;
      // Moved by what is between them now, so a margin or a border on either side does not shift it.
      const s = slot.getBoundingClientRect();
      const now = a.getBoundingClientRect();
      a.style.left = `${a.offsetLeft + s.left - now.left}px`;
      a.style.top = `${a.offsetTop + s.top - now.top}px`;
    };
    place();
    // The line's words move once the web font is in.
    void document.fonts?.ready.then(place);
    if (typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(place);
    watch.observe(item);
    return () => watch.disconnect();
  }, [href]);
  if (!href) return null;
  return (
    <Link ref={ref} className="status-fix card-fix" href={href} aria-label={`Fix: ${t.turn?.line ?? 'paused'}`}>
      Fix
    </Link>
  );
}

/** The line for the signed-in person. */
export function TaskStatus({ t }: { t: Shown }) {
  const me = useQuery(meQuery).data?.id;
  return <StatusLine t={t} me={me} />;
}
