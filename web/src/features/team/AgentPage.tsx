import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useAction } from '@/actions';
import { meQuery, useActors, type Actor } from '@/data';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { Avatar } from '@/kit/Avatar';
import { Icon, type IconName } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Link } from '@/kit/Link';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { fmtDate, fmtMoney } from '@/lib';
import { FiguresStrip, Record as RecordPage, Section, type PageState } from '@/pagekinds';
import type { Tab } from './actions';
import { ComputerPage } from './ComputerPage';
import { bareId, historyOf, modelLine, runFigures, startOfWeek, teamOf, type HistLine } from './model';
import { ModelPicker } from './ModelPicker';
import { CopiesSection, CopyFacts, CopyPick } from './Copies';
import { CredentialDialog, WorksForDialog } from './dialogs';
import { ConfirmDialog, EditDialog, Foot, Fold, OwnRow, PickDialog, SecLink, waitsOn } from './parts';
import {
  agentQuery,
  agentsQuery,
  changesQuery,
  computersQuery,
  doneTasksQuery,
  groupsQuery,
  instructionsQuery,
  latestUpdateQuery,
  learnedQuery,
  modelsQuery,
  openTasksQuery,
  pauseQuery,
  rulesQuery,
  runsQuery,
  type AgentRow,
  type RuleLine,
} from './queries';

export interface AgentPageProps {
  actor: Actor;
  tab?: Tab;
}

/**
 * An agent's own page: one column beside its chat (mates.js col). Its widgets; the gear turns it to Settings; the
 * instructions and its computer are one step in from there.
 */
export function AgentPage({ actor, tab }: AgentPageProps) {
  const slug = actor.slug ?? actor.id.slice(4);
  const path = `/team/${encodeURIComponent(slug)}`;
  useFollow({ party: actor.id });
  const roster = useQuery(agentsQuery);
  const row = roster.data?.find((r) => r.slug === slug);
  const { data: models } = useQuery(modelsQuery);
  const { data: paused } = useQuery(pauseQuery(slug));

  if (tab === 'computer' || tab === 'screen') return <ComputerPage agent={row} actor={actor} base={path} tab={tab} />;

  const settings = tab === 'settings' || tab === 'instructions' || tab === 'model';
  const sub = row ? modelLine(row, models?.models) : '';
  return (
    <AgentHead actor={actor} slug={slug} path={path} row={row} sub={sub} settings={settings} paused={paused?.state === 'paused'}>
      {(head) =>
        tab === 'instructions' ? (
          <Instructions slug={slug} path={path} head={head} />
        ) : settings ? (
          <AgentSettings slug={slug} actor={actor} row={row} path={path} head={head} pick={tab === 'model'} />
        ) : (
          <Widgets slug={slug} actor={actor} head={head} />
        )
      }
    </AgentHead>
  );
}

interface Head {
  title: string;
  path: string;
  lead: ReactNode;
  after: ReactNode;
  actions: ReactNode;
  more: ReactNode;
  className: string;
  size: 'large';
  onRename?: (name: string) => void;
}

/** The column's head: face, name, its model line, then the gear and More. */
function AgentHead({
  actor,
  slug,
  path,
  row,
  sub,
  settings,
  paused,
  children,
}: {
  actor: Actor;
  slug: string;
  path: string;
  row: AgentRow | undefined;
  sub: string;
  settings: boolean;
  paused: boolean;
  children: (head: Head) => ReactNode;
}) {
  const open = useAction('team.open');
  const pause = useAction('agents.pause');
  const resume = useAction('agents.resume');
  const archive = useAction('agents.archive');
  const rename = useAction('agents.rename');
  const [archiving, setArchiving] = useState(false);
  const head: Head = {
    title: actor.name,
    path,
    className: 'rec is-col',
    size: 'large',
    onRename: row?.can_manage === false ? undefined : (name) => void rename.run({ slug, name }),
    lead: <Avatar kind="agent" name={actor.name} hue={actor.hue} live={actor.working} size={36} />,
    after: (
      <>
        <CopyPick slug={slug} botId={row?.id ?? bareId(actor.id)} />
        {paused && <Pill flag>Paused</Pill>}
        {sub && <p className="col-sub">{sub}</p>}
      </>
    ),
    actions: (
      <IconButton
        icon="gear"
        label="Settings"
        on={settings}
        aria-pressed={settings}
        onClick={() => void open.run({ who: slug, ...(settings ? {} : { tab: 'settings' }) })}
      />
    ),
    more: (
      <>
        <Menu trigger={<IconButton icon="more" label="More" />}>
          {paused ? (
            <MenuItem label="Resume" icon="play" onSelect={() => void resume.run({ slug })} />
          ) : (
            <MenuItem label="Pause" icon="pause" onSelect={() => void pause.run({ slug })} />
          )}
          <MenuRule />
          <MenuItem label={`Archive ${actor.name}`} icon="archive" disabled={row?.can_manage === false} onSelect={() => setArchiving(true)} />
        </Menu>
        <ConfirmDialog
          open={archiving}
          onOpenChange={setArchiving}
          title={`Archive ${actor.name}?`}
          text="It stops taking work. Its tasks and history stay."
          verb="Archive"
          onConfirm={() => void archive.run({ slug })}
        />
      </>
    ),
  };
  return children(head);
}

/* ---------------------------------------------------------------- widgets */

function Widgets({ slug, actor, head }: { slug: string; actor: Actor; head: Head }) {
  const { data: me } = useQuery(meQuery);
  const actors = useActors();
  const [since] = useState(() => startOfWeek(new Date()).toISOString());
  const open = useQuery(openTasksQuery(actor.id));
  const done = useQuery(doneTasksQuery(actor.id, since));
  const runs = useQuery(runsQuery(slug));
  const update = useQuery(latestUpdateQuery(slug));
  const detail = useQuery(agentQuery(slug));

  const tasks = open.data?.tasks ?? [];
  const nowId = detail.data?.status?.task_id;
  const working = actor.working ? tasks.filter((t) => t.id === nowId) : [];
  const mine = tasks.filter((t) => !working.includes(t));
  const waits = mine.filter((t) => waitsOn(t, me?.id));
  // Stuck on someone else (ui bot-tasks blocked): who it waits on is the row's note.
  const waiting = mine.filter((t) => !waitsOn(t, me?.id) && t.turn?.state === 'person' && !!t.turn.who);
  const rest = mine.filter((t) => !waits.includes(t) && !waiting.includes(t));
  const nameOf = (id: string) => actors.find((a) => a.id === id)?.name ?? 'someone';
  const { turns, spend } = runFigures(runs.data ?? [], new Date());
  const lines = (update.data?.body ?? '')
    .split('\n')
    .map((l) => l.replace(/^\s*[-*•]\s+/, '').trim())
    .filter(Boolean);
  const doneN = done.data?.tasks.length ?? 0;

  const state: PageState = open.isError ? 'fault' : open.isPending ? 'loading' : !tasks.length && !lines.length && !doneN && !actor.working ? 'empty' : 'ready';
  return (
    <RecordPage
      {...head}
      state={state}
      onRetry={() => void open.refetch()}
      empty={{ title: 'Nothing yet' }}
      figures={
        <FiguresStrip
          label={`${actor.name} at a glance`}
          figures={[
            { label: 'Open', value: tasks.length },
            { label: 'Done this week', value: doneN },
            { label: 'Turns today', value: turns },
            { label: 'Spend this month', value: fmtMoney(spend, { cents: true }) },
          ]}
        />
      }
      sections={
        <>
          {actor.working && (
            <Section name="Working" count={working.length || undefined}>
              {working.map((t) => (
                <OwnRow key={t.id} task={t} me={me?.id} />
              ))}
              {actor.status && (
                <p className="now-step">
                  {actor.status}
                  <span className="typing">
                    <i />
                    <i />
                    <i />
                  </span>
                </p>
              )}
            </Section>
          )}
          {waits.length > 0 && (
            <Section name="Waits on you" count={waits.length}>
              {waits.map((t) => (
                <OwnRow key={t.id} task={t} me={me?.id} />
              ))}
            </Section>
          )}
          {waiting.length > 0 && (
            <Section name="Waiting" count={waiting.length}>
              {waiting.map((t) => (
                <OwnRow key={t.id} task={t} me={me?.id} sub={`On ${nameOf(t.turn?.who ?? '')}`} />
              ))}
            </Section>
          )}
          {lines.length > 0 && update.data && (
            <Section name="Latest update" actions={<Ago at={update.data.updated_at ?? update.data.created_at} className="row-age" />}>
              <ul className="upd">
                {lines.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </Section>
          )}
          <Section name="Tasks" actions={<SecLink href="/tasks">All tasks</SecLink>}>
            {rest.map((t) => (
              <OwnRow key={t.id} task={t} me={me?.id} />
            ))}
            {doneN > 0 && <Fold label="Done" text={`${doneN} this week`} />}
          </Section>
          <CopiesSection slug={slug} botId={bareId(actor.id)} actors={actors} />
        </>
      }
    />
  );
}

/* ---------------------------------------------------------------- settings */

const HOW: Record<RuleLine['how'], [IconName, string]> = {
  set: ['gear', 'From its settings'],
  own: ['rules', 'Its own'],
  outside: ['link', 'From outside'],
};
const KIND: Record<HistLine['kind'], [IconName, string]> = { run: ['play', 'Run'], set: ['gear', 'Setting'], learn: ['spark', 'Learned'] };
const HISTORY_ROWS = 40;

/** `pick`: opened at `?tab=model` (the Settings page's link), the model picker over Settings; closing it leaves Settings. */
function AgentSettings({ slug, actor, row, path, head, pick }: { slug: string; actor: Actor; row: AgentRow | undefined; path: string; head: Head; pick: boolean }) {
  const open = useAction('team.open');
  const actors = useActors();
  const roster = useQuery(agentsQuery);
  const groups = useQuery(groupsQuery);
  const computers = useQuery(computersQuery);
  const detail = useQuery(agentQuery(slug));
  const { data: models } = useQuery(modelsQuery);
  const instructions = useQuery(instructionsQuery(slug));
  const rules = useQuery(rulesQuery(slug));
  const runs = useQuery(runsQuery(slug));
  const learned = useQuery(learnedQuery(slug));
  const changes = useQuery(changesQuery(slug));
  const [editing, setEditing] = useState<'' | 'role' | 'team' | 'reports' | 'model' | 'computer' | 'users' | 'credential'>('');
  const close = (o: boolean) => !o && setEditing('');

  const botId = bareId(actor.id);
  const team = teamOf(groups.data ?? [], 'agent', botId);
  const reportsTo = row?.reports_to ?? detail.data?.reports_to ?? '';
  const boss = actors.find((a) => bareId(a.id) === reportsTo);
  const computer = computers.data?.find((c) => c.id === row?.machine?.runner_id) ?? computers.data?.find((c) => c.bots?.includes(botId));
  const role = row?.description ?? detail.data?.description ?? '';
  const manage = row?.can_manage !== false;
  const users = row?.users ?? [];
  const history = historyOf(runs.data ?? [], learned.data ?? [], changes.data ?? []);
  const lead = (instructions.data?.content ?? '')
    .split(/\n\s*\n/)
    .map((p) => p.replace(/^#+\s*/, '').trim())
    .filter(Boolean)
    .slice(0, 2);

  const state: PageState = roster.isError ? 'fault' : roster.isPending ? 'loading' : 'ready';
  return (
    <RecordPage
      {...head}
      state={state}
      onRetry={() => void roster.refetch()}
      sections={
        <>
          <Section name="Info">
            <Facts label="Info">
              <CopyFacts slug={slug} botId={botId} manage={manage} />
              <LabelValue label="Role" onEdit={() => setEditing('role')}>
                {role || 'None'}
              </LabelValue>
              <LabelValue label="Team" onEdit={() => setEditing('team')}>
                {team?.name ?? 'None'}
              </LabelValue>
              <LabelValue label="Reports to" onEdit={() => setEditing('reports')}>
                {boss?.name ?? detail.data?.reports_to_name ?? 'Nobody'}
              </LabelValue>
              <LabelValue label="Works for" onEdit={manage ? () => setEditing('users') : undefined}>
                {users.length ? users.map((u) => u.name ?? u.id).join(', ') : 'Nobody'}
              </LabelValue>
              <LabelValue label="Computer" onEdit={manage && computers.data?.length ? () => setEditing('computer') : undefined}>
                <Link className="fact-link" href={`${path}?tab=computer`}>
                  {computer?.label ?? row?.machine?.label ?? (row?.cloud_machine?.shared ? 'Shared' : 'None')}
                  <Icon name="arrow" />
                </Link>
              </LabelValue>
              <LabelValue label="Model" onEdit={() => setEditing('model')}>
                {row ? modelLine(row, models?.models) : ''}
              </LabelValue>
            </Facts>
            {instructions.data && (
              <div className="ins">
                <div className="ins-head">
                  <span>Instructions</span>
                </div>
                <Link className="ins-text" href={`${path}?tab=instructions`} title="Open the instructions">
                  {lead.map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </Link>
                {instructions.data.updated_at && <p className="ins-by">{fmtDate(new Date(instructions.data.updated_at))}</p>}
              </div>
            )}
            {row?.agent && manage && (
              <Foot>
                <button className="verb" type="button" onClick={() => setEditing('credential')}>
                  New credential
                </button>
              </Foot>
            )}
          </Section>
          <Section name="Rules" count={rules.data?.length}>
            {(rules.data ?? []).map((r) => (
              <div key={r.key} className={r.off ? 'row rule is-off' : 'row rule'} title={r.text}>
                <span className="rule-how" title={HOW[r.how][1]}>
                  <Icon name={HOW[r.how][0]} />
                </span>
                <span className="row-title">
                  {r.text}
                  {(r.from || r.due) && <small className="rule-from">{[r.from && `From ${r.from}`, r.due && `Due ${r.due}`].filter(Boolean).join(', ')}</small>}
                </span>
                <span className="rule-marks">
                  {r.off && (
                    <span title="Paused">
                      <Icon name="pause" />
                    </span>
                  )}
                </span>
                <span />
              </div>
            ))}
          </Section>
          <Section name="History">
            {history.slice(0, HISTORY_ROWS).map((h) => (
              <HistRow key={h.key} h={h} />
            ))}
            {history.length > HISTORY_ROWS && <Fold label="Earlier" text={`${history.length - HISTORY_ROWS} more`} />}
          </Section>
          <EditDialog
            open={editing === 'role'}
            onOpenChange={close}
            title="Role"
            label="Role"
            value={role}
            area
            action="agents.setRole"
            input={(r) => ({ slug, role: r })}
          />
          <PickDialog
            open={editing === 'team'}
            onOpenChange={close}
            title="Team"
            options={(groups.data ?? []).map((g) => ({ value: g.id, label: g.name }))}
            value={team?.id ?? ''}
            action="team.move"
            input={(t) => ({ kind: 'agent', id: botId, team: t })}
          />
          <PickDialog
            open={editing === 'reports'}
            onOpenChange={close}
            title="Reports to"
            options={actors
              .filter((a) => a.id !== actor.id)
              .map((a) => ({ value: bareId(a.id), label: a.name }))}
            value={reportsTo}
            action="agents.setReportsTo"
            input={(to) => ({ slug, to })}
          />
          <PickDialog
            open={editing === 'computer'}
            onOpenChange={close}
            title="Computer"
            options={(computers.data ?? []).map((c) => ({ value: c.id, label: c.label }))}
            value={row?.machine?.runner_id ?? computer?.id ?? ''}
            action="agents.move"
            input={(c) => ({ slug, computer: c })}
          />
          <WorksForDialog open={editing === 'users'} onOpenChange={close} slug={slug} name={actor.name} now={users.map((u) => u.id)} />
          {row?.agent && <CredentialDialog open={editing === 'credential'} onOpenChange={close} slug={slug} name={actor.name} row={row} />}
          {row && (
            <ModelPicker
              agent={row}
              open={editing === 'model' || pick}
              onOpenChange={(o) => {
                if (o) return;
                if (pick) void open.run({ who: slug, tab: 'settings' });
                else setEditing('');
              }}
            />
          )}
        </>
      }
    />
  );
}

function HistRow({ h }: { h: HistLine }) {
  const [icon, label] = h.bad ? (['alert', 'Stopped'] as const) : KIND[h.kind];
  const cells = (
    <>
      <span className="hist-kind" title={label}>
        <Icon name={icon} />
      </span>
      <span className="row-title">
        {h.title}
        {h.live && (
          <span className="typing">
            <i />
            <i />
            <i />
          </span>
        )}
      </span>
      <span className="row-pills">{h.bad ? <Pill flag>{h.bad}</Pill> : h.kind === 'learn' ? <Pill>Learned</Pill> : null}</span>
      <span className="hist-meta">
        <span className="num">{h.dur ?? ''}</span>
        <span className="num">{h.cost ? fmtMoney(h.cost, { cents: true }) : ''}</span>
        <Ago at={h.at} className="row-age" />
      </span>
    </>
  );
  const cls = ['row hist', h.bad && 'is-bad', h.kind === 'learn' && 'is-learn'].filter(Boolean).join(' ');
  return h.task ? (
    <Link className={cls} href={`/task/${encodeURIComponent(h.task)}`} title={h.title}>
      {cells}
    </Link>
  ) : (
    <div className={cls} title={h.title}>
      {cells}
    </div>
  );
}

/* ---------------------------------------------------------------- the instructions */

/** The whole instructions, read at a measure (mates.js insPage). Headings, lists and paragraphs; nothing else. */
function Instructions({ slug, path, head }: { slug: string; path: string; head: Head }) {
  const q = useQuery(instructionsQuery(slug));
  const blocks = (q.data?.content ?? '').split(/\n\s*\n/).filter((b) => b.trim());
  return (
    <RecordPage
      {...head}
      state={q.isError ? 'fault' : q.isPending ? 'loading' : blocks.length ? 'ready' : 'empty'}
      onRetry={() => void q.refetch()}
      empty={{ title: 'No instructions yet' }}
      sections={
        <>
          <div className="ins-bar t-label">
            <Link className="ib ib-sm" href={`${path}?tab=settings`} aria-label="Back to Settings" title="Back to Settings">
              <Icon name="chevron" className="flip" />
            </Link>
            <b>Instructions</b>
          </div>
          <article className="read">
            {blocks.map((b, i) => {
              const t = b.trim();
              if (/^#+\s/.test(t)) return <h2 key={i} className="t-head">{t.replace(/^#+\s*/, '')}</h2>;
              const items = t.split('\n');
              if (items.every((l) => /^\s*[-*]\s/.test(l)))
                return (
                  <ul key={i}>
                    {items.map((l, j) => (
                      <li key={j} className="t-read">{l.replace(/^\s*[-*]\s+/, '')}</li>
                    ))}
                  </ul>
                );
              return <p key={i} className="t-read">{t}</p>;
            })}
          </article>
        </>
      }
    />
  );
}
