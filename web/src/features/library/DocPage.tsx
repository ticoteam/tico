import { useQuery } from '@tanstack/react-query';
import { useBlocker, useNavigate } from '@tanstack/react-router';
import { useRef, useState } from 'react';
import { refusal, run } from '@/actions';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Document, PageMore, type PageState } from '@/pagekinds';
import { headings, Markdown } from '@/kit/Markdown';
import { Face, OwnerButton, press, when } from './parts';
import { docQuery, docVersionQuery, docVersionsQuery, externalDocQuery, type Doc } from './queries';
import type { DocSearch } from './search';
import './library.css';

/* One doc (files.js docPage): its text at the reading measure, an outline beside it, the version in the head. Editing
   is in place: the text becomes a plain text box and the foot holds Cancel and Save. `/library/new` is a new doc, and
   `/library/ext:<id>` a doc read from an outside source. */

const CRUMB = [{ label: 'Docs', to: '/library/docs' }];

export function DocPage({ doc, search }: { doc: string; search: DocSearch }) {
  if (doc === 'new') return <NewDoc />;
  if (doc.startsWith('ext:')) return <OutsideDoc id={doc.slice(4)} />;
  return <OneDoc id={doc} search={search} />;
}

function stateOf(q: { isError: boolean; error: unknown; data: unknown }): PageState {
  if (q.isError) return (q.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault';
  return q.data ? 'ready' : 'loading';
}

function OneDoc({ id, search }: { id: string; search: DocSearch }) {
  const d = useQuery(docQuery(id));
  const versions = useQuery(docVersionsQuery(id));
  const old = useQuery(docVersionQuery(id, search.v ?? 0));
  const doc = d.data;
  const editing = !!search.edit && !!doc && !search.v;
  const past = search.v && doc && search.v !== doc.version ? old.data : undefined;
  const body = past?.body ?? doc?.body ?? '';
  const outline = headings(body);
  // A locked doc changes only by an owner (docs-page.ts canChange; the server lets agent administrators too).
  const owner = useQuery(meQuery).data?.role === 'owner';
  const canChange = !!doc && (owner || !doc.locked);
  const [researching, setResearching] = useState(false);
  // Keyed by the doc, not its version: a save's shown version and its rollback must not reset what the person typed.
  if (editing && doc) return <EditDoc key={doc.id} doc={doc} />;

  const versionPick = doc && (versions.data?.length ?? 0) > 1 && (
    <Menu
      align="start"
      trigger={
        <button type="button" className="btn btn-quiet ver-pick" title="Versions" data-target={`doc-version:${id}`}>
          v{past ? past.version : doc.version}
          <Icon name="chevdown" />
        </button>
      }
    >
      {versions.data?.map((v) => (
        <MenuItem
          key={v.version}
          label={`v${v.version}`}
          meta={`${v.actor_name}, ${when(v.created_at)}`}
          on={v.version === (past?.version ?? doc.version)}
          onSelect={() => void press('docs.open', { doc: id, version: v.current ? undefined : v.version })}
        />
      ))}
    </Menu>
  );

  return (
    <Document
      title={past?.title ?? doc?.title ?? 'Doc'}
      path={`/library/${id}`}
      crumb={CRUMB}
      className="lib"
      after={versionPick}
      actions={
        <>
          {doc && !past && canChange && (
            <IconButton icon="edit" label="Edit" onClick={() => void press('docs.open', { doc: id, edit: true })} data-target={`doc-edit:${id}`} />
          )}
          <OwnerButton page="docs" phone />
          {researching && doc && <ResearchDialog doc={doc} onClose={() => setResearching(false)} />}
        </>
      }
      more={
        doc &&
        !past &&
        !doc.archived && (
          <PageMore>
            {owner && (
              <MenuItem
                label={doc.locked ? 'Unlock' : 'Lock'}
                onSelect={() => void press('docs.lock', { doc: id, version: doc.version, locked: !doc.locked }, doc.locked ? 'Not unlocked' : 'Not locked')}
              />
            )}
            {canChange && <MenuItem label="Research" onSelect={() => setResearching(true)} />}
            <MenuItem label="Archive" onSelect={() => void press('docs.archive', { doc: id, version: doc.version, archived: true }, 'Not archived')} />
          </PageMore>
        )
      }
      state={stateOf(d)}
      onRetry={() => void d.refetch()}
      noAccess={{ text: 'This doc is not shared with you.' }}
      side={outline.length > 1 ? <Outline items={outline} /> : undefined}
      body={
        doc && (
          <>
            <p className="read-by">
              <Face actor={past ? past.actor : doc.updated_by_id} name={past ? past.actor_name : doc.updated_by_name} />
              <b>{past ? past.actor_name : doc.updated_by_name}</b>
              <span>{when(past ? past.created_at : doc.updated_at)}</span>
            </p>
            {search.v && !past && old.isLoading ? null : <Markdown text={body} />}
          </>
        )
      }
      foot={
        past && (
          <>
            <Button variant="quiet" onClick={() => void press('docs.open', { doc: id })}>
              Latest
            </Button>
            {canChange && (
              <Button variant="primary" onClick={() => void press('docs.restore', { doc: id, version: past.version })}>
                Restore
              </Button>
            )}
          </>
        )
      }
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

/** The doc's title and text in place. Save sends the version it started from, so a doc changed meanwhile is refused. */
function EditDoc({ doc: shown }: { doc: Doc }) {
  // The doc as editing began: the cache changes under it (the save shows at once, a refusal puts it back, a conflict
  // reads the newer one), and a Save again must still be refused if someone else changed it.
  const [doc] = useState(shown);
  const [title, setTitle] = useState(doc.title);
  const [path, setPath] = useState(doc.path);
  const [body, setBody] = useState(doc.body);
  const [busy, setBusy] = useState(false);
  // Leaving with changes asks first (docs-editor.ts dirty guard); a save on its way out is not leaving them.
  const saving = useRef(false);
  const dirty = title.trim() !== doc.title || path.trim() !== doc.path || body !== doc.body;
  const guard = useBlocker({
    shouldBlockFn: ({ current, next }) => !saving.current && (next.pathname !== current.pathname || !(next.search as { edit?: unknown }).edit),
    enableBeforeUnload: dirty,
    disabled: !dirty,
    withResolver: true,
  });
  const save = async () => {
    setBusy(true);
    saving.current = true;
    const r = await run(
      'docs.save',
      {
        doc: doc.id,
        version: doc.version,
        title: title.trim() !== doc.title ? title.trim() : undefined,
        body: body !== doc.body ? body : undefined,
        path: path.trim() && path.trim() !== doc.path ? path.trim() : undefined,
      },
      { via: 'click' },
    );
    saving.current = false;
    setBusy(false);
    if (!r.ok) toast(/stale|conflict|version/.test(r.error ?? '') ? { text: 'Someone changed it meanwhile. Copy your text and open it again.', kind: 'failed' } : refusal(r, 'Not saved'));
  };
  return (
    <Document
      title={doc.title}
      path={`/library/${doc.id}`}
      crumb={CRUMB}
      className="lib"
      body={
        <div className="doc-edit">
          <Field label="Title" value={title} onValueChange={setTitle} />
          <Field label="Path" value={path} onValueChange={setPath} autoComplete="off" />
          <textarea className="doc-text t-read" aria-label="Text" value={body} onChange={(e) => setBody(e.target.value)} spellCheck />
          {guard.status === 'blocked' && (
            <Dialog
              open
              alert
              onOpenChange={(o) => !o && guard.reset()}
              title="Discard changes?"
              actions={
                <>
                  <Button variant="quiet" onClick={guard.reset}>
                    Keep editing
                  </Button>
                  <Button variant="primary" onClick={guard.proceed}>
                    Discard
                  </Button>
                </>
              }
            />
          )}
        </div>
      }
      foot={
        <>
          <Button variant="quiet" onClick={() => void press('docs.open', { doc: doc.id })}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!title.trim() || busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    />
  );
}

function NewDoc() {
  const [title, setTitle] = useState('');
  const [path, setPath] = useState('');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    const ok = await press('docs.create', { title: title.trim(), body, path: filePath(path, title) }, 'Not made');
    if (!ok) setBusy(false);
  };
  return (
    <Document
      title="New doc"
      path="/library/new"
      crumb={CRUMB}
      className="lib"
      body={
        <div className="doc-edit">
          <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
          <Field label="Folder" value={path} onValueChange={setPath} placeholder="Company/Handbook" />
          <textarea className="doc-text t-read" aria-label="Text" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write here" spellCheck />
        </div>
      }
      foot={
        <>
          <Button variant="quiet" onClick={() => void press('docs.list', {})}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!title.trim() || busy} onClick={() => void make()}>
            Make doc
          </Button>
        </>
      }
    />
  );
}

/** The server takes `path` as the doc's file (`Company` would be `Company.md`), so a folder gets the file name the
    server would give the title by itself (backend docs slugify): "Company" and "Hiring plan" is
    `Company/hiring-plan.md`. A path that already names its file is kept. */
function filePath(folder: string, title: string): string | undefined {
  const f = folder.trim().replace(/^\/+|\/+$/g, '');
  if (!f) return undefined;
  if (/\.md$/i.test(f)) return f;
  const slug = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'doc';
  return `${f}/${slug}.md`;
}

const WHOLE = 'page';

/** The Librarian researches the web for this doc, or one section of it, and adds what it finds with its sources
    (docs-editor.ts research): one task to it, open from the toast. */
function ResearchDialog({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const sections = headings(doc.body);
  const [section, setSection] = useState(WHOLE);
  const [what, setWhat] = useState('');
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const start = async () => {
    setBusy(true);
    const heading = sections.find((h) => h.id === section)?.text;
    const r = await run('docs.research', { doc: doc.id, section: heading, instructions: what.trim() }, { via: 'click' });
    setBusy(false);
    if (!r.ok) {
      toast(r.error === 'librarian_off' ? { text: 'Research is off', kind: 'failed' } : refusal(r, 'Not started'));
      return;
    }
    const task = typeof r.out === 'string' ? r.out : undefined;
    toast({ text: 'Research started', action: task ? { label: 'Open', run: () => void navigate({ to: '/task/$task', params: { task } }) } : undefined });
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Research"
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!what.trim() || busy} onClick={() => void start()}>
            Start research
          </Button>
        </>
      }
    >
      <Select
        label="Section"
        options={[{ value: WHOLE, label: 'Whole page' }, ...sections.map((h) => ({ value: h.id, label: h.text }))]}
        value={section}
        onValueChange={setSection}
      />
      <Field label="What to find" value={what} onValueChange={setWhat} area autoFocus />
    </Dialog>
  );
}

/** A doc read from an outside source: shown as it was read, never edited here. */
function OutsideDoc({ id }: { id: string }) {
  const d = useQuery(externalDocQuery(id));
  const doc = d.data;
  return (
    <Document
      title={doc?.title ?? 'Outside doc'}
      path={`/library/ext:${id}`}
      crumb={CRUMB}
      className="lib"
      actions={
        doc?.citation_url && (
          <a className="ib" href={doc.citation_url} target="_blank" rel="noreferrer" aria-label="Open where it lives" title="Open where it lives">
            <Icon name="popout" />
          </a>
        )
      }
      state={stateOf(d)}
      onRetry={() => void d.refetch()}
      body={
        doc && (
          <>
            <p className="read-by">
              <b>{doc.source_title}</b>
              <span>{doc.path}</span>
            </p>
            {doc.reference_notice && <p className="read-note t-small">{doc.reference_notice}</p>}
            <Markdown text={doc.content} />
          </>
        )
      }
    />
  );
}
