import type { AnchorHTMLAttributes, ComponentType, Ref } from 'react';

export type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  /** An in-app address ("/tasks/engineering?view=list"), or an outside one ("https://…"). */
  href: string;
  ref?: Ref<HTMLAnchorElement>;
};

/* The kit may not import the router, so main.tsx hands it the router's link once at startup. Until then (tests, a
   part shown alone) an in-app link is a plain anchor. */
const Plain = (p: LinkProps) => <a {...p} />;
let InApp: ComponentType<LinkProps> = Plain;

export function setLinkComponent(c: ComponentType<LinkProps>): void {
  InApp = c;
}

const inApp = (href: string) => /^#\//.test(href) || (href.startsWith('/') && !href.startsWith('//'));

/** A link: in-app addresses move through the router with no page load; outside ones are plain anchors. */
export function Link(p: LinkProps) {
  const To = inApp(p.href) ? InApp : Plain;
  return <To {...p} />;
}
