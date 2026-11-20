import { useQuery, useSuspenseInfiniteQuery } from '@tanstack/react-query';
import { useState, type FocusEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { FilterRow, List, RecordPanel, Section, type PageState, type PageTab } from '@/pagekinds';
import { ContactRules, PlaceFacts, PlaceSections, useUs } from './ContactPlace';
import { Face, Find, Foot, OwnerButton, press, useGo, Who } from './parts';
import {
  attributesQuery,
  cardQuery,
  contactThreadsQuery,
  contactsQuery,
  countsQuery,
  factHistoryQuery,
  typesQuery,
  type Attribute,
  type Card,
  type ContactRow,
  type FactPast,
  type FactValue,
} from './queries';
import type { ContactsSearch } from './search';
import './library.css';

/* Contacts (records.js contactsPage, contactRec): people, organizations and places in one table, one kind at a time.
   A contact opens beside the list; each fact saves as soon as it is changed, and a refused change is taken back. */

const panelHref = (search: ContactsSearch, id: string, type?: string) => {
  const p = new URLSearchParams();
  const t = type ?? search.type;
  if (t) p.set('type', t);
  if (search.q && !type) p.set('q', search.q);
  if (search.link && !type) p.set('link', search.link);
  p.set('panel', id);
  return `/library/contacts?${p.toString()}`;
};

/** A fact's value as words: one value, or several joined. */
export function shown(v: FactValue | FactValue[] | undefined): string {
  if (!v) return '';
  const one = (x: FactValue) => (x.value == null ? '' : typeof x.value === 'object' ? JSON.stringify(x.value) : String(x.value));
  return Array.isArray(v) ? v.map(one).filter(Boolean).join(', ') : one(v);
}

const FIRST = ['person', 'organization', 'place', 'product'];

const EDITABLE = new Set(['text', 'string', 'email', 'phone', 'url', 'number', 'date', 'money']);

// How a link to us reads as a relationship (directory.ts DIR_RELATIONS, backend/entities/schema.py link types).
export const RELATIONS: Record<string, string> = {
  customer_of: 'Customer',
  vendor_to: 'Vendor',
  partner_of: 'Partner',
  invests_in: 'Investor',
  advises: 'Advisor',
  represents: 'Represents',
  regulates: 'Regulator',
  sells_to: 'Reseller',
  competes_with: 'Competitor',
};
// Where a contact is known from (directory.ts DIR_SOURCES: entity_sources.source_type).
const SOURCES: Record<string, string> = {
  mailbox: 'Mail',
  meeting: 'Meetings',
  finance_book: 'Finance',
  note_me: 'My note',
  market: 'Market',
  hub: 'Tico',
  system: 'Tico',
  counsel: 'Rules Owner',
  company_page: 'Company page',
  places: 'Shared',
};
const words = (map: Record<string, string>) => (key: string) => map[key] ?? key.replace(/_/g, ' ');
export const relation = words(RELATIONS);
export const fromWhere = (keys: readonly string[] | undefined) => [...new Set((keys ?? []).map(words(SOURCES)))].join(' · ');
/** A person with no link of their own reads as their organization's (a vendor's account manager is a vendor). */
const relationsOf = (r: ContactRow) => (r.links_to_us?.length ? r.links_to_us : r.type === 'person' ? (r.organization?.links_to_us ?? []) : []);

export function ContactsPage({ search }: { search: ContactsSearch }) {
  const type = search.type ?? 'person';
  const types = useQuery(typesQuery);
  const counts = useQuery(countsQuery);
  const list = useSuspenseInfiniteQuery(contactsQuery(type, search.q ?? '', search.link ?? ''));
  const rows = list.data.pages.flatMap((p) => p.rows);
  const kinds = types.data ?? [];
  const kind = kinds.find((k) => k.key === type);
  const countOf = (key: string) => counts.data?.find((c) => c.type === key)?.count;

  const state: PageState = !rows.length && !search.q && !search.link ? 'empty' : 'ready';
  // An empty tab beside full ones names its own kind; with none at all, the page says so.
  const none = !(counts.data ?? []).some((c) => c.count > 0);
  const emptyTitle = none || !kind ? 'No contacts yet' : `No ${kind.plural_name.toLowerCase()} yet`;

  const listed = (to: { type?: string; q?: string; link?: string }) => void press('contacts.list', to);
  const [adding, setAdding] = useState(false);
  const add = (
    <Button variant="primary" onClick={() => setAdding(true)} data-target="contacts:add">
      Add
    </Button>
  );

  // People, organizations, places: one tab each (records.js contactsPage). People is the page with no `type`.
  // People first, then organizations, places and products (records.js), then any other kind the company keeps.
  const rank = (key: string) => (FIRST.indexOf(key) + 1 || FIRST.length + 1);
  const tabs: PageTab[] = [...kinds].sort((a, b) => rank(a.key) - rank(b.key)).map((k) => ({
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
      search={<Find label="Search contacts" value={search.q} onFind={(q) => listed({ type: search.type, q, link: search.link })} />}
      actions={
        <>
          {(type === 'person' || type === 'organization') && (
            <Menu trigger={<IconButton icon="filter" label="Relationship" on={!!search.link} data-phone />}>
              <MenuItem label="All" on={!search.link} onSelect={() => listed({ type: search.type, q: search.q })} />
              {Object.keys(RELATIONS).map((k) => (
                <MenuItem key={k} label={relation(k)} on={k === search.link} onSelect={() => listed({ type: search.type, q: search.q, link: k })} />
              ))}
            </Menu>
          )}
          <OwnerButton page="contacts" />
          {state !== 'empty' && add}
          {adding && <AddContact type={type} kind={kind?.name ?? 'contact'} onClose={() => setAdding(false)} />}
        </>
      }
      filter={search.link && <FilterRow filters={[{ label: relation(search.link), onClear: () => listed({ type: search.type, q: search.q }) }]} />}
      state={state}
      empty={{ title: emptyTitle, action: add }}
      sections={
        rows.length ? (
          <VirtualList
            className="lib-vl"
            label={kind?.plural_name ?? 'Contacts'}
            items={rows}
            getKey={(r) => r.id}
            estimateSize={36}
            measure
            onReachEnd={() => list.hasNextPage && !list.isFetchingNextPage && void list.fetchNextPage()}
            renderItem={(r) => <ContactLine r={r} search={search} selected={r.id === search.panel} />}
          />
        ) : (
          <Empty title="Nothing matches" />
        )
      }
      panel={search.panel ? <ContactPanel key={search.panel} id={search.panel} search={search} /> : undefined}
    />
  );
}

/** Name, then organization (a person) or domain (an organization), relationship, owner and last touch. On a phone the
    second column sits under the name and the rest give way to the age. */
function ContactLine({ r, search, selected }: { r: ContactRow; search: ContactsSearch; selected: boolean }) {
  return (
    <Row className="lrow crow" title={r.name} href={panelHref(search, r.id)} selected={selected} target={`contact:${r.id}`}>
      <span className="lead">
        <Face actor={null} name={r.name} />
      </span>
      <span className="row-title">{r.name}</span>
      <span className="row-ctx crow-of">{r.type === 'organization' ? (r.primary_domain ?? '') : (r.organization?.name ?? '')}</span>
      <span className="row-ctx crow-rel">{relationsOf(r).map(relation).join(', ')}</span>
      <span className="crow-who">{r.owner && <Face actor={r.owner} />}</span>
      {r.last_activity_at ? <Ago at={r.last_activity_at} className="row-age" /> : <span className="row-age" />}
    </Row>
  );
}

/** A new contact of the tab's kind, by its name; it opens beside the list, where the rest of its facts are typed in. */
function AddContact({ type, kind, onClose }: { type: string; kind: string; onClose: () => void }) {
  const [name, setName] = useState('');
  const [busy, go] = useGo(onClose);
  const add = () => void go('contacts.add', { type, name: name.trim() }, 'Not added');
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add ${kind.toLowerCase()}`}
      actions={<Foot onClose={onClose} label="Add" busy={busy} ready={!!name.trim()} onGo={add} />}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) add();
        }}
      >
        <Field label="Name" value={name} onValueChange={setName} autoComplete="off" autoFocus />
      </form>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- one contact */

function ContactPanel({ id, search }: { id: string; search: ContactsSearch }) {
  const card = useQuery(cardQuery(id));
  const c = card.data;
  const attrs = useQuery({ ...attributesQuery(c?.type ?? ''), enabled: !!c });
  const close = () => void press('contacts.open', { contact: null });
  const save = (key: string) => (value: string) => void press('contacts.update', { contact: id, type: c?.type ?? 'person', key, value }, 'Not saved');
  const [past, setPast] = useState<Attribute | null>(null);
  const us = useUs(c);
  const href = (other: string, type?: string) => panelHref(search, other, type);
  const place = c?.type === 'place';

  return (
    <RecordPanel
      title={c?.name ?? 'Contact'}
      target={`contact:${id}`}
      lead={c && <Face actor={null} name={c.name} size={28} />}
      onClose={close}
      facts={
        card.isPending ? (
          <SkeletonRows rows={6} label="Loading the contact" />
        ) : card.isError ? (
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
                <Fact
                  key={a.key}
                  label={a.name}
                  value={shown(c.values[a.key])}
                  onSave={editable(a) ? save(a.key) : undefined}
                  onHistory={() => setPast(a)}
                />
              ))}
              {place && <PlaceFacts c={c} us={us} href={href} />}
              {relationsOf(c).length > 0 && <LabelValue label="Relationship">{relationsOf(c).map(relation).join(', ')}</LabelValue>}
              {c.owner && (
                <LabelValue label="Owner">
                  <Who actor={c.owner} className="fact-who" />
                </LabelValue>
              )}
              {(c.sources?.length ?? 0) > 0 && <LabelValue label="From">{fromWhere(c.sources)}</LabelValue>}
            </Facts>
          )
        )
      }
      sections={
        c && (
          <>
            {past && <FactHistory contact={id} field={past} onClose={() => setPast(null)} />}
            <CardSections c={c} search={search} place={place && <PlaceSections c={c} us={us} href={href} />} />
          </>
        )
      }
      foot={c && <NoteBox id={id} />}
    />
  );
}

const editable = (a: Attribute) => !a.multiple && !a.sensitive && EDITABLE.has(a.value_type);

/** A fact that changes in place: press it, type, and it saves when the field is left or Enter is pressed. Its ⋯ holds
   History. */
function Fact({ label, value, onSave, onHistory }: { label: string; value: string; onSave?: (value: string) => void; onHistory?: () => void }) {
  if (!onHistory) return <FactLine label={label} value={value} onSave={onSave} />;
  return (
    <div className="fact-more">
      <FactLine label={label} value={value} onSave={onSave} />
      <Menu trigger={<IconButton icon="more" label={`More on ${label}`} />}>
        <MenuItem label="History" onSelect={onHistory} />
      </Menu>
    </div>
  );
}

function FactLine({ label, value, onSave }: { label: string; value: string; onSave?: (value: string) => void }) {
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

/** The summary, a place's rules, then the links: as one list, or a place's own (Bodies, Inside, Located here). */
function CardSections({ c, search, place }: { c: Card; search: ContactsSearch; place?: ReactNode }) {
  return (
    <>
      {c.summary && <p className="rec-summary t-read">{c.summary}</p>}
      <ContactRules id={c.id} />
      {place}
      <Links links={place ? [] : c.links} search={search} words={c.primary_email ?? c.name} />
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

/** What a contact is linked to, and the threads that name it, in one section (a place's links are its own sections). */
function Links({ links, search, words }: { links: Card['links']; search: ContactsSearch; words: string }) {
  const threads = useQuery(contactThreadsQuery(words)).data ?? [];
  const n = links.length + threads.length;
  if (!n) return null;
  return (
    <Section name="Links" count={n}>
      {links.map((l) => (
        <Link key={l.id} className="row lrow" href={panelHref(search, l.other_id, l.other_type)} title={l.other_name}>
          <span className="lead">
            <Icon name="link" />
          </span>
          <span className="row-title">{l.other_name}</span>
          <span className="row-ctx">{l.link_name}</span>
        </Link>
      ))}
      {threads.map((t) => (
        <Link key={t.thread_id} className="row lrow" href={`/threads/${encodeURIComponent(t.thread_id)}`} title={t.title ?? t.excerpt}>
          <span className="lead">
            <Icon name="threads" />
          </span>
          <span className="row-title">{t.title ?? t.excerpt}</span>
          <span className="row-ctx">Thread</span>
        </Link>
      ))}
    </Section>
  );
}

/** A note for the company, or (the lock pressed) for the person alone (directory-entity.ts: Company / Only me). */
function NoteBox({ id }: { id: string }) {
  const [text, setText] = useState('');
  const [mine, setMine] = useState(false);
  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = text.trim();
    if (!body) return;
    setText('');
    if (!(await press('contacts.note', { contact: id, body, audience: mine ? 'me' : 'company' }, 'Note not added'))) setText(body);
  };
  return (
    <form className="note-box" onSubmit={(e) => void send(e)}>
      <Field label="Add a note" labelHidden value={text} onValueChange={setText} placeholder="Add a note" wide />
      <IconButton icon="lock" label="Only me" quiet on={mine} onClick={() => setMine((m) => !m)} />
      <IconButton icon="send" label="Add the note" type="submit" disabled={!text.trim()} />
    </form>
  );
}

/** Where a past value came from: a teammate when the source names one, else the kind of source. */
const pastFrom = (h: FactPast) => (h.source_id && /^(bot|human):/.test(h.source_id) ? h.source_id : null);

/** A field's past values, newest first, at the top of the panel. Read-only. */
function FactHistory({ contact, field, onClose }: { contact: string; field: Attribute; onClose: () => void }) {
  const rows = useQuery(factHistoryQuery(contact, field.key));
  return (
    <Section name={`${field.name} history`} count={rows.data?.length} actions={<IconButton icon="close" label="Close" onClick={onClose} />}>
      {rows.isError && <Empty title="This did not load" icon="alert" />}
      {rows.data && !rows.data.length && <Empty title="No past values" />}
      {rows.data?.map((h) => (
        <div key={h.id} className="row lrow hrow">
          <span className="lead">{pastFrom(h) ? <Face actor={pastFrom(h)} /> : null}</span>
          <span className="row-title">{shown({ value: h.value, source_type: h.source_type, source_id: h.source_id, updated_at: '' })}</span>
          <span className="row-ctx">{pastFrom(h) ? '' : h.source_type}</span>
          {h.active_until ? <Ago at={h.active_until} className="row-age" /> : <span className="row-age" />}
        </div>
      ))}
    </Section>
  );
}
