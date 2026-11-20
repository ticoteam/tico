import { useQuery } from '@tanstack/react-query';
import { useState, type ReactElement } from 'react';
import type { WebsiteTool } from '@/api/schema';
import { meQuery } from '@/data';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Menu, MenuItem } from '@/kit/Menu';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Foot, ToolGlyph, trimmed, useGo } from './parts';
import { ConnectMailboxDialog, MailingAddressDialog, NewAddressDialog, RequestNumberDialog, WebhookDialog } from './ToolDialogs';
import { probeMcp, type LoginAdd, type McpProbe } from './queries';
import { useAdmin } from './ToolShell';

/* Add tool (tools.js addMenu; the old app's tools-api, tools-mcp and tools-website dialogs): an API, an MCP server,
   a website, an outgoing webhook, or mail. Secrets typed here are only sent: nothing typed comes back to the page. */

export type Adding = 'api' | 'mcp' | 'website' | 'webhook' | 'mailbox' | 'tico' | 'number' | 'address';

/** The mock's Add menu, in its order; each with the glyph its tool shows. */
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

export function AddMenu({ trigger, onPick }: { trigger: ReactElement; onPick: (a: Adding) => void }) {
  return (
    <Menu trigger={trigger}>
      <AddItems onPick={onPick} />
    </Menu>
  );
}

/** What only an owner or admin adds, as in the old app: a company mailbox, a paid number, a paid address. */
const ADMIN_ONLY: Adding[] = ['mailbox', 'number', 'address'];

/** The Add menu's rows, for a menu that holds them as a second level (a phone's ⋯). */
export function AddItems({ onPick }: { onPick: (a: Adding) => void }) {
  const admin = useAdmin();
  return ADD.filter(([k]) => admin || !ADMIN_ONLY.includes(k)).map(([k, label, glyph]) => (
    <MenuItem key={k} label={label} lead={<ToolGlyph kind={glyph} />} onSelect={() => onPick(k)} />
  ));
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

function AddMcp({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
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
    ...(l.shared ? { all_bots: true } : { holder: me?.id }),
  });
  return [l, setLogin, body] as const;
}

function AddWebsite({ onClose }: { onClose: () => void }) {
  const [site, setSite] = useState('');
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
