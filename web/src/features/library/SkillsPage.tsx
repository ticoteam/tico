import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { run } from '@/actions';
import { useActor } from '@/data';
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
import { Document, List, type PageState } from '@/pagekinds';
import { Markdown } from '@/kit/Markdown';
import { Foot, useGo } from './AddTool';
import { Face, Find, OwnerButton, press, when, Who } from './parts';
import {
  deviceCodeQuery,
  installTargetsQuery,
  proposalQuery,
  proposalsQuery,
  skillQuery,
  skillRecentQuery,
  skillsQuery,
  skillTextQuery,
  skillUsageQuery,
  type Proposal,
  type SkillActor,
  type SkillFile,
  type SkillRow,
  type SkillUse,
  type SkillVersion,
} from './queries';
import type { SkillsSearch } from './search';
import './library.css';

/* Skills (tools.js skillsPage, skillPage, proposalPage): one list, no tabs. A proposed change waits at the top with the
   Flow mark; a skill opens as its text with who has it beside it; a proposal opens as its changes, with Approve and
   Send back at the foot. */

const CRUMB = [{ label: 'Skills', to: '/library/skills' }];

/** A skill's install or use as a directory id: agents are `bot:<id>`, people `human:<id>`. */
export const actorOf = (a: { type: string; id: string }) => `${a.type === 'Bot' ? 'bot' : 'human'}:${a.id}`;

export function SkillsPage({ search }: { search: SkillsSearch }) {
  let page;
  if (search.proposal) page = <ProposalPage id={search.proposal} />;
  else if (search.skill) page = <SkillPage name={search.skill} />;
  else page = <SkillsList search={search} />;
  return (
    <>
      {page}
      {search.connect && <ConnectDialog code={search.connect === 'setup' ? undefined : search.connect} />}
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

  let state: PageState = 'ready';
  if (skills.isError) state = 'fault';
  else if (!skills.data) state = 'loading';
  else if (!skills.data.length && !asks.data?.length) state = 'empty';

  return (
    <List
      title="Skills"
      path="/library/skills"
      className="lib"
      actions={
        <>
          <Find label="Search skills" value={search.q} onFind={(q) => void press('skills.list', { q })} />
          <OwnerButton page="skills" />
          <Button icon="computer" title="Connect Claude Code" onClick={() => void press('skills.connect', {})} data-target="skills:connect">
            Claude Code
          </Button>
        </>
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
                <SkillLine key={s.id} s={s} use={used.get(s.name)} />
              ))}
            </section>
          </>
        ) : (
          <Empty title="Nothing matches" />
        )
      }
    />
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

function SkillLine({ s, use }: { s: SkillRow; use: SkillUse | undefined }) {
  return (
    <Row className="lrow skillrow" title={s.name} href={`/library/skills?skill=${encodeURIComponent(s.name)}`} target={`skill:${s.name}`}>
      <span className="lead" />
      <span className="skill-name">
        <span className="row-title">{s.name}</span>
        <span className="skill-does">{s.description}</span>
      </span>
      <span className="row-ctx" />
      <Stack on={s.on} total={s.installs_count} />
      <Uses use={use} has={s.installs_count > 0} />
    </Row>
  );
}

const proposer = (p: Proposal) => `${p.proposed_by.type === 'Bot' ? 'bot' : 'human'}:${p.proposed_by.id}`;

function AskLine({ p, skill, use }: { p: Proposal; skill: SkillRow | undefined; use: SkillUse | undefined }) {
  const from = useActor(proposer(p));
  const what = p.base_version ? 'Change' : 'New';
  return (
    <Row className="lrow skillrow is-ask" title={p.name} href={`/library/skills?proposal=${encodeURIComponent(p.id)}`} target={`proposal:${p.id}`}>
      <span className="lead" title="Waits on you">
        <FlowMark />
      </span>
      <span className="skill-name">
        <span className="row-title">{p.name}</span>
        <span className="skill-does">{p.description}</span>
      </span>
      <span className="row-ctx">{from ? `${what} from ${from.name}` : what}</span>
      <Stack on={skill?.on ?? []} total={skill?.installs_count ?? 0} />
      <Uses use={use} has={!!skill?.installs_count} />
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
  const recent = useQuery(skillRecentQuery(name));
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
          <OwnerButton page="skills" />
          {latest && (
            <Button variant={d.data?.installs.length ? 'plain' : 'primary'} onClick={() => setInstalling(true)} data-target={`skill-install:${name}`}>
              Install
            </Button>
          )}
          {installing && <InstallDialog name={name} versions={d.data?.versions ?? []} onClose={() => setInstalling(false)} />}
        </>
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
                    <em />
                  </span>
                ))}
              </>
            )}
            {(recent.data?.length ?? 0) > 0 && (
              <>
                <h3 className="sp-h t-label">Recent runs</h3>
                {recent.data?.map((r, n) => {
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
  return (
    <Link className="sp-wait" href={`/library/skills?proposal=${encodeURIComponent(p.id)}`} data-target={`proposal:${p.id}`}>
      <FlowMark />
      <b>{from ? `Change from ${from.name}` : 'A change'}</b>
      <span>{p.note ?? ''}</span>
      <span className="grow" />
      <em>
        <Ago at={p.created_at} />
      </em>
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

function ProposalPage({ id }: { id: string }) {
  const d = useQuery(proposalQuery(id));
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

  const approve = async (acknowledged = false) => {
    setBusy(true);
    const r = await run('skills.approve', { proposal: id, acknowledged: acknowledged || undefined }, { via: 'click' });
    setBusy(false);
    if (r.ok) return;
    if (r.error === 'acknowledgement_required')
      toast({ text: 'Screening rated this high risk', kind: 'failed', action: { label: 'Approve', run: () => void approve(true) } });
    else if (r.error === 'not_screened') toast({ text: 'Screening is not done yet', kind: 'failed' });
    else if (r.error === 'stale') toast({ text: 'The skill changed since. It needs screening again.', kind: 'failed' });
    else toast({ text: r.error === 'not_allowed' ? 'You cannot do that here' : 'Not approved', kind: 'failed' });
  };

  return (
    <Document
      title={p?.name ?? 'Proposed change'}
      path={`/library/skills?proposal=${id}`}
      crumb={CRUMB}
      className="lib"
      actions={<OwnerButton page="skills" />}
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
              <LabelValue label="Screening">{!s ? 'Not done yet' : risky ? 'High risk' : 'Nothing risky'}</LabelValue>
            </Facts>
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
        open && (
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

/**
 * Connect Claude Code (ui/app/skills-connect.ts): the one line to run on a computer, or, with the code the helper
 * shows, approving that computer.
 */
function ConnectDialog({ code }: { code?: string }) {
  if (code) return <ApproveDevice code={code} />;
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

function ApproveDevice({ code }: { code: string }) {
  const d = useQuery(deviceCodeQuery(code));
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
      onOpenChange={(o) => !o && closeConnect()}
      title="Connect Claude Code"
      description={
        done === 'approved' ? 'Connected. Go back to your terminal.' : done === 'denied' ? 'Denied.' : gone ? 'This code has expired or was used. Run setup again.' : undefined
      }
      actions={
        done || gone ? (
          <Button onClick={closeConnect}>Done</Button>
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
