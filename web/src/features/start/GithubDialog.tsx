import { Radio } from '@base-ui/react/radio';
import { RadioGroup } from '@base-ui/react/radio-group';
import { useState, type FormEvent, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { keyLost, pendingSetup, reasonOf } from './handoff';
import './start.css';

/* Connect GitHub (s1158), from Tools › GitHub: where GitHub makes the App, then the hand-off: the browser goes to GitHub
   and comes back through the server. A form, so Enter reaches its button. */

const GITHUB_ORG = /^[A-Za-z0-9-]{1,39}$/;

export function GithubDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} className="wl" title="Connect GitHub">
      <GithubForm />
    </Dialog>
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

function Choice({ value, label, on }: { value: string; label: string; on: boolean }) {
  return (
    <label className={on ? 'wl-row radio is-on' : 'wl-row radio'}>
      <Radio.Root value={value} className="radio-box" />
      <b>{label}</b>
      <span />
    </label>
  );
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
