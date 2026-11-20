import { describe, expect, it } from 'vitest';
import { besideThread, chatActor, chatAddress } from './chat';

describe('chatAddress', () => {
  it('names an agent and a person as the shell does', () => {
    expect(chatAddress('bot:f00d-1')).toBe('agent:f00d-1');
    expect(chatAddress('human:42')).toBe('person:42');
    expect(chatAddress('helper', 'agent')).toBe('agent:helper');
    expect(chatAddress('42', 'person')).toBe('person:42');
  });
});

describe('chatActor', () => {
  it('reads the address back to an actor id', () => {
    expect(chatActor('agent:f00d-1')).toBe('bot:f00d-1');
    expect(chatActor('person:42')).toBe('human:42');
  });
  it('reads an actor id put in the address by mistake', () => {
    expect(chatActor('bot:qa')).toBe('bot:qa');
    expect(chatActor('human:42')).toBe('human:42');
  });
  it('names no one for a thread or an unknown kind', () => {
    expect(chatActor('thread:t1')).toBeUndefined();
    expect(chatActor('support')).toBeUndefined();
    expect(chatActor('robot:x')).toBeUndefined();
    expect(chatActor('agent:')).toBeUndefined();
  });
});

describe('besideThread', () => {
  it('carries the thread and marks the page as opened from it', () => {
    expect(besideThread('/tasks/p1/12/email', 'agent:gm')).toBe('/tasks/p1/12/email?chat=agent%3Agm&via=thread');
    expect(besideThread('/flow/12?tab=email', 'thread:t1')).toBe('/flow/12?tab=email&chat=thread%3At1&via=thread');
  });
  it('leaves the address as it is with no thread', () => {
    expect(besideThread('/tasks/p1/12', undefined)).toBe('/tasks/p1/12');
  });
});
