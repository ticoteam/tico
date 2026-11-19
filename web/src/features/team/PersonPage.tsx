import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { configQuery, meQuery, useActors, type Actor } from '@/data';
import { goalFigure, goalLink, goalsTreeQuery } from '@/features/company/exports';
import { useFollow } from '@/live/follow';
import { Avatar } from '@/kit/Avatar';
import { IconButton } from '@/kit/IconButton';
import { Row } from '@/kit/Row';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { FiguresStrip, Record as RecordPage, Section, type PageState } from '@/pagekinds';
import { bareId, startOfMonth, teamOf } from './model';
import { AccessDialog, GiveTaskDialog, PasswordDialog } from './dialogs';
import { ConfirmDialog, EditDialog, Foot, Fold, MateRow, OwnRow, PickDialog, SecLink, waitsOn } from './parts';
import { accessQuery, agentsQuery, doneTasksQuery, groupsQuery, openTasksQuery, peopleQuery, reachLine, teammateAccessQuery } from './queries';

export interface PersonPageProps {
  actor: Actor;
  settings?: boolean;
}

/** A person's own page: the same column as an agent's, beside the chat with them (mates.js personDesk). */
export function PersonPage({ actor, settings }: PersonPageProps) {
  const id = actor.id.slice(actor.id.indexOf(':') + 1);
  const path = `/team/${encodeURIComponent(id)}`;
  useFollow({ party: actor.id });
  const open = useAction('team.open');
  const rename = useAction('people.rename');
  const { data: access } = useQuery(accessQuery);
  const admin = access?.you === 'owner' || access?.you === 'admin';
  const head: Head = {
    title: actor.name,
    path,
    className: 'rec is-col',
    size: 'large',
    onRename: admin ? (name) => void rename.run({ person: id, name }) : undefined,
    lead: <Avatar kind="person" name={actor.name} hue={actor.hue} size={36} />,
    after: actor.title ? <p className="col-sub">{actor.title}</p> : undefined,
    actions: (
      <IconButton
        icon="gear"
        label="Settings"
        on={!!settings}
        aria-pressed={!!settings}
        onClick={() => void open.run({ who: id, ...(settings ? {} : { tab: 'settings' }) })}
      />
    ),
  };
  return settings ? <PersonSettings actor={actor} id={id} head={head} /> : <PersonWidgets actor={actor} id={id} head={head} />;
}

type Head = {
  title: string;
  path: string;
  className: string;
  size: 'large';
  onRename?: (name: string) => void;
  lead: ReactNode;
  after: ReactNode;
  actions: ReactNode;
};

function PersonWidgets({ actor, id, head }: { actor: Actor; id: string; head: Head }) {
  const { data: me } = useQuery(meQuery);
  const actors = useActors();
  const roster = useQuery(agentsQuery);
  const people = useQuery(peopleQuery);
  const [since] = useState(() => startOfMonth(new Date()).toISOString());
  const open = useQuery(openTasksQuery(actor.id));
  const goals = (useQuery(goalsTreeQuery).data?.goals ?? []).filter((g) => g.owner_id === actor.id);
  const done = useQuery(doneTasksQuery(actor.id, since));
  const [giving, setGiving] = useState(false);
  const tasks = open.data?.tasks ?? [];
  const waits = tasks.filter((t) => waitsOn(t, me?.id));
  const rest = tasks.filter((t) => !waitsOn(t, me?.id));
  const reports = new Set([
    ...(roster.data ?? []).filter((r) => r.reports_to === id).map((r) => r.id),
    ...(people.data ?? []).filter((p) => p.reports_to === id).map((p) => p.id),
  ]);
  const manages = actors.filter((a) => reports.has(bareId(a.id)));
  const doneN = done.data?.tasks.length ?? 0;
  const state: PageState = open.isError ? 'fault' : open.isPending ? 'loading' : 'ready';
  return (
    <RecordPage
      {...head}
      state={state}
      onRetry={() => void open.refetch()}
      figures={
        <FiguresStrip
          label={`${actor.name} at a glance`}
          figures={[
            { label: 'Open', value: tasks.length },
            { label: 'Done this month', value: doneN },
            { label: 'Waits on you', value: waits.length },
            { label: 'Manages', value: manages.length },
          ]}
        />
      }
      sections={
        <>
          {waits.length > 0 && (
            <Section name="Waits on you" count={waits.length}>
              {waits.map((t) => (
                <OwnRow key={t.id} task={t} me={me?.id} />
              ))}
            </Section>
          )}
          {goals.length > 0 && (
            <Section name="Goals" count={goals.length}>
              {goals.map((g) => (
                <Row key={g.id} className="line" title={g.title} href={goalLink(g.id).href} target={`goal:${g.id}`}>
                  <span className="row-mark" />
                  <span className="row-title">{g.title}</span>
                  <span className="row-age">{goalFigure(g)}</span>
                </Row>
              ))}
            </Section>
          )}
          <Section
            name="Tasks"
            actions={
              <>
                <IconButton icon="plus" size="sm" quiet label={`Give ${actor.name} a task`} onClick={() => setGiving(true)} />
                <SecLink href="/tasks">All tasks</SecLink>
              </>
            }
          >
            {rest.map((t) => (
              <OwnRow key={t.id} task={t} me={me?.id} />
            ))}
            {doneN > 0 && <Fold label="Done" text={`${doneN} this month`} />}
            <GiveTaskDialog open={giving} onOpenChange={setGiving} person={id} name={actor.name} />
          </Section>
          {manages.length > 0 && (
            <Section name="Manages" count={manages.length}>
              {manages.map((a) => (
                <MateRow key={a.id} actor={a} />
              ))}
            </Section>
          )}
        </>
      }
    />
  );
}

function PersonSettings({ actor, id, head }: { actor: Actor; id: string; head: Head }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const people = useQuery(peopleQuery);
  const groups = useQuery(groupsQuery);
  const { data: access } = useQuery(accessQuery);
  const actors = useActors();
  const remove = useAction('people.remove');
  const reach = useQuery(teammateAccessQuery('person', id)).data;
  const [editing, setEditing] = useState<'' | 'title' | 'team' | 'role' | 'remove' | 'access' | 'password'>('');
  const close = (o: boolean) => !o && setEditing('');

  const p = people.data?.find((x) => x.id === id);
  const role = access?.people.find((x) => x.id === id);
  const admin = access?.you === 'owner' || access?.you === 'admin';
  const self = me?.id === actor.id;
  const team = teamOf(groups.data ?? [], 'person', id);
  const boss = p?.reports_to ? actors.find((a) => bareId(a.id) === p.reports_to) : undefined;
  const company = config?.company_name || config?.app_name || 'the company';
  const state: PageState = people.isError ? 'fault' : people.isPending ? 'loading' : 'ready';
  const title = p?.title ?? actor.title ?? '';
  return (
    <RecordPage
      {...head}
      state={state}
      onRetry={() => void people.refetch()}
      sections={
        <Section name="Info">
          <Facts label="Info">
            {p?.email && <LabelValue label="Email">{p.email}</LabelValue>}
            <LabelValue label="Role" onEdit={admin || self ? () => setEditing('title') : undefined}>
              {title || 'None'}
            </LabelValue>
            <LabelValue label="Team" onEdit={admin ? () => setEditing('team') : undefined}>
              {team?.name ?? 'None'}
            </LabelValue>
            <LabelValue label="Reports to">{boss?.name ?? 'Nobody'}</LabelValue>
            {role && (
              <LabelValue label="Permissions" onEdit={admin && role.role !== 'owner' && !self ? () => setEditing('role') : undefined}>
                {role.role === 'owner' ? 'Owner' : role.role === 'admin' ? 'Admin' : 'Member'}
              </LabelValue>
            )}
            {reach?.you?.manage && (
              <LabelValue label="Access" onEdit={() => setEditing('access')}>
                {reachLine(reach)}
              </LabelValue>
            )}
            {p?.slack && <LabelValue label="Slack">{p.slack}</LabelValue>}
            {p?.github && <LabelValue label="GitHub">{p.github}</LabelValue>}
          </Facts>
          {admin && !self && (
            <Foot>
              <button className="verb" type="button" onClick={() => setEditing('password')}>
                Make a password
              </button>
              {role?.removable !== false && role?.role !== 'owner' && (
                <button className="verb" type="button" onClick={() => setEditing('remove')}>
                  Remove from {company}
                </button>
              )}
            </Foot>
          )}
          {reach?.you?.manage && <AccessDialog open={editing === 'access'} onOpenChange={close} kind="person" id={id} name={actor.name} now={reach} />}
          <PasswordDialog open={editing === 'password'} onOpenChange={close} person={id} name={actor.name} />
          <EditDialog
            open={editing === 'title'}
            onOpenChange={close}
            title="Role"
            label="Role"
            value={title}
            action="people.setTitle"
            input={(t) => ({ person: id, title: t })}
          />
          <PickDialog
            open={editing === 'team'}
            onOpenChange={close}
            title="Team"
            options={(groups.data ?? []).map((g) => ({ value: g.id, label: g.name }))}
            value={team?.id ?? ''}
            action="team.move"
            input={(t) => ({ kind: 'person', id, team: t })}
          />
          <PickDialog
            open={editing === 'role'}
            onOpenChange={close}
            title="Permissions"
            options={[
              { value: 'member', label: 'Member' },
              { value: 'admin', label: 'Admin' },
            ]}
            value={role?.role ?? 'member'}
            action="people.setRole"
            input={(r) => ({ person: id, role: r })}
          />
          <ConfirmDialog
            open={editing === 'remove'}
            onOpenChange={close}
            title={`Remove ${actor.name}?`}
            text="Ends their sign-in and API tokens. Their history stays."
            verb="Remove"
            onConfirm={() => void remove.run({ person: id })}
          />
        </Section>
      }
    />
  );
}
