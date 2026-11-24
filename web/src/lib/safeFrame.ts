/* A page's HTML made safe to draw in a srcdoc frame: a chat's page card and the file viewer's page.
   Its own policy, first in <head>, lets it load nothing from anywhere (only pictures and fonts written into it as
   data: addresses), so an uploaded page cannot call out while it is shown. Every <meta http-equiv>, <link>, <base>
   and <script> is dropped: a refresh, a resource hint or a base could act before the policy is read, and the frame
   runs no script anyway. A link to the web or mail opens a new tab, one to a place in the page stays in it, and any
   other loses its address; SVG keeps no href or xlink:href at all.
   The result is parsed again before it is used: markup that changes shape when parsed twice (mutation XSS) and grows
   one of those tags back, or a link or SVG address the rules above did not write, is refused (null), and the file is
   offered as a download instead. */

export const FRAME_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'";

const DROPPED = 'meta[http-equiv], link, base, script';
const OPENS = /^(https?:|mailto:)/i;
const SVG = 'http://www.w3.org/2000/svg';
const XLINK = 'http://www.w3.org/1999/xlink';

/** A link as the rules leave it: to a place in the page with no target, or out to a new tab. */
const linkKept = (a: Element) => {
  const href = (a.getAttribute('href') ?? '').trim();
  if (href.startsWith('about:srcdoc#')) return !a.hasAttribute('target');
  return OPENS.test(href) && a.getAttribute('target') === '_blank' && a.getAttribute('rel') === 'noopener noreferrer';
};
const svgAddress = (el: Element) =>
  [...el.attributes].some((at) => at.name === 'xlink:href' || (at.localName === 'href' && at.namespaceURI === XLINK)) ||
  (el.namespaceURI === SVG && el.hasAttribute('href'));

export function safeFrame(html: string): string | null {
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html'); // an inert document: nothing in it loads or runs
  for (const el of doc.querySelectorAll(DROPPED)) el.remove();
  for (const a of doc.querySelectorAll('a[href], area[href]')) {
    const href = (a.getAttribute('href') ?? '').trim();
    if (href.startsWith('#')) {
      // a srcdoc page's own address is about:srcdoc: a bare #fragment would resolve against the app's page instead
      a.setAttribute('href', `about:srcdoc${href}`);
      a.removeAttribute('target');
      a.removeAttribute('rel');
    } else if (OPENS.test(href)) {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
    } else a.removeAttribute('href');
  }
  // SVG reaches out by href and xlink:href (<use>, <image>, its own <a>): none of them is kept
  for (const el of doc.querySelectorAll('*')) {
    for (const at of [...el.attributes]) if (at.name === 'xlink:href' || (at.localName === 'href' && at.namespaceURI === XLINK)) el.removeAttributeNode(at);
    if (el.namespaceURI === SVG) el.removeAttribute('href');
  }
  const policy = doc.createElement('meta');
  policy.setAttribute('http-equiv', 'Content-Security-Policy');
  policy.setAttribute('content', FRAME_POLICY);
  doc.head.prepend(policy);
  const out = `<!doctype html>${doc.documentElement.outerHTML}`;
  const again = parser.parseFromString(out, 'text/html');
  const kept = again.querySelectorAll(DROPPED);
  const policyOnly = kept.length === 1 && kept[0] === again.head.firstElementChild && kept[0]?.getAttribute('content') === FRAME_POLICY;
  const linksKept = [...again.querySelectorAll('a[href], area[href]')].every(linkKept);
  return policyOnly && linksKept && ![...again.querySelectorAll('*')].some(svgAddress) ? out : null;
}
