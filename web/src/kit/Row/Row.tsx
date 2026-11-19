import type { AnchorHTMLAttributes, ReactNode } from 'react';
import { Ago } from '@/kit/Ago';
import { Count } from '@/kit/Count';
import { Icon, type IconName } from '@/kit/Icon';
import { LatePill } from '@/kit/Pill';

type Base = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'title' | 'children' | 'className'> & {
  /** The row's words: shown once, ellipsed, and in full on hover. Rows are title-only (R10). */
  title: string;
  selected?: boolean;
  /** `<kind>:<id>` so Tico's flights can find the row (`@/motion`). */
  target?: string;
};

export type RowProps = Base & {
  /** The grid: `task`, `tl-row`, `who`, or a page's own (`auto`, `up-row`). */
  className: string;
  unread?: boolean;
  /** The cells in the grid's order. */
  children: ReactNode;
};

/** The one row: 34 high, title-only, a link when it has an address (parts.css `.row`). */
export function Row({ className, selected, unread, target, title, href, children, ...rest }: RowProps) {
  const cls = ['row', className, selected && 'is-selected', unread && 'is-unread'].filter(Boolean).join(' ');
  if (href === undefined) {
    return (
      <div className={cls} title={title} data-target={target}>
        {children}
      </div>
    );
  }
  return (
    <a {...rest} href={href} className={cls} title={title} data-target={target} aria-current={selected ? 'true' : undefined}>
      {children}
    </a>
  );
}

const Title = ({ children }: { children: string }) => <span className="row-title">{children}</span>;
const Gap = () => <span className="av-gap" />;

export type TaskRowProps = Base & {
  /** The Flow mark or the stuck mark, hanging in the margin. */
  mark?: ReactNode;
  /** Late: the amber pill, first. */
  late?: boolean;
  /** Any other grey pills ("Blocks Launch"). */
  pills?: ReactNode;
  /** Who has it: a 22px `<Avatar>`. */
  avatar?: ReactNode;
  at: Date | string;
};

/** A task: mark, title, pills, who, age (parts-kit.js taskRow). */
export function TaskRow({ mark, late, pills, avatar, at, title, ...rest }: TaskRowProps) {
  return (
    <Row className="task" title={title} {...rest}>
      <span className="row-mark">{mark}</span>
      <Title>{title}</Title>
      <span className="row-pills">
        {late && <LatePill />}
        {pills}
      </span>
      {avatar ?? <Gap />}
      <Ago at={at} className="row-age" />
    </Row>
  );
}

export type ThreadRowProps = Base & {
  /** Who it is with: a 22px `<Avatar>`; or `kind` for a channel, mail or call. */
  avatar?: ReactNode;
  kind?: IconName;
  unread?: boolean;
  at: Date | string;
  /** The Flow mark when it waits on you. */
  flow?: ReactNode;
  /** New messages, when it does not wait on you. */
  count?: number;
};

/** A thread in the list: who, title, age, then the Flow mark or a count (parts-kit.js threadRow). */
export function ThreadRow({ avatar, kind, at, flow, count, title, ...rest }: ThreadRowProps) {
  return (
    <Row className="tl-row" title={title} {...rest}>
      {avatar ?? (
        <span className="tl-kind">
          <Icon name={kind ?? 'threads'} />
        </span>
      )}
      <Title>{title}</Title>
      <Ago at={at} className="row-age" />
      <span className="tl-slot">{flow ?? (count ? <Count n={count} label={`${count} new`} /> : null)}</span>
    </Row>
  );
}

export type WhoRowProps = Base & {
  avatar: ReactNode;
  flow?: ReactNode;
  count?: number;
};

/** A teammate: face, name, the Flow mark when they wait on you, their count (parts-kit.js whoRow). */
export function WhoRow({ avatar, flow, count, title, ...rest }: WhoRowProps) {
  return (
    <Row className="who" title={title} {...rest}>
      {avatar}
      <Title>{title}</Title>
      <span className="tl-slot">{flow}</span>
      {count ? <Count n={count} /> : <span />}
    </Row>
  );
}
