import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { run, useAction } from '@/actions';
import type { TaskQuestion, TaskQuestionRow } from '@/api/schema';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { DateText } from '@/kit/DateText';
import { FlowMark } from '@/kit/FlowMark';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { nextAfter, useFlowRows } from './flowOrder';
import { snoozeChoices } from './Head';
import { failedToast, reasonOf, tabAddress } from './model';
import { Face, Go, NameOf } from './parts';
import { type TaskData } from './queries';

/* A question is a task for a person, filed under the agent's task that asked it (backend/questions.py). Its page opens
   on the question: who asks, on what, the detail, and where it stands. While it is open, the person it is for answers
   in the box that takes the chat's message box's place, as the decision does: on a computer at the foot of the task's
   chat, on a phone at the bottom of every tab. Only that box answers. Any other task shows its description at the top,
   and the questions asked on it, each a row to its own task. */

/** Whether the reader answers this task's question now: it is open and asks them. Their chat's message box gives way
    to the answer box then. */
export function answersHere(t: TaskData, me: string | undefined): boolean {
  return t.question?.state === 'open' && !!me && t.question.person === me;
}

/** A task's own address by its number, wherever it lives (`/task/<n>` finds its pipeline). */
const taskHref = (number: number | null, id: string) => `/task/${number ?? encodeURIComponent(id)}`;

/** The top of the open task: the question card on a question; else its description, then the questions asked on it. */
export function TaskLead({ t }: { t: TaskData }) {
  const asked = (t.questions ?? []).filter((q) => q.state === 'open');
  const body = (t.body ?? '').trim();
  if (!t.question && !body && !asked.length) return null;
  return (
    <div className="tv-lead">
      {t.question ? <QuestionCard t={t} q={t.question} /> : body && <Description text={body} />}
      {asked.length > 0 && <AskedHereRows rows={asked} />}
    </div>
  );
}

function QuestionCard({ t, q }: { t: TaskData; q: TaskQuestion }) {
  const me = useQuery(meQuery).data?.id;
  const body = (t.body ?? '').trim();
  // A task deleted, or one the reader may not open, is not named.
  const on = q.parent;
  return (
    <section className="answer qn" aria-label="Question" data-target={`task-question:${t.id}`}>
      <p className="qn-from">
        <Face id={q.asker} size={16} decorative />
        <span>
          From <NameOf id={q.asker} />
          {on && (
            <>
              {' · '}on{' '}
              <Go to={taskHref(on.number, on.id)} className="qn-parent">
                {on.title}
                {on.number != null && ` (#${on.number})`}
              </Go>
            </>
          )}
        </span>
      </p>
      {body && <p className="qn-body">{body}</p>}
      {q.state === 'answered' ? (
        <Answered q={q} />
      ) : q.state === 'withdrawn' ? (
        <p className="qn-note">
          <Ended q={q} />
        </p>
      ) : answersHere(t, me) ? null : (
        <p className="qn-note">
          Waiting on <NameOf id={q.person} />
        </p>
      )}
    </section>
  );
}

/** Why a question ended without an answer, in a few words. */
function Ended({ q }: { q: TaskQuestion }) {
  switch (q.ended_reason) {
    case 'parent_ended':
      return <>Withdrawn: its task ended</>;
    case 'parent_moved':
      return <>Withdrawn: its task moved</>;
    case 'asker_gone':
      return (
        <>
          Withdrawn: <NameOf id={q.asker} /> left
        </>
      );
    case 'person_gone':
      return (
        <>
          Withdrawn: <NameOf id={q.person} /> left
        </>
      );
    default:
      return <>Withdrawn</>;
  }
}

/** Who answered and when, then the answer itself. */
function Answered({ q }: { q: TaskQuestion }) {
  return (
    <>
      <p className="qn-note">
        Answered by <NameOf id={q.answered_by} />
        {q.answered_at && (
          <>
            {' · '}
            <DateText at={q.answered_at} />
          </>
        )}
      </p>
      {q.answer_text && <p className="qn-said">{q.answer_text}</p>}
    </>
  );
}

export interface QuestionReplyProps {
  t: TaskData;
  /** Opened from Flow: once answered, the next item opens. */
  flow: boolean;
  /** The page's own dock (a phone's bottom); on a computer the box is in the task's chat. */
  dock?: boolean;
}

/** The answer box, for the person the open question asks; nothing for anyone else or once it has ended. */
export function QuestionReply({ t, flow, dock = false }: QuestionReplyProps) {
  const me = useQuery(meQuery).data?.id;
  if (!t.question || !answersHere(t, me)) return null;
  const box = <Reply t={t} q={t.question} flow={flow} />;
  return dock ? (
    <div className="dock" data-dock>
      {box}
    </div>
  ) : (
    box
  );
}

/** Free text, Answer, Snooze, and Open when the question links to where it is done. Answer is given to the round the
    person was shown; a question asked again since is refused, read again, and says Look again. */
function Reply({ t, q, flow }: { t: TaskData; q: TaskQuestion; flow: boolean }) {
  const answer = useAction('task.answerQuestion');
  const navigate = useNavigate();
  const order = useFlowRows(flow);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  // The round the person has read: a refused answer reads the question again, and its new round counts as read once the
  // card shows it with Look again.
  const [seen, setSeen] = useState(q.round);
  if (stale && seen !== q.round) setSeen(q.round);

  const onward = (next: ReturnType<typeof nextAfter>) => {
    const to = next ? tabAddress(next, undefined, true) : { to: '/flow', search: {} };
    void navigate({ to: to.to as never, search: to.search as never });
  };

  const press = async () => {
    setError('');
    setStale(false);
    // Picked before answering: the answered question leaves Flow at once.
    const next = flow ? nextAfter(order.rows, t.id) : null;
    const res = await answer.run({ task: t.id, text, round: seen });
    if (!res.ok) {
      if (res.error?.startsWith('question_stale')) return setStale(true);
      return setError(reasonOf(res.error));
    }
    setText('');
    toast({ text: 'Answered' });
    if (flow) onward(next);
  };

  const snooze = async (until: string | null) => {
    const next = flow && until ? nextAfter(order.rows, t.id) : null;
    const r = await run('task.snooze', { task: t.id, until }, { via: 'click' });
    if (!r.ok) return toast(failedToast(r));
    if (flow && until) onward(next);
  };

  return (
    <section className="answer qn qn-reply" aria-label="Answer the question">
      <label className="dcx-ask">
        <span>Your answer</span>
        <textarea className="dcx-field" rows={2} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {(stale || error) && (
        <p className="an-err" role="alert">
          {stale ? 'Look again' : error}
        </p>
      )}
      <div className="an-row">
        {q.link_url && (
          <a className="btn" href={q.link_url} target="_blank" rel="noreferrer">
            Open ↗
          </a>
        )}
        <span className="grow" />
        {t.snoozed ? (
          <Button onClick={() => snooze(null)}>Unsnooze</Button>
        ) : (
          <Menu trigger={<Button>Snooze</Button>}>
            {snoozeChoices().map(([label, at]) => (
              <MenuItem key={label} label={label} onSelect={() => void snooze(at.toISOString())} />
            ))}
          </Menu>
        )}
        <Button variant="primary" busy={answer.pending} disabled={!text.trim() || answer.pending} onClick={() => press()}>
          Answer
        </Button>
      </div>
    </section>
  );
}

/** A task's description: what it is for, in a few lines; a long one opens on More. */
function Description({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const long = text.length > 280 || text.split('\n').length > 4;
  return (
    <section className={long && !all ? 'tv-desc is-short' : 'tv-desc'} aria-label="Description">
      <p className="tv-desc-text">{text}</p>
      {long && (
        <Button variant="quiet" className="btn-sm" onClick={() => setAll(!all)}>
          {all ? 'Less' : 'More'}
        </Button>
      )}
    </section>
  );
}

/** The questions open on this task, a row each to its own task: "Waiting on Dana: Which carrier?". */
function AskedHereRows({ rows }: { rows: TaskQuestionRow[] }) {
  const me = useQuery(meQuery).data?.id;
  return (
    <ul className="tv-asked" aria-label="Questions">
      {rows.map((q) => (
        <li key={q.task_id}>
          <Go to={taskHref(q.number, q.task_id)} className="tv-asked-row">
            {q.person === me ? <FlowMark size={10} /> : <i className="hollow" />}
            <span>
              Waiting on <NameOf id={q.person} />: {q.title}
            </span>
          </Go>
        </li>
      ))}
    </ul>
  );
}
