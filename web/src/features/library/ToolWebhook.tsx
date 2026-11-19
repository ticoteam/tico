import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { ToolIndexRow } from '@/api/schema';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { MenuItem, MenuRule } from '@/kit/Menu';
import { toast } from '@/kit/Toast';
import { PageMore, Section } from '@/pagekinds';
import { Foot } from './AddTool';
import { press } from './parts';
import { webhookQuery, webhookSendsQuery } from './queries';
import { Secret } from './ToolDialogs';
import { Confirm } from './ToolMore';
import { bad, Shell, stateOf } from './ToolShell';

/* An outgoing webhook under Tools (old settings-webhooks.ts whHookDetail): where it sends, how its sends went, and in
   its ⋯ Send test, Pause, New secret and Delete; a failed send retries from its row. Adding
   and editing one stay in Routines › Webhooks. A new signing secret lives in its dialog's state only. */

const SENT: Record<string, string> = {
  ready: 'Waiting',
  sending: 'Sending',
  delivered: 'Delivered',
  failed: 'Failed',
  canceled: 'Canceled',
  withheld: 'Held back',
};

type HookDialog = 'secret' | 'delete';

export function WebhookView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const hook = useQuery(webhookQuery(id));
  const sends = useQuery(webhookSendsQuery(id));
  const [dialog, setDialog] = useState<HookDialog | null>(null);
  const w = hook.data?.webhook;
  const paused = !!w?.paused_reason;
  const may = !!w?.can_change;
  const name = w?.name ?? row?.name ?? 'Outgoing webhook';
  const more = w && may && (
    <PageMore>
      <MenuItem label="Send test" onSelect={() => void press('tools.testWebhook', { webhook: id }, 'The test did not send')} />
      <MenuItem
        label={paused ? 'Send again' : 'Pause'}
        onSelect={() => void press('tools.pauseWebhook', { webhook: id, on: paused }, paused ? 'It did not restart' : 'It did not pause')}
      />
      <MenuItem label="New secret" onSelect={() => setDialog('secret')} />
      <MenuRule />
      <MenuItem label="Delete" onSelect={() => setDialog('delete')} />
    </PageMore>
  );
  return (
    <Shell
      name={name}
      kind="webhook"
      problem={(row && bad(row)) || w?.disabled_reason || null}
      more={more}
      state={stateOf(hook)}
      onRetry={() => void hook.refetch()}
      target={`tool:webhook:${id}`}
    >
      {w && (
        <Section name="What it is">
          <Facts>
            <LabelValue label="Sends to">{w.url || w.host}</LabelValue>
            <LabelValue label="Events">{w.events.length ? w.events.join(', ') : 'All'}</LabelValue>
            <LabelValue label="State">{paused ? w.paused_reason : w.enabled ? 'On' : 'Off'}</LabelValue>
            <LabelValue label="Last 7 days">
              {`${w.last_7_days.delivered} delivered, ${w.last_7_days.failed} failed, ${w.last_7_days.pending} waiting`}
            </LabelValue>
          </Facts>
        </Section>
      )}
      {sends.data && sends.data.length > 0 && (
        <Section name="Recent sends" count={sends.data.length}>
          {sends.data.map((x) => (
            <div key={x.id} className="row urow" title={x.last_error || x.subject || x.type}>
              <span className="row-title">{x.subject || x.type}</span>
              <span className="row-ctx">{x.last_status ? `${SENT[x.state] ?? x.state} (${x.last_status})` : (SENT[x.state] ?? x.state)}</span>
              {may && (x.state === 'failed' || x.state === 'canceled') && (
                <IconButton icon="refresh" label="Retry" size="sm" onClick={() => void press('tools.retrySend', { webhook: id, send: x.id }, 'Not sent again')} />
              )}
              <Ago at={x.delivered_at ?? x.created_at} className="row-age" />
            </div>
          ))}
        </Section>
      )}
      {dialog === 'secret' && <NewSecretDialog webhook={id} onClose={() => setDialog(null)} />}
      {dialog === 'delete' && (
        <Confirm
          title={`Delete ${name}?`}
          description="Nothing more is sent to it."
          label="Delete"
          onGo={() => press('tools.deleteWebhook', { webhook: id }, 'Not deleted')}
          onClose={() => setDialog(null)}
        />
      )}
    </Shell>
  );
}

/** Asks first; then the new secret, once, in this dialog's state. Closing drops it. */
function NewSecretDialog({ webhook, onClose }: { webhook: string; onClose: () => void }) {
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    const r = await run('tools.newWebhookSecret', { webhook }, { via: 'click' });
    setBusy(false);
    if (r.ok && typeof r.out === 'string') setSecret(r.out);
    else toast(refusal(r, 'No new secret'));
  };
  if (secret === null)
    return (
      <Dialog
        open
        alert
        onOpenChange={(o) => !o && onClose()}
        title="Make a new signing secret?"
        description="The old one keeps working for a day."
        actions={<Foot onClose={onClose} label="Make" busy={busy} ready onGo={() => void make()} />}
      />
    );
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Signing secret" description="Shown once." className="connect" actions={<Button onClick={onClose}>Done</Button>}>
      <Secret label="Signing secret" value={secret} onHide={onClose} />
    </Dialog>
  );
}
