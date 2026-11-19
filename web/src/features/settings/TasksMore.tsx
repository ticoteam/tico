import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions/run';
import { refusal } from '@/actions';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Foot, Line, SwitchLine, Verb, act } from './parts';
import { askQuery, type AskRules, type ReplayRow } from './queries';

/* Settings › Tasks, the parts the old app had beside the roadmap labels: what agents may put on a person (their own
   rules, or the team default for an owner or admin), the lines they want to be asked about, and a replay of one past
   day. */

/** One short line typed into a small dialog: a criterion. */
function AddLine({ label, title, field, onAdd }: { label: string; title: string; field: string; onAdd: (line: string) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    const ok = await onAdd(text.trim());
    setBusy(false);
    if (!ok) return;
    setOpen(false);
    setText('');
  };
  return (
    <>
      <IconButton icon="plus" label={label} size="sm" quiet onClick={() => setOpen(true)} />
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={title}
        actions={
          <>
            <Button variant="quiet" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} disabled={!text.trim()} onClick={() => void add()}>
              Add
            </Button>
          </>
        }
      >
        <Field label={field} value={text} onValueChange={setText} autoFocus autoComplete="off" />
      </Dialog>
    </>
  );
}

/** A whole number typed in, saved with its own Save (a number saves on purpose, not per keystroke). */
function NumberLine({ label, value, min, max, target, onSave }: { label: string; value: number; min: number; max: number; target?: string; onSave: (n: number) => void }) {
  const [text, setText] = useState(String(value));
  const n = Number(text);
  const bad = !/^\d+$/.test(text.trim()) || n < min || n > max;
  return (
    <Line label={label} target={target}>
      <Field label={label} labelHidden type="number" wide={false} value={text} onValueChange={setText} error={bad ? `${min} to ${max}` : undefined} />
      {!bad && n !== value && <Verb onClick={() => onSave(n)}>Save</Verb>}
    </Line>
  );
}

const WOULD: Record<string, string> = { through: 'Reaches you', fyi: 'Refused', sent_back: 'Refused', merged: 'Merged', failed_open: 'Reaches you' };
const KEPT_OFF = ['sent_back', 'fyi', 'merged'];
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** What On would have done with one past day of agents' asks. Nothing on any plate moves. */
function Replay() {
  const [day, setDay] = useState<Date>(() => new Date());
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<ReplayRow[] | null>(null);
  const replay = async () => {
    setBusy(true);
    const r = await run('settings.askReplay', { date: isoDay(day) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't replay"));
    setRows((r.out as ReplayRow[] | undefined) ?? []);
  };
  const off = rows?.filter((x) => KEPT_OFF.includes(x.would)) ?? [];
  return (
    <>
      <Line label="Try it on">
        <DatePick label="Day" labelHidden withTime={false} value={day} onValueChange={setDay} />
        <Verb onClick={() => void (busy ? undefined : replay())}>{busy ? 'Judging' : 'Replay the day'}</Verb>
      </Line>
      {rows && (
        <>
          <LabelValue label="Kept off" num>
            {`${off.length} of ${rows.length}`}
          </LabelValue>
          <ul className="cl-list">
            {[...off, ...rows.filter((x) => !off.includes(x))].map((x, i) => (
              <li key={i} className="cl-act">
                <span>{`${x.asker_name || x.asker || 'An agent'}: ${x.private ? 'A private item' : x.title || 'Untitled'}`}</span>
                <span className="fact-dim">{WOULD[x.would] ?? x.would}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** The rules shown: the person's own, or (for an owner or admin, "Everyone") the team default. */
export function AskSection() {
  const ask = useQuery({ ...askQuery, retry: false });
  const [everyone, setEveryone] = useState(false);
  const v = ask.data;
  if (!v?.settings || !v.team) return null;
  const team = everyone && !!v.may_edit_team;
  const r: AskRules = team ? v.team : v.settings;
  const save = (change: Partial<Pick<AskRules, 'mode' | 'budget' | 'criteria'>>) =>
    act('settings.askRules', { mode: r.mode === 'off' ? 'off' : 'on', budget: r.budget, criteria: r.criteria, ...change, ...(team ? { team: true } : {}) });
  const who = team ? 'everyone' : 'you';
  return (
    <>
      <Section name="What agents may ask" target="setting:ask">
        <Facts label="What agents may ask">
          {v.may_edit_team && (
            <Line label="For">
              <Segmented
                label="Whose rules"
                options={[
                  { value: 'you', label: 'You' },
                  { value: 'everyone', label: 'Everyone' },
                ]}
                value={who}
                onValueChange={(w) => setEveryone(w === 'everyone')}
              />
            </Line>
          )}
          <SwitchLine label="Screen asks" checked={r.mode !== 'off'} onChange={(on) => void save({ mode: on ? 'on' : 'off' })} />
          <NumberLine key={`${who}-${r.budget}`} label="Tighten after" value={r.budget} min={1} max={200} onSave={(budget) => void save({ budget })} />
          {!team && <Replay />}
        </Facts>
        {!team && v.settings.own && (
          <Foot>
            <Verb onClick={() => void act('settings.askReset', {})}>Reset</Verb>
          </Foot>
        )}
      </Section>
      <Section
        name="Ask me about"
        count={r.criteria.length}
        target="setting:ask-lines"
        actions={
          r.criteria.length < 10 && (
            <AddLine
              label="Add a line"
              title="Ask me about"
              field="Line"
              onAdd={(line) => save({ criteria: [...r.criteria.filter((c) => c !== line), line] })}
            />
          )
        }
      >
        {r.criteria.length === 0 ? (
          <Empty title="Nothing yet" />
        ) : (
          r.criteria.map((c) => (
            <div key={c} className="row g-line" title={c}>
              <span className="row-title">{c}</span>
              <Menu trigger={<IconButton icon="more" label={`More for ${c}`} size="sm" quiet />}>
                <MenuItem label="Remove line" onSelect={() => void save({ criteria: r.criteria.filter((x) => x !== c) })} />
              </Menu>
            </div>
          ))
        )}
      </Section>
    </>
  );
}
