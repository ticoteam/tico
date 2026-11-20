import { useQuery, useSuspenseQuery, type UseQueryResult } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { FoldButton } from '@/kit/SectionHead';
import { SkeletonRows } from '@/kit/Skeleton';
import { fmtDate } from '@/lib';
import { Document, type PageTab } from '@/pagekinds';
import { GoalsPage, type GoalsSearch } from './GoalsPage';
import { MarketTab, spendOf } from './MarketPage';
import { OrgTab } from './OrgPage';
import { Face, Go, NameOf, PageOwner, readState, usdShort, useDo } from './parts';
import { brandProposalsQuery, brandQuery, brandVersionQuery, marketSpendQuery, strategyQuery, strategyVersionQuery, strategyVersionsQuery, type Brand, type BrandBody, type BrandProposal, type VersionRow } from './queries';
import { STRATEGY_TABS, type StrategyTab } from './routing';
import './company.css';

/* Strategy and its tabs, in the mock's order (pages/3-company/company.js strategyPage). Each tab is its own address
   under /company/strategy, so Back and a shared link restore it. Budget is the agents' spend limits (company.js
   budgetBody, old #/strategy/budget): its tab opens Usage's Limits, where a limit is changed. Strategy and Brand keep
   versions: the version in the head opens a past one (`?v=`). */

export { STRATEGY_TABS, type StrategyTab };
export type StrategySearch = GoalsSearch & { v?: number; edit?: boolean };
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

/** The version, and the menu of all of them; the newest opens without `?v=`. Inline, it is the "v3" after the author's
    name (company.js strategyPage); otherwise a quiet button in the head. */
function VersionPick({ rows, shown, action, inline = false }: { rows: readonly VersionRow[]; shown: number; action: string; inline?: boolean }) {
  const open = useDo(action);
  if (rows.length < 2) return null;
  const latest = rows[0]?.n;
  return (
    <Menu
      trigger={
        <button type="button" className={inline ? 'ver-pick is-inline' : 'btn btn-quiet ver-pick'} title="Versions" data-target={`versions:${action}`}>
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

/** Text in paragraphs, as written: one per blank line, a heading's marks dropped. */
const paragraphs = (text: string) =>
  text
    .split(/\n\s*\n/)
    .map((p) => p.trim().replace(/^#+\s*/, ''))
    .filter(Boolean);

function StrategyTabBody({ search }: { search: StrategySearch }) {
  const s = useSuspenseQuery(strategyQuery).data;
  const versions = useQuery(strategyVersionsQuery);
  const latest = s.current?.n;
  const asked = search.v && search.v !== latest ? search.v : undefined;
  const past = useQuery({ ...strategyVersionQuery(asked ?? 0), enabled: !!asked });
  const shown = asked ?? latest;
  const v = asked ? past.data : s.current;
  const bets = (asked ? (past.data?.bets ?? []) : s.bets).filter((b) => b.status !== 'retired');
  const extra = Object.entries(v?.sections.extra ?? {});
  return (
    <Document
      {...head('strategy')}
      actions={<PageOwner page="strategy" />}
      // a past version is its own read, with its own states
      state={asked && !past.isSuccess ? readState(past) : !v && !s.bets.length ? 'empty' : 'ready'}
      onRetry={() => void past.refetch()}
      empty={{ title: 'No strategy yet' }}
      body={
        <>
          {v && (
            <div className="read-by">
              <Face id={v.published_by} />
              <b>
                <NameOf id={v.published_by} />
              </b>
              {(versions.data?.length ?? 0) > 1 ? <VersionPick rows={versions.data ?? []} shown={shown ?? v.n} action="strategy.openVersion" inline /> : <span>v{v.n}</span>}
              <span className="grow" />
              {v.published_at && <span className="row-age">{fmtDate(v.published_at)}</span>}
            </div>
          )}
          {(v?.summary || v?.vision) && <p>{v.summary || v.vision}</p>}
          <WherePlay />
          {bets.length > 0 && (
            <Part name="Bets">
              <ol className="bets">
                {bets.map((b) => {
                  // The newest version's goals and health; a past version has none.
                  const owner = asked ? undefined : s.goals_by_bet?.[b.id]?.[0]?.owner_id;
                  const health = asked ? undefined : s.health_by_bet?.[b.id];
                  return (
                    <li key={b.id} className="bet">
                      <span>{b.statement}</span>
                      {b.status === 'proposed' ? <Pill flag>Proposed</Pill> : health === 'red' ? <Pill flag>Behind</Pill> : <span />}
                      {owner ? <Face id={owner} /> : <span />}
                    </li>
                  );
                })}
              </ol>
            </Part>
          )}
          {[...PARTS.map(([k, name]) => [k, name, v?.sections[k]] as const), ...extra.map(([k, x]) => [k, x.title, x.body] as const)].map(([k, name, body]) =>
            typeof body === 'string' && body.trim() ? (
              <Part key={k} name={name}>
                {paragraphs(body).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </Part>
            ) : null,
          )}
        </>
      }
    />
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

// ---------------------------------------------------------------- the market's rows, as Market shows them

const MARKET = '/company/strategy/market';

/** The market's segments, each with its size as Market shows it, ours in bold; a row opens its Market panel. The mock's
    share column is left out: the server has no number for it (GET /api/v2/market/spend sizes segments, it splits nothing). */
function WherePlay() {
  const q = useQuery(marketSpendQuery);
  const segs = q.data?.segments ?? [];
  // Nothing to show: no segments yet, or a market closed to this reader. The section is left out, not explained.
  if ((q.isSuccess && !segs.length) || readState(q) === 'no-access') return null;
  return (
    <Part name="Where we play">
      {q.isSuccess ? (
        segs.map((s) => {
          const usd = spendOf(s);
          return (
            <Go key={s.id} to={MARKET} search={{ panel: `segment:${s.id}` }} className={s.self ? 'play is-us' : 'play'} data-target={`segment:${s.id}`}>
              <span className="row-title">{s.name}</span>
              <span className="row-fig">{usd != null ? usdShort(usd) : <span className="none">Not sized</span>}</span>
            </Go>
          );
        })
      ) : q.isPending ? (
        <SkeletonRows rows={3} />
      ) : (
        <MarketFault q={q} />
      )}
    </Part>
  );
}

/** The Market read failed: Try again. */
function MarketFault({ q }: { q: UseQueryResult<unknown> }) {
  return (
    <p className="play-fault">
      <span className="none">Market did not load</span>
      <Button onClick={() => void q.refetch()}>Try again</Button>
    </p>
  );
}

// ---------------------------------------------------------------- Brand: the published book, in a frame

/** The sections the book shows, in its order. */
const BOOK = ['name', 'positioning', 'voice', 'tone', 'words', 'naming', 'visual', 'claims'] as const;
type BookKey = (typeof BOOK)[number];
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

function BrandTab({ search }: { search: StrategySearch }) {
  const b = useSuspenseQuery(brandQuery).data;
  if (search.edit && b.may_edit) return <BrandEdit key={b.draft?.revision ?? 'new'} brand={b} />;
  return <BrandBook b={b} search={search} />;
}

function BrandBook({ b, search }: { b: Brand; search: StrategySearch }) {
  const edit = useDo('brand.edit');
  const latest = b.published?.n;
  const asked = search.v && search.v !== latest ? search.v : undefined;
  const past = useQuery({ ...brandVersionQuery(asked ?? 0), enabled: !!asked });
  const shown = asked ?? latest;
  const book = asked ? past.data : b.published;
  const editButton = b.may_edit && !asked && (
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
          {shown && <VersionPick rows={b.versions ?? []} shown={shown} action="brand.openVersion" />}
          {book && editButton}
          <PageOwner page="strategy" />
        </>
      }
      // a past version is its own read, with its own states
      state={asked && !past.isSuccess ? readState(past) : book ? 'ready' : 'empty'}
      onRetry={() => void past.refetch()}
      empty={{ title: 'No brand book yet', action: editButton }}
      body={
        book && (
          <div className="frame is-inset brand-frame">
            <iframe title="Brand book" srcDoc={brandDoc(book.body, shown)} sandbox="" />
          </div>
        )
      }
    />
  );
}

const esc = (s: string | null | undefined) => (s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const isHex = (s: string | null | undefined): s is string => !!s && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(s);

/** How light a colour is, 0 to 1 (relative luminance). */
function light(hex: string): number {
  const h = hex.length === 4 ? hex.replace(/[0-9a-f]/gi, (c) => c + c).slice(1) : hex.slice(1);
  const ch = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (ch[0] ?? 0) + 0.7152 * (ch[1] ?? 0) + 0.0722 * (ch[2] ?? 0);
}

/** "TIDY Green (primary, calls to action)": the name, and what it is for. */
const named = (s: string) => {
  const m = /^(.*?)\s*\((.*)\)\s*$/.exec(s);
  return m ? { name: m[1] ?? s, use: m[2] ?? '' } : { name: s, use: '' };
};
/** A font as the book names it ("Geist (marketing web)"): the family, for CSS. */
const family = (f: string) => named(f).name.replace(/["\\;{}<>]/g, '').trim();
/** A sentence end, unless it has one already. */
const stop = (s: string) => (/[.!?:]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** The brand book as its own small page (company.js BRAND_DOC). It keeps its own look: its colours and faces come from
    the book; the page's ground is the book's lightest colour, its ink the darkest, its accent the first. */
export function brandDoc(b: BrandBody, n?: number): string {
  const colors = (b.visual?.colors ?? []).filter((c): c is { name?: string | null; hex: string } => isHex(c.hex));
  const byLight = [...colors].sort((x, y) => light(x.hex) - light(y.hex));
  const ground = byLight.at(-1) && light(byLight.at(-1)!.hex) > 0.8 ? byLight.at(-1)!.hex : '#FFFFFF';
  const ink = byLight[0] && light(byLight[0].hex) < 0.05 ? byLight[0].hex : '#1F2420';
  const accent = colors.find((c) => light(c.hex) > 0.03 && light(c.hex) < 0.6)?.hex ?? ink;
  const fonts = (b.visual?.fonts ?? []).map(family).filter(Boolean);
  const sans = '-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif';
  // the book's first face for headlines, its second (or the first) for text; with none named, Georgia over the system face
  const head = fonts[0] ? `"${fonts[0]}",${sans}` : 'Georgia,"Times New Roman",serif';
  const text = fonts[1] ?? fonts[0];
  const body = `${text ? `"${text}",` : ''}${sans}`;
  const sec = (name: string, inner: string) => (inner ? `<h2>${esc(name)}</h2>${inner}` : '');
  const rows = <T,>(list: readonly T[] | null | undefined, li: (x: T) => string) => (list?.length ? `<ul class="rows">${list.map((x) => `<li>${li(x)}</li>`).join('')}</ul>` : '');
  const cols = (a: [string, string[]], z: [string, string[]]) =>
    a[1].length || z[1].length
      ? `<div class="cols"><div><h3>${esc(a[0])}</h3><ul>${a[1].map((x) => `<li>${x}</li>`).join('')}</ul></div><div class="no"><h3>${esc(z[0])}</h3><ul>${z[1].map((x) => `<li>${x}</li>`).join('')}</ul></div></div>`
      : '';
  const swatch = (c: { name?: string | null; hex: string }) => {
    const { name, use } = named(c.name ?? c.hex);
    const dark = light(c.hex) < 0.4;
    return `<div style="background:${c.hex};color:${dark ? '#FFFFFF' : ink}${light(c.hex) > 0.8 ? `;box-shadow:inset 0 0 0 1px color-mix(in srgb,${ink} 14%,transparent)` : ''}"><b>${esc(name)}</b>${use ? `<span>${esc(use)}</span>` : ''}${esc(c.hex.toUpperCase())}</div>`;
  };
  const examples = b.examples ?? [];
  // the type sample: a line the book says is on voice, other than the positioning shown just above
  const sample = examples.find((e) => e.verdict === 'on' && e.text !== b.positioning)?.text ?? b.positioning ?? '';
  const words = b.words ?? {};
  const claims = b.claims ?? {};
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>
:root{--ink:${ink};--accent:${accent};--soft:color-mix(in srgb,var(--ink) 62%,${ground});--rule:color-mix(in srgb,var(--ink) 14%,${ground})}
*{box-sizing:border-box}html{background:${ground}}
body{margin:0;color:var(--ink);font:15px/1.55 ${body};-webkit-font-smoothing:antialiased}
.wrap{max-width:700px;margin:0 auto;padding:40px 40px 56px}
h1,h2,h3,p,ul{margin:0;padding:0}
.cover{display:flex;align-items:center;gap:16px;padding-bottom:28px;border-bottom:2px solid var(--accent)}
h1{font:700 40px/1 ${head};letter-spacing:-.02em;color:var(--accent)}
.cover p{margin-top:8px;font:italic 17px/1.3 ${head};color:var(--soft)}
.ver{margin-left:auto;align-self:flex-start;font-size:12px;color:var(--soft);white-space:nowrap}
h2{font:600 15px/1.2 ${body};color:var(--soft);margin:32px 0 12px}
.note{margin-top:12px;font-size:14px;color:var(--soft)}
.sw{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.sw div{border-radius:10px;min-height:92px;padding:10px 12px;display:flex;flex-direction:column;justify-content:flex-end;font-size:12.5px;line-height:1.35}
.sw b{font-size:14px}.sw span{opacity:.8}
.type{display:grid;grid-template-columns:auto 1fr;gap:6px 24px;align-items:center}
.type .aa{font:700 64px/1 ${head};color:var(--accent)}
.type .big{font:700 26px/1.2 ${head};text-wrap:balance;max-width:16em}
.type .txt{margin-top:6px;color:var(--soft)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:0 28px}
h3{margin:0 0 4px;font:700 15px/1.3 ${head};color:var(--accent)}
ul{list-style:none}
.cols li,.rows li{padding:8px 0;border-top:1px solid var(--rule)}
.no li s{color:var(--soft);text-decoration-color:color-mix(in srgb,var(--ink) 30%,${ground})}
.rows b{display:block;font-weight:600}
.rows i,.cols i{display:block;font-style:normal;font-size:14px;color:var(--soft)}
@media (max-width:520px){.wrap{padding:24px 20px 40px}h1{font-size:32px}.ver{display:none}.sw{grid-template-columns:1fr 1fr}.cols{grid-template-columns:1fr;gap:20px}.type .aa{font-size:48px}.type .big{font-size:21px}}
</style></head><body><div class="wrap">
<div class="cover"><div><h1>${esc(b.name)}</h1>${b.positioning ? `<p>${esc(b.positioning)}</p>` : ''}</div>${n ? `<span class="ver">Brand book, v${n}</span>` : ''}</div>
${sec('Color', colors.length ? `<div class="sw">${colors.map(swatch).join('')}</div>` : '')}
${sec('Type', fonts.length ? `<div class="type"><span class="aa">Aa</span><div>${sample ? `<p class="big">${esc(sample)}</p>` : ''}<p class="txt">${esc((b.visual?.fonts ?? []).join(', '))}</p></div></div>` : '')}
${sec('Voice', cols(['We say', examples.filter((e) => e.verdict === 'on').map((e) => esc(e.text))], ['We never say', examples.filter((e) => e.verdict === 'off').map((e) => `<s>${esc(e.text)}</s>`)]) + rows(b.voice, (v) => `<b>${esc(v.trait)}</b>${v.do ? esc(stop(v.do)) : ''}${v.dont ? `<i>${esc(stop(v.dont))}</i>` : ''}`))}
${sec('Tone', rows(b.tone, (t) => `<b>${esc(t.situation)}</b>${esc(t.guide)}`))}
${sec('Words', cols(['We use', (words.use ?? []).map((w) => `${esc(w.word)}${w.not ? `<i>not ${esc(w.not)}</i>` : ''}`)], ['We avoid', (words.avoid ?? []).map((w) => `<s>${esc(w.word)}</s>${w.why ? `<i>${esc(w.why)}</i>` : ''}`)]))}
${sec('Naming', rows(b.naming, (x) => `<b>${esc(x.name)}</b>${esc(x.rule)}${x.wrong?.length ? `<i>Not ${esc(x.wrong.join(', '))}</i>` : ''}`))}
${sec('Imagery', b.visual?.imagery ? `<p>${esc(b.visual.imagery)}</p>` : '')}
${sec('Claims', cols(['We can claim', (claims.allowed ?? []).map((c) => `${esc(c.claim)}${c.proof ? `<i>${esc(c.proof)}</i>` : ''}`)], ['We never claim', (claims.never ?? []).map((c) => `<s>${esc(c)}</s>`)]))}
</div></body></html>`;
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
  const suggested = useQuery(brandProposalsQuery).data ?? [];
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
      side={suggested.length > 0 ? <BrandRail list={suggested} /> : undefined}
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

// ---------------------------------------------------------------- Brand: what is suggested

/** Beside the draft: the changes teammates suggest (brand_draft proposals), each added to the draft or declined
    (brand-page.ts brandEditHtml's rail). */
function BrandRail({ list }: { list: readonly BrandProposal[] }) {
  const decide = useDo('brand.decide');
  return (
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
  );
}
