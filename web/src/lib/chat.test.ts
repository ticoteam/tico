import { describe, expect, it } from 'vitest';
import { chatActor, chatAddress } from './chat';

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
