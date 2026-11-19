import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState, type CSSProperties } from 'react';
import { useAction } from '@/actions';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { fmtDate } from '@/lib';
import { Record as RecordPage, Section } from '@/pagekinds';
import { computerStatus, modelLine, whoHref } from './model';
import { ModelPicker } from './ModelPicker';
import { ConfirmDialog, Foot } from './parts';
import { Screen, ScreenTools, type ScreenMode } from './Screen';
import { SignIns } from './SignIns';
import { agentsQuery, computersQuery, modelsQuery, performanceQuery, type AgentRow, type Computer } from './queries';

const time = (d: Date) => fmtDate(d);
const pct = (n: number | undefined) => (n === undefined || !Number.isFinite(n) ? '' : `${Math.round(n)}%`);

export interface ComputerPageProps {
  /** The agent whose computer this is; its page is the crumb back. */
  agent: AgentRow | undefined;
  actor: Actor;
  /** The agent's address: the tabs are `?tab=computer` and `?tab=screen` on it. */
  base: string;
  tab: 'computer' | 'screen';
}

/** The computer an agent runs on (mates.js compPage): Info and Screen, its agents, how it is doing, Restart. */
export function ComputerPage({ agent, actor, base, tab }: ComputerPageProps) {
  const { data: computers } = useSuspenseQuery(computersQuery);
  const botId = agent?.id ?? actor.id.slice(actor.id.indexOf(':') + 1);
  const c = computers.find((x) => x.id === agent?.machine?.runner_id) ?? computers.find((x) => x.bots?.includes(botId));
  const restart = useAction('computers.restart');
  const [asking, setAsking] = useState(false);
  const [mode, setMode] = useState<ScreenMode>('view');
  const title = c?.label ?? agent?.machine?.label ?? (agent?.cloud_machine?.shared ? 'Shared computer' : 'Computer');
  return (
    <RecordPage
      title={title}
      path={`${base}?tab=${tab}`}
      crumb={[{ label: actor.name, to: base }]}
      className="rec"
      lead={
        <span className="comp-av" style={{ '--a': '36px' } as CSSProperties} aria-hidden="true">
          <Icon name="computer" />
        </span>
      }
      after={c && !c.online && <Pill flag>Offline</Pill>}
      actions={
        c &&
        !c.online &&
        c.may_manage !== false && (
          <Button variant="primary" onClick={() => setAsking(true)}>
            Restart
          </Button>
        )
      }
      tabs={[
        { label: 'Info', to: base, search: { tab: 'computer' } },
        { label: 'Screen', to: base, search: { tab: 'screen' } },
      ]}
      tabsAfter={tab === 'screen' && c && <ScreenTools c={c} mode={mode} onMode={setMode} />}
      state={c ? 'ready' : 'empty'}
      empty={{ title: agent?.cloud_machine?.shared ? 'It runs on the shared computer' : 'Not on a computer yet' }}
      sections={
        c && (
          <>
            {tab === 'screen' ? <Screen c={c} mode={mode} /> : <Info c={c} onRestart={() => setAsking(true)} />}
            <ConfirmDialog
              open={asking}
              onOpenChange={setAsking}
              title={`Restart ${c.label}?`}
              text="The agents on it stop their turns and start again in about a minute."
              verb="Restart"
              onConfirm={() => void restart.run({ id: c.id })}
            />
          </>
        )
      }
    />
  );
}

function Info({ c, onRestart }: { c: Computer; onRestart: () => void }) {
  const roster = useQuery(agentsQuery);
  const actors = useActors();
  const { data: models } = useQuery(modelsQuery);
  const last = useQuery({ ...performanceQuery(c.id), enabled: c.online }).data?.points?.at(-1);
  const [picking, setPicking] = useState<AgentRow[] | null>(null);
  const on = (roster.data ?? []).filter((r) => c.bots?.includes(r.id) || r.machine?.runner_id === c.id);
  const lines = on.map((r) => modelLine(r, models?.models));
  const status = [...computerStatus(c, time), ...(c.online && last ? [`CPU ${pct(last[1])}`, `Memory ${pct(last[3])}`] : [])];
  const pick = (words: string, label: string, rows: AgentRow[]) => (
    <button className="field is-select m-pick" type="button" aria-haspopup="dialog" aria-label={label} title={words} onClick={() => setPicking(rows)}>
      <span>{words}</span>
      <Icon name="chevdown" />
    </button>
  );
  return (
    <>
      <p className="status">
        {status.map((x) => (
          <span key={x}>{x}</span>
        ))}
      </p>
      <Section name="Agents on this computer" count={on.length}>
        {on.length > 1 && (
          <div className="row bm">
            <span className="row-mark" />
            <span className="comp-all" aria-hidden="true">
              <Icon name="rules" />
            </span>
            <span className="mate-name">All agents</span>
            <span className="mate-now" />
            <span className="row-pills" />
            {pick(new Set(lines).size === 1 ? lines[0]! : 'Mixed', 'Model for all agents', on)}
          </div>
        )}
        {on.map((r, i) => {
          const a = actors.find((x) => x.slug === r.slug);
          const name = a?.name ?? r.display_name ?? r.slug;
          return (
            <div key={r.slug} className="row bm" title={name}>
              <span className="row-mark" />
              <Avatar kind="agent" name={name} hue={a?.hue} live={a?.working} size={22} />
              <Link className="mate-name" href={a ? whoHref(a) : `/team/${encodeURIComponent(r.slug)}`}>
                {name}
              </Link>
              <span className="mate-now">{a?.status ?? ''}</span>
              <span className="row-pills">{(!c.online || r.status === 'paused') && <Pill flag>Stopped</Pill>}</span>
              {pick(lines[i]!, `${name}'s model`, [r])}
            </div>
          );
        })}
      </Section>
      <SignIns c={c} />
      <Section name="Computer">
        <Facts label="Computer">
          <LabelValue label="Kind">{c.cloud_machine ? 'Cloud machine' : 'Own computer'}</LabelValue>
          {c.cloud_machine && <LabelValue label="Shared">{c.cloud_machine.shared ? 'Yes' : 'No'}</LabelValue>}
          {c.cloud_machine?.size && <LabelValue label="Size">{c.cloud_machine.size}</LabelValue>}
          {c.platform && <LabelValue label="Platform">{c.platform}</LabelValue>}
          {c.version && <LabelValue label="Version">{c.version}</LabelValue>}
        </Facts>
        {c.online && c.may_manage !== false && (
          <Foot>
            <button className="verb" type="button" onClick={onRestart}>
              Restart
            </button>
          </Foot>
        )}
      </Section>
      {picking?.[0] && <ModelPicker agent={picking[0]} also={picking.slice(1)} open onOpenChange={(o) => !o && setPicking(null)} />}
    </>
  );
}
