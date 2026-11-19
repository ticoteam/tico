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
import { refusal, run } from '@/actions';
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

/** `shown` is the full address held by the page (state only) right after it was made or given a new secret; `signing`
   a signing secret the server made when it was added. */
export function WebhookPanel({ id, shown, signing: made, onShown, onClose }: { id: string; shown: string | null; signing?: string | null; onShown: (url: string) => void; onClose: () => void }) {
  const hooks = useQuery(hooksQuery);
  const h = hooks.data?.find((x) => x.id === id);
  const remove = useConfirmRemove();
  const [signing, setSigning] = useState(false);
  const [editing, setEditing] = useState(false);
  const [rotating, setRotating] = useState(false);
  // a signing secret shown once: this state only, never the cache, a toast or the address
  // until the panel sets its own, the one made with the webhook (if any)
  const [own, setSecret] = useState<string | null | undefined>(undefined);
  const secret = own === undefined ? (made ?? null) : own;
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
        <Menu trigger={<IconButton icon="more" label="More" quiet disabled={tmp} />}>
          <MenuItem label="Edit" icon="edit" onSelect={() => setEditing(true)} />
          {!h.builtin && (
            <>
              <MenuItem label="New secret" icon="keys" onSelect={() => setRotating(true)} />
              <MenuItem label="Signing" icon="lock" onSelect={() => setSigning(true)} />
              <RemoveItem onSelect={() => remove.confirm(h.name, () => void press('webhooks.remove', { hook: h.id }))} />
            </>
          )}
        </Menu>
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
          <LabelValue label="24 h">{`${h.received_24h ?? 0} calls${h.refused_24h ? `, ${h.refused_24h} refused` : ''}`}</LabelValue>
          {!h.builtin && h.type_from && <LabelValue label="Type from">{h.type_from}</LabelValue>}
          {!h.builtin && h.hourly != null && <LabelValue label="Per hour">{String(h.hourly)}</LabelValue>}
          {h.keep_days != null && <LabelValue label="Keep">{`${h.keep_days} ${h.keep_days === 1 ? 'day' : 'days'}`}</LabelValue>}
          {!h.builtin && <LabelValue label="If nothing listens">{UNHEARD.find((u) => u.value === h.unheard)?.label ?? 'Log it'}</LabelValue>}
          {!shown && h.url && (
            <LabelValue label="Address">
              <span className="rr-addr">{`${h.url}/…`}</span>
            </LabelValue>
          )}
        </Facts>
      </section>
      {!tmp && <Deliveries hook={h} />}
      {remove.node}
      {signing && <Signing hook={h} onSecret={setSecret} onClose={() => setSigning(false)} />}
      {editing && <HookDialog hook={h} onClose={() => setEditing(false)} />}
      <Dialog
        open={rotating}
        onOpenChange={setRotating}
        alert
        title="Make a new address?"
        description="The current one stops working at once."
        actions={
          <>
            <Button variant="quiet" onClick={() => setRotating(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setRotating(false);
                void rotate(h.id, onShown);
              }}
            >
              New secret
            </Button>
          </>
        }
      />
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

const refused = (d: Delivery) => d.outcome === 'refused' || d.outcome === 'rate_limited';

function Deliveries({ hook }: { hook: Hook }) {
  const id = hook.id;
  const q = useQuery(deliveriesQuery(id));
  const list = q.data ?? [];
  const [open, setOpen] = useState<string | null>(null);
  // a refused call still kept can be checked again under the current secret (not one already recovered)
  const recoverable = !hook.builtin && list.some((d) => refused(d) && d.stored && !/\(recovered\)$/.test(d.reason ?? ''));
  return (
    <section className="rail-sec">
      <h3>
        Recent calls
        {recoverable && (
          <Verb onClick={() => void pressSaid('webhooks.recover', { hook: id })} target={`hook-recover:${id}`}>
            Recover refused
          </Verb>
        )}
      </h3>
      {q.isError && <Word>Calls did not load</Word>}
      {list.map((d) => (
        <Row key={d.delivery} className="prun" title={deliveryWords(d)}>
          <span className="run-at">{fmtDate(d.received_at)}</span>
          <span className="row-sub">{deliveryWords(d)}</span>
          <span className="rr-field">
            {d.outcome === 'accepted' ? <span className="row-age">{OUTCOME.accepted}</span> : <Word>{OUTCOME[d.outcome] ?? 'Refused'}</Word>}
            {d.stored && <Verb onClick={() => setOpen(d.delivery)}>Payload</Verb>}
            {d.stored && !hook.builtin && !refused(d) && <Verb onClick={() => void pressSaid('webhooks.replay', { hook: id, delivery: d.delivery })}>Run again</Verb>}
          </span>
        </Row>
      ))}
      {open && <PayloadDialog hook={id} delivery={open} onClose={() => setOpen(null)} />}
    </section>
  );
}

/** A press whose result is worth saying (how many calls were recovered, how many tasks a rerun started). */
async function pressSaid(action: string, input: unknown) {
  const r = await run(action, input, { via: 'click' });
  toast(r.ok ? { text: r.did ?? 'Done', kind: 'done' } : refusal(r, "Couldn't do that"));
}

export const UNHEARD = [
  { value: 'log', label: 'Log it' },
  { value: 'note', label: 'Tell the maker' },
] as const;
type Unheard = (typeof UNHEARD)[number]['value'];
/** The type a preset reads by default (settings-webhooks.ts WH_TYPE_FROM). */
const TYPE_FROM: Record<string, string> = { github: 'X-GitHub-Event', stripe: '/type', standard: '/type', slack: '/event/type|/type' };
const NONE = 'none';

/** Its settings, or a new one's (with how its calls are signed): name, where its type is read, calls an hour, days a
   call is kept, and what a call nothing listens for does. A built-in one changes only how long calls are kept. */
export function HookDialog({ hook, onMade, onClose }: { hook?: Hook; onMade?: (m: { hook: string; url: string; signing?: string }) => void; onClose: () => void }) {
  const builtin = !!hook?.builtin;
  const [name, setName] = useState(hook?.name ?? '');
  const [preset, setPreset] = useState<string>(NONE);
  const [secret, setSecret] = useState('');
  const [typeFrom, setTypeFrom] = useState(hook?.type_from ?? '');
  const [hourly, setHourly] = useState(String(hook?.hourly ?? 60));
  const [keep, setKeep] = useState(String(hook?.keep_days ?? 7));
  const [unheard, setUnheard] = useState<Unheard>(hook?.unheard === 'note' ? 'note' : 'log');
  const [busy, setBusy] = useState(false);
  const perHour = Number(hourly);
  const keepDays = Number(keep);
  const badHour = !(Number.isInteger(perHour) && perHour >= 1 && perHour <= 600);
  const badKeep = !(Number.isInteger(keepDays) && keepDays >= 1 && keepDays <= 30);
  const ready = (builtin || !!name.trim()) && !badKeep && (builtin || !badHour);
  const pickPreset = (p: string) => {
    // the type follows the preset unless one was typed
    if (!typeFrom || Object.values(TYPE_FROM).includes(typeFrom)) setTypeFrom(TYPE_FROM[p] ?? '');
    setPreset(p);
  };
  const save = async () => {
    if (!ready) return;
    setBusy(true);
    if (!hook) {
      // only what differs from a new webhook's own settings is sent
      const r = await pressOut<{ hook: string; url: string; signing?: string }>('webhooks.add', {
        name: name.trim(),
        ...(preset !== NONE ? { preset, ...(secret.trim() ? { secret: secret.trim() } : {}) } : {}),
        ...(typeFrom.trim() && typeFrom.trim() !== (TYPE_FROM[preset] ?? '') ? { typeFrom: typeFrom.trim() } : {}),
        ...(perHour !== 60 ? { hourly: perHour } : {}),
        ...(keepDays !== 7 ? { keepDays } : {}),
        ...(unheard !== 'log' ? { unheard } : {}),
      });
      setBusy(false);
      setSecret('');
      if (r) {
        if (r.out?.url) onMade?.(r.out);
        onClose();
      }
      return;
    }
    const ok = await press(
      'webhooks.update',
      builtin ? { hook: hook.id, keepDays } : { hook: hook.id, name: name.trim(), typeFrom: typeFrom.trim(), hourly: perHour, keepDays, unheard },
    );
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={hook ? (builtin ? hook.name : 'Edit webhook') : 'Add webhook'}
      description={hook ? undefined : 'Its address is shown once, after you add it.'}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {hook ? 'Save' : 'Add'}
          </Button>
        </>
      }
    >
      <div className="rr-form">
        {!builtin && <Field label="Name" value={name} onValueChange={setName} placeholder="Shipping" autoFocus />}
        {!hook && <Select label="Signing" options={[{ value: NONE, label: 'None' }, ...PRESETS]} value={preset} onValueChange={pickPreset} />}
        {!hook && preset !== NONE && <Field label="Signing secret" type="password" autoComplete="off" value={secret} onValueChange={setSecret} placeholder="Leave empty to make one" />}
        {!builtin && <Field label="Type from" value={typeFrom} onValueChange={setTypeFrom} placeholder="Header or /json/path" />}
        {!builtin && <Field label="Per hour" type="number" value={hourly} onValueChange={setHourly} error={badHour ? '1 to 600' : undefined} />}
        <Field label="Keep days" type="number" value={keep} onValueChange={setKeep} error={badKeep ? '1 to 30' : undefined} />
        {!builtin && <Select label="If nothing listens" options={UNHEARD} value={unheard} onValueChange={setUnheard} />}
      </div>
    </Dialog>
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
