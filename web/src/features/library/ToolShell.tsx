import type { ReactNode } from 'react';
import { Ago } from '@/kit/Ago';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { Pill } from '@/kit/Pill';
import { ItemHead } from '@/pagekinds';
import { Face, Held, KindTile, OwnerButton } from './parts';
import type { ToolsSearch } from './search';

/* What every open tool is drawn with (ToolsPage.tsx, ToolMore.tsx): its head and body, a teammate's row, the kinds. */

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
  vendor: 'Service',
};

/** A tool's address, keeping the list's filter. */
export const toolHref = (id: string, s: ToolsSearch = {}) => {
  const p = new URLSearchParams();
  if (s.kind) p.set('kind', s.kind);
  if (s.needs) p.set('needs', '1');
  if (s.who) p.set('who', s.who);
  p.set('tool', id);
  return `/library/tools?${p.toString()}`;
};

export const bad = (r: { needs_you: boolean; status: { tone: string; text: string } }) => (r.needs_you || r.status.tone === 'fail' ? r.status.text : null);

export interface ShellProps {
  name: string;
  kind: string;
  mcp?: boolean;
  lock?: boolean;
  problem?: string | null;
  fill?: ReactNode;
  state?: 'loading' | 'fault' | 'no-access' | 'ready';
  onRetry?: () => void;
  target: string;
  /** "⋯": what else may change on it. */
  more?: ReactNode;
  /** The name renames in place. */
  onRename?: (name: string) => void;
  children?: ReactNode;
}

/** The open tool's head and its scrolling body (tools.css `.tool-head`, `.tool-body`). */
export function Shell({ name, kind, mcp, lock, problem, fill, state = 'ready', onRetry, target, more, onRename, children }: ShellProps) {
  return (
    <section className="tool" aria-label={name}>
      <ItemHead
        title={name}
        target={target}
        more={more}
        onRename={onRename}
        back={{ to: '/library/tools', label: 'Tools' }}
        lead={<KindTile kind={kind} mcp={mcp} big />}
        actions={
          <>
            {lock && <Held />}
            {problem && <Pill>{problem}</Pill>}
            <OwnerButton page="tools" />
            {fill}
          </>
        }
      />
      <div className="conv-body tool-body" data-scroll>
        {state === 'loading' ? null : state === 'fault' ? (
          <Empty title={`${name} did not load`} icon="alert">
            {onRetry && <Button onClick={onRetry}>Try again</Button>}
          </Empty>
        ) : state === 'no-access' ? (
          <Empty title="This tool is not shared with you" icon="lock" />
        ) : (
          children
        )}
      </div>
    </section>
  );
}

export const stateOf = (q: { isError: boolean; error: unknown; data: unknown }) =>
  q.isError ? ((q.error as { status?: number } | null)?.status === 403 ? 'no-access' : 'fault') : q.data ? 'ready' : 'loading';

export const Code = ({ children }: { children: ReactNode }) => <code className="fact-code">{children}</code>;
export const Dim = ({ children }: { children: ReactNode }) => <span className="fact-n">{children}</span>;

const LEVEL_WORDS: Record<string, string> = { read: 'Read', read_write: 'Read and write' };
export const LEVEL = (l: string) => LEVEL_WORDS[l] ?? l;

export function Who({ actor, name, right, at }: { actor: string | null; name: string; right?: ReactNode; at?: string | null }) {
  return (
    <div className="row wrow" title={name}>
      <Face actor={actor} name={name} />
      <span className="row-title">{name}</span>
      {right}
      {at ? <Ago at={at} className="row-age" /> : <span className="row-age" />}
    </div>
  );
}
