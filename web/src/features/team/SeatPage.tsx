import { useQuery } from '@tanstack/react-query';
import { useAction } from '@/actions';
import { ApiError } from '@/api/client';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { MenuItem } from '@/kit/Menu';
import { TaskRow } from '@/kit/Row';
import { fmtWhen } from '@/lib';
import { PageMore, Record as RecordPage, Section } from '@/pagekinds';
import { connectStep, seatBoss, seatHref } from './model';
import { taskHref } from './parts';
import { groupsQuery, isOpenRequest, seatAgentQuery } from './queries';

const STEP: Record<string, string> = { requested: 'Requested', building: 'Building', ready: 'Ready' };

/**
 * A requested seat's Connecting page (s736; the old app's cloud-agents.ts seatPage): the open seat's page as the mock
 * draws it (a dashed face, its facts, what waits for it), with where the request stands and Cancel request under ⋯.
 */
export function SeatPage({ seat: id }: { seat: string }) {
  const path = seatHref(id);
  const read = useQuery(seatAgentQuery(id));
  const groups = useQuery(groupsQuery).data;
  const cancel = useAction('agents.cancelCloudRequest');
  const detail = read.data;
  const boss = useActor(seatBoss(detail?.seat.reports_to));
  const lead = <Avatar kind="agent" name={detail?.agent.name ?? ''} initials="" size={36} className="is-seat" />;
  const head = { path, className: 'rec seat-page', size: 'large' as const, lead };
  if (!detail) {
    const gone = read.error instanceof ApiError && read.error.status === 404;
    return (
      <RecordPage
        {...head}
        title="Cloud agent"
        state={gone ? 'empty' : read.isError ? 'fault' : 'loading'}
        empty={{ title: 'No such request' }}
        onRetry={() => void read.refetch()}
      />
    );
  }
  const { seat, agent, steps, tasks } = detail;
  const at = connectStep(steps, seat.request_state);
  const ready = seat.request_state === 'ready';
  const team = groups?.find((g) => g.id === seat.team);
  return (
    <RecordPage
      {...head}
      title={seat.title}
      more={
        isOpenRequest(seat) && seat.request_state !== 'canceling' ? (
          <PageMore target={`seat:${seat.id}`}>
            <MenuItem label="Cancel request" icon="close" onSelect={() => void cancel.run({ seat: seat.id })} />
          </PageMore>
        ) : undefined
      }
      facts={
        <Facts label="Request">
          <LabelValue label="Status">
            <ol className="seat-steps" aria-label="Connecting">
              {steps.map((step, i) => {
                const done = ready || i < at;
                const now = !ready && i === at;
                return (
                  <li key={step} className={done ? 'is-done' : now ? 'is-now' : undefined} aria-current={now ? 'step' : undefined}>
                    {done ? <Icon name="check" /> : <i aria-hidden="true" />}
                    {STEP[step] ?? step}
                  </li>
                );
              })}
            </ol>
          </LabelValue>
          {!ready && <LabelValue label="Ready in">12 to 24 hours</LabelValue>}
          {seat.title !== agent.name && <LabelValue label="Agent">{agent.name}</LabelValue>}
          {agent.site && (
            <LabelValue label="Site">
              <Link href={`https://${agent.site}`} target="_blank" rel="noopener noreferrer">
                {agent.site}
              </Link>
            </LabelValue>
          )}
          {team && <LabelValue label="Team">{team.name}</LabelValue>}
          {boss && (
            <LabelValue label="Reports to">
              <span className="seat-who">
                <Avatar kind={boss.kind} name={boss.name} hue={boss.hue} size={22} />
                {boss.name}
              </span>
            </LabelValue>
          )}
          {seat.created_at && <LabelValue label="Requested">{fmtWhen(seat.created_at)}</LabelValue>}
        </Facts>
      }
      sections={
        tasks.length > 0 && (
          <Section name={`Waiting for ${agent.name}`} count={tasks.length}>
            {tasks.map((t) => (
              <TaskRow
                key={t.id}
                title={t.title}
                href={taskHref({ id: t.id, pipeline_id: null, number: null })}
                target={`task:${t.id}`}
                avatar={<Avatar kind="agent" name={agent.name} initials="" size={22} className="is-seat" decorative />}
                at={seat.created_at ?? new Date(0).toISOString()}
              />
            ))}
          </Section>
        )
      }
    />
  );
}
