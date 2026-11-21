import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { Conversation, MessageBones, ticoRoomQuery } from '@/conversation';
import { AssistantCall } from '@/features/calls/exports';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { IconButton } from '@/kit/IconButton';
// the open/close state alone, not the dialog: Tico's sheet only opens it
import { openSearch } from '@/search/state';
import { Sheet, SheetHead, TICO_MARK, fromPage, pageAt } from '@/shell';
import { usePhoneCards } from '@/shell/phone';
import { Dot } from '@/tico/dot';
import { TicoChips } from './Chips';
import { okCard } from './OkCard';
import { usePageName } from './pageName';
import { useTicoPower } from './power';
import { TicoOff } from './TicoOff';
import { Tracker } from './Tracker';

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot and search (talking to Tico or searching: both start
   here, on the phone's bar too), then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Tucked, the frame draws its edge with the Dot. While
   Tico is off, the sheet shows that in place of the chat, and the Dot is grey and hollow. Over its box, what to ask
   Tico about the open page (Chips). Over the chat, the tasks the person handed off (Tracker). Under a message that
   links one OK waiting on the person, a card that opens it (OkCard). In the head, a call with Tico (AssistantCall). */
export function TicoSheet() {
  const power = useTicoPower();
  const room = useQuery(ticoRoomQuery);
  // what the person is looking at: the page's own name, not the window's title
  const [path, search] = useLocation({ select: (l) => `${l.pathname}\n${l.searchStr}` }).split('\n');
  const name = usePageName();
  // On a phone the card draws Tico's one Dot itself, where the lip had it: the head has none of its own.
  const phone = usePhoneCards();
  return (
    <Sheet kind="tico" label="Tico">
      <SheetHead kind="tico" title="Tico" lead={phone ? undefined : <Dot size={TICO_MARK} />}>
        {power?.available !== false && power?.bot && room.data && <AssistantCall thread={room.data} bot={power.bot} />}
        <IconButton icon="search" label="Search" onClick={() => openSearch()} />
      </SheetHead>
      {power?.available === false ? (
        <TicoOff canTurnOn={power.can_turn_on} />
      ) : room.data ? (
        <>
          <Tracker />
          <Conversation id={room.data} variant="tico" from={fromPage(pageAt(path ?? '/', search), name)} foot={<TicoChips room={room.data} />} below={okCard} />
        </>
      ) : room.isError ? (
        // The grey fault, then the one way out (the kit's empty-state row).
        <div className="chat-body">
          <div className="empty">
            <Fault label="Tico did not load" size={16} />
            <div className="q-row">
              <Button onClick={() => room.refetch()}>Try again</Button>
            </div>
          </div>
        </div>
      ) : room.data === null ? (
        <div className="chat-body">
          <Fault label="Tico has no chat yet" />
        </div>
      ) : (
        <MessageBones label="Loading Tico" />
      )}
    </Sheet>
  );
}
