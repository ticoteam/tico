import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { groupsQuery } from '@/features/team/exports';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { isDm, isMuted, type Thread } from './model';
import { press } from './people';

type Notify = 'live' | 'mentions' | 'digest' | 'muted';
const NOTIFY = [
  { value: 'live', label: 'All messages' },
  { value: 'mentions', label: 'Mentions' },
  { value: 'digest', label: 'Digest' },
  { value: 'muted', label: 'Off' },
] as const;
const KEEP = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' },
  { value: 'always', label: 'Always' },
] as const;
type Keep = (typeof KEEP)[number]['value'];

/** Rooms that are not a plain thread: an agent's Home, a task's chat, a meeting's. */
const special = (t: Thread) => t.audience === 'home' || t.audience === 'task' || t.audience === 'meeting' || !!t.task_id;

/**
 * One thread's settings beyond the ⋯ menu: how it notifies you, who can read it, pinned for everyone, how long it keeps
 * messages, and what can be done to it whole (summarize, mark read, open earlier messages, delete). A row shows only
 * where the server allows it (the old app's thread-settings sheet).
 */
export function Settings({ thread: t, onClose }: { thread: Thread; onClose: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const manage = t.me?.can_manage === true;
  const root = !t.parent_id;
  const dm = isDm(t);
  const notify: Notify = t.me?.following === false || isMuted(t) ? 'muted' : ((t.me?.notify as Notify | undefined) ?? 'live');
  const keep: Keep = t.retention_days === 30 || t.retention_days === 90 || t.retention_days === 365 ? (String(t.retention_days) as Keep) : 'always';
  const audience = t.audience === 'company' || t.audience === 'members' || t.audience === 'team' ? t.audience : null;
  const groups = useQuery({ ...groupsQuery, enabled: manage && !!audience });
  const teams = Array.isArray(groups.data) ? groups.data : [];
  // A team reads it as `team:<id>` (ui threads-page "A team" and its pick).
  const reader = audience === 'team' ? `team:${t.audience_group_id ?? ''}` : audience;
  const deletable = root && manage && t.audience !== 'home' && t.deletable !== false;

  const del = async () => {
    if (await press('threads.delete', { thread: t.id })) onClose();
    else setDeleting(false);
  };

  return (
    <>
      <Dialog
        open={!deleting}
        onOpenChange={(open) => !open && onClose()}
        title="Settings"
        actions={
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        }
      >
        <Facts label="Thread settings">
          <LabelValue label="Notify me">
            <Select
              label="Notify me"
              labelHidden
              look="quiet"
              options={NOTIFY}
              value={notify}
              onValueChange={(v) => void press('threads.notify', { thread: t.id, notify: v })}
            />
          </LabelValue>
          {manage && root && audience && !dm && !special(t) && (
            <LabelValue label="Who can read">
              <Select
                label="Who can read"
                labelHidden
                look="quiet"
                options={[
                  { value: 'members', label: 'Its members' },
                  ...teams.map((g) => ({ value: `team:${g.id}`, label: g.name || 'A team' })),
                  ...(reader?.startsWith('team:') && !teams.some((g) => `team:${g.id}` === reader) ? [{ value: reader, label: 'A team' }] : []),
                  { value: 'company', label: 'Everyone' },
                ]}
                value={reader}
                onValueChange={(v) =>
                  v !== reader &&
                  void press(
                    'threads.whoCanRead',
                    v.startsWith('team:') ? { thread: t.id, audience: 'team', team: v.slice(5) } : { thread: t.id, audience: v },
                  )
                }
              />
            </LabelValue>
          )}
          {manage && !dm && !special(t) && (
            <LabelValue label="Pin for everyone">
              <Switch
                label="Pin for everyone"
                labelHidden
                checked={t.pinned_for_everyone}
                onCheckedChange={(pinned) => void press('threads.pinForEveryone', { thread: t.id, pinned })}
              />
            </LabelValue>
          )}
          {manage && root && t.audience !== 'task' && t.audience !== 'meeting' && !t.task_id && (
            <LabelValue label="Keep messages">
              <Select
                label="Keep messages"
                labelHidden
                look="quiet"
                options={KEEP}
                value={keep}
                onValueChange={(v) => void press('threads.keepMessages', { thread: t.id, days: v === 'always' ? null : Number(v) })}
              />
            </LabelValue>
          )}
        </Facts>
        <div className="ts-acts">
          <Button icon="spark" onClick={() => void press('threads.summarize', { thread: t.id })}>
            Summarize
          </Button>
          <Button icon="check" onClick={() => void press('threads.markRead', { threads: [t.id] })}>
            Mark read
          </Button>
          {manage && t.earlier && t.earlier.count > 0 && (
            <Button icon="clock" onClick={() => void press('threads.openEarlier', { thread: t.id })}>
              Open earlier
            </Button>
          )}
          {deletable && (
            <Button icon="close" onClick={() => setDeleting(true)}>
              Delete thread
            </Button>
          )}
        </div>
      </Dialog>
      <Dialog
        open={deleting}
        alert
        onOpenChange={(open) => !open && setDeleting(false)}
        title="Delete this thread?"
        description="Its messages go for good."
        actions={
          <>
            <Button onClick={() => setDeleting(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => void del()}>
              Delete thread
            </Button>
          </>
        }
      />
    </>
  );
}
