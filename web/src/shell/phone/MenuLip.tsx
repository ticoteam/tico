import type { CSSProperties } from 'react';
import { Icon } from '@/kit/Icon';
import { usePageName } from '@/tico/pageName';

export interface MenuLipProps {
  /** The company's name. */
  company: string;
  /** The Flow count the menu shows on Tasks: what waits on the person. Nothing when none. */
  flow?: number;
}

/** The menu's lip at rest (phone/phone.js menuLip): the company, the page open behind, and what waits on you in Flow.
    Jade is the Flow mark's alone; the count stays in ink. */
export function MenuLip({ company, flow }: MenuLipProps) {
  const where = usePageName();
  return (
    <>
      <span className="co-mark" aria-hidden="true">
        {company.slice(0, 1).toUpperCase()}
      </span>
      <b>{company || 'Menu'}</b>
      <Icon name="chevron" />
      {where && <span className="lip-t">{where}</span>}
      {flow ? (
        <span className="lip-end">
          <span className="dot flow" style={{ '--d': '12px' } as CSSProperties} role="img" aria-label="Waits on you">
            <i />
            <b />
          </span>
          <b>{flow}</b>
        </span>
      ) : null}
    </>
  );
}
