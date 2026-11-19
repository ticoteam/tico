import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions/run';
import { refusal } from '@/actions';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Foot, Line, SwitchLine, Verb, act } from './parts';
import { askQuery, type AskRules, type ReplayRow, type RoadmapView } from './queries';

/* Settings › Tasks, the parts the old app had beside the roadmap labels: what agents may put on a person (their own
   rules, or the team default for an owner or admin), the lines they want to be asked about, a replay of one past day,
   and the roadmap's websites, who takes visitors' suggestions, and its limits. */

/** One short line typed into a small dialog: a criterion, a website. */
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
          <p className="fact-dim" role="status">{`${rows.length} asks; On would have kept ${off.length} off your plate`}</p>
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
            <Verb onClick={() => void act('settings.askReset', {})}>Use team default</Verb>
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

/** The roadmap's other settings, under its labels: where it may show, who takes suggestions, and visitors' limits. */
export function RoadmapMore({ r }: { r: RoadmapView }) {
  const sites = r.cors_origins ?? [];
  const actors = useActors();
  const agents = actors.filter((a) => a.kind === 'agent').sort((a, b) => a.name.localeCompare(b.name));
  // 'auto' stands for none chosen: the server then picks (suggestions_receiver says whom).
  const chosen = r.suggestions_bot ? (agents.find((a) => a.id === `bot:${r.suggestions_bot}` || a.slug === r.suggestions_bot)?.id.slice(4) ?? r.suggestions_bot) : 'auto';
  const receiver = r.suggestions_receiver;
  const auto = receiver?.source === 'automatic' && receiver.name ? `Automatic: ${receiver.name}` : 'Automatic';
  const options = [
    { value: 'auto', label: auto },
    ...(chosen !== 'auto' && !agents.some((a) => a.id === `bot:${chosen}`) ? [{ value: chosen, label: receiver?.name ?? chosen }] : []),
    ...agents.map((a) => ({ value: a.id.slice(4), label: a.name })),
  ];
  return (
    <>
      <Line label="Websites" target="setting:roadmap-sites">
        <AddLine
          label="Add a website"
          title="A website that can show the roadmap"
          field="Address"
          onAdd={(o) => act('settings.roadmapSites', { origins: [...sites.filter((x) => x !== o), o] })}
        />
      </Line>
      {sites.map((o) => (
        <div key={o} className="row g-line" title={o}>
          <code className="row-title fs-code">{o}</code>
          <IconButton icon="close" label={`Take ${o} off`} size="sm" quiet onClick={() => void act('settings.roadmapSites', { origins: sites.filter((x) => x !== o) })} />
        </div>
      ))}
      <Line label="Suggestions go to" target="setting:roadmap-suggestions">
        <Select
          label="Suggestions go to"
          labelHidden
          look="quiet"
          value={chosen}
          options={options}
          onValueChange={(slug) => void act('settings.roadmapSuggestions', { agent: slug === 'auto' ? null : slug })}
        />
      </Line>
      <NumberLine
        key={`a-${r.action_limit}`}
        label="Visitor requests a day"
        target="setting:roadmap-limits"
        value={r.action_limit}
        min={1}
        max={10000}
        onSave={(n) => void act('settings.roadmapLimits', { action_limit: n })}
      />
      <NumberLine
        key={`s-${r.suggestion_limit}`}
        label="Suggestions waiting"
        value={r.suggestion_limit ?? 20}
        min={1}
        max={1000}
        onSave={(n) => void act('settings.roadmapLimits', { suggestion_limit: n })}
      />
    </>
  );
}
