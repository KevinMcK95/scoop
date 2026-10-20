import { describe, expect, it } from 'vitest';
import { bar, cacheShare, seconds, stepTitle, tokens, type Trace } from './trace';

describe('trace helpers', () => {
  it('formats durations and token counts', () => {
    expect([seconds(850), seconds(12_340), seconds(undefined)]).toEqual(['850 ms', '12.3 s', '…']);
    expect([tokens(950), tokens(1234), tokens(45_600), tokens(null)]).toEqual(['950', '1.2k', '46k', '–']);
  });

  it('places steps on the turn timeline', () => {
    expect(bar({ kind: 'tool', start_ms: 2500, duration_ms: 5000 }, 10_000)).toEqual({ left: 25, width: 50 });
    expect(bar({ kind: 'tool', start_ms: 9000 }, 10_000)).toEqual({ left: 90, width: 10 }); // still running
  });

  it('names steps by what they did', () => {
    expect(stepTitle({ kind: 'model', start_ms: 0, reply: { role: 'ai', content: '', tool_calls: [{ name: 'region', args: {} }, { name: 'lookup', args: {} }] } }))
      .toBe('model → region, lookup');
    expect(stepTitle({ kind: 'model', start_ms: 0, reply: { role: 'ai', content: 'Done.' } })).toBe('model → answer');
    expect(stepTitle({ kind: 'tool', start_ms: 0, name: 'run_python' })).toBe('run_python');
    const trace = { totals: { input_tokens: 1000, cached_tokens: 900 } } as Trace;
    expect(cacheShare(trace)).toBe(0.9);
  });
});
