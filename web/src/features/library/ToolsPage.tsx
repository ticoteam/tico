import { useQuery } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import type { ApiTool, LetterOut, ToolIndexRow, WebsiteTool } from '@/api/schema';
import { useFollow } from '@/live/follow';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { ItemHead, ListAndItem, Section, type PageState } from '@/pagekinds';
import { AddAccountDialog, AddDialog, AddMenu, ReplaceLoginDialog, type Adding } from './AddTool';
import { Face, Held, KindTile, More, OwnerButton, press, when } from './parts';
import {
  apiToolQuery,
  inboxesQuery,
  inboxMessagesQuery,
  lettersQuery,
  mailboxQuery,
  mailQuery,
  toolsQuery,
  vendorQuery,
  websiteToolQuery,
} from './queries';
import type { ToolsSearch } from './search';
import './library.css';

/* Tools (tools.js toolsScreen, detail): the list of tools beside the open one. A vendor with several tools is one row;
   its page lists them. Every tool has one layout: what it is, how it connects, who may use it, what it can do, recent
   use. A lock means Tico holds the key. A key is only ever sent, never shown: its row shows a mask. */

export const KIND: Record<string, string> = {
  api: 'API',
  mcp: 'MCP server',
  website: 'Website',
  mailbox: 'Email',
  tico: 'Tico address',
  number: 'Phone number',
  address: 'Mailing address',
  slack: 'Slack',
  github: 'GitHub',
  meetings: 'Meetings',
  listening: 'Listening',
  webhook: 'Outgoing webhook',
  declared: 'Key',
  vendor: 'Service',
};

const toolHref = (id: string) => `/library/tools?tool=${encodeURIComponent(id)}`;
const held = (r: ToolIndexRow) => r.kind === 'api' && r.delivery === 'proxy';
const bad = (r: { needs_you: boolean; status: { tone: string; text: string } }) => (r.needs_you || r.status.tone === 'fail' ? r.status.text : null);

/** One row per tool, or one per vendor when a vendor has several. */
export interface Entry {
  id: string;
  name: string;
  kind: string;
  row?: ToolIndexRow;
  count?: number;
  inUse: boolean;
  problem: string | null;
}

export function entries(rows: ToolIndexRow[]): Entry[] {
  const byVendor = new Map<string, ToolIndexRow[]>();
  for (const r of rows) if (r.vendor) byVendor.set(r.vendor, [...(byVendor.get(r.vendor) ?? []), r]);
  const out: Entry[] = [];
  const done = new Set<string>();
  for (const r of rows) {
    const group = r.vendor ? byVendor.get(r.vendor) : undefined;
    if (group && group.length > 1) {
      if (done.has(r.vendor as string)) continue;
      done.add(r.vendor as string);
      out.push({
        id: `vendor:${r.vendor}`,
        name: r.vendor_name || (r.vendor as string),
        kind: group[0]?.kind ?? r.kind,
        count: group.length,
        inUse: group.some((g) => g.who_total > 0),
        problem: group.map(bad).find(Boolean) ?? null,
      });
    } else out.push({ id: r.id, name: r.name, kind: r.kind, row: r, inUse: r.who_total > 0, problem: bad(r) });
  }
  // Something wrong first, then by name.
  return out.sort((a, b) => Number(!!b.problem) - Number(!!a.problem) || a.name.localeCompare(b.name));
}

export function ToolsPage({ search }: { search: ToolsSearch }) {
  const tools = useQuery(toolsQuery);
  const [words, setWords] = useState(search.q ?? '');
  const w = words.trim().toLowerCase();
  const rows = (tools.data?.rows ?? []).filter(
    (r) => !w || r.name.toLowerCase().includes(w) || r.detail.toLowerCase().includes(w) || (r.vendor_name ?? '').toLowerCase().includes(w),
  );
  const all = entries(rows);
  const open = search.tool;
  const [adding, setAdding] = useState<Adding | null>(null);
  const none = !!tools.data && !tools.data.rows.length;

  let state: PageState = 'ready';
  if (tools.isError) state = 'fault';
  else if (!tools.data) state = 'loading';

  const group = (name: string, list: Entry[]) =>
    list.length > 0 && (
      <div className="tgroup">
        <header className="tgroup-head">
          <h3 className="t-label">{name}</h3>
          <span className="stage-n">{list.length}</span>
        </header>
        {list.map((e) => (
          <Row
            key={e.id}
            className={e.inUse ? 'tl-row trow' : 'tl-row trow is-off'}
            title={e.name}
            href={toolHref(e.id)}
            selected={e.id === open}
            target={`tool:${e.id}`}
          >
            <KindTile kind={e.kind} mcp={false} />
            <span className="trow-name">
              <span className="row-title">{e.name}</span>
              {e.row && held(e.row) && <Held />}
            </span>
            {e.problem ? <Pill>{e.problem}</Pill> : e.count ? <span className="row-fig">{e.count}</span> : <span />}
          </Row>
        ))}
      </div>
    );

  return (
    <>
      <ListAndItem
        title="Tools"
        path="/library/tools"
        className="lib tools"
        actions={<AddMenu trigger={<IconButton icon="plus" label="Add tool" />} onPick={setAdding} />}
        open={!!open}
        state={state}
        onRetry={() => void tools.refetch()}
        search={<Search label="Find a tool" value={words} onValueChange={setWords} placeholder="Find a tool" wide />}
        list={
          none ? (
            <Empty title="No tools yet" />
          ) : all.length ? (
            <div className="tools-list">
              {group(
                'In use',
                all.filter((e) => e.inUse),
              )}
              {group(
                'Available',
                all.filter((e) => !e.inUse),
              )}
            </div>
          ) : (
            <Empty title="Nothing matches" />
          )
        }
        conversation={open ? <Tool id={open} row={tools.data?.rows.find((r) => r.id === open)} /> : <NoTool none={none} onAdd={setAdding} />}
      />
      {adding && <AddDialog adding={adding} onClose={() => setAdding(null)} />}
    </>
  );
}

function NoTool({ none, onAdd }: { none: boolean; onAdd: (a: Adding) => void }) {
  return (
    <section className="tool is-empty" aria-label="Tools">
      <header className="conv-head tool-head">
        <span className="grow" />
        <OwnerButton page="tools" />
      </header>
      {none && (
        <Empty title="No tools yet">
          <AddMenu trigger={<Button variant="primary">Add tool</Button>} onPick={onAdd} />
        </Empty>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- one tool */

/** The open tool: its head, then the five sections the kind has. */
function Tool({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const at = id.indexOf(':');
  const kind = at > 0 ? id.slice(0, at) : id;
  const key = at > 0 ? id.slice(at + 1) : '';
  if (kind === 'api') return <ApiToolView id={key} row={row} />;
  if (kind === 'website') return <WebsiteView id={key} row={row} />;
  if (kind === 'vendor') return <VendorView vendor={key} />;
  if (kind === 'mailbox' && (row as { mailbox_id?: string } | undefined)?.mailbox_id)
    return <MailboxView id={(row as unknown as { mailbox_id: string }).mailbox_id} row={row as ToolIndexRow} />;
  if (kind === 'tico' || kind === 'number') return <InboxView inbox={key} row={row} number={kind === 'number'} />;
  if (kind === 'address') return <AddressView row={row} />;
  return <PlainView row={row} kind={kind} />;
}

interface ShellProps {
  name: string;
  kind: string;
  mcp?: boolean;
  lock?: boolean;
  problem?: string | null;
  fill?: ReactNode;
  state?: 'loading' | 'fault' | 'no-access' | 'ready';
  onRetry?: () => void;
  target: string;
  children?: ReactNode;
}

/** The open tool's head and its scrolling body (tools.css `.tool-head`, `.tool-body`). */
function Shell({ name, kind, mcp, lock, problem, fill, state = 'ready', onRetry, target, children }: ShellProps) {
  return (
    <section className="tool" aria-label={name}>
      <ItemHead
        title={name}
        target={target}
        back={{ to: '/library/tools', label: 'Tools' }}
        lead={<KindTile kind={kind} mcp={mcp} big />}
        actions={
          <>
            {lock && <Held />}
            {problem && <Pill>{problem}</Pill>}
            <OwnerButton page="tools" />
            {fill}
          </>
        }
      />
      <div className="conv-body tool-body" data-scroll>
        {state === 'loading' ? null : state === 'fault' ? (
          <Empty title={`${name} did not load`} icon="alert">
            {onRetry && <Button onClick={onRetry}>Try again</Button>}
          </Empty>
        ) : state === 'no-access' ? (
          <Empty title="This tool is not shared with you" icon="lock" />
        ) : (
          children
        )}
      </div>
    </section>
  );
}

const stateOf = (q: { isError: boolean; error: unknown; data: unknown }) =>
  q.isError ? ((q.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault') : q.data ? 'ready' : 'loading';

const Code = ({ children }: { children: ReactNode }) => <code className="fact-code">{children}</code>;
const Dim = ({ children }: { children: ReactNode }) => <span className="fact-n">{children}</span>;
const host = (url: string) => url.replace(/^https?:\/\//, '').split('/')[0];

const LEVEL_WORDS: Record<string, string> = { read: 'Read', read_write: 'Read and write' };
const LEVEL = (l: string) => LEVEL_WORDS[l] ?? l;

function Who({ actor, name, right, at }: { actor: string | null; name: string; right?: ReactNode; at?: string | null }) {
  return (
    <div className="row wrow" title={name}>
      <Face actor={actor} name={name} />
      <span className="row-title">{name}</span>
      {right}
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </div>
  );
}

/* ---------------------------------------------------------------- an API or MCP tool */

type Key = ApiTool['keys'][number];

function ApiToolView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(apiToolQuery(id));
  const [keyFor, setKeyFor] = useState<Key | 'new' | null>(null);
  const [connecting, setConnecting] = useState(false);
  const t = q.data;
  const mcp = t?.mcp ?? null;
  const name = t?.tool.name ?? row?.name ?? 'Tool';
  const lock = !!t && t.tool.delivery === 'proxy' && t.keys.length > 0;
  const problem = t && (t.status.tone === 'fail' ? t.status.text : null);
  const oauth = mcp?.auth === 'oauth';

  // One tap where the server signs in for us; a key otherwise.
  const connect = async () => {
    if (!oauth) return setKeyFor(t?.keys[0] ?? 'new');
    setConnecting(true);
    const r = await run('tools.connect', { tool: id }, { via: 'click' });
    if (!r.ok) {
      setConnecting(false);
      toast({ text: r.error === 'not_allowed' ? 'You cannot do that here' : 'Not connected', kind: 'failed' });
    }
  };
  const fill =
    t?.can_manage &&
    (!t.keys.length || problem ? (
      <Button variant="primary" busy={connecting} disabled={connecting} onClick={() => void connect()} data-target={`tool-connect:${id}`}>
        {t.keys.length && !oauth ? 'Replace key' : 'Connect'}
      </Button>
    ) : undefined);
  const last = t?.checks[0];

  return (
    <Shell name={name} kind="api" mcp={!!mcp} lock={lock} problem={problem} fill={fill} state={stateOf(q)} onRetry={() => void q.refetch()} target={`tool:api:${id}`}>
      {t && (
        <>
          <Section name="What it is" target={`tool-about:${id}`}>
            <Facts>
              <LabelValue label="Kind">{mcp ? KIND.mcp : KIND.api}</LabelValue>
              <LabelValue label="Address">
                <Code>{mcp ? host(mcp.url) : host(t.tool.base_url)}</Code>
              </LabelValue>
              {mcp?.tools != null && <LabelValue label="Tools">{mcp.read_only ? `${mcp.tools}, ${mcp.read_only} read-only` : String(mcp.tools)}</LabelValue>}
              {t.settings?.map((s) => (
                <LabelValue key={s.id} label={s.name}>
                  {s.value}
                </LabelValue>
              ))}
            </Facts>
          </Section>
          <Section
            name="How it connects"
            target={`tool-connect:${id}`}
            actions={t.can_manage && !oauth && <IconButton icon="plus" label="Add key" size="sm" onClick={() => setKeyFor('new')} />}
          >
            <Facts>
              {mcp?.signed_in_at && (
                <LabelValue label="Signed in">
                  {mcp.signed_in_by && <Face actor={mcp.signed_in_by} />}
                  {when(mcp.signed_in_at)}
                </LabelValue>
              )}
              {!t.keys.length && !mcp?.signed_in_at && <LabelValue label="Connected">Not yet</LabelValue>}
              {!mcp && t.keys.length > 0 && <LabelValue label="Key goes in">{t.tool.auth_scheme}</LabelValue>}
              {last && (
                <LabelValue label="Checked">
                  <Code>{last.status_code ? `${t.tool.check_path || 'Check'} → ${last.status_code}` : last.detail || (last.ok ? 'Works' : 'Failed')}</Code>{' '}
                  <Dim>
                    <Ago at={last.checked_at} />
                  </Dim>
                </LabelValue>
              )}
            </Facts>
            {t.keys.map((k) => (
              <div key={k.id} className="row arow" title={k.name}>
                <span className="row-title">
                  {k.name} <Code>{k.preview}</Code>
                </span>
                {t.tool.delivery === 'proxy' && <Held />}
                {k.expires_on && <span className="arow-w">Expires {k.expires_on}</span>}
                {t.can_manage && (
                  <>
                    <IconButton icon="edit" label="Replace key" size="sm" onClick={() => setKeyFor(k)} />
                    {k.can_remove !== false && (
                      <IconButton icon="close" label="Remove key" size="sm" onClick={() => void press('tools.removeKey', { tool: id, key: k.id }, 'Not removed')} />
                    )}
                  </>
                )}
              </div>
            ))}
            {t.can_manage && t.keys.length > 0 && <More onClick={() => void press('tools.check', { tool: id }, 'Not checked')}>Check now</More>}
          </Section>
          {t.access.length > 0 && (
            <Section name="Who may use it" count={t.access.length} target={`tool-who:${id}`}>
              {t.access.map((a) => (
                <Who
                  key={a.subject}
                  actor={a.kind === 'finance' ? null : a.subject}
                  name={a.name}
                  at={a.last_used_at}
                  right={
                    t.can_manage && !a.fixed ? (
                      <Menu
                        trigger={
                          <button type="button" className="lvl">
                            {LEVEL(a.level)}
                            <Icon name="chevdown" />
                          </button>
                        }
                      >
                        {(['read', 'read_write'] as const).map((l) => (
                          <MenuItem key={l} label={LEVEL(l)} on={a.level === l} onSelect={() => void press('tools.setAccess', { tool: id, subject: a.subject, level: l })} />
                        ))}
                        <MenuRule />
                        <MenuItem label="Remove" onSelect={() => void press('tools.setAccess', { tool: id, subject: a.subject, level: 'none' })} />
                      </Menu>
                    ) : (
                      <span className="lvl">{LEVEL(a.level)}</span>
                    )
                  }
                />
              ))}
            </Section>
          )}
          {mcp && mcp.names.length > 0 && <McpNames names={mcp.names} />}
          {t.recent.length > 0 && (
            <Section name="Recent use" target={`tool-recent:${id}`}>
              {t.recent.map((r, n) => (
                <CallLine key={String(r.id ?? n)} call={r} />
              ))}
            </Section>
          )}
          {keyFor && <KeyDialog tool={id} name={name} keyRow={keyFor === 'new' ? null : keyFor} onClose={() => setKeyFor(null)} />}
        </>
      )}
    </Shell>
  );
}

function McpNames({ names }: { names: string[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? names : names.slice(0, 8);
  return (
    <Section name="What it can do" count={names.length}>
      {shown.map((n) => (
        <div key={n} className="row arow" title={n}>
          <span className="row-title is-code">{n}</span>
        </div>
      ))}
      {!all && names.length > 8 && <More onClick={() => setAll(true)}>{`${names.length - 8} more`}</More>}
    </Section>
  );
}

const RESULT: Record<string, string> = { refused: 'Refused', failed: 'Failed', denied: 'Refused', error: 'Failed' };

/** One call: `GET /v1/balance → 200`, who made it, when. */
function CallLine({ call }: { call: Record<string, unknown> }) {
  const str = (k: string) => (typeof call[k] === 'string' ? (call[k] as string) : '');
  const what = str('method') ? `${str('method')} ${str('path')}${call.status_code ? ` → ${String(call.status_code)}` : ''}` : str('detail') || str('kind');
  const actor = str('bot') ? `bot:${str('bot')}` : str('actor') || null;
  const flag = RESULT[str('result')];
  return (
    <div className="row urow" title={what}>
      <span className="row-title is-code">{what}</span>
      {flag && <Pill>{flag}</Pill>}
      <Face actor={actor} />
      {str('at') ? <Ago at={str('at')} className="row-age" /> : <span className="row-age" />}
    </div>
  );
}

/** Add or replace a key. The field only sends: a stored key is never read back, so it starts empty. */
function KeyDialog({ tool, name, keyRow, onClose }: { tool: string; name: string; keyRow: Key | null; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await press(
      'tools.setKey',
      { tool, key: keyRow?.id, name: keyRow ? undefined : label.trim() || undefined, value: value.trim() },
      keyRow ? 'Not replaced' : 'Not added',
    );
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={keyRow ? `Replace the ${name} key` : `Connect ${name}`}
      className="connect"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!value.trim() || busy} onClick={() => void save()}>
            {keyRow ? 'Replace' : 'Connect'}
          </Button>
        </>
      }
    >
      {!keyRow && <Field label="Name" value={label} onValueChange={setLabel} placeholder="Key" />}
      <Field label="Key" type="password" value={value} onValueChange={setValue} autoComplete="off" autoFocus wide />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a website */

const VERB: Record<WebsiteTool['recent'][number]['verb'], string> = {
  open: 'Opened',
  type: 'Typed on',
  fill: 'Filled in',
  copy: 'Copied from',
  code: 'Got a sign-in code for',
  needs_you: 'Needed you on',
  control: 'Took over',
  sign_in: 'Signed in to',
  download: 'Downloaded from',
  upload: 'Uploaded to',
};

function WebsiteView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(websiteToolQuery(id));
  const t = q.data;
  const name = t?.tool.name ?? row?.name ?? 'Website';
  const people = new Map<string, { subject: string; name: string }>();
  for (const a of t?.accounts ?? []) for (const p of a.access) people.set(p.subject, p);
  const lock = !!t?.accounts.some((a) => a.has_password);
  const [adding, setAdding] = useState(false);
  const [replacing, setReplacing] = useState<WebsiteTool['accounts'][number] | null>(null);
  return (
    <Shell
      name={name}
      kind="website"
      lock={lock}
      problem={t?.status.tone === 'fail' ? t.status.label : null}
      state={stateOf(q)}
      onRetry={() => void q.refetch()}
      target={`tool:website:${id}`}
    >
      {t && (
        <>
          <Section name="What it is">
            <Facts>
              <LabelValue label="Kind">{KIND.website}</LabelValue>
              <LabelValue label="Site">
                <Code>{t.tool.sites.join(', ')}</Code>
              </LabelValue>
            </Facts>
          </Section>
          <Section name="How it connects" actions={<IconButton icon="plus" label="Add account" onClick={() => setAdding(true)} />}>
            <Facts>
              {t.accounts.map((a) => (
                <LabelValue key={a.id} label="Account">
                  {a.username || a.holder?.name || 'Account'} <Dim>{a.kind === 'shared' ? 'Shared' : 'Own'}</Dim>{' '}
                  <Button variant="quiet" onClick={() => setReplacing(a)}>
                    Replace login
                  </Button>
                </LabelValue>
              ))}
              {t.accounts.some((a) => a.has_password) && (
                <LabelValue label="Password">
                  <Code>••••••••</Code> <Held />
                </LabelValue>
              )}
              {t.accounts.some((a) => a.has_totp) && (
                <LabelValue label="Sign-in codes">
                  Authenticator <Held />
                </LabelValue>
              )}
              {t.host && (
                <LabelValue label="Runs on">
                  {t.host.label} <Dim>{t.host.online ? 'Online' : 'Offline'}</Dim>
                </LabelValue>
              )}
            </Facts>
          </Section>
          {people.size > 0 && (
            <Section name="Who may use it" count={people.size}>
              {[...people.values()].map((p) => (
                <Who key={p.subject} actor={p.subject} name={p.name} />
              ))}
            </Section>
          )}
          {adding && <AddAccountDialog tool={id} onClose={() => setAdding(false)} />}
          {replacing && <ReplaceLoginDialog tool={id} account={replacing} onClose={() => setReplacing(null)} />}
          {t.recent.length > 0 && (
            <Section name="Recent use">
              {t.recent.map((r) => (
                <div key={r.id} className="row urow" title={`${VERB[r.verb]} ${r.page}`}>
                  <span className="row-title">{`${VERB[r.verb]} ${r.page}`}</span>
                  {(r.result === 'refused' || r.result === 'failed') && <Pill>{RESULT[r.result]}</Pill>}
                  <Face actor={r.who.subject} name={r.who.name} />
                  <Ago at={r.at} className="row-age" />
                </div>
              ))}
            </Section>
          )}
        </>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- a vendor's tools */

function VendorView({ vendor }: { vendor: string }) {
  const q = useQuery(vendorQuery(vendor));
  const v = q.data;
  return (
    <Shell name={v?.name ?? vendor} kind={v?.rows[0]?.kind ?? 'api'} state={stateOf(q)} onRetry={() => void q.refetch()} target={`tool:vendor:${vendor}`}>
      {v && (
        <Section name="Its tools" count={v.rows.length}>
          {v.rows.map((r) => (
            <Row key={r.id} className="tl-row trow" title={r.name} href={toolHref(r.id)} target={`tool:${r.id}`}>
              <KindTile kind={r.kind} />
              <span className="trow-name">
                <span className="row-title">{r.name}</span>
                {held(r) && <Held />}
              </span>
              {bad(r) ? <Pill>{bad(r)}</Pill> : <span />}
            </Row>
          ))}
        </Section>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- a mailbox */

function MailboxView({ id, row }: { id: string; row: ToolIndexRow }) {
  const q = useQuery(mailboxQuery(id));
  const m = q.data?.mailbox;
  return (
    <Shell name={m?.address ?? row.name} kind="mailbox" problem={bad(row)} state={stateOf(q)} onRetry={() => void q.refetch()} target={`tool:${row.id}`}>
      {q.data && m && (
        <>
          <Section name="What it is">
            <Facts>
              <LabelValue label="Kind">{KIND.mailbox}</LabelValue>
              <LabelValue label="Address">
                <Code>{m.address}</Code>
              </LabelValue>
              <LabelValue label="Mailbox">{m.kind === 'shared' ? 'Shared' : `${m.person_name}'s`}</LabelValue>
            </Facts>
          </Section>
          <Section name="How it connects">
            <Facts>
              <LabelValue label="Through">{m.provider}</LabelValue>
              <LabelValue label="State">{row.status.text}</LabelValue>
              {m.synced_at && <LabelValue label="Synced">{when(m.synced_at)}</LabelValue>}
              {m.sync_error && <LabelValue label="Problem">{m.sync_error}</LabelValue>}
            </Facts>
          </Section>
          {q.data.accesses.length > 0 && (
            <Section name="Who may use it" count={q.data.accesses.length}>
              {q.data.accesses.map((a) => (
                <Who
                  key={`${a.grantee_type}:${a.grantee_id}`}
                  actor={a.grantee_type === 'admins' ? null : `${a.grantee_type === 'person' ? 'human' : 'bot'}:${a.grantee_id}`}
                  name={a.name}
                  right={<span className="lvl">{LEVEL(a.level)}</span>}
                />
              ))}
            </Section>
          )}
          <Section name="What it can do">
            <Facts>
              <LabelValue label="Read mail">{q.data.may.read ? 'Yes' : 'No'}</LabelValue>
              <LabelValue label="Send mail">{q.data.may.send ? 'Yes' : 'No'}</LabelValue>
              <LabelValue label="Organize">{q.data.may.organize ? 'Yes' : 'No'}</LabelValue>
              <LabelValue label="Change the calendar">{q.data.may.change_calendar ? 'Yes' : 'No'}</LabelValue>
            </Facts>
          </Section>
        </>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- a Tico address or phone number */

function InboxView({ inbox, row, number }: { inbox: string; row: ToolIndexRow | undefined; number: boolean }) {
  const boxes = useQuery(inboxesQuery);
  const messages = useQuery(inboxMessagesQuery(inbox));
  const box = boxes.data?.find((b) => b.inbox_id === inbox);
  const name = box?.name ?? row?.name ?? (number ? 'Phone number' : 'Tico address');
  return (
    <Shell name={name} kind={number ? 'number' : 'mailbox'} problem={row && bad(row)} state={stateOf(boxes)} onRetry={() => void boxes.refetch()} target={`tool:${row?.id ?? inbox}`}>
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{number ? KIND.number : KIND.tico}</LabelValue>
          <LabelValue label={number ? 'Number' : 'Address'}>
            <Code>{box?.address ?? row?.detail ?? ''}</Code>
          </LabelValue>
          {box?.audience && <LabelValue label="For">{box.audience === 'company' ? 'The company' : 'One team'}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects">
        <Facts>
          <LabelValue label="Through">Tico</LabelValue>
          <LabelValue label="State">{row?.status.text ?? box?.state ?? ''}</LabelValue>
        </Facts>
      </Section>
      {(messages.data?.length ?? 0) > 0 && (
        <Section name="Recent use">
          {messages.data?.map((m) => {
            const from = typeof m.from === 'string' ? m.from : '';
            const what = (typeof m.subject === 'string' && m.subject) || (typeof m.preview === 'string' && m.preview) || from;
            const at = typeof m.received_at === 'number' ? new Date(m.received_at * 1000) : null;
            return (
              <div key={String(m.message_id ?? m.seq)} className="row urow" title={what}>
                <span className="urow-lead" title="Came in">
                  <Icon name="download" />
                </span>
                <span className="row-title">{what}</span>
                <span className="row-ctx">{from}</span>
                {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
              </div>
            );
          })}
        </Section>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- a mailing address */

const STATUS_WORD: Record<string, string> = {
  queued: 'Waiting',
  pending: 'Waiting',
  waiting_approval: 'Waits on approval',
  approved: 'Approved',
  submitted: 'Sent to print',
  mailed: 'Mailed',
  in_transit: 'On its way',
  delivered: 'Delivered',
  returned: 'Returned',
  canceled: 'Stopped',
  cancelled: 'Stopped',
  failed: 'Failed',
  filed: 'Filed',
  archived: 'Archived',
  new: 'New',
};
const word = (s: string) => STATUS_WORD[s] ?? s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** Who a letter went to, when the server shows it (a redacted letter shows only its pages). */
function letterTitle(l: LetterOut): string {
  if (!l.redacted) {
    const r = l.recipient as { name?: unknown; company?: unknown; city?: unknown };
    const who = [r.name, r.company].find((v) => typeof v === 'string' && v) as string | undefined;
    if (who) return typeof r.city === 'string' && r.city ? `${who}, ${r.city}` : who;
  }
  return l.page_count ? `A letter, ${l.page_count} page${l.page_count === 1 ? '' : 's'}` : 'A letter';
}

function AddressView({ row }: { row: ToolIndexRow | undefined }) {
  useFollow({ topic: 'letters' });
  const inbound = useQuery(mailQuery);
  const letters = useQuery(lettersQuery);
  const name = row?.name ?? 'Mailing address';
  return (
    <Shell name={name} kind="address" problem={row && bad(row)} state={stateOf(letters)} onRetry={() => void letters.refetch()} target={`tool:${row?.id ?? 'address'}`}>
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{KIND.address}</LabelValue>
          {row?.detail && <LabelValue label="Address">{row.detail}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects">
        <Facts>
          <LabelValue label="Through">{row?.way || 'Tico'}</LabelValue>
          <LabelValue label="State">{row?.status.text ?? ''}</LabelValue>
        </Facts>
      </Section>
      <Section name="Letters out" count={letters.data?.length}>
        {letters.data?.length === 0 && <Empty title="No letters sent yet" />}
        {letters.data?.map((l) => (
          <div key={l.id} className="row urow" title={letterTitle(l)} data-target={`letter:${l.id}`}>
            <span className="urow-lead" title="Sent">
              <Icon name="send" />
            </span>
            <span className="row-title">{letterTitle(l)}</span>
            <span className="row-ctx">{word(l.status)}</span>
            {l.can_cancel && (
              <IconButton icon="close" label="Stop this letter" size="sm" onClick={() => void press('tools.cancelLetter', { letter: l.id }, 'Not stopped')} />
            )}
            <Ago at={l.created_at} className="row-age" />
          </div>
        ))}
      </Section>
      {(inbound.data?.length ?? 0) > 0 && (
        <Section name="Letters in" count={inbound.data?.length}>
          {inbound.data?.map((m) => (
            <div key={m.id} className="row urow" title={m.sender || m.summary}>
              <span className="urow-lead" title="Came in">
                <Icon name="download" />
              </span>
              <span className="row-title">{m.sender || m.summary || 'A letter'}</span>
              <span className="row-ctx">{word(m.status)}</span>
              <Ago at={m.received_at} className="row-age" />
            </div>
          ))}
        </Section>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- Slack, GitHub, meetings, webhooks... */

function PlainView({ row, kind }: { row: ToolIndexRow | undefined; kind: string }) {
  if (!row) return <Shell name="Tool" kind={kind} state="loading" target={`tool:${kind}`} />;
  return (
    <Shell name={row.name} kind={row.kind} problem={bad(row)} target={`tool:${row.id}`}>
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{KIND[row.kind] ?? row.kind}</LabelValue>
          {row.detail && <LabelValue label="About">{row.detail}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects">
        <Facts>
          {row.way && <LabelValue label="Through">{row.way}</LabelValue>}
          <LabelValue label="State">{row.status.text}</LabelValue>
        </Facts>
      </Section>
      {row.who.length > 0 && (
        <Section name="Who may use it" count={row.who_total}>
          {row.who.map((p) => (
            <Who key={p.id} actor={p.kind === 'group' ? null : p.id} name={p.name} />
          ))}
        </Section>
      )}
    </Shell>
  );
}
