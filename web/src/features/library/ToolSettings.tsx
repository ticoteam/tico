import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { MailboxDetail } from '@/api/schema';
import { useActors } from '@/data';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { press, when, Foot, useGo } from './parts';
import { Confirm } from './ToolDialogs';
import {
  agentSendingQuery,
  googleQuery,
  inboxesQuery,
  intakeQuery,
  legalMailQuery,
  letterGrantsQuery,
  lettersSettingsQuery,
  myGoogleQuery,
  numberRequestsQuery,
  postalAddressesQuery,
  sendingQuery,
  sharedInbox,
  type MailMay,
  type NumberRequest,
  type SendPolicy,
  type SharedInbox,
} from './queries';
import { AddSomeone, ReadFault, toolHref, useAdmin, WhoRow } from './ToolShell';

/* The company's settings for Email, Texts and Letters (the old Tools › Settings, ui/app/inboxes-*.ts), as the facts
   and controls of the tool they belong to: Google is how Email connects and what agents may do and send is what it
   can do; number requests are how a number connects; the letters' rules are what the mailing address can do. A
   secret typed here (a Workspace key, a connection code) or shown here (a setup command) lives in component state. */

export const words = (list: readonly (readonly [string, string])[]) => list.map(([value, label]) => ({ value, label }));
const wordOf = (list: readonly (readonly [string, string])[], v: string | null | undefined) => list.find(([k]) => k === v)?.[1] ?? '';

/* ---------------------------------------------------------------- Email: what agents may do in Google */

const MAY: [keyof MailMay, string][] = [
  ['organize_mail', 'Organize mail'],
  ['send_mail', 'Send mail'],
  ['change_calendar', 'Change the calendar'],
];

/** Reading is always on; the rest an admin turns on or off for the whole company. Others see the mailbox's own. */
export function MayFacts({ may }: { may?: MailboxDetail['may'] }) {
  const google = useQuery(googleQuery);
  const admin = useAdmin();
  const now = google.data?.permissions;
  const editable = admin && !!now;
  const own = may && { organize_mail: !!may.organize, send_mail: !!may.send, change_calendar: !!may.change_calendar };
  const shown = editable ? now : (own ?? now);
  if (!shown) return null;
  return (
    <Facts>
      <LabelValue label="Read mail">Yes</LabelValue>
      {MAY.map(([k, label]) => (
        <LabelValue key={k} label={label}>
          {editable ? (
            <Switch label={label} labelHidden checked={shown[k]} onCheckedChange={(on) => void press('tools.setMailMay', { ...now, [k]: on }, 'Not saved')} />
          ) : shown[k] ? (
            'Yes'
          ) : (
            'No'
          )}
        </LabelValue>
      ))}
    </Facts>
  );
}

/* ---------------------------------------------------------------- Email: the Google connection */

const KEY_STATE: Record<string, string> = { connected: 'Connected', pending: 'Waits for Google', failing: 'Failing' };
const MY_STATE: Record<string, string> = { connected: 'Connected', pending: 'Connecting', failed: 'Failed', none: 'Not connected' };

/** How Email connects: the Workspace key (company) and the person's own Google sign-in; changes in its ⋯. */
export function useGoogle() {
  const google = useQuery(googleQuery);
  const mine = useQuery(myGoogleQuery);
  const [dialog, setDialog] = useState<'setup' | 'key' | 'disconnect' | null>(null);
  const g = google.data;
  if (!g) return { facts: null, actions: null };
  const key = g.pending ?? g.workspace;
  const me = mine.data;
  const meOn = me && me.status !== 'none' && me.status !== 'failed';
  const facts = (
    <Facts>
      <LabelValue label="Workspace">{key ? `${key.domain}, ${(KEY_STATE[key.status] ?? key.status).toLowerCase()}` : 'Not connected'}</LabelValue>
      {g.pending && (
        <LabelValue label="Allow in Google">
          <a href={g.pending.delegation_url} target="_blank" rel="noopener noreferrer">
            Admin console
          </a>
        </LabelValue>
      )}
      {key?.checked_at && <LabelValue label="Checked">{when(key.checked_at)}</LabelValue>}
      {key?.last_error && <LabelValue label="Problem">{key.last_error}</LabelValue>}
      {me && <LabelValue label="Your account">{me.status === 'failed' && me.message ? me.message : (MY_STATE[me.status] ?? me.status)}</LabelValue>}
    </Facts>
  );
  const actions = (g.can_admin || me) && (
    <>
      <Menu trigger={<IconButton icon="more" label="Google settings" size="sm" />}>
        {g.can_admin && key && <MenuItem label="Check" onSelect={() => void press('tools.checkGoogle', {}, 'Not checked')} />}
        {g.can_admin && <MenuItem label="Setup command" onSelect={() => setDialog('setup')} />}
        {g.can_admin && <MenuItem label={key ? 'Replace key' : 'Upload key'} onSelect={() => setDialog('key')} />}
        {me?.configured && !meOn && <MenuItem label="Connect your Google" onSelect={() => void press('tools.connectMyGoogle', {}, 'Not connected')} />}
        {meOn && <MenuItem label="Disconnect your Google" onSelect={() => void press('tools.disconnectMyGoogle', {}, 'Not disconnected')} />}
        {g.can_admin && key && (
          <>
            <MenuRule />
            <MenuItem label="Disconnect Workspace" onSelect={() => setDialog('disconnect')} />
          </>
        )}
      </Menu>
      {dialog === 'setup' && <SetupDialog domain={key?.domain ?? ''} onClose={() => setDialog(null)} />}
      {dialog === 'key' && <KeyDialog domain={key?.domain ?? ''} onClose={() => setDialog(null)} />}
      {dialog === 'disconnect' && key && (
        <Confirm
          title={`Disconnect ${key.domain}?`}
          description="Mailboxes on it stop syncing."
          label="Disconnect"
          onGo={() => press('tools.disconnectGoogle', {}, 'Not disconnected')}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
  return { facts, actions };
}

/** A one-time command an agent or a terminal runs to make the key. The code in it is shown here once, never kept. */
export function SetupDialog({ domain: was, onClose }: { domain: string; onClose: () => void }) {
  const [domain, setDomain] = useState(was);
  const [command, setCommand] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    const r = await run('tools.googleSetup', { domain: domain.trim() }, { via: 'click' });
    setBusy(false);
    if (r.ok && typeof r.out === 'string') setCommand(r.out);
    else toast(refusal(r, 'Not made'));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Setup command"
      description={command ? 'Works once, for 30 minutes.' : undefined}
      className="connect"
      actions={command ? <Button onClick={onClose}>Done</Button> : <Foot onClose={onClose} label="Make" busy={busy} ready={/\.\w/.test(domain)} onGo={() => void make()} />}
    >
      {command ? (
        <div className="connect-cmd" role="group" aria-label="Setup command">
          <code>{command}</code>
          <IconButton icon="copy" label="Copy" size="sm" onClick={() => navigator.clipboard?.writeText(command)} />
        </div>
      ) : (
        <Field label="Domain" value={domain} onValueChange={setDomain} placeholder="example.com" autoFocus />
      )}
    </Dialog>
  );
}

const isJson = (text: string) => {
  try {
    return typeof JSON.parse(text) === 'object';
  } catch {
    return false;
  }
};

/** The service account's JSON key, sent once and sealed on the server. */
function KeyDialog({ domain: was, onClose }: { domain: string; onClose: () => void }) {
  const [domain, setDomain] = useState(was);
  const [key, setKey] = useState('');
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Workspace key"
      actions={
        <Foot onClose={onClose} label="Upload" busy={busy} ready={/\.\w/.test(domain) && isJson(key)} onGo={() => void go('tools.uploadGoogleKey', { domain: domain.trim(), key }, 'Not uploaded')} />
      }
    >
      <Field label="Domain" value={domain} onValueChange={setDomain} placeholder="example.com" />
      <Field label="Key (JSON)" value={key} onValueChange={setKey} area autoFocus />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- Email: what agents may send */

export const MODES = [
  ['ask_first', 'Ask first'],
  ['on_their_own', 'On their own'],
  ['never', 'Never'],
] as const;
const WHAT = [
  ['replies_only', 'Replies only'],
  ['replies_and_new', 'Replies and new'],
] as const;
const TO = [
  ['anyone', 'Anyone'],
  ['known_addresses', 'Known addresses'],
  ['internal', 'Inside the company'],
] as const;
const UNDO = [
  ['0', 'None'],
  ['30', '30 seconds'],
  ['120', '2 minutes'],
  ['600', '10 minutes'],
] as const;
const ASK = [
  ['ask_new_address', 'A new address', 'Ask before writing to a new address'],
  ['ask_legal_money', 'Legal or money', 'Ask on legal or money matters'],
  ['ask_attachments', 'Attachments', 'Ask before sending attachments'],
  ['ask_many_recipients', 'Many recipients', 'Ask before more than 10 recipients'],
  ['ask_outside_cc', 'Outside Cc', 'Ask before copying outside people'],
] as const;
type Ask = (typeof ASK)[number][0];

const n = (v: number | undefined) => (v ?? 0).toLocaleString('en-US');

/** What agents may send: the company's rules (an admin edits them), and each agent's own mode when given the agents. */
export function useSending(agents: { id: string; name: string }[] = []) {
  const q = useQuery(sendingQuery);
  const admin = useAdmin();
  const [editing, setEditing] = useState(false);
  const e = q.data?.effective;
  if (!e) return { facts: null, actions: null };
  const asks = ASK.filter(([k]) => e[k]).map(([, label]) => label);
  const facts = (
    <>
      <Facts>
        <LabelValue label="Agents send">{wordOf(MODES, e.mode) || 'Ask first'}</LabelValue>
        <LabelValue label="What">{wordOf(WHAT, e.what)}</LabelValue>
        <LabelValue label="To">{wordOf(TO, e.to)}</LabelValue>
        <LabelValue label="A day">{`${n(e.daily_cap)} an agent, ${n(e.mailbox_daily_cap)} a mailbox, ${n(e.company_daily_cap)} in all`}</LabelValue>
        <LabelValue label="Undo">{wordOf(UNDO, String(e.undo_seconds ?? 0))}</LabelValue>
        <LabelValue label="Always ask">{asks.join(', ') || 'Nothing'}</LabelValue>
        {!!e.blocked_recipients?.length && <LabelValue label="Never send to">{e.blocked_recipients.join(', ')}</LabelValue>}
      </Facts>
      {admin && agents.map((a) => <AgentSending key={a.id} bot={a.id} name={a.name} />)}
    </>
  );
  const actions = admin && (
    <>
      <IconButton icon="edit" label="Edit sending" size="sm" onClick={() => setEditing(true)} />
      {editing && <SendingDialog was={e} onClose={() => setEditing(false)} />}
    </>
  );
  return { facts, actions };
}

const AGENT_MODES = [{ value: '', label: 'Company rule' }, ...words(MODES)];

function AgentSending({ bot, name }: { bot: string; name: string }) {
  const q = useQuery(agentSendingQuery(bot));
  return (
    <WhoRow
      actor={`bot:${bot}`}
      name={name}
      right={
        <Select
          label={`${name} sends`}
          labelHidden
          look="quiet"
          options={AGENT_MODES}
          value={q.data ? (q.data.mode ?? '') : null}
          onValueChange={(v) => void press('tools.setAgentSending', { bot, mode: v || null }, 'Not saved')}
        />
      }
    />
  );
}

const num = (v: string) => (v.trim() === '' ? undefined : Math.max(0, Math.round(Number(v))));

function SendingDialog({ was, onClose }: { was: SendPolicy; onClose: () => void }) {
  const [mode, setMode] = useState(was.mode ?? 'ask_first');
  const [what, setWhat] = useState(was.what ?? 'replies_only');
  const [to, setTo] = useState(was.to ?? 'anyone');
  const [agent, setAgent] = useState(String(was.daily_cap ?? ''));
  const [box, setBox] = useState(String(was.mailbox_daily_cap ?? ''));
  const [all, setAll] = useState(String(was.company_daily_cap ?? ''));
  const [outside, setOutside] = useState(String(was.max_outside_recipients ?? 1));
  const [undo, setUndo] = useState(String(was.undo_seconds ?? 0));
  const [asks, setAsks] = useState<Record<Ask, boolean>>(Object.fromEntries(ASK.map(([k]) => [k, !!was[k]])) as Record<Ask, boolean>);
  const [blocked, setBlocked] = useState((was.blocked_recipients ?? []).join(', '));
  const [busy, go] = useGo(onClose);
  const list = blocked.split(/[\s,;]+/).filter(Boolean);
  const change: Record<string, unknown> = {};
  const put = (k: string, v: unknown, old: unknown) => {
    if (v !== undefined && v !== old) change[k] = v;
  };
  put('mode', mode, was.mode);
  put('what', what, was.what);
  put('to', to, was.to);
  put('daily_cap', num(agent), was.daily_cap);
  put('mailbox_daily_cap', num(box), was.mailbox_daily_cap);
  put('company_daily_cap', num(all), was.company_daily_cap);
  put('max_outside_recipients', num(outside), was.max_outside_recipients);
  put('undo_seconds', Number(undo), was.undo_seconds ?? 0);
  for (const [k] of ASK) put(k, asks[k], !!was[k]);
  if (list.join(',') !== (was.blocked_recipients ?? []).join(',')) change.blocked_recipients = list;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Sending"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={Object.keys(change).length > 0} onGo={() => void go('tools.setSending', change, 'Not saved')} />}
    >
      <Select label="Agents send" options={words(MODES)} value={mode} onValueChange={setMode} />
      <Select label="What" options={words(WHAT)} value={what} onValueChange={setWhat} />
      <Select label="To" options={words(TO)} value={to} onValueChange={setTo} />
      <Field label="Per agent per day" type="number" value={agent} onValueChange={setAgent} />
      <Field label="Per mailbox per day" type="number" value={box} onValueChange={setBox} />
      <Field label="Per company per day" type="number" value={all} onValueChange={setAll} />
      <Field label="Outside recipients per email" type="number" value={outside} onValueChange={setOutside} />
      <Select label="Undo" options={words(UNDO)} value={undo} onValueChange={setUndo} />
      {ASK.map(([k, , label]) => (
        <Checkbox key={k} label={label} checked={asks[k]} onCheckedChange={(on) => setAsks({ ...asks, [k]: on })} />
      ))}
      <Field label="Never send to" value={blocked} onValueChange={setBlocked} placeholder="Addresses or domains" />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a Tico address: the company's shared Inbox */

/** Joins the company's shared Inbox with a code from the customer portal: first who it is, then the person's word. */
export function SharedInboxDialog({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState('');
  const [seen, setSeen] = useState<SharedInbox | null>(null);
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [, go] = useGo(onClose);
  const look = async () => {
    setBusy(true);
    try {
      setSeen(await sharedInbox('preview', code.trim()));
    } catch {
      toast({ text: 'That code did not work', kind: 'failed' });
    }
    setBusy(false);
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Join shared Inbox"
      actions={
        seen ? (
          <Foot onClose={onClose} label="Join" busy={busy} ready={agree} onGo={() => void go('tools.joinSharedInbox', { code: code.trim() }, 'Not joined')} />
        ) : (
          <Foot onClose={onClose} label="Next" busy={busy} ready={code.trim().length === 43} onGo={() => void look()} />
        )
      }
    >
      <Field
        label="Connection code"
        type="password"
        value={code}
        onValueChange={(v) => {
          setCode(v);
          setSeen(null);
          setAgree(false);
        }}
        autoFocus
      />
      {seen && (
        <>
          <Facts>
            <LabelValue label="Company">{seen.company.name ?? seen.company.company_id}</LabelValue>
          </Facts>
          <Checkbox label="Share Inbox both ways with this company" checked={agree} onCheckedChange={setAgree} />
        </>
      )}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- phone numbers: requests and quotes */

const LIVE = ['requested', 'quoted', 'accepted', 'approved'];
export const usd = (micros: number) => `$${(micros / 1_000_000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function requestWord(r: NumberRequest, now: number): { text: string; quote: number | null } {
  if (r.state === 'quoted') {
    const fresh = !!r.price_micros && Number(r.quoted_until ?? 0) > now / 1000;
    return fresh ? { text: `${usd(r.price_micros as number)} for 30 days`, quote: r.price_micros as number } : { text: 'Quote expired', quote: null };
  }
  if (r.state === 'requested') return { text: 'Awaiting quote', quote: null };
  return { text: r.state.charAt(0).toUpperCase() + r.state.slice(1), quote: null };
}

/** The numbers asked for and not yet in use: accept a quote (paid) or cancel. `always`: "No requests" when none. */
export function NumberRequests({ always = false }: { always?: boolean }) {
  const q = useQuery(numberRequestsQuery);
  const [now] = useState(() => Date.now());
  const live = (q.data ?? []).filter((r) => LIVE.includes(r.state));
  if (q.isError && !q.data) return <ReadFault name="Requests" error={q.error} onRetry={() => void q.refetch()} />;
  if (!live.length) return always && q.data ? <Empty title="No requests" /> : null;
  return live.map((r) => {
    const w = requestWord(r, now);
    return (
      <div key={r.request_id} className="row urow" title={r.name} data-target={`number-request:${r.request_id}`}>
        <span className="row-title">{r.name}</span>
        <span className="row-ctx">{w.text}</span>
        {w.quote !== null && <Button onClick={() => press('tools.acceptQuote', { request: r.request_id, price_micros: w.quote }, 'Not accepted')}>Accept</Button>}
        <IconButton icon="close" label="Cancel request" size="sm" onClick={() => press('tools.cancelNumberRequest', { request: r.request_id }, 'Not canceled')} />
      </div>
    );
  });
}

/* ---------------------------------------------------------------- the mailing address: its addresses and letters' rules */

export const ADDRESS_STATE: Record<string, string> = { pending_1583: 'Waiting for Form 1583', active: 'Connected', closed: 'Closed' };
export const KINDS: Record<string, string> = { shared: 'Shared', private: 'Private' };
/** What a Read grant on an address names: its Gateway assignment, or an older active address by its exact text. */
export const scopeOf = (a: { assignment_id?: string | null; id?: string | null; address?: string | null; status: string }) =>
  String(a.assignment_id || a.id || '') || (a.status === 'active' && a.address) || '';

/** Every address and open order, each a link to its own page (`address:<id>`). A failed read (the Gateway off) says so. */
export function PostalAddresses() {
  const q = useQuery({ ...postalAddressesQuery, retry: false });
  if (q.isError && !q.data) return <ReadFault name="Addresses" error={q.error} onRetry={() => void q.refetch()} />;
  const rows = [
    ...(q.data?.addresses ?? []).map((a) => ({
      id: String(a.assignment_id || a.id || a.address || `${a.state}-${a.kind}`),
      open: scopeOf(a),
      title: a.status === 'active' && a.address ? a.address : `${a.state}, ${KINDS[a.kind] ?? a.kind}`,
      word: ADDRESS_STATE[a.status] ?? a.status,
    })),
    ...(q.data?.orders ?? [])
      .filter((o) => o.status === 'open')
      .map((o) => ({ id: `order:${o.order_id ?? `${o.state}-${o.kind}`}`, open: o.order_id ? `order-${o.order_id}` : '', title: `${o.state}, ${KINDS[o.kind] ?? o.kind}`, word: 'Ordered' })),
  ];
  return rows.map((r) => (
    <Row key={r.id} className="urow" title={r.title} href={r.open ? toolHref(`address:${r.open}`) : undefined} target={`tool:address:${r.open || r.id}`}>
      <span className="row-title">{r.title}</span>
      <span className="row-ctx">{r.word}</span>
    </Row>
  ));
}

const lettersState = (l: { mode: string; availability: string; return_address: unknown }) =>
  l.mode === 'never' ? 'Never' : l.availability === 'unconfigured' ? 'Coming soon' : l.availability !== 'ready' ? 'Not set up' : !l.return_address ? 'Return address needed' : 'Ready';

export interface Postal {
  name?: string;
  line1?: string;
  line2?: string;
  city?: string;
  state?: string;
  postal_code?: string;
}
export const addressLine = (a: Postal | null) => (a ? [a.name, a.line1, a.line2, [a.city, a.state, a.postal_code].filter(Boolean).join(' ')].filter(Boolean).join(', ') : '');

/** What the mailing address can do: send letters (each is paid; a month's cap, a return address), take in emailed
    scans, and where legal letters go. Its ⋯ edits them and checks the sending service. */
export function useLetters() {
  const settings = useQuery(lettersSettingsQuery);
  const intake = useQuery(intakeQuery);
  const legal = useQuery(legalMailQuery);
  const actors = useActors();
  const admin = useAdmin();
  const [editing, setEditing] = useState<'out' | 'intake' | null>(null);
  const l = settings.data;
  const i = intake.data;
  const short = (actor: string) => actor.slice(actor.indexOf(':') + 1);
  const agents = actors.filter((a) => a.kind === 'agent');
  const legalOptions = [{ value: '', label: 'Owner' }, ...agents.map((a) => ({ value: short(a.id), label: a.name }))];
  const facts = (
    <>
      {settings.isError && !l && <ReadFault name="Sending letters" error={settings.error} onRetry={() => void settings.refetch()} />}
      <Facts>
        {l && <LabelValue label="Sending letters">{lettersState(l)}</LabelValue>}
        {l && <LabelValue label="Agents send">{wordOf(MODES, l.mode)}</LabelValue>}
        {l && <LabelValue label="A month">{l.monthly_cap_micros == null ? 'No cap' : usd(l.monthly_cap_micros)}</LabelValue>}
        {l && <LabelValue label="Return address">{addressLine(l.return_address as Postal | null) || 'None'}</LabelValue>}
        {i && <LabelValue label="Emailed letters">{i.hidden ? 'One you cannot read' : i.inbox_id ? (i.address ?? i.inbox_id) : 'None'}</LabelValue>}
        {i?.inbox_id && !!i.senders?.length && <LabelValue label="From">{i.senders.join(', ')}</LabelValue>}
        {legal.data !== undefined && (
          <LabelValue label="Legal letters">
            {admin ? (
              <Select
                label="Legal letters go to"
                labelHidden
                look="quiet"
                options={legalOptions}
                value={legal.data ?? ''}
                onValueChange={(v) => void press('tools.setLegalMail', { bot: v || null }, 'Not saved')}
              />
            ) : (
              (actors.find((a) => a.kind === 'agent' && short(a.id) === legal.data)?.name ?? (legal.data || 'Owner'))
            )}
          </LabelValue>
        )}
      </Facts>
    </>
  );
  const actions = admin && (
    <>
      <Menu trigger={<IconButton icon="more" label="Letter settings" size="sm" />}>
        {l && <MenuItem label="Edit sending" onSelect={() => setEditing('out')} />}
        {i && <MenuItem label="Edit emailed letters" onSelect={() => setEditing('intake')} />}
        <MenuItem label="Check" onSelect={() => void press('tools.checkLetters', {}, 'Not checked')} />
      </Menu>
      {editing === 'out' && l && <LettersOutDialog mode={l.mode} cap={l.monthly_cap_micros} address={l.return_address as Postal | null} onClose={() => setEditing(null)} />}
      {editing === 'intake' && i && <IntakeDialog was={i} onClose={() => setEditing(null)} />}
    </>
  );
  return { facts, actions };
}

/** Who may read every letter (an admin's list): its rows and its "+". */
export function useLetterReaders() {
  const admin = useAdmin();
  const grants = useQuery({ ...letterGrantsQuery, enabled: admin });
  const actors = useActors();
  if (!admin || !grants.data) return { who: undefined, add: null };
  const subject = (type: string, who: string) => `${type === 'bot' ? 'bot' : 'human'}:${who}`;
  const granted = new Set(grants.data.map((g) => subject(g.grantee_type, g.grantee_id)));
  const who = grants.data.map((g) => {
    const actor = subject(g.grantee_type, g.grantee_id);
    const name = actors.find((a) => a.id === actor)?.name ?? g.grantee_id;
    return <WhoRow key={g.id} actor={actor} name={name} onRemove={() => void press('tools.setAccess', { kind: 'letters', who: actor, level: 'none' }, 'Not removed')} />;
  });
  const add = (
    <AddSomeone
      choices={actors.filter((a) => !granted.has(a.id))}
      onPick={(id) => void press('tools.setAccess', { kind: 'letters', who: id, level: 'read' }, 'Not added')}
    />
  );
  return { who, add };
}

const ADDRESS_FIELDS: [keyof Postal, string][] = [
  ['name', 'Name'],
  ['line1', 'Street'],
  ['line2', 'Suite'],
  ['city', 'City'],
  ['state', 'State'],
  ['postal_code', 'ZIP'],
];

function LettersOutDialog({ mode: wasMode, cap: wasCap, address: was, onClose }: { mode: string; cap: number | null; address: Postal | null; onClose: () => void }) {
  const [mode, setMode] = useState(wasMode);
  const [cap, setCap] = useState(wasCap == null ? '' : String(wasCap / 1_000_000));
  const [address, setAddress] = useState<Postal>(was ?? {});
  const [busy, go] = useGo(onClose);
  const capMicros = cap.trim() === '' ? null : Math.round(Number(cap) * 1_000_000);
  const filled = ADDRESS_FIELDS.some(([k]) => address[k]?.trim());
  const whole = ['name', 'line1', 'city', 'state', 'postal_code'].every((k) => address[k as keyof Postal]?.trim());
  const ready = (capMicros === null || (Number.isFinite(capMicros) && capMicros >= 0)) && (!filled || whole);
  const trimmed = Object.fromEntries(ADDRESS_FIELDS.map(([k]) => [k, address[k]?.trim() ?? '']).filter(([k, v]) => v || k !== 'line2'));
  const input = { mode, monthly_cap_micros: capMicros, return_address: filled ? { ...trimmed, state: String(trimmed.state).toUpperCase() } : null };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Sending letters"
      actions={<Foot onClose={onClose} label="Save" busy={busy} ready={ready} onGo={() => void go('tools.setLettersOut', input, 'Not saved')} />}
    >
      <Select label="Agents send" options={words(MODES)} value={mode} onValueChange={setMode} />
      <Field label="A month ($)" type="number" value={cap} onValueChange={setCap} placeholder="No cap" />
      {ADDRESS_FIELDS.map(([k, label]) => (
        <Field key={k} label={label} value={address[k] ?? ''} onValueChange={(v) => setAddress({ ...address, [k]: v })} />
      ))}
    </Dialog>
  );
}

function IntakeDialog({ was, onClose }: { was: { inbox_id?: string | null; senders?: string[] | null }; onClose: () => void }) {
  const boxes = useQuery(inboxesQuery);
  const [inbox, setInbox] = useState(was.inbox_id ?? '');
  const [senders, setSenders] = useState((was.senders ?? []).join(', '));
  const [busy, go] = useGo(onClose);
  const list = [...new Set(senders.toLowerCase().split(/[\s,;]+/).filter(Boolean))];
  const options = [
    { value: '', label: 'None' },
    ...(boxes.data ?? []).filter((b) => b.kind === 'email').map((b) => ({ value: b.inbox_id, label: b.name ? `${b.name}, ${b.address}` : b.address })),
  ];
  if (was.inbox_id && !options.some((o) => o.value === was.inbox_id)) options.push({ value: was.inbox_id, label: was.inbox_id });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Emailed letters"
      actions={
        <Foot onClose={onClose} label="Save" busy={busy} ready={!inbox || list.length > 0} onGo={() => void go('tools.setIntake', { inbox: inbox || null, senders: inbox ? list : [] }, 'Not saved')} />
      }
    >
      <Select label="Inbox" options={options} value={inbox} onValueChange={setInbox} />
      {inbox && <Field label="Senders" value={senders} onValueChange={setSenders} placeholder="scans@provider.com, @provider.com" />}
    </Dialog>
  );
}

