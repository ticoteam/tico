import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useAction } from '@/actions';
import type { Audience } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { agentsQuery, groupsQuery, peopleQuery, type AccessView, type AgentRow, type Credential, type Levels } from './queries';

const copy = (text: string, what: string) =>
  void navigator.clipboard.writeText(text).then(
    () => toast({ text: `${what} copied`, kind: 'done' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );

type Open = { open: boolean; onOpenChange: (open: boolean) => void };

/** The people an agent works for: at least one (ui settingsEditOwners). */
export function WorksForDialog({ open, onOpenChange, slug, name, now }: Open & { slug: string; name: string; now: string[] }) {
  const people = useQuery(peopleQuery);
  const save = useAction('agents.setWorksFor');
  const [pick, setPick] = useState(now);
  const same = pick.length === now.length && pick.every((p) => now.includes(p));
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setPick(now);
        onOpenChange(o);
      }}
      title={`Who ${name} works for`}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!pick.length || same}
            onClick={() => {
              onOpenChange(false);
              void save.run({ slug, people: pick });
            }}
          >
            Save
          </Button>
        </>
      }
    >
      {(people.data ?? []).map((p) => (
        <Checkbox
          key={p.id}
          label={p.name}
          checked={pick.includes(p.id)}
          onCheckedChange={(on) => setPick((cur) => (on ? [...cur, p.id] : cur.filter((x) => x !== p.id)))}
        />
      ))}
    </Dialog>
  );
}

/** A new credential for an agent that runs elsewhere: asked first when one exists, then shown once (ui settingsAgentCredential). */
export function CredentialDialog({ open, onOpenChange, slug, name, row }: Open & { slug: string; name: string; row: AgentRow }) {
  const make = useAction('agents.newCredential');
  const [made, setMade] = useState<Credential | null>(null);
  const rotating = !!row.agent?.credential;
  const close = (o: boolean) => {
    if (!o) setMade(null);
    onOpenChange(o);
  };
  const run = async () => {
    const r = await make.run({ slug });
    if (r.ok) setMade(r.out as Credential);
  };
  if (!made)
    return (
      <Dialog
        open={open}
        onOpenChange={close}
        title={rotating ? `New credential for ${name}?` : `Credential for ${name}`}
        description={rotating ? 'The one it has now stops at once. Install the new one on its computer.' : 'Shown once. It is the agent’s identity.'}
        alert={rotating}
        actions={
          <>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button variant="primary" busy={make.pending} onClick={() => void run()}>
              Make it
            </Button>
          </>
        }
      />
    );
  const url = made.setup?.url ?? location.origin;
  const openclaw = (made.harness ?? row.agent?.harness) === 'openclaw';
  const command =
    `curl -fsSL -H "Authorization: Bearer ${made.token}" ${url}/api/v2/agents/setup-script -o hermes_agent.py && python3 hermes_agent.py install` +
    `${openclaw ? ' --harness openclaw' : ''} --profile ${row.agent?.profile || slug} --url ${url} --bot ${slug} --token ${made.token}`;
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={`Credential for ${name}`}
      description="Shown once. On the computer that runs it, this command installs Tico and a heartbeat."
      actions={
        <>
          <Button onClick={() => copy(made.token, 'Token')}>Copy token</Button>
          <Button onClick={() => copy(command, 'Command')}>Copy command</Button>
          <Button variant="primary" onClick={() => close(false)}>
            Done
          </Button>
        </>
      }
    >
      <code className="once">{command}</code>
    </Dialog>
  );
}

/** A task for a person, from their page (ui personTaskModal). */
export function GiveTaskDialog({ open, onOpenChange, person, name }: Open & { person: string; name: string }) {
  const give = useAction('people.giveTask');
  const [title, setTitle] = useState('');
  const [message, setMessage] = useState('');
  const send = () => {
    onOpenChange(false);
    void give.run({ person, title: title.trim(), ...(message.trim() ? { message: message.trim() } : {}) });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) {
          setTitle('');
          setMessage('');
        }
        onOpenChange(o);
      }}
      title={`Task for ${name.split(' ')[0]}`}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!title.trim()} onClick={send}>
            Give it
          </Button>
        </>
      }
    >
      <Field label="Title" value={title} onValueChange={setTitle} autoFocus />
      <Field label="Message" value={message} onValueChange={setMessage} area />
    </Dialog>
  );
}

const LEVELS = [
  ['see', 'Can see'],
  ['read', 'Can read'],
  ['write', 'Can write'],
] as const;

/** Who may see, read and write to a person or a team: everyone, or the people and teams picked. */
export function AccessDialog({ open, onOpenChange, kind, id, name, now }: Open & { kind: 'person' | 'team'; id: string; name: string; now: AccessView }) {
  const people = useQuery(peopleQuery);
  const groups = useQuery(groupsQuery);
  const save = useAction('team.setAccess');
  const [draft, setDraft] = useState<Levels>({ see: now.see, read: now.read, write: now.write });
  const set = (key: keyof Levels, a: Audience) => setDraft((d) => ({ ...d, [key]: a }));
  const toggle = (list: string[], v: string, on: boolean) => (on ? [...list, v] : list.filter((x) => x !== v));
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setDraft({ see: now.see, read: now.read, write: now.write });
        onOpenChange(o);
      }}
      title={`Who can reach ${name}`}
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false);
              void save.run({ kind, id, ...draft });
            }}
          >
            Save
          </Button>
        </>
      }
    >
      {LEVELS.map(([key, label]) => {
        const a = draft[key];
        return (
          <fieldset key={key} className="lvl" aria-label={label}>
            <Checkbox label={`${label}: everyone`} checked={a.everyone} onCheckedChange={(on) => set(key, { ...a, everyone: on })} />
            {!a.everyone && (
              <>
                {(people.data ?? []).map((p) => (
                  <Checkbox key={p.id} label={p.name} checked={a.people.includes(p.id)} onCheckedChange={(on) => set(key, { ...a, people: toggle(a.people, p.id, on) })} />
                ))}
                {(groups.data ?? []).map((g) => (
                  <Checkbox key={g.id} label={g.name} checked={a.teams.includes(g.id)} onCheckedChange={(on) => set(key, { ...a, teams: toggle(a.teams, g.id, on) })} />
                ))}
              </>
            )}
          </fieldset>
        );
      })}
    </Dialog>
  );
}

/** A Tico account with a made-up password, shown once and kept nowhere. Asked first. */
export function PasswordDialog({ open, onOpenChange, person, name }: Open & { person: string; name: string }) {
  const make = useAction('people.makePassword');
  const [made, setMade] = useState<{ email?: string; password: string } | null>(null);
  const close = (o: boolean) => {
    if (!o) setMade(null);
    onOpenChange(o);
  };
  const run = async () => {
    const r = await make.run({ person });
    if (r.ok) setMade(r.out as { email?: string; password: string });
  };
  if (!made)
    return (
      <Dialog
        open={open}
        onOpenChange={close}
        title={`Make a password for ${name}?`}
        description="They sign in with their email and this password. It is shown once."
        alert
        actions={
          <>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button variant="primary" busy={make.pending} onClick={() => void run()}>
              Make it
            </Button>
          </>
        }
      />
    );
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={`Password for ${name}`}
      description={made.email ? `Shown once. They sign in as ${made.email}.` : 'Shown once.'}
      actions={
        <>
          <Button onClick={() => copy(made.password, 'Password')}>Copy</Button>
          <Button variant="primary" onClick={() => close(false)}>
            Done
          </Button>
        </>
      }
    >
      <code className="once">{made.password}</code>
    </Dialog>
  );
}

const OPERATOR = '-';

/** Archive, asked first: who takes its open tasks, the person who runs it unless another agent is picked (ui settingsArchiveConfirm). */
export function ArchiveDialog({ open, onOpenChange, slug, name, row }: Open & { slug: string; name: string; row: AgentRow | undefined }) {
  const roster = useQuery(agentsQuery);
  const people = useQuery(peopleQuery);
  const archive = useAction('agents.archive');
  const [heir, setHeir] = useState(OPERATOR);
  const operator = people.data?.find((p) => p.id === row?.owner_id)?.name ?? 'The person who runs it';
  const options = [
    { value: OPERATOR, label: operator },
    ...(roster.data ?? []).filter((r) => r.slug !== slug && r.state !== 'archived').map((r) => ({ value: r.id, label: r.display_name || r.slug })),
  ];
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (o) setHeir(OPERATOR);
        onOpenChange(o);
      }}
      title={`Archive ${name}?`}
      alert
      actions={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false);
              void archive.run({ slug, ...(heir === OPERATOR ? {} : { successor: heir }) });
            }}
          >
            Archive
          </Button>
        </>
      }
    >
      <Select label="Hand its work to" options={options} value={heir} onValueChange={setHeir} />
    </Dialog>
  );
}
