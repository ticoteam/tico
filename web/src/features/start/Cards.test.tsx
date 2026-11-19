import { within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { showPage } from '@/pagekinds/_parts/test-page';
import { backAddress, signInHref, StartScreen } from './Cards';

const retry = () => {};

describe('Before the app opens @s1155 @s1156 @s1157 @s1194 @s1195', () => {
  it('sign in links out to the server, coming back to where the person was going', async () => {
    const main = await showPage(<StartScreen screen="signin" error={new ApiError(401, 'auth', '', false, '/auth/sign-in')} retry={retry} />);
    expect(within(main).getByRole('heading', { level: 1, name: 'Sign in to Tico' })).toBeTruthy();
    expect(within(main).getByRole('link', { name: 'Sign in' }).getAttribute('href')).toBe(`/auth/sign-in?next=${encodeURIComponent(location.pathname)}`);
    expect(within(main).queryByLabelText(/password/i)).toBeNull();
    expect(signInHref('/auth/login?x=1', '/tasks')).toBe('/auth/login?x=1&next=%2Ftasks');
  });

  it('comes back to the path and its page words only: never the fragment or a one-time value', () => {
    expect(backAddress('/library/skills', '?connect=WXYZ-2345&tab=installed')).toBe('/library/skills?tab=installed');
    expect(backAddress('/library/tools', '?tool=github&github=installed&code=c0de-xyz&state=forged')).toBe('/library/tools?tool=github');
    expect(backAddress('/company/routines', '?panel=hook:h1&next=https://evil.example/x')).toBe('/company/routines?panel=hook%3Ah1');
    expect(backAddress('/tasks', '?view=%3Cscript%3E')).toBe('/tasks');
    history.replaceState(null, '', '/settings/people?open=p2&ticket=tk-1#access_token=secret-1');
    const link = signInHref('/auth/sign-in');
    expect(decodeURIComponent(link)).toBe('/auth/sign-in?next=/settings/people?open=p2');
    expect(link).not.toContain('secret-1');
    expect(link).not.toContain('tk-1');
    history.replaceState(null, '', '/');
  });

  it('a refusal says why, with another account as the way on', async () => {
    const main = await showPage(<StartScreen screen="refused" error={new ApiError(403, 'identity', 'Ask an owner to add you.', false, null)} retry={retry} />);
    expect(within(main).getByRole('heading', { level: 1, name: "You're not on the list" })).toBeTruthy();
    expect(within(main).getByText('Ask an owner to add you.')).toBeTruthy();
    expect(within(main).getByRole('link', { name: 'Use another account' }).getAttribute('href')).toBe('/api/v2/logout');
    expect(within(main).getByText('Contact Tico support')).toBeTruthy();
  });

  it('signed out offers to sign in again', async () => {
    const main = await showPage(<StartScreen screen="signed-out" error={new ApiError(401, 'auth', '', false, null)} retry={retry} />);
    expect(within(main).getByRole('heading', { level: 1, name: "You're signed out" })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Sign in' })).toBeTruthy();
  });

  it('a roster that never answers offers Try again', async () => {
    const late = await showPage(<StartScreen screen="timed-out" error={null} retry={retry} />);
    expect(within(late).getByRole('heading', { level: 1, name: "Couldn't load Tico" })).toBeTruthy();
    expect(within(late).getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a server out of reach says what to check, with Try again', async () => {
    const down = await showPage(<StartScreen screen="down" error={new ApiError(0, 'network', '', true, null)} retry={retry} />);
    expect(within(down).getByRole('heading', { level: 1, name: "Can't reach Tico" })).toBeTruthy();
    expect(within(down).getByText('Check your internet connection.')).toBeTruthy();
    expect(within(down).getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});
