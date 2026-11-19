import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { dropChecksQuery, type DropChecks, type DropEnd } from './queries';

/* A card let go over another card (ui/app/task-board.ts taskDropOpen): make it a subtask, or merge the two. Each choice
   the server refuses says why in its place; a merge says what moves, and who keeps the task when the owners differ. */

type Choice = 'subtask' | 'merge';

const nameOf = (e: DropEnd) => (e.number != null ? `#${e.number}` : e.title);
const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What a merge moves, a line each. */
function movesOf(c: DropChecks): string[] {
  const m = c.moves;
  const a = nameOf(c.task);
  const b = nameOf(c.into);
  const rows: string[] = [];
  if (m.description || m.files) rows.push(`Description${m.files ? `, ${count(m.files, 'file or page', 'files and pages')}` : ''}: added to ${b}`);
  if (m.subtasks) rows.push(`${count(m.subtasks, 'open subtask')}: move under ${b}`);
  if (m.comments) rows.push(`${count(m.comments, 'comment')}: stay on ${a}, linked from ${b}`);
  if (m.asks.length) rows.push(`${count(m.asks.length, 'open ask')}: end with ${a}`);
  const people = [m.followers ? count(m.followers, 'follower') : '', m.requesters ? count(m.requesters, 'requester') : ''].filter(Boolean);
  if (people.length) rows.push(`${people.join(', ')}: added to ${b}`);
  const more = [m.labels ? count(m.labels, 'label') : '', m.links ? count(m.links, 'link') : ''].filter(Boolean);
  if (more.length) rows.push(`${more.join(', ')}: added to ${b}`);
  rows.push(`${a}: closed as a duplicate`);
  return rows;
}

export function DropDialog({ task, onto, onClose }: { task: string; onto: string; onClose: () => void }) {
  const checks = useQuery(dropChecksQuery(task, onto));
  const c = checks.data;
  const [picked, setPicked] = useState<Choice | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const choice = picked ?? (c && c.subtask !== null && c.merge === null ? 'merge' : 'subtask');
  const merge = choice === 'merge';
  const why = c?.[choice];
  const title = c ? `Drop ${nameOf(c.task)} on ${nameOf(c.into)}` : 'Drop a task on another';

  const go = async () => {
    if (!c || why || busy) return;
    setBusy(true);
    const keeper = owner && owner !== c.into.owner_id ? owner : null;
    const r = merge
      ? await run('tasks.merge', { task: c.task.id, into: c.into.id, owner: keeper }, { via: 'click' })
      : await run('tasks.makeSubtask', { task: c.task.id, parent: c.into.id }, { via: 'click' });
    setBusy(false);
    // Refused: nothing changed; the toast says why and the dialog stays open.
    if (r.ok) onClose();
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title={title} description={checks.isError ? 'These tasks cannot be joined.' : undefined}>
      {c && (
        <form
          className="tb-drop"
          data-target={`task-drop:${c.task.id}`}
          onSubmit={(e) => {
            e.preventDefault();
            void go();
          }}
        >
          <p className="tb-drop-titles">
            <span>{c.task.title}</span>
            <span>{c.into.title}</span>
          </p>
          <Segmented<Choice>
            label="What to do"
            options={[
              { value: 'subtask', label: 'Subtask' },
              { value: 'merge', label: 'Merge' },
            ]}
            value={choice}
            onValueChange={setPicked}
          />
          <p className="tb-drop-line">
            {why ??
              (merge
                ? `${nameOf(c.into)} stays and gets ${nameOf(c.task)}'s details.`
                : `${nameOf(c.task)} goes under ${nameOf(c.into)}. Both keep their owners and stages.`)}
          </p>
          {merge && !why && !c.same_owner && (
            <Select
              label={`Who keeps ${nameOf(c.into)}`}
              options={[
                { value: c.into.owner_id ?? '', label: c.into.owner },
                { value: c.task.owner_id ?? '', label: c.task.owner },
              ]}
              value={owner ?? c.into.owner_id ?? ''}
              onValueChange={setOwner}
            />
          )}
          {merge && !why && (
            <ul className="tb-drop-moves">
              {movesOf(c).map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          <div className="modal-row">
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" type="submit" busy={busy} disabled={!!why}>
              {merge ? 'Merge' : 'Make subtask'}
            </Button>
          </div>
        </form>
      )}
      {checks.isError && (
        <div className="modal-row">
          <Button variant="primary" onClick={onClose}>
            OK
          </Button>
        </div>
      )}
    </Dialog>
  );
}
