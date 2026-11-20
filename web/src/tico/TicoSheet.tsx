import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { Conversation, MessageBones, ticoRoomQuery } from '@/conversation';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { Sheet, SheetHead } from '@/shell';
import { Dot } from '@/tico/dot';
import { usePageName } from './pageName';
import { useTicoPower } from './power';
import { TicoOff } from './TicoOff';

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot, then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Folded, the frame draws the Dot alone (Frame's TicoStrip). While
   Tico is off, the sheet shows that in place of the chat, and the Dot is grey and hollow. `foot` sits over its box (the
   root's Catch up chip). */
export function TicoSheet({ foot }: { foot?: ReactNode }) {
  const power = useTicoPower();
  const room = useQuery(ticoRoomQuery);
  // what the person is looking at: the page's own name, not the window's title
  const path = useLocation({ select: (l) => l.pathname });
  const name = usePageName();
  return (
    <Sheet kind="tico" label="Tico">
      <SheetHead kind="tico" title="Tico" lead={<Dot size={30} />} />
      {power?.available === false ? (
        <TicoOff canTurnOn={power.can_turn_on} />
      ) : room.data ? (
        <Conversation id={room.data} variant="tico" page={{ path, title: name }} foot={foot} />
      ) : room.isError ? (
        // The grey fault, then the one way out (the kit's empty-state row).
        <div className="chat-body">
          <div className="empty">
            <Fault label="Tico did not load" size={16} />
            <div className="q-row">
              <Button onClick={() => void room.refetch()}>Try again</Button>
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
