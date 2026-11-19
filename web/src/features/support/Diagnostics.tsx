import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import { diagnosticsQuery, type DiagnosticsBundle } from './queries';

/** What goes with the next message: a preview the person read, and their edits to it (checked again on send). */
export interface Attached {
  bundle: DiagnosticsBundle;
  hours: number;
  edited?: string;
}

const HOURS = [
  { value: '0', label: 'None' },
  { value: '24', label: '1 day' },
  { value: '168', label: '7 days' },
  { value: '336', label: '14 days' },
] as const;

const kb = (bytes: number) => `${Math.max(1, Math.ceil(bytes / 1024))} KB`;

/** The attachment's parts a person may leave out: every top-level key but its own header. */
const HEADER = ['format', 'created', 'capture'];
const parse = (text: string): Record<string, unknown> | null => {
  try {
    const v: unknown = JSON.parse(text);
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
};

export interface DiagnosticsProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Already attached: shown as it was, with its edits. */
  attached: Attached | null;
  onAttach: (a: Attached) => void;
}

/** Read diagnostics before they go: exactly the attachment, the computers' performance window, and the text to edit.
    Nothing is sent here. */
export function Diagnostics({ open, onOpenChange, attached, onAttach }: DiagnosticsProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Diagnostics" className="sup-diag" description="Exactly what goes with your message. Secrets are removed again before it is sent.">
      {open && <Review attached={attached} onAttach={(a) => (onAttach(a), onOpenChange(false))} onCancel={() => onOpenChange(false)} />}
    </Dialog>
  );
}

function Review({ attached, onAttach, onCancel }: { attached: Attached | null; onAttach: (a: Attached) => void; onCancel: () => void }) {
  const [hours, setHours] = useState(attached?.hours ?? 24);
  // Refresh drops what was attached and its edits for a new capture.
  const [again, setAgain] = useState(false);
  const keep = !again && attached && attached.hours === hours ? attached : null;
  const fresh = useQuery({ ...diagnosticsQuery(hours), enabled: !keep });
  const bundle = keep?.bundle ?? fresh.data;
  const [text, setText] = useState<string | null>(keep ? (attached?.edited ?? null) : null);
  const shown = text ?? bundle?.text ?? '';
  const edited = bundle && text !== null && text !== bundle.text ? text : undefined;
  const base = bundle ? parse(bundle.text) : null;
  const now = parse(shown);
  const sections = base ? Object.keys(base).filter((k) => !HEADER.includes(k)) : [];
  const toggle = (key: string, on: boolean) => {
    if (!base || !now) return;
    const next = { ...now };
    if (on) next[key] = base[key];
    else delete next[key];
    setText(JSON.stringify(next, null, 2));
  };
  const refresh = () => {
    setText(null);
    if (keep) setAgain(true);
    else void fresh.refetch();
  };
  return (
    <>
      <Select
        label="Computer performance"
        options={HOURS}
        value={String(hours) as (typeof HOURS)[number]['value']}
        onValueChange={(v) => {
          setHours(Number(v));
          setText(null);
        }}
      />
      {sections.length > 0 && (
        <div className="sup-secs" role="group" aria-label="Sections">
          {sections.map((k) => (
            <Checkbox key={k} label={k} checked={!!now && k in now} disabled={!now} onCheckedChange={(on) => toggle(k, on)} />
          ))}
        </div>
      )}
      <Field label="Diagnostics" labelHidden area value={shown} onValueChange={setText} disabled={!bundle} />
      <p className="sup-note" role="status">
        {fresh.isError && !keep ? 'Diagnostics did not load.' : bundle ? (edited !== undefined ? 'Edited. Checked again on send.' : kb(bundle.bytes)) : 'Loading'}
      </p>
      <div className="modal-row">
        <Button variant="quiet" disabled={fresh.isFetching} onClick={refresh}>
          Refresh
        </Button>
        <span className="grow" />
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" disabled={!bundle} onClick={() => bundle && onAttach({ bundle, hours, ...(edited !== undefined && { edited }) })}>
          Attach
        </Button>
      </div>
    </>
  );
}
