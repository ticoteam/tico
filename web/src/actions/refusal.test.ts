import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import { reasonIn, refusal, refusalFor } from './refusal';

describe('refusal', () => {
  it('puts the server reason second, after the short words', () => {
    expect(refusal({ error: 'factory_name', why: 'There is already a factory called Mill' }, 'Not saved')).toEqual({
      text: 'Not saved',
      name: 'There is already a factory called Mill',
      kind: 'failed',
    });
  });

  it('never turns a bare code into words', () => {
    expect(refusal({ error: 'factory_name' }, 'Not saved')).toEqual({ text: 'Not saved', kind: 'failed' });
    expect(reasonIn({ error: 'still_locked' })).toBeUndefined();
  });

  it('takes the first line of the words an action gave', () => {
    expect(reasonIn({ error: 'routine_invalid: schedule is missing\n- every\n- at' })).toBe('Schedule is missing');
  });

  it('says nothing for what the action layer wrote about the request itself', () => {
    expect(reasonIn({ error: 'failed: Cannot read properties of undefined' })).toBeUndefined();
    expect(reasonIn({ error: 'bad_input: name: Required' })).toBeUndefined();
    expect(reasonIn({ error: 'unknown_action: tasks.nope' })).toBeUndefined();
  });

  it('says the person may not, unless the server said why', () => {
    expect(refusal({ error: 'not_allowed' }, 'Not saved')).toEqual({ text: 'You cannot do that here', kind: 'failed' });
    expect(refusal({ error: 'not_allowed' }, 'Not saved', 'Only admins')).toEqual({ text: 'Only admins', kind: 'failed' });
    expect(refusal({ error: 'not_allowed', why: 'Only the owner can rename it' }, 'Not saved')).toEqual({
      text: 'Not saved',
      name: 'Only the owner can rename it',
      kind: 'failed',
    });
  });

  it('names a lost connection', () => {
    expect(refusal({ error: 'network' }, "Couldn't send")).toEqual({ text: "Couldn't send", name: 'No connection', kind: 'failed' });
  });

  it('drops a reason that reads like data', () => {
    expect(reasonIn({ error: 'bad_key: {"key": "sk-made-up"}' })).toBeUndefined();
    expect(reasonIn({ error: 'hook_failed: see https://example.test/log' })).toBeUndefined();
  });

  it('reads a caught server error through the same filter', () => {
    expect(refusalFor(new ApiError(409, 'name_taken', 'That name is taken. Pick another.'), 'Not saved')).toEqual({
      text: 'Not saved',
      name: 'That name is taken.',
      kind: 'failed',
    });
    expect(refusalFor(new ApiError(422, 'bad', '{"detail": "x"}'), 'Not saved')).toEqual({ text: 'Not saved', kind: 'failed' });
    expect(refusalFor(new ApiError(403, 'forbidden', ''), 'Not saved', 'Not allowed')).toEqual({ text: 'Not allowed', kind: 'failed' });
    expect(refusalFor(new Error('boom'), 'Not saved')).toEqual({ text: 'Not saved', kind: 'failed' });
  });
});
