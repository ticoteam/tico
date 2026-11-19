import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Link, setLinkComponent, type LinkProps } from './Link';

const Routed = ({ href, ...rest }: LinkProps) => <a {...rest} href={`/next${href}`} data-routed="" />;

afterEach(() => setLinkComponent((p: LinkProps) => <a {...p} />));

describe('Link', () => {
  it('sends in-app addresses through the injected link and leaves outside ones plain', () => {
    setLinkComponent(Routed);
    render(
      <>
        <Link href="/tasks/engineering">Tasks</Link>
        <Link href="https://example.com/x">Outside</Link>
      </>,
    );
    const inApp = screen.getByRole('link', { name: 'Tasks' });
    expect(inApp.getAttribute('href')).toBe('/next/tasks/engineering');
    expect(inApp.hasAttribute('data-routed')).toBe(true);
    expect(screen.getByRole('link', { name: 'Outside' }).hasAttribute('data-routed')).toBe(false);
  });
});
