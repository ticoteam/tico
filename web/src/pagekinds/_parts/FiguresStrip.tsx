import type { Figure } from '@/pagekinds/types';

export interface FiguresStripProps {
  figures: readonly Figure[];
  /** Names the strip for a screen reader. */
  label?: string;
}

/**
 * The one row of figures (aaa-audit-a, KPI strip): across the full width with a rule between them and a sparkline each
 * (pages/3-company/finance.js `widgets`). Two by two only in a narrow column (the agent's 360) and on a phone.
 */
export function FiguresStrip({ figures, label = 'Figures' }: FiguresStripProps) {
  return (
    <div className="widgets pk-figs" role="group" aria-label={label}>
      {figures.map((f) => (
        <div key={f.label} className="widget" data-target={f.target}>
          <div className="widget-main">
            <span className="widget-title">{f.label}</span>
            <b>{f.value}</b>
            {f.sub && <span className="widget-sub">{f.sub}</span>}
          </div>
          {f.spark}
        </div>
      ))}
    </div>
  );
}
