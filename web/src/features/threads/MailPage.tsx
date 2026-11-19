import { useInfiniteQuery, useQueries, useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { defaultStringifySearch } from '@tanstack/react-router';
import { useState } from 'react';
import { ApiError } from '@/api/client';
import { clock, Composer, dayKey, dayLabel, MessageBones, stamp } from '@/conversation';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Count } from '@/kit/Count';
import { Empty } from '@/kit/Empty';
import { SettledSearch } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Markdown } from '@/kit/Markdown';
import { Menu, MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { VirtualList } from '@/kit/VirtualList';
import { ItemHead, ListAndItem, PageMore, type ItemHeadProps } from '@/pagekinds';
import { letterAt, letterRows, letterUnread, senderName, type Letter, type LetterRow, type Text } from './model';
import { ChatWith, press, useMe } from './people';
import { letterQuery, mailboxesQuery, mailFindQuery, mailInstructionsQuery, textListQuery, textQuery, textsBySenderQuery, type Mailbox } from './queries';
import { NewButton, useThreadTabs } from './ThreadsPage';
import './threads.css';

const DRAFT = 'DRAFT';

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

/** Mail: the company's mailboxes, a row per conversation, the open one in full with a reply box. */
export function MailPage({ box, open, view, q: words }: MailPageProps) {
  const tabs = useThreadTabs('/threads');
  const boxes = useSuspenseQuery(mailboxesQuery).data;
  const current = box ?? boxes[0]?.address ?? '';
  const mailbox = boxes.find((b) => b.address === current);
  const agent = !!mailbox?.agent_instructions;
  const reading = view === 'instructions' && agent;
  const narrowed = !!words;
  // Nothing can read a mailbox until someone connects Google for it: ask for nothing, show the way there.
  const cut = mailbox?.connection === 'none';
  const list = useInfiniteQuery(mailFindQuery(cut ? '' : current, words));
  const rows = letterRows(list.data?.pages.flatMap((p) => p.messages) ?? []);
  // An opened mail keeps the words it was found by.
  const keep: Record<string, string> = words ? { q: words } : {};
  const back = { to: '/threads/mail', label: 'Mail', search: { box: current, q: words } };
  // With more than one mailbox an empty one stays a list: the filter in the head leads to the others.
  const pick = boxes.length > 1;
  const none = boxes.length === 0;
  const unconnected = cut || (list.error instanceof ApiError && list.error.code === 'not_connected');
  const state =
    none || unconnected
      ? 'empty'
      : current && list.isPending
        ? 'loading'
        : list.isError
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
      actions={
        <>
          {pick && (
            <Menu trigger={<IconButton icon="filter" label="Mailbox" />}>
              {boxes.map((b) => (
                <MenuItem key={b.address} label={b.name || b.address} on={b.address === current} onSelect={() => void press('mail.pickBox', { box: b.address })} />
              ))}
            </Menu>
          )}
          <NewButton />
        </>
      }
      search={<SettledSearch label="Find mail" placeholder="Find mail" value={words ?? ''} onSettle={(next) => void press('mail.find', { box, q: next || undefined })} wide />}
      tabs={tabs}
      listLabel="Mail"
      list={
        <>
          {agent && (
            <Row
              className="tl-row tl-catch"
              title="Instructions"
              href={`/threads/mail${defaultStringifySearch({ box: current, ...keep, view: 'instructions' })}`}
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
      empty={
        none || unconnected
          ? {
              title: unconnected ? 'Mail is not connected' : 'No mail yet',
              action: <Button onClick={() => void press('tools.open', { tool: 'mailbox' })}>Connect mail</Button>,
            }
          : { title: 'No mail' }
      }
      onRetry={() => void list.refetch()}
      {...(reading
        ? {
            itemHead: <ItemHead title="Instructions" target="page:/threads/mail?view=instructions" back={back} />,
            item: <Instructions box={current} />,
            itemLabel: 'Instructions',
          }
        : open && mailbox
          ? {
              itemHead: <LetterHead box={mailbox} id={open} back={back} />,
              item: <LetterBody box={current} id={open} />,
              foot: mailbox.may?.send !== false && <Reply box={current} id={open} />,
              itemLabel: 'Mail',
            }
          : {})}
    />
  );
}


function MailRow({ r, box, keep, selected }: { r: LetterRow; box: string; keep: Record<string, string>; selected: boolean }) {
  const l = r.letter;
  const from = senderName(l);
  const subject = l.subject || '(no subject)';
  return (
    <Row
      className="ml-row"
      title={`${from}: ${subject}`}
      href={`/threads/mail${defaultStringifySearch({ box, ...keep, open: l.thread_id })}`}
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

/** The open mail's head: who wrote first and their address, the mailbox's agent, Archive and ⋯. */
function LetterHead({ box, id, back }: { box: Mailbox; id: string; back: ItemHeadProps['back'] }) {
  const { data } = useQuery(letterQuery(box.address, id));
  const first = data?.find((l) => !l.labels?.includes(DRAFT));
  const last = data?.at(-1);
  const from = first ? senderName(first) : 'Mail';
  const organize = box.may?.organize !== false;
  return (
    <ItemHead
      title={from}
      target={`letter:${id}`}
      back={back}
      lead={first ? <Avatar kind="person" name={from} className="is-out" size={28} /> : undefined}
      actions={
        <>
          {first?.from_addr && first.from_addr !== from && <span className="lt-addr">{first.from_addr}</span>}
          {typeof box.agent_bot === 'string' && <ChatWith actor={`bot:${box.agent_bot}`} />}
          {organize && data && (
            <IconButton icon="archive" label="Archive" onClick={() => void press('mail.archive', { box: box.address, letter: id, ids: data.map((l) => l.msg_id) })} />
          )}
        </>
      }
      more={
        organize && last ? (
          <PageMore>
            <MenuItem label="Mark unread" icon="updates" onSelect={() => void press('mail.markUnread', { box: box.address, letter: last.msg_id })} />
          </PageMore>
        ) : undefined
      }
    />
  );
}

function LetterBody({ box, id }: { box: string; id: string }) {
  const { data, isPending, isError, refetch } = useQuery(letterQuery(box, id));
  if (isPending) return <MessageBones label="Loading mail" className="lt-bones" />;
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

/** The reply box under an open mail: what is typed is drafted in the thread and sent through the send gate. */
function Reply({ box, id }: { box: string; id: string }) {
  const me = useMe();
  const { data } = useQuery(letterQuery(box, id));
  if (!me) return null;
  // The mail API returns oldest first and replies to the newest letter from outside the mailbox, if there is one.
  const mailbox = box.toLowerCase();
  const to = data?.findLast((l) => (l.from_addr ?? '').toLowerCase() !== mailbox) ?? data?.at(-1);
  const name = to?.from_addr ? senderName(to) : 'this mail';
  return (
    <Composer
      id={`mail:${id}`}
      me={me}
      to={name}
      label={`Reply to ${name}`}
      attach={false}
      onSend={(text) => void press('mail.reply', { box, letter: id, text })}
    />
  );
}

/** What the mailbox's agent works from; written in the agent's own files, so read only here. */
function Instructions({ box }: { box: string }) {
  const { data, isPending, isError, refetch } = useQuery(mailInstructionsQuery(box));
  if (isPending) return <MessageBones label="Loading instructions" className="lt-bones" />;
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

const size = (n: number) => (n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function LetterPart({ l }: { l: Letter }) {
  const from = senderName(l);
  const draft = l.labels?.includes(DRAFT);
  return (
    <article className={draft ? 'lt is-draft' : 'lt'}>
      <div className="msg-meta">
        <Avatar kind="person" name={from} className="is-out" decorative />
        <b>{from}</b>
        {!!l.to?.length && <span className="lt-to">to {l.to.join(', ')}</span>}
        {draft ? <Pill>Draft</Pill> : <time dateTime={new Date(letterAt(l)).toISOString()}>{stamp(letterAt(l))}</time>}
      </div>
      <div className="lt-body">{l.body ?? l.snippet}</div>
      {l.attachments?.map((f, i) => (
        <span key={`${f.name}:${i}`} className="file">
          <Icon name="file" />
          <span>{f.name}</span>
          {!!f.size && <em>{size(f.size)}</em>}
        </span>
      ))}
    </article>
  );
}

/* ---------------------------------------------------------------- Messaging */

export interface MessagingPageProps {
  /** The open contact: who the texts came from. */
  open?: string;
}

/** Messaging: one row per contact who texted the company's numbers, newest first; the open one as a chat. Read only:
    the server receives texts and sends none. */
export function MessagingPage({ open }: MessagingPageProps) {
  const tabs = useThreadTabs('/threads');
  const [words, setWords] = useState('');
  const list = useInfiniteQuery(words ? textsBySenderQuery(words) : textListQuery);
  const find = words.toLowerCase();
  const messages = list.data?.pages.flatMap((p) => p.messages) ?? [];
  const contacts = contactsOf(find ? messages.filter((t) => (t.from ?? '').toLowerCase().includes(find)) : messages);
  const more = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  return (
    <ListAndItem
      title="Threads"
      path="/threads/messaging"
      actions={<NewButton />}
      search={<SettledSearch label="Find a conversation" placeholder="Find a conversation" value={words} onSettle={setWords} wide />}
      tabs={tabs}
      listLabel="Messaging"
      list={
        contacts.length === 0 && words ? (
          <Empty icon="search" title="No matches" />
        ) : (
          <VirtualList
            className="tl-vl"
            label="Messaging"
            items={contacts}
            getKey={(t) => t.from ?? t.message_id}
            estimateSize={35}
            onReachEnd={more}
            renderItem={(t) => <ContactRow t={t} selected={t.from === open} />}
          />
        )
      }
      open={!!open}
      state={list.isPending ? 'loading' : list.isError ? 'fault' : contacts.length === 0 && !open && !words ? 'empty' : 'ready'}
      empty={{ title: 'No texts yet' }}
      onRetry={() => void list.refetch()}
      {...(open
        ? {
            itemHead: (
              <ItemHead
                title={open}
                target={`contact:${open}`}
                back={{ to: '/threads/messaging', label: 'Messaging' }}
                lead={<Avatar kind="person" name={open} className="is-out" size={28} />}
              />
            ),
            item: <Texts from={open} />,
            itemLabel: open,
          }
        : {})}
    />
  );
}

/** The newest text from each contact, newest first. */
function contactsOf(texts: Text[]): Text[] {
  const by = new Map<string, Text>();
  for (const t of texts) if (!by.has(t.from ?? '')) by.set(t.from ?? '', t);
  return [...by.values()];
}

function ContactRow({ t, selected }: { t: Text; selected: boolean }) {
  const from = t.from || 'Unknown';
  return (
    <Row className="tl-row" title={from} href={`/threads/messaging${defaultStringifySearch({ open: from })}`} selected={selected} target={`contact:${from}`}>
      <Avatar kind="person" name={from} className="is-out" decorative />
      <span className="row-title">{from}</span>
      <Ago at={t.received_at} className="row-age" />
      <span className="tl-slot" />
    </Row>
  );
}

/** One contact's texts, oldest first, with a break at each day; each text in full. */
function Texts({ from }: { from: string }) {
  const list = useInfiniteQuery(textsBySenderQuery(from));
  const texts = (list.data?.pages.flatMap((p) => p.messages.filter((t) => t.from === from)) ?? []).reverse();
  const full = useQueries({ queries: texts.map((t) => textQuery(t.message_id)) });
  if (list.isPending) return <MessageBones label="Loading texts" className="lt-bones" />;
  if (list.isError)
    return (
      <Empty icon="alert" title="These texts did not load">
        <Button onClick={() => void list.refetch()}>Try again</Button>
      </Empty>
    );
  return (
    <div className="mg-list">
      {list.hasNextPage && (
        <Button onClick={() => void list.fetchNextPage()} busy={list.isFetchingNextPage}>
          Older texts
        </Button>
      )}
      {texts.map((t, i) => {
        const brk = i === 0 || dayKey(t.received_at) !== dayKey(texts[i - 1]?.received_at);
        return (
          <div key={t.message_id}>
            {brk && (
              <div className="day">
                <span>{dayLabel(t.received_at)}</span>
              </div>
            )}
            <div className="msg">
              <Avatar kind="person" name={from} className="is-out" size={28} decorative />
              <div className="msg-main">
                <div className="msg-meta">
                  <b>{from}</b>
                  <time dateTime={t.received_at}>{clock(t.received_at)}</time>
                </div>
                <p className="lt-body">{full[i]?.data?.text ?? t.preview}</p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
