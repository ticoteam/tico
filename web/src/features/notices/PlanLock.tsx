import { useQuery } from '@tanstack/react-query';
import { refusal, run, useAction } from '@/actions';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { toast } from '@/kit/Toast';
import { CenteredCard } from '@/pagekinds';
import { Cover } from './Cover';
import { SUPPORT_URL, usePlanLock } from './queries';

/* "Your plan needs attention": the company's Tico Cloud plan is locked, so the server refuses everything but billing
   and the files download. The whole app waits under it (pages/15-start `lock`). Paying on HQ's page and pressing
   Check again opens it. */

const WHY: Record<string, string> = {
  trial_over: 'Your trial has ended.',
  unpaid: "This month's payment didn't go through.",
};

const click = { via: 'click' } as const;

/** For the shell, beside the frame: nothing while the plan is fine. */
export function PlanLock() {
  const lock = usePlanLock();
  if (!lock) return null;
  return <Locked reason={lock.reason} />;
}

function Locked({ reason }: { reason: string | null }) {
  const { data: me } = useQuery(meQuery);
  const check = useAction('notices.checkPlan');
  const again = async () => {
    const r = await check.run({});
    if (!r.ok) toast(r.error?.startsWith('still_locked') ? { text: 'Your plan still needs attention', kind: 'failed' } : refusal(r, "Couldn't check your plan"));
  };
  return (
    <Cover label="Your plan needs attention">
      <CenteredCard
        title="Your plan needs attention"
        path="/plan-locked"
        fault="lock"
        text={WHY[reason ?? ''] ?? 'Your plan is paused.'}
        actions={
          <>
            {me?.role === 'owner' && (
              <Button variant="quiet" onClick={() => void run('notices.downloadFiles', {}, click)}>
                Download files
              </Button>
            )}
            <span className="nt-pair">
              <Button busy={check.pending} onClick={() => void again()}>
                Check again
              </Button>
              <Button variant="primary" onClick={() => void run('notices.openBilling', {}, click)}>
                Open billing
              </Button>
            </span>
          </>
        }
        foot={
          <span className="nt-foot">
            <a href={SUPPORT_URL} target="_blank" rel="noopener noreferrer">
              Contact Tico support
            </a>
            <Button variant="quiet" onClick={() => void run('start.signOut', {}, click)}>
              Sign out
            </Button>
          </span>
        }
      />
    </Cover>
  );
}
