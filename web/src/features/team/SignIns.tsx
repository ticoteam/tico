import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type CSSProperties } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { poll } from '@/lib';
import { Section } from '@/pagekinds';
import { computersQuery, LIVE_LOGIN, modelsQuery, readLogin, signOutStopsQuery, type Computer, type Login, type Runtime } from './queries';

type Tool = { runtime: string; name: string; value: Runtime; signIn: boolean; signOut: boolean };

/** The computer's one login per AI tool, used by every agent on it (mates.js compPage plans; ui computer-detail). */
export function SignIns({ c }: { c: Computer }) {
  const { data: models } = useQuery(modelsQuery);
  const [adding, setAdding] = useState(false);
  const [following, setFollowing] = useState<{ id: string; name: string } | null>(null);
  const [leaving, setLeaving] = useState<Tool | null>(null);
  const manage = c.may_manage !== false && c.online;
  const caps = (runtime: string) => models?.harnesses.find((h) => h.id === runtime)?.capabilities ?? {};
  const tools: Tool[] = Object.entries(c.readiness?.runtimes ?? {})
    .filter(([, v]) => v?.installed)
    .map(([runtime, v]) => ({
      runtime,
      value: v!,
      name:
        Object.values(c.readiness?.harnesses ?? {}).find((h) => h?.runtime === runtime)?.name ??
        models?.harnesses.find((h) => h.id === runtime)?.name ??
        runtime,
      signIn: !!caps(runtime).browser_sign_in,
      signOut: !!caps(runtime).sign_out,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  if (!tools.length) return null;
  return (
    <Section
      name="Sign-ins on this computer"
      count={tools.length}
      actions={manage && tools.some((t) => t.signIn) ? <IconButton icon="plus" size="sm" quiet label="Add a sign-in" onClick={() => setAdding(true)} /> : undefined}
    >
      {tools.map((t) => {
        const ready = t.value.authenticated === 'ready';
        const used = t.value.weekly?.used_percent;
        return (
          <div key={t.runtime} className={ready ? 'row plan' : 'row plan is-off'} title={t.name}>
            <span className="row-mark" />
            <span className="plan-name">{t.name}</span>
            <span className="plan-acct">{ready ? (t.value.account?.email ?? '') : 'Not signed in'}</span>
            <span className="row-pills">{t.value.authenticated === 'rejected' && <Pill flag>Refused</Pill>}</span>
            <span className="plan-use">
              {ready && typeof used === 'number' && (
                <>
                  <span className="meter" aria-hidden="true">
                    <i style={{ width: `${Math.min(100, Math.max(0, used))}%` } as CSSProperties} />
                  </span>
                  <span className="num">{Math.round(used)}% this week</span>
                </>
              )}
            </span>
            {manage && ready && t.signOut ? (
              <IconButton icon="leave" size="sm" quiet label={`Sign out of ${t.name}`} onClick={() => setLeaving(t)} />
            ) : manage && !ready && t.signIn ? (
              <SignInButton c={c} tool={t} onStarted={setFollowing} />
            ) : (
              <span />
            )}
          </div>
        );
      })}
      <AddSignIn open={adding} onOpenChange={setAdding} c={c} tools={tools.filter((t) => t.signIn)} onStarted={setFollowing} />
      {following && <SignInDialog c={c} login={following.id} name={following.name} onClose={() => setFollowing(null)} />}
      {leaving && <SignOutDialog c={c} tool={leaving} onClose={() => setLeaving(null)} />}
    </Section>
  );
}

type Started = (s: { id: string; name: string }) => void;

function useSignIn(c: Computer, onStarted: Started) {
  const start = useAction('computers.signIn');
  return {
    pending: start.pending,
    run: async (t: Pick<Tool, 'runtime' | 'name'>) => {
      const r = await start.run({ computer: c.id, runtime: t.runtime });
      const login = r.out as Login | undefined;
      if (r.ok && login?.id) onStarted({ id: login.id, name: t.name });
    },
  };
}

function SignInButton({ c, tool, onStarted }: { c: Computer; tool: Tool; onStarted: Started }) {
  const signIn = useSignIn(c, onStarted);
  return <IconButton icon="keys" size="sm" quiet label={`Sign in to ${tool.name}`} disabled={signIn.pending} onClick={() => signIn.run(tool)} />;
}

/** Add a sign-in: which tool, then the provider's sign-in. An API key is set in Tools, not here. */
function AddSignIn({ open, onOpenChange, c, tools, onStarted }: { open: boolean; onOpenChange: (o: boolean) => void; c: Computer; tools: Tool[]; onStarted: Started }) {
  const [pick, setPick] = useState('');
  const signIn = useSignIn(c, (s) => {
    onOpenChange(false);
    onStarted(s);
  });
  const tool = tools.find((t) => t.runtime === pick);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setPick(tools[0]?.runtime ?? '');
        onOpenChange(o);
      }}
      title="Add a sign-in"
      description={`Every agent on ${c.label} uses it.`}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!tool} busy={signIn.pending} onClick={() => tool && void signIn.run(tool)}>
            Sign in
          </Button>
        </>
      }
    >
      <Select label="AI tool" options={tools.map((t) => ({ value: t.runtime, label: t.name }))} value={pick || null} onValueChange={setPick} />
    </Dialog>
  );
}

/** A sign-in or sign-out on the computer, read every 3 s until it ends. The answer (a sign-in's one-time code and the
    provider's page) stays in this component's state, never the query cache. `again` reads it now. */
function useLogin(rid: string, lid: string | null) {
  const [data, setData] = useState<Login | undefined>(undefined);
  const [nudge, setNudge] = useState(0);
  const over = !!data && !LIVE_LOGIN.includes(data.state);
  useEffect(() => {
    if (!lid || over) return;
    return poll((signal) => readLogin(rid, lid, signal), setData, (l) => LIVE_LOGIN.includes(l.state), 3000);
  }, [rid, lid, over, nudge]);
  return { data, again: () => setNudge((n) => n + 1) };
}

const SAID: Record<string, string> = {
  requested: 'Starting the sign-in…',
  starting: 'Starting the sign-in…',
  waiting: 'Waiting for you to finish signing in…',
  finishing: 'Putting the new account in place…',
  signed_in: 'Signed in.',
  signed_out: 'Signed out. It stays signed out until someone signs in.',
  done: 'Done.',
  expired: 'It ran out of time. The sign-in is as it was.',
  canceled: 'Stopped. The sign-in is as it was.',
};

/** Follows a sign-in on the computer until it ends: the provider's page, a code to paste, Stop. */
function SignInDialog({ c, login, name, onClose }: { c: Computer; login: string; name: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useLogin(c.id, login);
  const send = useAction('computers.signInCode');
  const stop = useAction('computers.cancelSignIn');
  const [code, setCode] = useState('');
  const l = q.data;
  const live = !l || LIVE_LOGIN.includes(l.state);
  useEffect(() => {
    if (!live) void qc.invalidateQueries({ queryKey: computersQuery.queryKey });
  }, [live, qc]);
  const status = l ? (l.state === 'failed' ? (l.message ?? "It didn't finish. The sign-in is as it was.") : (SAID[l.state] ?? '')) : SAID.requested;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Sign in to ${name}`}
      description={status}
      actions={
        live ? (
          <Button disabled={l?.state === 'finishing'} onClick={() => stop.run({ computer: c.id, login }).then(onClose)}>
            Stop sign-in
          </Button>
        ) : (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        )
      }
    >
      {l?.state === 'waiting' && (
        <>
          {l.url && (
            <a className="once" href={l.url} target="_blank" rel="noopener noreferrer">
              {l.url.replace(/^https:\/\//, '')}
            </a>
          )}
          {l.code && (
            <code className="once" title="Enter this code on that page">
              {l.code}
            </code>
          )}
          {l.accepts_code && !l.code_sent && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!code.trim()) return;
                void send.run({ computer: c.id, login, code: code.trim() }).then((r) => {
                  if (r.ok) {
                    setCode('');
                    q.again();
                  }
                });
              }}
            >
              <Field label="Code from the sign-in page" value={code} onValueChange={setCode} autoComplete="off" />
              <Button type="submit" busy={send.pending} disabled={!code.trim()}>
                Send code
              </Button>
            </form>
          )}
        </>
      )}
    </Dialog>
  );
}

const names = (list: string[]) => (list.length < 3 ? list.join(' and ') : `${list.slice(0, 2).join(', ')} and ${list.length - 2} more`);

/** Asks first, naming the agents that stop; Sign out stays off until they are named (ui model-login signOut). */
function SignOutDialog({ c, tool, onClose }: { c: Computer; tool: Tool; onClose: () => void }) {
  const qc = useQueryClient();
  const stops = useQuery(signOutStopsQuery(c.id, tool.runtime));
  const out = useAction('computers.signOut');
  const [login, setLogin] = useState<string | null>(null);
  const follow = useLogin(c.id, login);
  const ended = !!follow.data && !LIVE_LOGIN.includes(follow.data.state);
  useEffect(() => {
    if (!ended) return;
    void qc.invalidateQueries({ queryKey: computersQuery.queryKey });
    if (follow.data?.state === 'signed_out') toast({ text: `Signed out of ${tool.name}`, kind: 'done' });
  }, [ended, follow.data?.state, qc, tool.name]);
  const bots = (stops.data ?? []).map((b) => b.name ?? b.bot);
  const said = stops.isError
    ? "Couldn't check which agents use it, so it can't be signed out yet."
    : stops.isPending
      ? 'Checking which agents use it…'
      : bots.length
        ? `${names(bots)} will stop until someone signs in again.`
        : 'No agent uses it right now.';
  const status = login ? (follow.data?.state === 'failed' ? (follow.data.message ?? "It didn't finish. The sign-in is as it was.") : (SAID[follow.data?.state ?? 'requested'] ?? '')) : said;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Sign out of ${tool.name} on ${c.label}?`}
      description={status}
      alert
      actions={
        login ? (
          <Button variant="primary" disabled={!ended} onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            {stops.isError ? (
              <Button onClick={() => stops.refetch()}>Try again</Button>
            ) : (
              <Button
                variant="primary"
                disabled={!stops.isSuccess}
                busy={out.pending}
                onClick={() =>
                  out.run({ computer: c.id, runtime: tool.runtime }).then((r) => {
                    const row = r.out as Login | undefined;
                    if (r.ok && row?.id) setLogin(row.id);
                  })
                }
              >
                Sign out
              </Button>
            )}
          </>
        )
      }
    />
  );
}
