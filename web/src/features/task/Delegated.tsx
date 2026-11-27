import type { TaskDetailEvent } from '@/api/schema';
import { clock } from '@/conversation/dates';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Section } from '@/pagekinds';
import { NameOf } from './parts';

/* The task history's lines for OKs the person's Assistant gave on their word (backend/assistant_oks.py, spec "Your
   assistant gives an OK when you tell it to"): "<name> OK'd · via assistant", their words quoted as the server checked
   them, the time, and a link to the chat they said them in. The task read's events carry `delegation` on those answers
   alone; its `cited` is null for a reader who may not open that chat, who reads the words with no link. A task with
   no such answer draws nothing. */

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
                <NameOf id={e.actor} /> OK'd · via assistant
              </span>
              {lent.words && <span>{` · “${lent.words}”`}</span>}
              {e.created_at && <small> · {clock(e.created_at)}</small>}
              {lent.cited && (
                <>
                  {' · '}
                  <Link href={`/threads/${encodeURIComponent(lent.cited.conversation_id)}`}>
                    Assistant room <Icon name="popout" />
                  </Link>
                </>
              )}
            </span>
          </div>
        );
      })}
    </Section>
  );
}
