import { Link, type LinkProps } from '@tanstack/react-router';
import { Fragment } from 'react';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { useSheet } from '@/shell';
import type { HeadProps } from '@/pagekinds/types';

export type PageHeadProps = HeadProps;

const to = (path: string) => path as LinkProps['to'];

/**
 * The page's head, one height on every page (`--head-h`). The trail sits small above the title and the title holds only
 * the name (aaa-audit-a, Breadcrumb). Then the controls, "⋯" last, then a button for each sheet the frame tucked
 * behind the page.
 */
export function PageHead({ title, path, crumb, lead, after, actions, more }: PageHeadProps) {
  const sheet = useSheet();
  const back = crumb?.at(-1);
  return (
    <header className="page-head pk-head">
      {back && (
        <Link className="ib back pk-back" to={to(back.to)} aria-label={`Back to ${back.label}`} title={`Back to ${back.label}`}>
          <Icon name="chevron" className="flip" />
        </Link>
      )}
      {lead}
      <div className="pk-title">
        {crumb && crumb.length > 0 && (
          <nav className="tv-where pk-crumb" aria-label="Breadcrumb">
            {crumb.map((c, i) => (
              <Fragment key={c.to}>
                {i > 0 && <Icon name="chevron" />}
                <Link to={to(c.to)}>{c.label}</Link>
              </Fragment>
            ))}
          </nav>
        )}
        <h1 data-target={`page:${path}`} title={title}>
          {title}
        </h1>
      </div>
      {after}
      <span className="grow" />
      {actions && <div className="pk-acts">{actions}</div>}
      {typeof more === 'function' ? <IconButton icon="more" label="More" onClick={more} /> : more}
      {sheet.tucked.map((t) => (
        <IconButton key={t.id} icon="panel" label={t.label} quiet onClick={t.open} />
      ))}
    </header>
  );
}
