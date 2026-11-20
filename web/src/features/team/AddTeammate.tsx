import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { useAction } from '@/actions';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Select } from '@/kit/Select';
import { SkeletonRows } from '@/kit/Skeleton';
import type { AddStep } from './actions';
import { seatHref } from './model';
import { cloudAgentsQuery, computersQuery, groupsQuery, isOpenRequest, seatsQuery, templatesQuery } from './queries';
import './team.css';

const TITLE: Record<AddStep, string> = {
  pick: 'Add teammate',
  new: 'New agent',
  template: 'From a template',
  person: 'Invite a person',
  cloud: 'Cloud agents',
};
const PICKS: [AddStep, IconName, string][] = [
  ['new', 'spark', 'New agent'],
  ['template', 'library', 'From a template'],
];

/**
 * Add teammate (mates.js ADD): a new agent, one from a template, an invite for a person, or a cloud agent (the mock's
 * External agent line, the old app's Cloud agents). The step is in the address (`?add=`), so Back steps back.
 */
export function AddTeammate({
  step,
  team,
  onStep,
  onClose,
}: {
  step: AddStep;
  team: string;
  /** Off a team page the step is held by the caller instead of the address. */
  onStep?: (step: AddStep) => void;
  onClose?: () => void;
}) {
  const navigate = useNavigate();
  const addOpen = useAction('team.addTeammate');
  // Cloud agents: the team picked there, and the agent being requested (its first task asked for) over the list.
  const [cloudTeam, setCloudTeam] = useState(team);
  const [asking, setAsking] = useState<Asking | null>(null);
  const to = (s: AddStep) => {
    setAsking(null);
    if (onStep) onStep(s);
    else void addOpen.run({ step: s });
  };
  const requesting = step === 'cloud' ? asking : null;
  const close =
    onClose ?? (() => void navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, add: undefined }) } as never));
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && close()}
      title={requesting ? (requesting.id === OTHER ? 'Another agent' : `Request ${requesting.name}`) : TITLE[step]}
      className="add"
      lead={
        step !== 'pick' && (
          <button type="button" className="ib ib-sm" aria-label="Back" title="Back" onClick={() => (requesting ? setAsking(null) : to('pick'))}>
            <Icon name="chevron" className="flip" />
          </button>
        )
      }
    >
      {step === 'pick' && (
        <>
          <div className="add-picks">
            {PICKS.map(([s, icon, label]) => (
              <button key={s} type="button" className="add-pick" onClick={() => to(s)}>
                <Icon name={icon} />
                <b>{label}</b>
              </button>
            ))}
          </div>
          <button type="button" className="add-person" onClick={() => to('person')}>
            <Icon name="people" />
            <span>Invite a person</span>
            <Icon name="chevron" />
          </button>
          <button type="button" className="add-person" onClick={() => to('cloud')} data-target="team:cloud">
            <Icon name="link" />
            <span>Cloud agents</span>
            <Icon name="chevron" />
          </button>
        </>
      )}
      {step === 'new' && <NewAgent team={team} />}
      {step === 'template' && <FromTemplate team={team} />}
      {step === 'person' && <Invite team={team} />}
      {step === 'cloud' &&
        (requesting ? (
          <CloudRequest key={requesting.id} agent={requesting} team={cloudTeam} />
        ) : (
          <CloudAgents team={cloudTeam} onTeam={setCloudTeam} onRequest={setAsking} />
        ))}
    </Dialog>
  );
}

/** Add agent from outside the team pages (Settings' fleet): the same dialog, its step held here. */
export function AddAgentButton({ team = '' }: { team?: string }) {
  const [step, setStep] = useState<AddStep | null>(null);
  return (
    <>
      <Button variant="primary" icon="plus" onClick={() => setStep('pick')} data-target="team:add">
        Add agent
      </Button>
      {step && <AddTeammate step={step} team={team} onStep={setStep} onClose={() => setStep(null)} />}
    </>
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
          onClick={() => add.run({ name: name.trim(), role: role.trim() || undefined, team: team || undefined, computer: computer || undefined })}
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
          onClick={() => invite.run({ email: email.trim(), name: name.trim() || undefined, team: team || undefined, role })}
        >
          Send invite
        </Button>
      </div>
    </>
  );
}

/** An agent not on the list, requested by the name or website the person types. */
const OTHER = 'other';
/** The agent a request is for: one from the list, or another one (`other`, its name typed in the request). */
type Asking = { id: string; name: string };

/** Cloud agents (cloud-agents.ts): the coding agents others make. One Tico connects is added as an agent under the
    team's lead; any other, or one not listed (Another agent), is requested, and a seat holds its place until Tico
    support connects it. A requested one opens its seat's Connecting page. */
function CloudAgents({ team, onTeam, onRequest }: { team: string; onTeam: (team: string) => void; onRequest: (agent: Asking) => void }) {
  const navigate = useNavigate();
  const { data: agents, isError } = useQuery(cloudAgentsQuery);
  const { data: seats } = useQuery(seatsQuery);
  const add = useAction('agents.addCloud');
  const cancel = useAction('agents.cancelCloudRequest');
  // The row waiting on the server: one press at a time.
  const [busy, setBusy] = useState('');
  const press = async (id: string, go: () => Promise<unknown>) => {
    setBusy(id);
    try {
      await go();
    } finally {
      setBusy('');
    }
  };
  const asked = (id: string) => seats?.find((x) => x.agent === id && isOpenRequest(x) && (x.team ?? '') === team);
  return (
    <>
      <TeamPick value={team} onValueChange={onTeam} />
      {isError ? (
        <Empty icon="alert" title="Cloud agents did not load" />
      ) : !agents ? (
        <SkeletonRows rows={8} label="Loading cloud agents" />
      ) : (
        <div className="tpl-list cloud-list" role="list" aria-label="Cloud agents">
          {agents.map((a) => {
            const seat = asked(a.id);
            return (
              <div key={a.id} role="listitem" className="tpl cloud" data-target={`cloud:${a.id}`}>
                <Avatar kind="agent" name={a.name} size={28} />
                <span className="tpl-name">{a.name}</span>
                <span className="tpl-line" title={a.line}>
                  {a.line}
                </span>
                <span className="cloud-act">
                  {a.supported ? (
                    <Button busy={busy === a.id} disabled={!!busy} onClick={() => press(a.id, () => add.run({ agent: a.id, team: team || undefined }))}>
                      Add
                    </Button>
                  ) : seat ? (
                    <>
                      <span className="cloud-mark">{seat.request_state === 'canceling' ? 'Canceling' : 'Requested'}</span>
                      <Menu trigger={<IconButton icon="more" label={`More on ${a.name}`} size="sm" quiet busy={busy === a.id} />}>
                        <MenuItem label="Open" icon="arrow" onSelect={() => void navigate({ to: seatHref(seat.id) })} />
                        <MenuItem label="Cancel request" icon="close" onSelect={() => void press(a.id, () => cancel.run({ seat: seat.id }))} />
                      </Menu>
                    </>
                  ) : (
                    <Button disabled={!!busy} onClick={() => onRequest({ id: a.id, name: a.name })}>
                      Request
                    </Button>
                  )}
                </span>
              </div>
            );
          })}
          <div role="listitem" className="tpl cloud" data-target={`cloud:${OTHER}`}>
            <Avatar kind="agent" name="Another agent" initials="" size={28} className="is-seat" />
            <span className="tpl-name">Another agent</span>
            <span className="tpl-line" />
            <span className="cloud-act">
              <Button disabled={!!busy} onClick={() => onRequest({ id: OTHER, name: '' })}>
                Request
              </Button>
            </span>
          </div>
        </div>
      )}
    </>
  );
}

/** A request (cloud-agents.ts requestDialog): another agent's name, then its first task, which waits on the seat until
    the agent connects. Sent, it opens the seat's Connecting page. */
function CloudRequest({ agent, team }: { agent: Asking; team: string }) {
  const request = useAction('agents.requestCloud');
  const other = agent.id === OTHER;
  const [name, setName] = useState('');
  const [task, setTask] = useState('');
  const ready = !other || !!name.trim();
  return (
    <>
      {other && <Field label="Name" placeholder="Name or website" value={name} onValueChange={setName} autoFocus />}
      <Field label="First task" placeholder="Optional" area value={task} onValueChange={setTask} autoFocus={!other} />
      <div className="modal-row">
        <Button
          variant="primary"
          busy={request.pending}
          disabled={!ready || request.pending}
          onClick={() =>
            void request.run({
              agent: agent.id,
              team: team || undefined,
              first_task: task.trim() || undefined,
              name: other ? name.trim() : undefined,
            })
          }
        >
          Request
        </Button>
      </div>
    </>
  );
}
