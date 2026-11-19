import { useQuery } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { refusal, run } from '@/actions';
import { meQuery, useActor } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Checkbox } from '@/kit/Checkbox';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Field } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Link } from '@/kit/Link';
import { Row } from '@/kit/Row';
import { Select } from '@/kit/Select';
import { toast } from '@/kit/Toast';
import { Document, List, PageMore, Section, type PageState } from '@/pagekinds';
import { Markdown } from '@/kit/Markdown';
import { Menu, MenuItem, MenuSub } from '@/kit/Menu';
import { Foot, useGo } from './AddTool';
import { forgetHelperCode, heldHelperCode } from './helperCode';
import { Face, Find, OwnerButton, press, when, Who } from './parts';
import {
  accessRoleQuery,
  installTargetsQuery,
  proposalQuery,
  proposalsQuery,
  skillQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
  skillDetailUsageQuery,
  skillsElsewhereQuery,
  skillSuggestionsQuery,
  type SkillCoverage,
  type SkillSuggestion,
  type Proposal,
  type SkillActor,
  type SkillFile,
  type SkillRow,
  type SkillUse,
  type SkillVersion,
  previewDevice,
  SKILL_NAME,
} from './queries';
import type { SkillsSearch } from './search';
import './library.css';

/* Skills (tools.js skillsPage, skillPage, proposalPage): one list, no tabs. A proposed change waits at the top with the
   Flow mark; a skill opens as its text with who has it beside it; a proposal opens as its changes, with Approve and
   Send back at the foot for an admin (anyone else reads it only). */

const CRUMB = [{ label: 'Skills', to: '/library/skills' }];

/** A skill's install or use as a directory id: agents are `bot:<id>`, people `human:<id>`. */
export const actorOf = (a: { type: string; id: string }) => `${a.type === 'Bot' ? 'bot' : 'human'}:${a.id}`;

export function SkillsPage({ search }: { search: SkillsSearch }) {
  const router = useRouter();
  // A helper's code is a one-time approval, held in this state only. The app took it out of the address at startup
  // (helperCode.ts), across a sign-in too; one that still comes by the address leaves it at once.
  const [device, setDevice] = useState<string | null>(heldHelperCode);
  useEffect(() => forgetHelperCode(), []);
  const incoming = search.connect && search.connect !== 'setup' ? search.connect : null;
  if (incoming && incoming !== device) setDevice(incoming);
  useEffect(() => {
    if (!incoming) return;
    void router.navigate({ to: '.', search: ((prev: Record<string, unknown>) => ({ ...prev, connect: undefined })) as never, replace: true });
  }, [incoming, router]);
  let page;
  if (search.proposal) page = <ProposalPage id={search.proposal} />;
  else if (search.skill) page = <SkillPage name={search.skill} />;
  else page = <SkillsList search={search} />;
  return (
    <>
      {page}
      {device ? (
        <ApproveDevice
          code={device}
          onClose={() => {
            setDevice(null);
            closeConnect();
          }}
        />
      ) : (
        search.connect === 'setup' && <ConnectDialog />
      )}
    </>
  );
}

function SkillsList({ search }: { search: SkillsSearch }) {
  const skills = useQuery(skillsQuery);
  const asks = useQuery(proposalsQuery);
  const usage = useQuery(skillUsageQuery);
  const words = search.q?.toLowerCase() ?? '';
  const match = (s: { name: string; description: string }) => !words || s.name.toLowerCase().includes(words) || s.description.toLowerCase().includes(words);
  const used = new Map((usage.data ?? []).map((u) => [u.name, u]));
  const byName = new Map((skills.data ?? []).map((s) => [s.name, s]));
  const pending = (asks.data ?? []).filter(match);
  const rows = (skills.data ?? []).filter(match);
  const [making, setMaking] = useState(false);
  const decider = useDecider();

  let state: PageState = 'ready';
  if (skills.isError) state = 'fault';
  else if (!skills.data) state = 'loading';
  else if (!skills.data.length && !asks.data?.length) state = 'empty';

  return (
    <List
      title="Skills"
      path="/library/skills"
      className="lib"
      search={<Find label="Search skills" value={search.q} onFind={(q) => void press('skills.list', { q })} />}
      actions={
        <>
          <OwnerButton page="skills" phone />
          <Button variant="primary" icon="plus" onClick={() => setMaking(true)} data-target="skills:new">
            New skill
          </Button>
          {making && <NewSkill onClose={() => setMaking(false)} />}
        </>
      }
      more={
        <PageMore target="skills:connect">
          <MenuItem label="Connect Claude Code" icon="computer" onSelect={() => void press('skills.connect', {})} />
        </PageMore>
      }
      state={state}
      empty={{ title: 'No skills yet' }}
      onRetry={() => void skills.refetch()}
      sections={
        pending.length || rows.length ? (
          <>
            <div className="skill-cols" aria-hidden="true">
              <span />
              <span>Skill</span>
              <span />
              <span>Has it</span>
              <span>This week</span>
              <span>Last run</span>
            </div>
            <section className="sec">
              {pending.map((p) => (
                <AskLine key={p.id} p={p} skill={byName.get(p.name)} use={used.get(p.name)} />
              ))}
              {rows.map((s) => (
                <SkillLine key={s.name} s={s} use={used.get(s.name)} />
              ))}
            </section>
            {decider && <ForAdmins />}
          </>
        ) : (
          <Empty title="Nothing matches" />
        )
      }
    />
  );
}

/* ---------------------------------------------------------------- an admin's Suggestions and Not in directory */

/** Below the list, for an owner or admin (old skills.ts skillsIdeasHTML, skillsElsewhereHTML). */
function ForAdmins() {
  const ideas = useQuery(skillSuggestionsQuery).data ?? [];
  const elsewhere = useQuery(skillsElsewhereQuery).data ?? [];
  return (
    <>
      {ideas.length > 0 && (
        <Section name="Suggestions" count={ideas.length}>
          {ideas.map((x) => (
            <Idea key={x.id} x={x} />
          ))}
        </Section>
      )}
      {elsewhere.length > 0 && (
        <Section name="Not in directory" count={elsewhere.length}>
          {elsewhere.map((r) => (
            <Row key={r.name} className="lrow skillrow" title={r.name} target={`skill-elsewhere:${r.name}`}>
              <span className="lead" />
              <span className="skill-name">
                <span className="row-title">{r.name}</span>
              </span>
              <span className="row-ctx" />
              <Stack on={r.bots} total={r.bots_count} />
              <span className="row-fig">{r.uses}</span>
              <span className="row-age" />
            </Row>
          ))}
        </Section>
      )}
    </>
  );
}

/** One suggestion: Remove (an install nobody used in 30 days) or Ask to submit (a skill agents run that the directory
    lacks), and Dismiss, in its ⋯. */
function Idea({ x }: { x: SkillSuggestion }) {
  const [removing, setRemoving] = useState(false);
  const who = x.target?.name ?? 'Teammate';
  const agent = x.target?.type === 'Bot' ? x.target.id : null;
  return (
    <Row className="lrow skillrow" title={x.name} target={`suggestion:${x.id}`}>
      <span className="lead" />
      <span className="skill-name">
        <span className="row-title">{x.name}</span>
      </span>
      <span className="row-ctx">{x.kind === 'remove' ? 'Unused' : 'Unlisted'}</span>
      <span className="avs">{x.target && <Face actor={actorOf(x.target)} name={x.target.name} />}</span>
      <span className="row-fig">{x.kind === 'promote' ? x.uses_30d : ''}</span>
      <Menu trigger={<IconButton icon="more" label={`More on ${x.name}`} size="sm" />}>
        {x.kind === 'remove' ? (
          <MenuItem label="Remove" onSelect={() => setRemoving(true)} />
        ) : (
          agent && <MenuItem label="Ask to submit" onSelect={() => void press('skills.askSubmit', { suggestion: x.id, skill: x.name, agent }, 'Not asked')} />
        )}
        <MenuItem label="Dismiss" onSelect={() => void press('skills.dismissSuggestion', { suggestion: x.id }, 'Not dismissed')} />
      </Menu>
      {removing && <RemoveDialog who={who} id="skills.removeUnused" input={{ suggestion: x.id }} onClose={() => setRemoving(false)} />}
    </Row>
  );
}

/** Taking a skill off someone: it leaves on their next session. */
function RemoveDialog({ who, id, input, onClose }: { who: string; id: string; input: unknown; onClose: () => void }) {
  const [busy, go] = useGo(onClose);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Remove from ${who}`}
      actions={<Foot onClose={onClose} label="Remove" busy={busy} ready onGo={() => void go(id, input, 'Not removed')} />}
    />
  );
}

// The front matter a skill's SKILL.md opens with; the server names the skill from the Name field.
const START = '---\ndescription: \n---\n\n';

/** New skill: its name and its SKILL.md, sent as a proposal for an admin's OK; lands on that proposal. */
function NewSkill({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [text, setText] = useState(START);
  const [busy, setBusy] = useState(false);
  const ready = SKILL_NAME.test(name) && !!text.trim() && text.trim() !== START.trim();
  const propose = async () => {
    setBusy(true);
    const r = await run('skills.propose', { name, text }, { via: 'click' });
    setBusy(false);
    if (r.ok) return onClose();
    toast(r.error === 'exists' ? { text: `${name} is already a skill`, kind: 'failed' } : refusal(r, 'Not proposed'));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New skill"
      actions={<Foot onClose={onClose} label="Propose" busy={busy} ready={ready} onGo={() => void propose()} />}
    >
      <Field label="Name" value={name} onValueChange={(v) => setName(v.toLowerCase().replace(/\s+/g, '-'))} placeholder="triage-calls" autoFocus />
      <Field label="Text" area value={text} onValueChange={setText} />
    </Dialog>
  );
}

/** Up to three faces, then how many more (tools.css `.avs`). */
function Stack({ on, total }: { on: SkillActor[]; total: number }) {
  return (
    <span className="avs" title={total ? `${total} teammate${total === 1 ? '' : 's'}` : undefined}>
      {on.slice(0, 3).map((a) => (
        <Face key={actorOf(a)} actor={actorOf(a)} name={a.name} />
      ))}
      <span className="avs-n">{total > 3 ? `+${total - 3}` : ''}</span>
    </span>
  );
}

function Uses({ use, has }: { use: SkillUse | undefined; has: boolean }) {
  return (
    <>
      <span className="row-fig">{has ? (use?.uses ?? 0) : ''}</span>
      {use?.last_used_at ? <Ago at={use.last_used_at} className="row-age" /> : <span className="row-age" />}
    </>
  );
}

/** How many a skill is on: its installs, or for one Tico ships the agents made from its roles. */
const onCount = (s: SkillRow | undefined) => (s ? (s.on_count ?? s.installs_count) : 0);

function SkillLine({ s, use }: { s: SkillRow; use: SkillUse | undefined }) {
  return (
    <Row className="lrow skillrow" title={s.name} href={`/library/skills?skill=${encodeURIComponent(s.name)}`} target={`skill:${s.name}`}>
      <span className="lead" />
      <span className="skill-name">
        <span className="row-title">{s.name}</span>
        <span className="skill-does">{s.description}</span>
      </span>
      <span className="row-ctx" />
      <Stack on={s.on} total={onCount(s)} />
      <Uses use={use} has={onCount(s) > 0} />
    </Row>
  );
}

const proposer = (p: Proposal) => `${p.proposed_by.type === 'Bot' ? 'bot' : 'human'}:${p.proposed_by.id}`;

/** Who a proposal waits on, for a reader who cannot decide it (a member reads only their own). */
const WAITS = 'Waits on an admin';

function AskLine({ p, skill, use }: { p: Proposal; skill: SkillRow | undefined; use: SkillUse | undefined }) {
  const from = useActor(proposer(p));
  const decider = useDecider();
  const what = p.base_version ? 'Change' : 'New';
  return (
    <Row className="lrow skillrow is-ask" title={p.name} href={`/library/skills?proposal=${encodeURIComponent(p.id)}`} target={`proposal:${p.id}`}>
      <span className="lead">{decider && <FlowMark />}</span>
      <span className="skill-name">
        <span className="row-title">{p.name}</span>
        <span className="skill-does">{p.description}</span>
      </span>
      <span className="row-ctx">{!decider ? WAITS : from ? `${what} from ${from.name}` : what}</span>
      <Stack on={skill?.on ?? []} total={onCount(skill)} />
      <Uses use={use} has={onCount(skill) > 0} />
    </Row>
  );
}

/* ---------------------------------------------------------------- one skill */

/** The skill's main text: SKILL.md without its front matter. */
export function mainText(files: SkillFile[]): string {
  const main = files.find((f) => /(^|\/)SKILL\.md$/i.test(f.path)) ?? files[0];
  return (main?.content ?? '').replace(/^---\n[\s\S]*?\n---\n?/, '');
}

function stateOf(q: { isError: boolean; error: unknown; data: unknown }): PageState {
  if (q.isError) return (q.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault';
  return q.data ? 'ready' : 'loading';
}

function SkillPage({ name }: { name: string }) {
  const d = useQuery(skillQuery(name));
  const latest = d.data?.skill.latest;
  const text = useQuery(skillTextQuery(name, latest?.number ?? 0));
  const usage = useQuery(skillUsageQuery);
  const detail = useQuery(skillDetailUsageQuery(name));
  const recent = detail.data?.recent ?? [];
  const coverage = new Map<string, SkillCoverage>((detail.data?.installs ?? []).map((i) => [i.id, i]));
  const removable = (d.data?.installs ?? []).filter((i) => coverage.get(i.id)?.removable);
  const [removing, setRemoving] = useState<{ id: string; who: string } | null>(null);
  const asks = useQuery(proposalsQuery);
  const ask = asks.data?.find((p) => p.name === name);
  const use = usage.data?.find((u) => u.name === name);
  const files = text.data?.files ?? [];
  const extra = files.filter((f) => !/(^|\/)SKILL\.md$/i.test(f.path));
  const [installing, setInstalling] = useState(false);

  return (
    <Document
      title={name}
      path={`/library/skills?skill=${name}`}
      crumb={CRUMB}
      className="lib"
      actions={
        <>
          <OwnerButton page="skills" phone />
          {latest && (
            <Button variant={d.data?.installs.length ? 'plain' : 'primary'} onClick={() => setInstalling(true)} data-target={`skill-install:${name}`}>
              Install
            </Button>
          )}
          {installing && <InstallDialog name={name} versions={d.data?.versions ?? []} onClose={() => setInstalling(false)} />}
          {removing && (
            <RemoveDialog who={removing.who} id="skills.removeInstall" input={{ skill: name, install: removing.id }} onClose={() => setRemoving(null)} />
          )}
        </>
      }
      more={
        <PageMore target={`skill-more:${name}`}>
          {removable.length > 0 && (
            <MenuSub label="Remove from">
              {removable.map((i) => (
                <MenuItem key={i.id} label={i.target.name ?? 'Teammate'} onSelect={() => setRemoving({ id: i.id, who: i.target.name ?? 'Teammate' })} />
              ))}
            </MenuSub>
          )}
        </PageMore>
      }
      state={stateOf(d)}
      onRetry={() => void d.refetch()}
      body={
        d.data && (
          <>
            {ask && <AskBar p={ask} />}
            {latest && (
              <p className="read-by">
                <Face actor={latest.approved_by_id} />
                <span>
                  v{latest.number}, {when(latest.approved_at)}
                </span>
              </p>
            )}
            {d.data.skill.description && <p className="read-lead">{d.data.skill.description}</p>}
            {text.data && <Markdown text={mainText(files)} />}
            {extra.map((f) => (
              <section key={f.path}>
                <h3 className="t-label">{f.path}</h3>
                <pre className="read-code t-code">{f.content}</pre>
              </section>
            ))}
          </>
        )
      }
      side={
        d.data && (
          <div className="sp-side">
            <Facts label="About this skill">
              <LabelValue label="Last run">{use?.last_used_at ? <Ago at={use.last_used_at} /> : 'Not yet'}</LabelValue>
              <LabelValue label="This week">{`${use?.uses ?? 0} runs`}</LabelValue>
              {latest?.approved_at && <LabelValue label="Changed">{when(latest.approved_at)}</LabelValue>}
            </Facts>
            {d.data.installs.length > 0 && (
              <>
                <h3 className="sp-h t-label">Has it</h3>
                {d.data.installs.map((i) => (
                  <span key={i.id} className="sp-who">
                    <Face actor={actorOf(i.target)} name={i.target.name} />
                    <span>{i.target.name ?? 'Teammate'}</span>
                    <em>{coverage.get(i.id)?.uses_30d ?? ''}</em>
                  </span>
                ))}
              </>
            )}
            {recent.length > 0 && (
              <>
                <h3 className="sp-h t-label">Recent runs</h3>
                {recent.map((r, n) => {
                  const href = r.task_id ? `/task/${r.task_id}` : r.thread_id ? `/threads/${r.thread_id}` : undefined;
                  const inner = (
                    <>
                      <Face actor={actorOf(r.actor)} name={r.actor.name} />
                      <span>{r.actor.name ?? 'Teammate'}</span>
                      <em>
                        <Ago at={r.used_at} />
                      </em>
                    </>
                  );
                  return href ? (
                    <Link key={n} className="sp-who" href={href}>
                      {inner}
                    </Link>
                  ) : (
                    <span key={n} className="sp-who">
                      {inner}
                    </span>
                  );
                })}
              </>
            )}
            {d.data.versions.length > 1 && (
              <>
                <h3 className="sp-h t-label">Versions</h3>
                {d.data.versions.map((v) => (
                  <span key={v.number} className="sp-who">
                    <Face actor={v.approved_by_id} />
                    <span>v{v.number}</span>
                    <em>{when(v.approved_at)}</em>
                  </span>
                ))}
              </>
            )}
          </div>
        )
      }
    />
  );
}

/** A change waiting on this skill, over its text (tools.css `.sp-wait`). */
function AskBar({ p }: { p: Proposal }) {
  const from = useActor(proposer(p));
  // The mark says it waits on the reader: only for one who can approve it.
  const decider = useDecider();
  return (
    <Link className="sp-wait" href={`/library/skills?proposal=${encodeURIComponent(p.id)}`} data-target={`proposal:${p.id}`}>
      {decider && <FlowMark />}
      <b>{from ? `Change from ${from.name}` : 'A change'}</b>
      <span>{p.note ?? ''}</span>
      <span className="grow" />
      <em>{decider ? <Ago at={p.created_at} /> : WAITS}</em>
    </Link>
  );
}

/* ---------------------------------------------------------------- a proposed change */

export interface DiffLine {
  kind: '' | 'h' | '+' | '-';
  text: string;
}

/** A unified diff as the page's lines: headings, added, removed, the same. File headers and hunk marks are dropped. */
export function diffLines(diff: string, many: boolean): DiffLine[] {
  const out: DiffLine[] = [];
  for (const line of diff.split('\n')) {
    if (line.startsWith('+++ ')) {
      if (many) out.push({ kind: 'h', text: line.slice(4).replace(/^b\//, '') });
      continue;
    }
    if (line.startsWith('--- ') || line.startsWith('@@') || line.startsWith('\\')) continue;
    const mark = line[0];
    const body = line.slice(1);
    if (mark === '+' || mark === '-') {
      if (body.trim()) out.push({ kind: mark, text: body.replace(/^#+\s*/, '') });
    } else if (body.trim()) out.push({ kind: /^#+\s/.test(body) ? 'h' : '', text: body.replace(/^#+\s*/, '') });
  }
  return out;
}

/** Whether the server lets this reader approve or send back a proposal (backend/brand.py admin): the owner, or an admin
    on their own click. Never an agent; a member reads it only. */
function useDecider(): boolean {
  const me = useQuery(meQuery).data;
  const role = useQuery({ ...accessRoleQuery, enabled: me?.role === 'person' }).data;
  return me?.role === 'owner' || (me?.role === 'person' && (role === 'owner' || role === 'admin'));
}

function ProposalPage({ id }: { id: string }) {
  const d = useQuery(proposalQuery(id));
  const decider = useDecider();
  const skills = useQuery(skillsQuery);
  const [back, setBack] = useState(false);
  const [busy, setBusy] = useState(false);
  const p = d.data?.proposal;
  const from = p ? proposer(p) : null;
  const lines = d.data ? diffLines(d.data.diff, d.data.files.length > 1) : [];
  const added = lines.filter((l) => l.kind === '+').length;
  const cut = lines.filter((l) => l.kind === '-').length;
  const s = d.data?.screening;
  const risky = s && (s.risk === 'high' || s.verdict === 'unsafe');
  const skill = skills.data?.find((k) => k.name === p?.name);
  const open = p?.status === 'pending';
  const me = useQuery(meQuery).data;
  const mine = !!p && !!me && proposer(p) === me.id;
  // What the server says to do about a screening that did not run (backend/skills.py SCREEN_REASONS).
  const action = (p as (Proposal & { screen_action?: string | null }) | undefined)?.screen_action;
  const screening = !s ? (p?.screen_status === 'not_screened' ? 'Not screened' : 'Not done yet') : risky ? 'High risk' : 'Nothing risky';

  const approve = async (acknowledged = false) => {
    setBusy(true);
    const r = await run('skills.approve', { proposal: id, acknowledged: acknowledged || undefined }, { via: 'click' });
    setBusy(false);
    if (r.ok) return;
    if (r.error === 'acknowledgement_required')
      toast({ text: 'Screening rated this high risk', kind: 'failed', action: { label: 'Approve', run: () => void approve(true) } });
    else if (r.error === 'not_screened') toast({ text: 'Screening is not done yet', kind: 'failed' });
    else if (r.error === 'stale') toast({ text: 'The skill changed since. It needs screening again.', kind: 'failed' });
    else toast(refusal(r, 'Not approved'));
  };

  return (
    <Document
      title={p?.name ?? 'Proposed change'}
      path={`/library/skills?proposal=${id}`}
      crumb={CRUMB}
      className="lib"
      actions={<OwnerButton page="skills" phone />}
      more={
        <PageMore target={`proposal-more:${id}`}>
          {open && (decider || mine) && (
            <MenuItem label="Screen again" onSelect={() => void press('skills.rescreen', { proposal: id }, 'Not sent to screening').then((ok) => ok && toast({ text: 'Screening' }))} />
          )}
          {open && mine && <MenuItem label="Withdraw" onSelect={() => void press('skills.withdraw', { proposal: id }, 'Not withdrawn')} />}
        </PageMore>
      }
      state={stateOf(d)}
      onRetry={() => void d.refetch()}
      noAccess={{ text: 'Only an admin or the one who proposed it can read it.' }}
      body={
        d.data &&
        p && (
          <>
            {d.data.stale && <p className="read-note t-small">The skill changed since this was proposed.</p>}
            {p.base_version ? (
              <div className="diff" aria-label="Proposed text against the current one">
                {lines.map((l, n) => (
                  <p
                    key={n}
                    className={l.kind === 'h' ? 'dl is-h' : l.kind === '+' ? 'dl is-add' : l.kind === '-' ? 'dl is-cut' : 'dl'}
                    title={l.kind === '+' ? 'Added' : l.kind === '-' ? 'Removed' : undefined}
                  >
                    {l.text}
                  </p>
                ))}
              </div>
            ) : (
              <>
                {p.description && <p className="read-lead">{p.description}</p>}
                <Markdown text={mainText(d.data.files)} />
              </>
            )}
          </>
        )
      }
      side={
        p && (
          <div className="sp-side">
            <Facts label="About this change">
              <LabelValue label="From">{from && <Who actor={from} className="fact-who" />}</LabelValue>
              <LabelValue label="Sent">{when(p.created_at)}</LabelValue>
              {p.base_version ? <LabelValue label="Changes">{`${added} added, ${cut} removed`}</LabelValue> : null}
              {open ? <LabelValue label="Screening">{screening}</LabelValue> : <LabelValue label="Status">{STATUS[p.status] ?? p.status}</LabelValue>}
            </Facts>
            {p.decline_reason && (
              <>
                <h3 className="sp-h t-label">What to change</h3>
                <p className="sp-why">{p.decline_reason}</p>
              </>
            )}
            {open && !s && p.screen_status === 'not_screened' && action && (
              <>
                <h3 className="sp-h t-label">Screening</h3>
                <p className="sp-why">{action}</p>
              </>
            )}
            {p.note && (
              <>
                <h3 className="sp-h t-label">Why</h3>
                <p className="sp-why">{p.note}</p>
              </>
            )}
            {s?.summary && risky && (
              <>
                <h3 className="sp-h t-label">Screening</h3>
                <p className="sp-why">{s.summary}</p>
              </>
            )}
            {skill && skill.on.length > 0 && (
              <>
                <h3 className="sp-h t-label">Gets it</h3>
                {skill.on.map((a) => (
                  <span key={actorOf(a)} className="sp-who">
                    <Face actor={actorOf(a)} name={a.name} />
                    <span>{a.name ?? 'Teammate'}</span>
                    <em />
                  </span>
                ))}
              </>
            )}
          </div>
        )
      }
      foot={
        open &&
        decider && (
          <>
            <Button onClick={() => setBack(true)} data-target={`proposal-back:${id}`}>
              Send back
            </Button>
            <Button variant="primary" busy={busy} disabled={busy} onClick={() => void approve()} data-target={`proposal-approve:${id}`}>
              Approve
            </Button>
            {back && <SendBack id={id} onClose={() => setBack(false)} />}
          </>
        )
      }
    />
  );
}

/** A decided proposal's state, in the words the page uses (declining is Send back). */
const STATUS: Record<string, string> = { approved: 'Approved', declined: 'Sent back', withdrawn: 'Withdrawn' };

function SendBack({ id, onClose }: { id: string; onClose: () => void }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    const ok = await press('skills.decline', { proposal: id, reason: reason.trim() }, 'Not sent back');
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Send back"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!reason.trim() || busy} onClick={() => void send()}>
            Send back
          </Button>
        </>
      }
    >
      <Field label="What to change" value={reason} onValueChange={setReason} area autoFocus />
    </Dialog>
  );
}

/* ---------------------------------------------------------------- connect Claude Code */

const helperCommand = () =>
  `curl -fsSL --create-dirs ${window.location.origin}/api/v2/skills/helper.mjs -o ~/.tico/skills-helper.mjs && node ~/.tico/skills-helper.mjs setup`;

const closeConnect = () => void press('skills.list', {});

/** Connect Claude Code (ui/app/skills-connect.ts): the one line to run on a computer. */
function ConnectDialog() {
  const command = helperCommand();
  const copy = () =>
    void navigator.clipboard.writeText(command).then(
      () => toast({ text: 'Copied' }),
      () => toast({ text: 'Not copied', kind: 'failed' }),
    );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && closeConnect()}
      title="Connect Claude Code"
      description="Run this on your computer. It opens a page here to approve it."
      actions={<Button onClick={closeConnect}>Done</Button>}
    >
      <div className="connect-cmd">
        <code className="t-code">{command}</code>
        <IconButton icon="copy" label="Copy" onClick={copy} />
      </div>
    </Dialog>
  );
}

type Device = Awaited<ReturnType<typeof previewDevice>>;

/** With the code the helper shows: approving that computer. The code and the server's answer stay in this state. */
function ApproveDevice({ code, onClose }: { code: string; onClose: () => void }) {
  const [d, setD] = useState<{ data?: Device; isError?: boolean }>({});
  useEffect(() => {
    let live = true;
    void previewDevice(code).then(
      (data) => live && setD({ data }),
      () => live && setD({ isError: true }),
    );
    return () => {
      live = false;
    };
  }, [code]);
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<'approved' | 'denied' | null>(null);
  const decide = async (approve: boolean) => {
    setBusy(true);
    const ok = await press('skills.decideDevice', { code, approve, share }, approve ? 'Not connected' : 'Not denied');
    setBusy(false);
    if (ok) setDone(approve ? 'approved' : 'denied');
  };
  const gone = d.isError;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Connect Claude Code"
      description={
        done === 'approved' ? 'Connected. Go back to your terminal.' : done === 'denied' ? 'Denied.' : gone ? 'This code has expired or was used. Run setup again.' : undefined
      }
      actions={
        done || gone ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button disabled={busy || !d.data} onClick={() => void decide(false)}>
              Deny
            </Button>
            <Button variant="primary" busy={busy} disabled={busy || !d.data} onClick={() => void decide(true)}>
              Approve
            </Button>
          </>
        )
      }
    >
      {d.data && !done && (
        <div className="connect-dev">
          <p className="t-read">
            <b>{d.data.label || 'This computer'}</b> <code className="t-code">{d.data.user_code}</code>
          </p>
          <Checkbox label="Share which skills I use" checked={share} onCheckedChange={setShare} />
        </div>
      )}
    </Dialog>
  );
}

/** Install (skills.ts skillsInstallDialog): for me, one agent, one person, or a team's current members; the latest
   version, or one pinned. */
function InstallDialog({ name, versions, onClose }: { name: string; versions: SkillVersion[]; onClose: () => void }) {
  const t = useQuery(installTargetsQuery);
  const [who, setWho] = useState<string | null>(null);
  const [version, setVersion] = useState('latest');
  const [busy, go] = useGo(onClose);
  const me = t.data?.me?.type === 'Human' ? t.data.me : null;
  const options = [
    ...(me ? [{ value: `Human:${me.id}`, label: 'Me' }] : []),
    ...(t.data?.bots ?? []).map((b) => ({ value: `Bot:${b.id}`, label: b.name })),
    ...(t.data?.people ?? []).filter((p) => p.id !== me?.id).map((p) => ({ value: `Human:${p.id}`, label: p.name })),
    ...(t.data?.teams ?? []).map((g) => ({ value: `team:${g.id}`, label: `${g.name} (${g.targets.length})` })),
  ];
  const targets = () => {
    if (who?.startsWith('team:')) return t.data?.teams.find((g) => `team:${g.id}` === who)?.targets ?? [];
    const at = who?.indexOf(':') ?? -1;
    return who && at > 0 ? [{ target_type: who.slice(0, at) as 'Bot' | 'Human', target_id: who.slice(at + 1) }] : [];
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Install ${name}`}
      actions={
        <Foot
          onClose={onClose}
          label="Install"
          busy={busy}
          ready={!!who}
          onGo={() => void go('skills.install', { skill: name, targets: targets(), version: version === 'latest' ? undefined : Number(version) }, 'Not installed')}
        />
      }
    >
      {t.isError && <Empty title="This did not load" icon="alert" />}
      <Select label="For" options={options} value={who} onValueChange={setWho} placeholder="Choose" />
      <Select
        label="Version"
        options={[{ value: 'latest', label: 'Latest' }, ...versions.map((v) => ({ value: String(v.number), label: `v${v.number} only` }))]}
        value={version}
        onValueChange={setVersion}
      />
    </Dialog>
  );
}
