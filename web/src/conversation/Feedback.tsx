import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { refusal, useAction, type Result } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Link } from '@/kit/Link';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { reasonsQuery, ruleDraftQuery, type Feedback, type Rating } from './queries';
import type { ChatMessage } from './types';

/** A message the person may rate: an agent's or a teammate's, not their own, not Tico's, not the system's. */
export function canRate(m: Pick<ChatMessage, 'sender_id' | 'pending' | 'deleted'>, me: string | undefined, tico: boolean): boolean {
  const a = m.sender_id ?? '';
  if (m.pending || m.deleted || !a || a === me || a === 'keeper') return false;
  if (a.startsWith('bot:')) return !tico;
  return a.startsWith('human:');
}

const failed = (r: Result) => toast(refusal(r, "Couldn't save"));

export interface RateControls {
  rating: Rating | null;
  good: () => Promise<void>;
  notGood: () => Promise<void>;
  /** The dialogs Not good opens; drawn beside the message, so they open from the long-press sheet too. */
  sheets: ReactNode;
}

/**
 * Good and Not good on a message. Not good is kept at once, then asks what was wrong; on an agent's work the reason
 * can go to the agent or become a rule. Pressing a chosen one again: Good takes it back, Not good shows what was said.
 */
function useRate(chat: string, m: ChatMessage, feedback: Feedback | null): RateControls {
  const rate = useAction('conversation.rate');
  const unrate = useAction('conversation.unrate');
  const [sheet, setSheet] = useState<'reasons' | 'rule' | 'view' | null>(null);
  const agent = (m.sender_id ?? '').startsWith('bot:');
  const rating = feedback?.rating ?? null;

  const give = async (r: Rating, extra: { reason?: string; tellAgent?: boolean } = {}) => {
    const out = await rate.run({ chat, message: m.id, rating: r, ...extra });
    if (!out.ok) failed(out);
    return out.ok;
  };
  const take = async (id: string) => {
    const r = await unrate.run({ chat, feedback: id, message: m.id });
    if (!r.ok) failed(r);
  };
  const good = async () => {
    if (rating === 'up' && feedback) await take(feedback.id);
    else await give('up');
  };
  const notGood = async () => {
    if (rating === 'down') return setSheet('view');
    if (await give('down')) setSheet('reasons');
  };

  const sheets = (
    <>
      {sheet === 'reasons' && (
        <Reasons
          agent={agent}
          said={feedback?.reason ?? ''}
          onSave={(reason) => give('down', { reason })}
          onTell={(reason) => give('down', { reason, tellAgent: true }).then((ok) => ok && toast({ text: 'Sent to the agent' }))}
          onRule={async (reason) => {
            if (await give('down', { reason })) setSheet('rule');
          }}
          onClose={() => setSheet(null)}
        />
      )}
      {sheet === 'rule' && feedback && <MakeRule chat={chat} feedback={feedback} onClose={() => setSheet(null)} />}
      {sheet === 'view' && feedback && (
        <NotGood
          feedback={feedback}
          onEdit={() => setSheet('reasons')}
          onRemove={async () => {
            setSheet(null);
            await take(feedback.id);
          }}
          onClose={() => setSheet(null)}
        />
      )}
    </>
  );
  return { rating, good, notGood, sheets };
}

/** A message the person may rate, drawn with its rating controls (its Good and Not good row, its long-press sheet). */
export function Rated({ chat, m, feedback, children }: { chat: string; m: ChatMessage; feedback: Feedback | null; children: (rate: RateControls) => ReactNode }) {
  return children(useRate(chat, m, feedback));
}

/** Good and Not good under a message (on a phone, a chosen one only: the long press has both). */
export function Rate({ rate }: { rate: RateControls }) {
  return (
    <div className={rate.rating ? 'rate is-rated' : 'rate'} role="group" aria-label="Rate this message">
      <button type="button" className="fold" aria-pressed={rate.rating === 'up'} onClick={() => void rate.good()}>
        Good
      </button>
      <button type="button" className="fold" aria-pressed={rate.rating === 'down'} onClick={() => void rate.notGood()}>
        Not good
      </button>
    </div>
  );
}

/** What was wrong: the person's recent reasons as one-tap chips, or their own words. */
function Reasons({
  agent,
  said,
  onSave,
  onTell,
  onRule,
  onClose,
}: {
  agent: boolean;
  said: string;
  onSave: (reason: string) => Promise<unknown>;
  onTell: (reason: string) => Promise<unknown>;
  onRule: (reason: string) => Promise<unknown>;
  onClose: () => void;
}) {
  const { data: recent = [] } = useQuery(reasonsQuery);
  const [reason, setReason] = useState(said);
  const again = recent.find((r) => r.text.trim().toLowerCase() === reason.trim().toLowerCase() && r.count >= 2);
  const changed = reason.trim() !== said.trim();
  const then = (fn: (r: string) => Promise<unknown>) => () => {
    onClose();
    void fn(reason.trim());
  };
  // Closing keeps what was typed: the rating is already saved, the reason goes with it.
  const close = () => {
    onClose();
    if (changed) void onSave(reason.trim());
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && close()}
      title="Not good"
      actions={
        agent ? (
          <>
            <Button onClick={then(onTell)}>Tell agent</Button>
            <Button variant={again ? 'primary' : 'plain'} onClick={then(onRule)}>
              Make rule
            </Button>
            {!again && (
              <Button variant="primary" onClick={close}>
                Save
              </Button>
            )}
          </>
        ) : (
          <Button variant="primary" onClick={close}>
            Save
          </Button>
        )
      }
    >
      {recent.length > 0 && (
        <div className="rate-chips">
          {recent.map((r) => (
            <button key={r.text} type="button" className="rate-chip" aria-pressed={reason === r.text} onClick={() => setReason(r.text)}>
              {r.text}
            </button>
          ))}
        </div>
      )}
      <Field label="What was wrong?" area value={reason} onValueChange={setReason} autoFocus />
      {again && <p className="rate-note">You've said this before.</p>}
    </Dialog>
  );
}

/** A rule from a Not good: drafted from the reason and the message, editable, for this agent or every agent. */
function MakeRule({ chat, feedback, onClose }: { chat: string; feedback: Feedback; onClose: () => void }) {
  const make = useAction('conversation.makeRule');
  const draft = useQuery({ ...ruleDraftQuery(feedback.id), placeholderData: { text: feedback.reason, drafted: false } });
  const [text, setText] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const words = text ?? draft.data?.text ?? '';
  const save = async () => {
    const r = await make.run({ chat, feedback: feedback.id, text: words, allAgents: all });
    if (!r.ok) return failed(r);
    onClose();
    toast({ text: r.did ?? 'Rule added' });
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Make a rule"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!words.trim()} busy={make.pending} onClick={() => void save()}>
            Save rule
          </Button>
        </>
      }
    >
      <Field label="Rule" labelHidden area value={words} onValueChange={setText} disabled={draft.isPlaceholderData && text == null} />
      <Switch label="All agents" checked={all} onCheckedChange={setAll} />
    </Dialog>
  );
}

/** What the person said about a message they marked not good, and what came of it. */
function NotGood({ feedback: f, onEdit, onRemove, onClose }: { feedback: Feedback; onEdit: () => void; onRemove: () => void; onClose: () => void }) {
  const outcome =
    f.outcome === 'rule' && f.org_rule_id ? (
      <Link className="lnk" href={`/company/rules?panel=org:${encodeURIComponent(f.org_rule_id)}`}>
        {f.rule_status === 'suggested' ? 'Rule suggested' : 'Rule'}
      </Link>
    ) : f.note_status === 'settled' || f.note_status === 'carried' ? (
      'Agent has read it'
    ) : f.note_status === 'waiting' ? (
      'Sent to the agent'
    ) : null;
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Not good"
      description={f.reason.trim() || 'No reason'}
      actions={
        <>
          <Button onClick={onRemove}>Remove</Button>
          <Button variant="primary" onClick={onEdit}>
            Edit
          </Button>
        </>
      }
    >
      {outcome && <p className="rate-note">{outcome}</p>}
    </Dialog>
  );
}
