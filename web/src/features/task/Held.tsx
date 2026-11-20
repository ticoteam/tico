import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { FlowMark } from '@/kit/FlowMark';
import { toast } from '@/kit/Toast';
import { answered } from './answered';
import { holdQuery, othersHeld, REVIEW_LABEL, taskHoldsQuery, type Hold, type HoldRule } from './holds';
import { failedToast, reasonOf, tabAddress } from './model';
import { Face, Go } from './parts';
import { waitingQuery, type TaskData } from './queries';

/* A held message's decision (plan/approvals.html: a send outside is a real approval, in the approval box). On a rule's
   review task ("Held by: <rule>") the box shows what was held, oldest first, one at a time: who it was for, the text
   with the part that breaks the rule marked, and the rule with its Why. Send anyway is the one filled button and is
   drawn only over the whole text the person can read; Keep blocked is always there while it waits. The server says
   who may decide, and a refusal is said in the box. */

/** Whether the task is a rule's review: its holds wait on whoever reviews the rule. */
export const isRuleReview = (t: Pick<TaskData, 'labels'>): boolean => !!t.labels?.includes(REVIEW_LABEL);

/** What was held, as a noun (org_rule_check.OPERATIONS). */
const NOUN: Record<string, string> = {
  mail: 'Email',
  letter: 'Letter',
  comment: 'Comment',
  comment_edit: 'Comment edit',
  threads_edit: 'Message edit',
  task_create: 'New task',
  task_update: 'Task change',
  task_owner: 'Assignment',
  task_ask: 'Question',
  question: 'Question',
  chat_question: 'Question',
  bulk: 'Task changes',
  automation: 'Automation step',
  update: 'Update',
  note: 'Note',
};

/** "Email to Dana Ruiz", "Message to Ana, Ben +2": what was held and who it was for. */
export function holdName(h: Pick<Hold, 'operation' | 'to'>): string {
  const noun = NOUN[h.operation] ?? 'Message';
  const to = h.to ?? [];
  if (!to.length) return noun;
  return `${noun} to ${to.slice(0, 2).join(', ')}${to.length > 2 ? ` +${to.length - 2}` : ''}`;
}

/** The rule it is held on: the first that is not Warn (the server lists it first; an older hold may not). */
const ruleOf = (h: Hold): HoldRule | undefined => h.rules.find((r) => r.if_broken !== 'warn') ?? h.rules[0];

/** An ask held before its copy dropped its labels reads without them too. */
const ASKS = new Set(['question', 'chat_question', 'task_ask']);
const textOf = (h: Hold): string => (ASKS.has(h.operation) ? (h.text ?? '').replace(/^(?:Ask|Shows): /gm, '') : (h.text ?? ''));

/** The part of the text a rule says breaks it, '' when none does. */
const partOf = (h: Hold, text: string): string => h.rules.map((r) => r.part ?? '').find((p) => !!p && text.includes(p)) ?? '';

/** The rule's own page: an outside rule under its tab. */
const ruleAddress = (r: HoldRule) => ({ to: '/company/rules', search: { ...(r.kind === 'external' && { tab: 'outside' }), panel: `org:${r.id}` } });

/** The review's holds that still wait, oldest first: the first is decided now. Nothing while none waits on this task. */
export function HeldDecision({ t, flow, dock = false }: { t: TaskData; flow: boolean; dock?: boolean }) {
  const list = useQuery(taskHoldsQuery(t.id));
  const first = (list.data ?? []).find((h) => h.state === 'held');
  if (!first) return null;
  const box = <HeldBox key={first.id} t={t} summary={first} flow={flow} />;
  return dock ? (
    <div className="dock" data-dock>
      {box}
    </div>
  ) : (
    box
  );
}

function HeldBox({ t, summary, flow }: { t: TaskData; summary: Hold; flow: boolean }) {
  const opened = useQuery(holdQuery(summary.id));
  const keep = useAction('task.keepHold');
  const send = useAction('task.sendHold');
  const waiting = useQuery({ ...waitingQuery, enabled: flow });
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const busy = keep.pending || send.pending;

  // A refusal that lands once the box has gone (the person moved on) is said in a toast: never silent.
  const here = useRef(true);
  useEffect(() => {
    here.current = true;
    return () => {
      here.current = false;
    };
  }, []);

  const h = opened.data ?? summary;
  const text = textOf(h);
  const part = partOf(h, text);
  const rule = ruleOf(h);
  // Send anyway only over what the person read: the whole text loaded, none of it cut, and a nonce for them.
  const whole = !!opened.data && !!text && !h.text_cut;
  const sendable = whole && h.can_send && !!h.nonce;

  const nextUp = () => {
    const rows = waiting.data ?? [];
    const i = rows.findIndex((w) => w.id === t.id);
    return (i >= 0 ? (rows[i + 1] ?? rows[i - 1]) : rows[0]) ?? null;
  };

  const decide = async (how: 'keep' | 'send') => {
    setError('');
    const next = nextUp();
    const more = othersHeld(t.id, h.id);
    const res = how === 'send' ? await send.run({ task: t.id, hold: h.id, nonce: h.nonce }) : await keep.run({ task: t.id, hold: h.id });
    if (!res.ok) {
      if (!here.current) toast(failedToast(res));
      return setError(reasonOf(res.error));
    }
    toast({ text: how === 'send' ? 'Sent anyway' : 'Kept blocked', name: holdName(h) });
    // Another hold of the rule waits: it takes this one's place in the box. Else the review is done.
    if (more) return;
    if (!flow) return void (here.current && answered());
    const to = next ? tabAddress(next, undefined, true) : { to: '/flow', search: {} };
    void navigate({ to: to.to as never, search: to.search as never });
  };

  return (
    <section className="answer dcx is-hold" aria-label="The decision" data-target={`task-answer:${t.id}`}>
      <div className="dcx-top">
        <FlowMark />
        <b className="an-name">{holdName(h)}</b>
        <span className="grow" />
        <Face id={h.author} />
      </div>
      <div className="hold-body">
        {opened.isError ? (
          <p className="an-err" role="alert">
            {opened.error.message || 'It did not load'}
            <Button variant="quiet" onClick={() => opened.refetch()}>
              Try again
            </Button>
          </p>
        ) : opened.data ? (
          text && (
            <blockquote className="hold-text" aria-label="What was held">
              {part ? (
                <>
                  {text.slice(0, text.indexOf(part))}
                  <mark>{part}</mark>
                  {text.slice(text.indexOf(part) + part.length)}
                </>
              ) : (
                text
              )}
              {h.text_cut ? '…' : null}
            </blockquote>
          )
        ) : (
          <div className="hold-text is-bones" aria-busy="true" />
        )}
        {!!h.text_cut && <p className="hold-note">Too long to show in full</p>}
        {h.why && <p className="hold-why">{h.why}</p>}
        {rule && (
          <p className="hold-rule">
            <span>{rule.text}</span>
            <Go to={ruleAddress(rule).to} search={ruleAddress(rule).search} className="hold-edit">
              Edit rule
            </Go>
            {rule.description && <span className="hold-reason">{rule.description}</span>}
          </p>
        )}
      </div>
      <div className="an-row">
        <span className="grow" />
        <Button busy={keep.pending} disabled={busy} onClick={() => decide('keep')}>
          Keep blocked
        </Button>
        {sendable && (
          <Button variant="primary" busy={send.pending} disabled={busy} onClick={() => decide('send')}>
            Send anyway
          </Button>
        )}
      </div>
      {error && (
        <p className="an-err" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
