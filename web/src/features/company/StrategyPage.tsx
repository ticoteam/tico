import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Fault } from '@/kit/Fault';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { FoldButton } from '@/kit/SectionHead';
import { SkeletonRows } from '@/kit/Skeleton';
import { fmtDate } from '@/lib';
import { Document, type PageState, type PageTab } from '@/pagekinds';
import { lineDiff, paragraphs } from './diff';
import { GoalsPage, type GoalsSearch } from './GoalsPage';
import { MarketTab, spendOf } from './MarketPage';
import { OrgTab } from './OrgPage';
import { Face, Go, NameOf, refusal, usdShort, useDo } from './parts';
import { useAction } from '@/actions';
import { toast } from '@/kit/Toast';
import {
  brandProposalsQuery,
  brandQuery,
  brandVersionQuery,
  listeningStatsQuery,
  marketSpendQuery,
  strategyDiffQuery,
  strategyQuery,
  strategyVersionQuery,
  strategyVersionsQuery,
  type Bet,
  type Brand,
  type BrandBody,
  type BrandHit,
  type StrategyDiff,
  type StrategyVersion,
  type VersionRow,
} from './queries';
import './company.css';

/* Strategy and its tabs, in the mock's order (pages/3-company/company.js strategyPage). Each tab is its own address
   under /company/strategy, so Back and a shared link restore it. Budget is the agents' spend limits (company.js
   budgetBody, old #/strategy/budget): its tab opens Usage's Limits, where a limit is changed. Strategy and Brand keep versions: `?v=` reads a past one, `?compare` marks what changed from the one
   before (files.js docPage: the version pick and the changes count in the head). */

export const STRATEGY_TABS = ['strategy', 'market', 'org', 'goals', 'brand'] as const;
export type StrategyTab = (typeof STRATEGY_TABS)[number];
export type StrategySearch = GoalsSearch & { v?: number; compare?: boolean; edit?: boolean };
const BASE = '/company/strategy';
const tab = (t: StrategyTab, label: string): PageTab => ({ label, to: t === 'strategy' ? BASE : `${BASE}/${t}`, target: `tab:${t}` });
export const strategyTabs: readonly PageTab[] = [
  tab('strategy', 'Strategy'),
  tab('market', 'Market'),
  tab('org', 'Org'),
  tab('goals', 'Goals'),
  { label: 'Budget', to: '/company/usage', search: { view: 'limits' }, target: 'tab:budget' },
  tab('brand', 'Brand'),
];
const head = (t: StrategyTab) => ({ title: 'Strategy', path: t === 'strategy' ? BASE : `${BASE}/${t}`, tabs: strategyTabs, className: 'co' });

export function StrategyPage({ tab: t, search = {} }: { tab: StrategyTab; search?: StrategySearch }) {
  switch (t) {
    case 'market':
      return <MarketTab head={head('market')} search={search} />;
    case 'org':
      return <OrgTab head={head('org')} search={search} />;
    case 'goals':
      return <GoalsPage head={head('goals')} search={search} />;
    case 'brand':
      return <BrandTab search={search} />;
    default:
      return <StrategyTabBody search={search} />;
  }
}

// ---------------------------------------------------------------- versions: the pick and the changes count

/** The version in the head, and the menu of all of them; the newest opens without `?v=`. */
function VersionPick({ rows, shown, action }: { rows: readonly VersionRow[]; shown: number; action: string }) {
  const open = useDo(action);
  if (rows.length < 2) return null;
  const latest = rows[0]?.n;
  return (
    <Menu
      trigger={
        <button type="button" className="btn btn-quiet ver-pick" title="Versions" data-target={`versions:${action}`}>
          v{shown}
          <Icon name="chevdown" />
        </button>
      }
    >
      {rows.map((r) => (
        <MenuItem
          key={r.n}
          label={`v${r.n}`}
          meta={r.published_at ? fmtDate(r.published_at) : undefined}
          lead={<Face id={r.published_by} />}
          on={r.n === shown}
          onSelect={() => void open.run({ v: r.n === latest ? undefined : r.n })}
        />
      ))}
    </Menu>
  );
}

/** How many things changed from the version before; pressed, the page marks them. */
function ChangesPick({ count, on, from }: { count: number; on: boolean; from?: number }) {
  const mark = useDo('strategy.markChanges');
  if (!count) return null;
  return (
    <button
      type="button"
      className="btn btn-quiet chg-pick"
      aria-pressed={on}
      title={`Mark what changed from v${from}`}
      onClick={() => void mark.run({ on: !on })}
    >
      {count} change{count === 1 ? '' : 's'}
    </button>
  );
}

/** Who saved the marked version, and when. */
function ChangedBy({ v }: { v: Pick<StrategyVersion, 'published_by' | 'published_at'> }) {
  return (
    <span className="chg-by">
      <span>
        <NameOf id={v.published_by} />
      </span>
      {v.published_at && <time>{fmtDate(v.published_at)}</time>}
    </span>
  );
}

/** The version just before `n` in the history (newest first), if there is one. */
const before = (rows: readonly VersionRow[], n: number | undefined) => {
  const i = rows.findIndex((r) => r.n === n);
  return i >= 0 ? rows[i + 1]?.n : undefined;
};

// ---------------------------------------------------------------- Strategy: the page the Strategy owner keeps

const PARTS: [string, string][] = [
  ['serve', 'Who we serve'],
  ['sell', 'What we sell, and for how much'],
  ['proof', 'How we know they want it'],
  ['now', 'Why now'],
  ['know', "What we know that others don't"],
  ['how', 'How we sell'],
  ['not', "What we won't do"],
];

function StrategyTabBody({ search }: { search: StrategySearch }) {
  const q = useQuery(strategyQuery);
  const versions = useQuery(strategyVersionsQuery);
  const s = q.data;
  const latest = s?.current?.n;
  const asked = search.v && search.v !== latest ? search.v : undefined;
  const past = useQuery({ ...strategyVersionQuery(asked ?? 0), enabled: !!asked });
  const rows = versions.data ?? [];
  const shown = asked ?? latest;
  const from = before(rows, shown);
  const diff = useQuery({ ...strategyDiffQuery(from ?? 0, shown ?? 0), enabled: !!from && !!shown });
  const d = diff.data;
  // The count is what the page shows: the guide is the page's setup, not its words.
  const count = d ? d.fields.filter((f) => f.key !== 'guide').length + d.bets.added.length + d.bets.retired.length + d.bets.changed.length : 0;
  const v = asked ? past.data : s?.current;
  const state: PageState =
    q.isPending || (asked && past.isPending) ? 'loading' : q.isError || (asked && past.isError) ? 'fault' : v || s?.bets.length ? 'ready' : 'empty';
  return (
    <Document
      {...head('strategy')}
      actions={
        shown && (
          <>
            <ChangesPick count={count} on={!!search.compare} from={from} />
            <VersionPick rows={rows} shown={shown} action="strategy.openVersion" />
          </>
        )
      }
      state={state}
      onRetry={() => void (asked ? past.refetch() : q.refetch())}
      empty={{ title: 'No strategy yet' }}
      body={
        s && (
          <StrategyText
            v={v ?? null}
            bets={asked ? (past.data?.bets ?? []) : s.bets}
            live={asked ? undefined : s}
            marks={search.compare ? d : undefined}
          />
        )
      }
    />
  );
}

type Live = { goals_by_bet?: Record<string, { owner_id: string }[]>; health_by_bet?: Record<string, string | null> };

/** The strategy as written in one version. `live` is the newest one's goals and health, which a past version has not;
    `marks` is what changed from the version before, shown in place. */
function StrategyText({ v, bets: all, live, marks }: { v: StrategyVersion | null; bets: Bet[]; live?: Live; marks?: StrategyDiff }) {
  const sections = v?.sections ?? {};
  const extra = { ...(v?.sections.extra ?? {}) };
  const changed = new Map((marks?.fields ?? []).map((f) => [f.key, f]));
  // An extra section the version took out still shows, struck through.
  for (const f of marks?.fields ?? []) {
    const k = f.key.startsWith('sections.extra.') ? f.key.slice(15) : '';
    if (k && !f.to && f.from && typeof f.from === 'object') extra[k] = f.from as { title: string; body: string };
  }
  const bets = all.filter((b) => b.status !== 'retired');
  const added = new Set(marks?.bets.added.map((b) => b.id));
  const edited = new Map(marks?.bets.changed.map((c) => [c.id, c.from]));
  const by = v && <ChangedBy v={v} />;
  const lead = changed.get('summary') ?? (v?.summary ? undefined : changed.get('vision'));
  return (
    <>
      {v && (
        <div className="read-by">
          <Face id={v.published_by} />
          <b>
            <NameOf id={v.published_by} />
          </b>
          <span>v{v.n}</span>
          <span className="grow" />
          {v.published_at && <span className="row-age">{fmtDate(v.published_at)}</span>}
        </div>
      )}
      {lead ? (
        <div className="chg">
          <p className="read-lead">
            <Marked from={lead.from} to={lead.to} />
          </p>
          {by}
        </div>
      ) : (
        (v?.summary || v?.vision) && <p className="read-lead">{v.summary || v.vision}</p>
      )}
      <WherePlay />
      {(bets.length > 0 || (marks?.bets.retired.length ?? 0) > 0) && (
        <Part name="Bets">
          <ol className="bets">
            {bets.map((b) => {
              const owner = live?.goals_by_bet?.[b.id]?.[0]?.owner_id;
              const health = live?.health_by_bet?.[b.id];
              const was = edited.get(b.id);
              const mark = added.has(b.id) || !!was;
              return (
                <li key={b.id} className={mark ? 'bet chg' : 'bet'}>
                  <span>{added.has(b.id) ? <ins>{b.statement}</ins> : was && was.statement !== b.statement ? <Marked from={was.statement} to={b.statement} /> : b.statement}</span>
                  {b.status === 'proposed' ? <Pill flag>Proposed</Pill> : health === 'red' ? <Pill flag>Behind</Pill> : <span />}
                  {owner ? <Face id={owner} /> : <span />}
                </li>
              );
            })}
            {marks?.bets.retired.map((b) => (
              <li key={b.id} className="bet chg">
                <span>
                  <del>{b.statement}</del>
                </span>
                <span />
                <span />
              </li>
            ))}
          </ol>
        </Part>
      )}
      <Rivals />
      <News />
      {PARTS.map(([k, name]) => {
        const f = changed.get(`sections.${k}`);
        const body = sections[k];
        if (f && (f.from || f.to))
          return (
            <Part key={k} name={name}>
              <div className="chg">
                <MarkedParas from={f.from} to={f.to} />
                {by}
              </div>
            </Part>
          );
        return typeof body === 'string' && body.trim() ? (
          <Part key={k} name={name}>
            <Paras text={body} />
          </Part>
        ) : null;
      })}
      {Object.entries(extra).map(([k, x]) => {
        const f = changed.get(`sections.extra.${k}`);
        const was = (f?.from ?? null) as { body?: string } | null;
        const now = (f?.to ?? null) as { body?: string } | null;
        return (
          <Part key={k} name={x.title}>
            {f ? (
              <div className="chg">
                <MarkedParas from={was?.body} to={now?.body} />
                {by}
              </div>
            ) : (
              <Paras text={x.body} />
            )}
          </Part>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------- the market's rows, as Market shows them

const MARKET = '/company/strategy/market';
const forbidden = (e: unknown) => (e as { status?: number } | null)?.status === 403;

/** The Market read's waiting, failed and closed states. */
function MarketState({ q }: { q: UseQueryResult<unknown> }) {
  if (q.isPending) return <SkeletonRows rows={3} />;
  if (forbidden(q.error)) return <p className="none">No access</p>;
  return (
    <div className="play-fault">
      <Fault label="Market did not load" size={16} />
      <Button onClick={() => void q.refetch()}>Try again</Button>
    </div>
  );
}

/** The market's segments, each with its size as Market shows it, ours in bold; a row opens its Market panel. The mock's
    share column is left out: the server has no number for it (GET /api/v2/market/spend sizes segments, it splits nothing). */
function WherePlay() {
  const q = useQuery(marketSpendQuery);
  const segs = q.data?.segments ?? [];
  return (
    <Part name="Where we play">
      {!q.isSuccess ? (
        <MarketState q={q} />
      ) : segs.length ? (
        <div>
          {segs.map((s) => {
            const usd = spendOf(s);
            return (
              <Go key={s.id} to={MARKET} search={{ panel: `segment:${s.id}` }} className={s.self ? 'play is-us' : 'play'} data-target={`segment:${s.id}`}>
                <span className="row-title">{s.name}</span>
                <span className="row-fig">{usd != null ? usdShort(usd) : <span className="none">Not sized</span>}</span>
              </Go>
            );
          })}
        </div>
      ) : (
        <p className="none">None yet</p>
      )}
    </Part>
  );
}

/** The organizations the market lists as players, once each; one with a note opens it on Market. */
function Rivals() {
  const q = useQuery(marketSpendQuery);
  const seen = new Set<string>();
  const players = (q.data?.segments ?? [])
    .flatMap((s) => s.players ?? [])
    .filter((p) => {
      const k = p.entity_id ?? `name:${p.name}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  // Waiting, failed or closed, Where we play above says so once.
  if (!q.isSuccess) return null;
  return (
    <Part name="Competitors">
      {players.length ? (
        <div>
          {players.map((p) =>
            p.entity_id ? (
              <Go key={p.entity_id} to={MARKET} search={{ panel: `player:${p.entity_id}` }} className="play" data-target={`player:${p.entity_id}`}>
                <span className="row-title">{p.name}</span>
              </Go>
            ) : (
              <div key={`name:${p.name}`} className="play">
                <span className="row-title">{p.name}</span>
              </div>
            ),
          )}
        </div>
      ) : (
        <p className="none">None yet</p>
      )}
    </Part>
  );
}

/** What Listening found lately (GET /api/v2/listening/stats): the newest posts with a link, each opening where it was
    written. Left out while Listening has nothing (the read gives null when it fails). */
function News() {
  const q = useQuery(listeningStatsQuery);
  const posts = (q.data?.recent ?? []).filter((p) => /^https?:\/\//i.test(p.url ?? '')).slice(0, 8);
  if (!posts.length) return null;
  return (
    <Part name="Related news">
      <div>
        {posts.map((p) => (
          <a key={p.item_id} href={p.url ?? undefined} target="_blank" rel="noreferrer" className="play">
            <span className="row-title" title={p.text}>
              {p.text}
            </span>
            <span className="row-age">{p.source}</span>
          </a>
        ))}
      </div>
    </Part>
  );
}

/** One line changed: the old words struck, the new ones marked. */
function Marked({ from, to }: { from: unknown; to: unknown }) {
  const was = typeof from === 'string' ? from : '';
  const now = typeof to === 'string' ? to : '';
  return (
    <>
      {was && <del>{was}</del>}
      {was && now && ' '}
      {now && <ins>{now}</ins>}
    </>
  );
}

/** Paragraphs changed: the ones kept as they were, the ones added marked, the ones taken out struck. */
function MarkedParas({ from, to }: { from: unknown; to: unknown }) {
  return (
    <>
      {lineDiff(paragraphs(from), paragraphs(to)).map(([op, p], i) => (
        <p key={i}>{op === '=' ? p : op === '+' ? <ins>{p}</ins> : <del>{p}</del>}</p>
      ))}
    </>
  );
}

/** A heading of the page that folds what stands under it. */
function Part({ name, children }: { name: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <h2>
        <FoldButton name={name} open={open} onToggle={() => setOpen(!open)} />
      </h2>
      {open && children}
    </>
  );
}

/** Text in paragraphs, as written: one per blank line. */
function Paras({ text }: { text: string }) {
  return paragraphs(text).map((p, i) => <p key={i}>{p}</p>);
}

// ---------------------------------------------------------------- Brand: the published book, in a frame

/** The sections the book shows, in its order. */
const BOOK = ['name', 'positioning', 'voice', 'tone', 'words', 'naming', 'visual', 'claims'] as const;
type BookKey = (typeof BOOK)[number];
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
/** The sections that differ between two bodies. */
const brandChanges = (was: BrandBody, now: BrandBody) => BOOK.filter((k) => !same(was[k], now[k]));

function BrandTab({ search }: { search: StrategySearch }) {
  const q = useQuery(brandQuery);
  const b = q.data;
  if (search.edit && b?.may_edit) return <BrandEdit key={b.draft?.revision ?? 'new'} brand={b} />;
  return <BrandBook q={q} search={search} />;
}

function BrandBook({ q, search }: { q: UseQueryResult<Brand>; search: StrategySearch }) {
  const edit = useDo('brand.edit');
  const b = q.data;
  const latest = b?.published?.n;
  const asked = search.v && search.v !== latest ? search.v : undefined;
  const past = useQuery({ ...brandVersionQuery(asked ?? 0), enabled: !!asked });
  const rows = b?.versions ?? [];
  const shown = asked ?? latest;
  const from = before(rows, shown);
  const prev = useQuery({ ...brandVersionQuery(from ?? 0), enabled: !!from });
  const book = asked ? past.data : b?.published;
  const count = book && prev.data ? brandChanges(prev.data.body, book.body).length : 0;
  const state: PageState = q.isPending || (asked && past.isPending) ? 'loading' : q.isError || (asked && past.isError) ? 'fault' : book ? 'ready' : 'empty';
  const editButton = b?.may_edit && !asked && (
    <Button onClick={() => void edit.run({ on: true })} data-target="brand:edit">
      Edit
    </Button>
  );
  return (
    <Document
      {...head('brand')}
      framed
      actions={
        <>
          {shown && <ChangesPick count={count} on={!!search.compare} from={from} />}
          {shown && <VersionPick rows={rows} shown={shown} action="brand.openVersion" />}
          {editButton}
        </>
      }
      state={state}
      onRetry={() => void (asked ? past.refetch() : q.refetch())}
      empty={{ title: 'No brand book yet', action: editButton }}
      body={
        book && (
          <div className="frame is-inset brand-frame">
            <iframe title="Brand book" srcDoc={brandDoc(book.body, search.compare ? prev.data?.body : undefined)} sandbox="" />
          </div>
        )
      }
    />
  );
}

const esc = (s: string | null | undefined) => (s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const isHex = (s: string | null | undefined) => !!s && /^#[0-9a-f]{3,8}$/i.test(s);

/** The brand book as its own small page. It keeps its own look: its colors come from the book. With `was`, what
    changed from that body is marked in place: a bar at the left, the new words marked, the old ones struck. */
export function brandDoc(b: BrandBody, was?: BrandBody): string {
  const sec = (name: string, inner: string) => (inner ? `<h2>${esc(name)}</h2>${inner}` : '');
  const items = <T,>(now: T[] | null | undefined, then: T[] | null | undefined, li: (x: T) => string): string[] => {
    const cur = (now ?? []).map(li);
    if (!was) return cur.map((h) => `<li>${h}</li>`);
    return lineDiff((then ?? []).map(li), cur).map(([op, h]) => (op === '=' ? `<li>${h}</li>` : `<li class="chg">${op === '+' ? `<ins>${h}</ins>` : `<del>${h}</del>`}</li>`));
  };
  const list = (rows: string[]) => (rows.length ? `<ul>${rows.join('')}</ul>` : '');
  const text = (now: string | null | undefined, then: string | null | undefined) =>
    !was || (now ?? '') === (then ?? '') ? esc(now) : `${then ? `<del>${esc(then)}</del> ` : ''}${now ? `<ins>${esc(now)}</ins>` : ''}`;
  const marked = (now: string | null | undefined, then: string | null | undefined) => !!was && (now ?? '') !== (then ?? '');
  const sw = (c: { name?: string | null; hex?: string | null }) => `<span class="sw"><i style="background:${c.hex}"></i>${esc(c.name ?? c.hex)}</span>`;
  const colors = (x: BrandBody) => (x.visual?.colors ?? []).filter((c) => isHex(c.hex));
  const swatches = (was ? lineDiff(colors(was).map(sw), colors(b).map(sw)) : colors(b).map((h) => ['=', sw(h)] as const))
    .map(([op, h]) => (op === '=' ? h : op === '+' ? `<ins>${h}</ins>` : `<del>${h}</del>`))
    .join('');
  const name = text(b.name, was?.name);
  const positioning = text(b.positioning, was?.positioning);
  const imagery = text(b.visual?.imagery, was?.visual?.imagery);
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:32px 40px;font:15px/1.55 ${(b.visual?.fonts ?? []).map((f) => `"${esc(f)}"`).join(',') || 'system-ui'},system-ui,sans-serif}
h1{font-size:28px;margin:0 0 8px}h2{font-size:15px;margin:28px 0 8px}ul{padding-left:18px;margin:0}li{margin:4px 0}
.sw{display:inline-flex;align-items:center;gap:8px;margin:0 16px 8px 0}.sw i{width:20px;height:20px;border-radius:50%;display:inline-block}
.chg{position:relative}.chg::before{content:"";position:absolute;left:-14px;top:3px;bottom:3px;width:2px;border-radius:1px;background:currentColor;opacity:.6}
li.chg::before{left:-32px}ins{text-decoration:none;background:color-mix(in srgb,currentColor 12%,transparent);border-radius:3px;padding:2px 1px}del{opacity:.6;text-decoration:line-through}
</style></head><body>
<h1${marked(b.name, was?.name) ? ' class="chg"' : ''}>${name}</h1>${positioning ? `<p${marked(b.positioning, was?.positioning) ? ' class="chg"' : ''}>${positioning}</p>` : ''}
${sec('Voice', list(items(b.voice, was?.voice, (v) => `<b>${esc(v.trait)}</b>${v.do ? `. Do: ${esc(v.do)}` : ''}${v.dont ? `. Don't: ${esc(v.dont)}` : ''}`)))}
${sec('Tone', list(items(b.tone, was?.tone, (t) => `<b>${esc(t.situation)}</b>: ${esc(t.guide)}`)))}
${sec('Words we use', list(items(b.words?.use, was?.words?.use, (w) => `${esc(w.word)}${w.not ? `, not ${esc(w.not)}` : ''}`)))}
${sec('Words we avoid', list(items(b.words?.avoid, was?.words?.avoid, (w) => `${esc(w.word)}${w.why ? `: ${esc(w.why)}` : ''}`)))}
${sec('Naming', list(items(b.naming, was?.naming, (n) => `<b>${esc(n.name)}</b>${n.rule ? `: ${esc(n.rule)}` : ''}`)))}
${sec('Colors', swatches)}
${sec('Imagery', imagery ? `<p${marked(b.visual?.imagery, was?.visual?.imagery) ? ' class="chg"' : ''}>${imagery}</p>` : '')}
${sec('Claims we can make', list(items(b.claims?.allowed, was?.claims?.allowed, (c) => `${esc(c.claim)}${c.proof ? `: ${esc(c.proof)}` : ''}`)))}
${sec('Claims we never make', list(items(b.claims?.never, was?.claims?.never, (c) => esc(c))))}
</body></html>`;
}

// ---------------------------------------------------------------- Brand: the draft, edited a section at a time

type Row = Record<string, unknown>;
type Cols = readonly (readonly [string, string])[];

/** The book as fields. Save writes the sections that changed onto the draft; with nothing unsaved and the draft
    ahead of the book, Publish makes it the next version. The draft keeps what this form does not show. */
function BrandEdit({ brand }: { brand: Brand }) {
  const save = useDo('brand.save');
  const publish = useDo('brand.publish');
  const close = useDo('brand.edit');
  const base = brand.draft?.body ?? brand.published?.body ?? {};
  const [body, setBody] = useState<BrandBody>(base);
  const [note, setNote] = useState('');
  const dirty = BOOK.filter((k) => !same(base[k], body[k]));
  const ahead = (brand.draft?.changed ?? []).length > 0;
  const set = <K extends BookKey>(k: K, value: BrandBody[K]) => setBody((x) => ({ ...x, [k]: value }));
  const words = body.words ?? {};
  const visual = body.visual ?? {};
  const claims = body.claims ?? {};
  return (
    <Document
      {...head('brand')}
      side={<BrandRail />}
      body={
        <div className="brand-edit">
          <h2>Name</h2>
          <Field label="Name" labelHidden value={body.name ?? ''} onValueChange={(v) => set('name', v)} />
          <h2>Positioning</h2>
          <Field label="Positioning" labelHidden area value={body.positioning ?? ''} onValueChange={(v) => set('positioning', v)} />
          <h2>Voice</h2>
          <Rows name="Voice" rows={body.voice} cols={[['trait', 'Trait'], ['do', 'Do'], ['dont', "Don't"]]} onChange={(r) => set('voice', r as BrandBody['voice'])} />
          <h2>Tone</h2>
          <Rows name="Tone" rows={body.tone} cols={[['situation', 'When'], ['guide', 'How']]} onChange={(r) => set('tone', r as BrandBody['tone'])} />
          <h2>Words we use</h2>
          <Rows name="Words we use" rows={words.use} cols={[['word', 'Word'], ['not', 'Not']]} onChange={(r) => set('words', { ...words, use: r as never })} />
          <h2>Words we avoid</h2>
          <Rows name="Words we avoid" rows={words.avoid} cols={[['word', 'Word'], ['why', 'Why']]} onChange={(r) => set('words', { ...words, avoid: r as never })} />
          <h2>Naming</h2>
          <Rows name="Naming" rows={body.naming} cols={[['name', 'Name'], ['rule', 'Rule']]} onChange={(r) => set('naming', r as BrandBody['naming'])} />
          <h2>Colors</h2>
          <Rows name="Colors" rows={visual.colors} cols={[['name', 'Name'], ['hex', 'Hex']]} onChange={(r) => set('visual', { ...visual, colors: r as never })} />
          <h2>Fonts</h2>
          <Field label="Fonts, one a line" labelHidden area value={(visual.fonts ?? []).join('\n')} onValueChange={(v) => set('visual', { ...visual, fonts: v.split('\n') })} />
          <h2>Imagery</h2>
          <Field label="Imagery" labelHidden area value={visual.imagery ?? ''} onValueChange={(v) => set('visual', { ...visual, imagery: v })} />
          <h2>Claims we can make</h2>
          <Rows name="Claims we can make" rows={claims.allowed} cols={[['claim', 'Claim'], ['proof', 'Proof']]} onChange={(r) => set('claims', { ...claims, allowed: r as never })} />
          <h2>Claims we never make</h2>
          <Field label="Claims we never make, one a line" labelHidden area value={(claims.never ?? []).join('\n')} onValueChange={(v) => set('claims', { ...claims, never: v.split('\n') })} />
        </div>
      }
      foot={
        <>
          <Button variant="quiet" onClick={() => void close.run({ on: false })}>
            Cancel
          </Button>
          {dirty.length > 0 ? (
            <Button
              variant="primary"
              busy={save.pending}
              disabled={save.pending}
              onClick={() => void save.run({ sections: Object.fromEntries(dirty.map((k) => [k, tidy(k, body[k])])), revision: brand.draft?.revision })}
            >
              Save
            </Button>
          ) : (
            ahead && (
              <>
                <Field label="What changed" labelHidden placeholder="What changed" value={note} onValueChange={setNote} />
                <Button variant="primary" busy={publish.pending} disabled={publish.pending} onClick={() => void publish.run({ note: note.trim() })}>
                  Publish
                </Button>
              </>
            )
          )}
        </>
      }
    />
  );
}

/** Rows of fields, a column each, the labels over the first; Add puts an empty row last. */
function Rows({ name, rows, cols, onChange }: { name: string; rows: readonly object[] | null | undefined; cols: Cols; onChange: (rows: Row[]) => void }) {
  const list = (rows ?? []) as Row[];
  return (
    <div className="be-rows" role="group" aria-label={name}>
      {list.map((r, i) => (
        <div key={i} className={`be-row c${cols.length}`}>
          {cols.map(([k, label]) => (
            <Field key={k} label={label} labelHidden={i > 0} value={typeof r[k] === 'string' ? r[k] : ''} onValueChange={(v) => onChange(list.map((x, j) => (j === i ? { ...x, [k]: v } : x)))} />
          ))}
          <IconButton icon="close" label="Remove" size="sm" quiet onClick={() => onChange(list.filter((_, j) => j !== i))} />
        </div>
      ))}
      <Button variant="quiet" icon="plus" onClick={() => onChange([...list, Object.fromEntries(cols.map(([k]) => [k, '']))])}>
        Add
      </Button>
    </div>
  );
}

/** A section as the server keeps it: rows without their first field and blank lines dropped, empty text taken out. */
function tidy(k: BookKey, v: BrandBody[BookKey]): unknown {
  const filled = (rows: readonly object[] | null | undefined, key: string) => (rows ?? []).filter((r) => String((r as Row)[key] ?? '').trim());
  const lines = (xs: readonly string[] | null | undefined) => (xs ?? []).map((x) => x.trim()).filter(Boolean);
  switch (k) {
    case 'name':
    case 'positioning':
      return typeof v === 'string' && v.trim() ? v.trim() : null;
    case 'voice':
      return filled(v as BrandBody['voice'], 'trait');
    case 'tone':
      return filled(v as BrandBody['tone'], 'situation');
    case 'naming':
      return filled(v as BrandBody['naming'], 'name');
    case 'words': {
      const w = (v ?? {}) as NonNullable<BrandBody['words']>;
      return { ...w, use: filled(w.use, 'word'), avoid: filled(w.avoid, 'word') };
    }
    case 'visual': {
      const x = (v ?? {}) as NonNullable<BrandBody['visual']>;
      return { ...x, colors: filled(x.colors, 'name').map((c) => ({ ...c, hex: String((c as Row).hex ?? '').trim() })), fonts: lines(x.fonts), imagery: (x.imagery ?? '').trim() };
    }
    case 'claims': {
      const c = (v ?? {}) as NonNullable<BrandBody['claims']>;
      return { ...c, allowed: filled(c.allowed, 'claim'), never: lines(c.never) };
    }
  }
}

// ---------------------------------------------------------------- Brand: what is suggested, and checking copy

/** Beside the draft: the changes teammates suggest (brand_draft proposals), each added to the draft or declined, and
    Check copy against the published book (brand-page.ts brandEditHtml's rail). */
function BrandRail() {
  const props = useQuery(brandProposalsQuery);
  const decide = useDo('brand.decide');
  const check = useAction('brand.check');
  const [text, setText] = useState('');
  const [hits, setHits] = useState<BrandHit[] | null>(null);
  const run = async () => {
    if (!text.trim()) return;
    const r = await check.run({ text });
    if (!r.ok) return void toast(refusal(r));
    setHits((r.out as BrandHit[] | undefined) ?? []);
  };
  const list = props.data ?? [];
  return (
    <>
      {list.length > 0 && (
        <section className="rail-sec" aria-label="Suggested">
          <h3>
            Suggested<span>{list.length}</span>
          </h3>
          {list.map((p) => (
            <div key={p.id} className="br-prop" data-target={`proposal:${p.id}`}>
              <p>{p.reason || p.card?.title || Object.keys(p.payload?.sections ?? {}).join(', ') || 'Brand change'}</p>
              <p className="chg-by">
                <span>
                  <NameOf id={p.proposed_by} />
                </span>
                <time>{fmtDate(p.proposed_at)}</time>
              </p>
              <div className="an-row">
                <Button variant="quiet" disabled={decide.pending} onClick={() => void decide.run({ proposal: p.id, decision: 'reject' })}>
                  Decline
                </Button>
                <Button disabled={decide.pending} onClick={() => void decide.run({ proposal: p.id, decision: 'confirm' })}>
                  Accept
                </Button>
              </div>
            </div>
          ))}
        </section>
      )}
      <section className="rail-sec" aria-label="Check copy">
        <h3>Check copy</h3>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          <Field
            label="Copy"
            labelHidden
            area
            value={text}
            onValueChange={(v) => {
              setText(v);
              setHits(null);
            }}
          />
          <Button type="submit" busy={check.pending}>
            Check
          </Button>
        </form>
        {hits &&
          (hits.length ? (
            <ul className="br-hits">
              {hits.map((h, i) => (
                <li key={i} title={h.rule}>
                  <span>“{h.match}”</span>
                  <Pill flag={h.level === 'block'}>{h.level === 'block' ? 'Block' : 'Warn'}</Pill>
                  {h.rule && <span className="none">{h.rule}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="none">Nothing to fix</p>
          ))}
      </section>
    </>
  );
}
