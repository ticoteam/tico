import type { ReactNode } from 'react';
import { Fault } from '@/kit/Fault';
import { Icon, type IconName } from '@/kit/Icon';
import './DidLine.css';

export type DidLineProps = {
  /** What was done, past tense, a few words ("Opened Finance"). */
  text: string;
  /** Its mark: the done tick unless given. */
  icon?: IconName;
  /** Who did it, in place of the mark: a 22 `<Avatar>` (system/shell.js did(text, icon, by)). */
  face?: ReactNode;
  /** It could not be done: the fault mark, the words in ink. */
  fault?: boolean;
};

/** One quiet line for each thing done (shell.css `.did`). No Undo in the first build. */
export function DidLine({ text, icon = 'check', face, fault = false }: DidLineProps) {
  return (
    <div className={fault ? 'did is-fault' : 'did'}>
      {face && !fault ? face : <span className="did-ico">{fault ? <Fault label="Could not do it" /> : <Icon name={icon} />}</span>}
      <span>{text}</span>
    </div>
  );
}
