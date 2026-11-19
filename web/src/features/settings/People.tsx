import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Facts } from '@/kit/LabelValue';
import { Segmented } from '@/kit/Segmented';
import { toast } from '@/kit/Toast';
import { Section } from '@/pagekinds';
import { Line, Verb, act } from './parts';
import { accessQuery, type AccessFull, type AccessPerson } from './queries';

/* What People could do in the old app (ui/app/settings-people.ts) and the first pass left out: who can join, the
   agents a person may have, and the people who left. */

/** The People section's head button: only for owners and admins. */
export function AddPersonButton() {
  const access = useQuery({ ...accessQuery, retry: false });
  const v = access.data as AccessFull | undefined;
  return v && Array.isArray(v.people) && (v.you === 'owner' || v.you === 'admin') ? <AddPerson v={v} /> : null;
}

/** Add a person; the server mails them a sign-in link when it can (Invite), else they are only added (Add). */
function AddPerson({ v }: { v: AccessFull }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const ready = /^[^\s@]+@[^\s@]+$/.test(email.trim());
  const invites = !!v.invite_email;
  const add = async () => {
    setBusy(true);
    const r = await run('settings.invite', { email: email.trim(), ...(name.trim() ? { name: name.trim() } : {}) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't add"));
    toast({ text: invites ? 'Invite sent' : 'Added', kind: 'done' });
    setOpen(false);
    setEmail('');
    setName('');
  };
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="Add a person"
      trigger={
        <Button variant="primary" data-phone>
          Add person
        </Button>
      }
      actions={
        <>
          <Button variant="quiet" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void add()}>
            {invites ? 'Invite' : 'Add'}
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="Email" value={email} onValueChange={setEmail} autoFocus autoComplete="off" />
        <Field label="Name" value={name} onValueChange={setName} />
      </div>
    </Dialog>
  );
}

/** Who can join, and how many agents a person may have. */
export function Joining({ v }: { v: AccessFull }) {
  const owner = v.you === 'owner';
  const home = v.home_domain ?? '';
  const [asking, setAsking] = useState(false);
  const [limit, setLimit] = useState(String(v.member_bot_limit ?? ''));
  const join = v.join === 'domain' ? 'domain' : 'admin';
  const setJoin = (to: string) => {
    if (to === join) return;
    if (to === 'domain') return void act('settings.setJoining', { allowed: v.allowed, domains: [...v.allowed_domains, home] });
    setAsking(true);
  };
  const n = Number(limit);
  const badLimit = limit.trim() === '' || !Number.isInteger(n) || n < 0 || n > 1000;
  const saveLimit = () => {
    if (!badLimit && n !== v.member_bot_limit) void act('settings.agentLimit', { limit: n });
  };
  return (
    <Section name="Joining" target="setting:joining">
      <Facts label="Joining">
        <Line label="Who can join" target="setting:join">
          {owner && home ? (
            <Segmented
              label="Who can join"
              options={[
                { value: 'admin', label: 'Invited' },
                { value: 'domain', label: `Anyone at ${home}` },
              ]}
              value={join}
              onValueChange={setJoin}
            />
          ) : join === 'domain' ? (
            `Anyone at ${home}`
          ) : (
            'Invited'
          )}
        </Line>
        {v.member_bot_limit != null && (
          <Line label="Agents a person" target="setting:agent-limit">
            <Field
              label="Agents a person"
              labelHidden
              type="number"
              wide={false}
              value={limit}
              onValueChange={setLimit}
              error={badLimit ? '0 to 1000' : undefined}
            />
            <Verb onClick={saveLimit}>Save</Verb>
          </Line>
        )}
      </Facts>
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title={`Stop sign-in for everyone at ${home}?`}
        description="Only people added here keep signing in."
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setAsking(false);
                void act('settings.setJoining', { allowed: v.allowed, domains: v.allowed_domains.filter((d) => d !== home) });
              }}
            >
              Stop
            </Button>
          </>
        }
      />
    </Section>
  );
}

/** People who left: the owner brings them back, or removes one with no history for good. */
export function LeftPeople({ v }: { v: AccessFull }) {
  const [forgetting, setForgetting] = useState<AccessPerson | null>(null);
  const left = v.people.filter((p) => p.left);
  if (left.length === 0) return null;
  const owner = v.you === 'owner';
  return (
    <Section name="Left" count={left.length} defaultOpen={false} target="setting:left">
      {left.map((p) => (
        <div key={p.id} className="row g-person is-off" data-target={`person:${p.id}`} title={p.name}>
          <Avatar kind="person" name={p.name} decorative />
          <span className="row-title">{p.name}</span>
          <span className="row-sub">{p.email}</span>
          <span className="row-cell">{owner && p.removable && <Verb quiet onClick={() => setForgetting(p)}>Remove</Verb>}</span>
          <span className="row-age">{owner && <Verb onClick={() => void act('settings.restorePerson', { person: p.id })}>Restore</Verb>}</span>
        </div>
      ))}
      <Dialog
        open={forgetting != null}
        onOpenChange={(o) => !o && setForgetting(null)}
        alert
        title={`Remove ${forgetting?.name ?? ''} for good?`}
        description="This can't be undone."
        actions={
          <>
            <Button variant="quiet" onClick={() => setForgetting(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (forgetting) void act('settings.forgetPerson', { person: forgetting.id });
                setForgetting(null);
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </Section>
  );
}
