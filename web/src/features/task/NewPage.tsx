import { useState } from 'react';
import { run } from '@/actions';
import type { PagePublished } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Segmented } from '@/kit/Segmented';
import { LinkPage, UploadFiles } from './Files';
import { reasonOf } from './model';

/* Add a file or page (more.js "Add a file or page": New page, Link a page or file, Upload a file). A new page is a title
   and its text (ui/app/task-pages.ts taskNewPageOpen); a link puts a page from elsewhere on the task; an upload adds
   files from disk (ui/app/task-files.ts). What is made or added opens. */

type Mode = 'page' | 'link' | 'upload';
const MODES = [
  { value: 'page', label: 'New page' },
  { value: 'link', label: 'Link a page' },
  { value: 'upload', label: 'Upload' },
] as const;
const TITLE: Record<Mode, string> = { page: 'New page', link: 'Link a page', upload: 'Upload a file' };

export function NewPage({ task, onClose, onSaved }: { task: string; onClose: () => void; onSaved: (file: string) => void }) {
  const [mode, setMode] = useState<Mode>('page');
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
    <Dialog open onOpenChange={(open) => !open && onClose()} title={TITLE[mode]}>
      <div className="tv-new">
        <Segmented label="What to add" options={MODES} value={mode} onValueChange={setMode} />
        {mode === 'link' && <LinkPage task={task} onDone={onClose} />}
        {mode === 'upload' && (
          <UploadFiles
            task={task}
            onDone={(file) => {
              onClose();
              if (file) onSaved(file);
            }}
          />
        )}
        {mode === 'page' && (
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
        )}
      </div>
    </Dialog>
  );
}
