import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { ApiTool, LetterOut, ToolIndexRow, WebsiteTool } from '@/api/schema';
import { directoryQuery, meQuery } from '@/data';
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
import { GithubConnection } from '@/features/start/exports';
import { BrowserDialog } from '@/features/team/exports';
import { FilterRow, ListAndItem, PageMore, Section, type PageState } from '@/pagekinds';
import { AddAccountDialog, AddDialog, AddMenu, ReplaceLoginDialog, type Adding } from './AddTool';
import {
  CodeDialog,
  EditApiDialog,
  EditInboxDialog,
  ExpiryDialog,
  MessageDialog,
  PasteDialog,
  ReleaseDialog,
  RevealDialog,
  SettingDialog,
  SitesDialog,
} from './ToolDialogs';
import { Face, Held, KindTile, More, OwnerButton, press, when } from './parts';
import {
  apiToolQuery,
  inboxesQuery,
  inboxMessagesQuery,
  lettersQuery,
  mailQuery,
  toolsQuery,
  vendorQuery,
  webhookQuery,
  webhookSendsQuery,
  websiteToolQuery,
} from './queries';
import { GoogleSection, LettersInSettings, LettersOutSection, MayDo, NumberRequests, PostalAddresses, SendingSection, SharedInboxDialog } from './ToolSettings';
import { useGithubReturn } from './githubReturn';
import {
  AccountAccess,
  CentralOnly,
  CheckInbox,
  DeleteWebsiteConfirm,
  EveryoneReads,
  hasWho,
  InboxReaders,
  KeyView,
  MailboxView,
  RemoveAccountConfirm,
  SeenBy,
  ServiceView,
  SlackLinks,
  subjectOf,
  WhoMenu,
} from './ToolMore';
import { teamsQuery, vaultQuery, type StoredKey } from './toolKeys';
import type { ToolsSearch } from './search';
import { bad, Code, Dim, KIND, LEVEL, Shell, stateOf, toolHref, Who } from './ToolShell';
import './library.css';

export { KIND } from './ToolShell';

/* Tools (tools.js toolsScreen, detail): the list of tools beside the open one. A vendor with several tools is one row;
   its page lists them. Every tool has one layout: what it is, how it connects, who may use it, what it can do, recent
   use. A lock means Tico holds the key. A key is only ever sent, never shown: its row shows a mask. */

const held = (r: ToolIndexRow) => r.kind === 'api' && r.delivery === 'proxy';
const host = (url: string) => url.replace(/^https?:\/\//, '').split('/')[0];

/** The rows the filter keeps: one kind, and (with `needs`) only those that need someone. */
export const narrow = (rows: ToolIndexRow[], s: Pick<ToolsSearch, 'kind' | 'needs'>) =>
  rows.filter((r) => (!s.kind || r.kind === s.kind) && (!s.needs || !!bad(r)));

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
  return out.sort(order);
}

/** Something wrong first, then by name. */
const order = (a: Entry, b: Entry) => Number(!!b.problem) - Number(!!a.problem) || a.name.localeCompare(b.name);

/** A stored key no tool holds: its own row, a Key, in use while it is given to someone. */
export const keyEntry = (k: StoredKey): Entry => ({ id: `key:${k.id}`, name: k.name, kind: 'key', inUse: k.grants.length > 0, problem: k.stored ? null : 'No value' });

export function ToolsPage({ search }: { search: ToolsSearch }) {
  const tools = useQuery(toolsQuery);
  const githubProblem = useGithubReturn(search);
  const [words, setWords] = useState(search.q ?? '');
  const w = words.trim().toLowerCase();
  // Keys no tool holds, for those who may see the vault (anyone else gets none, quietly).
  const loose = (useQuery(vaultQuery).data?.credentials ?? []).filter((k) => !k.tool_id);
  const teams = useQuery(teamsQuery).data ?? [];
  const dir = useQuery(directoryQuery).data;
  const same = (x: string) => dir?.byId[x]?.id ?? x;
  const who = search.who;
  const rows = narrow(tools.data?.rows ?? [], search).filter(
    (r) =>
      (!w || r.name.toLowerCase().includes(w) || r.detail.toLowerCase().includes(w) || (r.vendor_name ?? '').toLowerCase().includes(w)) &&
      (!who || hasWho((r.who as ToolIndexRow['who'] | undefined)?.map(subjectOf) ?? [], who, teams, same)),
  );
  const keys = loose.filter(
    (k) =>
      (!search.kind || search.kind === 'key') &&
      (!search.needs || !k.stored) &&
      (!w || [k.name, k.env, k.source].some((x) => (x ?? '').toLowerCase().includes(w))) &&
      (!who || hasWho(k.grants.map((g) => g.subject), who, teams, same)),
  );
  const all = [...entries(rows), ...keys.map(keyEntry)].sort(order);
  const open = search.tool;
  const [adding, setAdding] = useState<Adding | null>(null);
  const none = !!tools.data && !tools.data.rows.length && !loose.length;
  const kinds = [...new Set([...(tools.data?.rows ?? []).map((r) => r.kind as string), ...(loose.length ? ['key'] : [])])].sort((a, b) =>
    (KIND[a] ?? a).localeCompare(KIND[b] ?? b),
  );
  const filter = (change: { kind?: string; needs?: boolean }) => void press('tools.filter', change);
  const filterWho = (next?: string) => void press('tools.filterWho', next ? { who: next } : {});
  // Who has access: the teams, then everyone a tool or a key is given to.
  const people = new Map<string, string>();
  // `who` may be missing from an older index row.
  for (const r of tools.data?.rows ?? []) for (const p of (r.who as ToolIndexRow['who'] | undefined) ?? []) if (p.kind !== 'group') people.set(same(subjectOf(p)), p.name);
  for (const k of loose)
    for (const g of k.grants) if (/^(human|bot):/.test(g.subject)) people.set(same(g.subject), dir?.byId[g.subject]?.name ?? g.subject);
  const whoList = [...people].map(([subject, name]) => ({ subject, name })).sort((a, b) => a.name.localeCompare(b.name));
  const whoName = who && (who.startsWith('group:') ? teams.find((g) => `group:${g.id}` === who)?.name : (people.get(same(who)) ?? dir?.byId[who]?.name));
  const filters = [
    ...(search.needs ? [{ label: 'Needs you', onClear: () => filter({ kind: search.kind }) }] : []),
    ...(search.kind ? [{ label: KIND[search.kind] ?? search.kind, onClear: () => filter({ needs: !!search.needs }) }] : []),
    ...(who ? [{ label: whoName || 'Who has access', onClear: () => filterWho() }] : []),
  ];

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
            href={toolHref(e.id, search)}
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
        actions={
          <>
            <Menu trigger={<IconButton icon="filter" label="Filter tools" on={filters.length > 0} />}>
              <MenuItem
                label="All tools"
                on={!filters.length}
                onSelect={() => {
                  filter({});
                  if (who) filterWho();
                }}
              />
              <MenuItem label="Needs you" on={!!search.needs} onSelect={() => filter({ kind: search.kind, needs: !search.needs })} />
              <MenuRule />
              {kinds.map((k) => (
                <MenuItem key={k} label={KIND[k] ?? k} on={k === search.kind} onSelect={() => filter({ kind: k === search.kind ? undefined : k, needs: !!search.needs })} />
              ))}
              <WhoMenu who={who} teams={teams} people={whoList} onPick={filterWho} />
            </Menu>
            <AddMenu trigger={<IconButton icon="plus" label="Add tool" />} onPick={setAdding} />
            <PageMore>
              {SETTINGS.map(([tool, label]) => (
                <MenuItem key={tool} label={label} onSelect={() => void press('tools.open', { tool })} />
              ))}
            </PageMore>
          </>
        }
        open={!!open}
        state={state}
        onRetry={() => void tools.refetch()}
        search={
          <>
            <Search label="Find a tool" value={words} onValueChange={setWords} placeholder="Find a tool" wide />
            <FilterRow filters={filters} />
          </>
        }
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
        conversation={open ? <Tool id={open} row={tools.data?.rows.find((r) => r.id === open)} problem={open === 'github' ? githubProblem : null} /> : <NoTool none={none} onAdd={setAdding} />}
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

/** The company's settings for each kind of mail, on the tool they belong to (the old Tools › Settings). */
const SETTINGS = [
  ['mailbox', 'Email settings'],
  ['number', 'Number requests'],
  ['address', 'Letter settings'],
] as const;

/* ---------------------------------------------------------------- one tool */

/** The open tool: its head, then the five sections the kind has. */
function Tool({ id, row, problem }: { id: string; row: ToolIndexRow | undefined; problem?: string | null }) {
  const at = id.indexOf(':');
  const kind = at > 0 ? id.slice(0, at) : id;
  const key = at > 0 ? id.slice(at + 1) : '';
  if (kind === 'api') return <ApiToolView id={key} row={row} />;
  if (kind === 'website') return <WebsiteView id={key} row={row} />;
  if (kind === 'vendor') return <VendorView vendor={key} />;
  if (kind === 'key' && key) return <KeyView cid={key} />;
  if (kind === 'declared' && row) return <ServiceView row={row} />;
  if (kind === 'mailbox' && (row as { mailbox_id?: string } | undefined)?.mailbox_id)
    return <MailboxView id={(row as unknown as { mailbox_id: string }).mailbox_id} row={row as ToolIndexRow} />;
  if (kind === 'mailbox' && !key) return <EmailView />;
  if (kind === 'number' && !key) return <NumbersView />;
  if (kind === 'tico' || kind === 'number') return <InboxView inbox={key} row={row} number={kind === 'number'} />;
  if (kind === 'address') return <AddressView row={row} />;
  if (kind === 'webhook' && key) return <WebhookView id={key} row={row} />;
  return <PlainView row={row} kind={kind} problem={problem} />;
}

/* ---------------------------------------------------------------- an API or MCP tool */

type Key = ApiTool['keys'][number];
type ApiDialog =
  | { is: 'edit' | 'paste' }
  | { is: 'setting'; setting: NonNullable<ApiTool['settings']>[number] | null }
  | { is: 'expiry' | 'reveal'; key: Key };

function ApiToolView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(apiToolQuery(id));
  const [keyFor, setKeyFor] = useState<Key | 'new' | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [dialog, setDialog] = useState<ApiDialog | null>(null);
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
      toast(refusal(r, 'Not connected'));
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
  const more = t?.can_manage && (
    <PageMore>
      <MenuItem label="Edit" onSelect={() => setDialog({ is: 'edit' })} />
      {!mcp && <MenuItem label="Add many keys" onSelect={() => setDialog({ is: 'paste' })} />}
      <MenuItem label="Add run setting" onSelect={() => setDialog({ is: 'setting', setting: null })} />
    </PageMore>
  );

  return (
    <Shell
      name={name}
      kind="api"
      mcp={!!mcp}
      lock={lock}
      problem={problem}
      fill={fill}
      more={more}
      onRename={t?.can_manage ? (next) => void press('tools.editApi', { tool: id, name: next }, 'Not renamed') : undefined}
      state={stateOf(q)}
      onRetry={() => void q.refetch()}
      target={`tool:api:${id}`}
    >
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
                <LabelValue key={s.id} label={s.name} onEdit={t.can_manage ? () => setDialog({ is: 'setting', setting: s }) : undefined}>
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
                    <Menu trigger={<IconButton icon="more" label={`${k.name} options`} size="sm" />}>
                      <MenuItem label="Show key" onSelect={() => setDialog({ is: 'reveal', key: k })} />
                      {k.expires_source !== 'vendor' && <MenuItem label="Set end date" onSelect={() => setDialog({ is: 'expiry', key: k })} />}
                      {k.can_remove !== false && (
                        <>
                          <MenuRule />
                          <MenuItem label="Remove key" onSelect={() => void press('tools.removeKey', { tool: id, key: k.id }, 'Not removed')} />
                        </>
                      )}
                    </Menu>
                  </>
                )}
              </div>
            ))}
            {t.can_manage && t.keys.length > 0 && <More onClick={() => void press('tools.check', { tool: id }, 'Not checked')}>Check now</More>}
          </Section>
          {(t.access.length > 0 || t.can_manage) && (
            <Section
              name="Who may use it"
              count={t.access.length || undefined}
              target={`tool-who:${id}`}
              actions={t.can_manage && <AddWho tool={id} taken={t.access.map((a) => a.subject)} />}
            >
              {t.everyone_reads?.enforceable && <EveryoneReads tool={id} on={t.everyone_reads.on} can={t.can_manage} />}
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
          <SeenBy kind="api" tool={id} t={t} />
          {mcp && mcp.names.length > 0 && <McpNames names={mcp.names} />}
          {t.recent.length > 0 && (
            <Section name="Recent use" target={`tool-recent:${id}`}>
              {t.recent.map((r, n) => (
                <CallLine key={String(r.id ?? n)} call={r} />
              ))}
            </Section>
          )}
          {keyFor && <KeyDialog tool={id} name={name} keyRow={keyFor === 'new' ? null : keyFor} onClose={() => setKeyFor(null)} />}
          {dialog?.is === 'edit' && <EditApiDialog tool={id} settings={t.tool} onClose={() => setDialog(null)} />}
          {dialog?.is === 'paste' && <PasteDialog tool={id} onClose={() => setDialog(null)} />}
          {dialog?.is === 'setting' && <SettingDialog tool={id} setting={dialog.setting} onClose={() => setDialog(null)} />}
          {dialog?.is === 'expiry' && <ExpiryDialog tool={id} keyRow={dialog.key} onClose={() => setDialog(null)} />}
          {dialog?.is === 'reveal' && <RevealDialog credential={dialog.key.id} title={dialog.key.name} onClose={() => setDialog(null)} />}
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

type SiteDialog = { is: 'sites' | 'delete' } | { is: 'password' | 'code'; account: string } | { is: 'remove'; account: string; name: string };

/** Who may use an API tool, one more: a teammate not listed yet, at Read (tools-api.ts taWhoCard's Add). Its level
   changes on its row after. */
function AddWho({ tool, taken }: { tool: string; taken: string[] }) {
  const dir = useQuery(directoryQuery).data;
  const left = (dir?.actors ?? []).filter((a) => !taken.includes(a.id));
  if (!left.length) return null;
  return (
    <Menu trigger={<IconButton icon="plus" label="Add someone" size="sm" />}>
      {left.map((a) => (
        <MenuItem key={a.id} label={a.name} onSelect={() => void press('tools.setAccess', { tool, subject: a.id, level: 'read' }, 'Not added')} />
      ))}
    </Menu>
  );
}

function WebsiteView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(websiteToolQuery(id));
  const t = q.data;
  const name = t?.tool.name ?? row?.name ?? 'Website';
  const lock = !!t?.accounts.some((a) => a.has_password);
  const [adding, setAdding] = useState(false);
  const [replacing, setReplacing] = useState<WebsiteTool['accounts'][number] | null>(null);
  const [dialog, setDialog] = useState<SiteDialog | null>(null);
  const [browsing, setBrowsing] = useState<string | null>(null);
  return (
    <Shell
      name={name}
      kind="website"
      lock={lock}
      onRename={t?.can_manage ? (next) => void press('tools.editWebsite', { tool: id, name: next }, 'Not renamed') : undefined}
      more={
        t?.can_manage && (
          <PageMore>
            <MenuItem label="Delete website" onSelect={() => setDialog({ is: 'delete' })} />
          </PageMore>
        )
      }
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
              <LabelValue label={t.tool.sites.length > 1 ? 'Sites' : 'Site'} onEdit={t.can_manage ? () => setDialog({ is: 'sites' }) : undefined}>
                <Code>{t.tool.sites.join(', ')}</Code>
              </LabelValue>
              <CentralOnly tool={id} on={t.tool.central_only} can={t.can_manage} />
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
                  {a.can_open && (
                    <Button variant="quiet" onClick={() => setBrowsing(a.id)}>
                      Open browser
                    </Button>
                  )}
                  {((a.can_use && (a.has_password || a.has_totp)) || a.can_remove) && (
                    <Menu trigger={<IconButton icon="more" label={`${a.username || 'Account'} options`} size="sm" />}>
                      {a.can_use && a.has_password && <MenuItem label="Show password" onSelect={() => setDialog({ is: 'password', account: a.id })} />}
                      {a.can_use && a.has_totp && <MenuItem label="Show sign-in code" onSelect={() => setDialog({ is: 'code', account: a.id })} />}
                      {a.can_remove && (
                        <>
                          <MenuRule />
                          <MenuItem label="Remove account" onSelect={() => setDialog({ is: 'remove', account: a.id, name: a.username || a.holder?.name || 'Account' })} />
                        </>
                      )}
                    </Menu>
                  )}
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
          <AccountAccess tool={id} t={t} />
          <SeenBy kind="website" tool={id} t={t} />
          {adding && <AddAccountDialog tool={id} onClose={() => setAdding(false)} />}
          {replacing && <ReplaceLoginDialog tool={id} account={replacing} onClose={() => setReplacing(null)} />}
          {dialog?.is === 'sites' && <SitesDialog tool={id} sites={t.tool.sites} onClose={() => setDialog(null)} />}
          {dialog?.is === 'password' && <RevealDialog credential={dialog.account} title="Password" onClose={() => setDialog(null)} />}
          {dialog?.is === 'code' && <CodeDialog tool={id} account={dialog.account} onClose={() => setDialog(null)} />}
          {dialog?.is === 'remove' && <RemoveAccountConfirm tool={id} account={dialog.account} name={dialog.name} onClose={() => setDialog(null)} />}
          {dialog?.is === 'delete' && <DeleteWebsiteConfirm tool={id} name={name} onClose={() => setDialog(null)} />}
          {browsing && <BrowserDialog tool={id} account={browsing} title={name} onClose={() => setBrowsing(null)} />}
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

/** Email itself, before or beside any one mailbox: the Google connection, what agents may do and send. */
function EmailView() {
  return (
    <Shell name="Email" kind="mailbox" target="tool:mailbox">
      <GoogleSection />
      <MayDo />
      <SendingSection />
    </Shell>
  );
}

/** Numbers asked for: each waits on a price to accept, or is canceled. */
function NumbersView() {
  return (
    <Shell name="Phone numbers" kind="number" target="tool:number">
      <NumberRequests always />
    </Shell>
  );
}

/* ---------------------------------------------------------------- a Tico address or phone number */

function InboxView({ inbox, row, number }: { inbox: string; row: ToolIndexRow | undefined; number: boolean }) {
  const boxes = useQuery(inboxesQuery);
  const messages = useQuery(inboxMessagesQuery(inbox));
  const box = boxes.data?.find((b) => b.inbox_id === inbox);
  const name = box?.name ?? row?.name ?? (number ? 'Phone number' : 'Tico address');
  const [dialog, setDialog] = useState<'edit' | 'release' | 'join' | null>(null);
  const [reading, setReading] = useState<{ id: string; title: string } | null>(null);
  const more = box && (
    <PageMore>
      <MenuItem label="Edit" onSelect={() => setDialog('edit')} />
      {!number && <MenuItem label="Join shared Inbox" onSelect={() => setDialog('join')} />}
      <MenuRule />
      <MenuItem label="Stop using" onSelect={() => setDialog('release')} />
    </PageMore>
  );
  return (
    <Shell
      name={name}
      kind={number ? 'number' : 'mailbox'}
      problem={row && bad(row)}
      more={more}
      state={stateOf(boxes)}
      onRetry={() => void boxes.refetch()}
      target={`tool:${row?.id ?? inbox}`}
    >
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{number ? KIND.number : KIND.tico}</LabelValue>
          <LabelValue label={number ? 'Number' : 'Address'}>
            <Code>{box?.address ?? row?.detail ?? ''}</Code>
          </LabelValue>
          {box && <InboxReaders box={box} />}
          {box?.used_for && <LabelValue label="Used for">{box.used_for}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects">
        <Facts>
          <LabelValue label="Through">Tico</LabelValue>
          <LabelValue label="State">{row?.status.text ?? box?.state ?? ''}</LabelValue>
        </Facts>
        <CheckInbox />
      </Section>
      {(messages.data?.length ?? 0) > 0 && (
        <Section name="Recent use">
          {messages.data?.map((m) => {
            const from = typeof m.from === 'string' ? m.from : '';
            const what = (typeof m.subject === 'string' && m.subject) || (typeof m.preview === 'string' && m.preview) || from;
            const at = typeof m.received_at === 'number' ? new Date(m.received_at * 1000) : null;
            const id = typeof m.message_id === 'string' ? m.message_id : null;
            return (
              <button
                key={String(m.message_id ?? m.seq)}
                type="button"
                className="row urow"
                title={what}
                disabled={!id}
                onClick={() => id && setReading({ id, title: what || 'Message' })}
              >
                <span className="urow-lead" title="Came in">
                  <Icon name="download" />
                </span>
                <span className="row-title">{what}</span>
                <span className="row-ctx">{from}</span>
                {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
              </button>
            );
          })}
        </Section>
      )}
      {number && <NumberRequests />}
      {reading && <MessageDialog message={reading.id} title={reading.title} onClose={() => setReading(null)} />}
      {dialog === 'join' && <SharedInboxDialog onClose={() => setDialog(null)} />}
      {box && dialog === 'edit' && <EditInboxDialog box={box} onClose={() => setDialog(null)} />}
      {box && dialog === 'release' && <ReleaseDialog box={box} number={number} onClose={() => setDialog(null)} />}
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
      <PostalAddresses />
      <LettersOutSection />
      <LettersInSettings />
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

/* ---------------------------------------------------------------- Outgoing webhooks */

const SENT: Record<string, string> = {
  ready: 'Waiting',
  sending: 'Sending',
  delivered: 'Delivered',
  failed: 'Failed',
  canceled: 'Canceled',
  withheld: 'Held back',
};

function WebhookView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const hook = useQuery(webhookQuery(id));
  const sends = useQuery(webhookSendsQuery(id));
  const w = hook.data?.webhook;
  const paused = !!w?.paused_reason;
  const more = w?.can_change && (
    <PageMore>
      <MenuItem label="Send test" onSelect={() => void press('tools.testWebhook', { webhook: id }, 'The test did not send')} />
      <MenuItem
        label={paused ? 'Send again' : 'Pause'}
        onSelect={() => void press('tools.pauseWebhook', { webhook: id, on: paused }, paused ? 'It did not restart' : 'It did not pause')}
      />
    </PageMore>
  );
  return (
    <Shell
      name={w?.name ?? row?.name ?? 'Outgoing webhook'}
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
            <div key={x.id} className="row urow" title={x.last_error || undefined}>
              <span className="row-title">{x.subject || x.type}</span>
              <span className="row-ctx">{x.last_status ? `${SENT[x.state] ?? x.state} (${x.last_status})` : (SENT[x.state] ?? x.state)}</span>
              <Ago at={x.delivered_at ?? x.created_at} className="row-age" />
            </div>
          ))}
        </Section>
      )}
    </Shell>
  );
}

/* ---------------------------------------------------------------- Slack, GitHub, meetings... */

function PlainView({ row, kind, problem }: { row: ToolIndexRow | undefined; kind: string; problem?: string | null }) {
  const owner = useQuery(meQuery).data?.role === 'owner';
  if (!row && !problem) return <Shell name="Tool" kind={kind} state="loading" target={`tool:${kind}`} />;
  // GitHub: the owner gets the App's own state and fixes; everyone else the plain state.
  const github = kind === 'github' && owner ? <GithubConnection /> : null;
  // A GitHub return that did not work (useGithubReturn): what happened, and the install when the App waits for it.
  const returned = problem && (
    <Section name="Not connected">
      <p className="t-read">{problem}</p>
      {!github && row?.kind === 'github' && row.status.tone === 'wait' && <Button onClick={() => void press('tools.installGithub', {})}>Install</Button>}
    </Section>
  );
  if (!row)
    return (
      <Shell name={KIND[kind] ?? 'Tool'} kind={kind} target={`tool:${kind}`}>
        {returned}
        {github}
      </Shell>
    );
  return (
    <Shell name={row.name} kind={row.kind} problem={bad(row)} target={`tool:${row.id}`}>
      {returned}
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{KIND[row.kind] ?? row.kind}</LabelValue>
          {row.detail && <LabelValue label="About">{row.detail}</LabelValue>}
        </Facts>
      </Section>
      {github ?? (
        <Section name="How it connects">
          <Facts>
            {row.way && <LabelValue label="Through">{row.way}</LabelValue>}
            <LabelValue label="State">{row.status.text}</LabelValue>
          </Facts>
        </Section>
      )}
      {row.who.length > 0 && (
        <Section name="Who may use it" count={row.who_total}>
          {row.who.map((p) => (
            <Who key={p.id} actor={p.kind === 'group' ? null : p.id} name={p.name} />
          ))}
        </Section>
      )}
      {row.kind === 'slack' && <SlackLinks />}
    </Shell>
  );
}
