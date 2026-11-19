import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SidePanel } from '@/pagekinds/_parts';
import { showPage, stateTests } from '@/pagekinds/_parts/test-page';
import { Calendar } from './Calendar';

describe('Calendar', () => {
  it('draws the range after the title, the grid, the agenda and the open event', async () => {
    const main = await showPage(
      <Calendar
        title="Calendar"
        path="/calendar"
        after={<span>Nov 17 – 23</span>}
        actions={<button type="button">New event</button>}
        grid={<div role="grid" aria-label="Week" />}
        agenda={<ol aria-label="Agenda" />}
        panel={
          <SidePanel title="Planning" target="event:1" onClose={() => {}}>
            At ten
          </SidePanel>
        }
      />,
    );
    expect(within(main).getByRole('heading', { level: 1, name: 'Calendar' }).getAttribute('data-target')).toBe('page:/calendar');
    expect(within(main).getByText('Nov 17 – 23')).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'New event' })).toBeTruthy();
    expect(within(main).getByRole('grid', { name: 'Week' })).toBeTruthy();
    expect(within(main).getByRole('list', { name: 'Agenda' })).toBeTruthy();
    expect(within(within(main).getByRole('complementary', { name: 'Planning' })).getByText('At ten')).toBeTruthy();
  });

  stateTests('Calendar', (s) => <Calendar title="Calendar" path="/calendar" grid={null} {...s} />);
});
