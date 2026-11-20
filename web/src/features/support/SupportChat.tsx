import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { reasonIn, run } from '@/actions';
import { Composer, Message, type ChatMessage } from '@/conversation';
import { configQuery, meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { SkeletonRows } from '@/kit/Skeleton';
import { VirtualList } from '@/kit/VirtualList';
import { fmtDate, newId } from '@/lib';
import { SUPPORT_CHAT } from './actions';
import { Diagnostics, type Attached } from './Diagnostics';
import { composeQuery, refreshQuery, ticketsQuery, type Retry, type SupportList } from './queries';
import './support.css';

const NAME = 'Tico support';
/* The conversation's Message tells the person's own rows by this id: support's list says "from person", not who. */
const YOU = 'support:you';
const STARTERS = ['An agent is stuck', 'Something on this page looks wrong', 'A question about my bill'];

const press = (id: string, input: unknown = {}) => run(id, input, { via: 'click' });

/** Tico support's face (pages/8-settings more.js supAv): the help mark in a grey circle. */
export function SupportFace({ size }: { size: 22 | 28 }) {
  return (
    <span className={`av av-${size} is-person sup-av`} aria-hidden="true">
      <Icon name="help" />
    </span>
  );
}

interface Row {
  m: ChatMessage;
  diag: boolean;
  retry?: Retry;
}

type Item = { k: 'day'; key: string; label: string } | { k: 'msg'; key: string; row: Row; cont: boolean };

/** Every ticket's messages as one conversation, oldest first; a ticket's first message is the person's. */
function linesOf(list: SupportList): Item[] {
  const rows: (Row & { i: number })[] = [];
  for (const t of list.tickets) {
    rows.push({
      i: rows.length,
      m: { id: `t:${t.id}`, sender_id: YOU, body: t.message, created_at: t.created_at, ...(t.pending && { pending: t.pending }) },
      diag: !!t.sent?.diagnostics,
      ...(t.retry && { retry: t.retry }),
    });
    for (const m of t.messages ?? []) {
      const mine = m.from !== 'staff';
      rows.push({
        i: rows.length,
        m: { id: `m:${t.id}:${m.id}`, sender_id: mine ? YOU : 'support', sender_name: NAME, body: m.body, created_at: m.created_at, ...(m.pending && { pending: m.pending }) },
        diag: mine && !!m.has_diagnostics,
        ...(m.retry && { retry: m.retry }),
      });
    }
  }
  rows.sort((a, b) => (Date.parse(a.m.created_at ?? '') || 0) - (Date.parse(b.m.created_at ?? '') || 0) || a.i - b.i);
  const out: Item[] = [];
  let day = '';
  let who = '';
  for (const row of rows) {
    const d = fmtDate(row.m.created_at ?? '');
    if (d && d !== day) {
      out.push({ k: 'day', key: `day:${row.m.id}`, label: d });
      day = d;
      who = '';
    }
    out.push({ k: 'msg', key: row.m.id, row, cont: who === row.m.sender_id });
    who = row.m.sender_id ?? '';
  }
  return out;
}

/** The second chat with Tico support (pages/8-settings more.js support): its head, the facts support needs, the
    conversation and the box. The root puts it in the shell's chat sheet when the address says `?chat=support`. */
export function SupportChat() {
  const list = useQuery(ticketsQuery);
  const on = list.data?.enabled === true;
  const refresh = useQuery({ ...refreshQuery, enabled: on });
  const compose = useQuery({ ...composeQuery, enabled: on });
  const { data: config } = useQuery(configQuery);
  const { data: me } = useQuery(meQuery);
  const [attached, setAttached] = useState<Attached | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  const [fill, setFill] = useState<{ text: string } | null>(null);

  // Every reply on screen is read; one that could not be marked waits for the chat to open again.
  const tried = useRef(new Set<string>());
  const unreadIds = (list.data?.tickets ?? [])
    .filter((t) => t.unread && !t.pending)
    .map((t) => `${t.id}:${t.messages?.length ?? 0}`)
    .join(',');
  useEffect(() => {
    if (!unreadIds || document.visibilityState !== 'visible') return;
    for (const mark of unreadIds.split(',')) {
      if (tried.current.has(mark)) continue;
      tried.current.add(mark);
      void press('support.markRead', { ticket: mark.slice(0, mark.lastIndexOf(':')) });
    }
  }, [unreadIds]);

  /* A retry goes again with its own key, to where it went first: a new conversation sent again stays one. */
  const send = async (message: string, retry?: Retry) => {
    setError('');
    const key = retry?.key ?? newId();
    const diagnostics = retry ? retry.diagnostics : attached?.bundle.id;
    const edited = retry ? retry.edited : attached?.edited;
    const ticket = retry?.to;
    if (!retry) setAttached(null);
    const r = diagnostics
      ? await press('support.sendDiagnostics', { message, key, diagnostics, ...(ticket && { ticket }), ...(edited !== undefined && { edited }) })
      : ticket === 'new'
        ? await press('support.newTicket', { message, key })
        : await press('support.send', { message, key, ...(ticket && { ticket }) });
    if (!r.ok) setError(`Not sent. ${reasonIn(r) ?? 'Try again.'}`);
  };

  const head = (
    <header className="side-head" data-target={`chat:${SUPPORT_CHAT}`}>
      <SupportFace size={28} />
      <b>{NAME}</b>
      <span className="grow" />
      {on && (
        <Menu trigger={<IconButton icon="more" label="More" quiet />}>
          <MenuItem label="Attach diagnostics" icon="file" onSelect={() => setReviewing(true)} />
          {list.data?.tickets.length ? (
            <>
              <MenuRule />
              <MenuItem label="Delete chat history" onSelect={() => setAsking(true)} />
            </>
          ) : null}
        </Menu>
      )}
      <IconButton icon="close" label="Close" quiet onClick={() => press('shell.closeChat')} />
    </header>
  );

  if (!list.data) {
    return (
      <>
        {head}
        {list.isError ? (
          <Empty icon="alert" title="Tico support did not load">
            <Button onClick={() => list.refetch()}>Try again</Button>
          </Empty>
        ) : (
          <div className="chat-body">
            <SkeletonRows rows={4} label="Loading Tico support" />
          </div>
        )}
      </>
    );
  }
  if (!on) {
    return (
      <>
        {head}
        <Empty icon="offline" title="Tico support is off on this server" />
      </>
    );
  }

  const items = linesOf(list.data);
  const company = config?.company_name || config?.app_name;
  const version = compose.data?.version;
  return (
    <>
      {head}
      {(company || version) && (
        <div className="sup-facts">
          <Facts>
            {company && <LabelValue label="Company">{company}</LabelValue>}
            {version && <LabelValue label="Version">{version}</LabelValue>}
          </Facts>
        </div>
      )}
      {items.length ? (
        <VirtualList
          items={items}
          getKey={(it) => it.key}
          estimateSize={64}
          measure
          anchor="end"
          role="log"
          label={`Chat with ${NAME}`}
          className="chat-body"
          renderItem={(it) =>
            it.k === 'day' ? (
              <div className="day">
                <span>{it.label}</span>
              </div>
            ) : (
              <>
                <Message
                  m={it.row.m}
                  variant="side"
                  me={YOU}
                  cont={it.cont}
                  onRetry={it.row.retry ? (m) => void send(m.body ?? '', it.row.retry) : undefined}
                />
                {it.row.diag && <small className="sup-note sup-sent">Diagnostics attached</small>}
              </>
            )
          }
        />
      ) : (
        <div className="chat-body">
          <div className="starters">
            <ul>
              {STARTERS.map((s) => (
                <li key={s}>
                  <button className="starter" type="button" onClick={() => setFill({ text: s })}>
                    <span>{s}</span>
                    <Icon name="arrow" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <footer className="chat-foot" data-dock>
        {refresh.data?.failed || refresh.isError ? (
          <p className="sup-note" role="status">
            Could not check for replies just now.
          </p>
        ) : null}
        {attached && (
          <div className="sup-attached">
            <Icon name="file" />
            <span>Diagnostics</span>
            <button type="button" className="lnk" onClick={() => setReviewing(true)}>
              Review
            </button>
            <IconButton icon="close" label="Remove diagnostics" size="sm" quiet onClick={() => setAttached(null)} />
          </div>
        )}
        {error && (
          <p className="sup-note sup-err" role="alert">
            {error}
          </p>
        )}
        <Composer
          id={SUPPORT_CHAT}
          me={me?.id ?? YOU}
          to={NAME}
          fill={fill}
          onSend={(text, files) => {
            if (files.length) {
              setError('Files cannot go to Tico support. Attach diagnostics instead.');
              setFill({ text });
              return;
            }
            void send(text);
          }}
        />
      </footer>
      <Diagnostics open={reviewing} onOpenChange={setReviewing} attached={attached} onAttach={setAttached} />
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title="Delete chat history?"
        description="Every message and its diagnostics, here and at Tico support."
        actions={
          <>
            <Button onClick={() => setAsking(false)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={async () => {
                setAsking(false);
                const r = await press('support.deleteHistory');
                if (!r.ok) setError(reasonIn(r) ?? 'Could not delete. Try again.');
              }}
            >
              Delete
            </Button>
          </>
        }
      />
    </>
  );
}
