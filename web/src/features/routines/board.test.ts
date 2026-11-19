import { describe, expect, it } from 'vitest';
import { applyFlow, boardOf, dropItem, flowOf, putItem, takesTemplate, type Flow } from './board';
import type { Routine } from './types';

const DESTS = [
  { id: 'now', name: 'Now', then: [] },
  { id: 'later', name: 'Later', then: [{ do: 'archive', args: {} }] },
];
const FLOW = {
  trigger: { type: 'message', inbox: 'ada@company.example' },
  destinations: DESTS,
  steps: [
    {
      id: 'f1',
      type: 'filter',
      enabled: true,
      rules: [{ when: [{ field: 'message.from', op: 'contains', value: '@shop.example' }], match: 'all', then: [{ do: 'label', args: { name: 'Receipts' } }], to: 'later', next: 'end' }],
    },
    { id: 'd1', type: 'decide', enabled: true, out: 'd1', questions: { to: { type: 'choice', instructions: 'Pick.', criteria: { now: 'Now', later: 'Later' } } } },
    {
      id: 'f2',
      type: 'filter',
      enabled: true,
      rules: [
        { when: [{ field: 'd1.to', op: 'is', value: 'now' }], match: 'all', then: [], to: 'now', next: 'end' },
        { when: [{ field: 'd1.to', op: 'is', value: 'later' }], match: 'all', then: [], to: 'later', next: 'end' },
        { when: [], match: 'all', then: [], to: 'later', next: 'end' },
      ],
    },
  ],
} as unknown as Flow;
const owner = { owner_id: 'human:me' } as Pick<Routine, 'owner_id'>;
const board = () => {
  const b = boardOf(FLOW);
  if (!b) throw new Error('not a board');
  return b;
};

describe('the board', () => {
  it('reads a flow as a board and writes it back the same', () => {
    const b = board();
    expect(b.rules).toHaveLength(1);
    expect(b.llm).toMatchObject({ mode: 'sort', to: ['now', 'later'], default: 'later' });
    const again = boardOf({ ...flowOf(b), trigger: FLOW.trigger });
    // written back, a rule that ends also says stop (the engine's own field)
    expect(again?.rules).toMatchObject(b.rules);
    expect(again?.rules[0]?.stop).toBe(true);
    expect(again?.llm).toEqual(b.llm);
    expect(again?.dests).toEqual(b.dests);
  });

  it('is no board for a plain agent routine', () => {
    expect(boardOf({ trigger: { type: 'schedule' }, steps: [{ id: 's1', type: 'bot_turn' }], destinations: null } as unknown as Flow)).toBeNull();
  });

  it('adds a question and a destination, and refuses what the editor refused', () => {
    let b = putItem(board(), { part: 'question', text: 'Is this a receipt?', then: [{ do: 'star' }], goTo: 'later' }, owner);
    expect(b.questions[0]).toMatchObject({ text: 'Is this a receipt?', min: 0.75, to: 'later', then: [{ do: 'label', args: { name: 'STARRED' } }] });
    b = putItem(b, { part: 'destination', name: 'Read later' }, owner);
    expect(b.dests.at(-1)?.id).toBe('read_later');
    expect(b.llm.to).toContain('read_later');
    expect(() => putItem(b, { part: 'rule', when: [{ field: ' ', value: 'x' }] }, owner)).toThrow('Each condition needs a field');
    expect(() => putItem(b, { part: 'question', text: ' ' }, owner)).toThrow('Add the question');
    expect(() => putItem(b, { part: 'rule', goTo: 'nowhere' }, owner)).toThrow('No such destination');
    expect(() => putItem(b, { part: 'llm', dests: [] }, owner)).toThrow('Pick a destination');
  });

  it('ends what went to a removed destination, and keeps one', () => {
    const b = dropItem(board(), 'destination', 1);
    expect(b.dests.map((d) => d.id)).toEqual(['now']);
    expect(b.rules[0]).toMatchObject({ to: '', next: 'end' });
    expect(b.llm).toMatchObject({ to: ['now'], default: 'now' });
    expect(() => dropItem(b, 'destination', 0)).toThrow('A board keeps one destination at least');
  });

  it('moves an older inbox onto a template as Sort + draft, keeping its prompt', () => {
    const r = { id: 'r8', can_edit: true, owner_id: 'human:me', trigger: FLOW.trigger, steps: [{ id: 'l1', type: 'llm', enabled: true, prompt: 'Sort my mail.' }] } as unknown as Routine;
    expect(takesTemplate(r)).toBe(true);
    expect(takesTemplate({ ...r, can_edit: false })).toBe(false);
    const flow = applyFlow(r, { steps: FLOW.steps, destinations: DESTS });
    expect(flow?.steps.at(-1)).toMatchObject({ id: 'l1', type: 'llm', prompt: 'Sort my mail.', as: 'assistant:me', choose: { mode: 'draft', default: 'later' } });
    expect(applyFlow(r, { steps: FLOW.steps, destinations: [] })).toBeNull();
  });
});
