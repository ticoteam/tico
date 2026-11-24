import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { configQuery, isNetworkError, readConfigOnce, type ReleaseOffer } from '@/data';
import { Button } from '@/kit/Button';
import { Line } from './parts';
import { checkForUpdate, startUpdate, updateProgress, updateStatusQuery } from './queries';

const release = (version: string) => version.replace(/^v(?=\d)/, '');
const STATUS_INTERVAL = 3_000;
const RECOVERY_LIMIT = 5 * 60_000;
const TERMINAL = new Set(['healthy', 'rolled_back', 'failed']);

interface PendingUpdate {
  source: 'mount' | 'post';
  target: string;
  after: number;
  stopped?: boolean;
}

export function Version({ update, owner }: { update: ReleaseOffer; owner: boolean }) {
  const client = useQueryClient();
  const canCheck = owner && !update.managed;
  const [starting, setStarting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [pending, setPending] = useState<PendingUpdate | null>(null);
  const [checking, setChecking] = useState(false);
  const [readStatus, setReadStatus] = useState(false);
  const [progressTarget, setProgressTarget] = useState('');
  const recovering = !!pending && !pending.stopped;
  const statusQuery = useQuery({
    ...updateStatusQuery,
    enabled: canCheck,
    refetchInterval: ({ state }) =>
      !recovering && (state.status === 'success' || progressTarget) && updateProgress(state.data) ? STATUS_INTERVAL : false,
  });
  const { refetch: refetchStatus, isFetchedAfterMount, isSuccess } = statusQuery;
  const statusReads = client.getQueryState(updateStatusQuery.queryKey)?.dataUpdateCount ?? 0;
  const status = canCheck && isFetchedAfterMount && (isSuccess || progressTarget) ? statusQuery.data : undefined;
  const statusError = canCheck && isFetchedAfterMount ? statusQuery.error : null;
  const currentRelease = release(update.current);
  const latest = release(update.latest);
  const scheduled = release(update.scheduled ?? '');
  const target = release(status?.to ?? '');
  const requested = pending?.target || progressTarget;
  const statusProgress = updateProgress(status);
  const progress = !requested || target === requested ? statusProgress : undefined;
  const busy = starting || recovering || !!statusProgress;
  const terminal = isSuccess && status && TERMINAL.has(status.state) ? status : undefined;
  const fresh = !starting && pending?.after !== statusReads;
  const running = release(status?.running ?? '');
  const updated = fresh && status?.state === 'healthy' && target === progressTarget && (!running || running === target) ? target : '';
  const current = update.available && latest === (running || updated) ? latest : currentRelease;
  const available = update.available && !!latest && latest !== current;
  const matchesOutcome = fresh && !!target && target === (requested || (available ? latest : ''));
  const manual = status?.configured === false;

  if (canCheck && !readStatus && isFetchedAfterMount) {
    setReadStatus(true);
    const possibleTarget = scheduled || (available ? latest : '');
    if (isNetworkError(statusError) || (statusError instanceof ApiError && statusError.status === 503)) {
      setPending({ source: 'mount', target: possibleTarget, after: statusReads });
    }
  }
  if (isSuccess && progress && target && progressTarget !== target) setProgressTarget(target);
  if (pending) {
    const recovered =
      fresh && status && isSuccess && (terminal ? target === pending.target : pending.source === 'mount' || status.configured === false);
    if (!canCheck || recovered) {
      if (recovered && terminal) setProgressTarget(target);
      setPending(null);
      setFeedback('');
    }
  }
  if (feedback && isSuccess && (progress || (terminal && matchesOutcome))) setFeedback('');

  useEffect(() => {
    void client.invalidateQueries({ queryKey: configQuery.queryKey }, { cancelRefetch: false });
  }, [client]);

  useEffect(() => {
    if (terminal) void client.invalidateQueries({ queryKey: configQuery.queryKey });
  }, [client, terminal]);

  useEffect(() => {
    if (!pending || pending.stopped || !canCheck) return;
    let stopped = false;
    let consecutive = 0;
    let poll: ReturnType<typeof setTimeout>;
    const stop = (message: string) => {
      stopped = true;
      clearTimeout(poll);
      setPending({ ...pending, stopped: true });
      setFeedback(message);
    };
    const read = async () => {
      const answer = await refetchStatus({ cancelRefetch: false });
      if (stopped) return;
      const status = answer.isSuccess ? answer.data : undefined;
      if (
        status &&
        (TERMINAL.has(status.state) ? release(status.to ?? '') === pending.target : pending.source === 'mount' || status.configured === false)
      ) {
        clearTimeout(deadline);
        return;
      }
      consecutive = status && !updateProgress(status) ? consecutive + 1 : 0;
      if (consecutive === 3) {
        clearTimeout(deadline);
        stop("The update didn't start. Try again.");
      } else {
        poll = setTimeout(read, STATUS_INTERVAL);
      }
    };
    poll = setTimeout(read, STATUS_INTERVAL);
    const deadline = setTimeout(() => stop('Could not confirm the update.'), RECOVERY_LIMIT);
    return () => {
      stopped = true;
      clearTimeout(poll);
      clearTimeout(deadline);
    };
  }, [canCheck, pending, refetchStatus]);

  useEffect(() => {
    if (!checking) return;
    const controller = new AbortController();
    let remaining = 3;
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      const config = await readConfigOnce(client, controller.signal).catch(() => undefined);
      if (controller.signal.aborted) return;
      remaining--;
      if ((config?.update && !config.update.checking) || remaining === 0) setChecking(false);
      else timer = setTimeout(read, 5_000);
    };
    timer = setTimeout(read, 5_000);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [checking, client]);

  const start = async () => {
    setStarting(true);
    setFeedback('');
    setPending(null);
    setProgressTarget(latest);
    try {
      await client.cancelQueries({ queryKey: updateStatusQuery.queryKey });
      client.setQueryData(updateStatusQuery.queryKey, await startUpdate(update.latest));
    } catch (error) {
      // The updater may have accepted the request before the server stopped answering.
      if ((error instanceof ApiError && error.status === 0) || (error instanceof DOMException && error.name === 'AbortError')) {
        setPending({ source: 'post', target: latest, after: client.getQueryState(updateStatusQuery.queryKey)?.dataUpdateCount ?? 0 });
      } else {
        setFeedback(error instanceof Error && error.message ? error.message : 'Could not start the update.');
        void refetchStatus({ cancelRefetch: false });
      }
    } finally {
      setStarting(false);
    }
  };

  const check = async () => {
    setFeedback('');
    try {
      const found = await checkForUpdate();
      if (statusError) void refetchStatus({ cancelRefetch: false });
      if (found.checking) {
        setChecking(true);
      } else {
        setFeedback(found.available ? '' : 'Up to date.');
        await client.invalidateQueries({ queryKey: configQuery.queryKey });
      }
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'Could not check for updates.');
    }
  };

  let outcome = '';
  if (updated) {
    outcome = `Updated to v${updated}.`;
  } else if (matchesOutcome) {
    if (status?.state === 'rolled_back') outcome = 'Tico went back to the previous version.';
    if (status?.state === 'failed') outcome = 'The update failed.';
    if (outcome && status?.message) outcome += ` ${status.message}`;
  }
  const readProblem = available && statusError && (statusError.message || 'Could not read update status.');
  const action = starting ? 'Starting the update…' : recovering ? 'Restarting Tico…' : checking ? 'Still checking…' : '';
  const message = progress || outcome || feedback || action || readProblem;

  return (
    <Line label="Version" target="setting:version">
      <span className="set-version">
        <span>Tico {/^\d/.test(current) ? `v${current}` : current}</span>
        {available && <span>v{latest} is out</span>}
        {scheduled && <span>Tico will update to v{scheduled} when idle.</span>}
        {update.managed && <span>Tico manages updates for this installation.</span>}
        {available && !update.managed && !owner && <span>The owner can update Tico.</span>}
        {available && manual && <span>Update this installation manually. {status.command && <code>{status.command}</code>}</span>}
        {message && <span role="status">{message}</span>}
        {canCheck && (
          <span className="set-version-actions">
            {(busy || (available && !manual)) && (
              <Button busy={busy} disabled={!isSuccess || status?.configured !== true} onClick={start}>
                Update now
              </Button>
            )}
            <Button variant="quiet" disabled={busy || checking} onClick={check}>
              Check now
            </Button>
          </span>
        )}
      </span>
    </Line>
  );
}
