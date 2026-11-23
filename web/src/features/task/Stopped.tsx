import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { toast } from '@/kit/Toast';
import { AskCard, Docked, useAnswering } from './AskCard';
import type { TaskData } from './queries';

/* A stopped agent's notice (backend/bot_stops.py): Tico stopped an agent whose runs kept failing, or that ran over its
   budget, and this task asks the person to get it going. Release is in place, as the server would take it (the task
   read names the agent only to someone who may release it); its queued work starts and the server closes the task. */

export function StoppedDecision({ t, dock = false }: { t: TaskData; dock?: boolean }) {
  const release = useAction('task.releaseAgent');
  const { error, setError, refused, moveOn } = useAnswering();
  const agent = t.stopped_agent;
  if (!agent) return null;

  const go = async () => {
    setError('');
    const res = await release.run({ task: t.id, agent: agent.id });
    if (!res.ok) return refused(res);
    toast({ text: 'Released', name: agent.name });
    moveOn();
  };

  return (
    <Docked dock={dock}>
      <AskCard
        name={`${agent.name} is stopped`}
        who={`bot:${agent.id}`}
        target={`task-release:${t.id}`}
        actions={
          <Button variant="primary" busy={release.pending} onClick={go}>
            Release
          </Button>
        }
        error={error}
      >
        <p className="an-line">Its queued work starts again. Release it once the cause is fixed.</p>
      </AskCard>
    </Docked>
  );
}
