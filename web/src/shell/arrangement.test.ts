import { describe, expect, it } from 'vitest';
import { arrangementFor } from './arrangement';

describe('arrangementFor', () => {
  it('reads the arrangement from the address', () => {
    expect(arrangementFor('/next/tasks')).toBe('page');
    expect(arrangementFor('/tasks/p1')).toBe('page');
    expect(arrangementFor('/tasks/p1/12')).toBe('task');
    expect(arrangementFor('/tasks/p1/12/details')).toBe('task');
    expect(arrangementFor('/flow/12')).toBe('task');
    expect(arrangementFor('/task/12')).toBe('task');
    expect(arrangementFor('/call/r1')).toBe('call');
    expect(arrangementFor('/company', { chat: 'agent:qa' })).toBe('chat');
  });

  it('opens a teammate in the agent arrangement and a team as a page', () => {
    const isActor = (who: string) => who === 'qa' || who === 'p 1';
    expect(arrangementFor('/team/qa', {}, isActor)).toBe('agent');
    expect(arrangementFor('/team/p%201', {}, isActor)).toBe('agent');
    expect(arrangementFor('/team/engineering', {}, isActor)).toBe('page');
    expect(arrangementFor('/team/engineering', { chat: 'agent:qa' }, isActor)).toBe('chat');
    expect(arrangementFor('/team/anyone')).toBe('agent');
  });
});
