/* Dev only: every state of the Dot side by side, each run on the mock's own script and times (tico/dot-states.js
   L194-363), so a frame can be compared with tico/dot.html. `?state=<id>` shows one; `?freeze=<ms>` stops every run at
   that moment; `?motion=more|less|off` sets the level. The acting states fly real flights at small stand-in pages.
   Not built here: fill, chart, filter and phone (their landings belong to the pages), and the 20 second run. */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import {
  after,
  findTarget,
  flight,
  FLIGHT,
  freeze,
  freezeParam,
  isFrozen,
  now,
  onBurst,
  seek,
  setMotionChoice,
  useMotionLevel,
  type MotionLevel,
} from '@/motion';
import { CallBars, createBars, type BarsController } from './CallBars';
import { Dot } from './Dot';
import { smooth, voice } from './shapes';
import { createDot, type DotController } from './store';
import './dot.css';
import './DotGallery.css';

const FROZEN_AT = freezeParam();

type Ctx = {
  at(ms: number, fn: () => void): void;
  flag(sel: string, cls: string, from: number, to?: number): void;
  q<T extends Element = HTMLElement>(sel: string): T;
  dot: DotController;
  dots: Record<string, DotController>;
  bars: Record<string, BarsController>;
  keep(off: () => void): void;
};

type Tile = { key: string; size: number; cap?: string };

type Scene = {
  id: string;
  name: string;
  len: number;
  gap?: number;
  tiles?: Tile[];
  bars?: string[];
  body?: (c: { dots: Record<string, DotController>; bars: Record<string, BarsController> }) => ReactNode;
  script(c: Ctx): void;
};

const head = (c: Ctx) => c.q('.dg-head .dw');
const one = (key: string, size = 72, cap?: string): Tile => ({ key, size, cap });

/* ---------- small stand-in pages for the acting states (the mock's Fernhill rows) ---------- */

const FIN = [
  ['rev', 'Revenue', '$48,200', '$48,460'],
  ['fees', 'Stripe fees', '$386', '$412'],
  ['pay', 'Payroll', '$21,400', '$21,650'],
  ['ads', 'Ads', '$3,150', '$3,214'],
  ['soft', 'Software', '$1,240', '$1,310'],
  ['net', 'Net', '$22,024', '$21,998'],
];
const TEAM = [
  ['bd', 'Builder', 'Haiku', 'Opus'],
  ['rv', 'Reviewer', 'Haiku'],
  ['qa', 'QA Owner', 'Haiku'],
  ['ro', 'Release Owner', 'Sonnet'],
];

/* Each state's page has its own ids (`<state>-<row>`): findTarget looks in the whole document. */
const Rows = ({ ns, rows }: { ns: string; rows: string[][] }) =>
  rows.map(([k, label, v0, v1]) => (
    <div key={k} className="dg-row" data-target={`row:${ns}-${k}`} data-target-at="left">
      <s className="wash" />
      <span>{label}</span>
      <span className="dg-val" data-target={`field:${ns}-${k}`} data-wash="value">
        <s className="wash" />
        <i className="v0">{v0}</i>
        {v1 ? <i className="v1">{v1}</i> : null}
      </span>
    </div>
  ));

type PageProps = { ns: string; id: string; title: string; sub: string; on?: boolean; children?: ReactNode };
const Page = ({ ns, id, title, sub, on = true, children }: PageProps) => (
  <div className={`dg-pg${on ? ' on' : ''}`} data-pg={id}>
    <h4 data-target={`page:${ns}-${id}`} data-target-at="left" data-wash="title">
      <s className="wash" />
      {title}
      <small>{sub}</small>
    </h4>
    {children}
  </div>
);

const Stage = ({ dot, nav, page, line }: { dot: DotController; nav?: ReactNode; page: ReactNode; line?: boolean }) => (
  <div className="dg-stage">
    <div className="dg-head">
      <Dot of={dot} size={30} />
      <b>Tico</b>
      {line ? <p className="dg-line" /> : null}
    </div>
    {nav ? <div className="dg-nav">{nav}</div> : null}
    <div className="dg-page">{page}</div>
  </div>
);

const Faces = () =>
  [
    ['fo', 'FO', 'Finance Owner'],
    ['gm', 'GM', 'Growth Manager'],
    ['qa', 'QA', 'QA Owner'],
  ].map(([k, s, name]) => (
    <div key={k} className="dg-face">
      <span className="fa" data-target={`agent:${k}`}>
        {s}
      </span>
      <span>{name}</span>
    </div>
  ));

const Teams = () =>
  [
    ['eng', 'Engineering', 3],
    ['growth', 'Growth', 3],
    ['fin', 'Finance', 1],
  ].map(([k, name, n]) => (
    <div key={k} className="dg-team" data-target={`team:${k}`}>
      <s className="wash" />
      <span>{name}</span>
      <span className="stack">
        {Array.from({ length: Number(n) }, (_, i) => (
          <span key={i} className="fa sm" />
        ))}
      </span>
      {k === 'growth' ? <span hidden data-target="agent:wr" /> : null}
    </div>
  ));

/* ---------- the states, in the mock's order and with its times ---------- */

const SCENES: Scene[] = [
  { id: 'rest', name: 'At rest', len: 8800, gap: 0, script() {} },
  {
    id: 'think',
    name: 'Thinking',
    len: 9000,
    gap: 1200,
    tiles: [one('main', 72, 'Picked: slow turn'), one('alt', 72, 'Also tried: one droplet')],
    script(c) {
      c.at(900, () => {
        c.dot.set('think');
        c.dots.alt!.set('work', 'alt');
      });
      c.at(7600, () => {
        c.dot.clear('think');
        c.dots.alt!.clear('work', 'alt');
      });
    },
  },
  {
    id: 'work',
    name: 'Working out of sight',
    len: 11000,
    gap: 1500,
    tiles: [one('w1', 52, '1 thing'), one('w3', 52, '3 things'), one('w5', 52, '5 things'), one('main', 52, 'As things finish')],
    script(c) {
      c.dots.w1!.show('rest', 1);
      c.dots.w3!.show('rest', 3);
      c.dots.w5!.show('rest', 5);
      for (const [n, at] of [[1, 900], [2, 1700], [3, 2500], [2, 6400], [1, 7900], [0, 9400]] as const) c.at(at, () => c.dot.show('rest', n));
      c.at(9450, () => c.dot.set('done'));
    },
  },
  {
    id: 'done',
    name: 'Done',
    len: 4400,
    gap: 2200,
    script(c) {
      c.dot.set('write');
      c.at(2200, () => c.dot.clear('write'));
      c.at(2300, () => c.dot.set('done'));
    },
  },
  {
    id: 'type',
    name: 'You are typing to it',
    len: 5200,
    gap: 1600,
    script(c) {
      c.at(600, () => c.dot.set('type'));
      c.at(3300, () => c.dot.clear('type'));
    },
  },
  {
    id: 'write',
    name: 'It writes back',
    len: 5400,
    gap: 1800,
    script(c) {
      c.at(500, () => c.dot.set('write'));
      c.at(4300, () => c.dot.clear('write'));
    },
  },
  {
    id: 'call',
    name: 'On a call',
    len: 9200,
    gap: 0,
    tiles: [],
    bars: ['uA1', 'uA2', 'mA2', 'uB1', 'uB2'],
    body: ({ dots, bars }) => (
      <div className="dg-calls">
        {[
          ['A1', 'A: Tico talks'],
          ['A2', 'A: you talk'],
          ['B1', 'B: Tico talks'],
          ['B2', 'B: you talk'],
        ].map(([k, cap]) => (
          <div key={k} className="dg-call" data-call={k}>
            <Dot of={dots[`d${k}`]} size={56} />
            <span className="dg-under">
              <CallBars of={bars[`u${k}`]} />
            </span>
            <span className="dg-mic">{k === 'A2' ? <CallBars of={bars.mA2} /> : null}</span>
            <small>{cap}</small>
          </div>
        ))}
      </div>
    ),
    script(c) {
      const a = 500;
      const z = 7200;
      const you = (t: number) => voice(t + 1250);
      const tico = smooth(voice);
      c.at(a, () => {
        c.dots.dA1!.set('speak', tico);
        c.dots.dB1!.set('speak', tico);
        c.bars.uA1!.start(voice);
        c.bars.uB1!.start(voice);
        c.bars.mA2!.start(you);
        c.bars.uB2!.start(you);
      });
      c.flag('[data-call="A2"] .dg-under .lvl', 'gone', a, z);
      c.flag('[data-call="A2"] .dg-mic .lvl', 'in', a, z);
      c.flag('[data-call="B2"] .dg-under .lvl', 'dim', a, z);
      c.at(z - 300, () => {
        c.dots.dA1!.clear('speak');
        c.dots.dB1!.clear('speak');
      });
      c.at(z - 200, () => ['uA1', 'uB1', 'mA2', 'uB2'].forEach((k) => c.bars[k]!.stop()));
    },
  },
  {
    id: 'page',
    name: 'Opens a page for you',
    len: 4400,
    gap: 2200,
    script(c) {
      c.at(1000, () => {
        const to = findTarget('page', 'page-fin');
        if (!to) return;
        flight(head(c), to, {
          kind: 'open',
          onLand: () => {
            c.q('[data-pg="tasks"]').classList.remove('on');
            c.q('[data-pg="fin"]').classList.add('on');
          },
        });
      });
    },
  },
  {
    id: 'edit',
    name: 'Edits one field for you',
    len: 4600,
    gap: 2200,
    script(c) {
      c.at(1000, () => {
        const to = findTarget('field', 'edit-bd');
        if (to) flight(head(c), to, { onLand: () => c.q('[data-target="row:edit-bd"]').classList.add('ch') });
      });
    },
  },
  {
    id: 'edits',
    name: 'Several edits at once',
    len: 5400,
    gap: 2200,
    script(c) {
      const from = head(c);
      c.keep(
        onBurst((r) => {
          if (r.from !== from) return;
          const line = c.q('.dg-line');
          line.textContent = `Changed ${r.labels.join(', ')}${r.more ? ` and ${r.more} more` : ''}`;
          line.classList.add('in');
        }),
      );
      c.at(1000, () => {
        for (const k of ['fees', 'ads', 'soft', 'rev', 'pay', 'net']) {
          const to = findTarget('field', `edits-${k}`);
          const row = findTarget('row', `edits-${k}`);
          if (!to || !row) continue;
          flight(from, to, { wash: row, label: row.querySelector('span')?.textContent ?? k, onLand: () => row.classList.add('ch') });
        }
      });
    },
  },
  {
    id: 'ping',
    name: 'Pings an agent you can see',
    len: 6200,
    script(c) {
      c.at(1000, () => {
        const fo = findTarget('agent', 'fo');
        if (!fo) return;
        flight(head(c), fo, { kind: 'ask', onLand: () => after(200, () => fo.classList.add('live')) });
      });
      c.at(3400, () => {
        const fo = c.q('[data-target="agent:fo"]');
        fo.classList.remove('live');
        flight(fo, head(c), { kind: 'answer' });
      });
    },
  },
  {
    id: 'pingfar',
    name: 'Pings an agent you cannot see',
    len: 6400,
    script(c) {
      c.at(1000, () => {
        const row = findTarget('agent', 'wr');
        const stack = row?.querySelector('.stack');
        if (row && stack) flight(head(c), stack, { kind: 'ask', pop: FLIGHT.popStack, wash: row });
      });
      c.at(3500, () => flight(c.q('[data-target="team:growth"] .stack'), head(c), { kind: 'answer' }));
    },
  },
  {
    id: 'read',
    name: 'Reads a page or a file',
    len: 5400,
    script(c) {
      c.at(900, () => c.dot.set('think'));
      c.at(3600, () => {
        c.dot.clear('think');
        c.dot.set('write');
      });
      c.at(4300, () => c.dot.clear('write'));
    },
  },
  {
    id: 'far',
    name: 'The change is off screen',
    len: 4600,
    gap: 2200,
    script(c) {
      c.at(1000, () => {
        const to = findTarget('field', 'far-soft', { label: 'Software changed' });
        if (to) flight(head(c), to);
      });
    },
  },
  {
    id: 'wait',
    name: 'Waits on you',
    len: 9000,
    gap: 1500,
    script(c) {
      c.at(700, () => c.dot.set('wait'));
      c.at(7000, () => c.dot.clear('wait'));
    },
  },
  {
    id: 'fold',
    name: 'New message while Tico is folded',
    len: 7200,
    gap: 1500,
    script(c) {
      c.at(700, () => c.dot.set('fold'));
      c.at(5000, () => c.dot.clear('fold'));
    },
  },
  {
    id: 'cannot',
    name: 'Cannot do it',
    len: 5800,
    gap: 1800,
    script(c) {
      c.at(900, () => c.dot.set('think'));
      c.at(2300, () => {
        c.dot.set('cannot');
        c.dot.clear('think');
      });
    },
  },
  { id: 'off', name: 'Asleep or offline', len: 6000, gap: 0, script: (c) => void c.dot.set('off') },
];

/** The acting states' pages. */
function stageFor(ns: string, dot: DotController): ReactNode {
  switch (ns) {
    case 'page':
      return (
        <Stage
          dot={dot}
          page={
            <>
              <Page ns={ns} id="tasks" title="Tasks" sub="Engineering" />
              <Page ns={ns} id="fin" title="Finance" sub="June" on={false}>
                <Rows ns={ns} rows={FIN} />
              </Page>
            </>
          }
        />
      );
    case 'edit':
      return <Stage dot={dot} page={<Page ns={ns} id="team" title="Team" sub="Engineering"><Rows ns={ns} rows={TEAM} /></Page>} />;
    case 'edits':
      return <Stage dot={dot} line page={<Page ns={ns} id="fin" title="Finance" sub="June"><Rows ns={ns} rows={FIN} /></Page>} />;
    case 'ping':
      return <Stage dot={dot} nav={<Faces />} page={<Page ns={ns} id="fin" title="Finance" sub="June"><Rows ns={ns} rows={FIN} /></Page>} />;
    case 'pingfar':
      return <Stage dot={dot} nav={<Teams />} page={<Page ns={ns} id="tasks" title="Tasks" sub="Engineering" />} />;
    case 'far':
      return (
        <Stage
          dot={dot}
          page={
            <Page ns={ns} id="fin" title="Finance" sub="June">
              <div className="dg-scroll">
                <Rows ns={ns} rows={[...FIN.slice(0, 4), ['rent', 'Rent', '$2,800'], ['ins', 'Insurance', '$640'], ['ship', 'Shipping', '$4,120'], FIN[4]!]} />
              </div>
            </Page>
          }
        />
      );
    default:
      return null;
  }
}

const ACTING = new Set(['page', 'edit', 'edits', 'ping', 'pingfar', 'far']);

function SceneView({ s }: { s: Scene }) {
  const tiles = s.tiles ?? [one('main')];
  const [dots] = useState(() => {
    const keys = new Set(['main', ...tiles.map((t) => t.key), ...(s.id === 'call' ? ['dA1', 'dA2', 'dB1', 'dB2'] : [])]);
    return Object.fromEntries([...keys].map((k) => [k, createDot()])) as Record<string, DotController>;
  });
  const [bars] = useState(() => Object.fromEntries((s.bars ?? []).map((k) => [k, createBars()])) as Record<string, BarsController>);
  const [run, setRun] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const offs: (() => void)[] = [];
    Object.values(dots).forEach((d) => d.reset());
    const t0 = now();
    const at = (ms: number, fn: () => void) => offs.push(after(ms - (now() - t0), fn));
    const q = <T extends Element = HTMLElement>(sel: string) => box.current!.querySelector(sel) as T;
    s.script({
      at,
      q,
      flag: (sel, cls, from, to) => {
        at(from, () => q(sel)?.classList.add(cls));
        if (to !== undefined) at(to, () => q(sel)?.classList.remove(cls));
      },
      dot: dots.main!,
      dots,
      bars,
      keep: (off) => offs.push(off),
    });
    if (!isFrozen()) at(s.len + (s.gap ?? 2000), () => setRun((r) => r + 1));
    return () => {
      offs.forEach((off) => off());
      Object.values(bars).forEach((b) => b.stop());
    };
  }, [run, s, dots, bars]);

  return (
    <article className="dg-st" id={`s-${s.id}`}>
      <h3>{s.name}</h3>
      <div className="dg-box" ref={box} key={run}>
        {s.id === 'call'
          ? s.body?.({ dots, bars })
          : tiles.map((t) => (
              <div key={t.key} className="dg-zoom">
                <Dot of={dots[t.key]} size={t.size} />
                {t.cap ? <small>{t.cap}</small> : null}
              </div>
            ))}
        {ACTING.has(s.id) ? stageFor(s.id, dots.main!) : null}
      </div>
    </article>
  );
}

const LEVELS: [MotionLevel, string][] = [
  ['more', 'More'],
  ['less', 'Less'],
  ['off', 'Off'],
];

export default function DotGallery() {
  const level = useMotionLevel();
  const only = typeof location === 'undefined' ? null : new URLSearchParams(location.search).get('state');
  const scenes = only ? SCENES.filter((s) => s.id === only) : SCENES;

  // Frozen, the runs are written from 0 (the layout effect runs before the runs schedule) and the clock is then moved
  // to the asked moment once they are all scheduled (this effect runs after theirs).
  useLayoutEffect(() => {
    if (FROZEN_AT !== null) freeze(0);
  }, [level]);
  useEffect(() => {
    if (FROZEN_AT !== null) seek(FROZEN_AT);
  }, [level]);

  return (
    <main className="dg">
      <header className="dg-top">
        <h1>What the Dot does</h1>
        <div className="dg-pick" role="group" aria-label="Motion">
          {LEVELS.map(([v, label]) => (
            <button key={v} type="button" aria-pressed={level === v} onClick={() => setMotionChoice(v)}>
              {label}
            </button>
          ))}
        </div>
      </header>
      <div className="dg-grid" key={level}>
        {scenes.map((s) => (
          <SceneView key={s.id} s={s} />
        ))}
      </div>
    </main>
  );
}

export { DotGallery };
