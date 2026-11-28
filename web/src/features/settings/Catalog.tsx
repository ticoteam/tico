import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Link as To } from '@/kit/Link';
import { Select } from '@/kit/Select';
import { Section } from '@/pagekinds';
import { act } from './parts';
import { FIELD_KINDS, contactTypeQuery, contactTypesQuery, type FieldKind } from './queries';
import { AskSection } from './TasksMore';

/* Tasks (what agents may ask) and Contacts (the types and their fields): the two catalogs
   Settings edits. What the mock draws that the server cannot keep yet
   (new task defaults, sharing) is left out. */

const KIND_NAME: Record<string, string> = {
  text: 'Text',
  number: 'Number',
  currency: 'Money',
  date: 'Date',
  timestamp: 'Time',
  checkbox: 'Yes or no',
  select: 'Choice',
  status: 'Status',
  email: 'Email',
  phone: 'Phone',
  domain: 'Domain',
  url: 'Link',
  personal_name: 'Name',
  rating: 'Rating',
  actor: 'Person or agent',
};

// ---------------------------------------------------------------- tasks

export function Tasks() {
  return <AskSection />;
}

// ---------------------------------------------------------------- contacts

function AddField({ type, typeName }: { type: string; typeName: string }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<FieldKind>('text');
  const [about, setAbout] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await act('settings.addField', { type, name: name.trim(), kind, description: about.trim() });
    setBusy(false);
    if (!ok) return;
    setOpen(false);
    setName('');
    setAbout('');
  };
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={`Add a field to ${typeName}`}
      trigger={<Button icon="plus">Add field</Button>}
      actions={
        <>
          <Button variant="quiet" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!name.trim() || !about.trim()} onClick={() => save()}>
            Add
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="Name" value={name} onValueChange={setName} autoComplete="off" />
        <Select label="Kind" value={kind} onValueChange={setKind} options={FIELD_KINDS.map((k) => ({ value: k, label: KIND_NAME[k] ?? k }))} />
        <Field label="What it holds" value={about} onValueChange={setAbout} autoComplete="off" />
      </div>
    </Dialog>
  );
}

function Fields({ type, typeName }: { type: string; typeName: string }) {
  const fields = useQuery(contactTypeQuery(type));
  const list = fields.data?.attributes ?? [];
  return (
    <Section name={`${typeName} fields`} count={list.length} actions={<AddField type={type} typeName={typeName} />} target={`contact-type:${type}`}>
      {fields.data && list.length === 0 ? (
        <Empty title="No fields yet" />
      ) : (
        <>
          <div className="tbl-head g-cfield" aria-hidden="true">
            <span />
            <span>Kind</span>
            <span>Kept by</span>
          </div>
          {list.map((f) => (
            <div key={f.key} className="row g-cfield" title={f.name}>
              <span className="row-title">{f.name}</span>
              <span className="row-cell">{KIND_NAME[f.value_type] ?? f.value_type}</span>
              <span className="row-cell">
                {f.owner === 'tico' ? 'Tico' : 'A connection'}
                {f.owner !== 'tico' && <Icon name="lock" />}
              </span>
            </div>
          ))}
        </>
      )}
    </Section>
  );
}

export function Contacts({ open }: { open?: string }) {
  const types = useQuery(contactTypesQuery);
  const list = types.data ?? [];
  if (!types.data) return null;
  const opened = list.find((t) => t.key === open);
  return (
    <>
      <Section name="Types" count={list.length} target="setting:contact-types">
        {list.length === 0 ? (
          <Empty title="No types yet" />
        ) : (
          <>
            <div className="tbl-head g-ctype" aria-hidden="true">
              <span />
              <span className="is-num">Contacts</span>
            </div>
            {list.map((t) => (
              <To
                key={t.key}
                href={t.key === open ? '/settings/contacts' : `/settings/contacts?open=${encodeURIComponent(t.key)}`}
                className="row g-ctype"
                aria-current={t.key === open ? 'true' : undefined}
                title={t.name}
                data-target={`contact-type:${t.key}`}
              >
                <span className="row-title">{t.name}</span>
                <span className="row-fig">{t.count.toLocaleString('en-US') + (t.approximate ? '+' : '')}</span>
              </To>
            ))}
          </>
        )}
      </Section>
      {opened && <Fields type={opened.key} typeName={opened.name} />}
    </>
  );
}
