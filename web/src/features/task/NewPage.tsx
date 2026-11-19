import { useState } from 'react';
import { run } from '@/actions';
import type { PagePublished } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { reasonOf } from './model';

/* New page (ui/app/task-pages.ts taskNewPageOpen): a title and the page's text, saved as one of the task's pages. Once
   saved, the page opens. */
export function NewPage({ task, onClose, onSaved }: { task: string; onClose: () => void; onSaved: (file: string) => void }) {
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !!title.trim() && !!text.trim();

  const save = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const r = await run('task.newPage', { task, title, text }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return setError(reasonOf(r.error));
    const file = (r.out as PagePublished | undefined)?.page.file_id;
    onClose();
    if (file) onSaved(file);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title="New page">
      <form
        className="tv-new"
        data-target={`task-new-page:${task}`}
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
        <Field label="Text" value={text} onValueChange={setText} area />
        {error && (
          <p className="f-err" role="alert">
            {error}
          </p>
        )}
        <div className="modal-row">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" busy={busy} disabled={!ready}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
