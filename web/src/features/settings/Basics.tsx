import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { refusal, run } from '@/actions';
import { configQuery, meQuery, useActor } from '@/data';
import { tellDesktopSigningOut } from '@/desktop';
import { Avatar } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import type { MotionChoice } from '@/motion';
import { Section } from '@/pagekinds';
import { THEME_KEY, usePref, type Theme } from '@/prefs';
import { Repositories, TeamIcon } from './Company';
import { CompanyName } from './CompanyName';
import { AllTokens, EXPIRES } from './CompanyTokens';
import { TicoSection } from './Desktop';
import { NoticeSwitch } from './Notices';
import { Joining, LeftPeople } from './People';
import { ScreeningMore, SupportAccess } from './SecurityMore';
import { Foot, Line, SwitchLine, Verb, act, useAgentFaces, useFaces } from './parts';
import {
  accessQuery,
  supportAccessQuery,
  type AccessFull,
  type ScreeningFull,
  directorySyncQuery,
  humansQuery,
  pageOwnersQuery,
  screeningQuery,
  supportQuery,
  tokensQuery,
  type AccessPerson,
  type DirectoryCredentials,
  type DirectoryPreview,
  type DirectorySource,
  type DirectorySynced,
  type DirectoryView,
  type ScreenMode,
} from './queries';

const bare = (actor: string) => actor.replace(/^human:/, '');

// ---------------------------------------------------------------- General

export function General() {
  const config = useQuery(configQuery);
  const access = useQuery(accessQuery);
  const owners = useQuery(pageOwnersQuery);
  const agents = useAgentFaces();
  const faces = useFaces();
  const c = config.data;
  const named = (actor?: string | null) => (actor ? (faces[actor]?.name ?? '') : '');
  const pages = Object.entries(owners.data ?? {});
  return (
    <>
      <Section name="Company" target="setting:company">
        <Facts label="Company">
          {access.data?.you === 'owner' && c ? <CompanyName name={c.company_name} /> : <LabelValue label="Name">{c?.company_name}</LabelValue>}
          {access.data && <TeamIcon owner={access.data.you === 'owner'} />}
          {c?.public_url && (
            <Line label="Address">
              <code className="fs-code">{c.public_url.replace(/^https?:\/\//, '')}</code>
            </Line>
          )}
        </Facts>
      </Section>
      {access.data && (
        <Section name="Agents" target="setting:reports">
          <Facts label="Agents">
            <SwitchLine
              label="Reports to Tico support"
              checked={access.data.rules.bot_reports_direct !== false}
              onChange={(on) => void act('settings.reportsDirect', { on })}
            />
          </Facts>
        </Section>
      )}
      {pages.length > 0 && (
        <Section name="Page owners" count={pages.length}>
          <Facts label="Page owners">
            {pages.map(([page, row]) => {
              const fallback = named(row.default);
              const choice = row.choice ?? 'default';
              const value = choice === 'none' || choice === 'default' || agents.some((a) => a.id === choice) ? choice : 'default';
              return (
                <Line key={page} label={row.name ?? page} target={`page-owner:${page}`}>
                  <Select
                    label={`${row.name ?? page} owner`}
                    labelHidden
                    look="quiet"
                    value={value}
                    onValueChange={(owner) => void act('settings.setPageOwner', { page, owner })}
                    options={[
                      { value: 'default', label: fallback ? `Default (${fallback})` : 'Default' },
                      { value: 'none', label: 'No owner' },
                      ...agents.map((a) => ({ value: a.id, label: a.name })),
                    ]}
                  />
                </Line>
              );
            })}
          </Facts>
        </Section>
      )}
      {access.data && <Repositories admin={access.data.you === 'owner' || access.data.you === 'admin'} owner={access.data.you === 'owner'} />}
      <TicoSection version={c?.version} />
    </>
  );
}

// ---------------------------------------------------------------- People

const ROLE: Record<string, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' };

const looksLikeEmail = (s: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s);

/** The owner hands the company to another person: typed to confirm, and only by the owner's own press. */
function OwnerDialog({ p, ownerName, onClose }: { p: AccessPerson; ownerName: string; onClose: () => void }) {
  // Off unless chosen, as the old app had it: handing over does not keep extra rights by default.
  const [keep, setKeep] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const ready = typed.trim().toLowerCase() === p.email.toLowerCase();
  const go = async () => {
    setBusy(true);
    const r = await run('settings.makeOwner', { person: p.id, keepAdmin: keep }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't change the owner"));
    // The owner's controls are gone from this screen: start again from the top.
    window.location.assign('/');
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      alert
      title={`Make ${p.name} the owner?`}
      description={`${p.name} becomes the owner now. You lose the owner's controls.`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void go()}>
            Make owner
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Checkbox label={`Keep ${ownerName} as an admin`} checked={keep} onCheckedChange={setKeep} />
        <Field label={`Type ${p.email} to confirm`} value={typed} onValueChange={setTyped} autoComplete="off" />
      </div>
    </Dialog>
  );
}

/** The owner's own sign-in address, typed twice. The old one stops signing in at once. */
function EmailDialog({ p, onClose }: { p: AccessPerson; onClose: () => void }) {
  const me = useQuery(meQuery);
  const [email, setEmail] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const to = email.trim().toLowerCase();
  const ready = looksLikeEmail(to) && to === again.trim().toLowerCase() && to !== p.email.toLowerCase();
  const go = async () => {
    setBusy(true);
    const r = await run('settings.changeEmail', { email: to, again: again.trim().toLowerCase() }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't change the email"));
    // This browser signed in with the old address: a proxy session signs in again as the new one.
    if ((me.data as { proxy_session?: boolean } | undefined)?.proxy_session) {
      await tellDesktopSigningOut();
      window.location.assign('/api/v2/logout');
    } else window.location.assign('/');
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      alert
      title="Change your sign-in email?"
      description={`${p.email} stops working here at once. Set up the new address in your sign-in provider first.`}
      actions={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} disabled={!ready} onClick={() => void go()}>
            Change email
          </Button>
        </>
      }
    >
      <div className="set-add">
        <Field label="New sign-in email" type="email" value={email} onValueChange={setEmail} autoComplete="off" autoFocus />
        <Field label="Type it again" type="email" value={again} onValueChange={setAgain} autoComplete="off" />
      </div>
    </Dialog>
  );
}

function PersonOpen({ p, mayEdit, youOwn, ownerName }: { p: AccessPerson; mayEdit: boolean; youOwn: boolean; ownerName: string }) {
  const [confirm, setConfirm] = useState(false);
  const [handing, setHanding] = useState<'owner' | 'email' | null>(null);
  // Only the owner sees these: hand the company over, or change their own sign-in address.
  const mayHand = youOwn && !p.owner && !!p.email && (p.can_sign_in ?? p.sign_in);
  const myEmail = youOwn && p.owner;
  return (
    <>
      <Facts label={p.name}>
        <LabelValue label="Email">{p.email}</LabelValue>
        {p.title && <LabelValue label="Title">{p.title}</LabelValue>}
        {p.team && <LabelValue label="Team">{p.team}</LabelValue>}
        <Line label="Role" target={`person:${p.id}`}>
          {mayEdit && !p.owner ? (
            <Select
              label="Role"
              labelHidden
              look="quiet"
              value={p.role === 'admin' ? 'admin' : 'member'}
              onValueChange={(role) => void act('settings.setRole', { person: p.id, role })}
              options={[
                { value: 'admin', label: 'Admin' },
                { value: 'member', label: 'Member' },
              ]}
            />
          ) : (
            (ROLE[p.owner ? 'owner' : p.role] ?? p.role)
          )}
        </Line>
        <LabelValue label="Signs in">{p.sign_in ? 'Yes' : 'Not yet'}</LabelValue>
      </Facts>
      {((mayEdit && !p.owner) || mayHand || myEmail) && (
        <Foot open>
          {mayEdit && !p.owner && !p.sign_in && (
            <Verb onClick={() => void act('settings.resendInvite', { person: p.id }).then((ok) => ok && toast({ text: 'Invite sent', kind: 'done' }))}>
              Send invite again
            </Verb>
          )}
          {mayHand && <Verb onClick={() => setHanding('owner')}>Make owner</Verb>}
          {myEmail && <Verb onClick={() => setHanding('email')}>Change email</Verb>}
          {/* The owner marks someone as left (`removable` is about removing a left person for good, below). */}
          {youOwn && !p.owner && <Verb onClick={() => setConfirm(true)}>Remove</Verb>}
        </Foot>
      )}
      {handing === 'owner' && <OwnerDialog p={p} ownerName={ownerName} onClose={() => setHanding(null)} />}
      {handing === 'email' && <EmailDialog p={p} onClose={() => setHanding(null)} />}
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        alert
        title={`Remove ${p.name}?`}
        description="They can no longer sign in. Their tasks and messages stay."
        actions={
          <>
            <Button variant="quiet" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirm(false);
                void act('settings.removePerson', { person: p.id });
              }}
            >
              Remove
            </Button>
          </>
        }
      />
    </>
  );
}

export function People({ open }: { open?: string }) {
  const access = useQuery(accessQuery);
  const v = access.data as AccessFull | undefined;
  if (!v || !Array.isArray(v.people)) return null;
  const people = v.people.filter((p) => !p.left);
  // `you` is the reader's company role; only owners and admins read this at all.
  const mayEdit = v.you === 'owner' || v.you === 'admin';
  const owner = v.people.find((p) => p.owner);
  return (
    <>
    <Section name="People" count={people.length}>
      {people.length === 0 ? (
        <Empty title="No one yet" />
      ) : (
        <>
          <div className="tbl-head g-person" aria-hidden="true">
            <span />
            <span>Name</span>
            <span>Email</span>
            <span>Role</span>
            <span />
          </div>
          {people.map((p) => {
            const row = (
              <Link
                key={p.id}
                to="/settings/$section"
                params={{ section: 'people' }}
                search={open === p.id ? {} : { open: p.id }}
                className={`row g-person${p.sign_in ? '' : ' is-off'}`}
                data-target={`person:${p.id}`}
                aria-expanded={open === p.id}
                title={p.name}
              >
                <Avatar kind="person" name={p.name} decorative />
                <span className="row-title">{p.name}</span>
                <span className="row-sub">{p.email}</span>
                <span className="row-cell">{ROLE[p.owner ? 'owner' : p.role] ?? p.role}</span>
                <span className="row-age">{p.sign_in ? '' : 'Invited'}</span>
              </Link>
            );
            return open === p.id ? (
              <div key={p.id} className="open">
                {row}
                <PersonOpen p={p} mayEdit={mayEdit} youOwn={v.you === 'owner'} ownerName={owner?.name ?? 'yourself'} />
              </div>
            ) : (
              row
            );
          })}
        </>
      )}
    </Section>
    {mayEdit && <Joining v={v} />}
    <LeftPeople v={v} />
    {v.you === 'owner' && <DirectorySync />}
    </>
  );
}

// ---------------------------------------------------------------- directory sync (owner): people from Google, Entra or SCIM

/* Not drawn yet (s812 todo): kept at its least. Where people come from, the source's key, SCIM's address and token,
   and Sync now, which shows what would change before it applies. Filters, the schedule and the leave limit keep the
   server's values. */

const SOURCES = [
  { value: '', label: 'Off' },
  { value: 'google', label: 'Google Workspace' },
  { value: 'entra', label: 'Microsoft Entra ID' },
  { value: 'scim', label: 'SCIM' },
] as const;

/** The fields a source's key needs; all of them, or none is sent. */
const KEY_FIELDS: Record<'google' | 'entra', [keyof DirectoryCredentials, string, 'area' | 'email' | 'text' | 'password'][]> = {
  google: [
    ['service_account_json', 'Service account key (JSON)', 'area'],
    ['admin_email', 'Admin it acts as', 'email'],
  ],
  entra: [
    ['tenant', 'Tenant', 'text'],
    ['client_id', 'Client ID', 'text'],
    ['client_secret', 'Client secret', 'password'],
  ],
};

function DirectorySync() {
  const sync = useQuery({ ...directorySyncQuery, retry: false });
  return sync.data ? <SyncForm key={sync.data.revision} v={sync.data} /> : null;
}

function SyncForm({ v }: { v: DirectoryView }) {
  const [source, setSource] = useState<DirectorySource>(v.source);
  const [cred, setCred] = useState<DirectoryCredentials>({});
  // A new SCIM token is shown once: it lives here, in this component's state, and nowhere else.
  const [token, setToken] = useState<string | null>(null);
  const [preview, setPreview] = useState<DirectoryPreview | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const keyed = source === 'google' || source === 'entra' ? source : null;
  const asking = keyed && !v.credentials[keyed]?.configured ? KEY_FIELDS[keyed] : [];
  const typed = asking.every(([k]) => (cred[k] ?? '').trim());

  const save = async (to: DirectorySource) => {
    setBusy(true);
    const r = await run(
      'settings.saveDirectory',
      {
        source: to,
        interval: v.interval_minutes,
        massLeave: v.mass_leave_limit,
        groups: v.filter.groups,
        orgUnits: v.filter.org_units,
        domains: v.filter.domains,
        ...(asking.length && to ? { credentials: Object.fromEntries(asking.map(([k]) => [k, (cred[k] ?? '').trim()])) } : {}),
      },
      { via: 'click' },
    );
    setBusy(false);
    toast(r.ok ? { text: r.did ?? 'Saved', kind: 'done' } : refusal(r, "Couldn't save"));
  };
  // A source that needs a key saves once the key is typed; the others save as they are picked.
  const pick = (to: DirectorySource) => {
    setSource(to);
    const needsKey = (to === 'google' || to === 'entra') && !v.credentials[to]?.configured;
    if (!needsKey) void save(to);
  };
  const makeToken = async () => {
    const r = await run('settings.scimToken', {}, { via: 'click' });
    const made = r.ok ? (r.out as { token?: string } | undefined)?.token : undefined;
    if (made) setToken(made);
    else toast(refusal(r, "Couldn't make a token"));
  };
  const look = async () => {
    const r = await run('settings.previewDirectory', {}, { via: 'click' });
    if (!r.ok) return toast(refusal(r, "Couldn't read the directory"));
    setReviewed(false);
    setPreview(r.out as DirectoryPreview);
  };
  const p = preview?.plan;
  const needs = !!(preview?.needs_confirmation.first || preview?.needs_confirmation.mass_leave);
  const any = !!p && (p.adds.length || p.updates.length || p.restores.length || p.leaves.length) > 0;
  const apply = async () => {
    if (!p) return;
    setBusy(true);
    const r = await run('settings.syncDirectory', { plan: p.hash, confirm: needs && reviewed }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't sync"));
    toast({ text: r.did ?? 'Synced', kind: 'done' });
    if ((r.out as DirectorySynced | undefined)?.applied) setPreview(null);
  };
  const last = v.last.at ? `${v.last.ok === false ? 'Failed' : 'Synced'}, ${new Date(v.last.at).toLocaleString('en-US')}` : 'Never';
  return (
    <Section name="Directory sync" defaultOpen={!!v.source} target="setting:directory-sync">
      <Facts label="Directory sync">
        <Line label="People from">
          <Select label="People from" labelHidden look="quiet" value={source} options={SOURCES} onValueChange={(x) => pick(x as DirectorySource)} />
        </Line>
        {source === 'scim' && v.scim.url && <Line label="Address">{v.scim.url}</Line>}
        {source === 'scim' && (
          <Line label="Token">
            <Verb onClick={() => void makeToken()}>{v.scim.enabled ? 'Replace token' : 'Make token'}</Verb>
          </Line>
        )}
        {v.source && (
          <Line label="Last sync">
            {last}
            <Verb onClick={() => void look()}>Sync now</Verb>
          </Line>
        )}
      </Facts>
      {asking.length > 0 && (
        <div className="set-add set-dir">
          {asking.map(([k, label, kind]) => (
            <Field
              key={k}
              label={label}
              value={cred[k] ?? ''}
              onValueChange={(x) => setCred((c) => ({ ...c, [k]: x }))}
              area={kind === 'area'}
              type={kind === 'area' ? undefined : kind}
              autoComplete="off"
            />
          ))}
          <Button busy={busy} disabled={!typed} onClick={() => void save(source)}>
            Save key
          </Button>
        </div>
      )}
      <Dialog
        open={token != null}
        onOpenChange={(o) => !o && setToken(null)}
        title="Directory token"
        description="Copy it now. It is not shown again."
        actions={
          <Button variant="primary" onClick={() => setToken(null)}>
            Done
          </Button>
        }
      >
        <code className="fs-code">{token}</code>
      </Dialog>
      {p && (
        <Dialog
          open
          onOpenChange={(o) => !o && setPreview(null)}
          title="Sync now"
          description={`${p.adds.length} join, ${p.updates.length} change, ${p.restores.length} come back, ${p.leaves.length} leave.`}
          actions={
            <>
              <Button variant="quiet" onClick={() => setPreview(null)}>
                Cancel
              </Button>
              <Button variant="primary" busy={busy} disabled={!any || (needs && !reviewed)} onClick={() => void apply()}>
                Apply
              </Button>
            </>
          }
        >
          {/* who loses their sign-in is the part to check by name */}
          {p.leaves.length > 0 && <p className="fact-dim set-leaves">{p.leaves.map((r) => r.email).join(', ')}</p>}
          {needs && <Checkbox label="I checked this list" checked={reviewed} onCheckedChange={setReviewed} />}
        </Dialog>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- Account (you, this device, your tokens)

const THEMES = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'System' },
] as const;

const MOTIONS = [
  { value: 'system', label: 'System' },
  { value: 'more', label: 'More' },
  { value: 'less', label: 'Less' },
  { value: 'off', label: 'Off' },
] as const;

function Tokens() {
  const tokens = useQuery(tokensQuery);
  const [label, setLabel] = useState('');
  const [days, setDays] = useState('90');
  const [making, setMaking] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState<{ id: string; label: string } | null>(null);
  const live = (tokens.data?.tokens ?? []).filter((t) => !t.revoked_at);
  // The line a terminal or script needs, as the old app gave it: built here from the secret, never kept.
  const line = shown ? `export HUB_API_URL=${window.location.origin} HUB_TOKEN=${shown}` : '';
  const make = async () => {
    setBusy(true);
    const r = await run('settings.createToken', { label: label.trim(), days: Number(days) }, { via: 'click' });
    setBusy(false);
    if (!r.ok) return toast(refusal(r, "Couldn't make it"));
    setMaking(false);
    setLabel('');
    // The secret is shown once, here, and never read back.
    setShown((r.out as { token?: string } | undefined)?.token ?? null);
  };
  return (
    <Section
      name="API tokens"
      count={live.length}
      target="setting:tokens"
      actions={
        tokens.data?.can_create !== false && (
          <Dialog
            open={making}
            onOpenChange={setMaking}
            title="Make a token"
            description="It works as you. You see it once."
            trigger={<Button icon="plus">Make token</Button>}
            actions={
              <>
                <Button variant="quiet" onClick={() => setMaking(false)}>
                  Cancel
                </Button>
                <Button variant="primary" busy={busy} disabled={!label.trim()} onClick={() => void make()}>
                  Make
                </Button>
              </>
            }
          >
            <div className="set-add">
              <Field label="Name" value={label} onValueChange={setLabel} autoFocus />
              <Select label="Expires" value={days} options={EXPIRES} onValueChange={setDays} />
            </div>
          </Dialog>
        )
      }
    >
      {live.length === 0 ? (
        <Empty title="No tokens" />
      ) : (
        <>
          <div className="tbl-head g-tok" aria-hidden="true">
            <span>Name</span>
            <span>Made</span>
            <span>Last used</span>
            <span />
          </div>
          {live.map((t) => (
            <div key={t.id} className="row g-tok" data-target={`token:${t.id}`} title={t.label}>
              <span className="row-title">{t.label}</span>
              <span className="row-cell">{new Date(t.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
              <span className="row-cell">{t.last_used_at ? new Date(t.last_used_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'Never'}</span>
              <span className="hl-act">
                <Verb quiet onClick={() => setRevoking({ id: t.id, label: t.label })}>
                  Revoke
                </Verb>
              </span>
            </div>
          ))}
        </>
      )}
      {/* Revoking cannot be taken back: asked first, as the old app did (ui/app/settings-devices.ts). */}
      <Dialog
        open={revoking != null}
        onOpenChange={(o) => !o && setRevoking(null)}
        alert
        title={`Revoke ${revoking?.label ?? 'token'}?`}
        description="Anything using it stops at once."
        actions={
          <>
            <Button variant="quiet" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (revoking) void act('settings.revokeToken', { token: revoking.id });
                setRevoking(null);
              }}
            >
              Revoke
            </Button>
          </>
        }
      />
      <Dialog
        open={shown != null}
        onOpenChange={(o) => !o && setShown(null)}
        title="Your new token"
        description="Copy it now. It is not shown again."
        actions={
          <>
            <Button variant="quiet" onClick={() => void navigator.clipboard?.writeText(line)}>
              Copy line
            </Button>
            <Button
              variant="primary"
              icon="copy"
              onClick={() => {
                if (shown) void navigator.clipboard?.writeText(shown);
                setShown(null);
              }}
            >
              Copy
            </Button>
          </>
        }
      >
        <code className="fs-code" aria-label="Token">
          {shown}
        </code>
        <code className="fs-code" aria-label="Shell line">
          {line}
        </code>
      </Dialog>
    </Section>
  );
}

export function Account() {
  const me = useQuery(meQuery);
  const actor = useActor(me.data?.id);
  const humans = useQuery(humansQuery);
  const [theme] = usePref<Theme>(THEME_KEY, 'dark');
  const [motion] = usePref<MotionChoice>('motion.v1', 'system');
  const mine = humans.data?.find((p) => me.data && p.id === bare(me.data.id));
  const name = actor?.name ?? me.data?.email ?? '';
  return (
    <>
      <Section name="You" target="setting:you">
        <Facts label="You">
          <Line label="Face">
            <Avatar kind="person" name={name} hue={actor?.hue} size={28} decorative />
          </Line>
          <LabelValue label="Name">{name}</LabelValue>
          <LabelValue label="Email">{me.data?.email}</LabelValue>
          {mine && (
            <SwitchLine
              label="Daily email summary"
              target="setting:email_digest"
              checked={mine.email_digest !== false}
              onChange={(on) => void act('settings.notify', { field: 'email_digest', on })}
            />
          )}
          {mine && <NoticeSwitch on={mine.notify_browser === true} />}
        </Facts>
        <Foot>
          <Verb onClick={() => void run('settings.signOut', {}, { via: 'click' })}>Sign out</Verb>
        </Foot>
      </Section>
      <Section name="This device" target="setting:device">
        <Facts label="This device">
          <Line label="Theme" target="setting:theme">
            <Segmented label="Theme" options={THEMES} value={theme} onValueChange={(t) => void act('settings.setTheme', { theme: t })} />
          </Line>
          <Line label="Motion" target="setting:motion">
            <Segmented label="Motion" options={MOTIONS} value={motion} onValueChange={(m) => void act('settings.setMotion', { motion: m })} />
          </Line>
          <Line label="Sheet sizes" target="setting:sizes">
            <Verb onClick={() => void act('shell.resetSizes', {}).then((ok) => ok && toast({ text: 'Sheet sizes reset', kind: 'done' }))}>Reset</Verb>
          </Line>
        </Facts>
      </Section>
      <Tokens />
      <EveryonesTokens />
    </>
  );
}

/** Only the owner and the admins may read everyone's tokens; the rest never ask. */
function EveryonesTokens() {
  const access = useQuery({ ...accessQuery, retry: false });
  const you = access.data?.you;
  return you === 'owner' || you === 'admin' ? <AllTokens /> : null;
}

// ---------------------------------------------------------------- Security

const MODES: { value: ScreenMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'warn', label: 'Warn' },
  { value: 'hold', label: 'Hold' },
];
// Email can warn but not hold (old app ui/app/settings-release.ts screeningHTML).
const MAIL_MODES = MODES.filter((m) => m.value !== 'hold');

export function Security() {
  const screening = useQuery(screeningQuery);
  const support = useQuery(supportQuery);
  const letIn = useQuery({ ...supportAccessQuery, retry: false });
  const v = screening.data as ScreeningFull | undefined;
  if (!v) return null;
  // One choice for every door but mail, which has its own.
  const others = Object.keys(v.modes).filter((d) => d !== 'mail');
  return (
    <>
      <Section name="Screening" target="setting:screening">
        <Facts label="Screening">
          <SwitchLine
            label="Patterns"
            target="setting:detector-patterns"
            checked={v.layers.patterns}
            onChange={(on) => void act('settings.setDetector', { detector: 'patterns', on })}
          />
          <SwitchLine
            label="Decision model"
            target="setting:detector-model"
            checked={v.layers.model}
            onChange={(on) => void act('settings.setDetector', { detector: 'model', on })}
          />
          {others.length > 0 && (
            <Line label="When caught" target="setting:screen-doors">
              <Segmented
                label="When caught"
                options={MODES}
                value={v.modes[others[0]!] ?? 'off'}
                onValueChange={(mode) => others.forEach((door) => void act('settings.setScreening', { door, mode }))}
              />
            </Line>
          )}
          {'mail' in v.modes && (
            <Line label="In mail" target="setting:screen-mail">
              <Segmented label="In mail" options={MAIL_MODES} value={v.modes.mail ?? 'off'} onValueChange={(mode) => void act('settings.setScreening', { door: 'mail', mode })} />
            </Line>
          )}
          <ScreeningMore recent={v.recent} />
        </Facts>
      </Section>
      {(support.data?.available || letIn.data?.available) && (
        <Section name="Tico support" target="setting:support">
          <Facts label="Tico support">
            <SupportAccess />
            {support.data?.available && (
              <SwitchLine
                label="Reaches computers"
                checked={support.data.on === true}
                onChange={(on) => void act('settings.setSupport', { on })}
              />
            )}
          </Facts>
        </Section>
      )}
    </>
  );
}
