import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { Section } from '@/pagekinds';
import { Foot, Line, SwitchLine, Verb, act } from './parts';
import { askQuery, type AskRules } from './queries';

/* Settings › Tasks, the parts the old app had beside the roadmap labels: what agents may put on a person (their own
   rules, or the team default for an owner or admin) and the lines they want to be asked about. */

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

/** A whole number typed in: it saves on Enter or when the field is left, never per keystroke. */
function NumberLine({ label, unit, value, min, max, onSave }: { label: string; unit: string; value: number; min: number; max: number; onSave: (n: number) => void }) {
  const [text, setText] = useState(String(value));
  const n = Number(text);
  const bad = !/^\d+$/.test(text.trim()) || n < min || n > max;
  const save = () => !bad && n !== value && onSave(n);
  return (
    <Line label={label}>
      <Field label={label} labelHidden type="number" wide={false} value={text} onValueChange={setText} onCommit={save} error={bad ? `${min} to ${max}` : undefined} />
      <span className="fact-dim">{unit}</span>
    </Line>
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
          <NumberLine key={`${who}-${r.budget}`} label="Tighten after" unit="open asks" value={r.budget} min={1} max={200} onSave={(budget) => void save({ budget })} />
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
