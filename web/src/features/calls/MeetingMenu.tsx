import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { GranolaDevice, GranolaStatus, MeetingImporter, MeetingImporterComputer, MeetingSource } from '@/api/schema';
import { run } from '@/actions';
import { meQuery, useActors } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { PageMore } from '@/pagekinds';
import { TRANSCRIPT_SOURCES, type TranscriptSource } from './model';
import { PeoplePick } from './PeoplePick';
import { press, useNameOf } from './parts';
import { granolaQuery, importersQuery, meetingSettingsQuery, pollGranola, sourcesQuery } from './queries';

type Open = 'log' | 'sources' | 'granola' | 'settings' | null;

/** List's ⋯ (cal-merge.js moreMenu): Log a meeting, Meeting sources, Connect Granola, Calendar settings. `children`
    come first (a phone's head controls). */
export function MeetingMenu({ children }: { children?: ReactNode }) {
  const [open, setOpen] = useState<Open>(null);
  const granola = useQuery(granolaQuery).data;
  const sources = useQuery(sourcesQuery).data?.sources ?? [];
  const connected = (granola?.connected ? 1 : 0) + sources.filter((x) => x.id !== 'import' && x.status !== 'needs_setup').length;
  // What needs a look (meetings-sources.ts meetSourcesTrouble): a source that errors or lags, or Granola signed out or failing.
  const trouble =
    sources.some((x) => x.status === 'error' || x.status === 'delayed') || !!(granola && (granola.needs_signin || (granola.connected && granola.last_error)));
  const close = () => setOpen(null);
  return (
    <>
      <span className={trouble ? 'call-more is-trouble' : 'call-more'} title={trouble ? 'Meeting sources need a look' : undefined}>
        <PageMore>
          {children}
          <MenuItem label="Log a meeting" icon="edit" onSelect={() => setOpen('log')} />
          <MenuItem
            label="Meeting sources"
            icon="list"
            meta={trouble ? 'Needs a look' : connected ? String(connected) : undefined}
            onSelect={() => setOpen('sources')}
          />
          {granola && !granola.connected && <MenuItem label="Connect Granola" icon="link" onSelect={() => setOpen('granola')} />}
          <MenuRule />
          <MenuItem label="Calendar settings" icon="gear" onSelect={() => setOpen('settings')} />
        </PageMore>
      </span>
      <Dialog open={open === 'log'} onOpenChange={(o) => !o && close()} title="Log a meeting">
        {open === 'log' && <LogForm done={close} />}
      </Dialog>
      <Dialog open={open === 'sources'} onOpenChange={(o) => !o && close()} title="Meeting sources" actions={<Button onClick={close}>Done</Button>}>
        {open === 'sources' && <Sources onConnect={() => setOpen('granola')} onLeave={close} />}
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
  const nameOf = useNameOf();
  const [title, setTitle] = useState('');
  const [when, setWhen] = useState(() => new Date());
  const [who, setWho] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [transcript, setTranscript] = useState('');
  const [source, setSource] = useState<TranscriptSource>('upload');
  const [recording, setRecording] = useState('');
  const [send, setSend] = useState('');
  const [hidden, setHidden] = useState(false);
  const agents = useActors().filter((a) => a.kind === 'agent');
  const sends = [{ value: '', label: "Don't send" }, { value: 'auto', label: 'Tico' }, ...agents.map((a) => ({ value: a.id, label: a.name }))];
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const said = !!transcript.trim();
  const link = recording.trim();
  const badLink = said && !!link && !/^https:\/\/\S+$/.test(link);
  const ready = !!(notes.trim() || said) && !!(title.trim() || said) && !badLink;

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
    // Someone typed in from outside is kept as typed (a name or an email).
    const names = who.map(nameOf);
    const ok = await press('calls.logMeeting', {
      title: title.trim() || undefined,
      start: when.toISOString(),
      with: names.length ? names : undefined,
      notes: notes.trim() || undefined,
      transcript: said ? transcript : undefined,
      source: said ? source : undefined,
      recording: said && link ? link : undefined,
      private: hidden || undefined,
      send: send || undefined,
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
        <PeoplePick value={who} onChange={setWho} quiet free />
      </div>
      <Field label="Notes" area value={notes} onValueChange={setNotes} placeholder="What was discussed and decided" />
      <Field label="Transcript" area value={transcript} onValueChange={setTranscript} placeholder="Paste one, or upload a file" />
      {said && (
        <div className="call-f2">
          <Select label="Source" options={TRANSCRIPT_SOURCES.map(([value, label]) => ({ value, label }))} value={source} onValueChange={setSource} />
          <Field label="Recording link" type="url" value={recording} onValueChange={setRecording} placeholder="https://" error={badLink ? 'An https link' : undefined} />
        </div>
      )}
      <Select label="Send to an agent" options={sends} value={send} onValueChange={setSend} />
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

/**
 * Where meetings come in from (meetings-sources.ts): the person's own Granola, then Close and each importer that is on,
 * with its state. The owner sees every importer instead, turns it on or off and picks the computer that runs it; its
 * credentials are a file on that computer and never come to the page.
 */
function Sources({ onConnect, onLeave }: { onConnect: () => void; onLeave: () => void }) {
  const owner = useQuery(meQuery).data?.role === 'owner';
  const granola = useQuery(granolaQuery);
  const sources = useQuery(sourcesQuery);
  const importers = useQuery({ ...importersQuery, enabled: owner });
  if (granola.isPending && sources.isPending) return null;
  // The owner's own rows stand in for the importers' shared ones.
  const mine = owner ? (importers.data?.importers ?? []) : [];
  const computers = importers.data?.computers ?? [];
  const shown = (sources.data?.sources ?? []).filter((x) => x.id !== 'import' && !mine.some((i) => i.source === x.id));
  return (
    <ul className="call-src" data-target="dialog:meeting-sources">
      {granola.data ? (
        <GranolaRow g={granola.data} onConnect={onConnect} />
      ) : (
        granola.isError && <ShortRow name="Granola" />
      )}
      {shown.map((x) => (
        <li key={x.id} className="call-src-row">
          <b>{x.name}</b>
          <span className="call-src-st">{sourceWord(x)}</span>
          <Synced last={x.last_import} ok={x.last_succeeded_at} />
          {x.error && <span className="call-src-err">{x.error}</span>}
          {x.id === 'close' && (
            // Close is set up on its tool page (meetings-sources.ts), as every tool is.
            <span className="call-src-go">
              <Link to="/library/tools" search={{ tool: 'close-crm' }} className="btn btn-sm" onClick={onLeave}>
                {x.status === 'needs_setup' ? 'Connect' : 'Manage'}
              </Link>
            </span>
          )}
        </li>
      ))}
      {sources.isError && <ShortRow name="Other sources" />}
      {mine.map((i) => (
        <ImporterRow key={i.source} i={i} computers={computers} />
      ))}
      {owner && importers.isError && <ShortRow name="Importers" />}
      {owner && importers.data && !computers.length && <li className="call-src-row call-src-st">Add a computer first</li>}
    </ul>
  );
}

function ShortRow({ name }: { name: string }) {
  return (
    <li className="call-src-row">
      <b>{name}</b>
      <span className="call-src-err">Did not load</span>
    </li>
  );
}

const sourceWord = (x: MeetingSource) =>
  x.status === 'needs_setup' ? (x.id === 'close' ? 'Not connected' : 'Waiting') : x.status === 'syncing' || x.status === 'available' ? 'Connected' : WORDS[x.status];
const WORDS: Record<MeetingImporter['status'], string> = { off: 'Off', waiting: 'Waiting', syncing: 'Connected', delayed: 'Delayed', error: 'Error' };

/** A source's time: its last meeting in, else its last good sync. */
function Synced({ last, ok }: { last?: string | null; ok?: string | null }) {
  const at = last ?? ok;
  if (!at) return null;
  return (
    <span className="call-src-facts">
      <span>
        {last ? 'Last import' : 'Synced'} <Ago at={at} />
      </span>
    </span>
  );
}

/** An importer, for the owner: on or off, the computer that runs it, and where that computer keeps its credentials. */
function ImporterRow({ i, computers }: { i: MeetingImporter; computers: MeetingImporterComputer[] }) {
  const options = computers.map((c) => ({ value: c.id, label: c.label, meta: c.online ? undefined : 'Offline' }));
  const set = (on: boolean, computer?: string) => press('calls.setImporter', { source: i.source, on, ...(computer ? { computer } : {}) });
  return (
    <li className="call-src-row" data-target={`importer:${i.source}`}>
      <b>{i.name}</b>
      <span className="call-src-st">{WORDS[i.status]}</span>
      {i.enabled && <Synced last={i.last_import} ok={i.last_succeeded_at} />}
      {i.enabled && i.error && <span className="call-src-err">{i.error}</span>}
      {i.enabled && (
        <span className="call-src-setup" aria-label="Credentials file">
          <code>{i.setup.file}</code>
          {i.setup.keys.map((k) => (
            <code key={k}>{k}</code>
          ))}
          <code>{i.setup.doc}</code>
        </span>
      )}
      <span className="call-src-go">
        <Select
          label={`Computer for ${i.name}`}
          labelHidden
          look="quiet"
          placeholder="Computer"
          options={options}
          value={i.runner_id || null}
          disabled={!computers.length}
          onValueChange={(c) => set(i.enabled, c)}
        />
        <Switch label={i.name} labelHidden checked={i.enabled} disabled={!computers.length} onCheckedChange={(on) => set(on)} />
      </span>
    </li>
  );
}

/** The person's own Granola account. */
function GranolaRow({ g, onConnect }: { g: GranolaStatus; onConnect: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const sync = async () => {
    setSyncing(true);
    await press('calls.syncGranola', {});
    setSyncing(false);
  };
  return (
    <li className="call-src-row">
      <b>Granola</b>
      <span className="call-src-st">{word(g)}</span>
      {g.connected && <GranolaFacts g={g} />}
      {(g.connected || g.needs_signin) && g.last_error && <span className="call-src-err">{g.last_error}</span>}
      <span className="call-src-go">
        {g.connected && !g.needs_signin && (
          <Button className="btn-sm" busy={syncing || g.syncing} onClick={() => sync()}>
            Sync now
          </Button>
        )}
        {(g.connected || g.needs_signin) && (
          <Button className="btn-sm" variant="quiet" onClick={() => setRemoving(true)}>
            Remove
          </Button>
        )}
        {(!g.connected || g.needs_signin) && (
          <Button className="btn-sm" variant="primary" onClick={onConnect}>
            {g.needs_signin ? 'Sign in again' : 'Connect'}
          </Button>
        )}
      </span>
      <Dialog
        open={removing}
        onOpenChange={setRemoving}
        alert
        title="Remove Granola?"
        actions={
          <>
            <Button onClick={() => setRemoving(false)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => {
                setRemoving(false);
                void press('calls.disconnectGranola', {});
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </li>
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
          // The first notes come in at once, as in the old app (meetings-granola.ts granolaPoll).
          void press('calls.syncGranola', {});
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
              navigator.clipboard?.writeText(flow.code).then(
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
