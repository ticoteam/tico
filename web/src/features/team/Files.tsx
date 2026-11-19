import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useState, type MouseEvent } from 'react';
import { run } from '@/actions';
import { Ago } from '@/kit/Ago';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Section } from '@/pagekinds';
import { fileName, fileOpen } from './model';
import { Foot } from './parts';
import { agentFilesQuery, type AgentFile } from './queries';

const SHORT = 3;
// Library's own kinds (ui pages-shell LF_KINDS); "All kinds" is none.
const KINDS = [
  ['', 'All kinds'],
  ['page', 'Pages'],
  ['document', 'Documents'],
  ['image', 'Images'],
  ['data', 'Data'],
  ['media', 'Video and audio'],
  ['other', 'Other'],
] as const;

/**
 * The files it made or keeps (ui bot-files.ts): the three newest, then all of them a page at a time. A stored file and
 * a Google file open over the page; any other link opens at its provider. No files, no section.
 */
export function FilesSection({ slug }: { slug: string }) {
  const [find, setFind] = useState('');
  const [kind, setKind] = useState('');
  const [searching, setSearching] = useState(false);
  const words = find.trim();
  const narrowed = !!words || !!kind;
  const q = useInfiniteQuery({ ...agentFilesQuery(slug, { q: words, kind }), placeholderData: keepPreviousData });
  const [all, setAll] = useState(false);
  const files = q.data?.pages.flatMap((p) => p.files ?? []) ?? [];
  const total = q.data?.pages[0]?.total ?? files.length;
  // Nothing to list is no section; a search that finds nothing keeps its box (ui bot-files).
  if (!files.length && !narrowed) return null;
  const shown = all ? files : files.slice(0, SHORT);
  const tools = total > SHORT || narrowed || searching;
  return (
    <Section
      name="Files"
      count={total}
      target={`files:${slug}`}
      actions={
        tools && (
          <>
            {searching ? (
              <Field label="Search files" labelHidden placeholder="Search" lead="search" value={find} onValueChange={setFind} autoFocus />
            ) : (
              <IconButton icon="search" size="sm" quiet label="Search files" onClick={() => setSearching(true)} />
            )}
            <Menu trigger={<IconButton icon="filter" size="sm" quiet on={!!kind} label="Filter" aria-pressed={!!kind} />}>
              {KINDS.map(([value, label]) => (
                <MenuItem key={value} label={label} on={kind === value} onSelect={() => setKind(value)} />
              ))}
            </Menu>
          </>
        )
      }
    >
      {shown.map((f) => (
        <FileRow key={f.id} file={f} />
      ))}
      {!files.length && <p className="now-step">Nothing matches</p>}
      {(total > SHORT || all) && (
        <Foot>
          {!all && (
            <button className="verb" type="button" onClick={() => setAll(true)}>
              {total - SHORT} more
            </button>
          )}
          {all && q.hasNextPage && (
            <button className="verb" type="button" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
              More
            </button>
          )}
          {all && (
            <button className="verb" type="button" onClick={() => setAll(false)}>
              Less
            </button>
          )}
        </Foot>
      )}
    </Section>
  );
}

function FileRow({ file }: { file: AgentFile }) {
  const name = fileName(file);
  const to = fileOpen(file);
  const cells = (
    <>
      <span className="row-mark">
        <Icon name={file.locator === 'remote_link' ? 'link' : 'file'} />
      </span>
      <span className="row-title">{name}</span>
      <span className="row-pills" />
      <span className="av-gap" />
      <Ago at={file.last_activity_at} className="row-age" />
    </>
  );
  if (!to)
    return (
      <div className="row task" title={name} data-target={`file:${file.id}`}>
        {cells}
      </div>
    );
  // Over the page on a plain click; the link stays real for a new tab or a copy.
  const open = (e: MouseEvent) => {
    if (!to.view || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    void run('files.view', { file: file.id }, { via: 'click' });
  };
  return (
    <a className="row task" title={name} data-target={`file:${file.id}`} href={to.href} target="_blank" rel="noreferrer" onClick={open}>
      {cells}
    </a>
  );
}
