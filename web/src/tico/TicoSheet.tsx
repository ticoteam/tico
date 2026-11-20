import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { Suspense, lazy, useEffect, useRef } from 'react';
import { Conversation, MessageBones, ticoRoomQuery } from '@/conversation';
import { AssistantCall } from '@/features/calls/exports';
import { useSetupOwed, useSetupTranscript, wentLive } from '@/features/start/routing';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { IconButton } from '@/kit/IconButton';
import { passing, useReadFault } from '@/live';
// the open/close state alone, not the dialog: Tico's sheet only opens it
import { openSearch } from '@/search/state';
import { Sheet, SheetHead, TICO_MARK, fromPage, pageAt } from '@/shell';
import { usePhoneCards } from '@/shell/phone';
import { Dot } from '@/tico/dot';
import { TicoChips } from './Chips';
import { IntroPin } from './Intro';
import { okCard } from './OkCard';
import { ticoPowerQuery, useTicoPower } from './power';
import { useTicoName } from './look';
import { TicoOff } from './TicoOff';
import { Tracker } from './Tracker';

const SetupChat = lazy(() => import('@/features/start/exports').then((m) => ({ default: m.SetupChat })));

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot and search (talking to Tico or searching: both start
   here, on the phone's bar too), then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Tucked, the frame draws its edge with the Dot. While
   Tico is off, the sheet shows that in place of the chat, and the Dot is grey and hollow. Over its box, what to ask
   Tico about the open page (Chips). Over the chat, the tasks the person handed off (Tracker). Under a message that
   links one OK waiting on the person, a card that opens it (OkCard). In the head, a call with Tico (AssistantCall). A
   page's first-visit intro shows over the box (IntroPin).
   While the owner owes the first run, the sheet holds it in place of the chat (SetupChat: nothing runs before a plan is
   connected, so Tico's first lines are the app's own); once they finish, its lines stay above the chat for the page's
   life. */
export function TicoSheet() {
  const power = useTicoPower();
  const room = useQuery(ticoRoomQuery);
  // the power's own read state: one GET answers both, so one that failed fails both
  const powerRead = useQuery(ticoPowerQuery);
  // what the person is looking at: its page, and what is open there
  const [path, search] = useLocation({ select: (l) => `${l.pathname}\n${l.searchStr}` }).split('\n');
  // On a phone the bar draws Tico's one Dot: the head has none of its own.
  const phone = usePhoneCards();
  // your own name for it (Settings > Account > Your assistant)
  const tico = useTicoName();
  const owed = useSetupOwed();
  const setupLines = useSetupTranscript();
  // A failed read (most often a release's restart) says why, and is read again (both halves of it) once the stream is
  // back; only while its fault is what the sheet shows.
  const faulted = !owed && power?.available !== false && !room.data && room.isError;
  const retry = () => {
    void room.refetch();
    if (powerRead.isError) void powerRead.refetch();
  };
  const failed = faulted ? (powerRead.isError ? [room, powerRead] : [room]) : [];
  const fault = useReadFault(tico, failed, room.isSuccess);
  // Try again only when reading again by itself would not fix it (a refusal, a stopped company)
  const stuck = failed.some((r) => !passing(r.error));
  // On a phone, going live from a setup file puts Tico's card down so Tasks shows (the shell brings it up for setup).
  const tuck = phone?.tuck;
  const wasOwed = useRef(owed);
  useEffect(() => {
    if (wasOwed.current && !owed && wentLive(setupLines)) tuck?.();
    wasOwed.current = owed;
  }, [owed, setupLines, tuck]);
  return (
    <Sheet kind="tico" label={tico}>
      <SheetHead kind="tico" title={tico} lead={phone ? undefined : <Dot size={TICO_MARK} />}>
        {!owed && power?.available !== false && power?.bot && room.data && <AssistantCall thread={room.data} bot={power.bot} />}
        <IconButton icon="search" label="Search" onClick={() => openSearch()} />
      </SheetHead>
      {owed ? (
        <Suspense fallback={<MessageBones label="Loading setup" />}>
          <SetupChat />
        </Suspense>
      ) : power?.available === false ? (
        <TicoOff canTurnOn={power.can_turn_on} />
      ) : room.data ? (
        <>
          <Tracker />
          <Conversation
            id={room.data}
            variant="tico"
            from={fromPage(pageAt(path ?? '/', search))}
            foot={
              <>
                <IntroPin />
                <TicoChips room={room.data} />
              </>
            }
            below={okCard}
            extra={setupLines.length ? setupLines : undefined}
          />
        </>
      ) : fault ? (
        // The grey fault and why, then the one way out (the kit's empty-state row).
        <div className="chat-body">
          <div className="empty">
            {/* the line says it: the mark is drawn only */}
            <span aria-hidden="true">
              <Fault label={fault} size={16} />
            </span>
            <p>{fault}</p>
            {stuck && (
              <div className="q-row">
                <Button onClick={retry}>Try again</Button>
              </div>
            )}
          </div>
        </div>
      ) : room.data === null ? (
        <div className="chat-body">
          <Fault label={`${tico} has no chat yet`} />
        </div>
      ) : (
        <MessageBones label={`Loading ${tico}`} />
      )}
    </Sheet>
  );
}
