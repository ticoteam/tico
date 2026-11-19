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
import { Line, SwitchLine, Verb, act } from './parts';
import type { AccessFull, AccessPerson } from './queries';

/* What People could do in the old app (ui/app/settings-people.ts) and the first pass left out: who can join, the
   agents a person may have, each person's sign-in, the people who left, and adding someone without an email. */

const asDomain = (typed: string) => {
  const d = typed.trim().toLowerCase().replace(/^\*?@/, '');
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(d) ? d : '';
};

/** Add a person. Add sends no email; Invite mails them a sign-in link. A bare domain lets anyone at it in (owner). */
export function AddPerson({ v }: { v: AccessFull }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const domain = v.you === 'owner' && !email.includes('@') ? asDomain(email) : '';
  const ready = !!domain || /^[^\s@]+@[^\s@]+$/.test(email.trim());
  const add = async (invite: boolean) => {
    setBusy(true);
    const r = domain
      ? await run('settings.setJoining', { allowed: v.allowed, domains: [...new Set([...v.allowed_domains, domain])] }, { via: 'click' })
      : await run(invite ? 'settings.invite' : 'settings.addPerson', { email: email.trim(), ...(name.trim() ? { name: name.trim() } : {}) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't add"));
    toast({ text: domain ? `Anyone at ${domain} can sign in` : invite ? 'Invite sent' : 'Added, no email sent', kind: 'done' });
    setOpen(false);
    setEmail('');
    setName('');
  };
  const invites = v.invite_email && !domain;
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="Add a person"
      trigger={
        <Button icon="plus" data-phone>
          Add person
        </Button>
      }
      actions={
        <>
          <Button variant="quiet" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          {invites && (
            <Button variant="quiet" busy={busy} disabled={!ready} onClick={() => void add(false)}>
              Add only
            </Button>
          )}
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void add(invites)}>
            {invites ? 'Invite' : domain ? 'Let in' : 'Add'}
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="Email" value={email} onValueChange={setEmail} autoFocus autoComplete="off" />
        {!domain && <Field label="Name" value={name} onValueChange={setName} />}
      </div>
    </Dialog>
  );
}

/** A person's sign-in: off asks first and signs their open browsers out; on is the person's own press. */
export function SignInLine({ p }: { p: AccessPerson }) {
  const [asking, setAsking] = useState(false);
  return (
    <>
      <SwitchLine
        label="Signs in"
        target={`person-sign-in:${p.id}`}
        checked={p.sign_in}
        onChange={(on) => (on ? void act('settings.signIn', { person: p.id, on }) : setAsking(true))}
      />
      <Dialog
        open={asking}
        onOpenChange={setAsking}
        alert
        title={`Turn off sign-in for ${p.name}?`}
        description="They are signed out now. Their tasks, agents and history stay."
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setAsking(false);
                void act('settings.signIn', { person: p.id, on: false });
              }}
            >
              Turn off
            </Button>
          </>
        }
      />
    </>
  );
}

/** Who can join, the addresses and domains also let in, and how many agents a person may have. */
export function Joining({ v }: { v: AccessFull }) {
  const owner = v.you === 'owner';
  const home = v.home_domain ?? '';
  const [asking, setAsking] = useState<{ title: string; text: string; allowed: string[]; domains: string[] } | null>(null);
  const [limit, setLimit] = useState(String(v.member_bot_limit ?? ''));
  const onRoster = new Set(v.people.map((p) => p.email).filter(Boolean));
  const extras = [...v.allowed_domains.filter((d) => d !== home), ...v.allowed.filter((e) => !onRoster.has(e))];
  const join = v.join === 'domain' ? 'domain' : 'admin';
  const setJoin = (to: string) => {
    if (to === join) return;
    if (to === 'domain') return void act('settings.setJoining', { allowed: v.allowed, domains: [...v.allowed_domains, home] });
    setAsking({
      title: `Stop sign-in for everyone at ${home}?`,
      text: `Only people added here keep signing in.`,
      allowed: v.allowed,
      domains: v.allowed_domains.filter((d) => d !== home),
    });
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
        {extras.length > 0 && (
          <Line label="Also allowed" target="setting:allowed">
            <span className="set-picks">
              {extras.map((x) => (
                <span key={x} className="fs-chip">
                  {x}
                  {owner && (
                    <Verb
                      quiet
                      onClick={() =>
                        setAsking({
                          title: `Remove ${x}?`,
                          text: x.includes('@') ? `${x} can no longer sign in.` : `People at ${x} who are not added here can no longer sign in.`,
                          allowed: v.allowed.filter((e) => e !== x),
                          domains: v.allowed_domains.filter((d) => d !== x),
                        })
                      }
                    >
                      Remove
                    </Verb>
                  )}
                </span>
              ))}
            </span>
          </Line>
        )}
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
        open={asking != null}
        onOpenChange={(o) => !o && setAsking(null)}
        alert
        title={asking?.title ?? ''}
        description={asking?.text}
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsking(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (asking) void act('settings.setJoining', { allowed: asking.allowed, domains: asking.domains });
                setAsking(null);
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
