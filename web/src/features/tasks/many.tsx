import { useAction } from '@/actions';
import { useActors } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import type { Pipeline } from './queries';

const atMidnight = (d: Date) => {
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
};
const inDays = (n: number) => {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return atMidnight(d);
};
const nextMonday = () => {
  const d = new Date();
  d.setDate(d.getDate() + (((8 - d.getDay()) % 7) || 7));
  return atMidnight(d);
};

/** The old app's snooze choices (ui/app/stage-menu.ts): each wakes at local midnight. */
export const snoozeChoices = () => [
  { label: 'Tomorrow', until: inDays(1) },
  { label: 'Next week', until: nextMonday() },
  { label: 'In two weeks', until: inDays(14) },
  { label: 'In a month', until: inDays(30) },
];

/** Move to ›, Assign to ›, Snooze ›, then Close: what both a stage's "⋯" and the Select bar do to a set of tasks. */
function useMany(ids: readonly string[], after?: () => void) {
  const move = useAction('tasks.moveMany');
  const assign = useAction('tasks.assignMany');
  const snooze = useAction('tasks.snoozeMany');
  const done = (p: Promise<unknown>) => void p.finally(() => after?.());
  return {
    move: (to: string) => done(move.run({ tasks: [...ids], to })),
    assign: (owner: string) => done(assign.run({ tasks: [...ids], owner })),
    snooze: (until: string) => done(snooze.run({ tasks: [...ids], until })),
  };
}

function Subs({ p, ids, except, label, after }: { p: Pipeline; ids: readonly string[]; except?: string; label: (verb: string) => string; after?: () => void }) {
  const many = useMany(ids, after);
  const actors = useActors();
  return (
    <>
      <MenuSub label={label('Move')}>
        {p.stages
          .filter((s) => s.id !== except)
          .map((s) => (
            <MenuItem key={s.id} label={s.name} onSelect={() => many.move(s.id)} />
          ))}
        {p.done && <MenuItem label={p.done.name} onSelect={() => many.move('done')} />}
      </MenuSub>
      <MenuSub label={label('Assign')}>
        {actors.map((a) => (
          <MenuItem key={a.id} label={a.name} lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} decorative />} onSelect={() => many.assign(a.id)} />
        ))}
      </MenuSub>
      <MenuSub label={label('Snooze')}>
        {snoozeChoices().map((c) => (
          <MenuItem key={c.label} label={c.label} onSelect={() => many.snooze(c.until)} />
        ))}
      </MenuSub>
    </>
  );
}

/** A stage head's "⋯" (list.js `o.add`): Move all to ›, Close all, Assign all to ›, Snooze all ›, then Edit stage. */
export function StageMenu({ p, stage, ids, onEdit }: { p: Pipeline; stage: { id: string; name: string }; ids: readonly string[]; onEdit?: () => void }) {
  const many = useMany(ids);
  return (
    <Menu trigger={<IconButton icon="more" label={`More, ${stage.name}`} size="sm" className="ib-ghost" />}>
      {ids.length > 0 && (
        <>
          <Subs p={p} ids={ids} except={stage.id} label={(verb) => (verb === 'Snooze' ? 'Snooze all' : `${verb} all to`)} />
          <MenuItem label="Close all" onSelect={() => many.move('closed')} />
        </>
      )}
      {onEdit && (
        <>
          {ids.length > 0 && <MenuRule />}
          <MenuItem label="Edit stage" icon="edit" onSelect={onEdit} />
        </>
      )}
    </Menu>
  );
}

/** Select's bar, in the filter row's place: how many, what to do to them, and Cancel. */
export function SelectBar({ p, ids, onDone }: { p: Pipeline; ids: readonly string[]; onDone: () => void }) {
  const many = useMany(ids, onDone);
  return (
    <div className="filter-row" role="group" aria-label="Selected tasks" data-phone>
      <span className="scope">{ids.length} selected</span>
      {ids.length > 0 && (
        <Menu align="start" trigger={<Button>Change</Button>}>
          <Subs p={p} ids={ids} label={(verb) => (verb === 'Snooze' ? 'Snooze' : `${verb} to`)} after={onDone} />
          <MenuItem label="Close" onSelect={() => many.move('closed')} />
        </Menu>
      )}
      <button className="chip-clear" type="button" onClick={onDone}>
        Cancel
      </button>
    </div>
  );
}
