import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { run } from '@/actions';
import { Conversation, ticoRoomQuery } from '@/conversation';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuSub } from '@/kit/Menu';
import { Sheet, SheetHead } from '@/shell';
import { Dot } from '@/tico/dot';
import { useEarlier } from './earlier';
import { usePageName } from './pageName';
import { useTicoPower } from './power';
import { TicoOff } from './TicoOff';

const click = { via: 'click' } as const;

/* Tico's sheet (system/shell.js ticoPane): the head with the Dot, then Tico's own chat and its composer. The Dot follows
   Tico's runs in the room (Conversation sets it). Folded, the frame draws the Dot alone (Frame's TicoStrip). While
   Tico is off, the sheet shows that in place of the chat, and the Dot is grey and hollow. The owner's earlier chats
   (the old Assistant page's) sit under "⋯"; one opens here in place of Tico's chat, read only, with Back. */
export function TicoSheet() {
  const power = useTicoPower();
  const room = useQuery(ticoRoomQuery);
  // what the person is looking at: the page's own name, not the window's title
  const path = useLocation({ select: (l) => l.pathname });
  const name = usePageName();
  const earlier = power?.available === false ? null : (power?.earlier ?? null);
  const reading = useEarlier();
  if (earlier && reading === earlier.room_id)
    return (
      <Sheet kind="tico" label="Tico">
        <SheetHead
          kind="tico"
          title={earlier.name}
          lead={
            <button className="ib back" type="button" aria-label="Back to Tico" title="Back to Tico" onClick={() => void run('tico.closeEarlier', {}, click)}>
              <Icon name="chevron" className="flip" />
            </button>
          }
        />
        <Conversation id={earlier.room_id} variant="task" title={earlier.name} page={null} canPost={false} />
      </Sheet>
    );
  return (
    <Sheet kind="tico" label="Tico">
      <SheetHead
        kind="tico"
        title="Tico"
        lead={<Dot size={30} />}
        more={
          earlier ? (
            <Menu trigger={<IconButton icon="more" label="More" quiet />}>
              <MenuSub label="Earlier chats" icon="threads">
                <MenuItem label={earlier.name} onSelect={() => void run('tico.openEarlier', { room: earlier.room_id }, click)} />
              </MenuSub>
            </Menu>
          ) : undefined
        }
      />
      {power?.available === false ? (
        <TicoOff canTurnOn={power.can_turn_on} />
      ) : room.data ? (
        <Conversation id={room.data} variant="tico" page={{ path, title: name }} />
      ) : (
        <div className="chat-body">
          {room.isError ? (
            // The grey fault, then the one way out (the kit's empty-state row).
            <div className="empty">
              <Fault label="Tico did not load" size={16} />
              <div className="q-row">
                <Button onClick={() => void room.refetch()}>Try again</Button>
              </div>
            </div>
          ) : room.data === null ? (
            <Fault label="Tico has no chat yet" />
          ) : null}
        </div>
      )}
    </Sheet>
  );
}
