import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { Conversation, ticoRoomQuery } from '@/conversation';
import { Fault } from '@/kit/Fault';
import { Sheet, SheetHead } from '@/shell';
import { Dot } from '@/tico/dot';
import { usePageName } from './pageName';

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot, then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Folded, the frame draws the Dot alone (Frame's TicoStrip). */
export function TicoSheet() {
  const room = useQuery(ticoRoomQuery);
  // what the person is looking at: the page's own name, not the window's title
  const path = useLocation({ select: (l) => l.pathname });
  const name = usePageName();
  return (
    <Sheet kind="tico" label="Tico">
      <SheetHead kind="tico" title="Tico" lead={<Dot size={30} />} />
      {room.data ? (
        <Conversation id={room.data} variant="tico" page={{ path, title: name }} />
      ) : (
        <div className="chat-body">
          {room.isError || room.data === null ? <Fault label={room.isError ? 'Tico did not load' : 'Tico has no chat yet'} /> : null}
        </div>
      )}
    </Sheet>
  );
}
