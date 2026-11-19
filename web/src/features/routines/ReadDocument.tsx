import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Button } from '@/kit/Button';
import { Combobox } from '@/kit/Combobox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Link } from '@/kit/Link';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { pressOut } from './parts';
import { docFilesQuery, docSearchQuery, ruleSourcesQuery } from './queries';
import type { DocFile, DocRead } from './types';

/* Add document (org-rules.ts ruDocOpen): a file from Files or a public link, who it is from, and an optional
   description. An agent reads it in a task and suggests outside rules from it; the dialog then says so and links the
   task. Files are picked from the 50 most recent ones with text, and from a search of Files as words are typed. */

const KINDS = [
  { value: 'vendor', label: 'Vendor' },
  { value: 'customer', label: 'Customer' },
  { value: 'government', label: 'Government' },
  { value: 'other', label: 'Other' },
] as const;
type Kind = (typeof KINDS)[number]['value'];
const NEW = '+new';
const isLink = (v: string) => /^https?:\/\/\S+$/i.test(v);

export function ReadDocument({ onClose }: { onClose: () => void }) {
  const recent = useQuery(docFilesQuery).data ?? [];
  // the words typed in the picker, searched in Files a beat after typing stops: older files show too
  const [typed, setTyped] = useState('');
  const [q, setQ] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setQ(typed.trim()), 250);
    return () => clearTimeout(t);
  }, [typed]);
  const found = useQuery(docSearchQuery(q)).data ?? [];
  const [picked, setPicked] = useState<DocFile | null>(null);
  const files = [...recent, ...found.filter((f) => !recent.some((x) => x.id === f.id)), ...(picked && ![...recent, ...found].some((x) => x.id === picked.id) ? [picked] : [])];
  const sources = useQuery(ruleSourcesQuery).data ?? [];
  const [file, setFile] = useState<string | null>(null);
  const [link, setLink] = useState('');
  const [note, setNote] = useState('');
  const [source, setSource] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [kind, setKind] = useState<Kind>('vendor');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<DocRead | null>(null);
  const url = link.trim();
  const badLink = !!url && !isLink(url);
  const from = source === NEW ? newName.trim() : source;
  const ready = (!!file || (!!url && !badLink)) && !!from;
  const title = (done?.file?.title ?? files.find((f) => f.id === file)?.title ?? url) || 'the document';

  const go = async () => {
    if (!ready || !from) return;
    setBusy(true);
    const r = await pressOut<DocRead>('rules.readDocument', {
      ...(file ? { file } : { url }),
      ...(note.trim() ? { note: note.trim() } : {}),
      source: from,
      ...(source === NEW ? { kind } : {}),
    });
    setBusy(false);
    if (r?.out) setDone(r.out);
  };

  if (done) {
    const said =
      done.screening === 'hold'
        ? `Screening held ${title}. No rules are suggested from it.`
        : `${done.existing ? 'Already reading' : 'Reading'} ${title}.${done.truncated ? ' It is long, so only the start is read.' : ''}`;
    return (
      <Dialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Add document"
        description={said}
        actions={
          <>
            <Link className="verb" href={`/task/${encodeURIComponent(done.task_id)}`} onClick={onClose}>
              Open task
            </Link>
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </>
        }
      />
    );
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add document"
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void go()}>
            Read it
          </Button>
        </>
      }
    >
      <div className="rr-form">
        {!url && (
          <div onInput={(e) => setTyped((e.target as HTMLInputElement).value ?? '')}>
            <Combobox
              label="File"
              items={files.map((f) => ({ value: f.id, label: f.title || f.name || f.id }))}
              value={file}
              onValueChange={(v) => {
                setFile(v);
                setPicked(files.find((f) => f.id === v) ?? null);
              }}
              placeholder="Pick from Files"
            />
          </div>
        )}
        {!file && <Field label="Or a link" type="url" value={link} onValueChange={setLink} placeholder="https://" error={badLink ? 'A link starts with https://' : undefined} />}
        <Field label="Description" area value={note} onValueChange={setNote} placeholder="Optional" />
        <Select
          label="From"
          options={[...sources.map((x) => ({ value: x.id, label: x.name })), { value: NEW, label: 'New…' }]}
          value={source}
          onValueChange={setSource}
          placeholder="Pick one"
        />
        {source === NEW && (
          <>
            <Field label="Name" value={newName} onValueChange={setNewName} autoFocus />
            <Segmented label="Kind" options={KINDS} value={kind} onValueChange={setKind} />
          </>
        )}
      </div>
    </Dialog>
  );
}
