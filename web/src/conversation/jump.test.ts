import { afterEach, describe, expect, it } from 'vitest';
import { jumpTo, onJump } from './Outline';

/* A jump into a chat that is not drawn yet (the history line opens the chat, then asks for the message) waits for that
   chat to listen, once; a chat already listening takes it at once. */

const stops: (() => void)[] = [];
afterEach(() => stops.splice(0).forEach((s) => s()));

describe('a jump to a message', () => {
  it('reaches a chat that starts listening after it was asked, once', () => {
    jumpTo('room-a', 'm9');
    const got: string[] = [];
    stops.push(onJump('room-a', (m) => got.push(m)));
    expect(got).toEqual(['m9']);
    stops.push(onJump('room-a', (m) => got.push(`again ${m}`)));
    expect(got).toEqual(['m9']);
  });

  it('is not kept for another chat, and a listening chat takes it at once', () => {
    const got: string[] = [];
    stops.push(onJump('room-a', (m) => got.push(m)));
    jumpTo('room-a', 'm1');
    expect(got).toEqual(['m1']);
    stops.push(onJump('room-a', (m) => got.push(`late ${m}`)));
    jumpTo('room-b', 'm2');
    stops.push(onJump('room-c', (m) => got.push(`wrong ${m}`)));
    expect(got).toEqual(['m1']);
  });
});
