import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { meQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { Menu, MenuItem } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { Document, PageMore } from '@/pagekinds';
import { headings, Markdown } from '@/kit/Markdown';
import { Face, OwnerButton, press, when } from './parts';
import { docQuery, docVersionQuery, docVersionsQuery, externalDocAtQuery, externalDocQuery, type Doc } from './queries';
import type { DocSearch } from './search';
import './library.css';

/* One doc (files.js docPage): its text at the reading measure, an outline beside it, the version in the head. There is
   no edit mode: the title renames in place, and pressing the text types in it; leaving it saves. `/library/new` is a
   new doc, and `/library/ext:<id>` a doc read from an outside source. */


export function DocPage({ doc, search }: { doc: string; search: DocSearch }) {
  if (doc === 'new') return <NewDoc />;
  if (doc.startsWith('ext:')) return <OutsideDoc id={doc.slice(4)} revision={search.revision} />;
  return <OneDoc id={doc} search={search} />;
}

/** Save a change against the version it started from, so a doc changed meanwhile is refused. */
async function keep(from: Doc, change: { title?: string; body?: string }): Promise<boolean> {
  const r = await run('docs.save', { doc: from.id, version: from.version, ...change }, { via: 'click' });
  if (!r.ok)
    toast(/stale|conflict|version/.test(r.error ?? '') ? { text: 'Someone changed it meanwhile. Copy your text and open it again.', kind: 'failed' } : refusal(r, 'Not saved'));
  return r.ok;
}

/** The text as it reads under the page's title: a first "# heading" that only repeats the title (or its start, as
    "Docs sweep findings" under "Docs sweep findings, 2026-11-16") is left out, as the mock's docs start with their
    first paragraph. The text itself keeps it. */
const unTitled = (body: string, title: string) => {
  const m = /^\s*#[ \t]+(.+?)[ \t#]*(?:\r?\n|$)/.exec(body);
  const same = (a: string) => a.trim().toLowerCase().replace(/\s+/g, ' ');
  return m?.[1] && same(title).startsWith(same(m[1])) ? body.slice(m[0].length) : body;
};

function OneDoc({ id, search }: { id: string; search: DocSearch }) {
  const { data: doc } = useSuspenseQuery(docQuery(id));
  const versions = useQuery(docVersionsQuery(id));
  const requested = search.v !== doc.version ? search.v : undefined;
  const old = useQuery(docVersionQuery(id, requested ?? 0));
  const past = old.data;
  const body = unTitled(past?.body ?? doc.body, past?.title ?? doc.title);
  const outline = headings(body);
  // A locked doc changes only by an owner (docs-page.ts canChange; the server lets agent administrators too).
  const owner = useQuery(meQuery).data?.role === 'owner';
  const canChange = owner || !doc.locked;
  const typable = canChange && !requested;

  const versionPick = (versions.data?.length ?? 0) > 1 && (
    <Menu
      align="start"
      trigger={
        <button type="button" className="btn btn-quiet ver-pick" title="Versions" data-target={`doc-version:${id}`}>
          v{requested ?? doc.version}
          <Icon name="chevdown" />
        </button>
      }
    >
      {versions.data?.map((v) => (
        <MenuItem
          key={v.version}
          label={`v${v.version}`}
          meta={`${v.actor_name}, ${when(v.created_at)}`}
          on={v.version === (requested ?? doc.version)}
          onSelect={() => void press('docs.open', { doc: id, version: v.current ? undefined : v.version })}
        />
      ))}
    </Menu>
  );

  return (
    <Document
      title={past?.title ?? doc.title}
      path={`/library/${id}`}
      className="lib"
      state={requested && !past ? (old.isError ? 'fault' : 'loading') : 'ready'}
      onRetry={requested ? () => void old.refetch() : undefined}
      after={versionPick}
      onRename={typable ? (title) => void keep(doc, { title }) : undefined}
      actions={<OwnerButton page="docs" />}
      more={
        !requested &&
        !doc.archived && (
          <PageMore>
            {owner && (
              <MenuItem
                label={doc.locked ? 'Unlock' : 'Lock'}
                onSelect={() => void press('docs.lock', { doc: id, version: doc.version, locked: !doc.locked }, doc.locked ? 'Not unlocked' : 'Not locked')}
              />
            )}
            <MenuItem label="Archive" onSelect={() => void press('docs.archive', { doc: id, version: doc.version, archived: true }, 'Not archived')} />
          </PageMore>
        )
      }
      side={outline.length > 1 ? <Outline items={outline} /> : undefined}
      body={
        <>
          <p className="read-by">
            <Face actor={past ? past.actor : doc.updated_by_id} name={past ? past.actor_name : doc.updated_by_name} />
            <b>{past ? past.actor_name : doc.updated_by_name}</b>
            <span>{when(past ? past.created_at : doc.updated_at)}</span>
          </p>
          {typable ? <TypeHere doc={doc} /> : <Markdown text={body} />}
        </>
      }
      foot={
        past && (
          <>
            <Button variant="quiet" onClick={() => press('docs.open', { doc: id })}>
              Latest
            </Button>
            {canChange && (
              <Button variant="primary" onClick={() => press('docs.restore', { doc: id, version: past.version })}>
                Restore
              </Button>
            )}
          </>
        )
      }
    />
  );
}

/** The text as it reads; pressed (or reached by Tab), the same place takes typing, and leaving it saves. A refused
    save keeps what was typed. A link in the text still opens. */
function TypeHere({ doc }: { doc: Doc }) {
  // The doc as typing began: a save shows at once and a refusal puts it back, but the version sent stays this one.
  const [typing, setTyping] = useState<{ from: Doc; text: string } | null>(null);
  if (!typing)
    return (
      <div className="doc-type" tabIndex={0} aria-label="Text" onFocus={(e) => e.target === e.currentTarget && setTyping({ from: doc, text: doc.body })}>
        <Markdown text={unTitled(doc.body, doc.title)} />
      </div>
    );
  const done = async () => {
    if (typing.text === typing.from.body) return setTyping(null);
    if (await keep(typing.from, { body: typing.text })) setTyping(null);
  };
  return (
    <textarea
      className="doc-text t-read"
      aria-label="Text"
      value={typing.text}
      onChange={(e) => setTyping({ ...typing, text: e.target.value })}
      onBlur={() => void done()}
      autoFocus
      spellCheck
    />
  );
}

function Outline({ items }: { items: { id: string; text: string }[] }) {
  return (
    <nav className="outline" aria-label="Outline">
      {items.map((h) => (
        <a key={h.id} href={`#${h.id}`}>
          {h.text}
        </a>
      ))}
    </nav>
  );
}

/** The same page, empty: the title in the head, the text under it. Naming it makes the doc, with what is typed so far,
    and opens it. */
function NewDoc() {
  const [body, setBody] = useState('');
  return (
    <Document
      title="Title"
      path="/library/new"
      className="lib"
      onRename={(title) => void press('docs.create', { title, body }, 'Not made')}
      actions={<OwnerButton page="docs" />}
      body={<textarea className="doc-text t-read" aria-label="Text" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write here" spellCheck />}
    />
  );
}

/** A link in an outside doc's text, made whole against where the doc lives (old docs-page.ts): `guide.md`, `../x`
    and `/x` resolve against `base` (its citation, without query or fragment); an address, `#part` or `//host` stays. */
export function wholeLinks(text: string, base: string): string {
  const root = base.split('?')[0]?.split('#')[0] ?? base;
  return text.replace(/(!?\[[^\]]*\]\()([^()\s]+)(\))/g, (all, open: string, href: string, close: string) => {
    if (/^[a-z][a-z0-9+.-]*:|^#|^\/\//i.test(href)) return all;
    try {
      return open + new URL(href, root).href + close;
    } catch {
      return all;
    }
  });
}

/** A doc read from an outside source: shown as it was read (at `revision`, the commit a link saw, when one is given),
    never edited here. Its relative links lead to where it lives. */
function OutsideDoc({ id, revision }: { id: string; revision: string | undefined }) {
  const { data: doc } = useSuspenseQuery(revision ? externalDocAtQuery(id, revision) : externalDocQuery(id));
  return (
    <Document
      title={doc.title}
      path={`/library/ext:${id}`}
      className="lib"
      actions={
        doc.citation_url && (
          <a className="ib" href={doc.citation_url} target="_blank" rel="noreferrer" aria-label="Open where it lives" title="Open where it lives">
            <Icon name="popout" />
          </a>
        )
      }
      body={
        <>
          <p className="read-by">
            <b>{doc.source_title}</b>
            <span>{doc.path}</span>
            {doc.commit && <span title={doc.commit}>{doc.commit.slice(0, 7)}</span>}
            {doc.last_checked_at && <Ago at={doc.last_checked_at} />}
          </p>
          {doc.reference_notice && <p className="read-note t-small">{doc.reference_notice}</p>}
          <Markdown text={doc.citation_url ? wholeLinks(doc.content, doc.citation_url) : doc.content} />
        </>
      }
    />
  );
}
