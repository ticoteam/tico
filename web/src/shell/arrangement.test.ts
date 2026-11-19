import { describe, expect, it } from 'vitest';
import { arrangementFor, isTeammate } from './arrangement';

describe('arrangementFor', () => {
  it('reads the arrangement from the address', () => {
    expect(arrangementFor('/next/tasks')).toBe('page');
    expect(arrangementFor('/tasks/p1')).toBe('page');
    expect(arrangementFor('/tasks/p1/12')).toBe('task');
    expect(arrangementFor('/tasks/p1/12/details')).toBe('task');
    expect(arrangementFor('/tasks/p1/edit')).toBe('page');
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

  it('opens your own page as a plain page, and a person only once you are known', () => {
    const dir: Record<string, { id: string; kind: 'person' | 'agent' }> = {
      qa: { id: 'bot:qa', kind: 'agent' },
      'human:me1': { id: 'human:me1', kind: 'person' },
      'human:p2': { id: 'human:p2', kind: 'person' },
    };
    const find = (k: string) => dir[k];
    const at = (who: string, me?: string, known = true) => arrangementFor(`/team/${who}`, {}, (w) => isTeammate(w, known ? find : undefined, me));
    expect(at('me1', 'human:me1')).toBe('page');
    expect(at('p2', 'human:me1')).toBe('agent');
    expect(at('qa', 'human:me1')).toBe('agent');
    expect(at('engineering', 'human:me1')).toBe('page');
    // me not known yet: a person's page stays plain, an agent's is already the teammate arrangement
    expect(at('p2')).toBe('page');
    expect(at('qa')).toBe('agent');
    // the directory not here yet: anyone but you is a teammate
    expect(at('qa', 'human:me1', false)).toBe('agent');
    expect(at('me1', 'human:me1', false)).toBe('page');
  });
});
