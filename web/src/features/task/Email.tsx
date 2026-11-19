import { useQuery } from '@tanstack/react-query';
import { FileFrame, fileSize } from '@/features/library/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { PageBones } from './FileView';
import { Go } from './parts';
import { emailFileUrl, emailSendQuery } from './queries';

/* An email the task asks about, as it will be sent (ui/app/email-preview.ts): its subject, who it goes to, its words
   and its files. A file opens in the file viewer in its place; the address keeps which (`part`). */

export function Email({ task, send, part, at }: { task: string; send: string; part?: number; at: { to: string; search: { tab?: string } } }) {
  const q = useQuery(emailSendQuery(task, send));
  if (q.isError || q.data?.preview_complete === false)
    return (
      <Empty icon="alert" title="The email did not load">
        {q.isError && <Button onClick={() => void q.refetch()}>Try again</Button>}
      </Empty>
    );
  if (!q.data) return <PageBones />;
  const { send: s, body, files } = q.data;
  const f = part === undefined ? undefined : files.find((x) => x.part === part);
  if (f) {
    const url = emailFileUrl(send, f.part);
    return (
      <>
        <div className="fv-bar">
          <Go to={at.to} search={at.search} className="ib ib-sm" aria-label="Back to Email" title="Back to Email">
            <Icon name="chevron" className="flip" />
          </Go>
          <b>{f.name}</b>
          <small>{fileSize(f.size)}</small>
          <span className="grow" />
          <a className="ib ib-sm" href={url} download={f.name} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </div>
        <div className="frame fv">
          <FileFrame mime={f.type ?? ''} title={f.name} url={url} name={f.name} size={f.size} held />
        </div>
      </>
    );
  }
  return (
    <div className="frame fv">
      <article className="tv-mail" aria-label="Email">
        <h2>{s.subject}</h2>
        <p className="tv-mail-to">To {s.recipients.join(', ')}</p>
        <div className="tv-mail-body">{body}</div>
        {files.map((x) => (
          <Go key={x.part} to={at.to} search={{ ...at.search, part: x.part }} className="file">
            <Icon name="file" />
            <span>{x.name}</span>
            <em>{fileSize(x.size)}</em>
          </Go>
        ))}
      </article>
    </div>
  );
}
