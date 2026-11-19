import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useAction } from '@/actions';
import type { Actor } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Pill } from '@/kit/Pill';
import { fmtDate } from '@/lib';
import { lastStopAgain, lastStopQuery, type LastStop } from './queries';

/** Is anything needed? In the old dialog's words (ui/app/bot-page.ts lastStopOpen). */
export function needed(stop: LastStop, manage: boolean): string {
  if (!stop.needs_person) return 'No. Nothing was lost.';
  if (stop.action !== 'review') return 'Yes, as the line above says.';
  return manage ? 'Yes: say whether to try it again or let it go.' : 'Yes: a person who manages this agent decides whether to try it again.';
}

/**
 * Why its last run ended without finishing (backend/execution.py last_stop): a pill beside its name, Stopped when a
 * person must act, and Last run when nothing is needed. It opens what happened (so does the head's ⋯, where a narrow
 * page hides the pill). Its status moving reads it again.
 */
export function LastRun({ slug, actor, manage, open, onOpenChange }: { slug: string; actor: Actor; manage: boolean; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { data: stop } = useQuery(lastStopQuery(slug));
  const signal = `${actor.working ? 1 : 0}|${actor.status ?? ''}`;
  const seen = useRef(signal);
  useEffect(() => {
    if (seen.current === signal) return;
    seen.current = signal;
    lastStopAgain(slug);
  }, [signal, slug]);
  const review = useAction('agents.reviewStopped');
  if (!stop) return null;
  const reviewable = stop.action === 'review' && manage;
  return (
    <>
      <button type="button" className="last-stop" title={stop.text} onClick={() => onOpenChange(true)}>
        <Pill flag={stop.needs_person}>{stop.needs_person ? 'Stopped' : 'Last run'}</Pill>
      </button>
      <Dialog
        open={open}
        onOpenChange={onOpenChange}
        title="Last run"
        description={stop.text}
        actions={
          <>
            <Button onClick={() => onOpenChange(false)}>Close</Button>
            {reviewable && (
              <Button
                variant="primary"
                onClick={() => {
                  onOpenChange(false);
                  void review.run({ slug });
                }}
              >
                Review
              </Button>
            )}
          </>
        }
      >
        <div className="lr-facts">
          <Facts label="Last run">
            {stop.reason && <LabelValue label="What happened">{stop.reason}</LabelValue>}
            <LabelValue label="Is anything needed?">{needed(stop, manage)}</LabelValue>
            {stop.at && <LabelValue label="When">{fmtDate(stop.at)}</LabelValue>}
          </Facts>
        </div>
      </Dialog>
    </>
  );
}
