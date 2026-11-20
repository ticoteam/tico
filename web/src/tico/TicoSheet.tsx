import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { Conversation, MessageBones, ticoRoomQuery } from '@/conversation';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { IconButton } from '@/kit/IconButton';
// the open/close state alone, not the dialog: Tico's sheet only opens it
import { openSearch } from '@/search/state';
import { Sheet, SheetHead, TICO_MARK } from '@/shell';
import { usePhoneCards } from '@/shell/phone';
import { Dot } from '@/tico/dot';
import { TicoChips } from './Chips';
import { askedFrom, usePageName } from './pageName';
import { useTicoPower } from './power';
import { useTicoName } from './look';
import { TicoOff } from './TicoOff';

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot and search (talking to Tico or searching: both start
   here, on the phone's bar too), then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Tucked, the frame draws its edge with the Dot. While
   Tico is off, the sheet shows that in place of the chat, and the Dot is grey and hollow. Over its box, what to ask
   Tico about the open page (Chips). */
export function TicoSheet() {
  const power = useTicoPower();
  const room = useQuery(ticoRoomQuery);
  // what the person is looking at: the page's own name, not the window's title
  const address = useLocation({ select: (l) => l.pathname + l.searchStr });
  const name = usePageName();
  // On a phone the card draws Tico's one Dot itself, where the lip had it: the head has none of its own.
  const phone = usePhoneCards();
  // your own name for it (Settings > Account > Your assistant)
  const tico = useTicoName();
  return (
    <Sheet kind="tico" label={tico}>
      <SheetHead kind="tico" title={tico} lead={phone ? undefined : <Dot size={TICO_MARK} />}>
        <IconButton icon="search" label="Search" onClick={() => openSearch()} />
      </SheetHead>
      {power?.available === false ? (
        <TicoOff canTurnOn={power.can_turn_on} />
      ) : room.data ? (
        <Conversation id={room.data} variant="tico" page={askedFrom(address, name)} foot={<TicoChips room={room.data} />} />
      ) : room.isError ? (
        // The grey fault, then the one way out (the kit's empty-state row).
        <div className="chat-body">
          <div className="empty">
            <Fault label={`${tico} did not load`} size={16} />
            <div className="q-row">
              <Button onClick={() => room.refetch()}>Try again</Button>
            </div>
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
