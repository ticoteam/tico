import { useQuery } from '@tanstack/react-query';
import { useState, type MouseEvent } from 'react';
import { useAction } from '@/actions';
import { Avatar, type AvatarKind, type AvatarSize } from '@/kit/Avatar';
import { ConfirmCard } from '@/kit/ConfirmCard';
import { Fault } from '@/kit/Fault';
import { Icon } from '@/kit/Icon';
import { Dot } from '@/tico/dot';
import { clock, took } from './dates';
import { Markdown } from '@/kit/Markdown';
import { PictureViewer, type Picture } from '@/kit/PictureViewer';
import { Rate } from './Feedback';
import { proposalQuery, type Feedback } from './queries';
import type { Attachment, ChatMessage, LiveRun, Variant } from './types';

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

const titled = (slug: string) => slug.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

/**
 * Who said it. Tico's own sheet speaks as Tico. Without a directory of teammates (not in this module) a name falls
 * back to what the message carries, then to the agent's slug.
 */
export function whoOf(m: Pick<ChatMessage, 'sender_id' | 'sender_name'>, variant: Variant, me: string | undefined): Who {
  const actor = m.sender_id ?? '';
  if (actor && actor === me) return { name: 'You', kind: 'person', hue: hueOf(actor) };
  if (actor.startsWith('bot:') || actor === 'keeper') {
    if (variant === 'tico' || actor === 'keeper') return { name: 'Tico', kind: 'tico', hue: 0 };
    return { name: m.sender_name || titled(actor.slice(4)), kind: 'agent', hue: hueOf(actor) };
  }
  return { name: m.sender_name || 'Teammate', kind: 'person', hue: hueOf(actor) };
}

/* ---------------------------------------------------------------- parts inside a message */

const fileHref = (f: Attachment) => `/api/v2/files/${encodeURIComponent(f.id)}`;
const fileMeta = (f: Attachment) => (f.content_type?.split('/')[1] ?? '').toUpperCase();
const fileLabel = (f: Attachment) => f.title?.trim() || f.name || 'File';
const isPicture = (f: Attachment) => /^image\//.test(f.content_type ?? '');
const MD_PICTURE = /!\[([^\]]*)\]\((\/api\/[^()\s]+)\)/g;

/** Every picture a message holds, in the order it reads: those in its text, then its attached ones. */
export function picturesOf(m: Pick<ChatMessage, 'body' | 'refs' | 'pending' | 'deleted'>): Picture[] {
  if (m.pending || m.deleted) return [];
  const inText = [...(m.body ?? '').matchAll(MD_PICTURE)].map((x) => ({ src: x[2]!, name: x[1] || 'Picture' }));
  const attached = (m.refs?.attachments ?? []).filter(isPicture).map((f) => ({ src: fileHref(f), name: fileLabel(f) }));
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

function Files({ files, pending, onPicture }: { files: Attachment[] | undefined; pending: boolean; onPicture?: (src: string) => boolean }) {
  if (!files?.length) return null;
  return files.map((f) => {
    const label = fileLabel(f);
    const inner = (
      <>
        <Icon name="file" />
        <span>{label}</span>
        {fileMeta(f) && <em>{fileMeta(f)}</em>}
      </>
    );
    if (pending) return <span key={f.id} className="file">{inner}</span>;
    const href = fileHref(f);
    const open =
      isPicture(f) && onPicture
        ? (e: MouseEvent) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey) return;
            if (onPicture(href)) e.preventDefault();
          }
        : undefined;
    return (
      <a key={f.id} className="file" href={href} target="_blank" rel="noreferrer" onClick={open}>
        {inner}
      </a>
    );
  });
}

/** Said in Slack: a link to the message there (the app cannot read a Slack thread for a person yet). */
function FromSlack({ m }: { m: ChatMessage }) {
  const s = m.refs?.slack;
  const href = typeof s?.permalink === 'string' && /^https:\/\//.test(s.permalink) ? s.permalink : null;
  if (!href) return null;
  const where = s?.channel_name ? `#${s.channel_name.replace(/^#/, '')}` : 'Slack';
  return (
    <a className="fold msg-from" href={href} target="_blank" rel="noreferrer" title="Open in Slack">
      <Icon name="popout" />
      <span>{where === 'Slack' ? 'Slack' : `Slack ${where}`}</span>
    </a>
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
        onAct={() => void yes.run({ action: aid })}
        onNotNow={() => void no.run({ action: aid })}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- lines */

export interface DidLineProps {
  text: string;
  /** Tico could not do it: the fault mark, in ink. */
  fault?: boolean;
}

/** What Tico did on the page: "Opened Agents", with the done tick. No Undo in the first build. */
export function DidLine({ text, fault }: DidLineProps) {
  return (
    <div className={fault ? 'did is-fault' : 'did'}>
      <span className="did-ico">{fault ? <Fault label="Could not do it" /> : <Icon name="check" />}</span>
      <span>{text}</span>
    </div>
  );
}

export interface MessageProps {
  m: ChatMessage;
  variant: Variant;
  me: string | undefined;
  /** Same sender as the row above: no face, no name. */
  cont?: boolean;
  onRetry?: (m: ChatMessage) => void;
  /** The chat it is in, when the person may rate it: Good and Not good show under it. */
  rateIn?: string;
  /** The person's own rating of it. */
  feedback?: Feedback | null;
}

/** What someone said. Yours sits in a bubble at the right; a failed send says so and offers Retry. */
export function Message({ m, variant, me, cont, onRetry, rateIn, feedback = null }: MessageProps) {
  const text = m.deleted ? 'Deleted' : (m.body ?? '');
  const files = m.refs?.attachments;
  const pics = usePictures(m);
  if (m.pending || (me && m.sender_id === me)) {
    const failed = m.pending === 'failed' || m.pending === 'held';
    return (
      <div className={['msg', 'is-mine', cont && 'is-cont', failed && 'is-failed'].filter(Boolean).join(' ')} data-target={`message:${m.id}`}>
        <div className="mine-col" onClick={pics.onClick}>
          {text && (
            <div className="bubble">
              <Markdown text={text} compact />
            </div>
          )}
          <Files files={files} pending={!!m.pending} onPicture={pics.openAt} />
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
        {pics.viewer}
      </div>
    );
  }
  const who = whoOf(m, variant, me);
  const aid = typeof m.refs?.action === 'string' ? m.refs.action : null;
  return (
    <div className={['msg', cont && 'is-cont'].filter(Boolean).join(' ')} data-target={`message:${m.id}`}>
      {cont ? <span className="av-gap av-22" /> : <Face who={who} size={22} />}
      <div className="msg-main" onClick={pics.onClick}>
        {!cont && (
          <div className="msg-meta">
            <b>{who.name}</b>
            <time dateTime={m.created_at}>{clock(m.created_at)}</time>
          </div>
        )}
        {text && <Markdown text={text} compact />}
        <Steps run={m.run} />
        <Files files={files} pending={false} onPicture={pics.openAt} />
        {aid && <Proposal aid={aid} />}
        <FromSlack m={m} />
        {rateIn && <Rate chat={rateIn} m={m} feedback={feedback} />}
      </div>
      {pics.viewer}
    </div>
  );
}

/** A reply being written: its text so far, or what it is doing, with the typing dots. Heard once, when posted. */
export function LiveRow({ r, variant, text }: { r: LiveRun; variant: Variant; text: string }) {
  const who = whoOf({ sender_id: r.bot }, variant, undefined);
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
