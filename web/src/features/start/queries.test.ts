import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { screenFor } from './queries';

const err = (status: number, signIn: string | null = null) => new ApiError(status, 'x', 'Not on the list', false, signIn);

describe('where a person lands @s1155 @s1156 @s1157 @s1195', () => {
  it('names the card for what the server answered', () => {
    expect(screenFor(null)).toBeNull();
    expect(screenFor(err(401, '/auth/sign-in'))).toBe('signin');
    expect(screenFor(err(401))).toBe('signed-out');
    expect(screenFor(err(403))).toBe('refused');
    expect(screenFor(err(0))).toBe('down');
    expect(screenFor(new TypeError('fetch failed'))).toBe('down');
  });
});
