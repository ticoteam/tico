import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { fmtDate } from '@/lib';
import { SidePanel } from '@/pagekinds';
import { press, pressOut, RemoveItem, useConfirmRemove, Verb, Word } from './parts';
import { deliveriesQuery, hooksQuery, payloadQuery } from './queries';
import type { Delivery, Hook } from './types';
import { cap, startsWords } from './words';

/* One incoming webhook, as a panel: its address, what it starts, its recent deliveries. The full address, secret
   and all, shows only right after it is made or given a new secret; after that the secret cannot be read again. A
   signing secret the server makes shows the same way: held in this panel's state only, gone when it closes. A kept
   call opens its headers and body (Payload). */

const OUTCOME: Record<string, string> = {
  accepted: 'Taken',
  refused: 'Refused',
  rate_limited: 'Too many',
  replayed: 'Sent twice',
  duplicate: 'Sent twice',
};

const copy = (text: string, what = 'Address') =>
  void navigator.clipboard.writeText(text).then(
    () => toast({ text: `${what} copied`, kind: 'done' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );

/** The presets a person may pick here (backend/trigger_presets.py; the Finance ones are set by Finance). */
const PRESETS = [
  { value: 'hmac', label: 'HMAC' },
  { value: 'github', label: 'GitHub' },
  { value: 'standard', label: 'Standard Webhooks' },
  { value: 'stripe', label: 'Stripe' },
  { value: 'slack', label: 'Slack' },
];

/** `shown` is the full address held by the page (state only) right after it was made or given a new secret. */
export function WebhookPanel({ id, shown, onShown, onClose }: { id: string; shown: string | null; onShown: (url: string) => void; onClose: () => void }) {
  const hooks = useQuery(hooksQuery);
  const h = hooks.data?.find((x) => x.id === id);
  const remove = useConfirmRemove();
  const [signing, setSigning] = useState(false);
  // a signing secret shown once: this state only, never the cache, a toast or the address
  const [secret, setSecret] = useState<string | null>(null);
  if (!h) {
    return (
      <SidePanel className="rr" title="Webhook" onClose={onClose}>
        {!hooks.isPending && <Empty icon="alert" title="This webhook is gone" />}
      </SidePanel>
    );
  }
  const tmp = h.id.startsWith('tmp:');
  return (
    <SidePanel
      className="rr"
      title={h.name}
      target={`hook:${h.id}`}
      onClose={onClose}
      actions={
        !h.builtin && (
          <Menu trigger={<IconButton icon="more" label="More" quiet disabled={tmp} />}>
            <MenuItem label="New secret" icon="keys" onSelect={() => void rotate(h.id, onShown)} />
            <MenuItem label="Signing" icon="lock" onSelect={() => setSigning(true)} />
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
      {secret && (
        <section className="rail-sec rr-once" data-target={`hook-signing:${h.id}`}>
          <h3>Signing secret</h3>
          <p className="rr-addr">{secret}</p>
          <p className="row-sub">Copy it now. It is not shown again.</p>
          <Button icon="copy" onClick={() => copy(secret, 'Secret')}>
            Copy secret
          </Button>
        </section>
      )}
      <section className="rail-sec">
        <Facts label="About">
          <LabelValue label="Starts">{startsWords(h).replace(/^Starts /, '')}</LabelValue>
          <LabelValue label="Signed">{h.builtin ? 'Built in' : h.signed === false ? 'No' : (PRESETS.find((p) => p.value === h.preset)?.label ?? 'Yes')}</LabelValue>
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
      {signing && <Signing hook={h} onSecret={setSecret} onClose={() => setSigning(false)} />}
    </SidePanel>
  );
}

async function rotate(hook: string, onShown: (url: string) => void) {
  const r = await pressOut<{ url: string }>('webhooks.rotate', { hook });
  if (r?.out?.url) onShown(r.out.url);
}

/** Check each call with a preset and a secret: typed, or left empty for the server to make one (shown once). Replacing
   a secret or turning signing off asks first, since calls signed the old way are refused at once. */
function Signing({ hook, onSecret, onClose }: { hook: Hook; onSecret: (s: string | null) => void; onClose: () => void }) {
  const known = PRESETS.some((p) => p.value === hook.preset);
  const options = hook.signed && hook.preset && !known ? [...PRESETS, { value: hook.preset, label: cap(hook.preset) }] : PRESETS;
  const [preset, setPreset] = useState(hook.signed && hook.preset && hook.preset !== 'none' ? hook.preset : 'hmac');
  const [typed, setTyped] = useState('');
  const [ask, setAsk] = useState<'replace' | 'off' | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (hook.signed && ask !== 'replace') return setAsk('replace');
    setBusy(true);
    const r = await pressOut<{ secret?: string; replayed?: boolean }>('webhooks.sign', { hook: hook.id, preset, ...(typed.trim() ? { secret: typed.trim() } : {}) });
    setBusy(false);
    if (!r) return setAsk(null);
    setTyped('');
    onSecret(r.out?.secret ?? null);
    if (!typed.trim() && r.out?.replayed) toast({ text: 'Saved, but the secret was lost on the way. Save again for a new one.', kind: 'failed' });
    onClose();
  };
  const off = async () => {
    setBusy(true);
    const ok = await press('webhooks.signOff', { hook: hook.id });
    setBusy(false);
    if (ok) {
      onSecret(null);
      onClose();
    } else setAsk(null);
  };
  const close = () => {
    setTyped('');
    onClose();
  };
  if (ask) {
    const replace = ask === 'replace';
    return (
      <Dialog
        open
        alert
        onOpenChange={(o) => !o && close()}
        title={replace ? 'Replace the signing secret?' : 'Turn signing off?'}
        description={replace ? 'Calls signed with the current one are refused at once.' : 'Anyone with the address can call it.'}
        actions={
          <>
            <Button variant="quiet" onClick={() => setAsk(null)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void (replace ? save() : off())}>
              {replace ? 'Replace' : 'Turn off'}
            </Button>
          </>
        }
      />
    );
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && close()}
      title="Signing"
      actions={
        <>
          {hook.signed && (
            <Button variant="quiet" onClick={() => setAsk('off')}>
              Turn off
            </Button>
          )}
          <Button variant="quiet" onClick={close}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Select label="Preset" options={options} value={preset} onValueChange={setPreset} />
        <Field label="Signing secret" type="password" autoComplete="off" value={typed} onValueChange={setTyped} placeholder="Leave empty to make one" />
      </div>
    </Dialog>
  );
}

function Deliveries({ id }: { id: string }) {
  const q = useQuery(deliveriesQuery(id));
  const list = q.data ?? [];
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="rail-sec">
      <h3>Recent calls</h3>
      {q.isError && <Word>Calls did not load</Word>}
      {list.map((d) => (
        <Row key={d.delivery} className="prun" title={deliveryWords(d)}>
          <span className="run-at">{fmtDate(d.received_at)}</span>
          <span className="row-sub">{deliveryWords(d)}</span>
          <span className="rr-field">
            {d.outcome === 'accepted' ? <span className="row-age">{OUTCOME.accepted}</span> : <Word>{OUTCOME[d.outcome] ?? 'Refused'}</Word>}
            {d.stored && <Verb onClick={() => setOpen(d.delivery)}>Payload</Verb>}
          </span>
        </Row>
      ))}
      {open && <PayloadDialog hook={id} delivery={open} onClose={() => setOpen(null)} />}
    </section>
  );
}

const bytesWords = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

/** One kept call as it came: its headers (secrets hidden by the server) and its body, JSON laid out. */
function PayloadDialog({ hook, delivery, onClose }: { hook: string; delivery: string; onClose: () => void }) {
  const q = useQuery(payloadQuery(hook, delivery));
  const d = q.data;
  let body = d?.body ?? '';
  try {
    body = JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    // not JSON: shown as sent
  }
  const headers = Object.entries(d?.headers ?? {})
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n');
  const meta = d ? [fmtDate(d.received_at), d.type, bytesWords(d.bytes ?? 0)].filter(Boolean).join(', ') : undefined;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Payload"
      description={meta}
      actions={
        <Button variant="quiet" onClick={onClose}>
          Close
        </Button>
      }
    >
      {q.isError && <Word>The call did not load</Word>}
      {d &&
        (d.expired ? (
          <p className="row-sub">No longer kept</p>
        ) : (
          <div className="rr-form">
            <section aria-label="Headers">
              <h4>Headers</h4>
              <pre className="rr-pre">{headers}</pre>
            </section>
            <section aria-label="Body">
              <h4>Body</h4>
              <pre className="rr-pre">{body}</pre>
            </section>
          </div>
        ))}
    </Dialog>
  );
}

/** What a call started: "Started 2 tasks", or why it was turned away. */
export function deliveryWords(d: Delivery): string {
  if (d.outcome !== 'accepted') return d.reason || OUTCOME[d.outcome] || 'Refused';
  const n = d.tasks ?? 0;
  return n ? `Started ${n} ${n === 1 ? 'task' : 'tasks'}` : 'Started nothing';
}
