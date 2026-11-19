import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Count } from '@/kit/Count';
import { Empty } from '@/kit/Empty';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Markdown } from '@/kit/Markdown';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { VirtualList } from '@/kit/VirtualList';
import { ItemHead, ListAndItem } from '@/pagekinds';
import { letterAt, letterRows, letterUnread, senderName, type Letter, type LetterRow, type Text } from './model';
import { press } from './people';
import { letterQuery, mailboxesQuery, mailFindQuery, mailInstructionsQuery, textListQuery, textQuery } from './queries';
import { NewButton, threadTabs } from './ThreadsPage';
import './threads.css';

const TABS = threadTabs('/threads');
const q = (params: Record<string, string>) => `?${new URLSearchParams(params).toString()}`;

export interface MailPageProps {
  /** The mailbox read; the first one when left out. */
  box?: string;
  /** The open mail thread's id. */
  open?: string;
  /** The mailbox agent's instructions open instead of a mail. */
  view?: 'instructions';
  /** The words searched for (`?q=`). */
  q?: string;
}

/** Mail: the company's mailboxes, a row per conversation, the open one in full. Read only. */
export function MailPage({ box, open, view, q: words }: MailPageProps) {
  const boxes = useQuery(mailboxesQuery);
  const current = box ?? boxes.data?.[0]?.address ?? '';
  const agent = !!boxes.data?.find((b) => b.address === current)?.agent_instructions;
  const reading = view === 'instructions' && agent;
  const narrowed = !!words;
  const list = useInfiniteQuery(mailFindQuery(current, words));
  const rows = letterRows(list.data?.pages.flatMap((p) => p.messages) ?? []);
  // An opened mail keeps the words it was found by.
  const keep: Record<string, string> = words ? { q: words } : {};
  const choices = (boxes.data ?? []).map((b) => ({ value: b.address, label: b.name || b.address }));
  // With more than one mailbox an empty one stays a list: the pick (only shown on a ready list) leads to the others.
  const pick = choices.length > 1;
  const state =
    boxes.isPending || (!!current && list.isPending)
      ? 'loading'
      : boxes.isError || list.isError
        ? 'fault'
        : rows.length === 0 && !open && !agent && !pick && !narrowed
          ? 'empty'
          : 'ready';
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  return (
    <ListAndItem
      title="Threads"
      path="/threads/mail"
      className="is-mail"
      actions={<NewButton />}
      search={
        <>
          <FindMail value={words ?? ''} onFind={(next) => void press('mail.find', { box, q: next || undefined })} />
          {pick && (
            <div className="ml-box">
              <Select label="Mailbox" labelHidden options={choices} value={current} onValueChange={(v) => void press('mail.pickBox', { box: v })} />
            </div>
          )}
        </>
      }
      tabs={TABS}
      listLabel="Mail"
      list={
        <>
          {agent && (
            <Row
              className="tl-row tl-catch"
              title="Instructions"
              href={`/threads/mail${q({ box: current, view: 'instructions' })}`}
              selected={!!reading}
              target="page:/threads/mail?view=instructions"
            >
              <span className="tl-kind">
                <Icon name="rules" />
              </span>
              <span className="row-title">Instructions</span>
              <span />
              <span className="tl-slot" />
            </Row>
          )}
          {rows.length === 0 && narrowed && <Empty icon="search" title="No matches" />}
          {pick && rows.length === 0 && !agent && !narrowed && <Empty title="No mail" />}
          <VirtualList
            className="tl-vl"
            label="Mail"
            items={rows}
            getKey={(r) => r.letter.thread_id}
            estimateSize={35}
            onReachEnd={more}
            renderItem={(r) => <MailRow r={r} box={current} keep={keep} selected={!reading && r.letter.thread_id === open} />}
          />
        </>
      }
      open={!!open || !!reading}
      state={state}
      empty={{ title: boxes.data?.length === 0 ? 'No mailboxes yet' : 'No mail' }}
      onRetry={() => void (boxes.isError ? boxes.refetch() : list.refetch())}
      {...(reading
        ? {
            itemHead: <ItemHead title="Instructions" target="page:/threads/mail?view=instructions" back={BACK} />,
            item: <Instructions box={current} />,
            itemLabel: 'Instructions',
          }
        : open && current
          ? { itemHead: <LetterHead box={current} id={open} />, item: <LetterBody box={current} id={open} />, itemLabel: 'Mail' }
          : {})}
    />
  );
}

/** The search field, a step behind the typing: the address (and the server) hear the words once they settle. */
function FindMail({ value, onFind }: { value: string; onFind: (q: string) => void }) {
  const [words, setWords] = useState(value);
  // Cleared or set from elsewhere (Tico, Back): the field follows the address.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setWords(value);
  }
  useEffect(() => {
    const next = words.trim();
    if (next === value) return;
    const t = setTimeout(() => onFind(next), SEARCH_WAIT_MS);
    return () => clearTimeout(t);
  }, [words, value, onFind]);
  return <Search label="Find mail" placeholder="Find mail" value={words} onValueChange={setWords} wide />;
}

const SEARCH_WAIT_MS = 250;

function MailRow({ r, box, keep, selected }: { r: LetterRow; box: string; keep: Record<string, string>; selected: boolean }) {
  const l = r.letter;
  const from = senderName(l);
  const subject = l.subject || '(no subject)';
  return (
    <Row
      className="ml-row"
      title={`${from}: ${subject}`}
      href={`/threads/mail${q({ box, ...keep, open: l.thread_id })}`}
      selected={selected}
      unread={letterUnread(l)}
      target={`letter:${l.thread_id}`}
    >
      <Avatar kind="person" name={from} className="is-out" decorative />
      <span className="ml-main">
        <span className="ml-from">{from}</span>
        {r.count > 1 && <Count n={r.count} label={`${r.count} letters`} />}
        <span className="ml-sub">{subject}</span>
        {l.has_attachment && <Icon name="attach" label="Attachment" />}
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

/** What the mailbox's agent works from; written in the agent's own files, so read only here. */
function Instructions({ box }: { box: string }) {
  const { data, isPending, isError, refetch } = useQuery(mailInstructionsQuery(box));
  if (isPending) return null;
  if (isError)
    return (
      <Empty icon="alert" title="The instructions did not load">
        <Button onClick={() => void refetch()}>Try again</Button>
      </Empty>
    );
  if (!data.instructions.trim()) return <Empty icon="rules" title="No instructions" />;
  return (
    <article className="lt">
      <Markdown text={data.instructions} />
    </article>
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
