import { useRef, useState, type CSSProperties } from 'react';
import { Composer, LiveRow, Message, type ChatMessage, type LiveRun } from '@/conversation';
import { Avatar, Faces } from '@/kit/Avatar';
import { Button } from '@/kit/Button';
import { Sparkline } from '@/kit/Chart';
import { Checkbox } from '@/kit/Checkbox';
import { ConfirmCard } from '@/kit/ConfirmCard';
import { Count } from '@/kit/Count';
import { DidLine } from '@/kit/DidLine';
import { Dialog } from '@/kit/Dialog';
import { Empty } from '@/kit/Empty';
import { Fault } from '@/kit/Fault';
import { Field, Search } from '@/kit/Field';
import { FlowMark } from '@/kit/FlowMark';
import { IconButton } from '@/kit/IconButton';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { OwnerButton } from '@/kit/OwnerButton';
import { LatePill, Pill } from '@/kit/Pill';
import { TaskRow, ThreadRow, WhoRow } from '@/kit/Row';
import { SectionHead } from '@/kit/SectionHead';
import { Segmented } from '@/kit/Segmented';
import { Select } from '@/kit/Select';
import { SkeletonRows } from '@/kit/Skeleton';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { ValueButton } from '@/kit/ValueButton';
import { fmtMoney } from '@/lib';
import { flight, useMotionLevel, wash } from '@/motion';
import { FiguresStrip, FilterRow, PageMore, Section } from '@/pagekinds';
import { Dot } from '@/tico/dot';
import { Area, Face, MATES, Named, Sp, Specimens, Tok, ago } from './parts';

const noop = () => {};

/* ============================== Color ============================== */

const SWATCHES: readonly (readonly [string, readonly (readonly [string, string])[]])[] = [
  ['Sheets', [['--ground', 'Ground'], ['--sheet-low', 'The menu'], ['--sheet-mid', 'Tico, a chat'], ['--sheet', 'The page'], ['--raised', 'A card, a menu']]],
  ['On a sheet', [['--well', 'A well'], ['--hover', 'Under the pointer'], ['--selected', 'Chosen'], ['--bubble', 'Your message'], ['--line', 'A rule'], ['--line-strong', 'A firm rule']]],
  ['Ink', [['--ink', 'Titles, names'], ['--ink-2', 'Text'], ['--muted', 'Second lines'], ['--faint', 'Marks']]],
  ['The one button', [['--fill', 'Filled'], ['--on-fill', 'Its label']]],
  ['Color', [['--dot', 'The Dot, the Flow mark'], ['--warn-ink', 'Late']]],
];

/** The two colors that are not gray are shown as the only things that wear them: the Dot and Late. */
function SwatchMark({ name }: { name: string }) {
  if (name === '--dot') return <Dot size={20} still />;
  if (name === '--warn-ink') return <LatePill />;
  return <i style={{ '--sw': `var(${name})` } as CSSProperties} />;
}

function ThemeColumn({ theme }: { theme: 'dark' | 'light' }) {
  return (
    <div className="sg-theme" data-theme={theme}>
      <div className="sg-theme-name">
        <Pill>{theme === 'dark' ? 'Dark' : 'Light'}</Pill>
      </div>
      {SWATCHES.map(([group, list]) => (
        <div key={group} className="sw-group">
          <h3 className="t-label">{group}</h3>
          {list.map(([name, use]) => (
            <div key={name} className="sw">
              <span className="sw-mark">
                <SwatchMark name={name} />
              </span>
              <b className="t-code">{name}</b>
              <span className="sw-use">{use}</span>
              <code className="t-code">
                <Tok name={name} as="hex" />
              </code>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export function ColorArea() {
  return (
    <Area id="color" title="Color" rule="Grays only. Jade is the Dot and the Flow mark. Amber is Late.">
      <div className="sg-themes">
        <ThemeColumn theme="dark" />
        <ThemeColumn theme="light" />
      </div>
    </Area>
  );
}

/* ============================== Type ============================== */

const STEPS: readonly (readonly [string, string])[] = [
  ['title', 'Retry a declined card once'],
  ['ask', 'Ship the retry fix to everyone today?'],
  ['hall', 'Engineering'],
  ['figure-lg', '142'],
  ['figure', '12 of 14'],
  ['head', 'Maya Okafor'],
  ['lead', 'Nothing waits on you'],
  ['label', 'Review'],
  ['label-2', 'This week'],
  ['strong', 'Fix the flaky login test'],
  ['body', 'Moved to QA after two reviews.'],
  ['read', 'The second charge now waits 30 seconds.'],
  ['small', 'Opened by Maya Okafor'],
  ['meta', 'Tue, Nov 11, 10:15 AM'],
  ['micro', '⌘K'],
  ['code', 'git rebase origin/main'],
];

function NameOverRows() {
  const [open, setOpen] = useState(true);
  const rows: [React.ReactNode, React.ReactNode][] = [
    [
      <>
        --type-label
        <br />
        <Tok name="--t-sec" as="px" />, <Tok name="--w-title" />
      </>,
      <div className="sg-demo">
        <SectionHead name="Review" count={2} open={open} onToggle={() => setOpen(!open)} />
      </div>,
    ],
  ];
  if (open)
    rows.push(
      [
        <>
          --type-body
          <br />
          <Tok name="--t-base" as="px" />, <Tok name="--w-text" />
        </>,
        <TaskRow title="Shipping: QA report for the rate checks" mark={<FlowMark />} avatar={<Face who="qa" />} at={ago(240)} />,
      ],
      ['', <TaskRow title="Gift cards: send on a chosen date" avatar={<Face who="b2" />} at={ago(420)} />],
    );
  return <Named className="is-rule" rows={rows} />;
}

export function TypeArea() {
  return (
    <Area id="type" title="Type" rule="One family, Atkinson Hyperlegible Next. Size and weight carry the order.">
      <Section name="A section's name">
        <p className="sg-note sg-in">
          Bigger and heavier than the rows under it: <Tok name="--t-sec" as="px" /> at weight <Tok name="--w-title" /> over{' '}
          <Tok name="--t-base" as="px" />. In ink, never gray. Its count stays small.
        </p>
        <NameOverRows />
      </Section>
      <Section name="Steps">
        <div className="ty-list">
          {STEPS.map(([k, words]) => (
            <div key={k} className="ty">
              <span className={`ty-s t-${k}`}>{words}</span>
              <code className="t-code">--type-{k}</code>
              <span className="ty-v" />
            </div>
          ))}
        </div>
      </Section>
    </Area>
  );
}

/* ============================== Marks ============================== */

const PEOPLE = (['maya', 'priya', 'jonas', 'qa', 'b1', 'b2'] as const).map((k) => MATES[k]);

export function MarksArea() {
  return (
    <Area id="marks" title="Marks" rule="The Dot is Tico. The Flow mark is what waits on you. Nothing else is jade.">
      <Section name="The Dot">
        <Specimens>
          {[12, 22, 30, 48].map((n) => (
            <Sp key={n} name={String(n)}>
              <Dot size={n} state="rest" />
            </Sp>
          ))}
          <Sp name="Thinking">
            <Dot size={30} state="think" />
          </Sp>
          <Sp name="Writing">
            <Dot size={30} state="write" />
          </Sp>
          <Sp name="Working">
            <Dot size={30} state="rest" work={3} />
          </Sp>
          <Sp name="A message">
            <Dot size={30} state="rest" message />
          </Sp>
          <Sp name="Waits on you">
            <Dot size={30} state="wait" />
          </Sp>
          <Sp name="Off">
            <Dot size={30} state="off" />
          </Sp>
        </Specimens>
      </Section>
      <Section name="The Flow mark">
        <Specimens>
          <Sp name="On a row">
            <FlowMark size={12} />
          </Sp>
          <Sp name="Larger">
            <FlowMark size={22} />
          </Sp>
        </Specimens>
      </Section>
      <Section name="Avatars">
        <Specimens>
          {([16, 22, 28, 36] as const).map((n) => (
            <Sp key={n} name={String(n)}>
              <span className="sg-pair">
                <Face who="maya" size={n} />
                <Face who="b1" size={n} />
              </span>
            </Sp>
          ))}
          <Sp name="A person">
            <Face who="priya" size={28} />
          </Sp>
          <Sp name="An agent">
            <Face who="qa" size={28} />
          </Sp>
          <Sp name="Working now">
            <span className="sg-pair">
              <Face who="jonas" size={28} live />
              <Face who="rev" size={28} live />
            </span>
          </Sp>
          <Sp name="Tico">
            <Avatar kind="agent" name="Tico" size={28} dot={<Dot size={24} still />} />
          </Sp>
          <Sp name="A team">
            <Faces people={PEOPLE} size={22} />
          </Sp>
        </Specimens>
      </Section>
      <Section name="Faults">
        <Specimens>
          <Sp name="In a pill">
            <Fault size={12} label="Failed" />
          </Sp>
          <Sp name="In a row">
            <Fault size={14} label="Failed" />
          </Sp>
          <Sp name="Beside a title">
            <Fault size={16} label="Failed" />
          </Sp>
        </Specimens>
      </Section>
    </Area>
  );
}

/* ============================== Rows ============================== */

const REVIEW = (
  <>
    <TaskRow title="Shipping: QA report for the rate checks" mark={<FlowMark />} avatar={<Face who="qa" />} at={ago(240)} />
    <TaskRow title="Gift cards: send on a chosen date" avatar={<Face who="b2" />} at={ago(420)} />
  </>
);

export function RowsArea() {
  return (
    <Area id="rows" title="Rows" rule="A title, its marks, who has it, how long. One line.">
      <Section name="A section">
        <Named
          rows={[
            [
              'Open',
              <div className="sg-demo">
                <Section name="Review" count={2}>
                  {REVIEW}
                </Section>
              </div>,
            ],
            [
              'Folded',
              <div className="sg-demo">
                <Section name="Done" count={14} defaultOpen={false}>
                  {REVIEW}
                </Section>
              </div>,
            ],
            [
              'With a summary',
              <div className="sg-demo">
                <Section name="Builders" count={2} summary={`Haiku 5.5, ${fmtMoney(40)} this month`}>
                  {REVIEW}
                </Section>
              </div>,
            ],
          ]}
        />
      </Section>
      <Section name="A task">
        <Named
          rows={[
            ['Plain', <TaskRow title="Gift cards: send on a chosen date" avatar={<Face who="b2" />} at={ago(420)} />],
            ['Waits on you', <TaskRow title="Shipping: QA report for the rate checks" mark={<FlowMark />} avatar={<Face who="qa" />} at={ago(240)} />],
            ['Working', <TaskRow title="Search: rank kits in stock above back orders" avatar={<Face who="b1" live />} at={ago(38)} />],
            ['Late', <TaskRow title="Planting calendar: wrong frost dates" late avatar={<Face who="rev" />} at={ago(3 * 1440)} />],
            [
              'Chosen',
              <TaskRow title="Order email: round prices as the invoice does" selected pills={<Pill>Blocks E613</Pill>} avatar={<Face who="b2" />} at={ago(1440)} />,
            ],
            ['Loading', <SkeletonRows rows={1} label="Loading a row" />],
          ]}
        />
      </Section>
      <Section name="A thread">
        <Named
          rows={[
            ['Three new', <ThreadRow title="Apple Pay fails on Safari 18" kind="tasks" unread count={3} at={ago(2)} />],
            ['Waits on you', <ThreadRow title="Refund policy for damaged beds" kind="threads" flow={<FlowMark />} at={ago(60)} />],
            ['One teammate', <ThreadRow title="Engineering Manager" avatar={<Face who="em" />} at={ago(21)} />],
          ]}
        />
      </Section>
      <Section name="A teammate">
        <Named
          rows={[
            ['A person', <WhoRow title="Priya Nair" avatar={<Face who="priya" />} count={4} />],
            ['An agent working', <WhoRow title="Builder One" avatar={<Face who="b1" live />} count={6} />],
            ['Waits on you', <WhoRow title="QA Owner" avatar={<Face who="qa" />} flow={<FlowMark />} count={6} />],
          ]}
        />
      </Section>
    </Area>
  );
}

/* ============================== Controls ============================== */

const THEMES = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'auto', label: 'Auto' },
] as const;
const TEAMS = [
  { value: 'eng', label: 'Engineering' },
  { value: 'product', label: 'Product' },
  { value: 'growth', label: 'Growth' },
] as const;

function Choices() {
  const [theme, setTheme] = useState<'dark' | 'light' | 'auto'>('dark');
  const [on, setOn] = useState(true);
  const [off, setOff] = useState(false);
  const [check, setCheck] = useState(true);
  const [team, setTeam] = useState<'eng' | 'product' | 'growth'>('eng');
  const [q, setQ] = useState('');
  return (
    <Specimens>
      <Sp name="One of a few">
        <Segmented label="Theme" options={THEMES} value={theme} onValueChange={setTheme} />
      </Sp>
      <Sp name="On">
        <Switch label="Two-step sign-in" labelHidden checked={on} onCheckedChange={setOn} />
      </Sp>
      <Sp name="Off">
        <Switch label="Weekly summary" labelHidden checked={off} onCheckedChange={setOff} />
      </Sp>
      <Sp name="A box">
        <Checkbox label="Email me" checked={check} onCheckedChange={setCheck} />
      </Sp>
      <Sp name="A field">
        <span className="sg-w">
          <Field label="Name" labelHidden defaultValue="Fernhill" />
        </span>
      </Sp>
      <Sp name="Not right">
        <span className="sg-w">
          <Field label="Address" labelHidden defaultValue="fernhill" error="Already taken" />
        </span>
      </Sp>
      <Sp name="Off">
        <span className="sg-w">
          <Field label="Plan" labelHidden defaultValue="Team" disabled />
        </span>
      </Sp>
      <Sp name="A pick">
        <span className="sg-w">
          <Select label="Team" labelHidden options={TEAMS} value={team} onValueChange={setTeam} />
        </span>
      </Sp>
      <Sp name="Search a list">
        <Search label="Search tasks" placeholder="Search" value={q} onValueChange={setQ} />
      </Sp>
    </Specimens>
  );
}

const MODELS = ['Haiku 5.5', 'Claude Opus 5.5', 'GPT 6.2'] as const;

/** The page head's own parts, right of the title: a scope, the owner's face, then "⋯", last. */
function HeadParts() {
  const [scope, setScope] = useState<string>(TEAMS[0].label);
  const [chat, setChat] = useState(false);
  const em = MATES.em;
  return (
    <Specimens>
      <Sp name="A scope">
        <Menu align="start" trigger={<ValueButton look="scope">{scope}</ValueButton>}>
          {TEAMS.map((t) => (
            <MenuItem key={t.value} label={t.label} on={t.label === scope} onSelect={() => setScope(t.label)} />
          ))}
        </Menu>
      </Sp>
      <Sp name="The owner">
        <OwnerButton name={em.name} kind={em.kind} hue={em.hue} on={chat} onPress={() => setChat(!chat)} />
      </Sp>
      <Sp name="Their chat open">
        <OwnerButton name={em.name} kind={em.kind} hue={em.hue} on onPress={noop} />
      </Sp>
      <Sp name="⋯">
        <PageMore>
          <MenuItem icon="archive" label="Archived" onSelect={noop} />
          <MenuItem icon="edit" label="Edit pipeline" onSelect={noop} />
        </PageMore>
      </Sp>
    </Specimens>
  );
}

/** A value changed where it stands: its words and a small chevron. */
function Values() {
  const [model, setModel] = useState<string>(MODELS[0]);
  return (
    <Specimens>
      <Sp name="A value">
        <span className="sg-w">
          <Menu align="start" trigger={<ValueButton label="Model">{model}</ValueButton>}>
            {MODELS.map((m) => (
              <MenuItem key={m} label={m} on={m === model} onSelect={() => setModel(m)} />
            ))}
          </Menu>
        </span>
      </Sp>
      <Sp name="With a tail">
        <span className="sg-w">
          <ValueButton label="Tasks model" more="High" onClick={noop}>
            Claude Opus 5.5
          </ValueButton>
        </span>
      </Sp>
      <Sp name="The default">
        <span className="sg-w">
          <ValueButton label="Tasks model" muted onClick={noop}>
            Same
          </ValueButton>
        </span>
      </Sp>
      <Sp name="Off">
        <span className="sg-w">
          <ValueButton label="Computer" disabled>
            Studio Mac
          </ValueButton>
        </span>
      </Sp>
    </Specimens>
  );
}

export function ControlsArea() {
  return (
    <Area id="controls" title="Controls" rule="One filled button to a page, before the ⋯. A button is a verb of two words at most.">
      <Section name="Buttons">
        <Specimens>
          <Sp name="Filled">
            <Button variant="primary">Approve</Button>
          </Sp>
          <Sp name="Plain">
            <Button>Send back</Button>
          </Sp>
          <Sp name="Quiet">
            <Button variant="quiet">Cancel</Button>
          </Sp>
          <Sp name="With an icon">
            <Button icon="plus">New task</Button>
          </Sp>
          <Sp name="Busy">
            <Button busy>Saving</Button>
          </Sp>
          <Sp name="Off">
            <Button disabled>Approve</Button>
          </Sp>
        </Specimens>
      </Section>
      <Section name="Icon buttons">
        <Specimens>
          <Sp name="More">
            <IconButton icon="more" label="More" />
          </Sp>
          <Sp name="Quiet">
            <IconButton icon="panel" label="Fold" quiet />
          </Sp>
          <Sp name="Small">
            <IconButton icon="plus" label="Add" size="sm" />
          </Sp>
          <Sp name="Pressed">
            <IconButton icon="filter" label="Filter" on />
          </Sp>
        </Specimens>
      </Section>
      <Section name="In a page head">
        <HeadParts />
      </Section>
      <Section name="A value">
        <Values />
      </Section>
      <Section name="Choices">
        <Choices />
      </Section>
      <Section name="Pills">
        <Specimens>
          <Sp name="A pill">
            <Pill>Blocks E613</Pill>
          </Sp>
          <Sp name="Needs a look">
            <Pill flag>Offline</Pill>
          </Sp>
          <Sp name="With a mark">
            <Pill flag mark={<Fault size={12} label="Failed" />}>
              Failed
            </Pill>
          </Sp>
          <Sp name="A count">
            <Count n={12} />
          </Sp>
        </Specimens>
      </Section>
    </Area>
  );
}

/* ============================== Chat ============================== */

const ME = 'human:style-guide';
const at = (min: number) => ago(min).toISOString();
const MINE: ChatMessage = { id: 'sg-1', sender_id: ME, body: 'What should I do first?', created_at: at(3) };
const FROM_TICO: ChatMessage = {
  id: 'sg-2',
  sender_id: 'bot:tico',
  body: 'The shipping checks in Engineering. They are late, and two tasks wait behind them.',
  created_at: at(2),
};
const FROM_AGENT: ChatMessage = {
  id: 'sg-3',
  sender_id: 'bot:qa-owner',
  sender_name: 'QA Owner',
  body: 'The report is in the task. Two of the checks were done by hand.',
  created_at: at(1),
};
const NOT_SENT: ChatMessage = { id: 'sg-4', sender_id: ME, body: 'Ship it today', created_at: at(1), pending: 'failed' };
const READY = { text: 'Turn on two-step sign-in' };

function Chat({ m, variant }: { m: ChatMessage; variant: 'tico' | 'side' }) {
  return (
    <div className="chat-body sg-chat">
      <Message m={m} variant={variant} me={ME} onRetry={noop} />
    </div>
  );
}

function Writing({ variant, text }: { variant: 'tico' | 'side'; text: string }) {
  const r: LiveRun = { run: `sg-${variant}`, bot: 'bot:builder-one', parts: [], typing: null, doing: text ? 'type' : 'think', ended: false };
  return (
    <div className="chat-body sg-chat">
      <LiveRow r={r} variant={variant} text={text} />
    </div>
  );
}

export function ChatArea() {
  return (
    <Area id="chat" title="Chat" rule="Tico says each thing it did in a quiet line. Money, sending and deleting wait for a yes.">
      <Section name="Messages">
        <Named
          rows={[
            ['Yours', <Chat m={MINE} variant="tico" />],
            ['From Tico', <Chat m={FROM_TICO} variant="tico" />],
            ['From an agent', <Chat m={FROM_AGENT} variant="side" />],
            ['Not sent', <Chat m={NOT_SENT} variant="tico" />],
          ]}
        />
      </Section>
      <Section name="What Tico did">
        <Specimens>
          <Sp name="It did something">
            <div className="chat-body sg-chat is-did">
              <DidLine text="Opened Finance" />
            </div>
          </Sp>
          <Sp name="With its own mark">
            <div className="chat-body sg-chat is-did">
              <DidLine text="Moved to Friday" icon="calendar" />
            </div>
          </Sp>
          <Sp name="A teammate did it">
            <div className="chat-body sg-chat is-did">
              <DidLine text="Asked QA Owner" face={<Face who="qa" />} />
            </div>
          </Sp>
          <Sp name="It could not">
            <div className="chat-body sg-chat is-did">
              <DidLine text="Turn on Two-step" fault />
            </div>
          </Sp>
        </Specimens>
      </Section>
      <Section name="Writing">
        <Named
          rows={[
            ['Tico writing', <Writing variant="tico" text="The shipping checks in Engineering. They are" />],
            ['An agent thinking', <Writing variant="side" text="" />],
          ]}
        />
      </Section>
      <Section name="The message box">
        <Specimens>
          <Sp name="Empty">
            <div className="sg-foot">
              <Composer id="style-guide:empty" me={ME} to="Tico" onSend={noop} />
            </div>
          </Sp>
          <Sp name="Ready to send">
            <div className="sg-foot">
              <Composer id="style-guide:ready" me={ME} to="Tico" onSend={noop} fill={READY} />
            </div>
          </Sp>
        </Specimens>
      </Section>
      <Section name="Tico asks first">
        <div className="sg-in sg-card" data-sheet>
          <ConfirmCard
            mark={<Dot size={12} state="wait" />}
            question="Send these 3 emails?"
            steps={[
              { icon: 'mail', title: 'Email 3 customers', detail: 'Their orders ship on Monday' },
              { icon: 'tasks', title: 'Close the shipping task' },
            ]}
            note="Sent email cannot be taken back."
            act="Send emails"
            onAct={noop}
            onNotNow={noop}
          />
        </div>
      </Section>
    </Area>
  );
}

/* ============================== Sheets ============================== */

const SHEETS = [
  ['--sheet-low', 'The menu', '--lift-0'],
  ['--sheet-mid', 'Tico, a second chat', '--lift-1'],
  ['--sheet', 'The page', '--lift-2'],
] as const;

function Lifted() {
  const [open, setOpen] = useState(false);
  return (
    <Specimens top>
      <Sp name="A menu">
        <Menu trigger={<IconButton icon="more" label="More" />} align="start">
          <MenuItem icon="edit" label="Rename" onSelect={noop} />
          <MenuItem icon="arrow" label="Move" onSelect={noop} />
          <MenuItem icon="link" label="Copy link" onSelect={noop} />
          <MenuRule />
          <MenuItem icon="archive" label="Archive" onSelect={noop} />
        </Menu>
      </Sp>
      <Sp name="A dialog">
        <Dialog
          open={open}
          onOpenChange={setOpen}
          alert
          title="Delete this task?"
          description="Gift cards: send on a chosen date"
          trigger={<Button>Delete task</Button>}
          actions={
            <>
              <Button onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => setOpen(false)}>
                Delete task
              </Button>
            </>
          }
        />
      </Sp>
      <Sp name="A toast">
        <Button onClick={() => toast({ text: 'Moved to QA', action: { label: 'Undo', run: noop } })}>Move task</Button>
      </Sp>
    </Specimens>
  );
}

export function SheetsArea() {
  return (
    <Area id="sheets" title="Sheets" rule="Sheets lie on the ground and lighten as they rise. The page is the highest. Only a card or a menu lifts off it.">
      <div className="sg-ground">
        {SHEETS.map(([tone, name, lift]) => (
          <div key={tone} className="sg-sheet" style={{ '--sg-bg': `var(${tone})`, '--sg-lift': `var(${lift})` } as CSSProperties}>
            <b className="t-strong">{name}</b>
            <code className="t-code">{tone}</code>
            <code className="t-code">
              <Tok name={tone} as="hex" />
            </code>
            <code className="t-code">
              --r-pane <Tok name="--r-pane" as="px" />
            </code>
          </div>
        ))}
      </div>
      <Section name="Lifted off a sheet">
        <Lifted />
      </Section>
    </Area>
  );
}

/* ============================== Motion ============================== */

const LEVEL = { more: 'More', less: 'Less', off: 'Off' } as const;

export function MotionArea() {
  const level = useMotionLevel();
  const from = useRef<HTMLSpanElement>(null);
  const to = useRef<HTMLDivElement>(null);
  const row = () => to.current?.querySelector('.row') ?? null;
  const fly = () => {
    const a = from.current;
    const b = row();
    if (a && b) void flight(a, b, { kind: 'edit', label: 'Moved to QA' });
  };
  const washRow = () => {
    const b = row();
    if (b) wash(b);
  };
  return (
    <Area id="motion" title="Motion" rule="A change flies from the Dot to where it lands, then washes. Less sends one droplet. Off only washes.">
      <Section name="A change lands">
        <Specimens>
          <Sp name="From the Dot">
            <span ref={from} className="sg-from">
              <Dot size={22} state="rest" />
            </span>
          </Sp>
          <Sp name="A flight">
            <Button onClick={fly}>Fly</Button>
          </Sp>
          <Sp name="A wash">
            <Button onClick={washRow}>Wash</Button>
          </Sp>
          <Sp name="Motion now">
            <Pill>{LEVEL[level]}</Pill>
          </Sp>
        </Specimens>
        <div ref={to}>
          <TaskRow title="Shipping: QA report for the rate checks" avatar={<Face who="qa" />} at={ago(240)} target="sample:motion" />
        </div>
      </Section>
    </Area>
  );
}

/* ============================== Pages ============================== */

const SPARK = [12, 14, 13, 17, 16, 19, 21];
const FIGURES = [
  { label: 'Open tasks', value: '42', sub: 'was 38', spark: <Sparkline points={SPARK} /> },
  { label: 'Done this week', value: '17', sub: 'was 21', spark: <Sparkline points={[21, 19, 20, 18, 17, 18, 17]} /> },
  { label: 'Spend', value: fmtMoney(8640), sub: `was ${fmtMoney(7980)}`, spark: <Sparkline points={[70, 72, 75, 74, 80, 83, 86]} /> },
  { label: 'Waits on you', value: '3', sub: 'was 5', spark: <Sparkline points={[5, 6, 4, 5, 4, 3, 3]} /> },
];
const FILTERS = ['QA Owner', 'Late'];

function Filters() {
  const [on, setOn] = useState<string[]>(FILTERS);
  if (!on.length)
    return (
      <div className="sg-in">
        <Button variant="quiet" onClick={() => setOn(FILTERS)}>
          Put back
        </Button>
      </div>
    );
  return <FilterRow filters={on.map((label) => ({ label, onClear: () => setOn(on.filter((x) => x !== label)) }))} onClearAll={() => setOn([])} />;
}

export function PagesArea() {
  return (
    <Area id="pages" title="Pages" rule="Every page is one of nine kinds. These are the parts their slots share.">
      <Section name="Figures">
        <FiguresStrip figures={FIGURES} />
      </Section>
      <Section name="Filters">
        <Filters />
      </Section>
      <Section name="States">
        <div className="sg-states">
          <Sp name="Loading">
            <div className="sg-state">
              <SkeletonRows rows={3} label="Loading a list" />
            </div>
          </Sp>
          <Sp name="Empty">
            <Empty title="Nothing in Review" />
          </Sp>
          <Sp name="Did not load" sheet>
            <Empty icon="alert" title="Tasks did not load" text="Check your connection and try again.">
              <Button variant="primary" onClick={noop}>
                Try again
              </Button>
            </Empty>
          </Sp>
          <Sp name="No access">
            <Empty icon="lock" title="You don't have access to this page" text="An owner can share it with you." />
          </Sp>
        </div>
      </Section>
    </Area>
  );
}
