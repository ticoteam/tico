import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Line, SwitchLine, Verb, Word, act } from './parts';
import {
  supportAccessQuery,
  supportLogQuery,
  supportTicketsQuery,
  type ScreeningCaught,
  type ScreeningTestRun,
  type SupportSession,
} from './queries';

/* What Security could do in the old app and the first pass left out: what screening caught lately, a test run of the
   built-in samples, and letting Tico support sign in for a while (ui/support.ts). */

// ---------------------------------------------------------------- screening: caught lately, and a test run

const pct = (n?: number | null) => (n == null ? '' : `${Math.round(n * 100)}%`);

/** The Caught line: how many were held or warned lately; Show opens them. Run test sends the samples through. */
export function ScreeningMore({ recent }: { recent?: ScreeningCaught[] }) {
  const [showing, setShowing] = useState(false);
  const [ran, setRan] = useState<ScreeningTestRun | null>(null);
  const [busy, setBusy] = useState(false);
  const caught = Array.isArray(recent) ? recent : [];
  const test = async () => {
    setBusy(true);
    const r = await run('settings.testScreening', {}, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't run the test"));
    setRan(r.out as ScreeningTestRun);
  };
  return (
    <>
      <Line label="Caught" target="setting:screening-caught">
        {caught.length === 0 ? <span className="fact-dim">None lately</span> : String(caught.length)}
        {caught.length > 0 && <Verb onClick={() => setShowing(true)}>Show</Verb>}
      </Line>
      <Line label="Test" target="setting:screening-test">
        {busy ? <span className="fact-dim">Running</span> : ran ? `Passed ${ran.passed} of ${ran.total}` : <span className="fact-dim">Not run</span>}
        <Verb onClick={() => void test()}>Run test</Verb>
      </Line>
      <Dialog
        open={showing}
        onOpenChange={setShowing}
        title="Held and warned"
        actions={
          <Button variant="primary" onClick={() => setShowing(false)}>
            Done
          </Button>
        }
      >
        {caught.length === 0 ? (
          <Empty title="No holds or warnings yet" />
        ) : (
          caught.map((c, i) => (
            <div key={`${c.at ?? ''}-${i}`} className="row g-door" title={c.source ?? ''}>
              <span className="row-title">
                {[c.action === 'held' ? 'Held' : 'Warned', c.source, pct(c.score)].filter(Boolean).join(' · ')}
              </span>
              <span className="row-cell">
                {c.task ? (
                  <Link to="/task/$task" params={{ task: c.task }} onClick={() => setShowing(false)}>
                    {c.at ? <Ago at={c.at} /> : 'Task'}
                  </Link>
                ) : (
                  c.at && <Ago at={c.at} />
                )}
              </span>
            </div>
          ))
        )}
      </Dialog>
      <Dialog
        open={ran != null}
        onOpenChange={(o) => !o && setRan(null)}
        title={ran ? `Passed ${ran.passed} of ${ran.total}` : ''}
        description={ran?.model ? `Read by ${ran.model}` : undefined}
        actions={
          <Button variant="primary" onClick={() => setRan(null)}>
            Done
          </Button>
        }
      >
        {(ran?.results ?? []).map((t) => (
          <div key={t.name} className="row g-door" title={t.name}>
            <span className="row-title">{t.name}</span>
            <span className="row-cell">{t.ok === false ? <Word>Missed</Word> : [t.action, pct(t.score)].filter(Boolean).join(' · ')}</span>
          </div>
        ))}
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------- Tico support signs in for a while

const HOURS = [
  { value: '1', label: '1 hour' },
  { value: '4', label: '4 hours' },
  { value: '24', label: '1 day' },
  { value: '72', label: '3 days' },
  { value: '168', label: '1 week' },
  { value: '720', label: '30 days' },
  { value: '2160', label: '90 days' },
  { value: '8760', label: '1 year' },
];
const TIER: Record<string, string> = { app: 'App only', full: 'Full access' };

function left(s: SupportSession): string {
  const secs = s.seconds_left ?? Math.max(0, (new Date(s.expires_at).getTime() - Date.now()) / 1000);
  if (secs >= 86400) return `${Math.round(secs / 86400)} days left`;
  if (secs >= 3600) return `${Math.round(secs / 3600)} hours left`;
  return `${Math.max(1, Math.round(secs / 60))} min left`;
}

function LetIn({ canFull, onClose }: { canFull: boolean; onClose: () => void }) {
  const tickets = useQuery({ ...supportTicketsQuery, retry: false });
  const [tier, setTier] = useState<'app' | 'full'>('app');
  const [hours, setHours] = useState('72');
  const [busy, setBusy] = useState(false);
  // Support signs in against one of your own open requests: the newest one still waiting on them.
  const ticket = (tickets.data ?? [])
    .filter((t) => (t.status === 'open' || t.status === 'answered') && !t.pending)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const go = async () => {
    if (!ticket) return;
    setBusy(true);
    const r = await run('settings.letSupportIn', { ticket: ticket.id, tier, hours: Number(hours) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't let Tico support in"));
    toast({ text: 'Tico support can sign in', kind: 'done' });
    onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      alert
      title="Let Tico support sign in?"
      description={
        tickets.isPending ? undefined : ticket ? 'They act as an admin until the time runs out or you stop them.' : 'Ask Tico support first.'
      }
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          {ticket ? (
            <Button variant="primary" busy={busy} onClick={() => void go()}>
              Let in
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={tickets.isPending}
              onClick={() => {
                onClose();
                void act('support.open', {});
              }}
            >
              Open chat
            </Button>
          )}
        </>
      }
    >
      {ticket && (
        <div className="set-add">
          <Segmented
            label="Access"
            options={[
              { value: 'app', label: 'App only' },
              { value: 'full', label: 'Full access', disabled: !canFull },
            ]}
            value={tier}
            onValueChange={(t) => setTier(t as 'app' | 'full')}
          />
          <Select label="For" value={hours} options={HOURS} onValueChange={setHours} />
        </div>
      )}
    </Dialog>
  );
}

function SupportLog({ session, onClose }: { session: SupportSession; onClose: () => void }) {
  const log = useQuery({ ...supportLogQuery(session.id), retry: false });
  const lines = log.data ?? [];
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="What Tico support did"
      actions={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      {log.isPending ? null : lines.length === 0 ? (
        <Empty title="Nothing yet" />
      ) : (
        lines.map((l, i) => (
          <div key={`${l.created_at}-${i}`} className="row g-door" title={l.path}>
            <span className="row-title">{`${l.by === 'support-agent' ? 'Support agent' : 'Staff'} · ${l.method} ${l.path}`}</span>
            <span className="row-cell">
              {String(l.status)} <Ago at={l.created_at} />
            </span>
          </div>
        ))
      )}
    </Dialog>
  );
}

/** "May sign in": on lets Tico support in against your open request; off, or Stop, ends it at once. */
export function SupportAccess() {
  const q = useQuery({ ...supportAccessQuery, retry: false });
  const [asking, setAsking] = useState(false);
  const [log, setLog] = useState<SupportSession | null>(null);
  const v = q.data;
  if (!v?.available) return null;
  const on = v.active;
  const shown = on ?? v.last;
  return (
    <>
      <SwitchLine
        label="Tico support may sign in"
        target="setting:support-access"
        checked={!!on}
        disabled={!on && !v.can_start}
        onChange={(want) => (want ? setAsking(true) : on && void act('settings.stopSupport', { session: on.id }))}
      />
      {on && (
        <Line label="Signed in" target="setting:support-live">
          {`${TIER[on.tier] ?? on.tier}, ${left(on)}`}
          <Verb onClick={() => void act('settings.stopSupport', { session: on.id })}>Stop</Verb>
        </Line>
      )}
      {shown && (
        <Line label={on ? 'Log' : 'Last time'}>
          {!on && (shown.ended_at ? <Ago at={shown.ended_at} /> : TIER[shown.tier])}
          <Verb onClick={() => setLog(shown)}>Show log</Verb>
        </Line>
      )}
      {asking && <LetIn canFull={v.can_full} onClose={() => setAsking(false)} />}
      {log && <SupportLog session={log} onClose={() => setLog(null)} />}
    </>
  );
}
