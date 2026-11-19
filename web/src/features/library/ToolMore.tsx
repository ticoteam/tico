import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import type { Group, ToolIndexRow, WebsiteTool } from '@/api/schema';
import { directoryQuery, type Directory } from '@/data';
import { Ago } from '@/kit/Ago';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Markdown } from '@/kit/Markdown';
import { Menu, MenuItem, MenuRule, MenuSub } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { Row } from '@/kit/Row';
import { toast } from '@/kit/Toast';
import { PageMore, Section } from '@/pagekinds';
import { Foot, useGo } from './AddTool';
import { Face, Held, More, press, when } from './parts';
import { inboxesQuery, mailboxQuery, type Inbox } from './queries';
import { RevealDialog } from './ToolDialogs';
import {
  envsOf,
  keysOf,
  mailboxMailQuery,
  mailboxSendsQuery,
  servicePageQuery,
  servicesQuery,
  teamsQuery,
  vaultQuery,
  type StoredKey,
  type Vault,
} from './toolKeys';
import { bad, Code, KIND, LEVEL, Shell, stateOf, toolHref, Who } from './ToolShell';
import { MayDo, SendingSection } from './ToolSettings';

/* The Tools gap rows' parts: a stored key no tool holds (s788 s909), a declared tool's own page (s787), a mailbox you
   can change (s866), who reads a Tico address (s867), a website's accounts (s865) and Tools'
   Who has access filter (s908). A secret is typed into a dialog's state and only sent. */

/** "Are you sure": for what cannot be undone. */
export function Confirm({ title, description, label, onGo, onClose }: { title: string; description?: string; label: string; onGo: () => Promise<boolean>; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    const ok = await onGo();
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      alert
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description={description}
      actions={<Foot onClose={onClose} label={label} busy={busy} ready onGo={() => void go()} />}
    />
  );
}

/** An add menu's trigger. */
const addTrigger = <IconButton icon="plus" label="Add someone" size="sm" />;

/* ---------------------------------------------------------------- a stored key no tool holds */

export const KEY_KIND: Record<string, string> = { api_key: 'API key', password: 'Password', token: 'Token', file: 'File', connection: 'Connection' };

/** A grant's subject as a name: a person or an agent the vault or the directory knows, or every computer. */
function subjectName(subject: string, vault: Vault | undefined, dir: Directory | undefined): string {
  if (subject === 'computers') return 'Every computer';
  const [kind, id = ''] = subject.split(':');
  if (kind === 'computer') return 'A computer';
  const known = kind === 'human' ? vault?.people.find((p) => p.id === id) : vault?.bots.find((b) => b.id === id || b.slug === id);
  return known?.name || dir?.byId[subject]?.name || id;
}

export function KeyView({ cid }: { cid: string }) {
  const q = useQuery(vaultQuery);
  const dir = useQuery(directoryQuery).data;
  const vault = q.data;
  const k = vault?.credentials.find((c) => c.id === cid);
  const [dialog, setDialog] = useState<'rotate' | 'reveal' | 'delete' | null>(null);
  const state = stateOf(q);
  if (state !== 'ready' || !k)
    return (
      <Shell name="Key" kind="key" state={state} onRetry={() => void q.refetch()} target={`tool:key:${cid}`}>
        <Empty title="No key here" />
      </Shell>
    );
  const manage = !!vault?.can_manage;
  const removable = k.can_remove ?? manage;
  const taken = new Set(k.grants.map((g) => g.subject));
  const adds = [
    ...(vault?.people ?? []).map((p) => ({ subject: `human:${p.id}`, name: p.name || p.email || p.id })),
    ...(vault?.bots ?? []).map((b) => ({ subject: `bot:${b.id}`, name: b.name })),
  ].filter((x) => !taken.has(x.subject));
  return (
    <Shell
      name={k.name}
      kind="key"
      lock={k.stored}
      problem={k.stored ? null : 'No value'}
      target={`tool:key:${k.id}`}
      more={
        <PageMore>
          {manage && <MenuItem label={k.stored ? 'Rotate' : 'Set value'} onSelect={() => setDialog('rotate')} />}
          {k.stored && k.can_reveal && <MenuItem label="Show key" onSelect={() => setDialog('reveal')} />}
          {removable && (
            <>
              <MenuRule />
              <MenuItem label="Delete key" onSelect={() => setDialog('delete')} />
            </>
          )}
        </PageMore>
      }
    >
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{KEY_KIND[k.kind] ?? k.kind}</LabelValue>
          {k.username && <LabelValue label="Username">{k.username}</LabelValue>}
          {k.env && (
            <LabelValue label="Variable">
              <Code>{k.env}</Code>
            </LabelValue>
          )}
          {k.source && <LabelValue label="From">{k.source}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects" target={`tool-connect:key:${k.id}`}>
        <Facts>
          <LabelValue label="Value">
            {k.stored ? (
              <>
                <Code>{k.preview || '••••••••'}</Code> <Held />
              </>
            ) : (
              'Not set'
            )}
          </LabelValue>
          {k.updated_at && <LabelValue label="Changed">{when(k.updated_at)}</LabelValue>}
          <LabelValue label="Last used">
            {k.last_used_at ? (
              <>
                {k.last_used_by && <Face actor={`bot:${k.last_used_by}`} />}
                {when(k.last_used_at)}
              </>
            ) : (
              'Never'
            )}
          </LabelValue>
        </Facts>
      </Section>
      <Section
        name="Who may use it"
        count={k.grants.length || undefined}
        target={`tool-who:key:${k.id}`}
        actions={
          manage &&
          !k.remove_only &&
          adds.length > 0 && (
            <Menu trigger={addTrigger}>
              {adds.map((x) => (
                <MenuItem key={x.subject} label={x.name} onSelect={() => void press('tools.grantKey', { credential: k.id, subject: x.subject }, 'Not added')} />
              ))}
            </Menu>
          )
        }
      >
        {k.grants.map((g) => {
          const name = subjectName(g.subject, vault, dir);
          return (
            <Who
              key={g.id}
              actor={/^(human|bot):/.test(g.subject) ? g.subject : null}
              name={name}
              right={
                g.can_revoke && (
                  <IconButton icon="close" label={`Remove ${name}`} size="sm" onClick={() => void press('tools.revokeKey', { credential: k.id, grant: g.id }, 'Not removed')} />
                )
              }
            />
          );
        })}
      </Section>
      {dialog === 'rotate' && <RotateDialog k={k} onClose={() => setDialog(null)} />}
      {dialog === 'reveal' && <RevealDialog credential={k.id} title={k.name} onClose={() => setDialog(null)} />}
      {dialog === 'delete' && (
        <Confirm
          title={`Delete ${k.name}?`}
          description="Everyone it is given to loses it. This cannot be undone."
          label="Delete"
          onGo={() => press('tools.deleteKey', { credential: k.id }, 'Not deleted')}
          onClose={() => setDialog(null)}
        />
      )}
    </Shell>
  );
}

/** A new value, typed here and only sent (a password keeps its spaces). */
function RotateDialog({ k, onClose }: { k: StoredKey; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [busy, go] = useGo(onClose);
  const sent = k.kind === 'password' ? value : value.trim();
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={k.stored ? `Rotate ${k.name}` : `Set ${k.name}`}
      className="connect"
      actions={
        <Foot onClose={onClose} label={k.stored ? 'Rotate' : 'Save'} busy={busy} ready={!!value.trim()} onGo={() => void go('tools.rotateKey', { credential: k.id, value: sent }, 'Not saved')} />
      }
    >
      <Field label="New value" type="password" value={value} onValueChange={setValue} autoComplete="off" autoFocus wide />
    </Dialog>
  );
}

/** A key a declared tool names that the vault does not hold yet. */
function StoreKeyDialog({ env, name, service, onClose }: { env: string; name: string; service: string; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add ${env}`}
      className="connect"
      actions={
        <Foot
          onClose={onClose}
          label="Add"
          busy={busy}
          ready={!!value.trim()}
          onGo={() => void go('tools.storeKey', { name, env, source: service, kind: /TOKEN/.test(env) ? 'token' : 'api_key', value: value.trim() }, 'Not added')}
        />
      }
    >
      <Field label="Key" type="password" value={value} onValueChange={setValue} autoComplete="off" autoFocus wide />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- a tool an agent declares */

const WRITE: Record<string, string> = { never: 'Read', approval: 'Asks first', allowed: 'Read and write' };
const RANK: Record<string, number> = { never: 0, approval: 1, allowed: 2 };

export function ServiceView({ row }: { row: ToolIndexRow }) {
  const service = row.service ?? row.id.slice(row.id.indexOf(':') + 1);
  const list = useQuery(servicesQuery);
  const page = useQuery(servicePageQuery(service));
  const vault = useQuery(vaultQuery).data;
  const dir = useQuery(directoryQuery).data;
  const [storing, setStoring] = useState<string | null>(null);
  const s = list.data?.find((x) => x.service === service);
  const removed = s?.status === 'removed';
  const keys = s && !removed ? keysOf(s, vault?.credentials ?? []) : [];
  const held = new Set(keys.map((k) => k.env).filter(Boolean));
  const missing = s && !removed ? envsOf(s).filter((e) => !held.has(e)) : [];
  // Who has it: each agent at its highest level (with any problem its computer reported), then people a key is given to.
  const agents = new Map<string, { level: string; problem: string }>();
  for (const c of s?.connections ?? []) {
    const level = c.write ?? s?.writes ?? 'never';
    const had = agents.get(c.bot);
    if (!had || (RANK[level] ?? 0) > (RANK[had.level] ?? 0)) agents.set(c.bot, { level, problem: c.problem || had?.problem || '' });
    else if (c.problem && !had.problem) had.problem = c.problem;
  }
  for (const b of s?.bots ?? []) if (!agents.has(b)) agents.set(b, { level: s?.writes ?? 'never', problem: '' });
  const people = new Set<string>();
  for (const k of keys) for (const g of k.grants) if (g.subject.startsWith('human:')) people.add(g.subject);
  const body = page.data?.body?.trim();
  return (
    <Shell name={s?.title || row.name} kind={row.kind} problem={bad(row)} state={stateOf(list)} onRetry={() => void list.refetch()} target={`tool:${row.id}`}>
      <Section name="What it is">
        <Facts>
          <LabelValue label="Kind">{KIND[row.kind] ?? row.kind}</LabelValue>
          {(s?.summary || row.detail) && <LabelValue label="About">{s?.summary || row.detail}</LabelValue>}
        </Facts>
      </Section>
      <Section name="How it connects" target={`tool-connect:${row.id}`}>
        <Facts>
          {row.way && <LabelValue label="Through">{row.way}</LabelValue>}
          <LabelValue label="State">{row.status.text}</LabelValue>
        </Facts>
        {keys.map((k) => (
          <Row key={k.id} className="row arow" title={k.name} href={toolHref(`key:${k.id}`)} target={`tool:key:${k.id}`}>
            <span className="row-title">
              {k.name} {k.env && <Code>{k.env}</Code>}
            </span>
            {k.stored ? <Held /> : <Pill>No value</Pill>}
            {k.last_used_at ? <Ago at={k.last_used_at} className="row-age" /> : <span className="row-age" />}
          </Row>
        ))}
        {missing.map((env) => {
          const conns = (s?.connections ?? []).filter((c) => c.env === env);
          const word = conns.some((c) => c.problem) ? 'Missing' : conns.some((c) => c.status === 'ready') ? 'On computer' : 'Not set';
          return (
            <div key={env} className="row arow" title={env}>
              <span className="row-title">
                <Code>{env}</Code>
              </span>
              <span className="arow-w">{word}</span>
              {vault?.can_manage && <IconButton icon="plus" label={`Add ${env}`} size="sm" onClick={() => setStoring(env)} />}
            </div>
          );
        })}
      </Section>
      {agents.size + people.size > 0 && (
        <Section name="Who may use it" count={agents.size + people.size}>
          {[...agents].map(([bot, a]) => {
            const name = dir?.byId[`bot:${bot}`]?.name ?? bot;
            return (
              <Who
                key={bot}
                actor={`bot:${bot}`}
                name={name}
                right={
                  <>
                    {a.problem && <Pill>{a.problem}</Pill>}
                    <span className="lvl">{WRITE[a.level] ?? a.level}</span>
                  </>
                }
              />
            );
          })}
          {[...people].map((p) => (
            <Who key={p} actor={p} name={subjectName(p, vault, dir)} right={<span className="lvl">{WRITE[s?.writes ?? 'never']}</span>} />
          ))}
        </Section>
      )}
      {body && (
        <Section name="What it can do">
          <Markdown text={body} />
        </Section>
      )}
      {storing && <StoreKeyDialog env={storing} name={s?.title || row.name} service={service} onClose={() => setStoring(null)} />}
    </Shell>
  );
}

/* ---------------------------------------------------------------- a mailbox */

const SEND_STATE: Record<string, string> = {
  sent: 'Sent',
  waiting_approval: 'Waiting for OK',
  pending: 'Waiting',
  dispatching: 'Sending',
  unknown: 'Unknown',
  blocked: 'Refused',
  canceled: 'Canceled',
};

/** A press whose answer is a line to show (a check): the line, or why it failed. */
async function check(id: string, input: unknown) {
  const r = await run(id, input, { via: 'click' });
  const out = r.out as { ok: boolean; text: string } | undefined;
  toast(r.ok && out ? { text: out.text, kind: out.ok ? 'done' : 'failed' } : refusal(r, 'Not checked'));
}

export function MailboxView({ id, row }: { id: string; row: ToolIndexRow }) {
  const q = useQuery(mailboxQuery(id));
  const d = q.data;
  const m = d?.mailbox;
  const [stopping, setStopping] = useState(false);
  const agents = (d?.accesses ?? []).filter((a) => a.grantee_type === 'bot').map((a) => ({ id: a.grantee_id, name: a.name }));
  const sharable = !!d && !!m && d.can_manage && m.status === 'active' && m.kind === 'personal' && !m.person_id;
  const more = (
    <PageMore>
      <MenuItem label="Email settings" onSelect={() => void press('tools.open', { tool: 'mailbox' })} />
      {sharable && <MenuItem label="Make shared inbox" onSelect={() => void press('tools.shareMailbox', { mailbox: id }, 'Not shared')} />}
      {d?.can_stop && (
        <>
          <MenuRule />
          <MenuItem label="Stop using" onSelect={() => setStopping(true)} />
        </>
      )}
    </PageMore>
  );
  return (
    <Shell name={m?.address ?? row.name} kind="mailbox" problem={bad(row)} more={more} state={stateOf(q)} onRetry={() => void q.refetch()} target={`tool:${row.id}`}>
      {d && m && (
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
          <Section name="How it connects" target={`tool-connect:mailbox:${id}`}>
            <Facts>
              <LabelValue label="Through">{m.provider}</LabelValue>
              <LabelValue label="State">{row.status.text}</LabelValue>
              {m.synced_at && <LabelValue label="Synced">{when(m.synced_at)}</LabelValue>}
              {m.sync_error && <LabelValue label="Problem">{m.sync_error}</LabelValue>}
            </Facts>
            {m.status === 'active' && <More onClick={() => void check('tools.checkMailbox', { mailbox: id })}>Check now</More>}
          </Section>
          {(d.accesses.length > 0 || (d.can_manage && d.candidates.length > 0)) && (
            <Section
              name="Who may use it"
              count={d.accesses.length || undefined}
              target={`tool-who:mailbox:${id}`}
              actions={
                d.can_manage &&
                d.candidates.length > 0 && (
                  <Menu trigger={addTrigger}>
                    {d.candidates.map((c) => (
                      <MenuItem
                        key={`${c.type}:${c.id}`}
                        label={c.name}
                        onSelect={() => void press('tools.addMailboxAccess', { mailbox: id, type: c.type, who: c.id, level: 'read' }, 'Not added')}
                      />
                    ))}
                  </Menu>
                )
              }
            >
              {d.accesses.map((a) => {
                const access = a.id;
                return (
                  <Who
                    key={`${a.grantee_type}:${a.grantee_id}`}
                    actor={a.grantee_type === 'admins' ? null : `${a.grantee_type === 'person' ? 'human' : 'bot'}:${a.grantee_id}`}
                    name={a.name}
                    right={
                      d.can_manage && !a.implicit && access ? (
                        <Menu
                          trigger={
                            <button type="button" className="lvl" aria-label={`${a.name}: ${LEVEL(a.level)}`}>
                              {LEVEL(a.level)}
                            </button>
                          }
                        >
                          {(['read', 'read_write'] as const).map((l) => (
                            <MenuItem
                              key={l}
                              label={LEVEL(l)}
                              on={a.level === l}
                              onSelect={() => void press('tools.setMailboxAccess', { mailbox: id, access, level: l }, 'Not changed')}
                            />
                          ))}
                          <MenuRule />
                          <MenuItem label="Remove" onSelect={() => void press('tools.setMailboxAccess', { mailbox: id, access, level: 'none' }, 'Not removed')} />
                        </Menu>
                      ) : (
                        <span className="lvl">{LEVEL(a.level)}</span>
                      )
                    }
                  />
                );
              })}
            </Section>
          )}
          <MayDo may={d.may} />
          <SendingSection agents={agents} />
          {m.level !== 'none' && m.state === 'connected' && <MailboxMail address={m.address} />}
          <MailboxSends address={m.address} />
          {stopping && (
            <Confirm
              title={`Stop using ${m.address}?`}
              description="Tico stops reading and sending from it."
              label="Stop using"
              onGo={() => press('tools.stopMailbox', { mailbox: id }, 'Not stopped')}
              onClose={() => setStopping(false)}
            />
          )}
        </>
      )}
    </Shell>
  );
}

/** Its newest mail, from Gmail. Each opens in Mail. */
function MailboxMail({ address }: { address: string }) {
  const q = useQuery(mailboxMailQuery(address));
  if (!q.data?.length) return null;
  return (
    <Section name="Recent mail" count={q.data.length}>
      {q.data.map((x, n) => {
        const what = x.subject || '(No subject)';
        const at = x.epoch ? new Date(x.epoch * 1000) : null;
        return (
          <Row key={x.id ?? x.thread_id ?? n} className="row urow" title={what} href={`/threads/mail?box=${encodeURIComponent(address)}${x.thread_id ? `&open=${encodeURIComponent(x.thread_id)}` : ''}`}>
            <span className="row-title">{what}</span>
            <span className="row-ctx">{x.from_header || x.from_addr || ''}</span>
            {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
          </Row>
        );
      })}
    </Section>
  );
}

/** What was sent from it: when, by whom, to whom, how it ended; a page at a time. */
function MailboxSends({ address }: { address: string }) {
  const q = useInfiniteQuery(mailboxSendsQuery(address));
  const sends = q.data?.pages.flatMap((p) => p.sends) ?? [];
  if (!sends.length) return null;
  return (
    <Section name="Activity" count={sends.length}>
      {sends.map((x) => (
        <div key={x.id} className="row urow" title={`${x.subject || '(No subject)'} ${x.recipients.join(', ')}`}>
          <span className="row-title">{x.subject || '(No subject)'}</span>
          <span className="row-ctx">{x.recipients.join(', ')}</span>
          {x.state !== 'sent' && <Pill>{SEND_STATE[x.state] ?? x.state}</Pill>}
          <Face actor={x.bot ? `bot:${x.bot}` : x.actor || null} />
          <Ago at={x.created_at} className="row-age" />
        </div>
      ))}
      {q.hasNextPage && <More onClick={() => void q.fetchNextPage()}>More</More>}
    </Section>
  );
}

/* ---------------------------------------------------------------- a Tico address or number */

/** Who reads it: everyone, or one team (and the teams under it). Only an admin changes it (`can`); others read it. */
export function InboxReaders({ box, can = true }: { box: Inbox; can?: boolean }) {
  const teams = useQuery(teamsQuery).data ?? [];
  const group = (box as Inbox & { group_id?: string | null }).group_id ?? null;
  const team = box.audience === 'team' ? teams.find((t) => t.id === group) : undefined;
  const word = box.audience === 'team' ? (team?.name ?? 'One team') : 'Everyone';
  const pick = (audience: 'company' | 'team', g?: string) => void press('tools.setInboxReaders', { inbox: box.inbox_id, audience, group: g }, 'Not saved');
  if (!can) return <LabelValue label="Reads it">{word}</LabelValue>;
  return (
    <LabelValue label="Reads it">
      <Menu
        trigger={
          <button type="button" className="lvl" aria-label={`Reads it: ${word}`}>
            {word}
          </button>
        }
      >
        <MenuItem label="Everyone" on={box.audience !== 'team'} onSelect={() => pick('company')} />
        {teams.length > 0 && <MenuRule />}
        {teams.map((t) => (
          <MenuItem key={t.id} label={t.name} on={box.audience === 'team' && t.id === group} onSelect={() => pick('team', t.id)} />
        ))}
      </Menu>
    </LabelValue>
  );
}

export function CheckInbox() {
  return <More onClick={() => void check('tools.checkInbox', {})}>Check connection</More>;
}

/** Inboxes read again after a check: here so ToolsPage needs no new query of its own. */
export const inboxesKey = inboxesQuery.queryKey;

/* ---------------------------------------------------------------- a website */

type Account = WebsiteTool['accounts'][number];

/** Who may use each shared account: a person, an agent, a team or every agent; given here or taken away. */
export function AccountAccess({ tool, t }: { tool: string; t: WebsiteTool }) {
  const dir = useQuery(directoryQuery).data;
  const teams = useQuery(teamsQuery).data ?? [];
  const many = t.accounts.length > 1;
  const editable = (a: Account) => a.can_manage && a.kind === 'shared';
  const rows = t.accounts.flatMap((a) => [
    ...(a.all_bots ? [{ a, subject: 'bots', name: 'All agents', actor: null as string | null, via: false }] : []),
    ...a.access.map((p) => ({ a, subject: p.subject, name: p.name, actor: p.kind === 'group' ? null : p.subject, via: !!p.via?.length })),
  ]);
  const open = t.accounts.filter(editable);
  if (!rows.length && !open.length) return null;
  const give = (a: Account, subject: string) => void press('tools.setAccountAccess', { tool, account: a.id, subject, on: true }, 'Not added');
  const choices = (a: Account) => {
    const has = new Set(a.access.map((p) => p.subject));
    return (
      <>
        {!a.all_bots && <MenuItem label="All agents" onSelect={() => give(a, 'bots')} />}
        {teams
          .filter((g) => !has.has(`group:${g.id}`))
          .map((g) => (
            <MenuItem key={g.id} label={g.name} onSelect={() => give(a, `group:${g.id}`)} />
          ))}
        {(dir?.actors ?? [])
          .filter((x) => !has.has(x.id))
          .map((x) => (
            <MenuItem key={x.id} label={x.name} onSelect={() => give(a, x.id)} />
          ))}
      </>
    );
  };
  const label = (a: Account) => a.username || a.holder?.name || 'Account';
  return (
    <Section
      name="Who may use it"
      count={rows.length || undefined}
      target={`tool-who:website:${tool}`}
      actions={
        open.length > 0 && (
          <Menu trigger={addTrigger}>
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
    >
      {rows.map((r) => (
        <Who
          key={`${r.a.id}:${r.subject}`}
          actor={r.actor}
          name={r.name}
          right={
            <>
              {many && <span className="row-ctx">{label(r.a)}</span>}
              {editable(r.a) && !r.via && (
                <IconButton
                  icon="close"
                  label={`Remove ${r.name}`}
                  size="sm"
                  onClick={() => void press('tools.setAccountAccess', { tool, account: r.a.id, subject: r.subject, on: false }, 'Not removed')}
                />
              )}
            </>
          }
        />
      ))}
    </Section>
  );
}

/** For good: an account, or the whole website. */
export function RemoveAccountConfirm({ tool, account, name, onClose }: { tool: string; account: string; name: string; onClose: () => void }) {
  return (
    <Confirm
      title={`Remove ${name}?`}
      description="Its login and who may use it go. This cannot be undone."
      label="Remove"
      onGo={() => press('tools.removeAccount', { tool, account }, 'Not removed')}
      onClose={onClose}
    />
  );
}
export function DeleteWebsiteConfirm({ tool, name, onClose }: { tool: string; name: string; onClose: () => void }) {
  return (
    <Confirm
      title={`Delete ${name}?`}
      description="Its logins and who may use them go. This cannot be undone."
      label="Delete"
      onGo={() => press('tools.deleteWebsite', { tool }, 'Not deleted')}
      onClose={onClose}
    />
  );
}

/* ---------------------------------------------------------------- Tools' Who has access */

/** An index row's teammate as a subject: `human:<id>`, `bot:<id>` or `group:<id>`. */
export const subjectOf = (e: { kind: string; id: string }) => (/^(human|bot|group):/.test(e.id) ? e.id : `${e.kind === 'person' ? 'human' : e.kind}:${e.id}`);

/** Everyone a team holds, as subjects: the team, the teams under it, their people and their agents. */
export function teamSubjects(groups: Group[], gid: string): Set<string> {
  const out = new Set<string>();
  const walk = (id: string, depth: number) => {
    const g = groups.find((x) => x.id === id);
    if (!g || depth > 20 || out.has(`group:${g.id}`)) return;
    out.add(`group:${g.id}`);
    for (const p of g.people ?? []) out.add(p.includes(':') ? p : `human:${p}`);
    for (const b of [...(g.bots ?? []), ...(g.bot_slugs ?? [])]) out.add(b.includes(':') ? b : `bot:${b}`);
    for (const c of groups) if (c.parent === id) walk(c.id, depth + 1);
  };
  walk(gid, 0);
  return out;
}

/** Whether a tool given to these subjects is one the pick has: a team (anyone in it), or a teammate (also through a
    team they are in). `same` makes two spellings of one teammate equal (an agent's slug and its id). */
export function hasWho(subjects: string[], who: string, groups: Group[], same: (s: string) => string): boolean {
  if (who.startsWith('group:')) {
    const team = new Set([...teamSubjects(groups, who.slice(6))].map(same));
    return subjects.some((s) => team.has(same(s)));
  }
  const me = same(who);
  return subjects.some((s) => same(s) === me || (s.startsWith('group:') && [...teamSubjects(groups, s.slice(6))].some((x) => same(x) === me)));
}

/** The Filter menu's Who has access: teams first, then teammates. */
export function WhoMenu({ who, teams, people, onPick }: { who?: string; teams: Group[]; people: { subject: string; name: string }[]; onPick: (who?: string) => void }) {
  if (!teams.length && !people.length) return null;
  return (
    <MenuSub label="Who has access">
      <MenuItem label="Anyone" on={!who} onSelect={() => onPick(undefined)} />
      {teams.map((g) => (
        <MenuItem key={g.id} label={g.name} on={who === `group:${g.id}`} onSelect={() => onPick(`group:${g.id}`)} />
      ))}
      {people.map((p) => (
        <MenuItem key={p.subject} label={p.name} on={who === p.subject} onSelect={() => onPick(p.subject)} />
      ))}
    </MenuSub>
  );
}
