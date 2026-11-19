import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { useActors, type Actor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Combobox } from '@/kit/Combobox';
import { Dialog } from '@/kit/Dialog';
import { Field, Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { List, Section } from '@/pagekinds';
import { Dot } from '@/tico/dot';
import { Face, press, useMe } from './people';
import { learnedQuery, viewersQuery, type LearnedEntry, type Viewers } from './queries';
import './threads.css';

const MAX = 280;
const has = (text: string, q: string) => !q || text.toLowerCase().includes(q.toLowerCase());

/**
 * Learned: what Tico keeps about the person (and, for the owner, about the company), one sentence a line, and the
 * agents that may read the person's chat with Tico. The face on a line is who it came from: you, or Tico in a chat.
 */
export function LearnedPage() {
  const learned = useQuery(learnedQuery);
  const viewers = useQuery(viewersQuery);
  const [words, setWords] = useState('');
  const [editing, setEditing] = useState<LearnedEntry | null>(null);
  const [deleting, setDeleting] = useState<LearnedEntry | null>(null);
  const [adding, setAdding] = useState(false);
  const q = words.trim();
  const d = learned.data;
  const personal = (d?.personal ?? []).filter((e) => has(e.text, q));
  const company = d?.company;
  const companyRows = (company?.entries ?? []).filter((e) => has(e.text, q));
  const vision = company?.vision?.trim() && has(company.vision, q) ? company.vision.trim() : null;
  const goals = (company?.priorities ?? []).filter((g) => has(g.title, q));
  const readers = (viewers.data?.bots ?? []).filter((b) => has(b.name ?? b.slug ?? b.bot, q));
  const nothing = !d?.personal.length && !company?.entries.length && !vision && !goals.length && !viewers.data?.bots.length;
  const state = learned.isPending ? 'loading' : learned.isError ? 'fault' : nothing ? 'empty' : 'ready';
  const row = (e: LearnedEntry) => <Line key={e.id} e={e} onEdit={() => setEditing(e)} onDelete={() => setDeleting(e)} />;

  return (
    <>
    <List
      title="Learned"
      path="/learned"
      lead={<Dot still size={28} />}
      search={<Search label="Search" placeholder="Search" value={words} onValueChange={setWords} />}
      state={state}
      empty={{ title: 'Nothing learned yet' }}
      onRetry={() => void learned.refetch()}
      className="lr-page"
      sections={
        <div className="lr-body">
          {(personal.length > 0 || !q) && (
            <div className="lr-sec">
              <Section name="About you" count={personal.length}>
                {personal.map(row)}
              </Section>
            </div>
          )}
          {company && (companyRows.length > 0 || vision || goals.length > 0 || !q) && (
            <div className="lr-sec">
              <Section name="About the company" count={companyRows.length + (vision ? 1 : 0) + goals.length}>
                {companyRows.map(row)}
                {vision && <LinkLine text={vision} />}
                {goals.map((g) => (
                  <LinkLine key={g.id} text={g.title} goal />
                ))}
              </Section>
            </div>
          )}
          {viewers.data && (readers.length > 0 || !q) && (
            <div className="lr-sec">
              <Section
                name="Can read Tico's chat"
                count={readers.length}
                actions={<IconButton icon="plus" label="Add an agent" size="sm" onClick={() => setAdding(true)} />}
              >
                {readers.map((b) => (
                  <Reader key={b.bot} b={b} />
                ))}
              </Section>
            </div>
          )}
        </div>
      }
    />
    {editing && <EditLine e={editing} onClose={() => setEditing(null)} />}
    {deleting && <DeleteLine e={deleting} onClose={() => setDeleting(null)} />}
    {adding && <AddReader have={viewers.data} onClose={() => setAdding(false)} />}
    </>
  );
}

function Line({ e, onEdit, onDelete }: { e: LearnedEntry; onEdit: () => void; onDelete: () => void }) {
  const me = useMe();
  return (
    <div className="lr-row" data-target={`learned:${e.id}`}>
      <span className="lr-text">{e.text}</span>
      {e.taught_in_chat || !me ? <Dot still size={22} /> : <Face actor={me} />}
      <Ago at={e.updated_at ?? e.created_at} className="row-age" />
      <span className="lr-more">
        <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>
          <MenuItem label="Edit" icon="edit" onSelect={onEdit} />
          <MenuItem label="Delete" icon="close" onSelect={onDelete} />
        </Menu>
      </span>
    </div>
  );
}

/** A line the company layer takes from elsewhere: the strategy's vision, a live company goal. */
function LinkLine({ text, goal = false }: { text: string; goal?: boolean }) {
  const label = goal ? 'Goals' : 'Strategy';
  const inner = (
    <>
      <span className="lr-text">{text}</span>
      <span className="tl-kind">
        <Icon name="link" />
      </span>
      <span className="row-age">{label}</span>
      <span />
    </>
  );
  return goal ? (
    <Link className="lr-row" to="/company/strategy/$tab" params={{ tab: 'goals' }} title="Open Goals">
      {inner}
    </Link>
  ) : (
    <Link className="lr-row" to="/company/strategy" title="Open Strategy">
      {inner}
    </Link>
  );
}

function Reader({ b }: { b: Viewers['bots'][number] }) {
  const slug = b.slug ?? b.bot;
  const name = b.name ?? slug;
  return (
    <div className="lr-row is-who">
      <Face actor={`bot:${slug}`} name={name} />
      <span className="lr-text">{name}</span>
      <span />
      <span className="lr-more">
        <Menu trigger={<IconButton icon="more" label="More" size="sm" />}>
          <MenuItem label="Stop reading" icon="close" onSelect={() => void press('learned.removeReader', { agent: slug })} />
        </Menu>
      </span>
    </div>
  );
}

function EditLine({ e, onClose }: { e: LearnedEntry; onClose: () => void }) {
  const [text, setText] = useState(e.text);
  const [busy, setBusy] = useState(false);
  const words = text.trim();
  const save = async () => {
    setBusy(true);
    const ok = await press('learned.edit', { entry: e.id, text: words });
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Edit line"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!words || words.length > MAX || words === e.text} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Line" labelHidden area value={text} onValueChange={setText} error={words.length > MAX ? 'Too long' : undefined} autoFocus />
    </Dialog>
  );
}

function DeleteLine({ e, onClose }: { e: LearnedEntry; onClose: () => void }) {
  return (
    <Dialog
      open
      alert
      onOpenChange={(open) => !open && onClose()}
      title="Delete this line?"
      description="Tico stops using it from the next reply."
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              onClose();
              void press('learned.forget', { entry: e.id });
            }}
          >
            Delete
          </Button>
        </>
      }
    />
  );
}

function AddReader({ have, onClose }: { have: Viewers | undefined; onClose: () => void }) {
  const actors = useActors();
  const [picked, setPicked] = useState<Actor | null>(null);
  const taken = new Set((have?.bots ?? []).flatMap((b) => [`bot:${b.bot}`, `bot:${b.slug ?? ''}`]));
  const agents = actors.filter((a) => a.kind === 'agent' && !taken.has(a.id));
  const add = async () => {
    if (!picked) return;
    if (await press('learned.addReader', { agent: picked.slug ?? picked.id.slice(4) })) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Add an agent"
      description="It can read your chat with Tico."
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!picked} onClick={() => void add()}>
            Add
          </Button>
        </>
      }
    >
      <Combobox
        label="Agent"
        labelHidden
        placeholder="Name an agent"
        items={agents.map((a) => ({ value: a.id, label: a.name, lead: <Avatar kind="agent" name={a.name} hue={a.hue} size={16} decorative /> }))}
        value={picked?.id ?? null}
        onValueChange={(v) => setPicked(agents.find((a) => a.id === v) ?? null)}
      />
    </Dialog>
  );
}
