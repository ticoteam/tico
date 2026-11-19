import type { ComponentProps, ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import './ConfirmCard.css';

type IconName = ComponentProps<typeof Icon>['name'];

export type ConfirmStep = {
  icon: IconName;
  /** What happens, to whom, how much. */
  title: string;
  detail?: string;
};

export type ConfirmCardProps = {
  /** The Dot waiting on you: `<Dot state="wait" size={12} />` (the kit does not import the Dot). */
  mark: ReactNode;
  /** Tico's question, such as "Send these 3 emails?". */
  question: string;
  /** Each outcome on its own line. */
  steps: readonly ConfirmStep[];
  /** What cannot be taken back. */
  note?: string;
  /** The filled button's words name the act: "Send 3 emails", "Pay $1,200". */
  act: string;
  onAct: () => void;
  onNotNow: () => void;
  busy?: boolean;
};

/** Tico asks first: money, sending and deleting wait for a yes. */
export function ConfirmCard({ mark, question, steps, note, act, onAct, onNotNow, busy = false }: ConfirmCardProps) {
  return (
    <section className="ask" aria-label="Tico asks first">
      <div className="ask-head t-label-2">
        {mark}
        <span>{question}</span>
      </div>
      <ul className="ask-steps">
        {steps.map((s, i) => (
          <li key={i}>
            <Icon name={s.icon} />
            <div>
              <b>{s.title}</b>
              {s.detail && <span>{s.detail}</span>}
            </div>
          </li>
        ))}
      </ul>
      {note && <p className="ask-note">{note}</p>}
      <div className="ask-row">
        <Button variant="primary" busy={busy} onClick={() => onAct()}>
          {act}
        </Button>
        <Button disabled={busy} onClick={() => onNotNow()}>
          Not now
        </Button>
      </div>
    </section>
  );
}
