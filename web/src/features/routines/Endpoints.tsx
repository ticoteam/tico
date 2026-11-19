import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { EventType } from '@/api/schema';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Row } from '@/kit/Row';
import { Segmented } from '@/kit/Segmented';
import { toast } from '@/kit/Toast';
import type { Held } from './actions';
import { pressOut, Word } from './parts';
import { eventTypesQuery, type Endpoint } from './queries';

/* Outgoing webhooks on Routines › Webhooks (settings-webhooks.ts whHooks, whHookDialog): a row each, and the New or
   Edit dialog: name, address, plain http for an http address, events (all, or chosen by area) and custom headers. A
   new endpoint's signing secret shows once, in this dialog's state only. Changing the address while secret headers are
   saved asks first: send them to the new address, or clear them. */

const AREAS: [string, string, string[]][] = [
  ['tasks', 'Tasks', ['task']],
  ['messages', 'Messages', ['thread', 'message']],
  ['agents', 'Agents', ['bot']],
  ['rules', 'Rules', ['rules', 'org_rule']],
];
const MAX_HEADERS = 10;

/** Whether a saved list holds an event: by name, or by its group's `group.*`. */
const holds = (events: string[]) => {
  const set = new Set(events);
  return (t: EventType) => set.has('*') || set.has(t.name) || set.has(`${t.group}.*`);
};

/** What is sent: per catalog group, `group.*` when it has more than one event and all are picked, else the names; codes
    the catalog does not know as they are, last. */
export function eventsValue(types: EventType[], picked: Set<string>, extra: string[]): string[] {
  const groups = new Map<string, EventType[]>();
  for (const t of types) groups.set(t.group, [...(groups.get(t.group) ?? []), t]);
  const out: string[] = [];
  for (const [g, items] of groups) {
    if (items.length > 1 && items.every((t) => picked.has(t.name))) out.push(`${g}.*`);
    else out.push(...items.filter((t) => picked.has(t.name)).map((t) => t.name));
  }
  return [...out, ...extra.filter((e) => picked.has(e))];
}

/** An endpoint's events in a few words: "All events", or the first three and "+N". */
export function eventsWords(events: string[], types: EventType[]): string {
  if (!events.length || events.includes('*')) return 'All events';
  const words = events.map((e) => types.find((t) => t.name === e)?.label ?? e);
  return words.length <= 3 ? words.join(', ') : `${words.slice(0, 3).join(', ')} +${words.length - 3}`;
}

export function EndpointRow({ h, onEdit }: { h: Endpoint; onEdit: () => void }) {
  const types = useQuery(eventTypesQuery).data ?? [];
  const week = h.last_7_days;
  return (
    <Row className="hook" title={h.name} target={`endpoint:${h.id}`}>
      {h.can_change ? (
        <button type="button" className="row-title row-open" onClick={onEdit}>
          {h.name}
        </button>
      ) : (
        <span className="row-title">{h.name}</span>
      )}
      <span className="row-sub">{`${h.host} · ${eventsWords(h.events, types)}`}</span>
      <span className="row-state">{!h.enabled ? <Word icon="pause">Paused</Word> : week.failed ? <Word>{`${week.failed} failed`}</Word> : null}</span>
      <span className="row-fig">{`${week.delivered} in 7 d`}</span>
      <span className="row-age" />
    </Row>
  );
}

interface HeaderRow {
  key: number;
  name: string;
  value: string;
  secret: boolean;
  /** A secret saved on the server: its value never comes back; left empty it keeps it. */
  saved: boolean;
}

const savedHeaders = (h: Endpoint | null): HeaderRow[] =>
  (h?.headers ?? []).map((x, key) => ({
    key,
    name: String(x.name ?? ''),
    value: x.secret ? '' : String(x.value ?? ''),
    secret: !!x.secret,
    saved: !!x.secret && x.set === true,
  }));

const URL_OK = /^https?:\/\/\S+$/i;

export function EndpointDialog({ endpoint, onClose }: { endpoint: Endpoint | null; onClose: () => void }) {
  const types = useQuery(eventTypesQuery).data ?? [];
  const was = endpoint?.events?.length ? endpoint.events : ['*'];
  const [name, setName] = useState(endpoint?.name ?? '');
  const [url, setUrl] = useState(endpoint?.url ?? '');
  const [http, setHttp] = useState(!!endpoint?.allow_http);
  const [all, setAll] = useState(was.includes('*'));
  // the catalog's names picked, and any saved code it does not know
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [headers, setHeaders] = useState<HeaderRow[]>(() => savedHeaders(endpoint));
  const [busy, setBusy] = useState(false);
  // shown once: this dialog's state only, never the cache, a toast or the address
  const [secret, setSecret] = useState<string | null>(null);
  const [held, setHeld] = useState<Held | null>(null);

  const known = new Set([...types.map((t) => t.name), ...types.map((t) => `${t.group}.*`)]);
  const extra = was.filter((e) => e !== '*' && !known.has(e));
  const on = picked ?? new Set([...types.filter(holds(all ? [] : was)).map((t) => t.name), ...extra]);
  const events = all ? ['*'] : eventsValue(types, on, extra);
  const unnamed = headers.some((x) => !x.name.trim() && (x.value || x.saved));
  const ready = !!name.trim() && URL_OK.test(url.trim()) && !!events.length && !unnamed && !busy;

  const input = () => ({
    name: name.trim(),
    url: url.trim(),
    events,
    allowHttp: http,
    headers: headers.filter((x) => x.name.trim() || x.value).map((x) => ({ name: x.name.trim(), ...(x.value ? { value: x.value } : {}), secret: x.secret })),
  });

  const save = async (credentials?: 'keep' | 'clear') => {
    setBusy(true);
    if (endpoint) {
      const r = await pressOut<{ held: Held | null }>('webhooks.updateEndpoint', { endpoint: endpoint.id, ...input(), ...(credentials ? { credentials } : {}) });
      setBusy(false);
      if (r?.out?.held) return setHeld(r.out.held);
      if (r) {
        if (credentials === 'clear') toast({ text: 'Saved. Type the secret headers again: it is paused until then.', kind: 'done' });
        onClose();
      }
      return;
    }
    const r = await pressOut<{ secret?: string; replayed?: boolean; approval?: boolean }>('webhooks.addEndpoint', input());
    setBusy(false);
    if (!r) return;
    if (r.out?.approval) toast({ text: 'Sent for approval', kind: 'done' });
    else if (r.out?.replayed || !r.out?.secret) toast({ text: 'Added. Its secret was lost on the way.', kind: 'failed' });
    else return setSecret(r.out.secret);
    onClose();
  };

  if (secret) {
    const copy = () =>
      void navigator.clipboard.writeText(secret).then(
        () => toast({ text: 'Secret copied', kind: 'done' }),
        () => toast({ text: "Couldn't copy", kind: 'failed' }),
      );
    return (
      <Dialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Signing secret"
        description="Shown once. Copy it now."
        actions={
          <>
            <Button onClick={copy}>Copy</Button>
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </>
        }
      >
        <code className="rr-secret">{secret}</code>
      </Dialog>
    );
  }

  if (held) {
    return (
      <Dialog
        open
        onOpenChange={(o) => !o && setHeld(null)}
        title="Send saved headers to the new address?"
        description={`${held.credentials.join(', ')} would go to ${held.to} instead of ${held.from}.`}
        alert
        actions={
          <>
            <Button variant="quiet" onClick={() => setHeld(null)}>
              Cancel
            </Button>
            <Button busy={busy} onClick={() => void save('clear')}>
              Clear them
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void save('keep')}>
              Send them
            </Button>
          </>
        }
      />
    );
  }

  const setHeader = (key: number, patch: Partial<HeaderRow>) => setHeaders((list) => list.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const tick = (names: string[], next: boolean) => {
    const set = new Set(on);
    for (const n of names) {
      if (next) set.add(n);
      else set.delete(n);
    }
    setPicked(set);
  };
  const areas = [
    ...AREAS.map(([key, label, groups]) => ({ key, label, items: types.filter((t) => groups.includes(t.group)) })),
    { key: 'other', label: 'Other', items: types.filter((t) => !AREAS.some(([, , g]) => g.includes(t.group))) },
  ].filter((a) => a.items.length || (a.key === 'other' && extra.length));

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={endpoint ? 'Edit outgoing webhook' : 'New outgoing webhook'}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
            {endpoint ? 'Save' : 'Add'}
          </Button>
        </>
      }
    >
      <div className="rr-form">
        <Field label="Name" value={name} onValueChange={setName} autoComplete="off" autoFocus />
        <Field
          label="Address"
          type="url"
          value={url}
          onValueChange={setUrl}
          placeholder="https://"
          autoComplete="off"
          error={url.trim() && !URL_OK.test(url.trim()) ? 'Not an address' : undefined}
        />
        {/^http:/i.test(url.trim()) && <Checkbox label="Allow plain http" checked={http} onCheckedChange={setHttp} />}
        <Segmented
          label="Events"
          options={[
            { value: 'all', label: 'All events' },
            { value: 'some', label: 'Chosen' },
          ]}
          value={all ? 'all' : 'some'}
          onValueChange={(v) => {
            setAll(v === 'all');
            setPicked(v === 'all' ? null : new Set());
          }}
        />
        {!all &&
          areas.map((a) => (
            <fieldset key={a.key} className="rr-list">
              <legend>{a.label}</legend>
              {a.items.map((t) => (
                <Checkbox key={t.name} label={t.label || t.name} checked={on.has(t.name)} onCheckedChange={(c) => tick([t.name], c)} />
              ))}
              {a.key === 'other' && extra.map((e) => <Checkbox key={e} label={e} checked={on.has(e)} onCheckedChange={(c) => tick([e], c)} />)}
            </fieldset>
          ))}
        <fieldset className="rr-list">
          <legend>
            Headers
            {headers.length < MAX_HEADERS && (
              <Button variant="quiet" onClick={() => setHeaders((list) => [...list, { key: Math.max(-1, ...list.map((x) => x.key)) + 1, name: '', value: '', secret: false, saved: false }])}>
                Add header
              </Button>
            )}
          </legend>
          {headers.map((x, i) => (
            <div key={x.key} className="rr-line">
              <Field label={`Header ${i + 1} name`} labelHidden value={x.name} onValueChange={(v) => setHeader(x.key, { name: v })} placeholder="Name" autoComplete="off" />
              <Field
                label={`Header ${i + 1} value`}
                labelHidden
                type={x.secret ? 'password' : 'text'}
                value={x.value}
                onValueChange={(v) => setHeader(x.key, { value: v })}
                placeholder={x.saved ? 'Saved' : 'Value'}
                autoComplete="new-password"
              />
              <Checkbox label="Secret" checked={x.secret} onCheckedChange={(c) => setHeader(x.key, { secret: c, saved: c && x.saved })} />
              <IconButton icon="close" label={`Remove header ${i + 1}`} size="sm" quiet onClick={() => setHeaders((list) => list.filter((y) => y.key !== x.key))} />
            </div>
          ))}
        </fieldset>
      </div>
    </Dialog>
  );
}
