import { useState } from 'react';
import type { NeedsYouItem } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { toast } from '@/kit/Toast';
import { AskCard } from './AskCard';
import { declineAccess, grantAccess, grantKeyDrop, saveKey } from './queries';

/* What waits on the person without a task (master plan P1): an agent's access request, a key card and the Tools owner's
   key-drop proposal, each in the one ask card with its own body. The server checks again on every press and settles
   every copy of it; the card leaves Flow once its answer is taken, and says in place why one was refused. */

const LEVEL = { read: 'Read', read_write: 'Read and write' } as const;

/** The refusal's own words, else a plain one. */
const said = (err: unknown) => (err instanceof Error && err.message) || 'Not saved';

/** One press: the work, its toast once taken, or the refusal in the card. Returns the promise, so the button is busy. */
function useOnce(setError: (s: string) => void) {
  return (work: () => Promise<unknown>, done: string, name: string) => async () => {
    setError('');
    try {
      await work();
      toast({ text: done, name });
    } catch (err) {
      setError(said(err));
    }
  };
}

/** An agent asks for a level on a tool or a repository: Grant, Read only (when it asked for write) or Decline. */
function AccessCard({ it, a }: { it: NeedsYouItem; a: NonNullable<NeedsYouItem['access']> }) {
  const [error, setError] = useState('');
  const once = useOnce(setError);
  const name = `${a.subject_name} asks for ${a.resource_name}`;
  return (
    <AskCard
      name={name}
      note={LEVEL[a.level]}
      who={a.subject}
      label="Access request"
      target={`request:${it.id}`}
      error={error}
      actions={
        <>
          <Button onClick={once(() => declineAccess(it.id, a.id), 'Declined', a.resource_name)}>Decline</Button>
          {a.level === 'read_write' && <Button onClick={once(() => grantAccess(it.id, a.id, 'read'), 'Granted read', a.resource_name)}>Read only</Button>}
          <Button variant="primary" onClick={once(() => grantAccess(it.id, a.id), 'Granted', a.resource_name)}>
            Grant
          </Button>
        </>
      }
    >
      {a.why && <p className="askcard-what">{a.why}</p>}
    </AskCard>
  );
}

/** A key card (a replacement key, Reconnect): the key typed here goes to the server alone. */
function KeyCard({ it, k }: { it: NeedsYouItem; k: NonNullable<NeedsYouItem['key']> }) {
  const [error, setError] = useState('');
  const [value, setValue] = useState('');
  const once = useOnce(setError);
  return (
    <AskCard
      name={k.title}
      who={it.from_id}
      label="Key"
      target={`request:${it.id}`}
      error={error}
      actions={
        <Button variant="primary" disabled={!value.trim()} onClick={once(() => saveKey(it.id, k.id, value.trim()), 'Saved', k.label || k.env)}>
          Save
        </Button>
      }
    >
      <label className="askcard-field">
        <span>Key</span>
        <input type="password" autoComplete="off" spellCheck={false} placeholder={k.format || undefined} value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      {k.help_url && (
        <a className="askcard-help" href={k.help_url} target="_blank" rel="noreferrer">
          Where to get one
        </a>
      )}
    </AskCard>
  );
}

/** The Tools owner's proposal for keys the person dropped in its chat: each key, its tool, level and agents; the rows
    left ticked are granted. */
function KeyDropCard({ it, d }: { it: NeedsYouItem; d: NonNullable<NeedsYouItem['key_drop']> }) {
  const [error, setError] = useState('');
  const [kept, setKept] = useState<ReadonlySet<number>>(() => new Set(d.rows.map((_, i) => i)));
  const once = useOnce(setError);
  const name = d.rows.length === 1 ? `${d.rows[0]?.key} for ${d.rows[0]?.tool_name}` : `${d.rows.length} keys`;
  const flip = (i: number, on: boolean) => setKept((was) => (on ? new Set(was).add(i) : new Set([...was].filter((x) => x !== i))));
  return (
    <AskCard
      name={name}
      who={it.from_id}
      label="Key proposal"
      target={`request:${it.id}`}
      error={error}
      actions={
        <Button variant="primary" disabled={!kept.size} onClick={once(() => grantKeyDrop(it.id, d.message_id, [...kept].sort((x, y) => x - y)), 'Granted', name)}>
          Grant selected
        </Button>
      }
    >
      <div className="askcard-rows">
        {d.rows.map((r, i) => (
          <Checkbox
            key={`${r.key}:${i}`}
            checked={kept.has(i)}
            onCheckedChange={(on) => flip(i, on)}
            label={
              <span>
                <b>{r.key}</b> → {r.tool_name} · {LEVEL[r.level]} · {r.bots.map((b) => b.name).join(', ')}
              </span>
            }
          />
        ))}
      </div>
    </AskCard>
  );
}

/** A request's card, by its kind; nothing for a kind it does not know. */
export function RequestCard({ it }: { it: NeedsYouItem }) {
  if (it.kind === 'access' && it.access) return <AccessCard it={it} a={it.access} />;
  if (it.kind === 'key' && it.key) return <KeyCard it={it} k={it.key} />;
  if (it.kind === 'key_drop' && it.key_drop) return <KeyDropCard it={it} d={it.key_drop} />;
  return null;
}
