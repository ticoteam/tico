import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { stamp } from '@/conversation';
import { FileFrame, fileSize } from '@/features/library/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { PageBones } from './FileView';
import { Go } from './parts';
import { addressesOf, answeredQuery, emailFileUrl, emailSendQuery, type EmailFile, type Letter, type MailSend, type TaskData } from './queries';
import { useMatterOf } from './Waiting';
import { isWord, WordPages, WordSheet } from './Word';

/* An email the task asks about, exactly as it will go (plan/approvals.html, "Email"): the envelope in Tico's look (its
   subject, From, To, Cc, the email it answers and its files as cards), then the message on paper and the letter it
   carries under it. A file opens in this same sheet in its place, and the address keeps which (`part`); a Word file
   is drawn as its pages, with Download beside it. */

/** "DOCX", "PDF": the type badge on a file's card. */
const extOf = (name: string) => /\.([a-z0-9]{1,5})$/i.exec(name)?.[1]?.toUpperCase() ?? 'FILE';

/** A file read as a document (a letter): a PDF, a Word file or a web page. */
const isDoc = (f: EmailFile) => isWord(f.name, f.type) || /^(application\/pdf|text\/html)\b/i.test(f.type ?? '') || /\.(pdf|html?)$/i.test(f.name);

export function Email({ task, send, part, at, lead }: { task: string; send: string; part?: number; at: { to: string; search: { tab?: string } }; lead?: ReactNode }) {
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
  const who = addressesOf(q.data);
  const letter = files.find(isDoc);
  return (
    <div className="ap-scroll">
      {/* What is read before the email: the case in brief, and the decision when it is shown on the matter. */}
      {lead}
      <div className="ap-env">
        <h2 className="ap-subj">{s.subject}</h2>
        <dl className="ap-hdr">
          {s.mailbox.includes('@') && (
            <>
              <dt>From</dt>
              <dd>{s.mailbox}</dd>
            </>
          )}
          <dt>To</dt>
          <dd>{who.to.join(', ')}</dd>
          {who.cc.length > 0 && (
            <>
              <dt>Cc</dt>
              <dd>{who.cc.join(', ')}</dd>
            </>
          )}
          {who.bcc.length > 0 && (
            <>
              <dt>Bcc</dt>
              <dd>{who.bcc.join(', ')}</dd>
            </>
          )}
          <Answered task={task} send={s} />
        </dl>
        {files.length > 0 && (
          <div className="ap-atts">
            {files.map((x) => (
              <Go key={x.part} to={at.to} search={{ ...at.search, part: x.part }} className="ap-att" title={`Open ${x.name}`}>
                <span className="ap-ft" aria-hidden="true">
                  <i>{extOf(x.name)}</i>
                </span>
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
        {letter && <LetterInline send={send} f={letter} />}
      </div>
    </div>
  );
}

/** The email's first document, read where it lies: a Word file as its sheet, a PDF or a web page in its frame. */
function LetterInline({ send, f }: { send: string; f: EmailFile }) {
  const url = emailFileUrl(send, f.part);
  if (isWord(f.name, f.type)) return <WordSheet url={url} name={f.name} />;
  return (
    <div className="ap-doc" aria-label={f.name} role="group">
      <FileFrame mime={f.type ?? ''} title={f.name} url={url} name={f.name} size={f.size} held />
    </div>
  );
}

/** "Dana Ruiz <dana@…>" → "Dana Ruiz"; a bare address stays. */
const senderOf = (l: Letter) => /^"?([^"<]+?)"?\s*</.exec((l.from_header ?? '').trim())?.[1]?.trim() || l.from_addr || l.from_header || '';

/** The email this one answers, a row of the envelope: who wrote it and when, its first line, the whole of it on a
    press. Not read, or not readable here (no mail connection, no read of that mailbox), the row is not shown: the send
    itself does not carry who wrote the original. */
function Answered({ task, send }: { task: string; send: MailSend }) {
  const q = useQuery(answeredQuery(task, send.id, send.mailbox, send.reply_to_id ?? ''));
  const [open, setOpen] = useState(false);
  const l = q.data;
  const text = (l?.body || l?.snippet || '').trim();
  if (!l || !text) return null;
  const at = l.epoch ? new Date(l.epoch * 1000) : l.date;
  const when = stamp(at);
  return (
    <div className={open ? 'ap-orig is-open' : 'ap-orig'} role="region" aria-label="In reply to">
      <dt>In reply to</dt>
      <dd>
        <button type="button" className="ap-orig-head" aria-expanded={open} onClick={() => setOpen(!open)}>
          <b>{[senderOf(l), when].filter(Boolean).join(', ')}</b>
          <Icon name="chevron" />
        </button>
        <div className="ap-orig-body">{text}</div>
      </dd>
    </div>
  );
}

/** The matter an email's OK came from (`useMatterOf`): one quiet link to it in the head's line, never to another OK. */
export function EmailSource({ t, send }: { t: TaskData; send: string }) {
  const matter = useMatterOf(t, send);
  if (!matter) return null;
  return (
    <>
      {' · '}
      <Go to={matter.to} className="tv-from">
        From {matter.title}
      </Go>
    </>
  );
}
