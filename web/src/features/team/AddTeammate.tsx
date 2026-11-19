import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field, Search } from '@/kit/Field';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Pill } from '@/kit/Pill';
import { Select } from '@/kit/Select';
import type { AddStep } from './actions';
import { computersQuery, groupsQuery, templatesQuery } from './queries';

const TITLE: Record<AddStep, string> = { pick: 'Add teammate', new: 'New agent', template: 'From a template', person: 'Invite a person' };
const PICKS: [AddStep, IconName, string][] = [
  ['new', 'spark', 'New agent'],
  ['template', 'library', 'From a template'],
];

/**
 * Add teammate (mates.js ADD): a new agent, one from a template, or an invite for a person. External agents are one
 * dimmed line. The step is in the address (`?add=`), so Back steps back.
 */
export function AddTeammate({ step, team }: { step: AddStep; team: string }) {
  const navigate = useNavigate();
  const go = useAction('team.addTeammate');
  const close = () => void navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: undefined }) } as never);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && close()}
      title={TITLE[step]}
      actions={
        step === 'pick' ? (
          <Button onClick={close}>Cancel</Button>
        ) : (
          <IconButton icon="chevron" label="Back" size="sm" onClick={() => void go.run({ step: 'pick' })} />
        )
      }
    >
      {step === 'pick' && (
        <>
          <div className="add-picks">
            {PICKS.map(([s, icon, label]) => (
              <button key={s} type="button" className="add-pick" onClick={() => void go.run({ step: s })}>
                <Icon name={icon} />
                <b>{label}</b>
              </button>
            ))}
          </div>
          <button type="button" className="add-person" onClick={() => void go.run({ step: 'person' })}>
            <Icon name="people" />
            <span>Invite a person</span>
            <Icon name="chevron" />
          </button>
          <span className="add-person is-soon" aria-disabled="true">
            <Icon name="link" />
            <span>External agent</span>
            <Pill>Coming soon</Pill>
          </span>
        </>
      )}
      {step === 'new' && <NewAgent team={team} />}
      {step === 'template' && <FromTemplate team={team} />}
      {step === 'person' && <Invite team={team} />}
    </Dialog>
  );
}

function TeamPick({ value, onValueChange }: { value: string; onValueChange: (v: string) => void }) {
  const { data: groups } = useQuery(groupsQuery);
  return <Select label="Team" options={(groups ?? []).map((g) => ({ value: g.id, label: g.name }))} value={value || null} onValueChange={onValueChange} />;
}

function NewAgent({ team: start }: { team: string }) {
  const add = useAction('agents.add');
  const { data: computers } = useQuery(computersQuery);
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [team, setTeam] = useState(start);
  const [computer, setComputer] = useState('');
  return (
    <>
      <Field label="Name" value={name} onValueChange={setName} autoFocus />
      <Field label="Role" value={role} onValueChange={setRole} />
      <div className="f-pair">
        <TeamPick value={team} onValueChange={setTeam} />
        <Select
          label="Computer"
          options={(computers ?? []).map((c) => ({ value: c.id, label: c.label }))}
          value={computer || null}
          onValueChange={setComputer}
          placeholder="Shared"
        />
      </div>
      <div className="modal-row">
        <Button
          variant="primary"
          busy={add.pending}
          disabled={!name.trim() || add.pending}
          onClick={() => void add.run({ name: name.trim(), role: role.trim() || undefined, team: team || undefined, computer: computer || undefined })}
        >
          Add agent
        </Button>
      </div>
    </>
  );
}

function FromTemplate({ team }: { team: string }) {
  const add = useAction('agents.add');
  const { data: templates } = useQuery(templatesQuery);
  const [q, setQ] = useState('');
  const [on, setOn] = useState('');
  const shown = (templates ?? []).filter((t) => !q.trim() || `${t.name} ${t.summary ?? ''}`.toLowerCase().includes(q.trim().toLowerCase()));
  const chosen = templates?.find((t) => t.slug === on);
  return (
    <>
      <Search label="Search templates" placeholder={`Search ${templates?.length ?? ''} templates`} value={q} onValueChange={setQ} wide autoFocus />
      <div className="tpl-list" role="listbox" aria-label="Templates">
        {shown.map((t) => (
          <button key={t.slug} type="button" role="option" aria-selected={t.slug === on} className={t.slug === on ? 'tpl is-on' : 'tpl'} onClick={() => setOn(t.slug)}>
            <Avatar kind="agent" name={t.name} size={28} />
            <span className="tpl-name">{t.name}</span>
            <span className="tpl-line">{t.summary}</span>
            <span className="tpl-check">{t.slug === on && <Icon name="check" />}</span>
          </button>
        ))}
      </div>
      <div className="modal-row">
        <Button
          variant="primary"
          busy={add.pending}
          disabled={!chosen || add.pending}
          onClick={() => chosen && void add.run({ name: chosen.name, template: chosen.slug, team: team || undefined })}
        >
          Add agent
        </Button>
      </div>
    </>
  );
}

function Invite({ team: start }: { team: string }) {
  const invite = useAction('people.invite');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [team, setTeam] = useState(start);
  const [role, setRole] = useState<'member' | 'admin'>('member');
  const ok = /^[^@\s]+@[^@\s]+$/.test(email.trim());
  return (
    <>
      <Field label="Email" type="email" value={email} onValueChange={setEmail} autoFocus />
      <Field label="Name" value={name} onValueChange={setName} />
      <div className="f-pair">
        <TeamPick value={team} onValueChange={setTeam} />
        <Select
          label="Role"
          options={[
            { value: 'member', label: 'Member' },
            { value: 'admin', label: 'Admin' },
          ]}
          value={role}
          onValueChange={setRole}
        />
      </div>
      <div className="modal-row">
        <Button
          variant="primary"
          busy={invite.pending}
          disabled={!ok || invite.pending}
          onClick={() => void invite.run({ email: email.trim(), name: name.trim() || undefined, team: team || undefined, role })}
        >
          Send invite
        </Button>
      </div>
    </>
  );
}
