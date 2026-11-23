import { Radio } from '@base-ui/react/radio';
import { RadioGroup } from '@base-ui/react/radio-group';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { lazy, Suspense, useState, type FormEvent, type ReactNode } from 'react';
import { run, useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import { Checkbox } from '@/kit/Checkbox';
import { providerReady, subscriptionsQuery, type SignInKind } from '@/features/settings/routing';
import { Dot } from '@/tico/dot';
import { keyLost, pendingSetup, reasonOf } from './handoff';
import { githubQuery, setupQuery, type KeyKind } from './queries';
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
  model: 'Connect your AI plan',
  paste: 'Paste a key',
  team: 'Pick a team',
  github: 'Connect GitHub',
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
      <Step key={step ?? 'closed'} step={shown} />
    </Dialog>
  );
}

function Step({ step }: { step: SetupStep }) {
  if (step === 'path') return <PathStep />;
  if (step === 'name') return <NameStep />;
  if (step === 'model') return <ModelStep />;
  if (step === 'paste') return <PasteStep />;
  if (step === 'github') return <GithubStep />;
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
          <Button variant="primary" busy={choose.pending} onClick={() => choose.press({ path })}>
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

const ConnectPlan = lazy(() => import('@/features/settings/exports').then((m) => ({ default: m.ConnectPlan })));

const PLANS = [
  { kind: 'claude_token', runtime: 'claude', name: 'Claude plan', mark: 'C' },
  { kind: 'codex_login', runtime: 'codex', name: 'ChatGPT plan', mark: 'G' },
] as const;

function ModelStep() {
  const { data } = useQuery(subscriptionsQuery);
  const [kind, setKind] = useState<SignInKind | null>(null);
  const next = useStepAction('setup.next');
  const ready = (data?.subscriptions ?? []).filter(providerReady);
  return (
    <>
      <div className="wl-list">
        {PLANS.map((plan) => {
          const connected = ready.find((s) => s.runtime === plan.runtime);
          return (
            <div className="wl-row" key={plan.kind}>
              <span className="wl-mark" aria-hidden="true">{plan.mark}</span>
              <b>{plan.name}</b>
              {connected ? (
                <span className="wl-done">{connected.name}<Icon name="check" /></span>
              ) : (
                <Button disabled={kind !== null} onClick={() => setKind(plan.kind)}>Connect</Button>
              )}
            </div>
          );
        })}
      </div>
      {kind && (
        <Suspense fallback={null}>
          <ConnectPlan kind={kind} inline onClose={() => setKind(null)} />
        </Suspense>
      )}
      <Foot
        error={next.error}
        left={<Button variant="quiet" onClick={() => run('setup.open', { step: 'paste' }, click)}>Use an API key</Button>}
        right={ready.length > 0 && (
          <Button variant="primary" busy={next.pending} onClick={() => next.press({ from: 'model' })}>Continue</Button>
        )}
      />
    </>
  );
}

const KEYS: readonly { value: KeyKind; label: string }[] = [
  { value: 'anthropic_key', label: 'Anthropic' },
  { value: 'openai_key', label: 'OpenAI' },
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
        <Select label="Provider" labelHidden options={KEYS} value={kind} onValueChange={setKind} />
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
          <Button variant="quiet" disabled={finish.pending} onClick={() => finish.press({ job: '' })}>
            Skip
          </Button>
        }
        right={
          <Button variant="primary" busy={finish.pending} onClick={() => finish.press({ job })}>
            Start
          </Button>
        }
      />
    </>
  );
}

/* ---------------------------------------------------------------- Connect GitHub (the hand-off, s1158) */

const GITHUB_ORG = /^[A-Za-z0-9-]{1,39}$/;

/** Where GitHub makes the App, then the hand-off: the browser goes to GitHub and comes back through the server. */
function GithubStep() {
  const app = useQuery(githubQuery);
  if (app.data?.connected) return <GithubConnected org={app.data.org ?? ''} installed={!!app.data.installed} />;
  return <GithubForm />;
}

function GithubForm() {
  const [where, setWhere] = useState<'personal' | 'org'>('personal');
  const [org, setOrg] = useState('');
  const [admin, setAdmin] = useState(true);
  const [lost, setLost] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending] = useState(pendingSetup);
  const connect = useAction('github.connect');
  const replace = useAction('github.replaceKey');
  const finish = useAction('github.finish');
  const inOrg = where === 'org';
  const ready = !inOrg || GITHUB_ORG.test(org.trim());
  // A personal account always asks for Administration (the server sets it).
  const input = inOrg ? { org: org.trim(), administration: admin } : { administration: true };

  const press = async (action: typeof connect, i: unknown) => {
    setError(null);
    const r = await action.run(i);
    if (r.ok) return;
    if (keyLost(r.error)) setLost(true);
    else setError(reasonOf(r));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) void press(connect, input);
  };

  if (lost)
    return (
      <>
        <p className="t-read">The saved key is lost. Replacing it removes the GitHub app and directory sync keys.</p>
        <Foot
          error={error}
          left={
            <Button variant="quiet" disabled={replace.pending} onClick={() => setLost(false)}>
              Cancel
            </Button>
          }
          right={
            <Button variant="primary" busy={replace.pending} onClick={() => press(replace, input)}>
              Replace key
            </Button>
          }
        />
      </>
    );

  return (
    <form className="wl-form" onSubmit={submit}>
      <RadioGroup className="wl-list" value={where} onValueChange={(v) => setWhere(v as 'personal' | 'org')} aria-label="Where">
        <Choice value="personal" label="My account" on={!inOrg} />
        <Choice value="org" label="An organization" on={inOrg} />
      </RadioGroup>
      {inOrg && (
        <div className="wl-fields">
          <Field label="Organization" labelHidden placeholder="Organization" value={org} onValueChange={setOrg} autoComplete="off" autoFocus />
          <Checkbox label="Let agents create repositories" checked={admin} onCheckedChange={setAdmin} />
        </div>
      )}
      <Foot
        error={error}
        left={
          pending ? (
            <Button variant="quiet" busy={finish.pending} onClick={() => press(finish, {})}>
              Finish
            </Button>
          ) : undefined
        }
        right={
          <Button type="submit" variant="primary" busy={connect.pending} disabled={!ready}>
            Connect
          </Button>
        }
      />
    </form>
  );
}

function GithubConnected({ org, installed }: { org: string; installed: boolean }) {
  const install = useStepAction('tools.installGithub');
  return (
    <>
      <div className="wl-list">
        <div className="wl-row">
          <span className="wl-mark" aria-hidden="true">
            G
          </span>
          <b>{org}</b>
          <span className="wl-done">
            {installed ? 'Connected' : 'Not installed'}
            {installed && <Icon name="check" />}
          </span>
        </div>
      </div>
      <Foot
        error={install.error}
        right={
          installed ? (
            <Button variant="primary" onClick={() => run('setup.close', {}, click)}>
              Done
            </Button>
          ) : (
            <Button variant="primary" busy={install.pending} onClick={() => install.press({})}>
              Install
            </Button>
          )
        }
      />
    </>
  );
}
