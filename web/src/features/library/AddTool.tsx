import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { WebsiteTool } from '@/api/schema';
import { meQuery } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field, Search } from '@/kit/Field';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { newId } from '@/lib/ids';
import { Foot, press, ToolGlyph, trimmed, useGo } from './parts';
import { ConnectMailboxDialog, MailingAddressDialog, NewAddressDialog, RequestNumberDialog, WebhookDialog } from './ToolDialogs';
import { addApiTool, catalogQuery, probeMcp, type CatalogService, type LoginAdd, type McpProbe } from './queries';
import { useAdmin } from './ToolShell';

/* Find or connect (the plan's catalog-first Add): a service from the catalog, then one of its ways in (Sign in with
   the vendor, Paste a key, Sign in on the website); any other tool by its kind, one click away (the old app's
   tools-api, tools-mcp and tools-website dialogs). Secrets typed here are only sent: nothing typed comes back. */

export type Adding = 'api' | 'mcp' | 'website' | 'webhook' | 'mailbox' | 'tico' | 'number' | 'address';

/** The kinds, in the mock's order; each with the glyph its tool shows. */
const ADD: Array<[Adding, string, string]> = [
  ['api', 'API', 'api'],
  ['mcp', 'MCP server', 'mcp'],
  ['website', 'Website', 'website'],
  ['webhook', 'Webhook', 'webhook'],
  ['mailbox', 'Email', 'mailbox'],
  ['tico', 'Tico address', 'mailbox'],
  ['number', 'Phone number', 'number'],
  ['address', 'Mailing address', 'address'],
];

/** What only an owner or admin adds, as in the old app: a company mailbox, a paid number, a paid address. */
const ADMIN_ONLY: Adding[] = ['mailbox', 'number', 'address'];

type Way = 'mcp' | 'key' | 'site';

/** Find or connect: search the catalog and pick a service, or add a tool by its kind. `vendor` starts on that service's
    ways (a vendor's Add connection): what it offers comes from the catalog alone, never from the vendor's tools. */
export function ConnectDialog({ vendor, onKind, onClose }: { vendor?: string; onKind: (a: Adding) => void; onClose: () => void }) {
  const read = useQuery(catalogQuery);
  const catalog = read.data ?? [];
  const admin = useAdmin();
  const [words, setWords] = useState('');
  const [chosen, setChosen] = useState(vendor ?? '');
  const [way, setWay] = useState<Way | null>(null);
  const service = catalog.find((s) => s.id === chosen);
  if (service && way === 'mcp') return <AddMcp onClose={onClose} start={{ url: service.mcp_url, name: service.name }} />;
  if (service && way === 'site') return <AddWebsite onClose={onClose} site={service.site} />;
  if (service && way === 'key') return <PasteKey service={service} onClose={onClose} />;
  if (service) return <Ways service={service} onWay={setWay} onBack={vendor ? undefined : () => setChosen('')} onClose={onClose} />;
  const w = words.trim().toLowerCase();
  const found = catalog.filter((s) => !w || s.name.toLowerCase().includes(w) || s.id.includes(w));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Find or connect" className="connect-find">
      <Search label="Find a service" value={words} onValueChange={setWords} placeholder="Find a service" wide autoFocus />
      <div className="connect-list" aria-label="Services">
        {found.map((s) => (
          <button key={s.id} type="button" className="row connect-row" onClick={() => setChosen(s.id)}>
            <span className="row-title">{s.name}</span>
          </button>
        ))}
        {read.isSuccess && !found.length && <p className="connect-none">Not in the catalog</p>}
      </div>
      <div className="connect-kinds" role="group" aria-label="Add by kind">
        {ADD.filter(([k]) => admin || !ADMIN_ONLY.includes(k)).map(([k, label, glyph]) => (
          <button key={k} type="button" className="btn connect-kind" onClick={() => onKind(k)}>
            <ToolGlyph kind={glyph} />
            {label}
          </button>
        ))}
      </div>
    </Dialog>
  );
}

/** A catalog service's ways in: its MCP server's sign-in where it has one, a key (for whoever manages keys), and its
    website. */
function Ways({ service, onWay, onBack, onClose }: { service: CatalogService; onWay: (w: Way) => void; onBack?: () => void; onClose: () => void }) {
  const admin = useAdmin();
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={service.name} actions={onBack && <Button onClick={onBack}>Back</Button>}>
      <div className="connect-ways">
        {service.mcp_url && (
          <Button icon="link" onClick={() => onWay('mcp')}>
            {`Sign in with ${service.name}`}
          </Button>
        )}
        {admin && (
          <Button icon="keys" onClick={() => onWay('key')}>
            Paste a key
          </Button>
        )}
        {service.site && (
          <Button icon="globe" onClick={() => onWay('site')}>
            Sign in on the website
          </Button>
        )}
      </div>
    </Dialog>
  );
}

/** A key for a catalog service: a new connection made from the catalog's entry with its key, in one write the server
    takes once (one request key per key typed), so a refused key makes no connection and sending again makes no second
    one. A connection that already has a key is replaced from its own page. The key is only sent, never read back. */
function PasteKey({ service, onClose }: { service: CatalogService; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [attempt, setAttempt] = useState(newId);
  const [busy, setBusy] = useState(false);
  const type = (v: string) => {
    setValue(v);
    setAttempt(newId());
  };
  const save = async () => {
    setBusy(true);
    try {
      const { id } = await addApiTool({ name: service.name, service: service.id, auth_scheme: service.auth_scheme, key: value.trim() }, attempt);
      onClose();
      void press('tools.open', { tool: `api:${id}` });
    } catch (e) {
      toast({ text: e instanceof Error && e.message ? e.message : 'Not added', kind: 'failed' });
    }
    setBusy(false);
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Paste a ${service.name} key`}
      className="connect"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={!!value.trim()} onGo={() => void save()} />}
    >
      <Field label="Key" type="password" value={value} onValueChange={type} autoComplete="off" autoFocus wide />
    </Dialog>
  );
}

export function AddDialog({ adding, onClose }: { adding: Adding; onClose: () => void }) {
  if (adding === 'api') return <AddApi onClose={onClose} />;
  if (adding === 'mcp') return <AddMcp onClose={onClose} />;
  if (adding === 'webhook') return <WebhookDialog onClose={onClose} />;
  if (adding === 'mailbox') return <ConnectMailboxDialog onClose={onClose} />;
  if (adding === 'tico') return <NewAddressDialog onClose={onClose} />;
  if (adding === 'number') return <RequestNumberDialog onClose={onClose} />;
  if (adding === 'address') return <MailingAddressDialog onClose={onClose} />;
  return <AddWebsite onClose={onClose} />;
}

/* ---------------------------------------------------------------- an API */

export type Scheme = 'bearer' | 'basic_user' | 'basic_password' | 'header' | 'query';
export const SCHEMES: Array<{ value: Scheme; label: string }> = [
  { value: 'bearer', label: 'Bearer token' },
  { value: 'basic_user', label: 'Basic, key as user' },
  { value: 'basic_password', label: 'Basic, key as password' },
  { value: 'header', label: 'Header' },
  { value: 'query', label: 'Query parameter' },
];

function AddApi({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [vendor, setVendor] = useState('');
  const [base, setBase] = useState('');
  const [scheme, setScheme] = useState<Scheme>('bearer');
  const [param, setParam] = useState('');
  const [check, setCheck] = useState('');
  const [busy, go] = useGo(onClose);
  const named = scheme === 'header' || scheme === 'query';
  const ready = !!name.trim() && (!named || /^[\w-]+$/.test(param.trim()));
  const add = () =>
    void go('tools.addApi', {
      name: name.trim(),
      service: trimmed(vendor.toLowerCase()),
      base_url: trimmed(base),
      auth_scheme: named ? `${scheme}:${param.trim()}` : scheme,
      check_path: trimmed(check),
    });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Add API" actions={<Foot onClose={onClose} label="Add" busy={busy} ready={ready} onGo={add} />}>
      <Field label="Name" value={name} onValueChange={setName} placeholder="Acme CRM" autoFocus />
      <Field label="Vendor" value={vendor} onValueChange={setVendor} placeholder="acme" />
      <Field label="Base URL" type="url" value={base} onValueChange={setBase} placeholder="https://api.acme.com" />
      <Select label="Key goes in" options={SCHEMES} value={scheme} onValueChange={setScheme} />
      {named && <Field label={scheme === 'header' ? 'Header name' : 'Parameter name'} value={param} onValueChange={setParam} placeholder="X-Api-Key" />}
      <Field label="Check path" value={check} onValueChange={setCheck} placeholder="/v1/me" />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- an MCP server */

/** A name from the address: `https://mcp.acme.com/sse` is Acme. */
export function nameFromUrl(url: string) {
  try {
    const parts = new URL(url).hostname.split('.').filter((p) => !['www', 'api', 'mcp', 'app'].includes(p));
    const word = (parts.length > 1 ? parts[parts.length - 2] : parts[0]) ?? '';
    return word.charAt(0).toUpperCase() + word.slice(1);
  } catch {
    return '';
  }
}

function AddMcp({ onClose, start }: { onClose: () => void; start?: { url: string; name: string } }) {
  const [url, setUrl] = useState(start?.url ?? '');
  const [name, setName] = useState(start?.name ?? '');
  const [found, setFound] = useState<McpProbe | null>(null);
  const [probing, setProbing] = useState(false);
  const [way, setWay] = useState<'oauth' | 'token'>('token');
  const [clientId, setClientId] = useState('');
  const [secret, setSecret] = useState('');
  const [headerKind, setHeaderKind] = useState<'bearer' | 'other'>('bearer');
  const [header, setHeader] = useState('');
  const [token, setToken] = useState('');
  const [busy, go] = useGo(onClose);
  const changeUrl = (v: string) => {
    setUrl(v);
    setFound(null);
  };
  const next = async () => {
    setProbing(true);
    try {
      const f = await probeMcp(url.trim());
      setFound(f);
      setWay(f.oauth ? 'oauth' : 'token');
      if (!name.trim()) setName(f.server || nameFromUrl(url.trim()));
    } catch {
      setFound(null);
      toast({ text: 'Could not reach it', kind: 'failed' });
    }
    setProbing(false);
  };
  const oauth = way === 'oauth';
  const needsClient = !!found?.oauth && !found.dcr;
  const ready = !found
    ? /^https?:\/\/\S+$/i.test(url.trim())
    : oauth
      ? !needsClient || !!clientId.trim()
      : !!token && (headerKind === 'bearer' || /^[\w-]+$/.test(header.trim()));
  const add = () => {
    const base = {
      name: name.trim() || nameFromUrl(url.trim()) || 'MCP server',
      url: url.trim(),
    };
    void go(
      'tools.addMcpWithKey',
      oauth
        ? {
            ...base,
            auth: 'oauth',
            client_id: trimmed(clientId),
            client_secret: secret || undefined,
          }
        : {
            ...base,
            auth: 'token',
            token,
            header: headerKind === 'other' ? header.trim() : undefined,
          },
    );
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add MCP server"
      actions={
        <Foot
          onClose={onClose}
          label={!found ? 'Next' : oauth ? 'Sign in' : 'Add'}
          busy={busy || probing}
          ready={ready}
          onGo={() => (found ? add() : void next())}
        />
      }
    >
      <Field label="Address" type="url" value={url} onValueChange={changeUrl} placeholder="https://mcp.acme.com" autoFocus />
      <Field label="Name" value={name} onValueChange={setName} placeholder="Acme" />
      {found?.oauth && (
        <Segmented
          label="Connect with"
          options={[
            { value: 'oauth', label: 'Sign in' },
            { value: 'token', label: 'Token' },
          ]}
          value={way}
          onValueChange={setWay}
        />
      )}
      {found && oauth && needsClient && (
        <>
          <Field label="Client id" value={clientId} onValueChange={setClientId} autoComplete="off" />
          <Field label="Client secret" type="password" value={secret} onValueChange={setSecret} autoComplete="off" />
        </>
      )}
      {found && !oauth && (
        <>
          <Select
            label="Header"
            options={[
              { value: 'bearer', label: 'Authorization: Bearer' },
              { value: 'other', label: 'Other header' },
            ]}
            value={headerKind}
            onValueChange={setHeaderKind}
          />
          {headerKind === 'other' && <Field label="Header name" value={header} onValueChange={setHeader} placeholder="X-Api-Key" />}
          <Field label="Token" type="password" value={token} onValueChange={setToken} autoComplete="off" />
        </>
      )}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a website and its logins */

interface Login {
  shared: boolean;
  username: string;
  password: string;
  totp: string;
}
const NO_LOGIN: Login = { shared: false, username: '', password: '', totp: '' };

/** The login's fields. An owner may make it shared, for every agent; otherwise it is the person's own. */
function LoginFields({ login, onChange }: { login: Login; onChange: (l: Login) => void }) {
  const me = useQuery(meQuery).data;
  const set = (change: Partial<Login>) => onChange({ ...login, ...change });
  return (
    <>
      {me?.role === 'owner' && (
        <Segmented
          label="Account"
          options={[
            { value: 'shared', label: 'Shared' },
            { value: 'own', label: 'Own' },
          ]}
          value={login.shared ? 'shared' : 'own'}
          onValueChange={(v) => set({ shared: v === 'shared' })}
        />
      )}
      <Field label="Username" value={login.username} onValueChange={(v) => set({ username: v })} autoComplete="off" />
      <Field label="Password" type="password" value={login.password} onValueChange={(v) => set({ password: v })} autoComplete="new-password" />
      <Field
        label="2FA key (optional)"
        type="password"
        value={login.totp}
        onValueChange={(v) => set({ totp: v })}
        autoComplete="off"
        placeholder="Setup key or otpauth:// link"
      />
    </>
  );
}

/** No password is fine: an account a person signs in to by hand (Google, an emailed code, a passkey). */
function useLogin() {
  const me = useQuery(meQuery).data;
  const [login, setLogin] = useState<Login | null>(null);
  const l = login ?? { ...NO_LOGIN, shared: me?.role === 'owner' };
  const body = (): LoginAdd => ({
    username: trimmed(l.username),
    password: l.password || undefined,
    totp: trimmed(l.totp),
    ...(l.shared ? {} : { holder: me?.id }),
  });
  return [l, setLogin, body] as const;
}

function AddWebsite({ onClose, site: start = '' }: { onClose: () => void; site?: string }) {
  const [site, setSite] = useState(start);
  const [login, setLogin, body] = useLogin();
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add website"
      actions={
        <Foot
          onClose={onClose}
          label="Add"
          busy={busy}
          ready={!!site.trim()}
          onGo={() =>
            void go('tools.addWebsiteWithLogin', {
              site: site.trim(),
              ...body(),
            })
          }
        />
      }
    >
      <Field label="Site" value={site} onValueChange={setSite} placeholder="app.example.com" autoComplete="off" autoFocus />
      <LoginFields login={login} onChange={setLogin} />
    </Dialog>
  );
}

export function AddAccountDialog({ tool, onClose }: { tool: string; onClose: () => void }) {
  const [login, setLogin, body] = useLogin();
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add account"
      actions={<Foot onClose={onClose} label="Add" busy={busy} ready onGo={() => void go('tools.addAccount', { tool, ...body() })} />}
    >
      <LoginFields login={login} onChange={setLogin} />
    </Dialog>
  );
}

type Account = WebsiteTool['accounts'][number];

/** Change a login. Password and key start empty and are sent only when typed: a stored one is never read back. */
export function ReplaceLoginDialog({ tool, account, onClose }: { tool: string; account: Account; onClose: () => void }) {
  const [username, setUsername] = useState(account.username ?? '');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [dropTotp, setDropTotp] = useState(false);
  const [busy, go] = useGo(onClose);
  const user = username.trim() !== (account.username ?? '') ? username.trim() : undefined;
  const ready = user !== undefined || !!password || !!totp.trim() || dropTotp;
  const save = () =>
    void go(
      'tools.replaceLogin',
      {
        tool,
        account: account.id,
        username: user,
        password: password || undefined,
        totp: dropTotp ? undefined : trimmed(totp),
        remove_totp: dropTotp || undefined,
      },
      'Not saved',
    );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Replace login"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={ready} onGo={save} />}
    >
      <Field label="Username" value={username} onValueChange={setUsername} autoComplete="off" />
      <Field
        label="Password"
        type="password"
        value={password}
        onValueChange={setPassword}
        autoComplete="new-password"
        placeholder={account.has_password === false ? undefined : 'Unchanged'}
      />
      {!dropTotp && (
        <Field
          label="2FA key (optional)"
          type="password"
          value={totp}
          onValueChange={setTotp}
          autoComplete="off"
          placeholder={account.has_totp ? 'Unchanged' : 'Setup key or otpauth:// link'}
        />
      )}
      {account.has_totp && <Checkbox label="Remove 2FA" checked={dropTotp} onCheckedChange={setDropTotp} />}
    </Dialog>
  );
}
