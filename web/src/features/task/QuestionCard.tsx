import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiError } from '@/api/client';
import { Button } from '@/kit/Button';
import { AskCard } from './AskCard';
import { holdQuery } from './holds';
import { answerQuestion } from './queries';
import { questionLine } from './waits';

/* A ❓ an agent put to the person on a task, answered in place (backend/question_marks.py): the question, up to three
   answers the agent offers as buttons, and a reply box. A tap sends that answer as the person's reply on the task's
   chat, to the agent that asked; the server sends it only while the ❓ is open, so a second tap sends nothing. A ❓
   with no answers offered (a free-text one, or one from before) is the same card with the reply box alone. A reply a
   company rule held is not sent: the card stays, marked Held, with the reason, until its reviewer decides; kept
   blocked, the card's answers and reply box are back. A card drawn again (the Chat tab left and come back to) keeps its
   hold; after a reload it is open again, and Send anyway of the held reply then sends nothing once it was answered. */

export interface QuestionCardProps {
  /** The ❓'s id as a wait or a Needs-you item names it (`qmark:<id>`). */
  id: string;
  /** The task whose chat it is on. */
  task: string;
  /** The agent's line: the question. */
  line: string;
  /** Up to three answers, the agent's pick first. */
  choices?: readonly string[];
  /** The agent that asks. */
  who?: string | null;
}

type State = { at: 'open' | 'sending' | 'sent' } | { at: 'held'; hold: string; why: string };

/** The held reply of each ❓ answered from this page, by the ❓'s id, so a card drawn again still follows its hold. */
const heldReplies = new Map<string, { hold: string; why: string }>();

/** How often a held reply's hold is read again while it waits on its reviewer: no push carries its decision yet, so
    this is the five-minute backstop the app polls at, never faster. */
export const HELD_EVERY_MS = 5 * 60_000;

export function QuestionCard({ id, task, line, choices = [], who }: QuestionCardProps) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [state, setNow] = useState<State>(() => {
    const kept = heldReplies.get(id);
    return kept ? { at: 'held', ...kept } : { at: 'open' };
  });
  const setState = (next: State) => {
    if (next.at === 'held') heldReplies.set(id, { hold: next.hold, why: next.why });
    else heldReplies.delete(id);
    setNow(next);
  };
  const busy = state.at === 'sending';
  const send = async (reply: string) => {
    if (busy) return;
    setError('');
    setState({ at: 'sending' });
    try {
      const held = await answerQuestion(task, id, reply);
      setState(held ? { at: 'held', hold: held.id, why: held.reason } : { at: 'sent' });
    } catch (e) {
      setState({ at: 'open' });
      setError(e instanceof ApiError ? e.message : 'Not sent. Try again.');
    }
  };
  const held = state.at === 'held' ? state : null;
  const hold = useQuery({ ...holdQuery(held?.hold ?? ''), enabled: !!held?.hold, refetchInterval: HELD_EVERY_MS }).data;
  // The reviewer decided: sent, the reply went (the ❓ clears and the card goes with it); kept, it was never sent.
  if (held && hold && hold.id === held.hold && hold.state !== 'held') {
    if (hold.state === 'sent') setState({ at: 'sent' });
    else {
      setState({ at: 'open' });
      setError('Kept blocked: not sent');
    }
  }
  if (state.at === 'sent') return null;
  const name = questionLine(line);
  if (held) {
    return (
      <AskCard name={name} note="Held" label="Question" who={who} target={`question:${id}`} whole>
        <p className="askcard-what">{held.why}</p>
      </AskCard>
    );
  }
  // The agent's pick is the filled button, last in the row as on every card.
  const [pick, ...rest] = choices;
  const typed = text.trim();
  return (
    <AskCard
      name={name}
      label="Question"
      who={who}
      target={`question:${id}`}
      whole
      error={error}
      actions={
        <>
          <input className="askcard-reply" aria-label="Reply" placeholder="Reply…" maxLength={4000} value={text} disabled={busy} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && typed && void send(typed)} />
          {typed ? (
            <Button variant="primary" disabled={busy} onClick={() => send(typed)}>
              Send
            </Button>
          ) : (
            <>
              {rest.map((c) => (
                <Button key={c} className="askcard-choice" disabled={busy} onClick={() => send(c)}>
                  {c}
                </Button>
              ))}
              {pick && (
                <Button variant="primary" className="askcard-choice" disabled={busy} onClick={() => send(pick)}>
                  {pick}
                </Button>
              )}
            </>
          )}
        </>
      }
    />
  );
}
