import { useState } from 'react';
import { useAction } from '@/actions';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import type { Pipeline } from './queries';

/** New task: title, pipeline and owner, then Create (list.js newTask). The row shows at once, in `stage` when one is given. */
export function NewTask({
  open,
  onOpenChange,
  pipelines,
  pipeline,
  stage = null,
  owner,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pipelines: readonly Pipeline[];
  pipeline: string;
  /** A stage head's "+": the stage it lands in, while the pipeline stays the same. */
  stage?: string | null;
  owner: string | null;
}) {
  const create = useAction('tasks.create');
  const actors = useActors();
  const [title, setTitle] = useState('');
  const [into, setInto] = useState(pipeline);
  const [who, setWho] = useState<string>(owner ?? '');
  const ready = title.trim().length > 0;
  const submit = () => {
    if (!ready) return;
    void create.run({ title: title.trim(), pipeline: into, owner: who || null, stage: into === pipeline ? stage : null });
    setTitle('');
    onOpenChange(false);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New task"
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={submit}>
            Create
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
      </form>
      <Select label="Pipeline" options={pipelines.map((p) => ({ value: p.id, label: p.name }))} value={into} onValueChange={setInto} />
      <Select label="Owner" options={actors.map((a) => ({ value: a.id, label: a.name }))} value={who || null} onValueChange={setWho} placeholder="Nobody" />
    </Dialog>
  );
}
