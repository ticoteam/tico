import { useQuery } from '@tanstack/react-query';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { fmtDate } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { press, RemoveItem, useConfirmRemove, Word } from './parts';
import { deliveriesQuery, hooksQuery, shownOnceQuery } from './queries';
import type { Delivery } from './types';
import { startsWords } from './words';

/* One incoming webhook, as a panel: its address, what it starts, its recent deliveries. The full address, secret
   and all, shows only right after it is made or given a new secret; after that the secret cannot be read again. */

const OUTCOME: Record<string, string> = {
  accepted: 'Taken',
  refused: 'Refused',
  rate_limited: 'Too many',
  replayed: 'Sent twice',
  duplicate: 'Sent twice',
};

const copy = (text: string) =>
  void navigator.clipboard.writeText(text).then(
    () => toast({ text: 'Address copied', kind: 'done' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );

export function WebhookPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const hooks = useQuery(hooksQuery);
  const h = hooks.data?.find((x) => x.id === id);
  const shown = useQuery(shownOnceQuery(id)).data;
  const remove = useConfirmRemove();
  if (!h) {
    return (
      <SidePanel title="Webhook" onClose={onClose}>
        {!hooks.isPending && <Empty icon="alert" title="This webhook is gone" />}
      </SidePanel>
    );
  }
  const tmp = h.id.startsWith('tmp:');
  return (
    <SidePanel
      title={h.name}
      target={`hook:${h.id}`}
      onClose={onClose}
      actions={
        !h.builtin && (
          <Menu trigger={<IconButton icon="more" label="More" quiet disabled={tmp} />}>
            <MenuItem label="New secret" icon="keys" onSelect={() => void press('webhooks.rotate', { hook: h.id })} />
            <RemoveItem onSelect={() => remove.confirm(h.name, () => void press('webhooks.remove', { hook: h.id }))} />
          </Menu>
        )
      }
    >
      {shown && (
        <section className="rail-sec rr-once" data-target={`hook-secret:${h.id}`}>
          <h3>Address</h3>
          <p className="rr-addr">{shown}</p>
          <p className="row-sub">Copy it now. Its secret is not shown again.</p>
          <Button icon="copy" onClick={() => copy(shown)}>
            Copy address
          </Button>
        </section>
      )}
      <section className="rail-sec">
        <Facts label="About">
          <LabelValue label="Starts">{startsWords(h).replace(/^Starts /, '')}</LabelValue>
          <LabelValue label="Signed">{h.builtin ? 'Built in' : h.signed === false ? 'No' : 'Yes'}</LabelValue>
          <LabelValue label="Last call">{h.last_received ? <Ago at={h.last_received} /> : 'Never'}</LabelValue>
          {!shown && h.url && (
            <LabelValue label="Address">
              <span className="rr-addr">{`${h.url}/…`}</span>
            </LabelValue>
          )}
        </Facts>
      </section>
      {!tmp && <Deliveries id={h.id} />}
      {remove.node}
    </SidePanel>
  );
}

function Deliveries({ id }: { id: string }) {
  const q = useQuery(deliveriesQuery(id));
  const list = q.data ?? [];
  return (
    <section className="rail-sec">
      <h3>Recent calls</h3>
      {q.isError && <Word>Calls did not load</Word>}
      {list.map((d) => (
        <Row key={d.delivery} className="prun" title={deliveryWords(d)}>
          <span className="run-at">{fmtDate(d.received_at)}</span>
          <span className="row-sub">{deliveryWords(d)}</span>
          {d.outcome === 'accepted' ? <span className="row-age">{OUTCOME.accepted}</span> : <Word>{OUTCOME[d.outcome] ?? 'Refused'}</Word>}
        </Row>
      ))}
    </section>
  );
}

/** What a call started: "Started 2 tasks", or why it was turned away. */
export function deliveryWords(d: Delivery): string {
  if (d.outcome !== 'accepted') return d.reason || OUTCOME[d.outcome] || 'Refused';
  const n = d.tasks ?? 0;
  return n ? `Started ${n} ${n === 1 ? 'task' : 'tasks'}` : 'Started nothing';
}
