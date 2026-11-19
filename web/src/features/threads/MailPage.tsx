import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Count } from '@/kit/Count';
import { Empty } from '@/kit/Empty';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { VirtualList } from '@/kit/VirtualList';
import { ItemHead, ListAndItem } from '@/pagekinds';
import { letterAt, letterRows, letterUnread, senderName, type Letter, type LetterRow, type Text } from './model';
import { press } from './people';
import { letterQuery, mailboxesQuery, mailListQuery, textListQuery, textQuery } from './queries';
import { NewButton, threadTabs } from './ThreadsPage';
import './threads.css';

const TABS = threadTabs('/threads');
const q = (params: Record<string, string>) => `?${new URLSearchParams(params).toString()}`;

export interface MailPageProps {
  /** The mailbox read; the first one when left out. */
  box?: string;
  /** The open mail thread's id. */
  open?: string;
}

/** Mail: the company's mailboxes, a row per conversation, the open one in full. Read only. */
export function MailPage({ box, open }: MailPageProps) {
  const boxes = useQuery(mailboxesQuery);
  const current = box ?? boxes.data?.[0]?.address ?? '';
  const list = useInfiniteQuery(mailListQuery(current));
  const rows = letterRows(list.data?.pages.flatMap((p) => p.messages) ?? []);
  const state =
    boxes.isPending || (!!current && list.isPending)
      ? 'loading'
      : boxes.isError || list.isError
        ? 'fault'
        : rows.length === 0 && !open
          ? 'empty'
          : 'ready';
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  const choices = (boxes.data ?? []).map((b) => ({ value: b.address, label: b.name || b.address }));

  return (
    <ListAndItem
      title="Threads"
      path="/threads/mail"
      className="is-mail"
      actions={<NewButton />}
      search={
        choices.length > 1 ? (
          <div className="ml-box">
            <Select label="Mailbox" labelHidden options={choices} value={current} onValueChange={(v) => void press('mail.pickBox', { box: v })} />
          </div>
        ) : undefined
      }
      tabs={TABS}
      listLabel="Mail"
      list={
        <VirtualList
          className="tl-vl"
          label="Mail"
          items={rows}
          getKey={(r) => r.letter.thread_id}
          estimateSize={35}
          onReachEnd={more}
          renderItem={(r) => <MailRow r={r} box={current} selected={r.letter.thread_id === open} />}
        />
      }
      open={!!open}
      state={state}
      empty={{ title: boxes.data?.length === 0 ? 'No mailboxes yet' : 'No mail' }}
      onRetry={() => void (boxes.isError ? boxes.refetch() : list.refetch())}
      {...(open && current ? { itemHead: <LetterHead box={current} id={open} />, item: <LetterBody box={current} id={open} />, itemLabel: 'Mail' } : {})}
    />
  );
}

function MailRow({ r, box, selected }: { r: LetterRow; box: string; selected: boolean }) {
  const l = r.letter;
  const from = senderName(l);
  const subject = l.subject || '(no subject)';
  return (
    <Row
      className="ml-row"
      title={`${from}: ${subject}`}
      href={`/threads/mail${q({ box, open: l.thread_id })}`}
      selected={selected}
      unread={letterUnread(l)}
      target={`letter:${l.thread_id}`}
    >
      <Avatar kind="person" name={from} className="is-out" decorative />
      <span className="ml-main">
        <span className="ml-from">{from}</span>
        {r.count > 1 && <Count n={r.count} label={`${r.count} letters`} />}
        <span className="ml-sub">{subject}</span>
      </span>
      <Ago at={letterAt(l)} className="row-age" />
      <span className="ml-mark" />
    </Row>
  );
}

const BACK = { to: '/threads/mail', label: 'Mail' };

function LetterHead({ box, id }: { box: string; id: string }) {
  const { data } = useQuery(letterQuery(box, id));
  const first = data?.[0];
  const from = first ? senderName(first) : 'Mail';
  return (
    <ItemHead
      title={from}
      target={`letter:${id}`}
      back={BACK}
      lead={first ? <Avatar kind="person" name={from} className="is-out" size={22} /> : undefined}
    />
  );
}

function LetterBody({ box, id }: { box: string; id: string }) {
  const { data, isPending, isError, refetch } = useQuery(letterQuery(box, id));
  if (isPending) return null;
  if (isError)
    return (
      <Empty icon="alert" title="This mail did not load">
        <Button onClick={() => void refetch()}>Try again</Button>
      </Empty>
    );
  return (
    <>
      <div className="lt-top">
        <h3 className="lt-subject">
          {data[0]?.subject || '(no subject)'}
          {data.length > 1 && <Count n={data.length} label={`${data.length} letters`} />}
        </h3>
      </div>
      {data.map((l) => (
        <LetterPart key={l.msg_id} l={l} />
      ))}
    </>
  );
}

function LetterPart({ l }: { l: Letter }) {
  const from = senderName(l);
  return (
    <article className="lt">
      <div className="msg-meta">
        <Avatar kind="person" name={from} className="is-out" decorative />
        <b>{from}</b>
        {!!l.to?.length && <span className="lt-to">to {l.to.join(', ')}</span>}
        <Ago at={letterAt(l)} />
      </div>
      <div className="lt-body">{l.body ?? l.snippet}</div>
    </article>
  );
}

/* ---------------------------------------------------------------- Messaging */

export interface MessagingPageProps {
  /** The open text's id. */
  open?: string;
}

/** Messaging: texts the company's numbers received, newest first, the open one in full. Read only. */
export function MessagingPage({ open }: MessagingPageProps) {
  const list = useInfiniteQuery(textListQuery);
  const rows = list.data?.pages.flatMap((p) => p.messages) ?? [];
  const state = list.isPending ? 'loading' : list.isError ? 'fault' : rows.length === 0 && !open ? 'empty' : 'ready';
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  const shown = rows.find((r) => r.message_id === open);
  return (
    <ListAndItem
      title="Threads"
      path="/threads/messaging"
      actions={<NewButton />}
      tabs={TABS}
      listLabel="Messaging"
      list={
        <VirtualList
          className="tl-vl"
          label="Messaging"
          items={rows}
          getKey={(r) => r.message_id}
          estimateSize={35}
          onReachEnd={more}
          renderItem={(r) => <TextRow t={r} selected={r.message_id === open} />}
        />
      }
      open={!!open}
      state={state}
      empty={{ title: 'No texts yet' }}
      onRetry={() => void list.refetch()}
      {...(open
        ? {
            itemHead: <ItemHead title={shown?.from || 'Text'} target={`text:${open}`} back={{ to: '/threads/messaging', label: 'Messaging' }} />,
            item: <TextBody id={open} at={shown?.received_at} from={shown?.from} />,
            itemLabel: 'Text',
          }
        : {})}
    />
  );
}

function TextRow({ t, selected }: { t: Text; selected: boolean }) {
  const from = t.from || 'Unknown';
  return (
    <Row className="ml-row" title={`${from}: ${t.preview ?? ''}`} href={`/threads/messaging${q({ open: t.message_id })}`} selected={selected} target={`text:${t.message_id}`}>
      <Avatar kind="person" name={from} className="is-out" decorative />
      <span className="ml-main">
        <span className="ml-from">{from}</span>
        <span className="ml-sub">{t.preview}</span>
      </span>
      <Ago at={t.received_at} className="row-age" />
      <span className="ml-mark" />
    </Row>
  );
}

function TextBody({ id, at, from }: { id: string; at?: string; from?: string }) {
  const { data, isPending, isError, refetch } = useQuery(textQuery(id));
  if (isPending) return null;
  if (isError)
    return (
      <Empty icon="alert" title="This text did not load">
        <Button onClick={() => void refetch()}>Try again</Button>
      </Empty>
    );
  return (
    <article className="lt">
      <div className="msg-meta">
        <b>{from || 'Unknown'}</b>
        {at && <Ago at={at} />}
      </div>
      <div className="lt-body">{data.text}</div>
    </article>
  );
}
