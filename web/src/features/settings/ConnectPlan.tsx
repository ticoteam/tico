import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { reasonIn, run } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { LabelValue } from '@/kit/LabelValue';
import { toast } from '@/kit/Toast';
import { poll } from '@/lib';
import { useVisible } from './parts';
import './connect-plan.css';
import { LIVE_SIGN_IN, readSignIn, subscriptionsQuery, type SignIn, type SignInKind } from './queries';

const RETRY_MS = 3000;
const WAKE_LIMIT_MS = 5 * 60_000;

function cancelLiveSignIn(login: SignIn | null) {
  if (login?.runner_id && login.id && LIVE_SIGN_IN.includes(login.state ?? 'requested')) {
    return run('settings.cancelSignIn', { computer: login.runner_id, login: login.id }, { via: 'click' });
  }
}

type ConnectProps = { kind: SignInKind; subscription?: string; onClose: () => void; inline?: boolean };

export function ConnectPlan(props: ConnectProps) {
  const [attempt, setAttempt] = useState(0);
  return <Connection key={attempt} {...props} onRetry={() => setAttempt((n) => n + 1)} />;
}

function Connection({ kind, subscription, onClose, onRetry, inline }: ConnectProps & { onRetry: () => void }) {
  const qc = useQueryClient();
  const [login, setLogin] = useState<SignIn | null>(null);
  const [error, setError] = useState<{ message: string; code?: string } | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [ending, setEnding] = useState(false);
  const [restart, setRestart] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [secret, setSecret] = useState('');
  const [code, setCode] = useState('');
  const [sending, setSending] = useState(false);
  const [codeError, setCodeError] = useState<string | null>(null);
  const submitting = useRef(false);
  const [tries, setTries] = useState(0);
  const startedLogin = useRef<SignIn | 'dismissed' | null>(null);
  useEffect(() => {
    startedLogin.current = null;
    return () => {
      const login = startedLogin.current;
      startedLogin.current = 'dismissed';
      if (login !== 'dismissed') void cancelLiveSignIn(login);
    };
  }, []);
  // Bound the wait for a computer. Once it queues a sign-in, the server owns the attempt's deadline.
  const visible = useVisible();
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (login) return;
    const t = setTimeout(() => setLate(true), WAKE_LIMIT_MS);
    return () => clearTimeout(t);
  }, [login]);
  useEffect(() => {
    if (late || !visible || starting || login || error || restart || pasting) return;
    const timer = setTimeout(
      () => {
        const input = subscription ? { subscription, ...(confirmed && { confirm: true }) } : { kind };
        // A request in flight may queue a sign-in after the wake deadline.
        setStarting(true);
        void run('settings.connect', input, { via: 'click' }).then((r) => {
          const got = r.ok ? (r.out as SignIn | null | undefined) ?? null : null;
          if (startedLogin.current === 'dismissed') {
            void cancelLiveSignIn(got);
            return;
          }
          setStarting(false);
          if (!r.ok && r.error === 'confirm_restart' && !confirmed) {
            return setRestart(reasonIn(r) ?? 'Running work will restart');
          }
          if (!r.ok) return setError({ message: reasonIn(r) ?? "Couldn't start", code: r.error });
          if (!got) return setError({ message: 'No computer can sign in yet', code: 'offline' });
          if (got.id) {
            startedLogin.current = got;
            setLogin(got);
          } else if (got.waking) setTries((n) => n + 1);
          else setError({ message: got.message ?? 'No computer can sign in yet', code: 'offline' });
        });
      },
      tries === 0 ? 0 : RETRY_MS,
    );
    return () => clearTimeout(timer);
  }, [kind, subscription, confirmed, restart, pasting, tries, late, visible, starting, login, error]);
  // The follow: every 2 s while it runs, paused while the tab is hidden. Its answers (the provider's one-time code and
  // page) stay in this state, never the query cache.
  const [follow, setFollow] = useState<Partial<SignIn> | null>(null);
  const runner = login?.runner_id;
  const lid = login?.id;
  const now = { ...login, ...follow } as SignIn;
  const state = now.state ?? (login ? 'starting' : 'requested');
  const over = !LIVE_SIGN_IN.includes(state);
  useEffect(() => {
    if (!runner || !lid || !visible || over || pasting || sending) return;
    return poll((signal) => readSignIn(runner, lid, signal), (answer) => {
      startedLogin.current = { ...login, ...answer } as SignIn;
      setFollow(answer);
    }, (a) => LIVE_SIGN_IN.includes(a.state ?? 'requested'), 2000);
  }, [runner, lid, visible, over, pasting, login, sending]);
  const finished = useRef(false);
  useEffect(() => {
    if (state !== 'done' || finished.current) return;
    finished.current = true;
    void qc.invalidateQueries({ queryKey: subscriptionsQuery.queryKey });
    toast({ text: `${now.name ?? 'Subscription'} connected`, kind: 'done' });
    onClose();
  }, [state, now.name, qc, onClose]);
  const timedOut = late && !starting && !login;
  const ended =
    error?.message ??
    (state === 'failed'
      ? (now.message || 'The sign-in did not finish')
      : state === 'expired' || timedOut
        ? 'The sign-in ran out of time'
        : state === 'canceled'
          ? 'Sign-in canceled'
          : null);
  const hostWarning = now.not_yours ? `Credential passes through someone else's computer${now.computer ? ` (${now.computer})` : ''}` : null;
  const signingIn = state === 'waiting' ? undefined : `Signing in${now.computer ? ` on ${now.computer}` : ''}`;
  const offerPaste = error
    ? error.code === 'offline' || error.code === 'runner_version'
    : state !== 'done' && (!!hostWarning || !!ended);
  const endAttempt = async () => {
    const cancellation = cancelLiveSignIn(now);
    if (cancellation) {
      setEnding(true);
      const r = await cancellation;
      setEnding(false);
      const answer = r.out as SignIn | undefined;
      if (!r.ok || !answer?.state || LIVE_SIGN_IN.includes(answer.state)) {
        setCancelError(reasonIn(r) ?? "Couldn't cancel the sign-in");
        return false;
      }
      startedLogin.current = answer;
      setFollow(answer);
      if (answer.state === 'done') return false;
    }
    setCancelError(null);
    return true;
  };
  const switchToPaste = async () => {
    if (!(await endAttempt())) return;
    setError(null);
    setPasting(true);
  };
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!lid || !runner || !code.trim() || now.code_sent || submitting.current) return;
    submitting.current = true;
    setSending(true);
    setCodeError(null);
    try {
      const result = await run('settings.signInCode', { computer: runner, login: lid, code: code.trim() }, { via: 'click' });
      if (startedLogin.current === 'dismissed') return;
      if (!result.ok) {
        setCodeError(reasonIn(result) ?? "Couldn't send the code");
        return;
      }
      const answer = { ...now, ...(result.out as SignIn), code_sent: true };
      startedLogin.current = answer;
      setFollow(answer);
      setCode('');
    } finally {
      submitting.current = false;
      setSending(false);
    }
  };
  const codex = kind === 'codex_login';
  const label = codex ? 'ChatGPT plan' : 'Claude plan';
  const paste = async (confirm = confirmed) => {
    const value = secret.trim();
    const input = subscription ? { subscription, secret: value, ...(confirm && { confirm: true }) } : { kind: codex ? 'codex_login' : 'claude_token', secret: value };
    const r = await run('settings.pastePlan', input, { via: 'click' });
    if (r.ok) {
      const saved = r.out as { name?: string } | undefined;
      toast({ text: `${saved?.name || now.name || label} connected`, kind: 'done' });
      onClose();
    } else if (r.error === 'confirm_restart' && !confirm) {
      setRestart(reasonIn(r) ?? 'Running work will restart');
    } else {
      setRestart(null);
      setError({ message: reasonIn(r) ?? "Couldn't save" });
    }
  };
  const confirmRestart = () => {
    if (pasting) return paste(true);
    setRestart(null);
    setConfirmed(true);
  };
  const title = restart ? 'Restart runs?' : `${subscription ? 'Reconnect' : 'Connect'} ${label}`;
  const description = restart ?? (pasting ? error?.message : cancelError ?? ended ?? hostWarning ?? signingIn);
  const actions = (
    <>
      <Button variant="quiet" onClick={onClose}>{ended && !pasting && !restart ? 'Close' : 'Cancel'}</Button>
      {restart ? (
        <Button variant="primary" onClick={confirmRestart}>Reconnect</Button>
      ) : pasting ? (
        <Button variant="primary" disabled={!secret.trim()} onClick={() => paste()}>{subscription ? 'Reconnect' : 'Connect'}</Button>
      ) : (
        <>
          {ended && <Button disabled={ending} onClick={async () => { if (await endAttempt()) onRetry(); }}>Try again</Button>}
          {offerPaste && <Button disabled={ending} onClick={switchToPaste}>Paste instead</Button>}
          {!ended && !cancelError && now.url && (
            <a className="btn btn-fill" href={now.url} target="_blank" rel="noreferrer">
              Open {codex ? 'ChatGPT' : 'Claude'}
            </a>
          )}
        </>
      )}
    </>
  );
  const fields = (
    <>
      {pasting && !restart && (
        <Field label={codex ? 'auth.json' : 'Setup token'} type="password" value={secret} onValueChange={setSecret} autoComplete="off" autoFocus />
      )}
      {!pasting && !restart && !ended && now.code && <LabelValue label="Code">{now.code}</LabelValue>}
      {!pasting && !restart && !ended && now.accepts_code && !now.code_sent && (
        <form className="connect-plan-fields" onSubmit={(event) => void send(event)}>
          <Field label="Code from the page" value={code} onValueChange={setCode} disabled={sending} autoComplete="one-time-code" />
          {codeError && <div role="alert">{codeError}</div>}
          <Button type="submit" busy={sending} disabled={!code.trim()}>Send code</Button>
        </form>
      )}
    </>
  );
  if (inline) {
    return (
      <section className="connect-plan" aria-label={title}>
        <strong>{title}</strong>
        {description && <div role={ended || cancelError || (pasting && error) ? 'alert' : 'status'}>{description}</div>}
        {fields}
        <div className="connect-plan-actions">{actions}</div>
      </section>
    );
  }
  return <Dialog open onOpenChange={(open) => !open && onClose()} alert={!!restart} title={title} description={description} actions={actions}>{fields}</Dialog>;
}
