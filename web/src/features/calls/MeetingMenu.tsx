import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { GranolaDevice, GranolaStatus } from '@/api/schema';
import { run } from '@/actions';
import { directoryQuery, meQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { PeoplePick } from './PeoplePick';
import { press } from './parts';
import { granolaQuery, meetingSettingsQuery, pollGranola } from './queries';

type Open = 'log' | 'sources' | 'granola' | 'settings' | null;

/** List's ⋯ (cal-merge.js moreMenu): Log a meeting, Meeting sources, Connect Granola, Calendar settings. */
export function MeetingMenu() {
  const [open, setOpen] = useState<Open>(null);
  const granola = useQuery(granolaQuery).data;
  // Stable: Connect Granola's sign-in runs once per open, and it closes with this.
  const close = useCallback(() => setOpen(null), []);
  return (
    <>
      <Menu trigger={<IconButton icon="more" label="More" />}>
        <MenuItem label="Log a meeting" icon="edit" onSelect={() => setOpen('log')} />
        <MenuItem label="Meeting sources" icon="list" meta={granola?.connected ? '1' : undefined} onSelect={() => setOpen('sources')} />
        {granola && !granola.connected && <MenuItem label="Connect Granola" icon="link" onSelect={() => setOpen('granola')} />}
        <MenuRule />
        <MenuItem label="Calendar settings" icon="gear" onSelect={() => setOpen('settings')} />
      </Menu>
      <Dialog open={open === 'log'} onOpenChange={(o) => !o && close()} title="Log a meeting">
        {open === 'log' && <LogForm done={close} />}
      </Dialog>
      <Dialog open={open === 'sources'} onOpenChange={(o) => !o && close()} title="Meeting sources" actions={<Button onClick={close}>Done</Button>}>
        {open === 'sources' && <Sources onConnect={() => setOpen('granola')} />}
      </Dialog>
      <Dialog
        open={open === 'granola'}
        onOpenChange={(o) => !o && close()}
        title="Connect Granola"
        description="New notes land in Pending, where only you see them."
      >
        {open === 'granola' && <GranolaSignIn done={close} />}
      </Dialog>
      <Dialog open={open === 'settings'} onOpenChange={(o) => !o && close()} title="Calendar settings">
        {open === 'settings' && <SettingsForm done={close} />}
      </Dialog>
    </>
  );
}

/* ---------------------------------------------------------------- Log a meeting */

function LogForm({ done }: { done: () => void }) {
  const { data: dir } = useQuery(directoryQuery);
  const [title, setTitle] = useState('');
  const [when, setWhen] = useState(() => new Date());
  const [who, setWho] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [transcript, setTranscript] = useState('');
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const ready = !!(notes.trim() || transcript.trim()) && !!(title.trim() || transcript.trim());

  const read = (f: File | undefined) => {
    if (!f) return;
    void f.text().then(
      (text) => {
        setTranscript(text);
        if (!title.trim()) setTitle(f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim());
      },
      () => toast({ text: "Couldn't read that file", kind: 'failed' }),
    );
  };
  const save = async () => {
    if (!ready) return;
    setBusy(true);
    const names = who.map((id) => dir?.byId[id]?.name ?? id.slice(id.indexOf(':') + 1));
    const ok = await press('calls.logMeeting', {
      title: title.trim() || undefined,
      start: when.toISOString(),
      with: names.length ? names : undefined,
      notes: notes.trim() || undefined,
      transcript: transcript.trim() ? transcript : undefined,
      private: hidden || undefined,
    });
    setBusy(false);
    if (ok) done();
  };

  return (
    <form
      className="ev-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="call-f2">
        <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
        <DatePick label="When" value={when} onValueChange={setWhen} />
      </div>
      <div className="f">
        <span className="f-label">Who</span>
        <PeoplePick value={who} onChange={setWho} quiet />
      </div>
      <Field label="Notes" area value={notes} onValueChange={setNotes} placeholder="What was discussed and decided" />
      <Field label="Transcript" area value={transcript} onValueChange={setTranscript} placeholder="Paste one, or upload a file" />
      <input
        ref={file}
        type="file"
        accept=".txt,.vtt,.srt,.json,.md,text/plain,text/vtt,application/json"
        aria-label="Transcript file"
        hidden
        onChange={(e) => read(e.target.files?.[0])}
      />
      <Switch label="Private: only who was in it" checked={hidden} onCheckedChange={setHidden} />
      <div className="modal-row">
        <Button icon="attach" variant="quiet" onClick={() => file.current?.click()}>
          Upload transcript
        </Button>
        <span className="grow" />
        <Button onClick={done}>Cancel</Button>
        <Button variant="primary" type="submit" busy={busy} disabled={!ready}>
          Log meeting
        </Button>
      </div>
    </form>
  );
}

/* ---------------------------------------------------------------- Meeting sources */

/** Where meetings come in from. Granola is each person's own account. */
function Sources({ onConnect }: { onConnect: () => void }) {
  const { data: g, isError } = useQuery(granolaQuery);
  const [syncing, setSyncing] = useState(false);
  if (isError) return <p className="call-src-err">Granola did not load.</p>;
  if (!g) return null;
  const sync = async () => {
    setSyncing(true);
    await press('calls.syncGranola', {});
    setSyncing(false);
  };
  return (
    <ul className="call-src">
      <li className="call-src-row">
        <b>Granola</b>
        <span className="call-src-st">{word(g)}</span>
        {g.connected && <GranolaFacts g={g} />}
        {(g.connected || g.needs_signin) && g.last_error && <span className="call-src-err">{g.last_error}</span>}
        <span className="call-src-go">
          {g.connected && !g.needs_signin && (
            <Button className="btn-sm" busy={syncing || g.syncing} onClick={() => void sync()}>
              Sync now
            </Button>
          )}
          {(g.connected || g.needs_signin) && (
            <Button className="btn-sm" variant="quiet" onClick={() => void press('calls.disconnectGranola', {})}>
              Remove
            </Button>
          )}
          {(!g.connected || g.needs_signin) && (
            <Button className="btn-sm" variant="primary" onClick={onConnect}>
              {g.needs_signin ? 'Sign in again' : 'Connect'}
            </Button>
          )}
        </span>
      </li>
    </ul>
  );
}

const word = (g: GranolaStatus) => (g.needs_signin ? 'Signed out' : g.connected ? 'Connected' : 'Not connected');

function GranolaFacts({ g }: { g: GranolaStatus }) {
  const n = g.imported_count;
  return (
    <span className="call-src-facts">
      {g.email && <span>{g.email}</span>}
      <span>
        {g.syncing ? (
          'Syncing'
        ) : g.last_synced_at ? (
          <>
            Synced <Ago at={g.last_synced_at} />
          </>
        ) : (
          'Not synced yet'
        )}
      </span>
      <span>{n === 1 ? '1 note' : `${n} notes`}</span>
      {g.skipped > 0 && <span>{g.skipped} skipped</span>}
    </span>
  );
}

/* ---------------------------------------------------------------- Connect Granola: a code to enter in Granola */

type Flow = { phase: 'starting' } | { phase: 'code'; code: string; link: string; host: string } | { phase: 'failed'; why: string };

/** Only an https link on Granola's own site becomes "Open Granola"; anything else is shown as its host. */
function safeLink(value: string | undefined): string {
  try {
    const u = new URL(String(value));
    return u.protocol === 'https:' && (u.hostname === 'granola.ai' || u.hostname.endsWith('.granola.ai')) ? u.href : '';
  } catch {
    return '';
  }
}
function hostOf(value: string | undefined): string {
  try {
    const u = new URL(String(value));
    return /^https?:$/.test(u.protocol) ? u.host : '';
  } catch {
    return '';
  }
}

/** The sign-in: asks for a code, shows it, and asks Granola every few seconds until it connects, is denied or expires. */
function GranolaSignIn({ done }: { done: () => void }) {
  const [flow, setFlow] = useState<Flow>({ phase: 'starting' });
  const [tries, setTries] = useState(0);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fail = (why: string) => live && setFlow({ phase: 'failed', why });
    void run('calls.connectGranola', {}, { via: 'click' }).then((r) => {
      const d = r.out as GranolaDevice | undefined;
      if (!live) return;
      if (!r.ok || !d?.user_code) return fail(r.ok ? 'Granola sent no code' : "Couldn't reach Granola");
      setFlow({
        phase: 'code',
        code: d.user_code,
        link: safeLink(d.verification_uri_complete) || safeLink(d.verification_uri),
        host: hostOf(d.verification_uri) || hostOf(d.verification_uri_complete),
      });
      const every = Math.max(0.1, Number(d.interval) || 5) * 1000;
      const until = Date.now() + Math.max(1, Number(d.expires_in) || 600) * 1000;
      const ask = async () => {
        const s = await pollGranola().catch(() => ({ state: 'pending' }));
        if (!live) return;
        if (s.state === 'connected') {
          toast({ text: 'Granola connected' });
          return done();
        }
        if (s.state === 'denied') return fail('Granola access was denied');
        if (s.state === 'needs_signin') return fail('Granola sign-in failed');
        if (s.state === 'expired' || Date.now() > until) return fail('The code expired');
        timer = setTimeout(() => void ask(), every);
      };
      timer = setTimeout(() => void ask(), every);
    });
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [tries, done]);

  if (flow.phase === 'starting') return <p className="call-gr-wait">Getting a code</p>;
  if (flow.phase === 'failed')
    return (
      <>
        <p className="call-src-err" role="alert">
          {flow.why}
        </p>
        <div className="modal-row">
          <Button onClick={done}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              setFlow({ phase: 'starting' });
              setTries(tries + 1);
            }}
          >
            Try again
          </Button>
        </div>
      </>
    );
  return (
    <>
      <ol className="call-gr-steps">
        <li>
          <span>Open Granola and sign in</span>
          {flow.link ? (
            <a className="btn btn-sm" href={flow.link} target="_blank" rel="noopener noreferrer">
              Open Granola
            </a>
          ) : (
            flow.host && <span className="call-src-facts">{flow.host}</span>
          )}
        </li>
        <li>
          <span>Enter this code if asked</span>
          <output className="call-gr-code" aria-label="Granola code">
            {flow.code}
          </output>
          <Button
            className="btn-sm"
            icon="copy"
            onClick={() =>
              void navigator.clipboard?.writeText(flow.code).then(
                () => toast({ text: 'Code copied' }),
                () => toast({ text: "Couldn't copy", kind: 'failed' }),
              )
            }
          >
            Copy
          </Button>
        </li>
        <li>
          <span>Allow access</span>
        </li>
      </ol>
      <p className="call-gr-wait" role="status">
        Waiting for Granola
      </p>
      <div className="modal-row">
        <Button onClick={done}>Cancel</Button>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- Calendar settings: who sees meetings brought in */

type Mine = 'default' | 'review' | 'auto';
type Team = 'review' | 'auto';
const SHARE_WORDS: Record<Team, string> = { review: 'Review before sharing', auto: 'Share at once' };

function SettingsForm({ done }: { done: () => void }) {
  const { data: s, isError } = useQuery(meetingSettingsQuery);
  const owner = useQuery(meQuery).data?.role === 'owner';
  if (isError) return <p className="call-src-err">Settings did not load.</p>;
  if (!s) return null;
  return <SettingsFields key={String(s.auto_share) + s.review_default} s={s} owner={owner} done={done} />;
}

function SettingsFields({ s, owner, done }: { s: { auto_share: boolean | null; review_default: Team }; owner: boolean; done: () => void }) {
  const [mine, setMine] = useState<Mine>(s.auto_share === null ? 'default' : s.auto_share ? 'auto' : 'review');
  const [team, setTeam] = useState<Team>(s.review_default);
  const [busy, setBusy] = useState(false);
  const mineOptions = [
    { value: 'default' as const, label: `Company default: ${SHARE_WORDS[s.review_default].toLowerCase()}` },
    { value: 'review' as const, label: SHARE_WORDS.review },
    { value: 'auto' as const, label: SHARE_WORDS.auto },
  ];
  const teamOptions = [
    { value: 'review' as const, label: SHARE_WORDS.review },
    { value: 'auto' as const, label: SHARE_WORDS.auto },
  ];
  const save = async () => {
    setBusy(true);
    const ok = await press('calls.meetingSettings', owner ? { mine, team } : { mine });
    setBusy(false);
    if (ok) done();
  };
  return (
    <form
      className="ev-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <Select label="Your imported meetings" options={mineOptions} value={mine} onValueChange={setMine} />
      {owner && <Select label="Company default" options={teamOptions} value={team} onValueChange={setTeam} />}
      <div className="modal-row">
        <Button onClick={done}>Cancel</Button>
        <Button variant="primary" type="submit" busy={busy}>
          Save
        </Button>
      </div>
    </form>
  );
}
