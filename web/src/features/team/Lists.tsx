import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { FIELD_NAME, LIST_FIELDS, isListField, lineOf, type ListLine } from './model';
import { OwnRows } from './parts';
import { listKey, listTasksAfter, listTasksQuery, taskTabsQuery, type OwnTask, type TaskTab } from './queries';

/** Its own lists (the layout's tabs that are not a default), each with its key, and whether the viewer may change them. */
export function useLists(slug: string) {
  const { data } = useQuery(taskTabsQuery(slug));
  const tabs = data?.tabs ?? [];
  const lists = tabs.flatMap((tab, i) => (tab.builtin ? [] : [{ key: listKey(tabs, i), tab }]));
  return { lists, canEdit: !!data?.can_edit };
}

/** Its own lists on its page (ui bot-tasks custom tabs): one section each, its tasks as the Tasks section draws them. */
export function ListSections({ slug, me }: { slug: string; me: string | undefined }) {
  const { lists, canEdit } = useLists(slug);
  return lists.map(({ key, tab }) => <ListSection key={key} slug={slug} list={key} tab={tab} me={me} canEdit={canEdit} />);
}

function ListSection({ slug, list, tab, me, canEdit }: { slug: string; list: string; tab: TaskTab; me: string | undefined; canEdit: boolean }) {
  const q = useQuery(listTasksQuery(slug, list));
  const remove = useAction('agents.removeList');
  const [editing, setEditing] = useState(false);
  // Pages past the first 200, read on "N more" from the page before's `next` (ui bot-tasks Show more).
  const [later, setLater] = useState<{ rows: OwnTask[]; next: string | null } | null>(null);
  const [reading, setReading] = useState(false);
  const first = q.data?.tasks ?? [];
  const seen = new Set(first.map((t) => t.id));
  const rows = [...first, ...(later?.rows ?? []).filter((t) => !seen.has(t.id))];
  const next = later ? later.next : (q.data?.next ?? null);
  const more = (q.data?.count ?? 0) - rows.length;
  const readMore = async () => {
    if (!next) return;
    setReading(true);
    try {
      const page = await listTasksAfter(slug, list, next);
      setLater((cur) => ({ rows: [...(cur?.rows ?? []), ...page.tasks], next: page.next ?? null }));
    } catch {
      toast({ text: "Couldn't read more", kind: 'failed' });
    } finally {
      setReading(false);
    }
  };
  return (
    <Section
      name={tab.name}
      count={q.data?.count}
      target={`list:${slug}:${list}`}
      actions={
        canEdit && (
          <>
            <Menu trigger={<IconButton icon="more" size="sm" quiet label={`${tab.name} options`} />}>
              <MenuItem label="Edit" icon="edit" onSelect={() => setEditing(true)} />
              <MenuItem label="Remove" icon="close" onSelect={() => void remove.run({ slug, list })} />
            </Menu>
            {editing && <ListEditor open onOpenChange={setEditing} slug={slug} list={list} tab={tab} />}
          </>
        )
      }
    >
      <OwnRows tasks={rows} me={me} label={tab.name} onEnd={next && !reading ? () => void readMore() : undefined} />
      {more > 0 && (
        <div className="stage-fold">
          <span className="fold-label">Earlier</span>
          {next ? (
            <button type="button" disabled={reading} onClick={() => void readMore()}>
              {more} more
            </button>
          ) : (
            <span>{more} more</span>
          )}
        </div>
      )}
    </Section>
  );
}

type Line = { field: string; value: string };

const FIRST: Record<string, string> = {
  status: 'open',
  assigned_to: 'this_bot',
  asked_by: 'this_bot',
  waiting_on: 'any',
  needs: 'you',
  created_within: '7',
  updated_within: '7',
  done_within: '7',
  due: 'overdue',
  label: '',
};
const STATUSES = [
  ['open', 'Open'],
  ['done', 'Done'],
  ['closed', 'Closed'],
] as const;
const WAITING = [
  { value: 'any', label: 'Anyone' },
  { value: 'person', label: 'A person' },
  { value: 'bot', label: 'An agent' },
  { value: 'me', label: 'Me' },
  { value: 'not_me', label: 'Not me' },
];
const DUE = [
  { value: 'overdue', label: 'Overdue' },
  { value: '0', label: 'Today' },
  { value: '1', label: 'In 1 day' },
  { value: '7', label: 'In 7 days' },
  { value: '30', label: 'In 30 days' },
];
const FIELDS = LIST_FIELDS.map((f) => ({ value: f, label: FIELD_NAME[f] ?? f }));
const NEW_LIST: Line[] = [{ field: 'assigned_to', value: 'this_bot' }];

/** A row is whole when it has a value; a Stage or Pipeline row from an older layout is only read and removed here. */
const whole = (l: Line) => isListField(l.field) && !!l.value.trim() && (!/_within$/.test(l.field) || /^\d+$/.test(l.value.trim()));

/**
 * A list's name and filter (ui bot-sidebar botFilterOpen): rows a task must all match. New when `list` is left out.
 * Stage and Pipeline are not offered: the server refuses them on save (backend/models.py BotTaskCondition).
 */
export function ListEditor({
  open,
  onOpenChange,
  slug,
  list,
  tab,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slug: string;
  list?: string;
  tab?: TaskTab;
}) {
  const start = () => ({ name: tab?.name ?? '', lines: tab ? (tab.conditions ?? []).map(lineOf) : NEW_LIST });
  const [draft, setDraft] = useState(start);
  const add = useAction('agents.addList');
  const change = useAction('agents.changeList');
  const set = (i: number, l: Line) => setDraft((d) => ({ ...d, lines: d.lines.map((x, j) => (j === i ? l : x)) }));
  const name = draft.name.trim();
  const ready = !!name && draft.lines.every(whole);
  const save = () => {
    onOpenChange(false);
    const conditions = draft.lines as ListLine[];
    if (list) void change.run({ slug, list, name, conditions });
    else void add.run({ slug, name, conditions });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setDraft(start());
        onOpenChange(o);
      }}
      title={list ? 'Edit list' : 'New list'}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Name" value={draft.name} onValueChange={(v) => setDraft((d) => ({ ...d, name: v }))} autoFocus />
      <div className="list-rows" role="group" aria-label="Filter">
        {draft.lines.map((l, i) => (
          <div key={i} className="list-row">
            {isListField(l.field) ? (
              <Select label={`Row ${i + 1} field`} labelHidden options={FIELDS} value={l.field} onValueChange={(f) => set(i, { field: f, value: FIRST[f] ?? '' })} />
            ) : (
              <span className="list-old">{FIELD_NAME[l.field] ?? l.field}</span>
            )}
            <RowValue line={l} n={i + 1} onChange={(value) => set(i, { ...l, value })} />
            <IconButton
              icon="close"
              size="sm"
              quiet
              label={`Remove row ${i + 1}`}
              onClick={() => setDraft((d) => ({ ...d, lines: d.lines.filter((_x, j) => j !== i) }))}
            />
          </div>
        ))}
        {draft.lines.length < 20 && (
          <Button variant="quiet" onClick={() => setDraft((d) => ({ ...d, lines: [...d.lines, { field: 'status', value: 'open' }] }))}>
            Add row
          </Button>
        )}
      </div>
    </Dialog>
  );
}

function RowValue({ line, n, onChange }: { line: Line; n: number; onChange: (value: string) => void }) {
  const actors = useActors();
  const label = `Row ${n} value`;
  switch (line.field) {
    case 'status': {
      const on = new Set(line.value.split(',').filter(Boolean));
      return (
        <span className="list-value">
          {STATUSES.map(([v, word]) => (
            <Checkbox
              key={v}
              label={word}
              checked={on.has(v)}
              onCheckedChange={(c) => onChange(STATUSES.map(([x]) => x).filter((x) => (x === v ? c : on.has(x))).join(','))}
            />
          ))}
        </span>
      );
    }
    case 'assigned_to':
    case 'asked_by': {
      const options = [
        { value: 'this_bot', label: 'This agent' },
        { value: 'me', label: 'Me' },
        ...(line.field === 'assigned_to' ? [{ value: 'others', label: 'Others' }] : []),
        ...actors.map((a) => ({ value: a.id, label: a.name })),
      ];
      return <Select label={label} labelHidden options={options} value={line.value || null} onValueChange={onChange} />;
    }
    case 'waiting_on':
      return <Select label={label} labelHidden options={WAITING} value={line.value || null} onValueChange={onChange} />;
    case 'needs':
      return <span className="list-value">You</span>;
    case 'due': {
      const options = DUE.some((o) => o.value === line.value) ? DUE : [...DUE, { value: line.value, label: `In ${line.value} days` }];
      return <Select label={label} labelHidden options={options} value={line.value || null} onValueChange={onChange} />;
    }
    case 'created_within':
    case 'updated_within':
    case 'done_within':
      return <Field label={`${label} in days`} labelHidden type="number" value={line.value} onValueChange={onChange} placeholder="Days" />;
    case 'label':
      return <Field label={label} labelHidden value={line.value} onValueChange={onChange} placeholder="Label" />;
    default:
      return <span className="list-value list-old">{line.value}</span>;
  }
}
