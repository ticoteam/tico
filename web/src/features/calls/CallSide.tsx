import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type MouseEvent } from 'react';
import { Conversation } from '@/conversation';
import { directoryQuery } from '@/data';
import { followFromSmallWindow, smallWindowLink } from '@/desktop';
import { Avatar } from '@/kit/Avatar';
import { Count } from '@/kit/Count';
import { Empty } from '@/kit/Empty';
import { Tabs } from '@/kit/Tabs';
import { running } from './model';
import { Face, useNameOf, useSmallWindow } from './parts';
import { callQuery, followTranscript, transcriptQuery } from './queries';
import { useCall } from './session';
import './calls.css';

type Tab = 'chat' | 'transcript' | 'people';

export interface CallSideProps {
  room: string;
}

/** Beside the call: its chat, what has been said, and who is in it. The shell puts it in the side sheet on a desk. */
export function CallSide({ room }: CallSideProps) {
  const [tab, setTab] = useState<Tab>('chat');
  const call = useQuery(callQuery(room)).data;
  const s = useCall();
  const live = s.callId === room && (s.phase === 'live' || s.phase === 'reconnecting');
  const inCall = call?.participants.filter((p) => p.in_call) ?? [];
  const small = useSmallWindow();
  return (
    <aside className="pane win-side" aria-label="Call chat" onClickCapture={small ? keepOnCall : undefined}>
      <div className="cw-tabs">
        <Tabs
          label="Call"
          value={tab}
          onValueChange={setTab}
          tabs={[
            { value: 'chat', label: 'Chat' },
            { value: 'transcript', label: 'Transcript' },
            { value: 'people', label: 'People', after: <Count n={inCall.length} label={`${inCall.length} in the call`} /> },
          ]}
        />
      </div>
      {tab === 'chat' && call && <Conversation id={call.thread_id} variant="side" />}
      {tab === 'transcript' && call && <Transcript meeting={call.meeting_id} room={room} live={live} />}
      {tab === 'people' && (
        <div className="chat-body">
          {(call?.participants ?? []).map((p) => (
            <Person key={p.actor} actor={p.actor} inCall={p.in_call} />
          ))}
          {(call?.bots ?? []).map((b) => (
            <Person key={b} actor={b.startsWith('bot:') ? b : `bot:${b}`} inCall />
          ))}
        </div>
      )}
    </aside>
  );
}

/** In the desktop app's small window a link never moves the window off its call (desktop smallWindowLink). */
function keepOnCall(e: MouseEvent<HTMLElement>) {
  const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
  if (!a || !e.currentTarget.contains(a)) return;
  const link = smallWindowLink(a.getAttribute('href') ?? '', location.origin);
  // a file chip opens as it does anywhere (a picture in the viewer, anything else in a new window the app hands on)
  if (link.kind === 'file' && a.getAttribute('target') === '_blank') return;
  e.preventDefault();
  e.stopPropagation();
  void followFromSmallWindow(link);
}

function Person({ actor, inCall }: { actor: string; inCall: boolean }) {
  const nameOf = useNameOf();
  return (
    <span className="rail-row call-rail">
      <Face actor={actor} size={22} />
      <span>{nameOf(actor)}</span>
      <em>{inCall ? 'In the call' : 'Left'}</em>
    </span>
  );
}

/** What has been said: read once, then each line as the live meeting's stream sends it (no timer). */
function Transcript({ meeting, room, live }: { meeting: string; room: string; live: boolean }) {
  const t = useQuery(transcriptQuery(meeting));
  const after = t.data?.event_id;
  const loaded = t.isSuccess;
  useEffect(() => (live && loaded ? followTranscript(meeting, after ?? 0, room) : undefined), [live, loaded, meeting, room, after]);
  const dir = useQuery(directoryQuery).data;
  const nameOf = useNameOf();
  // A speaker is an actor id, or an agent's id or slug with no prefix, or a name the meeting tool gave.
  const actorOf = (sp: string) => (sp.includes(':') ? sp : dir?.byId[`bot:${sp}`] ? `bot:${sp}` : dir?.byId[`human:${sp}`] ? `human:${sp}` : null);
  const chunks = t.data?.chunks ?? [];
  if (!chunks.length) return t.isPending ? null : <Empty title="Nothing said yet" />;
  return (
    <div className="chat-body cw-ts">
      {chunks.map((c) => {
        const actor = c.speaker ? actorOf(c.speaker) : null;
        const name = actor ? nameOf(actor) : c.speaker || 'Someone';
        return (
          <div key={`${c.seq}:${c.revision ?? 0}`} className="msg ts">
            {actor ? <Face actor={actor} size={22} /> : <Avatar kind="person" name={name} size={22} decorative />}
            <div className="msg-main">
              <div className="msg-meta">
                <b>{name}</b>
                {c.start_ms != null && <time>{running(c.start_ms / 1000)}</time>}
              </div>
              <p>{c.text}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
