import { useId, type ReactNode } from 'react';
import { FoldButton } from '@/kit/SectionHead';
import { Skeleton } from '@/kit/Skeleton';
import { Page } from '@/pagekinds/_parts';
import type { PageBaseProps } from '@/pagekinds/types';

export interface BoardProps extends PageBaseProps {
  /** The filter row (`<FilterRow>`), while a filter is set. */
  filter?: ReactNode;
  /** One `<BoardColumn>` per stage, in order. */
  columns: ReactNode;
  /** Empty or folded stages, as strips at the right (`<BoardColumn folded>`). */
  folded?: ReactNode;
}

/** The same rows as cards in columns, one per stage, scrolling sideways. Only a pipeline has a board. */
export function Board({ filter, columns, folded, ...page }: BoardProps) {
  return (
    <Page kind="board" under={filter} skeleton={<BoardSkeleton />} {...page}>
      <div className="page-body board-wrap pk-body-board">
        <div className="board" data-scroll role="region" aria-label="Stages">
          <div className="board-in">
            {columns}
            {folded}
          </div>
        </div>
      </div>
    </Page>
  );
}

export interface BoardColumnProps {
  /** The stage's name. */
  name: string;
  count?: number;
  /** How many of its tasks wait on you: the Flow mark and the number at the head's right. */
  waits?: ReactNode;
  /** A strip the height of the board, its name written down it. */
  folded?: boolean;
  /** Makes the name the fold control. */
  onToggle?: () => void;
  target?: string;
  /** "12 more", under the cards a column shows. */
  more?: ReactNode;
  /** The cards. */
  children?: ReactNode;
}

/** One stage of a board: its name (the fold control), count and waits, then its cards. */
export function BoardColumn({ name, count, waits, folded = false, onToggle, target, more, children }: BoardColumnProps) {
  const id = useId();
  return (
    <section className={folded ? 'col is-fold' : 'col'} aria-label={name} data-target={target}>
      <header className="col-head">
        {onToggle ? (
          <FoldButton name={name} count={count} open={!folded} onToggle={onToggle} controls={id} />
        ) : (
          <h2 className="sec-name">
            {name}
            {count !== undefined && <span className="stage-n">{count}</span>}
          </h2>
        )}
        {waits}
      </header>
      {!folded && (
        <div id={id} className="col-cards">
          {children}
          {more}
        </div>
      )}
    </section>
  );
}

const SK = [
  [35, [[86, 54], [72], [90, 40]]],
  [24, [[78, 62], [88], [64, 48], [92, 30]]],
  [32, [[82], [70, 56]]],
] as const;

/** Three even columns of card-shaped bars (list.js, board loading). */
export function BoardSkeleton() {
  return (
    <div className="page-body board-wrap pk-body-board">
      <div className="board is-loading">
        <div className="board-in">
          {SK.map(([head, cards], i) => (
            <section key={i} className="col" aria-hidden="true">
              <header className="col-head">
                <Skeleton className="sk-head" width={head} />
              </header>
              <div className="col-cards">
                {cards.map((lines, j) => (
                  <div key={j} className="card is-skeleton">
                    {lines.map((w, k) => (
                      <Skeleton key={k} width={w} />
                    ))}
                    <div className="card-foot">
                      <Skeleton className="sk-av" />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
