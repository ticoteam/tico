import { useState } from 'react';
import type { NeedsYouItem } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { toast } from '@/kit/Toast';
import { AskCard } from './AskCard';
import { declineAccess, grantAccess, grantKeyDrop, saveKey, type AccessAnswer, type KeyDropAnswer } from './queries';

/* What waits on the person without a task (master plan P1): an agent's access request, a key card and the Tools owner's
   key-drop proposal, each in the one ask card with its own body. The server checks again on every press and settles
   every copy of it; the card leaves Flow once its answer is taken, and says in place why one was refused. */

const LEVEL = { read: 'Read', read_write: 'Read and write' } as const;

/** The refusal's own words, else a plain one. */
const said = (err: unknown) => (err instanceof Error && err.message) || 'Not saved';

/** One press: the work, its toast once taken, or the refusal in the card. Returns the promise, so the button is busy. */
function useOnce(setError: (s: string) => void) {
  return <T,>(work: () => Promise<T>, done: string | ((out: T) => string | { text: string; name: string }), name: string) => async () => {
    setError('');
    try {
      const out = await work();
      const said = typeof done === 'string' ? done : done(out);
      toast(typeof said === 'string' ? { text: said, name } : said);
    } catch (err) {
      setError(said(err));
    }
  };
}

/** A grant the server answered with the request still open (it could not be given yet): said in the card, which stays. */
const NOT_YET = 'The grant didn’t take effect yet';
const taken = async (out: Promise<AccessAnswer>) => {
  const answer = await out;
  if (answer.state === 'open') throw new Error(NOT_YET);
  return answer;
};

/** Rows named as the card names them: one key and its tool, or how many. */
const keysName = (rows: readonly { key: string; tool_name: string }[]) =>
  rows.length === 1 ? `${rows[0]?.key} for ${rows[0]?.tool_name}` : `${rows.length} keys`;

/** What a key proposal's Grant says happened, from its answer: the rows granted, and how many failed their check. */
export function keyDropDone(out: KeyDropAnswer): { text: string; name: string } {
  if (out.state !== 'granted') return { text: 'Not granted yet', name: keysName(out.rows) };
  const failed = (out.results ?? []).filter((r) => !r.ok).length;
  return { text: failed ? `Granted, ${failed} not connected` : 'Granted', name: keysName(out.rows) };
}

/** What the access request's answer says happened: the server's state and level, which may be someone else's. */
export function accessDone(pressed: 'grant' | 'decline', out: AccessAnswer): string {
  if (out.state === 'declined') return pressed === 'decline' ? 'Declined' : 'Declined by someone else';
  if (out.state === 'granted') {
    if (pressed === 'decline') return 'Granted by someone else';
    return out.level === 'read' ? 'Granted read only' : 'Granted';
  }
  return 'Closed';
}

/** An agent asks for a level on a tool or a repository: Grant, Read only (when it asked for write) or Decline. */
function AccessCard({ it, a }: { it: NeedsYouItem; a: NonNullable<NeedsYouItem['access']> }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const answer = useOnce(setError);
  // One answer at a time: while one is with the server, every button of the card waits.
  const once: typeof answer = (...args) => {
    const run = answer(...args);
    return async () => {
      setBusy(true);
      try {
        await run();
      } finally {
        setBusy(false);
      }
    };
  };
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
          <Button disabled={busy} onClick={once(() => taken(declineAccess(it.id, a.id)), (out) => accessDone('decline', out), a.resource_name)}>
            Decline
          </Button>
          {a.level === 'read_write' && (
            <Button disabled={busy} onClick={once(() => taken(grantAccess(it.id, a.id, 'read')), (out) => accessDone('grant', out), a.resource_name)}>
              Read only
            </Button>
          )}
          <Button variant="primary" disabled={busy} onClick={once(() => taken(grantAccess(it.id, a.id)), (out) => accessDone('grant', out), a.resource_name)}>
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
  // A row's place on the card (`at`): Needs you leaves out a row whose tool was deleted.
  const [kept, setKept] = useState<ReadonlySet<number>>(() => new Set(d.rows.map((r) => r.at)));
  // Only rows still on the card: one whose tool was deleted since it was drawn leaves the list, and what Grant sends.
  const ticked = d.rows.map((r) => r.at).filter((at) => kept.has(at));
  const once = useOnce(setError);
  const name = keysName(d.rows);
  const flip = (i: number, on: boolean) => setKept((was) => (on ? new Set(was).add(i) : new Set([...was].filter((x) => x !== i))));
  return (
    <AskCard
      name={name}
      who={it.from_id}
      label="Key proposal"
      target={`request:${it.id}`}
      error={error}
      actions={
        <Button variant="primary" disabled={!ticked.length} onClick={once(() => grantKeyDrop(it.id, d.message_id, ticked, d.losses), keyDropDone, name)}>
          Grant selected
        </Button>
      }
    >
      <div className="askcard-rows">
        {d.rows.map((r, i) => (
          <Checkbox
            key={`${r.key}:${i}`}
            checked={kept.has(r.at)}
            onCheckedChange={(on) => flip(r.at, on)}
            label={
              <span>
                <b>{r.key}</b> → {r.tool_name} · {LEVEL[r.level]} · {r.bots.map((b) => b.name).join(', ')}
                {r.replaces && (
                  <span className="askcard-replaces">
                    Replaces {r.replaces.key}
                    {r.replaces.loses.length > 0 && ` · ${r.replaces.loses.join(', ')} lose access`}
                  </span>
                )}
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
