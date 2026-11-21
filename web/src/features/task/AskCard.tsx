import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FlowMark } from '@/kit/FlowMark';
import { Link } from '@/kit/Link';
import { useCards } from '@/kit/Popover';
import { toast } from '@/kit/Toast';
import { answered } from './answered';
import { failedToast, reasonOf, tabAddress } from './model';
import { Face } from './parts';
import { waitingQuery, type TaskData } from './queries';
import './ask.css';

/* One ask card (master plan P1): whatever waits on a person draws in this one frame, its body by its kind. One line
   heads it: the Dot, the ask, what answering does, who asks. Then the kind's own body, one row of buttons (the primary
   last), and what went wrong, said in place. In a thread the whole card is a link to where it is answered. */

export interface AskCardProps {
  /** The ask in one line; `note` what answering does ("Sends now") or where it stands ("Changes asked"). */
  name: ReactNode;
  note?: ReactNode;
  /** Who asks (their face), or `end` in its place (Open, a chevron). */
  who?: string | null;
  end?: ReactNode;
  /** `asked`: it waits on someone else now (a hollow ring for the Dot); `asking`: "What should change?" is open. */
  asked?: boolean;
  asking?: boolean;
  /** The buttons, the primary last; `error` a refusal or what did not load; `href` makes the whole card a link, named by
      what it says. */
  actions?: ReactNode;
  error?: ReactNode;
  href?: string;
  label?: string;
  target?: string;
  children?: ReactNode;
}

export function AskCard({ name, note, who, end, asked, asking, actions, error, href, label = 'The decision', target, children }: AskCardProps) {
  const className = ['answer askcard', asked && 'is-asked', asking && 'is-asking', href && 'is-link'].filter(Boolean).join(' ');
  // On a computer what answering does sits at the left of the buttons' row while that row ends the card, and goes while
  // "What should change?" is open; on a phone, or with no row, it stays in the top line.
  const cards = useCards();
  const where = !note || (asking && !cards) ? null : !cards && !asked && actions && !error ? 'row' : 'top';
  const noted = <span className="askcard-note">{note}</span>;
  const inner = (
    <>
      <div className="askcard-top">
        {asked ? <i className="hollow" /> : <FlowMark />}
        <b className="an-name">{name}</b>
        {where === 'top' && noted}
        <span className="grow" />
        {end ?? <Face id={who} />}
      </div>
      {children}
      {actions && (
        <div className="an-row">
          {where === 'row' && noted}
          {actions}
        </div>
      )}
      {error && (
        <p className="an-err" role="alert">
          {error}
        </p>
      )}
    </>
  );
  return href ? (
    <Link className={className} href={href} data-target={target}>
      {inner}
    </Link>
  ) : (
    <section className={className} aria-label={label} data-target={target}>
      {inner}
    </section>
  );
}

/** On a phone the card holds the page's bottom (its dock); elsewhere it stands where it is drawn. */
export const Docked = ({ dock, children }: { dock: boolean; children: ReactNode }) =>
  dock ? (
    <div className="dock" data-dock>
      {children}
    </div>
  ) : (
    children
  );

/** Around a card's answer: a refusal is said in the card, or in a toast once the card has gone (never silent). After
    the answer, in Flow, the next task that waits on the person opens (`nextUp`, picked before answering: the answered
    row leaves the list at once); outside Flow the open task's page decides, not once the person has moved on. */
export function useAnswering(t: Pick<TaskData, 'id'>, flow: boolean) {
  const waiting = useQuery({ ...waitingQuery, enabled: flow });
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const here = useRef(true);
  useEffect(() => {
    here.current = true;
    return () => {
      here.current = false;
    };
  }, []);
  const nextUp = () => {
    const rows = waiting.data ?? [];
    const i = rows.findIndex((w) => w.id === t.id);
    return (i >= 0 ? (rows[i + 1] ?? rows[i - 1]) : rows[0]) ?? null;
  };
  const refused = (res: { error?: string; why?: string }) => {
    if (!here.current) toast(failedToast(res));
    setError(reasonOf(res.error));
  };
  const moveOn = (next: ReturnType<typeof nextUp>) => {
    if (!flow) return void (here.current && answered());
    const to = next ? tabAddress(next, undefined, true) : { to: '/flow', search: {} };
    void navigate({ to: to.to as never, search: to.search as never });
  };
  return { error, setError, here, nextUp, refused, moveOn };
}
