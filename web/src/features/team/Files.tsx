import { useInfiniteQuery } from '@tanstack/react-query';
import { useState, type MouseEvent } from 'react';
import { run } from '@/actions';
import { Ago } from '@/kit/Ago';
import { Icon } from '@/kit/Icon';
import { Section } from '@/pagekinds';
import { fileName, fileOpen } from './model';
import { Foot } from './parts';
import { agentFilesQuery, type AgentFile } from './queries';

const SHORT = 3;

/**
 * The files it made or keeps (ui bot-files.ts): the three newest, then all of them a page at a time. A stored file and
 * a Google file open over the page; any other link opens at its provider. No files, no section.
 */
export function FilesSection({ slug }: { slug: string }) {
  const q = useInfiniteQuery(agentFilesQuery(slug));
  const [all, setAll] = useState(false);
  const files = q.data?.pages.flatMap((p) => p.files ?? []) ?? [];
  const total = q.data?.pages[0]?.total ?? files.length;
  if (!files.length) return null;
  const shown = all ? files : files.slice(0, SHORT);
  return (
    <Section name="Files" count={total} target={`files:${slug}`}>
      {shown.map((f) => (
        <FileRow key={f.id} file={f} />
      ))}
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
