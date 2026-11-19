import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { FlowMark } from '@/kit/FlowMark';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';

/* The pieces Routines, Rules and Listening share, ported from pages/3-company/rules.js: a word, a quiet verb, the rule
   row and its marks, a face for any owner id, and the press that says why a refusal happened. */

/** Run an action from a click; a refusal toasts its reason ("code: reason" reads as the reason). */
export async function press(id: string, input: unknown): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) {
    const why = r.error?.includes(': ') ? r.error.slice(r.error.indexOf(': ') + 2).trim() : '';
    toast({ text: why || "Couldn't save", kind: 'failed' });
  }
  return r.ok;
}

/** Something went wrong, said in ink with a mark: no red. */
export const Word = ({ children, icon = 'alert' }: { children: string; icon?: IconName }) => (
  <span className="word">
    <Icon name={icon} />
    {children}
  </span>
);

/** A quiet verb at the end of a row. */
export const Verb = ({ children, onClick, target }: { children: string; onClick: () => void; target?: string }) => (
  <button className="verb" type="button" onClick={onClick} data-target={target}>
    {children}
  </button>
);

/** A switch whose words only a screen reader hears (a panel or page head). */
export const HeadSwitch = ({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (on: boolean) => void; disabled?: boolean }) => (
  <Switch label={label} labelHidden checked={checked} onCheckedChange={onChange} disabled={disabled} />
);

/** Any owner id the server sends (`bot:<id>`, `bot:<slug>`, `person:<id>`, `human:<id>`, a bare id) as the directory's key. */
export const actorKey = (id: string | null | undefined): string | undefined => (id ? id.replace(/^person:/, 'human:') : undefined);

/** The person or agent behind an owner id. */
const useOwner = (id: string | null | undefined) => useActor(actorKey(id));

/** Their face, 22 unless asked; a gap the same size when the directory does not know them. */
export function Face({ id, size = 22 }: { id: string | null | undefined; size?: 16 | 22 | 28 }) {
  const a = useOwner(id);
  if (!a) return <span className="av-gap" />;
  return <Avatar kind={a.kind} name={a.name} hue={a.hue} size={size} />;
}

/** Their name, or '' when the directory does not know them. */
export function useName(id: string | null | undefined): string {
  const a = useOwner(id);
  return a?.name ?? '';
}

// ---------------------------------------------------------------- the rule row

const X = ({ d }: { d: string[] }) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {d.map((p) => (
      <path key={p} d={p} />
    ))}
  </svg>
);
// rules.js ICO: the marks the shared icon set does not have
const ICO = {
  model: ['M12 3.2l8.8 8.8-8.8 8.8L3.2 12z', 'M8.8 12.2l2.3 2.3 4.2-4.6'],
  bot: ['M7 7h10a3.5 3.5 0 013.5 3.5V16a3.5 3.5 0 01-3.5 3.5H7A3.5 3.5 0 013.5 16v-5.5A3.5 3.5 0 017 7z', 'M9.5 12v2M14.5 12v2M12 3.5V7'],
  person: ['M12 5a3.5 3.5 0 110 7 3.5 3.5 0 010-7z', 'M5 19.5c.8-3.3 3.5-5 7-5s6.2 1.7 7 5'],
  coin: [
    'M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17z',
    'M14.7 9.6c-.4-.9-1.4-1.4-2.7-1.4-1.5 0-2.6.7-2.6 1.9 0 2.6 5.4 1.2 5.4 3.9 0 1.2-1.1 2-2.8 2-1.3 0-2.4-.5-2.9-1.5M12 6.5v1.7M12 16v1.6',
  ],
  wake: ['M13 3.5L5.5 13.5H11l-1 7 7.5-10H12z'],
  loop: ['M19.5 12a7.5 7.5 0 01-13.2 4.9M4.5 12a7.5 7.5 0 0113.2-4.9', 'M18 3.5v4h-4M6 20.5v-4h4'],
  out: ['M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17z', 'M3.5 12h17M12 3.5c2.4 2.5 3.5 5.3 3.5 8.5s-1.1 6-3.5 8.5c-2.4-2.5-3.5-5.3-3.5-8.5s1.1-6 3.5-8.5z'],
};

export type How = 'fixed' | 'model' | 'bot' | 'person' | 'link' | 'date' | 'wake' | 'auto' | 'out' | 'set';
const HOW: Record<Exclude<How, 'person'>, [ReactNode, string]> = {
  fixed: [<Icon key="i" name="check" />, 'A fixed check'],
  model: [<X key="i" d={ICO.model} />, 'Decision model'],
  bot: [<X key="i" d={ICO.bot} />, 'An agent reads it'],
  link: [<Icon key="i" name="link" />, 'Opens tasks in another pipeline'],
  date: [<Icon key="i" name="clock" />, 'On a date'],
  wake: [<X key="i" d={ICO.wake} />, 'Wakes an agent'],
  auto: [<X key="i" d={ICO.loop} />, 'Runs a routine'],
  out: [<X key="i" d={ICO.out} />, 'From outside'],
  set: [<Icon key="i" name="gear" />, 'A setting'],
};

export const Coin = () => (
  <span title="Uses tokens">
    <X d={ICO.coin} />
  </span>
);

function RuleHow({ how, by }: { how: How; by?: string | null }) {
  const name = useName(by);
  if (how === 'person') {
    return (
      <span className="rule-how" title={name ? `${name} decides` : 'A person decides'}>
        {by ? <Face id={by} size={16} /> : <X d={ICO.person} />}
      </span>
    );
  }
  const [icon, title] = HOW[how];
  return (
    <span className="rule-how" title={title}>
      {icon}
    </span>
  );
}

/** The sentence: When, check, Then, a faint chevron between. The chevron keeps to the word after it. */
function Sentence({ parts }: { parts: readonly string[] }) {
  return (
    <>
      {parts.map((p, i) => {
        if (!i) return <span key={i}>{p}</span>;
        const k = p.indexOf(' ');
        return (
          <span key={i}>
            <span className="rule-nb">
              <i className="rule-sep">
                <Icon name="chevron" />
              </i>
              {k < 0 ? p : p.slice(0, k)}
            </span>
            {k < 0 ? '' : p.slice(k)}
          </span>
        );
      })}
    </>
  );
}

export interface RuleRowProps {
  how: How;
  parts: readonly string[];
  /** The person who decides, for `how: 'person'`. */
  by?: string | null;
  /** Where the row opens its editor (`?panel=`); no address, no link. */
  to?: string;
  search?: Record<string, string>;
  selected?: boolean;
  off?: boolean;
  /** Built in: nothing to edit, no "⋯". */
  fixed?: boolean;
  cost?: boolean;
  lock?: boolean;
  word?: string;
  holds?: number;
  /** The cells every row on the page carries, so they line up. */
  cols?: { src?: boolean; who?: boolean; at?: boolean };
  src?: string;
  who?: string | null;
  at?: ReactNode;
  verb?: { label: string; onClick: () => void };
  /** The "⋯" menu's items. */
  more?: ReactNode;
  target?: string;
}

/** One rule on one line (rules.js ruleRow): how it is checked, the sentence, marks only when they say something. */
export function RuleRow(r: RuleRowProps) {
  const cols = r.cols ?? {};
  const cells = !!(r.word || r.verb || r.holds || (cols.src && r.src) || (cols.who && r.who) || (cols.at && r.at));
  const cls = ['row', 'rule', r.off && 'is-off', r.fixed && 'is-fixed', cells && 'has-cells', r.selected && 'is-selected', r.to && 'is-link'].filter(Boolean).join(' ');
  const title = r.parts.join(', ');
  return (
    <div className={cls} title={title} data-target={r.target}>
      <RuleHow how={r.how} by={r.by} />
      {r.to ? (
        <Link className="row-title" to={r.to} search={r.search ?? {}} aria-current={r.selected ? 'true' : undefined}>
          <Sentence parts={r.parts} />
        </Link>
      ) : (
        <span className="row-title">
          <Sentence parts={r.parts} />
        </span>
      )}
      <span className="rule-marks">
        {r.word && <Word>{r.word}</Word>}
        {!!r.holds && (
          <span className="rule-hold" title={`Holds ${r.holds} ${r.holds === 1 ? 'task' : 'tasks'}`}>
            <FlowMark />
          </span>
        )}
        {r.cost && <Coin />}
        {r.off && (
          <span title="Paused">
            <Icon name="pause" />
          </span>
        )}
        {r.lock && (
          <span title="Built in">
            <Icon name="lock" />
          </span>
        )}
        {cols.src && <span className="rule-src">{r.src ?? ''}</span>}
        {cols.who && (r.who ? <Face id={r.who} /> : <span className="rule-gap" />)}
        {cols.at && <span className="row-age rule-at">{r.at ?? ''}</span>}
        {r.verb && <Verb onClick={r.verb.onClick}>{r.verb.label}</Verb>}
      </span>
      {r.fixed || !r.more ? <span /> : <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>{r.more}</Menu>}
    </div>
  );
}

// ---------------------------------------------------------------- remove, asked first

/** "Remove X?": the one confirm every remove goes through. */
export function useConfirmRemove() {
  const [ask, setAsk] = useState<{ what: string; go: () => void } | null>(null);
  const node = (
    <Dialog
      open={!!ask}
      onOpenChange={(o) => !o && setAsk(null)}
      title={ask ? `Remove ${ask.what}?` : ''}
      alert
      actions={
        <>
          <Button variant="quiet" onClick={() => setAsk(null)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              ask?.go();
              setAsk(null);
            }}
          >
            Remove
          </Button>
        </>
      }
    />
  );
  return { confirm: (what: string, go: () => void) => setAsk({ what, go }), node };
}

/** "⋯" items for a row or a panel head. */
export const RemoveItem = ({ onSelect, disabled }: { onSelect: () => void; disabled?: boolean }) => (
  <MenuItem label="Remove" icon="archive" onSelect={onSelect} disabled={disabled} />
);
