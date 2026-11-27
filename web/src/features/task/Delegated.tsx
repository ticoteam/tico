import { useNavigate } from '@tanstack/react-router';
import { run } from '@/actions';
import type { AssistantOkCited, TaskDetailEvent } from '@/api/schema';
import { jumpTo, ticoRoomQuery } from '@/conversation';
import { clock } from '@/conversation/dates';
import { queryClient } from '@/data';
import { Icon } from '@/kit/Icon';
import { Pill } from '@/kit/Pill';
import { Section } from '@/pagekinds';
import { NameOf } from './parts';

/* The task history's lines for OKs the person's Assistant gave on their word (backend/assistant_oks.py, spec "Your
   assistant gives an OK when you tell it to"): "<name> OK'd · via assistant", their words quoted as the server checked
   them, the time, and Open message, which opens the chat they said them in at that message (their own Assistant room
   in its sheet, any other chat as its thread). The task read's events carry `delegation` on those answers alone; its
   `cited` is null for a reader who may not open that chat, who reads the words with no way in. An OK taken back since
   stays, marked Taken back (`undone`). Agent Ops gives them too, on the person's word in their chat with it (via
   "botops"), and its line says so. A task with no such answer draws nothing. */

/** A row of the task read's `events` (backend/task_history.py): the contract names `source` and `delegation`; the
    row's other fields come as the history writes them. */
export type HistoryEvent = TaskDetailEvent & {
  id?: string;
  field?: string;
  new?: string | null;
  actor?: string;
  via?: string | null;
  created_at?: string;
};

const given = (events: readonly HistoryEvent[]) => events.filter((e) => e.field === 'answer' && e.new === 'ok' && e.delegation);

/** Opens the chat at the message: the chat is drawn first, and the jump waits for it to listen (Outline jumpTo). */
function OpenMessage({ cited }: { cited: AssistantOkCited }) {
  const navigate = useNavigate();
  const open = async () => {
    const room = await queryClient.ensureQueryData(ticoRoomQuery).catch(() => null);
    if (room === cited.conversation_id) await run('shell.fold', { sheet: 'tico', folded: false }, { via: 'click' });
    else await navigate({ to: '/threads/$thread', params: { thread: cited.conversation_id } });
    jumpTo(cited.conversation_id, cited.message_id);
  };
  return (
    <button type="button" className="dt-act" onClick={() => void open()}>
      Open message <Icon name="popout" />
    </button>
  );
}

export function DelegatedOks({ events }: { events: readonly HistoryEvent[] }) {
  const oks = given(events);
  if (!oks.length) return null;
  return (
    <Section name="Given via assistant" count={oks.length}>
      {oks.map((e, i) => {
        const lent = e.delegation!;
        return (
          <div key={e.id ?? i} className="dt-line" data-target={`assistant-ok-line:${e.id ?? i}`}>
            <span className="dt-text">
              <span>
                <NameOf id={e.actor} /> OK'd{' · '}
                {e.via === 'botops' ? 'via Agent Ops' : 'via assistant'}
              </span>
              {lent.words && <span>{` · “${lent.words}”`}</span>}
              {e.created_at && <small> · {clock(e.created_at)}</small>}
              {lent.undone && (
                <>
                  {' '}
                  <Pill>Taken back</Pill>
                </>
              )}
              {lent.cited && (
                <>
                  {' · '}
                  <OpenMessage cited={lent.cited} />
                </>
              )}
            </span>
          </div>
        );
      })}
    </Section>
  );
}
