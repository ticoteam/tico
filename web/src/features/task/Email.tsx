import { useQuery } from '@tanstack/react-query';
import { FileFrame, fileSize } from '@/features/library/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { PageBones } from './FileView';
import { Go } from './parts';
import { emailFileUrl, emailSendQuery } from './queries';
import { isWord, WordPages } from './Word';

/* An email the task asks about, exactly as it will go (plan/approvals.html, "Email"): the envelope in Tico's look (its
   subject, From, To and its files as cards), then the message on paper. A file opens in this same sheet in its
   place, and the address keeps which (`part`); a Word file is drawn as its pages, with Download beside it. */

/** "DOCX", "PDF": the type badge on a file's card. */
const extOf = (name: string) => /\.([a-z0-9]{1,5})$/i.exec(name)?.[1]?.toUpperCase() ?? 'FILE';

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
          <Go to={at.to} search={at.search} className="ib ib-sm" aria-label="Back to the email" title="Back to the email">
            <Icon name="chevron" className="flip" />
          </Go>
          <b>{f.name}</b>
          <small>{fileSize(f.size)}</small>
          <span className="grow" />
          <a className="ib ib-sm" href={url} download={f.name} aria-label="Download" title="Download">
            <Icon name="download" />
          </a>
        </div>
        {isWord(f.name, f.type) ? (
          <WordPages url={url} name={f.name} />
        ) : (
          <div className="frame fv">
            <FileFrame mime={f.type ?? ''} title={f.name} url={url} name={f.name} size={f.size} held />
          </div>
        )}
      </>
    );
  }
  return (
    <div className="ap-scroll">
      <div className="ap-env">
        <h2 className="ap-subj">{s.subject}</h2>
        {/* The send keeps To and Cc as one list (backend/email_sending.py `recipients`): Cc is not apart yet. */}
        <dl className="ap-hdr">
          {s.mailbox.includes('@') && (
            <>
              <dt>From</dt>
              <dd>{s.mailbox}</dd>
            </>
          )}
          <dt>To</dt>
          <dd>{s.recipients.join(', ')}</dd>
        </dl>
        {files.length > 0 && (
          <div className="ap-atts">
            {files.map((x) => (
              <Go key={x.part} to={at.to} search={{ ...at.search, part: x.part }} className="ap-att" title={`Open ${x.name}`}>
                <Icon name="file" />
                <span>
                  <b>{x.name}</b>
                  <small>
                    {extOf(x.name)} · {fileSize(x.size)}
                  </small>
                </span>
              </Go>
            ))}
          </div>
        )}
      </div>
      <div className="ap-desk">
        <article className="ap-paper ap-mail" aria-label="Email">
          {body}
        </article>
      </div>
    </div>
  );
}
