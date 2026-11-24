import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { configQuery, isNetworkError, type ReleaseOffer } from '@/data';
import { Button } from '@/kit/Button';
import { Line } from './parts';
import { checkForUpdate, startUpdate, updateProgress, updateStatusQuery } from './queries';

const release = (version: string) => version.replace(/^v(?=\d)/, '');
const STATUS_INTERVAL = 3_000;
const RECOVERY_LIMIT = 5 * 60_000;
const TERMINAL = new Set(['healthy', 'rolled_back', 'failed']);

export function Version({ update, owner }: { update: ReleaseOffer; owner: boolean }) {
  const client = useQueryClient();
  const canCheck = owner && !update.managed;
  const [starting, setStarting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [pending, setPending] = useState<{ target: string; after: number } | null>(null);
  const [readStatus, setReadStatus] = useState(false);
  const [progressTarget, setProgressTarget] = useState('');
  const statusQuery = useQuery({
    ...updateStatusQuery,
    enabled: canCheck,
    refetchInterval: ({ state }) => (!pending && (state.status === 'success' || progressTarget) && updateProgress(state.data) ? STATUS_INTERVAL : false),
  });
  const { refetch: refetchStatus, dataUpdatedAt, isFetchedAfterMount, isSuccess } = statusQuery;
  const status = canCheck && isFetchedAfterMount && (isSuccess || progressTarget) ? statusQuery.data : undefined;
  const statusError = canCheck && isFetchedAfterMount ? statusQuery.error : null;
  const progress = updateProgress(status);
  const busy = starting || !!pending || !!progress;
  const currentRelease = release(update.current);
  const latest = release(update.latest);
  const scheduled = release(update.scheduled ?? '');
  const target = release(status?.to ?? '');
  const terminal = isSuccess && status && TERMINAL.has(status.state) ? status : undefined;
  const running = release(status?.running ?? '');
  const updated = status?.state === 'healthy' && target === progressTarget && (!running || running === target) ? target : '';
  const current = update.available && latest === (running || updated) ? latest : currentRelease;
  const available = update.available && !!latest && latest !== current;
  const manual = status?.configured === false;

  if (canCheck && !readStatus && isFetchedAfterMount) {
    setReadStatus(true);
    const possibleTarget = scheduled || (available ? latest : '');
    if (possibleTarget && (isNetworkError(statusError) || (statusError instanceof ApiError && statusError.status === 503))) {
      setPending({ target: possibleTarget, after: dataUpdatedAt });
    }
  }
  if (isSuccess && progress && target && progressTarget !== target) setProgressTarget(target);
  const recovered = pending && isSuccess && dataUpdatedAt !== pending.after && (progress || (terminal && target === pending.target));
  if (pending && (!canCheck || recovered || currentRelease === pending.target)) setPending(null);

  useEffect(() => {
    void client.invalidateQueries({ queryKey: configQuery.queryKey }, { cancelRefetch: false });
  }, [client]);

  useEffect(() => {
    if (terminal) void client.invalidateQueries({ queryKey: configQuery.queryKey });
  }, [client, terminal]);

  useEffect(() => {
    if (!pending || !canCheck) return;
    const poll = setInterval(() => void refetchStatus({ cancelRefetch: false }), STATUS_INTERVAL);
    const stop = setTimeout(() => {
      clearInterval(poll);
      setPending(null);
      setFeedback('Could not confirm the update.');
    }, RECOVERY_LIMIT);
    return () => {
      clearInterval(poll);
      clearTimeout(stop);
    };
  }, [canCheck, pending, refetchStatus]);

  const start = async () => {
    setStarting(true);
    setFeedback('');
    setProgressTarget('');
    try {
      await client.cancelQueries({ queryKey: updateStatusQuery.queryKey });
      client.setQueryData(updateStatusQuery.queryKey, await startUpdate(update.latest));
    } catch (error) {
      // The updater may have accepted the request before the server stopped answering.
      if (isNetworkError(error) || (error instanceof ApiError && error.status >= 500)) {
        setPending({ target: latest, after: client.getQueryState(updateStatusQuery.queryKey)?.dataUpdatedAt ?? 0 });
      } else {
        setFeedback(error instanceof Error && error.message ? error.message : 'Could not start the update.');
      }
    } finally {
      setStarting(false);
    }
  };

  const check = async () => {
    setFeedback('');
    try {
      const found = await checkForUpdate();
      setFeedback(found.checking ? 'Still checking…' : found.available || found.held ? '' : 'Up to date.');
      if (statusError) void refetchStatus({ cancelRefetch: false });
      await client.invalidateQueries({ queryKey: configQuery.queryKey });
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'Could not check for updates.');
    }
  };

  let outcome = '';
  if (updated) {
    outcome = `Updated to v${updated}.`;
  } else if (available && target === latest) {
    if (status?.state === 'rolled_back') outcome = 'Tico went back to the previous version.';
    if (status?.state === 'failed') outcome = 'The update failed.';
    if (outcome && status?.message) outcome += ` ${status.message}`;
  }
  const readProblem = available && statusError && (statusError.message || 'Could not read update status.');
  const message = pending ? 'Restarting Tico…' : progress || (starting ? 'Starting the update…' : feedback || outcome || readProblem);

  return (
    <Line label="Version" target="setting:version">
      <span className="set-version">
        <span>Tico {/^\d/.test(current) ? `v${current}` : current}</span>
        {available && <span>v{latest} is out</span>}
        {scheduled && <span>Tico will update to v{scheduled} when idle.</span>}
        {update.held && <span>{update.held}</span>}
        {update.managed && <span>Tico manages updates for this installation.</span>}
        {available && !update.managed && !owner && <span>The owner can update Tico.</span>}
        {available && manual && <span>Update this installation manually. {status.command && <code>{status.command}</code>}</span>}
        {message && <span role="status">{message}</span>}
        {canCheck && (
          <span className="set-version-actions">
            {(busy || (available && !update.held && !manual)) && (
              <Button busy={busy} disabled={!isSuccess || status?.configured !== true} onClick={start}>
                Update now
              </Button>
            )}
            <Button variant="quiet" disabled={busy} onClick={check}>
              Check now
            </Button>
          </span>
        )}
      </span>
    </Line>
  );
}
