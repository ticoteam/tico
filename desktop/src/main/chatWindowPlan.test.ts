import { describe, expect, it } from 'vitest';
import { agentArg } from './validate';
import { CHAT_WINDOW_CAP, chatWindowStep } from './chatWindowPlan';

const open = (companyId: string, ...agents: string[]) => agents.map((agent) => ({ companyId, agent }));

describe('an agent’s chat windows', () => {
  it('brings an agent’s open window forward instead of opening a second', () => {
    expect(chatWindowStep(open('c1', 'release-owner'), 'c1', 'release-owner')).toBe('focus');
    expect(chatWindowStep(open('c1', 'release-owner'), 'c1', 'builder')).toBe('open');
  });

  it('keeps each company’s windows apart', () => {
    expect(chatWindowStep(open('c2', 'release-owner'), 'c1', 'release-owner')).toBe('open');
  });

  it('opens at most six per company; a seventh is refused, while the open ones still come forward', () => {
    expect(CHAT_WINDOW_CAP).toBe(6);
    const six = open('c1', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6');
    expect(chatWindowStep(six, 'c1', 'a7')).toBe('full');
    expect(chatWindowStep(six, 'c1', 'a3')).toBe('focus');
    expect(chatWindowStep([...six, ...open('c2', 'b1')], 'c2', 'b2')).toBe('open');
  });

  it('takes only an agent’s slug from the page', () => {
    expect(agentArg('release-owner')).toBe('release-owner');
    expect(agentArg('01a1cc30-93ee-7a3c-b82f-ea9b3a965099')).toBe('01a1cc30-93ee-7a3c-b82f-ea9b3a965099');
    for (const bad of ['', '../settings', 'a/b', 'a?window=0', 'x'.repeat(101), 3, null]) expect(() => agentArg(bad)).toThrow();
  });
});
