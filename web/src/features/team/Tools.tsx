import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field, SettledSearch } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { ReadFault, Section } from '@/pagekinds';
import { agentToolsQuery, findToolsQuery, readable, toolRows, type AgentTool } from './queries';

/* Its Tools (backend tool guides): the software it lists, may use or used in the last 30 days. Each row is the tool's
   name, what the agent uses it for, how a run gets it, and its uses in 30 days; a mark when the agent built it. Add
   searches the company's registry; listing a tool gives no access, which stays the tool's own. */

const toolHref = (t: { slug: string; origin: string }) => `/library/tools?${new URLSearchParams({ tool: `${t.origin === 'built' ? 'built' : 'vendor'}:${t.slug}` }).toString()}`;

export function ToolsSection({ slug, name, manage }: { slug: string; name: string; manage: boolean }) {
  const q = useInfiniteQuery(agentToolsQuery(slug));
  const [adding, setAdding] = useState(false);
  const rows = toolRows(readable(q));
  // None yet is no section: Add tool is in the page's ⋯ too.
  if (!rows.length) return null;
  return (
    <Section
      name="Tools"
      count={rows.length}
      target={`tools:${slug}`}
      actions={manage && <IconButton icon="plus" size="sm" quiet label="Add tool" onClick={() => setAdding(true)} />}
    >
      {rows.map((t) => (
        <ToolRow key={t.slug} slug={slug} name={name} t={t} manage={manage} />
      ))}
      {q.hasNextPage && (
        <button className="verb" type="button" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
          More
        </button>
      )}
      {adding && <AddToolDialog slug={slug} name={name} onClose={() => setAdding(false)} />}
    </Section>
  );
}

const ACCESS: Record<AgentTool['access'], [string, 'lock' | 'keys'] | null> = {
  proxy: ['Tico holds the key', 'lock'],
  key: ['Its runs get the key', 'keys'],
  none: null,
};

function ToolRow({ slug, name, t, manage }: { slug: string; name: string; t: AgentTool; manage: boolean }) {
  const remove = useAction('agents.removeTool');
  const access = ACCESS[t.access];
  return (
    <div className="row task tool-row" title={t.name} data-target={`tool:${slug}:${t.slug}`}>
      <span className="row-mark">{t.built && <Icon name="spark" label={`Built by ${name}`} />}</span>
      <Link className="row-title" href={toolHref(t)}>
        {t.name}
      </Link>
      <span className="row-pills">
        {t.note && <span className="row-ctx">{t.note}</span>}
        {access && (
          <span className="tool-access" title={access[0]}>
            <Icon name={access[1]} label={access[0]} />
          </span>
        )}
      </span>
      {manage && t.listed ? (
        <IconButton
          icon="close"
          size="sm"
          quiet
          label={`Remove ${t.name}`}
          onClick={() => remove.run({ slug, tool: t.slug })}
        />
      ) : (
        <span className="av-gap" />
      )}
      <span className="row-age" title="Uses in 30 days">
        {t.uses || ''}
      </span>
    </div>
  );
}

/** Add tool: find one in the registry by name, say what it is for, and list it. */
export function AddToolDialog({ slug, name, onClose }: { slug: string; name: string; onClose: () => void }) {
  const add = useAction('agents.addTool');
  const has = new Set(toolRows(readable(useInfiniteQuery(agentToolsQuery(slug)))).filter((r) => r.listed).map((r) => r.slug));
  const [words, setWords] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const found = useInfiniteQuery({ ...findToolsQuery(words.trim()), placeholderData: keepPreviousData });
  const rows = (found.data?.pages.flatMap((p) => p.tool_guides ?? []) ?? []).filter((t) => !has.has(t.slug));
  // Only a tool the search still shows counts as chosen: one the words filtered out is never added unseen.
  // Rows from the last search while the new one loads do not count either.
  const chosen = picked && !found.isPlaceholderData && rows.some((t) => t.slug === picked) ? picked : null;
  const save = async () => {
    if (!chosen) return;
    const r = await add.run({ slug, tool: chosen, note: note.trim() || undefined });
    if (r.ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add a tool to ${name}`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={add.pending} disabled={!chosen || add.pending} onClick={() => void save()}>
            Add
          </Button>
        </>
      }
    >
      <SettledSearch label="Find a tool" value={words} onSettle={setWords} placeholder="Find a tool" wide autoFocus />
      <div className="tool-pick" role="listbox" aria-label="Tools">
        {rows.map((t) => (
          <button
            key={t.slug}
            type="button"
            role="option"
            aria-selected={t.slug === chosen}
            className={t.slug === chosen ? 'row tool-pick-row is-on' : 'row tool-pick-row'}
            title={t.summary || t.name}
            onClick={() => setPicked(t.slug)}
          >
            <span className="row-title">{t.name}</span>
            {t.summary && <span className="row-ctx">{t.summary}</span>}
          </button>
        ))}
        {found.isSuccess && !rows.length && !found.hasNextPage && <p className="now-step">Nothing matches</p>}
        {/* only a first page that failed says so: an older one that failed keeps More to press again */}
        {found.isLoadingError && (
          <p className="now-step">
            <ReadFault what="Search" reads={[found]} inline />
          </p>
        )}
      </div>
      {found.hasNextPage && (
        <button className="verb" type="button" disabled={found.isFetchingNextPage} onClick={() => void found.fetchNextPage()}>
          More
        </button>
      )}
      <Field label="Used for" value={note} onValueChange={setNote} placeholder="Optional" />
    </Dialog>
  );
}
