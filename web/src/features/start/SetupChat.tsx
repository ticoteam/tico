import { useQuery } from '@tanstack/react-query';
import { Suspense, lazy, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { reasonIn, run, useAction } from '@/actions';
import { Message, MessageBones, type ChatMessage } from '@/conversation';
import { configQuery, meQuery } from '@/data';
import { desktopApp, inOldDesktopApp } from '@/desktop';
import { desktopDownloadQuery, downloadHref, systemOf } from '@/features/notices/exports';
import { billingQuery, providerReady, subscriptionsQuery, type BillingView, type SignInKind } from '@/features/settings/routing';
import { Button } from '@/kit/Button';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Select } from '@/kit/Select';
import { invitees, setupQuery, type FilePerson, type KeyKind, type SetupRecord } from './queries';
import { keepTranscript } from './transcript';
import './start.css';

/* The first run, in Tico's sheet (deck: setup-walkthrough-v4, Part 2). Nothing runs before a plan is connected, so
   Tico's lines here are the app's own, with their cards in the chat: welcome, connect a plan (tested by its sign-in
   before anything runs), invite the people a setup file named, where to reach you, then live. A company a setup file
   made lands on Tasks with its drafts waiting in Needs you; one started here hands the rest to Tico, which sets it up in
   its own chat (its set-up-the-company skill). Each step follows what is true: a connected plan or a sent invite is
   done whether it was done here or not. */

const ConnectPlan = lazy(() => import('@/features/settings/exports').then((m) => ({ default: m.ConnectPlan })));

const TICO = 'bot:tico';
const FAILED = 'That did not go through. Try again.';
const LIVE = "You're live. Open any page and I'll show you around it the first time.";

const PLANS = [
  { kind: 'claude_token', runtime: 'claude', name: 'Claude' },
  { kind: 'codex_login', runtime: 'codex', name: 'ChatGPT' },
] as const;

const KEYS: readonly { value: KeyKind; label: string }[] = [
  { value: 'anthropic_key', label: 'Anthropic' },
  { value: 'openai_key', label: 'OpenAI' },
];

type Reach = 'desktop' | 'phone' | 'email' | 'later';

interface Line {
  id: string;
  mine?: boolean;
  text: string;
}
/** Where to reach the person, as they said it, and Tico's answer (none for Not now). */
interface Chosen {
  how: Reach;
  said: string;
  text: string;
}

export function SetupChat() {
  const record = useQuery(setupQuery);
  const me = useQuery(meQuery).data?.id;
  if (record.isError)
    return (
      <div className="chat-body">
        <div className="empty">
          <p>Setup did not load.</p>
          <div className="q-row">
            <Button onClick={() => record.refetch()}>Try again</Button>
          </div>
        </div>
      </div>
    );
  if (!record.data || !me) return <MessageBones label="Loading setup" />;
  return <Chat record={record.data} me={me} />;
}

/** "Priya", "Priya and Leo", "Priya, Leo and Sam": first names. */
export function namesOf(people: readonly FilePerson[]): string {
  const first = people.map((p) => p.name.trim().split(/\s+/)[0] || p.name);
  return first.length < 2 ? (first[0] ?? '') : `${first.slice(0, -1).join(', ')} and ${first.at(-1)}`;
}

/** What Tico says first: a company a setup file made is welcomed back with its drafts; any other meets Tico. */
export function welcomeOf(record: SetupRecord, company: string): string {
  if (!record.company_import) return "Hi, I'm Tico. I help companies win, with a team of AI agents.";
  const n = record.company_import.drafts?.waiting ?? 0;
  const saved = `Welcome back. ${company || 'Your company'} is saved.`;
  return n > 0 ? `${saved} ${n} ${n === 1 ? 'draft is' : 'drafts are'} waiting for your yes.` : saved;
}

/** The Gateway credit the company has left, in whole dollars, when it has any (Settings › Billing's Credit). */
export function creditLeft(b: BillingView | undefined): number {
  if (!b || !['active', 'low'].includes(b.state)) return 0;
  return Math.floor(b.credits.filter((c) => c.current).reduce((n, c) => n + c.left_micros, 0) / 1_000_000);
}

function Chat({ record, me }: { record: SetupRecord; me: string }) {
  const { data: config } = useQuery(configQuery);
  const subs = useQuery(subscriptionsQuery);
  const billing = useQuery({ ...billingQuery, retry: false });
  const fromFile = !!record.company_import;
  // The people offered an invite, as first shown: the offer stays in the chat once they are sent.
  const [offered] = useState(() => (fromFile ? invitees(record) : []));
  const [invited, setInvited] = useState<'yes' | 'no' | null>(null);
  const [reach, setReach] = useState<Chosen | null>(null);
  const [failed, setFailed] = useState(false);
  const finish = useAction('setup.finish');

  const ready = (subs.data?.subscriptions ?? []).some(providerReady);
  const credit = creditLeft(billing.data);
  const company = config?.company_name || record.names.company_name;

  const lines: Line[] = [
    { id: 'welcome', text: welcomeOf(record, company) },
    { id: 'plan', text: 'Agents run on your AI plan. Connect Claude or ChatGPT.' },
  ];
  if (ready) {
    lines.push({
      id: 'plan-done',
      text: [
        'Your plan runs the agents.',
        ...(credit > 0 ? [`The $${credit} credit covers rule checks.`] : []),
        'Agents ask before spending or reaching anyone new.',
      ].join('\n'),
    });
  }
  const inviting = ready && offered.length > 0;
  if (inviting) {
    lines.push({ id: 'invite', text: `Invite ${namesOf(offered)}?` });
    if (invited) lines.push({ id: 'invite-said', mine: true, text: invited === 'yes' ? 'Invite' : 'Not now' });
    if (invited === 'yes') lines.push({ id: 'invite-done', text: `${namesOf(offered)} ${offered.length === 1 ? 'has an invite' : 'have invites'}.` });
  }
  const reached = (r: Chosen): Line[] => [{ id: 'touch-said', mine: true, text: r.said }, ...(r.text ? [{ id: 'touch-done', text: r.text }] : [])];
  const touching = ready && (!inviting || !!invited);
  if (touching) lines.push({ id: 'touch', text: 'Where should I reach you when you’re needed?' });
  if (touching && reach) lines.push(...reached(reach));

  const goLive = async (said: Line[]) => {
    setFailed(false);
    // What was said here stays above Tico's chat once the sheet shows it (the finish swaps this chat for it).
    keepTranscript(asMessages([...said, ...(fromFile ? [{ id: 'live', text: LIVE }] : [])], me));
    const r = await finish.run({});
    if (!r.ok) {
      keepTranscript([]);
      setFailed(true);
    }
  };
  const choose = (r: Chosen) => {
    setReach(r);
    void goLive([...lines, ...reached(r)]);
  };
  // Each card stands under its line until its step is done.
  const cardOf = (id: string): ReactNode =>
    id === 'plan' && !ready ? <PlanCard /> : id === 'invite' && !invited ? <InviteCard people={offered} onDone={setInvited} /> : id === 'touch' && !reach ? <TouchChips onChoose={choose} /> : null;

  return (
    <div className="chat-body setup-chat" role="log" aria-label="Setup with Tico">
      {lines.map((l, i) => (
        <Message
          key={l.id}
          m={{ id: `tmp:setup:${l.id}`, sender_id: l.mine ? me : TICO, body: l.text }}
          variant="tico"
          me={me}
          cont={i > 0 && !l.mine && !lines[i - 1]!.mine}
          below={() => cardOf(l.id)}
        />
      ))}
      {reach && finish.pending && (
        <p className="setup-wait" role="status">
          Going live
        </p>
      )}
      {failed && (
        <div className="setup-card kcard" role="alert">
          <span>{FAILED}</span>
          <div className="setup-acts">
            <Button variant="primary" onClick={() => void goLive(lines)}>
              Try again
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** The chat's lines as messages, a second apart and ending now, so they sort before whatever Tico says next. */
function asMessages(lines: Line[], me: string): ChatMessage[] {
  const end = Date.now();
  return lines.map((l, i) => ({
    id: `tmp:setup:${l.id}`,
    sender_id: l.mine ? me : TICO,
    body: l.text,
    created_at: new Date(end - (lines.length - i) * 1000).toISOString(),
  }));
}

/* ---------------------------------------------------------------- the cards */

/** Claude or ChatGPT, signed in on a computer (ConnectPlan: retry, or paste its token, when that fails); another
    provider at any time; or an API key. Done once the server says a plan is ready. */
function PlanCard() {
  const { data } = useQuery(subscriptionsQuery);
  const [kind, setKind] = useState<SignInKind | null>(null);
  const [pasting, setPasting] = useState(false);
  // A plan that is there but not ready says why, so the person knows to reconnect or switch.
  const broken = (data?.subscriptions ?? []).find((s) => !providerReady(s));
  return (
    <div className="setup-card kcard">
      {PLANS.map((plan) => (
        <div className="setup-row" key={plan.kind}>
          <b>{plan.name}</b>
          <Button
            variant={kind === plan.kind ? 'quiet' : undefined}
            onClick={() => {
              setPasting(false);
              setKind(plan.kind);
            }}
          >
            Connect
          </Button>
        </div>
      ))}
      {broken && !kind && (
        <span className="f-err" role="status">
          {`${broken.name}: ${broken.problem || 'reconnect it'}`}
        </span>
      )}
      {kind && (
        <Suspense fallback={null}>
          <ConnectPlan key={kind} kind={kind} inline onClose={() => setKind(null)} />
        </Suspense>
      )}
      {pasting ? (
        <KeyForm onCancel={() => setPasting(false)} />
      ) : (
        <Button
          variant="quiet"
          onClick={() => {
            setKind(null);
            setPasting(true);
          }}
        >
          API key
        </Button>
      )}
    </div>
  );
}

function KeyForm({ onCancel }: { onCancel: () => void }) {
  const [kind, setKind] = useState<KeyKind>('anthropic_key');
  const [secret, setSecret] = useState('');
  const [error, setError] = useState<string | null>(null);
  const paste = useAction('setup.pasteKey');
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!secret.trim()) return;
    setError(null);
    const r = await paste.run({ kind, secret });
    // The key lives in this field only, and leaves it once it is taken.
    if (r.ok) setSecret('');
    else setError(reasonIn(r) ?? FAILED);
  };
  return (
    <form className="setup-key" onSubmit={(e) => void submit(e)}>
      <Select label="Provider" labelHidden options={KEYS} value={kind} onValueChange={setKind} />
      <Field label="Key" labelHidden placeholder="API key" type="password" value={secret} onValueChange={setSecret} autoComplete="off" />
      {error && (
        <span className="f-err" role="alert">
          {error}
        </span>
      )}
      <div className="setup-acts">
        <Button variant="quiet" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" busy={paste.pending} disabled={!secret.trim()}>
          Connect
        </Button>
      </div>
    </form>
  );
}

function InviteCard({ people, onDone }: { people: FilePerson[]; onDone: (said: 'yes' | 'no') => void }) {
  const invite = useAction('setup.invite');
  const [error, setError] = useState<string | null>(null);
  const press = async () => {
    setError(null);
    const r = await invite.run({});
    if (r.ok) onDone('yes');
    else setError(reasonIn(r) ?? FAILED);
  };
  return (
    <div className="setup-card kcard">
      {people.map((p) => (
        <div className="setup-row" key={p.id}>
          <b>{p.name}</b>
          <span className="setup-sub">{p.email}</span>
        </div>
      ))}
      {error && (
        <span className="f-err" role="alert">
          {error}
        </span>
      )}
      <div className="setup-acts">
        <Button variant="quiet" disabled={invite.pending} onClick={() => onDone('no')}>
          Not now
        </Button>
        <Button variant="primary" busy={invite.pending} onClick={() => void press()}>
          Invite
        </Button>
      </div>
    </div>
  );
}

/** Where Tico reaches the person, each doing the real thing: the desktop app's download (left out inside it), the
    app on their phone's home screen, or the daily email summary. */
function TouchChips({ onChoose }: { onChoose: (chosen: Chosen) => void }) {
  const inApp = !!desktopApp() || inOldDesktopApp();
  const system = inApp ? null : systemOf(navigator);
  const download = useQuery({ ...desktopDownloadQuery(system ?? 'mac'), enabled: !!system });
  const href = downloadHref(download.data);
  const direct = href !== downloadHref(undefined);
  // One choice: a second press, or one while the email switch is saving, would go live twice.
  const [busy, setBusy] = useState(false);
  const taken = useRef(false);
  const take = () => {
    if (taken.current) return false;
    taken.current = true;
    setBusy(true);
    return true;
  };
  const choose = (chosen: Chosen) => {
    if (take()) onChoose(chosen);
  };
  const email = async () => {
    if (!take()) return;
    const r = await run('settings.notify', { field: 'email_digest', on: true }, { via: 'click' });
    onChoose({ how: 'email', said: 'Email digest', text: r.ok ? 'Done. I’ll email you a daily summary.' : 'That did not go through. Turn it on in Settings › You.' });
  };
  return (
    <div className="qchips setup-chips">
      {!inApp && (
        <a
          className="qchip"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => (busy ? e.preventDefault() : choose({ how: 'desktop', said: 'Desktop app', text: direct ? 'Done. The desktop app is downloading.' : 'Done. Pick your computer on the download page.' }))}
          aria-disabled={busy || undefined}
        >
          <Icon name="download" />
          Desktop app
        </a>
      )}
      <button type="button" className="qchip" disabled={busy} onClick={() => choose({ how: 'phone', said: 'Phone', text: `Open ${location.host} on your phone and add it to your home screen.` })}>
        <Icon name="sms" />
        Phone
      </button>
      <button type="button" className="qchip" disabled={busy} onClick={() => void email()}>
        <Icon name="mail" />
        Email digest
      </button>
      <button type="button" className="qchip" disabled={busy} onClick={() => choose({ how: 'later', said: 'Not now', text: '' })}>
        Not now
      </button>
    </div>
  );
}
