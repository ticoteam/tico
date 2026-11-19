import { useEffect, useRef, useState } from 'react';
import { refusal, run } from '@/actions';
import type { ApiTool, ApiToolSettings } from '@/api/schema';
import { Button } from '@/kit/Button';
import { DatePick } from '@/kit/DatePick';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { LabelValue } from '@/kit/LabelValue';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { Foot, SCHEMES, trimmed, useGo, type Scheme } from './AddTool';
import { More, press } from './parts';
import { readMessage, type Inbox, type PasteItem } from './queries';

/* The dialogs an open tool raises (the old app's tools-api, tools-website and inboxes dialogs). A secret shown here
   lives in this dialog's state only: never the query cache, a toast, a log line or the address. Closing drops it. */

/* ---------------------------------------------------------------- an API tool */

const schemeOf = (auth: string): [Scheme, string] => {
  const [k, param = ''] = auth.split(':');
  return SCHEMES.some((s) => s.value === k) ? [k as Scheme, param] : ['bearer', ''];
};

/** Its name, vendor, address, where the key goes and what checks it. Only what changed is sent. */
export function EditApiDialog({ tool, settings, onClose }: { tool: string; settings: ApiToolSettings; onClose: () => void }) {
  const [was, wasParam] = schemeOf(settings.auth_scheme);
  const [name, setName] = useState(settings.name);
  const [vendor, setVendor] = useState(settings.service);
  const [base, setBase] = useState(settings.base_url);
  const [scheme, setScheme] = useState<Scheme>(was);
  const [param, setParam] = useState(wasParam);
  const [check, setCheck] = useState(settings.check_path);
  const [busy, go] = useGo(onClose);
  const named = scheme === 'header' || scheme === 'query';
  const auth = named ? `${scheme}:${param.trim()}` : scheme;
  const change = {
    name: name.trim() !== settings.name ? name.trim() : undefined,
    service: vendor.trim() !== settings.service ? vendor.trim().toLowerCase() : undefined,
    base_url: base.trim() !== settings.base_url ? base.trim() : undefined,
    auth_scheme: auth !== settings.auth_scheme ? auth : undefined,
    check_path: check.trim() !== settings.check_path ? check.trim() : undefined,
  };
  const ready = !!name.trim() && (!named || /^[\w-]+$/.test(param.trim())) && Object.values(change).some((v) => v !== undefined);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Edit API"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={ready} onGo={() => void go('tools.editApi', { tool, ...change }, 'Not saved')} />}
    >
      <Field label="Name" value={name} onValueChange={setName} autoFocus />
      <Field label="Vendor" value={vendor} onValueChange={setVendor} placeholder="acme" />
      <Field label="Base URL" type="url" value={base} onValueChange={setBase} placeholder="https://api.acme.com" />
      <Select label="Key goes in" options={SCHEMES} value={scheme} onValueChange={setScheme} />
      {named && <Field label={scheme === 'header' ? 'Header name' : 'Parameter name'} value={param} onValueChange={setParam} placeholder="X-Api-Key" />}
      <Field label="Check path" value={check} onValueChange={setCheck} placeholder="/v1/me" />
    </Dialog>
  );
}

type Setting = NonNullable<ApiTool['settings']>[number];

/** A run setting: a plain value every call carries (an account id, a region). Never a secret: a key holds those. */
export function SettingDialog({ tool, setting, onClose }: { tool: string; setting: Setting | null; onClose: () => void }) {
  const [name, setName] = useState(setting?.name ?? '');
  const [value, setValue] = useState(setting?.value ?? '');
  const [busy, go] = useGo(onClose);
  const upper = name.trim().toUpperCase();
  const ready = /^[A-Z_][A-Z0-9_]*$/.test(upper) && !!value.trim() && (upper !== setting?.name || value.trim() !== setting.value);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={setting ? setting.name : 'Add run setting'}
      actions={
        <>
          {setting && (
            <>
              <Button variant="quiet" disabled={busy} onClick={() => void go('tools.removeSetting', { tool, setting: setting.id }, 'Not removed')}>
                Remove
              </Button>
              <span className="grow" />
            </>
          )}
          <Foot
            onClose={onClose}
            label="Save"
            busy={busy}
            ready={ready}
            onGo={() => void go('tools.saveSetting', { tool, setting: setting?.id, name: upper, value: value.trim() }, 'Not saved')}
          />
        </>
      }
    >
      <Field label="Name" value={name} onValueChange={setName} placeholder="ACCOUNT_ID" autoComplete="off" autoFocus={!setting} />
      <Field label="Value" value={value} onValueChange={setValue} autoComplete="off" autoFocus={!!setting} />
    </Dialog>
  );
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const fromIso = (v: string | null | undefined) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v ?? '');
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
};

type Key = ApiTool['keys'][number];

/** When a key stops working, so Tico says so ahead of time. A date the vendor reports is the vendor's. */
export function ExpiryDialog({ tool, keyRow, onClose }: { tool: string; keyRow: Key; onClose: () => void }) {
  const [day, setDay] = useState<Date | null>(fromIso(keyRow.expires_on));
  const [busy, go] = useGo(onClose);
  const set = (expires_on: string) => void go('tools.setKeyExpiry', { tool, key: keyRow.id, expires_on }, 'Not saved');
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`When ${keyRow.name} ends`}
      actions={
        <>
          {keyRow.expires_on && (
            <>
              <Button variant="quiet" disabled={busy} onClick={() => set('')}>
                Never ends
              </Button>
              <span className="grow" />
            </>
          )}
          <Foot onClose={onClose} label="Save" busy={busy} ready={!!day && iso(day) !== keyRow.expires_on} onGo={() => day && set(iso(day))} />
        </>
      }
    >
      <DatePick label="Ends on" value={day} onValueChange={setDay} withTime={false} />
    </Dialog>
  );
}

/** How long a shown secret stays on screen. */
export const SHOWN_MS = 60_000;

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast({ text: 'Copied' });
  } catch {
    toast({ text: 'Not copied', kind: 'failed' });
  }
}

/** A secret in this dialog's state for a minute, with Copy. Nothing is read until Show is pressed. */
export function Secret({ label, value, onHide }: { label: string; value: string; onHide: () => void }) {
  useEffect(() => {
    const t = setTimeout(onHide, SHOWN_MS);
    return () => clearTimeout(t);
  }, [onHide]);
  return (
    <div className="connect-cmd" role="group" aria-label={label}>
      <code>{value}</code>
      <IconButton icon="copy" label="Copy" size="sm" onClick={() => void copy(value)} />
    </div>
  );
}

/** A stored key or website password, read once for the person who asked (the server logs who and when). */
export function RevealDialog({ credential, title, onClose }: { credential: string; title: string; onClose: () => void }) {
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const show = async () => {
    setBusy(true);
    const r = await run('tools.reveal', { credential }, { via: 'click' });
    setBusy(false);
    if (r.ok && typeof r.out === 'string') setValue(r.out);
    else toast(refusal(r, 'Not shown', 'You cannot see this'));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description={value === null ? 'Seeing it is logged.' : undefined}
      className="connect"
      actions={
        value === null ? (
          <Foot onClose={onClose} label="Show" busy={busy} ready onGo={() => void show()} />
        ) : (
          <Button onClick={onClose}>Done</Button>
        )
      }
    >
      {value !== null && <Secret label={title} value={value} onHide={onClose} />}
    </Dialog>
  );
}

/** Many keys at once (a .env, a vendor's settings page): first what it holds, by name, then stored in one step. */
export function PasteDialog({ tool, onClose }: { tool: string; onClose: () => void }) {
  const [text, setText] = useState('');
  const [found, setFound] = useState<PasteItem[] | null>(null);
  const [busy, go] = useGo(onClose);
  const [checking, setChecking] = useState(false);
  const check = async () => {
    setChecking(true);
    const r = await run('tools.checkPaste', { text }, { via: 'click' });
    setChecking(false);
    const items = (r.out as { items?: PasteItem[] } | undefined)?.items;
    if (r.ok && items) setFound(items);
    else toast(refusal(r, 'Not read'));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add many keys"
      className="connect"
      actions={
        <Foot
          onClose={onClose}
          label={found ? 'Add' : 'Next'}
          busy={busy || checking}
          ready={!!text.trim() && (!found || found.length > 0)}
          onGo={() => (found ? void go('tools.pasteKeys', { tool, text }, 'Not added') : void check())}
        />
      }
    >
      {!found ? (
        <Field label="Paste" area value={text} onValueChange={setText} placeholder="ACME_KEY=..." autoComplete="off" autoFocus />
      ) : found.length ? (
        <div className="connect-dev">
          {found.map((i) => (
            <LabelValue key={i.name} label={i.name}>
              <code className="fact-code">{i.preview}</code> {i.role === 'secret' ? 'Key' : 'Setting'}
            </LabelValue>
          ))}
        </div>
      ) : (
        <Empty title="No keys found" />
      )}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a website */

/** The sites one login covers, one to a line. */
export function SitesDialog({ tool, sites, onClose }: { tool: string; sites: string[]; onClose: () => void }) {
  const [text, setText] = useState(sites.join('\n'));
  const [busy, go] = useGo(onClose);
  const list = [...new Set(text.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean))];
  const ready = list.length > 0 && list.join('\n') !== sites.join('\n');
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Sites"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={ready} onGo={() => void go('tools.editWebsite', { tool, sites: list }, 'Not saved')} />}
    >
      <Field label="Sites" labelHidden area value={text} onValueChange={setText} placeholder="app.example.com" autoComplete="off" autoFocus />
    </Dialog>
  );
}

const spaced = (code: string) => (/^\d{6}$/.test(code) ? `${code.slice(0, 3)} ${code.slice(3)}` : code);

/** The code the account's authenticator shows now, read again as each one runs out. */
export function CodeDialog({ tool, account, onClose }: { tool: string; account: string; onClose: () => void }) {
  const [code, setCode] = useState<{ code: string; left: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    let t: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      const r = await run('tools.signInCode', { tool, account }, { via: 'click' });
      if (!live.current) return;
      const out = r.out as { code?: string; expires_in?: number } | undefined;
      if (!r.ok || !out?.code) return setFailed(true);
      setCode({ code: out.code, left: out.expires_in ?? 30 });
      t = setTimeout(() => void read(), Math.max(1, out.expires_in ?? 30) * 1000);
    };
    void read();
    return () => {
      live.current = false;
      clearTimeout(t);
    };
  }, [tool, account]);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Sign-in code" className="connect" actions={<Button onClick={onClose}>Done</Button>}>
      {failed ? <Empty title="No code" icon="alert" /> : code && <Secret label="Sign-in code" value={spaced(code.code)} onHide={onClose} />}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a Tico address or number */

/** One message that came in, as plain text (it is untrusted), a page at a time. Read fresh each time it opens. One that
    carries a code offers Reveal code: the code is read on that press into this dialog's state only (code mode). */
export function MessageDialog({ message, title, hasCode = false, onClose }: { message: string; title: string; hasCode?: boolean; onClose: () => void }) {
  const [code, setCode] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const reveal = async () => {
    setRevealing(true);
    const r = await run('tools.revealCode', { message }, { via: 'click' });
    setRevealing(false);
    if (r.ok && typeof r.out === 'string') setCode(r.out);
    else toast(refusal(r, 'Not shown', 'You cannot see this'));
  };
  const [pages, setPages] = useState<string[]>([]);
  const [next, setNext] = useState<number | null>(0);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const more = async (offset: number) => {
    setBusy(true);
    try {
      const r = await readMessage(message, offset);
      setPages((p) => [...p, r.text]);
      setNext(r.next_offset ?? null);
    } catch {
      setFailed(true);
    }
    setBusy(false);
  };
  const first = useRef(false);
  useEffect(() => {
    if (first.current) return;
    first.current = true;
    void more(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      className="lib fv-dialog"
      actions={
        <>
          {hasCode && code === null && (
            <Button busy={revealing} disabled={revealing} onClick={() => void reveal()}>
              Reveal code
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {code !== null && (code ? <Secret label="Code" value={code} onHide={() => setCode(null)} /> : <Empty title="No code found" />)}
      <div className="fv-view">
        {failed ? (
          <Empty title="This message did not load" icon="alert" />
        ) : (
          <div className="fv-main">
            <pre className="fv-text">{pages.join('')}</pre>
            {next !== null && next > 0 && !busy && <More onClick={() => void more(next)}>Read more</More>}
          </div>
        )}
      </div>
    </Dialog>
  );
}

export function NewAddressDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New Tico address"
      actions={<Foot onClose={onClose} label="Make" busy={busy} ready={!!name.trim()} onGo={() => void go('tools.addTicoAddress', { name: name.trim() }, 'Not made')} />}
    >
      <Field label="Name" value={name} onValueChange={setName} placeholder="Account logins" autoFocus />
    </Dialog>
  );
}

export function RequestNumberDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('Mobile number');
  // the price comes back on Phone numbers' requests, which opens once asked
  const [busy, go] = useGo(() => {
    onClose();
    void press('tools.open', { tool: 'number' });
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Ask for a US mobile number"
      description="Monthly, texts only. A price comes back to accept first; nothing is bought now."
      actions={<Foot onClose={onClose} label="Ask" busy={busy} ready={!!name.trim()} onGo={() => void go('tools.requestNumber', { name: name.trim() }, 'Not asked')} />}
    >
      <Field label="Name" value={name} onValueChange={setName} autoFocus />
    </Dialog>
  );
}

/** Its name, and what depends on it (so stopping it asks for the address typed back). */
export function EditInboxDialog({ box, onClose }: { box: Inbox; onClose: () => void }) {
  const [name, setName] = useState(box.name);
  const [usedFor, setUsedFor] = useState(box.used_for ?? '');
  const [busy, go] = useGo(onClose);
  const ready = !!name.trim() && (name.trim() !== box.name || usedFor.trim() !== (box.used_for ?? ''));
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Edit"
      actions={
        <Foot
          onClose={onClose}
          label="Save"
          busy={busy}
          ready={ready}
          onGo={() => void go('tools.editInbox', { inbox: box.inbox_id, name: name.trim(), used_for: usedFor.trim() || null }, 'Not saved')}
        />
      }
    >
      <Field label="Name" value={name} onValueChange={setName} autoFocus />
      <Field label="Used for" area value={usedFor} onValueChange={setUsedFor} placeholder="Sign-in codes for the bank" />
    </Dialog>
  );
}

/** For good. When something depends on it, the address is typed back first. */
export function ReleaseDialog({ box, number, onClose }: { box: Inbox; number: boolean; onClose: () => void }) {
  const [typed, setTyped] = useState('');
  const [busy, go] = useGo(onClose);
  const asks = !!box.used_for;
  return (
    <Dialog
      open
      alert
      onOpenChange={(o) => !o && onClose()}
      title={`Stop using ${box.name}?`}
      description={number ? 'It is not renewed, and texts stop when the month ends.' : 'Mail to it stops coming in. This cannot be undone.'}
      actions={
        <Foot
          onClose={onClose}
          label="Stop using"
          busy={busy}
          ready={!asks || typed.trim() === box.address}
          onGo={() => void go('tools.releaseInbox', { inbox: box.inbox_id, confirm: asks ? typed.trim() : undefined }, 'Not stopped')}
        />
      }
    >
      {asks && <Field label={`Type ${box.address}`} value={typed} onValueChange={setTyped} autoComplete="off" autoFocus />}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a mailbox, a mailing address */

export function ConnectMailboxDialog({ onClose }: { onClose: () => void }) {
  const [address, setAddress] = useState('');
  const [shared, setShared] = useState(false);
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Connect mailbox"
      actions={
        <Foot
          onClose={onClose}
          label="Connect"
          busy={busy}
          ready={/^\S+@\S+\.\S+$/.test(address.trim())}
          onGo={() => void go('tools.connectMailbox', { address: address.trim(), shared }, 'Not connected')}
        />
      }
    >
      <Field label="Address" type="email" value={address} onValueChange={setAddress} placeholder="sales@example.com" autoComplete="off" autoFocus />
      <Switch label="Shared inbox" checked={shared} onCheckedChange={setShared} />
    </Dialog>
  );
}

const STATES = [
  { value: 'DE', label: 'Delaware' },
  { value: 'CA', label: 'California' },
  { value: 'TX', label: 'Texas' },
  { value: 'FL', label: 'Florida' },
  { value: 'NV', label: 'Nevada' },
] as const;
type State = (typeof STATES)[number]['value'];

export function MailingAddressDialog({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<'shared' | 'private'>('shared');
  const [state, setState] = useState<State>('DE');
  const [company, setCompany] = useState('');
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Get a mailing address"
      description={kind === 'shared' ? '$10 a month.' : 'From $25 a month.'}
      actions={
        <Foot
          onClose={onClose}
          label="Get address"
          busy={busy}
          ready={!!trimmed(company)}
          onGo={() => void go('tools.orderAddress', { kind, state, company_name: company.trim() }, 'Not asked')}
        />
      }
    >
      <Segmented
        label="Address"
        options={[
          { value: 'shared', label: 'Shared' },
          { value: 'private', label: 'Private' },
        ]}
        value={kind}
        onValueChange={setKind}
      />
      <Select label="State" options={[...STATES]} value={state} onValueChange={setState} />
      <Field label="Company name" value={company} onValueChange={setCompany} autoFocus />
    </Dialog>
  );
}
