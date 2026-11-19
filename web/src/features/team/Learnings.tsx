import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useAction } from '@/actions';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Markdown } from '@/kit/Markdown';
import { Section } from '@/pagekinds';
import { decisionsOf, dreamCards, dreamReply, type Decision } from './model';
import { Fold, Foot } from './parts';
import { dreamAgain, dreamOpen, dreamQuery, memoryQuery, openTasksQuery, type Dream } from './queries';

const DECISIONS = 'memory/decisions.md';
const DECISION_ROWS = 20;

/** Its decisions log (`memory/decisions.md`), newest first (ui bot-learnings: the Learnings panel's Decisions). */
export function DecisionsSection({ slug }: { slug: string }) {
  const memory = useQuery(memoryQuery(slug));
  const rows = decisionsOf(memory.data?.documents?.[DECISIONS] ?? '');
  if (!rows.length) return null;
  return (
    <Section name="Decisions" count={rows.length}>
      {rows.slice(0, DECISION_ROWS).map((d) => (
        <DecisionRow key={d.key} d={d} />
      ))}
      {rows.length > DECISION_ROWS && (
        <Fold label="Earlier" text={`${rows.length - DECISION_ROWS} more`}>
          {rows.slice(DECISION_ROWS).map((d) => (
            <DecisionRow key={d.key} d={d} />
          ))}
        </Fold>
      )}
    </Section>
  );
}

function DecisionRow({ d }: { d: Decision }) {
  return (
    <div className="row rule" title={d.text}>
      <span className="rule-how">
        <Icon name="check" />
      </span>
      <span className="row-title">{d.title}</span>
      <span className="rule-marks">{d.on && <Ago at={d.on} className="row-age" />}</span>
      <span />
    </div>
  );
}

const LINES: Record<Dream['state'], string> = { starting: 'Falling asleep', dreaming: 'Dreaming', asking: 'A few questions', done: 'Awake', stopped: 'Awake' };

/**
 * Its dream while it runs, and once more when it ends (ui bot-learnings dream mode): what it is doing, its questions to
 * answer, Stop; then its summary and Wake up. Started from the page's More menu. Its task moving on the stream reads it
 * again, so nothing polls.
 */
export function DreamSection({ slug, actorId }: { slug: string; actorId: string }) {
  const dream = useQuery(dreamQuery(slug));
  const d = dream.data;
  const open = useQuery({ ...openTasksQuery(actorId), enabled: dreamOpen(d) && !!d?.task });
  const t = d?.task ? open.data?.tasks.find((x) => x.id === d.task) : undefined;
  const signal = t ? `${t.updated_at ?? ''}|${t.turn?.state ?? ''}` : 'gone';
  const seen = useRef(signal);
  useEffect(() => {
    if (seen.current === signal) return;
    seen.current = signal;
    dreamAgain(slug);
  }, [signal, slug]);

  const stop = useAction('agents.stopDream');
  const wake = useAction('agents.wakeUp');
  if (!d) return null;
  const live = dreamOpen(d);
  const ask = d.state === 'asking' ? d.questions : null;
  return (
    <Section name="Dream" summary={LINES[d.state]} target={`dream:${slug}`}>
      {d.auto && d.reason && <p className="now-step">{d.reason}</p>}
      {d.state === 'dreaming' && (
        <p className="now-step">
          {d.doing && `${d.doing} · `}
          <Ago at={d.started_at} />
          <span className="typing">
            <i />
            <i />
            <i />
          </span>
        </p>
      )}
      {ask &&
        (ask.mine ? (
          <Questions key={ask.task_id} slug={slug} task={ask.task_id} text={ask.text} />
        ) : (
          <div className="dream-text">
            <Markdown text={ask.text} compact />
          </div>
        ))}
      {!live && d.summary && (
        <div className="dream-text">
          <Markdown text={d.summary} compact />
        </div>
      )}
      {d.task && (
        <Foot>
          <Link className="verb" href={`/task/${encodeURIComponent(d.task)}`}>
            Task
          </Link>
          {live ? (
            <button className="verb" type="button" disabled={stop.pending} onClick={() => void stop.run({ slug, task: d.task })}>
              Stop
            </button>
          ) : (
            <button className="verb" type="button" onClick={() => void wake.run({ slug, task: d.task })}>
              Wake up
            </button>
          )}
        </Foot>
      )}
    </Section>
  );
}

/** Its questions, one box each, sent as one numbered reply on the dream's task. */
function Questions({ slug, task, text }: { slug: string; task: string; text: string }) {
  const send = useAction('agents.answerDream');
  const cards = dreamCards(text);
  const [answers, setAnswers] = useState<string[]>(() => cards.map(() => ''));
  const some = answers.some((a) => a.trim());
  return (
    <form
      className="dream-text"
      onSubmit={(e) => {
        e.preventDefault();
        if (some) void send.run({ slug, task, text: dreamReply(cards, answers) });
      }}
    >
      {cards.map((c, i) => (
        <div key={i} className="dream-q">
          <Markdown text={c.n ? `${c.n}. ${c.text}` : c.text} compact />
          <Field
            label={cards.length > 1 ? `Answer ${c.n}` : 'Your answer'}
            labelHidden
            area
            value={answers[i] ?? ''}
            onValueChange={(v) => setAnswers((cur) => cur.map((a, j) => (j === i ? v : a)))}
          />
        </div>
      ))}
      <div className="modal-row">
        <Button type="submit" variant="primary" busy={send.pending} disabled={!some || send.pending}>
          Send
        </Button>
      </div>
    </form>
  );
}
