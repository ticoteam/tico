import { Radio } from '@base-ui/react/radio';
import { RadioGroup } from '@base-ui/react/radio-group';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useState, type FormEvent, type ReactNode } from 'react';
import { run, useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import { Dot } from '@/tico/dot';
import { loginLive, loginQuery, setupQuery, subscriptionsQuery, type KeyKind, type PlanKind } from './queries';
import { setupStep, stepNumber, type SetupStep } from './steps';
import './start.css';

/* The first run: a short dialog over the app (pages/9-first-run). Choose who sets the company up first; by hand it is
   name, model, team. The step is in the address, so it closes, reopens and resumes; the app stays readable under it.
   Each step ends in its own foot, so a form's Enter reaches its button. */

const click = { via: 'click' } as const;
const FAILED = 'That did not go through. Try again.';

const TITLES: Record<SetupStep, string> = {
  path: 'Set up your company',
  name: 'Name your company',
  model: 'Connect a model',
  paste: 'Paste a key',
  team: 'Pick a team',
};

export function FirstRun() {
  const step = useLocation({ select: (l) => setupStep((l.search as { setup?: unknown }).setup) });
  // The last step stays drawn while the dialog closes.
  const [shown, setShown] = useState<SetupStep>(step ?? 'path');
  if (step !== null && step !== shown) setShown(step);
  return (
    <Dialog
      open={step !== null}
      onOpenChange={(open) => {
        if (!open) void run('setup.close', {}, click);
      }}
      className="wl"
      lead={<Head step={shown} back={shown === 'paste'} />}
      title={TITLES[shown]}
    >
      <Step step={shown} />
    </Dialog>
  );
}

function Step({ step }: { step: SetupStep }) {
  if (step === 'path') return <PathStep />;
  if (step === 'name') return <NameStep />;
  if (step === 'model') return <ModelStep />;
  if (step === 'paste') return <PasteStep />;
  return <TeamStep />;
}

/* ---------------------------------------------------------------- parts */

function Head({ step, back }: { step: SetupStep; back: boolean }) {
  const n = stepNumber(step);
  return (
    <header className="wl-head">
      {back ? (
        <button className="ib" type="button" aria-label="Back" title="Back" onClick={() => void run('setup.back', { from: step }, click)}>
          <Icon name="chevron" className="flip" />
        </button>
      ) : (
        <Dot size={28} />
      )}
      <span className="grow" />
      {n !== null && (
        <span className="wl-steps" role="img" aria-label={`Step ${n} of 3`}>
          {[1, 2, 3].map((i) => (
            <i key={i} className={i <= n ? 'is-on' : undefined} />
          ))}
        </span>
      )}
    </header>
  );
}

function Foot({ left, right, error }: { left?: ReactNode; right?: ReactNode; error?: string | null }) {
  return (
    <>
      {error && (
        <span className="f-err" role="alert">
          {error}
        </span>
      )}
      <div className="modal-row wl-foot">
        {left ?? <span />}
        {right}
      </div>
    </>
  );
}

function Choice({ value, label, on, end }: { value: string; label: string; on: boolean; end?: ReactNode }) {
  return (
    <label className={on ? 'wl-row radio is-on' : 'wl-row radio'}>
      <Radio.Root value={value} className="radio-box" />
      <b>{label}</b>
      {end ?? <span />}
    </label>
  );
}

/** Runs an action and keeps its refusal to show under the step. */
function useStepAction(id: string) {
  const action = useAction(id);
  const [error, setError] = useState<string | null>(null);
  const press = async (input: unknown) => {
    setError(null);
    const r = await action.run(input);
    if (!r.ok) setError(FAILED);
    return r.ok;
  };
  return { press, pending: action.pending, error };
}

/* ---------------------------------------------------------------- the steps */

function PathStep() {
  const [path, setPath] = useState<'agents' | 'self'>('agents');
  const choose = useStepAction('setup.choosePath');
  return (
    <>
      <RadioGroup className="wl-list" value={path} onValueChange={(v) => setPath(v as 'agents' | 'self')} aria-label="Who sets it up">
        <Choice value="agents" label="Have my agent set it up" on={path === 'agents'} />
        <Choice value="self" label="Set it up myself" on={path === 'self'} />
      </RadioGroup>
      <Foot
        error={choose.error}
        right={
          <Button variant="primary" busy={choose.pending} onClick={() => void choose.press({ path })}>
            Continue
          </Button>
        }
      />
    </>
  );
}

function NameStep() {
  const { data: record } = useQuery(setupQuery);
  const [name, setName] = useState<string | null>(null);
  const shown = name ?? record?.names.company_name ?? '';
  const next = useStepAction('setup.next');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (shown.trim()) void next.press({ from: 'name', name: shown.trim() });
  };
  return (
    <form className="wl-form" onSubmit={submit}>
      <Field label="Company name" labelHidden value={shown} onValueChange={setName} autoComplete="organization" autoFocus />
      <Foot
        error={next.error}
        right={
          <Button type="submit" variant="primary" busy={next.pending} disabled={!shown.trim()}>
            Continue
          </Button>
        }
      />
    </form>
  );
}

const PLANS: readonly { kind: PlanKind; runtime: string; name: string; mark: string }[] = [
  { kind: 'claude_token', runtime: 'claude', name: 'Claude', mark: 'C' },
  { kind: 'codex_login', runtime: 'codex', name: 'ChatGPT', mark: 'G' },
];

function ModelStep() {
  const subs = useQuery(subscriptionsQuery);
  const { data: login } = useQuery(loginQuery);
  const connect = useStepAction('setup.connect');
  const list = subs.data?.subscriptions ?? [];
  const any = list.length > 0;
  const going = loginLive(login) ? login : null;

  const end = (plan: (typeof PLANS)[number], first: boolean) => {
    const sub = list.find((s) => s.runtime === plan.runtime);
    if (sub)
      return (
        <span className="wl-done">
          {sub.name}
          <Icon name="check" />
        </span>
      );
    if (going?.kind === plan.kind && going.state === 'waiting' && going.url)
      return (
        <a className="btn btn-fill" href={going.url} target="_blank" rel="noopener noreferrer">
          Open {plan.name}
        </a>
      );
    return (
      <Button
        variant={first && !any && !going ? 'primary' : 'plain'}
        busy={going?.kind === plan.kind}
        disabled={!!going}
        onClick={() => void connect.press({ kind: plan.kind })}
      >
        Connect
      </Button>
    );
  };

  const failed = login && !going && login.state === 'failed' ? login.message || 'The sign-in did not finish' : null;
  return (
    <>
      <div className="wl-list">
        {PLANS.map((plan, i) => (
          <div className="wl-row" key={plan.kind}>
            <span className="wl-mark" aria-hidden="true">
              {plan.mark}
            </span>
            <b>{plan.name}</b>
            {end(plan, i === 0)}
          </div>
        ))}
      </div>
      {going?.code && (
        <p className="wl-done">
          Code <b>{going.code}</b>
        </p>
      )}
      {going?.accepts_code && !going.code_sent && <CodeForm />}
      <Foot
        error={connect.error ?? failed}
        left={
          <Button variant="quiet" onClick={() => void run('setup.open', { step: 'paste' }, click)}>
            Paste key
          </Button>
        }
        right={
          any ? (
            <Button variant="primary" onClick={() => void run('setup.next', { from: 'model' }, click)}>
              Continue
            </Button>
          ) : undefined
        }
      />
    </>
  );
}

/** A sign-in that hands a code back on the provider's page: the person pastes it here. */
function CodeForm() {
  const [code, setCode] = useState('');
  const send = useStepAction('setup.sendCode');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (code.trim()) void send.press({ code: code.trim() });
  };
  return (
    <form className="wl-fields" onSubmit={submit}>
      <Field label="Code" labelHidden placeholder="Code" value={code} onValueChange={setCode} autoComplete="one-time-code" error={send.error ? 'Did not go through' : undefined} />
      <Button type="submit" busy={send.pending} disabled={!code.trim()}>
        Sign in
      </Button>
    </form>
  );
}

const KEYS: readonly { value: KeyKind; label: string }[] = [
  { value: 'anthropic_key', label: 'Claude' },
  { value: 'openai_key', label: 'ChatGPT' },
];

function PasteStep() {
  const [kind, setKind] = useState<KeyKind>('anthropic_key');
  const [secret, setSecret] = useState('');
  const paste = useStepAction('setup.pasteKey');
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!secret.trim()) return;
    if (await paste.press({ kind, secret })) setSecret('');
  };
  return (
    <form className="wl-form" onSubmit={(e) => void submit(e)}>
      <div className="wl-fields">
        <Select label="Model" labelHidden options={KEYS} value={kind} onValueChange={setKind} />
        <Field label="Key" labelHidden type="password" value={secret} onValueChange={setSecret} autoComplete="off" />
      </div>
      <Foot
        error={paste.error}
        right={
          <Button type="submit" variant="primary" busy={paste.pending} disabled={!secret.trim()}>
            Connect
          </Button>
        }
      />
    </form>
  );
}

function TeamStep() {
  const { data: record } = useQuery(setupQuery);
  const choices = record?.first_jobs?.choices ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const job = picked ?? (record?.first_jobs?.picked || choices[0]?.key || '');
  const finish = useStepAction('setup.finish');
  return (
    <>
      <RadioGroup className="wl-list" value={job} onValueChange={(v) => setPicked(v as string)} aria-label="First job">
        {choices.map((c) => (
          <Choice key={c.key} value={c.key} label={c.label} on={c.key === job} />
        ))}
      </RadioGroup>
      <Foot
        error={finish.error}
        left={
          <Button variant="quiet" disabled={finish.pending} onClick={() => void finish.press({ job: '' })}>
            Skip
          </Button>
        }
        right={
          <Button variant="primary" busy={finish.pending} onClick={() => void finish.press({ job })}>
            Start
          </Button>
        }
      />
    </>
  );
}
