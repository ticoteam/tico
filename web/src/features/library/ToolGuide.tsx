import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Markdown } from '@/kit/Markdown';
import { MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { PageMore, Section, SkRows } from '@/pagekinds';
import { agentsQuery } from '@/features/team/exports';
import { AddDialog, ConnectDialog, type Adding } from './AddTool';
import { Face, Foot, press } from './parts';
import { Confirm } from './ToolDialogs';
import {
  catalogQuery,
  FUNCTIONS,
  functionLabel,
  toolGuideQuery,
  toolGuidesQuery,
  vendorQuery,
  type ToolGuide,
  type ToolOrigin,
  type VendorRow,
} from './queries';
import { bad, Code, deliveryOf, KIND, Proxied, ReadFault, ToolPage, toolHref, WhoRow } from './ToolShell';

/* A tool's page from the registry (backend tool guides): About (what it is for, how to use it), the company's guidance,
   then a vendor's connections (its access, each with who may use it at which level) or a built tool's source and what it
   needs, who uses it, and what agents learned about it. Anyone may write the guide; the server records who. */

/** A vendor's (`vendor:<id>`) or a built tool's (`built:<slug>`) page. The address may name it by an alias (`gh`): once
    the guide reads, its own slug and origin are the page's, for its connections and every write. */
export function GuideView({ slug: named, origin: asked }: { slug: string; origin: ToolOrigin }) {
  const g = useQuery(toolGuideQuery(named));
  const guide = g.data;
  const slug = guide?.slug || named;
  const origin = guide?.origin ?? asked;
  const built = origin === 'built';
  // A vendor's connections, once the guide says whose (a 404 when the reader may see none: the guide still shows).
  const vendor = useQuery({ ...vendorQuery(slug), enabled: !built && !g.isPending });
  const known = useQuery(catalogQuery).data?.some((s) => s.id === slug);
  const guides = useQuery(toolGuidesQuery).data;
  const [connecting, setConnecting] = useState(false);
  const [adding, setAdding] = useState<Adding | null>(null);
  const [editing, setEditing] = useState(false);
  const [archiving, setArchiving] = useState(false);
  // The page waits on the guide; a vendor the registry does not know (404) still shows its connections. Any other
  // failure is the page's: a blank guide there could be saved over the real one.
  const unknown = (g.error as { status?: number } | null)?.status === 404;
  const read = built || !unknown ? g : vendor;
  // A company row (written, or archived and back) is what Archive hides; a shipped guide may be hidden too.
  const archivable = !!guide && (!!guide.updated_at || guide.shipped);
  const name = guide?.name || vendor.data?.name || slug;
  const users = guide?.used_by ?? [];
  // Used by answers bot ids; an agent's page is under its slug.
  const roster = useQuery(agentsQuery).data;
  const slugOfBot = (id: string) => roster?.find((r) => r.id === id)?.slug ?? id;
  const learnings = guide?.learnings ?? [];
  return (
    <ToolPage
      name={name}
      kind={built ? 'built' : 'vendor'}
      q={read}
      target={`tool:${origin}:${slug}`}
      bare
      more={
        // Who may archive is the server's to say: a refusal says so in its toast.
        archivable && (
          <PageMore>
            <MenuItem label="Archive" onSelect={() => setArchiving(true)} />
          </PageMore>
        )
      }
      fill={
        !built &&
        known && (
          <Button variant="primary" onClick={() => setConnecting(true)}>
            Add connection
          </Button>
        )
      }
    >
      <Section name="About" target={`tool-about:${origin}:${slug}`} actions={<IconButton icon="edit" label="Edit" size="sm" quiet onClick={() => setEditing(true)} />}>
        {guide?.summary || guide?.instructions ? (
          <div className="tool-read">
            {guide.summary && <p className="read-lead">{guide.summary}</p>}
            {guide.instructions && <Markdown text={guide.instructions} />}
          </div>
        ) : (
          <p className="tool-none">No guide yet</p>
        )}
        {guide?.category && (
          <Facts>
            <LabelValue label="Category">{functionLabel(guide.category)}</LabelValue>
          </Facts>
        )}
      </Section>
      <Guidance slug={slug} origin={origin} text={guide?.guidance ?? ''} />
      {built ? (
        <>
          {guide?.source && (
            <Section name="Source" target={`tool-source:${slug}`}>
              <Facts>
                <LabelValue label="Where">{/^https?:\/\//.test(guide.source) ? <Link href={guide.source}>{guide.source}</Link> : <Code>{guide.source}</Code>}</LabelValue>
              </Facts>
            </Section>
          )}
          {guide?.needs?.length ? (
            <Section name="Needs" count={guide.needs.length} target={`tool-needs:${slug}`}>
              {guide.needs.map((n) => {
                // The tool it needs as the registry knows it (`gh` is GitHub); one it does not know opens by its word.
                const t = guides?.find((x) => x.slug === n.tool);
                return (
                  <Row key={n.tool} className="arow" title={t?.name ?? n.tool} href={toolHref(`${t?.origin === 'built' ? 'built' : 'vendor'}:${t?.slug ?? n.tool}`)}>
                    <span className="row-title">{t?.name ?? n.tool}</span>
                    <span className="lvl">{n.level === 'write' ? 'Read and write' : 'Read'}</span>
                  </Row>
                );
              })}
            </Section>
          ) : null}
        </>
      ) : (
        <Connections q={vendor} />
      )}
      {users.length > 0 && (
        <Section name="Used by" count={users.length} target={`tool-used:${slug}`}>
          {users.map((u) => (
            <Row key={u.id} className="wrow" title={u.name} href={`/team/${encodeURIComponent(slugOfBot(u.id))}`}>
              <Face actor={`bot:${u.id}`} name={u.name} />
              <span className="row-title">{u.name}</span>
              {u.note && <span className="row-ctx">{u.note}</span>}
              <span className="row-fig" title="Uses in 30 days">
                {u.uses || ''}
              </span>
              {u.last_used_at ? <Ago at={u.last_used_at} className="row-age" /> : <span className="row-age" />}
            </Row>
          ))}
        </Section>
      )}
      {learnings.length > 0 && (
        <Section name="Learnings" count={learnings.length} target={`tool-learned:${slug}`}>
          {learnings.map((l) => (
            <div key={l.id} className="tool-read tool-learned">
              <Markdown text={l.text} />
              <Ago at={l.created_at} className="row-age" />
            </div>
          ))}
        </Section>
      )}
      {archiving && (
        <Confirm
          title={`Archive ${name}?`}
          description={built ? undefined : 'Its connections stay.'}
          label="Archive"
          onGo={() => press('tools.archiveGuide', { slug }, 'Not archived')}
          onClose={() => setArchiving(false)}
        />
      )}
      {editing && <EditGuideDialog slug={slug} origin={origin} guide={guide} onClose={() => setEditing(false)} />}
      {connecting && (
        <ConnectDialog
          vendor={slug}
          onClose={() => setConnecting(false)}
          onKind={(k) => {
            setConnecting(false);
            setAdding(k);
          }}
        />
      )}
      {adding && <AddDialog adding={adding} onClose={() => setAdding(null)} />}
    </ToolPage>
  );
}

/** The company's own rules for the tool, written in place: the pencil opens the text, Save keeps it once the server has. */
function Guidance({ slug, origin, text }: { slug: string; origin: ToolOrigin; text: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await press('tools.writeGuide', { slug, origin, guidance: (draft ?? '').trim() }, 'Not saved');
    setBusy(false);
    if (ok) setDraft(null);
  };
  return (
    <Section
      name="Company guidance"
      target={`tool-guidance:${slug}`}
      actions={draft === null && <IconButton icon="edit" label="Edit guidance" size="sm" quiet onClick={() => setDraft(text)} />}
    >
      {draft !== null ? (
        <div className="tool-edit">
          <Field label="Guidance" labelHidden area value={draft} onValueChange={setDraft} autoFocus />
          <div className="tool-edit-foot">
            <Foot onClose={() => setDraft(null)} label="Save" busy={busy} ready={draft.trim() !== text.trim()} onGo={() => void save()} />
          </div>
        </div>
      ) : text ? (
        <div className="tool-read">
          <Markdown text={text} />
        </div>
      ) : (
        <p className="tool-none">None</p>
      )}
    </Section>
  );
}

const NONE = 'none';
/** The job functions, and the guide's own category when it is none of them (`code`), so it shows and stays. */
const categoriesWith = (current: string) => [
  { value: NONE, label: 'None' },
  ...[...FUNCTIONS, ...(current && !(FUNCTIONS as readonly string[]).includes(current) ? [current] : [])].map((f) => ({ value: f as string, label: functionLabel(f) })),
];

/** Edit: the one-line summary, the category and the instructions. Only what changed is sent: a shipped guide's words
    are never copied into the company's own. */
function EditGuideDialog({ slug, origin, guide, onClose }: { slug: string; origin: ToolOrigin; guide: ToolGuide | undefined; onClose: () => void }) {
  const was = { summary: guide?.summary ?? '', category: guide?.category ?? '', instructions: guide?.instructions ?? '' };
  const [summary, setSummary] = useState(was.summary);
  const [category, setCategory] = useState(was.category || NONE);
  const [instructions, setInstructions] = useState(was.instructions);
  const [busy, setBusy] = useState(false);
  const now = { summary: summary.trim(), category: category === NONE ? '' : category, instructions: instructions.trim() };
  const change = Object.fromEntries(Object.entries(now).filter(([k, v]) => v !== was[k as keyof typeof was].trim()));
  const ready = Object.keys(change).length > 0 && (origin !== 'built' || (!!now.summary && !!now.instructions));
  const save = async () => {
    setBusy(true);
    const ok = await press('tools.writeGuide', { slug, origin, ...change }, 'Not saved');
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit ${guide?.name || slug}`}
      className="tool-guide-edit"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={ready} onGo={() => void save()} />}
    >
      <Field label="Summary" value={summary} onValueChange={setSummary} placeholder="One line" autoFocus />
      <Select label="Category" options={categoriesWith(was.category)} value={category} onValueChange={setCategory} />
      <Field label="Instructions" area value={instructions} onValueChange={setInstructions} placeholder="What it is for and how to use it" />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a vendor's connections */

/** A vendor's connections, read apart from its guide: grey bars while they load, Try again when the read fails; a 404
    (none the reader may see) is none. */
function Connections({ q }: { q: UseQueryResult<{ rows: VendorRow[] }> }) {
  if (q.isPending && q.fetchStatus !== 'idle') return <SkRows rows={3} />;
  if (q.isError && (q.error as { status?: number } | null)?.status !== 404)
    return <ReadFault name="Connections" error={q.error} onRetry={() => void q.refetch()} />;
  return q.data?.rows.map((r) => <Connection key={r.id} row={r} />) ?? null;
}

/** One connection on its vendor's page: what it is, how it reaches agents, its state, and who may use it at which level. */
function Connection({ row }: { row: VendorRow }) {
  const delivery = deliveryOf(row);
  const who =
    row.access?.map((a) => (
      <WhoRow key={a.subject} actor={a.kind === 'finance' ? null : a.subject} name={a.name} level={a.level} right={a.proxied_read && <Proxied />} />
    )) ?? row.who.map((w) => <WhoRow key={w.id} actor={w.kind === 'group' ? null : w.id} name={w.name} />);
  const more = (row.access_total ?? row.who_total) - who.length;
  return (
    <Section
      name={row.name}
      count={(row.access_total ?? row.who_total) || undefined}
      target={`tool-conn:${row.id}`}
      actions={
        <Link className="ib ib-sm" href={toolHref(row.id)} aria-label={`Open ${row.name}`} title={`Open ${row.name}`}>
          <Icon name="arrow" />
        </Link>
      }
    >
      <Facts>
        <LabelValue label="Kind">{row.mcp ? KIND.mcp : (KIND[row.kind] ?? row.kind)}</LabelValue>
        {row.detail && (
          <LabelValue label="Address">
            <Code>{row.detail}</Code>
          </LabelValue>
        )}
        {delivery && <LabelValue label="Default delivery">{delivery}</LabelValue>}
        <LabelValue label="State">{bad(row) ? <Pill>{bad(row)}</Pill> : row.status.text || 'Not yet'}</LabelValue>
      </Facts>
      {who}
      {more > 0 && (
        <Link className="verb" href={toolHref(row.id)}>
          {`${more} more`}
        </Link>
      )}
    </Section>
  );
}
