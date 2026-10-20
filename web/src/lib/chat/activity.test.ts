import { describe, expect, it } from 'vitest';
import { activitySummary, contextSummary, groupMessages, stepLabel } from './activity';

describe('chat activity', () => {
  it('names steps in words and groups consecutive tool steps', () => {
    expect(stepLabel('match')).toBe('Matched sources to footprints');
    expect(stepLabel('some_new_tool')).toBe('Some new tool');
    const m = (id: number, role: string) => ({ id, role });
    const grouped = groupMessages([m(1, 'user'), m(2, 'tool'), m(3, 'tool'), m(4, 'assistant'), m(5, 'tool')]);
    expect(grouped.map((g) => g.kind === 'activity' ? g.steps.map((s) => s.id) : g.message.id)).toEqual([1, [2, 3], 4, [5]]);
  });

  it('collapses a finished group to a count, noting failures', () => {
    expect(activitySummary([{ status: 'success' }])).toBe('1 tool call');
    expect(activitySummary([{ status: 'success' }, { status: 'error' }, { status: 'success' }])).toBe('3 tool calls · 1 failed');
  });

  it('summarizes what travelled with a message in one line', () => {
    expect(contextSummary({ field: 'draco_1', selection: { table: 'gaia', id: '1433' },
      userRegions: [{}], layers: [{ visible: true }, { visible: false }] }))
      .toBe('with draco_1 · selected gaia 1433 · 1 drawn region · 1 layers on');
    expect(contextSummary({})).toBe('');
  });
});
