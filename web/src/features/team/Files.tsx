import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useState, type MouseEvent } from 'react';
import { run } from '@/actions';
import { Ago } from '@/kit/Ago';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Thumbs, type Thumb } from '@/kit/Thumbs';
import { Section } from '@/pagekinds';
import { fileName, fileOpen } from './model';
import { Foot } from './parts';
import { agentFilesQuery, agentMediaQuery, type AgentFile } from './queries';

const SHORT = 3;

const fileAt = (f: AgentFile, part = '') => `/api/v2/files/${encodeURIComponent(f.id)}${part}?v=${f.version}`;

/** A picture or a video it made, as a preview: its small copy (a picture's thumb, a video's frame), the task it is for
    under it, a link that opens that task over the page (its list this agent's tasks). */
const thumbOf = (f: AgentFile): Thumb => {
  const video = f.kind === 'video';
  return {
    id: f.id,
    name: fileName(f),
    video,
    src: video ? f.poster_blob_id && fileAt(f, '/poster') : f.thumb_blob_id && fileAt(f, '/thumb'),
    full: video ? null : fileAt(f),
    sub: f.task_id ? (
      <Link href={`/task/${encodeURIComponent(f.task_id)}`} title={f.task_title ?? undefined}>
        {f.task_title || 'Its task'}
      </Link>
    ) : undefined,
  };
};

/**
 * Made recently (ui bot-files.ts, then its pictures first): its newest pictures and videos as previews, each opening in
 * the viewer over the page with the task it is for a click away; then all its files a page at a time, behind All files.
 * With no pictures or videos, the three newest files, as before. A stored file and a Google file open over the page;
 * any other link opens at its provider. Its pictures and videos are a read of their own, drawn without waiting on its
 * files. No pictures, videos or files, no section.
 */
export function FilesSection({ slug, list }: { slug: string; /** Its tasks' list, which a task opened from here has beside it. */ list?: string }) {
  const [find, setFind] = useState('');
  const [searching, setSearching] = useState(false);
  const words = find.trim();
  const narrowed = !!words;
  const q = useInfiniteQuery({ ...agentFilesQuery(slug, words), placeholderData: keepPreviousData });
  const made = useQuery(agentMediaQuery(slug));
  const [all, setAll] = useState(false);
  const files = q.data?.pages.flatMap((p) => p.files ?? []) ?? [];
  // its count once its files are read: its pictures and videos show before that, never waiting on the longer read
  const total = q.data ? (q.data.pages[0]?.total ?? files.length) : undefined;
  const media = (made.data?.files ?? []).filter((f) => f.kind === 'image' || f.kind === 'video').map(thumbOf);
  // Nothing to list is no section; a search that finds nothing keeps its box (ui bot-files).
  if (!files.length && !media.length && !narrowed) return null;
  // its pictures stand for its newest files; the rows wait behind All files, unless a search asks for them
  const pictures = media.length > 0 && !narrowed;
  const rows = !pictures || all;
  const shown = all ? files : files.slice(0, SHORT);
  const tools = (total ?? 0) > SHORT || narrowed || searching;
  return (
    <Section
      name="Made recently"
      count={total}
      target={`files:${slug}`}
      actions={
        tools &&
        (searching ? (
          <Field label="Search files" labelHidden placeholder="Search" lead="search" value={find} onValueChange={setFind} autoFocus />
        ) : (
          <IconButton icon="search" size="sm" quiet label="Search files" onClick={() => setSearching(true)} />
        ))
      }
    >
      {pictures && (
        <div className="side-made" data-list={list}>
          <Thumbs
            label="Pictures and videos"
            items={media}
            onOpen={(t) => void run('files.preview', { file: t.id }, { via: 'click' })}
          />
        </div>
      )}
      {rows && shown.map((f) => <FileRow key={f.id} file={f} />)}
      {rows && q.isSuccess && !files.length && <p className="now-step">Nothing matches</p>}
      {(pictures || (total ?? 0) > SHORT || all) && (
        <Foot>
          {!all && (
            <button className="verb" type="button" onClick={() => setAll(true)}>
              {pictures ? 'All files' : `${(total ?? 0) - SHORT} more`}
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
