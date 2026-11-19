import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BarChart, Funnel, Sparkline } from './Chart';

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
});
