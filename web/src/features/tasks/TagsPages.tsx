import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { Link, useLocation } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { useAction } from '@/actions';
import { meQuery, useActors } from '@/data';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field, Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { TaskRow } from '@/kit/Row';
import { Table, Td, Th } from '@/kit/Table';
import { FilterRow, List, PageMore, Section, type FilterChip } from '@/pagekinds';
import { Capped, LIST_CAP, LIST_SHOW, RowMore, ROW_H } from './capped';
import { NewTask } from './NewTask';
import { Face, markOf, taskHref } from './parts';
import { pipelinesQuery, tagKeyOf, tagQuery, tagsQuery, type Pipeline, type TaskItem } from './queries';
import type { Tag } from '@/api/schema';
import './tasks.css';

/** What narrows Tags or a tag's page, as the address holds it (`tags.filter`). */
function useNarrow() {
  const search = useLocation({ select: (l) => l.search }) as Record<string, unknown>;
  return {
    q: typeof search.q === 'string' ? search.q : '',
    owner: typeof search.owner === 'string' ? search.owner : '',
    late: search.late === true || search.late === 'true',
  };
}

/** The head's search field: typing narrows the page after a short pause, and the address keeps it. */
function Find({ label, q }: { label: string; q: string }) {
  const filter = useAction('tags.filter');
  const [words, setWords] = useState(q);
  const [seen, setSeen] = useState(q);
  if (seen !== q) {
    setSeen(q);
    setWords(q);
  }
  useEffect(() => {
    if (words.trim() === q) return;
    const t = setTimeout(() => void filter.run({ q: words.trim() || null }), 250);
    return () => clearTimeout(t);
  }, [words, q, filter]);
  return <Search label={label} value={words} onValueChange={setWords} placeholder="Search" />;
}

const nameOf = (t: Pick<Tag, 'label' | 'key'>) => t.label || t.key;

/** Tags: every tag and when it was last used (more.js tagsPage). A tag opens its tasks; + makes one. */
export function TagsPage() {
  const tags = useSuspenseQuery(tagsQuery).data;
  const { q } = useNarrow();
  const [making, setMaking] = useState(false);
  const words = q.toLowerCase();
  const list = [...tags]
    .filter((t) => !words || nameOf(t).toLowerCase().includes(words) || t.key.includes(words))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return (
    <>
      <List
        title="Tags"
        path="/tags"
        state={list.length ? 'ready' : 'empty'}
        empty={
          tags.length
            ? { title: 'No tags match' }
            : {
                title: 'No tags yet',
                action: (
                  <Button variant="primary" icon="plus" onClick={() => setMaking(true)}>
                    New tag
                  </Button>
                ),
              }
        }
        className="tasks-page"
        search={<Find label="Search tags" q={q} />}
        actions={<IconButton icon="plus" label="New tag" data-phone onClick={() => setMaking(true)} data-target="tags:new" />}
        sections={
          <Table label="Tags">
            <thead>
              <tr>
                <Th>Tag</Th>
                <Th num sorted="descending">
                  Last used
                </Th>
              </tr>
            </thead>
            <tbody>
              {list.map((t) => (
                <tr key={t.id}>
                  <Td>
                    <Link to="/tags/$tag" params={{ tag: t.id }} data-target={`tag:${t.id}`}>
                      {nameOf(t)}
                    </Link>
                  </Td>
                  <Td num>
                    <Ago at={t.updated_at} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        }
      />
      {making && <NewTagDialog open onOpenChange={setMaking} />}
    </>
  );
}

/** A new tag: its name; its key comes from the name. */
function NewTagDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const make = useAction('tags.create');
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  // Drawn only while open, so each opening starts empty.
  const madeKey = tagKeyOf(key) || tagKeyOf(name);
  const send = () => {
    if (!madeKey) return;
    onOpenChange(false);
    void make.run({ name: name.trim(), key: madeKey });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New tag"
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!name.trim() || !madeKey} onClick={send}>
            Create
          </Button>
        </>
      }
    >
      <Field label="Name" value={name} onValueChange={setName} placeholder="bug" autoFocus />
      <Field label="Key" value={key} onValueChange={setKey} placeholder={tagKeyOf(name) || 'Made from the name'} />
    </Dialog>
  );
}

interface Group {
  pipe: Pipeline | undefined;
  rows: TaskItem[];
}

/**
 * One tag: its tasks grouped by pipeline, each with its stage; Done is a section drawn shut (more.js tagPage, ui tags.ts
 * pageTag). Search, Filter and New task in the head; Edit in "⋯".
 */
export function TagPage({ tag }: { tag: string }) {
  useFollow({ allTasks: true });
  const detail = useSuspenseQuery(tagQuery(tag)).data;
  const pipes = useSuspenseQuery(pipelinesQuery).data;
  const me = useQuery(meQuery).data?.id;
  const narrow = useNarrow();
  const filter = useAction('tags.filter');
  const more = useAction('tags.moreTasks');
  const [editing, setEditing] = useState(false);
  const [filing, setFiling] = useState(false);
  const t = detail.tag;
  const name = nameOf(t);
  const editable = !!detail.editable;
  const words = narrow.q.toLowerCase();
  const all = detail.tasks as TaskItem[];
  const tasks = all.filter(
    (x) => (!words || x.title.toLowerCase().includes(words)) && (!narrow.owner || x.owner_id === narrow.owner) && (!narrow.late || !!x.late),
  );
  const open = tasks.filter((x) => x.status === 'open');
  const done = tasks.filter((x) => x.status !== 'open');
  const groups: Group[] = [];
  for (const x of open) {
    let g = groups.find((y) => (y.pipe?.id ?? null) === x.pipeline_id);
    if (!g) groups.push((g = { pipe: pipes.find((p) => p.id === x.pipeline_id), rows: [] }));
    g.rows.push(x);
  }
  const stageName = (x: TaskItem, p: Pipeline | undefined) =>
    p?.stages.find((s) => s.id === x.stage_id)?.name ?? (p?.done?.id === x.stage_id ? p?.done?.name : p?.closed?.name) ?? '';
  const row = (x: TaskItem) => {
    const p = pipes.find((y) => y.id === x.pipeline_id);
    return (
      <TaskRow
        key={x.id}
        title={x.title}
        href={p ? taskHref(p.id, x) : `/task/${x.number ?? encodeURIComponent(x.id)}`}
        target={`task:${x.number ?? x.id}`}
        mark={markOf(x, me)}
        late={!!x.late}
        pills={<span className="row-stage">{stageName(x, p)}</span>}
        avatar={<Face actor={x.owner_id} />}
        at={x.updated_at}
      />
    );
  };
  const narrowed = !!(narrow.q || narrow.owner || narrow.late);
  return (
    <>
      <List
        title={name}
        path={`/tags/${tag}`}
        state={tasks.length ? 'ready' : 'empty'}
        empty={{ title: narrowed ? 'No tasks match' : `Nothing tagged ${name}` }}
        className="tasks-page"
        search={<Find label="Search tasks" q={narrow.q} />}
        actions={
          <>
            <TagFilter owner={narrow.owner} late={narrow.late} />
            {!!pipes.length && <IconButton icon="plus" label="New task" data-phone onClick={() => setFiling(true)} data-target="tags:new-task" />}
          </>
        }
        more={
          editable && (
            <PageMore target="tag:more">
              <MenuItem icon="edit" label="Edit" onSelect={() => setEditing(true)} />
            </PageMore>
          )
        }
        filter={<TagChips owner={narrow.owner} late={narrow.late} onClearAll={() => void filter.run({ owner: null, late: null, q: null })} />}
        sections={
          <>
            {groups.map((g) => (
              <Section key={g.pipe?.id ?? 'none'} name={g.pipe?.name ?? 'Tasks'} count={g.rows.length}>
                <Capped list={g.rows} cap={LIST_CAP} show={LIST_SHOW} height={ROW_H} more={undefined} draw={row} moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />} />
              </Section>
            ))}
            {done.length > 0 && (
              <Section name="Done" count={done.length} defaultOpen={false}>
                <Capped list={done} cap={LIST_CAP} show={LIST_SHOW} height={ROW_H} more={undefined} draw={row} moreRow={(n, open) => <RowMore key="more" n={n} onClick={open} />} />
              </Section>
            )}
            {detail.next_offset != null && (
              <button className="row task row-more" type="button" disabled={more.pending} onClick={() => void more.run({ tag })}>
                <span className="row-mark" />
                <span className="row-title">More tasks</span>
              </button>
            )}
          </>
        }
      />
      {editable && editing && <EditTagDialog open onOpenChange={setEditing} tag={t} />}
      {filing && (
        <NewTask
          open
          onOpenChange={(o) => !o && setFiling(false)}
          pipelines={pipes}
          pipeline={pipes[0]!.id}
          owner={narrow.owner || null}
          tag={{ id: tag, key: t.key }}
        />
      )}
    </>
  );
}

/** Filter on a tag's page: its owner, and Late. */
function TagFilter({ owner, late }: { owner: string; late: boolean }) {
  const filter = useAction('tags.filter');
  const actors = useActors();
  return (
    <Menu trigger={<IconButton icon="filter" label="Filter" on={!!owner || late} data-target="tags:filter" />}>
      <MenuSub label="Owner">
        {actors.map((a) => (
          <MenuItem
            key={a.id}
            label={a.name}
            lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} decorative />}
            on={owner === a.id}
            onSelect={() => void filter.run({ owner: owner === a.id ? null : a.id })}
          />
        ))}
      </MenuSub>
      <MenuRule />
      <MenuItem label="Late" on={late} onSelect={() => void filter.run({ late: !late })} />
    </Menu>
  );
}

function TagChips({ owner, late, onClearAll }: { owner: string; late: boolean; onClearAll: () => void }) {
  const filter = useAction('tags.filter');
  const actors = useActors();
  const chips: FilterChip[] = [];
  if (owner) chips.push({ label: `${actors.find((a) => a.id === owner)?.name ?? 'Someone'} Owner`, onClear: () => void filter.run({ owner: null }) });
  if (late) chips.push({ label: 'Late', onClear: () => void filter.run({ late: null }) });
  return <FilterRow filters={chips} onClearAll={onClearAll} />;
}

/** A tag's name, notes and metadata (ui tags.ts tagEditForm), saved against the version it opened on. */
function EditTagDialog({ open, onOpenChange, tag }: { open: boolean; onOpenChange: (open: boolean) => void; tag: Tag }) {
  const save = useAction('tags.edit');
  const start = () => ({ name: nameOf(tag), notes: tag.markdown, metadata: JSON.stringify(tag.metadata ?? {}, null, 2) });
  // Drawn only while open, so each opening starts from the tag as it is now.
  const [draft, setDraft] = useState(start);
  const send = () => {
    onOpenChange(false);
    void save.run({ tag: tag.id, name: draft.name.trim(), notes: draft.notes, metadata: draft.metadata });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Edit tag"
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!draft.name.trim()} onClick={send}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Name" value={draft.name} onValueChange={(v) => setDraft((d) => ({ ...d, name: v }))} autoFocus />
      <Field label="Notes" value={draft.notes} onValueChange={(v) => setDraft((d) => ({ ...d, notes: v }))} area />
      <Field label="Metadata" value={draft.metadata} onValueChange={(v) => setDraft((d) => ({ ...d, metadata: v }))} area />
    </Dialog>
  );
}
