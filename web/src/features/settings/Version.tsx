import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { configQuery, type ReleaseOffer } from '@/data';
import { Button } from '@/kit/Button';
import { Line } from './parts';
import { checkForUpdate, startUpdate, updateProgress, updateStatusQuery } from './queries';

const release = (version: string) => version.replace(/^v(?=\d)/, '');

export function Version({ update, owner }: { update: ReleaseOffer; owner: boolean }) {
  const client = useQueryClient();
  const canCheck = owner && !update.managed;
  const statusQuery = useQuery({ ...updateStatusQuery, enabled: canCheck });
  const status = canCheck ? statusQuery.data : undefined;
  const [starting, setStarting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const progress = updateProgress(status);
  const busy = starting || !!progress;
  const target = release(status?.to ?? '');
  const updated = status?.state === 'healthy' && (!status.running || release(status.running) === target) ? target : '';
  const current = updated || release(update.current);
  const available = update.available && !!update.latest && release(update.latest) !== current;
  const manual = status?.configured === false;

  useEffect(() => {
    if (updated && updated !== release(update.current)) {
      void client.invalidateQueries({ queryKey: configQuery.queryKey });
    }
  }, [client, update.current, updated]);

  const start = async () => {
    setStarting(true);
    setFeedback('');
    try {
      await client.cancelQueries({ queryKey: updateStatusQuery.queryKey });
      client.setQueryData(updateStatusQuery.queryKey, await startUpdate(update.latest));
    } catch (error) {
      // The updater may have accepted the request before the server stopped answering.
      const { data } = await statusQuery.refetch();
      if (!updateProgress(data)) setFeedback(error instanceof Error ? error.message : 'Could not start the update.');
    } finally {
      setStarting(false);
    }
  };

  const check = async () => {
    setFeedback('');
    try {
      await checkForUpdate();
      await client.invalidateQueries({ queryKey: configQuery.queryKey });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'Could not check for updates.');
    }
  };

  const outcome =
    status?.state === 'rolled_back'
      ? 'Tico went back to the previous version.'
      : status?.state === 'failed'
        ? 'The update failed.'
        : updated
          ? `Updated to v${updated}.`
          : '';
  const message = progress || (starting ? 'Starting the update…' : feedback || outcome);

  return (
    <Line label="Version" target="setting:version">
      <span className="set-version">
        <span>Tico {/^\d/.test(current) ? `v${current}` : current}</span>
        {available && <span>v{release(update.latest)} is out</span>}
        {update.held && <span>{update.held}</span>}
        {update.managed && <span>Tico manages updates for this installation.</span>}
        {available && manual && <span>Update this installation manually.</span>}
        {message && <span role="status">{message}</span>}
        {canCheck && (
          <span className="set-version-actions">
            {(busy || (available && !update.held && !manual)) && (
              <Button busy={busy} onClick={start}>
                Update now
              </Button>
            )}
            <Button variant="quiet" disabled={busy} onClick={check}>
              Check for updates
            </Button>
          </span>
        )}
      </span>
    </Line>
  );
}
