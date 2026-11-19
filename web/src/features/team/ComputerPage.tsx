import { useQuery } from '@tanstack/react-query';
import { useState, type CSSProperties } from 'react';
import { useAction } from '@/actions';
import { useActors, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Sparkline } from '@/kit/Chart';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { Link } from '@/kit/Link';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { fmtDate } from '@/lib';
import { FiguresStrip, Record as RecordPage, Section, type PageState } from '@/pagekinds';
import { computerStatus, modelLine, whoHref } from './model';
import { ModelPicker } from './ModelPicker';
import { ConfirmDialog, Foot, MateRow } from './parts';
import { agentsQuery, computersQuery, modelsQuery, performanceQuery, type AgentRow, type Computer, type PerfPoint } from './queries';

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
  const computers = useQuery(computersQuery);
  const botId = agent?.id ?? actor.id.slice(actor.id.indexOf(':') + 1);
  const c = computers.data?.find((x) => x.id === agent?.machine?.runner_id) ?? computers.data?.find((x) => x.bots?.includes(botId));
  const restart = useAction('computers.restart');
  const [asking, setAsking] = useState(false);
  const title = c?.label ?? agent?.machine?.label ?? (agent?.cloud_machine?.shared ? 'Shared computer' : 'Computer');
  const state: PageState = computers.isError ? 'fault' : computers.isPending ? 'loading' : c ? 'ready' : 'empty';
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
      state={state}
      onRetry={() => void computers.refetch()}
      empty={{ title: agent?.cloud_machine?.shared ? 'It runs on the shared computer' : 'Not on a computer yet' }}
      sections={
        c && (
          <>
            {tab === 'screen' ? <Screen c={c} /> : <Info c={c} onRestart={() => setAsking(true)} />}
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
  const perf = useQuery({ ...performanceQuery(c.id), enabled: c.online });
  const [picking, setPicking] = useState<AgentRow | null>(null);
  const on = (roster.data ?? []).filter((r) => c.bots?.includes(r.id) || r.machine?.runner_id === c.id);
  const points = perf.data?.points ?? [];
  const last = points.at(-1);
  const series = (i: number) => points.map((p: PerfPoint) => p[i] ?? 0);
  return (
    <>
      <p className="status">
        {computerStatus(c, time).map((x) => (
          <span key={x}>{x}</span>
        ))}
      </p>
      {points.length > 1 && (
        <FiguresStrip
          label="Performance"
          figures={[
            { label: 'CPU', value: pct(last?.[1]), spark: <Sparkline points={series(1)} label="CPU over the last day" /> },
            { label: 'Memory', value: pct(last?.[3]), spark: <Sparkline points={series(3)} label="Memory over the last day" /> },
            { label: 'Load', value: last ? last[5].toFixed(1) : '', spark: <Sparkline points={series(5)} label="Load over the last day" /> },
          ]}
        />
      )}
      <Section name="Agents on this computer" count={on.length}>
        {on.map((r) => {
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
              <span className="row-pills">{r.status === 'paused' && <Pill flag>Paused</Pill>}</span>
              <button className="field is-select m-pick" type="button" aria-haspopup="dialog" title="Model" onClick={() => setPicking(r)}>
                <span>{modelLine(r, models?.models)}</span>
                <Icon name="chevdown" />
              </button>
            </div>
          );
        })}
      </Section>
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
      {picking && <ModelPicker agent={picking} open onOpenChange={(o) => !o && setPicking(null)} />}
    </>
  );
}

/** Who is on it now. The picture itself needs a live viewer this app does not have yet. */
function Screen({ c }: { c: Computer }) {
  const actors = useActors();
  if (!c.online) return <Empty icon="offline" title="It did not answer" />;
  const busy = actors.filter((a) => a.kind === 'agent' && a.working && c.bots?.includes(a.id.slice(a.id.indexOf(':') + 1)));
  return (
    <>
      {busy.length > 0 && (
        <Section name="On it now" count={busy.length}>
          {busy.map((a) => (
            <MateRow key={a.id} actor={a} />
          ))}
        </Section>
      )}
      <Empty icon="computer" title="No picture of the screen yet" />
    </>
  );
}
