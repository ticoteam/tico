import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BarChart, Funnel, LineChart, Sparkline } from './Chart';

describe('Chart', () => {
  it('draws a funnel whose bars are a share of the first step, with figures in the tabular class', () => {
    render(
      <Funnel
        label="Funnel"
        steps={[
          { name: 'Visits', count: 10000 },
          { name: 'Carts', count: 8420 },
          { name: 'Orders', count: 322 },
          { name: 'Reorders', count: 0 },
        ]}
      />,
    );
    const items = within(screen.getByRole('list', { name: 'Funnel' })).getAllByRole('listitem');
    const widths = items.map((it) => (it.querySelector('.hbar-track i') as HTMLElement).style.getPropertyValue('--w'));
    expect(widths).toEqual(['100.0%', '84.2%', '3.2%', '0.0%']);
    const fig = within(items[1]!).getByText('8,420');
    expect(fig.className).toBe('row-fig');
    expect(within(items[2]!).getByText('4%').className).toBe('count');
    expect(within(items[3]!).getByText('0').className).toBe('row-fig');
  });

  it('draws bars in greys with the last one now and named', () => {
    render(
      <BarChart
        label="Dollars by day"
        points={[20, 40, 0, 55]}
        width={300}
        max={70}
        ticks={[0, 20, 40, 60]}
        fmt={(v) => `$${v}`}
        xLabels={[
          [0, 'Oct 20'],
          [3, 'Today'],
        ]}
      />,
    );
    const svg = screen.getByRole('img', { name: 'Dollars by day' });
    const bars = svg.querySelectorAll('rect');
    expect(Array.from(bars).map((b) => b.getAttribute('class'))).toEqual(['cbar', 'cbar', 'cbar', 'cbar is-now']);
    expect(svg.querySelector('text.lbl')?.textContent).toBe('$55');
    expect(Array.from(svg.querySelectorAll('text')).map((t) => t.textContent)).toContain('$0');
  });

  it('draws a sparkline that ends in a point', () => {
    render(<Sparkline points={[3, 5, 4, 9]} label="Revenue rising" />);
    const svg = screen.getByRole('img', { name: 'Revenue rising' });
    expect(svg.querySelector('path')?.getAttribute('d')).toBe('M3.0 22.0 L25.0 16.0 L47.0 19.0 L69.0 4.0');
    expect(svg.querySelector('circle')?.getAttribute('cx')).toBe('69.0');
  });

  it('stacks a bar in greys, darkest at the bottom, and names its total', () => {
    render(<BarChart points={[[2, 1], [5, 3, 2]]} width={200} max={12} ticks={[0, 12]} fmt={String} xLabels={[]} label="Posts by source" />);
    const svg = screen.getByRole('img', { name: 'Posts by source' });
    expect(Array.from(svg.querySelectorAll('rect')).map((r) => r.getAttribute('class'))).toEqual(['c1', 'c2', 'c1', 'c2', 'c3']);
    expect(svg.querySelector('text.lbl')?.textContent).toBe('10');
  });

  it('draws a line with its target dashed and both named', () => {
    render(<LineChart points={[10, 14, 20]} target={40} fmt={(v) => `$${v}`} from="Sep 28" to="Nov 16" label="Cost against target" />);
    const svg = screen.getByRole('img', { name: 'Cost against target' });
    expect(svg.querySelector('line.lim')).toBeTruthy();
    expect(svg.querySelector('text.now')?.textContent).toBe('$20');
    expect(Array.from(svg.querySelectorAll('text')).map((t) => t.textContent)).toEqual(['$40', '$20', 'Sep 28', 'Nov 16']);
  });
});
