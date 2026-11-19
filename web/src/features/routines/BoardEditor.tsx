import { useState } from 'react';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Menu } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { SidePanel } from '@/pagekinds';
import { ACTS, actKey, actLine, ARG, argOf, condsWords, destName, llmModes, OPS, Q_MIN, TRIGGER_FIELDS, type Act, type Board, type Part } from './board';
import { press, RemoveItem, useConfirmRemove, Verb } from './parts';
import type { Routine } from './types';

/* The inbox board (automations-flow.ts auBoard, auItemEditor): rules → questions → LLM → destinations, each a dense
   row that opens its editor in the panel. Saving puts the one item back into the flow (`routines.saveBoardItem`);
   colors are left out (greys only). */

export interface ItemRef {
  part: Part;
  /** Which one; none adds a new one. */
  at?: number;
}

const MODES: Record<string, string> = { sort: 'Sort', draft: 'Sort + draft', reply: 'Sort + reply' };

/** A person's or agent's name for an owner or agent argument in any id form. */
function useNamer() {
  const actors = useActors();
  const by = new Map<string, string>();
  for (const a of actors) {
    by.set(a.id, a.name);
    by.set(a.id.replace(/^human:/, 'person:'), a.name);
    if (a.slug) by.set(`bot:${a.slug}`, a.name);
  }
  return (id: string) => by.get(id) ?? id.replace(/^\w+:/, '');
}

const chainWords = (then: Act[] | undefined, name: (id: string) => string) => (then ?? []).map((x) => actLine(x, name)).join(', ');

export function BoardView({ routine, board, onOpen }: { routine: Routine; board: Board; onOpen: (it: ItemRef) => void }) {
  const name = useNamer();
  const can = !!routine.can_edit && !routine.readonly;
  const dest = (id: string | undefined) => (id ? destName(id, board.dests) : '');
  const item = (key: string, title: string, sub: string, it: ItemRef) => (
    <Row key={key} className="brow" title={title} target={`board:${routine.id}:${it.part}:${it.at ?? 0}`}>
      {can ? (
        <button type="button" className="row-title row-open" onClick={() => onOpen(it)}>
          {title}
        </button>
      ) : (
        <span className="row-title">{title}</span>
      )}
      <span className="row-sub">{sub}</span>
    </Row>
  );
  const head = (title: string, add?: { label: string; it: ItemRef }) => (
    <h3 className="rr-head">
      {title}
      {can && add && <Verb onClick={() => onOpen(add.it)}>{add.label}</Verb>}
    </h3>
  );
  return (
    <>
      <section className="rail-sec">
        {head('Rules', { label: 'Add rule', it: { part: 'rule' } })}
        {board.rules.map((r, i) =>
          item(`r${i}`, condsWords(r), [chainWords(r.then, name), r.to ? `→ ${dest(r.to)}` : r.next === 'rule' ? 'Moves on' : 'Ends'].filter(Boolean).join(' · '), { part: 'rule', at: i }),
        )}
      </section>
      <section className="rail-sec">
        {head('Questions', { label: 'Add question', it: { part: 'question' } })}
        {board.questions.map((q, i) => item(`q${q.id}`, q.text, [chainWords(q.then, name), q.to ? `→ ${dest(q.to)}` : 'Ends'].filter(Boolean).join(' · '), { part: 'question', at: i }))}
      </section>
      <section className="rail-sec">
        {head('LLM')}
        {item('llm', MODES[board.llm.mode] ?? 'Sort', board.llm.to.map(dest).join(', '), { part: 'llm' })}
      </section>
      <section className="rail-sec">
        {head('Destinations', { label: 'Add destination', it: { part: 'destination' } })}
        {board.dests.map((d, i) => item(`d${d.id}`, d.name || d.id, chainWords(d.then, name) || 'Do nothing', { part: 'destination', at: i }))}
      </section>
    </>
  );
}

// ---------------------------------------------------------------- one item's editor

interface Link {
  key: number;
  do: string;
  args: Record<string, string>;
}
interface CondRow {
  key: number;
  field: string;
  op: string;
  value: string;
}

const linksOf = (then: Act[] | undefined): Link[] =>
  (then ?? []).map((x, key) => {
    const k = actKey(x);
    return { key, do: k, args: Object.fromEntries((ARG[k] ?? []).map((f) => [f.key, argOf(x, f.key)])) };
  });
const nextKey = (list: { key: number }[]) => Math.max(-1, ...list.map((x) => x.key)) + 1;
const TITLE: Record<Part, string> = { rule: 'Rule', question: 'Question', destination: 'Destination', llm: 'LLM' };

export function BoardItemEditor({ routine, board, it, onDone, onClose }: { routine: Routine; board: Board; it: ItemRef; onDone: () => void; onClose: () => void }) {
  const rule = it.part === 'rule' && it.at !== undefined ? board.rules[it.at] : undefined;
  const question = it.part === 'question' && it.at !== undefined ? board.questions[it.at] : undefined;
  const dest = it.part === 'destination' && it.at !== undefined ? board.dests[it.at] : undefined;
  const fields = TRIGGER_FIELDS[routine.trigger.type] ?? [];

  const [conds, setConds] = useState<CondRow[]>(() =>
    (rule?.when ?? []).map((c, key) => ({ key, field: String(c.field ?? ''), op: c.op || 'is', value: c.value === true ? 'yes' : c.value === false ? 'no' : String(c.value ?? '') })),
  );
  const [condsTouched, setCondsTouched] = useState(false);
  const [match, setMatch] = useState<'all' | 'any'>(rule?.match === 'any' ? 'any' : 'all');
  const [links, setLinks] = useState<Link[]>(() => linksOf(rule?.then ?? question?.then ?? dest?.then));
  const was = rule ? (rule.to ? `to:${rule.to}` : rule.next === 'rule' ? 'next' : 'end') : question?.to ? `to:${question.to}` : 'end';
  const [after, setAfter] = useState(was);
  const [text, setText] = useState(question?.text ?? '');
  const [min, setMin] = useState(() => {
    const m = question ? question.min : Q_MIN;
    return m == null || m === '' ? '' : String(Math.round(Number(m) * 100));
  });
  const [name, setName] = useState(dest?.name ?? '');
  const [mode, setMode] = useState(board.llm.mode);
  const [prompt, setPrompt] = useState(board.llm.prompt);
  const [to, setTo] = useState<string[]>(board.llm.to);
  const [unsure, setUnsure] = useState(board.llm.default);
  const [busy, setBusy] = useState(false);
  const remove = useConfirmRemove();

  const pct = min.trim() === '' ? null : Number(min);
  const minOk = pct === null || (Number.isFinite(pct) && pct >= 0 && pct <= 100);
  const ready =
    !busy &&
    (it.part === 'rule'
      ? conds.every((c) => c.field.trim())
      : it.part === 'question'
        ? !!text.trim() && minOk
        : it.part === 'destination'
          ? !!name.trim()
          : to.length > 0);

  const then = () => links.map((l) => ({ do: l.do, ...(Object.keys(l.args).length ? { args: l.args } : {}) }));
  const goTo = () => (after.startsWith('to:') ? { goTo: after.slice(3) } : { goTo: '', moveOn: after === 'next' });
  const input = () => {
    const base = { routine: routine.id, part: it.part, ...(it.at === undefined ? {} : { at: it.at }) };
    if (it.part === 'rule') {
      return { ...base, ...(condsTouched || !rule ? { when: conds.map((c) => ({ field: c.field.trim(), op: c.op, value: c.value })) } : {}), match, then: then(), ...goTo() };
    }
    if (it.part === 'question') return { ...base, text: text.trim(), min: pct === null ? null : pct / 100, then: then(), goTo: goTo().goTo };
    if (it.part === 'destination') return { ...base, name: name.trim(), then: then() };
    return { ...base, mode, prompt, dests: to, unsure: to.includes(unsure) ? unsure : to[0] };
  };
  const save = async () => {
    setBusy(true);
    const ok = await press('routines.saveBoardItem', input());
    setBusy(false);
    if (ok) onDone();
  };
  const drop = () =>
    remove.confirm(`this ${TITLE[it.part].toLowerCase()}`, () => {
      void press('routines.removeBoardItem', { routine: routine.id, part: it.part, at: it.at }).then((ok) => ok && onDone());
    });

  const destOptions = board.dests.map((d) => ({ value: `to:${d.id}`, label: `Go to ${d.name || d.id}` }));
  const setCond = (key: number, patch: Partial<CondRow>) => {
    setCondsTouched(true);
    setConds((list) => list.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  };

  return (
    <SidePanel
      className="rr"
      title={it.at === undefined && it.part !== 'llm' ? `New ${TITLE[it.part].toLowerCase()}` : TITLE[it.part]}
      target={`board:${routine.id}:${it.part}:${it.at ?? 'new'}`}
      onClose={onClose}
      actions={
        it.at !== undefined && it.part !== 'llm' ? (
          <Menu trigger={<IconButton icon="more" label="More" quiet />}>
            <RemoveItem onSelect={drop} />
          </Menu>
        ) : undefined
      }
      foot={
        <>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            Save
          </Button>
          <Button variant="quiet" onClick={onDone}>
            Cancel
          </Button>
        </>
      }
    >
      <section className="rail-sec rr-form">
        {it.part === 'rule' && (
          <>
            {conds.length > 1 && (
              <Segmented
                label="Match"
                options={[
                  { value: 'all', label: 'All of these' },
                  { value: 'any', label: 'Any of these' },
                ]}
                value={match}
                onValueChange={setMatch}
              />
            )}
            <fieldset className="rr-list">
              <legend>
                When
                <Verb
                  onClick={() => {
                    setCondsTouched(true);
                    setConds((list) => [...list, { key: nextKey(list), field: fields[0] ?? '', op: 'is', value: '' }]);
                  }}
                >
                  Add condition
                </Verb>
              </legend>
              {!conds.length && <p className="rr-note">Always</p>}
              {conds.map((c, i) => (
                <div key={c.key} className="rr-line">
                  <Field label={`Condition ${i + 1} field`} labelHidden value={c.field} onValueChange={(v) => setCond(c.key, { field: v })} placeholder={fields[0] ?? 'Field'} autoComplete="off" />
                  <Select
                    label={`Condition ${i + 1} test`}
                    labelHidden
                    options={Object.entries(OPS).map(([value, label]) => ({ value, label }))}
                    value={c.op}
                    onValueChange={(v) => setCond(c.key, { op: v })}
                  />
                  <Field label={`Condition ${i + 1} value`} labelHidden value={c.value} onValueChange={(v) => setCond(c.key, { value: v })} placeholder="Value" autoComplete="off" />
                  <IconButton
                    icon="close"
                    label={`Remove condition ${i + 1}`}
                    size="sm"
                    quiet
                    onClick={() => {
                      setCondsTouched(true);
                      setConds((list) => list.filter((x) => x.key !== c.key));
                    }}
                  />
                </div>
              ))}
            </fieldset>
          </>
        )}
        {it.part === 'question' && (
          <>
            <Field label="Question" area value={text} onValueChange={setText} placeholder="Is this a receipt?" autoFocus={!question} />
            <Field label="Sure at least (%)" type="number" value={min} onValueChange={setMin} error={minOk ? undefined : 'From 0 to 100'} />
          </>
        )}
        {it.part === 'destination' && <Field label="Name" value={name} onValueChange={setName} autoFocus={!dest} autoComplete="off" />}
        {it.part === 'llm' ? (
          <>
            {llmModes(routine).length > 1 && <Segmented label="Mode" options={llmModes(routine)} value={mode} onValueChange={setMode} />}
            <Field label="Instructions" area value={prompt} onValueChange={setPrompt} placeholder="How to sort" />
            <fieldset className="rr-list">
              <legend>Destinations</legend>
              {board.dests.map((d) => (
                <Checkbox
                  key={d.id}
                  label={d.name || d.id}
                  checked={to.includes(d.id)}
                  onCheckedChange={(c) => setTo((list) => (c ? [...list, d.id] : list.filter((x) => x !== d.id)))}
                />
              ))}
            </fieldset>
            <Select
              label="When unsure"
              options={to.map((id) => ({ value: id, label: destName(id, board.dests) }))}
              value={to.includes(unsure) ? unsure : (to[0] ?? null)}
              onValueChange={setUnsure}
              placeholder="Pick a destination"
            />
          </>
        ) : (
          <Chain links={links} onChange={setLinks} />
        )}
        {(it.part === 'rule' || it.part === 'question') && (
          <Select
            label="Then"
            options={[{ value: 'end', label: 'End' }, ...(it.part === 'rule' ? [{ value: 'next', label: 'Move on' }] : []), ...destOptions]}
            value={after}
            onValueChange={setAfter}
          />
        )}
      </section>
      {remove.node}
    </SidePanel>
  );
}

/** The actions an item runs, in order, each with the arguments the editor shows. */
function Chain({ links, onChange }: { links: Link[]; onChange: (l: Link[]) => void }) {
  const actors = useActors();
  const people = actors.map((a) => ({ value: a.id, label: a.name }));
  const agents = actors.filter((a) => a.kind === 'agent').map((a) => ({ value: a.id, label: a.name }));
  const set = (key: number, patch: Partial<Link>) => onChange(links.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pick = (v: string, options: { value: string }[]) => {
    const as = v.replace(/^person:/, 'human:');
    return options.find((o) => o.value === as || o.value === `bot:${v}`)?.value ?? null;
  };
  return (
    <fieldset className="rr-list">
      <legend>
        Do
        <Verb onClick={() => onChange([...links, { key: nextKey(links), do: 'create_task', args: { title: '', owner: '' } }])}>Add action</Verb>
      </legend>
      {!links.length && <p className="rr-note">Nothing</p>}
      {links.map((l, i) => (
        <div key={l.key} className="rr-line">
          <Select
            label={`Action ${i + 1}`}
            labelHidden
            options={Object.entries(ACTS).map(([value, label]) => ({ value, label }))}
            value={l.do}
            onValueChange={(v) => set(l.key, { do: v, args: Object.fromEntries((ARG[v] ?? []).map((f) => [f.key, ''])) })}
          />
          {(ARG[l.do] ?? []).map((f) =>
            f.key === 'owner' || f.key === 'bot' ? (
              <Select
                key={f.key}
                label={`Action ${i + 1} ${f.key === 'bot' ? 'agent' : 'owner'}`}
                labelHidden
                options={f.key === 'bot' ? agents : people}
                value={pick(l.args[f.key] ?? '', f.key === 'bot' ? agents : people)}
                onValueChange={(v) => set(l.key, { args: { ...l.args, [f.key]: v } })}
                placeholder={f.key === 'bot' ? 'Agent' : 'Owner'}
              />
            ) : (
              <Field
                key={f.key}
                label={`Action ${i + 1} ${f.key}`}
                labelHidden
                value={l.args[f.key] ?? ''}
                onValueChange={(v) => set(l.key, { args: { ...l.args, [f.key]: v } })}
                placeholder={f.key[0]?.toUpperCase() + f.key.slice(1)}
                autoComplete="off"
              />
            ),
          )}
          <IconButton icon="close" label={`Remove action ${i + 1}`} size="sm" quiet onClick={() => onChange(links.filter((x) => x.key !== l.key))} />
        </div>
      ))}
    </fieldset>
  );
}
