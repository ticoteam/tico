import { useQuery } from '@tanstack/react-query';
import type { ReactElement, ReactNode } from 'react';
import type { ToolIndexRow } from '@/api/schema';
import { meQuery } from '@/data';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Facts, LabelValue } from '@/kit/LabelValue';
import { Menu, MenuItem, MenuRule } from '@/kit/Menu';
import { Pill } from '@/kit/Pill';
import { ItemHead, Section, type PageState } from '@/pagekinds';
import { SkRows, StateBody } from '@/pagekinds';
import { Face, Held, KindTile, OwnerButton } from './parts';
import { googleQuery } from './queries';
import type { ToolsSearch } from './search';

/* The one page every tool is (tools.js detail): its head, then What it is, How it connects, Who may use it, What it
   can do and Recent use, each kind filling the sections it has. And the rows those sections hold. */

export const KIND: Record<string, string> = {
  api: 'API',
  mcp: 'MCP server',
  website: 'Website',
  mailbox: 'Email',
  tico: 'Tico address',
  number: 'Phone number',
  address: 'Mailing address',
  slack: 'Slack',
  github: 'GitHub',
  meetings: 'Meetings',
  listening: 'Listening',
  webhook: 'Outgoing webhook',
  declared: 'Key',
  key: 'Key',
};

/** A tool's address, keeping the list's filter. */
export const toolHref = (id: string, s: ToolsSearch = {}) => {
  const p = new URLSearchParams();
  if (s.q) p.set('q', s.q);
  if (s.kind) p.set('kind', s.kind);
  if (s.needs) p.set('needs', '1');
  if (s.who) p.set('who', s.who);
  p.set('tool', id);
  return `/library/tools?${p.toString()}`;
};

/** An admin: the owner, or whoever the server lets change company credentials. The server still decides. */
export function useAdmin() {
  const owner = useQuery(meQuery).data?.role === 'owner';
  const google = useQuery(googleQuery).data?.can_admin;
  return owner || !!google;
}

export const bad = (r: { needs_you: boolean; status: { tone: string; text: string } }) => (r.needs_you || r.status.tone === 'fail' ? r.status.text : null);

type Read = { isError: boolean; error: unknown; data: unknown; refetch: () => unknown };
/** A read's page state: a 403 is no access (nothing to retry), any other failure a fault. */
export const stateOf = (q: Pick<Read, 'isError' | 'error' | 'data'>): PageState =>
  q.isError ? ((q.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault') : q.data !== undefined ? 'ready' : 'loading';

/** Something in a section: an array counts only when it has rows. */
const has = (n: ReactNode) => (Array.isArray(n) ? n.length > 0 : n != null && n !== false);

export interface ToolPageProps {
  name: string;
  kind: string;
  /** The Kind fact, when the kind's word is not KIND's ("API key", an MCP server). */
  kindWord?: string;
  mcp?: boolean;
  lock?: boolean;
  problem?: string | null;
  /** The head's one filled button (Connect, Replace key). */
  fill?: ReactNode;
  /** "⋯": what else may change on it. */
  more?: ReactNode;
  onRename?: (name: string) => void;
  /** `tool:<id>`; each section's is `tool-about:<id>`, `tool-connect:<id>`, `tool-who:<id>`, `tool-can:<id>`. */
  target: string;
  /** The tool's own read: grey bars while it loads, Try again when it fails, no access on a 403. */
  q?: Read;
  /** The Tools list's row: what each section says when the kind has nothing more. */
  row?: ToolIndexRow;
  /** What it is, after Kind. */
  about?: ReactNode;
  connects?: ReactNode;
  connectsActions?: ReactNode;
  who?: ReactNode;
  whoCount?: number;
  whoActions?: ReactNode;
  can?: ReactNode;
  canActions?: ReactNode;
  recent?: ReactNode;
  recentActions?: ReactNode;
  /** Only the head and `children`: Messages, a list rather than a tool. */
  bare?: boolean;
  /** Dialogs. */
  children?: ReactNode;
}

/** The open tool (tools.css `.tool`, `.tool-head`, `.tool-body`). */
export function ToolPage(p: ToolPageProps) {
  const { name, kind, row, q, target } = p;
  const at = target.slice(target.indexOf(':') + 1);
  const state = q ? stateOf(q) : 'ready';
  const rowWho = row?.who.map((w) => <WhoRow key={w.id} actor={w.kind === 'group' ? null : w.id} name={w.name} />);
  const who = p.who ?? rowWho;
  return (
    <section className="tool" aria-label={name}>
      <ItemHead
        title={name}
        target={target}
        more={p.more}
        onRename={p.onRename}
        back={{ to: '/library/tools', label: 'Tools', search: (prev) => ({ ...prev, tool: undefined }) }}
        lead={<KindTile kind={kind} mcp={p.mcp} big />}
        actions={
          <>
            {p.lock && <Held />}
            {p.problem && <Pill>{p.problem}</Pill>}
            <OwnerButton page="tools" />
            {state === 'ready' && p.fill}
          </>
        }
      />
      <div className="conv-body tool-body" data-scroll>
        <StateBody state={state} title={name} onRetry={q && (() => void q.refetch())} skeleton={<SkRows rows={8} />}>
          {!p.bare && (
            <>
              <Section name="What it is" target={`tool-about:${at}`}>
                <Facts>
                  <LabelValue label="Kind">{p.kindWord ?? KIND[kind] ?? kind}</LabelValue>
                  {p.about ?? (row?.detail && <LabelValue label="About">{row.detail}</LabelValue>)}
                </Facts>
              </Section>
              <Section name="How it connects" target={`tool-connect:${at}`} actions={p.connectsActions}>
                {p.connects ?? (
                  <Facts>
                    {row?.way && <LabelValue label="Through">{row.way}</LabelValue>}
                    <LabelValue label="State">{row?.status.text ?? 'Not yet'}</LabelValue>
                  </Facts>
                )}
              </Section>
              {(has(who) || has(p.whoActions)) && (
                <Section name="Who may use it" count={p.whoCount ?? (p.who ? undefined : row?.who_total)} target={`tool-who:${at}`} actions={p.whoActions}>
                  {who}
                </Section>
              )}
              {has(p.can) && (
                <Section name="What it can do" target={`tool-can:${at}`} actions={p.canActions}>
                  {p.can}
                </Section>
              )}
              {has(p.recent) && (
                <Section name="Recent use" target={`tool-recent:${at}`} actions={p.recentActions}>
                  {p.recent}
                </Section>
              )}
            </>
          )}
          {p.children}
        </StateBody>
      </div>
    </section>
  );
}

/** A read inside a section that failed: it says so with Try again (a 403: not shared with you, nothing to retry). */
export function ReadFault({ name, error, onRetry }: { name: string; error: unknown; onRetry: () => void }) {
  if ((error as { status?: number } | null)?.status === 403) return <Empty title="Not shared with you" icon="lock" />;
  return (
    <Empty title={`${name} did not load`} icon="alert">
      <Button onClick={onRetry}>Try again</Button>
    </Empty>
  );
}

export const Code = ({ children }: { children: ReactNode }) => <code className="fact-code">{children}</code>;
export const Dim = ({ children }: { children: ReactNode }) => <span className="fact-n">{children}</span>;

const LEVEL_WORDS: Record<string, string> = { read: 'Read', read_write: 'Read and write' };
export const LEVEL = (l: string) => LEVEL_WORDS[l] ?? l;

/**
 * One teammate in Who may use it: a face, the name, then (`level`) the level that changes from its menu, or (`onRemove`)
 * an ×, then when they last used it.
 */
export function WhoRow({
  actor,
  name,
  at,
  level,
  onLevel,
  onRemove,
  right,
}: {
  actor: string | null;
  name: string;
  at?: string | null;
  level?: string;
  /** A level picked from the menu; 'none' takes it away. */
  onLevel?: (level: 'read' | 'read_write' | 'none') => void;
  onRemove?: () => void;
  right?: ReactNode;
}) {
  return (
    <div className="row wrow" title={name}>
      <Face actor={actor} name={name} />
      <span className="row-title">{name}</span>
      {right}
      {level &&
        (onLevel ? (
          <Menu
            trigger={
              <button type="button" className="lvl" aria-label={`${name}: ${LEVEL(level)}`}>
                {LEVEL(level)}
                <Icon name="chevdown" />
              </button>
            }
          >
            {(['read', 'read_write'] as const).map((l) => (
              <MenuItem key={l} label={LEVEL(l)} on={level === l} onSelect={() => onLevel(l)} />
            ))}
            <MenuRule />
            <MenuItem label="Remove" onSelect={() => onLevel('none')} />
          </Menu>
        ) : (
          <span className="lvl">{LEVEL(level)}</span>
        ))}
      {onRemove && <IconButton icon="close" label={`Remove ${name}`} size="sm" onClick={onRemove} />}
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </div>
  );
}

/** Who may use it's "+": someone not listed yet. Nothing when everyone is. */
export function AddSomeone({ label = 'Add someone', choices, onPick }: { label?: string; choices: { id: string; name: string }[]; onPick: (id: string) => void }) {
  if (!choices.length) return null;
  return (
    <Menu trigger={<IconButton icon="plus" label={label} size="sm" />}>
      {choices.map((c) => (
        <MenuItem key={c.id} label={c.name} onSelect={() => onPick(c.id)} />
      ))}
    </Menu>
  );
}

const FLAG: Record<string, string> = { refused: 'Refused', failed: 'Failed', denied: 'Refused', error: 'Failed', blocked: 'Refused' };

/** One line of Recent use: what happened, where or to whom, a pill when it went wrong, who, and when. */
export function UseRow({
  title,
  ctx,
  result,
  actor,
  at,
  code,
  lead,
  onOpen,
  children,
}: {
  title: string;
  ctx?: string;
  /** refused, failed...: drawn as a pill; anything else is not. */
  result?: string;
  actor?: string | null;
  at?: string | Date | null;
  code?: boolean;
  lead?: ReactElement;
  /** The row opens something (a message in the reader). */
  onOpen?: () => void;
  /** Its own controls (Retry, ⋯), before the face. */
  children?: ReactNode;
}) {
  const body = (
    <>
      {lead && <span className="urow-lead">{lead}</span>}
      <span className={code ? 'row-title is-code' : 'row-title'}>{title}</span>
      {ctx && <span className="row-ctx">{ctx}</span>}
      {result && FLAG[result] && <Pill>{FLAG[result]}</Pill>}
      {children}
      {actor !== undefined && <Face actor={actor} />}
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </>
  );
  return onOpen ? (
    <button type="button" className="row urow" title={title} onClick={onOpen}>
      {body}
    </button>
  ) : (
    <div className="row urow" title={title}>
      {body}
    </div>
  );
}
