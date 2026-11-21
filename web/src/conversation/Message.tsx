import { useQuery } from '@tanstack/react-query';
import { useState, type MouseEvent, type ReactElement, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { meQuery, useActor } from '@/data';
import { Avatar, type AvatarKind, type AvatarSize } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { ConfirmCard } from '@/kit/ConfirmCard';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ContextMenu, MenuItem } from '@/kit/Menu';
import { useCards } from '@/kit/Popover';
import { SkeletonRows } from '@/kit/Skeleton';
import { toast } from '@/kit/Toast';
import { Dot } from '@/tico/dot';
import { clock, took } from './dates';
import { Markdown } from '@/kit/Markdown';
import { PictureViewer, type Picture } from '@/kit/PictureViewer';
import { Viewer } from '@/kit/Viewer';
import { agentWords, Quote } from './Quote';
import { isPicture, linkRender, richText, Shelf } from './Rich';
import { fromSlack, proposalQuery, slackPlaceOf, slackThreadQuery, type SlackPlace } from './queries';
import type { Attachment, ChatMessage, LiveRun, ReplyQuote, Variant } from './types';

export interface Who {
  name: string;
  kind: AvatarKind | 'tico';
  hue: number;
}

/** A teammate's avatar, or Tico's face: the Dot in its `.av-dot` frame (parts-kit.js av()), drawn still here. */
export function Face({ who, size, live }: { who: Who; size: AvatarSize; live?: boolean }) {
  if (who.kind !== 'tico') return <Avatar kind={who.kind} name={who.name} hue={who.hue} size={size} live={live} />;
  const d = Math.round(size * 0.86);
  return <Avatar kind="agent" name={who.name} size={size} live={live} dot={live ? <Dot size={d} state="rest" /> : <Dot size={d} still />} />;
}

const hueOf = (s: string) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
};


/**
 * Who said it. Tico's own sheet speaks as Tico. A name is what the message carries, else `known` (the directory's name
 * for the sender, which a row from the stream or a reply being written needs), else the agent's slug in words.
 */
export function whoOf(m: Pick<ChatMessage, 'sender_id' | 'sender_name'>, variant: Variant, me: string | undefined, known?: string): Who {
  const actor = m.sender_id ?? '';
  if (actor && actor === me) return { name: 'You', kind: 'person', hue: hueOf(actor) };
  if (actor.startsWith('bot:') || actor === 'keeper') {
    if (variant === 'tico' || actor === 'keeper') return { name: 'Tico', kind: 'tico', hue: 0 };
    return { name: m.sender_name || known || agentWords(actor), kind: 'agent', hue: hueOf(actor) };
  }
  return { name: m.sender_name || known || 'Person', kind: 'person', hue: hueOf(actor) };
}

/* ---------------------------------------------------------------- parts inside a message */

const MD_PICTURE = /!\[([^\]]*)\]\((\/api\/[^()\s]+)\)/g;
const pictureHref = (f: Attachment) => (typeof f.url === 'string' && /^\/api\/v2\/files\//.test(f.url) ? f.url : `/api/v2/files/${encodeURIComponent(f.id)}`);

/** Every picture a message holds, in the order it reads: those in its text, then its attached ones. */
export function picturesOf(m: Pick<ChatMessage, 'body' | 'refs' | 'pending' | 'deleted'>): Picture[] {
  if (m.pending || m.deleted) return [];
  const inText = [...(m.body ?? '').matchAll(MD_PICTURE)].map((x) => ({ src: x[2]!, name: x[1] || 'Picture' }));
  const attached = (m.refs?.attachments ?? []).filter(isPicture).map((f) => ({ src: pictureHref(f), name: f.title?.trim() || f.name || 'Picture' }));
  return [...inText, ...attached];
}

/** A message's pictures open full size over the page, not in a new tab; the arrows walk the message's others. */
function usePictures(m: ChatMessage) {
  const [at, setAt] = useState<number | null>(null);
  const pictures = picturesOf(m);
  const openAt = (src: string) => {
    const i = pictures.findIndex((p) => p.src === src);
    if (i >= 0) setAt(i);
    return i >= 0;
  };
  // A picture drawn in the text is an <img> the Markdown made: a click on it opens it.
  const onClick = (e: MouseEvent) => {
    const img = (e.target as Element).closest?.('img.md-img');
    if (img && openAt(img.getAttribute('src') ?? '')) e.preventDefault();
  };
  const viewer = pictures.length ? <PictureViewer pictures={pictures} at={at} onAtChange={setAt} /> : null;
  return { openAt, onClick, viewer };
}

/** Past this many characters a message is a report: Read shows it full screen as a page (never cut in the chat). */
const LONG = 1500;

/** A long message as a page of its own, in the full-screen layer: its words at a reading measure. */
function Reading({ text, title, onClose }: { text: string; title: string; onClose: () => void }) {
  return (
    <Viewer open onClose={onClose} title={title}>
      <div className="vw-scroll">
        <div className="msg msg-read">
          <div className="msg-main">
            <Markdown text={richText(text)} compact link={linkRender} />
          </div>
        </div>
      </div>
    </Viewer>
  );
}

const slackHref = (m: ChatMessage) => {
  const link = m.refs?.slack?.permalink;
  return typeof link === 'string' && /^https:\/\//.test(link) ? link : null;
};
const slackChannel = (m: ChatMessage) => (m.refs?.slack?.channel_name ? `#${m.refs.slack.channel_name.replace(/^#/, '')}` : '');

/** Said in Slack: where, as a link to the message there. The owner reads the whole Slack thread here instead. A message
    carried by a linked Slack channel keeps only its channel and time there (backend/slack_threads.py): the mark then
    stands without a link. */
function FromSlack({ m, onRead }: { m: ChatMessage; onRead: () => void }) {
  const { data: me } = useQuery(meQuery);
  if (!fromSlack(m)) return null;
  const href = slackHref(m);
  const readable = me?.role === 'owner' && !!slackPlaceOf(m);
  const where = slackChannel(m) ? `Slack ${slackChannel(m)}` : 'Slack';
  if (!readable && !href)
    return (
      <span className="fold">
        <Icon name="threads" />
        <span>{where}</span>
      </span>
    );
  if (!readable)
    return (
      <a className="fold msg-from" href={href!} target="_blank" rel="noreferrer" title="Open in Slack">
        <Icon name="popout" />
        <span>{where}</span>
      </a>
    );
  return (
    <button type="button" className="fold msg-from" onClick={onRead}>
      <Icon name="threads" />
      <span>{where}</span>
    </button>
  );
}

const slackTime = (ts: string) => {
  const t = Number(ts) * 1000;
  return Number.isFinite(t) ? clock(new Date(t).toISOString()) : '';
};

/** The Slack thread a message came from, every message in full, as the server keeps it. */
function SlackThread({ place, m, onClose }: { place: SlackPlace; m: ChatMessage; onClose: () => void }) {
  const q = useQuery(slackThreadQuery(place));
  const lines = q.data ?? [];
  const link = slackHref(m) ?? lines[0]?.url ?? null;
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title="Slack thread"
      description={slackChannel(m) || undefined}
      actions={
        <>
          {link && /^https:\/\//.test(link) && (
            <a className="btn" href={link} target="_blank" rel="noreferrer">
              Open in Slack
            </a>
          )}
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      {q.isPending ? (
        <SkeletonRows rows={3} />
      ) : q.isError || lines.length === 0 ? (
        <Empty title={q.isError ? "Couldn't load the Slack thread" : 'No messages'} />
      ) : (
        <div className="st-list">
          {lines.map((l) => (
            <div key={l.event_id} className="msg">
              <Avatar kind="person" name={l.author || 'Slack'} size={22} />
              <div className="msg-main">
                <div className="msg-meta">
                  <b>{l.author || 'Slack'}</b>
                  <time>{slackTime(l.ts)}</time>
                </div>
                <p className="st-text">{l.deleted ? 'Removed' : l.text}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}

/** "3 steps · 21s": the work behind a reply. */
function Steps({ run }: { run: ChatMessage['run'] }) {
  if (!run?.steps) return null;
  return (
    <span className="fold">
      <Icon name="chevron" />
      <span>{run.steps === 1 ? '1 step' : `${run.steps} steps`}</span>
      {run.took_s != null && <time>{took(run.took_s)}</time>}
    </span>
  );
}

const ASKED: Record<string, string> = { done: 'Done', running: 'Doing it now', failed: 'Did not work', canceled: 'Turned down', expired: 'Expired' };

/** An agent asks first: the filled button names the act, "Not now" turns it down. Answered, one quiet line. */
function Proposal({ aid }: { aid: string }) {
  const { data } = useQuery(proposalQuery(aid));
  const yes = useAction('conversation.confirm');
  const no = useAction('conversation.cancel');
  if (!data) return null;
  if (data.status !== 'pending') return <p className="ask-said">{ASKED[data.status] ?? data.status}</p>;
  return (
    <div data-target={`proposal:${aid}`}>
      <ConfirmCard
        mark={<Dot state="wait" size={12} />}
        question={data.description || data.summary}
        steps={[]}
        act={data.summary}
        busy={yes.pending || no.pending}
        onAct={() => yes.run({ action: aid })}
        onNotNow={() => no.run({ action: aid })}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- lines */

export interface MessageProps {
  m: ChatMessage;
  variant: Variant;
  me: string | undefined;
  /** Same sender as the row above: no face, no name. */
  cont?: boolean;
  onRetry?: (m: ChatMessage) => void;
  /** What to draw in place of its text (a call's card), or nothing for a plain message. */
  card?: (m: ChatMessage) => ReactNode;
  /** What to draw under its words when someone else wrote it (Tico's approval card). */
  below?: (m: ChatMessage) => ReactNode;
  /** Reply to it: the quote goes above the box. Left out where the person cannot write. */
  onReply?: (m: ChatMessage) => void;
  /** What it answers, drawn above it. */
  quote?: ReplyQuote | null;
  /** Go to the message a quote names. */
  onQuote?: (id: string) => void;
}

const copy = (text: string) =>
  void (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject(new Error('no clipboard'))).then(
    () => toast({ text: 'Copied' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );

/**
 * On a phone there is no hover: a long press on a message opens its actions as a sheet, the same ones a computer
 * shows on it (Reply), and Copy, since the press takes the place of selecting the text.
 */
function Held({ text, reply, read, children }: { text: string; reply: (() => void) | null; read: (() => void) | null; children: ReactElement }) {
  const cards = useCards();
  if (!cards || (!text && !reply)) return children;
  return (
    <ContextMenu
      items={
        <>
          {text && <MenuItem icon="copy" label="Copy" onSelect={() => copy(text)} />}
          {reply && <MenuItem icon="reply" label="Reply" onSelect={reply} />}
          {read && <MenuItem icon="expand" label="Read" onSelect={read} />}
        </>
      }
    >
      {children}
    </ContextMenu>
  );
}

/** Reply, and Read for a long one, on the message under the pointer (a phone has the long press instead). */
function Acts({ reply, read }: { reply: (() => void) | null; read: (() => void) | null }) {
  if (!reply && !read) return null;
  return (
    <div className="msg-acts">
      {read && <IconButton icon="expand" label="Read" size="sm" quiet onClick={read} />}
      {reply && <IconButton icon="reply" label="Reply" size="sm" quiet onClick={reply} />}
    </div>
  );
}

/** What someone said. Yours sits in a bubble at the right; a failed send says so and offers Retry. */
export function Message({ m, variant, me, cont, onRetry, card, below, onReply, quote, onQuote }: MessageProps) {
  const [reading, setReading] = useState(false);
  const [full, setFull] = useState(false);
  const known = useActor(m.sender_name ? undefined : m.sender_id)?.name;
  const text = m.deleted ? 'Deleted' : (m.body ?? '');
  const shown = m.deleted ? null : card?.(m);
  // What Copy takes: the words as written, not a card drawn in their place.
  const words = m.deleted || m.pending || shown ? '' : text.trim();
  const pics = usePictures(m);
  const place = reading ? slackPlaceOf(m) : null;
  const real = !m.pending && !m.deleted && !m.id.startsWith('tmp:');
  const reply = real && onReply ? () => onReply(m) : null;
  const quoted = quote && !m.deleted ? <Quote q={quote} me={me} onOpen={onQuote} /> : null;
  const read = words.length > LONG ? () => setFull(true) : null;
  const md = <Markdown text={richText(text)} compact link={m.pending ? undefined : linkRender} />;
  // What opens over the page stays outside the long-pressed row: a press inside it is not a press on the message.
  const over = (
    <>
      {pics.viewer}
      {place && <SlackThread place={place} m={m} onClose={() => setReading(false)} />}
      {full && <Reading text={text} title={whoOf(m, variant, me, known).name} onClose={() => setFull(false)} />}
    </>
  );
  if (m.pending || (me && m.sender_id === me)) {
    const failed = m.pending === 'failed' || m.pending === 'held';
    return (
      <>
        <Held text={words} reply={reply} read={read}>
          <div className={['msg', 'is-mine', cont && 'is-cont', failed && 'is-failed'].filter(Boolean).join(' ')} data-target={`message:${m.id}`}>
            <Acts reply={reply} read={read} />
            <div className="mine-col" onClick={pics.onClick}>
              {quoted}
              {shown ??
                (text && (
                  <div className="bubble">{md}</div>
                ))}
              <Shelf m={m} onPicture={pics.openAt} />
              {failed && (
                <div className="msg-fail">
                  <Icon name="alert" />
                  <span>{m.pending === 'held' ? 'Held' : 'Not sent'}</span>
                  {m.pending === 'failed' && onRetry && (
                    <button type="button" onClick={() => onRetry(m)}>
                      Retry
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </Held>
        {over}
      </>
    );
  }
  const who = whoOf(m, variant, me, known);
  const aid = typeof m.refs?.action === 'string' ? m.refs.action : null;
  return (
    <>
      <Held text={words} reply={reply} read={read}>
        <div className={['msg', cont && 'is-cont'].filter(Boolean).join(' ')} data-target={`message:${m.id}`}>
          <Acts reply={reply} read={read} />
          {cont ? <span className="av-gap av-22" /> : <Face who={who} size={22} />}
          <div className="msg-main" onClick={pics.onClick}>
            {!cont && (
              <div className="msg-meta">
                <b>{who.name}</b>
                <time dateTime={m.created_at}>{clock(m.created_at)}</time>
              </div>
            )}
            {quoted}
            {shown ?? (text && md)}
            {!m.deleted && below?.(m)}
            <Steps run={m.run} />
            <Shelf m={m} onPicture={pics.openAt} />
            {aid && <Proposal aid={aid} />}
            {m.refs?.slack && <FromSlack m={m} onRead={() => setReading(true)} />}
          </div>
        </div>
      </Held>
      {over}
    </>
  );
}

/** A reply being written: its text so far, or what it is doing, with the typing dots. Heard once, when posted. */
export function LiveRow({ r, variant, text }: { r: LiveRun; variant: Variant; text: string }) {
  const who = whoOf({ sender_id: r.bot }, variant, undefined, useActor(r.bot)?.name);
  const dots = (
    <span className="typing">
      <i />
      <i />
      <i />
    </span>
  );
  return (
    <div className={['msg', 'is-live'].filter(Boolean).join(' ')} aria-hidden="true">
      <Face who={who} size={22} live />
      <div className="msg-main">
        <div className="msg-meta">
          <b>{who.name}</b>
        </div>
        {text ? <Markdown text={text} compact tail={r.ended ? null : dots} /> : <p>{r.doing === 'work' ? 'Working' : 'Thinking'}{dots}</p>}
      </div>
    </div>
  );
}
