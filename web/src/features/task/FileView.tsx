import { useQuery } from '@tanstack/react-query';
import { run } from '@/actions';
import type { TaskDocument } from '@/api/schema';
import { Ago } from '@/kit/Ago';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { PictureViewer, type Picture } from '@/kit/PictureViewer';
import { DETAILS, tabAddress } from './model';
import { Face, Go } from './parts';
import { taskFilesQuery, taskPageQuery, type TaskData } from './queries';

/* What a task shows in its page sheet: one of its pages (a tab), or one of its files opened from Details. Both keep
   their own look in a frame (page-types: a file is a Document with its content in a frame). */

/** The sandbox a page drawn by an agent runs in: its own scripts, never this app's cookies (ui/app/visual-pictures.ts). */
export const PAGE_SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox';

export function DocFrame({ task, doc }: { task: string; doc: TaskDocument }) {
  const page = useQuery(taskPageQuery(task, doc.file_id));
  if (page.isError) return <Empty icon="alert" title={`${doc.title || doc.name} did not load`} />;
  const src = page.data?.src;
  return (
    <div className="frame is-inset">
      {src && <iframe src={src} title={doc.title || doc.name} sandbox={PAGE_SANDBOX} referrerPolicy="no-referrer" />}
    </div>
  );
}

/** A file open in the page sheet (more.js fileBar): back to Details, its name, which of the task's files it is. */
export function FileView({ t, file, full, flow }: { t: TaskData; file: string; full?: string; flow: boolean }) {
  const files = useQuery(taskFilesQuery(t.id));
  const list = (files.data ?? []).filter((f) => !f.archived);
  const i = list.findIndex((f) => f.id === file);
  const f = list[i];
  const details = tabAddress(t, DETAILS, flow);
  const at = (id: string) => (flow ? { tab: DETAILS, file: id } : { file: id });
  const back = flow ? { tab: DETAILS } : {};
  const v = f?.versions.find((x) => x.n === f.current_version) ?? f?.versions.at(-1);
  // The task's pictures, each at its current version: the full size view walks them.
  const pictures = list.flatMap((x) => {
    const cur = x.versions.find((y) => y.n === x.current_version) ?? x.versions.at(-1);
    return cur?.mime.startsWith('image/') && cur.url.startsWith('/api/') ? [{ id: x.id, src: cur.url, name: x.title || x.name } satisfies Picture & { id: string }] : [];
  });
  const fullAt = full ? pictures.findIndex((p) => p.id === full) : -1;
  const showPicture = (id: string | null) => void run('task.showPicture', { task: t.id, file: id }, { via: 'click' });
  const step = (d: number, label: string, flip: boolean) => {
    const to = list[i + d];
    return to ? (
      <Go to={details.to} search={at(to.id)} className="ib ib-sm" aria-label={label} title={label}>
        <Icon name="chevdown" className={flip ? 'flip' : undefined} />
      </Go>
    ) : (
      <button className="ib ib-sm" type="button" aria-label={label} title={label} disabled>
        <Icon name="chevdown" className={flip ? 'flip' : undefined} />
      </button>
    );
  };

  return (
    <>
      <div className="fv-bar">
        <Go to={details.to} search={back} className="ib ib-sm" aria-label="Back to Details" title="Back to Details">
          <Icon name="chevron" className="flip" />
        </Go>
        <b>{f ? f.title || f.name : 'File'}</b>
        {v?.width && v.height ? (
          <small>
            {v.width} by {v.height}
          </small>
        ) : null}
        {v && <Face id={v.by} decorative />}
        {v && <Ago at={v.created_at} />}
        <span className="grow" />
        {list.length > 1 && i >= 0 && (
          <span className="fv-n">
            <b>{i + 1}</b> of {list.length}
          </span>
        )}
        {list.length > 1 && step(-1, 'Previous', true)}
        {list.length > 1 && step(1, 'Next', false)}
      </div>
      {files.isError ? (
        <Empty icon="alert" title="This file did not load" />
      ) : files.isSuccess && !f ? (
        <Empty title="This file is gone" />
      ) : (
        <div className="frame fv">
          {v && f && (v.mime.startsWith('image/') ? (
            <button type="button" className="fv-pic" aria-label={`${f.title || f.name}, full size`} title="Full size" onClick={() => showPicture(f.id)}>
              <img src={v.url} alt={f.title || f.name} />
            </button>
          ) : v.mime.startsWith('video/') ? (
            <video src={v.url} poster={v.poster_url ?? undefined} controls />
          ) : (
            <iframe src={v.url} title={f.title || f.name} sandbox="" referrerPolicy="no-referrer" />
          ))}
        </div>
      )}
      <PictureViewer pictures={pictures} at={fullAt >= 0 ? fullAt : null} onAtChange={(i) => showPicture(i == null ? null : (pictures[i]?.id ?? null))} />
    </>
  );
}
