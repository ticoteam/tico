import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useState, type FocusEvent, type FormEvent, type KeyboardEvent } from 'react';
import { Ago } from '@/kit/Ago';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Table, Td, Th, Tr } from '@/kit/Table';
import { List, RecordPanel, Section, type PageState, type PageTab } from '@/pagekinds';
import { Face, Find, More, OwnerButton, press, Who } from './parts';
import { attributesQuery, cardQuery, contactThreadsQuery, contactsQuery, countsQuery, typesQuery, type Attribute, type Card, type ContactRow, type FactValue } from './queries';
import type { ContactsSearch } from './search';
import './library.css';

/* Contacts (records.js contactsPage, contactRec): people, organizations and places in one table, one kind at a time.
   A contact opens beside the list; each fact saves as soon as it is changed, and a refused change is taken back. */

const panelHref = (search: ContactsSearch, id: string, type?: string) => {
  const p = new URLSearchParams();
  const t = type ?? search.type;
  if (t) p.set('type', t);
  if (search.q && !type) p.set('q', search.q);
  p.set('panel', id);
  return `/library/contacts?${p.toString()}`;
};

/** A fact's value as words: one value, or several joined. */
export function shown(v: FactValue | FactValue[] | undefined): string {
  if (!v) return '';
  const one = (x: FactValue) => (x.value == null ? '' : typeof x.value === 'object' ? JSON.stringify(x.value) : String(x.value));
  return Array.isArray(v) ? v.map(one).filter(Boolean).join(', ') : one(v);
}

const EDITABLE = new Set(['text', 'string', 'email', 'phone', 'url', 'number', 'date', 'money']);

export function ContactsPage({ search }: { search: ContactsSearch }) {
  const type = search.type ?? 'person';
  const types = useQuery(typesQuery);
  const counts = useQuery(countsQuery);
  const list = useInfiniteQuery(contactsQuery(type, search.q ?? ''));
  const rows = list.data?.pages.flatMap((p) => p.rows) ?? [];
  const kinds = types.data ?? [];
  const kind = kinds.find((k) => k.key === type);
  const countOf = (key: string) => counts.data?.find((c) => c.type === key)?.count;

  let state: PageState = 'ready';
  if (list.isError) state = (list.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault';
  else if (!list.data) state = 'loading';
  else if (!rows.length && !search.q && !(counts.data ?? []).some((c) => c.count > 0)) state = 'empty';

  // People, organizations, places: one tab each (records.js contactsPage). People is the page with no `type`.
  const tabs: PageTab[] = kinds.map((k) => ({
    label: k.plural_name,
    to: '/library/contacts',
    search: k.key === 'person' ? undefined : { type: k.key },
    count: countOf(k.key),
    target: `tab:contacts:${k.key}`,
  }));

  return (
    <List
      title="Contacts"
      path="/library/contacts"
      className="lib"
      tabs={tabs.length > 1 ? tabs : undefined}
      actions={
        <>
          <Find label="Search contacts" value={search.q} onFind={(q) => void press('contacts.list', { type: search.type, q })} />
          <OwnerButton page="contacts" />
        </>
      }
      state={state}
      empty={{ title: 'No contacts yet' }}
      onRetry={() => void list.refetch()}
      sections={
        rows.length ? (
          <>
            <Table label={kind?.plural_name ?? 'Contacts'} stick>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Organization</Th>
                  <Th>From</Th>
                  <Th>Owner</Th>
                  <Th num>Last</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <ContactLine key={r.id} r={r} search={search} selected={r.id === search.panel} />
                ))}
              </tbody>
            </Table>
            {list.hasNextPage && <More onClick={() => void list.fetchNextPage()}>More</More>}
          </>
        ) : (
          <Empty title="Nothing matches" />
        )
      }
      panel={search.panel ? <ContactPanel id={search.panel} search={search} /> : undefined}
    />
  );
}

function ContactLine({ r, search, selected }: { r: ContactRow; search: ContactsSearch; selected: boolean }) {
  const owner = r.owner?.id ? `${r.owner.type === 'Bot' || r.owner.type === 'bot' ? 'bot' : 'human'}:${r.owner.id}` : null;
  return (
    <Tr selected={selected}>
      <Td>
        <Link className="who-cell" href={panelHref(search, r.id)} title={r.name} data-target={`contact:${r.id}`}>
          <Face actor={null} name={r.name} />
          <span>{r.name}</span>
        </Link>
      </Td>
      <Td>{r.organization?.name ?? ''}</Td>
      <Td>{(r.sources ?? []).join(' · ')}</Td>
      <Td>{owner && <Face actor={owner} name={r.owner?.name} />}</Td>
      <Td num>{r.last_activity_at ? <Ago at={r.last_activity_at} /> : null}</Td>
    </Tr>
  );
}

/* ---------------------------------------------------------------- one contact */

function ContactPanel({ id, search }: { id: string; search: ContactsSearch }) {
  const card = useQuery(cardQuery(id));
  const c = card.data;
  const attrs = useQuery({ ...attributesQuery(c?.type ?? ''), enabled: !!c });
  const close = () => void press('contacts.open', { contact: null });
  const save = (key: string) => (value: string) => void press('contacts.update', { contact: id, type: c?.type ?? 'person', key, value }, 'Not saved');

  return (
    <RecordPanel
      title={c?.name ?? 'Contact'}
      target={`contact:${id}`}
      lead={c && <Face actor={null} name={c.name} size={28} />}
      onClose={close}
      facts={
        card.isError ? (
          <Empty title="This contact did not load" icon="alert" />
        ) : (
          c && (
            <Facts label="About">
              <Fact label="Name" value={c.name} onSave={save('name')} />
              {c.organization && (
                <LabelValue label="Organization">
                  <Link href={panelHref(search, c.organization.id, 'organization')}>{c.organization.name}</Link>
                </LabelValue>
              )}
              {(attrs.data ?? []).map((a) => (
                <Fact key={a.key} label={a.name} value={shown(c.values[a.key])} onSave={editable(a) ? save(a.key) : undefined} />
              ))}
              {c.owner?.name && (
                <LabelValue label="Owner">
                  <Who actor={c.owner.id ? `${c.owner.type === 'Bot' || c.owner.type === 'bot' ? 'bot' : 'human'}:${c.owner.id}` : null} name={c.owner.name} className="fact-who" />
                </LabelValue>
              )}
              {(c.sources?.length ?? 0) > 0 && <LabelValue label="From">{c.sources?.join(' · ')}</LabelValue>}
            </Facts>
          )
        )
      }
      sections={c && <CardSections c={c} search={search} />}
      foot={c && <NoteBox id={id} />}
    />
  );
}

const editable = (a: Attribute) => !a.multiple && !a.sensitive && EDITABLE.has(a.value_type);

/** A fact that changes in place: press it, type, and it saves when the field is left or Enter is pressed. */
function Fact({ label, value, onSave }: { label: string; value: string; onSave?: (value: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (!onSave || !editing)
    return (
      <LabelValue
        label={label}
        onEdit={
          onSave &&
          (() => {
            setDraft(value);
            setEditing(true);
          })
        }
      >
        {value}
      </LabelValue>
    );
  const done = () => {
    setEditing(false);
    if (draft.trim() !== value.trim()) onSave(draft);
  };
  return (
    <form
      className="fact fact-edit"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        done();
      }}
      onBlur={(e: FocusEvent<HTMLFormElement>) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) done();
      }}
      onKeyDown={(e: KeyboardEvent) => {
        if (e.key === 'Escape') setEditing(false);
      }}
    >
      <Field label={label} value={draft} onValueChange={setDraft} autoFocus wide />
    </form>
  );
}

function CardSections({ c, search }: { c: Card; search: ContactsSearch }) {
  return (
    <>
      {c.summary && <p className="rec-summary t-read">{c.summary}</p>}
      {c.links.length > 0 && (
        <Section name="Links" count={c.links.length}>
          {c.links.map((l) => (
            <Link key={l.id} className="row lrow" href={panelHref(search, l.other_id, l.other_type)} title={l.other_name}>
              <span className="lead">
                <Icon name="link" />
              </span>
              <span className="row-title">{l.other_name}</span>
              <span className="row-ctx">{l.link_name}</span>
            </Link>
          ))}
        </Section>
      )}
      <Threads words={c.primary_email ?? c.name} />
      {c.notes.length > 0 && (
        <Section name="Notes" count={c.notes.length}>
          {c.notes.map((n) => (
            <div key={n.id} className="note">
              <p className="t-read">{n.body}</p>
              <div className="note-by t-small">
                {n.audience === 'me' ? <Icon name="lock" /> : <Face actor={`${n.author_type === 'bot' || n.author_type === 'Bot' ? 'bot' : 'human'}:${n.author_id}`} />}
                <span>{n.audience === 'me' ? 'Only me' : ''}</span>
                <Ago at={n.created_at} />
              </div>
            </div>
          ))}
        </Section>
      )}
    </>
  );
}

function Threads({ words }: { words: string }) {
  const found = useQuery(contactThreadsQuery(words));
  const rows = found.data ?? [];
  if (!rows.length) return null;
  return (
    <Section name="Threads" count={rows.length}>
      {rows.map((t) => (
        <Link key={t.thread_id} className="row lrow" href={`/threads/${encodeURIComponent(t.thread_id)}`} title={t.title ?? t.excerpt}>
          <span className="lead">
            <Icon name="threads" />
          </span>
          <span className="row-title">{t.title ?? t.excerpt}</span>
          {t.created_at && <Ago at={t.created_at} className="row-age" />}
        </Link>
      ))}
    </Section>
  );
}

function NoteBox({ id }: { id: string }) {
  const [text, setText] = useState('');
  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = text.trim();
    if (!body) return;
    setText('');
    if (!(await press('contacts.note', { contact: id, body }, 'Note not added'))) setText(body);
  };
  return (
    <form className="note-box" onSubmit={(e) => void send(e)}>
      <Field label="Add a note" labelHidden value={text} onValueChange={setText} placeholder="Add a note" wide />
      <IconButton icon="send" label="Add the note" type="submit" disabled={!text.trim()} />
    </form>
  );
}
