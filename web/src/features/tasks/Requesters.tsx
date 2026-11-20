import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { useActor, useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { SkeletonRows } from '@/kit/Skeleton';
import { Face } from './parts';
import { requestersQuery, type Requester } from './queries';

type Source = Requester['source'];
const SOURCES: { value: Source; label: string }[] = [
  { value: 'manual', label: 'Added by hand' },
  { value: 'public_form', label: 'Public roadmap' },
  { value: 'inbox', label: 'Email' },
  { value: 'meeting', label: 'Meeting' },
];
const OUTSIDE = 'outside';

/** The dialog's two views: the list, and one requester being added or changed. */
type View = { at: 'list' } | { at: 'edit'; who: Requester | null };

/**
 * Who asked for a roadmap task (old app: roadmaps.ts requesters): teammates and people outside the team, each with
 * their organization, contact, note and whether they are important. Add, change and remove are actions.
 */
export function RequestersDialog({ task, title, onClose }: { task: string; title: string; onClose: () => void }) {
  const [view, setView] = useState<View>({ at: 'list' });
  const close = (open: boolean) => !open && onClose();
  if (view.at === 'edit') return <Editor task={task} who={view.who} onDone={() => setView({ at: 'list' })} onClose={close} />;
  return <List task={task} title={title} onEdit={(who) => setView({ at: 'edit', who })} onClose={close} />;
}

function List({ task, title, onEdit, onClose }: { task: string; title: string; onEdit: (who: Requester | null) => void; onClose: (open: boolean) => void }) {
  const list = useInfiniteQuery(requestersQuery(task));
  const rows = list.data?.pages.flatMap((p) => p.requesters) ?? [];
  let body;
  if (list.isError)
    body = (
      <p className="rq-line">
        Requesters did not load. <Button onClick={() => list.refetch()}>Try again</Button>
      </p>
    );
  else if (!list.data) body = <SkeletonRows rows={3} />;
  else if (!rows.length) body = <p className="rq-line">No requesters yet</p>;
  else
    body = (
      <div className="rq-list">
        {rows.map((r) => (
          <RequesterRow key={r.id} r={r} onEdit={() => onEdit(r)} />
        ))}
        {list.hasNextPage && (
          <Button variant="quiet" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
            More requesters
          </Button>
        )}
      </div>
    );
  return (
    <Dialog
      open
      onOpenChange={onClose}
      title="Requesters"
      description={title}
      actions={
        <>
          <Button onClick={() => onClose(false)}>Close</Button>
          <Button variant="primary" icon="plus" onClick={() => onEdit(null)}>
            Add requester
          </Button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}

function RequesterRow({ r, onEdit }: { r: Requester; onEdit: () => void }) {
  const who = useActor(r.actor);
  const name = r.name || who?.name || r.email || 'Someone';
  const meta = [r.important ? 'Important' : '', r.organization].filter(Boolean).join(', ');
  const pending = r.id.startsWith('tmp:');
  return (
    <Row className="rq" title={name} target={`requester:${r.id}`}>
      {r.actor ? <Face actor={r.actor} /> : <span className="av-gap" />}
      <span className="row-title">{name}</span>
      <span className="row-stage">{meta}</span>
      {pending ? <span /> : <IconButton icon="edit" label={`Edit ${name}`} size="sm" onClick={onEdit} />}
    </Row>
  );
}

interface Draft {
  actor: string;
  name: string;
  email: string;
  phone: string;
  other: string;
  organization: string;
  note: string;
  source: Source;
  important: boolean;
}

const draftOf = (r: Requester | null): Draft => ({
  actor: r?.actor ?? OUTSIDE,
  name: r?.name ?? '',
  email: r?.email ?? '',
  phone: r?.phone ?? '',
  other: r?.other ?? '',
  organization: r?.organization ?? '',
  note: r?.note ?? '',
  source: r?.source ?? 'manual',
  important: !!r?.important,
});

/** Add or change one requester (old app: roadmapRequesterEditor). Who they are is chosen once, when they are added. */
function Editor({ task, who, onDone, onClose }: { task: string; who: Requester | null; onDone: () => void; onClose: (open: boolean) => void }) {
  const actors = useActors();
  const add = useAction('tasks.addRequester');
  const update = useAction('tasks.updateRequester');
  const remove = useAction('tasks.removeRequester');
  const [d, setD] = useState<Draft>(() => draftOf(who));
  const set = <K extends keyof Draft>(k: K) => (v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const outside = d.actor === OUTSIDE;
  const ready = !outside || !!(d.name.trim() || d.email.trim() || d.phone.trim() || d.other.trim());
  const contact = outside ? { name: d.name.trim(), email: d.email.trim(), phone: d.phone.trim(), other: d.other.trim(), source: d.source } : {};
  const fields = { ...contact, organization: d.organization.trim(), note: d.note.trim(), important: d.important };

  const save = async () => {
    if (!ready) return;
    if (who) {
      // shown at once in the list; a refusal says why and takes it back
      void update.run({ task, requester: who.id, ...fields });
      onDone();
      return;
    }
    const r = await add.run({ task, ...(outside ? {} : { actor: d.actor }), ...fields });
    if (r.ok) onDone();
  };
  const drop = () => {
    if (!who) return;
    void remove.run({ task, requester: who.id });
    onDone();
  };

  return (
    <Dialog
      open
      onOpenChange={onClose}
      title={who ? 'Edit requester' : 'Add requester'}
      actions={
        <>
          {who && (
            <Button variant="quiet" onClick={drop} disabled={remove.pending}>
              Remove
            </Button>
          )}
          <Button onClick={() => onDone()}>Cancel</Button>
          <Button variant="primary" disabled={!ready || add.pending} onClick={() => save()}>
            Save
          </Button>
        </>
      }
    >
      <form
        className="rq-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Select
          label="Requester"
          options={[{ value: OUTSIDE, label: 'Someone outside the team' }, ...actors.map((a) => ({ value: a.id, label: a.name }))]}
          value={d.actor}
          onValueChange={set('actor')}
          disabled={!!who}
        />
        {outside && (
          <>
            <Field label="Name" value={d.name} onValueChange={set('name')} autoFocus={!who} />
            <Field label="Email" type="email" value={d.email} onValueChange={set('email')} />
            <Field label="Phone" type="tel" value={d.phone} onValueChange={set('phone')} />
            <Field label="Other contact" value={d.other} onValueChange={set('other')} />
          </>
        )}
        <Field label="Organization" value={d.organization} onValueChange={set('organization')} />
        <Field label="Quote or note" area value={d.note} onValueChange={set('note')} />
        {outside && <Select label="Source" options={SOURCES} value={d.source} onValueChange={set('source')} />}
        <Checkbox label="Important" checked={d.important} onCheckedChange={set('important')} />
      </form>
    </Dialog>
  );
}
