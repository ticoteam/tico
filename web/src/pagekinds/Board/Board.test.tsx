import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { showPage, stateTests } from '@/pagekinds/_parts/test-page';
import { Board, BoardColumn } from './Board';

describe('Board', () => {
  it('draws a column per stage in the stages scroller, and folded stages without cards', async () => {
    const main = await showPage(
      <Board
        title="Engineering"
        path="/tasks/engineering"
        columns={
          <BoardColumn name="Build" count={1} target="stage:build">
            <a href="/t/1" className="card" data-target="task:1">
              Ship it
            </a>
          </BoardColumn>
        }
        folded={<BoardColumn name="Done" count={4} folded onToggle={() => {}} />}
      />,
    );
    expect(within(main).getByRole('heading', { level: 1, name: 'Engineering' }).getAttribute('data-target')).toBe('page:/tasks/engineering');
    const stages = within(main).getByRole('region', { name: 'Stages' });
    const build = within(stages).getByRole('region', { name: 'Build' });
    expect(within(build).getByRole('link', { name: 'Ship it' }).getAttribute('data-target')).toBe('task:1');
    const done = within(stages).getByRole('region', { name: 'Done' });
    expect(within(done).getByRole('button', { name: /Done/, expanded: false })).toBeTruthy();
  });

  stateTests('Engineering', (s) => <Board title="Engineering" path="/tasks/engineering" columns={null} {...s} />);
});
