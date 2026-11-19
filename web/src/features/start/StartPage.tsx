import { useQuery } from '@tanstack/react-query';
import { run } from '@/actions';
import { configQuery, directoryQuery, meQuery, useActor, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { TaskRow, WhoRow } from '@/kit/Row';
import { List, Section } from '@/pagekinds';
import { PartFault } from './Cards';
import { needsQuery, needsSetup, todayQuery, type Need } from './queries';
import './start.css';

/* Where a person lands: what waits on them, what their agents are doing, today, and Tico. Every row opens its page.
   No mock draws this page; it is the List kind with one section each. */

const click = { via: 'click' } as const;

function Face({ id }: { id: string }) {
  const who = useActor(id);
  return who ? <Avatar kind={who.kind} name={who.name} hue={who.hue} live={who.working} /> : null;
}

const agentLine = (a: Actor) => (a.status ? `${a.name}: ${a.status}` : a.name);

export function StartPage() {
  const needs = useQuery(needsQuery);
  const today = useQuery(todayQuery());
  const dir = useQuery(directoryQuery);
  const { data: me } = useQuery(meQuery);
  const { data: config } = useQuery(configQuery);

  const agents = (dir.data?.actors ?? [])
    .filter((a) => a.kind === 'agent' && (a.status || a.working))
    .sort((a, b) => Number(!!b.working) - Number(!!a.working));
  const items: Need[] = needs.data?.items ?? [];
  const events = today.data ?? [];
  const nothing = !items.length && !agents.length && !events.length && !today.isError;

  const actions = (
    <>
      {me?.role === 'owner' && needsSetup(config) && <Button onClick={() => void run('setup.open', {}, click)}>Finish setup</Button>}
      <Button variant="primary" onClick={() => void run('shell.fold', { sheet: 'tico', folded: false }, click)}>
        Ask Tico
      </Button>
    </>
  );

  return (
    <List
      title="Start"
      path="/start"
      actions={actions}
      state={needs.isPending ? 'loading' : needs.isError ? 'fault' : nothing ? 'empty' : 'ready'}
      onRetry={() => void needs.refetch()}
      empty={{ title: 'Nothing needs you' }}
      sections={
        <>
          {items.length > 0 && (
            <Section name="Needs you" count={needs.data?.count} target="section:needs">
              {items.map((n) => (
                <TaskRow
                  key={n.id}
                  title={n.title}
                  href={n.href}
                  at={n.at}
                  avatar={n.from ? <Face id={n.from} /> : undefined}
                  target={`need:${n.id}`}
                />
              ))}
            </Section>
          )}
          {agents.length > 0 && (
            <Section name="Agents" count={agents.length} target="section:agents">
              {agents.map((a) => (
                <WhoRow
                  key={a.id}
                  title={agentLine(a)}
                  href={`/team/${encodeURIComponent(a.slug ?? a.id)}`}
                  avatar={<Avatar kind="agent" name={a.name} hue={a.hue} live={a.working} />}
                  target={`agent:${a.slug ?? a.id}`}
                />
              ))}
            </Section>
          )}
          {(events.length > 0 || today.isError) && (
            <Section name="Today" count={events.length || undefined} target="section:today">
              {today.isError ? (
                <PartFault name="Today" onRetry={() => void today.refetch()} />
              ) : (
                events.map((o) => <TaskRow key={o.occurrence_id} title={o.title} href="/calendar" at={o.start} target={`event:${o.occurrence_id}`} />)
              )}
            </Section>
          )}
        </>
      }
    />
  );
}
