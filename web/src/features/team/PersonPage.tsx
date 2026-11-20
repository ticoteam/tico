import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { configQuery, meQuery, useActor, useActors, type Actor } from '@/data';
import { goalFigure, goalLink, goalsTreeQuery } from '@/features/company/exports';
import { useFollow } from '@/live/follow';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Row, TaskRow } from '@/kit/Row';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { FiguresStrip, Record as RecordPage, Section } from '@/pagekinds';
import { bareId, slackTitle, startOfMonth, teamOf } from './model';
import { AccessDialog, GiveTaskDialog, PasswordDialog } from './dialogs';
import { AllTasks, ConfirmDialog, EditDialog, Foot, Fold, MateRow, OwnRow, OwnRows, PickLine, SecLink, waitsOn } from './parts';
import {
  accessQuery,
  agentsQuery,
  doneTasksQuery,
  groupsQuery,
  openTasksQuery,
  peopleQuery,
  reachLine,
  slackQuery,
  teammateAccessQuery,
  type SlackThread,
} from './queries';

export interface PersonPageProps {
  actor: Actor;
  settings?: boolean;
  /** `?tab=slack`: all their Slack threads. */
  slack?: boolean;
}

/** A person's own page: the same column as an agent's, beside the chat with them (mates.js personDesk). */
export function PersonPage({ actor, settings, slack }: PersonPageProps) {
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
        data-phone
      />
    ),
  };
  if (settings) return <PersonSettings actor={actor} id={id} head={head} />;
  return slack ? <PersonSlack id={id} head={head} /> : <PersonWidgets actor={actor} id={id} head={head} />;
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
  const { data: open } = useSuspenseQuery(openTasksQuery(actor.id));
  const goals = (useQuery(goalsTreeQuery).data?.goals ?? []).filter((g) => g.owner_id === actor.id);
  const done = useQuery(doneTasksQuery(actor.id, since));
  const [giving, setGiving] = useState(false);
  const tasks = open.tasks;
  const waits = tasks.filter((t) => waitsOn(t, me?.id));
  const rest = tasks.filter((t) => !waitsOn(t, me?.id));
  const reports = new Set([
    ...(roster.data ?? []).filter((r) => r.reports_to === id).map((r) => r.id),
    ...(people.data ?? []).filter((p) => p.reports_to === id).map((p) => p.id),
  ]);
  const manages = actors.filter((a) => reports.has(bareId(a.id)));
  const doneN = done.data?.tasks.length ?? 0;
  return (
    <RecordPage
      {...head}
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
                <AllTasks owner={actor.id} />
              </>
            }
          >
            <OwnRows tasks={rest} me={me?.id} label="Open tasks" />
            {doneN > 0 && (
              <Fold label="Done" text={`${doneN} this month`}>
                <OwnRows tasks={done.data?.tasks ?? []} me={me?.id} label="Done tasks" />
              </Fold>
            )}
            {giving && <GiveTaskDialog open onOpenChange={setGiving} person={id} name={actor.name} />}
          </Section>
          {manages.length > 0 && (
            <Section name="Manages" count={manages.length}>
              {manages.map((a) => (
                <MateRow key={a.id} actor={a} />
              ))}
            </Section>
          )}
          <SlackSection id={id} path={head.path} handle={people.data?.find((p) => p.id === id)?.slack} />
        </>
      }
    />
  );
}

const PERMISSIONS = [
  { value: 'member', label: 'Member' },
  { value: 'admin', label: 'Admin' },
];

function PersonSettings({ actor, id, head }: { actor: Actor; id: string; head: Head }) {
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);
  const { data: people } = useSuspenseQuery(peopleQuery);
  const groups = useQuery(groupsQuery);
  const { data: access } = useQuery(accessQuery);
  const remove = useAction('people.remove');
  const reach = useQuery(teammateAccessQuery('person', id)).data;
  const [editing, setEditing] = useState<'' | 'title' | 'github' | 'remove' | 'access' | 'password'>('');
  const close = (o: boolean) => !o && setEditing('');

  const p = people.find((x) => x.id === id);
  const role = access?.people.find((x) => x.id === id);
  const admin = access?.you === 'owner' || access?.you === 'admin';
  const self = me?.id === actor.id;
  const manage = !!reach?.you?.manage;
  const team = teamOf(groups.data ?? [], 'person', id);
  const boss = useActor(p?.reports_to);
  const company = config?.company_name || config?.app_name || 'the company';
  const title = p?.title ?? actor.title ?? '';
  return (
    <RecordPage
      {...head}
      sections={
        <Section name="Info">
          <Facts label="Info">
            {p?.email && <LabelValue label="Email">{p.email}</LabelValue>}
            <LabelValue label="Role" onEdit={admin || self ? () => setEditing('title') : undefined}>
              {title || 'None'}
            </LabelValue>
            <PickLine
              label="Team"
              options={(groups.data ?? []).map((g) => ({ value: g.id, label: g.name }))}
              value={team?.id ?? ''}
              shown="None"
              action={admin ? 'team.move' : undefined}
              input={(t) => ({ kind: 'person', id, team: t })}
            />
            <PickLine
              label="Reports to"
              options={people.filter((x) => x.id !== id).map((x) => ({ value: x.id, label: x.name }))}
              value={p?.reports_to ?? ''}
              shown={boss?.name ?? 'Nobody'}
              action={manage ? 'people.setReportsTo' : undefined}
              input={(b) => ({ person: id, boss: b })}
            />
            {role && (
              <PickLine
                label="Permissions"
                options={role.role === 'owner' ? [{ value: 'owner', label: 'Owner' }] : PERMISSIONS}
                value={role.role}
                shown="Member"
                action={admin && role.role !== 'owner' && !self ? 'people.setRole' : undefined}
                input={(r) => ({ person: id, role: r })}
              />
            )}
            {reach?.you?.manage && (
              <LabelValue label="Access" onEdit={() => setEditing('access')}>
                {reachLine(reach)}
              </LabelValue>
            )}
            {p?.slack && <LabelValue label="Slack">{p.slack}</LabelValue>}
            {(p?.github || manage) && (
              <LabelValue label="GitHub" onEdit={manage ? () => setEditing('github') : undefined}>
                {p?.github || 'None'}
              </LabelValue>
            )}
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
          {reach?.you?.manage && editing === 'access' && <AccessDialog open onOpenChange={close} kind="person" id={id} name={actor.name} now={reach} />}
          <PasswordDialog open={editing === 'password'} onOpenChange={close} person={id} name={actor.name} />
          {editing === 'title' && (
            <EditDialog open onOpenChange={close} title="Role" label="Role" value={title} action="people.setTitle" input={(t) => ({ person: id, title: t })} />
          )}
          {editing === 'github' && (
            <EditDialog
              open
              onOpenChange={close}
              title="GitHub"
              label="GitHub login"
              value={p?.github ?? ''}
              action="people.setGithub"
              input={(t) => ({ person: id, login: t })}
            />
          )}
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

/* ---------------------------------------------------------------- Slack */

const SLACK_ROWS = 3;

/** One DM with an agent through Tico's Slack app: its newest line, the agent, its age. Opens the thread. */
function SlackRow({ t }: { t: SlackThread }) {
  const agent = useActor(t.bot);
  return (
    <TaskRow
      title={slackTitle(t)}
      href={`/threads/${encodeURIComponent(t.conversation_id)}`}
      target={`thread:${t.conversation_id}`}
      pills={<span className="row-stage">{agent?.name ?? t.display_name ?? t.bot}</span>}
      at={t.last_message_at ?? new Date(0).toISOString()}
    />
  );
}

/**
 * Their newest Slack threads in the column (mates.js personDesk). Only they and the owner may read them: for anyone
 * else, and while it loads or if it fails, the column goes on without it.
 */
function SlackSection({ id, path, handle }: { id: string; path: string; handle?: string }) {
  const { data: threads } = useQuery(slackQuery(id));
  if (!threads) return null;
  if (!threads.length)
    return (
      <Section name="Slack">
        <div className="row line is-quiet">
          <span className="row-mark" />
          <span className="row-title">{handle ? 'No threads yet' : 'Not connected'}</span>
          {!handle && <SecLink href="/library/tools">Connect</SecLink>}
        </div>
      </Section>
    );
  return (
    <Section name="Slack" count={threads.length} actions={<SecLink href={`${path}?tab=slack`}>All threads</SecLink>}>
      {threads.slice(0, SLACK_ROWS).map((t) => (
        <SlackRow key={t.conversation_id} t={t} />
      ))}
    </Section>
  );
}

/** `?tab=slack`: every Slack thread, one step in from the column (mates.js slackPage). */
function PersonSlack({ id, head }: { id: string; head: Head }) {
  const { data: threads } = useSuspenseQuery(slackQuery(id));
  return (
    <RecordPage
      {...head}
      state={threads.length ? 'ready' : 'empty'}
      empty={{ title: 'Nothing from Slack yet' }}
      sections={
        <>
          <div className="ins-bar t-label">
            <Link className="ib ib-sm" href={head.path} aria-label="Back" title="Back">
              <Icon name="chevron" className="flip" />
            </Link>
            <b>Slack</b>
          </div>
          <Section name="Threads" count={threads.length}>
            {threads.map((t) => (
              <SlackRow key={t.conversation_id} t={t} />
            ))}
          </Section>
        </>
      }
    />
  );
}
