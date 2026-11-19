import { useQuery, useSuspenseQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { ApiTool, ToolIndexRow, WebsiteTool } from '@/api/schema';
import { meQuery, useActors, useDirectory } from '@/data';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field, Search } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { useCards } from '@/kit/Popover';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { GithubConnection } from '@/features/start/exports';
import { BrowserDialog } from '@/features/team/exports';
import { FilterRow, ListAndItem, PageMore, Section } from '@/pagekinds';
import { AddAccountDialog, AddDialog, AddItems, AddMenu, ReplaceLoginDialog, type Adding } from './AddTool';
import { CodeDialog, Confirm, EditApiDialog, ExpiryDialog, PasteDialog, RevealDialog, SettingDialog, SitesDialog } from './ToolDialogs';
import { Face, Held, idsAndNames, KindTile, More, OwnerButton, press, when } from './parts';
import { apiToolQuery, teamsQuery, toolsQuery, vaultQuery, websiteToolQuery, type StoredKey } from './queries';
import { AddressOne, CompanyAddress } from './ToolLetters';
import { EmailView, hasWho, InboxView, KeyView, MailboxView, MessagesView, NumbersView, ServiceView, subjectOf, WebhookView, WhoMenu } from './ToolMore';
import { MayFacts, useSending } from './ToolSettings';
import { AddSomeone, bad, Code, Dim, KIND, ToolPage, toolHref, UseRow, WhoRow } from './ToolShell';
import { useGithubReturn } from './githubReturn';
import type { ToolsSearch } from './search';
import './library.css';

export { KIND } from './ToolShell';

/* Tools (tools.js toolsScreen, detail): the list of tools, In use then Available, beside the open one. Every tool is
   one page (ToolShell.tsx ToolPage): what it is, how it connects, who may use it, what it can do, recent use. A lock
   means Tico holds the key. A key is only ever sent, never shown: its row shows a mask. */

const held = (r: ToolIndexRow) => r.kind === 'api' && r.delivery === 'proxy';
const host = (url: string) => url.replace(/^https?:\/\//, '').split('/')[0];

/** The rows the filter keeps: one kind, and (with `needs`) only those that need someone. */
export const narrow = (rows: ToolIndexRow[], s: Pick<ToolsSearch, 'kind' | 'needs'>) => rows.filter((r) => (!s.kind || r.kind === s.kind) && (!s.needs || !!bad(r)));

/** One row of the list: a tool, or a stored key no tool holds. */
export interface Entry {
  id: string;
  name: string;
  kind: string;
  lock: boolean;
  inUse: boolean;
  problem: string | null;
}
const toolEntry = (r: ToolIndexRow): Entry => ({ id: r.id, name: r.name, kind: r.kind, lock: held(r), inUse: r.who_total > 0, problem: bad(r) });
/** A stored key no tool holds: its own row, a Key, in use while it is given to someone. */
const keyEntry = (k: StoredKey): Entry => ({ id: `key:${k.id}`, name: k.name, kind: 'key', lock: k.stored, inUse: k.grants.length > 0, problem: k.stored ? null : 'No value' });
/** Something wrong first, then by name. */
export const order = (a: Entry, b: Entry) => Number(!!b.problem) - Number(!!a.problem) || a.name.localeCompare(b.name);

export function ToolsPage({ search }: { search: ToolsSearch }) {
  const { data: tools } = useSuspenseQuery(toolsQuery);
  const githubProblem = useGithubReturn(search);
  const [words, setWords] = useState(search.q ?? '');
  const w = words.trim().toLowerCase();
  // Keys no tool holds, for those who may see the vault (anyone else gets none, quietly).
  const loose = (useQuery(vaultQuery).data?.credentials ?? []).filter((k) => !k.tool_id);
  const teams = useQuery(teamsQuery).data ?? [];
  const known = useDirectory(idsAndNames);
  const same = (x: string) => known?.[x]?.id ?? x;
  const who = search.who;
  const all = tools.rows;
  const rows = narrow(all, search).filter(
    (r) =>
      (!w || [r.name, r.detail, r.vendor_name].some((x) => (x ?? '').toLowerCase().includes(w))) &&
      // `who` may be missing from an older index row.
      (!who || hasWho((r.who as ToolIndexRow['who'] | undefined)?.map(subjectOf) ?? [], who, teams, same)),
  );
  const keys = loose.filter(
    (k) =>
      (!search.kind || search.kind === 'key') &&
      (!search.needs || !k.stored) &&
      (!w || [k.name, k.env, k.source].some((x) => (x ?? '').toLowerCase().includes(w))) &&
      (!who || hasWho(k.grants.map((g) => g.subject), who, teams, same)),
  );
  const shown = [...rows.map(toolEntry), ...keys.map(keyEntry)].sort(order);
  const open = search.tool;
  const [adding, setAdding] = useState<Adding | null>(null);
  const cards = useCards();
  const kinds = [...new Set([...all.map((r) => r.kind as string), ...(loose.length ? ['key'] : [])])].sort((a, b) => (KIND[a] ?? a).localeCompare(KIND[b] ?? b));
  const filter = (change: { kind?: string; needs?: boolean }) => void press('tools.filter', change);
  const filterWho = (next?: string) => void press('tools.filterWho', next ? { who: next } : {});
  // Who has access: the teams, then everyone a tool or a key is given to.
  const people = new Map<string, string>();
  for (const r of all) for (const p of (r.who as ToolIndexRow['who'] | undefined) ?? []) if (p.kind !== 'group') people.set(same(subjectOf(p)), p.name);
  for (const k of loose) for (const g of k.grants) if (/^(human|bot):/.test(g.subject)) people.set(same(g.subject), known?.[g.subject]?.name ?? g.subject);
  const whoList = [...people].map(([subject, name]) => ({ subject, name })).sort((a, b) => a.name.localeCompare(b.name));
  const whoName = who && (who.startsWith('group:') ? teams.find((g) => `group:${g.id}` === who)?.name : (people.get(same(who)) ?? known?.[who]?.name));
  const filters = [
    ...(search.needs ? [{ label: 'Needs you', onClear: () => filter({ kind: search.kind }) }] : []),
    ...(search.kind ? [{ label: KIND[search.kind] ?? search.kind, onClear: () => filter({ needs: !!search.needs }) }] : []),
    ...(who ? [{ label: whoName || 'Who has access', onClear: () => filterWho() }] : []),
  ];

  const group = (name: string, list: Entry[]) =>
    list.length > 0 && (
      <Section name={name} count={list.length}>
        {list.map((e) => (
          <Row key={e.id} className={e.inUse ? 'tl-row trow' : 'tl-row trow is-off'} title={e.name} href={toolHref(e.id, search)} selected={e.id === open} target={`tool:${e.id}`}>
            <KindTile kind={e.kind} />
            <span className="trow-name">
              <span className="row-title">{e.name}</span>
              {e.lock && e.kind !== 'key' && <Held />}
            </span>
            {e.problem ? <Pill>{e.problem}</Pill> : <span />}
          </Row>
        ))}
      </Section>
    );

  return (
    <>
      <ListAndItem
        title="Tools"
        path="/library/tools"
        className="lib tools"
        actions={
          <>
            <Menu trigger={<IconButton icon="filter" label="Filter tools" on={filters.length > 0} data-phone />}>
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
            {!cards && <AddMenu trigger={<IconButton icon="plus" label="Add tool" />} onPick={setAdding} />}
          </>
        }
        // A phone's head keeps Filter and the ⋯; Add tool then moves into the ⋯.
        more={
          <PageMore>
            {cards && (
              <MenuSub label="Add tool" icon="plus">
                <AddItems onPick={setAdding} />
              </MenuSub>
            )}
            <MenuItem label="Messages" onSelect={() => void press('tools.open', { tool: 'messages' })} />
            {SETTINGS.map(([tool, label]) => (
              <MenuItem key={tool} label={label} onSelect={() => void press('tools.open', { tool })} />
            ))}
          </PageMore>
        }
        open={!!open}
        state={!all.length && !loose.length ? 'empty' : 'ready'}
        empty={{ title: 'No tools yet', action: <AddMenu trigger={<Button variant="primary">Add tool</Button>} onPick={setAdding} /> }}
        search={
          <>
            <Search label="Find a tool" value={words} onValueChange={setWords} placeholder="Find a tool" wide />
            <FilterRow filters={filters} />
          </>
        }
        list={
          <div className="tools-list">
            {group(
              'In use',
              shown.filter((e) => e.inUse),
            )}
            {group(
              'Available',
              shown.filter((e) => !e.inUse),
            )}
            {!shown.length && <Empty title="Nothing matches" />}
          </div>
        }
        conversation={
          open ? (
            <Tool id={open} row={all.find((r) => r.id === open)} problem={open === 'github' ? githubProblem : null} />
          ) : (
            <section className="tool is-empty" aria-label="Tools">
              <header className="conv-head tool-head">
                <span className="grow" />
                <OwnerButton page="tools" />
              </header>
            </section>
          )
        }
      />
      {adding && <AddDialog adding={adding} onClose={() => setAdding(null)} />}
    </>
  );
}

/** The company's settings for each kind of mail, on the tool they belong to (the old Tools › Settings). */
const SETTINGS = [
  ['mailbox', 'Email settings'],
  ['number', 'Number requests'],
  ['address', 'Letter settings'],
] as const;

/** The open tool: which kind fills the one page. */
function Tool({ id, row, problem }: { id: string; row: ToolIndexRow | undefined; problem?: string | null }) {
  const at = id.indexOf(':');
  const kind = at > 0 ? id.slice(0, at) : id;
  const key = at > 0 ? id.slice(at + 1) : '';
  const mailbox = (row as { mailbox_id?: string } | undefined)?.mailbox_id;
  if (kind === 'api') return <ApiToolView id={key} row={row} />;
  if (kind === 'website') return <WebsiteView id={key} row={row} />;
  if (kind === 'key' && key) return <KeyView cid={key} />;
  if (kind === 'declared' && row) return <ServiceView row={row} />;
  if (kind === 'mailbox' && mailbox && row) return <MailboxView id={mailbox} row={row} />;
  if (kind === 'mailbox' && !key) return <EmailView />;
  if (kind === 'number' && !key) return <NumbersView />;
  if (kind === 'tico' || kind === 'number') return <InboxView inbox={key} row={row} number={kind === 'number'} />;
  if (kind === 'address') return key ? <AddressOne id={key} /> : <CompanyAddress row={row} />;
  if (kind === 'messages') return <MessagesView />;
  if (kind === 'webhook' && key) return <WebhookView id={key} row={row} />;
  return <PlainView row={row} kind={kind} problem={problem} />;
}

/* ---------------------------------------------------------------- an API or MCP tool */

type Key = ApiTool['keys'][number];
type ApiDialog = { is: 'edit' | 'paste' } | { is: 'setting'; setting: NonNullable<ApiTool['settings']>[number] | null } | { is: 'expiry' | 'reveal'; key: Key };

function ApiToolView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(apiToolQuery(id));
  const actors = useActors();
  const [keyFor, setKeyFor] = useState<Key | 'new' | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [dialog, setDialog] = useState<ApiDialog | null>(null);
  const [allNames, setAllNames] = useState(false);
  const t = q.data;
  const mcp = t?.mcp ?? null;
  const name = t?.tool.name ?? row?.name ?? 'Tool';
  const problem = t && (t.status.tone === 'fail' ? t.status.text : null);
  const oauth = mcp?.auth === 'oauth';
  const manage = !!t?.can_manage;
  const last = t?.checks[0];
  const names = mcp?.names ?? [];

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
  return (
    <ToolPage
      name={name}
      kind="api"
      kindWord={mcp ? KIND.mcp : 'API key'}
      mcp={!!mcp}
      lock={!!t && t.tool.delivery === 'proxy' && t.keys.length > 0}
      problem={problem}
      q={q}
      target={`tool:api:${id}`}
      onRename={manage ? (next) => void press('tools.editApi', { tool: id, name: next }, 'Not renamed') : undefined}
      fill={
        manage &&
        (!t?.keys.length || problem) && (
          <Button variant="primary" busy={connecting} disabled={connecting} onClick={() => void connect()} data-target={`tool-connect:api:${id}`}>
            {t?.keys.length && !oauth ? 'Replace key' : 'Connect'}
          </Button>
        )
      }
      more={
        manage && (
          <PageMore>
            <MenuItem label="Edit" onSelect={() => setDialog({ is: 'edit' })} />
            {!mcp && <MenuItem label="Add many keys" onSelect={() => setDialog({ is: 'paste' })} />}
            <MenuItem label="Add run setting" onSelect={() => setDialog({ is: 'setting', setting: null })} />
          </PageMore>
        )
      }
      about={
        t && (
          <>
            <LabelValue label="Address">
              <Code>{mcp ? host(mcp.url) : host(t.tool.base_url)}</Code>
            </LabelValue>
            {mcp?.tools != null && <LabelValue label="Tools">{mcp.read_only ? `${mcp.tools}, ${mcp.read_only} read-only` : String(mcp.tools)}</LabelValue>}
            {t.settings?.map((s) => (
              <LabelValue key={s.id} label={s.name} onEdit={manage ? () => setDialog({ is: 'setting', setting: s }) : undefined}>
                {s.value}
              </LabelValue>
            ))}
          </>
        )
      }
      connects={
        t && (
          <>
            <Facts>
              {mcp?.signed_in_at && (
                <LabelValue label="Connected">
                  {mcp.signed_in_by && <Face actor={mcp.signed_in_by} />}
                  {when(mcp.signed_in_at)}
                </LabelValue>
              )}
              {!t.keys.length && !mcp?.signed_in_at && <LabelValue label="Connected">Not yet</LabelValue>}
              {!mcp && t.keys.length > 0 && <LabelValue label="Key goes in">{t.tool.auth_scheme}</LabelValue>}
              {last && (
                <LabelValue label="Checked">
                  <Code>{last.status_code ? `${t.tool.check_path || 'Check'} → ${last.status_code}` : last.detail || (last.ok ? 'Works' : 'Failed')}</Code>{' '}
                  <Dim>{when(last.checked_at)}</Dim>
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
                {manage && <IconButton icon="edit" label="Replace key" size="sm" onClick={() => setKeyFor(k)} />}
                {/* Show key is offered to anyone who sees the key: the server lets whoever it is given to read it. */}
                <Menu trigger={<IconButton icon="more" label={`${k.name} options`} size="sm" />}>
                  <MenuItem label="Show key" onSelect={() => setDialog({ is: 'reveal', key: k })} />
                  {manage && k.expires_source !== 'vendor' && <MenuItem label="Set end date" onSelect={() => setDialog({ is: 'expiry', key: k })} />}
                  {(k.can_remove ?? manage) && (
                    <>
                      <MenuRule />
                      <MenuItem label="Remove key" onSelect={() => void press('tools.removeKey', { tool: id, key: k.id }, 'Not removed')} />
                    </>
                  )}
                </Menu>
              </div>
            ))}
          </>
        )
      }
      connectsActions={
        manage && (
          <>
            {!!t?.keys.length && <IconButton icon="refresh" label="Check now" size="sm" onClick={() => void press('tools.check', { tool: id }, 'Not checked')} />}
            {!oauth && <IconButton icon="plus" label="Add key" size="sm" onClick={() => setKeyFor('new')} />}
          </>
        )
      }
      who={t?.access.map((a) => (
        <WhoRow
          key={a.subject}
          actor={a.kind === 'finance' ? null : a.subject}
          name={a.name}
          at={a.last_used_at}
          level={a.level}
          onLevel={manage && !a.fixed ? (level) => void press('tools.setAccess', { tool: id, subject: a.subject, level }) : undefined}
        />
      ))}
      whoCount={t?.access.length || undefined}
      whoActions={
        manage && (
          <AddSomeone
            choices={actors.filter((a) => !t?.access.some((x) => x.subject === a.id))}
            onPick={(subject) => void press('tools.setAccess', { tool: id, subject, level: 'read' }, 'Not added')}
          />
        )
      }
      can={
        names.length > 0 && (
          <>
            {(allNames ? names : names.slice(0, 8)).map((n) => (
              <div key={n} className="row arow" title={n}>
                <span className="row-title is-code">{n}</span>
              </div>
            ))}
            {!allNames && names.length > 8 && <More onClick={() => setAllNames(true)}>{`${names.length - 8} more`}</More>}
          </>
        )
      }
      recent={t?.recent.map((r, n) => {
        const str = (k: string) => (typeof r[k] === 'string' ? (r[k] as string) : '');
        const what = str('method') ? `${str('method')} ${str('path')}${r.status_code ? ` → ${String(r.status_code)}` : ''}` : str('detail') || str('kind');
        return <UseRow key={String(r.id ?? n)} title={what} code result={str('result')} actor={str('bot') ? `bot:${str('bot')}` : str('actor') || null} at={str('at') || null} />;
      })}
    >
      {t && keyFor && <KeyDialog tool={id} name={name} keyRow={keyFor === 'new' ? null : keyFor} onClose={() => setKeyFor(null)} />}
      {t && dialog?.is === 'edit' && <EditApiDialog tool={id} settings={t.tool} onClose={() => setDialog(null)} />}
      {dialog?.is === 'paste' && <PasteDialog tool={id} onClose={() => setDialog(null)} />}
      {dialog?.is === 'setting' && <SettingDialog tool={id} setting={dialog.setting} onClose={() => setDialog(null)} />}
      {dialog?.is === 'expiry' && <ExpiryDialog tool={id} keyRow={dialog.key} onClose={() => setDialog(null)} />}
      {dialog?.is === 'reveal' && <RevealDialog credential={dialog.key.id} title={dialog.key.name} onClose={() => setDialog(null)} />}
    </ToolPage>
  );
}

/** Add or replace a key. The field only sends: a stored key is never read back, so it starts empty. */
function KeyDialog({ tool, name, keyRow, onClose }: { tool: string; name: string; keyRow: Key | null; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const ok = await press('tools.setKey', { tool, key: keyRow?.id, name: keyRow ? undefined : label.trim() || undefined, value: value.trim() }, keyRow ? 'Not replaced' : 'Not added');
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

type Account = WebsiteTool['accounts'][number];
type SiteDialog = { is: 'sites' | 'delete' | 'add' } | { is: 'password' | 'code' | 'browse'; account: string } | { is: 'remove' | 'replace'; account: Account };

function WebsiteView({ id, row }: { id: string; row: ToolIndexRow | undefined }) {
  const q = useQuery(websiteToolQuery(id));
  const actors = useActors();
  const teams = useQuery(teamsQuery).data ?? [];
  const [dialog, setDialog] = useState<SiteDialog | null>(null);
  const t = q.data;
  const name = t?.tool.name ?? row?.name ?? 'Website';
  const label = (a: Account) => a.username || a.holder?.name || 'Account';
  // Who may use each shared account: a person, an agent, a team or every agent; given here or taken away.
  const editable = (a: Account) => a.can_manage && a.kind === 'shared';
  const accounts = t?.accounts ?? [];
  const many = accounts.length > 1;
  const give = (a: Account, subject: string) => void press('tools.setAccountAccess', { tool: id, account: a.id, subject, on: true }, 'Not added');
  const choices = (a: Account) => {
    const has = new Set(a.access.map((p) => p.subject));
    return [
      ...(a.all_bots ? [] : [{ id: 'bots', name: 'All agents' }]),
      ...teams.filter((g) => !has.has(`group:${g.id}`)).map((g) => ({ id: `group:${g.id}`, name: g.name })),
      ...actors.filter((x) => !has.has(x.id)),
    ].map((c) => <MenuItem key={c.id} label={c.name} onSelect={() => give(a, c.id)} />);
  };
  const open = accounts.filter(editable);
  return (
    <ToolPage
      name={name}
      kind="website"
      lock={accounts.some((a) => a.has_password)}
      problem={t?.status.tone === 'fail' ? t.status.label : null}
      q={q}
      target={`tool:website:${id}`}
      onRename={t?.can_manage ? (next) => void press('tools.editWebsite', { tool: id, name: next }, 'Not renamed') : undefined}
      more={
        t?.can_manage && (
          <PageMore>
            <MenuItem label="Delete website" onSelect={() => setDialog({ is: 'delete' })} />
          </PageMore>
        )
      }
      about={
        t && (
          <LabelValue label={t.tool.sites.length > 1 ? 'Sites' : 'Site'} onEdit={t.can_manage ? () => setDialog({ is: 'sites' }) : undefined}>
            <Code>{t.tool.sites.join(', ')}</Code>
          </LabelValue>
        )
      }
      connects={
        <Facts>
          {accounts.map((a) => (
            <LabelValue key={a.id} label="Account">
              {label(a)} <Dim>{a.kind === 'shared' ? 'Shared' : 'Own'}</Dim>{' '}
              <Button variant="quiet" onClick={() => setDialog({ is: 'replace', account: a })}>
                Replace login
              </Button>
              {a.can_open && (
                <Button variant="quiet" onClick={() => setDialog({ is: 'browse', account: a.id })}>
                  Open browser
                </Button>
              )}
              {((a.can_use && (a.has_password || a.has_totp)) || a.can_remove) && (
                <Menu trigger={<IconButton icon="more" label={`${label(a)} options`} size="sm" />}>
                  {a.can_use && a.has_password && <MenuItem label="Show password" onSelect={() => setDialog({ is: 'password', account: a.id })} />}
                  {a.can_use && a.has_totp && <MenuItem label="Show sign-in code" onSelect={() => setDialog({ is: 'code', account: a.id })} />}
                  {a.can_remove && (
                    <>
                      <MenuRule />
                      <MenuItem label="Remove account" onSelect={() => setDialog({ is: 'remove', account: a })} />
                    </>
                  )}
                </Menu>
              )}
            </LabelValue>
          ))}
          {accounts.some((a) => a.has_password) && (
            <LabelValue label="Password">
              <Code>••••••••</Code> <Held />
            </LabelValue>
          )}
          {accounts.some((a) => a.has_totp) && (
            <LabelValue label="Sign-in codes">
              Authenticator <Held />
            </LabelValue>
          )}
          {t?.host && (
            <LabelValue label="Runs on">
              {t.host.label} <Dim>{t.host.online ? 'Online' : 'Offline'}</Dim>
            </LabelValue>
          )}
        </Facts>
      }
      connectsActions={<IconButton icon="plus" label="Add account" size="sm" onClick={() => setDialog({ is: 'add' })} />}
      who={accounts.flatMap((a) => [
        ...(a.all_bots ? [{ subject: 'bots', name: 'All agents', actor: null as string | null, via: false }] : []),
        ...a.access.map((p) => ({ subject: p.subject, name: p.name, actor: p.kind === 'group' ? null : p.subject, via: !!p.via?.length })),
      ].map((r) => (
        <WhoRow
          key={`${a.id}:${r.subject}`}
          actor={r.actor}
          name={r.name}
          right={many && <span className="row-ctx">{label(a)}</span>}
          onRemove={editable(a) && !r.via ? () => void press('tools.setAccountAccess', { tool: id, account: a.id, subject: r.subject, on: false }, 'Not removed') : undefined}
        />
      )))}
      whoActions={
        open.length > 0 && (
          <Menu trigger={<IconButton icon="plus" label="Add someone" size="sm" />}>
            {open.length === 1 && open[0]
              ? choices(open[0])
              : open.map((a) => (
                  <MenuSub key={a.id} label={label(a)}>
                    {choices(a)}
                  </MenuSub>
                ))}
          </Menu>
        )
      }
      recent={t?.recent.map((r) => <UseRow key={r.id} title={`${VERB[r.verb]} ${r.page}`} result={r.result} actor={r.who.subject} at={r.at} />)}
    >
      {dialog?.is === 'add' && <AddAccountDialog tool={id} onClose={() => setDialog(null)} />}
      {dialog?.is === 'replace' && <ReplaceLoginDialog tool={id} account={dialog.account} onClose={() => setDialog(null)} />}
      {t && dialog?.is === 'sites' && <SitesDialog tool={id} sites={t.tool.sites} onClose={() => setDialog(null)} />}
      {dialog?.is === 'password' && <RevealDialog credential={dialog.account} title="Password" onClose={() => setDialog(null)} />}
      {dialog?.is === 'code' && <CodeDialog tool={id} account={dialog.account} onClose={() => setDialog(null)} />}
      {dialog?.is === 'browse' && <BrowserDialog tool={id} account={dialog.account} title={name} onClose={() => setDialog(null)} />}
      {dialog?.is === 'remove' && (
        <Confirm
          title={`Remove ${label(dialog.account)}?`}
          description="Its login and who may use it go. This cannot be undone."
          label="Remove"
          onGo={() => press('tools.removeAccount', { tool: id, account: dialog.account.id }, 'Not removed')}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.is === 'delete' && (
        <Confirm
          title={`Delete ${name}?`}
          description="Its logins and who may use them go. This cannot be undone."
          label="Delete"
          onGo={() => press('tools.deleteWebsite', { tool: id }, 'Not deleted')}
          onClose={() => setDialog(null)}
        />
      )}
    </ToolPage>
  );
}

/* ---------------------------------------------------------------- Slack, GitHub, meetings... */

function PlainView({ row, kind, problem }: { row: ToolIndexRow | undefined; kind: string; problem?: string | null }) {
  const owner = useQuery(meQuery).data?.role === 'owner';
  const sending = useSending();
  // A mailbox the index lists without its own record (no mailbox_id): the company's rules, as MailboxView shows them.
  const mail = row?.kind === 'mailbox';
  // GitHub: the owner gets the App's own state and fixes; everyone else the plain state.
  const github = kind === 'github' && owner ? <GithubConnection /> : null;
  // A GitHub return that did not work (useGithubReturn): what happened, and the install when the App waits for it.
  const returned = problem && (
    <>
      <p className="t-read">{problem}</p>
      {!github && row?.kind === 'github' && row.status.tone === 'wait' && <Button onClick={() => void press('tools.installGithub', {})}>Install</Button>}
    </>
  );
  return (
    <ToolPage
      name={row?.name ?? KIND[kind] ?? 'Tool'}
      kind={row?.kind ?? kind}
      row={row}
      problem={row && bad(row)}
      bare={!row && !returned && !github}
      target={`tool:${row?.id ?? kind}`}
      connects={
        (returned || github) && (
          <>
            {returned}
            {github}
          </>
        )
      }
      can={
        mail && (
          <>
            <MayFacts />
            {sending.facts}
          </>
        )
      }
      canActions={mail && sending.actions}
    >
      {/* The list has loaded (the page draws a tool only then): a tool it does not list is gone, or never was. */}
      {!row && !returned && !github && <Empty title="No such tool" />}
    </ToolPage>
  );
}
